import pytest

from ..controller import RemoteController
from ..errors import RemoteError
from .conftest import FakeRunner, fail, ok

CPU0 = b"cpu  1000 0 1000 8000 0 0 0 0 0 0\n"
CPU1_BUSY = b"cpu  1500 0 1500 8100 0 0 0 0 0 0\n"   # 差分: 全体 1100、idle 100 → 使用率 約 90.9%


def _out(mem=b"5000\n", util=b"3\n", total=b"32607\n", cpu=CPU0 + CPU1_BUSY, leases=b"0\n", apps=b"", ps=b"24\n0\n"):
    return mem + b"---\n" + util + b"---\n" + total + b"---\n" + cpu + b"---\n" + leases + b"---\n" + apps + b"---\n" + ps


def test_gpu_usage_reads_utilization_cpu_and_leases(settings_factory):
    runner = FakeRunner(responses=[ok(stdout=_out(util=b"45\n10\n", leases=b"2\n"))])
    usage = RemoteController(settings_factory(), runner=runner).gpu_usage()
    assert (usage.total_used_mib, usage.total_mib, usage.gpu_util_pct, usage.leases) == (5000, 32607, 45.0, 2)
    assert usage.cpu_util_pct == pytest.approx(90.909, abs=0.01)
    cmd = runner.calls[0]["args"][-1]
    assert "utilization.gpu" in cmd and "/proc/stat" in cmd and "leases" in cmd


def test_gpu_usage_sums_multiple_gpus_memory(settings_factory):
    runner = FakeRunner(responses=[ok(stdout=_out(mem=b"100\n200\n"))])
    assert RemoteController(settings_factory(), runner=runner).gpu_usage().total_used_mib == 300


def test_gpu_usage_reads_own_process_gpu_memory_and_cpu(settings_factory):
    # コンテナ内から見える GPU プロセスのメモリと、コンテナ内プロセスの CPU 合計（コア数 24、合計 1200% → 全体の 50%）
    runner = FakeRunner(responses=[ok(stdout=_out(apps=b"16556\n", ps=b"24\n1200.0\n"))])
    usage = RemoteController(settings_factory(), runner=runner).gpu_usage()
    assert usage.own_mib == 16556 and usage.own_cpu_pct == pytest.approx(50.0)


def test_gpu_usage_ssh_failure_raises(settings_factory):
    runner = FakeRunner(responses=[fail(stderr=b"nvidia-smi: command not found")])
    with pytest.raises(RemoteError):
        RemoteController(settings_factory(), runner=runner).gpu_usage()


def test_launch_writes_lease_and_poll_removes_it_when_dead(settings_factory):
    from ..types import LaunchSpec
    c = RemoteController(settings_factory(), runner=FakeRunner(responses=[]))
    spec = LaunchSpec(run_id="20261018_000000", job_id="j1", kind="train", project_dir="src/p", config_toml="", parent_run_id=None)
    cmd = c._build_launch_command(spec, "/r/runs/20261018_000000/j1")
    assert "leases/20261018_000000" in cmd and cmd.index("leases/") < cmd.index("setsid")
    runner = FakeRunner(responses=[ok(stdout=b"DEAD\n0\n")])
    RemoteController(settings_factory(), runner=runner).poll(1234, "/r/runs/20261018_000000/j1")
    assert "rm -f" in runner.calls[0]["args"][-1] and "leases" in runner.calls[0]["args"][-1]
