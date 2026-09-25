"""read_log() のoffset計算・欠損ファイル対応。"""
from __future__ import annotations

from ..controller import RemoteController
from ..errors import RemoteError
from .conftest import FakeRunner, fail, ok


def test_read_log_returns_data_and_new_offset(settings_factory):
    runner = FakeRunner(responses=[ok(stdout=b"hello")])
    controller = RemoteController(settings_factory(), runner=runner)
    data, new_offset = controller.read_log("/run/root/job1", 0)
    assert data == b"hello"
    assert new_offset == 5
    cmd = runner.calls[0]["args"][-1]
    assert "tail -c +1 " in cmd
    assert "run.log" in cmd


def test_read_log_uses_offset_plus_one(settings_factory):
    runner = FakeRunner(responses=[ok(stdout=b" world")])
    controller = RemoteController(settings_factory(), runner=runner)
    data, new_offset = controller.read_log("/run/root/job1", 5)
    assert data == b" world"
    assert new_offset == 11
    cmd = runner.calls[0]["args"][-1]
    assert "tail -c +6 " in cmd


def test_read_log_missing_file_returns_empty(settings_factory):
    # test -f が失敗する状況を模す（|| true があるため呼び出し自体は成功扱い）
    runner = FakeRunner(responses=[ok(stdout=b"")])
    controller = RemoteController(settings_factory(), runner=runner)
    data, new_offset = controller.read_log("/run/root/job1", 3)
    assert data == b""
    assert new_offset == 3
    cmd = runner.calls[0]["args"][-1]
    assert "|| true" in cmd


def test_read_log_raises_on_ssh_failure(settings_factory):
    runner = FakeRunner(responses=[fail(returncode=255, stderr=b"connection lost")])
    controller = RemoteController(settings_factory(), runner=runner)
    try:
        controller.read_log("/run/root/job1", 0)
        assert False, "RemoteError が送出されるべき"
    except RemoteError as e:
        assert "connection lost" in str(e)
