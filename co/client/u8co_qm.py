"""检验单（来料 / 产品检验单）参照报检单生单时，表头可带检验项目 items 列表。其余字段仍不能嵌套。

其他检验单生单、检验单修改（vouchers/update）同样可带 items。
"""

from __future__ import annotations

from typing import Any, Callable

CHECK_KINDS = ("qm_incoming_check", "qm_product_check", "qm_other_check")
_ITEMS_MAX = 50


def _item(row: object) -> dict[str, Any]:
    if not isinstance(row, dict) or not row:
        raise ValueError("items 的每一项必须是非空对象")
    for key, value in row.items():
        if type(key) is not str or key == "":
            raise ValueError("字段名必须是非空字符串")
        if type(value) not in (str, int, float, bool):
            raise ValueError("items 的字段值只能是字符串、数字或布尔")
    return dict(row)


def _items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= _ITEMS_MAX:
        raise ValueError(f"items 必须是 1 到 {_ITEMS_MAX} 个检验项目")
    return [_item(row) for row in value]


def qm_head(kind: str, head: object, clean: Callable[[object], dict[str, Any] | None]) -> dict[str, Any] | None:
    """检验单把 items 拿出来单独校验，其余交给 clean（与别的类型同一套平铺校验）；别的类型原样交给 clean。"""
    if kind not in CHECK_KINDS or not isinstance(head, dict):
        return clean(head)
    keys = [key for key in head if isinstance(key, str) and key.lower() == "items"]
    if not keys:
        return clean(head)
    if len(keys) > 1:
        raise ValueError("items 重复")
    rest = {key: value for key, value in head.items() if key != keys[0]}
    found = clean(rest) or {}
    found[keys[0]] = _items(head[keys[0]])
    return found
