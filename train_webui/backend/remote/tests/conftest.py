"""RemoteController のテスト共通フィクスチャ。"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ...settings import RemoteSettings, ServerSettings, Settings


def make_settings(repo_root: Path, **overrides) -> Settings:
    remote = RemoteSettings(
        host="gpu-host.example",
        user="user",
        port=22,
        key_path="~/.ssh/id_ed25519",
        repo_dir="/home/user/td_plan",
        python="python3",
        run_root="/home/user/train_webui_runs",
        stop_timeout_sec=overrides.pop("stop_timeout_sec", 10.0),
    )
    server = ServerSettings(
        host="0.0.0.0",
        port=8000,
        public_url="http://100.1.2.3:8000",
        db_path=repo_root / "train_webui" / "data" / "webui.sqlite",
        media_dir=repo_root / "train_webui" / "data" / "media",
    )
    return Settings(
        remote=remote,
        server=server,
        poll_interval_sec=1.5,
        lost_after_sec=180.0,
        sync_exclude=overrides.pop("sync_exclude", [".git/", "venv/", "__pycache__/"]),
        repo_root=repo_root,
        **overrides,
    )


class FakeRunner:
    """呼ばれた args/stdin を記録し、あらかじめ用意した返り値を順に返す偽 CommandRunner。"""

    def __init__(self, responses: list[subprocess.CompletedProcess] | None = None) -> None:
        self.calls: list[dict] = []
        self._responses = list(responses or [])
        self._default = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")

    def run(self, args, *, input=None, timeout=None):
        self.calls.append({"args": list(args), "input": input, "timeout": timeout})
        if self._responses:
            return self._responses.pop(0)
        return self._default


def ok(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def fail(returncode: int = 1, stderr: bytes = b"error") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=b"", stderr=stderr)


@pytest.fixture
def settings_factory(tmp_path):
    def _factory(**overrides):
        return make_settings(tmp_path, **overrides)
    return _factory
