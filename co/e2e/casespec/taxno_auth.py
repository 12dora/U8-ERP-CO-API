"""销售发票字段权限拒绝路径：临时给非主管测试操作员加 AA_ColumnAuth 行，核对，删行（桥权限缓存须已关闭）。

col-auth-mask-sale-invoice-taxno：07/<数电号列> 不可见（列名取 testdata taxno.tax_invoice_no_field，未配置记 N/A）。
  按数电号搜索不靠隐藏列命中；已知发票的 tax_invoice_no 为 null 且列进 masked_fields；数电号列是表头自定义项
  cDefineN 时 defines.defineN 筛选 403（别名遮蔽由桥离线测试 CheckDefinesMask 覆盖）。
col-auth-mask-sale-invoice-amount：07/iNatSum 不可见。settled 筛选 403「无权按核销状态筛选：缺少金额字段权限」；
  无筛选搜索的 amount、open_amount、settled 均为 null。

独占。复用 col_auth 的前提核对（桥权限缓存已关闭）/插入/删除（含中断后仍发出的收尾 DELETE）。按单号找不到用于核对的发票时记 N/A，不记 PASS。
配置了数电号列而响应缺少 tax_invoice_no 键记 FAIL；键在但扫描页全空记 N/A「样本中没有数电号」；触到页数上限记 N/A「扫描上限内未找到样本」。
"""

from __future__ import annotations

import re
from collections.abc import Callable

from co.e2e.casespec import col_auth as ca
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.flip_util import Fail, with_restore
from co.e2e.testdata import DataMissing, value as testdata_value

ROUTE = "/v1/vouchers/search"
KIND = "sale_invoice"
PAGE = 50
PAGES = 20
MONEY_DENY = "无权按核销状态筛选：缺少金额字段权限"
MONEY_PLAN = ca.Plan("07", ("iNatSum",), KIND)
_COLUMN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_HEAD_DEFINE = re.compile(r"cdefine(\d{1,2})", re.IGNORECASE)
MONEY_KEYS = ("amount", "open_amount", "settled")


class Skip(Exception):
    """插入之后无法核对（找不到用于对照的发票）：记 N/A，照常删行。"""


def _rows(payload: dict) -> list:
    items = payload.get("items")
    return items if isinstance(items, list) else []


def _code_of(row: dict) -> str:
    return str(row.get("code") or "").strip()


def _tax_of(row: dict) -> str:
    return str(row.get("tax_invoice_no") or "").strip()


def _configured_field() -> str:
    try:
        return testdata_value("taxno.tax_invoice_no_field").strip()
    except DataMissing:
        return ""


def define_alias(field: str) -> str:
    """表头自定义项 cDefineN → defineN；其他列为空串（不测 defines 筛选）。"""
    got = _HEAD_DEFINE.fullmatch(field)
    return f"define{got.group(1)}" if got else ""


def define_deny(alias: str) -> str:
    return f"无权按表头自定义项 {alias} 搜索"


def tax_plan(field: str) -> ca.Plan:
    return ca.Plan("07", (field,), KIND)


def tax_mask(field: str) -> frozenset[str]:
    """masked_fields / 读取结果里算作数电号的键（大写）：API 字段名、列名、defines 别名。"""
    names = {"TAX_INVOICE_NO", field.upper()}
    alias = define_alias(field)
    if alias:
        names.add(alias.upper())
    return frozenset(names)


def _money_shown(row: dict) -> bool:
    return any(row.get(name) is not None for name in MONEY_KEYS)


def _ask(env: Env, **body: object) -> tuple[int, dict]:
    return ca.send(env, ROUTE, dict(body))


def _run_na(case: ca._Case, check: Callable[[], str], again: Callable[[], str]) -> tuple[str, str]:
    """同 col_auth._run，check 抛 Skip 时记 N/A 并删行。"""

    def body() -> tuple[str, str]:
        case.apply()
        try:
            detail = check()
        except Skip as exc:
            return "N/A", str(exc)[:240]
        problem = case.snapshot_problem()
        if problem:
            raise Fail(problem)
        return "PASS", detail

    return with_restore(body, (("删除临时字段权限行并确认字段恢复", lambda: ca._undo(case, again)),))


def _row_tax(row: object, need_code: bool) -> str:
    """hit / key / miss / skip。"""
    if not isinstance(row, dict):
        return "skip"
    if "tax_invoice_no" not in row:
        return "miss"
    text = str(row.get("tax_invoice_no") or "").strip()
    if 1 <= len(text) <= 40 and (not need_code or _code_of(row)):
        return "hit"
    return "key"


def _scan_miss(saw: bool, saw_key: bool, missing_key: bool, capped: bool) -> tuple[str, str]:
    if not saw:
        return "N/A", "本账套没有销售发票"
    if _configured_field() and missing_key:
        return "FAIL", "响应缺少 tax_invoice_no"
    if capped:
        return "N/A", "扫描上限内未找到样本"
    if saw_key:
        return "N/A", "样本中没有数电号"
    return "N/A", "本账套没有配置数电号列"


