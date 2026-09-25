from __future__ import annotations

import sys

import pytest

from train_webui.client import _core
from ._fake_server import FakeServer

_ENV_KEYS = [
    "TRAIN_WEBUI_URL", "TRAIN_WEBUI_RUN_ID", "TRAIN_WEBUI_PROJECT", "TRAIN_WEBUI_SOURCE",
]


@pytest.fixture(autouse=True)
def _clean_client_state(monkeypatch):
    """各テストの前後でclientのグローバル状態を初期化する。"""
    original_excepthook = sys.excepthook
    _core._client = None
    _core._atexit_registered = True  # テストのたびにatexit登録が積み上がるのを防ぐ
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    # 待ち時間をテストで短くする
    monkeypatch.setattr(_core, "FLUSH_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(_core, "HTTP_TIMEOUT_SEC", 3.0)
    monkeypatch.setattr(_core, "HEARTBEAT_INTERVAL_SEC", 0.1)
    monkeypatch.setattr(_core, "SENDER_JOIN_TIMEOUT_SEC", 3.0)
    monkeypatch.setattr(_core, "HEARTBEAT_JOIN_TIMEOUT_SEC", 1.0)
    yield
    if _core._client is not None:
        try:
            _core._client.do_finish(status="finished", error=None)
        except Exception:
            pass
    _core._client = None
    sys.excepthook = original_excepthook


@pytest.fixture
def fake_server():
    server = FakeServer()
    yield server
    server.stop()
