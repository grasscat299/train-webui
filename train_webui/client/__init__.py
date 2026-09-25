"""train_webui client 公開API（wandbのSDKに近い書き味）。標準ライブラリのみで実装する（design.md §5）。"""
from __future__ import annotations

import inspect

from . import _core

__all__ = ["init", "log", "log_image", "define_plot", "log_xy", "Plot", "finish"]


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


class Plot:
    """define_plot が返すハンドル。label を持つだけで、add は log_xy を呼ぶ（design.md §5.3）。"""

    def __init__(self, label: str) -> None:
        self.label = label

    def add(self, x, y, series: str = "default", step: int | None = None) -> None:
        log_xy(self.label, x, y, series=series, step=step)


def define_plot(
    label: str, kind: str = "line", title: str | None = None, xlabel: str = "", ylabel: str = "",
    xscale: str = "linear", yscale: str = "linear", xlim=None, ylim=None,
) -> Plot:
    """軸つきグラフの軸を定義する。同じラベルで再度呼ぶと定義を上書きする（点は消えない）。"""
    _core.define_plot(label, kind, title, xlabel, ylabel, xscale, yscale, xlim, ylim)
    return Plot(label)


def log_xy(label: str, x, y, series: str = "default", step: int | None = None) -> None:
    """軸つきグラフに点を追加する。x, y はスカラー同士か同じ長さの配列同士。"""
    _core.log_xy(label, x, y, series, step)


def finish() -> None:
    """送信キューを吐き出してrunを終了する。"""
    _core.finish()
