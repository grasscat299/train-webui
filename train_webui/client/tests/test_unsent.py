"""送信の詰まり対策（plot の点をまとめる・送れなかった分をファイルに残す・replay で取り込む）のテスト。"""
from __future__ import annotations

import json
import threading

import train_webui.client as client
from train_webui.client import _core, replay


def _items(fake_server):
    return [item for r in fake_server.requests_for("POST", r"^/api/results$") for item in r.body["items"]]


def test_coalesce_merges_points_per_label_series():
    pts = lambda label, x, series="default": {"kind": "plot_points", "label": label, "series": series,
                                              "xs": [x], "ys": [x * 10], "step": x, "sent_at": "t"}
    batch = [pts("a", 1), {"kind": "metric", "label": "m", "value": 1.0}, pts("b", 1), pts("a", 2),
             pts("a", 3, series="s2"), pts("a", 4)]
    out = _core._coalesce(batch)
    assert [(i["kind"], i["label"], i.get("series")) for i in out] == [
        ("plot_points", "a", "default"), ("metric", "m", None), ("plot_points", "b", "default"),
        ("plot_points", "a", "s2")]
    assert out[0]["xs"] == [1, 2, 4] and out[0]["ys"] == [10, 20, 40] and out[0]["step"] == 4
    assert batch[0]["xs"] == [1]  # 元の item は変えない


def test_many_points_are_sent_in_few_items(monkeypatch, fake_server):
    monkeypatch.setenv("TRAIN_WEBUI_URL", fake_server.url)
    monkeypatch.setenv("TRAIN_WEBUI_RUN_ID", "20260201_000000")
    fake_server.route("POST", r"^/api/results$", lambda m, b, q: (204, None))
    client.init()
    plot = client.define_plot("train/loss", kind="line")
    for s in range(3000):
        plot.add(s, 1.0 / (s + 1), step=s)
    client.finish()
    items = [i for i in _items(fake_server) if i["kind"] == "plot_points"]
    xs = [x for i in items for x in i["xs"]]
    assert xs == [float(s) for s in range(3000)]
    assert len(items) < 100


def test_finish_timeout_spools_rest_and_replay_imports(monkeypatch, fake_server, tmp_path):
    monkeypatch.setenv("TRAIN_WEBUI_URL", fake_server.url)
    monkeypatch.setenv("TRAIN_WEBUI_RUN_ID", "20260202_000000")
    monkeypatch.setenv("TRAIN_WEBUI_ARTIFACT_DIR", str(tmp_path / "run" / "artifacts"))
    monkeypatch.setattr(_core, "SENDER_JOIN_TIMEOUT_SEC", 0.3)
    monkeypatch.setattr(_core, "HTTP_TIMEOUT_SEC", 1.0)
    release = threading.Event()
    received: list[dict] = []

    def slow(m, b, q):  # 最初の送信は server が遅くて timeout する
        if not release.is_set():
            release.wait(2.0)
        received.extend(b["items"])
        return 204, None

    fake_server.route("POST", r"^/api/results$", slow)
    client.init()
    for s in range(50):
        client.log({"train/loss": float(s)}, step=s)
    import time
    time.sleep(0.2)  # 送信スレッドが最初のバッチを取り出してから、残りを積む
    for s in range(50, 100):
        client.log({"train/loss": float(s)}, step=s)
    client.finish()
    files = list((tmp_path / "run" / "unsent").glob("*.jsonl"))
    assert len(files) == 1
    spooled = [it for line in files[0].read_text().splitlines() for it in json.loads(line)["items"]]
    assert len(spooled) == 100  # timeout した送信中のバッチも、残りもファイルに残る
    release.set()
    time.sleep(0.3)  # timeout した最初の送信が server 側で終わるのを待ってから数え直す
    received.clear()
    assert replay.main([str(files[0]), "--url", fake_server.url]) == 0
    assert sorted(i["step"] for i in received) == list(range(100))
    assert not files[0].exists() and files[0].with_name(files[0].name + ".done").exists()


def test_connect_error_retries_then_spools(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAIN_WEBUI_URL", "http://127.0.0.1:9")  # 誰も待っていない port
    monkeypatch.setenv("TRAIN_WEBUI_RUN_ID", "20260203_000000")
    monkeypatch.setenv("TRAIN_WEBUI_ARTIFACT_DIR", str(tmp_path / "run" / "artifacts"))
    monkeypatch.setattr(_core, "CONNECT_RETRY_SEC", (0.01, 0.01))
    client.init()
    client.log({"a": 1.0}, step=0)
    client.finish()
    files = list((tmp_path / "run" / "unsent").glob("*.jsonl"))
    assert len(files) == 1
    assert json.loads(files[0].read_text())["items"][0]["label"] == "a"
