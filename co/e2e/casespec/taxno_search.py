"""销售发票数电号搜索、核销状态筛选。只读，不改账套。

vouchers-search-taxno：分页（每页 50、最多 20 页）找一条带 tax_invoice_no 的销售发票，再按该号搜索，
  命中行的 tax_invoice_no 等于它。未配置数电号列、没有销售发票、样本中没有数电号、扫描上限内未找到样本时记 N/A。
  已配置数电号列而响应缺少 tax_invoice_no 键记 FAIL。
vouchers-search-settled-type：sale_order 带 settled 400 field=settled（不依赖账套有没有发票）。
vouchers-search-settled：settled=true/false 只返回对应核销状态的行；没有销售发票记 N/A。
字段权限拒绝路径见 taxno_auth（独占，临时插 AA_ColumnAuth）。
testdata 可选 taxno.tax_invoice_no_field（本账套配置列，如 cDefine10）。
不断言真实单号、数电号或金额本身。步骤 kind 为 "custom"，sql_name 是操作名（TAXNO_OPS 并入 ops_run）。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from co.e2e.casespec.assertutil import status_problem
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.taxno_auth import AUTH_OPS, auth_cases, scan_tax_row
from co.e2e.casespec.dry_ops import send

ROUTE = "/v1/vouchers/search"
KIND = "sale_invoice"
PAGE = 50
PAGES = 20
SETTLED_DENY = "该单据类型不支持按 settled 搜索"
MONEY_DENY = "无权按核销状态筛选：缺少金额字段权限"


def _search(env: Env, **body: object) -> tuple[int, dict]:
    return send(env, ROUTE, dict(body))


def _rows(payload: dict) -> list:
    items = payload.get("items")
    return items if isinstance(items, list) else []


def taxno_search(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    status, detail, row = scan_tax_row(env, _search)
    if status:
        return status, detail
    needle = str(row.get("tax_invoice_no") or "").strip()
    status, payload = _search(env, type=KIND, code_like=needle, limit=200)
    if status != 200:
        return "FAIL", f"按数电号搜索 {status} {payload.get('code')}"[:240]
    hits = _rows(payload)
    if not hits:
        return "FAIL", "按该行的数电号搜索没有命中"
    if any(not isinstance(item, dict) or str(item.get("tax_invoice_no") or "") != needle for item in hits):
        return "FAIL", "命中行的 tax_invoice_no 与筛选值不同"
    return "PASS", f"{len(hits)} 行"


def _amount_problem(amount: object, want_settled: bool) -> str:
    if amount is None:
        return ""
    try:
        value = Decimal(str(amount))
    except InvalidOperation:
        return "open_amount 不是数"
    if want_settled:
        return "" if value == 0 else "已核销行的 open_amount 不是 0"
    return "" if value != 0 else "未核销行的 open_amount 为 0"


def _open_problem(rows: list, want_settled: bool) -> str:
    for row in rows:
        if not isinstance(row, dict):
            return "行不是对象"
        if row.get("settled") is not want_settled:
            return "settled 筛选结果里有相反状态的行"
        problem = _amount_problem(row.get("open_amount"), want_settled)
        if problem:
            return problem
    return ""


def settled_type(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    status, payload = _search(env, type="sale_order", settled=False, limit=1)
    bad = status_problem(status, payload, 400, "bad_request")
    message = str(payload.get("message") or "")
    if bad or payload.get("field") != "settled" or SETTLED_DENY not in message:
        return "FAIL", f"sale_order settled 得 {bad or payload.get('field')!r}，期望 400 field=settled"[:240]
    return "PASS", "sale_order settled 400"


def _settled_page(env: Env, want: bool) -> tuple[str, str]:
    flag = "true" if want else "false"
    status, payload = _search(env, type=KIND, settled=want, limit=PAGE)
    if status == 403:
        if MONEY_DENY not in str(payload.get("message") or ""):
            return "FAIL", "核销筛选 403 的消息不是无权按核销状态筛选"
        return "N/A", "测试操作员无权按核销状态筛选"
    if status != 200:
        return "FAIL", f"settled={flag} {status} {payload.get('code')}"[:240]
    problem = _open_problem(_rows(payload), want)
    return ("FAIL", problem) if problem else ("", "")


def settled_filter(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    status, payload = _search(env, type=KIND, limit=PAGE)
    if status == 403:
        return "N/A", "测试操作员无权按核销状态筛选"
    if status != 200:
        return "FAIL", f"销售发票搜索 {status} {payload.get('code')}"[:240]
    if not _rows(payload):
        return "N/A", "本账套没有销售发票"
    for want in (False, True):
        status, detail = _settled_page(env, want)
        if status:
            return status, detail
    return "PASS", "未核销与已核销筛选"


_SEARCH_OPS = {
    "taxno_search_op": taxno_search,
    "settled_type": settled_type,
    "settled_filter": settled_filter,
}


def taxno_search_cases() -> list[CaseSpec]:
    def case(name: str, title: str, op: str, extra: frozenset[str] = frozenset()) -> CaseSpec:
        tags = frozenset(("list:" + KIND,)) | extra
        step = Step("custom", ROUTE, sql_name=op, id_from="none")
        return CaseSpec(name, title, (), (step,), True, optional=True, tags=tags, routes=(ROUTE,))

    return [
        case("vouchers-search-taxno", "销售发票分页找一条带数电号的发票再按该号搜索，命中行 tax_invoice_no 等于它",
             "taxno_search_op"),
        case("vouchers-search-settled-type", "销售订单带 settled 400 field=settled",
             "settled_type", frozenset(("list:sale_order",))),
        case("vouchers-search-settled", "settled 筛选只返回对应核销状态的行；没有销售发票记 N/A",
             "settled_filter"),
    ]


TAXNO_OPS = {**_SEARCH_OPS, **AUTH_OPS}


def taxno_cases() -> list[CaseSpec]:
    return taxno_search_cases() + auth_cases()
