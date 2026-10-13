"""モデル（project）を表示上のプロジェクトにまとめる（design.md §2.5）。categories.toml の glob で決める。"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

import tomli

CATEGORIES_PATH = Path(__file__).resolve().parent.parent / "categories.toml"
UNCATEGORIZED = "未分類"


@dataclass(frozen=True)
class Workspace:
    name: str
    patterns: tuple[str, ...]


def load_workspaces(path: Path = CATEGORIES_PATH) -> list[Workspace]:
    """ファイルが無ければ空（全モデルが未分類）。"""
    if not path.is_file():
        return []
    raw = tomli.loads(path.read_text(encoding="utf-8"))
    return [Workspace(p["name"], tuple(p.get("match", []))) for p in raw.get("project", [])]


def classify(model: str, workspaces: list[Workspace]) -> str:
    """モデル（repo からの相対パス）→ プロジェクト名。"""
    for w in workspaces:
        if any(fnmatch.fnmatchcase(model, p) for p in w.patterns):
            return w.name
    return UNCATEGORIZED
