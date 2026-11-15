"""退货单（红字发货单）参照蓝字发货单生单的专用校验。与桥 SaleGen.CheckReturn 一致。"""

from __future__ import annotations

import re

# 退货申请单的修改校验和生单说明在 co_return_apply，经这里给 co_models_edit（那边文件行数已到上限）。
from u8co_api.co_return_apply import APPLY_GENERATE_HELP as APPLY_GENERATE_HELP
from u8co_api.co_return_apply import APPLY_UPDATE_HELP as APPLY_UPDATE_HELP
from u8co_api.co_return_apply import check_apply_update as check_apply_update

_HEAD_KEYS = frozenset({"ddate", "cmemo", "cdepcode", "cpersoncode", "invoiced"})
_LINE_KEYS = frozenset({"source_line_id", "quantity", "cwhcode", "cmemo"})
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUMBERED = re.compile(r"(cdefine)([1-9]\d?)")


def _in_span(low: str, first: int, last: int) -> bool:
    match = _NUMBERED.fullmatch(low)
    return match is not None and first <= int(match.group(2)) <= last


def _check_keys(row: dict, allowed: frozenset[str], first: int, last: int) -> None:
    for key in row:
        low = key.lower()
        if low not in allowed and not _in_span(low, first, last):
            raise ValueError("不能设置字段 " + key)


def check_sale_return(head: dict | None, lines: list[dict]) -> None:
    """表头只收 dDate（yyyy-MM-dd）、cMemo、cDepCode、cPersonCode、cDefine1–16 和布尔 invoiced，可省略。

    invoiced：false 未开票退货、true 已开票退货；省略时桥按可退数量自动选。

    明细是原蓝字发货行 iDLsID（source_line_id）和正数 quantity，另可带 cWhCode、cMemo、cDefine22–37；不收自由项。
    """
    found = head or {}
    _check_keys(found, _HEAD_KEYS, 1, 16)
    for key, value in found.items():
        if key.lower() == "ddate" and (type(value) is not str or _DATE.fullmatch(value) is None):
            raise ValueError("dDate 必须是 yyyy-MM-dd")
        if key.lower() == "invoiced" and type(value) is not bool:
            raise ValueError("invoiced 必须是布尔")
    for row in lines:
        _check_keys(row, _LINE_KEYS, 22, 37)
