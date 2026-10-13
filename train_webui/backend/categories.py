"""モデル（project）をプロジェクト > カテゴリーに分類する（design.md §2.5）。categories.toml の glob で決める。"""
from __future__ import annotations

import fnmatch
import tomli
from dataclasses import dataclass
from pathlib import Path

CATEGORIES_PATH = Path(__file__).resolve().parent.parent / "categories.toml"
UNCATEGORIZED = "未分類"
DEFAULT_WORKSPACE = "td_plan"


@dataclass(frozen=True)
class Category:
    workspace: str
    name: str
    patterns: tuple[str, ...]


def load_categories(path: Path = CATEGORIES_PATH) -> list[Category]:
    """ファイルが無ければ空（全モデルが未分類）。"""
    if not path.is_file():
        return []
    raw = tomli.loads(path.read_text(encoding="utf-8"))
    result: list[Category] = []
    for proj in raw.get("project", []):
        for cat in proj.get("category", []):
            result.append(Category(proj["name"], cat["name"], tuple(cat.get("match", []))))
    return result


def classify(model: str, categories: list[Category]) -> tuple[str, str]:
    """モデル（repo からの相対パス）→ (プロジェクト名, カテゴリー名)。"""
    for c in categories:
        if any(fnmatch.fnmatchcase(model, p) for p in c.patterns):
            return c.workspace, c.name
    return (categories[0].workspace if categories else DEFAULT_WORKSPACE), UNCATEGORIZED
