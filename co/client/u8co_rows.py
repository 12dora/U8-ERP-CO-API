"""单据表头、表体行的校验（只收字符串键、标量值，表体 1 到 200 行，采购手工结算 400 行）。从 u8co_client 拆出，供 U8CoClient 用。

例外：物料清单（bom）行的 substitutes、locators 是对象数组，每项同样只收标量。
"""

from __future__ import annotations

from typing import Any

from co.client.u8co_kinds import whole_generate


# 值可以是对象数组的字段，按单据类型登记（与桥 NestedFields、API co_models_nested 一致）：
# 检验单表头的 items（u8co_qm.qm_head 细查），物料清单明细行的 substitutes、locators（每项只收标量）。
NESTED_HEAD = {kind: ("items",) for kind in ("qm_incoming_check", "qm_product_check", "qm_other_check")}
NESTED_LINES = {"bom": ("substitutes", "locators")}


def _row(row: object, nested: tuple[str, ...] = ()) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("head 和 lines 的每一行必须是对象")
    clean: dict[str, Any] = {}
    for key, value in row.items():
        if type(key) is not str or key == "":
            raise ValueError("字段名必须是非空字符串")
        if type(value) in (list, tuple) and key.lower() in nested:
            clean[key] = [_row(item) for item in value]
            continue
        if type(value) not in (str, int, float, bool):
            raise ValueError("字段值只能是字符串、数字或布尔")
        clean[key] = value
    return clean


def _rows(lines: object, most: int = 200, kind: str = "") -> list[dict[str, Any]]:
    if not isinstance(lines, (list, tuple)) or not 1 <= len(lines) <= most:
        raise ValueError(f"lines 必须是 1 到 {most} 行")
    nested = NESTED_LINES.get(kind, ())
    return [_row(item, nested) for item in lines]


def _create_rows(kind: str, lines: object) -> list[dict[str, Any]]:
    """新建的表体：采购手工结算（purchase_settle）1 到 400 行，其余 1 到 200 行。"""
    return _rows(lines, 400 if kind == "purchase_settle" else 200, kind)


def _present_row(row: object) -> dict[str, Any] | None:
    if row is None:
        return None
    clean = _row(row)
    if not clean:
        return None
    return clean


def _present_lines(lines: object, kind: str = "") -> list[dict[str, Any]] | None:
    if lines is None or (isinstance(lines, (list, tuple)) and len(lines) == 0):
        return None
    return _rows(lines, kind=kind)


def _gen_lines(kind: str, lines: object, source: str = "") -> list[dict[str, Any]] | None:
    if lines is None and whole_generate(kind, source):
        return None  # 销售出库、红字销售发票（参照退货单）、采购结算、采购入库（参照到货单）省略 lines 时整单生成
    return _rows(lines)
