"""train_webui client 公開API（wandbのSDKに近い書き味）。標準ライブラリのみで実装する（design.md §5）。"""
from __future__ import annotations

import inspect

from . import _core

__all__ = ["init", "log", "log_image", "finish"]


def init(new_run: bool = False, run_id: str | None = None, source: str | None = None) -> None:
    """送信先runを決める（design.md §5.1）。呼び出し元ファイルの位置からproject検出に使う。"""
    caller_file = inspect.stack()[1].filename
    _core.init(new_run=new_run, run_id=run_id, source=source, caller_file=caller_file)


def log(data: dict, step: int | None = None) -> None:
    """数値系列を送る。キューに積むだけで即returnする。"""
    _core.log(data, step)


def log_image(label: str, fig_or_path, step: int | None = None) -> None:
    """画像を送る。matplotlibのFigure（savefigを持つオブジェクト）か画像ファイルパスを受け付ける。"""
    _core.log_image(label, fig_or_path, step)


def finish() -> None:
    """送信キューを吐き出してrunを終了する。"""
    _core.finish()
