"""采购手工结算用例（vouchers/create，type=purchase_settle，第一级）。全程自建自删。

三条写入用例都先自建单据（仓库、供应商、部门、存货、采购类型同无来源红字采购入库的挑单），日期都是今天（结算日期即今天）。
入库单的来源看采购选项「普通业务必有订单」（bPTHavePO）：关闭时无来源新增；打开时蓝字走「采购订单 → 审核 → 参照订单生成
采购入库」。要红字入库的两条用例蓝红一起建：「采购订单 → 审核 → 参照订单生成到货单 → 审核 → 参照到货单生成蓝字采购入库 → 审核 →
参照到货单生成采购退货单 → 审核 → 参照退货单生成红字采购入库 → 审核」（有到货单之后再参照订单生成入库，U8 报「该操作会
造成订单到货和入库同时存在」；参照到货单的退货要到货行已合格入库 fValidInQuan，由蓝字入库写上）。
purchase-settle-manual-partial：蓝字采购入库 2 个 → 审核 → 参照它生成采购发票 2 个 → 采购复核 → 预演手工结算 1 个
  （200、没有结算行）→ 手工结算 1 个（入库行、发票行配对）→ 入库行已结算 1、发票行已结算 1 且没有结算日期、结算金额为发票金额的一半、
  入库行的金额 iPrice、单价 iUnitCost 不变（部分结算不改写，同自动结算）→ 再结算 2 个 409（超过未结算数量）→ 删除结算单 →
  入库行、发票行已结算数量回到 0，iPrice、iUnitCost 仍是结算前的值。
purchase-settle-rd-offset：蓝字入库 1 个、红字入库 −1 个（同一存货，都审核）→ 红蓝入库对冲（两行都没有发票行）→ 两条入库行已结算
  ±1、结算单两行没有发票行 → 删除 → 回到 0。
purchase-settle-inv-offset：蓝字入库 1 个 → 蓝字发票 1 个；红字入库 −1 个 → 红字发票 −1 个（PuInvRed），两张发票都采购复核 →
  红蓝发票对冲（两行都没有入库行）→ 两条发票行已结算 ±1、行和表头都写了结算日期、入库行已结算数量不变 → 删除 → 发票结算日期清空。
清理按建的倒序（删结算单 → 取消复核、删发票 → 弃审、删入库单 → 弃审、删退货单、到货单、订单）。挑不到数据、结算月以前还有
采购未结账的期间时记 N/A。
purchase-settle-manual-tier1（原 purchase-settle-manual-test-only 反转）：非测试账套用不存在的行预演手工结算，
  应过了分级、到行校验 400 / 409；403 test_account_only 说明第二级闸门还在，FAIL。预演不写数据，各通道都可跑。
步骤 kind 为 "custom"，sql_name 是操作名（PU_SETTLE_MAN_OPS 并入 ops_run）。
"""

from __future__ import annotations

from datetime import datetime

from co.e2e.casespec import pu_settle_manual_sql as q
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _dec, _doc_id, _Fail, _guard, _NotApplicable, _Run
from co.e2e.casespec.stock_plus_sql import havepo_sql, po_line_sql, safe_lot

KIND = "purchase_settle"
_CREATE = "/v1/vouchers/create"
_GEN = "/v1/vouchers/generate"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_MEMO = "U8CO-E2E-SETTLE-MAN"
_TEST_ONLY = "test_account_only"
_PROBE_ID = 2147483647


class _Seed:
    """挑出的档案：仓库、供应商、部门、存货、采购类型。"""

    def __init__(self, parts: list[str], havepo: bool) -> None:
        self.wh, self.ven, self.dep, self.inv, self.ptype = (safe_lot(part) for part in parts)
        self.havepo = havepo


def _seed(run: _Run) -> _Seed:
    parts = run.pick(q.pick_sql(), 5)
    if parts is None:
        raise _NotApplicable("测试账套挑不到有结存的外购存货")
    if run.cell(q.prior_open_sql(run.env.today)) not in ("0", ""):
        raise _NotApplicable("今天所在月以前还有采购未结账的期间，U8 不许结算")
    return _Seed(parts, run.cell(havepo_sql()) == "1")


