"""订单执行、单据追溯命令：report-order-exec、report-doc-trace。由 u8co_cli_reports 并入。"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_reports_trace import ORDER_TYPES, TRACE_DIRECTIONS, TRACE_KINDS, DocTraceQuery, OrderExecQuery


def add_commands(sub: Any) -> None:
    execution = sub.add_parser("report-order-exec")
    _add_auth(execution)
    execution.add_argument("--type", required=True, choices=ORDER_TYPES)
    execution.add_argument("--id", action="append", type=int, metavar="ID", help="订单 id，可重复（最多 100 个）")
    execution.add_argument("--code", default="")
    execution.add_argument("--date-from", default="")
    execution.add_argument("--date-to", default="")
    execution.add_argument("--partner", default="", help="客户或供应商编码")
    execution.add_argument("--only-open", action="store_true", help="只要未执行完且未关闭的行")
    execution.add_argument("--after", default="")
    execution.add_argument("--limit", type=int)
    trace = sub.add_parser("report-doc-trace")
    _add_auth(trace)
    trace.add_argument("--type", required=True, choices=TRACE_KINDS)
    trace.add_argument("--id", required=True, type=int)
    trace.add_argument("--depth", type=int, help="每个方向的跳数，1 到 3，缺省 3")
    trace.add_argument("--direction", choices=TRACE_DIRECTIONS, default="", help="缺省 both")
    trace.add_argument("--max-nodes", type=int, help="最多节点数，1 到 200，缺省 200")


def order_exec_query(parsed: argparse.Namespace) -> OrderExecQuery:
    return OrderExecQuery(
        type=parsed.type,
        ids=None if parsed.id is None else tuple(parsed.id),
        code=parsed.code,
        date_from=parsed.date_from,
        date_to=parsed.date_to,
        partner=parsed.partner,
        only_open=True if parsed.only_open else None,
        after=parsed.after,
        limit=parsed.limit,
    )


def doc_trace_query(parsed: argparse.Namespace) -> DocTraceQuery:
    return DocTraceQuery(parsed.type, parsed.id, parsed.depth, parsed.direction, parsed.max_nodes)


def _cmd_order_exec(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.report_order_execution(call, order_exec_query(parsed))


def _cmd_doc_trace(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.report_doc_trace(call, doc_trace_query(parsed))


HANDLERS = {
    "report-order-exec": _cmd_order_exec,
    "report-doc-trace": _cmd_doc_trace,
}
