"""期间损益结转、自定义转账用例（gl_transfer）的挑月份、读 U8 结转凭证和口径核对 SQL。

只读；表名、列名是常量。拼进 SQL 的年度只接受 4 位数字，期间 1 到 12，凭证键过 gl_voucher_sql.gl_key，
摘要只接受汉字、字母、数字和少量标点（safe_digest，不含引号、分号、注释符）。每行输出 ROW:<值>，多个值用 | 分隔。
gl_transfer_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

import re

from co.e2e.casespec.gl_voucher_sql import GlKey, gl_key

PNL_SIGN = "期间损益"
CUSTOM_SIGN = "自定义转账"
_YEAR = re.compile(r"^20[0-9]{2}\Z")
_DIGEST = re.compile(r"^[0-9A-Za-z一-鿿（）()、，,.·\- ]{1,120}\Z")
_LIVE = "ISNULL(v.iflag, 0) <> 1"
_POSTED = "ISNULL(CONVERT(int, v.ibook), 0) = 1"
_AUX = ("cdept_id", "cperson_id", "ccus_id", "csup_id", "citem_id")
MONTH_COLS = 3
# 用到按年取数（cformula 含「年」：桥的公式只认 QM 的期间参数「年」）的自定义转账定义的摘要，按桥的取法：
# 第一个非空 cdigest（按 inid），否则第一行 ctext，再否则「自定义转账」。桥不生成这类定义（跳过或 400）。
_YEAR_DIGEST = (
    "COALESCE((SELECT TOP (1) d.cdigest FROM dbo.GL_bautotran AS d WHERE d.itype = 10 AND d.ctran_id = t.ctran_id"
    " AND ISNULL(d.cdigest, N'') <> N'' ORDER BY d.inid), (SELECT TOP (1) NULLIF(ISNULL(d.ctext, N''), N'')"
    " FROM dbo.GL_bautotran AS d WHERE d.itype = 10 AND d.ctran_id = t.ctran_id ORDER BY d.inid), N'自定义转账')"
)
_YEAR_FROM = "FROM dbo.GL_bautotran AS t WHERE t.itype = 10 AND t.cformula LIKE N'%年%' GROUP BY t.ctran_id"
_YEAR_DEFS = f"SELECT {_YEAR_DIGEST} {_YEAR_FROM}"


def _year(year: object) -> int:
    text = str(year or "").strip()
    if _YEAR.fullmatch(text) is None:
        raise ValueError("年度不合法")
    return int(text)


def _period(period: object) -> int:
    text = str(period or "").strip()
    if not text.isdigit() or not 1 <= int(text) <= 12:
        raise ValueError("期间不合法")
    return int(text)


def safe_digest(raw: str) -> str:
    if _DIGEST.fullmatch(raw or "") is None:
        raise ValueError("摘要含桥核对不了的字符")
    return raw


def _sign(kind: str) -> str:
    if kind not in (PNL_SIGN, CUSTOM_SIGN):
        raise ValueError("结转标记不合法")
    return kind


def _aux(alias: str) -> str:
    return " + N'|' + ".join(f"ISNULL({alias}.{col}, N'')" for col in _AUX)


def _money(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18, 2), {expr}))"


def month_sql(year: object, kind: str, skip_year: bool = False) -> str:
    """一行 <年度>|<期间>|<结转凭证张数>：不晚于 year 年、本期凭证全部记账的最近一个有 U8 结转凭证（kind）的期间。
    skip_year：只看摘要不属于按年取数的自定义转账定义的凭证（_YEAR_DEFS）。"""
    y = _year(year)
    yearly = f" AND ISNULL(v.cdigest, N'') NOT IN ({_YEAR_DEFS})" if skip_year else ""
    unposted = (
        "NOT EXISTS (SELECT 1 FROM dbo.GL_accvouch AS u WHERE u.iyear = v.iyear AND u.iperiod = v.iperiod"
        " AND ISNULL(CONVERT(int, u.ibook), 0) = 0 AND ISNULL(u.iflag, 0) <> 1)"
    )
    count = "COUNT(DISTINCT v.csign + N'-' + CONVERT(varchar(12), v.ino_id))"
    expr = f"CONVERT(varchar(4), v.iyear) + N'|' + CONVERT(varchar(2), v.iperiod) + N'|' + CONVERT(varchar(12), {count})"
    return (
        "SET NOCOUNT ON;\n"
        f"SELECT TOP (1) N'ROW:' + {expr} AS v FROM dbo.GL_accvouch AS v"
        f" WHERE v.iyear <= {y} AND v.coutsign = N'{_sign(kind)}' AND {_LIVE} AND {unposted}{yearly}"
        " GROUP BY v.iyear, v.iperiod ORDER BY v.iyear DESC, v.iperiod DESC;\n"
    )


def year_digests_sql() -> str:
    """每个用到按年取数的自定义转账定义一行 <摘要>（取法同 _YEAR_DEFS）。"""
    return f"SET NOCOUNT ON;\nSELECT N'ROW:' + {_YEAR_DIGEST} AS v {_YEAR_FROM};\n"


def vouchers_sql(year: object, period: object, kind: str) -> str:
    """每张 U8 结转凭证一行：<类别>|<凭证号>|<摘要（第一行）>|<行数>|<已记账 1/0>|<已审核或已签字 1/0>|<制单人>。"""
    y, p = _year(year), _period(period)
    groups = (
        "SELECT v.csign, v.ino_id, COUNT(*) AS n, MIN(ISNULL(CONVERT(int, v.ibook), 0)) AS posted,"
        " CASE WHEN MAX(ISNULL(v.ccheck, N'')) = N'' AND MAX(ISNULL(v.ccashier, N'')) = N'' THEN 0 ELSE 1 END AS checked,"
        " MAX(ISNULL(v.cbill, N'')) AS maker FROM dbo.GL_accvouch AS v"
        f" WHERE v.iyear = {y} AND v.iperiod = {p} AND v.coutsign = N'{_sign(kind)}' AND {_LIVE} GROUP BY v.csign, v.ino_id"
    )
    first = (
        "ISNULL((SELECT TOP (1) ISNULL(f.cdigest, N'') FROM dbo.GL_accvouch AS f"
        f" WHERE f.iyear = {y} AND f.iperiod = {p} AND f.csign = g.csign AND f.ino_id = g.ino_id ORDER BY f.inid), N'')"
    )
    expr = (
        f"g.csign + N'|' + CONVERT(varchar(12), g.ino_id) + N'|' + {first} + N'|' + CONVERT(varchar(12), g.n)"
        " + N'|' + CONVERT(varchar(4), g.posted) + N'|' + CONVERT(varchar(4), g.checked) + N'|' + g.maker"
    )
    return f"SET NOCOUNT ON;\nSELECT N'ROW:' + {expr} AS v FROM ({groups}) AS g ORDER BY g.csign, g.ino_id;\n"


def lines_sql(year: object, key: GlKey) -> str:
    """一张凭证的分录，每行 <科目>|<部门>|<人员>|<客户>|<供应商>|<项目>|<借方>|<贷方>（金额两位小数），按行号。"""
    key = gl_key(key.period, key.sign, key.no)
    where = f"v.iyear = {_year(year)} AND v.iperiod = {key.period} AND v.csign = N'{key.sign}' AND v.ino_id = {key.no}"
    expr = f"v.ccode + N'|' + {_aux('v')} + N'|' + {_money('ISNULL(v.md, 0)')} + N'|' + {_money('ISNULL(v.mc, 0)')}"
    return f"SET NOCOUNT ON;\nSELECT N'ROW:' + {expr} AS v FROM dbo.GL_accvouch AS v WHERE {where} ORDER BY v.inid;\n"


def marks_sql(year: object, key: GlKey) -> str:
    """一行 <行数>|<coutsign 期间损益的行数>|<idoc=-1 的行数>|<coutno_id 是 GL+13 位的行数>|<coutsysname 为空的行数>
    |<不同 coutno_id 个数>|<未记账行数>。"""
    key = gl_key(key.period, key.sign, key.no)
    where = f"v.iyear = {_year(year)} AND v.iperiod = {key.period} AND v.csign = N'{key.sign}' AND v.ino_id = {key.no}"
    parts = (
        "COUNT(*)",
        f"SUM(CASE WHEN v.coutsign = N'{PNL_SIGN}' THEN 1 ELSE 0 END)",
        "SUM(CASE WHEN v.idoc = -1 THEN 1 ELSE 0 END)",
        "SUM(CASE WHEN v.coutno_id LIKE N'GL[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]' THEN 1 ELSE 0 END)",
        "SUM(CASE WHEN ISNULL(v.coutsysname, N'') = N'' THEN 1 ELSE 0 END)",
        "COUNT(DISTINCT v.coutno_id)",
        "SUM(CASE WHEN ISNULL(CONVERT(int, v.ibook), 0) = 0 THEN 1 ELSE 0 END)",
    )
    expr = " + N'|' + ".join(f"CONVERT(varchar(12), {part})" for part in parts)
    return f"SET NOCOUNT ON;\nSELECT N'ROW:' + {expr} AS v FROM dbo.GL_accvouch AS v WHERE {where};\n"


def pnl_oracle_sql(year: object, period: object) -> str:
    """不经桥的期间损益口径：期间损益定义（itype=30 第 1 行）里的损益类末级科目，按凭证上的辅助项，
    取 1 到本期已记账、未作废、去掉本期期间损益凭证的发生额累计（年初余额按 0），结转额 = -余额；
    与本期 U8 期间损益凭证同一科目、辅助项的净额（借-贷）比。
    一行 <参与比较的键数>|<不一致的键数>|<第一个不一致的键：科目|辅助项|应结转|U8>。"""
    y, p = _year(year), _period(period)
    defs = "EXISTS (SELECT 1 FROM dbo.GL_bautotran AS t WHERE t.itype = 30 AND t.inid = 1 AND t.ccode = v.ccode)"
    group = "v.ccode, " + ", ".join(f"ISNULL(v.{col}, N'')" for col in _AUX)
    cols = ", ".join(f"ISNULL(v.{col}, N'') AS {col}" for col in _AUX)
    bal = (
        f"SELECT v.ccode, {cols}, -SUM(ISNULL(v.md, 0) - ISNULL(v.mc, 0)) AS net FROM dbo.GL_accvouch AS v"
        " JOIN dbo.code AS c ON c.iyear = v.iyear AND c.ccode = v.ccode AND c.bend = 1 AND c.cclass = N'损益'"
        f" WHERE v.iyear = {y} AND v.iperiod BETWEEN 1 AND {p} AND {_POSTED} AND {_LIVE}"
        f" AND NOT (v.iperiod = {p} AND ISNULL(v.coutsign, N'') = N'{PNL_SIGN}') AND {defs} GROUP BY {group}"
    )
    made = (
        f"SELECT v.ccode, {cols}, SUM(ISNULL(v.md, 0) - ISNULL(v.mc, 0)) AS net FROM dbo.GL_accvouch AS v"
        f" WHERE v.iyear = {y} AND v.iperiod = {p} AND v.coutsign = N'{PNL_SIGN}' AND {_LIVE} AND {defs} GROUP BY {group}"
    )
    on = " AND ".join(["e.ccode = a.ccode"] + [f"e.{col} = a.{col}" for col in _AUX])
    key = " + N'|' + ".join(f"ISNULL(e.{col}, a.{col})" for col in ("ccode", *_AUX))
    pairs = f"SELECT {key} AS k, ISNULL(e.net, 0) AS en, ISNULL(a.net, 0) AS an FROM e FULL OUTER JOIN a ON {on}"
    diff = "ROUND(d.en - d.an, 2) <> 0"
    first = (
        f"ISNULL((SELECT TOP (1) d.k + N'|' + {_money('d.en')} + N'|' + {_money('d.an')} FROM d WHERE {diff} ORDER BY d.k), N'')"
    )
    expr = (
        f"CONVERT(varchar(12), COUNT(*)) + N'|' + CONVERT(varchar(12), ISNULL(SUM(CASE WHEN {diff} THEN 1 ELSE 0 END), 0))"
        f" + N'|' + {first}"
    )
    return (
        "SET NOCOUNT ON;\n"
        f"WITH e AS (SELECT * FROM ({bal}) AS b WHERE ROUND(b.net, 2) <> 0), a AS ({made}), d AS ({pairs})\n"
        f"SELECT N'ROW:' + {expr} AS v FROM d;\n"
    )


def regen_month_sql(year: object) -> str:
    """一行 <期间>|<期间损益凭证张数>：year 年里总账未结账、有未作废的 U8 期间损益凭证且这些凭证都未记账、未审核、
    未出纳签字，本期再没有别的未记账凭证的最近一个期间（桥重新生成要过的闸门）。没有时为空。"""
    y = _year(year)
    others = (
        "NOT EXISTS (SELECT 1 FROM dbo.GL_accvouch AS u WHERE u.iyear = v.iyear AND u.iperiod = v.iperiod"
        f" AND ISNULL(CONVERT(int, u.ibook), 0) = 0 AND ISNULL(u.iflag, 0) <> 1 AND ISNULL(u.coutsign, N'') <> N'{PNL_SIGN}')"
    )
    opened = "EXISTS (SELECT 1 FROM dbo.GL_mend AS m WHERE m.iyear = v.iyear AND m.iperiod = v.iperiod AND ISNULL(m.bflag, 0) = 0)"
    clean = (
        "MAX(ISNULL(CONVERT(int, v.ibook), 0)) = 0 AND MAX(ISNULL(v.ccheck, N'')) = N''"
        " AND MAX(ISNULL(v.ccashier, N'')) = N''"
    )
    count = "COUNT(DISTINCT v.csign + N'-' + CONVERT(varchar(12), v.ino_id))"
    return (
        "SET NOCOUNT ON;\n"
        f"SELECT TOP (1) N'ROW:' + CONVERT(varchar(2), v.iperiod) + N'|' + CONVERT(varchar(12), {count}) AS v"
        f" FROM dbo.GL_accvouch AS v WHERE v.iyear = {y} AND v.coutsign = N'{PNL_SIGN}' AND {_LIVE} AND {opened}"
        f" AND {others} GROUP BY v.iyear, v.iperiod HAVING {clean} ORDER BY v.iperiod DESC;\n"
    )


def gl_transfer_samples() -> list[tuple[str, str]]:
    key = gl_key(6, "转", 355)
    return [
        ("gltransfer-month", month_sql("2026", PNL_SIGN)),
        ("gltransfer-month-noyear", month_sql("2026", CUSTOM_SIGN, skip_year=True)),
        ("gltransfer-year-defs", year_digests_sql()),
        ("gltransfer-vouchers", vouchers_sql("2025", "6", CUSTOM_SIGN)),
        ("gltransfer-lines", lines_sql("2025", key)),
        ("gltransfer-marks", marks_sql("2025", key)),
        ("gltransfer-oracle", pnl_oracle_sql("2025", "6")),
        ("gltransfer-regen", regen_month_sql("2026")),
    ]
