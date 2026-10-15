import pytest
from fastapi.testclient import TestClient

from train_webui import ctl
from train_webui.backend.toml_utils import TomlValidationError, set_value

from .conftest import write_project

CONFIG = '''lr = 1e-3
[wandb]
project = "p"  # 名前
group = "g1"
[train]
lr = 3e-4  # 学習率
tags = ["a", "b"]
name = "x # y"
'''


def test_set_value_replaces_only_target_line():
    out = set_value(CONFIG, "train.lr", "1e-2")
    assert "lr = 1e-2  # 学習率" in out
    assert "lr = 1e-3\n" in out  # トップレベルの lr は別物
    assert set_value(CONFIG, "lr", "5").startswith("lr = 5\n")


def test_set_value_string_and_array():
    assert 'name = "z"' in set_value(CONFIG, "train.name", '"z"')
    assert 'tags = ["c"]' in set_value(CONFIG, "train.tags", '["c"]')


def test_set_value_errors():
    with pytest.raises(TomlValidationError):
        set_value(CONFIG, "train.missing", "1")  # 新規キーは追加しない
    with pytest.raises(TomlValidationError):
        set_value(CONFIG, "train.lr", "abc")  # 引用符なしの文字列


class _FakeApi(ctl.Api):
    def __init__(self, client):
        self.client = client

    def call(self, method, path, body=None):
        res = self.client.request(method, path, json=body)
        if res.status_code >= 400:
            raise ctl.CtlError(f"{res.status_code}: {res.json().get('detail')}")
        return res.json() if res.content else None


def test_draft_set_comment_flow(app_factory, capsys):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    api = _FakeApi(TestClient(app))
    run = api.call("POST", "/api/runs/draft", {"project": "proj_a", "group": "g1", "new_group": True})
    run_id = run["run_id"]
    old = run["config_toml"]
    assert "group" in old

    import argparse
    ctl.cmd_comment(api, argparse.Namespace(run_id=run_id, text="試す"))
    assert api.call("GET", f"/api/runs/{run_id}")["comment"] == "試す"

    with pytest.raises(ctl.CtlError):
        ctl.cmd_set(api, argparse.Namespace(run_id=run_id, assignments=["nope.key=1"]))
    with pytest.raises(ctl.CtlError):
        ctl.cmd_set(api, argparse.Namespace(run_id=run_id, assignments=["no_equal"]))


def test_set_add_creates_missing_key_only_with_flag(app_factory):
    import argparse

    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    api = _FakeApi(TestClient(app))
    run_id = api.call("POST", "/api/runs/draft", {"project": "proj_a", "group": "g1", "new_group": True})["run_id"]
    with pytest.raises(ctl.CtlError):  # 既定では新しいキーを足せない（タイプミスの防止）
        ctl.cmd_set(api, argparse.Namespace(run_id=run_id, assignments=["wandb.newkey=1"]))
    ctl.cmd_set(api, argparse.Namespace(run_id=run_id, assignments=["wandb.newkey=1"], add=True))
    assert "newkey = 1" in api.call("GET", f"/api/runs/{run_id}")["config_toml"]


def test_machines_and_start_with_machine(app_factory, capsys):
    import argparse

    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    api = _FakeApi(TestClient(app))
    ctl.cmd_machines(api, argparse.Namespace())
    out = capsys.readouterr().out
    assert "OK" in out and "default" in out and "自動選択される台: default" in out
    run_id = api.call("POST", "/api/runs/draft", {"project": "proj_a", "group": "g1", "new_group": True})["run_id"]
    with pytest.raises(ctl.CtlError, match="未登録のマシン"):
        ctl.cmd_start(api, argparse.Namespace(run_id=run_id, machine="zzz"))
    ctl.cmd_start(api, argparse.Namespace(run_id=run_id, machine="default"))
    assert "@default" in capsys.readouterr().out
