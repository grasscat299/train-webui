"""run を起動した時点のコードを git の ref として残し、後から取り出す（design.md §3.5）。

作業ツリー（未コミットの変更・未追跡ファイルも含む。.gitignore されたものは除く）を、HEAD を親にした
1つの commit にして `refs/webui/runs/<run_id>` に付ける。ブランチ・index・作業ツリーは変えない。
ref が指す commit は `git gc` で消えない。
"""
from __future__ import annotations

import io
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

REF_PREFIX = "refs/webui/runs/"
_ENV_IDENT = {
    "GIT_AUTHOR_NAME": "train_webui", "GIT_AUTHOR_EMAIL": "train_webui@localhost",
    "GIT_COMMITTER_NAME": "train_webui", "GIT_COMMITTER_EMAIL": "train_webui@localhost",
}


def _git(repo_root: Path, args: list[str], env: dict | None = None, text: bool = True):
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args], capture_output=True, text=text, env={**os.environ, **(env or {})},
    )
    if proc.returncode != 0:
        err = proc.stderr if text else proc.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"git {' '.join(args)} に失敗しました: {err.strip()}")
    return proc.stdout


def snapshot_code(repo_root: Path, run_id: str) -> str:
    """作業ツリー全体の commit を作り、ref を付けて commit のハッシュを返す。"""
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index"), **_ENV_IDENT}
        _git(repo_root, ["read-tree", "HEAD"], env)
        _git(repo_root, ["add", "-A"], env)
        tree = _git(repo_root, ["write-tree"], env).strip()
    commit = _git(
        repo_root, ["commit-tree", tree, "-p", "HEAD", "-m", f"train_webui run {run_id}"], _ENV_IDENT,
    ).strip()
    _git(repo_root, ["update-ref", REF_PREFIX + run_id, commit])
    return commit


def set_ref(repo_root: Path, run_id: str, commit: str) -> None:
    """既にある commit（過去の run のコード）に、この run の ref を付ける。元の run を消してもコードが残る。"""
    _git(repo_root, ["update-ref", REF_PREFIX + run_id, commit])


def export_commit(repo_root: Path, commit: str, dest: Path) -> None:
    """commit の中身を dest（空のディレクトリ）に取り出す。作業ツリーには触らない。"""
    dest.mkdir(parents=True, exist_ok=True)
    archive = _git(repo_root, ["archive", "--format=tar", commit], text=False)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest)  # noqa: S202 - 自分のリポジトリの commit なので信頼できる


def delete_ref(repo_root: Path, run_id: str) -> None:
    """run を消すとき ref も消す。ref が無くても何もしない。"""
    subprocess.run(["git", "-C", str(repo_root), "update-ref", "-d", REF_PREFIX + run_id], capture_output=True)
