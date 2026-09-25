"""server.toml を読み、型付きで公開する（design.md §1.5）。backend 内の設定の唯一の参照口。"""
from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class ServerSettings:
    host: str
    port: int
    public_url: str
    db_path: Path
    media_dir: Path
    allowed_networks: tuple[str, ...] = DEFAULT_ALLOWED_NETWORKS


@dataclass(frozen=True)
class Settings:
    remote: RemoteSettings
    server: ServerSettings
    poll_interval_sec: float
    lost_after_sec: float
    sync_exclude: list[str]
    repo_root: Path


def settings_from_dict(raw: dict, repo_root: Path = REPO_ROOT) -> Settings:
    srv = dict(raw["server"])
    srv["db_path"] = repo_root / srv["db_path"]
    srv["media_dir"] = repo_root / srv["media_dir"]
    if "allowed_networks" in srv:
        srv["allowed_networks"] = tuple(srv["allowed_networks"])
    return Settings(
        remote=RemoteSettings(**raw["remote"]),
        server=ServerSettings(**srv),
        poll_interval_sec=float(raw["log"]["poll_interval_sec"]),
        lost_after_sec=float(raw["heartbeat"]["lost_after_sec"]),
        sync_exclude=list(raw["sync"]["exclude"]),
        repo_root=repo_root,
    )


def load_settings(path: Path = DEFAULT_PATH) -> Settings:
    with open(path, "rb") as f:
        return settings_from_dict(tomli.load(f))
