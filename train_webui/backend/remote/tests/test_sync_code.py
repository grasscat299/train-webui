"""sync_code() のexclude・untracked JSON化・git操作。"""
from __future__ import annotations

import json

from ..controller import RemoteController
from ..errors import RemoteError
from .conftest import FakeRunner, fail, ok


def _git_responses(commit="abc123\n", diff="diff --git a b\n", untracked_list=""):
    return [ok(stdout=commit.encode()), ok(stdout=diff.encode()), ok(stdout=untracked_list.encode())]


def test_sync_code_runs_git_then_rsync_with_excludes(settings_factory, tmp_path):
    responses = _git_responses() + [ok()]  # 最後がrsync
    runner = FakeRunner(responses=responses)
    settings = settings_factory(sync_exclude=[".git/", "venv/", "wandb/"])
    controller = RemoteController(settings, runner=runner)

    info = controller.sync_code()

    assert info.git_commit == "abc123"
    assert info.git_diff == "diff --git a b\n"
    assert json.loads(info.git_untracked) == {}

    calls = runner.calls
    assert calls[0]["args"][:3] == ["git", "-C", str(tmp_path)]
    assert calls[0]["args"][3:] == ["rev-parse", "HEAD"]
    assert calls[1]["args"][3:] == ["diff", "HEAD"]
    assert calls[2]["args"][3:] == ["ls-files", "--others", "--exclude-standard"]

    rsync_args = calls[3]["args"]
    assert rsync_args[0] == "rsync"
    assert "-az" in rsync_args
    assert "--delete" in rsync_args
    assert "-e" in rsync_args
    ssh_e = rsync_args[rsync_args.index("-e") + 1]
    assert ssh_e.startswith("ssh ")
    assert "-p 22" in ssh_e
    exclude_values = [rsync_args[i + 1] for i, a in enumerate(rsync_args) if a == "--exclude"]
    assert exclude_values == [".git/", "venv/", "wandb/"]
    assert rsync_args[-2] == f"{tmp_path}/"
    assert rsync_args[-1] == "user@gpu-host.example:/home/user/td_plan/"


def test_sync_code_collects_small_text_untracked_files(settings_factory, tmp_path):
    (tmp_path / "new_file.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "note.txt").write_text("メモ\n", encoding="utf-8")

    untracked_list = "new_file.py\nsub/note.txt\n"
    responses = _git_responses(untracked_list=untracked_list) + [ok()]
    controller = RemoteController(settings_factory(), runner=FakeRunner(responses=responses))

    info = controller.sync_code()
    data = json.loads(info.git_untracked)
    assert data == {"new_file.py": "print('hi')\n", "sub/note.txt": "メモ\n"}


def test_sync_code_skips_large_and_binary_untracked_files(settings_factory, tmp_path):
    big = tmp_path / "big.txt"
    big.write_bytes(b"a" * (1024 * 1024 + 1))
    binary = tmp_path / "image.png"
    binary.write_bytes(b"\x89PNG\x00\x01\x02binarydata")
    small = tmp_path / "small.txt"
    small.write_text("ok\n", encoding="utf-8")

    untracked_list = "big.txt\nimage.png\nsmall.txt\n"
    responses = _git_responses(untracked_list=untracked_list) + [ok()]
    controller = RemoteController(settings_factory(), runner=FakeRunner(responses=responses))

    info = controller.sync_code()
    data = json.loads(info.git_untracked)
    assert data == {"small.txt": "ok\n"}


def test_sync_code_raises_on_rsync_failure(settings_factory):
    responses = _git_responses() + [fail(returncode=23, stderr=b"rsync error: partial transfer")]
    controller = RemoteController(settings_factory(), runner=FakeRunner(responses=responses))
    try:
        controller.sync_code()
        assert False, "RemoteError が送出されるべき"
    except RemoteError as e:
        assert "partial transfer" in str(e)


def test_sync_code_raises_on_git_failure(settings_factory):
    responses = [fail(returncode=128, stderr=b"not a git repository")]
    controller = RemoteController(settings_factory(), runner=FakeRunner(responses=responses))
    try:
        controller.sync_code()
        assert False, "RemoteError が送出されるべき"
    except RemoteError as e:
        assert "not a git repository" in str(e)
