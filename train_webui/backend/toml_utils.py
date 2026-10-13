"""config.toml の検証・[wandb].group の読み書き（design.md §3）。"""
from __future__ import annotations

import re

import tomli


class TomlValidationError(ValueError):
    """TOMLとして不正、または [wandb].group が無い／不正な場合。"""


def parse_toml(text: str) -> dict:
    try:
        return tomli.loads(text)
    except tomli.TOMLDecodeError as e:
        raise TomlValidationError(f"TOMLの構文が不正です: {e}") from e


def extract_group(text: str) -> str:
    """[wandb].group を取り出す。無い・空・文字列でなければ TomlValidationError。"""
    data = parse_toml(text)
    wandb = data.get("wandb")
    if not isinstance(wandb, dict) or "group" not in wandb:
        raise TomlValidationError("[wandb].group がありません")
    group = wandb["group"]
    if not isinstance(group, str) or not group:
        raise TomlValidationError("[wandb].group が空、または文字列ではありません")
    return group


_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(#.*)?$")
_GROUP_KEY_RE = re.compile(
    r'^(?P<prefix>\s*group\s*=\s*)'
    r'(?P<value>"(?:[^"\\]|\\.)*"|\'[^\']*\'|[^\s#]+)'
    r'(?P<suffix>\s*(?:#.*)?)$'
)


def set_wandb_group(text: str, new_group: str) -> str:
    """[wandb] セクション内の `group = ...` 行だけを書き換える（他の行・コメントは保持する）。"""
    escaped = '"' + new_group.replace("\\", "\\\\").replace('"', '\\"') + '"'
    in_wandb = False
    found = False
    out_lines: list[str] = []
    for raw_line in text.splitlines(keepends=True):
        stripped = raw_line.splitlines()[0] if raw_line else ""
        ending = raw_line[len(stripped):]
        sec = _SECTION_RE.match(stripped)
        if sec:
            in_wandb = sec.group(1).strip() == "wandb"
            out_lines.append(raw_line)
            continue
        if in_wandb and not found:
            m = _GROUP_KEY_RE.match(stripped)
            if m:
                out_lines.append(f"{m.group('prefix')}{escaped}{m.group('suffix')}{ending}")
                found = True
                continue
        out_lines.append(raw_line)
    if not found:
        raise TomlValidationError("[wandb].group の行が見つかりません")
    return "".join(out_lines)


def set_value(text: str, dotted_key: str, literal: str, add_missing: bool = False) -> str:
    """`section.key`（トップレベルは `key`）の既存の行の値だけを TOML リテラル `literal` に書き換える。

    他の行・コメントは保持する。存在しないキーは追加せず TomlValidationError（タイプミス防止）。
    add_missing=True なら、無いキーは既存の section の見出しの直後に1行足す（推論時の上書き用。
    コード側で後から増えた既定値つきのキーを、古い run の config に足すため）。section が無ければエラー。
    複数行にわたる値の書き換えは非対応（結果が不正なTOMLになるので検証で弾く）。
    """
    section, _, key = dotted_key.rpartition(".")
    if not key:
        raise TomlValidationError(f"キーの指定が不正です: {dotted_key!r}")
    try:
        tomli.loads(f"v = {literal}")
    except tomli.TOMLDecodeError as e:
        raise TomlValidationError(f"値がTOMLのリテラルではありません（文字列は \"...\" で囲む）: {literal!r}") from e
    key_re = re.compile(
        r"^(?P<prefix>\s*" + re.escape(key) + r"\s*=\s*)"
        r'(?:"(?:[^"\\]|\\.)*"|\'[^\']*\'|[^#]*?)'
        r"(?P<suffix>\s*(?:#.*)?)$"
    )
    in_section = section == ""
    found = False
    out_lines: list[str] = []
    for raw_line in text.splitlines(keepends=True):
        stripped = raw_line.splitlines()[0] if raw_line else ""
        ending = raw_line[len(stripped):]
        sec = _SECTION_RE.match(stripped)
        if sec:
            in_section = sec.group(1).strip() == section
            out_lines.append(raw_line)
            continue
        if in_section and not found:
            m = key_re.match(stripped)
            if m:
                out_lines.append(f"{m.group('prefix')}{literal}{m.group('suffix')}{ending}")
                found = True
                continue
        out_lines.append(raw_line)
    if not found and add_missing and section:
        for i, line in enumerate(out_lines):
            sec = _SECTION_RE.match(line.splitlines()[0] if line else "")
            if sec and sec.group(1).strip() == section:
                out_lines.insert(i + 1, f"{key} = {literal}\n")
                found = True
                break
    if not found:
        raise TomlValidationError(f"config に {dotted_key} の行がありません（新しいキーの追加は非対応）")
    result = "".join(out_lines)
    parse_toml(result)
    return result


def extract_init_from(text: str) -> str | None:
    """config の `init_from`（引き継ぎ学習の元の run_id）。どのセクションに書いてあってもよい。無ければ None。"""
    def find(d: dict) -> str | None:
        v = d.get("init_from")
        if isinstance(v, str) and v.strip():
            return v.strip()
        for sub in d.values():
            if isinstance(sub, dict) and (found := find(sub)):
                return found
        return None

    try:
        return find(parse_toml(text))
    except TomlValidationError:
        return None
