"""客户字段 `archive-customer-fields` 和收货地址 `archive-customer-address` 用例的 SQL。

标识符是常量；拼进 SQL 的客户、地区、协议、地址编码先过白名单（safe_code）或转义成 N'' 字面量（lit），日期先过 _day。
整行快照复用 arc_cus_person_sql.customer_row_sql，这里只换「读原值的列」。
SQL 文本里不出现 sqlguard 拒绝的词（还原一词只写在 Python 名字里）。
"""

from __future__ import annotations

import re
from collections.abc import Callable

from co.e2e.casespec.arc_cus_fields_more_sql import more_samples
from co.e2e.casespec.arc_cus_person_sql import NIL, customer_row_sql
from co.e2e.casespec.gl_voucher_sql import safe_code

_ALNUM = "NOT LIKE N'%[^0-9A-Za-z]%'"
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_ID = re.compile(r"^[1-9][0-9]{0,18}\Z")
_DAY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
# 销售默认收付款协议：销售协议（iLZYJ=1，启用销售后 U8 另放行 0，取 1 在两种情况下都成立）且没停用：
# 停用日期为空或晚于登录日（与桥同口径：dEDate 非空且 <= 登录日即停用）。
_SA = "a.iLZYJ = 1"
_ALIVE = _SA + " AND (a.dEDate IS NULL OR CONVERT(varchar(10), a.dEDate, 23) > N'{day}')"
# 客户字段用例要读原值的列（其余列只比哈希）；cModifyPerson 要和操作员姓名比，也读原值。
FIELD_COLUMNS = (
    "cCusAbbName",
    "cDCCode",
    "dEndDate",
    "cCusSAProtocol",
    "cCusName",
    "cCusRegCode",
    "cCusPerson",
    "cCusBank",
    "cCusTradeCCode",
    "cTrade",
    "cCusDefine1",
    "cModifyPerson",
)
# 收货地址表里 customer_address 可能改的列：还原兜底只写这几列。
ADDR_COLUMNS = ("cAddCode", "cDeliverAdd", "cDeliverUnit", "bDefault")
# 一个客户最多挑几条地址（快照输出不要太长）。
MAX_ROWS = 8


def lit(text: str) -> str:
    """N'' 字面量：拒绝控制字符，单引号翻倍。"""
    if _CTRL.search(text or ""):
        raise ValueError("值含控制字符")
    return "N'" + (text or "").replace("'", "''") + "'"


def _row_id(value: str) -> str:
    if _ID.fullmatch(value or "") is None:
        raise ValueError("行号不合法")
    return value


def _day(text: str) -> str:
    if _DAY.fullmatch(text or "") is None:
        raise ValueError("日期不合法")
    return text


def cus_row_sql(code: str) -> str:
    return customer_row_sql(code, FIELD_COLUMNS)


def customer_created_sql(code: str) -> str:
    safe = safe_code(code)
    return (
        "SET NOCOUNT ON;\n"
        "SELECT TOP (1) N'ROW:' + ISNULL(CONVERT(varchar(10), c.dCusCreateDatetime, 23), N'-') AS v\n"
        f"FROM dbo.Customer AS c WHERE c.cCusCode = N'{safe}';\n"
    )


def other_abbr_sql(code: str) -> str:
    """别的客户已用的简称（首尾无空白、1–60 字）：简称重复 409 abbrname_taken 用。只当请求正文，不拼回 SQL。"""
    safe = safe_code(code)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + c.cCusAbbName AS v
FROM dbo.Customer AS c
WHERE c.cCusCode <> N'{safe}' AND LEN(c.cCusAbbName) BETWEEN 1 AND 60
  AND c.cCusAbbName = LTRIM(RTRIM(c.cCusAbbName))
ORDER BY c.cCusCode;
"""


def district_pick_sql(skip: str) -> str:
    """末级地区分类里挑一个编码（不是 skip）。"""
    other = f"\n  AND LTRIM(RTRIM(d.cDCCode)) <> N'{safe_code(skip)}'" if skip else ""
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(d.cDCCode)) AS v
FROM dbo.DistrictClass AS d
WHERE d.bDCEnd = 1 AND LTRIM(RTRIM(d.cDCCode)) {_ALNUM}{other}
ORDER BY d.cDCCode;
"""


def district_leaf_sql(code: str) -> str:
    """编码存在且是末级就回编码。"""
    safe = safe_code(code)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(d.cDCCode)) AS v
