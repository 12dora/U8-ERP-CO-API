"""只读用例：字段元数据、六张报表和账套体检、只读档案（各类基础档案、汇率、固定资产卡片、操作员、角色）、单据类型的列表（含蓝字 / 红字分列）。不改库。

步骤 kind 为 "custom"，sql_name 是操作名（见 ops_run）。报表期间取 1 月到登录日期所在月份。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from co.client.u8co_client import U8CoError
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.doc_list import ids_of, items_of
from co.e2e.casespec.readiness import READY_OPS, expect_ready, readiness_cases
from co.e2e.casespec.operator_probe import ua_probe_step
from co.e2e.casespec.steps import auth_fields, invoke

LIMIT = 5
NEW_KINDS = ("sale_return", "purchase_return", "purchase_requisition", "dispatch", "arrival", "stock_opening")
# 同表蓝字 / 红字单据：列表只列各自的一种（red 取表上的红字标志）。
_RED_LIST = {"dispatch": False, "arrival": False, "sale_return": True, "purchase_return": True}
# 其余十类只读档案也在这里跑同一套「列 5 行再读一个」；两列主键的编码是 <第一段>:<第二段>。
RO6_ARCHIVES = (
    "position", "rd_style", "purchase_type", "sale_type", "district_class", "trade_class", "aa_bank",
    "customer_address", "user_define", "customer_inventory", "pay_agreement", "customer_invoice_company",
)
PAIR_ARCHIVES = ("customer_address", "user_define", "customer_inventory", "customer_invoice_company")
# 汇率：列登录年度的前 5 项再读第一项，按第一项的币种、年度（currency、fiscal_year）再列一次，
# 另按日期（<币种>:<该月 15 日>）读一次，固定汇率时应命中同一项。
EXCH = "exchange_rate"
# 固定资产卡片：缺省列表只有在役卡片；读第一张，核对金额、使用部门和口径（as_of、fiscal_year）；
# 再按第一张的类别（type_code，含下级）列一次，每一项的类别都以它开头。
FA = "fa_card"
_FA_KEYS = ("asset_num", "type_code", "original_value", "accumulated_depreciation", "net_value", "disposed", "depts")
# U8 操作员、角色：只有账套主管能读（测试操作员不是账套主管时 403，记 N/A）；读回的对象不能带口令类键，
# 操作员有 disabled、roles、person_code，角色有 members。
UA = ("operator", "role")
_UA_KEYS = {"operator": ("name", "disabled", "roles", "person_code"), "role": ("name", "members")}
_UA_LISTS = {"operator": "roles", "role": "members"}
RO_ARCHIVES = (
    "account", "unit", "unit_group", "settle_style", "voucher_sign", "currency", "bank", "project",
    *RO6_ARCHIVES,
    EXCH,
    FA,
    *UA,
)
_DIGITS = re.compile(r"^[0-9]{1,19}\Z")
_CLASS = re.compile(r"^[0-9A-Za-z]{1,2}\Z")
# 报表 → 响应必有的键、每一行必有的键。
_SHAPE = {
    "close_status": (("fiscal_year", "modules", "periods"), ("period", "closed"), "periods"),
    "gl_balance": (("fiscal_year", "period_from", "period_to", "items", "next"), ("code", "grade", "close_dir"), "items"),
    "gl_aux_balance": (("fiscal_year", "dim", "items", "next"), ("code", "dim_code", "close_dir"), "items"),
    "arap_balance": (("side", "as_of", "items", "next"), ("partner", "balance"), "items"),
    "arap_aging": (
        ("side", "as_of", "basis", "buckets", "items", "next"),
        ("partner", "aging", "prepaid", "max_overdue_days", "oldest_overdue_date"),
        "items",
    ),
    "bom": (("parent", "as_of", "levels", "bom_id", "version", "items", "truncated"), ("level", "component", "qty"), "items"),
}


def _ask(env: Env, route: str, extra: dict) -> dict:
    fields = auth_fields(env)
    fields.update(extra)
    status, payload = invoke(env.client, route, fields)
    if status != 200:
        raise ValueError(f"{route} {status} {payload.get('code')} {payload.get('message')}"[:200])
    return payload


def _names(rows: object) -> set[str]:
    if not isinstance(rows, list):
        return set()
    return {str(row.get("name")) for row in rows if isinstance(row, dict)}


def meta(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    # 签名但不带账套和口令；桥不登录 U8。
    try:
        payload = env.client.meta()
    except U8CoError as exc:
        return "FAIL", f"{exc.status} {exc.code} {exc.message}"[:200]
    revision = payload.get("revision")
    if payload.get("ok") is not True or not isinstance(revision, str) or revision == "":
        return "FAIL", "meta 缺 ok 或 revision"
    if not isinstance(payload.get("features"), dict):
        return "FAIL", "meta 缺 features"
    lost = [kind for kind in NEW_KINDS if kind not in _names(payload.get("kinds"))]
    lost += [name for name in RO_ARCHIVES if name not in _names(payload.get("archives"))]
    if lost:
        return "FAIL", "meta 缺 " + ",".join(lost)
    return "PASS", revision[:16]


def report_body(env: Env, name: str) -> dict:
    month = int(env.today[5:7])
    bodies = {
        "close_status": {},
        "gl_balance": {"period_from": 1, "period_to": month, "grade_to": 1, "nonzero": True},
        "gl_aux_balance": {"dim": "customer", "period_from": 1, "period_to": month, "limit": LIMIT},
        "arap_balance": {"side": "ar", "limit": LIMIT},
        "arap_aging": {"side": "ap", "basis": "due", "limit": LIMIT},
    }
    if name == "bom":
        return {"parent": env.bag.get("bom_parent", ""), "levels": 2}
    return bodies[name]


def _row_gap(rows: object, keys: tuple[str, ...]) -> str:
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return "行不是对象列表"
    for row in rows:
        lost = [key for key in keys if key not in row]
        if lost:
            return "行缺 " + ",".join(lost)
    return ""


_GL_EMPTY = "科目余额表没有行"


def _gl_rules(payload: dict, rows: list) -> str:
    if not rows:
        return _GL_EMPTY
    if any(row.get("grade") != 1 for row in rows):
        return "grade_to=1 仍有下级科目"
    return ""


def _pos(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _date_filled(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _days_match_as_of(days: object, oldest: object, as_of: object) -> str:
    raw = str(as_of or "").strip()
    if not raw:
        return ""
    try:
        start = date.fromisoformat(str(oldest).strip()[:10])
    except (TypeError, ValueError):
        return "oldest_overdue_date 不是 yyyy-MM-dd"
    try:
        report = date.fromisoformat(raw[:10])
    except (TypeError, ValueError):
        return ""
    if start + timedelta(days=int(days)) != report:
        return "oldest_overdue_date 加 max_overdue_days 与报表日期不一致"
    return ""


def _overdue_extrema_problem(days: object, oldest: object, as_of: object) -> str:
    if days is None:
        return "" if not _date_filled(oldest) else "oldest_overdue_date 有值时 max_overdue_days 不能空"
    if not _pos(days):
        return "max_overdue_days 不是正整数"
    if not _date_filled(oldest):
        return "有 max_overdue_days 时 oldest_overdue_date 不能空"
    return _days_match_as_of(days, oldest, as_of)


def _overdue_keys_problem(row: dict, as_of: object) -> str:
    if "max_overdue_days" not in row or "oldest_overdue_date" not in row:
        return "缺 max_overdue_days 或 oldest_overdue_date"
    return _overdue_extrema_problem(row.get("max_overdue_days"), row.get("oldest_overdue_date"), as_of)


def _aging_rules(payload: dict, rows: list) -> str:
    buckets = payload.get("buckets")
    if payload.get("basis") != "due" or not isinstance(buckets, list):
        return "账龄 basis 或 buckets 不对"
    if any(not isinstance(row.get("aging"), list) or len(row["aging"]) != len(buckets) for row in rows):
        return "账龄列数与 buckets 不一致"
    as_of = payload.get("as_of")
    for row in rows:
        problem = _overdue_keys_problem(row, as_of)
        if problem:
            return problem
    return ""


def _bom_rules(payload: dict, rows: list) -> str:
    if not rows or any(row.get("level") not in (1, 2) for row in rows):
        return "物料清单没有行或层级超过 2"
    if not isinstance(payload.get("truncated"), bool):
        return "truncated 不是布尔"
    return ""


def _limit_rules(payload: dict, rows: list) -> str:
    return "" if len(rows) <= LIMIT else "行数超过 limit"


_RULES = {
    "gl_balance": _gl_rules,
    "gl_aux_balance": _limit_rules,
    "arap_balance": _limit_rules,
    "arap_aging": _aging_rules,
    "bom": _bom_rules,
}


def report_problem(name: str, payload: dict, body: dict) -> str:
    top, row_keys, list_key = _SHAPE[name]
    lost = [key for key in top if key not in payload]
    if payload.get("ok") is not True or lost:
        return "响应缺 " + ",".join(lost or ["ok"])
    for key in ("dim", "side", "parent"):
        if key in body and payload.get(key) != body[key]:
            return key + " 回显不一致"
    gap = _row_gap(payload.get(list_key), row_keys)
    if gap:
        return gap
    rule = _RULES.get(name)
    return rule(payload, payload[list_key]) if rule else ""


def report(env: Env, _spec: CaseSpec, step: Step) -> tuple[str, str]:
    name = dict(step.fields)["name"]
    body = report_body(env, name)
    payload = _ask(env, "/v1/reports/" + name, body)
    problem = report_problem(name, payload, body)
    if problem == _GL_EMPTY and not expect_ready():
        # 新建的测试账套本年可能既没有期初余额也没有已记账凭证；期望就绪的通道（readiness.expect_ready）上为空仍算失败。
        return "N/A", "测试账套本年没有余额和已记账发生额（科目余额表为空）"
    if problem:
        return "FAIL", name + " " + problem
    rows = payload[_SHAPE[name][2]]
    return "PASS", f"{name} {len(rows)} 行"


def _pick_code(env: Env, archive: str, rows: list[dict]) -> str:
    if archive == "currency":
        return env.bag.get("currency_name", "")
    if not rows:
        return ""
    return str(rows[0].get("code") or "")


def _get_problem(archive: str, code: str, got: dict) -> str:
    if got.get("archive") != archive or str(got.get("code")) != code:
        return "读回的编码不对"
    if not isinstance(got.get("fields"), dict) or not got["fields"]:
        return "fields 为空"
    if archive == "project":
        klass, sep, item = code.partition(":")
        if sep != ":" or _CLASS.fullmatch(klass) is None or item == "" or got.get("project_class") != klass:
            return "项目编码不是 <大类>:<编码>"
    if archive == "account" and "fiscal_year" not in got:
        return "科目缺 fiscal_year"
    if archive in PAIR_ARCHIVES and ":" not in code[1:-1]:
        return "编码不是 <第一段>:<第二段>"
    return ""


def _exch_filter_problem(env: Env, first: dict) -> str:
    """按第一项的币种、年度再列一次（currency、fiscal_year）：回显年度，每一项都是这个币种和年度。"""
    currency, year = first.get("currency"), first.get("year")
    listed = _ask(env, "/v1/archives/list", {"archive": EXCH, "currency": currency, "fiscal_year": year, "limit": LIMIT})
    rows = items_of(listed)
    if listed.get("fiscal_year") != year or not rows:
        return "按币种、年度过滤的列表回显或行数不对"
    if any((r.get("currency"), r.get("year")) != (currency, year) for r in rows):
        return "按币种、年度过滤的列表混入其他币种或年度"
    return ""


def _exch_problem(env: Env, rows: list[dict]) -> str:
    """先按币种、年度过滤列一次；再按日期读取：固定汇率的项取该月 15 日，读回的 code、rate 应与列表这一项相同。
    没有固定汇率的项就不做按日期读取。"""
    problem = _exch_filter_problem(env, rows[0])
    if problem:
        return problem
    row = next((r for r in rows if r.get("mode") == "fixed" and r.get("rate") is not None), None)
    if row is None:
        return ""
    code = f"{row.get('currency')}:{row.get('year')}-{int(row.get('period') or 0):02d}-15"
    got = _ask(env, "/v1/archives/get", {"archive": EXCH, "code": code})
    fields = got.get("fields") if isinstance(got.get("fields"), dict) else {}
    if (fields.get("code"), fields.get("rate")) != (row.get("code"), row.get("rate")):
        return "按日期读取的汇率与该月记账汇率不一致"
    if got.get("rate_mode") not in ("fixed", "floating"):
        return "按日期读取缺 rate_mode"
    return ""


def _fa_problem(env: Env, listed: dict, rows: list[dict], got: dict) -> str:
    """缺省列表不含已减少的卡片；读回的卡片带金额和部门；按类别过滤后每一项都在该类别下。"""
    if not listed.get("as_of") or not isinstance(listed.get("fiscal_year"), int) or listed.get("watermark") is not None:
        return "卡片列表缺 as_of、fiscal_year，或 watermark 不是 null"
    if any(row.get("disposed") is not False for row in rows):
        return "缺省列表混入已减少的卡片"
    fields = got.get("fields") if isinstance(got.get("fields"), dict) else {}
    lost = [key for key in _FA_KEYS if key not in fields]
    if lost or not isinstance(fields.get("depts"), list):
        return "卡片缺 " + ",".join(lost or ["depts"])
    type_code = str(fields.get("type_code") or "")
    if type_code == "":
        return ""
    typed = items_of(_ask(env, "/v1/archives/list", {"archive": FA, "type_code": type_code, "limit": LIMIT}))
    if not typed or any(not str(row.get("type_code") or "").startswith(type_code) for row in typed):
        return "按类别过滤的列表为空或混入其他类别"
    return ""


def _ua_problem(archive: str, listed: dict, got: dict) -> str:
    """操作员、角色：没有 watermark；fields 有约定的键，成员列表是列表，没有口令类键。"""
    if listed.get("watermark") is not None:
        return "列表的 watermark 不是 null"
    fields = got.get("fields") if isinstance(got.get("fields"), dict) else {}
    lost = [key for key in _UA_KEYS[archive] if key not in fields]
    if lost or not isinstance(fields.get(_UA_LISTS[archive]), list):
        return "缺 " + ",".join(lost or [_UA_LISTS[archive]])
    if any("password" in key.lower() or "pwd" in key.lower() for key in fields):
        return "返回了口令类字段"
    if archive == "operator" and not isinstance(fields.get("disabled"), bool):
        return "disabled 不是布尔"
    return ""


def _ua_denied(env: Env, archive: str) -> bool:
    """操作员、角色只认账套主管：测试操作员没有权限（403）时返回 True。"""
    if archive not in UA:
        return False
    fields = auth_fields(env)
    fields.update({"archive": archive, "limit": 1})
    status, _payload = invoke(env.client, "/v1/archives/list", fields)
    return status == 403


def archive_read(env: Env, _spec: CaseSpec, step: Step) -> tuple[str, str]:
    archive = dict(step.fields)["archive"]
    if _ua_denied(env, archive):
        return "N/A", "测试操作员不是账套主管，不能读操作员、角色"
    listed = _ask(env, "/v1/archives/list", {"archive": archive, "limit": LIMIT})
    rows = items_of(listed)
    if listed.get("archive") != archive or len(rows) > LIMIT:
        return "FAIL", "列表回显或行数不对"
    if any(str(row.get("code") or "") == "" for row in rows):
        return "FAIL", "列表行缺 code"
    code = _pick_code(env, archive, rows)
    if code == "":
        # 复制来的账套可能根本没有这类档案（例如开户银行、项目）：记 N/A，不算失败。
        return "N/A", "本账套没有该档案"
    got = _ask(env, "/v1/archives/get", {"archive": archive, "code": code})
    problem = _get_problem(archive, code, got)
    if not problem and archive == EXCH:
        problem = _exch_problem(env, rows)
    if not problem and archive == FA:
        problem = _fa_problem(env, listed, rows, got)
    if not problem and archive in UA:
        problem = _ua_problem(archive, listed, got)
    if problem:
        return "FAIL", problem
    return "PASS", f"{len(rows)} 行，读取 {code}"


def voucher_list(env: Env, _spec: CaseSpec, step: Step) -> tuple[str, str]:
    kind = dict(step.fields)["type"]
    payload = _ask(env, "/v1/vouchers/list", {"type": kind, "limit": LIMIT})
    rows = items_of(payload)
    ids_of(rows)
    if payload.get("type") != kind or len(rows) > LIMIT:
        return "FAIL", "列表回显或行数不对"
    if _DIGITS.fullmatch(str(payload.get("watermark") or "")) is None:
        return "FAIL", "watermark 不是数字串"
    if kind in _RED_LIST and any(bool(row.get("red")) != _RED_LIST[kind] for row in rows):
        return "FAIL", "列表混入了另一种（红字或蓝字）单据"
    return "PASS", f"{len(rows)} 行"


READ_OPS = {"meta": meta, "report": report, "archive_read": archive_read, "voucher_list": voucher_list}
# 账套体检（readiness）。
READ_OPS.update(READY_OPS)


def _custom(op: str, fields: tuple = ()) -> Step:
    return Step("custom", fields=fields, sql_name=op, id_from="none")


_REPORT_TITLES = (
    ("close_status", "月结状态"),
    ("gl_balance", "科目余额表（一级科目、非零）"),
    ("gl_aux_balance", "客户辅助核算余额表"),
    ("arap_balance", "应收往来余额"),
    ("arap_aging", "应付账龄（按到期日）"),
    ("bom", "物料清单两层展开"),
)


def read_cases() -> list[CaseSpec]:
    rows = [CaseSpec("meta-fields", "字段元数据含新单据类型和只读档案", (), (_custom("meta"),), False, routes=("/v1/meta",))]
    for name, title in _REPORT_TITLES:
        needs = ("bom_parent",) if name == "bom" else ()
        step = _custom("report", (("name", name),))
        routes = ("/v1/reports/" + name,)
        rows.append(CaseSpec("report-" + name.replace("_", "-"), "报表：" + title, needs, (step,), True, routes=routes))
    rows += readiness_cases()  # 账套体检
    for archive in RO_ARCHIVES:
        needs = ("currency_name",) if archive == "currency" else ()
        steps = (_custom("archive_read", (("archive", archive),)),)
        if archive == "operator":
            # 附加核对（operator_probe）：state 与 disabled 一致、人员关联与账套库一致、登录操作员 state 0。
            steps += (ua_probe_step(),)
        title = "只读档案列表和读取：" + archive
        routes = ("/v1/archives/list", "/v1/archives/get")
        rows.append(CaseSpec("archive-read-" + archive.replace("_", "-"), title, needs, steps, True, routes=routes))
    for kind in NEW_KINDS:
        step = _custom("voucher_list", (("type", kind),))
        routes = ("/v1/vouchers/list",)
        rows.append(CaseSpec("list-" + kind.replace("_", "-"), "新单据类型列表：" + kind, (), (step,), True, routes=routes))
    return rows
