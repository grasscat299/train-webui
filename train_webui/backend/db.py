"""sqlite でのデータ保存（design.md §6 / CONTRACT.md §4）。ORMは使わない。

スレッド（FastAPIのリクエストハンドラ、JobManagerのポーリングスレッド）から触られるため、
操作ごとに接続を開いて閉じる（コネクションを使い回さない）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    run_group TEXT NOT NULL,
    origin TEXT NOT NULL,
    status TEXT NOT NULL,
    config_toml TEXT NOT NULL,
    git_commit TEXT,
    git_diff TEXT,
    git_untracked TEXT,
    git_info_source TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    last_heartbeat_at TEXT,
    hostname TEXT,
    pid INTEGER,
    exit_code INTEGER,
    error TEXT
);

CREATE TABLE IF NOT EXISTS syncs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    synced_at TEXT NOT NULL,
    git_commit TEXT,
    git_diff TEXT,
    git_untracked TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    pid INTEGER,
    job_dir TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    exit_code INTEGER,
    log_path TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    step INTEGER,
    value REAL,
    image_path TEXT,
    source TEXT NOT NULL,
    sent_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_run_id ON jobs(run_id);
CREATE INDEX IF NOT EXISTS idx_results_run_id ON results(run_id);
"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Database:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._path), timeout=10, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ---- runs ----------------------------------------------------------

    def create_run(self, **fields: Any) -> None:
        cols = list(fields.keys())
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT INTO runs ({', '.join(cols)}) VALUES ({placeholders})"
        with self._connect() as conn:
            conn.execute(sql, [fields[c] for c in cols])

    def get_run(self, run_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE runs SET {set_clause} WHERE run_id = ?",
                [*fields.values(), run_id],
            )

    def delete_run(self, run_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM results WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM jobs WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))

    def list_runs_by_project(self, project: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE project = ? ORDER BY created_at DESC", (project,)
            ).fetchall()
        return [dict(r) for r in rows]

    def list_run_ids(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT run_id FROM runs").fetchall()
        return {r["run_id"] for r in rows}

    def find_latest_train_run(self, project: str, group: str) -> dict | None:
        """そのgroupで最後に起動された train の run（draft以外、origin不問）。design.md §4/§5.1。"""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM runs
                WHERE project = ? AND run_group = ? AND status != 'draft'
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (project, group),
            ).fetchone()
        return dict(row) if row else None

    def get_running_cli_run(self) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE origin = 'cli' AND status = 'running' LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def list_running_cli_runs(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE origin = 'cli' AND status = 'running'"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- syncs -----------------------------------------------------------

    def create_sync(self, synced_at: str, git_commit: str, git_diff: str, git_untracked: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO syncs (synced_at, git_commit, git_diff, git_untracked) VALUES (?, ?, ?, ?)",
                (synced_at, git_commit, git_diff, git_untracked),
            )
            return int(cur.lastrowid)

    def get_last_sync(self) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM syncs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    # ---- jobs --------------------------------------------------------

    def create_job(self, **fields: Any) -> None:
        cols = list(fields.keys())
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({placeholders})"
        with self._connect() as conn:
            conn.execute(sql, [fields[c] for c in cols])

    def get_job(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE jobs SET {set_clause} WHERE job_id = ?",
                [*fields.values(), job_id],
            )

    def list_jobs_for_run(self, run_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE run_id = ? ORDER BY started_at ASC", (run_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def list_running_jobs(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM jobs WHERE status = 'running'").fetchall()
        return [dict(r) for r in rows]

    def get_running_job(self) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE status = 'running' LIMIT 1").fetchone()
        return dict(row) if row else None

    def get_running_job_for_run(self, run_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE run_id = ? AND status = 'running' LIMIT 1", (run_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_job_ids(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT job_id FROM jobs").fetchall()
        return {r["job_id"] for r in rows}

    # ---- results -------------------------------------------------------

    def create_result(self, **fields: Any) -> int:
        cols = list(fields.keys())
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT INTO results ({', '.join(cols)}) VALUES ({placeholders})"
        with self._connect() as conn:
            cur = conn.execute(sql, [fields[c] for c in cols])
            return int(cur.lastrowid)

    def list_results(self, run_id: str, kind: str | None = None, label: str | None = None) -> list[dict]:
        query = "SELECT * FROM results WHERE run_id = ?"
        params: list[Any] = [run_id]
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        if label is not None:
            query += " AND label = ?"
            params.append(label)
        query += " ORDER BY sent_at ASC, id ASC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def update_result(self, result_id: int, **fields: Any) -> None:
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE results SET {set_clause} WHERE id = ?",
                [*fields.values(), result_id],
            )

    def list_labels(self, run_ids: Iterable[str]) -> list[dict]:
        run_ids = list(run_ids)
        if not run_ids:
            return []
        placeholders = ", ".join("?" for _ in run_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT label, kind FROM results WHERE run_id IN ({placeholders}) ORDER BY label ASC",
                run_ids,
            ).fetchall()
        return [dict(r) for r in rows]
