"""其他报检单（QM11）无来源新增、其他检验单（QM15）参照生单 / 审核 / 弃审 / 删除的一整轮。

qm-other-cycle：照最近一张真实的其他检验单挑存货、检验员、检验部门、检验方案，模板必输的自定义项照抄最近一张同类单据
（模板设为必输的自定义项因账套而异）→ 预演新增 200 dry_run、其他报检单最大 ID 不变 →
新增 1 行（数量 1）→ 回读 1 行；桥按 QM.bOtherInspectAutoVerify 补审核（响应 auto_verify_error 时未审核）→ 其他报检单
弃审 → 审核（vouchers/verify，没审核的直接审核）→ 预演生单 200 dry_run、没有检验单 → 生单 → 回读：一张检验单、
INSPECTAUTOID 是报检单行、报检单行 BFLAG=1、IsWfControlled=0、未审核 → 再生单 409（万一 200，新单记下供清理）→
已有检验单的报检单弃审 409 → 审核 → 弃审 → 删除检验单 → 报检单行 BFLAG 回到 NULL / 0 → 删除报检单
（先弃审再删，响应 unverified=true）→ 按两个 ID 计数都是 0。中途失败时尽力弃审、删除新单（快照还原兜底）。

挑不到「其他报检单 → 其他检验单」的真实数据（只有有这类单据的账套才有）或模板把接口带不了的自定义项设为必输时记 N/A。
步骤 kind 为 "custom"，sql_name 是操作名（QM_OTHER_WRITE_OPS 并入 ops_run）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from co.e2e.casespec.assertutil import first_cell, sql_cells
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.qm_tpl import required_defines, stuck_text
from co.e2e.casespec.qm_other_write_sql import (
    CHECK_VT,
    INSPECT_VT,
    adopt_sql,
    defines_sql,
    gone_sql,
    max_sql,
    pick_sql,
    state_sql,
)
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke

INSPECT = "qm_other_inspect"
CHECK = "qm_other_check"
CREATE = "/v1/vouchers/create"
GEN = "/v1/vouchers/generate"
VERIFY = "/v1/vouchers/verify"
DELETE = "/v1/vouchers/delete"
_NO_DATA = "挑不到其他报检单 → 其他检验单的真实数据（VT 361 / 365，只有有这类单据的账套才有）"


@dataclass(frozen=True)
class Pick:
    inv: str
    person: str
    dep: str
    project: str
    inspect_head: tuple[tuple[str, str], ...] = ()
    check_head: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class State:
    count: int
    verifier: str
    line: str
    flag: str
    checks: int
    check: str
    check_verifier: str
    wf: str
    vs: str
    auto: str
    lines: int


@dataclass
class Run:
    env: Env
    spec: CaseSpec
    picked: Pick
    before: str = "0"
    note: str = ""
    made: dict[str, int] = field(default_factory=dict)


class _Fail(Exception):
    pass


def _send(env: Env, route: str, body: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(body)
    return invoke(env.client, route, fields)


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _cell(env: Env, spec: CaseSpec, query: str) -> str:
    return first_cell(run_guarded(env, query, spec.name)).strip()


def _head(env: Env, spec: CaseSpec, vt: int) -> tuple[dict[str, str] | None, str]:
    """模板必输的表头自定义项：照抄最近一张同类单据的值，挑不到时用 qm_tpl 的占位值；表体或请求带不了的必输项返回说明。"""
    fill, stuck = required_defines(env, spec, vt)
    if stuck:
        return None, stuck_text(vt, stuck)
    if fill:
        names = sorted(fill)
        parts = _cell(env, spec, defines_sql(vt, names)).split("|")
        if len(parts) == len(names) and all(part.strip() for part in parts):
            fill = {name: part.strip() for name, part in zip(names, parts, strict=True)}
    return fill, ""


def pick(env: Env, spec: CaseSpec) -> tuple[Pick | None, str]:
    """挑存货、检验员、检验部门、检验方案和两张模板必输的自定义项；挑不到时返回 (None, N/A 说明)。"""
    parts = [part.strip() for part in _cell(env, spec, pick_sql()).split("|")]
    if len(parts) != 4 or not all(parts):
        return None, _NO_DATA
    inspect, why = _head(env, spec, INSPECT_VT)
    if inspect is None:
        return None, why
    check, why = _head(env, spec, CHECK_VT)
    if check is None:
        return None, why
    return Pick(parts[0], parts[1], parts[2], parts[3], tuple(sorted(inspect.items())), tuple(sorted(check.items()))), ""


def state(env: Env, spec: CaseSpec, inspect: int) -> State:
    parts = [part.strip() for part in _cell(env, spec, state_sql(str(inspect))).split("|")]
    if len(parts) != 11 or not parts[0].isdigit() or not parts[4].isdigit() or not parts[10].isdigit():
        raise _Fail(f"读不到其他报检单 {inspect} 的状态")
    return State(int(parts[0]), parts[1], parts[2], parts[3], int(parts[4]), *parts[5:10], int(parts[10]))


def create_body(picked: Pick, dry: bool = False) -> dict:
    made = {"type": INSPECT, "head": dict(picked.inspect_head), "lines": [{"cinvcode": picked.inv, "quantity": 1}]}
    if dry:
        made["dry_run"] = True
    return made


def generate_body(picked: Pick, inspect: int, line: int, dry: bool = False) -> dict:
    head = {"ccheckpersoncode": picked.person, "cdepcode": picked.dep, "project_code": picked.project}
    head.update(dict(picked.check_head))
    made = {"type": CHECK, "source_type": INSPECT, "id": inspect, "head": head,
            "lines": [{"source_line_id": line, "quantity": 1}]}
    if dry:
        made["dry_run"] = True
    return made


def _adopt_inspect(run: Run) -> None:
    # 新增没拿到 200（504、超时、客户端异常）时 U8 可能已自行提交：按调用前最大 ID 和存货找回，记进 made 供清理。
    try:
        cells = sql_cells(run_guarded(run.env, adopt_sql(run.before, run.picked.inv), run.spec.name))
    except (RuntimeError, ValueError):
        return
    found = [int(cell) for cell in cells if cell.strip().isdigit()]
    if len(found) == 1:
        run.made["inspect"] = found[0]


def _adopt_check(run: Run) -> None:
    try:
        found = state(run.env, run.spec, run.made["inspect"]).check
    except (_Fail, RuntimeError, ValueError):
        return
    if found.isdigit():
        run.made["check"] = int(found)


def _create(run: Run) -> State:
    env, spec, picked = run.env, run.spec, run.picked
    run.before = _cell(env, spec, max_sql()) or "0"
    status, payload = _send(env, CREATE, create_body(picked, dry=True))
    if status != 200 or payload.get("dry_run") is not True:
        raise _Fail("预演新增应 200 dry_run=true，实际 " + _brief(status, payload))
    if _cell(env, spec, max_sql()) != run.before:
        raise _Fail("预演新增后其他报检单的最大 ID 变了")
    try:
        status, payload = _send(env, CREATE, create_body(picked))
    except Exception:
        _adopt_inspect(run)
        raise
    doc = payload.get("id")
    if status != 200 or type(doc) is not int or doc <= 0:
        _adopt_inspect(run)
        raise _Fail("新增应 200 并返回新单 id，实际 " + _brief(status, payload))
    run.made["inspect"] = doc
    st = state(env, spec, doc)
    if (st.count, st.lines) != (1, 1) or not st.line.isdigit():
        raise _Fail(f"新增后（张数、行数）应为 (1, 1)，实际 ({st.count}, {st.lines})")
    if bool(st.verifier) != ((payload.get("state") or {}).get("verified") is True):
        raise _Fail("新增响应的 state.verified 与回读的审核人不一致")
    if not st.verifier and not payload.get("auto_verify_error"):
        raise _Fail("其他报检单保存后未审核，响应应带 auto_verify_error")
    run.note = "保存后已自动审核" if st.verifier else "自动审核没做：" + str(payload.get("auto_verify_error"))[:40]
    _inspect_audit(run, doc, bool(st.verifier))
    return st


def _inspect_verify(run: Run, doc: int, action: str) -> None:
    status, payload = _send(run.env, VERIFY, {"type": INSPECT, "id": doc, "action": action})
    if status != 200:
        raise _Fail(f"其他报检单 {action} 应 200，实际 " + _brief(status, payload))
    if bool(state(run.env, run.spec, doc).verifier) != (action == "verify"):
        raise _Fail(f"其他报检单 {action} 后审核人应{'有值' if action == 'verify' else '清空'}")


def _inspect_audit(run: Run, doc: int, verified: bool) -> None:
    # 已审核的先弃审再审核；补审核没做的直接审核（接口补审的路径）。
    if verified:
        _inspect_verify(run, doc, "unverify")
    _inspect_verify(run, doc, "verify")


def _check_made(st: State, doc: int, line: str) -> None:
    got = (st.checks, st.check, st.auto, st.flag, st.wf, st.vs, st.check_verifier)
    want = (1, str(doc), line, "1", "0", "0", "")
    if got != want:
        raise _Fail(f"生单后（张数、ID、INSPECTAUTOID、BFLAG、IsWfControlled、iVerifyStateNew、审核人）应为 {want}，实际 {got}")


def _generate(run: Run, line: str) -> int:
    env, spec, picked = run.env, run.spec, run.picked
    inspect = run.made["inspect"]
    status, payload = _send(env, GEN, generate_body(picked, inspect, int(line), dry=True))
    if status != 200 or payload.get("dry_run") is not True:
        raise _Fail("预演生单应 200 dry_run=true，实际 " + _brief(status, payload))
    if state(env, spec, inspect).checks != 0:
        raise _Fail("预演生单后不应有其他检验单")
    try:
        status, payload = _send(env, GEN, generate_body(picked, inspect, int(line)))
    except Exception:
        _adopt_check(run)
        raise
    doc = payload.get("id")
    if status != 200 or type(doc) is not int or doc <= 0:
        _adopt_check(run)
        raise _Fail("生单应 200 并返回新单 id，实际 " + _brief(status, payload))
    run.made["check"] = doc
    _check_made(state(env, spec, inspect), doc, line)
    status, payload = _send(env, GEN, generate_body(picked, inspect, int(line)))
    if status != 409:
        extra = payload.get("id")
        if status == 200 and type(extra) is int and extra > 0:
            run.made["check2"] = extra
        raise _Fail("已生成检验单的报检单行再生单应 409，实际 " + _brief(status, payload))
    status, payload = _send(env, VERIFY, {"type": INSPECT, "id": inspect, "action": "unverify"})
    if status != 409:
        raise _Fail("已有其他检验单的报检单弃审应 409，实际 " + _brief(status, payload))
    return doc


def _audit(run: Run, doc: int) -> None:
    inspect = run.made["inspect"]
    for action, want in (("verify", True), ("unverify", False)):
        status, payload = _send(run.env, VERIFY, {"type": CHECK, "id": doc, "action": action})
        if status != 200:
            raise _Fail(f"其他检验单 {action} 应 200，实际 " + _brief(status, payload))
        if bool(state(run.env, run.spec, inspect).check_verifier) != want:
            raise _Fail(f"其他检验单 {action} 后审核人应{'有值' if want else '清空'}")


def _delete(run: Run, kind: str, doc: int) -> dict:
    status, payload = _send(run.env, DELETE, {"type": kind, "id": doc})
    if status != 200 or payload.get("deleted") is not True:
        raise _Fail(f"删除 {kind} {doc} 应 200 deleted=true，实际 " + _brief(status, payload))
    return payload


def _finish(run: Run, doc: int) -> None:
    env, spec = run.env, run.spec
    inspect = run.made["inspect"]
    _delete(run, CHECK, doc)
    del run.made["check"]
    st = state(env, spec, inspect)
    if st.checks != 0 or st.flag not in ("N", "0"):
        raise _Fail(f"删除检验单后报检单应没有检验单、行 BFLAG 为 NULL / 0，实际 {st.checks} 张、BFLAG={st.flag}")
    payload = _delete(run, INSPECT, inspect)
    if payload.get("unverified") is not True:
        raise _Fail("删除已审核的其他报检单应先弃审（响应 unverified=true）")
    del run.made["inspect"]
    left = _cell(env, spec, gone_sql(str(inspect), str(doc)))
    if left != "0|0|0|0":
        raise _Fail(f"删除后按 ID 计数（报检单表头、表体、检验单表头、检验项目）应全为 0，实际 {left}")


def _cycle(run: Run) -> str:
    st = _create(run)
    inspect = run.made["inspect"]
    doc = _generate(run, st.line)
    _audit(run, doc)
    _finish(run, doc)
    return (f"其他报检单 {inspect}（{run.note}，弃审、审核）行 {st.line} → 其他检验单 {doc}：预演、生单、重复 409、"
            "报检单弃审 409、审核、弃审、删除（BFLAG 回退），报检单弃审后删除，计数归零")


def _drop(run: Run, kind: str, doc: int) -> str:
    try:
        if kind == CHECK:
            _send(run.env, VERIFY, {"type": CHECK, "id": doc, "action": "unverify"})
        status, _payload = _send(run.env, DELETE, {"type": kind, "id": doc})
    except Exception as exc:  # 清理尽力而为，任何异常都只记一句
        return f"{kind} {doc} 清理出错：{str(exc)[:60]}，留给快照还原"
    return f"{kind} {doc} " + ("已清理" if status in (200, 404) else f"删除返回 {status}，留给快照还原")


def _cleanup(run: Run) -> str:
    # 先检验单（弃审、删除），再报检单；失败不改判定（快照还原兜底）。
    parts = []
    if "check2" in run.made:
        parts.append(_drop(run, CHECK, run.made["check2"]))
    if "check" in run.made:
        parts.append(_drop(run, CHECK, run.made["check"]))
    if "inspect" in run.made:
        parts.append(_drop(run, INSPECT, run.made["inspect"]))
    return "；".join(parts)


def qm_other_cycle(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    picked, why = pick(env, spec)
    if picked is None:
        return "N/A", why
    run = Run(env, spec, picked)
    try:
        return "PASS", _cycle(run)
    except Exception as exc:
        # 断言失败、回读 SQL 失败、中断、网络超时等任何异常，都先尽力清掉新单再记 FAIL。
        tail = _cleanup(run)
        return "FAIL", (str(exc) + ("；" + tail if tail else ""))[:240]


QM_OTHER_WRITE_OPS = {"qm_other_cycle": qm_other_cycle}


def qm_other_write_cases() -> list[CaseSpec]:
    # 新增、生单与桥 DocLocks 的 new:<类型> 同名；删除、审核是 w:<类型>，让 qm-other-read 的列表断言避开。
    tags = frozenset(("new:qm_other_inspect", "new:qm_other_check", "w:qm_other_inspect", "w:qm_other_check"))
    step = Step("custom", sql_name="qm_other_cycle", id_from="none")
    title = "其他报检单无来源新增（自动审核）→ 其他检验单参照生单、重复 409、审核弃审、删除，报检单弃审后删除"
    return [CaseSpec("qm-other-cycle", title, (), (step,), True, optional=True, tags=tags,
                     routes=(CREATE, GEN, VERIFY, DELETE))]
