"""学習結果を CLI から読むための読み取り専用コマンド（Claude と結果を見ながら相談する用）。

DB（train_webui/data/webui.sqlite）と画像フォルダを直接読むので、WebUI の server が止まっていても使える。
何も書き込まない。リポジトリ直下から実行する:

    venv/bin/python -m train_webui.cli ls [project]
    venv/bin/python -m train_webui.cli show <project> <group> [run]
    venv/bin/python -m train_webui.cli plot <project> <group> <label> [run] [--points 50]
    venv/bin/python -m train_webui.cli compare <project> <group1> <group2> [--run1 v1] [--run2 v2]
    venv/bin/python -m train_webui.cli view [run_id ...] [--comparison 名前かid] [--config full] [--log N]
    venv/bin/python -m train_webui.cli comparisons

- comparisons は、Result タブで名前を付けて残した比較の一覧。`view --comparison <名前>` でその比較を読む。
- view は、WebUI の画面で今見ている構成（タブ・比較中の run・表示中のラベル）をそのまま読む。run_id を
  並べると、画面の代わりにその run たちを同じ形で出す（ツリーの📋ボタンでコピーした run_id を貼れる）。

- project はパスの末尾だけでもよい（`world_model_v1` → `src/world_model_v1`）。
- group は group_id でも group_name でもよい。
- run は表示名（v1, v2, ...）か run_id。省略するとその group で最後に起動した（draft 以外の）run。
"""
from __future__ import annotations

import argparse
import difflib
import json
import sqlite3
import sys
from pathlib import Path

from .backend.categories import classify, load_stages, load_workspaces, stage_of
from .backend.decimate import decimate_minmax, decimate_stride
from .backend.settings import DEFAULT_PATH, REPO_ROOT, load_settings

_DEFAULT_DB = REPO_ROOT / "train_webui" / "data" / "webui.sqlite"
_DEFAULT_MEDIA = REPO_ROOT / "train_webui" / "data" / "media"


class CliError(Exception):
    pass


# ---------------------------------------------------------------------------
# DB の読み出し


