"""命令行：总账凭证 gl-*、基础档案 arc-*、单据列表 list、现存量 stock。"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth, _file_parts, _json_object
from co.client.u8co_client import KIND_NAMES, U8Call, U8CoClient
from co.client.u8co_kinds import NOTE_KINDS  # 票据列表
from co.client.u8co_cli_lock import HANDLERS as LOCK_HANDLERS  # 锁定、解锁
from co.client.u8co_cli_lock import add_commands as add_lock_commands
from co.client.u8co_cli_openings import HANDLERS as OPENINGS_HANDLERS  # 期初记账
from co.client.u8co_cli_openings import add_commands as add_openings_commands
from co.client.u8co_cli_periods import HANDLERS as PERIODS_HANDLERS  # 月末结账
from co.client.u8co_cli_periods import add_commands as add_periods_commands
from co.client.u8co_cli_ia import HANDLERS as IA_HANDLERS  # 存货核算记账、期末处理
from co.client.u8co_cli_ia import add_commands as add_ia_commands
from co.client.u8co_cli_gl_reverse import HANDLERS as GL_REVERSE_HANDLERS  # 红字冲销
from co.client.u8co_cli_gl_reverse import add_commands as add_gl_reverse_commands
from co.client.u8co_cli_gl_transfer import HANDLERS as GL_TRANSFER_HANDLERS  # 期间损益结转、自定义转账
from co.client.u8co_cli_gl_transfer import add_commands as add_gl_transfer_commands
from co.client.u8co_idem import add_key_arg, key_kwargs  # 幂等键
from co.client.u8co_gl_arc import (
    BOOL_FILTERS,
    DELETE_ARCHIVES,
    GL_OPS,
    GL_STATES,
    READ_ARCHIVES,
    UPDATE_ARCHIVES,
    WRITE_ARCHIVES,
    ArcQuery,
    ArcRecord,
    GlDraft,
    GlKey,
    GlQuery,
    StockQuery,
    VoucherQuery,
)

_BOOL_TEXT = {"true": True, "1": True, "false": False, "0": False}
# arc-* 的 --archive 可选值；arc-create 用 WRITE_ARCHIVES。
_ARC_CHOICES = {"arc-get": READ_ARCHIVES, "arc-delete": DELETE_ARCHIVES, "arc-update": UPDATE_ARCHIVES}


def add_commands(sub: Any) -> None:
    _gl_commands(sub)
    _arc_commands(sub)
    _list_commands(sub)
    add_lock_commands(sub)
    add_openings_commands(sub)
    add_periods_commands(sub)
    add_ia_commands(sub)
    add_gl_reverse_commands(sub)
    add_gl_transfer_commands(sub)


def _gl_key_args(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--period", required=True, type=int)
    cmd.add_argument("--sign", required=True)
    cmd.add_argument("--no", required=True, type=int)


def _gl_commands(sub: Any) -> None:
    for name in ["gl-load"] + [f"gl-{op}" for op in GL_OPS]:
        cmd = sub.add_parser(name)
        _add_auth(cmd)
        _gl_key_args(cmd)
    create = sub.add_parser("gl-create")
    _add_auth(create)
    create.add_argument("--file", required=True)
    add_key_arg(create)
    update = sub.add_parser("gl-update")
    _add_auth(update)
    _gl_key_args(update)
    update.add_argument("--file", required=True)
    listing = sub.add_parser("gl-list")
    _add_auth(listing)
    listing.add_argument("--period-from", required=True, type=int)
    listing.add_argument("--period-to", required=True, type=int)
    for flag in ("--sign", "--date-from", "--date-to", "--maker", "--after"):
        listing.add_argument(flag, default="")
    listing.add_argument("--state", choices=GL_STATES, default="")
    listing.add_argument("--limit", type=int)


def _arc_commands(sub: Any) -> None:
    for name, has_file in (("arc-get", False), ("arc-delete", False), ("arc-create", True), ("arc-update", True)):
        cmd = sub.add_parser(name)
        _add_auth(cmd)
        # 只读档案只能 arc-get；开户银行、项目可新增、修改、删除（项目被引用时桥拒绝删除）；
        # 货位、计量单位、自定义项档案、客户存货对照可新增、删除，前两类还能修改。
        cmd.add_argument("--archive", required=True, choices=_ARC_CHOICES.get(name, WRITE_ARCHIVES))
        cmd.add_argument("--code", required=True)
        if has_file:
            cmd.add_argument("--file", required=True)
        if name == "arc-create":
            add_key_arg(cmd)
    listing = sub.add_parser("arc-list")
    _add_auth(listing)
    listing.add_argument("--archive", required=True, choices=READ_ARCHIVES)
    for flag in ("--code-prefix", "--name-like", "--changed-since", "--after", "--project-class", "--currency"):
        listing.add_argument(flag, default="")
    listing.add_argument("--limit", type=int)
    # 只给 exchange_rate：汇率的年度，省略时取登录年份。
    listing.add_argument("--fiscal-year", type=int)
    # 只给 fa_card：资产类别（含下级）、使用部门、是否含已减少的卡片。
    listing.add_argument("--type-code", default="")
    listing.add_argument("--dept-code", default="")
    listing.add_argument("--include-disposed", action="store_true")
    # 每项只返回 code、ufts。
    listing.add_argument("--keys-only", action="store_true")


def _list_commands(sub: Any) -> None:
    listing = sub.add_parser("list")
    _add_auth(listing)
    # 另收票据 ar_note / ap_note（只读列表）。
    listing.add_argument("--type", required=True, choices=KIND_NAMES + NOTE_KINDS)
    listing.add_argument("--filter", action="append", default=[], metavar="KEY=VALUE")
    listing.add_argument("--keys-only", action="store_true")
    listing.add_argument("--changed-since", default="")
    listing.add_argument("--after")
    listing.add_argument("--limit", type=int)
    stock = sub.add_parser("stock")
    _add_auth(stock)
    for flag in ("--wh", "--inv", "--batch"):
        stock.add_argument(flag, default="")
    stock.add_argument("--after")
    stock.add_argument("--limit", type=int)


def gl_key(parsed: argparse.Namespace) -> GlKey:
    return GlKey(parsed.period, parsed.sign, parsed.no)


def gl_draft(path: str) -> GlDraft:
    head, lines = _file_parts(path)
    return GlDraft(head, lines)


def arc_record(parsed: argparse.Namespace, creating: bool) -> ArcRecord:
    data = _json_object(parsed.file)
    allowed = {"fields", "template"} if creating else {"fields"}
    if "fields" not in data or not set(data) <= allowed:
        hint = "fields，可选 template" if creating else "fields"
        raise SystemExit(f"档案文件只能包含 {hint}")
    template = data.get("template", "")
    if not isinstance(template, str):
        raise SystemExit("template 必须是档案编码字符串")
    return ArcRecord(parsed.archive, parsed.code, data["fields"], template)


def parse_filters(items: list[str]) -> dict[str, Any] | None:
    clean: dict[str, Any] = {}
    for item in items:
        key, sep, value = item.partition("=")
        key = key.strip()
        if not sep or not key:
            raise SystemExit("--filter 要写成 KEY=VALUE")
        if key in clean:
            raise SystemExit(f"过滤条件 {key} 重复")
        clean[key] = _filter_value(key, value)
    return clean or None


def _filter_value(key: str, value: str) -> object:
    if key not in BOOL_FILTERS:
        return value
    flag = _BOOL_TEXT.get(value.strip().lower())
    if flag is None:
        raise SystemExit(f"过滤条件 {key} 只能是 true 或 false")
    return flag


def keyset_after(text: str | None) -> int | None:
    # 单据列表和现存量按整数主键翻页。
    if text is None:
        return None
    if not (text.isascii() and text.isdecimal()):
        raise SystemExit("--after 必须是上一页返回的整数 next")
    return int(text)


def _cmd_gl_key(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    if parsed.command == "gl-load":
        return client.gl_load(call, gl_key(parsed))
    return client.gl_op(call, parsed.command[len("gl-") :], gl_key(parsed))


def _cmd_gl_create(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.gl_create(call, gl_draft(parsed.file), **key_kwargs(parsed))


def _cmd_gl_update(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.gl_update(call, gl_key(parsed), gl_draft(parsed.file))


def _cmd_gl_list(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    query = GlQuery(
        parsed.period_from, parsed.period_to, parsed.sign, parsed.date_from, parsed.date_to,
        parsed.maker, parsed.state, parsed.after, parsed.limit,
    )
    return client.gl_list(call, query)


def _cmd_arc(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    if parsed.command == "arc-get":
        return client.arc_get(call, parsed.archive, parsed.code)
    if parsed.command == "arc-delete":
        return client.arc_delete(call, parsed.archive, parsed.code)
    if parsed.command == "arc-create":
        return client.arc_create(call, arc_record(parsed, True), **key_kwargs(parsed))
    return client.arc_update(call, arc_record(parsed, False))


def _cmd_arc_list(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    query = ArcQuery(
        parsed.archive,
        parsed.code_prefix,
        parsed.name_like,
        parsed.changed_since,
        parsed.after,
        parsed.limit,
        parsed.project_class,
        parsed.currency,
        parsed.fiscal_year,
        parsed.type_code,
        parsed.dept_code,
        True if parsed.include_disposed else None,
        keys_only=parsed.keys_only,
    )
    return client.arc_list(call, query)


def _cmd_list(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    query = VoucherQuery(
        parsed.type, parse_filters(parsed.filter), parsed.keys_only, parsed.changed_since,
        keyset_after(parsed.after), parsed.limit,
    )
    return client.list_vouchers(call, query)


def _cmd_stock(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    query = StockQuery(parsed.wh, parsed.inv, parsed.batch, keyset_after(parsed.after), parsed.limit)
    return client.stock_current(call, query)


HANDLERS = {
    "gl-load": _cmd_gl_key,
    "gl-create": _cmd_gl_create,
    "gl-update": _cmd_gl_update,
    "gl-list": _cmd_gl_list,
    "arc-get": _cmd_arc,
    "arc-delete": _cmd_arc,
    "arc-create": _cmd_arc,
    "arc-update": _cmd_arc,
    "arc-list": _cmd_arc_list,
    "list": _cmd_list,
    "stock": _cmd_stock,
}
HANDLERS.update({f"gl-{op}": _cmd_gl_key for op in GL_OPS})
HANDLERS.update(LOCK_HANDLERS)
HANDLERS.update(OPENINGS_HANDLERS)
HANDLERS.update(PERIODS_HANDLERS)
HANDLERS.update(IA_HANDLERS)
HANDLERS.update(GL_REVERSE_HANDLERS)
HANDLERS.update(GL_TRANSFER_HANDLERS)
