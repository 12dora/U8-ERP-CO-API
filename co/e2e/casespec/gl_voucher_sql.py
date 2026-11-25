"""总账和档案用例的挑单与回读。标识符是常量；拼进 SQL 的值先过白名单。

现金流量项目、候选空闲编码和依赖档案来自测试数据（co.e2e.testdata）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from co.e2e.testdata import DEFAULT_TRANSFER_SIGN
from co.e2e.testdata import value as td
from co.e2e.testdata import values as td_list

_YEAR = re.compile(r"^20[0-9]{2}\Z")
_PERIOD = re.compile(r"^(1[0-2]|[1-9])\Z")
_NO = re.compile(r"^[1-9][0-9]{0,6}\Z")
_SIGN = re.compile(r"^[一-鿿]{1,2}\Z")
_CODE = re.compile(r"^[0-9A-Za-z]{1,20}\Z")
_PREFIX = re.compile(r"^[0-9A-Za-z]{0,20}\Z")


def cash_item() -> str:
    return safe_code(td("gl.cash_flow_item"))


@dataclass(frozen=True)
class GlKey:
    period: int
    sign: str
    no: int


def gl_key(period: object, sign: object, no: object) -> GlKey:
    text_p, text_s, text_n = str(period).strip(), str(sign).strip(), str(no).strip()
    if not (_PERIOD.fullmatch(text_p) and _SIGN.fullmatch(text_s) and _NO.fullmatch(text_n)):
        raise ValueError("凭证键不合法")
    return GlKey(int(text_p), text_s, int(text_n))


def pack_key(key: GlKey) -> str:
    return f"{key.period}|{key.sign}|{key.no}"


def parse_key(text: str) -> GlKey:
    parts = (text or "").split("|")
    if len(parts) != 3:
        raise ValueError("没有凭证键")
    return gl_key(*parts)


def safe_code(code: str) -> str:
    if _CODE.fullmatch(code or "") is None:
        raise ValueError("档案编码不合法")
    return code


def _year(year: str) -> str:
    if _YEAR.fullmatch(str(year or "")) is None:
        raise ValueError("年度不合法")
    return str(year)


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _vouch_where(year: str, key: GlKey) -> str:
    return f"v.iyear = {_year(year)} AND v.iperiod = {key.period} AND v.csign = N'{key.sign}' AND v.ino_id = {key.no}"


def _cash_where(year: str, key: GlKey) -> str:
    # 导入后 csign 可能还是 NULL，按 dsign 的 isignseq 也认。
    seq = f"(SELECT TOP (1) s.isignseq FROM dbo.dsign AS s WHERE s.csign = N'{key.sign}')"
    return (
        f"c.iyear = {_year(year)} AND c.iPeriod = {key.period} AND c.iNo_id = {key.no}"
        f" AND (c.csign = N'{key.sign}' OR c.iSignSeq = {seq})"
    )


def _rows(year: str, key: GlKey) -> str:
    return _one("CONVERT(varchar(12), COUNT(*))", "FROM dbo.GL_accvouch AS v WHERE " + _vouch_where(year, key))


def _void_rows(year: str, key: GlKey) -> str:
    where = _vouch_where(year, key) + " AND v.iflag = 1"
    return _one("CONVERT(varchar(12), COUNT(*))", "FROM dbo.GL_accvouch AS v WHERE " + where)


def _cash_rows(year: str, key: GlKey) -> str:
    return _one("CONVERT(varchar(12), COUNT(*))", "FROM dbo.GL_CashTable AS c WHERE " + _cash_where(year, key))


def _cash_sign(year: str, key: GlKey) -> str:
    # 每一行现金流量的 csign 都已补成凭证类别字，且有配置的现金流量项目这一项。
    expr = f"""CONVERT(varchar(2), CASE WHEN COUNT(*) > 0
  AND SUM(CASE WHEN c.csign = N'{key.sign}' THEN 1 ELSE 0 END) = COUNT(*)
  AND SUM(CASE WHEN LTRIM(RTRIM(c.cCashItem)) = N'{cash_item()}' THEN 1 ELSE 0 END) > 0
  THEN 1 ELSE 0 END)"""
    return _one(expr, "FROM dbo.GL_CashTable AS c WHERE " + _cash_where(year, key))


_GL_READ = {"rows": _rows, "void_rows": _void_rows, "cash_rows": _cash_rows, "cash_sign": _cash_sign}


def gl_sql(name: str, year: str, key: GlKey) -> str:
    builder = _GL_READ.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(year, key)


def gl_module_sql(year: str) -> str:
    # 模块生成（AR/AP/IA 等）、未记账、未作废、未审核、未签字、期间未结账且没人在编辑的凭证。
    safe = _year(year)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(2), v.iperiod) + N'|' + LTRIM(RTRIM(v.csign)) + N'|'
  + CONVERT(varchar(12), v.ino_id) AS v
FROM dbo.GL_accvouch AS v
WHERE v.iyear = {safe} AND v.iperiod BETWEEN 1 AND 12
  AND ISNULL(v.coutsysname, N'') NOT IN (N'', N'GL')
  AND EXISTS (SELECT 1 FROM dbo.GL_mend AS m
    WHERE m.iyear = {safe} AND m.iperiod = v.iperiod AND ISNULL(m.bflag, 0) = 0)
  AND NOT EXISTS (SELECT 1 FROM dbo.GL_mvcontrol AS k
    WHERE k.iyear = {safe} AND k.iperiod = v.iperiod AND k.csign = v.csign AND k.ino_id = v.ino_id)
GROUP BY v.iperiod, v.csign, v.ino_id
HAVING SUM(CASE WHEN ISNULL(v.ibook, 0) <> 0 THEN 1 ELSE 0 END) = 0
  AND SUM(CASE WHEN v.iflag IS NOT NULL THEN 1 ELSE 0 END) = 0
  AND SUM(CASE WHEN LTRIM(RTRIM(ISNULL(v.ccheck, N''))) <> N'' THEN 1 ELSE 0 END) = 0
  AND SUM(CASE WHEN LTRIM(RTRIM(ISNULL(v.ccashier, N''))) <> N'' THEN 1 ELSE 0 END) = 0
ORDER BY v.iperiod DESC, v.ino_id DESC;
"""


