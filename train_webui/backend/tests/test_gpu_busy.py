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
    remote.gpu = GpuUsage(total_used_mib=6000, own_mib=0, gpu_util_pct=70.0)

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
    remote.gpu = GpuUsage(total_used_mib=6000, own_mib=0, gpu_util_pct=90.0, cpu_util_pct=60.0, leases=1)  # 使用率は高いが、自分たちの記録（lease）がある = 自分の使用

    assert client.post(f"/api/runs/{run['run_id']}/start").status_code == 200
    assert len(remote.launches) == 1
    assert getattr(remote, 'cleared_leases', 0) >= 1      # 自分のジョブが無いのに残っていた lease は掃除する


def test_start_allowed_when_memory_is_used_but_utilization_is_low(app_factory):
    # メモリ量では他の人とみなさない（使用率が低ければ使ってよい）
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    run = _draft(client)
    remote.gpu = GpuUsage(total_used_mib=20000, own_mib=0, gpu_util_pct=2.0, cpu_util_pct=5.0)
    assert client.post(f"/api/runs/{run['run_id']}/start").status_code == 200


def test_start_blocked_when_cpu_is_busy_without_lease(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    run = _draft(client)
    remote.gpu = GpuUsage(total_used_mib=0, own_mib=0, gpu_util_pct=0.0, cpu_util_pct=80.0)
    resp = client.post(f"/api/runs/{run['run_id']}/start")
    assert resp.status_code == 409 and "CPU 80%" in resp.json()["detail"]


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

    remote.gpu = GpuUsage(total_used_mib=8000, own_mib=0, gpu_util_pct=70.0)
    resp = client.post("/api/groups/infer", json={"project": "proj_a", "group": "g1"})
    assert resp.status_code == 409
    assert len(remote.launches) == 1  # train の1回だけ
