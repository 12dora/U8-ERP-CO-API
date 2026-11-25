"""总账凭证和档案用例的请求正文和响应断言。不连接服务器。

凭证键（期间|类别|号）和档案编码按用例放在 bag 的 `core:<用例名>` 下，只来自桥的响应或挑单 SQL。
科目、部门、人员类别、存货模板等站点档案来自测试数据（co.e2e.testdata），缺了用例记 SKIP。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from co.e2e.casespec.checks import View, read_path, same
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.gl_voucher_sql import cash_item, gl_key, parse_key, safe_code, safe_prefix
from co.e2e.testdata import SIGN_BANK, SIGN_TRANSFER, current, gl_sign
from co.e2e.testdata import value as td

_DIGEST = "CO桥回归"
GL_DATA = ("gl.expense_account", "gl.expense_dept", "gl.payable_account", SIGN_TRANSFER)
BANK_DATA = GL_DATA + ("gl.bank_account", "gl.cash_flow_item", SIGN_BANK)
_ARC_DATA = {
    # 行业编码 trade_code 可不配：账套没有行业分类时不传 cCusTradeCCode（见 _customer）。
    "customer": ("sort_code", "super_dept", "spec_operator", "currency"),
    "vendor": ("sort_code", "currency"),
    "person": ("dept", "employ_state", "person_type", "employment_form"),
    "inventory": ("template",),
}


def arc_data(archive: str) -> tuple[str, ...]:
    base = "archives." + archive + "."
    return (base + "codes",) + tuple(base + key for key in _ARC_DATA.get(archive, ()))


def slot(spec: CaseSpec) -> str:
    return "core:" + spec.name


# ---- 总账凭证正文 ----


def _key_fields(text: str) -> dict:
    key = parse_key(text)
    return {"period": key.period, "sign": key.sign, "no": key.no}


def _transfer_lines(amount: float) -> list[dict]:
    return [
        {"account": td("gl.expense_account"), "digest": _DIGEST, "debit": amount, "dept": td("gl.expense_dept")},
        {"account": td("gl.payable_account"), "digest": _DIGEST, "credit": amount},
    ]


def _gl_create_transfer(env: Env, _key: str) -> dict:
    return {"head": {"sign": gl_sign(SIGN_TRANSFER), "date": env.today}, "lines": _transfer_lines(1.0)}


def _gl_update_transfer(env: Env, key: str) -> dict:
    body = _key_fields(key)
    body.update({"head": {"sign": body["sign"], "date": env.today}, "lines": _transfer_lines(2.0)})
    return body


def _gl_create_bank(env: Env, _key: str) -> dict:
    lines = [
        {
            "account": td("gl.expense_account"),
            "digest": _DIGEST,
            "debit": 1.0,
            "dept": td("gl.expense_dept"),
            "cash_flow": [{"item": cash_item(), "debit": 1.0}],
        },
        {"account": td("gl.bank_account"), "digest": _DIGEST, "credit": 1.0},
    ]
    return {"head": {"sign": gl_sign(SIGN_BANK), "date": env.today}, "lines": lines}


def _gl_key(_env: Env, key: str) -> dict:
    return _key_fields(key)


def _gl_list(env: Env, key: str) -> dict:
    got = parse_key(key)
    return {
        "period_from": got.period,
        "period_to": got.period,
        "sign": got.sign,
        "date_from": env.today,
        "date_to": env.today,
        "limit": 200,
    }


GL_BODIES = {
    "create_transfer": _gl_create_transfer,
    "update_transfer": _gl_update_transfer,
    "create_bank": _gl_create_bank,
    "key": _gl_key,
    "list": _gl_list,
}


# ---- 档案正文 ----


def _trade_code() -> str:
    # 没配这个键时返回空；配了但不合法照常抛 DataMissing。
    archives = current().get("archives")
    customer = archives.get("customer") if isinstance(archives, dict) else None
    if not isinstance(customer, dict) or "trade_code" not in customer:
        return ""
    return td("archives.customer.trade_code")


def _customer(code: str, day: str) -> dict:
    # ccusmngtypecode 故意不传，由桥补默认值。测试数据没配行业编码时不传 cCusTradeCCode。
    fields = {
        "name": "CO回归客户" + code,
        "abbrname": "CO回归客户" + code,
        "sort_code": td("archives.customer.sort_code"),
        "seed_date": day,
        "super_dept": td("archives.customer.super_dept"),
        "spec_operator": td("archives.customer.spec_operator"),
        "ccusexch_name": td("archives.customer.currency"),
        "tax_reg_code": "91000000CO" + code.rjust(8, "0"),
    }
    trade = _trade_code()
    if trade:
        fields["cCusTradeCCode"] = trade
    return fields


def _vendor(code: str, day: str) -> dict:
    # bvencargo 故意不传，由桥补默认值。
    return {
        "name": "CO回归供应商" + code,
        "abbrname": "CO回归供应商" + code,
        "sort_code": td("archives.vendor.sort_code"),
        "seed_date": day,
        "tax_in_price_flag": 1,
        "cvenexch_name": td("archives.vendor.currency"),
        "tax_reg_code": "91000000CO" + code.rjust(8, "0"),
    }


def _department(code: str, day: str) -> dict:
    return {"name": "CO回归部门" + code, "rank": 1, "endflag": 1, "ddepbegindate": day}


def _warehouse(code: str, _day: str) -> dict:
    return {"name": "CO回归仓库" + code, "valuestyle": "全月平均法", "whmanage": 0, "bmrp": 0, "bincost": 1}


def _klass(code: str, _day: str) -> dict:
    return {"name": "CO回归分类" + code, "rank": 1, "end_rank_flag": 1}


def _person(code: str, day: str) -> dict:
    # rIDType 故意不传，由桥补默认值 0。
    return {
        "name": "CO回归人员" + code,
        "rsex": 1,
        "rEmployState": td("archives.person.employ_state"),
        "rpersontype": td("archives.person.person_type"),
        "EmploymentForm": td("archives.person.employment_form"),
        "bpsnperson": 1,
        "cdept_num": td("archives.person.dept"),
        "cdepcode": td("archives.person.dept"),
        "dpvaliddate": day,
    }


def _inventory(code: str, _day: str) -> dict:
    # Inventory_Sub 的列（都取非缺省值，U8 没写会被桥的落库核对发现）：新增时写入，修改规格后仍保持。
    # 发货允超上限、是否依据产品检验结果入库（缺省 1）、保税品、保修期限、请购超额上限。
    return {
        "name": "CO回归存货" + code,
        "specs": "CO回归",
        "finvoutuplimit": 12.5,
        "binbyprocheck": 0,
        "bbondedinv": 1,
        "iwarrantyperiod": 5,
        "fbuyexcess": 3.5,
    }


# 档案 → (新建字段, 修改的标签, 修改后的值, 修改后仍要保持的标签)
ARC_SPECS = {
    "customer": (_customer, "abbrname", "CO回归改", ("name", "tax_reg_code", "sort_code")),
    "vendor": (_vendor, "abbrname", "CO回归改", ("name", "tax_reg_code", "sort_code")),
    "department": (_department, "remark", "CO回归改", ("name",)),
    "warehouse": (_warehouse, "name", "CO回归仓库改", ("valuestyle",)),
    "customer_class": (_klass, "name", "CO回归分类改", ("rank",)),
    "vendor_class": (_klass, "name", "CO回归分类改", ("rank",)),
    "inventory_class": (_klass, "name", "CO回归分类改", ("rank",)),
    "person": (_person, "name", "CO回归人员改", ("cdept_num",)),
    "inventory": (_inventory, "specs", "CO回归改", ("name", "finvoutuplimit", "binbyprocheck", "bbondedinv", "iwarrantyperiod", "fbuyexcess")),
}
TEMPLATE = {"inventory": "archives.inventory.template"}


def archive_of(step: Step) -> str:
    name = dict(step.fields).get("archive", "")
    if name not in ARC_SPECS:
        raise ValueError("未知档案")
    return name


def arc_body(env: Env, spec: CaseSpec, step: Step) -> dict:
    archive = archive_of(step)
    value = env.bag.get(slot(spec), "")
    if step.sql_name in ("list", "list_next"):
        body = {"archive": archive, "code_prefix": safe_prefix(value), "limit": 5}
        if step.sql_name == "list_next":
            body["after"] = env.bag.get(slot(spec) + "#next", "")
        return body
    code = safe_code(value)
    body = {"archive": archive, "code": code}
    make, tag, new, _kept = ARC_SPECS[archive]
    if step.sql_name == "create":
        body["fields"] = make(code, env.today)
        if archive in TEMPLATE:
            body["template"] = td(TEMPLATE[archive])
    elif step.sql_name == "update":
        body["fields"] = {tag: new}
    return body


def arc_expect(env: Env, spec: CaseSpec, step: Step) -> tuple:
    # get 之后按新建时的字段核对；修改后核对新值，其余字段保持不变。
    if step.sql_name not in ("get_created", "get_updated"):
        return ()
    archive = archive_of(step)
    code = env.bag.get(slot(spec), "")
    make, tag, new, kept = ARC_SPECS[archive]
    made = make(code, env.today)
    wants = [("fields." + name, "$num:" + str(made[name])) for name in kept]
    if step.sql_name == "get_created":
        return tuple([("fields.name", made["name"])] + wants)
    if tag != "name":
        wants.append(("fields.name", made["name"]))
    return tuple([("fields." + tag, new)] + wants)


# ---- 响应断言 ----


def _num(val: object) -> Decimal:
    return Decimal(str(val).strip() or "0")


def _sum(payload: dict, field: str) -> Decimal | None:
    lines = payload.get("lines")
    if not isinstance(lines, list):
        return None
    return sum((_num(row.get(field) or 0) for row in lines if isinstance(row, dict)), Decimal(0))


def _lines_debit(payload: dict, _env: Env, _slot: str) -> str:
    total = _sum(payload, "debit")
    return "" if total is None else format(total.normalize(), "f")


def _balanced(payload: dict, _env: Env, _slot: str) -> str:
    debit, credit = _sum(payload, "debit"), _sum(payload, "credit")
    if debit is None or credit is None or debit <= 0:
        return ""
    return "yes" if debit == credit else "no"


def _cashflow(payload: dict, _env: Env, _slot: str) -> str:
    items = []
    for row in payload.get("lines") or []:
        flows = row.get("cash_flow") if isinstance(row, dict) else None
        for flow in flows if isinstance(flows, list) else []:
            if isinstance(flow, dict):
                items.append(str(flow.get("item") or "").strip())
    return ",".join(items)


def _items(payload: dict) -> list[dict]:
    rows = payload.get("items")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _items_has(payload: dict, env: Env, key_slot: str) -> str:
    want = parse_key(env.bag.get(key_slot, ""))
    for row in _items(payload):
        try:
            if gl_key(row.get("period"), row.get("sign"), row.get("no")) == want:
                return "yes"
        except ValueError:
            continue
    return "no"


def codes(payload: dict) -> list[str]:
    return [str(row.get("code") or "") for row in _items(payload)]


def _prefixed(payload: dict, env: Env, key_slot: str) -> str:
    head = env.bag.get(key_slot, "")
    got = codes(payload)
    return "yes" if head and got and all(code.startswith(head) for code in got) else "no"


def _fresh(payload: dict, env: Env, key_slot: str) -> str:
    before = set(env.bag.get(key_slot + "#codes", "").split(","))
    got = codes(payload)
    return "yes" if got and not any(code in before for code in got) else "no"


_SPECIAL = {
    "lines#debit": _lines_debit,
    "lines#balanced": _balanced,
    "lines#cashflow": _cashflow,
    "items#has": _items_has,
    "items#prefixed": _prefixed,
    "items#fresh": _fresh,
}


def _num_eq(got: str, want: str) -> bool:
    try:
        return _num(got) == _num(want)
    except InvalidOperation:
        return got.strip() == want.strip()


def _ok(got: str, want: str, env: Env, key_slot: str) -> bool:
    if want.startswith("$td:"):
        return got.strip() == td(want[4:])
    if want == "$code":
        code = env.bag.get(key_slot, "")
        return code != "" and got.strip() == code
    if want.startswith("$num:"):
        return got.strip() != "" and _num_eq(got, want[5:])
    return same(got.strip(), want, env.operator_name)


def judge(env: Env, payload: dict, checks: tuple, key_slot: str) -> tuple[str, str]:
    view = View(env.focus, "", env.today, env.operator_name)
    for path, want in checks:
        reader = _SPECIAL.get(path)
        try:
            got = reader(payload, env, key_slot) if reader else read_path(payload, path, view)
        except (InvalidOperation, ValueError, AttributeError):
            got = "?"
        if not _ok(got, want, env, key_slot):
            return "FAIL", (path + "=" + got)[:240]
    return "PASS", "ok"
