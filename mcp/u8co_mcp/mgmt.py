"""经营管理查询工具（u8_mgmt_*）：定义、令牌声明检查和请求体。

只在配置了 mgmt、enabled 为 true、且当前令牌的 JWT 载荷里 mgmt.claim 声明为 true，或配置了 mgmt.scope 且
令牌的 scope / scp 含这个值时列出（与 API 信任项的 mgmt_claim / mgmt_scope 对应）。
本服务只解码载荷决定列不列（不验签），API 仍会校验签名和权限。
请求体：stdio 方式下 logins 由配置的各账套登录注入（账套、操作员、口令），工具参数只选账套和期间；
incoming 方式（person_proxy）下不带任何登录，一次交给身份绑定服务，以调用者本人的 U8 操作员执行（多账套全有或全无）。
没给 accounts 时取配置的账套；令牌带账套声明（mgmt.accounts_claim，与 API 信任项的 accounts_claim 对应）时
只取其中令牌授权的账套。
"""

from __future__ import annotations

import base64
import binascii
import calendar
import datetime as dt
import json
import re

from u8co_mcp.config import MgmtConfig, read_secret_file

MAX_LOGINS = 3
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
_ACCS_SPLIT = re.compile(r"[\s,]+")
_ACC = re.compile(r"^\d{3}\Z")

# 工具名 → API 路由（/v1/co/ 之后）
ROUTES = {
    "u8_mgmt_overview": "mgmt/overview",
    "u8_mgmt_pnl": "mgmt/pnl",
    "u8_mgmt_sales": "mgmt/sales",
    "u8_mgmt_arap": "mgmt/arap",
    "u8_mgmt_cash_stock": "mgmt/cash_stock",
}
# 工具名 → 身份绑定服务的路由名（person_proxy 协议里的 route）
PROXY_ROUTES = {
    "u8_mgmt_overview": "reports/mgmt/overview",
    "u8_mgmt_pnl": "reports/mgmt/pnl",
    "u8_mgmt_sales": "reports/mgmt/sales",
    "u8_mgmt_arap": "reports/mgmt/arap_terms",
    "u8_mgmt_cash_stock": "reports/mgmt/cash_stock",
}
_BASE_ARGS = ("accounts", "fiscal_year", "period_from", "period_to", "consolidate")


class MgmtArgsError(ValueError):
    """参数组合不对。args[0] 是中文说明，field 是参数名。"""

    def __init__(self, message: str, field: str = ""):
        super().__init__(message)
        self.field = field


def token_claims(token: str) -> dict:
    """解码 JWT 载荷（不验签）。不是 JWT 或解不出对象时返回空字典。"""
    parts = token.split(".")
    if len(parts) != 3 or not parts[1]:
        return {}
    segment = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(segment.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeError, binascii.Error):
        return {}
    return payload if isinstance(payload, dict) else {}


def has_claim(token: str, claim: str) -> bool:
    return token_claims(token).get(claim) is True


def token_scopes(claims: dict) -> frozenset[str]:
    """scope（空格分隔的字符串或数组）和 scp 里的值，与 API 的解析相同。"""
    found: set[str] = set()
    for key in ("scope", "scp"):
        raw = claims.get(key)
        if isinstance(raw, str):
            found.update(raw.split())
        elif isinstance(raw, list):
            found.update(item for item in raw if isinstance(item, str))
    return frozenset(found)


def allowed(token: str, mgmt: MgmtConfig) -> bool:
    """令牌带 mgmt.claim 声明（true），或配置了 mgmt.scope 且令牌的 scope 含它。"""
    claims = token_claims(token)
    if claims.get(mgmt.claim) is True:
        return True
    return bool(mgmt.scope) and mgmt.scope in token_scopes(claims)


def token_accounts(token: str, mgmt: MgmtConfig) -> frozenset[str] | None:
    """令牌账套声明里的账套（数组，或逗号、空格分隔的字符串，同 API 的解析）。没配声明名或令牌没有这个声明时
    返回 None（不收窄，由 API 判断）。不是三位数字的项忽略。"""
    if not mgmt.accounts_claim:
        return None
    raw = token_claims(token).get(mgmt.accounts_claim)
    if isinstance(raw, str):
        raw = _ACCS_SPLIT.split(raw.strip())
    if not isinstance(raw, list):
        return None
    return frozenset(item for item in raw if isinstance(item, str) and _ACC.match(item))


