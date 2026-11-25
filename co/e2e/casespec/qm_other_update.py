"""其他报检单（QM11）、其他检验单（QM15）修改（vouchers/update，VO 的 UpdateVoucher，U8 自己提交）。

照 qm-other-cycle（qm_other_write）挑数据、新增其他报检单（新增后已审核）、生单，再在中间插入修改：
qm11-update-nodown：报检单弃审 → 预演修改 cDefine10 200 dry_run、单据不变 → 修改 → 回读 cDefine10、UFTS 变了 → 审核 → 删除。
qm11-update-verified-409：报检单已审核时修改 409 state_mismatch、单据不变（不是因为已有检验单）→ 删除。
qm15-update-cycle：生单 → 预演修改 200 dry_run、单据不变 → 修改为不良 1（不带原因：原因是让步接收原因）、结论、cDefine10、
  第一个检验项目的单项判定 → 回读这些字段、UFTS 变了、修改人是本操作员 → 审核、弃审 → 删除检验单和报检单。
qm15-update-verified-409：生单 → 审核 → 修改 409 state_mismatch、单据不变 → 弃审 → 删除。

挑不到数据时记 N/A（同 qm-other-cycle）。中途失败时尽力弃审、删除新单（qm_other_write._cleanup，快照还原兜底）。
步骤 kind 为 "custom"，sql_name 是操作名（QMU_OTHER_OPS 并入 ops_run）。
"""

from __future__ import annotations

from collections.abc import Callable

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.qm_other_write import (
    CHECK,
    INSPECT,
    VERIFY,
    Run,
    _audit,
    _brief,
    _cleanup,
    _create,
    _delete,
    _Fail,
    _finish,
    _generate,
    _inspect_verify,
    _send,
    pick,
)
from co.e2e.casespec.qm_update_sql import check_state_sql, inspect_state_sql, item_pick_sql, item_value_sql
from co.e2e.casespec.sqlexec import run_guarded

UPDATE = "/v1/vouchers/update"
_TEXT_MAX = 60


def _cell(run: Run, query: str) -> str:
    return first_cell(run_guarded(run.env, query, run.spec.name)).strip()


def _parts(run: Run, query: str, count: int) -> list[str]:
    parts = [part.strip() for part in _cell(run, query).split("|")]
    if len(parts) != count:
        raise _Fail("回读的列数不对：" + "|".join(parts)[:80])
    return parts


def _bumped(old: str) -> str:
    # 新值：原值后加 R，原值为空时用 QMUE2E；不超过 60 字。
    return (old[: _TEXT_MAX - 1] + "R") if old else "QMUE2E"


def _update(run: Run, kind: str, doc: int, head: dict, dry: bool = False) -> tuple[int, dict]:
    body = {"type": kind, "id": doc, "head": head}
    if dry:
        body["dry_run"] = True
    return _send(run.env, UPDATE, body)


def _want(status: int, payload: dict, code: int, what: str, err: str = "") -> None:
    if status != code or (err and payload.get("code") != err) or (code == 200 and payload.get("ok") is not True):
        raise _Fail(what + " 应 " + str(code) + (" " + err if err else "") + "，实际 " + _brief(status, payload))


def _dry_then_same(run: Run, kind: str, doc: int, head: dict, query: str) -> None:
    before = _cell(run, query)
    status, payload = _update(run, kind, doc, head, dry=True)
    if status != 200 or payload.get("dry_run") is not True:
        raise _Fail("预演修改应 200 dry_run=true，实际 " + _brief(status, payload))
    if _cell(run, query) != before:
        raise _Fail("预演修改后单据变了")


def _refused(run: Run, kind: str, doc: int, head: dict, query: str) -> None:
    before = _cell(run, query)
    status, payload = _update(run, kind, doc, head)
    _want(status, payload, 409, "已审核的单据修改", "state_mismatch")
    if _cell(run, query) != before:
        raise _Fail("修改被拒绝后单据变了")


# ---- 其他报检单 ----


def _inspect_edit(run: Run) -> str:
    doc = run.made["inspect"]
    _inspect_verify(run, doc, "unverify")
    query = inspect_state_sql(str(doc))
    old, ufts, _who = _parts(run, query, 3)
    head = {"cDefine10": _bumped(old)}
    _dry_then_same(run, INSPECT, doc, head, query)
    status, payload = _update(run, INSPECT, doc, head)
    _want(status, payload, 200, "其他报检单修改")
    got, after, who = _parts(run, query, 3)
    if got != head["cDefine10"] or after == ufts or who:
        raise _Fail(f"修改后（cDefine10、UFTS 变化、审核人）应为 ({head['cDefine10']}, 变, 空)，实际 ({got}, {after != ufts}, {who})")
    _inspect_verify(run, doc, "verify")
    return f"其他报检单 {doc} 弃审、预演修改、修改 cDefine10 {old!r} → {got!r}、审核"


def _drop_inspect(run: Run) -> None:
    _delete(run, INSPECT, run.made["inspect"])
    del run.made["inspect"]


def _inspect_nodown(run: Run) -> str:
    _create(run)
    note = _inspect_edit(run)
    _drop_inspect(run)
    return note + "、删除"


