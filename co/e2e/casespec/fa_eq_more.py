"""固定资产的拒绝路径与设备台账（EQ_EQData）用例；固定资产卡片的写入在 fa_eq。

fa-dispose-refused（任何通道）：变动单、资产减少不在 API 里（在 U8 客户端做）。单据类型 fa_change、fa_dispose 的新增和
  vouchers/load fa_change（未知单据类型）、archives/update fa_card（卡片不能直接修改）都 400（不限错误码）。全部带 dry_run，
  卡片编号用不存在的 00000，万一桥放行也不会写账套。
archive-read-equipment：设备台账列表 → 读取首行（编码一致、fields 非空）→ keys_only 编码同完整列表 → code_prefix 找回首行。
  没有设备记 N/A（不少账套没有设备数据）。
equipment-create：照最新一台设备挑设备类别、使用部门、设备状态 → 预演新增（validate，编码仍不存在）→ 新增 ZZ 开头的编码
  → 读取（名称一致）→ 修改 400、删除 400（U8 官方 EAI eqdata 只有新增）→ 表里正好一行。
  **新设备留给快照还原**：API 没有设备删除。没有设备的账套 N/A。
"""

from __future__ import annotations

from datetime import datetime

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _Fail, _guard, _NotApplicable, _Run
from co.e2e.casespec.dry_judge import Want, preview_problem
from co.e2e.casespec.fa_eq_sql import eq_count_sql, eq_ref_sql

_ARC = "/v1/archives/"
_VCREATE = "/v1/vouchers/create"
_VLOAD = "/v1/vouchers/load"
EQ = "equipment"
_NAME = "E2E回归设备"
_NO_CARD = "00000"
# 拒绝路径：（说明, 路由, 正文）。
_REFUSALS = (
    ("单据类型 fa_change", _VCREATE,
     {"type": "fa_change", "head": {"card_code": _NO_CARD, "change_type": 1, "value": "1", "reason": "R21e2e"}}),
    ("单据类型 fa_dispose", _VCREATE, {"type": "fa_dispose", "head": {"card_code": _NO_CARD}}),
    ("修改卡片", _ARC + "update", {"archive": "fa_card", "code": _NO_CARD, "fields": {"name": "R21e2e"}}),
    ("读取变动单", _VLOAD, {"type": "fa_change", "id": 1}),
)


def _refused(run: _Run) -> tuple[str, str]:
    bad = []
    for what, route, body in _REFUSALS:
        status, payload = run.send(route, dict(body, dry_run=True))
        if status != 400:
            bad.append(f"{what} {status} {payload.get('code')}")
    if bad:
        raise _Fail("应 400：" + "；".join(bad))
    return "PASS", f"{len(_REFUSALS)} 个固定资产拒绝路径都 400（变动单、资产减少、改卡片、读变动单）"


def _codes(payload: dict) -> list[str]:
    rows = payload.get("items")
    if not isinstance(rows, list):
        raise _Fail("列表响应缺 items")
    return [str(row.get("code") or "") for row in rows if isinstance(row, dict)]


def _read(run: _Run) -> tuple[str, str]:
    codes = _codes(run.ok(_ARC + "list", {"archive": EQ, "limit": 5}, "设备台账列表"))
    if not codes:
        raise _NotApplicable("账套没有设备台账")
    if "" in codes:
        raise _Fail("设备台账列表行缺 code")
    got = run.ok(_ARC + "get", {"archive": EQ, "code": codes[0]}, "读取设备")
    if str(got.get("code")) != codes[0] or not isinstance(got.get("fields"), dict) or not got["fields"]:
        raise _Fail(f"读回设备 {codes[0]} 的编码不对或 fields 为空")
    keys = _codes(run.ok(_ARC + "list", {"archive": EQ, "limit": 5, "keys_only": True}, "keys_only 列表"))
    if keys != codes:
        raise _Fail("keys_only 的编码与完整列表不同")
    found = _codes(run.ok(_ARC + "list", {"archive": EQ, "code_prefix": codes[0], "limit": 5}, "按前缀列表"))
    if codes[0] not in found:
        raise _Fail(f"code_prefix={codes[0]} 没有找回该设备")
    return "PASS", f"首页 {len(codes)} 台，读取 {codes[0]}、keys_only、code_prefix 一致"


