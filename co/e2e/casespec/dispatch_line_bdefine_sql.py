"""发货单参照销售订单生单的表体扩展自定义项 cbdefineN（dispatch_line_bdefine）的回读 SQL。

只读；标识符是常量或经 ext_col 校验的 cbdefineN，id 只接受正整数（safe_id）再拼进 SQL。挑单沿用
dispatch_add_sql.pick_sql（样例在那里登记）。列按目录 sys.columns 查，不写死类型和长度：
文本列按字符计长度，日期列按 yyyy-MM-dd 输出（CONVERT 23）。
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from co.e2e.casespec.stock_misc_sql import safe_id

TABLE = "DispatchLists_extradefine"
# 用例要写的列（编号固定，账套没有的列跳过，响应说明里逐列写明）。
WANTED = ("cbdefine5", "cbdefine6", "cbdefine7")
_EXT = re.compile(r"cbdefine([1-9]|[1-9][0-9])\Z")
_DATES = frozenset(("date", "datetime", "smalldatetime", "datetime2"))
_NUMBERS = frozenset(("int", "smallint", "tinyint", "bigint", "decimal", "numeric", "float", "real", "money", "smallmoney"))
_TEXTS = frozenset(("char", "nchar", "varchar", "nvarchar"))


def ext_col(name: str) -> str:
    """合规的 cbdefineN（N 为 1 到 99、无前导零）转小写原样返回，否则空串。"""
    text = name.strip().lower()
    return text if _EXT.fullmatch(text) else ""


def kind_of(data_type: str) -> str:
    """列类型归类：text / number / date；其余空串（用例不写）。"""
    low = data_type.strip().lower()
    if low in _TEXTS:
        return "text"
    if low in _NUMBERS:
        return "number"
    return "date" if low in _DATES else ""


def _ids(values: list[str]) -> str:
    if not values:
        raise ValueError("至少一个 id")
    return ", ".join(str(safe_id(value)) for value in values)


def cols_sql() -> str:
    """DispatchLists_extradefine 里 WANTED 中账套有的列：列名（小写）|类型|字符长度（-1 不限，非文本 0），按列名。"""
    chars = (
        "CASE WHEN c.max_length < 0 THEN -1 WHEN TYPE_NAME(c.system_type_id) IN (N'nchar', N'nvarchar') THEN c.max_length / 2"
        " WHEN TYPE_NAME(c.system_type_id) IN (N'char', N'varchar') THEN c.max_length ELSE 0 END"
    )
    names = ", ".join(f"N'{name}'" for name in WANTED)
    return (
        "SET NOCOUNT ON;\nSELECT N'ROW:' + LOWER(c.name) + N'|' + TYPE_NAME(c.system_type_id) + N'|' + "
        f"CONVERT(varchar(20), {chars}) AS v\nFROM sys.columns AS c\n"
        f"WHERE c.object_id = OBJECT_ID(N'dbo.{TABLE}', N'U') AND LOWER(c.name) IN ({names})\n"
        "ORDER BY c.name;\n"
    )


def absent_col_sql() -> str:
    """表存在时 cbdefine1–99 里账套没有的一列（编号最大的），给拒绝用例；全都有或没有这张表时没有行。"""
    values = ", ".join(f"(N'cbdefine{n}')" for n in range(1, 100))
    return (
        "SET NOCOUNT ON;\nSELECT TOP (1) N'ROW:' + n.c AS v\n"
        f"FROM (VALUES {values}) AS n(c)\n"
        f"WHERE OBJECT_ID(N'dbo.{TABLE}', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.{TABLE}', n.c) IS NULL\n"
        "ORDER BY LEN(n.c) DESC, n.c DESC;\n"
    )


def so_lines_sql(so: str) -> str:
    """用例自建订单的每一行 iSOsID（按 iSOsID）。"""
    frm = f"FROM dbo.SO_SODetails AS d WHERE d.ID = {safe_id(so)} ORDER BY d.iSOsID"
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(20), d.iSOsID) AS v\n" + frm + ";\n"


def _expr(col: str, kind: str) -> str:
    if ext_col(col) != col or not kind:
        raise ValueError("扩展自定义项列名或类型不合法")
    if kind == "date":
        return f"ISNULL(CONVERT(varchar(10), x.{col}, 23), N'')"
    return f"ISNULL(LTRIM(RTRIM(CONVERT(nvarchar(200), x.{col}))), N'')"


def ext_lines_sql(dl: str, cols: list[tuple[str, str]]) -> str:
    """发货单每一行（按 iDLsID）：iDLsID|来源订单行 iSOsID|cols 各列的值（日期 yyyy-MM-dd，没有扩展行时空）。"""
    if not cols:
        raise ValueError("至少一列")
    expr = "CONVERT(varchar(20), b.iDLsID) + N'|' + CONVERT(varchar(20), ISNULL(b.iSOsID, 0))"
    for col, kind in cols:
        expr += " + N'|' + " + _expr(col, kind)
    frm = (
        f"FROM dbo.DispatchLists AS b LEFT JOIN dbo.{TABLE} AS x ON x.iDLsID = b.iDLsID"
        f" WHERE b.DLID = {safe_id(dl)} ORDER BY b.iDLsID"
    )
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def disp_by_lines_sql(so_lines: list[str]) -> str:
    """挂这些订单行的发货单行：DLID|iDLsID（清理前按用例自己的订单行对账，找出响应没给 id 的发货单）。"""
    frm = f"FROM dbo.DispatchLists AS b WHERE b.iSOsID IN ({_ids(so_lines)}) ORDER BY b.DLID, b.iDLsID"
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(20), b.DLID) + N'|' + CONVERT(varchar(20), b.iDLsID) AS v\n" + frm + ";\n"


def gone_sql(so_lines: list[str], dl_lines: list[str]) -> str:
    """清理后的残留：挂这些订单行或是这些 iDLsID 的发货单行数|这些 iDLsID 的扩展表行数，干净时 0|0。"""
    lines = f"(SELECT COUNT(*) FROM dbo.DispatchLists AS b WHERE b.iSOsID IN ({_ids(so_lines)})"
    ext = "N'0'"
    if dl_lines:
        ids = _ids(dl_lines)
        lines += f" OR b.iDLsID IN ({ids})"
        ext = (
            f"CASE WHEN OBJECT_ID(N'dbo.{TABLE}', N'U') IS NULL THEN N'0' ELSE"
            f" (SELECT CONVERT(varchar(20), COUNT(*)) FROM dbo.{TABLE} AS x WHERE x.iDLsID IN ({ids})) END"
        )
    lines += ")"
    return f"SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(20), {lines}) + N'|' + {ext} AS v;\n"


def disp_count_sql(so_lines: list[str]) -> str:
    """挂这些订单行的发货单行数（被拒、预演前后应不变，重放后不翻倍）。"""
    frm = f"FROM dbo.DispatchLists AS b WHERE b.iSOsID IN ({_ids(so_lines)})"
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(20), COUNT(*)) AS v\n" + frm + ";\n"


# 参照生单的行金额：表名、表头键、行键。都是常量，不拼请求里的值。
_AMOUNT_TABLES = {"dispatch": ("DispatchLists", "DLID", "iDLsID"), "sale_invoice": ("SaleBillVouchs", "SBVID", "AutoID")}
_CENT = Decimal("0.01")


def amounts_sql(kind: str, doc: str) -> str:
    """单据每一行（按行键：发货单 iDLsID、发票 AutoID）：行键|数量|含税单价|价税合计|无税金额|税额（6 位 / 2 位小数，空当 0）。"""
    if kind not in _AMOUNT_TABLES:
        raise ValueError("单据类型不支持")
    table, key, line = _AMOUNT_TABLES[kind]
    cols = [("b.iQuantity", 6), ("b.iTaxUnitPrice", 6), ("b.iSum", 2), ("b.iMoney", 2), ("b.iTax", 2)]
    expr = f"CONVERT(varchar(20), b.{line})"
    for col, places in cols:
        expr += f" + N'|' + CONVERT(varchar(40), CONVERT(decimal(28, {places}), ISNULL({col}, 0)))"
    frm = f"FROM dbo.{table} AS b WHERE b.{key} = {safe_id(doc)} ORDER BY b.{line}"
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def amount_problem(rows: list[str], taxed: bool = True) -> str:
    """核对 amounts_sql 的行：含税单价非 0、价税合计 = 无税金额 + 税额；taxed（账套报价含税）时另要
    价税合计 = round(数量 × 含税单价, 2)（不含税时 U8 按无税单价算，不核这一条）。全部符合返回空串，否则返回第一处不符的说明。"""
    if not rows:
        return "没有明细行"
    for row in rows:
        parts = row.strip().split("|")
        try:
            if len(parts) != 6:
                raise ValueError(row)
            qty, price, total, money, tax = (Decimal(part) for part in parts[1:])
        except (InvalidOperation, ValueError):
            return f"行金额读不出：{row.strip()}"
        if price == 0:
            return f"行 {parts[0]} 没有含税单价：{row.strip()}"
        want = (qty * price).quantize(_CENT, rounding=ROUND_HALF_UP)
        if taxed and total != want:
            return f"行 {parts[0]} 价税合计 {total} 应为 round({qty.normalize()} × {price.normalize()}, 2) = {want}"
        if total != money + tax:
            return f"行 {parts[0]} 价税合计 {total} 应等于无税金额 {money} + 税额 {tax}"
    return ""


# 行上的报价含税 bsaleprice：表名、表头键、行键，都是常量。
_FLAG_TABLES = {
    "sale_order": ("SO_SODetails", "ID", "iSOsID"),
    "dispatch": ("DispatchLists", "DLID", "iDLsID"),
    "sale_invoice": ("SaleBillVouchs", "SBVID", "AutoID"),
}


def sale_price_option_sql() -> str:
    """账套销售选项「销售报价是否含税」（AccInformation SA.bSalePrice，空或空白时取 cDefault）；没有这一项时没有行。"""
    return (
        "SET NOCOUNT ON;\nSELECT TOP (1) N'ROW:' + ISNULL(LTRIM(RTRIM(CONVERT(nvarchar(20), ISNULL(NULLIF(LTRIM(RTRIM(a.cValue)), N''), a.cDefault)))), N'') AS v\n"
        "FROM dbo.AccInformation AS a WHERE a.cSysID = N'SA' AND a.cName = N'bSalePrice';\n"
    )


def option_on(value: str) -> bool:
    return value.strip().lower() in ("1", "true")


def flag_col_sql(kind: str) -> str:
    """表体有没有 bsaleprice 列：1 有、0 没有（没有时不查 flags_sql，免得列名报错）。"""
    if kind not in _FLAG_TABLES:
        raise ValueError("单据类型不支持")
    table = _FLAG_TABLES[kind][0]
    return (
        f"SET NOCOUNT ON;\nSELECT N'ROW:' + CASE WHEN COL_LENGTH(N'dbo.{table}', N'bsaleprice') IS NULL"
        " THEN N'0' ELSE N'1' END AS v;\n"
    )


def flags_sql(kind: str, doc: str) -> str:
    """单据每一行：行键|bsaleprice（0 / 1，NULL 为空串），按行键。"""
    if kind not in _FLAG_TABLES:
        raise ValueError("单据类型不支持")
    table, key, line = _FLAG_TABLES[kind]
    expr = f"CONVERT(varchar(20), b.{line}) + N'|' + ISNULL(CONVERT(varchar(5), CONVERT(int, b.bsaleprice)), N'')"
    frm = f"FROM dbo.{table} AS b WHERE b.{key} = {safe_id(doc)} ORDER BY b.{line}"
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def dispatch_bdefine_samples() -> list[tuple[str, str]]:
    return [
        ("dlbd-cols", cols_sql()),
        ("dlbd-absent", absent_col_sql()),
        ("dlbd-so-lines", so_lines_sql("100")),
        ("dlbd-ext-lines", ext_lines_sql("101", [("cbdefine5", "text"), ("cbdefine6", "number"), ("cbdefine7", "date")])),
        ("dlbd-by-lines", disp_by_lines_sql(["100", "102"])),
        ("dlbd-gone", gone_sql(["100", "102"], ["201", "202"])),
        ("dlbd-gone-no-lines", gone_sql(["100"], [])),
        ("dlbd-count", disp_count_sql(["100", "102"])),
        ("dlbd-amounts", amounts_sql("dispatch", "101")),
        ("dlbd-amounts-inv", amounts_sql("sale_invoice", "101")),
        ("dlbd-sale-price-option", sale_price_option_sql()),
        ("dlbd-flag-col", flag_col_sql("sale_invoice")),
        ("dlbd-flags-so", flags_sql("sale_order", "100")),
        ("dlbd-flags-dl", flags_sql("dispatch", "101")),
        ("dlbd-flags-inv", flags_sql("sale_invoice", "101")),
    ]
