from train_webui.backend.idle_alert import IdleAlert
from train_webui.backend.settings import NotifySettings


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _status(free):
    return {"selected": free[0] if free else None, "machines": [{"name": n, "available": True} for n in free]}


def test_alert_appears_only_when_machine_is_free_and_claude_is_idle_and_clears_on_heartbeat():
    clock, state, posts = Clock(), {"free": ["gpu3", "gpu5"]}, []
    a = IdleAlert(lambda: _status(state["free"]), NotifySettings(idle_after_sec=600, renotify_sec=1800, webhook_url="http://x"), clock, lambda u, b: posts.append((u, b)))
    a.heartbeat()
    assert a.check() == []                                              # Claude は今動いている
    clock.t += 601
    al = a.check()
    assert len(al) == 1 and al[0]["kind"] == "idle_machines" and al[0]["machines"] == ["gpu3", "gpu5"] and "gpu3" in al[0]["message"]
    assert len(posts) == 1 and posts[0][0] == "http://x"                # 通知が出た時に webhook
    clock.t += 100
    a.check()
    assert len(posts) == 1                                              # 出続けている間は、renotify_sec までは送らない
    clock.t += 1800
    a.check()
    assert len(posts) == 2
    a.heartbeat()
    assert a.check() == [] and a.alerts() == []                         # 動き出したら消える
    clock.t += 601
    state["free"] = []
    assert a.check() == []                                              # 空き台が無ければ通知しない（台が埋まっている = 待つしかない）


def test_no_heartbeat_yet_counts_as_idle_and_webhook_failure_does_not_stop_checks():
    clock = Clock()

    def boom(u, b):
        raise RuntimeError("x")

    a = IdleAlert(lambda: _status(["gpu3"]), NotifySettings(webhook_url="http://x"), clock, boom)
    al = a.check()
    assert len(al) == 1 and "確認できていません" in al[0]["message"]
    assert a.check()                                                    # 例外が出ても、監視は続く


def test_settings_parse_notify_and_endpoints(app_factory):
    from fastapi.testclient import TestClient
    app, settings, remote = app_factory()
    assert settings.notify.idle_after_sec == 600.0 and settings.notify.enabled
    c = TestClient(app)
    assert c.post("/api/claude/heartbeat").status_code == 204
    r = c.get("/api/alerts")
    assert r.status_code == 200 and r.json() == {"alerts": []}          # 動いた直後は、通知なし
