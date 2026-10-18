"""複数の学習用PCの状態を測り、ジョブを載せる台を選ぶ（ルール本体は scheduler.py）。"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .remote.registry import RemoteRegistry
from .remote.types import GpuUsage
from .scheduler import MachineState, Rule, evaluate, pick
from .settings import Settings


class SelectionError(Exception):
    """台を決められない。status_code はそのまま HTTP のステータスにする。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class MachineManager:
    def __init__(self, settings: Settings, registry: RemoteRegistry,
                 own_jobs: Callable[[], dict[str, int]],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._settings = settings
        self._registry = registry
        self._own_jobs = own_jobs  # 台の名前 → 自分のジョブ数
        self._clock = clock
        self._lock = threading.Lock()
        self._failed_at: dict[str, float] = {}  # 台ごとに、最後に測定に失敗した時刻
        self._last_other_at: dict[str, float] = {}  # 台ごとに、他の人の使用を最後に見た時刻
        self._cache: tuple[float, list[tuple[MachineState, GpuUsage | None]]] | None = None

    @property
    def rule(self) -> Rule:
        s = self._settings
        return Rule(max_parallel_jobs=s.max_parallel_jobs, job_headroom_mib=s.job_headroom_mib,
                    reserve_groups=s.scheduler.reserve_groups, min_free=s.scheduler.min_free,
                    always_free=s.scheduler.always_free)

    def _probe(self, name: str, fresh: bool) -> GpuUsage | None:
        failed = self._failed_at.get(name)
        if not fresh and failed is not None and self._clock() - failed < self._settings.scheduler.unreachable_retry_sec:
            return None  # 画面の更新では、不通の台に何度も接続しに行かない（起動の判定 = fresh のときだけ測り直す）
        try:
            usage = self._registry.get(name).gpu_usage()
        except Exception:  # noqa: BLE001 - 接続できない台は候補から外すだけ
            self._failed_at[name] = self._clock()
            return None
        self._failed_at.pop(name, None)
        return usage

    def _measure(self, fresh: bool) -> list[tuple[MachineState, GpuUsage | None]]:
        names = self._registry.names
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            usages = list(pool.map(lambda n: self._probe(n, fresh), names))
        own = self._own_jobs()
        now = self._clock()
        # 自分たちのジョブが無いのに残っている lease は古い記録なので掃除する（残すと他の人の使用を見落とす）
        for name, usage in zip(names, usages):
            if usage is not None and usage.leases > 0 and own.get(name, 0) == 0:
                try:
                    self._registry.get(name).clear_leases()
                except Exception:  # noqa: BLE001
                    pass
        grace = self._settings.scheduler.other_grace_sec
        out = []
        for name, usage in zip(names, usages):
            sc = self._settings.scheduler
            # 他の人 = 自分たちの記録（lease・ジョブ）が無い台で、GPU か CPU の使用率が高いもの
            mine = own.get(name, 0) > 0 or (usage is not None and usage.leases > 0)
            current = (usage is not None and not mine
                       and (usage.gpu_util_pct >= sc.gpu_util_busy_pct or usage.cpu_util_pct >= sc.cpu_util_busy_pct))
            if current:
                self._last_other_at[name] = now
            seen = self._last_other_at.get(name)
            recent = current or (seen is not None and now - seen < grace)
            out.append((MachineState(
                name=name, priority=self._settings.machines[name].priority, reachable=usage is not None,
                own_jobs=own.get(name, 0), other_active=recent,
                free_mib=(usage.free_mib if usage is not None and usage.total_mib else None),
                gpu_util_pct=usage.gpu_util_pct if usage is not None else 0.0,
                cpu_util_pct=usage.cpu_util_pct if usage is not None else 0.0,
            ), usage))
        return out

    def _states(self, fresh: bool) -> list[tuple[MachineState, GpuUsage | None]]:
        with self._lock:
            at = self._cache[0] if self._cache else None
            if fresh or at is None or self._clock() - at >= self._settings.scheduler.probe_ttl_sec:
                self._cache = (self._clock(), self._measure(fresh))
            return self._cache[1]

    def status(self, fresh: bool = False) -> dict:
        """GET /api/machines 用。各台の使用状況・使えるか・使えない理由と、今選ばれる台。"""
        pairs = self._states(fresh)
        states = [s for s, _ in pairs]
        reasons = evaluate(states, self.rule)
        groups = self._settings.scheduler.reserve_groups
        sc = self._settings.scheduler
        free_now = len(sc.always_free) + sum(1 for s in states if s.reachable and not s.other_active and s.own_jobs == 0)
        return {
            "default": self._registry.default_name,
            "selected": pick(states, reasons),
            # 全体で空ける決まり（min_free）に対する余裕: 今の空き台数 - min_free（新しく使い始められる台数の上限）。無効なら None
            "min_free": sc.min_free, "min_free_slack": (free_now - sc.min_free) if sc.min_free > 0 else None,
            "machines": [{
                "name": s.name, "priority": s.priority, "reachable": s.reachable, "own_jobs": s.own_jobs,
                "gpu_util_pct": s.gpu_util_pct, "cpu_util_pct": s.cpu_util_pct, "other_active": s.other_active,
                "free_mib": s.free_mib, "total_mib": usage.total_mib if usage else None,
                "available": reasons[s.name] is None, "reason": reasons[s.name],
                "group": next((list(g) for g in groups if s.name in g), None),
            } for s, usage in pairs],
        }

    def choose(self, requested: str | None = None) -> str:
        """起動する台を決める。測り直してから判定する。requested があればその台が使えるかだけを見る。"""
        pairs = self._states(fresh=True)
        states = [s for s, _ in pairs]
        reasons = evaluate(states, self.rule)
        if requested is not None:
            if requested not in reasons:
                raise SelectionError(400, f"未登録のマシンです: {requested}（登録済み: {', '.join(reasons)}）")
            reason = reasons[requested]
            if reason is None:
                return requested
            code = 502 if not next(s for s in states if s.name == requested).reachable else 409
            raise SelectionError(code, f"{requested} には起動できません: {reason}")
        name = pick(states, reasons)
        if name is not None:
            return name
        detail = " / ".join(f"{n}: {r}" for n, r in reasons.items())
        code = 502 if not any(s.reachable for s in states) else 409
        raise SelectionError(code, f"起動できる学習用PCがありません（{detail}）")
