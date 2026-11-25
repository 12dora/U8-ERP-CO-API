"""出入库调整单（ia_adjust）、存货调价单（inventory_price_adjust）与固定资产报表（fa_changes、fa_depreciation）的只读核对。

ia-adjust-read：取一页列表 → 行有 vouch_type、line_count，verified 与 posted_lines = line_count 一致 → keys_only 主键与完整列表相同
  → 读取首行：单号一致、有表体、state.posted 与 state.verified 相同 → vouchers/search 按单号找回首行。没有调整单记 N/A。
list-inv-price-adjust：列表（账套里可能是空表，SQL 照样要能跑通）；有单据时读取首行、单号一致。空表也记 PASS。
report-fa-changes：变动单报表首页 → 有数据时每行的变动日期都在该年度，按首行的卡片过滤，每行都是这张卡片且含首行；
  按单号过滤恰好一行。
report-fa-depreciation：折旧报表首页 → 每行的期间都在 posted_periods 里 → 取首行卡片的 fa_card（archives/get），fa_card 的
  fiscal_year 与本次年度相同时，在它的 depr_period 上读这张卡片的折旧：卡片未减少、累计折旧不为 0 时与 fa_card 的
  accumulated_depreciation 相等。
两张固定资产报表各跑两次：缺省（登录日期的年份）和 fiscal_year = 登录年份 - 1。fiscal_year 是固定资产业务年度，
不是账套库年份（一个账套库里常有多个业务年度的数据），所以不能只看登录年度；两个年度都没有数据才记 N/A。

全部只读，不改账套。步骤 kind 为 "custom"，sql_name 是操作名（IA_SA_FA_OPS 并入 ops_run）。
"""

from __future__ import annotations

from decimal import Decimal

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.dry_ops import send

IA = "ia_adjust"
PRICE = "inventory_price_adjust"
_LIST = "/v1/vouchers/list"
_LOAD = "/v1/vouchers/load"
_SEARCH = "/v1/vouchers/search"
_CHANGES = "/v1/reports/fa_changes"
_DEPR = "/v1/reports/fa_depreciation"
_ARC = "/v1/archives/get"
_PAGE = 20


def _ok(env: Env, route: str, body: dict) -> dict:
    status, payload = send(env, route, body)
    if status != 200:
        raise ValueError(f"{route} {status} {payload.get('code')} {payload.get('message')}"[:200])
    return payload


def _items(payload: dict) -> list[dict]:
    rows = payload.get("items")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("响应缺 items")
    return rows


def _num(value: object) -> int:
    text = str(value if value is not None else "").strip()
    return int(text) if text.lstrip("-").isdigit() else -1


def _ia_row_problem(row: dict) -> str:
    if str(row.get("vouch_type") or "").strip() == "":
        return f"出入库调整单 {row.get('code')} 没有 vouch_type"
    lines, posted = _num(row.get("line_count")), _num(row.get("posted_lines"))
    if lines < 0 or posted < 0:
        return f"出入库调整单 {row.get('code')} 的 line_count / posted_lines 不是整数"
    if bool(row.get("verified")) != (lines > 0 and posted == lines):
        return f"出入库调整单 {row.get('code')} 的 verified 与已记账行数不一致"
    return ""


def _ia_load_problem(env: Env, row: dict) -> str:
    payload = _ok(env, _LOAD, {"type": IA, "id": row["id"]})
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    lines = payload.get("lines")
    if payload.get("code") != row.get("code"):
        return f"出入库调整单 {row['id']} 读取的单号 {payload.get('code')} 与列表 {row.get('code')} 不同"
    if not isinstance(lines, list) or not lines:
        return f"出入库调整单 {row.get('code')} 读取没有表体"
    if state.get("posted") is not state.get("verified") or not isinstance(state.get("posted"), bool):
        return f"出入库调整单 {row.get('code')} 的 state.posted / verified 不一致"
    return ""


