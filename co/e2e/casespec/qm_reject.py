"""来料 / 产品不良品处理单（QM05 / QM06）参照检验单生单、直接审核 / 弃审与删除。

qm-incoming-reject-cycle / qm-product-reject-cycle：挑造数步骤 qmr_* 建的已审核检验单（不良数量 ≥ 1、BREJFLAG=0、
还没有处理单）→ 预演生单 200 dry_run 且不留单据 → 生单 1 行（数量 = 不良数量；QM05 处理方式取流程 0 拒收 Sys01，
QM06 取流程 2 降级 Sys03、cdiminvcode = 检验单存货）→ 回读 QMREJECTVOUCHER：单号前缀 LLBL / CPBL、CHECKID、IVTID 355 / 356、
1 行、数量对，检验单 BREJFLAG=1 → 再生单 409 state_mismatch → 审核、回读审核人 → 弃审、审核人清空 → 删除 →
处理单没有了、BREJFLAG 回到 0。处理单受审批流控制（IsWfControlled=1）时直接审核应 409（请用 workflow/*），
跳过审核、弃审直接删除。中途失败时尽力弃审并删除新单（快照还原兜底）。
qm-reject-bad-request：产品检验单预演生单，降级（流程 2）不给 cdiminvcode → 400 bad_request（预演，不写单据）。

挑不到造数的检验单、账套没有质量管理类不良原因（Reason iReasontype=1；造数步骤 qm_reason 经原因码档案
接口建 01 检测指标不合格）或没有对应流程的处理方式时记 N/A。步骤 kind 为 "custom"，sql_name 是操作名（QM_REJECT_OPS 并入 ops_run）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from co.e2e.casespec.assertutil import first_cell, sql_cells
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.qm_write_sql import tpl_required_sql
from co.e2e.casespec.qm_tpl import split_rows, stuck_text
from co.e2e.casespec.qm_reject_sql import check_pick_sql, dispose_pick_sql, reason_pick_sql, reject_state_sql
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke

GEN = "/v1/vouchers/generate"
VERIFY = "/v1/vouchers/verify"
DELETE = "/v1/vouchers/delete"
_NO_REASON = (
    "账套没有质量管理类不良原因（Reason iReasontype=1）：没有档案接口，造数不写；"
    "先在 U8 基础档案「不良原因」里建 01 检测指标不合格（类型：质量管理）"
)


@dataclass(frozen=True)
class Flow:
    kind: str
    source: str
    biz: str
    check_biz: str
    prefix: str
    ivtid: str
    flow: int
    seed: str


INCOMING = Flow("qm_incoming_reject", "qm_incoming_check", "QM05", "QM03", "LLBL", "355", 0, "qmr_po … qmr_approve")
PRODUCT = Flow("qm_product_reject", "qm_product_check", "QM06", "QM04", "CPBL", "356", 2, "qmr_mo … qmr_papprove")


@dataclass(frozen=True)
class Pick:
    check: int
    qty: int | float
    inv: str
    reason: str
    dispose: str
    # 模板（VT 355 / 356）必输的表头自定义项占位值（qm_tpl），生单时放进 head。
    head: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class State:
    count: int
    flag: str
    doc: str
    code: str
    ivtid: str
    biz: str
    verifier: str
    wf: str
    lines: int
    qty: str


class _Fail(Exception):
    pass


def _send(env: Env, route: str, body: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(body)
    return invoke(env.client, route, fields)


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _number(raw: str) -> int | float | None:
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    if value <= 0:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def pick(env: Env, spec: CaseSpec, flow: Flow) -> tuple[Pick | None, str]:
    """挑造数的检验单、不良原因和处理方式；挑不到时返回 (None, N/A 说明)。"""
    parts = first_cell(run_guarded(env, check_pick_sql(flow.check_biz), spec.name)).split("|")
    qty = _number(parts[1].strip()) if len(parts) == 3 else None
    if qty is None or not parts[0].strip().isdigit() or not parts[2].strip():
        return None, f"挑不到造数的已审核 {flow.check_biz} 检验单（不良数量 ≥ 1、还没有处理单），先跑造数步骤 {flow.seed}"
    reason = first_cell(run_guarded(env, reason_pick_sql(), spec.name)).strip()
    if not reason:
        return None, _NO_REASON
    dispose = first_cell(run_guarded(env, dispose_pick_sql(flow.flow), spec.name)).strip()
    if not dispose:
        return None, f"QMSCRAPDISPOSE 里没有处理流程为 {flow.flow} 的处理方式"
    vt = int(flow.ivtid)
    fill, stuck = split_rows(sql_cells(run_guarded(env, tpl_required_sql(vt), spec.name)), vt, str(env.today or ""))
    if stuck:
        return None, stuck_text(vt, stuck)
    return Pick(int(parts[0]), qty, parts[2].strip(), reason, dispose, tuple(sorted(fill.items()))), ""


def body(flow: Flow, picked: Pick, dry: bool = False, dim: bool = True) -> dict:
    line: dict = {
        "source_line_id": picked.check,
        "quantity": picked.qty,
        "cscrapdiscode": picked.dispose,
        "creasoncode": picked.reason,
    }
    if flow.flow == 2 and dim:
        line["cdiminvcode"] = picked.inv
    made = {"type": flow.kind, "source_type": flow.source, "id": picked.check, "lines": [line]}
    if picked.head:
        made["head"] = dict(picked.head)
    if dry:
        made["dry_run"] = True
    return made


def state(env: Env, spec: CaseSpec, check: int) -> State:
    parts = [part.strip() for part in first_cell(run_guarded(env, reject_state_sql(str(check)), spec.name)).split("|")]
    if len(parts) != 10 or not parts[0].isdigit() or not parts[8].isdigit():
        raise _Fail(f"读不到检验单 {check} 的不良品处理单状态")
    return State(int(parts[0]), parts[1], *parts[2:8], int(parts[8]), parts[9])


def _clean(st: State, when: str) -> None:
    if st.count != 0 or st.flag != "0":
        raise _Fail(f"{when}检验单应没有处理单、BREJFLAG=0，实际 {st.count} 张、BREJFLAG={st.flag}")


def _check_made(flow: Flow, st: State, made: int, picked: Pick) -> None:
    got = (st.count, st.flag, st.doc, st.ivtid, st.biz, st.lines)
    want = (1, "1", str(made), flow.ivtid, flow.biz, 1)
    if got != want:
        raise _Fail(f"生单后（张数、BREJFLAG、ID、IVTID、类型、行数）应为 {want}，实际 {got}")
    if not st.code.startswith(flow.prefix):
        raise _Fail(f"处理单号应以 {flow.prefix} 开头，实际 {st.code or '空'}")
    if Decimal(st.qty) != Decimal(str(picked.qty)):
        raise _Fail(f"处理单表体数量应为 {picked.qty}，实际 {st.qty}")


def _verify(env: Env, flow: Flow, made: int, action: str) -> tuple[int, dict]:
    return _send(env, VERIFY, {"type": flow.kind, "id": made, "action": action})


def _audit(env: Env, spec: CaseSpec, flow: Flow, picked: Pick, made: int) -> str:
    """审核再弃审；受审批流控制时直接审核应 409。返回一句说明。"""
    status, payload = _verify(env, flow, made, "verify")
    after = state(env, spec, picked.check)
    if after.wf == "1":
        if status != 409:
            raise _Fail("处理单受审批流控制，直接审核应 409，实际 " + _brief(status, payload))
        return "受审批流控制，直接审核 409"
    if status != 200:
        raise _Fail("审核应 200，实际 " + _brief(status, payload))
    if not after.verifier:
        raise _Fail("审核后处理单的审核人仍为空")
    status, payload = _verify(env, flow, made, "unverify")
    if status != 200:
        raise _Fail("弃审应 200，实际 " + _brief(status, payload))
    if state(env, spec, picked.check).verifier:
        raise _Fail("弃审后处理单的审核人应清空")
    return "审核、弃审"


def _adopt(env: Env, spec: CaseSpec, picked: Pick, made: list[int]) -> None:
    # 生单没拿到 200（504、超时、客户端异常）时 U8 可能已自行提交：按检验单回读，找到的处理单记进 made 供清理。
    try:
        found = state(env, spec, picked.check)
    except (_Fail, RuntimeError, ValueError):
        return
    if found.count > 0 and found.doc.isdigit() and int(found.doc) not in made:
        made.append(int(found.doc))


def _send_made(env: Env, spec: CaseSpec, flow: Flow, picked: Pick, made: list[int]) -> int:
    try:
        status, payload = _send(env, GEN, body(flow, picked))
    except Exception:
        _adopt(env, spec, picked, made)
        raise
    doc = payload.get("id")
    if status != 200 or type(doc) is not int or doc <= 0:
        _adopt(env, spec, picked, made)
        raise _Fail("生单应 200 并返回新单 id，实际 " + _brief(status, payload))
    made.append(doc)
    return doc


def _generate(env: Env, spec: CaseSpec, flow: Flow, picked: Pick, made: list[int]) -> int:
    status, payload = _send(env, GEN, body(flow, picked, dry=True))
    if status != 200 or payload.get("dry_run") is not True:
        raise _Fail("预演生单应 200 dry_run=true，实际 " + _brief(status, payload))
    _clean(state(env, spec, picked.check), "预演后")
    doc = _send_made(env, spec, flow, picked, made)
    _check_made(flow, state(env, spec, picked.check), doc, picked)
    status, payload = _send(env, GEN, body(flow, picked))
    if status != 409 or payload.get("code") != "state_mismatch":
        raise _Fail("已生成处理单的检验单再生单应 409 state_mismatch，实际 " + _brief(status, payload))
    return doc


def _cycle(env: Env, spec: CaseSpec, flow: Flow, picked: Pick, made: list[int]) -> str:
    _clean(state(env, spec, picked.check), "生单前")
    doc = _generate(env, spec, flow, picked, made)
    audited = _audit(env, spec, flow, picked, doc)
    status, payload = _send(env, DELETE, {"type": flow.kind, "id": doc})
    if status != 200 or payload.get("deleted") is not True:
        raise _Fail("删除应 200 deleted=true，实际 " + _brief(status, payload))
    made.remove(doc)
    _clean(state(env, spec, picked.check), "删除后")
    return f"检验单 {picked.check} → {flow.biz} {doc}：预演、生单、重复生单 409、{audited}、删除，BREJFLAG 回到 0"


def _drop(env: Env, flow: Flow, doc: int) -> str:
    try:
        _verify(env, flow, doc, "unverify")
        status, _payload = _send(env, DELETE, {"type": flow.kind, "id": doc})
    except Exception as exc:  # 清理尽力而为，任何异常都只记一句
        return f"{flow.biz} {doc} 清理出错：{str(exc)[:60]}，留给快照还原"
    return f"{flow.biz} {doc} " + ("已清理" if status in (200, 404) else f"删除返回 {status}，留给快照还原")


def _cleanup(env: Env, flow: Flow, made: list[int]) -> str:
    # 尽力而为：弃审（失败不管）再删除；失败不改判定（快照还原兜底）。
    return "；".join(_drop(env, flow, doc) for doc in made)


def run_cycle(env: Env, spec: CaseSpec, flow: Flow) -> tuple[str, str]:
    picked, why = pick(env, spec, flow)
    if picked is None:
        return "N/A", why
    made: list[int] = []
    try:
        return "PASS", _cycle(env, spec, flow, picked, made)
    except Exception as exc:
        # 断言失败、回读 SQL 失败、中断、网络超时等任何异常，都先尽力清掉新单再记 FAIL。
        tail = _cleanup(env, flow, made)
        return "FAIL", (str(exc) + ("；" + tail if tail else ""))[:240]


def qm_incoming_reject_cycle(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return run_cycle(env, spec, INCOMING)


def qm_product_reject_cycle(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return run_cycle(env, spec, PRODUCT)


def qm_reject_bad_request(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    picked, why = pick(env, spec, PRODUCT)
    if picked is None:
        return "N/A", why
    status, payload = _send(env, GEN, body(PRODUCT, picked, dry=True, dim=False))
    if status == 400 and payload.get("code") == "bad_request" and "cdiminvcode" in str(payload.get("message")):
        return "PASS", f"降级处理方式 {picked.dispose} 不给 cdiminvcode：400 bad_request"
    return "FAIL", "降级不给 cdiminvcode 应 400 bad_request（消息提到 cdiminvcode），实际 " + _brief(status, payload)


QM_REJECT_OPS = {
    "qm_incoming_reject_cycle": qm_incoming_reject_cycle,
    "qm_product_reject_cycle": qm_product_reject_cycle,
    "qm_reject_bad_request": qm_reject_bad_request,
}


def _case(name: str, title: str, op: str, tags: frozenset[str], routes: tuple[str, ...]) -> CaseSpec:
    step = Step("custom", sql_name=op, id_from="none")
    return CaseSpec(name, title, (), (step,), True, optional=True, tags=tags, routes=routes)


def qm_reject_cases() -> list[CaseSpec]:
    # 生单与桥 DocLocks 的 new:<类型> 同名；生单、删除回写检验单 BREJFLAG（w:检验单）。预演的 400 也挑同一张产品检验单，
    # 带 new:qm_product_reject 与产品一侧的整轮互斥，免得挑中时检验单正好 BREJFLAG=1。
    incoming = frozenset(("new:qm_incoming_reject", "w:qm_incoming_reject", "w:qm_incoming_check"))
    product = frozenset(("new:qm_product_reject", "w:qm_product_reject", "w:qm_product_check"))
    cycle = (GEN, VERIFY, DELETE)
    return [
        _case("qm-incoming-reject-cycle", "来料不良品处理单参照来料检验单：预演、生单、重复 409、审核弃审、删除，BREJFLAG 回写",
              "qm_incoming_reject_cycle", incoming, cycle),
        _case("qm-product-reject-cycle", "产品不良品处理单参照产品检验单（降级）：预演、生单、重复 409、审核弃审、删除，BREJFLAG 回写",
              "qm_product_reject_cycle", product, cycle),
        _case("qm-reject-bad-request", "产品不良品处理单降级不给 cdiminvcode：预演 400 bad_request",
              "qm_reject_bad_request", frozenset(("new:qm_product_reject",)), (GEN,)),
    ]
