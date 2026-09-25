"""テスト用の偽WebUI server（http.serverのみ・localhost限定）。

受け取ったリクエストを記録しつつ、テストごとに登録したルールでレスポンスを返す。
"""
from __future__ import annotations

import json
import re
import threading
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class RecordedRequest:
    method: str
    path: str
    query: dict
    body: dict | None


@dataclass
class _Route:
    method: str
    pattern: re.Pattern
    handler: callable  # (match, body, query) -> (status, response_body_or_None)


class FakeServer:
    def __init__(self) -> None:
        self.requests: list[RecordedRequest] = []
        self._lock = threading.Lock()
        self._routes: list[_Route] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: D401 -- テスト出力を静かにする
                pass

            def _handle(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw.decode("utf-8")) if raw else None
                path, _, query_str = self.path.partition("?")
                query = {k: v[0] for k, v in urllib.parse.parse_qs(query_str).items()}
                with server._lock:
                    server.requests.append(RecordedRequest(method, path, query, body))
                for route in server._routes:
                    if route.method != method:
                        continue
                    match = route.pattern.match(path)
                    if not match:
                        continue
                    status, resp_body = route.handler(match, body, query)
                    self._write(status, resp_body)
                    return
                self._write(404, {"detail": f"no route for {method} {path}"})

            def _write(self, status: int, resp_body) -> None:
                self.send_response(status)
                if resp_body is None:
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                data = json.dumps(resp_body).encode("utf-8")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address
        return f"http://127.0.0.1:{port}"

    def route(self, method: str, pattern: str, handler) -> None:
        self._routes.append(_Route(method, re.compile(pattern), handler))

    def requests_for(self, method: str, pattern: str) -> list[RecordedRequest]:
        p = re.compile(pattern)
        return [r for r in self.requests if r.method == method and p.match(r.path)]

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