def _purchase_in(run: _Run, seed: _Seed, qty: int) -> tuple[int, int]:
    """采购入库（qty < 0 为红字，只在无来源时）→ 审核；返回（单据 id, 第一行 AutoID）。必有订单时参照订单生成蓝字。"""
    if seed.havepo:
        pin = _blue_from_order(run, seed, qty)
    else:
        pin = _nosrc_in(run, seed, qty)
    run.ok(_VERIFY, {"type": "purchase_in", "id": pin, "action": "verify"}, "审核采购入库单")
    run.unverify("purchase_in", pin)
    return pin, _line_id(run.cell(q.rd_line_sql(pin)), "入库行")


def _made_verified(run: _Run, route: str, body: dict, what: str) -> int:
    """新建或生单 → 审核，记下清理（弃审后删除）。"""
    doc = run.made(route, body, what)
    kind = body["type"]
    run.drop(kind, doc)
    run.ok(_VERIFY, {"type": kind, "id": doc, "action": "verify"}, "审核" + kind)
    run.unverify(kind, doc)
    return doc


def _order(run: _Run, seed: _Seed, qty: int) -> tuple[int, int]:
    """本币普通采购订单（无税单价 1、税率 13）→ 审核；返回（订单 id, 订单行 ID）。"""
    head = {"cVenCode": seed.ven, "cDepCode": seed.dep, "cPTCode": seed.ptype, "cBusType": "普通采购",
            "cexch_name": "人民币", "nflat": 1, "iTaxRate": 13, "dPODate": run.env.today, "cMemo": _MEMO}
    line = {"cInvCode": seed.inv, "iQuantity": qty, "iUnitPrice": 1, "iPerTaxRate": 13}
    po = _made_verified(run, _CREATE, {"type": "purchase_order", "head": head, "lines": [line]}, "新建采购订单")
    return po, _line_id(run.cell(po_line_sql(str(po))), "订单行")


def _gen_body(kind: str, src: int, line: int, qty: int, head: dict) -> dict:
    return {"type": kind, "id": src, "head": head, "lines": [{"source_line_id": line, "quantity": qty}]}


def _blue_from_order(run: _Run, seed: _Seed, qty: int) -> int:
    po, po_line = _order(run, seed, qty)
    pin = run.made(_GEN, _gen_body("purchase_in", po, po_line, qty, {"cWhCode": seed.wh}), "参照采购订单生成采购入库")
    run.drop("purchase_in", pin)
    return pin


def _pair_ins(run: _Run, seed: _Seed) -> tuple[int, int, int, int]:
    """蓝字入库 1 个、红字入库 −1 个（都已审核）；返回（蓝字单 id, 蓝字行, 红字单 id, 红字行）。"""
    if not seed.havepo:
        blue, blue_rd = _purchase_in(run, seed, 1)
        red, red_rd = _purchase_in(run, seed, -1)
        return blue, blue_rd, red, red_rd
    # 必有订单：订单 → 到货单 → 参照到货单生成蓝字入库 → 参照到货单生成退货单 → 参照退货单生成红字入库。
    # 到货之后再参照订单生成入库，U8 报「该操作会造成订单到货和入库同时存在」。
    po, po_line = _order(run, seed, 1)
    arr = _made_verified(run, _GEN, _gen_body("arrival", po, po_line, 1, {"cWhCode": seed.wh}), "参照采购订单生成到货单")
    arr_line = _line_id(run.cell(q.arrival_line_sql(arr)), "到货单行")
    blue_body = dict(_gen_body("purchase_in", arr, arr_line, 1, {"cWhCode": seed.wh}), source_type="arrival")
    blue = _verified_in(run, blue_body, "参照到货单生成采购入库")
    dated = {"cWhCode": seed.wh, "dDate": run.env.today}
    ret_body = dict(_gen_body("purchase_return", arr, arr_line, 1, dated), source_type="arrival")
    ret = _made_verified(run, _GEN, ret_body, "参照到货单生成采购退货单")
    ret_line = _line_id(run.cell(q.arrival_line_sql(ret)), "退货单行")
    in_body = dict(_gen_body("purchase_in", ret, ret_line, 1, {"cWhCode": seed.wh, "dDate": run.env.today}),
                   source_type="purchase_return")
    red = _verified_in(run, in_body, "参照采购退货单生成红字采购入库")
    return blue, _line_id(run.cell(q.rd_line_sql(blue)), "入库行"), red, _line_id(run.cell(q.rd_line_sql(red)), "入库行")


