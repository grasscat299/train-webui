"""学習を載せるマシンの自動選択のルール（純粋関数。接続や時間は machines.py 側で扱う）。

ルール（ほかの利用者と共用の台）:
- 他の人が使っている台、自分のジョブで満杯の台、つながらない台は使わない。
- 予約グループ（同じチームが使う台の組）では、自分が新しく台を使い始めた後にも、誰も使っていない台が
  グループ内に1台は残るようにする。つながらない台は、チームも使えないので空きに数えない。
- 全体の空き（min_free）: 自分が新しく台を使い始めた後にも、誰も使っていない台（自分のジョブも無い台）が
  全体で min_free 台は残るようにする。GPU が無く使えない台（always_free）は常に空きに数える。つながらない台は数えない。
- 使える台の中では priority の大きい順（同じなら設定に書いた順）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MachineState:
    name: str
    priority: int
    reachable: bool
    own_jobs: int            # 自分（WebUI）のジョブでこの台で動いている数
    other_active: bool       # 他の人が使っている（直近も含む）
    free_mib: int | None     # GPU の空きメモリ。不明なら None
    other_mib: int = 0       # 表示用


@dataclass(frozen=True)
class Rule:
    max_parallel_jobs: int
    job_headroom_mib: float
    reserve_groups: tuple[tuple[str, ...], ...] = ()
    min_free: int = 0                           # 全体で空けておく台数（0 = 無効）
    always_free: tuple[str, ...] = ()           # GPU が無いなど、使えないが空きとして数える台（registry に無い名前でもよい）


def evaluate(states: list[MachineState], rule: Rule) -> dict[str, str | None]:
    """台ごとに、今新しくジョブを載せてよければ None、だめなら理由。"""
    by_name = {s.name: s for s in states}
    out: dict[str, str | None] = {}
    for s in states:
        out[s.name] = _reason(s, by_name, rule)
    return out


def _reason(s: MachineState, by_name: dict[str, MachineState], rule: Rule) -> str | None:
    if not s.reachable:
        return "接続できません"
    if s.other_active:
        return f"他の人が使用中です（{s.other_mib} MiB）" if s.other_mib else "他の人が直近まで使用していました"
    if s.own_jobs >= max(1, rule.max_parallel_jobs):
        return "自分の別のジョブで満杯です"
    if s.own_jobs > 0 and s.free_mib is not None and s.free_mib < rule.job_headroom_mib:
        return f"GPU の空きメモリが足りません（空き {s.free_mib} MiB、必要 {rule.job_headroom_mib:g} MiB）"
    if s.own_jobs == 0:  # 新しく台を使い始めるときだけ、チームの枠を確認する
        for group in rule.reserve_groups:
            if s.name not in group:
                continue
            others = [by_name[n] for n in group if n != s.name and n in by_name]
            if not any(o.reachable and not o.other_active and o.own_jobs == 0 for o in others):
                return f"同じグループ（{', '.join(group)}）にチーム用の空き台が残らなくなるため使いません"
    if s.own_jobs == 0 and rule.min_free > 0:  # 新しく台を使い始めるときだけ、全体の空きを確認する
        free = len(rule.always_free) + sum(
            1 for o in by_name.values() if o.name != s.name and o.reachable and not o.other_active and o.own_jobs == 0
        )
        if free < rule.min_free:
            return f"全体で{rule.min_free}台は空けておく決まりのため使いません（使うと空きが{free}台になります）"
    return None


def pick(states: list[MachineState], reasons: dict[str, str | None]) -> str | None:
    order = {s.name: i for i, s in enumerate(states)}
    ok = [s for s in states if reasons[s.name] is None]
    if not ok:
        return None
    return min(ok, key=lambda s: (-s.priority, order[s.name])).name
