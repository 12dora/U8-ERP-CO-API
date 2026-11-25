"""造单据（二）：销售。客户除 so_ret 外都是 C0001（带税号，开票要用），订单都在当天，满足按客户列表、搜索至少两张。

- so_open：两行的销售订单，不审核（新增-修改-删除、审核用例从它克隆）。
- so_main：两行的销售订单，审核；发货只发第一行的一部分，两行都留有未发数量。
- dispatch：参照 so_main 第一行发 2 个（仓库 06），审核。按表头备注找。
- sale_out：参照发货单整张生成销售出库，不审核。按来源发货单（rdrecord32.cDLCode 是 DLID）找。
- sale_invoice：参照发货单开 1 个的专用发票（26），不复核，留 1 个未开票。按表头备注找。

以下给回归挑单的补充单据（都按表头备注找；发货单按创建先后排，挑单取最新的 DLID，顺序不能改）：
- other_in_sale：仓库 06 普通件其他入库 20 个，审核。补上后面出库和待发的数量，不挤占别的挑单要的现存量。
- so_ret：客户 C0006（无税号）的销售订单 1 行 4 个，审核；so_more：C0001 的销售订单 6 行，审核。两张都全数发货，
  不留未发数量，so_more 建在后面，最新一张订单仍是 C0001。订单类挑单（so_ship、se_so、disp_mod_src、sale_master 等）
  取最新的留有未发数量的订单，造数全跑完后是收付款步骤里的 arwo_so（同为 C0001、第一行剩 3 个）。
- dispatch_spo：so_more 两行各 2 个，审核，不出库：给 sale-out-partial（spo_src）和 sale-out-batch-refuse（plain_line）。
- dispatch_arap：4 个，审核，整单出库，不开票：给 ar-invoice-audit / ar-voucher（arv_inv，剩余 >= 3）。
- dispatch_sinv_mod：4 个，审核，整单出库，不开票：给 sale-invoice-update（sinv_mod_src，剩余 >= 2）。
- dispatch_ret：C0006 4 个，审核，整单出库：给 sale-return-cycle / sale-return-update / dry-sale-return*（sr_src、sret_mod_src，
  可退 >= 2）。客户无税号，开票类挑单（都要求客户有税号）不会挑它，退货挑单也就不和它们挤同一张发货单。
- dispatch_inv：2 个，审核，整单出库，不开票：最新的可开票发货单，给 invoice-generate / dry-sale-invoice（inv_src）。
- dispatch_open：1 个，不审核：给 dispatch-verify（dispatch_id）。
各整单出库的销售出库不审核，步骤名是发货单步骤名加 _out，按来源发货单找。
"""

from __future__ import annotations

from dataclasses import dataclass

from co.e2e.seed import data
from co.e2e.seed.base import Step
from co.e2e.seed.context import SeedContext, SeedError
from co.e2e.seed.steps_docs_base import (
    Doc,
    create_doc,
    doc_step,
    find_ids,
    find_marked,
    generate_doc,
    line_ids,
    marker,
    need_one,
    one_or_none,
    plus_days,
    safe_id,
    taxed_amounts,
    verify_after,
)
from co.e2e.seed.steps_docs_stock import RD_OTHER_IN

CUSTOMER = data.CUSTOMERS[0].code
WH = data.WH_PLAIN[0]
SO_QTY = 5
SO_PRICE = 10
SHIP_QTY = 2
BILL_QTY = 1
INVOICE_TYPE = "26"

SO_LINES_SQL = "SELECT iSOsID AS id FROM SO_SODetails WHERE ID = {id} ORDER BY iSOsID"
DL_LINES_SQL = "SELECT iDLsID AS id FROM DispatchLists WHERE DLID = {id} ORDER BY iDLsID"
_SALE_OUT_SQL = "SELECT ID AS id FROM rdrecord32 WHERE cDLCode = N'{id}' ORDER BY ID"


# ---- 正文（纯函数） ----


def so_line(day: str, inv: str = data.PLAIN_INVENTORY) -> dict:
    line: dict = {"cinvcode": inv, "iquantity": SO_QTY}
    line.update(taxed_amounts(SO_QTY, SO_PRICE, data.TAX_RATE))
    line["dpredate"] = plus_days(day, 7)
    return line


def so_body(day: str, key: str) -> dict:
    head = {
        "ccuscode": CUSTOMER,
        "cstcode": "XS",
        "cdepcode": data.DEPT_SALES,
        "cpersoncode": data.SALES_PERSON,
        "cbustype": "普通销售",
        "cexch_name": data.CURRENCY,
        "iexchrate": 1,
        "itaxrate": data.TAX_RATE,
        "ddate": day,
        "cmemo": marker(key),
    }
    return {"type": "sale_order", "head": head, "lines": [so_line(day), so_line(day)]}


