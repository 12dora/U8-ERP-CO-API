"""存货核算记账、期末处理（ia/post、ia/period_end）。只对桥 config.json 的 testAccounts 里的账套开放，
其余账套登录前 403 test_account_only。

ia-post-dry：预演本年本月的正常单据记账（dry_run: true，on_uncosted 缺省）→ 200 标准预演体（mode rollback、
  action ia_post），detail.counts 带 subsidiary_month；第一次 409「…U8 无法确定成本…」时改用 on_uncosted=skip 再预演一次。
  回读本月 IA_Subsidiary / IA_Summary 行数和 bflag_IA 与预演前相同（预演回滚）。
  本月存货核算已结账，或 409 是已知的环境原因（接口暂不支持、未启用 / 早于启用月份、已结账）记 N/A，原文写进说明；
  其他 409（含 u8_rejected、桥没对上号的「存货核算拒绝本次操作（编号 …）」）一律 FAIL。
ia-period-end-dry：本月已有 IA_Subsidiary 行、又没结账时预演期末处理（action run）→ 200 预演体（action ia_period_end），
  回读同上不变；本月没有明细账（预演记账会回滚，造不出来）、已结账或已知环境原因的 409 记 N/A，其他 409 FAIL。
ia-bad-action：action 不支持，登录前 400 bad_request 且 field 为 action（字段校验在测试账套名单之前，任何账套都是 400）。

前两条的账套不在 testAccounts 里时桥回 403 test_account_only（桥总开关未打开时 feature_disabled），记 N/A（桥不公开名单，只能按这个错误码判断）。
两条都在事务里跑完整的 U8 存货核算过程（生产规模的月份要一到三分钟），持全局写闸门、锁库存单据行，所以独占。
步骤 kind 为 "custom"，sql_name 是操作名（IA_OPS 并入 ops_run）。
"""

from __future__ import annotations

from co.client.u8co_errors import U8CoError

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.ia_post_sql import ia_month_sql
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke
from co.e2e.casespec.tier2 import tier2_refused

POST_ROUTE = "/v1/ia/post"
PERIOD_END_ROUTE = "/v1/ia/period_end"
_REFUSED = frozenset(("state_mismatch", "u8_rejected"))
# 记 N/A 的已知环境原因（桥的中文 message 片段）：账套设置不支持、存货核算未启用或该月早于启用月份、已结账、
# 期末处理时该月还没有记账数据。其余 409 都按回归失败报。
_ENV_REASONS = ("接口暂不支持", "启用", "已结账", "没有记账数据")
_UNCOSTED = "无法确定成本"


