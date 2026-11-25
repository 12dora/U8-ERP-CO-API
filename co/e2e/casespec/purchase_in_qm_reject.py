"""purchase-in-from-qm-reject：采购入库参照来料不良品处理单（QM05，处理流程 2 降级 / 让步接收）。

从造数建的已审核来料检验单（qmr_*：到货单 → 报检 → 检验，不良数量 ≥ 1，还没有处理单）起：生成处理流程 2 的 QM05（cdiminvcode 取检验单存货）
→ 审核（受审批流控制时处理单不能直接审核，记 N/A 并清理）→ 回读处理单行（处理后数量、BFLAG=0）→ 部分数量 409「须一次入库全部」、
超量 409「超过可生单数量」→ 全部数量生成采购入库 200 → 回读入库行（1 行、数量 = 处理后数量、cSource = 来料不良品处理单、iRejectIds 挂回
处理单行）和处理单行 BFLAG=1 → 再生单 409「已入库完毕」→ 删除采购入库 200，入库行没有了、BFLAG 回到 0 → 弃审并删除处理单，检验单 BREJFLAG 回到 0。
挑不到造数的检验单、不良原因、流程 2 的处理方式、仓库时记 N/A（同 qm_reject）。中途失败时尽力删除新单（先入库单，后处理单；快照还原兜底）。
步骤 kind 为 "custom"，sql_name 是操作名（PURCHASE_IN_QM_REJECT_OPS 并入 ops_run）。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.qm_reject import (
    DELETE,
    GEN,
    INCOMING,
    VERIFY,
    Flow,
    Pick,
    _brief,
    _cleanup,
    _clean,
    _Fail,
    _send,
    _send_made,
    _verify,
    pick,
    state,
)
from co.e2e.casespec.qm_reject_sql import reject_in_state_sql, reject_line_sql, reject_wh_sql
from co.e2e.casespec.sqlexec import run_guarded

_FLOW = replace(INCOMING, flow=2)
_MEMO = "DEMO-001"


class _Na(Exception):
    pass


@dataclass
class Run:
    env: Env
    spec: CaseSpec
    picked: Pick
    wh: str
    reject: list[int] = field(default_factory=list)  # 新建的处理单
    stock_in: list[int] = field(default_factory=list)  # 新建的采购入库


def _cells(run: Run, query: str, want: int) -> list[str]:
    parts = [part.strip() for part in first_cell(run_guarded(run.env, query, run.spec.name)).split("|")]
    if len(parts) != want:
        raise _Fail("回读不到预期的列：" + query.splitlines()[1][:60])
    return parts


def _body(doc: int, line: int, qty: Decimal | str, run: Run) -> dict:
    head = {"cWhCode": run.wh, "dDate": run.env.today, "cMemo": _MEMO}
    return {
        "type": "purchase_in",
        "source_type": "qm_incoming_reject",
        "id": doc,
        "head": head,
        "lines": [{"source_line_id": line, "quantity": float(qty)}],
    }


def _refuse(run: Run, body: dict, code: str, text: str) -> None:
    status, payload = _send(run.env, GEN, body)
    if status != 409 or payload.get("code") != code or text not in str(payload.get("message")):
        raise _Fail(f"应 409 {code}（含「{text}」），实际 " + _brief(status, payload))


def _line(run: Run, doc: int) -> tuple[int, Decimal]:
    auto, qty, flag = _cells(run, reject_line_sql(str(doc)), 3)
    if not auto.isdigit() or flag != "0":
        raise _Fail(f"处理单 {doc} 的表体应有 AUTOID、BFLAG=0，实际 {auto}、{flag}")
    return int(auto), Decimal(qty)


def _in_state(run: Run, line: int) -> tuple[str, Decimal, str, str, str]:
    count, qty, doc, source, flag = _cells(run, reject_in_state_sql(str(line)), 5)
    return count, Decimal(qty), doc, source, flag


def _check_in(run: Run, line: int, qty: Decimal, made: int) -> None:
    count, got, doc, source, flag = _in_state(run, line)
    want = ("1", qty, str(made), "来料不良品处理单", "1")
    if (count, got, doc, source, flag) != want:
        raise _Fail(f"入库后（行数、数量、入库单 ID、cSource、BFLAG）应为 {want}，实际 {(count, got, doc, source, flag)}")


def _adopt_in(run: Run, line: int) -> None:
    # 生单没拿到 200（504、超时、客户端异常）时 U8 可能已自行提交：按处理单行回读，找到的入库单记进 stock_in 供清理。
    try:
        count, _qty, doc, _source, _flag = _in_state(run, line)
    except (_Fail, RuntimeError, ValueError, ArithmeticError):
        return
    if count != "0" and doc.isdigit() and int(doc) not in run.stock_in:
        run.stock_in.append(int(doc))


def _stock_in(run: Run, doc: int, line: int, qty: Decimal) -> int:
    try:
        status, payload = _send(run.env, GEN, _body(doc, line, qty, run))
    except Exception:
        _adopt_in(run, line)
        raise
    made = payload.get("id")
    if status != 200 or type(made) is not int or made <= 0:
        _adopt_in(run, line)
        raise _Fail("整数量生成采购入库应 200 并返回新单 id，实际 " + _brief(status, payload))
    run.stock_in.append(made)
    return made


def _cycle(run: Run) -> str:
    env, spec, picked = run.env, run.spec, run.picked
    _clean(state(env, spec, picked.check), "生单前")
    doc = _send_made(env, spec, _FLOW, picked, run.reject)
    status, payload = _verify(env, _FLOW, doc, "verify")
    if state(env, spec, picked.check).wf == "1":
        raise _Na("处理单受审批流控制，不能直接审核，采购入库只接审批完成的处理单")
    if status != 200:
        raise _Fail("处理单审核应 200，实际 " + _brief(status, payload))
    line, qty = _line(run, doc)
    _refuse(run, _body(doc, line, qty / 2, run), "state_mismatch", "须一次入库全部")
    _refuse(run, _body(doc, line, qty + 1, run), "state_mismatch", "超过可生单数量")
    made = _stock_in(run, doc, line, qty)
    _check_in(run, line, qty, made)
    _refuse(run, _body(doc, line, qty, run), "state_mismatch", "已入库完毕")
    status, payload = _send(env, DELETE, {"type": "purchase_in", "id": made})
    if status != 200 or payload.get("deleted") is not True:
        raise _Fail("删除采购入库应 200 deleted=true，实际 " + _brief(status, payload))
    run.stock_in.remove(made)
    back = _in_state(run, line)
    if (back[0], back[1], back[4]) != ("0", Decimal(0), "0"):
        raise _Fail(f"删除后（入库行数、数量、BFLAG）应为 0、0、0，实际 {back[0]}、{back[1]}、{back[4]}")
    status, payload = _verify(env, _FLOW, doc, "unverify")
    if status != 200:
        raise _Fail("处理单弃审应 200，实际 " + _brief(status, payload))
    status, payload = _send(env, DELETE, {"type": _FLOW.kind, "id": doc})
    if status != 200:
        raise _Fail("删除处理单应 200，实际 " + _brief(status, payload))
    run.reject.remove(doc)
    _clean(state(env, spec, picked.check), "删除后")
    return f"检验单 {picked.check} → QM05 {doc} → 采购入库 {made}：部分 / 超量 409、整数量 {qty} 入库、重复 409、删除回退，BFLAG 回到 0"


def _drop(run: Run) -> str:
    notes = []
    for doc in run.stock_in:
        try:
            status, _payload = _send(run.env, DELETE, {"type": "purchase_in", "id": doc})
            notes.append(f"采购入库 {doc} " + ("已清理" if status in (200, 404) else f"删除返回 {status}，留给快照还原"))
        except Exception as exc:  # 清理尽力而为，任何异常都只记一句
            notes.append(f"采购入库 {doc} 清理出错：{str(exc)[:60]}，留给快照还原")
    notes.append(_cleanup(run.env, _FLOW, run.reject))
    return "；".join(note for note in notes if note)


def run_case(env: Env, spec: CaseSpec) -> tuple[str, str]:
    picked, why = pick(env, spec, _FLOW)
    if picked is None:
        return "N/A", why
    wh = first_cell(run_guarded(env, reject_wh_sql(picked.inv), spec.name)).strip()
    if not wh:
        return "N/A", f"挑不到存货 {picked.inv} 可入库的仓库（未冻结、不管货位）"
    run = Run(env, spec, picked, wh)
    try:
        return "PASS", _cycle(run)
    except _Na as exc:
        return "N/A", (str(exc) + "；" + _drop(run))[:240]
    except Exception as exc:
        # 断言失败、回读 SQL 失败、中断、网络超时等任何异常，都先尽力清掉新单再记 FAIL。
        tail = _drop(run)
        return "FAIL", (str(exc) + ("；" + tail if tail else ""))[:240]


def purchase_in_qm_reject(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return run_case(env, spec)


PURCHASE_IN_QM_REJECT_OPS = {"purchase_in_qm_reject": purchase_in_qm_reject}


def purchase_in_qm_reject_cases() -> list[CaseSpec]:
    # 与 qm-incoming-reject-cycle 同挑一张造数检验单，整轮互斥；入库、删除动现存量，回写到货行与订单行（推导出 stock、chain:pu）。
    tags = frozenset((
        "new:qm_incoming_reject", "w:qm_incoming_reject", "w:qm_incoming_check", "new:purchase_in", "w:purchase_in",
    ))
    step = Step("custom", sql_name="purchase_in_qm_reject", id_from="none")
    return [
        CaseSpec(
            "purchase-in-from-qm-reject",
            "来料不良品处理单（流程 2）生成采购入库：部分 / 超量 409，整数量入库挂回处理单行、BFLAG 回写，删除回退",
            (),
            (step,),
            True,
            optional=True,
            tags=tags,
            routes=(GEN, VERIFY, DELETE),
        )
    ]
