"""存货：第一条模板用 SQL 插入（sql/inventory_template.sql），变体经 archives/create 按模板复制，再用 SQL 改标志。

存货的业务标志（自制、BOM、质检、批次……）在 EAI 标签里没有稳定的写法，复制后直接 UPDATE Inventory。
"""

from __future__ import annotations

import re
from pathlib import Path

from co.e2e.seed import data
from co.e2e.seed.base import Probe, Step, archive_step, ensure_archives, existing_codes, quoted, remember
from co.e2e.seed.context import SeedContext, SeedError

TEMPLATE_FILE = Path(__file__).resolve().parent / "sql" / "inventory_template.sql"
_FLAG = re.compile(r"^b[A-Za-z]{2,30}\Z")


def template_sql() -> str:
    text = TEMPLATE_FILE.read_text(encoding="utf-8")
    values = {
        "code": data.INV_TEMPLATE,
        "name": data.INV_TEMPLATE_NAME,
        "class": data.INV_CLASS,
        "unit": data.UNIT,
        "group": data.UNIT_GROUP,
        "tax": str(int(data.TAX_RATE)),
    }
    for key, value in values.items():
        if data.SAFE_CODE.fullmatch(value) is None:
            raise SeedError("模板取值不合法：" + key)
        text = text.replace("{{" + key + "}}", value)
    if "{{" in text:
        raise SeedError("存货模板 SQL 里还有没替换的占位符")
    return text


def flags_sql(roles: tuple = data.INVENTORY_ROLES) -> str:
    lines = []
    for code, _name, flags in roles:
        sets = []
        for column, value in flags.items():
            if _FLAG.fullmatch(column) is None or value not in (0, 1):
                raise SeedError("存货标志不合法：" + column)
            sets.append(f"{column} = {value}")
        lines.append(f"UPDATE Inventory SET {', '.join(sets)} WHERE cInvCode = {quoted([code])};")
    return "\n".join(lines)


def _probe_template(ctx: SeedContext) -> Probe:
    if existing_codes(ctx, "inventory", [data.INV_TEMPLATE]):
        return Probe((data.INV_TEMPLATE,), ())
    return Probe((), (data.INV_TEMPLATE,))


def _apply_template(ctx: SeedContext) -> tuple[int, int]:
    if existing_codes(ctx, "inventory", [data.INV_TEMPLATE]):
        return 0, 1
    ctx.exec(template_sql())
    if not existing_codes(ctx, "inventory", [data.INV_TEMPLATE]):
        raise SeedError("模板存货插入后仍查不到 " + data.INV_TEMPLATE, saved=ctx.last_call)
    remember(ctx, "inventory", data.INV_TEMPLATE)
    return 1, 0


def _apply_inventories(ctx: SeedContext) -> tuple[int, int]:
    new, existed = ensure_archives(ctx, data.INVENTORIES)
    # 标志每次都按 data.py 覆盖一遍，重跑也回到同一状态。
    ctx.exec(flags_sql())
    return new, existed


_VARIANTS = archive_step("inventories", "存货变体", data.INVENTORIES)

STEPS = (
    Step("inventory_template", "模板存货（SQL）", _probe_template, _apply_template),
    Step("inventories", "存货变体", _VARIANTS.probe, _apply_inventories),
)
