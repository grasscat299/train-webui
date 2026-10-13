import subprocess

from fastapi.testclient import TestClient

from train_webui.backend.remote import RemoteController

from .conftest import make_settings, write_project


def _finished_run(app, client, settings, code_commit=None):
    """git_* と code_commit を持つ終了済みの run を1つ作る。"""
    write_project(settings.repo_root, "proj_a")
    run = client.post("/api/runs/draft", json={"project": "proj_a", "group": None, "new_group": True}).json()
    app.state.db.update_run(run["run_id"], status="finished", code_commit=code_commit, git_commit="abc",
                            git_diff="d", git_untracked="{}", git_info_source="sync")
    return run


def test_continue_draft_copies_config_and_links_parent(app_factory):
    app, settings, _ = app_factory()
    client = TestClient(app)
    parent = _finished_run(app, client, settings)
    child = client.post("/api/runs/draft", json={
        "project": "proj_a", "new_group": False, "parent_run_id": parent["run_id"]}).json()
    assert child["parent_run_id"] == parent["run_id"]
    assert child["group"] == parent["group"]
    assert child["config_toml"] == parent["config_toml"]
    assert child["code_commit"] is None  # 引き継ぎは今のコードで走る


def test_rerun_draft_pins_code_and_launch_uses_recorded_code(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    src = _finished_run(app, client, settings, code_commit="deadbeef")
    exported = []
    tagged = []
    app.state.job_manager._export_code = lambda commit, dest: exported.append((commit, dest))
    app.state.job_manager._tag_code = lambda run_id, commit: tagged.append((run_id, commit))
    draft = client.post("/api/runs/draft", json={
        "project": "proj_a", "new_group": False, "rerun_of": src["run_id"]}).json()
    assert draft["code_commit"] == "deadbeef" and draft["parent_run_id"] is None
    assert draft["git_diff"] == "d"
    assert client.post(f"/api/runs/{draft['run_id']}/start").status_code in (200, 202, 204)
    assert exported and exported[0][0] == "deadbeef"
    assert tagged == [(draft["run_id"], "deadbeef")]  # 再実行した run にも自分の ref が付く
    assert remote.sync_sources[-1] is not None  # 作業ツリーではなく取り出したディレクトリを送った


def test_rerun_refused_when_code_not_recorded(app_factory):
    app, settings, _ = app_factory()
    client = TestClient(app)
    src = _finished_run(app, client, settings, code_commit=None)
    resp = client.post("/api/runs/draft", json={"project": "proj_a", "new_group": False, "rerun_of": src["run_id"]})
    assert resp.status_code == 400


def test_register_and_list_artifacts_and_delete_removes_files(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    run = _finished_run(app, client, settings)
    rid = run["run_id"]
    body = {"name": "model.pt", "kind": "checkpoint", "path": f"/remote/run_root/{rid}/artifacts/model.pt", "size": 10}
    assert client.post(f"/api/runs/{rid}/artifacts", json=body).status_code == 204
    assert client.post(f"/api/runs/{rid}/artifacts", json={**body, "size": 20}).status_code == 204  # 上書き
    arts = client.get(f"/api/runs/{rid}/artifacts").json()
    assert [(a["name"], a["size"]) for a in arts] == [("model.pt", 20)]
    assert client.get(f"/api/runs/{rid}").json()["artifacts"][0]["name"] == "model.pt"
    assert client.delete(f"/api/runs/{rid}").status_code == 204
    assert remote.removed_runs == [rid]


class _Runner:
    def __init__(self):
        self.calls = []

    def run(self, args, *, input=None, timeout=None):
        self.calls.append((args, input))
        return subprocess.CompletedProcess(args, 0, stdout=b"4321\n", stderr=b"")


def test_launch_passes_artifact_dir_and_init_from(tmp_path):
    from train_webui.backend.remote import LaunchSpec

    runner = _Runner()
    ctl = RemoteController(make_settings(tmp_path, tmp_path), runner=runner)
    ctl.launch(LaunchSpec(run_id="r2", job_id="j1", kind="train", project_dir="src/m", config_toml="x=1\n",
                          parent_run_id="r1"))
    cmds = [c[0][-1] for c in runner.calls]
    launch_cmd = cmds[-1]
    assert "TRAIN_WEBUI_ARTIFACT_DIR=/remote/run_root/r2/artifacts" in launch_cmd
    assert "TRAIN_WEBUI_RUN_ROOT=/remote/run_root" in launch_cmd
    assert "TRAIN_WEBUI_INIT_FROM=r1" in launch_cmd
    assert "mkdir -p" in launch_cmd and "/remote/run_root/r2/artifacts" in launch_cmd
    # config のスナップショットが run の保管場所に書かれる
    assert any("/remote/run_root/r2/config.toml" in c for c in cmds)


def test_infer_by_run_uses_that_run_and_requires_registered_checkpoint(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    older = _finished_run(app, client, settings)          # v1: 重みあり
    newer = client.post("/api/runs/draft", json={"project": "proj_a", "group": older["group"], "new_group": False}).json()
    app.state.db.update_run(newer["run_id"], status="finished")  # v2（group の最新）: 重みなし

    # 重みが登録されていない run は断る
    resp = client.post("/api/groups/infer", json={"project": "proj_a", "run_id": older["run_id"]})
    assert resp.status_code == 400

    body = {"name": "model.pt", "kind": "checkpoint", "path": "/remote/x/model.pt", "size": 1}
    client.post(f"/api/runs/{older['run_id']}/artifacts", json=body)
    resp = client.post("/api/groups/infer", json={"project": "proj_a", "run_id": older["run_id"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["run_id"] == older["run_id"]       # group の最新（newer）ではなく、指定した run
    assert remote.launches[-1].run_id == older["run_id"] and remote.launches[-1].kind == "infer"


def test_infer_rejects_bad_targets(app_factory):
    app, settings, _ = app_factory()
    client = TestClient(app)
    write_project(settings.repo_root, "proj_a")
    draft = client.post("/api/runs/draft", json={"project": "proj_a", "group": None, "new_group": True}).json()
    assert client.post("/api/groups/infer", json={"project": "proj_a", "run_id": draft["run_id"]}).status_code == 400  # draft
    assert client.post("/api/groups/infer", json={"project": "proj_a", "run_id": "nope"}).status_code == 404
    assert client.post("/api/groups/infer", json={"project": "proj_a"}).status_code == 400  # group も run_id も無い


def test_db_uses_wal_so_readers_do_not_block_writers(app_factory):
    app, _settings, _ = app_factory()
    db = app.state.db
    with db._connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    # 読み取りの途中でも書き込めること（既定の方式ではここでロックされうる）
    reader = db._connect()
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM runs").fetchall()
    db.upsert_artifact("r", "model.pt", "checkpoint", "/x", 1, "2026-01-01T00:00:00+09:00")
    reader.close()