FROM dbo.DistrictClass AS d
WHERE d.cDCCode = N'{safe}' AND d.bDCEnd = 1;
"""


def district_count_sql(code: str) -> str:
    safe = safe_code(code)
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), COUNT(*)) AS v\n"
        f"FROM dbo.DistrictClass WHERE cDCCode = N'{safe}';\n"
    )


def district_delete_sql(code: str) -> str:
    """路由删不掉（只读账套只放行 create）时，测试账套上用 SQL 删掉本用例新建的地区。"""
    safe = safe_code(code)
    return (
        "SET NOCOUNT ON;\n"
        f"DELETE FROM dbo.DistrictClass WHERE cDCCode = N'{safe}' AND bDCEnd = 1;\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def agreement_pick_sql(skip: str, day: str) -> str:
    """没停用的收付款协议里挑一个编码（不是 skip，不分大小写）；skip 不是字母数字时挑出的码本来就不会等于它。"""
    try:
        other = f"\n  AND UPPER(LTRIM(RTRIM(a.cCode))) <> UPPER(N'{safe_code(skip)}')" if skip else ""
    except ValueError:
        other = ""
    alive = _ALIVE.format(day=_day(day))
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(a.cCode)) AS v
FROM dbo.AA_Agreement AS a
WHERE LTRIM(RTRIM(a.cCode)) {_ALNUM} AND LEN(LTRIM(RTRIM(a.cCode))) BETWEEN 1 AND 20
  AND {alive}{other}
ORDER BY a.cCode;
"""


def agreement_alive_sql(code: str, day: str) -> str:
    """编码存在且没停用就回档案里存的编码（调用方再和客户上的原值逐字比）。"""
    safe = safe_code(code)
    alive = _ALIVE.format(day=_day(day))
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(a.cCode)) AS v
FROM dbo.AA_Agreement AS a
WHERE a.cCode = N'{safe}' AND {alive};
"""


def agreement_stopped_sql(day: str) -> str:
    """登录日当天或之前已停用的收付款协议里挑一个编码（否定路径用）。"""
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(a.cCode)) AS v
FROM dbo.AA_Agreement AS a
WHERE LTRIM(RTRIM(a.cCode)) {_ALNUM} AND LEN(LTRIM(RTRIM(a.cCode))) BETWEEN 1 AND 20
  AND {_SA} AND a.dEDate IS NOT NULL AND CONVERT(varchar(10), a.dEDate, 23) <= N'{_day(day)}'
ORDER BY a.cCode;
"""


def pick_protocols(ask: Callable[[str], str], now: str, day: str) -> tuple[str, str, str]:
    """协议步骤要用的码：(没停用的另一码, 已停用的码, 跳过说明)。

    说明非空时只跳过协议步骤，其余三键照测。客户现值非空时必须没停用且与档案编码逐字一致（含字符白名单），
    否则路由写不回去。ask 跑一条 SQL、回第一格。
    """
    try:
        alive = ask(agreement_alive_sql(now, day)) if now else ""
    except ValueError:
        alive = ""
    if now and alive != now:
        return "", "", "客户现有的收付款协议已停用、不在档案里或与档案编码不一致，跳过协议步骤"
    try:
        picked = safe_code(ask(agreement_pick_sql(now, day)))
    except ValueError:
        return "", "", "账套没有可用的收付款协议（不新建协议档案），跳过协议步骤"
    try:
        stopped = safe_code(ask(agreement_stopped_sql(day)))
    except ValueError:
        stopped = ""
    return picked, stopped, ""


def agreement_count_sql(code: str) -> str:
    safe = safe_code(code)
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), COUNT(*)) AS v\n"
        f"FROM dbo.AA_Agreement WHERE cCode = N'{safe}';\n"
    )


def addr_customer_sql() -> str:
    """有收货地址的客户：优先 2 条以上（能测默认地址切换），行数不多的。"""
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(a.cCusCode)) AS v
FROM dbo.CusDeliverAdd AS a
INNER JOIN dbo.Customer AS c ON c.cCusCode = a.cCusCode
WHERE LTRIM(RTRIM(a.cCusCode)) {_ALNUM}
GROUP BY a.cCusCode
HAVING COUNT(*) BETWEEN 1 AND {MAX_ROWS}
ORDER BY CASE WHEN COUNT(*) >= 2 THEN 0 ELSE 1 END, COUNT(*), a.cCusCode;
"""


def addr_dump_sql(code: str) -> str:
    """客户全部收货地址的逐列快照，每列一行 ``ROW:行号<TAB>列名<TAB>值``；NULL 为 ``<nil>``，制表、换行转义成 \\t \\n \\r。

    别名只用两段写法（``n.value``、``m.value``）：sqlguard 拒绝三段名称。
    """
    safe = safe_code(code)
    value = (
        "REPLACE(REPLACE(REPLACE(m.value(N'.', N'nvarchar(1000)'),"
        " NCHAR(9), N'\\t'), NCHAR(13), N'\\r'), NCHAR(10), N'\\n')"
    )
    return (
        "SET NOCOUNT ON;\n"
        "SET QUOTED_IDENTIFIER ON;\n"
        "SELECT N'ROW:' + n.value(N'(id)[1]', N'nvarchar(20)') + NCHAR(9)"
        " + m.value(N'local-name(.)', N'nvarchar(128)') + NCHAR(9) + CASE\n"
        "  WHEN m.value(N'(@*:nil)[1]', N'nvarchar(5)') = N'true' THEN N'<nil>'\n"
        f"  ELSE {value}\n"
        "END AS v\n"
        "FROM (SELECT (SELECT t.* FROM dbo.CusDeliverAdd AS t "
        f"WHERE t.cCusCode = N'{safe}' ORDER BY t.id FOR XML RAW(N'r'), ELEMENTS XSINIL, TYPE) AS x) AS s\n"
        "CROSS APPLY x.nodes(N'/r') AS q(n)\n"
        "CROSS APPLY n.nodes(N'*') AS c(m);\n"
    )


def addr_used_sql() -> str:
    """被发货单引用的收货地址：输出 客户编码<TAB>地址编码（地址编码可以不是字母数字，只当请求正文用）。"""
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(a.cCusCode)) + NCHAR(9) + a.cAddCode AS v
FROM dbo.CusDeliverAdd AS a
WHERE LTRIM(RTRIM(a.cCusCode)) {_ALNUM}
  AND LEN(a.cAddCode) BETWEEN 1 AND 30 AND a.cAddCode = LTRIM(RTRIM(a.cAddCode))
  AND EXISTS (SELECT 1 FROM dbo.DispatchList AS d WHERE d.cCusCode = a.cCusCode AND d.cAddCode = a.cAddCode)
ORDER BY a.id;
"""


