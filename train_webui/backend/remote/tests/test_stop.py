"""stop() の TERM→KILL フロー。"""
from __future__ import annotations

from ..controller import RemoteController
from .conftest import FakeRunner, ok


def _make_controller(settings_factory, stop_timeout_sec, responses):
    controller = RemoteController(settings_factory(stop_timeout_sec=stop_timeout_sec), runner=FakeRunner(responses=responses))
    controller._sleep = lambda _s: None  # テストでは実待機しない
    return controller


def test_stop_sends_term_only_when_process_dies_quickly(settings_factory):
    # TERM送信 → kill -0 が非0（死んでいる）を返せば即終了、KILLは送らない
    responses = [ok(), ok(returncode=1)]
    controller = _make_controller(settings_factory, 5.0, responses)
    controller.stop(4242)

    calls = controller._runner.calls
    assert len(calls) == 2
    assert "kill -TERM -4242" in calls[0]["args"][-1]
    assert "kill -0 4242" in calls[1]["args"][-1]


def test_stop_escalates_to_kill_when_process_survives_timeout(settings_factory):
    # TERM送信 → kill -0 が「生きている」(0) を返し続ける → タイムアウトでKILL送信
    responses = [ok()]  # TERM
    responses += [ok(returncode=0) for _ in range(5)]  # kill -0 は常に生存
    responses += [ok()]  # KILL（多めに用意しておく）
    controller = _make_controller(settings_factory, 0.05, responses)
    controller.stop(999)

    calls = controller._runner.calls
    assert "kill -TERM -999" in calls[0]["args"][-1]
    assert any("kill -0 999" in c["args"][-1] for c in calls[1:-1])
    assert "kill -KILL -999" in calls[-1]["args"][-1]


def test_kill_command_suppresses_missing_process_error():
    # kill -TERM/-KILL は「; true」でプロセス不在時の非0終了を握りつぶす形にする
    settings = _dummy_settings()
    controller = RemoteController(settings, runner=FakeRunner(responses=[ok()]))
    controller._kill(123, "TERM")
    cmd = controller._runner.calls[0]["args"][-1]
    assert cmd.endswith("true")
    assert "kill -TERM -123" in cmd


def _dummy_settings():
    from pathlib import Path
    from .conftest import make_settings
    return make_settings(Path("/tmp/dummy_repo_for_test"))