class Store:
    def __init__(self, db_path: Path, media_dir: Path) -> None:
        if not db_path.is_file():
            raise CliError(f"DB がありません: {db_path}")
        # 読み取り専用で開く（WebUI の server が動いていても邪魔しない）
        self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row
        self.media_dir = media_dir

    def q(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def view_state(self) -> tuple[dict, str] | None:
        """画面が最後に送ってきた構成と、その時刻。まだ無ければ None（view_state 追加前の DB も含む）"""
        try:
            rows = self.q("SELECT data, updated_at FROM view_state WHERE id = 1")
        except sqlite3.OperationalError:
            return None
        return (json.loads(rows[0]["data"]), rows[0]["updated_at"]) if rows else None

    def comparisons(self) -> list[dict]:
        try:
            rows = self.q("SELECT * FROM comparisons ORDER BY created_at, id")
        except sqlite3.OperationalError:  # comparisons 追加前の DB
            return []
        return [{"id": r["id"], "name": r["name"], "run_ids": json.loads(r["run_ids"]),
                 "labels": json.loads(r["labels"]), "hidden_labels": json.loads(r["hidden_labels"]),
                 "updated_at": r["updated_at"]} for r in rows]

    def resolve_comparison(self, key: str) -> dict:
        items = self.comparisons()
        hits = [c for c in items if c["id"] == key] or [c for c in items if c["name"] == key]
        if len(hits) == 1:
            return hits[0]
        listing = ", ".join(f"{c['name']}({c['id']})" for c in items) or "（なし）"
        if not hits:
            raise CliError(f"比較が見つかりません: {key}（候補: {listing}）")
        raise CliError(f"同じ名前の比較が複数あります: {key} → id で指定してください（候補: {listing}）")

    def run_by_id(self, run_id: str) -> sqlite3.Row | None:
        rows = self.q("SELECT * FROM runs WHERE run_id = ?", (run_id,))
        return rows[0] if rows else None

    def projects(self) -> list[str]:
        return [r["project"] for r in self.q("SELECT DISTINCT project FROM runs ORDER BY project")]

    def resolve_project(self, name: str) -> str:
        projects = self.projects()
        if name in projects:
            return name
        hits = [p for p in projects if p.endswith("/" + name.strip("/"))]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise CliError(f"project が見つかりません: {name}（候補: {', '.join(projects)}）")
        raise CliError(f"project が複数に当てはまります: {name} → {', '.join(hits)}")

    def group_names(self, project: str) -> dict[str, str]:
        rows = self.q("SELECT group_id, group_name FROM groups WHERE project = ?", (project,))
        return {r["group_id"]: r["group_name"] for r in rows}

    def group_description(self, project: str, group: str) -> str:
        """group（=モデル構成）の説明（Markdown）。description 列の追加前の DB や未記入は空文字"""
        try:
            rows = self.q("SELECT description FROM groups WHERE project = ? AND group_id = ?", (project, group))
        except sqlite3.OperationalError:
            return ""
        return (rows[0]["description"] if rows else "") or ""

    def resolve_group(self, project: str, name: str) -> str:
        ids = [r["run_group"] for r in self.q("SELECT DISTINCT run_group FROM runs WHERE project = ?", (project,))]
        if name in ids:
            return name
        names = self.group_names(project)
        hits = [gid for gid in ids if names.get(gid) == name]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            listing = ", ".join(f"{names.get(g, g)}({g})" for g in ids)
            raise CliError(f"group が見つかりません: {name}（候補: {listing}）")
        raise CliError(f"group_name が複数の group に当てはまります: {name} → {', '.join(hits)}（group_id で指定してください）")

    def runs_in_group(self, project: str, group: str) -> list[sqlite3.Row]:
        """作成順（古い順）"""
        return self.q(
            "SELECT * FROM runs WHERE project = ? AND run_group = ? ORDER BY created_at ASC, run_id ASC",
            (project, group),
        )

    def resolve_run(self, project: str, group: str, name: str | None) -> sqlite3.Row:
        runs = self.runs_in_group(project, group)
        if name is None:
            started = [r for r in runs if r["status"] != "draft"]
            if not started:
                raise CliError(f"{group} に起動済みの run がありません")
            # 同じ秒に起動した run は run_id（同じ秒なら _2, _3 が付く）で後のものを選ぶ
            return max(started, key=lambda r: (r["started_at"] or "", r["created_at"], r["run_id"]))
        for r in runs:
            if name in (r["display_name"], r["run_id"]):
                return r
        listing = ", ".join(f"{r['display_name']}({r['status']})" for r in runs)
        raise CliError(f"run が見つかりません: {name}（候補: {listing}）")

    def plots(self, run_id: str) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM plots WHERE run_id = ? ORDER BY label", (run_id,))

    def series(self, run_id: str, label: str) -> dict[str, list[tuple[int, float, float]]]:
        """{series名: [(id, x, y), ...]}（最初に現れた順、点は送信順）"""
        out: dict[str, list[tuple[int, float, float]]] = {}
        for r in self.q(
            "SELECT id, series, x, y FROM plot_points WHERE run_id = ? AND label = ? ORDER BY id", (run_id, label),
        ):
            out.setdefault(r["series"], []).append((r["id"], r["x"], r["y"]))
        return out

    def metrics(self, run_id: str) -> dict[str, list[tuple[int, float]]]:
        """{label: [(step, value), ...]}（送信順）"""
        out: dict[str, list[tuple[int, float]]] = {}
        for r in self.q(
            "SELECT label, step, value FROM results WHERE run_id = ? AND kind = 'metric' ORDER BY sent_at, id",
            (run_id,),
        ):
            out.setdefault(r["label"], []).append((r["step"] if r["step"] is not None else 0, r["value"]))
        return out

    def images(self, run_id: str) -> dict[str, list[sqlite3.Row]]:
        """{label: [results行, ...]}（古い順）"""
        out: dict[str, list[sqlite3.Row]] = {}
        for r in self.q(
            "SELECT * FROM results WHERE run_id = ? AND kind = 'image' ORDER BY sent_at, id", (run_id,),
        ):
            out.setdefault(r["label"], []).append(r)
        return out

    def tables(self, run_id: str) -> dict[str, dict[str, float]]:
        """{表label: {指標名: 最新の値}}"""
        out: dict[str, dict[str, float]] = {}
        for r in self.q(
            "SELECT label, name, value FROM results WHERE run_id = ? AND kind = 'table' ORDER BY sent_at, id",
            (run_id,),
        ):
            out.setdefault(r["label"], {})[r["name"]] = r["value"]
        return out

    def jobs(self, run_id: str) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM jobs WHERE run_id = ? ORDER BY started_at", (run_id,))


# ---------------------------------------------------------------------------
# 表示


def _comment(run: sqlite3.Row) -> str:
    # comment 列の追加前のDB（server 未再起動）でも動くようにする
    return (run["comment"] if "comment" in run.keys() else "") or ""


def _fmt(v: float) -> str:
    return f"{v:.6g}"


def _run_title(store: Store, run: sqlite3.Row) -> str:
    gname = store.group_names(run["project"]).get(run["run_group"], run["run_group"])
    return f"{run['project']} / {gname} / {run['display_name']}"


def _series_summary(points: list[tuple[int, float, float]]) -> str:
    first, last = points[0], points[-1]
    lo = min(points, key=lambda p: p[2])
    return (f"{len(points)}点  最初 ({_fmt(first[1])}, {_fmt(first[2])})  "
            f"最小 ({_fmt(lo[1])}, {_fmt(lo[2])})  最後 ({_fmt(last[1])}, {_fmt(last[2])})")


def _previous_run(store: Store, run: sqlite3.Row) -> sqlite3.Row | None:
    """同じ group で、この run より前に作られた最後の（draft 以外の）run"""
    before = [r for r in store.runs_in_group(run["project"], run["run_group"])
              if r["status"] != "draft" and (r["created_at"], r["run_id"]) < (run["created_at"], run["run_id"])]
    return before[-1] if before else None


def _config_diff(a: str, b: str, name_a: str, name_b: str) -> str:
    lines = difflib.unified_diff(a.splitlines(), b.splitlines(), name_a, name_b, lineterm="", n=0)
    return "\n".join(line for line in lines if not line.startswith("@@"))


def cmd_ls(store: Store, project: str | None) -> None:
    projects = [store.resolve_project(project)] if project else store.projects()
    cats, stages = load_workspaces(), load_stages()
    shown: set[tuple[str, str]] = set()
    for p in projects:
        key = (classify(p, cats), stage_of(p, stages))
        if key not in shown:  # プロジェクト > 段階 の見出しは最初の1回だけ
            shown.add(key)
            print(f"[{key[0]} > {key[1]}]")
        print(p)
        names = store.group_names(p)
        groups = sorted({r["run_group"] for r in store.q("SELECT run_group FROM runs WHERE project = ?", (p,))},
                        key=lambda g: names.get(g, g))
        for g in groups:
            label = names.get(g, g)
            note = "" if store.group_description(p, g).strip() else "  [モデルの説明なし]"
            print(f"  {label}" + (f"  (group_id: {g})" if label != g else "") + note)
            for r in store.runs_in_group(p, g):
                when = (r["started_at"] or r["created_at"])[:16].replace("T", " ")
                print(f"    {r['display_name']:<4} {r['status']:<9} {r['origin']:<5} {when}  {r['run_id']}")


def print_descriptions(store: Store, runs: list[sqlite3.Row]) -> None:
    """runs の group（=モデル構成）ごとの説明を、重複なく1回ずつ出す。"""
    seen: set[tuple[str, str]] = set()
    for run in runs:
        key = (run["project"], run["run_group"])
        if key in seen:
            continue
        seen.add(key)
        gname = store.group_names(run["project"]).get(run["run_group"], run["run_group"])
        text = store.group_description(*key).strip()
        print(f"## モデルの説明: {run['project']} / {gname}")
        print(text if text else "（未記入。WebUI の Train タブか `ctl describe` で書ける）")
        print()


def cmd_show(store: Store, run: sqlite3.Row, config_mode: str, log_lines: int, labels: set[str] | None = None,
             describe: bool = True) -> None:
    """labels を渡すと、そのラベルのグラフ・画像だけ出す（画面で表示中のものだけ読む時）。"""
    print(f"# {_run_title(store, run)}")
    print(f"run_id: {run['run_id']}  status: {run['status']}  origin: {run['origin']}  group_id: {run['run_group']}")
    print(f"開始: {run['started_at'] or '-'}  終了: {run['ended_at'] or '-'}")
    if describe:
        print()
        print_descriptions(store, [run])
    if _comment(run):
        print("コメント:")
        for line in _comment(run).splitlines():
            print(f"  {line}")
    if run["error"]:
        err = run["error"].strip().splitlines()
        print("エラー（末尾）:")
        for line in err[-10:]:
            print(f"  {line}")

    print("\n## config")
    prev = _previous_run(store, run)
    if config_mode == "full" or prev is None:
        print(run["config_toml"].rstrip())
    else:
        diff = _config_diff(prev["config_toml"], run["config_toml"], prev["display_name"], run["display_name"])
        print(f"（同じ group の前の run {prev['display_name']} からの差分。全文は --config full）")
        print(diff if diff else "差分なし")

    def shown(label: str) -> bool:
        return labels is None or label in labels

    plots = [p for p in store.plots(run["run_id"]) if shown(p["label"])]
    metrics = {k: v for k, v in store.metrics(run["run_id"]).items() if shown(k)}
    if plots or metrics:
        print("\n## グラフ")
    for p in plots:
        axes = f"{p['kind']}  x: {p['xlabel'] or '-'}({p['xscale']})  y: {p['ylabel'] or '-'}({p['yscale']})"
        print(f"{p['label']}  [{axes}]" + (f"  {p['title']}" if p["title"] else ""))
        for name, points in store.series(run["run_id"], p["label"]).items():
            print(f"  {name}: {_series_summary(points)}")
    for label, values in metrics.items():
        pts = [(i, float(s), v) for i, (s, v) in enumerate(values)]
        print(f"{label}  [log()で送った数値  x: step]")
        print(f"  {_series_summary(pts)}")

    tables = {k: v for k, v in store.tables(run["run_id"]).items() if shown(k)}
    if tables:
        print("\n## 表（log_table で送った値。同じ指標は最新）")
    for label, values in tables.items():
        print(f"{label}")
        for name, v in values.items():
            print(f"  {name}: {_fmt(v)}")

    images = {k: v for k, v in store.images(run["run_id"]).items() if shown(k)}
    if images:
        print("\n## 画像（Read で開ける。新しいものが最後）")
    for label, rows in images.items():
        print(f"{label}: {len(rows)}枚")
        for r in rows[-3:]:
            step = f" step {r['step']}" if r["step"] is not None else ""
            print(f"  {store.media_dir / r['image_path']}  ({r['source']}{step})")
        if len(rows) > 3:
            print(f"  …ほか{len(rows) - 3}枚（最新3枚だけ表示）")

    jobs = store.jobs(run["run_id"])
    if jobs:
        print("\n## ジョブ")
    for j in jobs:
        log = store.media_dir / j["log_path"] if j["log_path"] else None
        print(f"{j['kind']:<5} {j['status']:<9} exit={j['exit_code']}  {j['job_id']}" + (f"  log: {log}" if log else ""))
        if log_lines and log and log.is_file():
            tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-log_lines:]
            for line in tail:
                print(f"    | {line}")


