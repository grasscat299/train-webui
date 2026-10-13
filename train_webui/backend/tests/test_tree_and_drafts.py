from fastapi.testclient import TestClient

from .conftest import write_project


def _insert_run(db, run_id, project, group, created_at, status="finished", config_toml=None):
    db.create_run(
        run_id=run_id, project=project, run_group=group, origin="webui", status=status,
        config_toml=config_toml or f'[wandb]\ngroup = "{group}"\n',
        git_commit=None, git_diff=None, git_untracked=None, git_info_source=None,
        created_at=created_at, started_at=created_at, ended_at=None, last_heartbeat_at=None,
        hostname=None, pid=None, exit_code=None, error=None,
    )


def test_tree_lists_projects_groups_runs_sorted(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    write_project(settings.repo_root, "proj_b")
    client = TestClient(app)
    db = app.state.db

    _insert_run(db, "20260101_000000", "proj_a", "g2", "2026-01-01T00:00:00+09:00")
    _insert_run(db, "20260102_000000", "proj_a", "g1", "2026-01-02T00:00:00+09:00")
    _insert_run(db, "20260103_000000", "proj_a", "g1", "2026-01-03T00:00:00+09:00")

    tree = client.get("/api/tree").json()
    assert [p["project"] for p in tree] == ["proj_a", "proj_b"]

    proj_a = tree[0]
    assert [g["group"] for g in proj_a["groups"]] == ["g1", "g2"]

    g1_runs = proj_a["groups"][0]["runs"]
    assert [r["run_id"] for r in g1_runs] == ["20260103_000000", "20260102_000000"]

    proj_b = tree[1]
    assert proj_b["groups"] == []


def test_rescan_matches_tree(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    assert client.post("/api/rescan").json() == client.get("/api/tree").json()


def test_draft_project_has_no_runs_copies_repo_config(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a", config_text='[wandb]\nproject = "p"\ngroup = "base"\n')
    client = TestClient(app)
    resp = client.post("/api/runs/draft", json={"project": "proj_a", "group": "new1", "new_group": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "draft"
    assert body["group"] == "new1"
    assert 'group = "new1"' in body["config_toml"]


def test_draft_unknown_project_404(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    resp = client.post("/api/runs/draft", json={"project": "does_not_exist", "group": "g1", "new_group": True})
    assert resp.status_code == 404


def test_draft_existing_group_copies_latest_in_group(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    db = app.state.db
    _insert_run(db, "20260101_000000", "proj_a", "g1", "2026-01-01T00:00:00+09:00",
                config_toml='[wandb]\ngroup = "g1"\nmarker = "old"\n')
    _insert_run(db, "20260102_000000", "proj_a", "g1", "2026-01-02T00:00:00+09:00",
                config_toml='[wandb]\ngroup = "g1"\nmarker = "new"\n')
    client = TestClient(app)
    resp = client.post("/api/runs/draft", json={"project": "proj_a", "group": "g1", "new_group": False})
    assert resp.status_code == 200
    assert 'marker = "new"' in resp.json()["config_toml"]


def test_draft_new_group_copies_latest_non_draft_in_project(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    db = app.state.db
    _insert_run(db, "20260101_000000", "proj_a", "g_other", "2026-01-01T00:00:00+09:00",
                config_toml='[wandb]\ngroup = "g_other"\nmarker = "cross"\n')
    client = TestClient(app)
    resp = client.post("/api/runs/draft", json={"project": "proj_a", "group": "g_new", "new_group": True})
    assert resp.status_code == 200
    body = resp.json()
    assert 'marker = "cross"' in body["config_toml"]
    assert 'group = "g_new"' in body["config_toml"]


def test_tree_reuses_project_scan_until_rescan(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    assert [p["project"] for p in client.get("/api/tree").json()] == ["proj_a"]

    # 走査結果を使い回している間は、新しい project はツリーに出ない
    write_project(settings.repo_root, "proj_b")
    assert [p["project"] for p in client.get("/api/tree").json()] == ["proj_a"]
    # 「再スキャン」はすぐ探し直す
    assert [p["project"] for p in client.post("/api/rescan").json()] == ["proj_a", "proj_b"]


def test_draft_for_new_project_rescans(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    client.get("/api/tree")
    write_project(settings.repo_root, "proj_c")
    resp = client.post("/api/runs/draft", json={"project": "proj_c", "new_group": True})
    assert resp.status_code == 200


def test_tree_has_workspace(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    tree = TestClient(app).get("/api/tree").json()
    assert tree[0]["workspace"]


def test_classify_by_glob():
    from train_webui.backend.categories import Workspace, classify

    ws = [Workspace("ws", ("src/*",))]
    assert classify("src/world_model_v2", ws) == "ws"
    assert classify("other/x", ws) == "未分類"
    assert classify("x", []) == "未分類"


def test_stage_of():
    from train_webui.backend.categories import stage_of

    st = {"src": "本番", "scratch": "scratch"}
    assert stage_of("src/enemy_world_model", st) == "本番"
    assert stage_of("scratch/DDPM", st) == "scratch"
    assert stage_of("other/x", st) == "other"
