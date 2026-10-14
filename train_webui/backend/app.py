"""FastAPI アプリ本体（CONTRACT.md §2）。`create_app` がテスト・本番共通のエントリーポイント。"""
from __future__ import annotations

import base64
import binascii
import json
import ipaddress
import math
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import binning, categories, code_snapshot, discovery
from .db import Database, now_iso
from .decimate import decimate_minmax, decimate_stride
from .ids import new_group_id, new_run_id
from .jobs import JobBusyError, JobManager
from .remote.types import RemoteControllerProtocol
from .settings import Settings
from .toml_utils import TomlValidationError, extract_group, find_number, set_value, set_wandb_group

_FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


def _is_allowed_client(host: str | None, networks: list) -> bool:
    if host is None:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in net for net in networks if ip.version == net.version)


# ---------------------------------------------------------------------------
# リクエストボディ（CONTRACT.md §2.2 / §2.3）

class DraftRequest(BaseModel):
    project: str
    # 既存groupへの追加なら group_id。新規group（new_group=true）で省略すると group_id / group_name を自動で振る
    group: str | None = None
    new_group: bool
    group_name: str | None = None
    # どちらかを指定すると、その run と同じ group に、その run の config を初期値にした下書きを作る
    parent_run_id: str | None = None  # 引き継ぎ学習: その run の重みを初期値にして続きを学習する
    rerun_of: str | None = None       # 同じコードで再実行: その run を起動した時点のコードで走らせる


class ArtifactRegister(BaseModel):
    name: str   # 成果物の名前（run 内で一意。例 "model.pt"）
    kind: str   # "checkpoint" / "samples" など
    path: str   # 学習用PC上のパス
    size: int | None = None


class GroupRenameRequest(BaseModel):
    project: str
    group: str  # group_id
    group_name: str


class GroupDescriptionRequest(BaseModel):
    project: str
    group: str  # group_id
    description: str


class ConfigRequest(BaseModel):
    config_toml: str


class CommentRequest(BaseModel):
    comment: str


class ViewStateRequest(BaseModel):
    """画面で今見ている構成。Claude が cli の view で読む。"""
    tab: Literal["train", "result"]
    run_ids: list[str]  # Train タブなら選択中の run、Result タブなら比較中の run
    labels: list[str] = []  # Result タブで表示中のラベル


class ComparisonCreate(BaseModel):
    name: str
    run_ids: list[str]
    labels: list[str] = []  # 表示中のラベル
    hidden_labels: list[str] = []  # 非表示にしていたラベル（読み込み時に同じ表示に戻すため）


class ComparisonUpdate(BaseModel):
    """渡した項目だけ更新する（名前だけ変える、構成だけ上書きする、など）。"""
    name: str | None = None
    run_ids: list[str] | None = None
    labels: list[str] | None = None
    hidden_labels: list[str] | None = None


class InferRequest(BaseModel):
    project: str
    # run_id を指定するとその run の重みで、無ければ group の最新の学習済み run の重みで推論する（どちらか必須）
    group: str | None = None
    run_id: str | None = None
    # その推論ジョブに渡す config だけを書き換える（run に保存された config は変えない）。
    # `section.key=TOMLリテラル` の並び。無いキーは section の中に足す。
    overrides: list[str] = []


class ClientRunCreate(BaseModel):
    project: str
    group: str
    config_toml: str
    hostname: str
    pid: int


class FinishRequest(BaseModel):
    status: Literal["finished", "failed"]
    error: str | None = None


class ResultItem(BaseModel):
    kind: Literal["metric", "image", "table", "plot_def", "plot_points"]
    label: str
    name: str | None = None  # table: 表の行名（指標名）
    step: int | None = None
    value: float | None = None
    image_b64: str | None = None
    # plot_def
    plot_kind: Literal["line", "scatter"] | None = None
    title: str | None = None
    xlabel: str | None = None
    ylabel: str | None = None
    xscale: Literal["linear", "log"] | None = None
    yscale: Literal["linear", "log"] | None = None
    xlim: tuple[float, float] | None = None
    ylim: tuple[float, float] | None = None
    # plot_points
    series: str | None = None
    xs: list[float] | None = None
    ys: list[float] | None = None
    sent_at: str


class ResultsRequest(BaseModel):
    run_id: str
    source: str
    items: list[ResultItem]


