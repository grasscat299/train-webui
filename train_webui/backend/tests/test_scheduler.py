from train_webui.backend.scheduler import MachineState, Rule, evaluate, pick

GROUPS = (("gpu1", "gpu5"), ("gpu2", "gpu4"))
RULE = Rule(max_parallel_jobs=1, job_headroom_mib=4096, reserve_groups=GROUPS)


def st(name, priority=0, reachable=True, own=0, other=False, free=None):
    return MachineState(name, priority, reachable, own, other, free, gpu_util_pct=80.0 if other else 0.0)


def fleet(**over):
    base = {"gpu1": st("gpu1", 100), "gpu5": st("gpu5", 60), "gpu2": st("gpu2", 50), "gpu4": st("gpu4", 40)}
    base.update(over)
    return list(base.values())


def choose(states, rule=RULE):
    return pick(states, evaluate(states, rule))


def test_all_idle_prefers_5090():
    assert choose(fleet()) == "gpu1"


def test_partner_used_by_others_blocks_whole_group():
    states = fleet(gpu1=st("gpu1", 100, other=True))
    r = evaluate(states, RULE)
    assert r["gpu1"] is not None and "他の人" in r["gpu1"]
    assert r["gpu5"] is not None and "チーム用" in r["gpu5"]
    assert choose(states) == "gpu2"


def test_symmetric_other_on_5_blocks_1():
    states = fleet(gpu5=st("gpu5", 60, other=True))
    assert evaluate(states, RULE)["gpu1"] is not None
    assert choose(states) == "gpu2"


def test_own_use_of_one_blocks_new_partner_but_not_same_machine():
    two = Rule(max_parallel_jobs=2, job_headroom_mib=4096, reserve_groups=GROUPS)
    states = fleet(gpu5=st("gpu5", 60, own=1, free=20000))
    r = evaluate(states, two)
    assert r["gpu1"] is not None  # 自分が gpu5 を使っている間は gpu1 をチーム用に空ける
    assert r["gpu5"] is None      # 同じ台への追加は枠を増やさない
    assert choose(states, two) == "gpu5"


def test_everything_blocked_returns_none_with_reasons():
    states = fleet(gpu1=st("gpu1", 100, other=True), gpu2=st("gpu2", 50, other=True))
    assert choose(states) is None


def test_unreachable_partner_does_not_count_as_free():
    states = fleet(gpu5=st("gpu5", 60, reachable=False))
    r = evaluate(states, RULE)
    assert r["gpu5"] == "接続できません"
    assert r["gpu1"] is not None
    assert choose(states) == "gpu2"


def test_headroom_only_matters_when_adding_to_busy_machine():
    two = Rule(max_parallel_jobs=2, job_headroom_mib=4096, reserve_groups=())
    states = [st("a", own=1, free=1000), st("b", priority=-1)]
    assert evaluate(states, two)["a"] is not None
    assert choose(states, two) == "b"


def test_priority_tie_uses_listing_order_and_no_groups_is_plain():
    states = [st("a"), st("b")]
    assert choose(states, Rule(1, 4096)) == "a"


# ── 全体で min_free 台を空ける決まり ─────────────────────────────────────────────
MF = Rule(max_parallel_jobs=1, job_headroom_mib=4096, min_free=2, always_free=("gpu3",))


def six(**over):
    base = {n: st(n, p) for n, p in (("gpu1", 100), ("gpu2", 50), ("gpu4", 50), ("gpu5", 50), ("gpu6", 50))}
    base.update(over)
    return list(base.values())


def test_min_free_allows_use_while_two_stay_free():
    states = six()      # gpu3（GPU 無し）が常に空き 1 台 + 他の 4 台が空き → どれを使っても 2 台以上残る
    r = evaluate(states, MF)
    assert all(v is None for v in r.values())
    assert choose(states, MF) == "gpu1"


def test_min_free_blocks_the_use_that_would_leave_fewer_than_two():
    # 自分が gpu1 を使い、他の人が gpu2・gpu4 を使っている。空きは gpu3（常に）+ gpu5・gpu6 = 3。gpu5 を使うと 2 台残る
    states = six(gpu1=st("gpu1", 100, own=1, free=20000), gpu2=st("gpu2", 50, other=True), gpu4=st("gpu4", 50, other=True))
    r = evaluate(states, MF)
    assert r["gpu5"] is None and r["gpu6"] is None
    # さらに gpu5 を自分が使うと、空きは gpu3 + gpu6 = 2。gpu6 を使うと 1 台になるので使わない
    states = six(gpu1=st("gpu1", 100, own=1), gpu5=st("gpu5", 50, own=1), gpu2=st("gpu2", 50, other=True),
                 gpu4=st("gpu4", 50, other=True))
    r = evaluate(states, MF)
    assert r["gpu6"] is not None and "空けておく" in r["gpu6"]
    assert choose(states, MF) is None


def test_min_free_unreachable_machine_is_not_free_and_added_job_on_same_machine_is_allowed():
    states = six(gpu2=st("gpu2", 50, reachable=False), gpu4=st("gpu4", 50, other=True),
                 gpu1=st("gpu1", 100, own=1, free=20000))
    r = evaluate(states, Rule(max_parallel_jobs=2, job_headroom_mib=4096, min_free=2, always_free=("gpu3",)))
    assert r["gpu1"] is None                 # 同じ台にもう 1 本載せるのは、空きを増やさない・減らさない
    assert r["gpu5"] is None                 # 空き: gpu3 + gpu6 = 2（gpu5 を使うと gpu6 + gpu3 = 2）
    assert r["gpu6"] is None


def test_min_free_zero_is_disabled():
    states = six(gpu2=st("gpu2", 50, other=True), gpu4=st("gpu4", 50, other=True), gpu5=st("gpu5", 50, other=True))
    assert evaluate(states, Rule(1, 4096))["gpu6"] is None
