from fastapi.testclient import TestClient

from .conftest import write_project


def _draft_and_start(client, settings, group="g1"):
    write_project(settings.repo_root, "proj_a")
    run = client.post("/api/runs/draft", json={"project": "proj_a", "group": group, "new_group": True}).json()
    resp = client.post(f"/api/runs/{run['run_id']}/start")
    assert resp.status_code == 200
    return resp.json()


def test_start_syncs_and_launches(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run = _draft_and_start(client, settings)
    assert run["status"] == "running"
    assert remote.sync_calls == 1
    assert len(remote.launches) == 1
    assert remote.launches[0].kind == "train"
    assert remote.launches[0].project_dir == "proj_a"
    assert len(run["jobs"]) == 1
    assert run["jobs"][0]["kind"] == "train"
    assert run["jobs"][0]["status"] == "running"
    assert run["git_info_source"] == "sync"


def test_poll_marks_finished(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run = _draft_and_start(client, settings)
    pid = next(iter(remote.poll_results))
    remote.set_finished(pid, exit_code=0)
    app.state.job_manager.poll_once()
    got = client.get(f"/api/runs/{run['run_id']}").json()
    assert got["status"] == "finished"
    assert got["jobs"][0]["status"] == "finished"
    assert got["jobs"][0]["exit_code"] == 0


def test_poll_marks_failed_on_nonzero_exit(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run = _draft_and_start(client, settings)
    pid = next(iter(remote.poll_results))
    remote.set_finished(pid, exit_code=1)
    app.state.job_manager.poll_once()
    got = client.get(f"/api/runs/{run['run_id']}").json()
    assert got["status"] == "failed"
    assert got["jobs"][0]["status"] == "failed"


def test_sync_failure_marks_run_failed(app_factory):
    app, settings, remote = app_factory()
    remote.fail_sync = True
    client = TestClient(app)
    write_project(settings.repo_root, "proj_a")
    run = client.post("/api/runs/draft", json={"project": "proj_a", "group": "g1", "new_group": True}).json()
    resp = client.post(f"/api/runs/{run['run_id']}/start")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error"]
    assert body["jobs"][0]["status"] == "failed"


def test_launch_failure_marks_run_failed(app_factory):
    app, settings, remote = app_factory()
    remote.fail_launch = True
    client = TestClient(app)
    run = _draft_and_start_expect_failed(client, settings)
    assert run["status"] == "failed"


def _draft_and_start_expect_failed(client, settings):
    write_project(settings.repo_root, "proj_a")
    run = client.post("/api/runs/draft", json={"project": "proj_a", "group": "g1", "new_group": True}).json()
    return client.post(f"/api/runs/{run['run_id']}/start").json()


def test_stop_sets_stopped_and_calls_remote(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run = _draft_and_start(client, settings)
    pid = next(iter(remote.poll_results))
    resp = client.post(f"/api/runs/{run['run_id']}/stop")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "stopped"
    assert body["jobs"][0]["status"] == "stopped"
    assert pid in remote.stops


def test_stop_without_running_job_409(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    write_project(settings.repo_root, "proj_a")
    run = client.post("/api/runs/draft", json={"project": "proj_a", "group": "g1", "new_group": True}).json()
    resp = client.post(f"/api/runs/{run['run_id']}/stop")
    assert resp.status_code == 409


def test_start_on_non_draft_409(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run = _draft_and_start(client, settings)
    resp = client.post(f"/api/runs/{run['run_id']}/start")
    assert resp.status_code == 409


def test_busy_blocks_second_start(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run1 = _draft_and_start(client, settings, group="g1")
    run2 = client.post("/api/runs/draft", json={"project": "proj_a", "group": "g2", "new_group": True}).json()
    resp = client.post(f"/api/runs/{run2['run_id']}/start")
    assert resp.status_code == 409
    state = client.get("/api/state").json()
    assert state["busy"] is True
    assert state["active"]["run_id"] == run1["run_id"]
    assert state["active"]["origin"] == "webui"


def test_job_log_endpoint_reads_local_ingested_log(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run = _draft_and_start(client, settings)
    job_id = run["jobs"][0]["job_id"]
    job_dir = remote.launches[0]
    job_row = app.state.db.get_job(job_id)
    remote.append_log(job_row["job_dir"], b"hello\n")

    app.state.job_manager.poll_once()  # まだalive=Trueのまま。ログだけ取り込まれる

    resp = client.get(f"/api/jobs/{job_id}/log", params={"offset": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == "hello\n"
    assert body["done"] is False

    resp2 = client.get(f"/api/jobs/{job_id}/log", params={"offset": body["offset"]})
    assert resp2.json()["data"] == ""


def _draft_with_reserved_job(client, settings, app):
    """起動処理の途中（job行はあるがpid未記録）の状態を作る。"""
    write_project(settings.repo_root, "proj_a")
    run = client.post("/api/runs/draft", json={"project": "proj_a", "group": "g1", "new_group": True}).json()
    app.state.job_manager._reserve_job(run["run_id"], kind="train")
    app.state.db.update_run(run["run_id"], status="running")
    return run


def test_stop_during_launch_409(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run = _draft_with_reserved_job(client, settings, app)
    resp = client.post(f"/api/runs/{run['run_id']}/stop")
    assert resp.status_code == 409
    assert remote.stops == []


def test_interrupted_launch_is_failed_on_restart(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run = _draft_with_reserved_job(client, settings, app)
    app2, _, _ = app_factory(start_background=True)  # 同じDBを開き直す＝server再起動
    got = TestClient(app2).get(f"/api/runs/{run['run_id']}").json()
    assert got["status"] == "failed"
    assert got["jobs"][0]["status"] == "failed"
    assert TestClient(app2).get("/api/state").json()["busy"] is False


def test_poll_does_not_touch_job_being_stopped(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run = _draft_and_start(client, settings)
    job_id = run["jobs"][0]["job_id"]
    pid = next(iter(remote.poll_results))
    remote.set_finished(pid, exit_code=143)
    app.state.job_manager._stopping.add(job_id)
    app.state.job_manager.poll_once()
    assert app.state.db.get_job(job_id)["status"] == "running"


def test_job_log_unknown_job_404(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    resp = client.get("/api/jobs/no_such_job/log")
    assert resp.status_code == 404