def cmd_plot(store: Store, run: sqlite3.Row, label: str, points: int) -> None:
    print(f"# {_run_title(store, run)}  {label}")
    plot = next((p for p in store.plots(run["run_id"]) if p["label"] == label), None)
    if plot is not None:
        series = store.series(run["run_id"], label)
        decimate = decimate_minmax if plot["kind"] == "line" else decimate_stride
        print(f"x: {plot['xlabel'] or 'x'}  y: {plot['ylabel'] or 'y'}  （{plot['kind']}。{points}点までに間引き）")
    else:
        metrics = store.metrics(run["run_id"])
        if label not in metrics:
            labels = [p["label"] for p in store.plots(run["run_id"])] + list(metrics)
            raise CliError(f"ラベルが見つかりません: {label}（候補: {', '.join(labels)}）")
        series = {"default": [(i, float(s), v) for i, (s, v) in enumerate(metrics[label])]}
        decimate = decimate_minmax
        print(f"x: step  y: value  （{points}点までに間引き）")
    for name, pts in series.items():
        shown = decimate(pts, max(points, 4))
        print(f"\n[{name}] 全{len(pts)}点" + (f" → {len(shown)}点" if len(shown) < len(pts) else ""))
        for _id, x, y in shown:
            print(f"  {_fmt(x)}\t{_fmt(y)}")


