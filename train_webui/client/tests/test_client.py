"""train_webui.client の結合テスト。fake_server（http.server）をlocalhostに立てて検証する。"""
from __future__ import annotations

import base64
import sys
import time

import train_webui.client as client
from train_webui.client import _core


def _make_project(tmp_path, rel="proj/demo", group="grp1"):
    project_dir = tmp_path / rel
    project_dir.mkdir(parents=True)
    (project_dir / "main.py").write_text("import argparse\n", encoding="utf-8")
    (project_dir / "config.toml").write_text(
        f'[wandb]\nproject = "p"\ngroup = "{group}"\n', encoding="utf-8",
    )
    return project_dir


def _wait_until(predicate, timeout=2.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# --- init() のrun_id決定（3通り） ---


def test_run_id_from_env_takes_priority(monkeypatch, fake_server):
    monkeypatch.setenv("TRAIN_WEBUI_URL", fake_server.url)
    monkeypatch.setenv("TRAIN_WEBUI_RUN_ID", "20260101_000000")
    client.init(new_run=True)
    assert _core._client.run_id == "20260101_000000"
    assert _core._client.owns_run is False
    assert _core._client.can_send is True
    # 環境変数で決まったのでrun作成・resolveのどちらも呼ばれない
    assert fake_server.requests_for("POST", r"^/api/runs$") == []
    assert fake_server.requests_for("GET", r"^/api/runs/resolve$") == []


def test_new_run_creates_run_via_api(monkeypatch, fake_server, tmp_path):
    _make_project(tmp_path, "proj/demo", "grp1")
    monkeypatch.setattr(_core, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("TRAIN_WEBUI_URL", fake_server.url)
    monkeypatch.setenv("TRAIN_WEBUI_PROJECT", "proj/demo")

    def handle_create(match, body, query):
        assert body["project"] == "proj/demo"
        assert body["group"] == "grp1"
        assert "wandb" in body["config_toml"]
        assert isinstance(body["hostname"], str)
        assert isinstance(body["pid"], int)
        return 200, {"run_id": "20260102_000000", "warning": None}

    fake_server.route("POST", r"^/api/runs$", handle_create)

    client.init(new_run=True)

    assert _core._client.run_id == "20260102_000000"
    assert _core._client.owns_run is True
    assert _core._client.can_send is True
    assert len(fake_server.requests_for("POST", r"^/api/runs$")) == 1


def test_resolve_existing_run(monkeypatch, fake_server, tmp_path):
    _make_project(tmp_path, "proj/demo", "grp1")
    monkeypatch.setattr(_core, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("TRAIN_WEBUI_URL", fake_server.url)
    monkeypatch.setenv("TRAIN_WEBUI_PROJECT", "proj/demo")

    def handle_resolve(match, body, query):
        assert query == {"project": "proj/demo", "group": "grp1"}
        return 200, {"run_id": "20260103_000000"}

    fake_server.route("GET", r"^/api/runs/resolve$", handle_resolve)

    client.init()  # new_run=False

    assert _core._client.run_id == "20260103_000000"
    assert _core._client.owns_run is False
    assert _core._client.can_send is True


def test_resolve_not_found_disables_sending(monkeypatch, fake_server, tmp_path, capsys):
    _make_project(tmp_path, "proj/demo", "grp1")
    monkeypatch.setattr(_core, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("TRAIN_WEBUI_URL", fake_server.url)
    monkeypatch.setenv("TRAIN_WEBUI_PROJECT", "proj/demo")

    fake_server.route("GET", r"^/api/runs/resolve$", lambda m, b, q: (404, {"detail": "not found"}))

    client.init()
    client.log({"train/loss": 1.0})
    client.finish()

    assert _core._client.can_send is False
    assert fake_server.requests_for("POST", r"^/api/results$") == []
    assert "[train_webui]" in capsys.readouterr().err


# --- バッチ送信・画像 ---


def test_log_batches_are_sent_to_results(monkeypatch, fake_server):
    monkeypatch.setenv("TRAIN_WEBUI_URL", fake_server.url)
    monkeypatch.setenv("TRAIN_WEBUI_RUN_ID", "20260104_000000")
    fake_server.route("POST", r"^/api/results$", lambda m, b, q: (204, None))

    client.init(source="unit-test")
    for i in range(5):
        client.log({"train/loss": float(i), "train/acc": float(i) * 2}, step=i)
    client.finish()

    reqs = fake_server.requests_for("POST", r"^/api/results$")
    assert len(reqs) >= 1
    all_items = [item for r in reqs for item in r.body["items"]]
    assert len(all_items) == 10
    losses = sorted(item["value"] for item in all_items if item["label"] == "train/loss")
    assert losses == [0.0, 1.0, 2.0, 3.0, 4.0]
    for r in reqs:
        assert r.body["run_id"] == "20260104_000000"
        assert r.body["source"] == "unit-test"


class _FakeFigure:
    def __init__(self, payload: bytes):
        self._payload = payload

    def savefig(self, buf, format=None):  # noqa: A002 -- wandb/matplotlib風のシグネチャに合わせる
        assert format == "png"
        buf.write(self._payload)


def test_log_image_accepts_figure_and_path(monkeypatch, fake_server, tmp_path):
    monkeypatch.setenv("TRAIN_WEBUI_URL", fake_server.url)
    monkeypatch.setenv("TRAIN_WEBUI_RUN_ID", "20260105_000000")
    fake_server.route("POST", r"^/api/results$", lambda m, b, q: (204, None))

    client.init()

    fig_bytes = b"\x89PNGfakefromfigure"
    client.log_image("fig", _FakeFigure(fig_bytes))

    path_bytes = b"\x89PNGfakefrompath"
    png_path = tmp_path / "sample.png"
    png_path.write_bytes(path_bytes)
    client.log_image("path", str(png_path))

    client.finish()

    reqs = fake_server.requests_for("POST", r"^/api/results$")
    all_items = [item for r in reqs for item in r.body["items"]]
    by_label = {item["label"]: item for item in all_items}
    assert base64.b64decode(by_label["fig"]["image_b64"]) == fig_bytes
    assert base64.b64decode(by_label["path"]["image_b64"]) == path_bytes
    for item in all_items:
        assert item["kind"] == "image"


# --- heartbeat / finish / 例外 ---


def test_heartbeat_sent_periodically(monkeypatch, fake_server, tmp_path):
    _make_project(tmp_path, "proj/demo", "grp1")
    monkeypatch.setattr(_core, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("TRAIN_WEBUI_URL", fake_server.url)
    monkeypatch.setenv("TRAIN_WEBUI_PROJECT", "proj/demo")
    fake_server.route("POST", r"^/api/runs$", lambda m, b, q: (200, {"run_id": "20260106_000000", "warning": None}))
    fake_server.route("POST", r"^/api/runs/[^/]+/heartbeat$", lambda m, b, q: (204, None))

    client.init(new_run=True)

    assert _wait_until(
        lambda: len(fake_server.requests_for("POST", r"^/api/runs/20260106_000000/heartbeat$")) >= 2,
        timeout=2.0,
    )


def test_finish_sends_finish_request_once(monkeypatch, fake_server, tmp_path):
    _make_project(tmp_path, "proj/demo", "grp1")
    monkeypatch.setattr(_core, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("TRAIN_WEBUI_URL", fake_server.url)
    monkeypatch.setenv("TRAIN_WEBUI_PROJECT", "proj/demo")
    fake_server.route("POST", r"^/api/runs$", lambda m, b, q: (200, {"run_id": "20260107_000000", "warning": None}))
    fake_server.route("POST", r"^/api/runs/[^/]+/heartbeat$", lambda m, b, q: (204, None))
    fake_server.route("POST", r"^/api/runs/[^/]+/finish$", lambda m, b, q: (204, None))

    client.init(new_run=True)
    client.finish()
    client.finish()  # 2回目は無視される

    reqs = fake_server.requests_for("POST", r"^/api/runs/20260107_000000/finish$")
    assert len(reqs) == 1
    assert reqs[0].body == {"status": "finished", "error": None}


def test_uncaught_exception_reports_failed(monkeypatch, fake_server, tmp_path, capsys):
    _make_project(tmp_path, "proj/demo", "grp1")
    monkeypatch.setattr(_core, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("TRAIN_WEBUI_URL", fake_server.url)
    monkeypatch.setenv("TRAIN_WEBUI_PROJECT", "proj/demo")
    fake_server.route("POST", r"^/api/runs$", lambda m, b, q: (200, {"run_id": "20260108_000000", "warning": None}))
    fake_server.route("POST", r"^/api/runs/[^/]+/heartbeat$", lambda m, b, q: (204, None))
    fake_server.route("POST", r"^/api/runs/[^/]+/finish$", lambda m, b, q: (204, None))

    client.init(new_run=True)

    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = sys.exc_info()
    sys.excepthook(*exc_info)
    capsys.readouterr()  # 元のexcepthookの出力を握りつぶす

    reqs = fake_server.requests_for("POST", r"^/api/runs/20260108_000000/finish$")
    assert len(reqs) == 1
    assert reqs[0].body["status"] == "failed"
    assert "ValueError" in reqs[0].body["error"]
    assert "boom" in reqs[0].body["error"]


# --- URL未設定・init前呼び出し ---


def test_no_url_set_does_not_raise(monkeypatch, capsys):
    client.init(new_run=True)
    client.log({"a": 1})
    client.log_image("img", "/no/such/file.png")
    client.finish()
    assert _core._client.can_send is False
    assert "[train_webui]" in capsys.readouterr().err


def test_calls_before_init_do_not_raise(capsys):
    _core._client = None
    client.log({"a": 1})
    client.log_image("img", "/no/such/file.png")
    client.finish()
    assert "[train_webui]" in capsys.readouterr().err
