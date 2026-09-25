"""server.toml の接続設定で学習用PCに接続し、無害なコマンドだけで接続と環境を確認する。

    venv/bin/python -m train_webui.backend.check_remote [--config path/to/server.toml]

1. pwd と repo_dir / run_root への cd で接続とパスを確認する
2. check_env.sh を ssh の標準入力で送り、パッケージ・GPU・sshd/tailscale・server への到達性を確認する
rsync・config書き込み・ジョブ起動は一切行わない。実行する ssh コマンドを表示してから実行する。
"""
from __future__ import annotations

import argparse
import shlex
from pathlib import Path

from .remote import RemoteController
from .settings import DEFAULT_PATH, load_settings

_CHECK_ENV_SCRIPT = Path(__file__).with_name("check_env.sh")


def _run(controller: RemoteController, remote_command: str, input: bytes | None = None,
         show_stdout_raw: bool = False) -> bool:
    args = controller._ssh_base_args() + [remote_command]
    print(f"$ {shlex.join(args)}" + ("  < " + str(_CHECK_ENV_SCRIPT.name) if input is not None else ""))
    proc = controller._run(args, input=input, timeout=120)
    out = proc.stdout.decode("utf-8", errors="replace").rstrip()
    err = proc.stderr.decode("utf-8", errors="replace").strip()
    if show_stdout_raw:
        print(out)
    else:
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

    if not results[0][1]:
        print("\n接続できないため、環境の確認は行いません。")
        return
    print("\n==================== 学習用PCの環境確認（check_env.sh） ====================\n")
    # 学習の起動と同じく repo_dir に移動してから実行する（python を相対パスで書いた場合も同じ解決になるように）
    remote_cmd = (f"cd {shlex.quote(r.repo_dir)} 2>/dev/null; "
                  f"bash -s -- {shlex.quote(settings.server.public_url)} {shlex.quote(r.python)}")
    _run(controller, remote_cmd, input=_CHECK_ENV_SCRIPT.read_bytes(), show_stdout_raw=True)
    print("WebUI server に届くかの確認は、WSL で server（python -m train_webui.backend）を起動した状態で行ってください。")


if __name__ == "__main__":
    main()
