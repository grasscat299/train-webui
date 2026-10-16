"""backendのテスト用フィクスチャ。学習用PCに触れないよう、RemoteControllerProtocol を満たす FakeRemote に差し替える。"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from train_webui.backend.remote.types import GpuUsage, JobState, LaunchResult, LaunchSpec, SyncInfo
from train_webui.backend.settings import RemoteSettings, ServerSettings, Settings


class FakeRemote:
    """呼び出しを記録し、poll の結果をテストから制御できる偽 RemoteController。"""

    def __init__(self) -> None:
        self.sync_calls = 0
        self.sync_sources: list = []
        self.removed_runs: list[str] = []
        self.launches: list[LaunchSpec] = []
        self.stops: list[int] = []
        self.stop_requests: list[str] = []
        self.poll_results: dict[int, JobState] = {}
        self.logs: dict[str, bytes] = {}
        self.fail_sync = False
        self.fail_launch = False
        self.gpu = GpuUsage(total_used_mib=0, own_mib=0)
        self.fail_gpu = False
        self.checkpoints: dict[str, bytes] = {}   # run_id -> 重みの tar（read/write_checkpoint 用）
        self.fail_checkpoint = False
        self._next_pid = 1000

    def remove_run_files(self, run_id: str) -> None:
        self.removed_runs.append(run_id)

    def read_checkpoint(self, run_id: str) -> bytes:
        if self.fail_checkpoint:
            raise RuntimeError("重みの読み出しに失敗しました（fake）")
        return self.checkpoints[run_id]

    def write_checkpoint(self, run_id: str, data: bytes) -> None:
        if self.fail_checkpoint:
            raise RuntimeError("重みの書き込みに失敗しました（fake）")
        self.checkpoints[run_id] = data

    def sync_code(self, source_dir=None) -> SyncInfo:
        self.sync_calls += 1
        self.sync_sources.append(source_dir)
        if self.fail_sync:
            raise RuntimeError("sync に失敗しました（fake）")
        return SyncInfo(git_commit="abc123", git_diff="diff", git_untracked="{}")

    def launch(self, spec: LaunchSpec) -> LaunchResult:
        if self.fail_launch:
            raise RuntimeError("launch に失敗しました（fake）")
        pid = self._next_pid
        self._next_pid += 1
        job_dir = f"/remote/run_root/{spec.run_id}/{spec.job_id}"
        self.launches.append(spec)
        self.poll_results[pid] = JobState(alive=True, exit_code=None)
        self.logs[job_dir] = b""
        return LaunchResult(pid=pid, job_dir=job_dir)

    def stop(self, pid: int) -> None:
        self.stops.append(pid)
        self.poll_results[pid] = JobState(alive=False, exit_code=None)

    def request_stop(self, job_dir: str) -> None:
        self.stop_requests.append(job_dir)

    def poll(self, pid: int, job_dir: str) -> JobState:
        return self.poll_results.get(pid, JobState(alive=False, exit_code=1))

    def read_log(self, job_dir: str, offset: int) -> tuple[bytes, int]:
        data = self.logs.get(job_dir, b"")
        chunk = data[offset:]
        return chunk, offset + len(chunk)

    def gpu_usage(self) -> GpuUsage:
        if self.fail_gpu:
            raise RuntimeError("ssh に失敗しました（fake）")
        return self.gpu

    # ---- テストヘルパー -------------------------------------------------

    def set_finished(self, pid: int, exit_code: int = 0) -> None:
        self.poll_results[pid] = JobState(alive=False, exit_code=exit_code)

    def append_log(self, job_dir: str, data: bytes) -> None:
        self.logs[job_dir] = self.logs.get(job_dir, b"") + data


def make_settings(
    tmp_path: Path, repo_root: Path, poll_interval_sec: float = 0.01, lost_after_sec: float = 5.0,
    allowed_networks: tuple[str, ...] = (),
) -> Settings:
    server = ServerSettings(
        host="127.0.0.1", port=0, public_url="http://test.invalid",
        db_path=tmp_path / "webui.sqlite", media_dir=tmp_path / "media",
        allowed_networks=allowed_networks,
    )
    remote = RemoteSettings(
        host="gpu-host", user="u", port=22, key_path="~/.ssh/id_ed25519",
        repo_dir="/remote/td_plan", python="python3",
        run_root="/remote/run_root", stop_timeout_sec=1.0,
    )
    return Settings(
        remote=remote, server=server, poll_interval_sec=poll_interval_sec,
        lost_after_sec=lost_after_sec, sync_exclude=[], repo_root=repo_root,
    )


def write_project(
    base: Path, rel: str, choices: tuple[str, ...] = ("train", "infer"),
    config_name: str | None = "config.example.toml", config_text: str | None = None,
) -> Path:
    """偽projectを一時ディレクトリに作る（design.md §2 の検出条件テスト用）。"""
    d = base / rel
    d.mkdir(parents=True, exist_ok=True)
    choices_src = ", ".join(f'"{c}"' for c in choices)
    (d / "main.py").write_text(
        "import argparse\n\n"
        "def main():\n"
        "    p = argparse.ArgumentParser()\n"
        f"    p.add_argument(\"--mode\", choices=[{choices_src}], required=True)\n"
        "    p.parse_args()\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8",
    )
    if config_name:
        text = config_text if config_text is not None else '[wandb]\nproject = "p"\ngroup = "g1"\n'
        (d / config_name).write_text(text, encoding="utf-8")
    return d


@pytest.fixture()
def app_factory(tmp_path: Path):
    from train_webui.backend.app import create_app

    created = []

    def _make(repo_root: Path | None = None, remote: FakeRemote | None = None,
              start_background: bool = False, poll_interval_sec: float = 0.01,
              lost_after_sec: float = 5.0, max_parallel_jobs: int = 1):
        root = repo_root or (tmp_path / "repo")
        root.mkdir(parents=True, exist_ok=True)
        settings = make_settings(tmp_path, root, poll_interval_sec, lost_after_sec)
        if max_parallel_jobs != 1:
            settings = dataclasses.replace(settings, max_parallel_jobs=max_parallel_jobs)
        fake = remote or FakeRemote()
        app = create_app(settings, remote=fake, start_background=start_background)
        created.append(app)
        return app, settings, fake

    yield _make

    for app in created:
        app.state.job_manager.shutdown()