# 档案 → (主表, 编码列, 另需查重的 (表, 列))。候选编码按测试数据 archives.<档案>.codes 的顺序挑第一个空闲的。
_ARC = {
    "customer": ("Customer", "cCusCode", ()),
    "vendor": ("Vendor", "cVenCode", ()),
    "department": ("Department", "cDepCode", ()),
    "warehouse": ("Warehouse", "cWhCode", ()),
    "customer_class": ("CustomerClass", "cCCCode", ()),
    "vendor_class": ("VendorClass", "cVCCode", ()),
    "inventory_class": ("InventoryClass", "cInvCCode", ()),
    "person": ("hr_hi_person", "cPsn_Num", (("Person", "cPersonCode"),)),
    "inventory": ("Inventory", "cInvCode", ()),
}
ARCHIVES = tuple(_ARC)


def _depends(archive: str) -> str:
    # 人员挂在配置的部门下；存货按配置的模板存货新建。依赖档案不在就挑不出编码，用例记 SKIP。
    if archive == "person":
        return f"EXISTS (SELECT 1 FROM dbo.Department AS d WHERE d.cDepCode = N'{safe_code(td('archives.person.dept'))}')"
    if archive == "inventory":
        code = safe_code(td("archives.inventory.template"))
        return f"EXISTS (SELECT 1 FROM dbo.Inventory AS t WHERE t.cInvCode = N'{code}')"
    return ""


def arc_table(archive: str) -> tuple[str, str]:
    if archive not in _ARC:
        raise ValueError("未知档案")
    table, col, _extra = _ARC[archive]
    return table, col


def free_code_sql(archive: str) -> str:
    table, col = arc_table(archive)
    extra = _ARC[archive][2]
    cand = td_list("archives." + archive + ".codes")[:20]
    dep = _depends(archive)
    values = ", ".join(f"({i}, N'{safe_code(code)}')" for i, code in enumerate(cand))
    clash = [(table, col)] + list(extra)
    wheres = [f"NOT EXISTS (SELECT 1 FROM dbo.{t} AS x WHERE x.{c} = c.code)" for t, c in clash]
    if dep:
        wheres.append(dep)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + c.code AS v
FROM (VALUES {values}) AS c(n, code)
WHERE {" AND ".join(wheres)}
ORDER BY c.n;
"""


def arc_count_sql(archive: str, code: str) -> str:
    table, col = arc_table(archive)
    frm = f"FROM dbo.{table} AS t WHERE t.{col} = N'{safe_code(code)}'"
    return _one("CONVERT(varchar(12), COUNT(*))", frm)


def used_customer_sql() -> str:
    # 已被销售订单、发货单或收款单引用的客户；优先 C000001。
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(c.cCusCode)) AS v
FROM dbo.Customer AS c
WHERE LTRIM(RTRIM(c.cCusCode)) NOT LIKE N'%[^0-9A-Za-z]%'
  AND (EXISTS (SELECT 1 FROM dbo.SO_SOMain AS m WHERE m.cCusCode = c.cCusCode)
    OR EXISTS (SELECT 1 FROM dbo.DispatchList AS d WHERE d.cCusCode = c.cCusCode)
    OR EXISTS (SELECT 1 FROM dbo.Ap_CloseBill AS b WHERE b.cDwCode = c.cCusCode AND b.cFlag = N'AR'))
ORDER BY CASE WHEN c.cCusCode = N'C000001' THEN 0 ELSE 1 END, c.cCusCode;
"""


def list_prefix_sql() -> str:
    # 至少 6 个客户才能在 limit 5 时拿到 next；C1000 不够就放宽。
    return """SET NOCOUNT ON;
SELECT N'ROW:' + CASE
  WHEN (SELECT COUNT(*) FROM dbo.Customer WHERE cCusCode LIKE N'C1000%') >= 6 THEN N'C1000'
  WHEN (SELECT COUNT(*) FROM dbo.Customer WHERE cCusCode LIKE N'C10%') >= 6 THEN N'C10'
  WHEN (SELECT COUNT(*) FROM dbo.Customer WHERE cCusCode LIKE N'C%') >= 6 THEN N'C'
  ELSE N'-' END AS v;
"""


def safe_prefix(text: str) -> str:
    if text == "" or _PREFIX.fullmatch(text) is None:
        raise ValueError("前缀不合法")
    return text


def core_fin_samples() -> list[tuple[str, str]]:
    key = GlKey(9, DEFAULT_TRANSFER_SIGN, 1)
    rows = [("core-gl-" + name.replace("_", "-"), gl_sql(name, "2026", key)) for name in _GL_READ]
    rows.append(("core-gl-module", gl_module_sql("2026")))
    rows.extend(("core-free-" + name.replace("_", "-"), free_code_sql(name)) for name in ARCHIVES)
    rows.extend(("core-count-" + name.replace("_", "-"), arc_count_sql(name, "C199901")) for name in ARCHIVES)
    rows.append(("core-used-customer", used_customer_sql()))
    rows.append(("core-list-prefix", list_prefix_sql()))
    return rows
