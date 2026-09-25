from fastapi.testclient import TestClient

from .conftest import write_project


def _finished_train_run(client, remote, app, settings, group="g1"):
    write_project(settings.repo_root, "proj_a")
    run = client.post("/api/runs/draft", json={"project": "proj_a", "group": group, "new_group": True}).json()
    client.post(f"/api/runs/{run['run_id']}/start")
    pid = next(iter(remote.poll_results))
    remote.set_finished(pid, exit_code=0)
    app.state.job_manager.poll_once()
    return run


def test_infer_no_target_404(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    write_project(settings.repo_root, "proj_a")
    resp = client.post("/api/groups/infer", json={"project": "proj_a", "group": "none"})
    assert resp.status_code == 404


def test_infer_launches_against_latest_train_run(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run = _finished_train_run(client, remote, app, settings)

    resp = client.post("/api/groups/infer", json={"project": "proj_a", "group": "g1"})
    assert resp.status_code == 200
    job = resp.json()
    assert job["run_id"] == run["run_id"]
    assert job["kind"] == "infer"
    assert job["status"] == "running"

    got = client.get(f"/api/runs/{run['run_id']}").json()
    assert got["status"] == "finished"  # inferは対象runのstatusを変えない
    assert len(got["jobs"]) == 2
    assert remote.launches[-1].kind == "infer"


def test_infer_busy_while_train_running_409(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    write_project(settings.repo_root, "proj_a")
    run = client.post("/api/runs/draft", json={"project": "proj_a", "group": "g1", "new_group": True}).json()
    client.post(f"/api/runs/{run['run_id']}/start")  # まだrunning

    resp = client.post("/api/groups/infer", json={"project": "proj_a", "group": "g1"})
    assert resp.status_code == 409


def test_infer_poll_finishes_job_without_changing_run_status(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run = _finished_train_run(client, remote, app, settings)
    resp = client.post("/api/groups/infer", json={"project": "proj_a", "group": "g1"})
    job_id = resp.json()["job_id"]
    infer_pid = next(pid for pid in remote.poll_results if remote.poll_results[pid].alive)
    remote.set_finished(infer_pid, exit_code=0)
    app.state.job_manager.poll_once()

    got = client.get(f"/api/runs/{run['run_id']}").json()
    assert got["status"] == "finished"
    infer_job = next(j for j in got["jobs"] if j["job_id"] == job_id)
    assert infer_job["status"] == "finished"
