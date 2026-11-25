"""明细账报表只读用例：往来明细账（arap_detail）和科目明细账（gl_detail）。不改库。

步骤 kind 为 "custom"，sql_name 是操作名（DETAIL_OPS 并入 ops_run）。
往来明细账：用往来余额挑一个有余额的客户，从当年 1 月 1 日查到登录日期，期末应等于往来余额的 balance；
科目明细账：用科目余额表（1 月到登录月份、一级、非零）挑一个本期有发生的科目，期初、合计、期末应与余额表一致。
有下一页时再读一页，核对滚动余额在两页之间连续。挑不到记 N/A。
"""

from __future__ import annotations

from decimal import Decimal

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.readonly import _ask

LIMIT = 5
_CENT = Decimal("0.01")


def _dec(value: object) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _differs(left: object, right: object) -> bool:
    return abs(_dec(left) - _dec(right)) > _CENT


def _signed(row: dict, prefix: str = "") -> Decimal:
    """方向拆分的余额还原成借正贷负：总账行是 dir + balance，汇总是 <prefix>_debit / <prefix>_credit。"""
    if prefix:
        return _dec(row.get(prefix + "_debit")) - _dec(row.get(prefix + "_credit"))
    sign = {"借": 1, "贷": -1}.get(str(row.get("dir")), 0)
    return _dec(row.get("balance")) * sign


def _items(payload: dict) -> list[dict]:
    rows = payload.get("items")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("items 不是对象列表")
    return rows


def _continuity(first: list[dict], second: list[dict], gl: bool) -> str:
    """第二页第一行的余额减去本行发生额，应等于第一页最后一行的余额（同一往来单位时）。"""
    if not first or not second:
        return ""
    last, head = first[-1], second[0]
    if not gl and last.get("partner") != head.get("partner"):
        return ""
    if gl:
        before = _signed(head) - _dec(head.get("debit")) + _dec(head.get("credit"))
        return "" if abs(before - _signed(last)) <= _CENT else "翻页后滚动余额不连续"
    # 用例只查应收：余额 = 上一行 + 借 − 贷。
    before = _dec(head.get("balance")) - _dec(head.get("debit")) + _dec(head.get("credit"))
    return "" if abs(before - _dec(last.get("balance"))) <= _CENT else "翻页后滚动余额不连续"


def _next_page(env: Env, route: str, body: dict, payload: dict, gl: bool) -> str:
    token = payload.get("next")
    if not token:
        return ""
    more = _ask(env, route, dict(body, after=token))
    return _continuity(_items(payload), _items(more), gl)


def _arap_problem(payload: dict, partner: str, balance: object) -> str:
    rows = _items(payload)
    if payload.get("side") != "ar" or len(rows) > LIMIT:
        return "side 回显或行数不对"
    summary = [row for row in payload.get("partners") or [] if isinstance(row, dict) and row.get("partner") == partner]
    if len(summary) != 1:
        return "partners 里没有挑中的往来单位"
    head = summary[0]
    if _differs(head.get("closing"), balance):
        return f"期末 {head.get('closing')} 与往来余额 {balance} 不一致"
    moved = _dec(head.get("debit")) - _dec(head.get("credit"))
    if _differs(_dec(head.get("opening")) + moved, head.get("closing")):
        return "期初 + 借 − 贷 不等于期末"
    if rows and not payload.get("next") and _differs(rows[-1].get("balance"), head.get("closing")):
        return "最后一行的滚动余额不等于期末"
    return ""


def arap_detail(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    picked = _items(_ask(env, "/v1/reports/arap_balance", {"side": "ar", "limit": 1}))
    if not picked:
        return "N/A", "本账套没有应收余额"
    partner, balance = str(picked[0].get("partner") or ""), picked[0].get("balance")
    body = {"side": "ar", "partner": partner, "date_from": env.today[:4] + "-01-01", "limit": LIMIT}
    route = "/v1/reports/arap_detail"
    payload = _ask(env, route, body)
    problem = _arap_problem(payload, partner, balance) or _next_page(env, route, body, payload, False)
    if problem:
        return "FAIL", problem
    return "PASS", f"{partner} {len(_items(payload))} 行"


def _gl_problem(payload: dict, account: dict) -> str:
    if payload.get("code") != account.get("code") or len(_items(payload)) > LIMIT:
        return "code 回显或行数不对"
    if _differs(_signed(payload, "open"), _signed(account, "open")):
        return "期初与科目余额表不一致"
    if _differs(payload.get("total_debit"), account.get("period_debit")):
        return "借方合计与科目余额表不一致"
    if _differs(payload.get("total_credit"), account.get("period_credit")):
        return "贷方合计与科目余额表不一致"
    if _differs(_signed(payload, "close"), _signed(account, "close")):
        return "期末与科目余额表不一致"
    return ""


def gl_detail(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    month = int(env.today[5:7])
    balance = {"period_from": 1, "period_to": month, "grade_to": 1, "nonzero": True, "limit": 50}
    rows = _items(_ask(env, "/v1/reports/gl_balance", balance))
    active = [row for row in rows if _dec(row.get("period_debit")) + _dec(row.get("period_credit")) > 0]
    if not active:
        return "N/A", "测试账套本年没有已记账的发生额"
    account = active[0]
    body = {"code": account.get("code"), "period_from": 1, "period_to": month, "limit": LIMIT}
    route = "/v1/reports/gl_detail"
    payload = _ask(env, route, body)
    problem = _gl_problem(payload, account) or _next_page(env, route, body, payload, True)
    if problem:
        return "FAIL", problem
    return "PASS", f"{account.get('code')} {len(_items(payload))} 行"


DETAIL_OPS = {"report_arap_detail": arap_detail, "report_gl_detail": gl_detail}


def detail_cases() -> list[CaseSpec]:
    return [
        CaseSpec(
            "report-arap-detail",
            "报表：应收明细账（期末与往来余额一致、翻页连续）",
            (),
            (Step("custom", sql_name="report_arap_detail", id_from="none"),),
            True,
            routes=("/v1/reports/arap_balance", "/v1/reports/arap_detail"),
        ),
        CaseSpec(
            "report-gl-detail",
            "报表：科目明细账（期初、合计、期末与科目余额表一致、翻页连续）",
            (),
            (Step("custom", sql_name="report_gl_detail", id_from="none"),),
            True,
            routes=("/v1/reports/gl_balance", "/v1/reports/gl_detail"),
        ),
    ]
