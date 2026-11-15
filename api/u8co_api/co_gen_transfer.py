"""调拨单参照调拨申请单（vouchers/generate，type=transfer，source_type=transfer_request）的请求校验，与桥 StockGenTr 一致。

表头只收 dTVDate（yyyy-MM-dd）、cMemo、cODepCode、cIDepCode、cPersonCode、cORdCode、cIRdCode、cDefine1–16，可省略；
仓库取自申请单，不能填。表体 1 到 200 行，只收 source_line_id（申请单行 autoID）、quantity（大于 0、最多 6 位小数）、cbMemo。
"""

from __future__ import annotations

import re

from u8co_api.co_doctext import section
from u8co_api.co_stmisc import check_stmisc_qty

TRANSFER_GEN_HELP = section(
    "调拨单（source_type=transfer_request，id 是申请单 ID）",
    (
        "表头只收 dTVDate、cMemo、cODepCode、cIDepCode、cPersonCode、cORdCode、cIRdCode、cDefine1–16",
        "仓库取自申请单",
        "行只收 source_line_id（申请单行 autoID）、quantity、cbMemo",
        "数量不超过核准数量减累计调拨数量",
    ),
)
_HEAD_KEYS = frozenset({"dtvdate", "cmemo", "codepcode", "cidepcode", "cpersoncode", "cordcode", "cirdcode"})
_LINE_KEYS = frozenset({"source_line_id", "quantity", "cbmemo"})
_DEFINE = re.compile(r"cdefine([1-9]|1[0-6])")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_ID_MAX = 2147483647


def _head(head: dict | None) -> None:
    for low, value in _lowered(head or {}).items():
        if low not in _HEAD_KEYS and _DEFINE.fullmatch(low) is None:
            raise ValueError("不能设置字段 " + low)
        if low == "dtvdate" and (not isinstance(value, str) or _DATE.fullmatch(value) is None):
            raise ValueError("dTVDate 必须是 yyyy-MM-dd")


def _lowered(row: dict) -> dict:
    # 键不分大小写：先按小写收拢（只差大小写的重复键拒绝），校验和取值都用它。
    low: dict = {}
    for key, value in row.items():
        name = str(key).lower()
        if name in low:
            raise ValueError("字段重复 " + str(key))
        low[name] = value
    return low


def _line(raw_row: dict, seen: set[int]) -> None:
    row = _lowered(raw_row)
    for key in row:
        if key not in _LINE_KEYS:
            raise ValueError("不能设置字段 " + key)
    raw = row.get("source_line_id")
    if type(raw) is not int or raw < 1 or raw > _ID_MAX:
        raise ValueError("source_line_id 无效")
    if raw in seen:
        raise ValueError("来源明细重复")
    seen.add(raw)
    if "quantity" not in row:
        raise ValueError("缺少 quantity")
    check_stmisc_qty(row["quantity"], False)


def check_transfer_gen(head: dict | None, lines: list[dict] | None) -> None:
    _head(head)
    if not lines or len(lines) > 200:
        raise ValueError("lines 必须是 1 到 200 行")
    seen: set[int] = set()
    for row in lines:
        _line(row, seen)
