"""应收应付期初单据（openings/arap）。只对桥 config.json 的 testAccounts 里的账套开放，其余账套登录前
403 test_account_only。本模块的三条用例只跑应收一侧（side=ar）；应付一侧和「启用月份已结账时临时打开」的一整轮
在 openings_live，共用这里的 run_cycle（挑数、SQL 回读按 side 取 openings_arap_sql 的登记）。

openings-arap-test-only：预演审核一张不存在的期初单据，非测试账套应登录前 403 test_account_only
  「期初单据只对配置为测试账套的账套开放」（桥总开关未打开时 403 feature_disabled，同样 PASS）；回 404 / 409 / 200 说明账套在名单里，记 N/A。
openings-arap-closed：应收启用月份已结账（GL_mend 启用年度、启用月份 bflag_AR=1）时预演新增 → 409 state_mismatch
  「…启用的第一个月已经结账，不能修改期初单据」。启用月份未结账、应收未启用、非测试账套记 N/A。
openings-arap-cycle：启用月份未结账的测试账套上：预演新增 → 新增 → 审核 → SQL 回读期初形态（Ap_Vouch.bStartFlag=1、
  单据日期和审核日期为启用日前一天、往来明细全在第 0 期且登记日期同单据日期）→ 弃审 → 删除 → 回读已不存在。
  中途失败时尽力弃审、删除（快照还原兜底）。启用月份已结账、应收未启用、没有可用的客户或应收受控科目、非测试账套记 N/A。
  步骤由 run_cycle 实现。

步骤 kind 为 "custom"，sql_name 是操作名（OPENINGS_ARAP_OPS 并入 ops_run）。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.openings_arap_sql import doc_sql, pick_sql, state_sql
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke
from co.e2e.casespec.tier2 import TIER2_TEXT, refusal_verdict, tier2_refused

ROUTE = "/v1/openings/arap"
_TEST_ONLY_TEXT = "期初单据只对配置为测试账套的账套开放"
_CLOSED_TEXT = "启用的第一个月已经结账"
_NOT_TEST_NA = f"桥没开第二级写入或账套不在 testAccounts 里（{TIER2_TEXT}，openings-arap-test-only 覆盖）"
_PROBE_ID = 2147483647
_AMOUNT = 12.34
# 启用月份已结账的账套上字段只做格式校验（闸门在档案检查之前），挑不到时用占位值。
_PLACEHOLDER = ("C001", "1122")
_YMD = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
# side → (模块简称, 单据类型, 往来单位, 受控科目前缀)。
SIDE_TEXT = {"ar": ("应收", "ar_bill", "客户", "1122"), "ap": ("应付", "ap_bill", "供应商", "2202")}


def side_state(env: Env, spec: CaseSpec, side: str) -> tuple[str, str]:
    """(应收 / 应付启用日期, 启用月份结账标志 "0" / "1")；没启用时启用日期为空串。"""
    start, _sep, flag = first_cell(run_guarded(env, state_sql(side), spec.name)).partition("|")
    start = start.strip()
    return (start if _YMD.match(start) else ""), flag.strip()


def ar_state(env: Env, spec: CaseSpec) -> tuple[str, str]:
    return side_state(env, spec, "ar")


def _pick(env: Env, spec: CaseSpec, start: str, side: str = "ar") -> tuple[str, str] | None:
    partner, _sep, account = first_cell(run_guarded(env, pick_sql(side, int(start[:4])), spec.name)).partition("|")
    partner, account = partner.strip(), account.strip()
    return (partner, account) if partner and account else None


def _read_doc(env: Env, spec: CaseSpec, doc_id: int, side: str) -> list[str]:
    cell = first_cell(run_guarded(env, doc_sql(side, doc_id), spec.name))
    return [part.strip() for part in cell.split("|")] if cell else []


def _day_before(start: str) -> str:
    return (date.fromisoformat(start) - timedelta(days=1)).isoformat()


def _send(env: Env, body: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(body)
    return invoke(env.client, ROUTE, fields)


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _not_test(status: int, payload: dict) -> bool:
    return tier2_refused(status, payload)


def _create_body(partner: str, account: str, side: str = "ar") -> dict:
    return {"side": side, "action": "create", "partner": partner, "amount": _AMOUNT, "account": account,
            "digest": "回归期初" + SIDE_TEXT[side][0]}


def openings_arap_test_only(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del spec, step
    status, payload = _send(env, {"side": "ar", "action": "verify", "id": _PROBE_ID, "dry_run": True})
    if _not_test(status, payload):
        return refusal_verdict(payload, _TEST_ONLY_TEXT, "预演期初单据审核")
    if status in (200, 404) or (status == 409 and payload.get("code") == "state_mismatch"):
        return "N/A", f"账套在桥的 testAccounts 里（预演回 {status}）"
    return "FAIL", f"预演期初单据应 {TIER2_TEXT}（测试账套 404 或 409），实际 " + _brief(status, payload)


def openings_arap_closed(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    start, closed = ar_state(env, spec)
    if not start:
        return "N/A", "应收款管理未启用"
    if closed != "1":
        return "N/A", f"应收启用月份 {start[:7]} 未结账（openings-arap-cycle 覆盖）"
    partner, account = _pick(env, spec, start) or _PLACEHOLDER
    status, payload = _send(env, dict(_create_body(partner, account), dry_run=True))
    if _not_test(status, payload):
        return "N/A", _NOT_TEST_NA
    if status == 409 and payload.get("code") == "state_mismatch" and _CLOSED_TEXT in str(payload.get("message")):
        return "PASS", f"应收启用月份 {start[:7]} 已结账：预演新增期初单据 409"
    return "FAIL", f"预演新增应 409 state_mismatch「…{_CLOSED_TEXT}…」，实际 " + _brief(status, payload)


def _dry_problem(status: int, payload: dict) -> str:
    if status != 200:
        return "预演新增应 200，实际 " + _brief(status, payload)
    if payload.get("dry_run") is not True or payload.get("mode") != "rollback":
        return f"预演响应应 dry_run 为 true、mode 为 rollback：{payload}"[:240]
    return ""


def _created_problem(payload: dict, start: str, side: str) -> str:
    want = {"type": SIDE_TEXT[side][1], "side": side, "opening": True, "start_date": start, "date": _day_before(start)}
    got = {key: payload.get(key) for key in want}
    if got != want:
        return f"新增响应应为 {want}，实际 {got}"[:240]
    return ""


def _step(env: Env, side: str, doc_id: int, action: str) -> str:
    status, payload = _send(env, {"side": side, "action": action, "id": doc_id})
    if status != 200:
        return f"{action} 应 200，实际 " + _brief(status, payload)
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    if action in ("verify", "unverify") and state.get("verified") is not (action == "verify"):
        return f"{action} 后 state.verified 不对：{state}"[:240]
    if action == "delete" and payload.get("deleted") is not True:
        return f"删除响应应 deleted 为 true：{payload}"[:240]
    if payload.get("opening") is not True:
        return f"{action} 响应应带 opening=true"
    return ""


def _shape_problem(row: list[str], day: str) -> str:
    # 期初标志|单据日期|审核日期|往来明细行数|第 0 期且登记日期同单据日期的行数。
    if len(row) != 5 or not row[3].isdigit():
        return f"回读期初单据失败：{row}"
    if row[:3] != ["1", day, day]:
        return f"期初标志、单据日期、审核日期应为 1、{day}、{day}，实际 {row[:3]}"
    if int(row[3]) < 1 or row[3] != row[4]:
        return f"往来明细应全在第 0 期、登记日期 {day}：共 {row[3]} 行，符合 {row[4]} 行"
    return ""


def _lifecycle(env: Env, spec: CaseSpec, side: str, doc_id: int, day: str) -> str:
    problem = _step(env, side, doc_id, "verify")
    problem = problem or _shape_problem(_read_doc(env, spec, doc_id, side), day)
    problem = problem or _step(env, side, doc_id, "unverify")
    problem = problem or _step(env, side, doc_id, "delete")
    if not problem and _read_doc(env, spec, doc_id, side):
        problem = "删除之后 Ap_Vouch 里仍有这张单据"
    return problem


def _cleanup(env: Env, side: str, doc_id: int) -> None:
    # 尽力而为：已审核的先弃审再删；失败不改判定（快照还原兜底）。
    _send(env, {"side": side, "action": "unverify", "id": doc_id})
    _send(env, {"side": side, "action": "delete", "id": doc_id})


def run_cycle(env: Env, spec: CaseSpec, side: str, start: str) -> tuple[str, str]:
    """一整轮（openings-arap-cycle 与 openings_live 共用）：挑往来单位和受控科目 → 预演新增 → 新增 → 审核 →
    SQL 回读期初形态 → 弃审 → 删除 → 回读已不存在。调用方先确认启用月份可以改期初（未结账或已临时打开）。"""
    name, _kind, party, prefix = SIDE_TEXT[side]
    picked = _pick(env, spec, start, side)
    if picked is None:
        return "N/A", f"{start[:4]} 年没有可用的{party}或带{party}辅助核算的{name}受控末级科目（{prefix}）"
    body = _create_body(*picked, side)
    status, payload = _send(env, dict(body, dry_run=True))
    if _not_test(status, payload):
        return "N/A", _NOT_TEST_NA
    problem = _dry_problem(status, payload)
    if problem:
        return "FAIL", problem
    status, payload = _send(env, body)
    doc_id = payload.get("id") if status == 200 else None
    if type(doc_id) is not int or doc_id <= 0:
        return "FAIL", "新增期初单据应 200 并带 id，实际 " + _brief(status, payload)
    problem = _created_problem(payload, start, side) or _lifecycle(env, spec, side, doc_id, _day_before(start))
    if problem:
        _cleanup(env, side, doc_id)
        return "FAIL", f"期初单据 {doc_id}：{problem}"[:240]
    return "PASS", f"期初{name}单 {doc_id}（{picked[0]}，{picked[1]}）新增、审核、期初形态、弃审、删除都对"


def openings_arap_cycle(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    start, closed = ar_state(env, spec)
    if not start:
        return "N/A", "应收款管理未启用"
    if closed == "1":
        return "N/A", f"应收启用月份 {start[:7]} 已结账（openings-arap-closed 覆盖）"
    return run_cycle(env, spec, "ar", start)


OPENINGS_ARAP_OPS = {
    "openings_arap_test_only": openings_arap_test_only,
    "openings_arap_closed": openings_arap_closed,
    "openings_arap_cycle": openings_arap_cycle,
}


def openings_arap_cases() -> list[CaseSpec]:
    # 期初单据与应收单共用编号和桥的锁（new:ar_bill），审核写往来明细（rpt:arap 的报表要避开）；
    # 闸门读 GL_mend 启用月份的结账标志，与月末结账（period:close）互斥。
    write = frozenset(("new:ar_bill", "w:ar_bill", "period:close"))
    read = frozenset(("period:close",))
    return [
        CaseSpec(
            "openings-arap-test-only",
            "非测试账套预演期初单据：登录前 403 test_account_only / feature_disabled（测试账套 N/A）",
            (),
            (Step("custom", sql_name="openings_arap_test_only", id_from="none"),),
            True,
            tags=read,
            routes=(ROUTE,),
        ),
        CaseSpec(
            "openings-arap-closed",
            "应收启用月份已结账时预演新增期初单据 409（未结账、未启用、非测试账套 N/A）",
            (),
            (Step("custom", sql_name="openings_arap_closed", id_from="none"),),
            True,
            tags=read,
            routes=(ROUTE,),
        ),
        CaseSpec(
            "openings-arap-cycle",
            "期初应收单新增、审核、SQL 核对期初形态、弃审、删除（已结账、非测试账套 N/A）",
            (),
            (Step("custom", sql_name="openings_arap_cycle", id_from="none"),),
            True,
            tags=write,
            routes=(ROUTE,),
        ),
    ]
