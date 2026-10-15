import pytest

from ..controller import RemoteController
from ..errors import RemoteError
from .conftest import FakeRunner, fail, ok


def test_gpu_usage_subtracts_own_processes(settings_factory):
    # GPU全体 5000MiB のうち、pid 1457（ps にも見える = 自分たち）が 1142MiB、pid 99999 は ps に無い（= 他の人）
    stdout = b"5000\n---\n1457, 1142\n99999, 3000\n---\n    1\n   14\n 1457\n"
    runner = FakeRunner(responses=[ok(stdout=stdout)])
    usage = RemoteController(settings_factory(), runner=runner).gpu_usage()
    assert usage.total_used_mib == 5000
    assert usage.own_mib == 1142
    assert usage.other_mib == 3858
    cmd = runner.calls[0]["args"][-1]
    assert "nvidia-smi --query-gpu=memory.used" in cmd and "ps -eo pid" in cmd


def test_gpu_usage_sums_multiple_gpus_and_handles_no_apps(settings_factory):
    runner = FakeRunner(responses=[ok(stdout=b"100\n200\n---\n---\n1\n")])
    usage = RemoteController(settings_factory(), runner=runner).gpu_usage()
    assert (usage.total_used_mib, usage.own_mib, usage.other_mib) == (300, 0, 300)


def test_gpu_usage_ssh_failure_raises(settings_factory):
    runner = FakeRunner(responses=[fail(stderr=b"nvidia-smi: command not found")])
    with pytest.raises(RemoteError):
        RemoteController(settings_factory(), runner=runner).gpu_usage()


def test_gpu_usage_reads_total_memory_when_present(settings_factory):
    stdout = b"6011\n---\n1457, 5962\n---\n 1457\n---\n32607\n"
    runner = FakeRunner(responses=[ok(stdout=stdout)])
    usage = RemoteController(settings_factory(), runner=runner).gpu_usage()
    assert (usage.total_used_mib, usage.total_mib, usage.free_mib) == (6011, 32607, 26596)
