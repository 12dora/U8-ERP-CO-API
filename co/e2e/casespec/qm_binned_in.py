"""参照来料检验单生成带货位的采购入库。

binned-qm-purchase-in-delete：挑一张可入库的来料检验单和一个货位仓库的末级货位（qm_binned_in_sql.qm_bin_pick_sql）→
  生成采购入库 1 个、行带 cposition → 回读表体（1 行、cPosition 是该货位、iCheckIdBaks 指回检验单）、检验单累计入库数 +1、
  InvPosition 行数和货位存量（保存后）→ 审核 → 再回读（审核后）→ 弃审 → 删除（桥先清货位再删）。
  断言同 binned-purchase-in-delete：审核后 InvPosition 1 行、货位存量 +1；保存后已写（1 行、+1）或未写（0 行、不变）
  都接受，结论写进说明；删除后表头和 InvPosition 都没有，货位存量、现存量、检验单累计入库数回到生成前。

purchase-in-gen-position-bad：采购入库生单的货位校验都在调用 U8 之前，只发会被拒的请求：参照来料检验单、
  参照采购订单各三个——进货位仓不带货位（检验单另试不存在的货位）、订单进普通仓却带货位，都是 400 field
  lines.0.cposition；进不存在的仓库 400 field head.cwhcode。意外保存了就删掉并记 FAIL。两种来源都挑不到记 N/A。

挑不到数据记 N/A；清理按建的倒序（_guard），清理失败记 FAIL。
步骤 kind 为 "custom"，sql_name 是操作名（QM_BIN_OPS 并入 ops_run）。
"""

from __future__ import annotations

from decimal import Decimal

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_misc_sql import safe_id
from co.e2e.casespec.stock_plus import _dec, _doc_id, _Fail, _guard, _Run
from co.e2e.casespec.stock_plus_sql import safe_lot
from co.e2e.casespec.stock_binned import _Bin, _saved_problem, _stage_problem
from co.e2e.casespec.qm_binned_in_sql import bin_wh_sql, check_sum_sql, line_pos_sql, qm_bin_pick_sql
from co.e2e.casespec.dry_ops import send
from co.e2e.casespec.flow_pick_sql import po_recv_sql

_GEN = "/v1/vouchers/generate"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_MEMO = "CO桥回归"


def _fsum(run: _Run, check: str) -> Decimal:
    return _dec(run.cell(check_sum_sql(check)))


def _line_problem(got: str, pos: str, check: str) -> str:
    want = f"1|{pos}|{check}"
    if got != want:
        return f"表体应为 1 行、货位 {pos}、检验单 {check}（{want}），实际 {got or '空'}"
    return ""


def _fsum_problem(label: str, got: Decimal, want: Decimal) -> str:
    if got != want:
        return f"{label}检验单累计入库数应为 {want}，实际 {got}"
    return ""


def _generate(run: _Run, bin_: _Bin, check: str) -> None:
    head = {"cwhcode": bin_.wh, "ddate": run.env.today, "cmemo": _MEMO}
    line = {"source_line_id": int(check), "quantity": 1, "cposition": bin_.pos}
    body = {"type": "purchase_in", "id": int(check), "source_type": "qm_incoming_check", "head": head, "lines": [line]}
    bin_.doc = run.made(_GEN, body, "参照来料检验单生成采购入库")
    run.drop("purchase_in", bin_.doc)


def _body(run: _Run) -> tuple[str, str]:
    parts = run.pick(qm_bin_pick_sql(), 4)
    if parts is None:
        return "N/A", "测试账套挑不到可入库的来料检验单（不管批次的存货）或货位仓库的末级货位"
    check = safe_id(parts[0])
    bin_ = _Bin(run, "01", parts[1:4])
    fsum = _fsum(run, check)
    _generate(run, bin_, check)
    line = run.cell(line_pos_sql(str(bin_.doc)))
    made = _fsum(run, check)
    saved = bin_.probe()
    run.ok(_VERIFY, {"type": "purchase_in", "id": bin_.doc, "action": "verify"}, "审核采购入库")
    run.unverify("purchase_in", bin_.doc)
    verified = bin_.probe()
    # 先弃审、删除（记下的两个清理动作），再判回读，断言失败也不会把单据留在库里。
    run.settle(2)
    problem = (
        _line_problem(line, bin_.pos, check)
        or _fsum_problem("生成后", made, fsum + 1)
        or _saved_problem(bin_, saved)
        or _stage_problem("审核后", verified, bin_.psum + 1)
        or bin_.gone_problem()
        or _fsum_problem("删除后", _fsum(run, check), fsum)
    )
    if problem:
        raise _Fail(problem)
    when = "保存时" if saved[0] == "1" else "审核时"
    return "PASS", f"检验单 {check} → 采购入库 {bin_.where()}：行上货位已写，InvPosition 由 U8 {when}写，删除后全部回退"


