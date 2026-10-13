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


def load_stages(path: Path = CATEGORIES_PATH) -> dict[str, str]:
    """[stages] の「パスの先頭の要素 → 段階名」。無ければ空。"""
    if not path.is_file():
        return {}
    return dict(tomli.loads(path.read_text(encoding="utf-8")).get("stages", {}))


def stage_of(model: str, stages: dict[str, str]) -> str:
    """モデル（repo からの相対パス）→ 段階（本番 / scratch など）。先頭の要素で決め、未登録ならその要素名。"""
    head = model.split("/", 1)[0]
    return stages.get(head, head)
