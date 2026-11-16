"""server.toml を読み、型付きで公開する（design.md §1.5）。backend 内の設定の唯一の参照口。"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import tomli

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = REPO_ROOT / "train_webui" / "server.toml"
# tailscale（CGNAT帯とIPv6のULA帯）とこのPC自身からの接続だけを受け付ける
DEFAULT_ALLOWED_NETWORKS = ("127.0.0.0/8", "::1/128", "100.64.0.0/10", "fd7a:115c:a1e0::/48")


@dataclass(frozen=True)
class RemoteSettings:
    host: str
    user: str
    port: int
    key_path: str
    repo_dir: str
    python: str
    run_root: str
    stop_timeout_sec: float
    priority: int = 0  # 自動選択で大きいほど先に選ぶ


@dataclass(frozen=True)
class SchedulerSettings:
    """学習用PCの自動選択のルール（machines.py / scheduler.py）。"""
    # 同じチームが使う台のグループ。他の人が使っていてグループ内に空き（誰も使っていない台）が残らなくなる台は、自分では使わない
    reserve_groups: tuple[tuple[str, ...], ...] = ()
    # 自分が新しく台を使い始めた後も、誰も使っていない台を全体でこの台数だけ残す（0 = 無効）。always_free は GPU が無く使えない台で、常に空きに数える
    min_free: int = 0
    always_free: tuple[str, ...] = ()
    # 他の人の使用が見えなくなってからも、これだけの秒数は使用中とみなす（学習の合間にメモリが空く瞬間があるため）
    other_grace_sec: float = 300.0
    # 他の人が使っているとみなす使用率（自分たちの lease が無い台で、GPU か CPU のどちらかがこの値以上）。メモリ使用量では判断しない
    gpu_util_busy_pct: float = 10.0
    cpu_util_busy_pct: float = 30.0
    # GET /api/machines の結果を使い回す秒数（起動の判定は毎回測り直す）
    probe_ttl_sec: float = 5.0
    # つながらなかった台を、画面の更新（起動の判定ではない）で測り直すまでの秒数。不通の台の接続待ちで一覧が遅くならないように
    unreachable_retry_sec: float = 30.0


@dataclass(frozen=True)
class ServerSettings:
    host: str
    port: int
    public_url: str
    db_path: Path
    media_dir: Path
    allowed_networks: tuple[str, ...] = DEFAULT_ALLOWED_NETWORKS


@dataclass(frozen=True)
class MonitorSettings:
    """Monitor タブの取得先（gpu5 の lab_monitor server。lab_monitor/README.md）。空なら使えない。"""
    url: str = ""
    timeout_sec: float = 8.0


@dataclass(frozen=True)
class NotifySettings:
    """「台が空いているのに Claude が止まっている」ときの通知（idle_alert.py）。"""
    enabled: bool = True
    idle_after_sec: float = 600.0      # Claude の最後の活動（heartbeat）からこの秒数たったら「止まっている」とみなす
    renotify_sec: float = 1800.0       # 同じ通知を、この秒数あけて繰り返す（webhook）
    check_interval_sec: float = 30.0
    webhook_url: str = ""              # 空なら画面のバナーだけ。値があれば、通知のたびに POST する（ntfy.sh など。{"message": ...} の JSON）


@dataclass(frozen=True)
class Settings:
    remote: RemoteSettings  # 既定のマシン。1台構成の呼び出し側（check_remote・既存テスト）はこれを読む
    server: ServerSettings
    poll_interval_sec: float
    lost_after_sec: float
    sync_exclude: list[str]
    repo_root: Path
    # 学習用PCへ送るコードの元（[sync] source_ref）。git の ref（master・タグ・commit）を書くと、その commit の中身を送る
    # （作業ツリーの HEAD・未コミットの変更には依らない）。空なら従来どおり作業ツリーをそのまま送る
    sync_ref: str = "master"
    # 起動前チェック: 自分たち以外が学習用PCのGPUメモリをこれより多く使っていたら起動しない（design.md §4）
    gpu_busy_threshold_mib: float = 1024.0
    # 同時に動かせるジョブ（学習・推論）の数。1 = 従来どおり排他。2 以上にすると、ジョブごとにコードを複製して動かし、
    # 起動前に GPU の空きメモリ（job_headroom_mib 以上）も確認する
    max_parallel_jobs: int = 1
    job_headroom_mib: float = 4096.0
    # project の検出（main.py の AST 解析を含むリポジトリ全体の走査）結果を使い回す秒数。0 なら毎回探す
    project_scan_interval_sec: float = 30.0
    # 名前 → 接続情報。1台構成（旧 [remote]）のときは {"default": remote}
    machines: dict[str, RemoteSettings] = field(default_factory=dict)
    default_machine: str = "default"
    scheduler: SchedulerSettings = field(default_factory=SchedulerSettings)
    monitor: MonitorSettings = field(default_factory=MonitorSettings)
    notify: NotifySettings = field(default_factory=NotifySettings)

    def __post_init__(self) -> None:
        if not self.machines:  # remote だけ渡された場合（テスト等）は、それを唯一のマシンとして扱う
            object.__setattr__(self, "machines", {self.default_machine: self.remote})

    def machine_settings(self, name: str) -> Settings:
        """name のマシンを remote に据えた Settings（RemoteController はこれを渡して作る）。"""
        if name not in self.machines:
            raise KeyError(f"未登録のマシン: {name}（登録済み: {', '.join(self.machines)}）")
        return replace(self, remote=self.machines[name])


def parse_machines(raw_remote: dict) -> tuple[dict[str, RemoteSettings], str]:
    """[remote] を読む。旧形式（host を直に書く）は "default" 1台。
    新形式は [remote.defaults]（共通値）と [remote.<名前>]（台ごと。defaults を上書き）。
    最初に書いた台が既定のマシン。"""
    if "host" in raw_remote:
        return {"default": RemoteSettings(**raw_remote)}, "default"
    common = dict(raw_remote.get("defaults", {}))
    machines = {name: RemoteSettings(**{**common, **conf})
                for name, conf in raw_remote.items() if name != "defaults"}
    if not machines:
        raise ValueError("[remote] にマシンが1台もありません")
    return machines, next(iter(machines))


def _parse_scheduler(raw: dict, machines: dict[str, RemoteSettings]) -> SchedulerSettings:
    groups = tuple(tuple(g) for g in raw.get("reserve_groups", []))
    for g in groups:
        for name in g:
            if name not in machines:
                raise ValueError(f"[scheduler] reserve_groups に未登録のマシン {name!r} があります（登録済み: {', '.join(machines)}）")
    return SchedulerSettings(
        reserve_groups=groups,
        min_free=int(raw.get("min_free", 0)),
        always_free=tuple(str(n) for n in raw.get("always_free", [])),
        other_grace_sec=float(raw.get("other_grace_sec", 300)),
        gpu_util_busy_pct=float(raw.get("gpu_util_busy_pct", 10)),
        cpu_util_busy_pct=float(raw.get("cpu_util_busy_pct", 30)),
        probe_ttl_sec=float(raw.get("probe_ttl_sec", 5)),
        unreachable_retry_sec=float(raw.get("unreachable_retry_sec", 30)),
    )


def settings_from_dict(raw: dict, repo_root: Path = REPO_ROOT) -> Settings:
    srv = dict(raw["server"])
    srv["db_path"] = repo_root / srv["db_path"]
    srv["media_dir"] = repo_root / srv["media_dir"]
    if "allowed_networks" in srv:
        srv["allowed_networks"] = tuple(srv["allowed_networks"])
    machines, default_machine = parse_machines(raw["remote"])
    return Settings(
        remote=machines[default_machine],
        machines=machines,
        default_machine=default_machine,
        server=ServerSettings(**srv),
        poll_interval_sec=float(raw["log"]["poll_interval_sec"]),
        lost_after_sec=float(raw["heartbeat"]["lost_after_sec"]),
        sync_exclude=list(raw["sync"]["exclude"]),
        repo_root=repo_root,
        sync_ref=str(raw["sync"].get("source_ref", "master")).strip(),
        gpu_busy_threshold_mib=float(raw.get("gpu", {}).get("busy_threshold_mib", 1024)),
        max_parallel_jobs=int(raw.get("gpu", {}).get("max_parallel_jobs", 1)),
        job_headroom_mib=float(raw.get("gpu", {}).get("job_headroom_mib", 4096)),
        scheduler=_parse_scheduler(raw.get("scheduler", {}), machines),
        monitor=MonitorSettings(url=str(raw.get("monitor", {}).get("url", "")).rstrip("/"),
                                timeout_sec=float(raw.get("monitor", {}).get("timeout_sec", 8))),
        notify=NotifySettings(**{k: type(getattr(NotifySettings, k))(v) for k, v in raw.get("notify", {}).items() if hasattr(NotifySettings, k)}),
        project_scan_interval_sec=float(raw.get("log", {}).get("project_scan_interval_sec", 30)),
    )


def load_settings(path: Path = DEFAULT_PATH) -> Settings:
    with open(path, "rb") as f:
        return settings_from_dict(tomli.load(f))
