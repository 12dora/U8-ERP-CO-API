"""取消记账用例（gl_unpost）的回读 SQL。标识符是常量；拼进 SQL 的年度、期间、类别、凭证号先过白名单。

totals_sql：本凭证分录涉及的科目（科目总账含各级上级，辅助总账、多辅助总账按末级科目）在本期到 12 期的行，
按（行数|校验和）三段拼成一串。全零的行不计入（记账时为新科目补插的行，取消记账后留成全零，U8 的余额表同样不显示），
金额为零时不比方向（U8 的公式在余额为零时一律写「平」）；数量列是 float，按 6 位小数比。
gl_unpost_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

import re

from co.e2e.casespec.gl_voucher_sql import GlKey, gl_key
from co.e2e.testdata import DEFAULT_TRANSFER_SIGN

_YEAR = re.compile(r"^20[0-9]{2}\Z")

_MONEY = ("mb", "md", "mc", "me", "mb_f", "md_f", "mc_f", "me_f")
_QTY = ("nb_s", "nd_s", "nc_s", "ne_s")
_ASS = ("cdept_id", "cperson_id", "ccus_id", "csup_id", "citem_id", "citem_class")
_MULTI_ADD = ("addmd", "addmc", "addmd_f", "addmc_f")


def _year(year: str) -> str:
    if _YEAR.fullmatch(str(year or "")) is None:
        raise ValueError("年度不合法")
    return str(year)


def _key_where(alias: str, year: str, key: GlKey) -> str:
    key = gl_key(key.period, key.sign, key.no)
    return (
        f"{alias}.iyear = {_year(year)} AND {alias}.iperiod = {key.period} AND {alias}.csign = N'{key.sign}'"
        f" AND {alias}.ino_id = {key.no}"
    )


def _nonzero(cols: tuple[str, ...]) -> str:
    return "(" + " OR ".join(f"ISNULL(s.{col}, 0) <> 0" for col in cols) + ")"


def _cols(cols: tuple[str, ...], text: tuple[str, ...]) -> str:
    money = [f"ISNULL(s.{col}, 0)" for col in cols if not col.startswith("n")]
    qty = [f"CONVERT(decimal(28, 6), ISNULL(s.{col}, 0))" for col in cols if col.startswith("n")]
    keys = [f"ISNULL(s.{col}, N'')" for col in text]
    return ", ".join(["s.ccode", "ISNULL(s.cexch_name, N'')", "s.iperiod", *keys, *money, *qty])


def _part(table: str, cols: tuple[str, ...], text: tuple[str, ...], match: str, frm: str) -> str:
    checksum = f"CHECKSUM({_cols(cols, text)})"
    if table != "GL_AccMultiAss":
        checksum = (
            f"CHECKSUM({_cols(cols, text)}, CASE WHEN ISNULL(s.mb, 0) = 0 THEN N'' ELSE s.cbegind_c END,"
            " CASE WHEN ISNULL(s.me, 0) = 0 THEN N'' ELSE s.cendd_c END)"
        )
    return (
        f"(SELECT CONVERT(varchar(20), COUNT_BIG(*)) + N'|' + CONVERT(varchar(20), ISNULL(CHECKSUM_AGG({checksum}), 0))"
        f" FROM dbo.{table} s WHERE {frm} AND EXISTS (SELECT 1 FROM dbo.GL_accvouch v WHERE {match})"
        f" AND {_nonzero(cols)})"
    )


def totals_sql(year: str, key: GlKey) -> str:
    key = gl_key(key.period, key.sign, key.no)
    frm = f"s.iyear = {_year(year)} AND s.iperiod BETWEEN {key.period} AND 12"
    lines = _key_where("v", year, key)
    sums = _part("GL_accsum", _MONEY + _QTY, (), lines + " AND v.ccode LIKE s.ccode + N'%'", frm)
    ass = _part("GL_accass", _MONEY + _QTY, _ASS, lines + " AND v.ccode = s.ccode", frm)
    multi = _part("GL_AccMultiAss", _MONEY + _QTY + _MULTI_ADD, _ASS + ("cname",), lines + " AND v.ccode = s.ccode", frm)
    expr = f"{sums} + N'#' + {ass} + N'#' + {multi}"
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v;\n"


def range_sql(year: str) -> str:
    # GL_mpostcond 本年度的行数（最近一次记账的范围；取消记账后应为 0）。
    expr = "CONVERT(varchar(12), COUNT(*))"
    return f"SET NOCOUNT ON;\nSELECT N'ROW:' + {expr} AS v FROM dbo.GL_mpostcond WHERE iyear = {_year(year)};\n"


def gl_unpost_samples() -> list[tuple[str, str]]:
    key = gl_key(9, DEFAULT_TRANSFER_SIGN, 3)
    return [("glunpost-totals", totals_sql("2026", key)), ("glunpost-range", range_sql("2026"))]
