"""红冲蓝字销售发票（vouchers/generate type=sale_invoice、source_type=sale_invoice，id 是蓝字发票 SBVID）。

sale-red-from-blue：挑一个不管货位的仓库里可用量 >= 4 的可销售简单存货，自建 1 行销售订单 3 个并审核 → 参照生成发货单
  3 个并审核 → 参照发货单生成蓝字发票 3 个 → 未复核时红冲 409 → 复核蓝字发票 → 红冲 4 个（超过 3）409 → 红冲 1 个 →
  红字发票 bReturnFlag=1、类型同蓝字、数量合计 −1、1 行且 iSBVID 指向蓝字行 → 再红冲 3 个（只剩 2）409 →
  红字日期早于蓝字 400 → 已被红冲的蓝字发票弃复、删除 409 →
  复核、弃复红字发票 → 删除红字发票、回读已删 → 弃复并删除蓝字发票 → 弃审删除发货单、订单。
蓝字发票复核又弃复后 U8 不回退发货行的已复核开票数量，删除可能 409（docs/co/e2e/README.md「已知的还原差异」 invoice-generate 同一例外）：
  这时蓝字发票、发货单、订单留给快照还原，结果仍记 PASS 并在说明里写明。挑不到数据记 N/A。
桥对发货行、订单行累计开票的回写只记审计（「红冲回写」），本用例不断言，第一次跑后看审计。
步骤 kind 为 "custom"，sql_name 是操作名（BLUE_RED_OPS 并入 ops_run）。
"""

from __future__ import annotations

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _SALE_TAGS, _dispatch, _Fail, _guard, _Run, _sale_order
from co.e2e.casespec.stock_plus_sql import safe_lot
from co.e2e.casespec.sale_blue_red_sql import blue_line_sql, inv_gone_sql, red_shape_sql
from co.e2e.casespec.dispatch_add_sql import pick_sql

_GEN = "/v1/vouchers/generate"
_CREATE = "/v1/vouchers/create"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_QTY = 3
_RED = 1
# 桥拒绝弃复、删除已被红冲的蓝字发票时的说明（SaleGenRedDel.RefuseRedLinked）。
_HELD = "蓝字发票已被红冲"


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _red_body(inv: int, line: int, qty: int) -> dict:
    lines = [{"source_line_id": line, "quantity": qty}]
    return {"type": "sale_invoice", "id": inv, "source_type": "sale_invoice", "lines": lines}


def _refused(run: _Run, inv: int, line: int, qty: int, what: str) -> None:
    status, payload = run.send(_GEN, _red_body(inv, line, qty))
    if status != 409:
        raise _Fail(f"{what}应 409，实际 {_brief(status, payload)}")


def _blue_held(run: _Run, inv: int, line: int) -> None:
    """红字发票还在时：日期早于蓝字的红冲 400，蓝字发票弃复 409「蓝字发票已被红冲」；删除 409（蓝字仍是复核状态，桥先报「单据已复核」）。"""
    early = dict(_red_body(inv, line, _RED), head={"ddate": "2000-01-01"})
    checks = (
        (_GEN, early, 400, "", "红字发票日期早于蓝字发票"),
        (_VERIFY, {"type": "sale_invoice", "id": inv, "action": "unverify"}, 409, _HELD, "弃复已被红冲的蓝字发票"),
        (_DELETE, {"type": "sale_invoice", "id": inv}, 409, _HELD + "|单据已复核", "删除已被红冲的蓝字发票"),
    )
    for route, body, want, text, what in checks:
        status, payload = run.send(route, body)
        message = str(payload.get("message") or "")
        if status != want or not any(part in message for part in text.split("|")):
            need = f"应 {want}" + (f"「{text}」" if text else "")
            raise _Fail(f"{what}{need}，实际 {_brief(status, payload)}")


