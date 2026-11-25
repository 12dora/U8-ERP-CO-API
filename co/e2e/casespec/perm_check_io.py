"""权限核对（perm_check）的桥调用和对照 SQL：评估操作员凭据、perm/evaluate、perm/snapshot、操作员枚举、账套库事实。

评估操作员（桥 permEvaluateOperators 里的操作员）凭据来自通道配置 account.perm_evaluator_file（格式同测试操作员凭据，
权限 0600）；口令进 Run.needles，报告里打码。没配置时调用方记 N/A。
操作员枚举走桥的只读档案 operator（ArcUa：本账套有授权、年度窗口同 PermLoad），只认账套主管能读：先用测试操作员，
403 时改用评估操作员。
"""

from __future__ import annotations

from pathlib import Path

from co.e2e.cases import Operator, load_operator
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec
from co.e2e.casespec.flip_util import rows
from co.e2e.casespec.perm_check_sql import columns_sql, lane_sql, op_sql
from co.e2e.casespec.steps import invoke
from co.e2e.perm_oracle import LaneFacts, OpFacts, parse_lane, parse_op
from co.e2e.settings import conf

SNAP = "/v1/perm/snapshot"
EVAL = "/v1/perm/evaluate"
ARC_LIST = "/v1/archives/list"
NO_EVALUATOR = "本通道没有配置 account.perm_evaluator_file（权限核对的评估操作员）"
_PAGE = 500
_PAGES = 20


class NoEvaluator(Exception):
    """通道没有配置评估操作员：用例记 N/A。"""


def evaluator(env: Env) -> Operator | None:
    path = conf().perm_evaluator_file
    if not path:
        return None
    op = load_operator(Path(path))
    if op.password not in env.ctx.needles:
        env.ctx.needles.append(op.password)
    return op


def call_as(env: Env, op: Operator, route: str, extra: dict) -> tuple[int, dict]:
    fields = {"acc": op.acc, "year": env.year or op.year, "operator": op.code, "password": op.password, "date": env.today}
    fields.update(extra)
    return invoke(env.client, route, fields)


def evaluate(env: Env, ev: Operator, subject: str) -> dict:
    status, payload = call_as(env, ev, EVAL, {"subject": subject})
    if status == 403:
        raise ValueError(f"评估操作员 {ev.code} 调 perm/evaluate 得 403：桥的 permEvaluateOperators 没有列出它？")
    if status != 200:
        raise ValueError(f"perm/evaluate {subject} 得 {status} {payload.get('code')}")
    return payload


def snapshot(env: Env, op: Operator) -> dict:
    status, payload = call_as(env, op, SNAP, {})
    if status != 200:
        raise ValueError(f"perm/snapshot（{op.code}）得 {status} {payload.get('code')}")
    return payload


def _pages(env: Env, op: Operator) -> list[dict] | None:
    found: list[dict] = []
    after = None
    for _page in range(_PAGES):
        extra: dict = {"archive": "operator", "limit": _PAGE}
        if after not in (None, ""):
            extra["after"] = after
        status, payload = call_as(env, op, ARC_LIST, extra)
        if status == 403 and not found:
            return None
        if status != 200:
            raise ValueError(f"操作员列表 {status} {payload.get('code')}")
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("操作员列表缺 items")
        found.extend(item for item in items if isinstance(item, dict))
        after = payload.get("next")
        if after in (None, ""):
            return found
    raise ValueError(f"操作员超过 {_PAGE * _PAGES} 个，没有列完")


def operators(env: Env, ev: Operator | None) -> list[dict]:
    """本账套有授权的全部操作员（含停用）。测试操作员和评估操作员都不是账套主管时报错。"""
    for op in (env.operator, ev):
        if op is None:
            continue
        found = _pages(env, op)
        if found is not None:
            return found
    raise ValueError("测试操作员、评估操作员都读不了操作员档案（需账套主管），无法枚举操作员")


def enabled(items: list[dict]) -> dict[str, list[str]]:
    """启用的操作员 → 操作员档案里的角色（本账套有授权的角色）。"""
    found: dict[str, list[str]] = {}
    for item in items:
        code = str(item.get("code") or "").strip()
        if code and item.get("state") == 0 and item.get("disabled") is False:
            roles = item.get("roles")
            found[code] = [str(r) for r in roles] if isinstance(roles, list) else []
    return found


def lane_facts(env: Env, spec: CaseSpec) -> LaneFacts:
    return parse_lane(rows(env, spec, lane_sql()))


def op_facts(env: Env, spec: CaseSpec, lane: LaneFacts, op: str, roles: list[str]) -> OpFacts:
    """数据权限开关全关时桥不读管理员和编码，这里也不查。"""
    if not lane.on:
        return OpFacts(frozenset(), {})
    return parse_op(rows(env, spec, op_sql(op, roles)))


def col_rows(env: Env, spec: CaseSpec, op: str, roles: list[str]) -> list[list[str]]:
    """字段权限的原始行（perm_oracle.expected_columns 合并）；与数据权限开关无关，总要查。"""
    return rows(env, spec, columns_sql(op, roles))