# ---- 工具定义 ----


def _int(low: int, high: int, description: str) -> dict:
    return {"type": "integer", "minimum": low, "maximum": high, "description": description}


def _base_props(mgmt: MgmtConfig) -> dict:
    accs = [item.acc for item in mgmt.accounts]
    return {
        "accounts": {
            "type": "array",
            "items": {"type": "string", "enum": accs},
            "minItems": 1,
            "maxItems": MAX_LOGINS,
            "description": (
                f"账套号，可选 {'、'.join(accs)}，一次最多 {MAX_LOGINS} 个、不能重复；"
                "缺省为配置的账套中令牌有权限的全部账套"
            ),
        },
        "fiscal_year": _int(2000, 2099, "会计年度，如 2026"),
        "period_from": _int(1, 12, "起始会计期间 1 到 12，缺省等于 period_to"),
        "period_to": _int(1, 12, "截止会计期间 1 到 12"),
        "consolidate": {
            "type": "boolean",
            "description": "多账套时是否合并（抵销公司间往来和内部收入成本），缺省 true",
        },
    }


def _tool(name: str, title: str, description: str, mgmt: MgmtConfig, extra: dict | None = None) -> dict:
    props = _base_props(mgmt)
    props.update(extra or {})
    schema = {
        "type": "object",
        "properties": props,
        "required": ["fiscal_year", "period_to"],
        "additionalProperties": False,
    }
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": schema,
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    }


_PNL = {
    "include_unposted": {"type": "boolean", "description": "包括未记账凭证，缺省 false（只算已记账）"},
    "dims": {
        "type": "array",
        "items": {"type": "string", "enum": ["dept", "item"]},
        "maxItems": 1,
        "description": '辅助维度：["dept"] 按部门，["item"] 按项目；缺省不分',
    },
}
_SALES = {
    "group_by": {
        "type": "array",
        "items": {"type": "string", "enum": ["customer", "inventory", "person", "department"]},
        "minItems": 1,
        "maxItems": 4,
        "description": "分组：customer 客户，inventory 存货，person 业务员，department 部门；可多选",
    },
    "top": _int(1, 500, "按收入从高到低取前几行，其余合并为「其他」，缺省 200"),
}
_ARAP = {
    "side": {"type": "string", "enum": ["ar", "ap"], "description": "ar 应收（客户），ap 应付（供应商）"},
    "as_of": {
        "type": "string",
        "minLength": 10,
        "maxLength": 10,
        "description": "截止日期 yyyy-MM-dd，缺省为 period_to 的月末",
    },
    "buckets": {
        "type": "array",
        "items": {"type": "integer", "minimum": 1, "maximum": 3650},
        "minItems": 1,
        "maxItems": 8,
        "description": "账龄分段的天数上限，升序，如 [30, 60, 90, 180]",
    },
    "default_credit_days": _int(0, 3650, "单据没有信用期时按这个天数算到期日"),
}


def tool_list(mgmt: MgmtConfig) -> list[dict]:
    return [
        _tool(
            "u8_mgmt_overview",
            "经营概览",
            "经营管理：期间关键指标一次给出——收入、毛利及毛利率、净利润、货币资金、应收、应付、逾期应收、"
            "存货金额、应收周转天数（DSO）、应付周转天数（DPO）。多账套按公司间对照合并。只读。",
            mgmt,
        ),
        _tool(
            "u8_mgmt_pnl",
            "利润表（管理口径）",
            "经营管理：按期间的管理利润表（收入、成本、税金、三项费用、营业利润、利润总额、净利润），"
            "取自总账凭证，剔除期间损益结转；多账套合并时抵销内部收入成本。只读。",
            mgmt,
            _PNL,
        ),
        _tool(
            "u8_mgmt_sales",
            "销售分析",
            "经营管理：销售收入、数量、销售成本、毛利和毛利率，按客户、存货、业务员、部门分组排行。只读。",
            mgmt,
            _SALES,
        ),
        _tool(
            "u8_mgmt_arap",
            "应收应付分析",
            "经营管理：往来单位的余额、逾期金额、按到期日的账龄分段、信用期分布和回款天数；多账套合并时抵销公司间往来。只读。",
            mgmt,
            _ARAP,
        ),
        _tool(
            "u8_mgmt_cash_stock",
            "资金与存货",
            "经营管理：期末货币资金、未到期应收票据、存货金额和数量、本期产量和按供应商的采购额。只读。",
            mgmt,
        ),
    ]


