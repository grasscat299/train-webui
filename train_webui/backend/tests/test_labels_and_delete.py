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