def _last_values(store: Store, run: sqlite3.Row) -> dict[str, tuple[float, float]]:
    """{"label[series]": (最後の値, 最小値)}"""
    out: dict[str, tuple[float, float]] = {}
    for p in store.plots(run["run_id"]):
        for name, pts in store.series(run["run_id"], p["label"]).items():
            key = p["label"] if name == "default" else f"{p['label']}[{name}]"
            out[key] = (pts[-1][2], min(y for _i, _x, y in pts))
    for label, values in store.metrics(run["run_id"]).items():
        out[label] = (values[-1][1], min(v for _s, v in values))
    return out


def cmd_compare(store: Store, run1: sqlite3.Row, run2: sqlite3.Row) -> None:
    t1, t2 = _run_title(store, run1), _run_title(store, run2)
    print(f"# 比較\n  A: {t1}  ({run1['run_id']})\n  B: {t2}  ({run2['run_id']})\n")
    print_descriptions(store, [run1, run2])
    for tag, run in (("A", run1), ("B", run2)):
        if _comment(run):
            print(f"  {tag} のコメント: " + _comment(run).replace("\n", " / "))
    print("\n## config の差分（A → B）")
    diff = _config_diff(run1["config_toml"], run2["config_toml"], "A", "B")
    print(diff if diff else "差分なし")
    v1, v2 = _last_values(store, run1), _last_values(store, run2)
    keys = sorted(set(v1) | set(v2))
    if keys:
        print("\n## 数値（最後の値 / 最小値）")
        width = max(len(k) for k in keys)
        print(f"{'ラベル':<{width}}  {'A 最後':>12} {'A 最小':>12}  {'B 最後':>12} {'B 最小':>12}")
        for k in keys:
            a = v1.get(k)
            b = v2.get(k)
            fa = f"{_fmt(a[0]):>12} {_fmt(a[1]):>12}" if a else f"{'-':>12} {'-':>12}"
            fb = f"{_fmt(b[0]):>12} {_fmt(b[1]):>12}" if b else f"{'-':>12} {'-':>12}"
            print(f"{k:<{width}}  {fa}  {fb}")
    i1, i2 = store.images(run1["run_id"]), store.images(run2["run_id"])
    labels = sorted(set(i1) | set(i2))
    if labels:
        print("\n## 画像（それぞれ最新1枚。Read で開いて見比べる）")
        for label in labels:
            print(label)
            for tag, imgs in (("A", i1), ("B", i2)):
                rows = imgs.get(label)
                print(f"  {tag}: {store.media_dir / rows[-1]['image_path']}" if rows else f"  {tag}: なし")


