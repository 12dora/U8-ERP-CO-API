"""命令行：字段标签 meta-fields、单据搜索 search、批量读取 load-many / arc-get-many。由 u8co_cli_assist 并入。

meta-fields (--type sale_order [--op create|update|generate] [--source <来源类型>] | --archive customer | --gl)
search --type sale_order [--code-like X] [--partner C900001] [--date-from 2026-09-01] [--verified true] [--after N] [--limit 50]
       [--define define1=HT202601001] [--define-like define10=框架] [--define-prefix define2=HT2026]
       （表头文本自定义项，可重复，最多 4 个键）
load-many --type sale_order --ids 101,102
arc-get-many --archive customer --codes C900001,C900002
"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth
from co.client.u8co_client import KIND_NAMES, U8Call, U8CoClient
from co.client.u8co_gl_arc import READ_ARCHIVES
from co.client.u8co_lookup import FIELD_OPS, SEARCH_TEXT, FieldsTarget, SearchQuery

_BOOL_TEXT = {"true": True, "1": True, "false": False, "0": False}


def add_commands(sub: Any) -> None:
    fields = sub.add_parser("meta-fields")
    _add_auth(fields)
    target = fields.add_mutually_exclusive_group(required=True)
    target.add_argument("--type", choices=KIND_NAMES)
    target.add_argument("--archive", choices=READ_ARCHIVES)
    target.add_argument("--gl", action="store_true")
    fields.add_argument("--op", choices=FIELD_OPS, default="")
    fields.add_argument("--source", choices=KIND_NAMES, default="")
    _search_command(sub)
    many = sub.add_parser("load-many")
    _add_auth(many)
    many.add_argument("--type", required=True, choices=KIND_NAMES)
    many.add_argument("--ids", required=True, help="逗号分隔的单据 id，1 到 20 个")
    codes = sub.add_parser("arc-get-many")
    _add_auth(codes)
    codes.add_argument("--archive", required=True, choices=READ_ARCHIVES)
    codes.add_argument("--codes", required=True, help="逗号分隔的档案编码，1 到 20 个")


def _search_command(sub: Any) -> None:
    search = sub.add_parser("search")
    _add_auth(search)
    search.add_argument("--type", required=True, choices=KIND_NAMES)
    for name in ("code_like", *SEARCH_TEXT, "date_from", "date_to"):
        search.add_argument("--" + name.replace("_", "-"), default="")
    search.add_argument("--verified", choices=tuple(_BOOL_TEXT))
    search.add_argument("--closed", choices=tuple(_BOOL_TEXT))
    search.add_argument("--after", type=int)
    search.add_argument("--limit", type=int)
    for op, help_text in (("", "表头自定义项等于"), ("like", "表头自定义项包含"), ("prefix", "表头自定义项开头是")):
        flag = "--define" + (f"-{op}" if op else "")
        search.add_argument(flag, action="append", default=[], metavar="defineN=值", help=help_text + "（可重复）")


def parse_ids(text: str) -> list[int]:
    parts = [item.strip() for item in text.split(",")]
    if not parts or not all(part.isascii() and part.isdecimal() for part in parts):
        raise SystemExit("--ids 必须是逗号分隔的正整数")
    return [int(part) for part in parts]


def parse_codes(text: str) -> list[str]:
    parts = [item.strip() for item in text.split(",")]
    if not all(parts):
        raise SystemExit("--codes 不能有空项")
    return parts


def _flag(text: str | None) -> bool | None:
    return None if text is None else _BOOL_TEXT[text]


def parse_defines(parsed: argparse.Namespace) -> dict[str, Any] | None:
    """--define / --define-like / --define-prefix defineN=值 → defines；一个都没给返回 None。同一个键只能给一次。"""
    defines: dict[str, Any] = {}
    for op in ("eq", "like", "prefix"):
        for item in getattr(parsed, "define" if op == "eq" else f"define_{op}", None) or []:
            key, sep, value = item.partition("=")
            key = key.strip()
            if not sep or not key:
                raise SystemExit("--define 的写法是 defineN=值")
            if key in defines:
                raise SystemExit(f"自定义项 {key} 只能给一次")
            defines[key] = value if op == "eq" else {op: value}
    return defines or None


def search_query(parsed: argparse.Namespace) -> SearchQuery:
    texts = {name: getattr(parsed, name) for name in ("code_like", *SEARCH_TEXT, "date_from", "date_to")}
    return SearchQuery(
        parsed.type, **texts, verified=_flag(parsed.verified), closed=_flag(parsed.closed),
        after=parsed.after, limit=parsed.limit, defines=parse_defines(parsed),
    )


def _cmd_fields(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    target = FieldsTarget(parsed.type or "", parsed.archive or "", parsed.gl, parsed.op, parsed.source)
    return client.meta_fields(call, target)


def _cmd_search(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.search(call, search_query(parsed))


def _cmd_load_many(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.load_many(call, parsed.type, parse_ids(parsed.ids))


def _cmd_get_many(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.get_many(call, parsed.archive, parse_codes(parsed.codes))


HANDLERS = {
    "meta-fields": _cmd_fields,
    "search": _cmd_search,
    "load-many": _cmd_load_many,
    "arc-get-many": _cmd_get_many,
}
