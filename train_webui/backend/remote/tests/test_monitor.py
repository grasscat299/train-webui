import pytest

from ..controller import RemoteController
from ..errors import RemoteError
from .conftest import FakeRunner, fail, ok

# cpu0 は 100 のうち暇 50（idle=40+iowait=10）で使用率 50%、cpu1 は全部 user で 100%。cpu（全体）は 75%
_BEFORE = "cpu  0 0 0 0 0 0 0 0\ncpu0 0 0 0 0 0 0 0 0\ncpu1 0 0 0 0 0 0 0 0\nintr 1\n"
_AFTER = "cpu  100 0 0 50 0 0 0 0\ncpu0 50 0 0 40 10 0 0 0\ncpu1 50 0 0 0 0 0 0 0\nintr 2\n"
_GPU = "0, NVIDIA GeForce GTX 1080, 87, 5000, 8192, 71, 150.5\n"


def _out(gpu: str = _GPU) -> bytes:
    return f"{_BEFORE}---\n{_AFTER}---\n{gpu}".encode()


def test_monitor_parses_cpu_cores_and_gpu(settings_factory):
    runner = FakeRunner(responses=[ok(stdout=_out())])
    m = RemoteController(settings_factory(), runner=runner).monitor()
    assert m["cpu_cores"] == [50.0, 100.0]
    assert m["cpu_total"] == pytest.approx(66.7, abs=0.1)
    assert m["gpus"] == [{"index": 0, "name": "NVIDIA GeForce GTX 1080", "util": 87.0, "mem_used_mib": 5000.0,
                          "mem_total_mib": 8192.0, "temp_c": 71.0, "power_w": 150.5}]


def test_monitor_without_gpu_or_na_values(settings_factory):
    m = RemoteController(settings_factory(), runner=FakeRunner(responses=[ok(stdout=_out(""))])).monitor()
    assert m["gpus"] == []
    na = "0, X, 10, 1, 2, [N/A], [N/A]\n"
    m = RemoteController(settings_factory(), runner=FakeRunner(responses=[ok(stdout=_out(na))])).monitor()
    assert m["gpus"][0]["temp_c"] is None and m["gpus"][0]["power_w"] is None


def test_monitor_ssh_failure_raises(settings_factory):
    runner = FakeRunner(responses=[fail(stderr=b"connection refused")])
    with pytest.raises(RemoteError):
        RemoteController(settings_factory(), runner=runner).monitor()
