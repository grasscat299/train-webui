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
    runner = FakeRunner(responses=[ok(), ok(), ok(stdout=b"12345\n")])
    controller = RemoteController(settings_factory(), runner=runner)
    spec = LaunchSpec(
        run_id="20260925_143012", job_id="20260925_143012__train__143012",
        kind="train", project_dir="src/world_model_v1", config_toml="[wandb]\ngroup='g'\n",
    )
    result = controller.launch(spec)

    assert result.pid == 12345
    assert result.job_dir == "/home/user/train_webui_runs/20260925_143012/20260925_143012__train__143012"
    assert len(runner.calls) == 3  # config 書き込み・run 保管場所への config 複製・起動

    # 1回目: config.toml の書き込み
    write_call = runner.calls[0]
    assert write_call["input"] == b"[wandb]\ngroup='g'\n"
    write_cmd = write_call["args"][-1]
    assert "cat > " in write_cmd
    assert "/home/user/td_plan/src/world_model_v1/config.toml" in write_cmd

    # 2回目: 起動コマンド
    launch_call = runner.calls[2]
    launch_cmd = launch_call["args"][-1]
    assert "setsid nohup sh -c" in launch_cmd
    assert "mkdir -p" in launch_cmd
    assert result.job_dir in launch_cmd
    assert "TRAIN_WEBUI_RUN_ID='20260925_143012'" in launch_cmd or "TRAIN_WEBUI_RUN_ID=20260925_143012" in launch_cmd
    assert "TRAIN_WEBUI_PROJECT=" in launch_cmd
    assert "TRAIN_WEBUI_SOURCE=" in launch_cmd
    assert "TRAIN_WEBUI_URL=" in launch_cmd
    assert "singularity" not in launch_cmd
    assert "python3 -m src.world_model_v1.main --mode train" in launch_cmd
    assert "run.pid" in launch_cmd
    assert "cat " in launch_cmd

    # 全体・内側スクリプトともに構文が壊れていないことを sh -n で確認する
    _assert_valid_sh_syntax(launch_cmd)
    _assert_valid_sh_syntax(write_cmd)


def test_launch_quotes_paths_with_special_characters(settings_factory):
    runner = FakeRunner(responses=[ok(), ok(), ok(stdout=b"999\n")])
    settings = settings_factory()
    # repo_dir/python/run_root にスペースやシングルクォートを含めて壊れないことを確認する
    from ...settings import RemoteSettings
    tricky_remote = RemoteSettings(
        host=settings.remote.host, user=settings.remote.user, port=settings.remote.port,
        key_path=settings.remote.key_path,
        repo_dir="/home/g rass/auto ark's repo",
        python="/opt/py thon/it's/python3",
        run_root="/home/g rass/run root",
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

    launch_cmd = runner.calls[2]["args"][-1]
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


def test_launch_isolates_code_per_job_when_parallel(settings_factory):
    import dataclasses

    runner = FakeRunner(responses=[ok(), ok(), ok(), ok(stdout=b"777\n")])
    settings = dataclasses.replace(settings_factory(), max_parallel_jobs=2)
    spec = LaunchSpec(run_id="r1", job_id="r1__train__1", kind="train", project_dir="src/world_model_v2",
                      config_toml="x=1\n")
    result = RemoteController(settings, runner=runner).launch(spec)
    job_dir = result.job_dir
    snap, write, snap_cfg, launch = (c["args"][-1] for c in runner.calls)
    assert f"cp -a /home/user/td_plan {job_dir}/code" in snap
    # config は共有の repo_dir ではなく、複製したコードの中に書く
    assert f"cat > {job_dir}/code/src/world_model_v2/config.toml" in write
    assert f"cd {job_dir}/code &&" in launch
    assert result.pid == 777


def test_checkpoint_relay_commands_and_safe_ids():
    """read_checkpoint は元の台で tar c、write_checkpoint は送り先で tar x（標準入力で渡す）。run_id は検査する。"""
    import pytest

    from ..controller import RemoteController
    from ..errors import RemoteError
    from .conftest import FakeRunner, make_settings, ok
    from pathlib import Path

    settings = make_settings(Path("/tmp/repo"))
    runner = FakeRunner([ok(stdout=b"TARBYTES"), ok()])
    rc = RemoteController(settings, runner=runner)
    assert rc.read_checkpoint("20261016_190753") == b"TARBYTES"
    cmd = runner.calls[0]["args"][-1]
    assert "tar c artifacts/*.pt" in cmd and "/home/user/train_webui_runs/20261016_190753" in cmd
    rc.write_checkpoint("20261016_190753", b"TARBYTES")
    w = runner.calls[1]
    assert "tar x -C" in w["args"][-1] and w["input"] == b"TARBYTES"
    for bad in ("../x", "a b", ""):
        with pytest.raises(RemoteError):
            rc.read_checkpoint(bad)
        with pytest.raises(RemoteError):
            rc.write_checkpoint(bad, b"")
