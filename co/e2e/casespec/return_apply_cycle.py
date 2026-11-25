"""退货申请单写入：新增 → 修改 → 审核 → 参照它生成退货单 → 删除退货单 → 弃审 → 删除申请单。

sale-return-apply-cycle：挑一条已审核蓝字发货单的行（可申请、未开票可退都 >= 2），参照它新增退货申请单 2 个 →
  申请行数量 −2、挂着原发货行 → 修改成 1 个（op=update）→ 数量 −1 → 审核 → 参照申请单生成退货单 1 个 →
  退货行 irtnappid / crtnappcode 指回申请单、申请行 fretqty −1 → 该退货行改数量、删行都 409（irtnappid > 0）→
  有退货单时弃审申请单 409 → 删除退货单 → fretqty 回到 0 →
  弃审、删除申请单 → 发货行累计退货、未开票 / 已开票退货和订单行退货、累计发货数量都回到开始时的值。
清理按建的倒序（stock_plus._guard）：删退货单 → 弃审、删申请单；清理失败记 FAIL。挑不到数据记 N/A。
步骤 kind 为 "custom"，sql_name 是操作名（并入 return_apply.RETURN_APPLY_OPS）。
桥的销售 CO VT 34 新增、修改、审核与退货单的申请单关联、fretqty 回写都按推断写，第一次跑失败时先看审计 detail。
"""

from __future__ import annotations

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _Fail, _guard, _Run
from co.e2e.casespec.return_apply_cycle_sql import apply_lines_sql, cum_sql, link_sql, pick_sql, ret_line_sql

KIND = "sale_return_apply"
_CREATE = "/v1/vouchers/create"
_UPDATE = "/v1/vouchers/update"
_VERIFY = "/v1/vouchers/verify"
_GEN = "/v1/vouchers/generate"
_DELETE = "/v1/vouchers/delete"
_MEMO = "CO桥回归"
_TAGS = frozenset(
    (
        "new:" + KIND, "w:" + KIND, "new:sale_return", "w:sale_return", "new:dispatch", "w:dispatch",
        "chain:sale",
    )
)


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _line(run: _Run, app: int) -> list[str]:
    rows = [row.split("|") for row in run.cells(apply_lines_sql(str(app)))]
    if len(rows) != 1 or len(rows[0]) != 4:
        raise _Fail(f"退货申请单 {app} 应有 1 行，实际 {len(rows)} 行")
    return rows[0]


def _expect(run: _Run, app: int, qty: str, fret: str) -> str:
    row = _line(run, app)
    if row[1] != qty or row[2] != fret:
        raise _Fail(f"退货申请单 {app} 行数量 / fretqty 应为 {qty} / {fret}，实际 {row[1]} / {row[2]}")
    return row[0]


def _create(run: _Run, dl_line: str) -> tuple[int, str]:
    line = {"source_line_id": int(dl_line), "quantity": 2, "cMemo": _MEMO}
    app = run.made(_CREATE, {"type": KIND, "head": {"cMemo": _MEMO}, "lines": [line]}, "参照发货单行新增退货申请单")
    run.drop(KIND, app)
    row = _line(run, app)
    if row[1] != "-2.0000" or row[3] != dl_line:
        raise _Fail(f"新增的申请行应为 −2、挂发货行 {dl_line}，实际 {row[1]}、{row[3]}")
    return app, row[0]


def _ret_line_locked(run: _Run, ret: int, app: int) -> None:
    """挂着申请单行（irtnappid > 0）的退货行：改数量、删行都 409（申请行 fretqty 回写未实测，桥先拒绝）。"""
    line = run.cell(ret_line_sql(str(ret), str(app)))
    if not line.isdigit():
        raise _Fail(f"退货单 {ret} 读不到指回申请单 {app} 的行")
    edits = (
        ({"op": "update", "line_id": int(line), "iQuantity": 0.5}, "改数量"),
        ({"op": "delete", "line_id": int(line)}, "删行"),
    )
    for edit, what in edits:
        status, payload = run.send(_UPDATE, {"type": "sale_return", "id": ret, "lines": [edit]})
        if status != 409 or "退货申请单" not in str(payload.get("message")):
            raise _Fail(f"参照申请单生成的退货行{what}应 409（退货申请单关联），实际 {_brief(status, payload)}")


def _return(run: _Run, app: int, auto: str) -> int:
    body = {"type": "sale_return", "id": app, "source_type": KIND, "lines": [{"source_line_id": int(auto), "quantity": 1}]}
    ret = run.made(_GEN, body, "参照退货申请单生成退货单")
    run.drop("sale_return", ret)
    if run.cell(link_sql(str(ret), str(app))) != "1":
        raise _Fail(f"退货单 {ret} 没有一行指回退货申请单 {app}（irtnappid / crtnappcode）")
    _expect(run, app, "-1.0000", "-1.0000")
    _ret_line_locked(run, ret, app)
    _expect(run, app, "-1.0000", "-1.0000")
    status, payload = run.send(_VERIFY, {"type": KIND, "id": app, "action": "unverify"})
    if status != 409:
        raise _Fail(f"有退货单时弃审退货申请单应 409，实际 {_brief(status, payload)}")
    return ret


def _cycle_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(pick_sql(), 3)
    if parts is None:
        return "N/A", "账套挑不到已审核、可申请和未开票可退数量都 >= 2 的蓝字发货单行"
    dl_line = parts[1]
    base = run.cell(cum_sql(dl_line))
    app, auto = _create(run, dl_line)
    run.ok(_UPDATE, {"type": KIND, "id": app, "lines": [{"op": "update", "line_id": int(auto), "iQuantity": 1}]},
           "修改退货申请单数量")
    _expect(run, app, "-1.0000", "0.0000")
    run.ok(_VERIFY, {"type": KIND, "id": app, "action": "verify"}, "审核退货申请单")
    run.unverify(KIND, app)
    ret = _return(run, app, auto)
    run.settle(1)
    _expect(run, app, "-1.0000", "0.0000")
    run.settle(2)
    after = run.cell(cum_sql(dl_line))
    if after != base:
        raise _Fail(f"发货行 {dl_line} 与订单行的累计列应回到 {base}，实际 {after}")
    return "PASS", (
        f"发货行 {dl_line} → 退货申请单 {app}（2 改 1、审核）→ 退货单 {ret} 指回申请单、fretqty −1，改数量、删行 409；"
        f"删除、弃审、删除后累计列回到 {base}"
    )


def return_apply_cycle(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _cycle_body)


CYCLE_OPS = {"return_apply_cycle": return_apply_cycle}


def return_apply_cycle_cases() -> list[CaseSpec]:
    step = Step("custom", sql_name="return_apply_cycle", id_from="none")
    routes = (_CREATE, _UPDATE, _VERIFY, _GEN, _DELETE)
    title = "退货申请单参照发货单行新增、修改、审核，参照它生成退货单，删除后发货行、订单行累计还原"
    return [CaseSpec("sale-return-apply-cycle", title, (), (step,), True, tags=_TAGS, routes=routes)]