def _ia_keys_problem(env: Env, rows: list[dict]) -> str:
    keys = _items(_ok(env, _LIST, {"type": IA, "limit": _PAGE, "keys_only": True}))
    if [row.get("id") for row in keys] != [row.get("id") for row in rows]:
        return "出入库调整单 keys_only 的主键与完整列表不同"
    return ""


def _ia_search_problem(env: Env, row: dict) -> str:
    code = str(row.get("code") or "").strip()
    found = _items(_ok(env, _SEARCH, {"type": IA, "code_like": code, "limit": _PAGE}))
    if row.get("id") not in [item.get("id") for item in found]:
        return f"出入库调整单按单号 {code} 搜索没有找回 {row.get('id')}"
    return ""


def ia_adjust_read(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    rows = _items(_ok(env, _LIST, {"type": IA, "limit": _PAGE}))
    if not rows:
        return "N/A", "账套没有出入库调整单"
    problem = next((text for text in map(_ia_row_problem, rows) if text), "")
    problem = problem or _ia_keys_problem(env, rows) or _ia_load_problem(env, rows[0]) or _ia_search_problem(env, rows[0])
    if problem:
        return "FAIL", problem
    types = sorted({str(row.get("vouch_type")).strip() for row in rows})
    return "PASS", f"首页 {len(rows)} 张（类型 {','.join(types)}）：列表、keys_only、读取、按单号搜索一致"


def price_adjust_read(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    rows = _items(_ok(env, _LIST, {"type": PRICE, "limit": _PAGE}))
    if not rows:
        return "PASS", "存货调价单列表为空（账套里没有调价单），列表 SQL 能跑通"
    payload = _ok(env, _LOAD, {"type": PRICE, "id": rows[0].get("id")})
    if payload.get("code") != rows[0].get("code"):
        return "FAIL", f"存货调价单 {rows[0].get('id')} 读取的单号与列表不同"
    return "PASS", f"存货调价单首页 {len(rows)} 张，首张读取一致"


def _fa_years(env: Env) -> tuple[tuple[int, dict], ...]:
    # 年度 → 额外参数：登录年度走缺省（不传 fiscal_year），上一年度显式传。
    year = int(str(env.today)[:4])
    return (year, {}), (year - 1, {"fiscal_year": year - 1})


def _changes_problem(env: Env, first: dict, extra: dict) -> str:
    card, code = first.get("card_code"), first.get("code")
    by_card = _items(_ok(env, _CHANGES, {"card": card, "limit": 1000, **extra}))
    if not by_card or any(row.get("card_code") != card for row in by_card) or code not in [r.get("code") for r in by_card]:
        return f"按卡片 {card} 过滤的变动单不对"
    by_code = _items(_ok(env, _CHANGES, {"code": code, **extra}))
    if [row.get("code") for row in by_code] != [code]:
        return f"按单号 {code} 过滤应恰好一行，实际 {len(by_code)} 行"
    if not isinstance(by_code[0].get("depts"), list):
        return f"变动单 {code} 没有 depts"
    return ""


def _year_problem(rows: list[dict], year: int) -> str:
    for row in rows:
        if not str(row.get("change_date") or "").startswith(f"{year}-"):
            return f"变动单 {row.get('code')} 的日期 {row.get('change_date')} 不在 {year} 年"
    return ""


def fa_changes_read(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    seen = []
    for year, extra in _fa_years(env):
        rows = _items(_ok(env, _CHANGES, {"limit": _PAGE, **extra}))
        if not rows:
            continue
        problem = _year_problem(rows, year) or _changes_problem(env, rows[0], extra)
        if problem:
            return "FAIL", f"{year} 年：{problem}"
        seen.append(f"{year} 年首页 {len(rows)} 张")
    if not seen:
        return "N/A", "登录年度和上一年度都没有固定资产变动单"
    return "PASS", "；".join(seen) + "：变动日期在该年度，按卡片、按单号过滤一致"


def _same_money(left: object, right: object) -> bool:
    try:
        return abs(Decimal(str(left)) - Decimal(str(right))) <= Decimal("0.01")
    except ArithmeticError:
        return False


def _card_problem(env: Env, card: str, year: int) -> str:
    # fa_card 按登录年度取累计折旧（响应的 fiscal_year）；只在同一年度上比较。
    archive = _ok(env, _ARC, {"archive": "fa_card", "code": card})
    fields = archive.get("fields") if isinstance(archive.get("fields"), dict) else {}
    period = archive.get("depr_period")
    if not isinstance(period, int) or fields.get("disposed") or archive.get("fiscal_year") != year:
        return ""
    rows = _items(_ok(env, _DEPR, {"card": card, "period": period, "fiscal_year": year}))
    if len(rows) > 1:
        return f"卡片 {card} 在第 {period} 期应最多一行折旧，实际 {len(rows)} 行"
    if not rows:
        return ""
    accumulated = rows[0].get("accumulated")
    if accumulated in (None, 0) or _same_money(accumulated, 0):
        return ""
    if not _same_money(accumulated, fields.get("accumulated_depreciation")):
        return f"卡片 {card} 第 {period} 期累计折旧 {accumulated} 与 fa_card 的 {fields.get('accumulated_depreciation')} 不同"
    return ""


def _depr_year(env: Env, year: int, extra: dict) -> tuple[str, str]:
    """一个年度的折旧核对，返回（问题，摘要）；该年度没有折旧时两者都为空。"""
    payload = _ok(env, _DEPR, {"limit": _PAGE, **extra})
    rows = _items(payload)
    posted = payload.get("posted_periods")
    if not isinstance(posted, list):
        return "折旧报表缺 posted_periods", ""
    if not rows:
        return "", ""
    stray = [row for row in rows if row.get("period") not in posted]
    if stray:
        return f"卡片 {stray[0].get('card_code')} 的期间 {stray[0].get('period')} 不在 posted_periods 里", ""
    return _card_problem(env, str(rows[0].get("card_code")), year), f"{year} 年已计提期间 {posted}，首页 {len(rows)} 行"


def fa_depr_read(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    seen = []
    for year, extra in _fa_years(env):
        problem, summary = _depr_year(env, year, extra)
        if problem:
            return "FAIL", f"{year} 年：{problem}"
        if summary:
            seen.append(summary)
    if not seen:
        return "N/A", "登录年度和上一年度都没有已计提的折旧"
    return "PASS", "；".join(seen) + "；与 fa_card 的累计折旧一致"


IA_SA_FA_OPS = {
    "ia_adjust_read": ia_adjust_read,
    "price_adjust_read": price_adjust_read,
    "fa_changes_read": fa_changes_read,
    "fa_depr_read": fa_depr_read,
}


def ia_sa_fa_cases() -> list[CaseSpec]:
    # 只读；list:<类型> 让之后新建、删除这两类单据的用例不同时跑（目前都没有写路由）。
    def case(name: str, title: str, op: str, routes: tuple[str, ...], tags: tuple[str, ...] = ()) -> CaseSpec:
        step = Step("custom", sql_name=op, id_from="none")
        return CaseSpec(name, title, (), (step,), True, optional=True, tags=frozenset(tags), routes=routes)

    return [
        case("ia-adjust-read", "出入库调整单的列表、keys_only、读取（state.posted）和按单号搜索", "ia_adjust_read",
             (_LIST, _LOAD, _SEARCH), ("list:" + IA,)),
        case("list-inv-price-adjust", "存货调价单列表（空表也要能跑通）与读取", "price_adjust_read", (_LIST, _LOAD),
             ("list:" + PRICE,)),
        case("report-fa-changes", "固定资产变动单报表：按卡片、按单号过滤", "fa_changes_read", (_CHANGES,)),
        case("report-fa-depreciation", "固定资产折旧报表：期间在已计提期间内，累计折旧与 fa_card 一致", "fa_depr_read",
             (_DEPR, _ARC)),
    ]
