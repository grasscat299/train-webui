"""launch() の組み立て・PIDパース・クォートの検査。"""
from __future__ import annotations

import subprocess

from ..controller import RemoteController
from ..errors import RemoteError
from ..types import LaunchSpec
from .conftest import FakeRunner, ok


def test_ssh_base_args_expand_tilde_and_options(settings_factory):
    controller = RemoteController(settings_factory(), runner=FakeRunner())
    args = controller._ssh_base_args()
    assert args[0] == "ssh"
    assert "-p" in args and args[args.index("-p") + 1] == "22"
    assert "-i" in args
    key = args[args.index("-i") + 1]
    assert not key.startswith("~")  # ~ が展開されている
    assert "-o" in args
    assert "BatchMode=yes" in args
    assert "ConnectTimeout=10" in args
    assert args[-1] == "user@gpu-host.example"


def test_launch_sends_config_then_launch_command(settings_factory):
    runner = FakeRunner(responses=[ok(), ok(stdout=b"12345\n")])
    controller = RemoteController(settings_factory(), runner=runner)
    spec = LaunchSpec(
        run_id="20260925_143012", job_id="20260925_143012__train__143012",
        kind="train", project_dir="src/world_model_v1", config_toml="[wandb]\ngroup='g'\n",
    )
    result = controller.launch(spec)

    assert result.pid == 12345
    assert result.job_dir == "/home/user/train_webui_runs/20260925_143012/20260925_143012__train__143012"
    assert len(runner.calls) == 2

    # 1回目: config.toml の書き込み
    write_call = runner.calls[0]
    assert write_call["input"] == b"[wandb]\ngroup='g'\n"
    write_cmd = write_call["args"][-1]
    assert "cat > " in write_cmd
    assert "/home/user/td_plan/src/world_model_v1/config.toml" in write_cmd

    # 2回目: 起動コマンド
    launch_call = runner.calls[1]
    launch_cmd = launch_call["args"][-1]
    assert "setsid nohup sh -c" in launch_cmd
    assert "mkdir -p" in launch_cmd
    assert result.job_dir in launch_cmd
    assert "TRAIN_WEBUI_RUN_ID='20260925_143012'" in launch_cmd or "TRAIN_WEBUI_RUN_ID=20260925_143012" in launch_cmd
    assert "TRAIN_WEBUI_PROJECT=" in launch_cmd
    assert "TRAIN_WEBUI_SOURCE=" in launch_cmd
    assert "TRAIN_WEBUI_URL=" in launch_cmd
    assert "singularity exec" in launch_cmd
    assert "-m src.world_model_v1.main --mode train" in launch_cmd
    assert "run.pid" in launch_cmd
    assert "cat " in launch_cmd

    # 全体・内側スクリプトともに構文が壊れていないことを sh -n で確認する
    _assert_valid_sh_syntax(launch_cmd)
    _assert_valid_sh_syntax(write_cmd)


def test_launch_quotes_paths_with_special_characters(settings_factory):
    runner = FakeRunner(responses=[ok(), ok(stdout=b"999\n")])
    settings = settings_factory()
    # repo_dir/sif_path/singularity_args にスペースやシングルクォートを含めて壊れないことを確認する
    from ...settings import RemoteSettings
    tricky_remote = RemoteSettings(
        host=settings.remote.host, user=settings.remote.user, port=settings.remote.port,
        key_path=settings.remote.key_path,
        repo_dir="/home/g rass/auto ark's repo",
        sif_path="/path/with space/it's.sif",
        python="venv/bin/python",
        run_root="/home/g rass/run root",
        singularity_args=["--nv", "--bind", "/data with space:/data"],
        stop_timeout_sec=settings.remote.stop_timeout_sec,
    )
    from dataclasses import replace
    settings = replace(settings, remote=tricky_remote)

    controller = RemoteController(settings, runner=runner)
    spec = LaunchSpec(
        run_id="20260925_143012", job_id="20260925_143012__train__143012",
        kind="train", project_dir="src/world_model_v1", config_toml="x = 1\n",
    )
    result = controller.launch(spec)
    assert result.pid == 999

    launch_cmd = runner.calls[1]["args"][-1]
    write_cmd = runner.calls[0]["args"][-1]
    _assert_valid_sh_syntax(launch_cmd)
    _assert_valid_sh_syntax(write_cmd)


def test_launch_raises_remote_error_on_ssh_failure(settings_factory):
    runner = FakeRunner(responses=[subprocess.CompletedProcess(args=[], returncode=255, stdout=b"", stderr=b"Connection refused")])
    controller = RemoteController(settings_factory(), runner=runner)
    spec = LaunchSpec(run_id="r", job_id="r__train__1", kind="train", project_dir="src/p", config_toml="")
    try:
        controller.launch(spec)
        assert False, "RemoteError が送出されるべき"
    except RemoteError as e:
        assert "Connection refused" in str(e)


def test_launch_raises_remote_error_when_pid_unparseable(settings_factory):
    runner = FakeRunner(responses=[ok(), ok(stdout=b"not-a-pid\n")])
    controller = RemoteController(settings_factory(), runner=runner)
    spec = LaunchSpec(run_id="r", job_id="r__train__1", kind="train", project_dir="src/p", config_toml="")
    try:
        controller.launch(spec)
        assert False, "RemoteError が送出されるべき"
    except RemoteError:
        pass


def _assert_valid_sh_syntax(command: str) -> None:
    proc = subprocess.run(["sh", "-n", "-c", command], capture_output=True)
    assert proc.returncode == 0, f"sh -n が失敗: {proc.stderr.decode(errors='replace')}\ncommand={command!r}"
