"""总账受控科目选项用例（gl_controlled）的 SQL：总账选项 bUseYSKM / bUseYFKM 的读和临时改写、受控科目挑选、凭证回读。

写语句只改那一个选项行：带原值作条件，回 @@ROWCOUNT 供核对；拼进去的只有白名单里的选项名、校验过的短字母数字串、
年度和凭证键。gl_controlled_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

import re

from co.e2e.casespec.gl_voucher_sql import GlKey, gl_key, safe_code

_VALUE = re.compile(r"^[A-Za-z0-9]{0,10}\Z")
_YEAR = re.compile(r"^20[0-9]{2}\Z")
_DATE = re.compile(r"^20[0-9]{2}-[01][0-9]-[0-3][0-9]\Z")
# 原值按 nvarchar 的 UTF-16LE 字节转十六进制回读、原样写回（空格不丢）；最多 100 个字符。
_HEX = re.compile(r"^(?:[0-9A-F]{4}){0,100}\Z")
_RAW = "CONVERT(varchar(400), CONVERT(varbinary(200), CONVERT(nvarchar(100), cValue)), 2)"
DIGEST = "CO桥回归"
_OPTIONS = ("bUseYSKM", "bUseYFKM")
_TEXT = "ISNULL(LTRIM(RTRIM(CONVERT(nvarchar(20), cValue))), N'')"
# 受控方 → （受控系统标记, 另一方标记, 往来单位辅助列, 档案表, 编码列）。
_SIDES = {
    "AR": ("AR", "AP", "bcus", "Customer", "cCusCode"),
    "AP": ("AP", "AR", "bsup", "Vendor", "cVenCode"),
}


def _value(text: str) -> str:
    if type(text) is not str or _VALUE.fullmatch(text) is None:
        raise ValueError("选项值只能是 10 个以内的字母数字")
    return text


def _option(name: str) -> str:
    if name not in _OPTIONS:
        raise ValueError("不是受控科目选项")
    return f"cSysID = N'GL' AND cName = N'{name}'"


def _year(year: str) -> str:
    if _YEAR.fullmatch(str(year or "")) is None:
        raise ValueError("年度不合法")
    return str(year)


def option_sql(name: str) -> str:
    """一行：<选项行数>|<cValue 为 NULL 时 1，否则 0>|<原值的十六进制（UTF-16LE）>|<选项值（去空格）>。"""
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(varchar(4), COUNT(*))"
        " + N'|' + CONVERT(varchar(1), MAX(CASE WHEN cValue IS NULL THEN 1 ELSE 0 END))"
        f" + N'|' + ISNULL(MAX({_RAW}), N'') + N'|' + ISNULL(MAX({_TEXT}), N'') AS v\n"
        f"FROM dbo.AccInformation WHERE {_option(name)};\n"
    )


def option_set_sql(name: str, new: str, old: str) -> str:
    """把选项从 old 改成 new（只改值等于 old 的那一行）；一行：<改动行数>。"""
    return (
        "SET NOCOUNT ON;\n"
        f"UPDATE dbo.AccInformation SET cValue = N'{_value(new)}' WHERE {_option(name)} AND {_TEXT} = N'{_value(old)}';\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def option_restore_sql(name: str, raw: str | None, now: str) -> str:
    """把选项从 now（去空格后的值）写回原值：raw 为 None 时写 NULL，否则写十六进制原值还原的 nvarchar；一行：<改动行数>。"""
    if raw is None:
        value = "NULL"
    elif _HEX.fullmatch(raw or "") is None:
        raise ValueError("原值不是 UTF-16LE 十六进制")
    else:
        value = f"CONVERT(nvarchar(100), 0x{raw})" if raw else "N''"
    return (
        "SET NOCOUNT ON;\n"
        f"UPDATE dbo.AccInformation SET cValue = {value} WHERE {_option(name)} AND {_TEXT} = N'{_value(now)}';\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def account_pick_sql(side: str, year: str) -> str:
    """本年一个只受 side 系统控制的末级科目：未封存、本币、不挂现金流量，辅助核算只有往来单位（AR 客户、AP 供应商），
    外加一个未停用的往来单位。一行：<科目编码>|<往来单位编码>；挑不到为空。"""
    if side not in _SIDES:
        raise ValueError("side 只能是 AR / AP")
    mine, other, aux, table, col = _SIDES[side]
    flags = " AND ".join(
        f"ISNULL(k.{flag}, 0) = {1 if flag == aux else 0}" for flag in ("bcus", "bsup", "bdept", "bperson", "bitem")
    )
    cother = "UPPER(ISNULL(k.cother, N''))"
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(k.ccode)) + N'|' + LTRIM(RTRIM(p.code)) AS v
FROM dbo.code AS k
CROSS APPLY (
  SELECT TOP (1) u.{col} AS code FROM dbo.{table} AS u
  WHERE u.dEndDate IS NULL AND LTRIM(RTRIM(u.{col})) NOT LIKE N'%[^0-9A-Za-z]%' AND LEN(LTRIM(RTRIM(u.{col}))) BETWEEN 1 AND 20
  ORDER BY u.{col}
) AS p
WHERE k.iyear = {_year(year)} AND ISNULL(k.bend, 0) = 1 AND ISNULL(k.bclose, 0) = 0
  AND {cother} LIKE N'%{mine}%' AND {cother} NOT LIKE N'%{other}%'
  AND {flags}
  AND ISNULL(k.bCashItem, 0) = 0 AND LTRIM(RTRIM(ISNULL(k.cexch_name, N''))) = N''
  AND LTRIM(RTRIM(k.ccode)) NOT LIKE N'%[^0-9A-Za-z]%' AND LEN(LTRIM(RTRIM(k.ccode))) <= 20
ORDER BY k.ccode;
"""


