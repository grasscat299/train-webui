import dataclasses

from fastapi.testclient import TestClient

from train_webui.backend.app import create_app
from train_webui.backend.remote.registry import RemoteRegistry
from train_webui.backend.remote.types import GpuUsage
from train_webui.backend.settings import SchedulerSettings

from .conftest import FakeRemote, make_settings, write_project

BIG = 32000


def _setup(tmp_path, groups=(("a1", "a2"), ("b1", "b2")), priorities=None):
    root = tmp_path / "repo"
    root.mkdir()
    write_project(root, "proj_a")
    base = make_settings(tmp_path, root)
    prio = priorities or {"a1": 100, "a2": 60, "b1": 50, "b2": 40}
    machines = {n: dataclasses.replace(base.remote, host=n, priority=p) for n, p in prio.items()}
    settings = dataclasses.replace(
        base, remote=machines["a1"], machines=machines, default_machine="a1",
        scheduler=SchedulerSettings(reserve_groups=groups, other_grace_sec=0.0),
    )
    fakes = {n: FakeRemote() for n in machines}
    for f in fakes.values():
        f.gpu = GpuUsage(total_used_mib=0, own_mib=0, total_mib=BIG)
    registry = RemoteRegistry(fakes, "a1")
    app = create_app(settings, remote=registry, start_background=False)
    return app, TestClient(app), fakes


def _draft(client, parent=None):
    body = {"project": "proj_a", "group": "g1", "new_group": True}
    return client.post("/api/runs/draft", json=body).json()


def test_auto_selects_highest_priority_and_records_machine(tmp_path):
    app, client, fakes = _setup(tmp_path)
    run = _draft(client)
    resp = client.post(f"/api/runs/{run['run_id']}/start")
    assert resp.status_code == 200 and resp.json()["machine"] == "a1"
    assert len(fakes["a1"].launches) == 1 and not fakes["b1"].launches
    assert client.get(f"/api/runs/{run['run_id']}").json()["jobs"][0]["machine"] == "a1"
    app.state.job_manager.shutdown()


def test_others_on_a1_skips_whole_group(tmp_path):
    app, client, fakes = _setup(tmp_path)
    fakes["a1"].gpu = GpuUsage(total_used_mib=9000, own_mib=0, total_mib=BIG)
    run = _draft(client)
    resp = client.post(f"/api/runs/{run['run_id']}/start")
    assert resp.status_code == 200 and resp.json()["machine"] == "b1"
    assert not fakes["a2"].launches


def test_explicit_machine_and_rule_rejection(tmp_path):
    app, client, fakes = _setup(tmp_path)
    fakes["a1"].gpu = GpuUsage(total_used_mib=9000, own_mib=0, total_mib=BIG)
    run = _draft(client)
    resp = client.post(f"/api/runs/{run['run_id']}/start", params={"machine": "a2"})
    assert resp.status_code == 409 and "チーム用" in resp.json()["detail"]
    assert client.post(f"/api/runs/{run['run_id']}/start", params={"machine": "zzz"}).status_code == 400
    assert client.post(f"/api/runs/{run['run_id']}/start", params={"machine": "b2"}).status_code == 200
    assert len(fakes["b2"].launches) == 1


def test_all_unavailable_lists_reasons(tmp_path):
    app, client, fakes = _setup(tmp_path, priorities={"a1": 1, "b1": 1}, groups=())
    for f in fakes.values():
        f.gpu = GpuUsage(total_used_mib=9000, own_mib=0, total_mib=BIG)
    run = _draft(client)
    resp = client.post(f"/api/runs/{run['run_id']}/start")
    assert resp.status_code == 409 and "a1" in resp.json()["detail"] and "b1" in resp.json()["detail"]


def test_infer_and_continue_stay_on_the_machine_with_the_weights(tmp_path):
    app, client, fakes = _setup(tmp_path)
    run = _draft(client)
    client.post(f"/api/runs/{run['run_id']}/start", params={"machine": "b1"})
    jm = app.state.job_manager
    pid = next(iter(fakes["b1"].poll_results))
    fakes["b1"].set_finished(pid, 0)
    jm.poll_once()
    assert client.get(f"/api/runs/{run['run_id']}").json()["status"] == "finished"
    # 推論は a1 が空いていても、重みのある b1 で動く
    app.state.db.upsert_artifact(run["run_id"], "w.pt", "checkpoint", "p", 1, "t")
    resp = client.post("/api/groups/infer", json={"project": "proj_a", "run_id": run["run_id"]})
    assert resp.status_code == 200 and resp.json()["machine"] == "b1"
    assert len(fakes["b1"].launches) == 2 and not fakes["a1"].launches
    # 引き継ぎ学習の下書き: 台を指定しなければ元の run と同じ台。違う台を指定すると、元の run の重みをその台に送って起動する
    app.state.db.update_run(run["run_id"], machine="b1")
    fakes["b1"].checkpoints[run["run_id"]] = b"TAR-BYTES"
    child = _draft(client)
    app.state.db.update_run(child["run_id"], parent_run_id=run["run_id"])
    fakes["b1"].fail_direct = True              # 直接送信が失敗したら、サーバー経由の中継に切り替える
    fakes["b1"].fail_checkpoint = True          # 重みを送れなければ、理由を返して起動しない
    bad = client.post(f"/api/runs/{child['run_id']}/start", params={"machine": "a1"})
    assert bad.status_code == 400 and "b1" in bad.json()["detail"] and "a1" in bad.json()["detail"]
    assert not fakes["a1"].launches
    fakes["b1"].fail_checkpoint = False
    ok = client.post(f"/api/runs/{child['run_id']}/start", params={"machine": "a1"})
    assert ok.status_code == 200 and ok.json()["machine"] == "a1"
    assert fakes["a1"].checkpoints[run["run_id"]] == b"TAR-BYTES"          # 重みが a1 に置かれた
    assert fakes["a1"].launches[0].parent_run_id == run["run_id"]


