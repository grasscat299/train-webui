"""backend 本体（JobManager 等）と学習用PC操作部分（RemoteController）の境界（CONTRACT.md §3）。

このファイルの型・シグネチャは両者の取り決めなので、変更する場合は CONTRACT.md と揃えること。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class SyncInfo:
    """コード同期時点のローカルgit情報。"""
    git_commit: str
    git_diff: str
    git_untracked: str  # JSON文字列 {"相対パス": "中身", ...}（テキストかつ1MB以下のみ）


@dataclass(frozen=True)
class LaunchSpec:
    run_id: str
    job_id: str
    kind: Literal["train", "infer"]
    project_dir: str   # リポジトリ直下からの相対パス（例 "src/world_model_v1"）
    config_toml: str   # 学習用PCの <repo_dir>/<project_dir>/config.toml に書き込む内容
    parent_run_id: str | None = None  # 引き継ぎ学習の元の run（その run の重みを初期値にする）


@dataclass(frozen=True)
class LaunchResult:
    pid: int           # setsid で起動したプロセス（= プロセスグループリーダー）のPID
    job_dir: str       # 学習用PC上の <run_root>/<run_id>/<job_id>


@dataclass(frozen=True)
class JobState:
    alive: bool
    exit_code: int | None  # alive=False かつ run.exit があればその値。無ければ None


@dataclass(frozen=True)
class GpuUsage:
    total_used_mib: int  # nvidia-smi の GPU 全体の使用メモリ（コンテナの外のプロセスも含む。複数GPUなら合計）
    own_mib: int         # そのうち、コンテナ内から見える（= 自分たちの）プロセスが使っているメモリ
    total_mib: int = 0   # GPU の全メモリ（複数GPUなら合計）。0 = 不明（空きメモリの確認は飛ばす）

    @property
    def free_mib(self) -> int:
        return max(self.total_mib - self.total_used_mib, 0)

    @property
    def other_mib(self) -> int:
        """自分たち以外（コンテナの外 = 他の人）が使っているメモリ。"""
        return max(self.total_used_mib - self.own_mib, 0)


class CommandRunner(Protocol):
    """サブプロセス実行の抽象。テストでは偽物に差し替えて、組み立てたコマンドを検査する。"""

    def run(self, args: list[str], *, input: bytes | None = None,
            timeout: float | None = None) -> subprocess.CompletedProcess[bytes]: ...


class RemoteControllerProtocol(Protocol):
    def sync_code(self, source_dir: Path | None = None) -> SyncInfo:
        """ローカルのリポジトリ全体（source_dir があればそのディレクトリ）を rsync --delete で学習用PCへ送り、
        送った時点のgit情報を返す。"""

    def remove_run_files(self, run_id: str) -> None:
        """学習用PCの <run_root>/<run_id>/（重み・ログなど）を消す。無ければ何もしない。"""

    def launch(self, spec: LaunchSpec) -> LaunchResult:
        """config.toml を書き込み、setsid+nohup でジョブを起動する（sync_code は呼ばない）。"""

    def stop(self, pid: int) -> None:
        """プロセスグループへ SIGTERM、stop_timeout_sec 後も生きていれば SIGKILL。"""

    def poll(self, pid: int, job_dir: str) -> JobState:
        """kill -0 と run.exit でジョブの生存・終了コードを返す。"""

    def read_log(self, job_dir: str, offset: int) -> tuple[bytes, int]:
        """run.log の offset バイト目以降を返す（新しい offset も返す）。ファイルが無ければ (b"", offset)。"""

    def gpu_usage(self) -> GpuUsage:
        """学習用PCのGPUの使用メモリを、全体と自分たちのプロセスの分に分けて返す（起動前の使用中チェック用）。"""
