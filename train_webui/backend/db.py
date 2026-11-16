"""sqlite でのデータ保存（design.md §6 / CONTRACT.md §4）。ORMは使わない。

スレッド（FastAPIのリクエストハンドラ、JobManagerのポーリングスレッド）から触られるため、
操作ごとに接続を開いて閉じる（コネクションを使い回さない）。
"""
from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    run_group TEXT NOT NULL,
    display_name TEXT,
    comment TEXT NOT NULL DEFAULT '',
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

-- run が学習用PCに残した成果物（重み・サンプルなど）。本体は学習用PCの run_root/<run_id>/artifacts/ にあり、ここには場所だけ持つ
CREATE TABLE IF NOT EXISTS artifacts (
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    size INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, name)
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
    name TEXT,
    image_path TEXT,
    source TEXT NOT NULL,
    sent_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_run_id ON jobs(run_id);

-- group の表示名。group_id（= runs.run_group = config の [wandb].group）は固定で、group_name だけ編集できる
CREATE TABLE IF NOT EXISTS groups (
    project TEXT NOT NULL,
    group_id TEXT NOT NULL,
    group_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project, group_id)
);
CREATE INDEX IF NOT EXISTS idx_results_run_id ON results(run_id);

CREATE TABLE IF NOT EXISTS plots (
    run_id TEXT NOT NULL,
    label TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT,
    xlabel TEXT NOT NULL,
    ylabel TEXT NOT NULL,
    xscale TEXT NOT NULL,
    yscale TEXT NOT NULL,
    xmin REAL,
    xmax REAL,
    ymin REAL,
    ymax REAL,
    defined INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, label)
);

CREATE TABLE IF NOT EXISTS plot_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    label TEXT NOT NULL,
    series TEXT NOT NULL,
    x REAL NOT NULL,
    y REAL NOT NULL,
    step INTEGER,
    source TEXT NOT NULL,
    sent_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plot_points_run_label ON plot_points(run_id, label, id);
CREATE INDEX IF NOT EXISTS idx_plot_points_run_label_series ON plot_points(run_id, label, series, id);