def read_month(env: Env, spec: CaseSpec, year: int, month: int) -> tuple[int, ...] | None:
    """(明细账行数, 汇总表行数, 已期末处理的汇总行数, 结账标志)；读不出或格式不对为 None。"""
    parts = first_cell(run_guarded(env, ia_month_sql(year, month), spec.name)).split("|")
    if len(parts) != 4 or not all(part.strip().isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _send(env: Env, route: str, body: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(body)
    return invoke(env.client, route, fields)


def _today(env: Env) -> tuple[int, int]:
    text = str(env.today or "")
    return int(text[:4]), int(text[5:7])


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _not_test(status: int, payload: dict) -> bool:
    return tier2_refused(status, payload)


def _refused(status: int, payload: dict) -> bool:
    return status == 409 and payload.get("code") in _REFUSED


def _env_refusal(status: int, payload: dict) -> bool:
    if status != 409 or payload.get("code") != "state_mismatch":
        return False
    message = str(payload.get("message") or "")
    return any(reason in message for reason in _ENV_REASONS)


def _refusal_verdict(status: int, payload: dict, what: str) -> tuple[str, str]:
    """409 的判定：已知环境原因 N/A，其余 FAIL。"""
    if _env_refusal(status, payload):
        return "N/A", (f"账套不满足{what}条件：" + str(payload.get("message")))[:240]
    return "FAIL", (f"{what}被拒，不是已知的环境原因：" + _brief(status, payload))[:240]


def _dry_problem(payload: dict, action: str) -> str:
    shape = (payload.get("dry_run"), payload.get("mode"), payload.get("action"))
    if shape != (True, "rollback", action):
        return f"预演响应应 dry_run 为 true、mode 为 rollback、action 为 {action}：{payload}"[:240]
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    counts = detail.get("counts")
    if not isinstance(counts, dict) or ("subsidiary_month" not in counts and "summary_month" not in counts):
        return f"detail.counts 应是诊断计数（含 subsidiary_month 或 summary_month）：{counts}"[:240]
    return ""


def _month_state(env: Env, spec: CaseSpec) -> tuple[int, int, tuple[int, ...] | None]:
    year, month = _today(env)
    return year, month, read_month(env, spec, year, month)


def _judge(env: Env, spec: CaseSpec, before: tuple[int, ...], reply: tuple[int, dict, str]) -> str:
    """预演的响应形状和回读：reply 是 (状态码, 响应, 应有的预演 action)。返回问题说明，没问题为空串。"""
    status, payload, action = reply
    if status != 200:
        return "预演应 200，实际 " + _brief(status, payload)
    problem = _dry_problem(payload, action)
    if problem:
        return problem
    if read_month(env, spec, *_today(env)) != before:
        return "预演之后本月 IA_Subsidiary / IA_Summary 行数或 bflag_IA 变了（应回滚）"
    return ""


def ia_post_dry(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    year, month, before = _month_state(env, spec)
    if before is None:
        return "FAIL", "读不出本月的存货核算状态"
    if before[3] == 1:
        return "N/A", f"{year} 年 {month} 期存货核算已结账"
    body = {"fiscal_year": year, "period": month, "action": "post", "dry_run": True}
    status, payload = _send(env, POST_ROUTE, body)
    if _not_test(status, payload):
        return "N/A", "桥没开第二级写入或账套不在 testAccounts 里（403 feature_disabled / test_account_only）"
    note = ""
    if _refused(status, payload) and _UNCOSTED in str(payload.get("message")):
        note = "；缺省 refuse 被拒（有 U8 无法确定成本的存货），改用 skip"
        status, payload = _send(env, POST_ROUTE, dict(body, on_uncosted="skip"))
    if _refused(status, payload):
        return _refusal_verdict(status, payload, "记账")
    problem = _judge(env, spec, before, (status, payload, "ia_post"))
    if problem:
        return "FAIL", problem
    counts = payload["detail"]["counts"]
    return "PASS", (f"预演记账 {year} 年 {month} 期：200，事务内明细账 {counts.get('subsidiary_month')} 行，回读不变" + note)[:240]


def ia_period_end_dry(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    year, month, before = _month_state(env, spec)
    if before is None:
        return "FAIL", "读不出本月的存货核算状态"
    if before[0] == 0:
        return "N/A", f"{year} 年 {month} 期没有存货明细账（IA_Subsidiary）行，期末处理无可处理"
    if before[3] == 1:
        return "N/A", f"{year} 年 {month} 期存货核算已结账"
    body = {"fiscal_year": year, "period": month, "action": "run", "dry_run": True}
    status, payload = _send(env, PERIOD_END_ROUTE, body)
    if _not_test(status, payload):
        return "N/A", "桥没开第二级写入或账套不在 testAccounts 里（403 feature_disabled / test_account_only）"
    if _refused(status, payload):
        return _refusal_verdict(status, payload, "期末处理")
    problem = _judge(env, spec, before, (status, payload, "ia_period_end"))
    if problem:
        return "FAIL", problem
    return "PASS", f"预演期末处理 {year} 年 {month} 期：200，明细账 {before[0]} 行，回读不变"


def ia_bad_action(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    """action 不支持：400 bad_request，field 必须是 action（别的字段出错也是 400，不能算通过）。"""
    del spec, step
    fields = auth_fields(env)
    fields.update({"fiscal_year": 2026, "period": 1, "action": "xx"})
    try:
        env.client.call(POST_ROUTE, fields)
    except U8CoError as exc:
        got = (exc.status, exc.code, exc.field)
        if got == (400, "bad_request", "action"):
            return "PASS", "400 bad_request，field 为 action"
        return "FAIL", f"应 400 bad_request、field 为 action，实际 {got} {exc.message}"[:240]
    return "FAIL", "action 不支持却成功了"


IA_OPS = {"ia_post_dry": ia_post_dry, "ia_period_end_dry": ia_period_end_dry, "ia_bad_action": ia_bad_action}


def ia_cases() -> list[CaseSpec]:
    # 存货核算过程改库存单据行（记账人）和 IA_* 表，又读 GL_mend 的结账标志：与月末结账、期初记账错开，并且独占。
    tags = frozenset(("ia:post", "period:close", "opening:pu"))
    post = Step("custom", sql_name="ia_post_dry", id_from="none")
    end = Step("custom", sql_name="ia_period_end_dry", id_from="none")
    bad = Step("custom", sql_name="ia_bad_action", id_from="none")
    return [
        CaseSpec(
            "ia-post-dry",
            "预演本月正常单据记账：200 dry_run 带 detail.counts，IA_Subsidiary 行数不变（已结账、非测试账套 N/A）",
            (),
            (post,),
            True,
            tags=tags,
            routes=(POST_ROUTE,),
            exclusive=True,
        ),
        CaseSpec(
            "ia-period-end-dry",
            "本月有存货明细账时预演期末处理：200 dry_run，回读不变（没有明细账、非测试账套 N/A）",
            (),
            (end,),
            True,
            tags=tags,
            routes=(PERIOD_END_ROUTE,),
            exclusive=True,
        ),
        CaseSpec(
            "ia-bad-action",
            "存货核算记账 action 不支持：登录前 400，field 为 action",
            (),
            (bad,),
            tags=frozenset(("probe",)),
            routes=(POST_ROUTE,),
        ),
    ]