def _verified_in(run: _Run, body: dict, what: str) -> int:
    pin = run.made(_GEN, body, what)
    run.drop("purchase_in", pin)
    run.ok(_VERIFY, {"type": "purchase_in", "id": pin, "action": "verify"}, "审核采购入库单")
    run.unverify("purchase_in", pin)
    return pin


def _nosrc_in(run: _Run, seed: _Seed, qty: int) -> int:
    head = {"cwhcode": seed.wh, "cvencode": seed.ven, "cdepcode": seed.dep, "cptcode": seed.ptype,
            "ddate": run.env.today, "cmemo": _MEMO}
    if qty < 0:
        head["red"] = True
    line = {"cinvcode": seed.inv, "iquantity": abs(qty), "iunitcost": 1, "itaxrate": 13}
    pin = run.made(_CREATE, {"type": "purchase_in", "head": head, "lines": [line]}, "新建采购入库单")
    run.drop("purchase_in", pin)
    return pin


def _invoice(run: _Run, pin: int, rd_line: int, qty: int) -> int:
    """参照采购入库生成专用发票（红字入库生成红字发票，数量送正数）→ 采购复核；返回发票第一行 ID。"""
    code = "SM" + datetime.now().strftime("%m%d%H%M%S%f")[:14]
    head = {"cPBVCode": code, "cPBVBillType": "01", "dPBVDate": run.env.today}
    body = {"type": "purchase_invoice", "id": pin, "head": head, "lines": [{"source_line_id": rd_line, "quantity": qty}]}
    pbv = run.made(_GEN, body, "参照采购入库生成采购发票")
    run.drop("purchase_invoice", pbv)
    run.ok(_VERIFY, {"type": "purchase_invoice", "id": pbv, "action": "verify"}, "采购发票复核")
    run.unverify("purchase_invoice", pbv)
    return _line_id(run.cell(q.bill_line_sql(pbv)), "发票行")


def _line_id(text: str, what: str) -> int:
    if not text.isdigit() or int(text) <= 0:
        raise _Fail(f"回读不到{what}：{text or '空'}")
    return int(text)


def _settle_body(run: _Run, lines: list[dict], dry: bool = False) -> dict:
    body = {"type": KIND, "head": {"settle_date": run.env.today}, "lines": lines}
    if dry:
        body["dry_run"] = True
    return body


def _settle(run: _Run, lines: list[dict]) -> int:
    payload = run.ok(_CREATE, _settle_body(run, lines), "手工结算")
    psvid = _doc_id(payload)
    if psvid == 0:
        raise _Fail("手工结算的响应没有结算单 id")
    run.drop(KIND, psvid)
    if payload.get("lines") != len(lines) or payload.get("settle_date") not in (None, run.env.today):
        raise _Fail(f"手工结算响应的 lines / settle_date 不符：{payload.get('lines')} / {payload.get('settle_date')}")
    return psvid


def _state(run: _Run, query: str, cols: int, what: str) -> list[str]:
    parts = run.pick(query, cols)
    if parts is None:
        raise _Fail("回读不到" + what)
    return parts


def _rd_qty(run: _Run, line: int) -> float:
    return float(_dec(_state(run, q.rd_state_sql(line), q.RD_COLS, "入库行")[0]))


def _price(run: _Run, line: int) -> tuple[str, str]:
    amount, cost = _state(run, q.rd_price_sql(line), q.PRICE_COLS, "入库行单价")
    return amount, cost


def _bill(run: _Run, line: int) -> tuple[float, str, str]:
    qty, day, head = _state(run, q.bill_state_sql(line), q.BILL_COLS, "发票行")
    return float(_dec(qty)), day, head


def _expect(ok: bool, message: str) -> None:
    if not ok:
        raise _Fail(message)


# ---- 配对、部分数量 ----


