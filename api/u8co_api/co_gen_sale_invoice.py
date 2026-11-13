"""销售发票参照发货单（vouchers/generate，type=sale_invoice）的多来源形状与回签字段，与桥 SaleInvSources、SaleGenInvPlan、
SalePinsInv 一致。

- sources：[{id: 发货单 DLID, lines: [...]}, ...]，1 到 200 张、DLID 不重复，各张 lines 合计 1 到 200 行，与 id + lines 互斥；
  单张的 id + lines 是 n = 1 的同一条路径。只给参照发货单（source_type 省略或 dispatch），红字发票、红冲不收。
- 同一客户、业务类型、币种、汇率、表头税率，每张都蓝字、非期初、已审核、未关闭，数量不超过各行剩余可开票数量：桥查库核对。
- 蓝字发票表体另收 cbdefine1（回签数量）、cbdefine2（回签含税价）、cbdefine3（回签价税合计），写进 SaleBillVouchs_extradefine，
  保存后同一事务回读；账套没有扩展表或该列时 400。值是字符串或数字。
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from u8co_api.co_models import Cell

_ID_MAX = 2147483647
_MAX_LINES = 200
_SIGNBACK = ("cbdefine1", "cbdefine2", "cbdefine3")

SOURCES_HELP = (
    "销售发票参照多张发货单（只用于 type=sale_invoice、source_type 省略或 dispatch），与 id、lines 互斥。\n\n"
    "- 每项 {id: 发货单 DLID, lines: [...]}，行形状同 lines；1 到 200 张、id 不重复，各张合计 1 到 200 行。\n"
    "- 各张须同一客户、业务类型、币种、汇率、表头税率；表头照第一张抄，响应 source_id 是第一张，另有 source_ids。\n"
    "- 单张的 id + lines 与只有一项的 sources 走同一条路径。"
)
SIGNBACK_HELP = (
    "销售发票参照发货单（蓝字）的表体另可带回签字段：cbdefine1 回签数量、cbdefine2 回签含税价、cbdefine3 回签价税合计"
    "（SaleBillVouchs_extradefine，保存后回读核对；账套没有该列时 400）。发票数量仍是 quantity。"
)


class CoInvSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(..., gt=0, le=_ID_MAX, description="来源发货单主键 DLID")
    lines: list[dict[str, Cell]] = Field(
        ..., min_length=1, max_length=_MAX_LINES, description="这张发货单要开票的行，形状同 vouchers/generate 的 lines"
    )


def check_invoice_sources(
    kind: str,
    source: str,
    plain: tuple[int | None, list | None],
    sources: list[CoInvSource],
    row_check: Callable[[dict, set[int]], None],
) -> None:
    """多来源只给参照发货单的销售发票；不与 id、lines 同时给；DLID 不重复；合计行数；每行按 row_check（共用一个 seen）。"""
    if kind != "sale_invoice" or source != "dispatch":
        raise ValueError("sources 只用于参照发货单生成销售发票（type=sale_invoice，source_type 省略或 dispatch）")
    if plain[0] is not None or plain[1] is not None:
        raise ValueError("sources 不能与 id、lines 同时给出")
    ids = [one.id for one in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("来源发货单重复")
    if sum(len(one.lines) for one in sources) > _MAX_LINES:
        raise ValueError("各来源合计的明细必须是 1 到 200 行")
    seen: set[int] = set()
    for one in sources:
        for row in one.lines:
            row_check(row, seen)
            _signback(row)


def check_invoice_lines(kind: str, source: str, lines: list[dict[str, Cell]] | None) -> None:
    """单张形状：回签字段只给参照发货单的蓝字发票（红字、红冲由桥按白名单拒绝，这里只核值的类型）。"""
    if kind != "sale_invoice" or source != "dispatch" or not lines:
        return
    for row in lines:
        _signback(row)


def _signback(row: dict) -> None:
    for key, value in row.items():
        if key.lower() not in _SIGNBACK or value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError(f"{key} 必须是字符串或数字")
