import base64
import time

from fastapi.testclient import TestClient

from .conftest import write_project

_PNG_1PX_B64 = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8ffff3f0005fe02fea739663d0000000049454e"
    "44ae426082"
)).decode()


def _create_cli_run(client, project="proj_a", group="g1", hostname="gpu1", pid=123):
    resp = client.post("/api/runs", json={
        "project": project, "group": group, "config_toml": '[wandb]\ngroup = "g1"\n',
        "hostname": hostname, "pid": pid,
    })
    assert resp.status_code == 200
    return resp.json()


def test_client_create_run_and_resolve(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    body = _create_cli_run(client)
    assert body["warning"] is None
    run_id = body["run_id"]

    got = client.get(f"/api/runs/{run_id}").json()
    assert got["origin"] == "cli"
    assert got["status"] == "running"
    assert got["hostname"] == "gpu1"

    resolved = client.get("/api/runs/resolve", params={"project": "proj_a", "group": "g1"})
    assert resolved.status_code == 200
    assert resolved.json()["run_id"] == run_id


def test_resolve_404_when_not_found(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    resp = client.get("/api/runs/resolve", params={"project": "proj_a", "group": "none"})
    assert resp.status_code == 404


def test_client_create_run_carries_last_sync_git_info(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    write_project(settings.repo_root, "proj_a")
    run = client.post("/api/runs/draft", json={"project": "proj_a", "group": "g1", "new_group": True}).json()
    client.post(f"/api/runs/{run['run_id']}/start")  # 1回syncされる

    body = _create_cli_run(client, project="proj_b", group="gx")
    got = client.get(f"/api/runs/{body['run_id']}").json()
    assert got["git_info_source"] == "last_sync"
    assert got["git_commit"] == "abc123"


def test_client_create_run_warns_when_busy(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    write_project(settings.repo_root, "proj_a")
    run = client.post("/api/runs/draft", json={"project": "proj_a", "group": "g1", "new_group": True}).json()
    client.post(f"/api/runs/{run['run_id']}/start")

    body = _create_cli_run(client, project="proj_b", group="g1")
    assert body["warning"] is not None


def test_heartbeat_lost_transition_and_recovery(app_factory):
    app, settings, remote = app_factory(lost_after_sec=0.05)
    client = TestClient(app)
    run_id = _create_cli_run(client)["run_id"]

    time.sleep(0.15)
    app.state.job_manager.poll_once()
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "lost"

    resp = client.post(f"/api/runs/{run_id}/heartbeat")
    assert resp.status_code == 204
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "running"


def test_heartbeat_unknown_run_404(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    resp = client.post("/api/runs/no_such_run/heartbeat")
    assert resp.status_code == 404


def test_finish_origin_cli_updates_status(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run_id = _create_cli_run(client)["run_id"]

    resp = client.post(f"/api/runs/{run_id}/finish", json={"status": "failed", "error": "boom"})
    assert resp.status_code == 204
    got = client.get(f"/api/runs/{run_id}").json()
    assert got["status"] == "failed"
    assert got["error"] == "boom"
    assert got["ended_at"] is not None


def test_finish_origin_webui_does_not_change_status(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    write_project(settings.repo_root, "proj_a")
    run = client.post("/api/runs/draft", json={"project": "proj_a", "group": "g1", "new_group": True}).json()
    client.post(f"/api/runs/{run['run_id']}/start")

    resp = client.post(f"/api/runs/{run['run_id']}/finish", json={"status": "failed", "error": "note"})
    assert resp.status_code == 204
    got = client.get(f"/api/runs/{run['run_id']}").json()
    assert got["status"] == "running"
    assert got["error"] == "note"


def test_results_metric_and_image(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run_id = _create_cli_run(client)["run_id"]

    resp = client.post("/api/results", json={
        "run_id": run_id, "source": "train",
        "items": [
            {"kind": "metric", "label": "train/loss", "step": 1, "value": 0.5,
             "sent_at": "2026-01-01T00:00:00+09:00"},
            {"kind": "image", "label": "infer/xy", "image_b64": _PNG_1PX_B64,
             "sent_at": "2026-01-01T00:00:01+09:00"},
        ],
    })
    assert resp.status_code == 204

    results = client.get(f"/api/runs/{run_id}/results").json()
    assert len(results) == 2
    metric = next(r for r in results if r["kind"] == "metric")
    assert metric["value"] == 0.5
    assert metric["step"] == 1
    image = next(r for r in results if r["kind"] == "image")
    assert image["image_url"].startswith(f"/media/images/{run_id}/")

    saved_path = settings.server.media_dir / image["image_url"].split("/media/", 1)[1]
    assert saved_path.is_file()
    assert saved_path.read_bytes() == base64.b64decode(_PNG_1PX_B64)


def test_results_run_not_found_404(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    resp = client.post("/api/results", json={"run_id": "nope", "source": "x", "items": []})
    assert resp.status_code == 404


def test_results_bad_base64_400(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run_id = _create_cli_run(client)["run_id"]
    resp = client.post("/api/results", json={
        "run_id": run_id, "source": "s",
        "items": [{"kind": "image", "label": "x", "image_b64": "not-base64!!", "sent_at": "2026-01-01T00:00:00+09:00"}],
    })
    assert resp.status_code == 400
