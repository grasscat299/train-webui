"""学習用PC操作（rsyncでのコード同期、sshでのconfig書き込み・ジョブ起動・停止・
生存確認・ログ取得）の実装（CONTRACT.md §3）。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time

from pathlib import Path

from ..settings import Settings
from .errors import RemoteError
from .types import CommandRunner, GpuUsage, JobState, LaunchResult, LaunchSpec, SyncInfo

_SAFE_ID = re.compile(r"[A-Za-z0-9_.-]+")
_SSH_TIMEOUT_SEC = 30.0
_RSYNC_TIMEOUT_SEC = 300.0
_GIT_TIMEOUT_SEC = 15.0
_MAX_UNTRACKED_BYTES = 1024 * 1024
_STDERR_TAIL_CHARS = 2000
_BINARY_SNIFF_BYTES = 8000  # 先頭何バイトでNUL文字を見てバイナリ判定するか


class _SubprocessCommandRunner:
    """runner未指定時に使う既定実装。"""

    def run(self, args: list[str], *, input: bytes | None = None,
            timeout: float | None = None) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(args, input=input, capture_output=True, timeout=timeout)


class RemoteController:
    def __init__(self, settings: Settings, runner: CommandRunner | None = None) -> None:
        self._settings = settings
        self._runner: CommandRunner = runner if runner is not None else _SubprocessCommandRunner()
        # stop() のポーリング間隔。テストでは瞬時に進めるため差し替え可能にしておく。
        self._sleep = time.sleep

    # ------------------------------------------------------------------
    # RemoteControllerProtocol 実装
    # ------------------------------------------------------------------

    def sync_code(self, source_dir: Path | None = None) -> SyncInfo:
        if source_dir is not None:  # 過去の run のコードを取り出したディレクトリ。git情報は呼び出し側が持っている
            self._rsync(source_dir)
            return SyncInfo(git_commit="", git_diff="", git_untracked="{}")
        git_commit = self._git(["rev-parse", "HEAD"]).strip()
        git_diff = self._git(["diff", "HEAD"])
        untracked_paths = [p for p in self._git(["ls-files", "--others", "--exclude-standard"]).splitlines() if p]
        git_untracked = json.dumps(self._collect_untracked(untracked_paths), ensure_ascii=False)
        self._rsync()
        return SyncInfo(git_commit=git_commit, git_diff=git_diff, git_untracked=git_untracked)

    def remove_run_files(self, run_id: str) -> None:
        if not _SAFE_ID.fullmatch(run_id):
            raise RemoteError(f"run_id が不正です: {run_id!r}")
        proc = self._ssh_run(f"rm -rf {shlex.quote(f'{self._settings.remote.run_root}/{run_id}')}",
                             timeout=_SSH_TIMEOUT_SEC)
        self._check(proc, "runのファイルの削除")

    def launch(self, spec: LaunchSpec) -> LaunchResult:
        r = self._settings.remote
        job_dir = f"{r.run_root}/{spec.run_id}/{spec.job_id}"
        isolate = self._settings.max_parallel_jobs > 1
        # 同時に複数のジョブを動かす時は、ジョブごとにコードの複製（<job_dir>/code）を作ってそこで動かす。
        # 共有の repo_dir は次のジョブの rsync --delete や config.toml の書き換えで、動いているジョブの足元が変わるため
        code_dir = f"{job_dir}/code" if isolate else r.repo_dir
        if isolate:
            snap = f"mkdir -p {shlex.quote(job_dir)} && cp -a {shlex.quote(r.repo_dir)} {shlex.quote(code_dir)}"
            self._check(self._ssh_run(snap, timeout=_RSYNC_TIMEOUT_SEC), "コードの複製")
        remote_config_path = f"{code_dir}/{spec.project_dir}/config.toml"
        write_cmd = f"cat > {shlex.quote(remote_config_path)}"
        proc = self._ssh_run(write_cmd, input=spec.config_toml.encode("utf-8"), timeout=_SSH_TIMEOUT_SEC)
        self._check(proc, "config.tomlの書き込み")
        # run の保管場所に config のスナップショットも残す（<run_root>/<run_id>/config.toml）
        run_dir = f"{r.run_root}/{spec.run_id}"
        snap_cmd = f"mkdir -p {shlex.quote(run_dir)} && cat > {shlex.quote(run_dir + '/config.toml')}"
        proc = self._ssh_run(snap_cmd, input=spec.config_toml.encode("utf-8"), timeout=_SSH_TIMEOUT_SEC)
        self._check(proc, "configスナップショットの書き込み")

        remote_cmd = self._build_launch_command(spec, job_dir, code_dir)
        proc = self._ssh_run(remote_cmd, timeout=_SSH_TIMEOUT_SEC)
        self._check(proc, "ジョブの起動")
        pid = self._parse_pid(proc.stdout)
        return LaunchResult(pid=pid, job_dir=job_dir)

    def stop(self, pid: int) -> None:
        self._kill(pid, "TERM")
        timeout = self._settings.remote.stop_timeout_sec
        poll_interval = max(min(timeout / 5, 0.5), 0.01)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._is_alive(pid):
                return
            self._sleep(poll_interval)
        if self._is_alive(pid):
            self._kill(pid, "KILL")

    def poll(self, pid: int, job_dir: str) -> JobState:
        quoted_job_dir = shlex.quote(job_dir)
        # 末尾の `; true` は cat が run.exit 未生成時に非0終了してもコマンド全体の終了コードを汚さないため
        cmd = (
            f"if kill -0 {int(pid)} 2>/dev/null; then echo ALIVE; else echo DEAD; fi; "
            f"cat {quoted_job_dir}/run.exit 2>/dev/null; true"
        )
        proc = self._ssh_run(cmd, timeout=_SSH_TIMEOUT_SEC)
        self._check(proc, "ジョブ状態の確認")
        lines = proc.stdout.decode("utf-8", errors="replace").splitlines()
        alive = bool(lines) and lines[0].strip() == "ALIVE"
        exit_code = None
        if not alive:
            rest = "\n".join(lines[1:]).strip()
            if rest and rest.lstrip("-").isdigit():
                exit_code = int(rest)
        return JobState(alive=alive, exit_code=exit_code)

    def read_log(self, job_dir: str, offset: int) -> tuple[bytes, int]:
        quoted_log_path = shlex.quote(f"{job_dir}/run.log")
        cmd = f"test -f {quoted_log_path} && tail -c +{offset + 1} {quoted_log_path} || true"
        proc = self._ssh_run(cmd, timeout=_SSH_TIMEOUT_SEC)
        self._check(proc, "ログの取得")
        data = proc.stdout
        return data, offset + len(data)

    def gpu_usage(self) -> GpuUsage:
        # instance は --containall で PID 名前空間が分かれているため、ps では他の人のプロセスは見えない。
        # nvidia-smi のメモリ使用量は GPU 全体の値なので、そこから「ps にも見える（= 自分たちの）プロセス」の
        # 分を引いた残りを、他の人の使用量とみなす。
        cmd = (
            "nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits; echo ---; "
            "nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits; echo ---; "
            "ps -eo pid --no-headers; echo ---; "
            "nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits"
        )
        proc = self._ssh_run(cmd, timeout=_SSH_TIMEOUT_SEC)
        self._check(proc, "GPU使用状況の確認")
        parts = proc.stdout.decode("utf-8", errors="replace").split("---")
        if len(parts) not in (3, 4):
            raise RemoteError(f"GPU使用状況の出力を解釈できませんでした: {proc.stdout[-500:]!r}")
        total = sum(int(v) for v in parts[0].split() if v.strip().isdigit())
        own_pids = {int(v) for v in parts[2].split() if v.strip().isdigit()}
        own = 0
        for line in parts[1].splitlines():
            cols = [c.strip() for c in line.split(",")]
            if len(cols) == 2 and cols[0].isdigit() and cols[1].isdigit() and int(cols[0]) in own_pids:
                own += int(cols[1])
        total_mem = sum(int(v) for v in parts[3].split() if v.strip().isdigit()) if len(parts) == 4 else 0
        return GpuUsage(total_used_mib=total, own_mib=own, total_mib=total_mem)

    def monitor(self) -> dict:
        # /proc/stat を 0.5 秒あけて2回読み、差分からコアごとの使用率を出す。GPU は nvidia-smi を1回
        cmd = (
            "cat /proc/stat; echo ---; sleep 0.5; cat /proc/stat; echo ---; "
            "nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw "
            "--format=csv,noheader,nounits 2>/dev/null; true"
        )
        proc = self._ssh_run(cmd, timeout=_SSH_TIMEOUT_SEC)
        self._check(proc, "CPU/GPU使用状況の確認")
        return parse_monitor(proc.stdout.decode("utf-8", errors="replace"))

    def monitor(self) -> dict:
        # /proc/stat を 0.5 秒あけて2回読み、差分からコアごとの使用率を出す。GPU は nvidia-smi を1回
        cmd = (
            "cat /proc/stat; echo ---; sleep 0.5; cat /proc/stat; echo ---; "
            "nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw "
            "--format=csv,noheader,nounits 2>/dev/null; true"
        )
        proc = self._ssh_run(cmd, timeout=_SSH_TIMEOUT_SEC)
        self._check(proc, "CPU/GPU使用状況の確認")
        return parse_monitor(proc.stdout.decode("utf-8", errors="replace"))

    # ------------------------------------------------------------------
    # ssh/rsync コマンド組み立て
    # ------------------------------------------------------------------

    def _ssh_base_args(self) -> list[str]:
        r = self._settings.remote
        return [
            "ssh", "-p", str(r.port), "-i", os.path.expanduser(r.key_path),
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"{r.user}@{r.host}",
        ]

    def _ssh_dash_e(self) -> str:
        r = self._settings.remote
        key = shlex.quote(os.path.expanduser(r.key_path))
        return f"ssh -p {r.port} -i {key} -o BatchMode=yes -o ConnectTimeout=10"

    def _build_launch_command(self, spec: LaunchSpec, job_dir: str, code_dir: str | None = None) -> str:
        r = self._settings.remote
        module = spec.project_dir.replace("/", ".")
        env_parts = [
            "PYTHONUNBUFFERED=1",
            f"TRAIN_WEBUI_URL={shlex.quote(self._settings.server.public_url)}",
            f"TRAIN_WEBUI_RUN_ID={shlex.quote(spec.run_id)}",
            f"TRAIN_WEBUI_PROJECT={shlex.quote(spec.project_dir)}",
            f"TRAIN_WEBUI_SOURCE={shlex.quote(spec.kind)}",
            # 重みなどの保存先（run ごと）と、引き継ぎ元の run の重みを探すための起点（design.md §3.5）
            f"TRAIN_WEBUI_ARTIFACT_DIR={shlex.quote(f'{r.run_root}/{spec.run_id}/artifacts')}",
            f"TRAIN_WEBUI_RUN_ROOT={shlex.quote(r.run_root)}",
        ]
        if spec.parent_run_id:
            env_parts.append(f"TRAIN_WEBUI_INIT_FROM={shlex.quote(spec.parent_run_id)}")
        quoted_job_dir = shlex.quote(job_dir)
        # ssh先はsingularity instance内のsshdなので、コンテナで包まずそのままpythonを実行する
        inner = (
            f"env {' '.join(env_parts)} {shlex.quote(r.python)} -m {module}.main --mode {spec.kind}; "
            f"echo $? > {quoted_job_dir}/run.exit"
        )
        # `a && b && c &` だと連結全体が背景化され mkdir 前に run.pid を書きうるので、背景化部分だけ括弧で囲む
        return (
            f"cd {shlex.quote(code_dir or r.repo_dir)} && mkdir -p {quoted_job_dir} {shlex.quote(f'{r.run_root}/{spec.run_id}/artifacts')} && "
            f"( setsid nohup sh -c {shlex.quote(inner)} > {quoted_job_dir}/run.log 2>&1 < /dev/null & "
            f"echo $! > {quoted_job_dir}/run.pid ) && cat {quoted_job_dir}/run.pid"
        )

    def _kill(self, pid: int, signal: str) -> None:
        # プロセスが既に居ない場合の非0終了はエラー扱いにしない（stderrを捨てて常に成功扱いにする）。
        cmd = f"kill -{signal} -{int(pid)} 2>/dev/null; true"
        proc = self._ssh_run(cmd, timeout=_SSH_TIMEOUT_SEC)
        self._check(proc, f"プロセスグループへのSIG{signal}送信")

    def _is_alive(self, pid: int) -> bool:
        proc = self._ssh_run(f"kill -0 {int(pid)} 2>/dev/null", timeout=_SSH_TIMEOUT_SEC)
        return proc.returncode == 0

    def _parse_pid(self, stdout: bytes) -> int:
        text = stdout.decode("utf-8", errors="replace")
        for line in reversed([l.strip() for l in text.splitlines() if l.strip()]):
            if line.isdigit():
                return int(line)
        raise RemoteError(f"起動コマンドの出力からPIDを取得できませんでした: {text!r}")

    def _rsync(self, source: Path | None = None) -> None:
        r = self._settings.remote
        # rsync は最後の1階層しか作らない。新しい学習用PC（repo_dir・run_root がまだ無い）でも初回から動くように先に作る
        mk = self._ssh_run(f"mkdir -p {shlex.quote(r.repo_dir)} {shlex.quote(r.run_root)}", timeout=_SSH_TIMEOUT_SEC)
        self._check(mk, "同期先ディレクトリの作成")
        args = ["rsync", "-az", "--delete", "-e", self._ssh_dash_e()]
        for pattern in self._settings.sync_exclude:
            args += ["--exclude", pattern]
        args += [f"{source or self._settings.repo_root}/", f"{r.user}@{r.host}:{r.repo_dir}/"]
        proc = self._run(args, timeout=_RSYNC_TIMEOUT_SEC)
        self._check(proc, "コードの同期(rsync)")

    def _git(self, args: list[str]) -> str:
        proc = self._run(["git", "-C", str(self._settings.repo_root), *args], timeout=_GIT_TIMEOUT_SEC)
        self._check(proc, f"git {' '.join(args)} の実行")
        return proc.stdout.decode("utf-8", errors="replace")

    def _collect_untracked(self, paths: list[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        root = self._settings.repo_root
        for rel in paths:
            try:
                data = (root / rel).read_bytes()
            except OSError:
                continue
            if len(data) > _MAX_UNTRACKED_BYTES or not self._is_text(data):
                continue
            result[rel] = data.decode("utf-8", errors="replace")
        return result

    @staticmethod
    def _is_text(data: bytes) -> bool:
        return b"\x00" not in data[:_BINARY_SNIFF_BYTES]

    # ------------------------------------------------------------------
    # runner呼び出し・エラー変換
    # ------------------------------------------------------------------

    def _ssh_run(self, remote_command: str, *, input: bytes | None = None,
                 timeout: float | None = None) -> subprocess.CompletedProcess[bytes]:
        return self._run(self._ssh_base_args() + [remote_command], input=input, timeout=timeout)

    def _run(self, args: list[str], *, input: bytes | None = None,
             timeout: float | None = None) -> subprocess.CompletedProcess[bytes]:
        try:
            return self._runner.run(args, input=input, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise RemoteError(f"コマンドがタイムアウトしました（{timeout}秒）: {' '.join(args)}") from e

    @staticmethod
    def _check(proc: subprocess.CompletedProcess[bytes], context: str) -> None:
        if proc.returncode != 0:
            stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
            tail = stderr[-_STDERR_TAIL_CHARS:].strip()
            raise RemoteError(f"{context}に失敗しました（終了コード{proc.returncode}）: {tail}")


def _cpu_times(text: str) -> dict[str, tuple[int, int]]:
    """/proc/stat の cpuN 行から {名前: (全体, 暇な時間)}。cpu（全体）の行は "cpu" で入る。"""
    out: dict[str, tuple[int, int]] = {}
    for line in text.splitlines():
        cols = line.split()
        if not cols or not cols[0].startswith("cpu"):
            continue
        vals = [int(v) for v in cols[1:] if v.isdigit()]
        if len(vals) < 5:
            continue
        out[cols[0]] = (sum(vals[:8]), vals[3] + vals[4])  # idle + iowait
    return out


def parse_monitor(output: str) -> dict:
    parts = output.split("---")
    if len(parts) != 3:
        raise RemoteError(f"モニタの出力を解釈できませんでした: {output[-500:]!r}")
    before, after = _cpu_times(parts[0]), _cpu_times(parts[1])

    def usage(name: str) -> float:
        total = after[name][0] - before[name][0]
        idle = after[name][1] - before[name][1]
        return round(100.0 * (total - idle) / total, 1) if total > 0 else 0.0

    names = sorted((n for n in after if n in before and n != "cpu"), key=lambda n: int(n[3:]))
    gpus = []
    for line in parts[2].splitlines():
        c = [v.strip() for v in line.split(",")]
        if len(c) != 7:
            continue

        def num(v: str) -> float | None:
            try:
                return float(v)
            except ValueError:
                return None  # "[N/A]" など

        gpus.append({
            "index": int(c[0]), "name": c[1], "util": num(c[2]), "mem_used_mib": num(c[3]),
            "mem_total_mib": num(c[4]), "temp_c": num(c[5]), "power_w": num(c[6]),
        })
    return {
        "cpu_total": usage("cpu") if "cpu" in after and "cpu" in before else None,
        "cpu_cores": [usage(n) for n in names],
        "gpus": gpus,
    }
