"""RemoteError への変換（非0終了・タイムアウト・stderr末尾切り詰め）。"""
from __future__ import annotations

import subprocess

from ..controller import RemoteController
from ..errors import RemoteError
from .conftest import FakeRunner, fail


class _TimeoutRunner:
    def __init__(self):
        self.calls = []

    def run(self, args, *, input=None, timeout=None):
        self.calls.append(args)
        raise subprocess.TimeoutExpired(cmd=args, timeout=timeout)


def test_timeout_is_converted_to_remote_error(settings_factory):
    controller = RemoteController(settings_factory(), runner=_TimeoutRunner())
    try:
        controller.poll(1, "/x")
        assert False, "RemoteError が送出されるべき"
    except RemoteError as e:
        assert "タイムアウト" in str(e)


def test_stderr_tail_is_included_and_truncated(settings_factory):
    long_stderr = ("x" * 5000 + "END_MARKER").encode()
    runner = FakeRunner(responses=[fail(returncode=1, stderr=long_stderr)])
    controller = RemoteController(settings_factory(), runner=runner)
    try:
        controller.poll(1, "/x")
        assert False, "RemoteError が送出されるべき"
    except RemoteError as e:
        msg = str(e)
        assert "END_MARKER" in msg  # 末尾は残る
        assert len(msg) < len(long_stderr.decode())  # 全部は含めず切り詰めている
