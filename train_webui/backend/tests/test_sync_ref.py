"""学習用PCへ送るコードを git の ref（[sync] source_ref）から作る。作業ツリーの HEAD・未コミットの変更には依らない。"""
import subprocess

from fastapi.testclient import TestClient

from train_webui.backend import code_snapshot
from train_webui.backend.settings import settings_from_dict

from .conftest import write_project


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def _repo_with_detached_head(root):
    """master は新しい commit。作業ツリーは古い commit の detached HEAD で、未追跡のファイルもある。"""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "master")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "a.py").write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "old")
    old = _git(root, "rev-parse", "HEAD")
    (root / "a.py").write_text("x = 2\n")
    (root / "new_flag.py").write_text("FLAG = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "new")
    new = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", "--detach", old)
    (root / "untracked.txt").write_text("not in master")
    return old, new


def test_resolve_ref_ignores_head(tmp_path):
    old, new = _repo_with_detached_head(tmp_path / "repo")
    assert code_snapshot.resolve_ref(tmp_path / "repo", "master") == new
    assert _git(tmp_path / "repo", "rev-parse", "HEAD") == old


def test_resolve_ref_unknown_raises(tmp_path):
    _repo_with_detached_head(tmp_path / "repo")
    try:
        code_snapshot.resolve_ref(tmp_path / "repo", "no-such-branch")
    except ValueError as e:
        assert "no-such-branch" in str(e)
    else:
        raise AssertionError("ValueError にならなかった")


def test_cache_has_only_tracked_files_of_the_commit_and_is_reused(tmp_path):
    repo = tmp_path / "repo"
    old, new = _repo_with_detached_head(repo)
    cache = tmp_path / "cache"
    d = code_snapshot.ensure_commit_cache(repo, new, cache)
    assert d == cache / new
    assert (d / "a.py").read_text() == "x = 2\n"
    assert (d / "new_flag.py").exists()
    assert not (d / "untracked.txt").exists()
    (d / "marker").write_text("1")
    assert code_snapshot.ensure_commit_cache(repo, new, cache) == d
    assert (d / "marker").exists()  # 作り直さない
    assert not [p for p in cache.iterdir() if p.name.startswith(".tmp-")]


def test_cache_keeps_only_newest(tmp_path):
    repo = tmp_path / "repo"
    old, new = _repo_with_detached_head(repo)
    cache = tmp_path / "cache"
    code_snapshot.ensure_commit_cache(repo, old, cache, keep=1)
    code_snapshot.ensure_commit_cache(repo, new, cache, keep=1)
    assert [p.name for p in cache.iterdir()] == [new]


def test_settings_source_ref_default_and_empty():
    raw = {
        "remote": {"host": "h", "user": "u", "port": 22, "key_path": "k", "repo_dir": "/r", "python": "p",
                   "run_root": "/rr", "stop_timeout_sec": 1},
        "server": {"host": "h", "port": 1, "public_url": "u", "db_path": "d", "media_dir": "m"},
        "log": {"poll_interval_sec": 1}, "heartbeat": {"lost_after_sec": 1}, "sync": {"exclude": []},
    }
    assert settings_from_dict(raw).sync_ref == "master"
    raw["sync"]["source_ref"] = ""
    assert settings_from_dict(raw).sync_ref == ""
    raw["sync"]["source_ref"] = "release"
    assert settings_from_dict(raw).sync_ref == "release"


def _wire_ref_sync(app, repo, tmp_path):
    jm = app.state.job_manager
    jm._resolve_ref = lambda ref: code_snapshot.resolve_ref(repo, ref)
    jm._export_ref = lambda commit: code_snapshot.ensure_commit_cache(repo, commit, tmp_path / "cache")
    return jm


def _draft(client, settings):
    write_project(settings.repo_root, "proj_a")
    return client.post("/api/runs/draft", json={"project": "proj_a", "group": "g1", "new_group": True}).json()


def test_start_sends_master_commit_not_worktree(app_factory, tmp_path):
    repo = tmp_path / "repo"
    old, new = _repo_with_detached_head(repo)
    app, settings, remote = app_factory(repo_root=repo)
    _wire_ref_sync(app, repo, tmp_path)
    client = TestClient(app)
    run = _draft(client, settings)
    resp = client.post(f"/api/runs/{run['run_id']}/start")
    assert resp.status_code == 200
    detail = client.get(f"/api/runs/{run['run_id']}").json()
    assert detail["git_commit"] == new
    assert detail["code_commit"] == new
    assert detail["git_info_source"] == "ref:master"
    assert detail["git_diff"] == ""
    assert remote.sync_sources == [tmp_path / "cache" / new]
    assert (remote.sync_sources[0] / "new_flag.py").exists()


def test_start_ref_override_and_worktree_escape(app_factory, tmp_path):
    repo = tmp_path / "repo"
    old, new = _repo_with_detached_head(repo)
    app, settings, remote = app_factory(repo_root=repo)
    _wire_ref_sync(app, repo, tmp_path)
    client = TestClient(app)

    run = _draft(client, settings)
    assert client.post(f"/api/runs/{run['run_id']}/start?ref={old}").status_code == 200
    detail = client.get(f"/api/runs/{run['run_id']}").json()
    assert detail["git_commit"] == old and detail["sync_ref"] == old

    # 作業ツリーから送る逃げ道（server.toml で source_ref = "" と同じ）
    app.state.job_manager._settings = app.state.settings
    run2 = client.post("/api/runs/draft", json={"project": "proj_a", "group": run["group"], "new_group": False}).json()
    remote.set_finished(1000)  # 1台構成なので、先の run を終わらせる
    app.state.job_manager.poll_once()
    assert client.post(f"/api/runs/{run2['run_id']}/start?ref=worktree").status_code == 200
    d2 = client.get(f"/api/runs/{run2['run_id']}").json()
    assert d2["git_info_source"] == "sync"
    assert remote.sync_sources[-1] is None


def test_start_unknown_ref_is_rejected_before_launch(app_factory, tmp_path):
    repo = tmp_path / "repo"
    _repo_with_detached_head(repo)
    app, settings, remote = app_factory(repo_root=repo)
    _wire_ref_sync(app, repo, tmp_path)
    client = TestClient(app)
    run = _draft(client, settings)
    resp = client.post(f"/api/runs/{run['run_id']}/start?ref=nope")
    assert resp.status_code == 400
    assert remote.sync_calls == 0
    assert client.get(f"/api/runs/{run['run_id']}").json()["status"] == "draft"
