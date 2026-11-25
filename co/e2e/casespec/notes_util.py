"""票据用例共用的金额常量与小工具（发请求、按列核对、回读一行）。

不引用回读 SQL 模块，notes_sql 也从这里取票面，避免循环引用；notes 再导出这些名字。
"""

from __future__ import annotations

import re
from decimal import Decimal

from co.e2e.casespec.stock_plus import _Fail, _NotApplicable, _Run
from co.e2e.casespec.arap_proc import DROP, VOUCHER
from co.e2e.testdata import DataMissing
from co.e2e.testdata import value as td

FACE = Decimal("100.00")
# 部分金额：非分包票据只能整张处理，用它断言桥拒绝。
PART = Decimal("40.00")
FEE = Decimal("1.00")
ZERO = Decimal("0.00")
_CENT = Decimal("0.01")
_MISSING = ("未知路径", "Not Found")
# 处理制单按数据来源推不出现金流量项目时桥 409 的文案，科目编码取自消息；重试用测试数据里的现金流量项目。
_NO_CASH = re.compile(r"科目 (\S+) 的现金流量项目无法按数据来源唯一确定")
CASH_FLOW_ITEM = "gl.cash_flow_item"
_PZ = re.compile(r"^AR[0-9]{1,20}\Z")


def money(value: Decimal) -> str:
    return format(value.quantize(_CENT), "f")


def missing(status: int, payload: dict) -> bool:
    message = str(payload.get("message"))
    return status == 404 and any(word in message for word in _MISSING)


def send(run: _Run, route: str, body: dict, what: str) -> dict:
    """发请求；接口还没有该路由记 N/A，其余非 200 记 FAIL。"""
    status, payload = run.send(route, body)
    if missing(status, payload):
        raise _NotApplicable("接口还没有 " + route.removeprefix("/v1/") + " 路由")
    if status != 200:
        raise _Fail(f"{what}：{status} {payload.get('code')} {payload.get('message')}"[:240])
    return payload


def expect(parts: list[str], want: dict[int, str], what: str) -> None:
    """parts 的指定位置等于期望值；不等时列出全部不符。"""
    bad = [f"第 {idx + 1} 列 {parts[idx]!r}≠{value!r}" for idx, value in want.items() if parts[idx] != value]
    if bad:
        raise _Fail(f"{what}：" + "，".join(bad))


def row(run: _Run, query: str, width: int, what: str) -> list[str]:
    cell = run.cell(query)
    parts = cell.split("|") if cell else []
    if len(parts) != width:
        raise _Fail(f"{what}回读不到（{cell[:60] or '空'}）")
    return parts


def cash_items(status: int, payload: dict) -> dict[str, str] | None:
    """处理制单 409「科目 X 的现金流量项目无法按数据来源唯一确定…」→ 重试用的 {X: 测试数据 gl.cash_flow_item}；
    其余应答返回 None。测试数据没配记 N/A。"""
    hit = _NO_CASH.search(str(payload.get("message") or "")) if status == 409 else None
    if hit is None:
        return None
    try:
        item = td(CASH_FLOW_ITEM)
    except DataMissing as exc:
        raise _NotApplicable(f"凭证要指定科目 {hit.group(1)} 的现金流量项目，但{exc.reason()}") from None
    return {hit.group(1): item}


def proc_voucher(run: _Run, no: str) -> tuple[str, dict[str, str] | None]:
    """应收处理制单（一个处理号）；409 现金流量项目推不出时带 cash_items 重试一次。返回外部业务号和用过的 cash_items，
    成功后记下取消制单。"""
    body: dict = {"flag": "AR", "cancel_nos": [no]}
    status, payload = run.send(VOUCHER, body)
    items = cash_items(status, payload)
    if items is not None:
        status, payload = run.send(VOUCHER, dict(body, cash_items=items))
    if status != 200:
        raise _Fail(f"处理制单：{status} {payload.get('code')} {payload.get('message')}"[:240])
    pz = str(payload.get("pz_id") or "")
    if _PZ.fullmatch(pz) is None:
        raise _Fail("处理制单的响应没有外部业务号：" + pz[:40])
    run.undo.append((DROP, {"flag": "AR", "pz_id": pz}, "取消制单"))
    return pz, items