-- Result タブで名前を付けて残した比較（比較する run と、表示中・非表示のラベル。いずれも JSON の配列）
CREATE TABLE IF NOT EXISTS comparisons (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    run_ids TEXT NOT NULL,
    labels TEXT NOT NULL,
    hidden_labels TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 画面で今見ている構成（タブ・比較中の run・表示中のラベル）。1行だけ持ち、Claude が cli の view で読む
CREATE TABLE IF NOT EXISTS view_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Database:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        # 数万行を fetchall する読み出しは、複数スレッドで同時に走らせると行ごとの GIL の取り合いで極端に遅くなる
        # （6 本同時で 0.5 秒の仕事が 24 秒になった）ので、1 本ずつ流す
        self._bulk_read_lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._path), timeout=30, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            # WAL: 読み取り（画面の定期更新・グラフの取得）が書き込みを止めない。既定の方式だと、読み取りが続く間
            # ジョブ起動後の pid の記録などが `database is locked` で失敗し、ジョブが「起動処理中」のまま固まった。
            # 設定はDBファイルに残るので、一度切り替えれば以降は自動
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            # display_name 追加前に作られたDBの移行: 列を足し、既存runに group 内の作成順で v1, v2, ... を振る
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
            if "display_name" not in cols:
                conn.execute("ALTER TABLE runs ADD COLUMN display_name TEXT")
            if "comment" not in cols:  # run へのコメント（メモ）。追加前のDBは空文字で移行
                conn.execute("ALTER TABLE runs ADD COLUMN comment TEXT NOT NULL DEFAULT ''")
            if "code_commit" not in cols:  # 起動時のコードを残した commit（refs/webui/runs/<run_id> が指す）。無ければ NULL
                conn.execute("ALTER TABLE runs ADD COLUMN code_commit TEXT")
            if "parent_run_id" not in cols:  # 引き継ぎ学習の元になった run（config の init_from）。追加前のDBは NULL
                conn.execute("ALTER TABLE runs ADD COLUMN parent_run_id TEXT")
            if "machine" not in cols:  # 学習を動かした学習用PCの名前（server.toml の [remote.<名前>]）。追加前のDBは NULL = 既定のマシン
                conn.execute("ALTER TABLE runs ADD COLUMN machine TEXT")
            if "sync_ref" not in cols:  # この run だけ送るコードの ref を上書きした値（NULL = server.toml の [sync] source_ref。"" = 作業ツリー）
                conn.execute("ALTER TABLE runs ADD COLUMN sync_ref TEXT")
            jcols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "machine" not in jcols:
                conn.execute("ALTER TABLE jobs ADD COLUMN machine TEXT")
            rows = conn.execute(
                "SELECT run_id, project, run_group FROM runs WHERE display_name IS NULL "
                "ORDER BY created_at ASC, run_id ASC"
            ).fetchall()
            for r in rows:
                name = self._next_display_name(conn, r["project"], r["run_group"])
                conn.execute("UPDATE runs SET display_name = ? WHERE run_id = ?", (name, r["run_id"]))
            # group の説明（モデルの構成を書いた Markdown。Claude が読む）の追加前のDBは空文字で移行
            rcols = {r["name"] for r in conn.execute("PRAGMA table_info(results)").fetchall()}
            if "name" not in rcols:
                conn.execute("ALTER TABLE results ADD COLUMN name TEXT")
            gcols = {r["name"] for r in conn.execute("PRAGMA table_info(groups)").fetchall()}
            if "description" not in gcols:
                conn.execute("ALTER TABLE groups ADD COLUMN description TEXT NOT NULL DEFAULT ''")
            # groups 追加前のDBの移行: run にある group を、今の名前をそのまま group_name にして登録する
            conn.execute(
                "INSERT OR IGNORE INTO groups (project, group_id, group_name, created_at) "
                "SELECT project, run_group, run_group, MIN(created_at) FROM runs GROUP BY project, run_group"
            )

    @staticmethod
    def _next_display_name(conn: sqlite3.Connection, project: str, group: str) -> str:
        rows = conn.execute(
            "SELECT display_name FROM runs WHERE project = ? AND run_group = ? AND display_name IS NOT NULL",
            (project, group),
        ).fetchall()
        used = [int(r["display_name"][1:]) for r in rows if re.fullmatch(r"v\d+", r["display_name"])]
        return f"v{max(used, default=0) + 1}"

    def next_display_name(self, project: str, group: str) -> str:
        """group 内で次に振る表示名（v1, v2, ...）= 今ある最大の番号 + 1。途中の番号が削除で空いても詰めない（既存runの表示名を変えないため）。"""
        with self._connect() as conn:
            return self._next_display_name(conn, project, group)

    # ---- groups --------------------------------------------------------

    def ensure_group(self, project: str, group_id: str, group_name: str | None = None) -> None:
        """group が未登録なら登録する（group_name 省略時は group_id と同じ）。登録済みなら何もしない。"""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO groups (project, group_id, group_name, created_at) VALUES (?, ?, ?, ?)",
                (project, group_id, group_name or group_id, now_iso()),
            )

    def group_exists(self, project: str, group_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM groups WHERE project = ? AND group_id = ?", (project, group_id),
            ).fetchone()
        return row is not None

    def group_names(self, project: str) -> dict[str, str]:
        """{group_id: group_name}"""
        with self._connect() as conn:
            rows = conn.execute("SELECT group_id, group_name FROM groups WHERE project = ?", (project,)).fetchall()
        return {r["group_id"]: r["group_name"] for r in rows}

    def rename_group(self, project: str, group_id: str, group_name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE groups SET group_name = ? WHERE project = ? AND group_id = ?", (group_name, project, group_id),
            )

    def group_descriptions(self, project: str) -> dict[str, str]:
        """{group_id: description}（モデルの構成の説明。Markdown）"""
        with self._connect() as conn:
            rows = conn.execute("SELECT group_id, description FROM groups WHERE project = ?", (project,)).fetchall()
        return {r["group_id"]: r["description"] for r in rows}

    def set_group_description(self, project: str, group_id: str, description: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE groups SET description = ? WHERE project = ? AND group_id = ?", (description, project, group_id),
            )

    def next_group_name(self, project: str) -> str:
        """新規groupの既定の表示名（group1, group2, ...）。project 内で使われていない最小の番号。"""
        used = set(self.group_names(project).values())
        n = 1
        while f"group{n}" in used:
            n += 1
        return f"group{n}"

    # ---- runs ----------------------------------------------------------

    def create_run(self, **fields: Any) -> None:
        cols = list(fields.keys())
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT INTO runs ({', '.join(cols)}) VALUES ({placeholders})"
        with self._connect() as conn:
            conn.execute(sql, [fields[c] for c in cols])

    # ---- comparisons ---------------------------------------------------

    def list_comparisons(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM comparisons ORDER BY created_at, id").fetchall()
        return [dict(r) for r in rows]

    def get_comparison(self, comparison_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM comparisons WHERE id = ?", (comparison_id,)).fetchone()
        return dict(row) if row else None

    def create_comparison(self, comparison_id: str, name: str, run_ids: str, labels: str, hidden_labels: str) -> None:
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO comparisons (id, name, run_ids, labels, hidden_labels, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (comparison_id, name, run_ids, labels, hidden_labels, now, now),
            )

    def update_comparison(self, comparison_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        with self._connect() as conn:
            conn.execute(f"UPDATE comparisons SET {set_clause} WHERE id = ?", [*fields.values(), comparison_id])

    def delete_comparison(self, comparison_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM comparisons WHERE id = ?", (comparison_id,))

    def get_view_state(self) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT data, updated_at FROM view_state WHERE id = 1").fetchone()
        return {"data": row["data"], "updated_at": row["updated_at"]} if row else None

    def set_view_state(self, data: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO view_state (id, data, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                (data, now_iso()),
            )

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
            conn.execute("DELETE FROM plot_points WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM plots WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM jobs WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM artifacts WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))

    def upsert_artifact(self, run_id: str, name: str, kind: str, path: str, size: int | None, updated_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO artifacts (run_id, name, kind, path, size, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, name) DO UPDATE SET kind = excluded.kind, path = excluded.path, "
                "size = excluded.size, updated_at = excluded.updated_at",
                (run_id, name, kind, path, size, updated_at),
            )

    def list_artifacts(self, run_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM artifacts WHERE run_id = ? ORDER BY name", (run_id,)).fetchall()
        return [dict(r) for r in rows]

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

    def list_running_jobs(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM jobs WHERE status = 'running'").fetchall()
        return [dict(r) for r in rows]

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

    def create_metric_results(self, run_id: str, source: str, rows: list[tuple]) -> None:
        """metric をまとめて1トランザクションで入れる。rows = (label, step, value, sent_at)。"""
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO results (run_id, kind, label, step, value, image_path, source, sent_at) "
                "VALUES (?, 'metric', ?, ?, ?, NULL, ?, ?)",
                [(run_id, label, step, value, source, sent_at) for label, step, value, sent_at in rows],
            )

    def list_results(self, run_id: str, kind: str | None = None, label: str | None = None,
                     after_id: int | None = None, exclude_labels: Iterable[str] = ()) -> list[dict]:
        query = "SELECT * FROM results WHERE run_id = ?"
        params: list[Any] = [run_id]
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        if label is not None:
            query += " AND label = ?"
            params.append(label)
        if after_id is not None:
            query += " AND id > ?"
            params.append(after_id)
        exclude = list(exclude_labels)
        if exclude:
            query += f" AND label NOT IN ({', '.join('?' for _ in exclude)})"
            params.extend(exclude)
        query += " ORDER BY sent_at ASC, id ASC"
        with self._bulk_read_lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def metric_label_counts(self, run_id: str) -> dict[str, int]:
        """run の metric の label ごとの件数。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT label, COUNT(*) AS n FROM results WHERE run_id = ? AND kind = 'metric' GROUP BY label",
                (run_id,),
            ).fetchall()
        return {r["label"]: r["n"] for r in rows}

    def results_fingerprint(self, run_id: str) -> str:
        """results の変化を見るための値（ETag 用）。結果は追記だけなので件数と最大 id でよい。"""
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n, MAX(id) AS m FROM results WHERE run_id = ?", (run_id,)).fetchone()
        return f"{row['n']}-{row['m']}"

    def plots_fingerprint(self, run_id: str) -> str:
        """plots（定義）の変化を見るための値（ETag 用）。点の変化は max_plot_point_id で見る。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n, MAX(updated_at) AS u FROM plots WHERE run_id = ?", (run_id,),
            ).fetchone()
        return f"{row['n']}-{row['u']}"

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
                f"SELECT DISTINCT label, kind FROM results WHERE run_id IN ({placeholders}) "
                f"UNION SELECT DISTINCT label, 'plot' AS kind FROM plots WHERE run_id IN ({placeholders}) "
                "ORDER BY label ASC",
                [*run_ids, *run_ids],
            ).fetchall()
        return [dict(r) for r in rows]

    def result_label_kind(self, run_id: str, label: str) -> str | None:
        """その run で label が metric/image として使われていればその kind を返す。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT kind FROM results WHERE run_id = ? AND label = ? LIMIT 1", (run_id, label),
            ).fetchone()
        return row["kind"] if row else None

    # ---- plots（軸つきグラフ） -----------------------------------------

    def get_plot(self, run_id: str, label: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM plots WHERE run_id = ? AND label = ?", (run_id, label),
            ).fetchone()
        return dict(row) if row else None

    def upsert_plot(self, **fields: Any) -> None:
        cols = list(fields.keys())
        placeholders = ", ".join("?" for _ in cols)
        sql = f"INSERT OR REPLACE INTO plots ({', '.join(cols)}) VALUES ({placeholders})"
        with self._connect() as conn:
            conn.execute(sql, [fields[c] for c in cols])

    def list_plots(self, run_id: str, label: str | None = None) -> list[dict]:
        query = "SELECT * FROM plots WHERE run_id = ?"
        params: list[Any] = [run_id]
        if label is not None:
            query += " AND label = ?"
            params.append(label)
        query += " ORDER BY label ASC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def add_plot_points(self, run_id: str, label: str, series: str, xs: list[float], ys: list[float],
                        step: int | None, source: str, sent_at: str) -> None:
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO plot_points (run_id, label, series, x, y, step, source, sent_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(run_id, label, series, x, y, step, source, sent_at) for x, y in zip(xs, ys)],
            )

    def list_plot_series_stats(self, run_id: str, label: str, upto: int) -> list[dict]:
        """系列ごとの全点数と最初の点id（最初に現れた順に並べるため）。id が upto 以下の点だけを数える。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT series, COUNT(*) AS total, MIN(id) AS first_id FROM plot_points "
                "WHERE run_id = ? AND label = ? AND id <= ? GROUP BY series ORDER BY first_id ASC",
                (run_id, label, upto),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_plot_bins(self, run_id: str, label: str, series: str, upto: int, width: float) -> list[tuple]:
        """x を width ごとの区間 [k*width, (k+1)*width) に分けた (k, y の平均, 点数) を k の昇順で返す。
        集計を SQLite の中で行うので、数万点の行を Python に取り込まない（binning.bin_mean と同じ結果）。"""
        floor = "(CAST(x / :w AS INTEGER) - (x / :w < CAST(x / :w AS INTEGER)))"  # SQLite に floor が無い場合があるので手で
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {floor} AS k, AVG(y) AS m, COUNT(*) AS n FROM plot_points "
                "WHERE run_id = :r AND label = :l AND series = :s AND id <= :u GROUP BY k ORDER BY k ASC",
                {"w": float(width), "r": run_id, "l": label, "s": series, "u": upto},
            ).fetchall()
        return [(int(r["k"]), r["m"], r["n"]) for r in rows]

    def list_plot_points(self, run_id: str, label: str, series: str, upto: int,
                         after_id: int | None = None) -> list[tuple]:
        query = "SELECT id, x, y FROM plot_points WHERE run_id = ? AND label = ? AND series = ? AND id <= ?"
        params: list[Any] = [run_id, label, series, upto]
        if after_id is not None:
            query += " AND id > ?"
            params.append(after_id)
        query += " ORDER BY id ASC"
        with self._bulk_read_lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [(r["id"], r["x"], r["y"]) for r in rows]

    def max_plot_point_id(self, run_id: str, label: str | None = None, upto: int | None = None) -> int | None:
        query = "SELECT MAX(id) AS m FROM plot_points WHERE run_id = ?"
        params: list[Any] = [run_id]
        if label is not None:
            query += " AND label = ?"
            params.append(label)
        if upto is not None:
            query += " AND id <= ?"
            params.append(upto)
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return row["m"]
