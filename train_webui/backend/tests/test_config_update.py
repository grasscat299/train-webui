from fastapi.testclient import TestClient

from .conftest import write_project


def _create_draft(client, project="proj_a", group="g1"):
    return client.post("/api/runs/draft", json={"project": project, "group": group, "new_group": True}).json()


def test_update_config_ok_and_reclassifies_group(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    run = _create_draft(client)
    resp = client.put(
        f"/api/runs/{run['run_id']}/config",
        json={"config_toml": '[wandb]\nproject = "p"\ngroup = "g2"\n'},
    )
    assert resp.status_code == 200
    assert resp.json()["group"] == "g2"

    tree = client.get("/api/tree").json()
    groups = [g["group"] for g in tree[0]["groups"]]
    assert groups == ["g2"]


def test_update_config_invalid_toml_400(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    run = _create_draft(client)
    resp = client.put(f"/api/runs/{run['run_id']}/config", json={"config_toml": "not [ valid"})
    assert resp.status_code == 400


def test_update_config_missing_group_400(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    run = _create_draft(client)
    resp = client.put(f"/api/runs/{run['run_id']}/config", json={"config_toml": "[train]\nlr = 1\n"})
    assert resp.status_code == 400


def test_update_config_on_non_draft_409(app_factory):
    app, settings, remote = app_factory()
    write_project(settings.repo_root, "proj_a")
    client = TestClient(app)
    run = _create_draft(client)
    client.post(f"/api/runs/{run['run_id']}/start")
    resp = client.put(
        f"/api/runs/{run['run_id']}/config", json={"config_toml": '[wandb]\ngroup = "x"\n'}
    )
    assert resp.status_code == 409


def test_update_config_unknown_run_404(app_factory):
    app, settings, remote = app_factory()
    client = TestClient(app)
    resp = client.put("/api/runs/no_such_run/config", json={"config_toml": '[wandb]\ngroup = "x"\n'})
    assert resp.status_code == 404
