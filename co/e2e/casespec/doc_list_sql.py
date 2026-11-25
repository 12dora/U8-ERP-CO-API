"""列表用例的挑单和计数。编码只接受字母、数字、-、_、.，日期只接受 yyyy-MM-dd，再拼进 SQL。"""

from __future__ import annotations

import re

_CODE = re.compile(r"^[0-9A-Za-z._-]{1,20}\Z")
_DAY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"

# kind → (表, 往来单位列, 日期列, 固定条件)。与桥的列表类型一致。
_LIST = {
    "sale_order": ("dbo.SO_SOMain", "cCusCode", "dDate", "1 = 1"),
    "ar_receipt": ("dbo.Ap_CloseBill", "cDwCode", "dVouchDate", "cFlag = N'AR' AND cVouchType = N'48'"),
}


def _code(text: str) -> str:
    if _CODE.fullmatch(text or "") is None:
        raise ValueError("编码不合法")
    return text


def _day(text: str) -> str:
    if _DAY.fullmatch(text or "") is None:
        raise ValueError("日期不合法")
    return text


def _range_pick(kind: str) -> str:
    # 单据最多的往来单位里取最近一张的日期，往前 60 天作为日期范围：往来单位|起|止。
    table, cus, day, cond = _LIST[kind]
    return (
        "SET NOCOUNT ON;\n"
        "SELECT TOP (1)\n"
        f"  N'ROW:' + LTRIM(RTRIM(h.{cus})) + N'|'\n"
        f"  + CONVERT(varchar(10), DATEADD(day, -60, MAX(h.{day})), 23) + N'|'\n"
        f"  + CONVERT(varchar(10), MAX(h.{day}), 23) AS v\n"
        f"FROM {table} AS h\n"
        f"WHERE {cond}\n"
        f"  AND LTRIM(RTRIM(ISNULL(h.{cus}, N''))) <> N''\n"
        f"  AND LTRIM(RTRIM(h.{cus})) {_SAFE}\n"
        f"GROUP BY h.{cus}\n"
        "HAVING COUNT(*) >= 2\n"
        f"ORDER BY COUNT(*) DESC, MAX(h.{day}) DESC, h.{cus};\n"
    )


def so_list_sql() -> str:
    return _range_pick("sale_order")


def ar_list_sql() -> str:
    return _range_pick("ar_receipt")


def stock_pair_sql() -> str:
    # 现存量行最多的仓库+存货（多批次时能看出筛选是否只按这两列）：仓库|存货。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(cs.cWhCode)) + N'|' + LTRIM(RTRIM(cs.cInvCode)) AS v
FROM dbo.CurrentStock AS cs
WHERE cs.iQuantity > 0
  AND LTRIM(RTRIM(cs.cWhCode)) {_SAFE}
  AND LTRIM(RTRIM(cs.cInvCode)) {_SAFE}
GROUP BY cs.cWhCode, cs.cInvCode
ORDER BY COUNT(*) DESC, cs.cWhCode, cs.cInvCode;
"""


def list_count_sql(kind: str, cus: str, day_from: str, day_to: str) -> str:
    # 与列表筛选同一口径：往来单位相等，日期含两端。
    if kind not in _LIST:
        raise ValueError("未知列表类型")
    table, col, day, cond = _LIST[kind]
    safe = _code(cus)
    low = _day(day_from)
    high = _day(day_to)
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), COUNT(*)) AS v\n"
        f"FROM {table}\n"
        f"WHERE {cond} AND {col} = N'{safe}'\n"
        f"  AND {day} >= CONVERT(date, '{low}', 23)\n"
        f"  AND {day} < DATEADD(day, 1, CONVERT(date, '{high}', 23));\n"
    )


def stock_count_sql(wh: str, inv: str) -> str:
    safe_wh = _code(wh)
    safe_inv = _code(inv)
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), COUNT(*)) AS v\n"
        "FROM dbo.CurrentStock\n"
        f"WHERE cWhCode = N'{safe_wh}' AND cInvCode = N'{safe_inv}';\n"
    )


def list_samples() -> tuple[tuple[str, str], ...]:
    return (
        ("core-so-list", so_list_sql()),
        ("core-ar-list", ar_list_sql()),
        ("core-stock-pair", stock_pair_sql()),
        ("core-count-so", list_count_sql("sale_order", "C000002", "2026-06-01", "2026-08-07")),
        ("core-count-ar", list_count_sql("ar_receipt", "C000002", "2026-06-01", "2026-08-07")),
        ("core-count-stock", stock_count_sql("13", "INV0002")),
    )
