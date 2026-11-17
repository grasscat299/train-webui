"""送れなかった結果（client が <run_root>/<run_id>/unsent/*.jsonl に残した分）を server へ取り込む。

使い方（hub で。server が動いていること）:
    venv/bin/python -m train_webui.client.replay --ssh gpu1 --run-id 20261117_045630   # 学習用PCのを取り込む
    venv/bin/python -m train_webui.client.replay path/to/file.jsonl ...                # 手元のファイル

各行は {"run_id", "source", "items": [...]}（POST /api/results の本文そのもの）。取り込めたファイルは
名前の末尾に .done を付ける（二重に取り込まないため）。途中で失敗したら、そのファイルは .done にしない。
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from ._core import UNSENT_DIRNAME, _coalesce, _http_request

DEFAULT_URL = os.environ.get("TRAIN_WEBUI_URL", "http://127.0.0.1:8000")
DEFAULT_RUN_ROOT = "/home/user/train_webui/td_plan/runs"
CHUNK = 20000  # 1回の POST に入れる件数（まとめる前）


def post_lines(lines: list[str], url: str) -> int:
    """JSONL の各行を送る。送った件数を返す。失敗したら例外。"""
    sent = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        items = rec["items"]
        for i in range(0, len(items), CHUNK):
            chunk = _coalesce(items[i:i + CHUNK])
            _http_request("POST", url.rstrip("/") + "/api/results",
                          {"run_id": rec["run_id"], "source": rec["source"], "items": chunk})
            sent += len(items[i:i + CHUNK])
    return sent


def _ssh(host: str, cmd: str) -> str:
    proc = subprocess.run(["ssh", host, cmd], capture_output=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"ssh {host} に失敗: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout.decode("utf-8")


def replay_remote(host: str, run_id: str, run_root: str, url: str) -> int:
    d = f"{run_root}/{run_id}/{UNSENT_DIRNAME}"
    listing = _ssh(host, f"ls -1 {shlex.quote(d)}/*.jsonl 2>/dev/null; true").split()
    if not listing:
        print(f"{host}:{d} に未取り込みのファイルはありません")
        return 0
    total = 0
    for path in listing:
        n = post_lines(_ssh(host, f"cat {shlex.quote(path)}").splitlines(), url)
        _ssh(host, f"mv {shlex.quote(path)} {shlex.quote(path + '.done')}")
        print(f"{host}:{path}: {n}件を取り込みました")
        total += n
    return total


def replay_local(paths: list[str], url: str) -> int:
    total = 0
    for p in map(Path, paths):
        n = post_lines(p.read_text(encoding="utf-8").splitlines(), url)
        p.rename(p.with_name(p.name + ".done"))
        print(f"{p}: {n}件を取り込みました")
        total += n
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="手元の unsent の .jsonl")
    ap.add_argument("--ssh", help="学習用PCの ssh 名（gpu1 など）。--run-id と一緒に使う")
    ap.add_argument("--run-id")
    ap.add_argument("--run-root", default=DEFAULT_RUN_ROOT)
    ap.add_argument("--url", default=DEFAULT_URL)
    a = ap.parse_args(argv)
    if a.ssh:
        if not a.run_id:
            ap.error("--ssh には --run-id が要ります")
        total = replay_remote(a.ssh, a.run_id, a.run_root, a.url)
    elif a.files:
        total = replay_local(a.files, a.url)
    else:
        ap.error("ファイルか --ssh/--run-id を指定してください")
    print(f"計 {total} 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
