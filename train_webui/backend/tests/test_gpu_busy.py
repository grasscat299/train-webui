from fastapi.testclient import TestClient

from train_webui.backend.remote.types import GpuUsage

from .conftest import write_project


def _draft(client):
    return client.post("/api/runs/draft", json={"project": "proj_a", "group": "g1", "new_group": True}).json()


def test_start_blocked_when_others_use_gpu(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    run = _draft(client)
    remote.gpu = GpuUsage(total_used_mib=6000, own_mib=0)

    resp = client.post(f"/api/runs/{run['run_id']}/start")
    assert resp.status_code == 409
    assert "他の人が使用中" in resp.json()["detail"]
    # 起動処理には進まず、draft のまま残る
    assert remote.sync_calls == 0 and remote.launches == []
    assert client.get(f"/api/runs/{run['run_id']}").json()["status"] == "draft"


def test_start_allowed_when_only_own_processes_use_gpu(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    run = _draft(client)
    remote.gpu = GpuUsage(total_used_mib=6000, own_mib=5500)  # 他の人の分は 500MiB でしきい値(1024)以下

    assert client.post(f"/api/runs/{run['run_id']}/start").status_code == 200
    assert len(remote.launches) == 1


def test_start_blocked_when_gpu_check_fails(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    run = _draft(client)
    remote.fail_gpu = True

    resp = client.post(f"/api/runs/{run['run_id']}/start")
    assert resp.status_code == 502
    assert remote.launches == []


def test_infer_blocked_when_others_use_gpu(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    run = _draft(client)
    client.post(f"/api/runs/{run['run_id']}/start")
    pid = next(iter(remote.poll_results))
    remote.set_finished(pid, exit_code=0)
    app.state.job_manager.poll_once()

    remote.gpu = GpuUsage(total_used_mib=8000, own_mib=0)
    resp = client.post("/api/groups/infer", json={"project": "proj_a", "group": "g1"})
    assert resp.status_code == 409
    assert len(remote.launches) == 1  # train の1回だけ
