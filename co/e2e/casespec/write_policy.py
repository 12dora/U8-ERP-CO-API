"""写入策略（桥 config.json 的 writePolicyFile）：临时换本通道桥的策略文件，核对各步拒绝，退出时写回原文件。

wp-health：/health 的 write_policy：state ok、version 1、有 loaded_at、freeze、window_open。
wp-deny-unlisted-type：策略只许本账套 sale_order/create → 总账凭证新建预演 403 write_not_allowed（detail type/op）；
  销售订单新建预演放行（有 sale_master 时 200 dry_run，没有时用占位正文，只要求不是策略拒绝）。
wp-freeze-account：冻结本账套（带原因）→ health freeze.accounts 含本账套；写预演 503 write_frozen，消息带原因；读取仍 200。
wp-window：denyDates 写今天 → health window_open 为 false；写预演 503 write_window。
wp-operator-deny：operators.deny 是本通道操作员 → 写预演 403 operator_not_allowed。
wp-max-lines：maxLines 1，两行新建预演 → 400 write_limit，field 为 lines。
wp-quota-minute：只在可造数的通道（account.seedable）跑，其余 N/A。writesPerMinute 1：先预演（窗口里已有别的写入时
  按 retry_after_seconds 等一次），真建一张销售订单，再预演 → 429 write_quota（detail.retry_after_seconds > 0）；
  写回原策略后删掉新建的订单。
wp-invalid-keeps-last：先冻结本账套，再写入未知键的无效内容 → health state invalid、version 仍在；写预演仍 503 write_frozen
  （保留上一份有效策略）。
wp-tier2-still-test-only：策略放行本账套全部写入，月末结账预演仍 403 test_account_only（测试账套名单独立在前；
  桥总开关未打开时 403 feature_disabled 同样 PASS）；本通道是测试账套（结果不是这两种拒绝）时 N/A。

除 wp-quota-minute 外只发预演和登录前就被拒的写，不改账套。桥没有 write_policy 或 state 为 off 时全部 N/A；
state 不是 ok 时 FAIL（通道策略本身坏了，先修）。全部独占（exclusive）：换策略期间本通道不跑别的用例。
写回原策略重试后仍失败时本用例 FAIL，通道标记阻塞，之后的用例都记 SKIP（wpolicy.lane_blocked）。
步骤 kind 为 "custom"，sql_name 是操作名（WRITE_POLICY_OPS 并入 ops_run）。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from co.client.u8co_client import U8CoError
from co.e2e.casespec.bodies import sale_body
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.steps import auth_fields
from co.e2e.casespec.tier2 import tier2_refused
from co.e2e.settings import conf
from co.e2e.wpolicy import PolicyNotRestored, PolicySwap, health_policy, policy_swap

CREATE = "/v1/vouchers/create"
DELETE = "/v1/vouchers/delete"
LIST = "/v1/vouchers/list"
GL_CREATE = "/v1/gl/vouchers/create"
PERIODS = "/v1/periods/close"
HEALTH = "/v1/health"
_REASON = "回归冻结"
_MEMO = "写入策略回归"
_QUOTA_WAIT_MAX = 65
Result = tuple[str, str]


def send(env: Env, route: str, body: dict) -> tuple[int, dict]:
    """调桥；错误时带上 field 和 detail（429 的 retry_after_seconds 在 detail 里）。"""
    fields = auth_fields(env)
    fields.update(body)
    try:
        return 200, env.client.call(route, fields)
    except U8CoError as exc:
        return exc.status, {"code": exc.code, "message": exc.message, "field": exc.field, "detail": exc.detail or {}}


def brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def expect(status: int, payload: dict, want: tuple[int, str], what: str) -> str:
    if (status, payload.get("code")) == want:
        return ""
    return f"{what}应 {want[0]} {want[1]}，实际 " + brief(status, payload)


def placeholder_sale(env: Env, lines: int = 1) -> dict:
    # 登录前就会被策略拒绝的写入用占位正文：不依赖挑单；万一没被拒，也只是预演，U8 回滚。
    line = {"cInvCode": "WP-NONE", "iQuantity": 1}
    head = {"cCusCode": "WP-NONE", "dDate": env.today, "cMemo": _MEMO}
    return {"type": "sale_order", "head": head, "lines": [dict(line) for _ in range(lines)]}


def dry(body: dict) -> dict:
    return dict(body, dry_run=True)


def acc_policy(env: Env, entry: dict | None = None, **extra: object) -> dict:
    """本账套一条（缺省放行全部写入）的策略；extra 是顶层键（freeze、denyDates、defaults……）。"""
    rule = {"allow": [{"type": "*", "ops": ["*"]}]}
    rule.update(entry or {})
    policy: dict = {"version": 1, "reloadSeconds": 1, "unlisted": "deny", "accounts": {env.operator.acc: rule}}
    policy.update(extra)
    return policy


def guarded(env: Env, body: Callable[[Env, PolicySwap], Result]) -> Result:
    """策略关闭时 N/A；不是 ok 时 FAIL；否则换策略跑 body，退出时写回原文件并等重载。"""
    found = health_policy(env.client)
    if not found or found.get("state") in (None, "off"):
        return "N/A", "桥没有配置写入策略（health 没有 write_policy 或 state 为 off）"
    if found.get("state") != "ok":
        return "FAIL", f"通道的写入策略不可用：state={found.get('state')}，先修好策略文件"
    try:
        with policy_swap(env.ctx.remote, env.client) as swap:
            return body(env, swap)
    except PolicyNotRestored as exc:
        # 本用例记 FAIL；通道已标记阻塞，之后的用例由 runner 记 SKIP。
        return "FAIL", str(exc)[:240]


def wp_health(env: Env, _spec: CaseSpec, _step: Step) -> Result:
    found = health_policy(env.client)
    if not found or found.get("state") in (None, "off"):
        return "N/A", "桥没有配置写入策略（health 没有 write_policy 或 state 为 off）"
    freeze = found.get("freeze")
    shaped = isinstance(freeze, dict) and {"global", "accounts"} <= set(freeze)
    problems = [
        "state 应为 ok" if found.get("state") != "ok" else "",
        "version 应为 1" if found.get("version") != 1 else "",
        "缺 loaded_at" if not found.get("loaded_at") else "",
        "" if shaped else "freeze 应有 global、accounts",
        "window_open 应为布尔值" if not isinstance(found.get("window_open"), bool) else "",
    ]
    bad = [text for text in problems if text]
    if bad:
        return "FAIL", ("；".join(bad) + f"：{found}")[:240]
    return "PASS", f"state ok，version 1，loaded_at {found.get('loaded_at')}"


def _deny_type(env: Env, swap: PolicySwap) -> Result:
    swap.apply(acc_policy(env, {"allow": [{"type": "sale_order", "ops": ["create"]}]}))
    lines = [{"account": "1001", "digest": _MEMO, "debit": 1}, {"account": "1001", "digest": _MEMO, "credit": 1}]
    status, payload = send(env, GL_CREATE, dry({"head": {"date": env.today}, "lines": lines}))
    problem = expect(status, payload, (403, "write_not_allowed"), "总账凭证新建预演")
    detail = payload.get("detail") or {}
    if not problem and (detail.get("type"), detail.get("op")) != ("gl", "create"):
        problem = f"write_not_allowed 的 detail 应为 gl/create，实际 {detail}"
    if problem:
        return "FAIL", problem
    real = bool(env.bag.get("sale_master"))
    body = sale_body(env) if real else placeholder_sale(env)
    status, payload = send(env, CREATE, dry(body))
    if real and (status != 200 or payload.get("dry_run") is not True):
        return "FAIL", "销售订单新建预演应 200 dry_run，实际 " + brief(status, payload)
    if not real and payload.get("code") in ("write_not_allowed", "operator_not_allowed", "write_policy_unavailable"):
        return "FAIL", "销售订单新建预演不应被策略拒绝，实际 " + brief(status, payload)
    note = "200 dry_run" if real else f"占位正文 {status} {payload.get('code')}（没有 sale_master，只核未被策略拒绝）"
    return "PASS", "总账凭证 403 write_not_allowed；销售订单新建预演 " + note


def _freeze(env: Env, swap: PolicySwap) -> Result:
    found = swap.apply(acc_policy(env, freeze={"accounts": [env.operator.acc], "reason": _REASON}))
    if env.operator.acc not in ((found.get("freeze") or {}).get("accounts") or []):
        return "FAIL", f"health 的 freeze.accounts 应含 {env.operator.acc}：{found.get('freeze')}"
    status, payload = send(env, CREATE, dry(placeholder_sale(env)))
    problem = expect(status, payload, (503, "write_frozen"), "冻结后写预演")
    if not problem and _REASON not in str(payload.get("message")):
        problem = f"write_frozen 的消息应带原因「{_REASON}」：{payload.get('message')}"
    if problem:
        return "FAIL", problem
    status, payload = send(env, LIST, {"type": "sale_order", "limit": 1})
    if status != 200:
        return "FAIL", "冻结时读取应 200，实际 " + brief(status, payload)
    return "PASS", "冻结后写预演 503 write_frozen（带原因），读取 200"


def _window(env: Env, swap: PolicySwap) -> Result:
    found = swap.apply(acc_policy(env, denyDates=[env.today]))
    if found.get("window_open") is not False:
        return "FAIL", f"denyDates 含今天时 health window_open 应为 false：{found.get('window_open')}"
    status, payload = send(env, CREATE, dry(placeholder_sale(env)))
    problem = expect(status, payload, (503, "write_window"), "禁写日写预演")
    return ("FAIL", problem) if problem else ("PASS", f"denyDates {env.today}：503 write_window")


def _operator(env: Env, swap: PolicySwap) -> Result:
    swap.apply(acc_policy(env, {"operators": {"allow": [], "deny": [env.operator.code]}}))
    status, payload = send(env, CREATE, dry(placeholder_sale(env)))
    problem = expect(status, payload, (403, "operator_not_allowed"), "操作员在 deny 名单时写预演")
    return ("FAIL", problem) if problem else ("PASS", "403 operator_not_allowed")


def _max_lines(env: Env, swap: PolicySwap) -> Result:
    swap.apply(acc_policy(env, {"quotas": {"maxLines": 1}}))
    status, payload = send(env, CREATE, dry(placeholder_sale(env, 2)))
    problem = expect(status, payload, (400, "write_limit"), "两行新建预演")
    if not problem and payload.get("field") != "lines":
        problem = f"write_limit 的 field 应为 lines，实际 {payload.get('field')!r}"
    return ("FAIL", problem) if problem else ("PASS", "maxLines 1，两行：400 write_limit")


def _retry_after(payload: dict) -> int:
    value = (payload.get("detail") or {}).get("retry_after_seconds")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _quota_probe(env: Env, body: dict) -> str:
    """预演一次；分钟窗口里已有别的写入时按 retry_after_seconds 等一次再预演。返回问题，空串表示可以真写。"""
    status, payload = send(env, CREATE, dry(body))
    if status == 429 and payload.get("code") == "write_quota":
        time.sleep(min(_retry_after(payload) + 1, _QUOTA_WAIT_MAX))
        status, payload = send(env, CREATE, dry(body))
    if status != 200:
        return "限额内的预演应 200，实际 " + brief(status, payload)
    return ""


def _quota_body(env: Env, swap: PolicySwap, made: list[int]) -> Result:
    swap.apply(acc_policy(env, defaults={"writesPerMinute": 1}))
    body = sale_body(env)
    problem = _quota_probe(env, body)
    if problem:
        return "FAIL", problem
    status, payload = send(env, CREATE, body)
    if status != 200 or not isinstance(payload.get("id"), int):
        return "FAIL", "真建销售订单应 200，实际 " + brief(status, payload)
    made.append(payload["id"])
    status, payload = send(env, CREATE, dry(body))
    problem = expect(status, payload, (429, "write_quota"), "额满后写预演")
    if not problem and _retry_after(payload) <= 0:
        problem = f"write_quota 的 detail.retry_after_seconds 应为正整数：{payload.get('detail')}"
    if problem:
        return "FAIL", problem
    return "PASS", f"真写 1 张后预演 429 write_quota，retry_after {_retry_after(payload)} 秒"


def _drop(env: Env, made: list[int]) -> str:
    for doc in made:
        status, payload = send(env, DELETE, {"type": "sale_order", "id": doc})
        if status != 200 or payload.get("deleted") is not True:
            return f"删除新建的销售订单 {doc} 失败：" + brief(status, payload)
    return ""


def wp_quota_minute(env: Env, _spec: CaseSpec, _step: Step) -> Result:
    if not conf().seedable:
        return "N/A", "只在可造数的通道（account.seedable）真写；本通道只跑预演类用例"
    if not env.bag.get("sale_master"):
        return "N/A", "没有挑到 sale_master，无法真建销售订单"
    made: list[int] = []
    try:
        result = guarded(env, lambda e, swap: _quota_body(e, swap, made))
    finally:
        # 策略已写回（不再限额）才删：限额期间删除也会被 429 拒。
        dropped = _drop(env, made)
    if dropped:
        return "FAIL", dropped
    return result


def _invalid(env: Env, swap: PolicySwap) -> Result:
    swap.apply(acc_policy(env, freeze={"accounts": [env.operator.acc], "reason": _REASON}))
    found = swap.apply('{"version": 1, "e2eUnknownKey": true}', want="invalid")
    if found.get("version") != 1:
        return "FAIL", f"无效内容时应保留上一份有效策略，health version 应为 1：{found}"[:240]
    status, payload = send(env, CREATE, dry(placeholder_sale(env)))
    problem = expect(status, payload, (503, "write_frozen"), "无效内容时写预演（上一份策略冻结了本账套）")
    return ("FAIL", problem) if problem else ("PASS", "state invalid，上一份策略仍生效：503 write_frozen")


def _tier2(env: Env, swap: PolicySwap) -> Result:
    swap.apply(acc_policy(env))
    body = {"module": "gl", "action": "close", "fiscal_year": int(env.today[:4]), "period": 1, "dry_run": True}
    status, payload = send(env, PERIODS, body)
    if tier2_refused(status, payload):
        return "PASS", f"策略放行全部写入，月末结账预演仍 403 {payload.get('code')}"
    if payload.get("code") in ("write_not_allowed", "operator_not_allowed", "write_policy_unavailable"):
        return "FAIL", "策略放行 * 时不应被策略拒绝，实际 " + brief(status, payload)
    return "N/A", "本通道是测试账套（月末结账预演 " + brief(status, payload) + "）"


def _wrap(body: Callable[[Env, PolicySwap], Result]) -> Callable[[Env, CaseSpec, Step], Result]:
    def run(env: Env, _spec: CaseSpec, _step: Step) -> Result:
        return guarded(env, body)

    return run


WRITE_POLICY_OPS = {
    "wp_health": wp_health,
    "wp_deny_type": _wrap(_deny_type),
    "wp_freeze": _wrap(_freeze),
    "wp_window": _wrap(_window),
    "wp_operator": _wrap(_operator),
    "wp_max_lines": _wrap(_max_lines),
    "wp_quota_minute": wp_quota_minute,
    "wp_invalid": _wrap(_invalid),
    "wp_tier2": _wrap(_tier2),
}

# (用例名, 标题, 操作名, 路由)；wp-quota-minute 另有挑单和 new: 标签，见 write_policy_cases。
_CASES = (
    ("wp-health", "写入策略健康检查：state ok、version、loaded_at、freeze、window_open", "wp_health", (HEALTH,)),
    ("wp-deny-unlisted-type", "策略只许销售订单新建：总账凭证预演 403 write_not_allowed，销售订单预演放行", "wp_deny_type",
     (GL_CREATE, CREATE)),
    ("wp-freeze-account", "冻结本账套：写预演 503 write_frozen（带原因），读取 200", "wp_freeze", (CREATE, LIST)),
    ("wp-window", "denyDates 今天：window_open false，写预演 503 write_window", "wp_window", (CREATE,)),
    ("wp-operator-deny", "操作员在 deny 名单：写预演 403 operator_not_allowed", "wp_operator", (CREATE,)),
    ("wp-max-lines", "maxLines 1：两行新建预演 400 write_limit", "wp_max_lines", (CREATE,)),
    ("wp-invalid-keeps-last", "无效策略内容：state invalid，上一份策略仍生效", "wp_invalid", (CREATE,)),
    ("wp-tier2-still-test-only", "策略放行全部写入，月末结账预演仍 403 test_account_only / feature_disabled（测试账套 N/A）", "wp_tier2",
     (PERIODS,)),
)


def write_policy_cases() -> list[CaseSpec]:
    # 换的是整个桥的策略文件，期间别的用例的写入也会被拒：全部独占。wpolicy 标签让它们彼此也互斥（独占已保证）。
    tags = frozenset(("wpolicy", "w:sale_order"))

    def case(name: str, title: str, op: str, routes: tuple[str, ...]) -> CaseSpec:
        step = Step("custom", sql_name=op, id_from="none")
        return CaseSpec(name, title, (), (step,), name != "wp-health", tags=tags, routes=routes, exclusive=True)

    quota = CaseSpec(
        "wp-quota-minute",
        "writesPerMinute 1：真建一张销售订单后预演 429 write_quota，写回策略后删除（只在可造数的通道，其余 N/A）",
        ("sale_master",),
        (Step("custom", sql_name="wp_quota_minute", id_from="none"),),
        True,
        tags=tags | {"new:sale_order"},
        routes=(CREATE, DELETE),
        exclusive=True,
    )
    return [case(*row) for row in _CASES] + [quota]
