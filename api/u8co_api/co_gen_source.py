"""参照生单的来源类型，以及到货单、采购发票、来料检验生成采购入库的专用校验。与桥一致。"""

from __future__ import annotations

import re
from typing import Literal

from u8co_api.co_gen_qm import QM_SOURCES

SourceType = Literal[
    "sale_order",
    "dispatch",
    "purchase_order",
    "qm_incoming_check",
    "purchase_in",
    "production_order",
    "qm_product_check",
    # 产成品入库参照产品不良品处理单（QM06）。
    "qm_product_reject",
    # 采购退货单参照原蓝字到货单。
    "arrival",
    # 检验单参照报检单（QM03 ← QM01，QM04 ← QM02）。
    "qm_incoming_inspect",
    "qm_product_inspect",
    # 红字采购入库参照采购退货单（红字到货单）。
    "purchase_return",
    # 红字销售发票参照退货单（红字发货单）。
    "sale_return",
    # 调拨单参照调拨申请单。
    "transfer_request",
    # 其他检验单参照其他报检单。
    "qm_other_inspect",
    # 采购结算单参照采购发票（整张发票自动结算）。
    "purchase_invoice",
    # 红字销售发票参照已复核的蓝字销售发票（红冲）。
    "sale_invoice",
    # 退货单参照退货申请单。
    "sale_return_apply",
]
# 目标类型 → 允许的来源类型。第一个是 source_type 省略时的缺省值。
SOURCES = {
    "dispatch": ("sale_order",),
    "sale_out": ("dispatch",),
    "arrival": ("purchase_order",),
    # 红字采购入库参照采购退货单（purchase_return）；蓝字采购入库参照到货单（arrival，lines 可省）。
    "purchase_in": ("purchase_order", "qm_incoming_check", "purchase_return", "arrival"),
    # 红字销售发票参照退货单（sale_return）；红冲蓝字销售发票（sale_invoice）。
    "sale_invoice": ("dispatch", "sale_return", "sale_invoice"),
    "material_out": ("production_order",),
    # 产成品入库另可参照生产订单（id 是 MoId，source_line_id 是 MoDId）。
    "product_in": ("qm_product_check", "qm_product_reject", "production_order"),
    "purchase_invoice": ("purchase_in",),
    # 采购退货单参照原蓝字到货单（缺省）或采购订单。
    "purchase_return": ("arrival", "purchase_order"),
    # 退货单（红字发货单）参照原蓝字发货单；另可参照已审核的退货申请单。
    "sale_return": ("dispatch", "sale_return_apply"),
    # 调拨单参照调拨申请单。
    "transfer": ("transfer_request",),
    # 采购结算单参照采购发票（co_gen_pu_settle）。
    "purchase_settle": ("purchase_invoice",),
}
# 来料报检单参照到货单、产品报检单参照生产订单、检验单参照报检单。
SOURCES.update(QM_SOURCES)
_INVOICE_KEYS = frozenset({"cpbvcode", "cpbvbilltype", "dpbvdate", "cpbvmemo"})
_ARRIVAL_KEYS = frozenset({"cwhcode", "ddate", "cmemo", "cdepcode"})
_PLAIN_LINE = frozenset({"source_line_id", "quantity"})
_QM_LINE = frozenset({"source_line_id", "quantity", "cbatch", "cbmemo", "cposition"})
# 采购入库参照到货单：行上另可带 cWhCode（表头没给时各行须一致，与桥 StockGenArr.ArrWarehouse 一致）。
_ARR_IN_LINE = _QM_LINE | {"cwhcode"}
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# 产成品入库参照产品检验单的最多行数（合并检验一个来源一行；非合并检验只能 1 行，由桥按检验单判断）。
# 与桥 MfgReq.CheckLinesMax 一致。
PRODUCT_CHECK_LINES = 20


def source_of(kind: str, source: str | None) -> str:
    allowed = SOURCES[kind]
    if source is None:
        return allowed[0]
    if source not in allowed:
        raise ValueError("source_type 不能是 " + source)
    return source