def _page_tax(got: list, need_code: bool) -> tuple[dict | None, bool, bool]:
    hit: dict | None = None
    saw_key = missing_key = False
    for row in got:
        flag = _row_tax(row, need_code)
        if flag == "miss":
            missing_key = True
        elif flag == "key":
            saw_key = True
        elif flag == "hit" and isinstance(row, dict) and hit is None:
            hit = row
    return hit, saw_key, missing_key


def _ask_page(search: Callable[..., tuple[int, dict]], env: Env, after: object) -> tuple[int, dict]:
    body: dict[str, object] = {"type": KIND, "limit": PAGE}
    if after is not None:
        body["after"] = after
    return search(env, **body)


def scan_tax_row(env: Env, search: Callable[..., tuple[int, dict]], need_code: bool = False) -> tuple[str, str, dict]:
    """翻页找带数电号的行。(状态, 说明, 行)；状态为空表示找到。"""
    after: object = None
    saw = saw_key = missing_key = False
    for page in range(PAGES):
        status, payload = _ask_page(search, env, after)
        if status != 200:
            return "FAIL", f"销售发票搜索 {status} {payload.get('code')}"[:240], {}
        got = _rows(payload)
        saw = saw or bool(got)
        hit, page_key, page_miss = _page_tax(got, need_code)
        saw_key = saw_key or page_key
        missing_key = missing_key or page_miss
        if _configured_field() and missing_key:
            return "FAIL", "响应缺少 tax_invoice_no", {}
        if hit is not None:
            return "", "", hit
        nxt = payload.get("next")
        if not nxt or not got:
            break
        if page == PAGES - 1:
            status, detail = _scan_miss(saw, saw_key, missing_key, True)
            return status, detail, {}
        after = nxt
    status, detail = _scan_miss(saw, saw_key, missing_key, False)
    return status, detail, {}


def _find_tax_row(env: Env) -> tuple[str, str, dict]:
    status, detail, row = scan_tax_row(env, _ask, True)
    if status == "N/A":
        return status, detail + "，数电号列拒绝路径未测", row
    return status, detail, row


def _find_money_row(env: Env) -> tuple[str, dict]:
    status, payload = _ask(env, type=KIND, limit=PAGE)
    if status != 200:
        raise Fail(f"销售发票搜索 {status} {payload.get('code')}"[:240])
    hits = [row for row in _rows(payload) if isinstance(row, dict)]
    if not hits:
        return "本账套没有销售发票，金额拒绝路径未测", {}
    for row in hits:
        if _money_shown(row):
            return "", row
    return "销售发票金额字段已无值（或已被遮蔽），金额拒绝路径未测", {}


def _doc_id(row: dict) -> int:
    got = row.get("id")
    if isinstance(got, int) and not isinstance(got, bool):
        return got
    return 0


def _assert_lookup(env: Env, code: str, mask: frozenset[str]) -> None:
    # search 不请求 defines：响应不含该键。defines 别名遮蔽由桥 CheckDefinesMask 覆盖。
    status, payload = _ask(env, type=KIND, code_like=code, limit=200)
    if status != 200:
        raise Fail(f"按单号搜索 {status} {payload.get('code')}"[:240])
    hits = [row for row in _rows(payload) if isinstance(row, dict) and _code_of(row) == code]
    if not hits:
        raise Skip("按单号找不到用于核对的销售发票，数电号列拒绝路径未测")
    if any(_tax_of(row) for row in hits):
        raise Fail("数电号列应拒绝时仍返回 tax_invoice_no")
    if not (ca.masked_of(payload) & mask):
        raise Fail("masked_fields 未列出 tax_invoice_no")


def _assert_no_hidden_hit(env: Env, needle: str) -> None:
    status, payload = _ask(env, type=KIND, code_like=needle, limit=200)
    if status != 200:
        raise Fail(f"按数电号搜索 {status} {payload.get('code')}"[:240])
    extra = [
        row for row in _rows(payload)
        if isinstance(row, dict) and needle.casefold() not in _code_of(row).casefold()
    ]
    if extra:
        raise Fail("数电号列应拒绝时仍按隐藏列命中了单号不含该片段的发票")


def _assert_defines_403(env: Env, alias: str) -> None:
    status, payload = _ask(env, type=KIND, defines={alias: "x"}, limit=1)
    message = str(payload.get("message") or "")
    deny = define_deny(alias)
    if status != 403 or payload.get("code") != "no_permission" or deny not in message:
        raise Fail(f"defines.{alias} 筛选应得 403「{deny}」")


def _assert_load_masked(env: Env, doc_id: int, mask: frozenset[str]) -> None:
    payload = ca.ok(env, ca.LOAD, {"type": KIND, "id": doc_id}, "读取销售发票 ")
    leaked = sorted(ca.shown(payload, mask))
    if leaked:
        raise Fail("读取销售发票仍返回被拒绝字段的值")


