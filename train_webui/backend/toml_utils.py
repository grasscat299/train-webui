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
