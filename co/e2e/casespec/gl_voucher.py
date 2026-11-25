"""总账凭证用例：新建、修改、审核、出纳签字、作废、删除和列表。期间取运行当天所在月份。

每条用例在自己的步骤里作废并删除新建的凭证。凭证键来自桥的新建响应，放在 bag 的 `core:<用例名>` 下。
"""

from __future__ import annotations

from co.e2e.casespec.assertutil import first_cell, judge_cell, status_problem
from co.e2e.casespec.gl_voucher_bodies import BANK_DATA, GL_BODIES, GL_DATA, judge, slot
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.gl_voucher_sql import gl_key, gl_module_sql, gl_sql, pack_key, parse_key
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke
from co.e2e.testdata import SIGN_BANK, SIGN_TRANSFER

_ROUTE = "/v1/gl/vouchers/"
_OK = (("ok", "true"),)


def call_core(env: Env, step: Step, body: dict) -> tuple[int, dict, str]:
    fields = auth_fields(env)
    fields.update(body)
    status, payload = invoke(env.client, step.route, fields)
    return status, payload, status_problem(status, payload, step.status, step.code)[:240]


def step_gl(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    try:
        body = GL_BODIES[step.sql_name](env, env.bag.get(slot(spec), ""))
    except (KeyError, ValueError) as exc:
        return "FAIL", str(exc)[:200]
    status, payload, bad = call_core(env, step, body)
    if bad:
        return "FAIL", bad
    if status != 200:
        return "PASS", step.code or str(status)
    if step.route.endswith("/create"):
        try:
            env.bag[slot(spec)] = pack_key(gl_key(payload.get("period"), payload.get("sign"), payload.get("no")))
        except ValueError as exc:
            return "FAIL", str(exc)
    return judge(env, payload, step.checks, slot(spec))


def step_gl_sql(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    try:
        # 会计年度取登录日期的年份；env.year 是账套库的启用年度。
        sql = gl_sql(step.sql_name, env.today[:4], parse_key(env.bag.get(slot(spec), "")))
        cell = first_cell(run_guarded(env, sql, spec.name))
    except (RuntimeError, ValueError) as exc:
        return "FAIL", str(exc)[:200]
    return judge_cell(cell, step.code)


def step_gl_pick(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    try:
        cell = first_cell(run_guarded(env, gl_module_sql(env.today[:4]), spec.name))
    except (RuntimeError, ValueError) as exc:
        return "FAIL", str(exc)[:200]
    if not cell:
        return "N/A", "账套没有模块生成的凭证"
    try:
        env.bag[slot(spec)] = pack_key(parse_key(cell))
    except ValueError:
        return "SKIP", "模块生成凭证的键不合法：" + cell[:80]
    return "PASS", cell


FIN_HANDLERS = {"core_gl": step_gl, "core_glsql": step_gl_sql, "core_glpick": step_gl_pick}


def _gl(op: str, body: str, checks: tuple = (), status: int = 200, code: str = "") -> Step:
    return Step("core_gl", _ROUTE + op, (), status, code, checks, body)


def _sql(name: str, want: str) -> Step:
    return Step("core_glsql", sql_name=name, code=want)


def _load(checks: tuple) -> Step:
    return _gl("load", "key", checks)


def _drop() -> tuple[Step, ...]:
    # 作废 → 删除 → 读取 404 → 库里没有行。
    return (
        _gl("void", "key", _OK),
        _gl("delete", "key", (("deleted", "true"),)),
        _gl("load", "key", (), 404, "not_found"),
        _sql("rows", "0"),
    )


def _cycle() -> CaseSpec:
    made = (("ok", "true"), ("sign", "$td:" + SIGN_TRANSFER), ("no", "$gt0"))
    first = (("lines#len", "2"), ("lines#balanced", "yes"), ("lines#debit", "$num:1"))
    first += (("voucher.maker", "$opname"), ("voucher.checker", ""), ("voucher.void", "false"))
    second = (("lines#len", "2"), ("lines#balanced", "yes"), ("lines#debit", "$num:2"), ("voucher.maker", "$opname"))
    steps = (
        _gl("create", "create_transfer", made),
        _load(first),
        _gl("update", "update_transfer", _OK),
        _load(second),
        _gl("verify", "key", _OK),
        _load((("voucher.checker", "$opname"),)),
        _gl("update", "update_transfer", (), 409, "state_mismatch"),
        _gl("unverify", "key", _OK),
        _load((("voucher.checker", ""),)),
        # 没有现金或银行科目行，出纳签字应被拒绝。
        _gl("sign", "key", (), 409),
        _load((("voucher.cashier", ""),)),
        _gl("void", "key", _OK),
        _load((("voucher.void", "true"),)),
        _gl("unvoid", "key", _OK),
        _load((("voucher.void", "false"),)),
    ) + _drop()
    return CaseSpec("gl-cycle", "总账凭证新建修改审核作废删除", (), steps, True, data=GL_DATA)


def _bank() -> CaseSpec:
    made = (("ok", "true"), ("sign", "$td:" + SIGN_BANK), ("no", "$gt0"))
    loaded = (("lines#len", "2"), ("lines#balanced", "yes"), ("lines#cashflow", "$td:gl.cash_flow_item"))
    loaded += (("voucher.maker", "$opname"),)
    steps = (
        _gl("create", "create_bank", made),
        _load(loaded),
        _sql("cash_sign", "1"),
        _gl("sign", "key", _OK),
        _load((("voucher.cashier", "$opname"),)),
        _gl("unsign", "key", _OK),
        _load((("voucher.cashier", ""),)),
    ) + _drop() + (_sql("cash_rows", "0"),)
    return CaseSpec("gl-bank-cashflow", "银行凭证带现金流量：出纳签字取消后删除", (), steps, True, data=BANK_DATA)


def _module() -> CaseSpec:
    steps = (
        Step("core_glpick", sql_name="module"),
        _load((("voucher.source_system", "$nonempty"),)),
        _gl("void", "key", (), 409),
        _sql("void_rows", "0"),
    )
    return CaseSpec("gl-module-readonly", "模块生成的凭证不能在总账作废", (), steps, True)


def _listed() -> CaseSpec:
    steps = (
        _gl("create", "create_transfer", (("ok", "true"),)),
        _gl("list", "list", (("items#has", "yes"),)),
    ) + _drop()
    return CaseSpec("gl-list", "总账凭证列表能查到新建的凭证", (), steps, True, data=GL_DATA)


def cases() -> list[CaseSpec]:
    return [_cycle(), _bank(), _module(), _listed()]
