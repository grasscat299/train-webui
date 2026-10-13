"""ジョブ管理（CONTRACT.md §3）。起動フロー（sync_code → launch → DB更新）とポーリングループ。

remote（学習用PC操作）呼び出しで例外が出た場合は何でも捕まえて run/job を failed にし、
メッセージを error に入れる。
"""
from __future__ import annotations

import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .db import Database, now_iso
from .ids import new_job_id
from .remote.types import LaunchSpec, RemoteControllerProtocol
from .settings import Settings


class JobBusyError(Exception):
    """すでに他のジョブが実行中（システム全体で1ジョブまで）。"""


class JobManager:
    def __init__(self, settings: Settings, db: Database, remote: RemoteControllerProtocol,
                 launch_in_background: bool = True,
                 snapshot_code: Callable[[str], str] | None = None,
                 export_code: Callable[[str, Path], None] | None = None) -> None:
        # snapshot_code(run_id) -> commit: 起動時のコードを ref にして残す。export_code(commit, dest): 過去のコードを取り出す
        # どちらも None のとき（テスト等）はコードの記録・復元をしない
        self._snapshot_code = snapshot_code
        self._export_code = export_code
        self._settings = settings
        self._db = db
        self._remote = remote
        self._launch_in_background = launch_in_background
        self._launch_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._stopping: set[str] = set()
        self._stopping_lock = threading.Lock()

    # ---- バックグラウンドループ ------------------------------------

    def start_background(self) -> None:
        if self._thread is not None:
            return
        self._fail_interrupted_launches()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _fail_interrupted_launches(self) -> None:
        """pid未記録のrunningジョブ＝前回server停止時に起動処理の途中だったもの。放置するとbusyが解けない。"""
        for job in self._db.list_running_jobs():
            if job["pid"] is None:
                self._fail_job_and_run(job["job_id"], job["run_id"], "server再起動により起動処理が中断されました",
                                       also_run=job["kind"] == "train")

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop_event.wait(self._settings.poll_interval_sec):
            self.poll_once()

    def poll_once(self) -> None:
        """テストから1回分を直接呼べるようにした、ポーリング処理本体。"""
        for job in self._db.list_running_jobs():
            self._poll_one_job(job)
        self._check_heartbeats()

    # ---- 排他状態 --------------------------------------------------

    def get_busy_state(self) -> tuple[bool, dict[str, Any] | None]:
        job = self._db.get_running_job()
        if job is not None:
            return True, {
                "run_id": job["run_id"], "job_id": job["job_id"],
                "kind": job["kind"], "origin": "webui",
            }
        cli_run = self._db.get_running_cli_run()
        if cli_run is not None:
            return True, {
                "run_id": cli_run["run_id"], "job_id": None,
                "kind": "train", "origin": "cli",
            }
        return False, None

    # ---- 起動 --------------------------------------------------------

    def start_train(self, run_id: str) -> None:
        """draft を起動する。jobを確保したら戻り、sync_code → launch は裏で進める（rsyncが長いため）。"""
        run = self._db.get_run(run_id)
        if run is None:
            raise ValueError("run が見つかりません")
        job_id = self._reserve_job(run_id, kind="train")
        self._db.update_run(run_id, status="running", started_at=now_iso())
        self._spawn(self._launch_train, run_id, job_id, run)

    def _spawn(self, fn, *args) -> None:
        if self._launch_in_background:
            threading.Thread(target=fn, args=args, daemon=True).start()
        else:
            fn(*args)

    def _launch_train(self, run_id: str, job_id: str, run: dict) -> None:
        try:
            if run.get("code_commit"):
                # 過去の run のコードで再実行する下書き。ローカルの作業ツリーではなく、記録した commit を送る
                self._sync_recorded_code(run["code_commit"])
            else:
                sync_info = self._remote.sync_code()
                self._db.create_sync(
                    synced_at=now_iso(), git_commit=sync_info.git_commit,
                    git_diff=sync_info.git_diff, git_untracked=sync_info.git_untracked,
                )
                self._db.update_run(
                    run_id, git_commit=sync_info.git_commit, git_diff=sync_info.git_diff,
                    git_untracked=sync_info.git_untracked, git_info_source="sync",
                )
                if self._snapshot_code is not None:
                    self._db.update_run(run_id, code_commit=self._snapshot_code(run_id))
            spec = LaunchSpec(
                run_id=run_id, job_id=job_id, kind="train",
                project_dir=run["project"], config_toml=run["config_toml"],
                parent_run_id=run.get("parent_run_id"),
            )
            result = self._remote.launch(spec)
            self._db.update_job(job_id, pid=result.pid, job_dir=result.job_dir)
        except Exception as e:  # noqa: BLE001 - remoteの失敗は種類を問わずfailedにする
            self._fail_job_and_run(job_id, run_id, str(e), also_run=True)

    def _sync_recorded_code(self, commit: str) -> None:
        if self._export_code is None:
            raise RuntimeError("過去のコードを取り出せません（コードの記録が無効です）")
        with tempfile.TemporaryDirectory() as tmp:
            self._export_code(commit, Path(tmp))
            self._remote.sync_code(Path(tmp))

    def start_infer(self, run_id: str, project: str, config_toml: str | None = None) -> dict:
        """groupの[infer]ボタン。対象runは変えず、jobだけ作って起動する。
        config_toml を渡すとそのjobだけその config で動かす（ctl infer --set の上書き）。"""
        run = self._db.get_run(run_id)
        if run is None:
            raise ValueError("run が見つかりません")
        if config_toml is not None:
            run = {**run, "config_toml": config_toml}
        job_id = self._reserve_job(run_id, kind="infer")
        self._spawn(self._launch_infer, run_id, job_id, run, project)
        job = self._db.get_job(job_id)
        assert job is not None
        return job

    def _launch_infer(self, run_id: str, job_id: str, run: dict, project: str) -> None:
        try:
            sync_info = self._remote.sync_code()
            self._db.create_sync(
                synced_at=now_iso(), git_commit=sync_info.git_commit,
                git_diff=sync_info.git_diff, git_untracked=sync_info.git_untracked,
            )
            spec = LaunchSpec(
                run_id=run_id, job_id=job_id, kind="infer",
                project_dir=project, config_toml=run["config_toml"],
            )  # 推論は parent を使わない（その run 自身の重みを artifacts から読む）
            result = self._remote.launch(spec)
            self._db.update_job(job_id, pid=result.pid, job_dir=result.job_dir)
        except Exception as e:  # noqa: BLE001
            self._fail_job_and_run(job_id, run_id, str(e), also_run=False)

    def _reserve_job(self, run_id: str, kind: str) -> str:
        """busyチェックとjob行の作成をロック内でアトミックに行う（2重起動レース対策）。"""
        with self._launch_lock:
            busy, _ = self.get_busy_state()
            if busy:
                raise JobBusyError("GPUを使用中の別のジョブがあります")
            job_id = new_job_id(run_id, kind, exists=lambda j: j in self._db.list_job_ids())
            self._db.create_job(
                job_id=job_id, run_id=run_id, kind=kind, status="running", started_at=now_iso(),
            )
            return job_id

    def _fail_job_and_run(self, job_id: str, run_id: str, message: str, also_run: bool) -> None:
        now = now_iso()
        self._db.update_job(job_id, status="failed", ended_at=now, error=message)
        if also_run:
            self._db.update_run(run_id, status="failed", ended_at=now, error=message)

    # ---- 停止 --------------------------------------------------------

    def stop_job(self, run_id: str) -> None:
        job = self._db.get_running_job_for_run(run_id)
        if job is None:
            raise ValueError("停止対象のジョブがありません")
        if job["pid"] is None:
            # ここで stopped にすると、裏の起動処理がこの後プロセスを立ち上げてWebUIの管理外に残る
            raise ValueError("起動処理中のため停止できません。数秒後にもう一度試してください")
        with self._stopping_lock:
            self._stopping.add(job["job_id"])
        try:
            error = None
            try:
                self._remote.stop(job["pid"])
            except Exception as e:  # noqa: BLE001
                error = str(e)
            self._pull_log(job)
            now = now_iso()
            self._db.update_job(job["job_id"], status="stopped", ended_at=now, error=error)
            if job["kind"] == "train":
                self._db.update_run(run_id, status="stopped", ended_at=now)
        finally:
            with self._stopping_lock:
                self._stopping.discard(job["job_id"])

    # ---- ポーリング ----------------------------------------------------

    def _poll_one_job(self, job: dict) -> None:
        if job["job_dir"] is None or job["pid"] is None:
            return  # launchがまだ完了していない（起動処理のスレッド側で進行中）
        with self._stopping_lock:
            if job["job_id"] in self._stopping:
                return  # 停止処理側が stopped で確定させる（ここで failed と書くと競合する）
        self._pull_log(job)
        try:
            state = self._remote.poll(job["pid"], job["job_dir"])
        except Exception:  # noqa: BLE001 - 一時的な通信断はリトライに任せる
            return
        if state.alive:
            return
        self._pull_log(job)  # 終了直前の差分を取りこぼさないよう最後にもう一度
        status = "finished" if state.exit_code == 0 else "failed"
        now = now_iso()
        self._db.update_job(job["job_id"], status=status, ended_at=now, exit_code=state.exit_code)
        if job["kind"] == "train":
            self._db.update_run(job["run_id"], status=status, ended_at=now, exit_code=state.exit_code)

    def _log_path(self, job_id: str) -> Path:
        return self._settings.server.media_dir / "logs" / f"{job_id}.log"

    def _pull_log(self, job: dict) -> None:
        local_path = self._log_path(job["job_id"])
        local_path.parent.mkdir(parents=True, exist_ok=True)
        offset = local_path.stat().st_size if local_path.exists() else 0
        try:
            data, _new_offset = self._remote.read_log(job["job_dir"], offset)
        except Exception:  # noqa: BLE001 - ログ取得の失敗はジョブを止めない
            return
        if data:
            with open(local_path, "ab") as f:
                f.write(data)
        rel = f"logs/{job['job_id']}.log"
        if job.get("log_path") != rel:
            self._db.update_job(job["job_id"], log_path=rel)
            job["log_path"] = rel

    def read_local_log(self, job_id: str, offset: int) -> tuple[str, int]:
        """`GET /api/jobs/{job_id}/log` 用。ローカルに取り込み済みの分だけを返す。"""
        local_path = self._log_path(job_id)
        if not local_path.exists():
            return "", offset
        with open(local_path, "rb") as f:
            f.seek(offset)
            chunk = f.read()
        text = chunk.decode("utf-8", errors="replace")
        return text, offset + len(chunk)

    # ---- heartbeat -----------------------------------------------------

    def _check_heartbeats(self) -> None:
        now = datetime.now().astimezone()
        for run in self._db.list_running_cli_runs():
            last = run["last_heartbeat_at"]
            if last is None:
                continue
            last_dt = datetime.fromisoformat(last)
            if now - last_dt > timedelta(seconds=self._settings.lost_after_sec):
                self._db.update_run(run["run_id"], status="lost")
