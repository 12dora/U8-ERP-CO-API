"""档案写入：货位、计量单位、自定义项档案、客户存货对照各走一遍 新增 → 读取 → 修改 → 读取 → 删除 → 读取 404
（自定义项档案、客户存货对照的 EAI 不提供修改，修改走桥的受控 SQL，只改 alias / 名称这类非主键列）；
另有删除闸门（有下级的货位、已被存货使用的计量单位，桥在调用 U8 之前 409）。收付款协议（受控 SQL）同样走一遍，
另有它的删除闸门（被客户档案的销售协议栏引用的协议不能删）。
之后可写的七类（计量单位组、结算方式、收发类别、采购类型、销售类型、地区分类、银行档案）同样走一遍，
编码挑第一个空闲的（ZZ 打头，分级档案建成一级，见 arc_write_more_sql._free）；计量单位组建固定换算（无换算组 U8 只许一个），
采购类型、销售类型带一个末级的入库（出库）类别 rstype_code（U8 档案设置可能要求必输）。币种的写入见 arc_gl。
另有删除闸门（有下级的收发类别、已有计量单位的计量单位组）。

依赖（仓库、计量单位组、自定义项、客户和存货）从测试账套挑（arc_write_more_sql），新编码带时间（ZZ + 月日时分秒），
挑不到记 N/A。步骤 kind 为 "custom"，sql_name 是操作名（ARC_MORE_OPS 并入 ops_run）。
用例中途失败时尽力删掉本用例建的档案（快照还原前也不留数据）。
"""

from __future__ import annotations

import datetime as dt

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arc_write_more_sql import arc_more_pick_sql, safe_part
from co.e2e.casespec.arc_inv_sub import ARC_MORE_SUB_OPS
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke

_ROUTE = "/v1/archives/"
_NAME = "u8co e2e"
# 档案 → get 的 fields 里核对的列（表列名）：新增后的值、修改后的值。
_COLUMN = {
    "position": "cPosName",
    "unit": "cComUnitName",
    "user_define": "cAlias",
    "customer_inventory": "cCusInvName",
    "unit_group": "cGroupName",
    "settle_style": "cSSName",
    "rd_style": "cRdName",
    "purchase_type": "cPTName",
    "sale_type": "cSTName",
    "district_class": "cDCName",
    "aa_bank": "cBankName",
    "pay_agreement": "cName",
}
_TITLES = (
    ("position", "货位"),
    ("unit", "计量单位"),
    ("user_define", "自定义项档案"),
    ("customer_inventory", "客户存货对照"),
    ("unit_group", "计量单位组"),
    ("settle_style", "结算方式"),
    ("rd_style", "收发类别"),
    ("purchase_type", "采购类型"),
    ("sale_type", "销售类型"),
    ("district_class", "地区分类"),
    ("aa_bank", "银行档案"),
    ("pay_agreement", "收付款协议"),
)
# 七类的名称后缀（结算方式、收发类别的名称最长 12，"u8co e2e 结算改" 正好 12）。
_LABEL = {
    "unit_group": " 组",
    "settle_style": " 结算",
    "rd_style": " 收发",
    "purchase_type": " 采购",
    "sale_type": " 销售",
    "district_class": " 地区",
    "aa_bank": " 银行",
}


# 新增、删除走 EAI，修改走桥的受控 SQL 的两类，和全程受控 SQL 的协议：只影响用例标题。
_SQL_EDIT = ("user_define", "customer_inventory")