# ---------------------------------------------------------------------------
# DB行 -> レスポンスJSON（CONTRACT.md §2.1）

def _run_summary(row: dict, group_name: str | None = None) -> dict:
    return {
        "run_id": row["run_id"], "display_name": row["display_name"],
        "project": row["project"], "group": row["run_group"], "group_name": group_name or row["run_group"],
        "origin": row["origin"], "status": row["status"],
        "created_at": row["created_at"], "started_at": row["started_at"], "ended_at": row["ended_at"],
        "comment": row["comment"] or "", "parent_run_id": row.get("parent_run_id"),
        "code_commit": row.get("code_commit"),
    }


def _job_public(row: dict) -> dict:
    return {
        "job_id": row["job_id"], "run_id": row["run_id"], "kind": row["kind"], "status": row["status"],
        "started_at": row["started_at"], "ended_at": row["ended_at"],
        "exit_code": row["exit_code"], "error": row["error"],
    }


def _run_detail(db: Database, row: dict) -> dict:
    detail = _run_summary(row, db.group_names(row["project"]).get(row["run_group"]))
    detail.update({
        "group_description": db.group_descriptions(row["project"]).get(row["run_group"], ""),
        "artifacts": [
            {k: a[k] for k in ("name", "kind", "path", "size", "updated_at")} for a in db.list_artifacts(row["run_id"])
        ],
        "config_toml": row["config_toml"], "git_commit": row["git_commit"], "git_diff": row["git_diff"],
        "git_untracked": row["git_untracked"], "git_info_source": row["git_info_source"],
        "hostname": row["hostname"], "pid": row["pid"], "last_heartbeat_at": row["last_heartbeat_at"],
        "exit_code": row["exit_code"], "error": row["error"],
        "jobs": [_job_public(j) for j in db.list_jobs_for_run(row["run_id"])],
    })
    return detail


def _result_public(row: dict) -> dict:
    base = {
        "id": row["id"], "run_id": row["run_id"], "kind": row["kind"], "label": row["label"],
        "step": row["step"], "source": row["source"], "sent_at": row["sent_at"],
    }
    if row["kind"] == "image":
        base["image_url"] = f"/media/{row['image_path']}"
    else:
        base["value"] = row["value"]
        if row["kind"] == "table":
            base["name"] = row["name"]
    return base


def _plot_public(db: Database, row: dict, after_id: int | None, max_points: int, upto: int) -> dict:
    """CONTRACT.md §2.1 の Plot。max_points を超える系列は間引き済みの全体を返す（§4）。

    upto（リクエスト開始時点の最大の点id）以下の点だけを扱う。読んでいる途中に追加された点を
    last_point_id に含めてしまうと、次回の after_id による差分取得でその点が漏れるため。
    """
    run_id, label = row["run_id"], row["label"]
    series = []
    for stat in db.list_plot_series_stats(run_id, label, upto):
        total = stat["total"]
        if total > max_points:
            pts = db.list_plot_points(run_id, label, stat["series"], upto)
            decimate = decimate_minmax if row["kind"] == "line" else decimate_stride
            pts = decimate(pts, max_points)
            downsampled = True
        else:
            pts = db.list_plot_points(run_id, label, stat["series"], upto, after_id=after_id)
            downsampled = False
        series.append({
            "name": stat["series"], "points": [[x, y] for _id, x, y in pts],
            "total_points": total, "downsampled": downsampled,
        })

    def lim(lo, hi):
        return [lo, hi] if lo is not None and hi is not None else None

    return {
        "run_id": run_id, "label": label, "kind": row["kind"], "defined": bool(row["defined"]),
        "title": row["title"], "xlabel": row["xlabel"], "ylabel": row["ylabel"],
        "xscale": row["xscale"], "yscale": row["yscale"],
        "xlim": lim(row["xmin"], row["xmax"]), "ylim": lim(row["ymin"], row["ymax"]),
        "updated_at": row["updated_at"], "series": series,
        "last_point_id": db.max_plot_point_id(run_id, label, upto=upto),
    }


