"""`python -m train_webui.backend` で起動するエントリーポイント（リポジトリ直下から実行する）。"""
from __future__ import annotations

import uvicorn

from .app import create_app
from .settings import load_settings


def main() -> None:
    settings = load_settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.server.host, port=settings.server.port)


if __name__ == "__main__":
    main()
