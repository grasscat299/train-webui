"""ssh を「ローカルで sh -c を実行する偽runner」に差し替えた結合テスト。

settings.remote.python を、引数を無視して決まった動きをするスタブスクリプトに差し替える。
外部ホストへの接続は一切行わない。
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import replace
from pathlib import Path

from ..controller import RemoteController
from ..types import LaunchSpec
from ...settings import RemoteSettings

_STUB_FINISH_QUICKLY = """#!/bin/sh
for i in 1 2 3; do
  echo "tick $i"
  sleep 0.05
done
exit 7
"""

_STUB_IGNORES_TERM = """#!/bin/sh
trap '' TERM
i=0
while [ $i -lt 200 ]; do
  echo "tick $i"
  sleep 0.05
  i=$((i + 1))
done
"""


class LocalShellRunner:
    """ssh呼び出しをローカルの sh -c 実行に差し替える偽 CommandRunner。"""

    def run(self, args, *, input=None, timeout=None):
        if args and args[0] == "ssh":
            return subprocess.run(["sh", "-c", args[-1]], input=input, capture_output=True, timeout=timeout)
        return subprocess.run(args, input=input, capture_output=True, timeout=timeout)


def _write_python_stub(tmp_path: Path, script_body: str) -> str:
    stub = tmp_path / "bin" / "fake-python"
    stub.parent.mkdir(exist_ok=True)
    stub.write_text(script_body)
    stub.chmod(0o755)
    return str(stub)


def _prepare(settings_factory, tmp_path, script_body, stop_timeout_sec: float = 10.0):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "proj").mkdir()
    run_root = tmp_path / "runs"

    settings = settings_factory(stop_timeout_sec=stop_timeout_sec)
    remote = replace(
        settings.remote,
        repo_dir=str(repo_dir),
        run_root=str(run_root),
        python=_write_python_stub(tmp_path, script_body),
    )
    settings = replace(settings, remote=remote)
    controller = RemoteController(settings, runner=LocalShellRunner())
    return controller, repo_dir, run_root


def test_integration_launch_runs_to_completion_and_reports_exit_code(settings_factory, tmp_path):
    controller, repo_dir, _ = _prepare(settings_factory, tmp_path, _STUB_FINISH_QUICKLY)
    spec = LaunchSpec(
        run_id="20260925_000000", job_id="20260925_000000__train__000000",
        kind="train", project_dir="proj", config_toml="[wandb]\ngroup='g'\n",
    )

    result = controller.launch(spec)

    assert (repo_dir / "proj" / "config.toml").read_text() == "[wandb]\ngroup='g'\n"
    job_dir = Path(result.job_dir)
    assert job_dir.exists()
    assert job_dir.joinpath("run.pid").read_text().strip() == str(result.pid)

    deadline = time.time() + 5.0
    state = controller.poll(result.pid, result.job_dir)
    while state.alive and time.time() < deadline:
        time.sleep(0.05)
        state = controller.poll(result.pid, result.job_dir)

    assert state.alive is False
    assert state.exit_code == 7

    data, offset = controller.read_log(result.job_dir, 0)
    assert b"tick 1" in data
    assert offset == len(data)
    more, offset2 = controller.read_log(result.job_dir, offset)
    assert more == b""
    assert offset2 == offset


def test_integration_stop_terminates_running_job(settings_factory, tmp_path):
    controller, repo_dir, _ = _prepare(settings_factory, tmp_path, _STUB_FINISH_QUICKLY, stop_timeout_sec=2.0)
    spec = LaunchSpec(
        run_id="20260925_000001", job_id="20260925_000001__train__000001",
        kind="train", project_dir="proj", config_toml="x = 1\n",
    )
    result = controller.launch(spec)

    time.sleep(0.1)
    state = controller.poll(result.pid, result.job_dir)
    assert state.alive is True

    controller.stop(result.pid)

    state = controller.poll(result.pid, result.job_dir)
    assert state.alive is False


def test_integration_stop_escalates_to_kill_for_term_resistant_job(settings_factory, tmp_path):
    controller, repo_dir, _ = _prepare(settings_factory, tmp_path, _STUB_IGNORES_TERM, stop_timeout_sec=0.3)
    spec = LaunchSpec(
        run_id="20260925_000002", job_id="20260925_000002__train__000002",
        kind="train", project_dir="proj", config_toml="x = 1\n",
    )
    result = controller.launch(spec)

    time.sleep(0.1)
    state = controller.poll(result.pid, result.job_dir)
    assert state.alive is True

    controller.stop(result.pid)

    state = controller.poll(result.pid, result.job_dir)
    assert state.alive is False