def _send(env: Env, op: str, extra: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(extra)
    return invoke(env.client, _ROUTE + op, fields)


def _pick(env: Env, spec: CaseSpec, name: str) -> list[str]:
    cell = first_cell(run_guarded(env, arc_more_pick_sql(name), spec.name)).strip()
    if cell == "":
        return []
    return [safe_part(part) for part in cell.split("|")]


def _stamp() -> str:
    return "ZZ" + dt.datetime.now().strftime("%m%d%H%M%S")


def _plan_style(archive: str, picked: list[str], stamp: str) -> tuple[str, dict, dict]:
    """七类：picked[0] 是空闲编码，采购 / 销售类型的 picked[1] 是末级入库 / 出库类别。核对的列对应 fields 的第一个标签。"""
    del stamp
    label = _NAME + _LABEL[archive]
    made: dict = {"name": label}
    if archive == "unit_group":
        made["type"] = 1
    elif archive == "rd_style":
        made["rsflag"] = 1
    elif archive in ("purchase_type", "sale_type"):
        made["rstype_code"] = picked[1]
    return picked[0], made, {"name": label + "改"}


def _plan(archive: str, picked: list[str]) -> tuple[str, dict, dict]:
    """编码、新增的 fields、修改的 fields。"""
    stamp = _stamp()
    if archive in _LABEL:
        return _plan_style(archive, picked, stamp)
    if archive == "pay_agreement":
        made = {"cName": _NAME + " 协议", "iLZYJ": 0, "iPLNum": 3, "dblZQNum": 30}
        return picked[0], made, {"cName": _NAME + " 协议改", "dblZQNum": 45}
    if archive == "position":
        return picked[1], {"name": _NAME + " 货位", "warehouse_code": picked[0]}, {"name": _NAME + " 货位改"}
    if archive == "unit":
        made = {"name": _NAME + " 单位", "group_code": picked[0]}
        changed = {"name": _NAME + " 单位改"}
        if picked[1] != "0":
            made["changerate"] = 10
            changed["changerate"] = 20
        return stamp, made, changed
    if archive == "user_define":
        return picked[0] + ":" + stamp, {"alias": "ZA"}, {"alias": "ZB"}
    made = {"ccusinvname": _NAME + " 对照", "ccusinvcode": stamp}
    return picked[0] + ":" + picked[1], made, {"ccusinvname": _NAME + " 对照改"}


def _expect(status: int, payload: dict, want: int, label: str) -> str:
    if status != want:
        return f"{label} {status} {payload.get('code')} {payload.get('message')}"[:200]
    return ""


def _read_problem(env: Env, key: dict, column: str, want: object) -> str:
    status, got = _send(env, "get", key)
    bad = _expect(status, got, 200, "读取")
    if bad:
        return bad
    fields = got.get("fields") if isinstance(got.get("fields"), dict) else {}
    value = next((v for k, v in fields.items() if k.lower() == column.lower()), None)
    if str(value or "").strip() != str(want):
        return f"读取 {column}={value!r}，期望 {want!r}"
    return ""


def _cycle_problem(env: Env, archive: str, code: str, bodies: tuple[dict, dict]) -> str:
    made, changed = bodies
    key = {"archive": archive, "code": code}
    column = _COLUMN[archive]
    status, payload = _send(env, "create", dict(key, fields=made))
    bad = _expect(status, payload, 200, "新增")
    if bad:
        return bad
    bad = _read_problem(env, key, column, made.get(_first_tag(made)))
    if bad:
        return bad
    status, payload = _send(env, "update", dict(key, fields=changed))
    bad = _expect(status, payload, 200, "修改") or _read_problem(env, key, column, changed.get(_first_tag(changed)))
    if bad:
        return bad
    status, payload = _send(env, "delete", key)
    bad = _expect(status, payload, 200, "删除")
    if bad or payload.get("deleted") is not True:
        return bad or "删除响应缺 deleted"
    status, payload = _send(env, "get", key)
    return _expect(status, payload, 404, "删除后读取")


def _first_tag(fields: dict) -> str:
    # 每类核对的列对应 fields 里的第一个标签（name / alias / ccusinvname / cName）。
    return next(iter(fields))


def _cleanup(env: Env, archive: str, code: str) -> str:
    try:
        status, _payload = _send(env, "delete", {"archive": archive, "code": code})
    except (OSError, RuntimeError, ValueError) as exc:
        return "补偿删除出错：" + str(exc)[:80]
    if status in (200, 404):
        return "已补偿删除 " + code
    return f"补偿删除 {code} 返回 {status}，留给快照还原"


def arc_more_cycle(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    archive = dict(step.fields)["archive"]
    picked = _pick(env, spec, archive)
    if not picked:
        return "N/A", "测试账套里挑不到依赖档案或空闲编码"
    code, made, changed = _plan(archive, picked)
    problem = _cycle_problem(env, archive, code, (made, changed))
    if problem:
        return "FAIL", (_cleanup(env, archive, code) + "；" + problem)[:240]
    return "PASS", f"{archive} {code}"


def arc_more_guard(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    options = dict(step.fields)
    archives = options.get("archives", "position,unit").split(",")
    picked = _pick(env, spec, options.get("pick", "guard"))
    if len(picked) != len(archives):
        return "N/A", "测试账套里挑不到删除闸门要用的档案"
    for archive, code in zip(archives, picked, strict=True):
        status, payload = _send(env, "delete", {"archive": archive, "code": code})
        if status != 409 or payload.get("code") != "state_mismatch":
            return "FAIL", f"{archive} {code} 删除应 409 state_mismatch，实际 {status} {payload.get('code')}"[:240]
        status, payload = _send(env, "get", {"archive": archive, "code": code})
        bad = _expect(status, payload, 200, archive + " 删除被拒后读取")
        if bad:
            return "FAIL", bad
    return "PASS", "、".join(f"{archive} {code}" for archive, code in zip(archives, picked, strict=True)) + " 删除被拒"


# 标题的说明：自定义项档案、客户存货对照新增删除走 EAI、修改走受控 SQL；收付款协议全程受控 SQL。
_WHAT = {
    "user_define": "档案新增读取修改删除（新增、删除走 EAI，修改走受控 SQL）",
    "customer_inventory": "档案新增读取修改删除（新增、删除走 EAI，修改走受控 SQL）",
    "pay_agreement": "档案新增读取修改删除（受控 SQL）",
}
def arc_more_alias_dup(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    """自定义项档案：同一自定义项下代码 alias 不能重复（U8 的 CheckcAliasUnique）。建两个档案值，把第二个的 alias 改成第一个的 → 409。"""
    del step
    picked = _pick(env, spec, "user_define")
    if not picked:
        return "N/A", "测试账套里挑不到需要建档的自定义项"
    stamp = _stamp()
    keys = [{"archive": "user_define", "code": picked[0] + ":" + prefix + stamp[2:]} for prefix in ("ZZ", "ZY")]
    aliases = ["E" + stamp[2:], "F" + stamp[2:]]
    made = []
    problem = ""
    try:
        for key, alias in zip(keys, aliases, strict=True):
            status, payload = _send(env, "create", dict(key, fields={"alias": alias}))
            problem = _expect(status, payload, 200, "新增")
            if problem:
                break
            made.append(key)
        if not problem:
            status, payload = _send(env, "update", dict(keys[1], fields={"alias": aliases[0]}))
            problem = _expect(status, payload, 409, "改成重复的代码")
            if not problem and payload.get("code") != "state_mismatch":
                problem = f"改成重复的代码应 409 state_mismatch，实际 {payload.get('code')}"
        if not problem:
            problem = _read_problem(env, keys[1], "cAlias", aliases[1])
    finally:
        notes = [_cleanup(env, key["archive"], key["code"]) for key in made]
    if problem:
        return "FAIL", (problem + "；" + "；".join(notes))[:240]
    return "PASS", f"user_define {keys[1]['code']}：代码重复被拒，档案值不变"


ARC_MORE_OPS = {"arc_more_cycle": arc_more_cycle, "arc_more_guard": arc_more_guard, "arc_more_alias_dup": arc_more_alias_dup, **ARC_MORE_SUB_OPS}


def _custom(op: str, fields: tuple = ()) -> Step:
    return Step("custom", fields=fields, sql_name=op, id_from="none")


# 路由（按改动选用例用）；资源标签由 casespec/tags.py 按 fields 的 archive / archives 推导。
_CYCLE_ROUTES = tuple(_ROUTE + op for op in ("create", "get", "update", "delete"))
_GUARD_ROUTES = (_ROUTE + "delete", _ROUTE + "get")


def arc_more_cases() -> list[CaseSpec]:
    # arc_probe 引用本模块的 _send，放到这里再引入，避免循环引用。
    from co.e2e.casespec.arc_probe import PROBED, probe_step

    rows = []
    for archive, label in _TITLES:
        steps = (_custom("arc_more_cycle", (("archive", archive),)),)
        if archive in PROBED:
            # 附加核对（arc_probe）：下级货位的末级标志、类型的默认标志、自定义项汉字长度。
            steps += (probe_step(archive),)
        name = "archive-" + archive.replace("_", "-") + "-write"
        what = _WHAT.get(archive, "档案新增读取修改删除（EAI）")
        rows.append(CaseSpec(name, label + what, (), steps, True, routes=_CYCLE_ROUTES))
    guard_unit = _custom("arc_more_guard")
    rows.append(CaseSpec("archive-rw7-delete-guard", "有下级的货位、已被存货使用的计量单位不能删除", (), (guard_unit,), True, routes=_GUARD_ROUTES))
    guard_style = _custom("arc_more_guard", (("pick", "guard_style"), ("archives", "rd_style,unit_group")))
    title_style = "有下级的收发类别、已有计量单位的计量单位组不能删除"
    rows.append(CaseSpec("archive-rw8-delete-guard", title_style, (), (guard_style,), True, routes=_GUARD_ROUTES))
    sub = _custom("arc_more_sub_ignored", (("archive", "inventory"),))
    title_sub = "存货新增带 bbatchproperty1（批次档案标志为 0 时 U8 不写）：桥回读发现没写入，502 u8_fields_ignored"
    rows.append(CaseSpec("archive-inventory-sub-ignored", title_sub, (), (sub,), True, routes=_CYCLE_ROUTES))
    dup = _custom("arc_more_alias_dup", (("archive", "user_define"),))
    title_dup = "自定义项档案的代码在同一自定义项下不能重复（修改成已有代码 409）"
    rows.append(CaseSpec("archive-user-define-alias-dup", title_dup, (), (dup,), True, routes=_CYCLE_ROUTES))
    guard_sa = _custom("arc_more_guard", (("pick", "guard_agreement"), ("archives", "pay_agreement")))
    title_sa = "已被客户档案销售协议栏引用的收付款协议不能删除"
    rows.append(CaseSpec("archive-pay-agreement-delete-guard", title_sa, (), (guard_sa,), True, routes=_GUARD_ROUTES))
    guard_doc = _custom("arc_more_guard", (("pick", "guard_agreement_doc"), ("archives", "pay_agreement")))
    title_doc = "只被销售订单的收付款协议栏引用的收付款协议不能删除"
    rows.append(CaseSpec("archive-pay-agreement-delete-guard-doc", title_doc, (), (guard_doc,), True, routes=_GUARD_ROUTES))
    return rows