def _blue(run: _Run, dl: int, dline: str) -> tuple[int, int, str]:
    body = {"type": "sale_invoice", "id": dl, "lines": [{"source_line_id": int(dline), "quantity": _QTY}]}
    inv = run.made(_GEN, body, "参照发货单生成蓝字发票")
    run.drop("sale_invoice", inv)
    parts = run.cell(blue_line_sql(str(inv))).split("|")
    if len(parts) != 2 or not parts[0].isdigit():
        raise _Fail("蓝字发票没有明细行")
    return inv, int(parts[0]), parts[1]


def _red_cycle(run: _Run, red: int) -> None:
    """复核、弃复红字发票，再删除并回读已删。"""
    run.ok(_VERIFY, {"type": "sale_invoice", "id": red, "action": "verify"}, "复核红字发票")
    run.unverify("sale_invoice", red)
    run.settle(2)
    if run.cell(inv_gone_sql(str(red))) != "0":
        raise _Fail(f"删除后红字发票 {red} 仍在")


def _finish(run: _Run, inv: int, note: str) -> tuple[str, str]:
    """弃复并删除蓝字发票；删除 409 时连同发货单、订单留给快照还原。"""
    run.settle(1)
    route, extra, what = run.undo.pop()
    status, payload = run.send(route, extra)
    if status == 200:
        return "PASS", note + "；蓝字发票、发货单、订单已清理"
    if status == 409:
        run.undo.clear()
        return "PASS", note + f"；蓝字发票 {inv} 弃复后删除 {_brief(status, payload)}，连同发货单、订单留给快照还原"
    raise _Fail(f"{what} {inv}：{_brief(status, payload)}")


def _body(run: _Run) -> tuple[str, str]:
    parts = run.pick(pick_sql(), 6)
    if parts is None:
        return "N/A", "账套挑不到不管货位的仓库里可用量 >= 4 的可销售简单存货"
    so = _sale_order(run, parts, _QTY)
    dl, dline = _dispatch(run, so, safe_lot(parts[5]), _QTY, "")
    inv, line, vouch = _blue(run, dl, dline)
    _refused(run, inv, line, _RED, "蓝字发票未复核时红冲")
    run.ok(_VERIFY, {"type": "sale_invoice", "id": inv, "action": "verify"}, "复核蓝字发票")
    run.unverify("sale_invoice", inv)
    _refused(run, inv, line, _QTY + 1, f"红冲 {_QTY + 1} 个（蓝字 {_QTY} 个）")
    red = run.made(_GEN, _red_body(inv, line, _RED), f"红冲蓝字发票 {_RED} 个")
    run.drop("sale_invoice", red)
    shape = run.cell(red_shape_sql(str(red), str(line)))
    want = f"1|{vouch}|-{_RED}.0000|1|1"
    if shape != want:
        raise _Fail(f"红字发票 {red} 应为 红字|类型|数量|行数|关联蓝字行 = {want}，实际 {shape or '无'}")
    _refused(run, inv, line, _QTY, f"再红冲 {_QTY} 个（只剩 {_QTY - _RED}）")
    _blue_held(run, inv, line)
    _red_cycle(run, red)
    return _finish(run, inv, f"蓝字发票 {inv} 行 {line} 红冲 {_RED} 个 → 红字发票 {red}，超量 409，复核、弃复、删除通过")


def sale_red_from_blue(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _body)


BLUE_RED_OPS = {"sale_red_from_blue": sale_red_from_blue}
_TAGS = _SALE_TAGS | frozenset(("new:sale_invoice", "w:sale_invoice"))
_ROUTES = (_CREATE, _VERIFY, _GEN, _DELETE)


def blue_red_cases() -> list[CaseSpec]:
    step = Step("custom", sql_name="sale_red_from_blue", id_from="none")
    title = "红冲蓝字销售发票：部分红冲、超量 409、红字行关联蓝字行，复核弃复后删除；自建订单、发货单、蓝字发票并清理"
    return [CaseSpec("sale-red-from-blue", title, (), (step,), True, tags=_TAGS, routes=_ROUTES)]
