from train_webui.backend.scheduler import MachineState, Rule, evaluate, pick

GROUPS = (("gpu1", "gpu5"), ("gpu2", "gpu4"))
RULE = Rule(max_parallel_jobs=1, job_headroom_mib=4096, reserve_groups=GROUPS)


def st(name, priority=0, reachable=True, own=0, other=False, free=None):
    return MachineState(name, priority, reachable, own, other, free, other_mib=2000 if other else 0)


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
