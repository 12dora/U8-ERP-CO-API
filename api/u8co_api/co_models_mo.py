"""生产订单新增（vouchers/create，type=production_order）的请求校验和响应明细。与桥 MoCreateReq 一致。"""

from __future__ import annotations

import datetime as dt
import math
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field

from u8co_api.co_doctext import section

_OUT = ConfigDict(extra="ignore")
_QTY_MAX = Decimal(10**12)
LINES_MAX = 50
# 与桥 TryParseExact("yyyy-MM-dd") 一致：四位年、两位月日，只收 ASCII 数字（fromisoformat 还收 2026-W39-1 这类写法）。
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# 表头：mo_code 省略则 U8 按 MO21 规则自动编号；remark 是各行备注的缺省。「code」在全局禁写名单里，所以单号叫 mo_code。
_HEAD = {"mo_code": 30, "remark": 255}
# 表体：字符串字段和最大长度；qty 另查。
_LINE_TEXT = {
    "inv_code": 60,
    "start_date": 10,
    "due_date": 10,
    "mo_type": 20,
    "dept_code": 12,
    "wh_code": 10,
    "remark": 255,
}
_REQUIRED = ("inv_code", "qty", "start_date", "due_date", "mo_type", "dept_code")
MO_CREATE_HELP = section(
    "生产订单（production_order）",
    (
        "表头只收 mo_code（最长 30，省略则 U8 自动编号）和 remark（最长 255，作各行备注的缺省）",
        "明细 1 到 50 行，必须有 inv_code（自制件）、qty、start_date、due_date、mo_type、dept_code",
        "qty 大于 0，最多 6 位小数，且不超过账套的存货数量小数位",
        "start_date、due_date 为 yyyy-MM-dd，完工不早于开工",
        "mo_type 是生产订单类别编码，dept_code 是末级生产部门",
        "可选 wh_code（预入仓库）和 remark",
        "U8 按存货的标准 BOM 自动展开子件",
        "没有有效 BOM 时照样新增，响应里该行 allocates 为 0 并带 warnings",
    ),
)


def check_mo_create(head: dict, lines: list[dict]) -> None:
    """不合规抛 ValueError（API 层 422）。字段名不分大小写，与桥一致。"""
    _only(head, _HEAD)
    for key, limit in _HEAD.items():
        _text(head, key, limit, required=False)
    if not 1 <= len(lines) <= LINES_MAX:
        raise ValueError("生产订单明细必须是 1 到 50 行")
    for row in lines:
        _check_line(row)


def _check_line(row: dict) -> None:
    _only(row, {**_LINE_TEXT, "qty": 0})
    for key in _REQUIRED:
        if _get(row, key) is None:
            raise ValueError("缺少字段 " + key)
    for key, limit in _LINE_TEXT.items():
        _text(row, key, limit, required=key in _REQUIRED)
    _qty(_get(row, "qty"))
    start = _date(_get(row, "start_date"), "start_date")
    due = _date(_get(row, "due_date"), "due_date")
    if due < start:
        raise ValueError("due_date 不能早于 start_date")


def _only(row: dict, allowed: dict) -> None:
    for key in row:
        if key.lower() not in allowed:
            raise ValueError("不能设置字段 " + key)


def _get(row: dict, name: str) -> object:
    for key, value in row.items():
        if key.lower() == name:
            return value
    return None


def _text(row: dict, key: str, limit: int, *, required: bool) -> None:
    value = _get(row, key)
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError(key + " 必须是字符串")
    text = value.strip()
    if required and not text:
        raise ValueError("缺少字段 " + key)
    if len(text) > limit:
        raise ValueError(f"{key} 不能超过 {limit} 个字符")
    if any(_is_control(char) for char in text):
        raise ValueError(key + " 含控制字符")


def _is_control(char: str) -> bool:
    # 只拒控制、格式、代理、私用、未分配（Unicode 类别 C*）；全角空格等分隔符照收。
    return char != " " and unicodedata.category(char)[0] == "C"


def _qty(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("qty 必须是数")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("qty 必须是有限数")
    try:
        qty = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("qty 必须是数") from exc
    if qty <= 0 or qty > _QTY_MAX:
        raise ValueError("qty 必须大于 0 且不超过 1000000000000")
    if qty != qty.quantize(Decimal("0.000001")):
        raise ValueError("qty 最多 6 位小数")


def _date(value: object, key: str) -> dt.date:
    text = value.strip() if isinstance(value, str) else ""
    try:
        if _DATE.fullmatch(text) is None:
            raise ValueError(text)
        return dt.datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(key + " 必须是 yyyy-MM-dd") from exc


class CoMoCreatedLine(BaseModel):
    model_config = _OUT
    line_id: int = Field(description="明细主键 MoDId")
    sort_seq: int = Field(description="行号")
    inv_code: str = Field(description="存货编码")
    qty: float = Field(description="生产数量")
    status: int = Field(description="行状态：1、2 未审核，3 已审核，4 已关闭。U8API 新增的订单是 2")
    allocates: int = Field(description="U8 按标准 BOM 展开的子件行数；0 表示没有展开（存货没有有效的标准 BOM）")