def _partial_body(run: _Run) -> tuple[str, str]:
    seed = _seed(run)
    pin, rd = _purchase_in(run, seed, 2)
    bs = _invoice(run, pin, rd, 2)
    pair = {"in_line_id": rd, "invoice_line_id": bs, "quantity": 1}
    status, payload = run.send(_CREATE, _settle_body(run, [pair], True))
    _expect(status == 200, f"预演手工结算应 200，实际 {status} {payload.get('code')} {payload.get('message')}")
    _expect(_rd_qty(run, rd) == 0, "预演之后入库行有了已结算数量")
    before = _price(run, rd)
    psvid = _settle(run, [pair])
    rows, qty, money, no_bill, no_rd = _state(run, q.settle_state_sql(psvid), q.SETTLE_COLS, "结算单")
    _expect((rows, no_bill, no_rd) == ("1", "0", "0") and _dec(qty) == 1, f"结算单 {rows} 行、数量 {qty}，应 1 行 1 个")
    _expect(_dec(money) == 1, f"结算金额 {money}，应为发票无税金额的一半（1.00）")
    bill, done = _bill(run, bs), _rd_qty(run, rd)
    _expect(done == 1 and bill[0] == 1, f"入库行、发票行已结算应各 1，实际 {done} / {bill[0]}")
    _expect(bill[1] == "" and bill[2] == "", f"发票没有结清，不应写结算日期：行 {bill[1] or '空'}、表头 {bill[2] or '空'}")
    after = _price(run, rd)
    _expect(after == before, f"部分结算改写了入库行的金额 / 单价：{'/'.join(before)} → {'/'.join(after)}")
    over = dict(pair, quantity=2)
    status, payload = run.send(_CREATE, _settle_body(run, [over]))
    _expect(status == 409, f"超过未结算数量应 409，实际 {status} {payload.get('message')}")
    run.settle(1)
    _expect(_rd_qty(run, rd) == 0 and _bill(run, bs)[0] == 0, "删除结算单后已结算数量没有回到 0")
    restored = _price(run, rd)
    _expect(restored == before, f"删除结算单后入库行的金额 / 单价没有还原：{'/'.join(before)} → {'/'.join(restored)}")
    return "PASS", f"入库行 {rd} 与发票行 {bs} 结算 1 / 2：结算单 {psvid}，单价不变，超量 409，删除后还原"


# ---- 红蓝入库对冲 ----


def _rd_offset_body(run: _Run) -> tuple[str, str]:
    _blue, blue, _red, red = _pair_ins(run, _seed(run))
    psvid = _settle(run, [{"in_line_id": blue, "quantity": 1}, {"in_line_id": red, "quantity": -1}])
    rows, qty, _money, no_bill, no_rd = _state(run, q.settle_state_sql(psvid), q.SETTLE_COLS, "结算单")
    _expect((rows, no_bill, no_rd) == ("2", "2", "0") and _dec(qty) == 0, f"对冲结算单 {rows} 行、没有发票 {no_bill} 行、数量 {qty}")
    _expect(_rd_qty(run, blue) == 1 and _rd_qty(run, red) == -1, "两条入库行的已结算数量应为 1、-1")
    run.settle(1)
    _expect(_rd_qty(run, blue) == 0 and _rd_qty(run, red) == 0, "删除结算单后入库行已结算数量没有回到 0")
    return "PASS", f"入库行 {blue} / {red} 红蓝对冲：结算单 {psvid}，删除后还原"


# ---- 红蓝发票对冲 ----


