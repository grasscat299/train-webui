"""学習用PC操作の失敗を表す例外（CONTRACT.md §3）。"""
from __future__ import annotations


class RemoteError(Exception):
    """ssh/rsync の非0終了・タイムアウトなど、学習用PC操作の失敗時に送出する。"""
