"""库存期初结存单（stock_opening，U8 单据类型 34）。新增走 U8 官方 EAI 导入（每行一张单据），审核、弃审、删除走
USERPCO；新增、删除、审核、弃审只对桥 config.json 的 testAccounts 里的账套开放，修改一律 400。

stock-opening-test-only：预演审核一张不存在的期初结存单，非测试账套应登录前 403 test_account_only
  「期初结存单只对配置为测试账套的账套开放」（桥总开关未打开时 403 feature_disabled，同样 PASS）；回 200 / 404 / 409 说明账套在名单里，记 N/A。
stock-opening-closed：启用年度起已有库存月结（GL_mend iperiod > 0 且 bflag_ST=1）的测试账套上真新增一行期初
  （预演是 validate，停在 EAI 之前，碰不到 U8 的规则）→ 409 u8_rejected「库存启用的第一个月已月结…」，
  前后 SQL 数 rdrecord34 张数不变。没有月结、库存未启用、存货核算已期初记账、挑不到仓库存货、非测试账套记 N/A。
  意外建成时尽力删除（快照还原兜底）。
stock-opening-update-refused：修改一律 400「期初结存单不能修改，请删除后重新录入」（登录前，任何账套）。

步骤 kind 为 "custom"，sql_name 是操作名（STOCK_OPENING_OPS 并入 ops_run）。
"""

from __future__ import annotations

import re

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_opening_sql import stock_pick_sql, stock_state_sql
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke
from co.e2e.casespec.tier2 import TIER2_TEXT, refusal_verdict, tier2_refused

KIND = "stock_opening"
CREATE = "/v1/vouchers/create"
DELETE = "/v1/vouchers/delete"
UPDATE = "/v1/vouchers/update"
VERIFY = "/v1/vouchers/verify"
_TEST_ONLY_TEXT = "期初结存单只对配置为测试账套的账套开放"
# U8 原文「库存启用的第一个月已月结,不可以增加删除期初!」；桥的说明里写作「已经月结」，分两段比，两种写法都认。
_CLOSED_TEXT = ("库存启用的第一个月", "月结")
_NO_UPDATE_TEXT = "期初结存单不能修改，请删除后重新录入"
_NOT_TEST_NA = f"桥没开第二级写入或账套不在 testAccounts 里（{TIER2_TEXT}，stock-opening-test-only 覆盖）"
_PROBE_ID = 2147483647
_YMD = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")


