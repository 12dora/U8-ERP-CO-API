"""采购入库参照到货单用例（vouchers/generate，type=purchase_in，source_type=arrival），全程自建自删。

purchase-in-from-arrival：采购订单 2 个 → 审核 → 参照订单生成到货单 2 个 → 审核 → 参照到货单生成采购入库 3 个 409（超过可生单
  数量）→ 参照到货单生成采购入库 1 个（带 lines）→ 入库单来源「采购到货单」、ipurarriveid 是到货单、一行指向到货行、数量 1，
  到货行 fValidInQuan 加 1 → 审核 → 弃审 → 删除 → fValidInQuan 回到 0 → 不带 lines 整单生成 → 数量 2、fValidInQuan 为 2 →
  删除 → 回到 0。清理按建的倒序（弃审删到货单、订单）。挑单同采购手工结算（不批次、不保质期、不需来料检验的外购存货，
  pu_settle_manual_sql.pick_sql），挑不到记 N/A。账套「普通业务必有订单」开或关都能跑（入库都参照到货单）。
步骤 kind 为 "custom"，sql_name 是操作名（PU_ARR_IN_OPS 并入 ops_run）。
"""

from __future__ import annotations

from co.e2e.casespec import pu_arrival_in_sql as q
from co.e2e.casespec import pu_settle_manual_sql as sm_sql
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _dec, _Fail, _guard, _NotApplicable, _Run
from co.e2e.casespec.stock_plus_sql import havepo_sql
from co.e2e.casespec.pu_settle_manual import _gen_body, _line_id, _made_verified, _order, _Seed

CASE_NAME = "purchase-in-from-arrival"
_GEN = "/v1/vouchers/generate"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_SOURCE = "采购到货单"


def _seed(run: _Run) -> _Seed:
    parts = run.pick(sm_sql.pick_sql(), 5)
    if parts is None:
        raise _NotApplicable("账套里挑不到不批次、不保质期、不需来料检验的外购存货")
    return _Seed(parts, run.cell(havepo_sql()) == "1")


def _expect(ok: bool, message: str) -> None:
    if not ok:
        raise _Fail(message)


def _want_valid(run: _Run, line: int, want: str, when: str) -> None:
    got = str(_dec(run.cell(q.arr_valid_sql(line))).normalize())
    _expect(got == want, f"{when}到货行累计入库 fValidInQuan 应为 {want}，实际 {got}")


def _arr_body(arr: int, wh: str, lines: list[dict] | None) -> dict:
    body: dict = {"type": "purchase_in", "id": arr, "source_type": "arrival", "head": {"cWhCode": wh}}
    if lines is not None:
        body["lines"] = lines
    return body


def _made_in(run: _Run, body: dict, what: str) -> int:
    payload = run.ok(_GEN, body, what)
    pin = payload.get("id")
    if type(pin) is not int or pin <= 0:
        raise _Fail(what + "：响应没有新单据 id")
    run.drop("purchase_in", pin)
    _expect(payload.get("source_type") == "arrival", f"{what}：响应的 source_type 是 {payload.get('source_type')}")
    return pin


def _check_link(run: _Run, pin: int, arr: int, line: int, qty: str) -> None:
    parts = run.pick(q.rd_link_sql(pin, line), q.LINK_COLS)
    _expect(parts is not None, f"回读不到采购入库单 {pin}")
    source, arrive, rows, total = parts or ["", "", "", ""]
    got = (source, arrive, rows, str(_dec(total).normalize()))
    _expect(got == (_SOURCE, str(arr), "1", qty), f"入库单 {pin}：来源|到货单|行数|数量 = {'|'.join(got)}，应为 {_SOURCE}|{arr}|1|{qty}")


def _partial(run: _Run, arr: int, line: int, wh: str) -> int:
    over = _arr_body(arr, wh, [{"source_line_id": line, "quantity": 3}])
    status, payload = run.send(_GEN, over)
    _expect(status == 409, f"超过到货数量应 409，实际 {status} {payload.get('code')} {payload.get('message')}")
    pin = _made_in(run, _arr_body(arr, wh, [{"source_line_id": line, "quantity": 1}]), "参照到货单生成采购入库 1 个")
    _check_link(run, pin, arr, line, "1")
    _want_valid(run, line, "1", "生成入库单后")
    run.ok(_VERIFY, {"type": "purchase_in", "id": pin, "action": "verify"}, "审核采购入库单")
    run.unverify("purchase_in", pin)
    run.settle(2)  # 弃审、删除
    _want_valid(run, line, "0", "删除入库单后")
    return pin


def _whole(run: _Run, arr: int, line: int, wh: str) -> int:
    pin = _made_in(run, _arr_body(arr, wh, None), "参照到货单整单生成采购入库")
    _check_link(run, pin, arr, line, "2")
    _want_valid(run, line, "2", "整单生成后")
    run.settle(1)
    _want_valid(run, line, "0", "删除整单生成的入库单后")
    return pin


def _body(run: _Run) -> tuple[str, str]:
    seed = _seed(run)
    po, po_line = _order(run, seed, 2)
    arr = _made_verified(run, _GEN, _gen_body("arrival", po, po_line, 2, {"cWhCode": seed.wh}), "参照采购订单生成到货单")
    line = _line_id(run.cell(sm_sql.arrival_line_sql(arr)), "到货单行")
    _want_valid(run, line, "0", "新到货单的")
    first = _partial(run, arr, line, seed.wh)
    second = _whole(run, arr, line, seed.wh)
    return "PASS", f"到货单 {arr}（行 {line}）→ 入库单 {first}（1 个，审核、弃审、删除）、{second}（整单 2 个），超量 409，回写归零"


def pu_arr_in(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _body)


PU_ARR_IN_OPS = {"pu_arr_in": pu_arr_in}


def pu_arr_in_cases() -> list[CaseSpec]:
    # 建采购订单、到货单（与采购退货单共用编号）、采购入库单；入库审核动现存量（stock）。
    tags = frozenset(("new:purchase_order", "w:purchase_order", "new:arrival", "w:arrival", "new:purchase_return",
                      "new:purchase_in", "w:purchase_in", "stock"))
    title = "采购入库参照到货单：部分数量（审核、弃审、删除）与整单生成，超量 409，到货行累计入库回写与归零"
    step = Step("custom", sql_name="pu_arr_in", id_from="none")
    return [CaseSpec(CASE_NAME, title, (), (step,), True, optional=True, tags=tags, routes=(_GEN, _VERIFY, _DELETE))]
