"""档案用例：九类档案各走一遍新建、读取、修改、删除；已被引用的客户不能删；客户列表分页；
客户专用写入（专管业务员、字段、新增后停用、收货地址）、发货单表头修正和销售订单表头自定义项写入各在自己的模块。

候选编码来自测试数据 archives.<档案>.codes，按顺序挑第一个空闲的（SQL 查重）。每条用例删掉自己新建的档案。
"""

from __future__ import annotations

from co.e2e.casespec.assertutil import first_cell, judge_cell
from co.e2e.casespec.gl_voucher_bodies import arc_body, arc_data, arc_expect, archive_of, codes, judge, slot
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arc_cus_addr import CUSADDR_HANDLERS, cusaddr_cases
from co.e2e.casespec.arc_cus_create import CUSCREATE_HANDLERS, cuscreate_cases
from co.e2e.casespec.arc_cus_fields import CUSFIELDS_HANDLERS, cusfields_cases
from co.e2e.casespec.arc_cus_invoice import CUSINV_HANDLERS, cusinv_cases
from co.e2e.casespec.arc_cus_addr_rows import CUSROWS_HANDLERS, cusrows_cases
from co.e2e.casespec.arc_cus_person import PPERSON_HANDLERS, pperson_cases
from co.e2e.casespec.gl_voucher import FIN_HANDLERS, call_core
from co.e2e.casespec.head_fix import HEADFIX_HANDLERS, headfix_cases
from co.e2e.casespec.so_defines import SODEFINES_HANDLERS, sodefines_cases
from co.e2e.casespec.gl_voucher_sql import (
    arc_count_sql,
    free_code_sql,
    list_prefix_sql,
    safe_code,
    safe_prefix,
    used_customer_sql,
)
from co.e2e.casespec.sqlexec import run_guarded

_ROUTE = "/v1/archives/"
_CODE = (("code", "$code"),)
# 运行顺序：分类和部门、仓库在前，再客户、供应商、人员、存货。
_ORDER = (
    ("customer_class", "客户分类"),
    ("vendor_class", "供应商分类"),
    ("inventory_class", "存货分类"),
    ("department", "部门"),
    ("warehouse", "仓库"),
    ("customer", "客户"),
    ("vendor", "供应商"),
    ("person", "人员"),
    ("inventory", "存货"),
)


# 挑不到时说明账套没有这类数据（不是测试数据配置问题）的挑单：挑单名 → N/A 原因。
_ABSENT = {
    "used": "账套没有被销售订单、发货单或收款单引用的客户",
    "prefix": "账套客户不足 6 个，无法分页列表",
}


def _pick_sql(step: Step) -> str:
    if step.sql_name == "used":
        return used_customer_sql()
    if step.sql_name == "prefix":
        return list_prefix_sql()
    return free_code_sql(archive_of(step))


