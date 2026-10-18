"""学習・推論を CLI から操作するコマンド（Claude が config を編集して実行する用）。

読み取り専用の `cli.py` とは別にしてある（こちらは server の HTTP API を叩いて状態を変える）。
server が起動している必要がある。リポジトリ直下から実行する:

    venv/bin/python -m train_webui.ctl draft <project> [--group G | --new-group] [--name N] [--continue-from RUN_ID | --rerun-of RUN_ID]
    venv/bin/python -m train_webui.ctl set <run_id> [--add] section.key=<TOMLリテラル> [...]
    venv/bin/python -m train_webui.ctl describe <project> <group> (--file パス | --text 文章 | 標準入力)
    venv/bin/python -m train_webui.ctl comment <run_id> <text>
    venv/bin/python -m train_webui.ctl machines
    venv/bin/python -m train_webui.ctl start <run_id> [--machine NAME]
    venv/bin/python -m train_webui.ctl infer <project> [<group> | --run RUN_ID] [--machine NAME] [--set section.key=<TOMLリテラル> ...]
    venv/bin/python -m train_webui.ctl stop <run_id> [--graceful]

- `describe` は group（=モデル構成）の説明（Markdown）を上書きする。group は group_id でも表示名でもよい。
  モデルの作りを Claude が後から読めるようにするためのもの。新しいモデルを作ったら書く。
- `draft` は新しい下書き run を作って run_id を出す（config は WebUI の「新規 run」と同じ作り方）。
- `set` は draft の config の既存キーの値だけを書き換える（`train.lr=3e-4` `train.name="a"` `train.use_x=true`）。`--add` を付けると無いキーを section の中に足す。
  値は TOML のリテラルなので、文字列は引用符で囲む。
- `infer --set` はその推論ジョブに渡す config だけを書き換える（run に保存された config は変えない）。
  無いキーは section の中に足す（例: `--set 'infer.mode="eval"' --set infer.eval_n=200`）。
- `machines` は学習用PC（複数台）ごとの使用状況・今起動してよいか（だめな理由つき）・自動選択される台を出す。
- `start` は台を自動で選ぶ（他の人が使っていない台・空きを残すルール・5090 優先）。`--machine NAME` で指定もできるが、
  ルールは同じく適用され、使えない台は断られる。引き継ぎ学習は元の run の台に固定される。
- `infer` は、その run を学習した台が使えればそこで動かす。使えない（自分の別のジョブ・他の人の使用・空きの決まり）ときは、
  使える別の台に重みを自動で送って動かす。`--machine NAME` で台を指定でもできる（元の台と違えば重みを送る）。
- `start` / `infer` は GPU が使用中なら server が断る。
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
import urllib.error
import urllib.parse
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
    machine = f"  @{run['machine']}" if run.get("machine") else ""
    print(f"run_id: {run['run_id']}  {run['project']} / {run['group']} / {run['display_name']}  [{run['status']}]{machine}")


def cmd_draft(api: Api, args: argparse.Namespace) -> None:
    body = {"project": args.project, "group": args.group, "new_group": args.new_group, "group_name": args.name,
            "parent_run_id": args.continue_from, "rerun_of": args.rerun_of}
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
            text = set_value(text, key.strip(), literal.strip(), add_missing=getattr(args, "add", False))
        except TomlValidationError as e:
            raise CtlError(str(e)) from e
    updated = api.call("PUT", f"/api/runs/{args.run_id}/config", {"config_toml": text})
    _show_run(updated)
    diff = "".join(difflib.unified_diff(old.splitlines(True), text.splitlines(True), "before", "after", n=0))
    print(diff if diff else "変更なし")


def cmd_describe(api: Api, args: argparse.Namespace) -> None:
    if args.text is not None:
        text = args.text
    elif args.file:
        text = open(args.file, encoding="utf-8").read()
    else:
        text = sys.stdin.read()
    tree = api.call("GET", "/api/tree")
    project = next((n for n in tree if n["project"] == args.project or n["project"].endswith("/" + args.project)), None)
    if project is None:
        raise CtlError(f"project が見つかりません: {args.project}（候補: {', '.join(n['project'] for n in tree)}）")
    hits = [g for g in project["groups"] if args.group in (g["group"], g["group_name"])]
    if len(hits) != 1:
        candidates = ", ".join("%s(%s)" % (g["group_name"], g["group"]) for g in project["groups"])
        raise CtlError(f"group が{'見つかりません' if not hits else '複数あります'}: {args.group}（候補: {candidates}）")
    api.call("PUT", "/api/groups/description",
             {"project": project["project"], "group": hits[0]["group"], "description": text})
    print(f"{project['project']} / {hits[0]['group_name']} の説明を保存しました（{len(text)}文字）")


def cmd_comment(api: Api, args: argparse.Namespace) -> None:
    _show_run(api.call("PUT", f"/api/runs/{args.run_id}/comment", {"comment": args.text}))


def cmd_start(api: Api, args: argparse.Namespace) -> None:
    query = f"?machine={urllib.parse.quote(args.machine)}" if args.machine else ""
    _show_run(api.call("POST", f"/api/runs/{args.run_id}/start{query}"))


def cmd_machines(api: Api, args: argparse.Namespace) -> None:
    data = api.call("GET", "/api/machines?fresh=true")
    for m in data["machines"]:
        state = "OK  " if m["available"] else "不可"
        use = "接続不可" if not m["reachable"] else f"他の人 {m['other_mib']} MiB / 自分のジョブ {m['own_jobs']}"
        print(f"{state} {m['name']:<8} priority={m['priority']:<4} {use}" + (f"  … {m['reason']}" if m["reason"] else ""))
    print(f"自動選択される台: {data['selected'] or 'なし（今は起動できません）'}")


def cmd_stop(api: Api, args: argparse.Namespace) -> None:
    suffix = "?graceful=true" if args.graceful else ""
    _show_run(api.call("POST", f"/api/runs/{args.run_id}/stop{suffix}"))


def cmd_infer(api: Api, args: argparse.Namespace) -> None:
    if not args.group and not args.run:
        raise CtlError("group か --run のどちらかを指定してください")
    job = api.call("POST", "/api/groups/infer",
                   {"project": args.project, "group": args.group, "run_id": args.run, "overrides": args.overrides,
                    "machine": args.machine})
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
    s.add_argument("--continue-from", metavar="RUN_ID", help="この run の重みから続きを学習する下書き（同じ group・同じ config から）")
    s.add_argument("--rerun-of", metavar="RUN_ID", help="この run を起動した時点のコードで再実行する下書き（同じ group・同じ config から）")
    s.set_defaults(func=cmd_draft)

    s = sub.add_parser("set", help="draft の config のキーを書き換える")
    s.add_argument("run_id")
    s.add_argument("assignments", nargs="+", metavar="section.key=値")
    s.add_argument("--add", action="store_true", help="config に無いキーを section の中に足す（既定は既存のキーだけ。タイプミスの防止）")
    s.set_defaults(func=cmd_set)

    s = sub.add_parser("describe", help="group（モデル構成）の説明（Markdown）を書く")
    s.add_argument("project")
    s.add_argument("group")
    s.add_argument("--file", help="説明を書いたファイル")
    s.add_argument("--text", help="説明の文章（省略時は --file、それも無ければ標準入力）")
    s.set_defaults(func=cmd_describe)

    s = sub.add_parser("comment", help="run にコメントを書く（上書き）")
    s.add_argument("run_id")
    s.add_argument("text")
    s.set_defaults(func=cmd_comment)

    s = sub.add_parser("machines", help="学習用PCごとの使用状況と、今起動できるかを表示する")
    s.set_defaults(func=cmd_machines)

    s = sub.add_parser("start", help="draft の学習を開始する")
    s.add_argument("run_id")
    s.add_argument("--machine", help="動かす台の名前（省略時は自動選択。`machines` で確認）")
    s.set_defaults(func=cmd_start)

    s = sub.add_parser("stop", help="実行中の run を止める")
    s.add_argument("run_id")
    s.add_argument("--graceful", action="store_true",
                   help="重みを保存してから止める（学習が次の区切りで保存して終了する。すぐには止まらない）")
    s.set_defaults(func=cmd_stop)

    s = sub.add_parser("infer", help="group の最新の学習済み run、または --run で指定した run の重みで推論する")
    s.add_argument("project")
    s.add_argument("group", nargs="?", help="group_id。省略して --run だけでもよい")
    s.add_argument("--run", metavar="RUN_ID", help="この run の重みで推論する（重みが登録された、学習の終わった run だけ）")
    s.add_argument("--set", dest="overrides", action="append", default=[], metavar="section.key=値",
                   help="この推論だけ config を上書き（run の config は変えない。無いキーは section に足す）")
    s.add_argument("--machine", help="推論を動かす台（省略時は、その run を学習した台。使えなければ使える別の台に重みを送って動かす）")
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
