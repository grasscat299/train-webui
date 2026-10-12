import base64

import pytest

from fastapi.testclient import TestClient

from train_webui import cli

from .conftest import write_project

T = "2026-01-01T00:00:00+09:00"


def _setup(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "src/proj_a")
    client = TestClient(app)
    runs = []
    for lr in ("1e-3", "1e-4"):
        run_id = client.post("/api/runs", json={
            "project": "src/proj_a", "group": "g1", "config_toml": f'[train]\nlr = {lr}\n[wandb]\ngroup = "g1"\n',
            "hostname": "h", "pid": 1,
        }).json()["run_id"]
        client.post("/api/results", json={"run_id": run_id, "source": "train", "items": [
            {"kind": "plot_def", "label": "epoch/loss", "plot_kind": "line", "xlabel": "epoch", "ylabel": "loss",
             "xscale": "linear", "yscale": "log", "sent_at": T},
            {"kind": "plot_points", "label": "epoch/loss", "series": "train", "xs": [1, 2, 3], "ys": [0.5, 0.2, 0.3],
             "sent_at": T},
            {"kind": "metric", "label": "train/lr", "step": 1, "value": 0.001, "sent_at": T},
            {"kind": "image", "label": "infer/xy", "image_b64": base64.b64encode(b"\x89PNG").decode(), "sent_at": T},
        ]})
        client.post(f"/api/runs/{run_id}/finish", json={"status": "finished", "error": None})
        runs.append(run_id)
    client.put("/api/groups", json={"project": "src/proj_a", "group": "g1", "group_name": "小さいモデル"})
    client.put(f"/api/runs/{runs[1]}/comment", json={"comment": "lr を 1e-4 に下げた"})
    return settings, runs


def test_ls_and_show(app_factory, capsys):
    settings, runs = _setup(app_factory)
    db = str(settings.server.db_path)

    assert cli.main(["--db", db, "ls"]) == 0
    out = capsys.readouterr().out
    assert "src/proj_a" in out and "小さいモデル  (group_id: g1)" in out and runs[0] in out

    # project はパスの末尾、group は group_name でもよい。run 省略時は最後の run（v2）
    assert cli.main(["--db", db, "show", "proj_a", "小さいモデル"]) == 0
    out = capsys.readouterr().out
    assert "/ v2" in out
    assert "-lr = 1e-3" in out and "+lr = 1e-4" in out  # 前の run からの config 差分
    assert "train: 3点" in out and "最小 (2, 0.2)" in out and "最後 (3, 0.3)" in out
    assert "train/lr" in out
    assert f"images/{runs[1]}/" in out
    assert "lr を 1e-4 に下げた" in out


def test_plot_and_compare(app_factory, capsys):
    settings, runs = _setup(app_factory)
    db = str(settings.server.db_path)

    assert cli.main(["--db", db, "plot", "proj_a", "g1", "epoch/loss", "v1"]) == 0
    out = capsys.readouterr().out
    assert "[train] 全3点" in out and "2\t0.2" in out

    assert cli.main(["--db", db, "compare", "proj_a", "g1", "g1", "--run1", "v1", "--run2", "v2"]) == 0
    out = capsys.readouterr().out
    assert "-lr = 1e-3" in out and "+lr = 1e-4" in out
    assert "epoch/loss[train]" in out


def test_unknown_names_report_candidates(app_factory, capsys):
    settings, runs = _setup(app_factory)
    db = str(settings.server.db_path)
    assert cli.main(["--db", db, "show", "proj_a", "nope"]) == 1
    assert "候補" in capsys.readouterr().err
    assert cli.main(["--db", db, "show", "proj_a", "g1", "v9"]) == 1


def test_view_reads_screen_state_and_run_ids(app_factory, capsys):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "src/proj_a")
    client = TestClient(app)
    ids = [
        client.post("/api/runs", json={
            "project": "src/proj_a", "group": "g1", "config_toml": f'[wandb]\ngroup = "g1"\n[train]\nlr = {lr}\n',
            "hostname": "h", "pid": 1,
        }).json()["run_id"]
        for lr in ("1e-3", "1e-4")
    ]
    for rid in ids:
        client.post("/api/results", json={"run_id": rid, "source": "train", "items": [
            {"kind": "metric", "label": "train/lr", "step": 1, "value": 0.001, "sent_at": T},
            {"kind": "metric", "label": "train/other", "step": 1, "value": 2.0, "sent_at": T},
        ]})
    db = str(settings.server.db_path)

    assert cli.main(["--db", db, "view"]) == 1  # まだ画面の構成が無い
    assert "WebUI をブラウザで開いてください" in capsys.readouterr().err

    assert client.put("/api/view", json={"tab": "result", "run_ids": ids, "labels": ["train/lr"]}).status_code == 204
    assert cli.main(["--db", db, "view"]) == 0
    out = capsys.readouterr().out
    assert "Resultタブ" in out and ids[0] in out and ids[1] in out
    assert "train/lr" in out and "train/other" not in out  # 表示中のラベルだけ
    assert "数値の比較" in out

    assert cli.main(["--db", db, "view", ids[0]]) == 0  # run_id を並べればその run
    assert "指定" in capsys.readouterr().out
    assert cli.main(["--db", db, "view", "nope"]) == 1


