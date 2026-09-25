"""server.toml の接続設定で、学習用PC側に無害なコマンド（pwd）だけを実行して確認する。

    venv/bin/python -m train_webui.backend.check_remote [--config path/to/server.toml]

rsync・config書き込み・ジョブ起動は一切行わない。実行する ssh コマンドを表示してから実行する。
"""
from __future__ import annotations

import argparse
import shlex
from pathlib import Path

from .remote import RemoteController
from .settings import DEFAULT_PATH, load_settings


def _run(controller: RemoteController, remote_command: str) -> bool:
    args = controller._ssh_base_args() + [remote_command]
    print(f"$ {shlex.join(args)}")
    proc = controller._run(args, timeout=30)
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    err = proc.stderr.decode("utf-8", errors="replace").strip()
    print(f"  終了コード: {proc.returncode}")
    if out:
        print(f"  stdout: {out}")
    if err:
        print(f"  stderr: {err}")
    print()
    return proc.returncode == 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=DEFAULT_PATH)
    args = p.parse_args()

    settings = load_settings(args.config)
    r = settings.remote
    controller = RemoteController(settings)
    print(f"接続先: {r.user}@{r.host}:{r.port}（鍵: {r.key_path}）\n")

    results = [
        ("接続してログイン直後のディレクトリを表示", _run(controller, "pwd")),
        ("repo_dir に移動できるか", _run(controller, f"cd {shlex.quote(r.repo_dir)} && pwd")),
        ("run_root に移動できるか", _run(controller, f"cd {shlex.quote(r.run_root)} && pwd")),
    ]
    for name, ok in results:
        print(f"{'OK' if ok else 'NG'}  {name}")


if __name__ == "__main__":
    main()
