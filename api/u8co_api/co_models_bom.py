"""物料清单（type=bom）新增、修改的请求校验和新增响应的子件明细。与桥 BomReq 一致，字段名不分大小写。"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field

from u8co_api.co_doctext import section
from u8co_api.co_models_mo import _date, _get, _only, _text

_OUT = ConfigDict(extra="ignore")
_QTY_MAX = Decimal(10**12)
_SEQ_MAX = 99999
_INT_MAX = 2147483647
LINES_MAX = 200
# 表头：新增必须有母件 inv_code；version 省略时取最大版本加版本增量；eff_date 缺省为登录日期。修改只收后三个。
_CREATE_HEAD = ("inv_code", "version", "version_desc", "eff_date", "parent_scrap")
_EDIT_HEAD = ("version_desc", "eff_date", "parent_scrap")
# 明细字符串字段和最大长度；数值字段另查。
_TEXT = {"inv_code": 60, "op_seq": 4, "wh_code": 10, "remark": 255}
_ADD_KEYS = ("sort_seq", "op_seq", "inv_code", "base_qty_n", "base_qty_d", "comp_scrap", "wip_type", "wh_code", "remark")
_EDIT_KEYS = ("op_seq", "base_qty_n", "base_qty_d", "comp_scrap", "wip_type", "wh_code", "remark")
BOM_CREATE_HELP = section(
    "物料清单（bom）",
    (
        "只建标准 BOM（主 BOM）的新版本；新版本按账套设置多为未审核",
        "表头必填 inv_code（母件，自制且允许做 BOM 母件）",
        "表头可省 version（整数，省略时取该母件最大版本加版本增量）、version_desc（最长 255）",
        "表头可省 eff_date（yyyy-MM-dd，缺省登录日期，不能与该母件其他版本相同）",
        "表头可省 parent_scrap（母件损耗率 0 到小于 100）",
        "明细 1 到 200 行，必填 inv_code（子件）、base_qty_n（基本用量分子，大于 0，最多 6 位小数）",
        "明细可省 base_qty_d（分母，缺省 1）、comp_scrap（子件损耗率）、wip_type（1 到 5，缺省 3 领用）",
        "明细可省 wh_code、remark、op_seq（缺省 0000）、sort_seq（缺省按 10、20… 往后排）",
    ),
)
BOM_UPDATE_HELP = section(
    "物料清单（bom）",
    (
        "只改未审核的标准 BOM；表头只收 version_desc、eff_date、parent_scrap",
        "明细按 sort_seq 定位（不用 line_id）",
        "update 改 base_qty_n、base_qty_d、comp_scrap、wip_type、wh_code、remark、op_seq，不能改 inv_code",
        "delete 只带 op 和 sort_seq；add 同新增的行",
    ),
)


def check_bom_create(head: dict, lines: list[dict]) -> None:
    """不合规抛 ValueError（API 层 422）。"""
    _only(head, dict.fromkeys(_CREATE_HEAD))
    _text(head, "inv_code", 60, required=True)
    if _get(head, "inv_code") is None:
        raise ValueError("缺少字段 inv_code")
    if _get(head, "version") is not None:
        _int(_get(head, "version"), "version", _INT_MAX)
    _check_head(head)
    if not 1 <= len(lines) <= LINES_MAX:
        raise ValueError("物料清单明细必须是 1 到 200 行")
    seqs: set[int] = set()
    for row in lines:
        _add_row(row, seqs)


def check_bom_update(head: dict | None, lines: list[dict] | None) -> None:
    head = head or {}
    _only(head, dict.fromkeys(_EDIT_HEAD))
    _check_head(head)
    keyed: set[int] = set()
    added: set[int] = set()
    for row in lines or []:
        _edit_row(row, keyed, added)


def _check_head(head: dict) -> None:
    _text(head, "version_desc", 255, required=False)
    if _get(head, "eff_date") is not None:
        _date(_get(head, "eff_date"), "eff_date")
    if _get(head, "parent_scrap") is not None:
        _rate(_get(head, "parent_scrap"), "parent_scrap")


def _add_row(row: dict, seqs: set[int]) -> None:
    _only(row, dict.fromkeys(_ADD_KEYS))
    if _get(row, "inv_code") is None:
        raise ValueError("缺少字段 inv_code")
    if _get(row, "base_qty_n") is None:
        raise ValueError("缺少字段 base_qty_n")
    if _get(row, "sort_seq") is not None:
        seq = _int(_get(row, "sort_seq"), "sort_seq", _SEQ_MAX)
        if seq in seqs:
            raise ValueError(f"sort_seq 重复：{seq}")
        seqs.add(seq)
    _fields(row)


def _edit_row(row: dict, keyed: set[int], added: set[int]) -> None:
    op = row.get("op")
    rest = {key: val for key, val in row.items() if key.lower() != "op"}
    if op == "add":
        _add_row(rest, added)
        return
    if op not in ("update", "delete"):
        raise ValueError("op 只能是 add、update 或 delete")
    if _get(rest, "sort_seq") is None:
        raise ValueError("修改和删除行必须带 sort_seq")
    seq = _int(_get(rest, "sort_seq"), "sort_seq", _SEQ_MAX)
    if seq in keyed:
        raise ValueError(f"明细行重复：sort_seq {seq}")
    keyed.add(seq)
    fields = {key: val for key, val in rest.items() if key.lower() != "sort_seq"}
    if op == "delete" and fields:
        raise ValueError("删除行只能带 op 和 sort_seq")
    if op == "update":
        _edit_fields(fields)


def _edit_fields(fields: dict) -> None:
    if _get(fields, "inv_code") is not None:
        raise ValueError("不能修改存货编码，请删除该行后新增")
    _only(fields, dict.fromkeys(_EDIT_KEYS))
    if not fields:
        raise ValueError("修改行至少要改一个字段")
    _fields(fields)
    for key in ("wh_code", "remark"):
        value = _get(fields, key)
        if isinstance(value, str) and not value.strip():
            raise ValueError("修改行不能把 wh_code、remark 改成空")


def _fields(row: dict) -> None:
    for key, limit in _TEXT.items():
        _text(row, key, limit, required=key in ("inv_code", "op_seq") and _get(row, key) is not None)
    for key in ("base_qty_n", "base_qty_d"):
        if _get(row, key) is not None:
            _qty(_get(row, key), key)
    if _get(row, "comp_scrap") is not None:
        _rate(_get(row, "comp_scrap"), "comp_scrap")
    if _get(row, "wip_type") is not None:
        _int(_get(row, "wip_type"), "wip_type", 5)


def _int(value: object, key: str, top: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(key + " 必须是整数")
    if not 1 <= value <= top:
        raise ValueError(f"{key} 必须是 1 到 {top} 的整数")
    return value


def _number(value: object, key: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(key + " 必须是数")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(key + " 必须是有限数")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(key + " 必须是数") from exc


def _qty(value: object, key: str) -> None:
    qty = _number(value, key)
    if qty <= 0 or qty > _QTY_MAX:
        raise ValueError(key + " 必须大于 0 且不超过 1000000000000")
    if qty != qty.quantize(Decimal("0.000001")):
        raise ValueError(key + " 最多 6 位小数")


def _rate(value: object, key: str) -> None:
    rate = _number(value, key)
    if rate < 0 or rate >= 100:
        raise ValueError(key + " 必须是 0 到小于 100 的数")
    if rate != rate.quantize(Decimal("0.001")):
        raise ValueError(key + " 最多 3 位小数")


class CoBomComponent(BaseModel):
    model_config = _OUT
    line_id: int = Field(description="子件行主键 OpComponentId")
    sort_seq: int = Field(description="子件行号")
    inv_code: str = Field(description="子件存货编码")
    base_qty_n: float = Field(description="基本用量分子")
    base_qty_d: float = Field(description="基本用量分母")
