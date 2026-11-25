"""基础档案的造数步骤（按依赖顺序）。每步先按 SQL 查已有编码，只经 archives/create 新建缺的。"""

from __future__ import annotations

from co.e2e.seed import data
from co.e2e.seed.base import Probe, Step, archive_step, ensure_archives, probe_archives, quoted
from co.e2e.seed.context import SeedContext

# 货位仓的 bWhPos 没有对应的 EAI 标签，建好仓库后用 SQL 打开。
_BINNED = "UPDATE Warehouse SET bWhPos = 1 WHERE cWhCode = {code} AND ISNULL(bWhPos, 0) <> 1"
_BINNED_CHECK = "SELECT cWhCode AS code FROM Warehouse WHERE cWhCode = {code} AND bWhPos = 1"


def _binned_ok(ctx: SeedContext) -> bool:
    return bool(ctx.rows(_BINNED_CHECK.format(code=quoted([data.WH_BINNED]))))


def _probe_warehouses(ctx: SeedContext) -> Probe:
    found = probe_archives(ctx, data.WAREHOUSES)
    if data.WH_BINNED in [item.split(":", 1)[1] for item in found.existing] and _binned_ok(ctx):
        return Probe(found.existing + ("bWhPos:" + data.WH_BINNED,), found.missing)
    return Probe(found.existing, found.missing + ("bWhPos:" + data.WH_BINNED,))


def _apply_warehouses(ctx: SeedContext) -> tuple[int, int]:
    new, existed = ensure_archives(ctx, data.WAREHOUSES)
    ctx.exec(_BINNED.format(code=quoted([data.WH_BINNED])))
    return new, existed


STEPS = (
    archive_step("units", "计量单位组和单位", data.UNITS),
    archive_step("rd_styles", "收发类别", data.RD_STYLES),
    archive_step("settle", "结算方式", data.SETTLE),
    archive_step("biz_types", "采购类型和销售类型", data.BIZ_TYPES),
    archive_step("district", "地区分类", data.DISTRICTS),
    archive_step("banks", "银行档案和本单位开户银行", data.BANKS),
    archive_step("currency", "外币", data.CURRENCIES),
    # 不良品处理单的不良原因，要在 qmr_* 之前（经 archives/create 建原因码 01，已有就跳过）。
    archive_step("qm_reason", "不良原因（原因码）", data.REASONS),
    archive_step("classes", "客户、供应商、存货分类", data.CLASSES),
    archive_step("departments", "部门", data.DEPARTMENTS),
    Step("warehouses", "仓库（含货位仓）", _probe_warehouses, _apply_warehouses),
    archive_step("positions", "货位", data.POSITIONS),
    archive_step("persons", "业务人员", data.PERSONS),
)

LATE_STEPS = (
    archive_step("customers", "客户", data.CUSTOMERS),
    archive_step("vendors", "供应商", data.VENDORS),
)
