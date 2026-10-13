import subprocess

from train_webui.backend.code_snapshot import REF_PREFIX, delete_ref, export_commit, set_ref, snapshot_code


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def _init_repo(root):
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "a.py").write_text("x = 1\n")
    (root / ".gitignore").write_text("ignored.txt\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")


def test_snapshot_includes_dirty_and_untracked_but_not_ignored(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n")       # 未コミットの変更
    (tmp_path / "new.py").write_text("y = 1\n")      # 未追跡
    (tmp_path / "ignored.txt").write_text("secret")  # ignore
    head_before = _git(tmp_path, "rev-parse", "HEAD")
    status_before = _git(tmp_path, "status", "--porcelain")

    commit = snapshot_code(tmp_path, "run1")

    assert _git(tmp_path, "rev-parse", REF_PREFIX + "run1") == commit
    assert _git(tmp_path, "rev-parse", commit + "^") == head_before  # 親は HEAD
    assert _git(tmp_path, "status", "--porcelain") == status_before  # 作業ツリー・indexは変わらない
    assert _git(tmp_path, "show", f"{commit}:a.py") == "x = 2"
    assert _git(tmp_path, "show", f"{commit}:new.py") == "y = 1"
    files = _git(tmp_path, "ls-tree", "-r", "--name-only", commit).splitlines()
    assert "ignored.txt" not in files


def test_export_restores_old_code_even_after_changes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "a.py").write_text("x = 2\n")
    commit = snapshot_code(repo, "run1")
    (repo / "a.py").write_text("x = 3\n")  # その後コードが変わっても

    export_commit(repo, commit, tmp_path / "out")
    assert (tmp_path / "out" / "a.py").read_text() == "x = 2\n"


def test_delete_ref(tmp_path):
    _init_repo(tmp_path)
    snapshot_code(tmp_path, "run1")
    delete_ref(tmp_path, "run1")
    delete_ref(tmp_path, "run1")  # 2回目も落ちない
    assert _git(tmp_path, "for-each-ref", "refs/webui/") == ""


def test_set_ref_keeps_code_after_source_run_deleted(tmp_path):
    _init_repo(tmp_path)
    commit = snapshot_code(tmp_path, "run1")
    set_ref(tmp_path, "run2", commit)
    delete_ref(tmp_path, "run1")
    assert _git(tmp_path, "rev-parse", REF_PREFIX + "run2") == commit
