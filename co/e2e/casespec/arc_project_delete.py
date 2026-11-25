"""项目档案删除 `archive-project-delete`：受控 SQL 的删除走一遍正反两面。

挑选 SQL（arc_write_more_sql 的 project）给出一个没有额外栏目的项目大类，以及该大类里已被辅助账 GL_accass 引用的一个项目。
正面：按 API 列表取该大类一个项目的所属分类（末级），新建 <大类>:ZZ<时间> → 读取 → 删除（deleted）→ 读取 404。
反面：删除被引用的项目 → 409 state_mismatch「项目已被使用…」，再读取仍是 200。
挑不到记 N/A；中途失败时尽力删掉本用例建的项目。步骤 kind 为 "custom"，sql_name 是操作名（PROJ_OPS 并入 ops_run）。
"""

from __future__ import annotations

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arc_write_more import _cleanup, _expect, _pick, _read_problem, _send, _stamp

NAME = "archive-project-delete"
_ARCHIVE = "project"
_TITLE = "项目新增后删除，被辅助账引用的项目不能删除（受控 SQL）"


def _leaf_class(env: Env, cls: str) -> str:
    """该大类一个现有项目的所属分类（项目只挂在末级分类上）；取不到返回空串。"""
    status, got = _send(env, "list", {"archive": _ARCHIVE, "project_class": cls, "limit": 1})
    items = got.get("items") if status == 200 else None
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return ""
    return str(items[0].get("class_code") or "").strip()


def _cycle(env: Env, code: str, leaf: str) -> str:
    key = {"archive": _ARCHIVE, "code": code}
    name = "u8co e2e 项目"
    status, payload = _send(env, "create", dict(key, fields={"name": name, "citemccode": leaf}))
    bad = _expect(status, payload, 200, "新增") or _read_problem(env, key, "citemname", name)
    if bad:
        return bad
    status, payload = _send(env, "delete", key)
    bad = _expect(status, payload, 200, "删除")
    if bad or payload.get("deleted") is not True:
        return bad or "删除响应缺 deleted"
    status, payload = _send(env, "get", key)
    return _expect(status, payload, 404, "删除后读取")


def _refused(env: Env, code: str) -> str:
    key = {"archive": _ARCHIVE, "code": code}
    status, payload = _send(env, "delete", key)
    if status != 409 or payload.get("code") != "state_mismatch":
        return f"被引用的 {code} 删除应 409 state_mismatch，实际 {status} {payload.get('code')} {payload.get('message')}"
    if "已被使用" not in str(payload.get("message") or ""):
        return f"被引用的 {code} 删除被拒的原因不对：{payload.get('message')}"
    status, payload = _send(env, "get", key)
    return _expect(status, payload, 200, "删除被拒后读取")


def arc_more_project(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    picked = _pick(env, spec, _ARCHIVE)
    if len(picked) != 2:
        return "N/A", "测试账套里挑不到被辅助账引用、又没有额外栏目的项目大类"
    cls, used = picked
    leaf = _leaf_class(env, cls)
    if leaf == "":
        return "N/A", f"项目大类 {cls} 列不出项目，取不到末级分类"
    code = cls + ":" + _stamp()
    problem = _cycle(env, code, leaf)
    if problem:
        return "FAIL", (_cleanup(env, _ARCHIVE, code) + "；" + problem)[:240]
    problem = _refused(env, cls + ":" + used)
    if problem:
        return "FAIL", problem[:240]
    return "PASS", f"{code} 已删除；{cls}:{used} 删除被拒"


PROJ_OPS = {"arc_more_project": arc_more_project}


def proj_cases() -> list[CaseSpec]:
    step = Step("custom", sql_name="arc_more_project", id_from="none")
    routes = tuple("/v1/archives/" + op for op in ("list", "create", "get", "delete"))
    return [CaseSpec(NAME, _TITLE, (), (step,), True, routes=routes)]
