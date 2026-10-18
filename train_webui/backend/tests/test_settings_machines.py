import pytest

from train_webui.backend.remote import RemoteRegistry
from train_webui.backend.settings import parse_machines, settings_from_dict

_COMMON = dict(user="student", port=2222, key_path="k", python="py", stop_timeout_sec=10)
_PATHS = dict(repo_dir="/r", run_root="/runs")


def test_legacy_single_remote_becomes_default():
    machines, default = parse_machines({"host": "h", **_COMMON, **_PATHS})
    assert list(machines) == ["default"] and default == "default"
    assert machines["default"].priority == 0


def test_multi_remote_merges_defaults_and_keeps_order():
    raw = {
        "defaults": {**_COMMON, **_PATHS},
        "gpu1": {"host": "gpu1", "priority": 100},
        "gpu2": {"host": "gpu2", "port": 2223},
    }
    machines, default = parse_machines(raw)
    assert list(machines) == ["gpu1", "gpu2"] and default == "gpu1"
    assert machines["gpu1"].priority == 100 and machines["gpu1"].port == 2222
    assert machines["gpu2"].port == 2223 and machines["gpu2"].repo_dir == "/r"


def test_no_machines_is_an_error():
    with pytest.raises(ValueError):
        parse_machines({"defaults": {}})


def test_registry_builds_controller_per_machine_with_its_remote():
    raw = {
        "remote": {"defaults": {**_COMMON, **_PATHS}, "a": {"host": "ha"}, "b": {"host": "hb"}},
        "server": {"host": "0.0.0.0", "port": 1, "public_url": "u", "db_path": "d", "media_dir": "m"},
        "log": {"poll_interval_sec": 1}, "heartbeat": {"lost_after_sec": 1}, "sync": {"exclude": []},
    }
    settings = settings_from_dict(raw)
    reg = RemoteRegistry.from_settings(settings, factory=lambda s: s.remote.host)
    assert reg.names == ["a", "b"] and reg.default_name == "a"
    assert reg.get("b") == "hb"
    with pytest.raises(KeyError):
        reg.get("zzz")


def test_scheduler_min_free_and_always_free_are_parsed():
    from train_webui.backend.settings import _parse_scheduler
    machines, _ = parse_machines({"defaults": {**_COMMON, **_PATHS}, "a": {"host": "a"}})
    sc = _parse_scheduler({"min_free": 2, "always_free": ["gpu3"]}, machines)
    assert sc.min_free == 2 and sc.always_free == ("gpu3",) and sc.reserve_groups == ()
    sc0 = _parse_scheduler({}, machines)
    assert sc0.min_free == 0 and sc0.always_free == ()
