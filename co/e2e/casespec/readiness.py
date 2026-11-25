"""账套体检（reports/account_readiness），只读。

report-account-readiness：body 为空 → 200；overall 和每一项的 status 都在 ok、warn、fail、unknown 里，
  十个检查项 id 一个不少、不重复，每项带 title、detail、fix_hint、docs_anchor（getting-started.md#<id>）和 safe=true。
  通道的结果因账套而异（空账套会有 fail），所以只查形状，不写死某个通道该是什么状态。
  只有测试数据（testdata.file）里写了 "readiness": {"expect_ready": true} 的期望就绪的通道，才另要求 overall 是 ok 或 warn
  （工作日历快到头时是 warn）；不是 ok 时把不过的项列出来。期望就绪的通道已知不过、又不影响回归的项写进
  "readiness": {"allow_fail": ["prior_gl_close"]}（例如账套本年前几个月总账没结账），这些项 fail 不算失败。

步骤 kind 为 "custom"，sql_name 是操作名（READY_OPS 并入 readonly.READ_OPS）。
"""

from __future__ import annotations

from co.e2e import testdata
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.steps import auth_fields, invoke

ROUTE = "/v1/reports/account_readiness"
CHECK_IDS = (
    "patches",
    "years",
    "calendar",
    "yearly_config",
    "pu_opening",
    "vendor_extradefine",
    "modules",
    "prior_gl_close",
    "workflow",
    "defaults",
)
STATUSES = ("ok", "warn", "fail", "unknown")
_ITEM_KEYS = ("id", "title", "status", "detail", "fix_hint", "docs_anchor", "safe")
_REF_KEY = "readiness"


def expect_ready() -> bool:
    """测试数据 readiness.expect_ready 为 true（布尔）时是期望就绪的通道。没配置或文件不存在都按否。"""
    node = testdata.current().get(_REF_KEY)
    return isinstance(node, dict) and node.get("expect_ready") is True


def allowed_fail() -> frozenset[str]:
    """测试数据 readiness.allow_fail：期望就绪的通道允许 fail 的检查项 id（字符串列表）。其余写法一律当空。"""
    node = testdata.current().get(_REF_KEY)
    names = node.get("allow_fail") if isinstance(node, dict) else None
    if not isinstance(names, list):
        return frozenset()
    return frozenset(name for name in names if isinstance(name, str))


def ref_failed(payload: dict) -> list[str]:
    """期望就绪的通道里 fail 且不在 allow_fail 的项。"""
    allowed = allowed_fail()
    return [item["id"] for item in payload["checks"] if item["status"] == "fail" and item["id"] not in allowed]


def _item_problem(item: object) -> str:
    if not isinstance(item, dict):
        return "检查项不是对象"
    name = item.get("id")
    lost = [key for key in _ITEM_KEYS if key not in item]
    if lost:
        return f"检查项 {name} 缺 " + ",".join(lost)
    if item.get("status") not in STATUSES:
        return f"检查项 {name} 的 status 不认识：{item.get('status')}"
    if item.get("safe") is not True:
        return f"检查项 {name} 的 safe 不是 true"
    if item.get("docs_anchor") != f"getting-started.md#{name}":
        return f"检查项 {name} 的 docs_anchor 不对：{item.get('docs_anchor')}"
    return ""


def shape_problem(payload: dict) -> str:
    """形状：overall、十个检查项、每项的键和取值。"""
    if payload.get("ok") is not True:
        return "响应缺 ok"
    if payload.get("overall") not in STATUSES:
        return f"overall 不认识：{payload.get('overall')}"
    checks = payload.get("checks")
    if not isinstance(checks, list):
        return "checks 不是列表"
    for item in checks:
        problem = _item_problem(item)
        if problem:
            return problem
    ids = [item["id"] for item in checks]
    lost = [name for name in CHECK_IDS if name not in ids]
    if lost:
        return "缺检查项 " + ",".join(lost)
    if len(ids) != len(set(ids)):
        return "检查项 id 重复"
    return ""


def not_ok(payload: dict) -> str:
    """不是 ok 的项：id=status，逗号分隔。"""
    return ",".join(f"{item['id']}={item['status']}" for item in payload["checks"] if item["status"] != "ok")


def account_readiness(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del spec, step
    status, payload = invoke(env.client, ROUTE, auth_fields(env))
    if status != 200:
        return "FAIL", f"账套体检应 200，实际 {status} {payload.get('code')} {payload.get('message')}"[:240]
    problem = shape_problem(payload)
    if problem:
        return "FAIL", problem[:240]
    overall = payload["overall"]
    rest = not_ok(payload)
    if expect_ready() and overall not in ("ok", "warn") and (overall != "fail" or ref_failed(payload)):
        return "FAIL", f"期望就绪的通道 overall 应为 ok 或 warn，实际 {overall}：{rest}"[:240]
    detail = f"overall {overall}（{rest}）" if rest else f"overall {overall}"
    return "PASS", detail[:240]


READY_OPS = {"account_readiness": account_readiness}


def readiness_cases() -> list[CaseSpec]:
    # 采购期初那一项读 GL_mend 第 0 期的采购标志，与期初记账用例（opening:pu）错开。
    step = Step("custom", sql_name="account_readiness", id_from="none")
    return [
        CaseSpec(
            "report-account-readiness",
            "报表：账套体检（十项检查齐全、状态取值合法；期望就绪的通道另要求 ok 或 warn）",
            (),
            (step,),
            True,
            True,
            tags=frozenset(("opening:pu",)),
            routes=(ROUTE,),
        )
    ]
