"""客户新增 `archive-customer-create`：建档的整条路径——archives/create 新建客户（EAI，INSERT）→ archives/get 核对
→ customer_fields 改名称、税号 → customer_person 换专管业务员 → customer_fields 写停用日期（不删除）。

只在测试账套跑，不进只读账套的轮次：只读账套只开放客户新增（只读例外 create + customer），
不开放删除，所以本用例也不删客户，而是写停用日期；新建的客户留到本次跑完，随测试账套快照还原。
编码取测试数据 archives.customer.codes 里第一个空闲的（同 archive-customer-cycle），挑不到记 SKIP。
步骤 kind 为 core_cuscreate，挂在 arc_write.CORE_HANDLERS。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.arc_cus_bodies import fields_body, new_key
from co.e2e.casespec.arc_cus_person_bodies import expect, person_body, send, shown
from co.e2e.casespec.gl_voucher_bodies import ARC_SPECS, arc_data
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arc_cus_sql import customer_created_sql
from co.e2e.casespec.gl_voucher_sql import free_code_sql, safe_code
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.testdata import DataMissing
from co.e2e.testdata import value as td

_NAME = "archive-customer-create"
_OTHER = "archives.customer.spec_operator_other"
_DATA = (*arc_data("customer"), _OTHER)
_ROUTES = (
    "/v1/archives/create",
    "/v1/archives/get",
    "/v1/archives/customer_fields",
    "/v1/archives/customer_person",
)
_OK = (200, "")


class _NoCode(Exception):
    """测试数据里的候选编码都被占用：记 SKIP。"""


@dataclass
class _State:
    code: str = ""
    name: str = ""
    tax: str = ""
    made: bool = False


def _post(env: Env, op: str, body: dict, label: str) -> tuple[str, dict]:
    status, payload = send(env, op, body)
    return expect(status, payload, _OK, label), payload


def _pick(env: Env, spec: CaseSpec, state: _State) -> str:
    cell = first_cell(run_guarded(env, free_code_sql("customer"), spec.name)).strip()
    try:
        state.code = safe_code(cell)
    except ValueError as exc:
        raise _NoCode("测试数据里的客户编码都被占用") from exc
    return ""


def _create(env: Env, state: _State) -> str:
    fields = ARC_SPECS["customer"][0](state.code, env.today)
    state.name, state.tax = fields["name"], fields["tax_reg_code"]
    body = {"archive": "customer", "code": state.code, "fields": fields, "idempotency_key": new_key("cuscreate")}
    bad, _payload = _post(env, "create", body, "新建客户")
    state.made = not bad
    return bad


def _get(env: Env, state: _State) -> str:
    bad, payload = _post(env, "get", {"archive": "customer", "code": state.code}, "读取新客户")
    if bad:
        return bad
    got = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    seen = (shown(got.get("name")), shown(got.get("tax_reg_code")))
    if seen != (state.name, state.tax):
        return f"新客户名称 / 税号={seen!r} 期望 {(state.name, state.tax)!r}"[:200]
    return ""


def _after(payload: dict, key: str, want: str, label: str) -> str:
    after = payload.get("after")
    got = shown(after.get(key)) if isinstance(after, dict) else None
    return "" if got == want else f"{label}：after.{key}={got!r} 期望 {want!r}"[:200]


def _rename(env: Env, state: _State) -> str:
    """名称、税号一起改（expect 是新建时的值）。"""
    name, tax = "CO回归客户改" + state.code, "91000000CX" + state.code.rjust(8, "0")
    body = fields_body(state.code, {"name": name, "tax_no": tax}, {"name": state.name, "tax_no": state.tax})
    bad, payload = _post(env, "customer_fields", body, "改名称税号")
    bad = bad or _after(payload, "name", name, "改名称税号") or _after(payload, "tax_no", tax, "改名称税号")
    if not bad:
        state.name, state.tax = name, tax
    return bad


def _person(env: Env, state: _State) -> str:
    """专管业务员从新建时的换成另一位（expect 是新建时的）。"""
    first = td("archives.customer.spec_operator")
    other = td(_OTHER)
    body = person_body(state.code, other, first, key=new_key("cuscreate"))
    bad, payload = _post(env, "customer_person", body, "换专管业务员")
    if bad:
        return bad
    got = shown(payload.get("after"))
    return "" if got.casefold() == other.casefold() else f"换专管业务员：after={got!r} 期望 {other!r}"[:200]


def _stop(env: Env, spec: CaseSpec, state: _State) -> str:
    """停用：end_date 写登录日与建档日中较晚的一天（桥拒绝早于建档日），代替删除。"""
    created = first_cell(run_guarded(env, customer_created_sql(state.code), spec.name)).strip()
    day = max(env.today[:10], "" if created in ("", "-") else created[:10])
    body = fields_body(state.code, {"end_date": day}, {"end_date": ""})
    bad, payload = _post(env, "customer_fields", body, "写停用日期")
    return bad or _after(payload, "end_date", day, "写停用日期")


def _first_bad(steps: tuple[Callable[[], str], ...]) -> str:
    for run in steps:
        bad = run()
        if bad:
            return bad
    return ""


def step_cuscreate(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    state = _State()
    try:
        bad = _first_bad((
            lambda: _pick(env, spec, state),
            lambda: _create(env, state),
            lambda: _get(env, state),
            lambda: _rename(env, state),
            lambda: _person(env, state),
            lambda: _stop(env, spec, state),
        ))
    except _NoCode as exc:
        return "SKIP", str(exc)
    except (DataMissing, RuntimeError, ValueError) as exc:
        bad = str(exc)[:200]
    if bad:
        left = f"；已新建 {state.code}，随快照还原" if state.made else ""
        return "FAIL", (bad + left)[:240]
    return "PASS", f"customer {state.code} created, renamed, stopped"


CUSCREATE_HANDLERS = {"core_cuscreate": step_cuscreate}


def cuscreate_cases() -> list[CaseSpec]:
    step = Step("core_cuscreate", id_from="none")
    title = "客户新增后改名称税号、换专管业务员、写停用日期（不删除）"
    tags = frozenset({"arc:customer", "arc:write"})
    return [CaseSpec(_NAME, title, (), (step,), True, data=_DATA, tags=tags, routes=_ROUTES)]