def dispatch_body(so_id: int, so_line_id: int) -> dict:
    line = {"source_line_id": so_line_id, "quantity": SHIP_QTY, "cwhcode": WH}
    head = {"cwhcode": WH, "cmemo": marker("dispatch")}
    return {"type": "dispatch", "source_type": "sale_order", "id": so_id, "head": head, "lines": [line]}


def sale_out_body(dl_id: int) -> dict:
    # 销售出库生单不能带任何表头字段，也就写不了标记。
    return {"type": "sale_out", "id": dl_id}


def invoice_body(dl_id: int, dl_line_id: int) -> dict:
    head = {"cvouchtype": INVOICE_TYPE, "cmemo": marker("sale_invoice")}
    line = {"source_line_id": dl_line_id, "quantity": BILL_QTY}
    return {"type": "sale_invoice", "id": dl_id, "head": head, "lines": [line]}


# ---- 查找 ----


def find_so_open(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "sale_order", "so_open")


def find_so_main(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "sale_order", "so_main")


def find_dispatch(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "dispatch", "dispatch")


def find_sale_out(ctx: SeedContext) -> list[int]:
    dl_id = one_or_none(ctx, find_dispatch)
    return find_ids(ctx, _SALE_OUT_SQL.format(id=safe_id(dl_id))) if dl_id else []


def find_invoice(ctx: SeedContext) -> list[int]:
    return find_marked(ctx, "sale_invoice", "sale_invoice")


# ---- 新建 ----


def _make_dispatch(ctx: SeedContext) -> int:
    so_id = need_one(ctx, find_so_main, "so_main")
    first = line_ids(ctx, SO_LINES_SQL, so_id)[0]
    return generate_doc(ctx, dispatch_body(so_id, first))


def _make_sale_out(ctx: SeedContext) -> int:
    return generate_doc(ctx, sale_out_body(need_one(ctx, find_dispatch, "dispatch")))


def _make_invoice(ctx: SeedContext) -> int:
    dl_id = need_one(ctx, find_dispatch, "dispatch")
    first = line_ids(ctx, DL_LINES_SQL, dl_id)[0]
    return generate_doc(ctx, invoice_body(dl_id, first))


# ---- 补充单据：给回归挑单（见模块说明）----

TOPUP_QTY = 20
RET_CUSTOMER = data.CUSTOMERS[5].code  # C0006：无税号，也不是留着不用的 C9999


@dataclass(frozen=True)
class Ship:
    key: str
    title: str
    so_key: str
    qtys: tuple[int, ...]
    verify: bool
    out: bool


# 创建顺序就是 DLID 顺序，挑单靠它分开（只靠 DLID 先后和剩余数量门槛）：用 --only / --skip 打乱了顺序（例如
# dispatch_inv 建在 dispatch_arap 之前）时，开票挑单会占掉 4 个的那张，应收审核（剩余 >= 3）就挑不到。必须按本表顺序造。
# 开票类挑单取最新的 dispatch_inv，发票改单用例取次新的有税号单 dispatch_sinv_mod，
# 应收审核再避开这两张取 dispatch_arap；退货挑单避开 dispatch_inv 后取最新的 dispatch_ret。dispatch_spo 没有出库，
# 开票、退货挑单都看不到它；部分出库挑单只看得到它（其余发货单都已整单出库）。
SHIPS = (
    Ship("dispatch_spo", "发货单（两行，审核，不出库）", "so_more", (2, 2), True, False),
    Ship("dispatch_arap", "发货单（4 个，审核，出库不开票）", "so_more", (4,), True, True),
    Ship("dispatch_sinv_mod", "发货单（4 个，审核，出库不开票）", "so_more", (4,), True, True),
    Ship("dispatch_ret", "发货单（无税号客户 4 个，审核，出库）", "so_ret", (4,), True, True),
    Ship("dispatch_inv", "发货单（2 个，审核，出库不开票）", "so_more", (2,), True, True),
    Ship("dispatch_open", "发货单（1 个，不审核）", "so_more", (1,), False, False),
)


def _so_qtys(so_key: str) -> tuple[int, ...]:
    return tuple(qty for ship in SHIPS if ship.so_key == so_key for qty in ship.qtys)


def _so_offset(target: Ship) -> int:
    # 本发货单用订单的第几行起（同一张订单按 SHIPS 顺序依次占行）。
    used = 0
    for ship in SHIPS:
        if ship is target:
            return used
        if ship.so_key == target.so_key:
            used += len(ship.qtys)
    raise ValueError(target.key)


