"""max_parallel_jobs > 1 のとき、GPU の空きがあれば複数のジョブを同時に許す。既定（1）は従来どおり排他。"""
from fastapi.testclient import TestClient

from train_webui.backend.remote.types import GpuUsage

from .conftest import write_project


def _draft(client):
    return client.post("/api/runs/draft", json={"project": "proj_a", "group": "g1", "new_group": True}).json()


def test_default_is_exclusive(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    a, b = _draft(client), _draft(client)
    assert client.post(f"/api/runs/{a['run_id']}/start").status_code == 200
    assert client.post(f"/api/runs/{b['run_id']}/start").status_code == 409


def test_two_parallel_then_third_blocked(app_factory):
    app, settings, remote = app_factory(max_parallel_jobs=2)
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    remote.gpu = GpuUsage(total_used_mib=6000, own_mib=6000, total_mib=32000)
    a, b, c = _draft(client), _draft(client), _draft(client)
    assert client.post(f"/api/runs/{a['run_id']}/start").status_code == 200
    assert client.post(f"/api/runs/{b['run_id']}/start").status_code == 200
    resp = client.post(f"/api/runs/{c['run_id']}/start")
    assert resp.status_code == 409 and "別のジョブ" in resp.json()["detail"]
    assert len(remote.launches) == 2
    assert client.get("/api/state").json()["busy"] is True


def test_blocked_when_gpu_free_memory_is_short(app_factory):
    app, settings, remote = app_factory(max_parallel_jobs=2)
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    remote.gpu = GpuUsage(total_used_mib=6000, own_mib=6000, total_mib=32000)
    a, b = _draft(client), _draft(client)
    assert client.post(f"/api/runs/{a['run_id']}/start").status_code == 200
    remote.gpu = GpuUsage(total_used_mib=30000, own_mib=30000, total_mib=32000)  # 空き 2000 < 4096
    resp = client.post(f"/api/runs/{b['run_id']}/start")
    assert resp.status_code == 409 and "空きメモリ" in resp.json()["detail"]


def test_first_job_ignores_headroom_when_nothing_running(app_factory):
    app, settings, remote = app_factory(max_parallel_jobs=2)
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    remote.gpu = GpuUsage(total_used_mib=30000, own_mib=0, total_mib=32000)
    a = _draft(client)
    # 他の人が 30000 使っていて、そもそも他の人の使用量しきい値で断られる（空きメモリの確認とは別の理由）
    assert client.post(f"/api/runs/{a['run_id']}/start").status_code == 409
