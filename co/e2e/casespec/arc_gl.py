"""总账档案写入（桥 ArcGl）：凭证类别、币种各走一遍 新增（EAI）→ 读取 → 修改（受控 SQL）→ 读取 → 删除（受控 SQL）
→ 读取 404，再各试一次删除闸门（预置类别、本位币，桥 409 state_mismatch，之后读取仍 200）。会计科目仍只读。
预置类别取测试数据 gl.sign_transfer（缺省「转」），不是 收 / 付 / 转 / 记 之一时不试删除闸门。

编码从测试账套挑（arc_gl_sql）：空闲的类别字 Z?、空闲的币种名称 ZZE2E? 和符号。挑不到记 N/A。
步骤 kind 为 "custom"，sql_name 是操作名（GLARC_OPS 并入 ops_run）。用例中途失败时尽力删掉本用例建的档案。
"""

from __future__ import annotations

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arc_write_more import _cleanup, _expect, _read_problem, _send, _stamp
from co.e2e.casespec.arc_write_more_sql import safe_part
from co.e2e.casespec.arc_gl_sql import PRESET_SIGNS, currency_free, sign_free, sign_preset
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.testdata import SIGN_TRANSFER, gl_sign

_ROUTES = tuple("/v1/archives/" + op for op in ("create", "get", "update", "delete", "list"))


def _pick(env: Env, spec: CaseSpec, query: str) -> list[str]:
    cell = first_cell(run_guarded(env, query, spec.name)).strip()
    if cell == "":
        return []
    return [safe_part(part) for part in cell.split("|")]


def _cycle(env: Env, key: dict, column: str, bodies: tuple[dict, dict, str, str]) -> str:
    """bodies：新增的 fields、修改的 fields、新增后 column 的值、修改后 column 的值。"""
    made, changed, want_made, want_changed = bodies
    status, payload = _send(env, "create", dict(key, fields=made))
    bad = _expect(status, payload, 200, "新增") or _read_problem(env, key, column, want_made)
    if bad:
        return bad
    status, payload = _send(env, "update", dict(key, fields=changed))
    bad = _expect(status, payload, 200, "修改") or _read_problem(env, key, column, want_changed)
    if bad:
        return bad
    status, payload = _send(env, "delete", key)
    bad = _expect(status, payload, 200, "删除")
    if bad or payload.get("deleted") is not True:
        return bad or "删除响应缺 deleted"
    status, payload = _send(env, "get", key)
    return _expect(status, payload, 404, "删除后读取")


def _refused(env: Env, archive: str, code: str) -> str:
    key = {"archive": archive, "code": code}
    status, payload = _send(env, "delete", key)
    if status != 409 or payload.get("code") != "state_mismatch":
        return f"{archive} {code} 删除应 409 state_mismatch，实际 {status} {payload.get('code')} {payload.get('message')}"
    status, payload = _send(env, "get", key)
    return _expect(status, payload, 200, archive + " 删除被拒后读取")


def _run(env: Env, archive: str, code: str, bodies: tuple, column: str) -> str:
    problem = _cycle(env, {"archive": archive, "code": code}, column, bodies)
    if problem:
        return (_cleanup(env, archive, code) + "；" + problem)[:240]
    return ""


def glarc_sign(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    picked = _pick(env, spec, sign_free())
    if not picked:
        return "N/A", "测试账套里挑不到空闲的凭证类别字"
    code, name = picked[0], "u8co e2e 类别" + _stamp()
    bodies = ({"type_name": name}, {"type_name": name + "改"}, name, name + "改")
    problem = _run(env, "voucher_sign", code, bodies, "ctext")
    if problem:
        return "FAIL", problem
    preset = gl_sign(SIGN_TRANSFER)
    # 只对预置类别试删除闸门：测试数据配了别的类别字时不去删它。
    if preset in PRESET_SIGNS and _pick(env, spec, sign_preset(preset)):
        problem = _refused(env, "voucher_sign", preset)
        if problem:
            return "FAIL", problem[:240]
    return "PASS", f"voucher_sign {code}"


def _base_currency(env: Env) -> str:
    """本位币的名称（get 的 fields 里 iotherused=-1）；找不到返回空串。"""
    status, got = _send(env, "list", {"archive": "currency", "limit": 50})
    items = got.get("items") if status == 200 else None
    for item in items if isinstance(items, list) else []:
        code = str(item.get("code") or "") if isinstance(item, dict) else ""
        status, one = _send(env, "get", {"archive": "currency", "code": code}) if code else (0, {})
        fields = one.get("fields") if status == 200 else None
        if isinstance(fields, dict) and str(fields.get("iotherused", "")).strip() == "-1":
            return code
    return ""


def glarc_currency(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    picked = _pick(env, spec, currency_free())
    if len(picked) != 2:
        return "N/A", "测试账套里挑不到空闲的币种名称和符号"
    name, symbol = picked
    bodies = ({"code": symbol, "caltype": 1, "precision": 4}, {"precision": 2}, "4", "2")
    problem = _run(env, "currency", name, bodies, "idec")
    if problem:
        return "FAIL", problem
    base = _base_currency(env)
    if base:
        problem = _refused(env, "currency", base)
        if problem:
            return "FAIL", problem[:240]
    return "PASS", f"currency {name}（{symbol}）"


GLARC_OPS = {"glarc_sign": glarc_sign, "glarc_currency": glarc_currency}

_CASES = (
    ("archive-voucher-sign-write", "凭证类别新增读取改名删除，预置类别不能删除", "glarc_sign", "voucher_sign"),
    ("archive-currency-write", "币种新增读取修改删除，本位币不能删除", "glarc_currency", "currency"),
)


def glarc_cases() -> list[CaseSpec]:
    rows = []
    for name, title, op, archive in _CASES:
        step = Step("custom", sql_name=op, id_from="none")
        tags = frozenset({"arc:" + archive})
        data = (SIGN_TRANSFER,) if op == "glarc_sign" else ()
        rows.append(CaseSpec(name, title, (), (step,), True, data=data, tags=tags, routes=_ROUTES))
    return rows
