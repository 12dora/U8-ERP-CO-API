"""客户、供应商的银行账户（桥 ArcPartnerBank，受控 SQL）和联系人（桥 ArcPartnerContact），一次一行、不碰同一客户的其他行。

银行账户：挑一个还没有任何账户的客户（供应商），依次核对默认账户规则——第一个账户自动成为默认；
新增非默认的第二个账户；把第二个设为默认后第一个变成非默认；默认账户不能删除、不能取消默认（409 state_mismatch）；
换回默认后两个都删掉，读取 404。账号是本用例现编的测试号（E2E + 时间戳）。
客户联系人：U8 自动编号，新增的 code 写成 "<客户编码>:"，从响应取 U8 编的号 → 读取 → 改备注 → 读取（手机不变）→ 删除 → 读取 404。
供应商联系人同样走一遍（新增走 EAI 分发器，修改、删除是桥的受控 SQL）；另有读取用例：列表，有数据时再读一条（不写）。
挑不到记 N/A。步骤 kind 为 "custom"，sql_name 是操作名（PARTNER_OPS 并入 ops_run）。用例中途失败时尽力删掉本用例建的行（先非默认、后默认）。
注意：银行账户设为默认会同步客户（供应商）档案上的开户银行、银行账号、所属银行编码，快照还原前可能留下这一改动。
"""

from __future__ import annotations

import datetime as dt

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arc_write_more import _cleanup, _expect, _read_problem, _send
from co.e2e.casespec.arc_write_more_sql import safe_part
from co.e2e.casespec.arc_partner_sql import bank_partner, contact_customer, vendor_contact_any
from co.e2e.casespec.sqlexec import run_guarded

_ROUTES = tuple("/v1/archives/" + op for op in ("create", "get", "update", "delete", "list"))
_BRANCH = "u8co e2e 测试支行"
# 银行账户的步骤：(操作, 第几个账户, fields, 期望状态, 之后核对的默认标志 {第几个账户: "1"/"0"})。
_BANK_STEPS = (
    ("create", 0, {"branch": _BRANCH}, 200, {0: "1"}),
    ("create", 1, {"branch": _BRANCH, "default": False}, 200, {0: "1", 1: "0"}),
    ("update", 1, {"default": True}, 200, {0: "0", 1: "1"}),
    ("delete", 1, None, 409, {1: "1"}),
    ("update", 1, {"default": False}, 409, {1: "1"}),
    ("update", 0, {"default": True}, 200, {0: "1", 1: "0"}),
    ("delete", 1, None, 200, {}),
    ("delete", 0, None, 200, {}),
)


def _pick(env: Env, spec: CaseSpec, query: str) -> list[str]:
    cell = first_cell(run_guarded(env, query, spec.name)).strip()
    if cell == "":
        return []
    return [safe_part(part) for part in cell.split("|")]


def _defaults(env: Env, keys: list[dict], want: dict[int, str]) -> str:
    for index, flag in want.items():
        problem = _read_problem(env, keys[index], "bDefault", flag)
        if problem:
            return f"账户 {index + 1} {problem}"
    return ""


def _bank_step(env: Env, keys: list[dict], row: tuple) -> str:
    op, index, fields, want, flags = row
    body = dict(keys[index]) if fields is None else dict(keys[index], fields=fields)
    status, payload = _send(env, op, body)
    label = f"{op} 账户 {index + 1}"
    if want == 409 and (status != 409 or payload.get("code") != "state_mismatch"):
        return f"{label} 应 409 state_mismatch，实际 {status} {payload.get('code')} {payload.get('message')}"[:200]
    return _expect(status, payload, want, label) or _defaults(env, keys, flags)


def _bank_run(env: Env, archive: str, partner: str) -> str:
    stamp = dt.datetime.now().strftime("%m%d%H%M%S")
    keys = [{"archive": archive, "code": f"{partner}:E2E{stamp}{n}"} for n in (1, 2)]
    for row in _BANK_STEPS:
        problem = _bank_step(env, keys, row)
        if problem:
            undo = "；".join(_cleanup(env, archive, key["code"]) for key in reversed(keys))
            return (undo + "；" + problem)[:240]
    status, payload = _send(env, "get", keys[0])
    return _expect(status, payload, 404, "删除后读取")


def _bank(env: Env, spec: CaseSpec, side: str) -> tuple[str, str]:
    picked = _pick(env, spec, bank_partner(side))
    if not picked:
        return "N/A", f"测试账套里挑不到没有银行账户的{'客户' if side == 'customer' else '供应商'}"
    problem = _bank_run(env, side + "_bank", picked[0])
    if problem:
        return "FAIL", problem
    return "PASS", f"{side}_bank {picked[0]}"