def _send(env: Env, route: str, body: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(body)
    return invoke(env.client, route, fields)


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _not_test(status: int, payload: dict) -> bool:
    return tier2_refused(status, payload)


def stock_state(env: Env, spec: CaseSpec) -> tuple[str, str, str, int]:
    """(库存启用日期, 有已月结月份 "0" / "1", 存货核算已期初记账 "0" / "1", 期初结存单张数；读不到为 -1)。"""
    parts = [part.strip() for part in first_cell(run_guarded(env, stock_state_sql(), spec.name)).split("|")]
    parts += [""] * (4 - len(parts))
    start = parts[0] if _YMD.match(parts[0]) else ""
    count = int(parts[3]) if parts[3].isdigit() else -1
    return start, parts[1], parts[2], count


def _pick(env: Env, spec: CaseSpec) -> tuple[str, str] | None:
    wh, _sep, inv = first_cell(run_guarded(env, stock_pick_sql(), spec.name)).partition("|")
    wh, inv = wh.strip(), inv.strip()
    return (wh, inv) if wh and inv else None


def stock_opening_test_only(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del spec, step
    status, payload = _send(env, VERIFY, {"type": KIND, "id": _PROBE_ID, "action": "verify", "dry_run": True})
    if _not_test(status, payload):
        return refusal_verdict(payload, _TEST_ONLY_TEXT, "预演期初结存单审核")
    if status in (200, 404) or (status == 409 and payload.get("code") == "state_mismatch"):
        return "N/A", f"账套在桥的 testAccounts 里（预演回 {status}）"
    return "FAIL", f"预演期初结存单审核应 {TIER2_TEXT}（测试账套 404 或 409），实际 " + _brief(status, payload)


def stock_opening_update_refused(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del spec, step
    body = {"type": KIND, "id": _PROBE_ID, "head": {"cmemo": "回归"}}
    status, payload = _send(env, UPDATE, body)
    if status == 400 and payload.get("code") == "bad_request" and _NO_UPDATE_TEXT in str(payload.get("message")):
        return "PASS", "修改期初结存单：400「期初结存单不能修改」"
    return "FAIL", f"修改期初结存单应 400 bad_request「{_NO_UPDATE_TEXT}」，实际 " + _brief(status, payload)


def _closed_na(start: str, closed: str, ia_posted: str, count: int) -> str:
    if not start:
        return "库存管理未启用"
    if closed != "1":
        return f"库存启用（{start}）以来没有已月结的月份（U8 允许增删期初）"
    if ia_posted == "1":
        return "存货核算已期初记账（桥先回 409 state_mismatch，碰不到 U8 的月结规则）"
    if count < 0:
        return "读不到期初结存单张数"
    return ""


def _cleanup(env: Env, payload: dict) -> None:
    # 尽力而为：意外建成的单据逐张删除；失败不改判定（快照还原兜底）。
    docs = payload.get("docs") if isinstance(payload.get("docs"), list) else [payload]
    for doc in docs:
        doc_id = doc.get("id") if isinstance(doc, dict) else None
        if type(doc_id) is int and doc_id > 0:
            _send(env, DELETE, {"type": KIND, "id": doc_id})


def stock_opening_closed(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    start, closed, ia_posted, before = stock_state(env, spec)
    reason = _closed_na(start, closed, ia_posted, before)
    if reason:
        return "N/A", reason
    picked = _pick(env, spec)
    if picked is None:
        return "N/A", "挑不到不管货位的仓库或不管批次、保质期的单计量存货"
    head = {"cwhcode": picked[0], "cmemo": "回归期初（应被拒绝）"}
    lines = [{"cinvcode": picked[1], "iquantity": 1}]
    status, payload = _send(env, CREATE, {"type": KIND, "head": head, "lines": lines})
    if _not_test(status, payload):
        return "N/A", _NOT_TEST_NA
    after = stock_state(env, spec)[3]
    if status == 200:
        _cleanup(env, payload)
        return "FAIL", f"库存已有月结时新增期初结存单应被 U8 拒绝，却建成了：{payload}"[:240]
    message = str(payload.get("message"))
    if status != 409 or payload.get("code") != "u8_rejected" or not all(part in message for part in _CLOSED_TEXT):
        return "FAIL", "新增应 409 u8_rejected「库存启用的第一个月已月结…」，实际 " + _brief(status, payload)
    if after != before:
        return "FAIL", f"U8 拒绝后期初结存单张数应不变：之前 {before}，之后 {after}"
    return "PASS", f"库存已月结：新增期初结存单（{picked[0]} / {picked[1]}）409 u8_rejected，没有留下单据"


STOCK_OPENING_OPS = {
    "stock_opening_test_only": stock_opening_test_only,
    "stock_opening_closed": stock_opening_closed,
    "stock_opening_update_refused": stock_opening_update_refused,
}


def stock_opening_cases() -> list[CaseSpec]:
    # 新增与桥的 new:stock_opening 锁同名、动现存量（stock）；闸门读 GL_mend 的结账和期初记账标志，与月末结账互斥。
    write = frozenset(("new:stock_opening", "w:stock_opening", "stock", "period:close"))
    read = frozenset(("period:close",))
    return [
        CaseSpec(
            "stock-opening-test-only",
            "非测试账套预演期初结存单审核：登录前 403 test_account_only / feature_disabled（测试账套 N/A）",
            (),
            (Step("custom", sql_name="stock_opening_test_only", id_from="none"),),
            True,
            tags=read,
            routes=(VERIFY,),
        ),
        CaseSpec(
            "stock-opening-closed",
            "库存已有月结时新增期初结存单 409 u8_rejected，张数不变（没有月结、非测试账套 N/A）",
            (),
            (Step("custom", sql_name="stock_opening_closed", id_from="none"),),
            True,
            tags=write,
            routes=(CREATE,),
        ),
        CaseSpec(
            "stock-opening-update-refused",
            "修改期初结存单一律 400「期初结存单不能修改，请删除后重新录入」",
            (),
            (Step("custom", sql_name="stock_opening_update_refused", id_from="none"),),
            True,
            tags=frozenset(("probe",)),
            routes=(UPDATE,),
        ),
    ]
