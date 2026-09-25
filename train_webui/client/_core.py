"""train_webui client の実装本体（design.md §5, CONTRACT.md §5）。標準ライブラリのみで書く。

公開APIは `train_webui/client/__init__.py` から export する。ここはモジュールレベルの
状態（`_client`）を1つ持ち、init/log/log_image/finish の実処理を行う。
"""
from __future__ import annotations

import atexit
import base64
import io
import json
import os
import queue
import re
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# 送信の細かいタイミング。テストから差し替えられるようモジュール変数にしておく。
FLUSH_INTERVAL_SEC = 0.5
BATCH_SIZE = 50
HTTP_TIMEOUT_SEC = 5.0
HEARTBEAT_INTERVAL_SEC = 30.0
SENDER_JOIN_TIMEOUT_SEC = 10.0
HEARTBEAT_JOIN_TIMEOUT_SEC = 2.0

_client: "_Client | None" = None
_atexit_registered = False


def _warn(message: str) -> None:
    print(f"[train_webui] {message}", file=sys.stderr)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _repo_root() -> Path:
    # train_webui パッケージの親（CONTRACT.md §5）。 client/_core.py -> client -> train_webui -> repo_root
    return Path(__file__).resolve().parents[2]


class _NotFoundError(Exception):
    pass