def _contact_create(env: Env, archive: str, partner: str, name: str) -> tuple[str, str]:
    """新增（编码第二段留空），返回（U8 编的完整编码, 问题）。"""
    fields = {"name": name, "sex": "男", "mobile": "13800000000"}
    if archive == "vendor_contact":
        fields = {"name": name, "mobile": "13800000000", "be_main_linker": False}
    status, payload = _send(env, "create", {"archive": archive, "code": partner + ":", "fields": fields})
    bad = _expect(status, payload, 200, "新增")
    code = str(payload.get("code") or "")
    if not bad and (not code.startswith(partner + ":") or code == partner + ":"):
        bad = f"新增响应的 code 不是 U8 编的号：{code!r}"
    return code, bad


def _contact_run(env: Env, key: dict, name: str) -> str:
    bad = _read_problem(env, key, "cContactName", name)
    if bad:
        return bad
    status, payload = _send(env, "update", dict(key, fields={"memo": "u8co e2e 备注"}))
    bad = _expect(status, payload, 200, "修改") or _read_problem(env, key, "cMemo", "u8co e2e 备注")
    if bad or _read_problem(env, key, "cMobilePhone", "13800000000"):
        return bad or "修改后手机变了"
    status, payload = _send(env, "delete", key)
    bad = _expect(status, payload, 200, "删除")
    if bad or payload.get("deleted") is not True:
        return bad or "删除响应缺 deleted"
    status, payload = _send(env, "get", key)
    return _expect(status, payload, 404, "删除后读取")


def _contact(env: Env, spec: CaseSpec, side: str) -> tuple[str, str]:
    archive = side + "_contact"
    picked = _pick(env, spec, contact_customer(side))
    if not picked:
        return "N/A", f"测试账套里挑不到{'客户' if side == 'customer' else '供应商'}"
    name = "u8co e2e 联系人" + dt.datetime.now().strftime("%m%d%H%M%S")
    code, problem = _contact_create(env, archive, picked[0], name)
    if problem:
        return "FAIL", problem[:240]
    problem = _contact_run(env, {"archive": archive, "code": code}, name)
    if problem:
        return "FAIL", (_cleanup(env, archive, code) + "；" + problem)[:240]
    return "PASS", f"{archive} {code}"


def _vendor_contact(env: Env, spec: CaseSpec) -> tuple[str, str]:
    status, payload = _send(env, "list", {"archive": "vendor_contact", "limit": 5})
    bad = _expect(status, payload, 200, "列表")
    if bad or not isinstance(payload.get("items"), list):
        return "FAIL", bad or "列表响应缺 items"
    picked = _pick(env, spec, vendor_contact_any())
    if len(picked) != 2:
        return "PASS", "vendor_contact 列表（本账套没有供应商联系人，未测读取）"
    key = {"archive": "vendor_contact", "code": f"{picked[0]}:{picked[1]}"}
    status, payload = _send(env, "get", key)
    bad = _expect(status, payload, 200, "读取")
    if bad:
        return "FAIL", bad
    return "PASS", "vendor_contact " + key["code"]


def partner_cus_bank(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    return _bank(env, spec, "customer")


def partner_ven_bank(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    return _bank(env, spec, "vendor")


def partner_cus_contact(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    return _contact(env, spec, "customer")


def partner_ven_contact_write(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    return _contact(env, spec, "vendor")


def partner_ven_contact(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    return _vendor_contact(env, spec)


PARTNER_OPS = {
    "partner_cus_bank": partner_cus_bank,
    "partner_ven_bank": partner_ven_bank,
    "partner_cus_contact": partner_cus_contact,
    "partner_ven_contact": partner_ven_contact,
    "partner_ven_contact_write": partner_ven_contact_write,
}

_CASES = (
    ("archive-customer-bank-write", "客户银行账户新增、切换默认、默认账户闸门、删除", "partner_cus_bank", "customer_bank"),
    ("archive-vendor-bank-write", "供应商银行账户新增、切换默认、默认账户闸门、删除", "partner_ven_bank", "vendor_bank"),
    ("archive-customer-contact-write", "客户联系人新增（U8 编号）读取修改删除", "partner_cus_contact", "customer_contact"),
    ("archive-vendor-contact-read", "供应商联系人读取：列表，有数据时读一条", "partner_ven_contact", "vendor_contact"),
    ("archive-vendor-contact-write", "供应商联系人新增（U8 编号）读取修改删除", "partner_ven_contact_write", "vendor_contact"),
)


def partner_cases() -> list[CaseSpec]:
    rows = []
    for name, title, op, archive in _CASES:
        step = Step("custom", sql_name=op, id_from="none")
        family = "arc-read:" if op == "partner_ven_contact" else "arc:"
        tags = frozenset({family + archive, family + archive.split("_", 1)[0]})
        rows.append(CaseSpec(name, title, (), (step,), True, tags=tags, routes=_ROUTES))
    return rows
