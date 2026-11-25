"""存货 Inventory_Sub 列落库核对的反例（桥的 ArcInvSub.Verify）：新增存货时带 bbatchproperty1=1。
U8 的存货 EAI 在批次档案标志 bBatchCreate 为 0 时把批次属性 bBatchProperty1–10 压成 0、且不报错，桥新增后回读发现没写进去：
502 u8_fields_ignored，detail.fields 里有 bbatchproperty1（sent 1、stored 0），存货本身已保存；用例随后删除它并核对读取 404。
U8 真写进了 bbatchproperty1（模板存货本来就建批次档案）时反例不成立，记 N/A。
"""

from __future__ import annotations

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.gl_voucher_bodies import ARC_SPECS, TEMPLATE
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.gl_voucher_sql import free_code_sql, safe_code
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke
from co.e2e.testdata import value as td

_ROUTE = "/v1/archives/"
_TAG = "bbatchproperty1"


def _send(env: Env, op: str, extra: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(extra)
    return invoke(env.client, _ROUTE + op, fields)


def _cleanup(env: Env, key: dict) -> str:
    status, payload = _send(env, "delete", key)
    if status not in (200, 404):
        return f"清理删除返回 {status} {payload.get('code')}"
    status, _payload = _send(env, "get", key)
    return "" if status == 404 else f"清理后读取返回 {status}"


def _problem(status: int, payload: dict) -> str:
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    fields = detail.get("fields") if isinstance(detail.get("fields"), dict) else {}
    pair = fields.get(_TAG) if isinstance(fields.get(_TAG), dict) else {}
    if status != 502 or payload.get("code") != "u8_fields_ignored":
        return f"新增应 502 u8_fields_ignored，实际 {status} {payload.get('code')} {payload.get('message')}"[:200]
    if str(pair.get("sent")) != "1" or str(pair.get("stored")) != "0":
        return f"detail.fields.{_TAG} 应为 sent 1、stored 0，实际 {pair}"[:200]
    return ""


def arc_more_sub_ignored(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    cell = first_cell(run_guarded(env, free_code_sql("inventory"), spec.name)).strip()
    if cell in ("", "-"):
        return "N/A", "账套里候选存货编码都被占用"
    try:
        code = safe_code(cell)
    except ValueError:
        return "SKIP", "挑出的存货编码不合法"
    key = {"archive": "inventory", "code": code}
    fields = ARC_SPECS["inventory"][0](code, env.today)
    fields[_TAG] = 1
    status, payload = _send(env, "create", dict(key, fields=fields, template=td(TEMPLATE["inventory"])))
    if status == 200:
        cleaned = _cleanup(env, key)
        return "N/A", ("U8 写入了 " + _TAG + "，反例不成立；" + cleaned)[:240]
    problem = _problem(status, payload)
    cleaned = _cleanup(env, key)
    if problem or cleaned:
        return "FAIL", (problem + "；" + cleaned).strip("；")[:240]
    return "PASS", f"inventory {code}：{_TAG} 未写入，502 u8_fields_ignored，已删除"


ARC_MORE_SUB_OPS = {"arc_more_sub_ignored": arc_more_sub_ignored}