def topup_body(day: str) -> dict:
    head = {"cwhcode": WH, "crdcode": RD_OTHER_IN, "cdepcode": data.DEPT_EXPENSE, "ddate": day}
    head["cmemo"] = marker("other_in_sale")
    line = {"cinvcode": data.PLAIN_INVENTORY, "iquantity": TOPUP_QTY, "iunitcost": 1}
    return {"type": "other_in", "head": head, "lines": [line]}


def extra_so_body(day: str, key: str, customer: str) -> dict:
    body = so_body(day, key)
    body["head"]["ccuscode"] = customer
    lines = []
    for qty in _so_qtys(key):
        line = so_line(day)
        line["iquantity"] = qty
        line.update(taxed_amounts(qty, SO_PRICE, data.TAX_RATE))
        lines.append(line)
    body["lines"] = lines
    return body


def ship_body(ship: Ship, so_id: int, so_line_ids: list[int]) -> dict:
    start = _so_offset(ship)
    picked = so_line_ids[start : start + len(ship.qtys)]
    if len(picked) != len(ship.qtys):
        raise SeedError("销售订单 " + str(so_id) + " 的行数不够 " + ship.key + " 用")
    lines = [{"source_line_id": i, "quantity": q, "cwhcode": WH} for i, q in zip(picked, ship.qtys)]
    head = {"cwhcode": WH, "cmemo": marker(ship.key)}
    return {"type": "dispatch", "source_type": "sale_order", "id": so_id, "head": head, "lines": lines}


def _so_step(key: str, title: str, customer: str) -> Step:
    def make(ctx: SeedContext) -> int:
        return create_doc(ctx, extra_so_body(ctx.day, key, customer))

    return doc_step(Doc(key, title, lambda ctx: find_marked(ctx, "sale_order", key), make, verify_after("sale_order")))


def _ship_steps(ship: Ship) -> tuple[Step, ...]:
    def find(ctx: SeedContext) -> list[int]:
        return find_marked(ctx, "dispatch", ship.key)

    def make(ctx: SeedContext) -> int:
        so_id = need_one(ctx, lambda c: find_marked(c, "sale_order", ship.so_key), ship.so_key)
        return generate_doc(ctx, ship_body(ship, so_id, line_ids(ctx, SO_LINES_SQL, so_id)))

    def find_out(ctx: SeedContext) -> list[int]:
        dl_id = one_or_none(ctx, find)
        return find_ids(ctx, _SALE_OUT_SQL.format(id=safe_id(dl_id))) if dl_id else []

    def make_out(ctx: SeedContext) -> int:
        return generate_doc(ctx, sale_out_body(need_one(ctx, find, ship.key)))

    after = verify_after("dispatch") if ship.verify else None
    steps = (doc_step(Doc(ship.key, ship.title, find, make, after)),)
    if ship.out:
        steps += (doc_step(Doc(ship.key + "_out", "销售出库单（参照 " + ship.key + "）", find_out, make_out)),)
    return steps


EXTRA_STEPS: tuple[Step, ...] = (
    doc_step(
        Doc(
            "other_in_sale",
            "其他入库（补销售用的现存量）",
            lambda ctx: find_marked(ctx, "other_in", "other_in_sale"),
            lambda ctx: create_doc(ctx, topup_body(ctx.day)),
            verify_after("other_in"),
        )
    ),
    _so_step("so_ret", "销售订单（无税号客户，审核，全数发货）", RET_CUSTOMER),
    _so_step("so_more", "销售订单（审核，全数发货）", CUSTOMER),
) + tuple(step for ship in SHIPS for step in _ship_steps(ship))


STEPS = (
    doc_step(Doc("so_open", "销售订单（未审核）", find_so_open, lambda ctx: create_doc(ctx, so_body(ctx.day, "so_open")))),
    doc_step(
        Doc(
            "so_main",
            "销售订单（审核，留未发数量）",
            find_so_main,
            lambda ctx: create_doc(ctx, so_body(ctx.day, "so_main")),
            verify_after("sale_order"),
        )
    ),
    doc_step(Doc("dispatch", "发货单（参照订单，审核）", find_dispatch, _make_dispatch, verify_after("dispatch"))),
    doc_step(Doc("sale_out", "销售出库单（参照发货单）", find_sale_out, _make_sale_out)),
    doc_step(Doc("sale_invoice", "销售专用发票（参照发货单，不复核）", find_invoice, _make_invoice)),
) + EXTRA_STEPS
