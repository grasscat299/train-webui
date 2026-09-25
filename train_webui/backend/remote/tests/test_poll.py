"""poll() の各ケース。"""
from __future__ import annotations

from ..controller import RemoteController
from ..errors import RemoteError
from .conftest import FakeRunner, fail, ok


def test_poll_alive(settings_factory):
    runner = FakeRunner(responses=[ok(stdout=b"ALIVE\n")])
    controller = RemoteController(settings_factory(), runner=runner)
    state = controller.poll(111, "/run/root/20260925_143012/job1")
    assert state.alive is True
    assert state.exit_code is None
    cmd = runner.calls[0]["args"][-1]
    assert "kill -0 111" in cmd
    assert "run.exit" in cmd


def test_poll_dead_with_exit_code(settings_factory):
    runner = FakeRunner(responses=[ok(stdout=b"DEAD\n0\n")])
    controller = RemoteController(settings_factory(), runner=runner)
    state = controller.poll(111, "/run/root/job1")
    assert state.alive is False
    assert state.exit_code == 0


def test_poll_dead_with_nonzero_exit_code(settings_factory):
    runner = FakeRunner(responses=[ok(stdout=b"DEAD\n1\n")])
    controller = RemoteController(settings_factory(), runner=runner)
    state = controller.poll(111, "/run/root/job1")
    assert state.alive is False
    assert state.exit_code == 1


def test_poll_dead_without_exit_file(settings_factory):
    runner = FakeRunner(responses=[ok(stdout=b"DEAD\n")])
    controller = RemoteController(settings_factory(), runner=runner)
    state = controller.poll(111, "/run/root/job1")
    assert state.alive is False
    assert state.exit_code is None


def test_poll_raises_on_ssh_failure(settings_factory):
    runner = FakeRunner(responses=[fail(returncode=255, stderr=b"ssh: connect failed")])
    controller = RemoteController(settings_factory(), runner=runner)
    try:
        controller.poll(111, "/run/root/job1")
        assert False, "RemoteError が送出されるべき"
    except RemoteError as e:
        assert "connect failed" in str(e)


def test_poll_quotes_job_dir_with_space(settings_factory):
    runner = FakeRunner(responses=[ok(stdout=b"ALIVE\n")])
    controller = RemoteController(settings_factory(), runner=runner)
    controller.poll(111, "/run root/job dir")
    cmd = runner.calls[0]["args"][-1]
    import subprocess
    proc = subprocess.run(["sh", "-n", "-c", cmd], capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
