"""「学習用PCの台が空いているのに、Claude が止まっている」ときに通知する（画面のバナーと、設定があれば webhook）。

Claude（Claude Code）の活動は、フック（.claude/settings.json の PostToolUse）が `POST /api/claude/heartbeat` を呼んで知らせる。
最後の活動から idle_after_sec たっていて、かつ今ジョブを載せてよい台がある（MachineManager.status の selected が None でない）とき、通知を出す。
どちらかが解けたら通知は消える。webhook は、通知が出た時と、出続けている間は renotify_sec ごとに 1 回送る。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Callable

from .settings import NotifySettings


class IdleAlert:
    def __init__(self, status_fn: Callable[[], dict], settings: NotifySettings, clock: Callable[[], float] = time.time,
                 post: Callable[[str, dict], None] | None = None) -> None:
        self._status_fn = status_fn
        self._s = settings
        self._clock = clock
        self._post = post or self._http_post
        self._lock = threading.Lock()
        self._last_seen: float | None = None
        self._since: float | None = None
        self._last_notified: float | None = None
        self._alerts: list[dict] = []
        self._stop = threading.Event()

    @staticmethod
    def _http_post(url: str, body: dict) -> None:
        req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=8).read()

    def heartbeat(self) -> None:
        """Claude が動いている印（フックから呼ぶ）。通知は次の確認で消える。"""
        with self._lock:
            self._last_seen = self._clock()

    def alerts(self) -> list[dict]:
        with self._lock:
            return list(self._alerts)

    def check(self) -> list[dict]:
        """1 回確認して、今の通知の一覧を返す。"""
        now = self._clock()
        with self._lock:
            seen = self._last_seen
        idle = seen is None or now - seen > self._s.idle_after_sec
        free: list[str] = []
        if idle:
            st = self._status_fn()
            if st.get("selected"):
                free = [m["name"] for m in st.get("machines", []) if m.get("available")]
        with self._lock:
            if idle and free:
                if self._since is None:
                    self._since = now
                msg = (f"空いている台があります（{', '.join(free)}）が、Claude が {int((now - (seen or self._since)) // 60)} 分以上動いていません。"
                       if seen is not None else f"空いている台があります（{', '.join(free)}）が、Claude の活動がまだ確認できていません。")
                self._alerts = [{"kind": "idle_machines", "message": msg, "machines": free, "since": self._since}]
                notify = self._last_notified is None or now - self._last_notified >= self._s.renotify_sec
                if notify:
                    self._last_notified = now
            else:
                self._since = None
                self._last_notified = None
                self._alerts = []
                notify = False
            alerts = list(self._alerts)
        if notify and self._s.webhook_url:
            try:
                self._post(self._s.webhook_url, {"message": alerts[0]["message"]})
            except Exception:  # noqa: BLE001 - 通知の失敗で監視を止めない
                pass
        return alerts

    def start_background(self) -> None:
        def loop() -> None:
            while not self._stop.wait(self._s.check_interval_sec):
                try:
                    self.check()
                except Exception:  # noqa: BLE001
                    pass
        threading.Thread(target=loop, daemon=True, name="idle-alert").start()

    def shutdown(self) -> None:
        self._stop.set()