# ---------------------------------------------------------------------------


def cmd_comparisons(store: Store) -> None:
    items = store.comparisons()
    if not items:
        print("保存された比較はありません")
    for c in items:
        print(f"{c['name']}  (id: {c['id']}, {len(c['run_ids'])}run, ラベル{len(c['labels'])}件, 更新 {c['updated_at']})")


def cmd_view(store: Store, run_ids: list[str], config_mode: str, log_lines: int, comparison: str | None = None) -> None:
    if comparison:
        saved_cmp = store.resolve_comparison(comparison)
        run_ids, labels = saved_cmp["run_ids"], set(saved_cmp["labels"])
        tab, stamp = f"保存した比較「{saved_cmp['name']}」", saved_cmp["updated_at"]
    elif run_ids:
        tab, labels, stamp = "指定", None, None
    else:
        saved = store.view_state()
        if saved is None:
            raise CliError("画面の構成がまだ送られていません。WebUI をブラウザで開いてください（開くと自動で送られます）")
        state, stamp = saved
        run_ids, tab = state["run_ids"], {"train": "Train", "result": "Result"}[state["tab"]]
        labels = set(state["labels"]) if state["tab"] == "result" else None
    runs = []
    for rid in run_ids:
        run = store.run_by_id(rid)
        if run is None:
            if comparison:  # 保存した比較の run が後から削除されていても、残りは読めるようにする
                print(f"（run_id {rid} は削除されています）")
                continue
            raise CliError(f"run_id が見つかりません: {rid}")
        runs.append(run)
    kind = tab if comparison else f"{tab}タブ"
    print(f"# WebUI の画面（{kind}" + (f"、{stamp} 時点" if stamp else "") + "）")
    print(f"対象の run: {len(runs)}件")
    for run in runs:
        print(f"  {run['run_id']}  {_run_title(store, run)}  [{run['status']}]")
    if labels is not None:
        print("表示中のラベル: " + (", ".join(sorted(labels)) if labels else "（なし）"))
    print()
    print_descriptions(store, runs)
    for run in runs:
        print("\n" + "=" * 60)
        cmd_show(store, run, config_mode, log_lines, labels, describe=False)
    if len(runs) >= 2:
        print("\n" + "=" * 60 + "\n# 数値の比較（最後の値 / 最小値）")
        values = {run["run_id"]: _last_values(store, run) for run in runs}
        keys = sorted({k for v in values.values() for k in v if labels is None or k.split("[")[0] in labels})
        for k in keys:
            cells = []
            for run in runs:
                v = values[run["run_id"]].get(k)
                name = f"{run['display_name']}({store.group_names(run['project']).get(run['run_group'], run['run_group'])})"
                cells.append(f"{name}: {_fmt(v[0])} / {_fmt(v[1])}" if v else f"{name}: -")
            print(f"{k}\t" + "\t".join(cells))


