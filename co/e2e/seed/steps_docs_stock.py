"""造单据（一）：物料清单和库存单据。

- bom：BOM 母件 + 3 个子件的标准 BOM，生效日取 data.SEED_DATE（早于昨天，生产订单按开工日期展开），再审核。
  母件是造数专用的存货，按母件找已有的标准 BOM，不另写标记。子件行带仓库 06：生产订单子件（mom_moallocate.WhCode）
  从这里带出，回归的 mo_alloc 挑单要子件有仓库。
- bom_plain：免检母件（bPropertyCheck=0）用同样 3 个子件的标准 BOM，审核。
- other_in：仓库 06 其他入库（收发类别 data.RD_OTHER_IN，缺省 205），每个存货 10 个（普通件多入，供出库、调拨、发货用；BOM 子件多入，
  生产订单领料后仍有 >= 10 个给无来源材料出库的挑单；批次件带批号），审核。
- other_out：普通件其他出库 2 个（收发类别 data.SALE_OUT_RD，缺省 101），审核。
- transfer：普通件 06 → 07 调拨 2 个（收发类别 data.TRANSFER_OUT_RD / TRANSFER_IN_RD，缺省 111 / 203），留着不审核。
- other_in_binned：货位仓 01 其他入库，普通件 10 个放末级货位 01，审核。供带货位的其他入库删除 `binned-other-in-delete`、
  货位存量报表 `report-position-stock` 和销售出库按货位拆行 `sale-out-bin-split`（要货位上结存 >= 2）挑单。
- other_in_lot2：仓库 06 其他入库，批次件第二个批号 SEED-LOT2 10 个，审核。销售出库改批号 `sale-out-batch-override`
  要同一仓库里两个批号可用量各 >= 1。
- other_out_open：普通件其他出库 1 个，留着不审核，供其他出库审核与弃审 `stock-09-verify` 挑单。
"""

from __future__ import annotations

from co.e2e.seed import data
from co.e2e.seed.base import quoted
from co.e2e.seed.context import SeedContext
from co.e2e.seed.steps_docs_base import (
    Doc,
    create_doc,
    doc_step,
    ensure_verified,
    find_ids,
    find_marked,
    marker,
    verify_after,
)

WH_MAIN = data.WH_PLAIN[0]
WH_OTHER = data.WH_PLAIN[1]
RD_OTHER_IN = data.RD_OTHER_IN
RD_OTHER_OUT = data.SALE_OUT_RD
BATCH = "SEED-LOT1"
BATCH_2 = "SEED-LOT2"
BINNED_QTY = 10
LOT2_QTY = 10
OPEN_OUT_QTY = 1
STOCK_QTY = 10
PLAIN_QTY = 30
CHILD_QTY = 20
OUT_QTY = 2
BOM_PARENT = data.INVENTORY_ROLES[0][0]
BOM_CHILDREN = tuple(code for code, _name, _flags in data.INVENTORY_ROLES[1:4])
PLAIN_BOM_PARENT = data.PLAIN_BOM_PARENT
BATCH_INVENTORY = data.INVENTORY_ROLES[5][0]

_BOM_SQL = (
    "SELECT b.BomId AS id FROM bom_bom AS b JOIN bom_parent AS p ON p.BomId = b.BomId"
    " JOIN bas_part AS bp ON bp.PartId = p.ParentId"
    " WHERE b.BomType = 1 AND bp.InvCode = {parent} ORDER BY b.BomId"
)


# ---- 正文（纯函数） ----


def bom_body(parent: str = BOM_PARENT, key: str = "bom") -> dict:
    head = {"inv_code": parent, "version_desc": marker(key), "eff_date": data.SEED_DATE, "parent_scrap": 0}
    lines = [
        {"inv_code": code, "base_qty_n": 1, "base_qty_d": 1, "wip_type": 3, "wh_code": WH_MAIN} for code in BOM_CHILDREN
    ]
    return {"type": "bom", "head": head, "lines": lines}


def _stock_qty(code: str) -> int:
    if code == data.PLAIN_INVENTORY:
        return PLAIN_QTY
    return CHILD_QTY if code in BOM_CHILDREN else STOCK_QTY


def other_in_body(day: str) -> dict:
    head = {
        "cwhcode": WH_MAIN,
        "crdcode": RD_OTHER_IN,
        "cdepcode": data.DEPT_EXPENSE,
        "ddate": day,
        "cmemo": marker("other_in"),
    }
    lines = []
    for code, _name, _flags in data.INVENTORY_ROLES:
        line: dict = {"cinvcode": code, "iquantity": _stock_qty(code), "iunitcost": 1}
        if code == BATCH_INVENTORY:
            line["cbatch"] = BATCH
        lines.append(line)
    return {"type": "other_in", "head": head, "lines": lines}


