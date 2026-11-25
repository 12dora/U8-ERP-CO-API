"""幂等键结果查询（idempotency/get）用例名 `idempotency-get`。

总账凭证带新幂等键新建 → 按同一路由和键查：found、state=ok、status=200、response 的期间 / 类别 / 凭证号与新建响应相同
→ 查一个没用过的键：found=false → 路由写成不支持幂等的 vouchers/load：400，field=route → 作废、删除凭证、读取 404、库里没有行
（凭证键放在 core:<用例名>，沿用总账凭证步骤；中途失败时 dry_cleanup 尽力作废并删除）。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

from co.e2e.casespec.assertutil import status_problem
from co.e2e.casespec.gl_voucher_bodies import GL_DATA, slot
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.dry_judge import idem_problem
from co.e2e.casespec.dry_ops import send
from co.e2e.casespec.gl_voucher_sql import gl_key, pack_key
from co.e2e.testdata import SIGN_TRANSFER, gl_sign
from co.e2e.testdata import value as td

ROUTE = "/v1/idempotency/get"
_CREATE = "/v1/gl/vouchers/create"
BRIDGE_CREATE = "/u8co/v1/gl/vouchers/create"
_KEY = re.compile(r"^e2e-dry-[0-9a-f]{16}\Z")
SLOT_KEY, _SLOT_RESP = "dry_idem_key", "dry_idem_resp"


def _new_key() -> str:
    return "e2e-dry-" + os.urandom(8).hex()


def make(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    key = _new_key()
    digest = "CO幂等查询" + datetime.now().strftime("%Y%m%d%H%M%S")
    lines = [
        {"account": td("gl.expense_account"), "digest": digest, "debit": 1.0, "dept": td("gl.expense_dept")},
        {"account": td("gl.payable_account"), "digest": digest, "credit": 1.0},
    ]
    body = {"head": {"sign": gl_sign(SIGN_TRANSFER), "date": env.today}, "lines": lines, "idempotency_key": key}
    # 先记下键：万一新建已生效而响应丢了（504），补偿能按键查回凭证号再作废删除（dry_cleanup）。
    env.bag[SLOT_KEY] = key
    status, payload = send(env, _CREATE, body)
    bad = status_problem(status, payload, 200, "")
    if bad:
        return "FAIL", bad[:240]
    env.bag[slot(spec)] = pack_key(gl_key(payload.get("period"), payload.get("sign"), payload.get("no")))
    env.bag[_SLOT_RESP] = json.dumps({name: payload.get(name) for name in ("period", "sign", "no")}, ensure_ascii=False)
    return "PASS", env.bag[slot(spec)]


def found(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    key = env.bag.get(SLOT_KEY, "")
    if _KEY.fullmatch(key) is None:
        return "FAIL", "没有记下幂等键"
    status, payload = send(env, ROUTE, {"route": BRIDGE_CREATE, "idempotency_key": key})
    if status != 200:
        return "FAIL", f"{status} {payload.get('code')} {payload.get('message')}"[:240]
    problem = idem_problem(payload, json.loads(env.bag.get(_SLOT_RESP) or "{}"))
    return ("FAIL", problem) if problem else ("PASS", "found ok")


def unknown(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    status, payload = send(env, ROUTE, {"route": BRIDGE_CREATE, "idempotency_key": _new_key()})
    if status != 200 or payload.get("ok") is not True or payload.get("found") is not False:
        return "FAIL", f"{status} found={payload.get('found')} {payload.get('code')}"
    return "PASS", "found=false"


def bad_route(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    status, payload = send(env, ROUTE, {"route": "/u8co/v1/vouchers/load", "idempotency_key": _new_key()})
    bad = status_problem(status, payload, 400, "bad_request")
    if bad:
        return "FAIL", bad[:240]
    if payload.get("field") != "route":
        return "FAIL", f"field={payload.get('field')!r}，期望 route"
    return "PASS", "400 route"


DRY_IDEM_OPS = {"dry_idem_make": make, "dry_idem_found": found, "dry_idem_unknown": unknown, "dry_idem_route": bad_route}


def _gl(op: str, checks: tuple = (), expect: tuple = (200, "")) -> Step:
    return Step("core_gl", "/v1/gl/vouchers/" + op, (), expect[0], expect[1], checks, "key")


def idem_get_case() -> CaseSpec:
    steps = tuple(Step("custom", ROUTE, sql_name=op, id_from="none") for op in DRY_IDEM_OPS)
    steps += (
        _gl("void", (("ok", "true"),)),
        _gl("delete", (("deleted", "true"),)),
        _gl("load", (), (404, "not_found")),
        Step("core_glsql", sql_name="rows", code="0"),
    )
    title = "幂等键结果查询：新建后按键查到同一张凭证，未用过的键 found=false，不支持的路由 400 field=route，再作废删除"
    tags = frozenset(("gl", "new:gl:转"))
    return CaseSpec("idempotency-get", title, (), steps, True, data=GL_DATA, tags=tags, routes=(ROUTE, _CREATE))