def _inspect_verified(run: Run) -> str:
    _create(run)
    doc = run.made["inspect"]
    query = inspect_state_sql(str(doc))
    old = _parts(run, query, 3)[0]
    _refused(run, INSPECT, doc, {"cDefine10": _bumped(old)}, query)
    _drop_inspect(run)
    return f"已审核的其他报检单 {doc} 修改 409、单据不变，删除"


# ---- 其他检验单 ----


def _check_head(run: Run, doc: int, old: str) -> tuple[dict, str]:
    """修改内容与回读期望（结论|合格|让步|不良|cDefine10）：改成不良 1、不带原因。其他检验单的 CREASONCODE 是让步接收原因，
    没有让步数量时 U8 审核报「没有'让步接收数量'，'让步接收原因'不能填写！」（实测），桥修改时 400。"""
    head: dict = {"cChkConclusion": "不合格", "cDefine10": _bumped(old), "fRegQuantity": 0, "fDisQuantity": 1}
    want = f"不合格|0.0000|0.0000|1.0000|{head['cDefine10']}"
    item = _cell(run, item_pick_sql(str(doc))).split("|")
    if len(item) == 2 and all(item):
        head["items"] = [{"cChkItemCode": item[0], "cChkGuideCode": item[1], "cTargetQJug": "不合格"}]
    return head, want


def _check_edit(run: Run, doc: int) -> str:
    query = check_state_sql(str(doc))
    before = _parts(run, query, 8)
    head, want = _check_head(run, doc, before[4])
    _dry_then_same(run, CHECK, doc, head, query)
    status, payload = _update(run, CHECK, doc, head)
    _want(status, payload, 200, "其他检验单修改")
    after = _parts(run, query, 8)
    if "|".join(after[:5]) != want or after[5] == before[5] or after[6] or after[7] != run.env.operator_name.strip():
        raise _Fail(f"修改后应为 {want}、UFTS 变、未审核、修改人是本操作员，实际 {'|'.join(after)}")
    if "items" in head and _cell(run, item_value_sql(str(doc))).split("|")[-1] != "不合格":
        raise _Fail("修改后第一个检验项目的单项判定应为 不合格")
    return f"其他检验单 {doc} 预演修改、修改（{', '.join(sorted(head))}）"


def _check_cycle(run: Run) -> str:
    st = _create(run)
    doc = _generate(run, st.line)
    note = _check_edit(run, doc)
    _audit(run, doc)
    _finish(run, doc)
    return note + "、审核、弃审、删除"


def _check_verified(run: Run) -> str:
    st = _create(run)
    doc = _generate(run, st.line)
    status, payload = _send(run.env, VERIFY, {"type": CHECK, "id": doc, "action": "verify"})
    _want(status, payload, 200, "其他检验单审核")
    query = check_state_sql(str(doc))
    _refused(run, CHECK, doc, {"cChkConclusion": "不合格"}, query)
    status, payload = _send(run.env, VERIFY, {"type": CHECK, "id": doc, "action": "unverify"})
    _want(status, payload, 200, "其他检验单弃审")
    _finish(run, doc)
    return f"已审核的其他检验单 {doc} 修改 409、单据不变，弃审、删除"


def _op(flow: Callable[[Run], str]) -> Callable[[Env, CaseSpec, Step], tuple[str, str]]:
    def run_case(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
        picked, why = pick(env, spec)
        if picked is None:
            return "N/A", why
        run = Run(env, spec, picked)
        try:
            return "PASS", flow(run)
        except Exception as exc:
            # 断言失败、回读失败、中断、超时等任何异常，都先尽力清掉新单再记 FAIL。
            tail = _cleanup(run)
            return "FAIL", (str(exc) + ("；" + tail if tail else ""))[:240]

    return run_case


QMU_OTHER_OPS = {
    "qmu_qm11_nodown": _op(_inspect_nodown),
    "qmu_qm11_verified": _op(_inspect_verified),
    "qmu_qm15_cycle": _op(_check_cycle),
    "qmu_qm15_verified": _op(_check_verified),
}
_CASES = (
    ("qm11-update-nodown", "qmu_qm11_nodown", "其他报检单弃审后预演修改、修改 cDefine10、回读、审核、删除"),
    ("qm11-update-verified-409", "qmu_qm11_verified", "已审核的其他报检单修改 409、单据不变"),
    ("qm15-update-cycle", "qmu_qm15_cycle", "其他检验单预演修改、修改数量 / 结论 / 自定义项 / 检验项目、回读、审核弃审、删除"),
    ("qm15-update-verified-409", "qmu_qm15_verified", "已审核的其他检验单修改 409、单据不变，弃审后删除"),
)


def qm_other_update_cases() -> list[CaseSpec]:
    # 与 qm-other-cycle 同一组标签（新增、生单、删除、审核同类单据），互斥。
    tags = frozenset(("new:qm_other_inspect", "new:qm_other_check", "w:qm_other_inspect", "w:qm_other_check"))
    routes = ("/v1/vouchers/create", "/v1/vouchers/generate", UPDATE, VERIFY, "/v1/vouchers/delete")
    return [
        CaseSpec(name, title, (), (Step("custom", sql_name=op, id_from="none"),), True, optional=True, tags=tags, routes=routes)
        for name, op, title in _CASES
    ]
