"""u8co 命令行。口令只从终端读取，不能写进参数。"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import re
import sys
from typing import Any

from co.client.u8co_client import (
    CLOSABLE_KINDS,
    CREATABLE_KINDS,
    DELETABLE_KINDS,
    GENERATABLE_KINDS,
    KIND_NAMES,
    U8Call,
    U8CoClient,
    U8CoError,
    UPDATABLE_KINDS,
    VERIFIABLE_KINDS,
    VoucherDraft,
    VoucherEdit,
    VoucherGen,
    WORKFLOW_KINDS,
    derive_keys,
)
from co.client.u8co_idem import add_key_arg, key_kwargs  # 幂等键
from co.client.u8co_kinds import SOURCE_TYPES, whole_generate
from co.client.u8co_settings import load_config, resolve_base_url, resolve_secret

_ACC = re.compile(r"^\d{3}\Z")
_YEAR = re.compile(r"^\d{4}\Z")
_FILE_LIMIT = 65536
# arap_verify / arap_unverify 只对采购发票、销售发票有效，客户端库再按类型校验。
_ACTIONS = ("verify", "unverify", "arap_verify", "arap_unverify")
_BASE_HELP = "桥地址，例如 http://192.0.2.10:18089/u8co；缺省取 U8CO_BASE_URL 或客户端配置"


def _reject_password_arg(argv: list[str]) -> None:
    for item in argv:
        if item == "--password" or item.startswith("--password="):
            raise SystemExit("口令不能放在命令行参数里")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="u8co_client", description="调用 U8 服务器上的 u8co。口令只从终端读取。")
    sub = parser.add_subparsers(dest="command", required=True)
    _legacy_commands(sub)
    _voucher_commands(sub)
    _edit_commands(sub)
    _workflow_commands(sub)
    _gl_arc_commands(sub)
    _report_commands(sub)
    _assist_commands(sub)
    # 字段元数据：同 health，不要账套和口令。
    meta = sub.add_parser("meta")
    meta.add_argument("--base-url", default=None, help=_BASE_HELP)
    return parser


def _gl_arc_commands(sub: Any) -> None:
    # 总账、档案、列表、现存量命令在 u8co_cli_gl_arc.py。它反过来用本模块的参数和文件读取，只能在这里导入。
    from co.client.u8co_cli_gl_arc import add_commands

    add_commands(sub)


def _report_commands(sub: Any) -> None:
    # 只读报表命令在 u8co_cli_reports.py，导入方式同上。
    from co.client.u8co_cli_reports import add_commands

    add_commands(sub)


def _assist_commands(sub: Any) -> None:
    # resolve、idem-get，以及各写命令的 --dry-run（要在其他命令都登记之后加）。导入方式同上。
    from co.client.u8co_cli_assist import add_commands, add_dry_flags

    add_commands(sub)
    add_dry_flags(sub)


def _legacy_commands(sub: Any) -> None:
    for name in ("health", "login-check", "sale-order", "dispatch"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--base-url", default=None, help=_BASE_HELP)
        if name == "health":
            continue
        cmd.add_argument("--acc", required=True)
        cmd.add_argument("--year", required=True)
        cmd.add_argument("--operator", required=True)
        cmd.add_argument("--date", required=True)
        if name == "login-check":
            continue
        cmd.add_argument("--id", required=True, type=int)
        cmd.add_argument("--action", required=True, choices=("verify", "unverify"))


def _voucher_commands(sub: Any) -> None:
    for name, kinds in (("load", KIND_NAMES), ("delete", DELETABLE_KINDS)):
        cmd = sub.add_parser(name)
        _add_auth(cmd)
        _doc_args(cmd, kinds, action=False)
    verify = sub.add_parser("verify")
    _add_auth(verify)
    _doc_args(verify, VERIFIABLE_KINDS, action=True)
    create = sub.add_parser("create")
    _add_auth(create)
    create.add_argument("--type", required=True, choices=CREATABLE_KINDS)
    create.add_argument("--head-json")
    create.add_argument("--lines-json")
    create.add_argument("--file")
    add_key_arg(create)


def _edit_commands(sub: Any) -> None:
    update = sub.add_parser("update")
    _add_auth(update)
    update.add_argument("--type", required=True, choices=UPDATABLE_KINDS)
    update.add_argument("--id", required=True, type=int)
    update.add_argument("--file", required=True)
    close = sub.add_parser("close")
    _add_auth(close)
    close.add_argument("--type", required=True, choices=CLOSABLE_KINDS)
    close.add_argument("--id", required=True, type=int)
    close.add_argument("--action", required=True, choices=("close", "open"))
    close.add_argument("--line-ids")
    generate = sub.add_parser("generate")
    _add_auth(generate)
    generate.add_argument("--type", required=True, choices=GENERATABLE_KINDS)
    generate.add_argument("--id", required=True, type=int)
    generate.add_argument("--file", required=True)
    generate.add_argument("--source-type", choices=SOURCE_TYPES, default="")
    add_key_arg(generate)


def _workflow_commands(sub: Any) -> None:
    for name in _WF_DOC:
        cmd = sub.add_parser(name)
        _add_auth(cmd)
        _doc_args(cmd, WORKFLOW_KINDS, action=False)
    tasks = sub.add_parser("wf-tasks")
    _add_auth(tasks)
    tasks.add_argument("--type", choices=KIND_NAMES)
    for name, (_method, required) in _OPINION.items():
        cmd = sub.add_parser(name)
        _add_auth(cmd)
        _doc_args(cmd, WORKFLOW_KINDS, action=False)
        cmd.add_argument("--opinion", required=required)


def _add_auth(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--base-url", default=None, help=_BASE_HELP)
    cmd.add_argument("--acc", required=True)
    cmd.add_argument("--year", required=True)
    cmd.add_argument("--operator", required=True)
    cmd.add_argument("--date", required=True)


def _doc_args(cmd: argparse.ArgumentParser, kinds: tuple[str, ...], action: bool) -> None:
    cmd.add_argument("--type", required=True, choices=kinds)
    cmd.add_argument("--id", required=True, type=int)
    if action:
        cmd.add_argument("--action", required=True, choices=_ACTIONS)


def _secret(config: dict[str, str] | None = None) -> str:
    value = resolve_secret(config)
    try:
        derive_keys(value)
    except ValueError:
        raise SystemExit("服务密钥必须是 64 位小写十六进制") from None
    return value


def _password() -> str:
    # 没有终端就停。getpass 在无 TTY 时会退回标准输入，口令可能进管道日志。
    try:
        fd = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        raise SystemExit("需要在终端里输入口令") from None
    os.close(fd)
    value = getpass.getpass("请输入 U8 操作员口令: ")
    if not value:
        raise SystemExit("口令为空")
    return value


def _field(pattern: re.Pattern[str], value: str, message: str) -> str:
    if pattern.fullmatch(value) is None:
        raise SystemExit(message)
    return value


def _date(value: str) -> str:
    try:
        dt.datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise SystemExit("日期必须是 yyyy-MM-dd") from None
    return value


def _operator(value: str) -> str:
    if not value or len(value) > 20:
        raise SystemExit("操作员编码不能为空，最长 20")
    if any(ch.isspace() or ch in "\\\"'" for ch in value):
        raise SystemExit("操作员编码不能含空白或引号")
    return value


def _call_from(parsed: argparse.Namespace, password: str) -> U8Call:
    _field(_ACC, parsed.acc, "账套号必须是 3 位数字")
    _field(_YEAR, parsed.year, "年度必须是 4 位数字")
    _date(parsed.date)
    _operator(parsed.operator)
    doc_id = getattr(parsed, "id", None)
    action = getattr(parsed, "action", "") or ""
    if doc_id is not None and int(doc_id) <= 0:
        raise SystemExit("单据 id 必须是正整数")
    return U8Call(parsed.acc, parsed.year, parsed.operator, password, parsed.date, doc_id, action)


def _loads(text: str, label: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise SystemExit(f"{label}不是 JSON") from None


def _read_file(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            raw = handle.read(_FILE_LIMIT + 1)
    except OSError:
        raise SystemExit("读不到单据文件") from None
    if len(raw) > _FILE_LIMIT:
        raise SystemExit("单据文件超过 64 KiB")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SystemExit("单据文件必须是 UTF-8 JSON") from None


def _json_object(path: str) -> dict[str, Any]:
    data = _loads(_read_file(path), "单据文件")
    if not isinstance(data, dict):
        raise SystemExit("单据文件必须是 JSON 对象")
    return data


def _file_parts(path: str) -> tuple[object, object]:
    data = _json_object(path)
    if set(data) != {"head", "lines"}:
        raise SystemExit("单据文件只能包含 head 和 lines")
    return data["head"], data["lines"]


def _head_lines(path: str) -> dict[str, Any]:
    data = _json_object(path)
    if not set(data) <= {"head", "lines"}:
        raise SystemExit("单据文件只能包含 head 和 lines")
    return data


def _edit_draft(parsed: argparse.Namespace) -> VoucherEdit:
    data = _head_lines(parsed.file)
    if "head" not in data and "lines" not in data:
        raise SystemExit("单据文件要包含 head 或 lines")
    return VoucherEdit(parsed.type, data.get("head"), data.get("lines"))


def _gen_draft(parsed: argparse.Namespace) -> VoucherGen:
    data = _head_lines(parsed.file)
    source = getattr(parsed, "source_type", "") or ""
    if whole_generate(parsed.type, source):
        return VoucherGen(parsed.type, data.get("head"), data.get("lines"), source)
    if "lines" not in data:
        raise SystemExit("generate 需要 lines")
    return VoucherGen(parsed.type, data.get("head"), data["lines"], source)


def _decimal_id(part: str) -> bool:
    return part.isascii() and part.isdecimal()


def _parse_line_ids(text: str | None) -> list[int] | None:
    if text is None:
        return None
    parts = [item.strip() for item in text.split(",")]
    if not parts or not all(_decimal_id(part) for part in parts):
        raise SystemExit("line-ids 必须是逗号分隔的正整数")
    return [int(part) for part in parts]


def _draft(parsed: argparse.Namespace) -> VoucherDraft:
    has_file = bool(parsed.file)
    has_flags = bool(parsed.head_json or parsed.lines_json)
    if has_file and has_flags:
        raise SystemExit("create 用 --file 时不要再传 --head-json 或 --lines-json")
    if has_file:
        head, lines = _file_parts(parsed.file)
        return VoucherDraft(parsed.type, head, lines)
    if not parsed.head_json or not parsed.lines_json:
        raise SystemExit("create 需要 --file，或同时给出 --head-json 和 --lines-json")
    return VoucherDraft(parsed.type, _loads(parsed.head_json, "head"), _loads(parsed.lines_json, "lines"))


def _cmd_login(client: U8CoClient, call: U8Call, _parsed: argparse.Namespace) -> dict[str, Any]:
    return client.login_check(call)


def _cmd_sale(client: U8CoClient, call: U8Call, _parsed: argparse.Namespace) -> dict[str, Any]:
    return client.verify_sale_order(call)


def _cmd_dispatch(client: U8CoClient, call: U8Call, _parsed: argparse.Namespace) -> dict[str, Any]:
    return client.verify_dispatch(call)


def _cmd_load(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.load_voucher(call, parsed.type)


def _cmd_verify(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.verify_voucher(call, parsed.type)


def _cmd_create(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.create_voucher(call, _draft(parsed), **key_kwargs(parsed))


def _cmd_delete(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.delete_voucher(call, parsed.type)


def _cmd_update(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.update_voucher(call, _edit_draft(parsed))


def _cmd_close(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.close_voucher(call, parsed.type, parsed.action, _parse_line_ids(parsed.line_ids))


def _cmd_generate(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.generate_voucher(call, _gen_draft(parsed), **key_kwargs(parsed))


def _cmd_tasks(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.workflow_tasks(call, parsed.type or "")


def _cmd_wf(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return _WF_DOC[parsed.command](client, call, parsed.type)


def _cmd_opinion(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    method, required = _OPINION[parsed.command]
    return method(client, call, parsed.type, _opinion_of(parsed, required))


def _opinion_of(parsed: argparse.Namespace, required: bool) -> str | None:
    text = parsed.opinion
    if required:
        return text if isinstance(text, str) else ""
    if isinstance(text, str):
        return text
    return None


_WF_DOC = {
    "wf-state": U8CoClient.workflow_state,
    "wf-history": U8CoClient.workflow_history,
    "wf-submit": U8CoClient.workflow_submit,
    "wf-withdraw": U8CoClient.workflow_withdraw,
    "wf-resubmit": U8CoClient.workflow_resubmit,
}
_OPINION = {
    "wf-approve": (U8CoClient.workflow_approve, False),
    "wf-disagree": (U8CoClient.workflow_disagree, True),
    "wf-return": (U8CoClient.workflow_return, True),
    "wf-abandon": (U8CoClient.workflow_abandon, False),
}
_HANDLERS = {
    "login-check": _cmd_login,
    "sale-order": _cmd_sale,
    "dispatch": _cmd_dispatch,
    "load": _cmd_load,
    "verify": _cmd_verify,
    "create": _cmd_create,
    "delete": _cmd_delete,
    "update": _cmd_update,
    "close": _cmd_close,
    "generate": _cmd_generate,
    "wf-tasks": _cmd_tasks,
}
_HANDLERS.update({name: _cmd_wf for name in _WF_DOC})
_HANDLERS.update({name: _cmd_opinion for name in _OPINION})


def _gl_arc_handler(command: str) -> Any:
    from co.client.u8co_cli_gl_arc import HANDLERS

    return HANDLERS.get(command)


def _report_handler(command: str) -> Any:
    from co.client.u8co_cli_reports import HANDLERS

    return HANDLERS.get(command)


def _assist_handler(command: str) -> Any:
    from co.client.u8co_cli_assist import HANDLERS

    return HANDLERS.get(command)


def _with_dry(client: U8CoClient, parsed: argparse.Namespace) -> U8CoClient:
    from co.client.u8co_cli_assist import dry_client

    return dry_client(client, parsed)


def _run(parsed: argparse.Namespace) -> dict[str, Any]:
    config = load_config()
    client = U8CoClient(resolve_base_url(parsed.base_url, config), _secret(config))
    if parsed.command == "health":
        return client.health()
    if parsed.command == "meta":
        return client.meta()
    handler = _HANDLERS.get(parsed.command) or _gl_arc_handler(parsed.command) or _report_handler(parsed.command)
    handler = handler or _assist_handler(parsed.command)
    if handler is None:
        raise SystemExit("未知命令")
    client = _with_dry(client, parsed)
    call = _call_from(parsed, _password())
    return handler(client, call, parsed)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    _reject_password_arg(args)
    try:
        payload = _run(_parser().parse_args(args))
    except U8CoError as exc:
        print(exc.describe(), file=sys.stderr)  # 有 field / hint 时一并显示
        return 1
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
