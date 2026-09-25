"""run_id / job_id の採番（CONTRACT.md §1）。"""
from __future__ import annotations

from datetime import datetime
from typing import Callable


def _with_suffix(base: str, exists: Callable[[str], bool]) -> str:
    if not exists(base):
        return base
    i = 2
    while exists(f"{base}_{i}"):
        i += 1
    return f"{base}_{i}"


def new_run_id(exists: Callable[[str], bool], now: datetime | None = None) -> str:
    """YYYYMMDD_HHMMSS。同一秒に衝突したら _2, _3 ... を付ける。"""
    now = now or datetime.now()
    base = now.strftime("%Y%m%d_%H%M%S")
    return _with_suffix(base, exists)


def new_job_id(run_id: str, kind: str, exists: Callable[[str], bool], now: datetime | None = None) -> str:
    """<run_id>__<kind>__<HHMMSS>。衝突したら _2, _3 ... を付ける。"""
    now = now or datetime.now()
    base = f"{run_id}__{kind}__{now.strftime('%H%M%S')}"
    return _with_suffix(base, exists)
