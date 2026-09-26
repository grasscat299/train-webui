"""`python -m train_webui.backend` で起動するエントリーポイント（リポジトリ直下から実行する）。"""
from __future__ import annotations

import uvicorn

from .app import create_app
from .settings import load_settings


def main() -> None:
    settings = load_settings()
    app = create_app(settings)
    # frontend が数秒ごとに API を取りに来るので、1リクエスト1行のアクセスログは出さない（エラーや起動時のログは出る）
    uvicorn.run(app, host=settings.server.host, port=settings.server.port, access_log=False)


if __name__ == "__main__":
    main()