def voucher_rows_sql(year: str, key: GlKey, account: str) -> str:
    """凭证：<行数>|<该科目行数>|<该科目行最小 bvalueedit（没有为 ->|<该科目行 coutsysname>。"""
    acct = f"N'{safe_code(account)}'"
    expr = (
        "CONVERT(varchar(10), COUNT(*))"
        f" + N'|' + CONVERT(varchar(10), ISNULL(SUM(CASE WHEN ccode = {acct} THEN 1 ELSE 0 END), 0))"
        f" + N'|' + ISNULL(CONVERT(varchar(4), MIN(CASE WHEN ccode = {acct} THEN CONVERT(int, ISNULL(bvalueedit, 1)) END)), N'-')"
        f" + N'|' + ISNULL(MAX(CASE WHEN ccode = {acct} THEN LTRIM(RTRIM(coutsysname)) END), N'')"
    )
    where = (
        f"iyear = {_year(year)} AND iperiod = {key.period} AND csign = N'{key.sign}' AND ino_id = {key.no}"
    )
    return f"SET NOCOUNT ON;\nSELECT N'ROW:' + {expr} AS v\nFROM dbo.GL_accvouch WHERE {where};\n"


def made_by_digest_sql(year: str, day: str, account: str) -> str:
    """新建结果不明（超时、504 等）时找可能已保存的凭证：本年、制单日期 day、摘要 DIGEST、带受控科目 account 的凭证键，
    每张一行：<期间>|<类别>|<凭证号>。"""
    if _DATE.fullmatch(str(day or "")) is None:
        raise ValueError("日期不合法")
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(varchar(4), t.iperiod) + N'|' + t.csign + N'|' + CONVERT(varchar(10), t.ino_id) AS v\n"
        "FROM (SELECT DISTINCT iperiod, LTRIM(RTRIM(csign)) AS csign, ino_id FROM dbo.GL_accvouch"
        f" WHERE iyear = {_year(year)} AND dbill_date = '{day}' AND cdigest = N'{DIGEST}'"
        f" AND ccode = N'{safe_code(account)}') AS t ORDER BY t.iperiod, t.ino_id;\n"
    )


def gl_controlled_samples() -> list[tuple[str, str]]:
    key = gl_key(9, "转", 3)
    return [
        ("glc-option", option_sql("bUseYSKM")),
        ("glc-option-set", option_set_sql("bUseYFKM", "True", "False")),
        ("glc-pick-ar", account_pick_sql("AR", "2026")),
        ("glc-pick-ap", account_pick_sql("AP", "2026")),
        ("glc-rows", voucher_rows_sql("2026", key, "112201")),
        ("glc-option-restore", option_restore_sql("bUseYSKM", "460061006C0073006500", "True")),
        ("glc-option-restore-null", option_restore_sql("bUseYSKM", None, "True")),
        ("glc-made-by-digest", made_by_digest_sql("2026", "2026-10-02", "112201")),
    ]
