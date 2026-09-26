"""project検出（design.md §2）。main.py は実行せず AST 解析だけを行う（重い import・CUDA初期化を避ける）。"""
from __future__ import annotations

import ast
import os
from pathlib import Path

# 探索から除外するディレクトリ名（名前一致。どの階層でも除外）
DEFAULT_EXCLUDE_DIRS = {
    ".git", ".hg", ".svn",
    "venv", ".venv", "env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "wandb", "node_modules",
    "train_webui",  # WebUI自身
    ".claude",
}

REQUIRED_MODE_CHOICES = frozenset({"train", "infer"})


def _mode_choices_ok(main_py: Path) -> bool:
    """main.py の argparse `--mode` の choices が {"train","infer"} ちょうどか確認する。"""
    try:
        source = main_py.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(main_py))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_add_argument = (isinstance(func, ast.Attribute) and func.attr == "add_argument") or (
            isinstance(func, ast.Name) and func.id == "add_argument"
        )
        if not is_add_argument or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == "--mode"):
            continue

        choices_node = next((kw.value for kw in node.keywords if kw.arg == "choices"), None)
        if not isinstance(choices_node, (ast.List, ast.Tuple, ast.Set)):
            continue
        elts = choices_node.elts
        values = [elt.value for elt in elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]
        if len(values) != len(elts):
            continue  # choices の要素が全部文字列リテラルとは限らない → 静的に確認できないので対象外
        if set(values) == REQUIRED_MODE_CHOICES:
            return True
    return False


def _has_config(dir_path: Path) -> bool:
    return (dir_path / "config.toml").is_file() or (dir_path / "config.example.toml").is_file()


def discover_projects(repo_root: Path, exclude_dirs: set[str] | None = None) -> list[str]:
    """条件を満たすディレクトリを、repo_root からの相対パス（posix区切り）のリストで返す（パス順）。"""
    exclude = exclude_dirs if exclude_dirs is not None else DEFAULT_EXCLUDE_DIRS
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(d for d in dirnames if d not in exclude)
        if "main.py" not in filenames:
            continue
        dpath = Path(dirpath)
        if dpath == repo_root:
            continue
        if not _has_config(dpath):
            continue
        if not _mode_choices_ok(dpath / "main.py"):
            continue
        found.append(dpath.relative_to(repo_root).as_posix())
    return sorted(found)
