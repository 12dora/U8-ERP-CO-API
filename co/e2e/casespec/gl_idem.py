"""幂等键用例：总账凭证新建带 idempotency_key。

第一次正文不合法（借贷不平，400）不占用键；同一个键换成合法正文照常新建；原样重发返回同一张凭证，
库里按本次摘要只有一张；同一个键换内容 409 idempotency_mismatch。最后作废并删除凭证（凭证键放在 core:<用例名>，
沿用总账凭证步骤）。摘要每次不同（日期时间加随机后缀），计数不会碰到别的凭证。
"""

from __future__ import annotations

import os
import re
from datetime import datetime

from co.e2e.casespec.assertutil import first_cell, status_problem
from co.e2e.casespec.gl_voucher_bodies import GL_DATA, slot
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.gl_voucher_sql import gl_key, pack_key
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke
from co.e2e.testdata import SIGN_TRANSFER, gl_sign
from co.e2e.testdata import value as td

_ROUTE = "/v1/gl/vouchers/create"
_DIGEST = re.compile(r"^CO幂等[0-9]{14}[0-9a-f]{4}\Z")
_KEY = re.compile(r"^e2e-custom-[0-9a-f]{16}\Z")
_YEAR = re.compile(r"^20[0-9]{2}\Z")


def _key_slot(spec: CaseSpec) -> str:
    return "custom_key:" + spec.name


def _digest_slot(spec: CaseSpec) -> str:
    return "custom_dig:" + spec.name


def _body(env: Env, spec: CaseSpec, debit: float, credit: float) -> dict:
    digest = env.bag.get(_digest_slot(spec), "")
    key = env.bag.get(_key_slot(spec), "")
    if _DIGEST.fullmatch(digest) is None or _KEY.fullmatch(key) is None:
        raise ValueError("缺幂等键或摘要")
    lines = [
        {"account": td("gl.expense_account"), "digest": digest, "debit": debit, "dept": td("gl.expense_dept")},
        {"account": td("gl.payable_account"), "digest": digest, "credit": credit},
    ]
    return {"head": {"sign": gl_sign(SIGN_TRANSFER), "date": env.today}, "lines": lines, "idempotency_key": key}


def _send(env: Env, body: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(body)
    return invoke(env.client, _ROUTE, fields)


def _packed(payload: dict) -> str:
    return pack_key(gl_key(payload.get("period"), payload.get("sign"), payload.get("no")))


def idem_bad(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    # 新键、新摘要；借 1 贷 2 不平，桥在登录前 400，不占用键。
    env.bag[_key_slot(spec)] = "e2e-custom-" + os.urandom(8).hex()
    # 摘要带日期时间和随机后缀：往次还原失败留下的凭证不会被算进来。
    env.bag[_digest_slot(spec)] = "CO幂等" + datetime.now().strftime("%Y%m%d%H%M%S") + os.urandom(2).hex()
    status, payload = _send(env, _body(env, spec, 1.0, 2.0))
    bad = status_problem(status, payload, 400, "bad_request")
    return ("FAIL", bad[:240]) if bad else ("PASS", "400 未占用键")


def idem_ok(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    status, payload = _send(env, _body(env, spec, 1.0, 1.0))
    bad = status_problem(status, payload, 200, "")
    if bad:
        return "FAIL", bad[:240]
    env.bag[slot(spec)] = _packed(payload)
    return "PASS", env.bag[slot(spec)]


def idem_again(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    status, payload = _send(env, _body(env, spec, 1.0, 1.0))
    bad = status_problem(status, payload, 200, "")
    if bad:
        return "FAIL", bad[:240]
    got = _packed(payload)
    if got != env.bag.get(slot(spec), ""):
        return "FAIL", "重放返回了另一张凭证 " + got
    return "PASS", got


def idem_mismatch(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    status, payload = _send(env, _body(env, spec, 2.0, 2.0))
    bad = status_problem(status, payload, 409, "idempotency_mismatch")
    return ("FAIL", bad[:240]) if bad else ("PASS", "409 idempotency_mismatch")


def count_sql(year: str, digest: str) -> str:
    if _YEAR.fullmatch(year or "") is None or _DIGEST.fullmatch(digest or "") is None:
        raise ValueError("年度或摘要不合法")
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), COUNT(DISTINCT CONVERT(varchar(12), v.iperiod) + N'|' + ISNULL(v.csign, N'') + N'|'"
        " + CONVERT(varchar(12), v.ino_id))) AS v\n"
        f"FROM dbo.GL_accvouch AS v\nWHERE v.iyear = {year} AND v.cdigest = N'{digest}';\n"
    )


def idem_count(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    # 会计年度取登录日期的年份。
    cell = first_cell(run_guarded(env, count_sql(env.today[:4], env.bag.get(_digest_slot(spec), "")), spec.name))
    if cell != "1":
        return "FAIL", "按摘要找到 " + (cell or "空") + " 张凭证，期望 1"
    return "PASS", "1 张"


IDEM_OPS = {
    "idem_bad": idem_bad,
    "idem_ok": idem_ok,
    "idem_again": idem_again,
    "idem_count": idem_count,
    "idem_mismatch": idem_mismatch,
}


def _gl(op: str, checks: tuple = (), status: int = 200, code: str = "") -> Step:
    return Step("core_gl", "/v1/gl/vouchers/" + op, (), status, code, checks, "key")


def idem_case() -> CaseSpec:
    steps = tuple(Step("custom", sql_name=op, id_from="none") for op in ("idem_bad", "idem_ok", "idem_again", "idem_count"))
    steps += (
        Step("custom", sql_name="idem_mismatch", id_from="none"),
        _gl("void", (("ok", "true"),)),
        _gl("delete", (("deleted", "true"),)),
        _gl("load", (), 404, "not_found"),
        Step("core_glsql", sql_name="rows", code="0"),
    )
    title = "幂等键：400 不占键，成功后重放同一张凭证，换内容 409，再作废删除"
    tags = frozenset(("gl", "new:gl:转"))
    return CaseSpec("idempotency-gl-create", title, (), steps, True, data=GL_DATA, tags=tags, routes=(_ROUTE,))