def _only(row: dict, allowed: frozenset[str]) -> None:
    for key in row:
        if key.lower() not in allowed:
            raise ValueError("不能设置字段 " + key)


def _string_fields(head: dict, allowed: frozenset[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, value in head.items():
        low = key.lower()
        if low not in allowed:
            raise ValueError("不能设置字段 " + key)
        if low in found:
            raise ValueError("字段重复 " + key)
        if type(value) is not str:
            raise ValueError(key + " 必须是字符串")
        found[low] = value
    return found


def check_invoice(head: dict | None, lines: list[dict]) -> None:
    """采购发票：表头只收 cPBVCode（必填）、cPBVBillType、dPBVDate、cPBVMemo；明细只有 source_line_id 和 quantity。"""
    found = _string_fields(head or {}, _INVOICE_KEYS)
    code = found.get("cpbvcode", "")
    if not code.strip() or len(code) > 30:
        raise ValueError("cPBVCode 必填，最长 30 个字符")
    if found.get("cpbvbilltype", "01") not in ("01", "02"):
        raise ValueError("cPBVBillType 只能是 01 或 02")
    if "dpbvdate" in found and _DATE.fullmatch(found["dpbvdate"]) is None:
        raise ValueError("dPBVDate 必须是 yyyy-MM-dd")
    if len(found.get("cpbvmemo", "")) > 255:
        raise ValueError("cPBVMemo 最长 255 个字符")
    for row in lines:
        _only(row, _PLAIN_LINE)


def check_arrival(head: dict | None, lines: list[dict]) -> None:
    """到货单参照采购订单：表头可省略，只收字符串 cWhCode、dDate、cMemo、cDepCode。

    明细只有 source_line_id（PO_Podetails.ID）和 quantity。
    """
    found = _string_fields(head or {}, _ARRIVAL_KEYS)
    if "ddate" in found and _DATE.fullmatch(found["ddate"]) is None:
        raise ValueError("dDate 必须是 yyyy-MM-dd")
    for row in lines:
        _only(row, _PLAIN_LINE)


def check_product_in(source: str, lines: list[dict]) -> None:
    """产成品入库的行数：参照产品检验单最多 PRODUCT_CHECK_LINES 行，其余来源 1 行。

    合并检验一个来源一行；非合并检验只能 1 行，由桥按检验单判断。
    """
    if source == "qm_product_check" and len(lines) > PRODUCT_CHECK_LINES:
        raise ValueError(f"参照产品检验单的产成品入库表体须为 1 到 {PRODUCT_CHECK_LINES} 行")
    if source != "qm_product_check" and len(lines) != 1:
        raise ValueError("产成品入库只能有 1 行")


def check_qm_in(head: dict | None, lines: list[dict]) -> None:
    """采购入库参照来料检验单：表头要有 cWhCode，明细只能 1 行。"""
    if not any(key.lower() == "cwhcode" for key in head or {}):
        raise ValueError("参照来料检验单时表头必须有 cWhCode")
    if len(lines) != 1:
        raise ValueError("参照来料检验单只能有 1 行")
    _only(lines[0], _QM_LINE)


def lines_optional(kind: str, source: str) -> bool:
    """不带 lines 时由桥整单生成的组合（这里只管采购入库参照到货单）。"""
    return kind == "purchase_in" and source == "arrival"


def _wh(row: dict | None) -> str:
    for key, value in (row or {}).items():
        if key.lower() == "cwhcode" and value is not None and type(value) is not bool:
            return str(value).strip().lower()
    return ""


def check_arrival_in(head: dict | None, lines: list[dict]) -> None:
    """采购入库参照到货单：lines 可省（整单按剩余）；仓库取表头 cWhCode，表头没给时取各行一致的 cWhCode。"""
    for row in lines:
        _only(row, _ARR_IN_LINE)
    found = {_wh(head)} | {_wh(row) for row in lines}
    found.discard("")
    if not found:
        raise ValueError("参照到货单时必须指定仓库 cWhCode（表头或表体）")
    if len(found) > 1:
        raise ValueError("一张采购入库单只能有一个仓库")
