"""FastAPI アプリ本体（CONTRACT.md §2）。`create_app` がテスト・本番共通のエントリーポイント。"""
from __future__ import annotations

import base64
import binascii
import ipaddress
import math
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import discovery
from .db import Database, now_iso
from .decimate import decimate_minmax, decimate_stride
from .ids import new_group_id, new_run_id
from .jobs import JobBusyError, JobManager
from .remote.types import RemoteControllerProtocol
from .settings import Settings
from .toml_utils import TomlValidationError, extract_group, set_wandb_group

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


class GroupRenameRequest(BaseModel):
    project: str
    group: str  # group_id
    group_name: str


class ConfigRequest(BaseModel):
    config_toml: str


class CommentRequest(BaseModel):
    comment: str


class InferRequest(BaseModel):
    project: str
    group: str


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
    kind: Literal["metric", "image", "plot_def", "plot_points"]
    label: str
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
        "comment": row["comment"] or "",
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


def _check_label_kind(db: Database, run_id: str, label: str, is_plot: bool) -> None:
    """同じ run で plot と metric/image に同じラベルを使わせない（CONTRACT.md §2.3）。"""
    if is_plot:
        other = db.result_label_kind(run_id, label)
        if other is not None:
            raise HTTPException(400, f"ラベル {label} は既に {other} として使われています")
    elif db.get_plot(run_id, label) is not None:
        raise HTTPException(400, f"ラベル {label} は既に plot として使われています")


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
    if remote is None:
        from .remote import RemoteController  # 遅延import（担当Cの実体。テストでは差し替える）

        remote = RemoteController(settings)

    settings.server.media_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.server.db_path)
    db.init_schema()
    job_manager = JobManager(settings, db, remote, launch_in_background=start_background)
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

    @app.get("/api/tree")
    def get_tree() -> list[dict]:
        return _build_tree(db, settings)

    @app.post("/api/rescan")
    def rescan() -> list[dict]:
        return _build_tree(db, settings)

    @app.get("/api/state")
    def get_state() -> dict:
        busy, active = job_manager.get_busy_state()
        return {"busy": busy, "active": active}

    @app.post("/api/runs/draft")
    def create_draft(body: DraftRequest) -> dict:
        projects = discovery.discover_projects(settings.repo_root)
        if body.project not in projects:
            raise HTTPException(404, "project が見つかりません")
        if body.new_group:
            group = body.group or new_group_id(exists=lambda g: db.group_exists(body.project, g))
            group_name = (body.group_name or "").strip() or db.next_group_name(body.project)
        elif body.group:
            group, group_name = body.group, None
        else:
            raise HTTPException(400, "既存groupへの追加では group（group_id）が必要です")
        try:
            config_toml = _build_draft_config(db, settings, body.project, group, body.new_group)
        except TomlValidationError as e:
            raise HTTPException(400, str(e)) from e
        db.ensure_group(body.project, group, group_name)
        run_id = new_run_id(exists=lambda r: r in db.list_run_ids())
        now = now_iso()
        db.create_run(
            run_id=run_id, project=body.project, run_group=group,
            display_name=db.next_display_name(body.project, group), origin="webui", status="draft",
            config_toml=config_toml, git_commit=None, git_diff=None, git_untracked=None,
            git_info_source=None, created_at=now, started_at=None, ended_at=None,
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
        return Response(status_code=204)

    @app.post("/api/groups/infer")
    async def start_infer(body: InferRequest) -> dict:
        target = db.find_latest_train_run(body.project, body.group)
        if target is None:
            raise HTTPException(404, "対象のrunが見つかりません")
        busy, _active = job_manager.get_busy_state()
        if busy:
            raise HTTPException(409, "GPUを使用中の別のジョブがあります")
        await run_in_threadpool(check_gpu_free)
        try:
            job = await run_in_threadpool(job_manager.start_infer, target["run_id"], body.project)
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
        for item in body.items:
            is_plot = item.kind in ("plot_def", "plot_points")
            _check_label_kind(db, body.run_id, item.label, is_plot)
            if is_plot:
                _save_plot_item(db, body.run_id, body.source, item)
                continue
            if item.kind == "metric":
                db.create_result(
                    run_id=body.run_id, kind="metric", label=item.label, step=item.step,
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
        return Response(status_code=204)

    # ---- 静的配信 --------------------------------------------------------

    app.mount("/media", StaticFiles(directory=str(settings.server.media_dir)), name="media")
    if _FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")

    return app


def _build_tree(db: Database, settings: Settings) -> list[dict]:
    projects = discovery.discover_projects(settings.repo_root)
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
        tree.append({"project": project, "groups": group_list})
    return tree