# ---- 货位校验的 400 ----

_POS = "lines.0.cposition"
_WH = "head.cwhcode"


def _expect_400(run: _Run, check: tuple[str, dict, str]) -> str:
    what, body, field = check
    # dry_ops.send 在错误里保留 field（steps.invoke 只留 code、message）。
    status, payload = send(run.env, _GEN, body)
    if status == 200:
        doc = _doc_id(payload)
        if doc:
            run.drop("purchase_in", doc)
        return f"{what}：应 400，实际保存了 {doc}"
    if status != 400 or payload.get("field") != field:
        got = f"{status} {payload.get('code')} field={payload.get('field')!r} {payload.get('message')}"
        return f"{what}：应 400 field={field}，实际 {got}"
    return ""


def _qm_checks(qm: list[str] | None, nowh: str) -> list[tuple[str, dict, str]]:
    if qm is None:
        return []
    check, wh = int(safe_id(qm[0])), safe_lot(qm[1])
    line = {"source_line_id": check, "quantity": 1}
    base = {"type": "purchase_in", "id": check, "source_type": "qm_incoming_check"}
    return [
        ("检验单进货位仓不带货位", {**base, "head": {"cwhcode": wh}, "lines": [line]}, _POS),
        ("检验单进货位仓、货位不存在", {**base, "head": {"cwhcode": wh}, "lines": [{**line, "cposition": "ZZNOPOS"}]}, _POS),
        ("检验单进不存在的仓库", {**base, "head": {"cwhcode": nowh}, "lines": [line]}, _WH),
    ]


def _po_checks(po: list[str] | None, bin_: list[str]) -> list[tuple[str, dict, str]]:
    if po is None:
        return []
    poid, line_id, plain = int(safe_id(po[0])), int(safe_id(po[1])), safe_lot(po[2])
    line = {"source_line_id": line_id, "quantity": 1}
    base = {"type": "purchase_in", "id": poid}
    return [
        ("订单进货位仓不带货位", {**base, "head": {"cwhcode": bin_[0]}, "lines": [line]}, _POS),
        ("订单进普通仓却带货位", {**base, "head": {"cwhcode": plain}, "lines": [{**line, "cposition": bin_[1]}]}, _POS),
        ("订单进不存在的仓库", {**base, "head": {"cwhcode": bin_[2]}, "lines": [line]}, _WH),
    ]


def _bad_body(run: _Run) -> tuple[str, str]:
    found = run.pick(bin_wh_sql(), 3)
    if found is None:
        return "N/A", "本账套没有带末级货位的货位仓库"
    bin_ = [safe_lot(part) for part in found]
    qm = run.pick(qm_bin_pick_sql(), 4)
    po = run.pick(po_recv_sql(), 3)
    checks = _qm_checks(qm, bin_[2]) + _po_checks(po, bin_)
    if not checks:
        return "N/A", "测试账套挑不到可入库的来料检验单或采购订单行"
    problems = [problem for problem in (_expect_400(run, check) for check in checks) if problem]
    if problems:
        raise _Fail("；".join(problems)[:240])
    sources = "、".join(name for name, got in (("来料检验单", qm), ("采购订单", po)) if got is not None)
    return "PASS", f"参照{sources}的 {len(checks)} 个请求都按货位规则 400，未调用 U8"


def binned_qm_purchase_in(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _body)


def purchase_in_position_bad(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _bad_body)


QM_BIN_OPS = {"binned_qm_purchase_in": binned_qm_purchase_in, "purchase_in_position_bad": purchase_in_position_bad}


def qm_bin_cases() -> list[CaseSpec]:
    # 动现存量和货位存量（stock），与其他货位用例互斥；回写检验单、到货行、订单行（chain:pu 由 new:purchase_in 推导）。
    step = Step("custom", sql_name="binned_qm_purchase_in", id_from="none")
    title = "参照来料检验单生成带货位的采购入库：表体 cPosition、货位台账写入时点，弃审删除后 InvPosition、货位存量与检验单回退"
    tags = frozenset(("new:purchase_in", "w:purchase_in", "w:qm_incoming_check", "stock"))
    bad = Step("custom", sql_name="purchase_in_position_bad", id_from="none")
    bad_title = "采购入库生单的货位校验：货位仓缺货位、货位不存在、普通仓带货位 400 lines.0.cposition，仓库不存在 400 head.cwhcode"
    return [
        CaseSpec("binned-qm-purchase-in-delete", title, (), (step,), True, tags=tags, routes=(_GEN, _VERIFY, _DELETE)),
        # 只发桥在进 U8 之前就拒绝的请求（probe）；意外保存时 _guard 删掉。
        CaseSpec("purchase-in-gen-position-bad", bad_title, (), (bad,), True, tags=frozenset(("probe",)), routes=(_GEN, _DELETE)),
    ]