def test_comparisons_crud_and_cli(app_factory, capsys):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "src/proj_a")
    client = TestClient(app)
    rid = client.post("/api/runs", json={
        "project": "src/proj_a", "group": "g1", "config_toml": '[wandb]\ngroup = "g1"\n', "hostname": "h", "pid": 1,
    }).json()["run_id"]
    db = str(settings.server.db_path)

    assert cli.main(["--db", db, "comparisons"]) == 0
    assert "ありません" in capsys.readouterr().out

    assert client.post("/api/comparisons", json={"name": " ", "run_ids": [rid]}).status_code == 400
    cmp = client.post("/api/comparisons", json={
        "name": "lr比較", "run_ids": [rid, "gone"], "labels": ["a"], "hidden_labels": ["b"]}).json()
    assert cmp["run_ids"] == [rid, "gone"] and cmp["hidden_labels"] == ["b"]
    assert [c["name"] for c in client.get("/api/comparisons").json()] == ["lr比較"]

    renamed = client.put(f"/api/comparisons/{cmp['id']}", json={"name": "新しい名前"}).json()
    assert renamed["name"] == "新しい名前" and renamed["run_ids"] == [rid, "gone"]  # 渡した項目だけ変わる
    assert client.put("/api/comparisons/nope", json={"name": "x"}).status_code == 404

    assert cli.main(["--db", db, "comparisons"]) == 0
    assert "新しい名前" in capsys.readouterr().out
    assert cli.main(["--db", db, "view", "--comparison", "新しい名前"]) == 0
    out = capsys.readouterr().out
    assert "保存した比較「新しい名前」" in out and rid in out and "gone は削除されています" in out
    assert cli.main(["--db", db, "view", "--comparison", "nope"]) == 1

    assert client.delete(f"/api/comparisons/{cmp['id']}").status_code == 204
    assert client.get("/api/comparisons").json() == []


def test_group_description_api_cli_and_ctl(app_factory, capsys):
    import argparse

    from train_webui import ctl
    from .test_ctl import _FakeApi

    app, settings, remote = app_factory()
    write_project(settings.repo_root, "src/proj_a")
    client = TestClient(app)
    db = str(settings.server.db_path)
    ids = [
        client.post("/api/runs", json={
            "project": "src/proj_a", "group": "g1", "config_toml": '[wandb]\ngroup = "g1"\n',
            "hostname": "h", "pid": 1,
        }).json()["run_id"]
        for _ in range(2)
    ]
    assert cli.main(["--db", db, "ls"]) == 0
    assert "[モデルの説明なし]" in capsys.readouterr().out
    assert cli.main(["--db", db, "show", "proj_a", "g1"]) == 0
    assert "未記入" in capsys.readouterr().out

    assert client.get(f"/api/runs/{ids[0]}").json()["group_description"] == ""
    assert client.put("/api/groups/description", json={"project": "src/proj_a", "group": "nope", "description": "x"}).status_code == 404

    api = _FakeApi(client)
    ctl.cmd_describe(api, argparse.Namespace(project="proj_a", group="g1", text="# 概要\nDiT 6層", file=None))
    assert client.get(f"/api/runs/{ids[1]}").json()["group_description"] == "# 概要\nDiT 6層"
    with pytest.raises(ctl.CtlError):
        ctl.cmd_describe(api, argparse.Namespace(project="proj_a", group="zzz", text="x", file=None))

    assert cli.main(["--db", db, "ls"]) == 0
    assert "[モデルの説明なし]" not in capsys.readouterr().out
    assert cli.main(["--db", db, "show", "proj_a", "g1"]) == 0
    assert "DiT 6層" in capsys.readouterr().out
    # 同じ group の run を並べても、説明は1回だけ出る
    assert cli.main(["--db", db, "view", *ids]) == 0
    assert capsys.readouterr().out.count("DiT 6層") == 1