# ---- 请求体 ----


def build_body(
    mgmt: MgmtConfig, name: str, args: dict, today: dt.date, granted: frozenset[str] | None = None
) -> tuple[str, dict, list[str]]:
    """stdio 方式：返回 (路由, 请求体, 用到的口令)。granted 是令牌授权的账套（None 表示不限），只用于缺省账套。
    参数组合不对抛 MgmtArgsError；口令文件不可用抛 ConfigError。"""
    if mgmt.person_proxy is not None:
        raise AssertionError("person_proxy 方式不能注入共用登录")
    accs, params = _params(mgmt, args, granted)
    year, period_to = args["fiscal_year"], args["period_to"]
    passwords: list[str] = []
    logins = []
    login_date = _login_date(year, period_to, today)
    for acc in accs:
        account = mgmt.account(acc)
        assert account is not None
        password = read_secret_file(account.password_file, f" {acc} 账套的 U8 口令")
        passwords.append(password)
        login = {"acc": acc, "operator": account.operator, "password": password, "year": str(year), "date": login_date}
        logins.append(login)
    body: dict = {"logins": logins}
    body.update(params)
    if "consolidate" in args:
        body["consolidate"] = args["consolidate"]
    return ROUTES[name], body, passwords


def person_request(
    mgmt: MgmtConfig, name: str, args: dict, granted: frozenset[str] | None = None
) -> tuple[str, list[str], dict]:
    """incoming 方式：返回 (身份绑定服务的路由名, 账套, 不含任何登录的请求体)。全部账套一次交给身份绑定服务，
    由它为每个账套注入调用者本人的登录后调一次 API（合并照常在 API 里做）。"""
    accs, params = _params(mgmt, args, granted)
    if "consolidate" in args:
        params["consolidate"] = args["consolidate"]
    return PROXY_ROUTES[name], accs, params


def _params(mgmt: MgmtConfig, args: dict, granted: frozenset[str] | None) -> tuple[list[str], dict]:
    """校验参数，返回 (账套, 除登录和 consolidate 以外的请求体)。"""
    year = args["fiscal_year"]
    period_to = args["period_to"]
    period_from = args.get("period_from", period_to)
    if period_from > period_to:
        raise MgmtArgsError("period_from 不能大于 period_to", "period_from")
    accs = _accounts(mgmt, args.get("accounts"), granted)
    as_of = args.get("as_of")
    if as_of is not None:
        _check_date(as_of)
    buckets = args.get("buckets")
    if buckets is not None and buckets != sorted(set(buckets)):
        raise MgmtArgsError("buckets 必须严格升序、不能重复", "buckets")
    params: dict = {"fiscal_year": year, "period_from": period_from, "period_to": period_to}
    for key, value in args.items():
        if key not in _BASE_ARGS:
            params[key] = value
    return accs, params


def _accounts(mgmt: MgmtConfig, given: list[str] | None, granted: frozenset[str] | None) -> list[str]:
    if given is None:
        accs = [item.acc for item in mgmt.accounts if granted is None or item.acc in granted]
        if not accs:
            raise MgmtArgsError("令牌没有配置里任何账套的权限", "accounts")
        if len(accs) > MAX_LOGINS:
            raise MgmtArgsError(f"配置了 {len(accs)} 个账套，一次最多查 {MAX_LOGINS} 个：用 accounts 指定", "accounts")
        return accs
    if len(set(given)) != len(given):
        raise MgmtArgsError("accounts 里的账套不能重复", "accounts")
    return list(given)


def _check_date(text: str) -> None:
    try:
        ok = bool(_DATE.match(text)) and dt.date.fromisoformat(text) is not None
    except ValueError:
        ok = False
    if not ok:
        raise MgmtArgsError("as_of 必须是 yyyy-MM-dd 格式的日期", "as_of")


def _login_date(year: int, period_to: int, today: dt.date) -> str:
    """登录日期要落在查询年度内：本年用今天，往年用 period_to 的月末（自然月口径）。"""
    if today.year == year:
        return today.isoformat()
    last = calendar.monthrange(year, period_to)[1]
    return dt.date(year, period_to, last).isoformat()
