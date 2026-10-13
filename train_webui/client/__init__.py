"""train_webui client 公開API（wandbのSDKに近い書き味）。標準ライブラリのみで実装する（design.md §5）。"""
from __future__ import annotations

import inspect

from . import _core

__all__ = ["init", "log", "log_image", "log_table", "define_plot", "log_xy", "Plot", "finish",
           "artifact_dir", "checkpoint_path", "init_checkpoint", "start_checkpoint", "log_artifact"]


def init(new_run: bool = False, run_id: str | None = None, source: str | None = None) -> None:
    """送信先runを決める（design.md §5.1）。呼び出し元ファイルの位置からproject検出に使う。"""
    caller_file = inspect.stack()[1].filename
    _core.init(new_run=new_run, run_id=run_id, source=source, caller_file=caller_file)


def log(data: dict, step: int | None = None) -> None:
    """数値系列を送る。キューに積むだけで即returnする。"""
    _core.log(data, step)


def log_table(table: str, data: dict) -> None:
    """表に1つずつの値を送る。data は {指標名: 値}。同じ指標名を再送すると新しい値が表に出る。"""
    _core.log_table(table, data)


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


def artifact_dir():
    """このrunの成果物（重みなど）の保存先 Path。WebUIから起動されていなければ None。"""
    return _core.artifact_dir()


def checkpoint_path(default, name: str = "model.pt") -> str:
    """重みの保存先。WebUIから起動されたら run ごとの専用ディレクトリ、手元で動かしたら default を返す。"""
    return _core.checkpoint_path(default, name)


def init_checkpoint(name: str = "model.pt") -> str | None:
    """引き継ぎ学習（WebUIで「この run から続きを学習」した run）のとき、元の run の重みのパス。それ以外は None。"""
    return _core.init_checkpoint(name)


def start_checkpoint(save_path) -> str | None:
    """学習の初期値にする重み。WebUIから起動なら引き継ぎ元の run の重み（無ければ None）、手元起動なら save_path があればそれ。"""
    return _core.start_checkpoint(save_path)


def log_artifact(path, kind: str = "checkpoint", name: str | None = None) -> None:
    """保存した重みなどの場所をWebUIに登録する（本体は送らない）。init の後に呼ぶ。"""
    _core.log_artifact(path, kind, name)


def finish() -> None:
    """送信キューを吐き出してrunを終了する。"""
    _core.finish()
