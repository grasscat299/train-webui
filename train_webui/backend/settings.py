"""server.toml を読み、型付きで公開する（design.md §1.5）。backend 内の設定の唯一の参照口。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import tomli

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = REPO_ROOT / "train_webui" / "server.toml"


@dataclass(frozen=True)
class RemoteSettings:
    host: str
    user: str
    port: int
    key_path: str
    repo_dir: str
    sif_path: str
    python: str
    run_root: str
    singularity_args: list[str]
    stop_timeout_sec: float


@dataclass(frozen=True)
class ServerSettings:
    host: str
    port: int
    public_url: str
    db_path: Path
    media_dir: Path


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
