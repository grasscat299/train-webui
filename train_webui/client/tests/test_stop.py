from train_webui.client import _core


def test_stop_requested(tmp_path, monkeypatch):
    f = tmp_path / "stop.request"
    monkeypatch.delenv("TRAIN_WEBUI_STOP_FILE", raising=False)
    assert _core.stop_requested() is False
    monkeypatch.setenv("TRAIN_WEBUI_STOP_FILE", str(f))
    assert _core.stop_requested() is False
    f.touch()
    assert _core.stop_requested() is True