def _check_label_kind(db: Database, run_id: str, label: str, is_plot: bool, kind: str = "") -> None:
    """同じ run で plot と metric/image に同じラベルを使わせない（CONTRACT.md §2.3）。"""
    if is_plot:
        other = db.result_label_kind(run_id, label)
        if other is not None:
            raise HTTPException(400, f"ラベル {label} は既に {other} として使われています")
    else:
        if db.get_plot(run_id, label) is not None:
            raise HTTPException(400, f"ラベル {label} は既に plot として使われています")
        other = db.result_label_kind(run_id, label)
        if other is not None and other != kind:
            raise HTTPException(400, f"ラベル {label} は既に {other} として使われています")


def _save_plot_item(db: Database, run_id: str, source: str, item: ResultItem) -> None:
    if item.kind == "plot_def":
        if item.plot_kind is None or item.xscale is None or item.yscale is None:
            raise HTTPException(400, "plot_def には plot_kind / xscale / yscale が必要です")
        xlim = item.xlim or (None, None)
        ylim = item.ylim or (None, None)
        db.upsert_plot(
            run_id=run_id, label=item.label, kind=item.plot_kind, title=item.title,
            xlabel=item.xlabel or "", ylabel=item.ylabel or "", xscale=item.xscale, yscale=item.yscale,
            xmin=xlim[0], xmax=xlim[1], ymin=ylim[0], ymax=ylim[1], defined=1, updated_at=item.sent_at,
        )
        return
    xs, ys = item.xs or [], item.ys or []
    if len(xs) != len(ys):
        raise HTTPException(400, "xs と ys の長さが違います")
    if not all(math.isfinite(v) for v in (*xs, *ys)):
        raise HTTPException(400, "xs / ys に有限でない値があります")
    if db.get_plot(run_id, item.label) is None:
        db.upsert_plot(
            run_id=run_id, label=item.label, kind="line", title=None, xlabel="", ylabel="",
            xscale="linear", yscale="linear", xmin=None, xmax=None, ymin=None, ymax=None,
            defined=0, updated_at=item.sent_at,
        )
    db.add_plot_points(run_id, item.label, item.series or "default", xs, ys, item.step, source, item.sent_at)


# ---------------------------------------------------------------------------
# draft の初期config（design.md §3）

def _read_repo_config(settings: Settings, project: str) -> str:
    project_dir = settings.repo_root / project
    for name in ("config.toml", "config.example.toml"):
        path = project_dir / name
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise HTTPException(400, f"{project} に config.toml / config.example.toml がありません")


def _build_draft_config(db: Database, settings: Settings, project: str, group: str, new_group: bool) -> str:
    runs = db.list_runs_by_project(project)  # created_at 降順
    if not new_group:
        for r in runs:
            if r["run_group"] == group:
                return r["config_toml"]
    else:
        for r in runs:
            if r["status"] != "draft":
                return set_wandb_group(r["config_toml"], group)
    base = _read_repo_config(settings, project)
    if new_group:
        base = set_wandb_group(base, group)
    return base