def _read_error_detail(err: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(err.read().decode("utf-8"))
        return str(body.get("detail", ""))
    except Exception:
        return ""


def _http_request(method: str, url: str, body: dict | None = None):
    """標準ライブラリのみでJSON HTTPリクエストを行う。404はNotFoundErrorに変換する。"""
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise _NotFoundError(str(e)) from e
        raise RuntimeError(f"HTTP {e.code}: {_read_error_detail(e)}") from e
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def _read_wandb_group(config_path: Path) -> str | None:
    """[wandb] セクション内の `group = "..."` 行だけを読む最小TOMLパーサ（tomllib/tomliは使わない）。"""
    text = config_path.read_text(encoding="utf-8")
    in_wandb = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("["):
            m = re.match(r"^\[([^\]]+)\]", line)
            in_wandb = bool(m and m.group(1).strip() == "wandb")
            continue
        if not in_wandb or not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != "group":
            continue
        value = value.split("#", 1)[0].strip()
        return value.strip("\"'")
    return None


def _find_project_dir(start_file: str) -> Path | None:
    """呼び出し元ファイルから親を辿り、main.pyとconfig.tomlが両方あるディレクトリを探す。"""
    repo_root = _repo_root()
    try:
        current = Path(start_file).resolve().parent
    except Exception:
        return None
    while True:
        if (current / "main.py").is_file() and (current / "config.toml").is_file():
            return current
        if current == repo_root or current.parent == current:
            return None
        current = current.parent


def _determine_project_and_group(caller_file: str) -> tuple[str | None, str | None, str | None]:
    """(project, group, config_toml本文) を決める。design.md §5.1。"""
    env_project = os.environ.get("TRAIN_WEBUI_PROJECT")
    if env_project:
        project_dir = _repo_root() / env_project
        project = env_project
    else:
        found = _find_project_dir(caller_file)
        if found is None:
            return None, None, None
        project_dir = found
        project = found.relative_to(_repo_root()).as_posix()

    config_path = project_dir / "config.toml"
    if not config_path.is_file():
        return project, None, None
    try:
        config_text = config_path.read_text(encoding="utf-8")
        group = _read_wandb_group(config_path)
    except OSError:
        return project, None, None
    return project, group, config_text


class _Client:
    """1回の init() 〜 finish() のライフサイクルに対応する状態。"""

    def __init__(self) -> None:
        self.base_url: str | None = None
        self.enabled = False  # TRAIN_WEBUI_URL が設定されているか
        self.can_send = False  # run_id まで解決できて実際に送信できるか
        self.run_id: str | None = None
        self.project: str | None = None
        self.group: str | None = None
        self.source: str | None = None
        self.owns_run = False  # 自分がPOST /api/runsで作ったrunか（heartbeat/finish/excepthook対象）
        self._queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._sender_thread: threading.Thread | None = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._finish_lock = threading.Lock()
        self._finished = False

    # --- 送信スレッド ---

    def _sender_loop(self) -> None:
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            batch = [item]
            deadline = time.monotonic() + FLUSH_INTERVAL_SEC
            while len(batch) < BATCH_SIZE:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    batch.append(self._queue.get(timeout=remaining))
                except queue.Empty:
                    break
            self._send_batch(batch)

    def _send_batch(self, batch: list[dict]) -> None:
        body = {"run_id": self.run_id, "source": self.source, "items": batch}
        try:
            _http_request("POST", self.base_url + "/api/results", body)
        except Exception as e:
            _warn(f"結果の送信に失敗したため{len(batch)}件を破棄しました: {e}")

    # --- heartbeat ---

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(HEARTBEAT_INTERVAL_SEC):
            try:
                _http_request("POST", f"{self.base_url}/api/runs/{self.run_id}/heartbeat")
            except Exception as e:
                _warn(f"heartbeatの送信に失敗しました: {e}")

    # --- 公開操作 ---

    def log(self, data: dict, step: int | None) -> None:
        if not self.can_send:
            return
        sent_at = _now_iso()
        for label, value in data.items():
            self._queue.put({
                "kind": "metric", "label": label, "step": step,
                "value": value, "sent_at": sent_at,
            })

    def log_image(self, label: str, fig_or_path, step: int | None) -> None:
        if not self.can_send:
            return
        try:
            png_bytes = _read_image_bytes(fig_or_path)
        except Exception as e:
            _warn(f"画像の読み込みに失敗したため送信をスキップしました(label={label}): {e}")
            return
        self._queue.put({
            "kind": "image", "label": label, "step": step,
            "image_b64": base64.b64encode(png_bytes).decode("ascii"),
            "sent_at": _now_iso(),
        })

    def do_finish(self, status: str, error: str | None) -> None:
        with self._finish_lock:
            if self._finished:
                return
            self._finished = True
        self._stop_event.set()
        if self._sender_thread is not None:
            self._sender_thread.join(timeout=SENDER_JOIN_TIMEOUT_SEC)
        if self._heartbeat_thread is not None:
            self._heartbeat_stop.set()
            self._heartbeat_thread.join(timeout=HEARTBEAT_JOIN_TIMEOUT_SEC)
        if self.owns_run and self.can_send:
            try:
                _http_request(
                    "POST", f"{self.base_url}/api/runs/{self.run_id}/finish",
                    {"status": status, "error": error},
                )
            except Exception as e:
                _warn(f"finishの送信に失敗しました: {e}")


def _read_image_bytes(fig_or_path) -> bytes:
    if hasattr(fig_or_path, "savefig"):
        buf = io.BytesIO()
        fig_or_path.savefig(buf, format="png")
        return buf.getvalue()
    with open(fig_or_path, "rb") as f:
        return f.read()


def _install_excepthook(client: _Client) -> None:
    previous = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        try:
            tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            client.do_finish(status="failed", error=tb_text)
        except Exception as e:
            _warn(f"未処理例外のfailed送信中にエラーが発生しました: {e}")
        previous(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


def _atexit_hook() -> None:
    if _client is not None:
        _client.do_finish(status="finished", error=None)


def _try_create_run(client: _Client, project: str | None, group: str | None, config_text: str | None) -> None:
    if project is None or group is None:
        _warn("projectまたはgroupを特定できないため新規runを作成できません。送信を無効化します。")
        return
    body = {
        "project": project, "group": group, "config_toml": config_text or "",
        "hostname": socket.gethostname(), "pid": os.getpid(),
    }
    try:
        resp = _http_request("POST", client.base_url + "/api/runs", body)
    except Exception as e:
        _warn(f"runの作成に失敗しました: {e}")
        return
    run_id = resp.get("run_id") if resp else None
    if not run_id:
        _warn("runの作成に失敗しました: レスポンスにrun_idがありません")
        return
    client.run_id = run_id
    client.owns_run = True
    client.can_send = True
    if resp.get("warning"):
        _warn(resp["warning"])


def _try_resolve_run(client: _Client, project: str | None, group: str | None) -> None:
    if project is None or group is None:
        _warn("projectまたはgroupを特定できないため送信先runを解決できません。")
        return
    query = urllib.parse.urlencode({"project": project, "group": group})
    try:
        resp = _http_request("GET", f"{client.base_url}/api/runs/resolve?{query}")
    except _NotFoundError:
        _warn(f"送信先run(project={project}, group={group})が見つかりません。送信をスキップします。")
        return
    except Exception as e:
        _warn(f"runの解決に失敗しました: {e}")
        return
    run_id = resp.get("run_id") if resp else None
    if not run_id:
        _warn("runの解決に失敗しました: レスポンスにrun_idがありません")
        return
    client.run_id = run_id
    client.owns_run = False
    client.can_send = True


def init(new_run: bool, run_id: str | None, source: str | None, caller_file: str) -> None:
    global _client, _atexit_registered
    try:
        if _client is not None and not _client._finished:
            _client.do_finish(status="finished", error=None)

        client = _Client()
        _client = client

        client.base_url = os.environ.get("TRAIN_WEBUI_URL")
        client.enabled = bool(client.base_url)
        if not client.enabled:
            _warn("TRAIN_WEBUI_URLが設定されていないため送信を無効化します。")

        resolved_run_id = run_id or os.environ.get("TRAIN_WEBUI_RUN_ID")
        project, group, config_text = _determine_project_and_group(caller_file)
        client.project = project
        client.group = group

        if resolved_run_id:
            client.run_id = resolved_run_id
            client.owns_run = False
            client.can_send = client.enabled
        elif new_run:
            if client.enabled:
                _try_create_run(client, project, group, config_text)
        else:
            if client.enabled:
                _try_resolve_run(client, project, group)

        if source is not None:
            client.source = source
        elif os.environ.get("TRAIN_WEBUI_SOURCE"):
            client.source = os.environ["TRAIN_WEBUI_SOURCE"]
        elif new_run:
            client.source = "train"
        else:
            client.source = Path(caller_file).stem

        if client.can_send:
            client._sender_thread = threading.Thread(target=client._sender_loop, daemon=True)
            client._sender_thread.start()
            if client.owns_run:
                client._heartbeat_thread = threading.Thread(target=client._heartbeat_loop, daemon=True)
                client._heartbeat_thread.start()
                _install_excepthook(client)

        if not _atexit_registered:
            atexit.register(_atexit_hook)
            _atexit_registered = True
    except Exception as e:
        _warn(f"初期化に失敗しました: {e}")


def log(data: dict, step: int | None) -> None:
    if _client is None:
        _warn("init前にlogが呼ばれたため無視します。")
        return
    try:
        _client.log(data, step)
    except Exception as e:
        _warn(f"logの処理に失敗しました: {e}")


def log_image(label: str, fig_or_path, step: int | None) -> None:
    if _client is None:
        _warn("init前にlog_imageが呼ばれたため無視します。")
        return
    try:
        _client.log_image(label, fig_or_path, step)
    except Exception as e:
        _warn(f"log_imageの処理に失敗しました: {e}")


def finish() -> None:
    if _client is None:
        _warn("init前にfinishが呼ばれたため無視します。")
        return
    try:
        _client.do_finish(status="finished", error=None)
    except Exception as e:
        _warn(f"finishの処理に失敗しました: {e}")
