"""学習・推論を CLI から操作するコマンド（Claude が config を編集して実行する用）。

読み取り専用の `cli.py` とは別にしてある（こちらは server の HTTP API を叩いて状態を変える）。
server が起動している必要がある。リポジトリ直下から実行する:

    venv/bin/python -m train_webui.ctl draft <project> [--group G | --new-group] [--name N]
    venv/bin/python -m train_webui.ctl set <run_id> section.key=<TOMLリテラル> [...]
    venv/bin/python -m train_webui.ctl comment <run_id> <text>
    venv/bin/python -m train_webui.ctl start <run_id>
    venv/bin/python -m train_webui.ctl infer <project> <group>
    venv/bin/python -m train_webui.ctl stop <run_id>

- `draft` は新しい下書き run を作って run_id を出す（config は WebUI の「新規 run」と同じ作り方）。
- `set` は draft の config の既存キーの値だけを書き換える（`train.lr=3e-4` `train.name="a"` `train.use_x=true`）。
  値は TOML のリテラルなので、文字列は引用符で囲む。
- `start` / `infer` は GPU が使用中なら server が断る。
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
import urllib.error
import urllib.request

from .backend.settings import DEFAULT_PATH, load_settings
from .backend.toml_utils import TomlValidationError, set_value


class CtlError(Exception):
    pass


class Api:
    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")

    def call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                raw = res.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except ValueError:
                pass
            raise CtlError(f"{e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise CtlError(f"server に接続できません（{self.base}）。WebUI の server は起動していますか: {e.reason}") from e
        return json.loads(raw) if raw else None


def _show_run(run: dict) -> None:
    print(f"run_id: {run['run_id']}  {run['project']} / {run['group']} / {run['display_name']}  [{run['status']}]")


def cmd_draft(api: Api, args: argparse.Namespace) -> None:
    body = {"project": args.project, "group": args.group, "new_group": args.new_group, "group_name": args.name}
    run = api.call("POST", "/api/runs/draft", body)
    _show_run(run)
    print("\n" + run["config_toml"])


def cmd_set(api: Api, args: argparse.Namespace) -> None:
    run = api.call("GET", f"/api/runs/{args.run_id}")
    if run["status"] != "draft":
        raise CtlError(f"draft 以外の run の config は変えられません（status={run['status']}）。draft を作り直してください")
    old = text = run["config_toml"]
    for assignment in args.assignments:
        key, eq, literal = assignment.partition("=")
        if not eq:
            raise CtlError(f"`section.key=値` の形で指定してください: {assignment!r}")
        try:
            text = set_value(text, key.strip(), literal.strip())
        except TomlValidationError as e:
            raise CtlError(str(e)) from e
    updated = api.call("PUT", f"/api/runs/{args.run_id}/config", {"config_toml": text})
    _show_run(updated)
    diff = "".join(difflib.unified_diff(old.splitlines(True), text.splitlines(True), "before", "after", n=0))
    print(diff if diff else "変更なし")


def cmd_comment(api: Api, args: argparse.Namespace) -> None:
    _show_run(api.call("PUT", f"/api/runs/{args.run_id}/comment", {"comment": args.text}))


def cmd_start(api: Api, args: argparse.Namespace) -> None:
    _show_run(api.call("POST", f"/api/runs/{args.run_id}/start"))


def cmd_stop(api: Api, args: argparse.Namespace) -> None:
    _show_run(api.call("POST", f"/api/runs/{args.run_id}/stop"))


def cmd_infer(api: Api, args: argparse.Namespace) -> None:
    job = api.call("POST", "/api/groups/infer", {"project": args.project, "group": args.group})
    print(json.dumps(job, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m train_webui.ctl", description=__doc__.split("\n")[0])
    p.add_argument("--url", help="server の URL（省略時は server.toml の port から http://127.0.0.1:<port>）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("draft", help="下書き run を作る")
    s.add_argument("project", help="project のパス（`ls` に出る形。例: scratch/PolylineInterp）")
    s.add_argument("--group", help="既存 group に追加する時の group_id")
    s.add_argument("--new-group", action="store_true", help="新しい group を作る")
    s.add_argument("--name", help="新しい group の表示名")
    s.set_defaults(func=cmd_draft)

    s = sub.add_parser("set", help="draft の config のキーを書き換える")
    s.add_argument("run_id")
    s.add_argument("assignments", nargs="+", metavar="section.key=値")
    s.set_defaults(func=cmd_set)

    s = sub.add_parser("comment", help="run にコメントを書く（上書き）")
    s.add_argument("run_id")
    s.add_argument("text")
    s.set_defaults(func=cmd_comment)

    s = sub.add_parser("start", help="draft の学習を開始する")
    s.add_argument("run_id")
    s.set_defaults(func=cmd_start)

    s = sub.add_parser("stop", help="実行中の run を止める")
    s.add_argument("run_id")
    s.set_defaults(func=cmd_stop)

    s = sub.add_parser("infer", help="group の最新の学習済み run で推論する")
    s.add_argument("project")
    s.add_argument("group")
    s.set_defaults(func=cmd_infer)

    args = p.parse_args(argv)
    try:
        url = args.url or f"http://127.0.0.1:{load_settings(DEFAULT_PATH).server.port}"
        args.func(Api(url), args)
    except CtlError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