def create_app(
    settings: Settings,
    remote: RemoteControllerProtocol | None = None,
    start_background: bool = True,
) -> FastAPI:
    real_remote = remote is None
    if remote is None:
        from .remote import RemoteController  # 遅延import（担当Cの実体。テストでは差し替える）

        remote = RemoteController(settings)

    settings.server.media_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.server.db_path)
    db.init_schema()
    snapshot_kwargs: dict = {}
    if real_remote:  # テストの偽 remote では、リポジトリが git とは限らないのでコードの記録・復元をしない
        snapshot_kwargs = {
            "snapshot_code": lambda run_id: code_snapshot.snapshot_code(settings.repo_root, run_id),
            "export_code": lambda commit, dest: code_snapshot.export_commit(settings.repo_root, commit, dest),
            "tag_code": lambda run_id, commit: code_snapshot.set_ref(settings.repo_root, run_id, commit),
        }
    job_manager = JobManager(settings, db, remote, launch_in_background=start_background, **snapshot_kwargs)
    if start_background:
        job_manager.start_background()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        job_manager.shutdown()

    app = FastAPI(lifespan=lifespan)

    allowed_networks = [ipaddress.ip_network(n) for n in settings.server.allowed_networks]
    if allowed_networks:  # 空なら制限しない（テスト用）
        @app.middleware("http")
        async def restrict_to_vpn(request: Request, call_next):
            if not _is_allowed_client(request.client.host if request.client else None, allowed_networks):
                return JSONResponse({"detail": "VPN（tailscale）内からのみアクセスできます"}, status_code=403)
            return await call_next(request)

    app.state.settings = settings
    app.state.db = db
    app.state.job_manager = job_manager

    # ---- frontend 向け（CONTRACT.md §2.2） ---------------------------

    def check_gpu_free() -> None:
        """学習用PCのGPUを他の人が使っていないか確認し、使っていれば起動させない（design.md §4）。"""
        try:
            usage = remote.gpu_usage()
        except Exception as e:  # ssh 失敗等。確認できないまま起動はしない
            raise HTTPException(502, f"学習用PCのGPU使用状況を確認できないため起動しません: {e}") from e
        threshold = settings.gpu_busy_threshold_mib
        if usage.other_mib > threshold:
            raise HTTPException(
                409,
                f"学習用PCのGPUを他の人が使用中のため起動しません（自分たち以外で {usage.other_mib} MiB 使用中、"
                f"しきい値 {threshold:g} MiB）",
            )

    # project の検出はリポジトリ全体を走査して main.py を解析するので重い（数十ms）。frontend は数秒ごとに
    # ツリーを取りに来るため、結果を project_scan_interval_sec だけ使い回す。「再スキャン」は即時に探し直す。
    scan_lock = threading.Lock()
    scan_cache: dict = {"at": None, "projects": []}

    def get_projects(force: bool = False) -> list[str]:
        with scan_lock:
            at = scan_cache["at"]
            if force or at is None or time.monotonic() - at >= settings.project_scan_interval_sec:
                scan_cache["projects"] = discovery.discover_projects(settings.repo_root)
                scan_cache["at"] = time.monotonic()
            return list(scan_cache["projects"])

    @app.get("/api/tree")
    def get_tree() -> list[dict]:
        return _build_tree(db, get_projects())

    @app.post("/api/rescan")
    def rescan() -> list[dict]:
        return _build_tree(db, get_projects(force=True))

    @app.get("/api/state")
    def get_state() -> dict:
        busy, active = job_manager.get_busy_state()
        return {"busy": busy, "active": active}

    @app.post("/api/runs/draft")
    def create_draft(body: DraftRequest) -> dict:
        # 使い回している一覧に無ければ、新しく作られた project かもしれないので探し直してから判定する
        if body.project not in get_projects() and body.project not in get_projects(force=True):
            raise HTTPException(404, "project が見つかりません")
        source = None
        if body.parent_run_id or body.rerun_of:
            source = db.get_run(body.parent_run_id or body.rerun_of)
            if source is None or source["project"] != body.project:
                raise HTTPException(404, "元の run が見つかりません")
            if source["status"] == "draft":
                raise HTTPException(400, "下書きの run は元にできません")
            if body.rerun_of and not source.get("code_commit"):
                raise HTTPException(400, "この run はコードを記録していないため、同じコードでは再実行できません")
            body = body.model_copy(update={"group": source["run_group"], "new_group": False})
        if body.new_group:
            group = body.group or new_group_id(exists=lambda g: db.group_exists(body.project, g))
            group_name = (body.group_name or "").strip() or db.next_group_name(body.project)
        elif body.group:
            group, group_name = body.group, None
        else:
            raise HTTPException(400, "既存groupへの追加では group（group_id）が必要です")
        try:
            config_toml = source["config_toml"] if source else _build_draft_config(
                db, settings, body.project, group, body.new_group)
        except TomlValidationError as e:
            raise HTTPException(400, str(e)) from e
        db.ensure_group(body.project, group, group_name)
        run_id = new_run_id(exists=lambda r: r in db.list_run_ids())
        now = now_iso()
        db.create_run(
            run_id=run_id, project=body.project, run_group=group,
            display_name=db.next_display_name(body.project, group), origin="webui", status="draft",
            config_toml=config_toml, parent_run_id=body.parent_run_id,
            code_commit=source["code_commit"] if body.rerun_of else None,
            git_commit=source["git_commit"] if body.rerun_of else None,
            git_diff=source["git_diff"] if body.rerun_of else None,
            git_untracked=source["git_untracked"] if body.rerun_of else None,
            git_info_source=source["git_info_source"] if body.rerun_of else None, created_at=now, started_at=None, ended_at=None,
            last_heartbeat_at=None, hostname=None, pid=None, exit_code=None, error=None,
        )
        return _run_detail(db, db.get_run(run_id))

    @app.put("/api/groups")
    def rename_group(body: GroupRenameRequest) -> dict:
        name = body.group_name.strip()
        if not name:
            raise HTTPException(400, "group_name が空です")
        if not db.group_exists(body.project, body.group):
            raise HTTPException(404, "group が見つかりません")
        db.rename_group(body.project, body.group, name)
        return {"project": body.project, "group": body.group, "group_name": name}

    @app.put("/api/groups/description")
    def set_group_description(body: GroupDescriptionRequest) -> dict:
        """group（=モデル構成）の説明。run の状態によらずいつでも書き換えられる。"""
        if not db.group_exists(body.project, body.group):
            raise HTTPException(404, "group が見つかりません")
        db.set_group_description(body.project, body.group, body.description)
        return {"project": body.project, "group": body.group, "description": body.description}

    @app.get("/api/runs/resolve")
    def resolve_run(project: str, group: str) -> dict:
        run = db.find_latest_train_run(project, group)
        if run is None:
            raise HTTPException(404, "対象のrunが見つかりません")
        return {"run_id": run["run_id"]}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run が見つかりません")
        return _run_detail(db, run)

    @app.put("/api/runs/{run_id}/config")
    def update_config(run_id: str, body: ConfigRequest) -> dict:
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run が見つかりません")
        if run["status"] != "draft":
            raise HTTPException(409, "draft以外のrunのconfigは編集できません")
        try:
            group = extract_group(body.config_toml)
        except TomlValidationError as e:
            raise HTTPException(400, str(e)) from e
        db.ensure_group(run["project"], group)  # config で未登録の group_id に書き換えられたら、その group を作る
        fields: dict = {"config_toml": body.config_toml, "run_group": group}
        if group != run["run_group"]:  # 別groupに移ったら、そのgroupでの次の番号を振り直す
            fields["display_name"] = db.next_display_name(run["project"], group)
        db.update_run(run_id, **fields)
        return _run_detail(db, db.get_run(run_id))

    @app.put("/api/runs/{run_id}/comment")
    def update_comment(run_id: str, body: CommentRequest) -> dict:
        """run へのコメント（メモ）。run の状態によらず（学習中・終了後でも）いつでも書き換えられる。"""
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run が見つかりません")
        db.update_run(run_id, comment=body.comment)
        return _run_detail(db, db.get_run(run_id))

    def _comparison_public(row: dict) -> dict:
        return {
            "id": row["id"], "name": row["name"], "run_ids": json.loads(row["run_ids"]),
            "labels": json.loads(row["labels"]), "hidden_labels": json.loads(row["hidden_labels"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    @app.get("/api/comparisons")
    def list_comparisons() -> list[dict]:
        return [_comparison_public(r) for r in db.list_comparisons()]

    @app.post("/api/comparisons")
    def create_comparison(body: ComparisonCreate) -> dict:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "名前が空です")
        comparison_id = new_run_id(exists=lambda c: db.get_comparison(c) is not None)
        db.create_comparison(
            comparison_id, name, json.dumps(body.run_ids), json.dumps(body.labels), json.dumps(body.hidden_labels),
        )
        return _comparison_public(db.get_comparison(comparison_id))

    @app.put("/api/comparisons/{comparison_id}")
    def update_comparison(comparison_id: str, body: ComparisonUpdate) -> dict:
        if db.get_comparison(comparison_id) is None:
            raise HTTPException(404, "比較が見つかりません")
        fields: dict = {}
        if body.name is not None:
            if not body.name.strip():
                raise HTTPException(400, "名前が空です")
            fields["name"] = body.name.strip()
        for key in ("run_ids", "labels", "hidden_labels"):
            value = getattr(body, key)
            if value is not None:
                fields[key] = json.dumps(value)
        db.update_comparison(comparison_id, **fields)
        return _comparison_public(db.get_comparison(comparison_id))

    @app.delete("/api/comparisons/{comparison_id}", status_code=204)
    def delete_comparison(comparison_id: str) -> Response:
        if db.get_comparison(comparison_id) is None:
            raise HTTPException(404, "比較が見つかりません")
        db.delete_comparison(comparison_id)
        return Response(status_code=204)

    @app.put("/api/view", status_code=204)
    def put_view(body: ViewStateRequest) -> Response:
        db.set_view_state(body.model_dump_json())
        return Response(status_code=204)

    @app.post("/api/runs/{run_id}/start")
    async def start_run(run_id: str) -> dict:
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run が見つかりません")
        if run["status"] != "draft":
            raise HTTPException(409, "draftではないrunは開始できません")
        busy, _active = job_manager.get_busy_state()
        if busy:
            raise HTTPException(409, "GPUを使用中の別のジョブがあります")
        await run_in_threadpool(check_gpu_free)
        try:
            await run_in_threadpool(job_manager.start_train, run_id)
        except JobBusyError as e:
            raise HTTPException(409, str(e)) from e
        return _run_detail(db, db.get_run(run_id))

    @app.post("/api/runs/{run_id}/stop")
    async def stop_run(run_id: str) -> dict:
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run が見つかりません")
        try:
            await run_in_threadpool(job_manager.stop_job, run_id)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        return _run_detail(db, db.get_run(run_id))

    @app.delete("/api/runs/{run_id}", status_code=204)
    def delete_run(run_id: str) -> Response:
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run が見つかりません")
        if run["status"] == "running":
            raise HTTPException(409, "runningのrunは削除できません")
        for job in db.list_jobs_for_run(run_id):
            (settings.server.media_dir / "logs" / f"{job['job_id']}.log").unlink(missing_ok=True)
        image_dir = settings.server.media_dir / "images" / run_id
        if image_dir.exists():
            shutil.rmtree(image_dir)
        db.delete_run(run_id)
        code_snapshot.delete_ref(settings.repo_root, run_id)
        try:
            remote.remove_run_files(run_id)  # 学習用PCの重み・ログ。繋がらなくても run の削除は成功させる
        except Exception:  # noqa: BLE001
            pass
        return Response(status_code=204)

    @app.post("/api/groups/infer")
    async def start_infer(body: InferRequest) -> dict:
        if body.run_id:
            target = db.get_run(body.run_id)
            if target is None or target["project"] != body.project:
                raise HTTPException(404, "対象のrunが見つかりません")
            if target["status"] in ("draft", "running", "failed"):
                raise HTTPException(400, f"{target['status']} の run では推論できません（学習が終わった run を選んでください）")
            if not any(a["kind"] == "checkpoint" for a in db.list_artifacts(target["run_id"])):
                raise HTTPException(400, "この run には重みが登録されていません（古い run は各 group の最新の run にしか重みがありません）")
        elif body.group:
            target = db.find_latest_train_run(body.project, body.group)
            if target is None:
                raise HTTPException(404, "対象のrunが見つかりません")
        else:
            raise HTTPException(400, "group か run_id のどちらかが必要です")
        config_toml = target["config_toml"]
        for assignment in body.overrides:
            key, eq, literal = assignment.partition("=")
            if not eq:
                raise HTTPException(400, f"`section.key=値` の形で指定してください: {assignment!r}")
            try:
                config_toml = set_value(config_toml, key.strip(), literal.strip(), add_missing=True)
            except TomlValidationError as e:
                raise HTTPException(400, str(e)) from e
        busy, _active = job_manager.get_busy_state()
        if busy:
            raise HTTPException(409, "GPUを使用中の別のジョブがあります")
        await run_in_threadpool(check_gpu_free)
        try:
            job = await run_in_threadpool(job_manager.start_infer, target["run_id"], body.project, config_toml)
        except JobBusyError as e:
            raise HTTPException(409, str(e)) from e
        return _job_public(job)

    @app.get("/api/jobs/{job_id}/log")
    def get_job_log(job_id: str, offset: int = 0) -> dict:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job が見つかりません")
        text, new_offset = job_manager.read_local_log(job_id, offset)
        return {"data": text, "offset": new_offset, "done": job["status"] != "running"}

    @app.get("/api/runs/{run_id}/results")
    def get_results(run_id: str, kind: str | None = None, label: str | None = None) -> list[dict]:
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run が見つかりません")
        return [_result_public(r) for r in db.list_results(run_id, kind=kind, label=label)]

    @app.get("/api/runs/{run_id}/plots")
    def get_plots(run_id: str, label: str | None = None, after_id: int | None = None,
                  max_points: int = Query(1000, ge=4)) -> list[dict]:
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run が見つかりません")
        upto = db.max_plot_point_id(run_id) or 0
        return [_plot_public(db, row, after_id, max_points, upto) for row in db.list_plots(run_id, label=label)]

    @app.get("/api/runs/{run_id}/plots/binned")
    def get_binned_plot(run_id: str, label: str, bin: str = "epoch") -> dict:
        """line のグラフを、x を一定の幅ごとに平均して返す（全点から作る。間引かない）。

        bin="epoch": その run の config の steps_per_epoch を幅にし、x は epoch 番号（1始まり）にする。
        bin=<数値>: その幅（x の単位）ごとに平均し、x は各区間の終わりの値にする。
        """
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run が見つかりません")
        plot = next((p for p in db.list_plots(run_id, label=label)), None)
        if plot is None:
            raise HTTPException(404, "グラフが見つかりません")
        if plot["kind"] != "line":
            raise HTTPException(400, "平均できるのは line のグラフだけです")
        if bin == "epoch":
            width = find_number(run["config_toml"], "steps_per_epoch")
            if width is None or width <= 0:
                raise HTTPException(400, "config に steps_per_epoch が無いため epoch ごとにはできません（数値で幅を指定してください）")
            to_x = lambda k: k + 1  # noqa: E731 - epoch 番号（1始まり）
        else:
            try:
                width = float(bin)
            except ValueError:
                raise HTTPException(400, "bin は \"epoch\" か正の数にしてください") from None
            if not width > 0:
                raise HTTPException(400, "bin は \"epoch\" か正の数にしてください")
            to_x = lambda k: (k + 1) * width  # noqa: E731 - 区間の終わり
        upto = db.max_plot_point_id(run_id) or 0
        series = []
        for stat in db.list_plot_series_stats(run_id, label, upto):
            pts = db.list_plot_points(run_id, label, stat["series"], upto)
            bins = binning.bin_mean(pts, width)
            series.append({"name": stat["series"], "points": [[to_x(k), mean] for k, mean, _n in bins],
                           "total_points": stat["total"]})
        return {"label": label, "bin": bin, "width": width, "epoch": bin == "epoch", "series": series}

    @app.get("/api/labels")
    def get_labels(run_ids: str = "") -> list[dict]:
        ids = [r for r in run_ids.split(",") if r]
        return db.list_labels(ids)

    # ---- client 向け（CONTRACT.md §2.3） ------------------------------

    @app.post("/api/runs")
    def client_create_run(body: ClientRunCreate) -> dict:
        busy, _active = job_manager.get_busy_state()
        warning = "GPUを使用中の別のジョブがある状態で起動されました" if busy else None
        run_id = new_run_id(exists=lambda r: r in db.list_run_ids())
        last_sync = db.get_last_sync()
        now = now_iso()
        db.ensure_group(body.project, body.group)
        db.create_run(
            run_id=run_id, project=body.project, run_group=body.group,
            display_name=db.next_display_name(body.project, body.group), origin="cli", status="running",
            config_toml=body.config_toml,
            git_commit=last_sync["git_commit"] if last_sync else None,
            git_diff=last_sync["git_diff"] if last_sync else None,
            git_untracked=last_sync["git_untracked"] if last_sync else None,
            git_info_source="last_sync" if last_sync else None,
            created_at=now, started_at=now, ended_at=None, last_heartbeat_at=now,
            hostname=body.hostname, pid=body.pid, exit_code=None, error=None,
        )
        return {"run_id": run_id, "warning": warning}

    @app.post("/api/runs/{run_id}/artifacts", status_code=204)
    def client_register_artifact(run_id: str, body: ArtifactRegister) -> Response:
        """client（学習プロセス）が、重みなどを artifact_dir に保存したあとに場所を知らせる。本体は送らない。"""
        if db.get_run(run_id) is None:
            raise HTTPException(404, "run が見つかりません")
        db.upsert_artifact(run_id, body.name, body.kind, body.path, body.size, now_iso())
        return Response(status_code=204)

    @app.get("/api/runs/{run_id}/artifacts")
    def list_artifacts(run_id: str) -> list[dict]:
        if db.get_run(run_id) is None:
            raise HTTPException(404, "run が見つかりません")
        return db.list_artifacts(run_id)

    @app.post("/api/runs/{run_id}/heartbeat", status_code=204)
    def heartbeat(run_id: str) -> Response:
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run が見つかりません")
        if run["origin"] == "cli":
            fields: dict = {"last_heartbeat_at": now_iso()}
            if run["status"] == "lost":
                fields["status"] = "running"
            db.update_run(run_id, **fields)
        return Response(status_code=204)

    @app.post("/api/runs/{run_id}/finish", status_code=204)
    def finish_run(run_id: str, body: FinishRequest) -> Response:
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run が見つかりません")
        if run["origin"] == "cli":
            db.update_run(run_id, status=body.status, ended_at=now_iso(), error=body.error)
        elif body.error is not None:
            db.update_run(run_id, error=body.error)
        return Response(status_code=204)

    @app.post("/api/results", status_code=204)
    def post_results(body: ResultsRequest) -> Response:
        run = db.get_run(body.run_id)
        if run is None:
            raise HTTPException(404, "run が見つかりません")
        # 学習は1秒に何百件も送るので、metric は溜めて1トランザクションで入れ、ラベルの検査も組ごとに1回だけにする
        # （1件ずつ接続・commit していた頃は秒30件ほどしか捌けず、送信が学習に追いつかなかった）
        checked: set[tuple[str, bool, str]] = set()
        pending_metrics: list[tuple] = []

        def flush_metrics() -> None:
            if pending_metrics:
                db.create_metric_results(body.run_id, body.source, pending_metrics)
                pending_metrics.clear()

        for item in body.items:
            is_plot = item.kind in ("plot_def", "plot_points")
            key = (item.label, is_plot, item.kind)
            if key not in checked:
                flush_metrics()  # 検査は DB を見るので、溜めた分を先に入れてから
                _check_label_kind(db, body.run_id, item.label, is_plot, item.kind)
                checked.add(key)
            if item.kind == "metric":
                pending_metrics.append((item.label, item.step, item.value, item.sent_at))
                continue
            flush_metrics()
            if is_plot:
                _save_plot_item(db, body.run_id, body.source, item)
                continue
            if item.kind == "table":
                if not item.name or item.value is None:
                    raise HTTPException(400, "table には name と value が必要です")
                db.create_result(
                    run_id=body.run_id, kind="table", label=item.label, name=item.name, step=item.step,
                    value=item.value, image_path=None, source=body.source, sent_at=item.sent_at,
                )
                continue
            if not item.image_b64:
                raise HTTPException(400, "image_b64 がありません")
            try:
                image_bytes = base64.b64decode(item.image_b64, validate=True)
            except binascii.Error as e:
                raise HTTPException(400, f"image_b64 のデコードに失敗しました: {e}") from e
            result_id = db.create_result(
                run_id=body.run_id, kind="image", label=item.label, step=item.step,
                value=None, image_path=None, source=body.source, sent_at=item.sent_at,
            )
            rel_path = f"images/{body.run_id}/{result_id}.png"
            abs_path = settings.server.media_dir / rel_path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_bytes(image_bytes)
            db.update_result(result_id, image_path=rel_path)
        flush_metrics()
        return Response(status_code=204)

    # ---- 静的配信 --------------------------------------------------------

    app.mount("/media", StaticFiles(directory=str(settings.server.media_dir)), name="media")
    if _FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")

    return app


def _build_tree(db: Database, projects: list[str]) -> list[dict]:
    cats, cats_stages = categories.load_workspaces(), categories.load_stages()  # 小さいファイルなので毎回読む（編集がすぐ反映される）
    tree = []
    for project in projects:
        runs = db.list_runs_by_project(project)  # created_at 降順
        groups: dict[str, list[dict]] = {}
        for r in runs:
            groups.setdefault(r["run_group"], []).append(r)
        names = db.group_names(project)
        group_list = [
            {"group": g, "group_name": names.get(g, g), "runs": [_run_summary(r, names.get(g)) for r in groups[g]]}
            for g in sorted(groups, key=lambda g: names.get(g, g))
        ]
        tree.append({"project": project, "workspace": categories.classify(project, cats),
                     "stage": categories.stage_of(project, cats_stages), "groups": group_list})
    return tree
