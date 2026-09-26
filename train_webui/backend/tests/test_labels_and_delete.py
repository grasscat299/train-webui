import base64

from fastapi.testclient import TestClient

from .conftest import write_project


def _create_cli_run(client, project="proj_a", group="g1", pid=1):
    return client.post("/api/runs", json={
        "project": project, "group": group, "config_toml": '[wandb]\ngroup = "g1"\n',
        "hostname": "h", "pid": pid,
    }).json()["run_id"]


def test_labels_union_sorted(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    r1 = _create_cli_run(client, pid=1)
    r2 = _create_cli_run(client, pid=2)
    client.post("/api/results", json={"run_id": r1, "source": "s", "items": [
        {"kind": "metric", "label": "train/loss", "step": 1, "value": 1.0, "sent_at": "2026-01-01T00:00:00+09:00"},
    ]})
    client.post("/api/results", json={"run_id": r2, "source": "s", "items": [
        {"kind": "metric", "label": "val/loss", "step": 1, "value": 1.0, "sent_at": "2026-01-01T00:00:00+09:00"},
    ]})
    resp = client.get("/api/labels", params={"run_ids": f"{r1},{r2}"})
    assert resp.status_code == 200
    labels = [item["label"] for item in resp.json()]
    assert labels == ["train/loss", "val/loss"]


def test_labels_empty_run_ids(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    resp = client.get("/api/labels", params={"run_ids": ""})
    assert resp.status_code == 200
    assert resp.json() == []


def test_delete_draft_removes_row(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    run = client.post("/api/runs/draft", json={"project": "proj_a", "group": "g1", "new_group": True}).json()
    resp = client.delete(f"/api/runs/{run['run_id']}")
    assert resp.status_code == 204
    assert client.get(f"/api/runs/{run['run_id']}").status_code == 404


def test_delete_running_409(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    run = client.post("/api/runs/draft", json={"project": "proj_a", "group": "g1", "new_group": True}).json()
    client.post(f"/api/runs/{run['run_id']}/start")
    resp = client.delete(f"/api/runs/{run['run_id']}")
    assert resp.status_code == 409


def test_delete_unknown_404(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    resp = client.delete("/api/runs/no_such_run")
    assert resp.status_code == 404


def test_delete_removes_image_and_log_files(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run_id = _create_cli_run(client)
    png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
    client.post("/api/results", json={"run_id": run_id, "source": "s", "items": [
        {"kind": "image", "label": "im", "image_b64": png_b64, "sent_at": "2026-01-01T00:00:00+09:00"},
    ]})
    image_dir = settings.server.media_dir / "images" / run_id
    assert image_dir.is_dir()
    client.post(f"/api/runs/{run_id}/finish", json={"status": "finished", "error": None})

    resp = client.delete(f"/api/runs/{run_id}")
    assert resp.status_code == 204
    assert not image_dir.exists()


def test_delete_finished_webui_run_removes_job_log(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    run = client.post("/api/runs/draft", json={"project": "proj_a", "group": "g1", "new_group": True}).json()
    client.post(f"/api/runs/{run['run_id']}/start")
    job_id = client.get(f"/api/runs/{run['run_id']}").json()["jobs"][0]["job_id"]
    pid = next(iter(remote.poll_results))
    job_dir = app.state.db.get_job(job_id)["job_dir"]
    remote.append_log(job_dir, b"log line\n")
    remote.set_finished(pid, exit_code=0)
    app.state.job_manager.poll_once()

    log_path = settings.server.media_dir / "logs" / f"{job_id}.log"
    assert log_path.is_file()

    resp = client.delete(f"/api/runs/{run['run_id']}")
    assert resp.status_code == 204
    assert not log_path.exists()


def test_display_name_numbered_per_group(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    r1 = _create_cli_run(client, group="g1", pid=1)
    r2 = _create_cli_run(client, group="g1", pid=2)
    r3 = _create_cli_run(client, group="g2", pid=3)
    tree = client.get("/api/tree").json()
    names = {r["run_id"]: r["display_name"] for p in tree for g in p["groups"] for r in g["runs"]}
    assert (names[r1], names[r2], names[r3]) == ("v1", "v2", "v1")

    # 途中の番号が削除で空いても詰めない（既存runの表示名は変わらない）
    client.post(f"/api/runs/{r1}/finish", json={"status": "finished", "error": None})
    client.delete(f"/api/runs/{r1}")
    draft = client.post("/api/runs/draft", json={"project": "proj_a", "group": "g1", "new_group": False}).json()
    assert draft["display_name"] == "v3"
    assert client.get(f"/api/runs/{r2}").json()["display_name"] == "v2"

    # draft の group を変えたら移った先の group で振り直す
    moved = client.put(f"/api/runs/{draft['run_id']}/config", json={"config_toml": '[wandb]\ngroup = "g2"\n'}).json()
    assert moved["group"] == "g2" and moved["display_name"] == "v2"


def test_display_name_backfilled_for_old_db(tmp_path):
    import sqlite3

    from train_webui.backend.db import Database

    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, project TEXT NOT NULL, run_group TEXT NOT NULL, "
        "origin TEXT NOT NULL, status TEXT NOT NULL, config_toml TEXT NOT NULL, git_commit TEXT, git_diff TEXT, "
        "git_untracked TEXT, git_info_source TEXT, created_at TEXT NOT NULL, started_at TEXT, ended_at TEXT, "
        "last_heartbeat_at TEXT, hostname TEXT, pid INTEGER, exit_code INTEGER, error TEXT)"
    )
    for run_id, group, created in [("b", "g1", "2026-01-02"), ("a", "g1", "2026-01-01"), ("c", "g2", "2026-01-03")]:
        conn.execute(
            "INSERT INTO runs (run_id, project, run_group, origin, status, config_toml, created_at) "
            "VALUES (?, 'p', ?, 'webui', 'finished', '', ?)", (run_id, group, created),
        )
    conn.commit()
    conn.close()

    db = Database(path)
    db.init_schema()
    assert [db.get_run(r)["display_name"] for r in ("a", "b", "c")] == ["v1", "v2", "v1"]


def test_run_comment_editable_in_any_status(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    run_id = _create_cli_run(client)
    assert client.get(f"/api/runs/{run_id}").json()["comment"] == ""

    resp = client.put(f"/api/runs/{run_id}/comment", json={"comment": "lr を下げたら val が安定"})
    assert resp.status_code == 200 and resp.json()["comment"] == "lr を下げたら val が安定"
    client.post(f"/api/runs/{run_id}/finish", json={"status": "finished", "error": None})
    assert client.put(f"/api/runs/{run_id}/comment", json={"comment": "終了後に追記"}).status_code == 200
    # ツリーの RunSummary にも入る（Result タブの比較でも表示・編集するため）
    tree = client.get("/api/tree").json()
    summary = next(r for p in tree for g in p["groups"] for r in g["runs"] if r["run_id"] == run_id)
    assert summary["comment"] == "終了後に追記"
    assert client.put("/api/runs/nope/comment", json={"comment": "x"}).status_code == 404
