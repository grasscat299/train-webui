import re
import sqlite3

from fastapi.testclient import TestClient

from train_webui.backend.db import Database

from .conftest import write_project


def _groups(client, project="proj_a"):
    tree = client.get("/api/tree").json()
    return next(p for p in tree if p["project"] == project)["groups"]


def test_new_group_gets_auto_id_and_name(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)

    r1 = client.post("/api/runs/draft", json={"project": "proj_a", "new_group": True}).json()
    r2 = client.post("/api/runs/draft", json={"project": "proj_a", "new_group": True}).json()
    assert re.fullmatch(r"g\d{8}_\d{6}(_\d+)?", r1["group"])
    assert r1["group"] != r2["group"]
    assert (r1["group_name"], r2["group_name"]) == ("group1", "group2")
    # config の [wandb].group には group_id が入る（checkpoint 名 model_{group_id}.pt に使われる）
    assert f'group = "{r1["group"]}"' in r1["config_toml"]
    assert {(g["group"], g["group_name"]) for g in _groups(client)} == {
        (r1["group"], "group1"), (r2["group"], "group2"),
    }


def test_rename_group_changes_only_name(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    run = client.post("/api/runs/draft", json={"project": "proj_a", "new_group": True}).json()

    resp = client.put("/api/groups", json={"project": "proj_a", "group": run["group"], "group_name": " 大きいモデル "})
    assert resp.status_code == 200 and resp.json()["group_name"] == "大きいモデル"
    detail = client.get(f"/api/runs/{run['run_id']}").json()
    assert detail["group"] == run["group"] and detail["group_name"] == "大きいモデル"
    assert f'group = "{run["group"]}"' in detail["config_toml"]

    # 同じ group に追加した draft も同じ group_name
    run2 = client.post("/api/runs/draft", json={"project": "proj_a", "group": run["group"], "new_group": False}).json()
    assert run2["group_name"] == "大きいモデル"

    assert client.put("/api/groups", json={"project": "proj_a", "group": run["group"], "group_name": "  "}).status_code == 400
    assert client.put("/api/groups", json={"project": "proj_a", "group": "nope", "group_name": "x"}).status_code == 404


def test_cli_run_registers_group_with_id_as_name(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    client.post("/api/runs", json={"project": "proj_a", "group": "g_cli", "config_toml": '[wandb]\ngroup = "g_cli"\n',
                                   "hostname": "h", "pid": 1})
    assert [(g["group"], g["group_name"]) for g in _groups(client)] == [("g_cli", "g_cli")]


def test_groups_backfilled_from_existing_runs(tmp_path):
    path = tmp_path / "old.sqlite"
    db = Database(path)
    db.init_schema()
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE groups")
    conn.execute(
        "INSERT INTO runs (run_id, project, run_group, display_name, origin, status, config_toml, created_at) "
        "VALUES ('a', 'p', 'wm_v1', 'v1', 'webui', 'finished', '', '2026-01-01')"
    )
    conn.commit()
    conn.close()
    db.init_schema()
    assert db.group_names("p") == {"wm_v1": "wm_v1"}
