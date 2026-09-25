"""学習用PC操作（rsyncでのコード同期、sshでのconfig書き込み・ジョブ起動・停止・
生存確認・ログ取得）の実装（CONTRACT.md §3）。
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time

from ..settings import Settings
from .errors import RemoteError
from .types import CommandRunner, JobState, LaunchResult, LaunchSpec, SyncInfo

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

    def sync_code(self) -> SyncInfo:
        git_commit = self._git(["rev-parse", "HEAD"]).strip()
        git_diff = self._git(["diff", "HEAD"])
        untracked_paths = [p for p in self._git(["ls-files", "--others", "--exclude-standard"]).splitlines() if p]
        git_untracked = json.dumps(self._collect_untracked(untracked_paths), ensure_ascii=False)
        self._rsync()
        return SyncInfo(git_commit=git_commit, git_diff=git_diff, git_untracked=git_untracked)

    def launch(self, spec: LaunchSpec) -> LaunchResult:
        r = self._settings.remote
        remote_config_path = f"{r.repo_dir}/{spec.project_dir}/config.toml"
        write_cmd = f"cat > {shlex.quote(remote_config_path)}"
        proc = self._ssh_run(write_cmd, input=spec.config_toml.encode("utf-8"), timeout=_SSH_TIMEOUT_SEC)
        self._check(proc, "config.tomlの書き込み")

        job_dir = f"{r.run_root}/{spec.run_id}/{spec.job_id}"
        remote_cmd = self._build_launch_command(spec, job_dir)
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

    def _build_launch_command(self, spec: LaunchSpec, job_dir: str) -> str:
        r = self._settings.remote
        module = spec.project_dir.replace("/", ".")
        env_parts = [
            "PYTHONUNBUFFERED=1",
            f"TRAIN_WEBUI_URL={shlex.quote(self._settings.server.public_url)}",
            f"TRAIN_WEBUI_RUN_ID={shlex.quote(spec.run_id)}",
            f"TRAIN_WEBUI_PROJECT={shlex.quote(spec.project_dir)}",
            f"TRAIN_WEBUI_SOURCE={shlex.quote(spec.kind)}",
        ]
        quoted_job_dir = shlex.quote(job_dir)
        # ssh先はsingularity instance内のsshdなので、コンテナで包まずそのままpythonを実行する
        inner = (
            f"env {' '.join(env_parts)} {shlex.quote(r.python)} -m {module}.main --mode {spec.kind}; "
            f"echo $? > {quoted_job_dir}/run.exit"
        )
        # `a && b && c &` だと連結全体が背景化され mkdir 前に run.pid を書きうるので、背景化部分だけ括弧で囲む
        return (
            f"cd {shlex.quote(r.repo_dir)} && mkdir -p {quoted_job_dir} && "
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

    def _rsync(self) -> None:
        r = self._settings.remote
        args = ["rsync", "-az", "--delete", "-e", self._ssh_dash_e()]
        for pattern in self._settings.sync_exclude:
            args += ["--exclude", pattern]
        args += [f"{self._settings.repo_root}/", f"{r.user}@{r.host}:{r.repo_dir}/"]
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