def other_out_body(day: str, key: str = "other_out", qty: int = OUT_QTY) -> dict:
    head = {
        "cwhcode": WH_MAIN,
        "crdcode": RD_OTHER_OUT,
        "cdepcode": data.DEPT_EXPENSE,
        "ddate": day,
        "cmemo": marker(key),
    }
    return {"type": "other_out", "head": head, "lines": [{"cinvcode": data.PLAIN_INVENTORY, "iquantity": qty}]}


def _extra_in_body(day: str, wh: str, key: str, line: dict) -> dict:
    head = {"cwhcode": wh, "crdcode": RD_OTHER_IN, "cdepcode": data.DEPT_EXPENSE, "ddate": day, "cmemo": marker(key)}
    return {"type": "other_in", "head": head, "lines": [dict(line, iunitcost=1)]}


def other_in_binned_body(day: str) -> dict:
    # 货位仓每行必须带末级货位（桥 400「仓库有货位管理，必须指定货位 cposition」），货位台账由 U8 保存时写。
    line = {"cinvcode": data.PLAIN_INVENTORY, "iquantity": BINNED_QTY, "cposition": data.POSITION}
    return _extra_in_body(day, data.WH_BINNED, "other_in_binned", line)


def other_in_lot2_body(day: str) -> dict:
    line = {"cinvcode": BATCH_INVENTORY, "iquantity": LOT2_QTY, "cbatch": BATCH_2}
    return _extra_in_body(day, WH_MAIN, "other_in_lot2", line)


def transfer_body(day: str) -> dict:
    head = {
        "cowhcode": WH_MAIN,
        "ciwhcode": WH_OTHER,
        "cordcode": data.TRANSFER_OUT_RD,
        "cirdcode": data.TRANSFER_IN_RD,
        "codepcode": data.DEPT_PURCHASE,
        "cidepcode": data.DEPT_SALES,
        "dtvdate": day,
        "cmemo": marker("transfer"),
    }
    return {"type": "transfer", "head": head, "lines": [{"cinvcode": data.PLAIN_INVENTORY, "itvquantity": OUT_QTY}]}


# ---- 查找 ----


def find_bom(ctx: SeedContext) -> list[int]:
    return find_ids(ctx, _BOM_SQL.format(parent=quoted([BOM_PARENT])))


def find_bom_plain(ctx: SeedContext) -> list[int]:
    return find_ids(ctx, _BOM_SQL.format(parent=quoted([PLAIN_BOM_PARENT])))


def find_other_in(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "other_in", "other_in")


def find_other_out(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "other_out", "other_out")


def find_transfer(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "transfer", "transfer")


def find_other_in_binned(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "other_in", "other_in_binned")


def find_other_in_lot2(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "other_in", "other_in_lot2")


def find_other_out_open(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "other_out", "other_out_open")


STEPS = (
    doc_step(
        Doc(
            "bom",
            "物料清单（新增并审核）",
            find_bom,
            lambda ctx: create_doc(ctx, bom_body()),
            lambda ctx, doc_id: ensure_verified(ctx, "bom", doc_id),
        )
    ),
    doc_step(
        Doc(
            "bom_plain",
            "免检母件的物料清单（新增并审核）",
            find_bom_plain,
            lambda ctx: create_doc(ctx, bom_body(PLAIN_BOM_PARENT, "bom_plain")),
            lambda ctx, doc_id: ensure_verified(ctx, "bom", doc_id),
        )
    ),
    doc_step(
        Doc(
            "other_in",
            "其他入库（各存货进仓 06，审核）",
            find_other_in,
            lambda ctx: create_doc(ctx, other_in_body(ctx.day)),
            verify_after("other_in"),
        )
    ),
    doc_step(
        Doc(
            "other_out",
            "其他出库（审核）",
            find_other_out,
            lambda ctx: create_doc(ctx, other_out_body(ctx.day)),
            verify_after("other_out"),
        )
    ),
    doc_step(Doc("transfer", "调拨单 06→07（不审核）", find_transfer, lambda ctx: create_doc(ctx, transfer_body(ctx.day)))),
    doc_step(
        Doc(
            "other_in_binned",
            "其他入库（普通件进货位仓 01 的货位 01，审核）",
            find_other_in_binned,
            lambda ctx: create_doc(ctx, other_in_binned_body(ctx.day)),
            verify_after("other_in"),
        )
    ),
    doc_step(
        Doc(
            "other_in_lot2",
            "其他入库（批次件第二个批号进仓 06，审核）",
            find_other_in_lot2,
            lambda ctx: create_doc(ctx, other_in_lot2_body(ctx.day)),
            verify_after("other_in"),
        )
    ),
    doc_step(
        Doc(
            "other_out_open",
            "其他出库（普通件 1 个，不审核）",
            find_other_out_open,
            lambda ctx: create_doc(ctx, other_out_body(ctx.day, "other_out_open", OPEN_OUT_QTY)),
        )
    ),
)