def addr_count_sql(code: str, addr: str) -> str:
    safe = safe_code(code)
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), COUNT(*)) AS v\n"
        f"FROM dbo.CusDeliverAdd WHERE cCusCode = N'{safe}' AND cAddCode = {lit(addr)};\n"
    )


def addr_insert_sql(code: str, addr: str) -> str:
    """造一条没有单据引用的测试地址（默认地址为否），本用例结束前自己删掉。"""
    safe = safe_code(code)
    return (
        "SET NOCOUNT ON;\n"
        "INSERT INTO dbo.CusDeliverAdd (cCusCode, cAddCode, cDeliverAdd, cDeliverUnit, bDefault)\n"
        f"VALUES (N'{safe}', N'{safe_code(addr)}', N'E2E临时地址', N'E2E临时单位', 0);\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def addr_delete_sql(code: str, addrs: tuple[str, ...]) -> str:
    safe = safe_code(code)
    names = ", ".join(f"N'{safe_code(item)}'" for item in addrs)
    return (
        "SET NOCOUNT ON;\n"
        f"DELETE FROM dbo.CusDeliverAdd WHERE cCusCode = N'{safe}' AND cAddCode IN ({names});\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def _text_or_null(value: str) -> str:
    return "NULL" if value == NIL else lit(value)


def addr_reset_sql(code: str, row_id: str, vals: dict[str, str]) -> str:
    """按行号把可能被路由改过的四列写回快照值（路由还原不了时的兜底，测试账套上用）。"""
    safe = safe_code(code)
    flag = "1" if vals.get("bDefault", "").strip().lower() in ("1", "true") else "0"
    return (
        "SET NOCOUNT ON;\n"
        f"UPDATE dbo.CusDeliverAdd SET cAddCode = {lit(vals.get('cAddCode', ''))},\n"
        f"  cDeliverAdd = {_text_or_null(vals.get('cDeliverAdd', ''))},\n"
        f"  cDeliverUnit = {_text_or_null(vals.get('cDeliverUnit', ''))},\n"
        f"  bDefault = {flag}\n"
        f"WHERE id = {_row_id(row_id)} AND cCusCode = N'{safe}';\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def cus_samples() -> list[tuple[str, str]]:
    return [
        ("core-cus-row", cus_row_sql("C900001")),
        ("core-cus-created", customer_created_sql("C900001")),
        ("core-cus-other-abbr", other_abbr_sql("C900001")),
        ("core-cus-district-pick", district_pick_sql("33")),
        ("core-cus-district-leaf", district_leaf_sql("33")),
        ("core-cus-district-count", district_count_sql("ZZ1")),
        ("core-cus-district-delete", district_delete_sql("ZZ1")),
        ("core-cus-agreement-pick", agreement_pick_sql("A001", "2026-09-28")),
        ("core-cus-agreement-pick-blank", agreement_pick_sql("", "2026-09-28")),
        ("core-cus-agreement-stopped", agreement_stopped_sql("2026-09-28")),
        ("core-cus-agreement-alive", agreement_alive_sql("A001", "2026-09-28")),
        ("core-cus-agreement-count", agreement_count_sql("ZZNOPROT")),
        ("core-cus-addr-customer", addr_customer_sql()),
        ("core-cus-addr-dump", addr_dump_sql("C900001")),
        ("core-cus-addr-used", addr_used_sql()),
        ("core-cus-addr-count", addr_count_sql("C900001", "E2Eab12")),
        ("core-cus-addr-insert", addr_insert_sql("C900001", "E2Eab12")),
        ("core-cus-addr-delete", addr_delete_sql("C900001", ("E2Eab12", "E2Ecd34"))),
        ("core-cus-addr-reset", addr_reset_sql("C900001", "7", {"cAddCode": "1", "cDeliverAdd": NIL, "bDefault": "1"})),
        *more_samples(),
    ]