def _open_store(db: str | None) -> Store:
    if db:
        db_path = Path(db)
        return Store(db_path, db_path.parent / "media")
    if DEFAULT_PATH.is_file():
        settings = load_settings()
        return Store(settings.server.db_path, settings.server.media_dir)
    return Store(_DEFAULT_DB, _DEFAULT_MEDIA)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m train_webui.cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", help="DB のパス（省略時は server.toml の db_path）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ls", help="project / group / run の一覧")
    p.add_argument("project", nargs="?")

    p = sub.add_parser("show", help="run の要約（config差分・グラフの要約・画像のパス・ジョブ）")
    p.add_argument("project")
    p.add_argument("group")
    p.add_argument("run", nargs="?")
    p.add_argument("--config", choices=["diff", "full"], default="diff", help="config を前の run との差分で出すか全文か")
    p.add_argument("--log", type=int, default=0, metavar="N", help="各ジョブのログの末尾 N 行も出す")

    p = sub.add_parser("plot", help="1つのグラフを少ない点数の表で出す")
    p.add_argument("project")
    p.add_argument("group")
    p.add_argument("label")
    p.add_argument("run", nargs="?")
    p.add_argument("--points", type=int, default=50, help="1系列あたりの点数（既定 50）")

    p = sub.add_parser("compare", help="2つの group（の run）の config 差分と数値を比べる")
    p.add_argument("project")
    p.add_argument("group1")
    p.add_argument("group2")
    p.add_argument("--run1")
    p.add_argument("--run2")

    p = sub.add_parser("view", help="WebUI の画面で今見ている構成をそのまま読む（run_id を並べればその run たち）")
    p.add_argument("run_ids", nargs="*", metavar="run_id")
    p.add_argument("--comparison", metavar="名前かid", help="Result タブで保存した比較を読む")
    p.add_argument("--config", choices=["diff", "full"], default="diff")
    p.add_argument("--log", type=int, default=0, metavar="N")

    sub.add_parser("comparisons", help="Result タブで保存した比較の一覧")

    args = parser.parse_args(argv)
    try:
        store = _open_store(args.db)
        if args.cmd == "ls":
            cmd_ls(store, args.project)
            return 0
        if args.cmd == "view":
            cmd_view(store, args.run_ids, args.config, args.log, args.comparison)
            return 0
        if args.cmd == "comparisons":
            cmd_comparisons(store)
            return 0
        project = store.resolve_project(args.project)
        if args.cmd == "show":
            group = store.resolve_group(project, args.group)
            cmd_show(store, store.resolve_run(project, group, args.run), args.config, args.log)
        elif args.cmd == "plot":
            group = store.resolve_group(project, args.group)
            cmd_plot(store, store.resolve_run(project, group, args.run), args.label, args.points)
        else:
            g1 = store.resolve_group(project, args.group1)
            g2 = store.resolve_group(project, args.group2)
            cmd_compare(store, store.resolve_run(project, g1, args.run1), store.resolve_run(project, g2, args.run2))
    except CliError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
