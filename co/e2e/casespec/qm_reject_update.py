"""不良品处理单修改（vouchers/update，VO 的 UpdateVoucher，U8 自己提交）。

qm-reject-update：照 qm-product-reject-cycle（qm_reject）挑造数的产品检验单、生单 1 行（降级）→ 预演修改 200 dry_run、单据不变
→ 修改：表头 cDefine11，第一行数量减半、另加一行（同一不良原因，处理流程 0 的处理方式，数量为另一半，合计不变）→ 回读 UFTS 变了、
cDefine11、修改人是本操作员、2 行且不良原因 + 处理方式不重复、合计不变 → vouchers/load 读到 2 行 → 未受审批流控制时：审核 →
再修改 409 state_mismatch、单据不变 → 弃审 → 删除，检验单 BREJFLAG 回到 0。受审批流控制时跳过审核那一段（直接审核本就 409）。

挑不到造数的检验单、原因或处理方式时记 N/A（同 qm_reject）。中途失败时尽力弃审并删除新单（快照还原兜底）。
步骤 kind 为 "custom"，sql_name 是操作名（QM_REJECT_UPDATE_OPS 并入 ops_run）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.qm_reject_update_sql import edit_state_sql, first_line_sql
from co.e2e.casespec.qm_reject import (
    DELETE,
    GEN,
    PRODUCT,
    VERIFY,
    Flow,
    Pick,
    _brief,
    _cleanup,
    _clean,
    _Fail,
    _send,
    _send_made,
    pick,
    state,
)
from co.e2e.casespec.qm_reject import body as gen_body
from co.e2e.casespec.qm_reject_sql import dispose_pick_sql
from co.e2e.casespec.sqlexec import run_guarded

UPDATE = "/v1/vouchers/update"
LOAD = "/v1/vouchers/load"
_MARK = "DEMO-001"


@dataclass
class Run:
    env: Env
    spec: CaseSpec
    flow: Flow
    picked: Pick
    other: str  # 拆出的那一行用的处理方式（另一种处理流程）
    made: list[int] = field(default_factory=list)


def _cell(run: Run, query: str) -> str:
    return first_cell(run_guarded(run.env, query, run.spec.name)).strip()


def _edit_state(run: Run, doc: int) -> list[str]:
    parts = [part.strip() for part in _cell(run, edit_state_sql(str(doc))).split("|")]
    if len(parts) != 7:
        raise _Fail(f"读不到处理单 {doc} 的修改状态")
    return parts


def _split(run: Run, doc: int) -> tuple[dict, Decimal]:
    parts = _cell(run, first_line_sql(str(doc))).split("|")
    if len(parts) != 2 or not parts[0].strip().isdigit():
        raise _Fail(f"读不到处理单 {doc} 的表体行")
    total = Decimal(parts[1].strip())
    half = (total / 2).quantize(Decimal("0.0001"))
    added: dict = {"op": "add", "quantity": float(half), "cScrapDisCode": run.other, "cReasonCode": run.picked.reason}
    if run.flow.flow != 2:
        added["cDimInvCode"] = run.picked.inv  # 另一行是降级（流程 2）时须有处理后存货
    lines = [{"op": "update", "line_id": int(parts[0]), "quantity": float(total - half)}, added]
    return {"type": run.flow.kind, "id": doc, "head": {"cDefine11": _MARK}, "lines": lines}, total


def _dry(run: Run, body: dict, doc: int) -> None:
    before = _edit_state(run, doc)
    status, payload = _send(run.env, UPDATE, dict(body, dry_run=True))
    if status != 200 or payload.get("dry_run") is not True:
        raise _Fail("预演修改应 200 dry_run=true，实际 " + _brief(status, payload))
    if _edit_state(run, doc) != before:
        raise _Fail("预演修改后处理单变了")


def _edited(run: Run, doc: int, before: list[str], total: Decimal) -> None:
    after = _edit_state(run, doc)
    if after[0] == before[0]:
        raise _Fail("修改后 UFTS 没变")
    want = (_MARK, "2", "2")
    got = (after[1], after[4], after[5])
    if got != want or Decimal(after[6]) != total:
        raise _Fail(f"修改后（cDefine11、行数、不同处理数）应为 {want}、合计 {total}，实际 {got}、合计 {after[6]}")
    if not after[2] or after[3]:
        raise _Fail(f"修改后应有修改人、仍未审核，实际修改人「{after[2]}」、审核人「{after[3]}」")


def _load(run: Run, doc: int) -> None:
    status, payload = _send(run.env, LOAD, {"type": run.flow.kind, "id": doc})
    lines = payload.get("lines")
    if status != 200 or not isinstance(lines, list) or len(lines) != 2:
        raise _Fail("修改后读取应 200 且 2 行，实际 " + _brief(status, payload))


def _verify(run: Run, doc: int, action: str) -> tuple[int, dict]:
    return _send(run.env, VERIFY, {"type": run.flow.kind, "id": doc, "action": action})


def _verified_409(run: Run, doc: int, head: dict) -> str:
    status, payload = _verify(run, doc, "verify")
    if state(run.env, run.spec, run.picked.check).wf == "1":
        if status != 409:
            raise _Fail("处理单受审批流控制，直接审核应 409，实际 " + _brief(status, payload))
        return "受审批流控制，跳过已审核修改"
    if status != 200:
        raise _Fail("审核应 200，实际 " + _brief(status, payload))
    before = _edit_state(run, doc)
    status, payload = _send(run.env, UPDATE, {"type": run.flow.kind, "id": doc, "head": head})
    if status != 409 or payload.get("code") != "state_mismatch":
        raise _Fail("已审核的处理单修改应 409 state_mismatch，实际 " + _brief(status, payload))
    if _edit_state(run, doc) != before:
        raise _Fail("已审核修改被拒后处理单变了")
    status, payload = _verify(run, doc, "unverify")
    if status != 200:
        raise _Fail("弃审应 200，实际 " + _brief(status, payload))
    return "已审核修改 409、弃审"


def _cycle(run: Run) -> str:
    env, spec, flow, picked = run.env, run.spec, run.flow, run.picked
    _clean(state(env, spec, picked.check), "生单前")
    status, payload = _send(env, GEN, gen_body(flow, picked, dry=True))
    if status != 200:
        raise _Fail("预演生单应 200，实际 " + _brief(status, payload))
    doc = _send_made(env, spec, flow, picked, run.made)
    body, total = _split(run, doc)
    _dry(run, body, doc)
    before = _edit_state(run, doc)
    status, payload = _send(env, UPDATE, body)
    if status != 200 or payload.get("ok") is not True or payload.get("lines") != 2:
        raise _Fail("修改应 200 且 lines=2，实际 " + _brief(status, payload))
    _edited(run, doc, before, total)
    _load(run, doc)
    verified = _verified_409(run, doc, body["head"])
    status, payload = _send(env, DELETE, {"type": flow.kind, "id": doc})
    if status != 200 or payload.get("deleted") is not True:
        raise _Fail("删除应 200 deleted=true，实际 " + _brief(status, payload))
    run.made.remove(doc)
    _clean(state(env, spec, picked.check), "删除后")
    return f"检验单 {picked.check} → {flow.biz} {doc}：预演、修改（cDefine11、拆成 2 行合计 {total}）、读取、{verified}、删除"


def run_update(env: Env, spec: CaseSpec, flow: Flow) -> tuple[str, str]:
    picked, why = pick(env, spec, flow)
    if picked is None:
        return "N/A", why
    other = first_cell(run_guarded(env, dispose_pick_sql(0 if flow.flow == 2 else 2), spec.name)).strip()
    if not other:
        return "N/A", "QMSCRAPDISPOSE 里没有第二种处理流程的处理方式，拆不出两行"
    run = Run(env, spec, flow, picked, other)
    try:
        return "PASS", _cycle(run)
    except Exception as exc:
        # 断言失败、回读 SQL 失败、中断、网络超时等任何异常，都先尽力清掉新单再记 FAIL。
        tail = _cleanup(env, flow, run.made)
        return "FAIL", (str(exc) + ("；" + tail if tail else ""))[:240]


def qm_reject_update(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return run_update(env, spec, PRODUCT)


QM_REJECT_UPDATE_OPS = {"qm_reject_update": qm_reject_update}


def qm_reject_update_cases() -> list[CaseSpec]:
    # 同 qm-product-reject-cycle：生单、删除回写检验单 BREJFLAG，与产品一侧的整轮互斥。
    tags = frozenset(("new:qm_product_reject", "w:qm_product_reject", "w:qm_product_check"))
    step = Step("custom", sql_name="qm_reject_update", id_from="none")
    return [
        CaseSpec(
            "qm-reject-update",
            "产品不良品处理单修改：预演、改表头自定义项、一行拆成两种处理（合计不变）、读取、已审核修改 409、删除",
            (),
            (step,),
            True,
            optional=True,
            tags=tags,
            routes=(GEN, UPDATE, LOAD, VERIFY, DELETE),
        )
    ]