def test_machines_endpoint_and_monitor_selection(tmp_path):
    app, client, fakes = _setup(tmp_path)
    fakes["a1"].gpu = GpuUsage(total_used_mib=9000, own_mib=0, total_mib=BIG)
    data = client.get("/api/machines", params={"fresh": True}).json()
    by = {m["name"]: m for m in data["machines"]}
    assert data["selected"] == "b1" and data["default"] == "a1"
    assert not by["a1"]["available"] and "他の人" in by["a1"]["reason"]
    assert not by["a2"]["available"] and by["b1"]["available"] and by["a1"]["group"] == ["a1", "a2"]
    assert client.get("/api/monitor", params={"machine": "nope"}).status_code == 400


def test_other_use_is_remembered_for_grace_period(tmp_path):
    from train_webui.backend.machines import MachineManager
    app, client, fakes = _setup(tmp_path, priorities={"a1": 1}, groups=())
    settings = dataclasses.replace(
        app.state.settings, scheduler=SchedulerSettings(other_grace_sec=300.0, probe_ttl_sec=0))
    now = [0.0]
    mm = MachineManager(settings, RemoteRegistry(fakes, "a1"), lambda: {}, clock=lambda: now[0])
    fakes["a1"].gpu = GpuUsage(total_used_mib=9000, own_mib=0, total_mib=BIG)
    assert not mm.status()["machines"][0]["available"]
    fakes["a1"].gpu = GpuUsage(total_used_mib=0, own_mib=0, total_mib=BIG)
    now[0] = 100.0
    assert not mm.status()["machines"][0]["available"]  # 猶予の間は使用中のまま
    now[0] = 400.0
    assert mm.status()["machines"][0]["available"]


def test_unreachable_machine_is_not_reprobed_by_display_but_is_by_start(tmp_path):
    from train_webui.backend.machines import MachineManager
    app, client, fakes = _setup(tmp_path, priorities={"a1": 1}, groups=())
    settings = dataclasses.replace(app.state.settings, scheduler=SchedulerSettings(probe_ttl_sec=0, unreachable_retry_sec=30.0))
    calls = []
    orig = fakes["a1"].gpu_usage
    fakes["a1"].gpu_usage = lambda: (calls.append(1), orig())[1]
    now = [0.0]
    mm = MachineManager(settings, RemoteRegistry(fakes, "a1"), lambda: {}, clock=lambda: now[0])
    fakes["a1"].fail_gpu = True
    assert not mm.status()["machines"][0]["reachable"]
    now[0] = 5.0
    mm.status()
    assert len(calls) == 1               # 画面の更新は30秒間、測り直さない
    fakes["a1"].fail_gpu = False
    now[0] = 6.0
    assert mm.choose() == "a1"           # 起動の判定は測り直すので、復旧をすぐ拾う
    assert len(calls) == 2


def test_continue_on_other_machine_prefers_direct_push(tmp_path):
    app, client, fakes = _setup(tmp_path)
    run = _draft(client)
    app.state.db.update_run(run["run_id"], machine="b1")
    child = _draft(client)
    app.state.db.update_run(child["run_id"], parent_run_id=run["run_id"])
    resp = client.post(f"/api/runs/{child['run_id']}/start", params={"machine": "a1"})
    assert resp.status_code == 200 and resp.json()["machine"] == "a1"
    assert fakes["b1"].direct_pushes == [(run["run_id"], "mesh-a1", "/remote/run_root")]
    assert not fakes["a1"].checkpoints           # 中継は使っていない


def test_metric_docs_endpoint(tmp_path):
    app, client, fakes = _setup(tmp_path)
    root = app.state.settings.repo_root
    (root / "proj_a" / "metric_docs.toml").write_text('[docs]\n"all_killed" = "全滅率"\n', encoding="utf-8")
    assert client.get("/api/metric_docs", params={"project": "proj_a"}).json() == {"all_killed": "全滅率"}
    assert client.get("/api/metric_docs", params={"project": "nope"}).status_code == 404
    (root / "proj_a" / "metric_docs.toml").unlink()
    assert client.get("/api/metric_docs", params={"project": "proj_a"}).json() == {}   # 無ければ空