def _name_of(got: dict) -> str:
    fields = got.get("fields") if isinstance(got.get("fields"), dict) else {}
    for key, value in fields.items():
        if str(key).lower() in ("name", "ceqname"):
            return str(value or "")
    return str(got.get("name") or "")


def _eq_fields(run: _Run) -> dict:
    parts = run.pick(eq_ref_sql(), 4)
    if parts is None or parts[0] in ("", "0"):
        raise _NotApplicable("账套没有设备台账")
    fields = {"name": _NAME}
    for key, value in zip(("ceqtypecode", "cdepcode", "cstacode"), parts[1:]):
        if value:
            fields[key] = value
    return fields


def _create(run: _Run) -> tuple[str, str]:
    fields = _eq_fields(run)
    code = "ZZ" + datetime.now().strftime("%m%d%H%M%S")
    if run.cell(eq_count_sql(code)) != "0":
        raise _NotApplicable(f"设备编码 {code} 已存在")
    key = {"archive": EQ, "code": code}
    status, payload = run.send(_ARC + "create", dict(key, fields=fields, dry_run=True))
    problem = preview_problem(payload, Want("validate", "any", EQ, 0, True)) if status == 200 else f"{status}"
    if problem or run.cell(eq_count_sql(code)) != "0":
        raise _Fail(f"预演新增设备：{problem or '预演之后编码已存在'} {payload.get('message') or ''}"[:240])
    made = run.ok(_ARC + "create", dict(key, fields=fields), "新增设备")
    if made.get("code") != code:
        raise _Fail(f"新增设备响应的编码 {made.get('code')!r} 不是 {code}")
    name = _name_of(run.ok(_ARC + "get", key, "读取新设备"))
    if name != _NAME:
        raise _Fail(f"读回的设备名称 {name!r} 不是 {_NAME}")
    for op in ("update", "delete"):
        extra = dict(key, fields={"name": _NAME + "改"}) if op == "update" else key
        status, payload = run.send(_ARC + op, extra)
        if status != 400:
            raise _Fail(f"设备{'修改' if op == 'update' else '删除'}应 400，实际 {status} {payload.get('code')}")
    if run.cell(eq_count_sql(code)) != "1":
        raise _Fail(f"设备编码 {code} 不是正好一行")
    return "PASS", f"设备 {code}：预演、新增、读取；修改、删除 400；新设备留给快照还原"


def _op(body):
    def run_op(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
        return _guard(env, spec, body)

    return run_op


MORE_OPS = {
    "fa_refused": _op(_refused),
    "eq_read": _op(_read),
    "eq_create": _op(_create),
}


def more_cases() -> list[CaseSpec]:
    def case(name: str, title: str, op: str, routes: tuple[str, ...], tags: tuple[str, ...]) -> CaseSpec:
        step = Step("custom", sql_name=op, id_from="none")
        return CaseSpec(name, title, (), (step,), True, optional=op != "fa_refused", tags=frozenset(tags),
                        routes=routes)

    arc = tuple(_ARC + op for op in ("list", "get"))
    return [
        case("fa-dispose-refused", "固定资产拒绝路径：变动单、资产减少、改卡片、读变动单都 400", "fa_refused",
             (_VCREATE, _ARC + "update", _VLOAD), ("probe",)),
        case("archive-read-equipment", "设备台账列表、读取、keys_only、code_prefix（没有设备 N/A）", "eq_read", arc,
             ("arc-read:" + EQ,)),
        case("equipment-create", "设备台账新增（EAI eqdata）、读取；修改删除 400；新设备留给快照还原", "eq_create",
             (*arc[1:], *(_ARC + op for op in ("create", "update", "delete"))), ("arc:" + EQ, "arc:write")),
    ]