def _inv_offset_body(run: _Run) -> tuple[str, str]:
    blue_in, blue_rd, red_in, red_rd = _pair_ins(run, _seed(run))
    blue = _invoice(run, blue_in, blue_rd, 1)
    red = _invoice(run, red_in, red_rd, 1)
    psvid = _settle(run, [{"invoice_line_id": blue, "quantity": 1}, {"invoice_line_id": red, "quantity": -1}])
    rows, qty, _money, no_bill, no_rd = _state(run, q.settle_state_sql(psvid), q.SETTLE_COLS, "结算单")
    _expect((rows, no_bill, no_rd) == ("2", "0", "2") and _dec(qty) == 0, f"对冲结算单 {rows} 行、没有入库 {no_rd} 行、数量 {qty}")
    day = run.env.today
    for line, want in ((blue, 1), (red, -1)):
        got = _bill(run, line)
        _expect(got == (want, day, day), f"发票行 {line} 应已结算 {want} 且行、表头结算日期为 {day}，实际 {got}")
    _expect(_rd_qty(run, blue_rd) == 0 and _rd_qty(run, red_rd) == 0, "发票对冲不应改入库行的已结算数量")
    run.settle(1)
    _expect(_bill(run, blue) == (0, "", "") and _bill(run, red) == (0, "", ""), "删除结算单后发票的结算没有清空")
    return "PASS", f"发票行 {blue} / {red} 红蓝对冲：结算单 {psvid}，删除后还原"


# ---- 第一级：非测试账套不再 403 ----


def _tier1_body(run: _Run) -> tuple[str, str]:
    line = {"in_line_id": _PROBE_ID, "invoice_line_id": _PROBE_ID, "quantity": 1}
    status, payload = run.send(_CREATE, _settle_body(run, [line], True))
    message = str(payload.get("message"))
    if status == 403 and payload.get("code") == _TEST_ONLY:
        raise _Fail(f"手工结算是第一级，不应再 403 test_account_only：{message}"[:240])
    if status in (400, 409):
        return "PASS", f"预演手工结算过了分级，到行校验 {status} {payload.get('code')}"
    raise _Fail(f"用不存在的行预演手工结算应 400 / 409，实际 {status} {payload.get('code')} {message}"[:240])


def settle_man_partial(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _partial_body)


def settle_man_rd_offset(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _rd_offset_body)


def settle_man_inv_offset(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _inv_offset_body)


def settle_man_tier1(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _tier1_body)


PU_SETTLE_MAN_OPS = {
    "settle_man_partial": settle_man_partial,
    "settle_man_rd_offset": settle_man_rd_offset,
    "settle_man_inv_offset": settle_man_inv_offset,
    "settle_man_tier1": settle_man_tier1,
}
CASE_NAMES = ("purchase-settle-manual-partial", "purchase-settle-rd-offset", "purchase-settle-inv-offset")
TIER1_CASE = "purchase-settle-manual-tier1"


def pu_settle_man_cases() -> list[CaseSpec]:
    # 结算与自动结算、删除同锁 new:purchase_settle；建入库单、发票（new:/w:），红字入库扣现存量（stock）；
    # 结算月以前各月采购须已结账，与改结账标志的用例互斥（opening:pu）。要自建单据：只在可造数的通道跑。
    # 必有订单时另建采购订单、到货单、采购退货单（到货单与退货单共用编号）。
    tags = frozenset(("new:" + KIND, "w:" + KIND, "new:purchase_in", "w:purchase_in", "new:purchase_invoice",
                      "w:purchase_invoice", "new:purchase_order", "w:purchase_order", "new:arrival", "w:arrival",
                      "new:purchase_return", "stock", "opening:pu"))
    routes = (_CREATE, _GEN, _VERIFY, _DELETE)
    titles = (
        "采购手工结算：入库与发票配对结算部分数量（入库单价不变）、预演、超量 409、删除后还原",
        "采购手工结算：红蓝入库对冲（没有发票行），删除后还原",
        "采购手工结算：红蓝发票对冲（没有入库行），发票写结算日期，删除后清空",
    )
    ops = ("settle_man_partial", "settle_man_rd_offset", "settle_man_inv_offset")
    cases = [
        CaseSpec(name, title, (), (Step("custom", sql_name=op, id_from="none"),), True, optional=True, tags=tags, routes=routes)
        for name, title, op in zip(CASE_NAMES, titles, ops, strict=True)
    ]
    # 预演、行不存在，不写数据：各通道都可跑。
    probe = CaseSpec(TIER1_CASE, "非测试账套预演采购手工结算：第一级，过了分级到行校验 400 / 409（403 FAIL）", (),
                     (Step("custom", sql_name="settle_man_tier1", id_from="none"),), True,
                     tags=frozenset(("probe",)), routes=(_CREATE,))
    return cases + [probe]