def step_arc_pick(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    try:
        cell = first_cell(run_guarded(env, _pick_sql(step), spec.name)).strip()
    except (RuntimeError, ValueError) as exc:
        return "FAIL", str(exc)[:200]
    if step.sql_name in _ABSENT and cell in ("", "-"):
        # 账套里真没有这类客户：业务缺失记 N/A，不是挑单问题。
        return "N/A", _ABSENT[step.sql_name]
    try:
        value = safe_prefix(cell) if step.sql_name == "prefix" else safe_code(cell)
    except ValueError:
        return "SKIP", "账套里候选编码都被占用，或缺依赖档案"
    env.bag[slot(spec)] = value
    return "PASS", value


def _remember_page(env: Env, spec: CaseSpec, payload: dict) -> None:
    env.bag[slot(spec) + "#next"] = str(payload.get("next") or "")
    env.bag[slot(spec) + "#codes"] = ",".join(codes(payload))


def step_arc(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    try:
        body = arc_body(env, spec, step)
        wants = step.checks + arc_expect(env, spec, step)
    except (KeyError, ValueError) as exc:
        return "FAIL", str(exc)[:200]
    status, payload, bad = call_core(env, step, body)
    if bad:
        return "FAIL", bad
    if status != 200:
        return "PASS", step.code or str(status)
    verdict = judge(env, payload, wants, slot(spec))
    if step.sql_name == "list":
        _remember_page(env, spec, payload)
    return verdict


def step_arc_sql(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    try:
        sql = arc_count_sql(archive_of(step), env.bag.get(slot(spec), ""))
        cell = first_cell(run_guarded(env, sql, spec.name))
    except (RuntimeError, ValueError) as exc:
        return "FAIL", str(exc)[:200]
    return judge_cell(cell, step.code)


# E1 把它并入 runner._HANDLERS（已含总账的三个步骤）。
CORE_HANDLERS = dict(FIN_HANDLERS)
CORE_HANDLERS.update({"core_arc": step_arc, "core_arcsql": step_arc_sql, "core_arcpick": step_arc_pick})
CORE_HANDLERS.update(PPERSON_HANDLERS)
CORE_HANDLERS.update(CUSFIELDS_HANDLERS)
CORE_HANDLERS.update(CUSCREATE_HANDLERS)
CORE_HANDLERS.update(CUSADDR_HANDLERS)
CORE_HANDLERS.update(CUSROWS_HANDLERS)
CORE_HANDLERS.update(CUSINV_HANDLERS)
CORE_HANDLERS.update(HEADFIX_HANDLERS)
CORE_HANDLERS.update(SODEFINES_HANDLERS)


def _arc(op: str, mode: str, archive: str, checks: tuple = (), expect: tuple = (200, "")) -> Step:
    return Step("core_arc", _ROUTE + op, (("archive", archive),), expect[0], expect[1], checks, mode)


def _count(archive: str, want: str) -> Step:
    return Step("core_arcsql", fields=(("archive", archive),), sql_name="count", code=want)


def _pick(archive: str, mode: str) -> Step:
    return Step("core_arcpick", fields=(("archive", archive),), sql_name=mode)


def _cycle(archive: str, label: str) -> CaseSpec:
    steps = (
        _pick(archive, "free"),
        _arc("create", "create", archive, (("ok", "true"),) + _CODE),
        _count(archive, "1"),
        _arc("get", "get_created", archive, _CODE),
        _arc("update", "update", archive, (("ok", "true"),) + _CODE),
        _arc("get", "get_updated", archive, _CODE),
        _arc("delete", "key", archive, (("deleted", "true"),) + _CODE),
        _arc("get", "key", archive, (), (404, "not_found")),
        _count(archive, "0"),
    )
    name = "archive-" + archive.replace("_", "-") + "-cycle"
    return CaseSpec(name, label + "档案新建读取修改删除", (), steps, True, data=arc_data(archive))


def _used() -> CaseSpec:
    steps = (
        _pick("customer", "used"),
        _arc("delete", "key", "customer", (), (409, "u8_rejected")),
        _arc("get", "key", "customer", _CODE),
        _count("customer", "1"),
    )
    return CaseSpec("archive-delete-used", "已被单据引用的客户不能删除", (), steps, True)


def _listed() -> CaseSpec:
    first = (("items#len", "5"), ("next", "$nonempty"), ("items#prefixed", "yes"))
    steps = (
        _pick("customer", "prefix"),
        _arc("list", "list", "customer", first),
        _arc("list", "list_next", "customer", (("items#prefixed", "yes"), ("items#fresh", "yes"))),
    )
    return CaseSpec("archive-list", "客户档案按编码前缀分页列表", (), steps, True)


def cases() -> list[CaseSpec]:
    head = [_cycle(archive, label) for archive, label in _ORDER] + [_used(), _listed()]
    tail = pperson_cases() + cusfields_cases() + cuscreate_cases() + cusaddr_cases() + cusrows_cases() + cusinv_cases()
    return head + tail + headfix_cases() + sodefines_cases()
