"""造数前的只读预检：造数用的编码合不合账套的编码方案，配置的科目、现金流量项目在不在。

任何写入之前跑（__main__ 在 plan / run 之前）。不合时抛一条 SeedError，逐项写明哪里不同、该改 e2e 配置 seed 段的哪个键。
编码方案取 GradeDef_Base（iYear = 0，CODINGRULE 每位是一级的长度，和桥算级次的口径一致）；账套里没有某个编码方案的行时
不判断，交给 U8。科目按造数年度查 code 表，必须存在且是末级；现金流量项目查 fitemss98，必须存在且未关闭。
"""

from __future__ import annotations

from dataclasses import dataclass

from co.e2e.seed import data
from co.e2e.seed.base import quoted
from co.e2e.seed.context import SeedContext, SeedError


@dataclass(frozen=True)
class Need:
    keyword: str  # GradeDef_Base.KEYWORD
    key: str  # e2e 配置里的键（seed.…）
    code: str
    rank: int  # 这个编码应在第几级


_KIND_NAMES = {
    "department": "部门",
    "customerclass": "客户分类",
    "vendorclass": "供应商分类",
    "inventoryclass": "存货分类",
    "rd_style": "收发类别",
    "settlestyle": "结算方式",
    "districtclass": "地区分类",
    "positionclass": "货位",
}
_CYCLE_KEYWORD = {
    "department": "department",
    "customer_class": "customerclass",
    "vendor_class": "vendorclass",
    "inventory_class": "inventoryclass",
}
_GRADE_SQL = "SELECT KEYWORD AS kw, CODINGRULE AS coding_rule FROM GradeDef_Base WHERE iYear = 0 AND KEYWORD IN ({names})"
_SUBJECT_SQL = "SELECT LTRIM(RTRIM(ccode)) AS code, bend AS leaf FROM code WHERE iyear = {year} AND ccode IN ({codes})"
_CASH_SQL = (
    "SELECT LTRIM(RTRIM(citemcode)) AS code FROM fitemss98 WHERE ISNULL(bclose, 0) = 0 AND citemcode IN ({codes})"
)


def needs() -> list[Need]:
    """造数要建的分级档案编码和它们应在的级次（按 data 当前的值，含 seed 段的覆盖）。"""
    items = [Need("department", "seed.codes.departments", code, 1) for code in data.DEPARTMENT_CODES]
    items += [
        Need("customerclass", "seed.codes.customer_class", data.CUSTOMER_CLASS, 1),
        Need("vendorclass", "seed.codes.vendor_class", data.VENDOR_CLASS, 1),
        Need("inventoryclass", "seed.codes.inventory_class", data.INV_CLASS_TOP, 1),
        Need("inventoryclass", "seed.codes.inventory_class", data.INV_CLASS, 2),
        Need("settlestyle", "seed.codes.settle_style", data.SETTLE_STYLE, 1),
        Need("districtclass", "seed.codes.district_class", data.DISTRICT, 1),
        Need("positionclass", "seed.codes.position", data.POSITION, 1),
    ]
    for role, code in data.RD.items():
        items.append(Need("rd_style", "seed.codes.rd_style." + role, code, 2 if role in data.RD_PARENT else 1))
    for kind, keyword in _CYCLE_KEYWORD.items():
        items += [Need(keyword, "seed.cycle_codes." + kind, code, 1) for code in data.CYCLE_CODES[kind]]
    return items


def levels(rule: str) -> list[int]:
    """CODINGRULE → 各级末尾的累计长度，例如 22222 → [2, 4, 6, 8, 10]；非数字位跳过（同桥）。"""
    total, out = 0, []
    for char in rule:
        if "1" <= char <= "9":
            total += int(char)
            out.append(total)
    return out


def scheme_problems(rules: dict[str, str], items: list[Need]) -> list[str]:
    """按 KEYWORD → CODINGRULE 核对编码长度；每个配置键只报第一个不合的编码。"""
    problems, seen = [], set()
    for need in items:
        rule = rules.get(need.keyword)
        if rule is None or need.key in seen:
            continue
        ends = levels(rule)
        if need.rank <= len(ends) and len(need.code) == ends[need.rank - 1]:
            continue
        seen.add(need.key)
        want = str(ends[need.rank - 1]) + " 位" if need.rank <= len(ends) else "（编码方案没有这一级）"
        problems.append(
            f"{_KIND_NAMES[need.keyword]}编码 {need.code} 应在第 {need.rank} 级，账套编码方案 {rule} 第 {need.rank} 级"
            f"累计 {want}：设置 {need.key}"
        )
    return problems


def subjects() -> list[tuple[str, str]]:
    """要核对的科目：(配置键, 科目)。银行科目只在 seed.gl.bank_account 配了时核对（否则造数现查）。"""
    items = [
        ("seed.gl.expense_account", data.GL["expense_account"]),
        ("seed.gl.payable_account", data.GL["payable_account"]),
    ]
    if data.BANK_FIXED:
        items.append(("seed.gl.bank_account", data.GL["bank_account"]))
    return items


def subject_problems(rows: list[dict[str, str]], items: list[tuple[str, str]]) -> list[str]:
    found = {row.get("code", "").strip(): row.get("leaf", "").strip() for row in rows}
    problems = []
    for key, code in items:
        if code not in found:
            problems.append(f"科目 {code} 在造数年度的科目表里不存在：设置 {key}")
        elif found[code] not in ("1", "True", "true"):
            problems.append(f"科目 {code} 不是末级科目：设置 {key}")
    return problems


def cash_problems(rows: list[dict[str, str]], item: str) -> list[str]:
    if any(row.get("code", "").strip() == item for row in rows):
        return []
    return [f"现金流量项目 {item} 不存在或已关闭：设置 seed.gl.cash_flow_item"]


def check(ctx: SeedContext) -> None:
    """只读核对；有不合的就抛 SeedError（一条消息列出全部），之前不写任何东西。"""
    year = ctx.day[:4]
    if not year.isdigit():
        raise SeedError("日期不合法：" + ctx.day)
    items = needs()
    keywords = sorted({need.keyword for need in items})
    rows = ctx.rows(_GRADE_SQL.format(names=quoted(keywords)))
    rules = {row.get("kw", "").strip().lower(): row.get("coding_rule", "").strip() for row in rows}
    problems = scheme_problems(rules, items)
    gl = subjects()
    problems += subject_problems(ctx.rows(_SUBJECT_SQL.format(year=year, codes=quoted([c for _k, c in gl]))), gl)
    cash = data.GL["cash_flow_item"]
    problems += cash_problems(ctx.rows(_CASH_SQL.format(codes=quoted([cash]))), cash)
    if problems:
        raise SeedError("造数前检查不通过（没有写入任何数据），在 e2e 配置的 seed 段改：\n- " + "\n- ".join(problems))