def _tax_check(env: Env, field: str, code: str, needle: str, doc_id: int) -> str:
    mask = tax_mask(field)
    _assert_lookup(env, code, mask)
    _assert_no_hidden_hit(env, needle)
    alias = define_alias(field)
    if alias:
        _assert_defines_403(env, alias)
    if doc_id:
        _assert_load_masked(env, doc_id, mask)
    tail = f"，defines.{alias} 筛选 403" if alias else ""
    return f"07/{field} 不可见：不按隐藏列命中，tax_invoice_no 为 null 并列进 masked_fields{tail}"


def _tax_restored(env: Env, code: str, mask: frozenset[str]) -> str:
    payload = ca.ok(env, ROUTE, {"type": KIND, "code_like": code, "limit": 200}, "按单号搜索 ")
    hits = [row for row in _rows(payload) if isinstance(row, dict) and _code_of(row) == code]
    if hits and not any(_tax_of(row) for row in hits):
        return "删除字段权限行后 tax_invoice_no 仍没有值"
    if ca.masked_of(payload) & mask:
        return "删除字段权限行后 tax_invoice_no 仍在 masked_fields"
    return ""


def taxno_mask(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    field = _configured_field()
    if not field:
        return "N/A", "testdata 未配置 taxno.tax_invoice_no_field（数电号列）"
    if not _COLUMN.fullmatch(field):
        return "N/A", "taxno.tax_invoice_no_field 不是合法列名"
    case = ca._Case(env, spec, tax_plan(field))
    reason = case.not_ready()
    if reason:
        return "N/A", reason
    status, detail, row = _find_tax_row(env)
    if status:
        return status, detail
    code, needle, doc_id = _code_of(row), _tax_of(row), _doc_id(row)

    def check() -> str:
        return _tax_check(env, field, code, needle, doc_id)

    def again() -> str:
        return _tax_restored(env, code, tax_mask(field))

    return _run_na(case, check, again)


def _money_check(env: Env) -> str:
    status, payload = _ask(env, type=KIND, settled=False, limit=PAGE)
    message = str(payload.get("message") or "")
    if status != 403 or payload.get("code") != "no_permission" or MONEY_DENY not in message:
        raise Fail("核销筛选应得 403「无权按核销状态筛选：缺少金额字段权限」")
    status, payload = _ask(env, type=KIND, limit=PAGE)
    if status != 200:
        raise Fail(f"销售发票搜索 {status} {payload.get('code')}"[:240])
    hits = [row for row in _rows(payload) if isinstance(row, dict)]
    if not hits:
        raise Skip("没有销售发票，金额拒绝路径未测")
    leaked = [name for name in MONEY_KEYS if any(row.get(name) is not None for row in hits)]
    if leaked:
        raise Fail("金额字段应拒绝时仍返回 " + "、".join(leaked))
    return "07/iNatSum 不可见：settled 筛选 403，amount/open_amount/settled 为 null"


def _money_restored(env: Env) -> str:
    payload = ca.ok(env, ROUTE, {"type": KIND, "limit": PAGE}, "销售发票搜索 ")
    hits = [row for row in _rows(payload) if isinstance(row, dict)]
    if hits and not any(_money_shown(row) for row in hits):
        return "删除字段权限行后金额字段仍没有值"
    return ""


def amount_mask(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    case = ca._Case(env, spec, MONEY_PLAN)
    reason = case.not_ready()
    if reason:
        return "N/A", reason
    miss = _find_money_row(env)[0]
    if miss:
        return "N/A", miss

    def check() -> str:
        return _money_check(env)

    def again() -> str:
        return _money_restored(env)

    return _run_na(case, check, again)


AUTH_OPS = {
    "col_auth_sale_invoice_taxno": ca._guarded(taxno_mask),
    "col_auth_sale_invoice_amount": ca._guarded(amount_mask),
}


def auth_cases() -> list[CaseSpec]:
    def case(name: str, title: str, op: str, routes: tuple[str, ...]) -> CaseSpec:
        step = Step("custom", sql_name=op, id_from="none")
        return CaseSpec(name, title, (), (step,), True, optional=True, tags=frozenset(("perm", "col-auth")),
                        routes=routes, exclusive=True)

    return [
        case("col-auth-mask-sale-invoice-taxno",
             "字段权限：07 数电号列不可见时销售发票不按隐藏列命中、tax_invoice_no 置空，表头自定义项筛选 403，结束删行",
             "col_auth_sale_invoice_taxno", (ca.SEARCH, ca.LOAD, ca.SNAP)),
        case("col-auth-mask-sale-invoice-amount",
             "字段权限：07 iNatSum 不可见时 settled 筛选 403，amount/open_amount/settled 为 null，结束删行",
             "col_auth_sale_invoice_amount", (ca.SEARCH, ca.SNAP)),
    ]
