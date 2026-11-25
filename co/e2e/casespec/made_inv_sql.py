"""自建存货用例（made_inv / mo_alloc）的挑单、写入与回读 SQL。

写入只有三类，都只动本用例刚经 API 新建的对象：按模板新建的存货改标志和计量单位（inv_flags_sql、inv_units_sql），
本用例新建、未审核的物料清单子件改变动 / 固定用量标志或补辅计量（bom_fixed_sql、bom_aux_sql）。
标识符和列名是常量；编码只接受 [0-9A-Za-z._-]（safe_lot），id 只接受正整数（safe_id），行号走 safe_seq，再拼进 SQL。
挑单返回一行 ROW:a|b|…，挑不到返回空。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from co.e2e.casespec.mo_close_sql import safe_id
from co.e2e.casespec.stock_plus_sql import _no_free, safe_lot
from co.e2e.casespec.bom_qty_sql import safe_seq

_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"
_ALIVE = "(i.dEDate IS NULL OR i.dEDate > DATEADD(day, 1, CONVERT(date, GETDATE())))"
_PART = "EXISTS (SELECT 1 FROM dbo.bas_part AS bp WHERE bp.InvCode = i.cInvCode AND COALESCE(" + ", ".join(
    f"NULLIF(bp.Free{n}, N'')" for n in range(1, 11)
) + ") IS NULL)"
_FLAG = re.compile(r"^(b[A-Za-z]{2,30}|iMassDate|cMassUnit)\Z")
_RATE = re.compile(r"^[0-9]{1,6}(\.[0-9]{1,6})?\Z")


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _num(expr: str) -> str:
    # 原样转成文本（decimal 列保留库里的小数位），空值为空串。
    return f"ISNULL(CONVERT(varchar(40), {expr}), N'')"


def _simple(alias: str) -> str:
    return (
        f"ISNULL({alias}.iGroupType, 0) = 0 AND ISNULL({alias}.bSerial, 0) = 0 AND {_no_free(alias)}"
        f" AND LTRIM(RTRIM({alias}.cInvCode)) {_SAFE} AND LEN({alias}.cInvCode) <= 12"
    )


def safe_rate(text: str) -> str:
    if _RATE.fullmatch(text or "") is None or Decimal(text) <= 0:
        raise ValueError("换算率不合法")
    return text


# ---- 新建存货：空闲编码、标志 ----


def free_inv_sql(codes: list[str]) -> str:
    """候选编码里第一个账套里还没有的（存货表和物料表都没有）。"""
    if not codes:
        raise ValueError("没有候选编码")
    values = ", ".join(f"({n}, N'{safe_lot(code)}')" for n, code in enumerate(codes))
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + c.code AS v
FROM (VALUES {values}) AS c(n, code)
WHERE NOT EXISTS (SELECT 1 FROM dbo.Inventory AS i WHERE i.cInvCode = c.code)
  AND NOT EXISTS (SELECT 1 FROM dbo.bas_part AS p WHERE p.InvCode = c.code)
ORDER BY c.n;
"""


def inv_flags_sql(code: str, flags: dict[str, int]) -> str:
    """按模板复制出的存货改用途标志（同造数 steps_inventory.flags_sql）。返回改动行数。"""
    sets = []
    for column, value in flags.items():
        if _FLAG.fullmatch(column) is None or isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 999:
            raise ValueError("存货标志不合法：" + column)
        sets.append(f"{column} = {value}")
    if not sets:
        raise ValueError("没有要改的标志")
    return (
        f"SET NOCOUNT ON;\nUPDATE dbo.Inventory SET {', '.join(sets)} WHERE cInvCode = N'{safe_lot(code)}';\n"
        "SELECT N'ROW:' + CONVERT(varchar(10), @@ROWCOUNT) AS v;\n"
    )


def inv_units_sql(code: str, group: str, main: str, aux: str) -> str:
    """把存货挂到固定换算组：主计量 main，库存、采购、销售、生产单位都用辅计量 aux，成本用主计量。返回改动行数。"""
    inv, grp, m, a = (safe_lot(part) for part in (code, group, main, aux))
    return (
        f"SET NOCOUNT ON;\nUPDATE dbo.Inventory SET cGroupCode = N'{grp}', iGroupType = 1, cComUnitCode = N'{m}',"
        f" cAssComUnitCode = N'{a}', cPUComUnitCode = N'{a}', cSAComUnitCode = N'{a}', cSTComUnitCode = N'{a}',"
        f" cProductUnit = N'{a}', cCAComUnitCode = N'{m}' WHERE cInvCode = N'{inv}';\n"
        "SELECT N'ROW:' + CONVERT(varchar(10), @@ROWCOUNT) AS v;\n"
    )


def zero_stock_sql(code: str) -> str:
    """删除本用例自建存货的零数量现存量记录（入库单删除后 U8 留下的空行；不删就删不掉存货，后面的挑单也会挑到它）。
    结存、件数、待入、待出都为 0 的行才删。返回删掉的行数。"""
    zero = " AND ".join(f"ISNULL({col}, 0) = 0" for col in ("iQuantity", "iNum", "fInQuantity", "fOutQuantity"))
    return (
        f"SET NOCOUNT ON;\nDELETE FROM dbo.CurrentStock WHERE cInvCode = N'{safe_lot(code)}' AND {zero};\n"
        "SELECT N'ROW:' + CONVERT(varchar(10), @@ROWCOUNT) AS v;\n"
    )


def unit_rate_sql(unit: str) -> str:
    """计量单位：所属组|换算率|是否主计量单位。"""
    expr = "LTRIM(RTRIM(u.cGroupCode)) + N'|' + " + _num("u.iChangRate") + " + N'|' + CONVERT(varchar(1), ISNULL(u.bMainUnit, 0))"
    return _one(expr, f"FROM dbo.ComputationUnit AS u WHERE u.cComunitCode = N'{safe_lot(unit)}'")


# ---- 保质期存货的无来源采购入库（`purchase-in-shelf-made`） ----


def shelf_made_pick_sql() -> str:
    # 不管货位、未冻结、已有现存量记录的仓库；同一仓库里有现存量记录的外购简单存货作模板（新存货按它复制）；
    # 供应商、部门、采购类型同 stock_more_sql.shelf_pick_sql。仓库|模板存货|供应商|部门|采购类型。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(w.cWhCode)) + N'|' + LTRIM(RTRIM(i.cInvCode)) + N'|' + LTRIM(RTRIM(r.cVenCode))
  + N'|' + LTRIM(RTRIM(r.cDepCode)) + N'|' + LTRIM(RTRIM(pt.cPTCode)) AS v
FROM dbo.Warehouse AS w
JOIN dbo.Inventory AS i ON EXISTS (SELECT 1 FROM dbo.CurrentStock AS e WHERE e.cWhCode = w.cWhCode AND e.cInvCode = i.cInvCode)
CROSS APPLY (
  SELECT TOP (1) t.cPTCode FROM dbo.PurchaseType AS t
  WHERE LTRIM(RTRIM(ISNULL(t.cPTCode, N''))) <> N'' AND LTRIM(RTRIM(t.cPTCode)) {_SAFE}
  ORDER BY ISNULL(t.bDefault, 0) DESC, t.cPTCode
) AS pt
CROSS APPLY (
  SELECT TOP (1) h.cVenCode, h.cDepCode FROM dbo.RdRecord01 AS h
  JOIN dbo.Vendor AS v ON v.cVenCode = h.cVenCode
  WHERE h.cBusType = N'普通采购' AND ISNULL(v.cVenExch_name, N'人民币') IN (N'', N'人民币')
    AND LTRIM(RTRIM(ISNULL(h.cDepCode, N''))) <> N''
    AND LTRIM(RTRIM(h.cVenCode)) {_SAFE} AND LTRIM(RTRIM(h.cDepCode)) {_SAFE}
  ORDER BY h.ID DESC
) AS r
WHERE ISNULL(w.bWhPos, 0) = 0 AND ISNULL(w.bFreeze, 0) = 0 AND LTRIM(RTRIM(w.cWhCode)) {_SAFE}
  AND ISNULL(i.bPurchase, 0) = 1 AND ISNULL(i.bInvBatch, 0) = 0 AND ISNULL(i.bInvQuality, 0) = 0
  AND ISNULL(i.bPropertyCheck, 0) = 0 AND ISNULL(i.bService, 0) = 0 AND {_ALIVE} AND {_simple("i")}
ORDER BY w.cWhCode, i.cInvCode;
"""


# ---- 生产订单子件：固定用量、损耗率、辅计量（mo-allocate-fixed-scrap / mo-allocate-aux） ----


def mo_alloc_pick_sql() -> str:
    # 母件模板：自制、可做 BOM 母件（不是 ATO 模型、模型）、有不带自由项的物料，免检的优先（新母件按它复制，再用 SQL 改标志）；
    # 两个子件：可做 BOM 子件、免检、简单存货（无换算、无序列号、无自由项）、有物料；部门、订单类别同 mo_create_sql.moc_src_sql。
    # 母件模板|子件 1|子件 2|部门|订单类别。
    sub = f"""SELECT TOP (2) i.cInvCode, ROW_NUMBER() OVER (ORDER BY i.cInvCode) AS n FROM dbo.Inventory AS i
  WHERE i.bBomSub = 1 AND ISNULL(i.bBomMain, 0) = 0 AND ISNULL(i.bPropertyCheck, 0) = 0 AND ISNULL(i.bInvBatch, 0) = 0
    AND ISNULL(i.bInvQuality, 0) = 0 AND {_ALIVE} AND {_simple("i")} AND {_PART}
  ORDER BY i.cInvCode"""
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + RTRIM(i.cInvCode) + N'|' + RTRIM(c1.cInvCode) + N'|' + RTRIM(c2.cInvCode)
  + N'|' + RTRIM(dep.MDeptCode) + N'|' + RTRIM(t.MotypeCode) AS v
FROM dbo.Inventory AS i
CROSS JOIN (SELECT s.cInvCode FROM ({sub}) AS s WHERE s.n = 1) AS c1
CROSS JOIN (SELECT s.cInvCode FROM ({sub}) AS s WHERE s.n = 2) AS c2
CROSS JOIN (SELECT TOP (1) d.MDeptCode FROM dbo.mom_orderdetail AS d
            JOIN dbo.Department AS dp ON dp.cDepCode = d.MDeptCode
            WHERE dp.bDepEnd = 1 AND LEN(d.MDeptCode) <= 12 AND LTRIM(RTRIM(d.MDeptCode)) {_SAFE}
            ORDER BY d.MoDId DESC) AS dep
CROSS JOIN (SELECT TOP (1) m.MotypeCode FROM dbo.mom_motype AS m
            WHERE LTRIM(RTRIM(m.MotypeCode)) {_SAFE} ORDER BY m.MoTypeId) AS t
WHERE i.bSelf = 1 AND i.bBomMain = 1 AND ISNULL(i.bInvBatch, 0) = 0 AND ISNULL(i.bInvQuality, 0) = 0
  AND ISNULL(i.bATOModel, 0) = 0 AND ISNULL(i.bInvModel, 0) = 0
  AND {_ALIVE} AND {_simple("i")} AND {_PART}
ORDER BY ISNULL(i.bPropertyCheck, 0), i.cInvCode;
"""


def digits_sql() -> str:
    """账套的存货数量小数位|件数小数位（AccInformation AA 的 iStrsQuanDecDgt / iNumDecDgt，读不到按 6）。"""
    expr = (
        "CONVERT(varchar(10), ISNULL(MAX(CASE WHEN o.cName = N'iStrsQuanDecDgt' THEN CONVERT(int, o.cValue) END), 6))"
        " + N'|' + CONVERT(varchar(10), ISNULL(MAX(CASE WHEN o.cName = N'iNumDecDgt' THEN CONVERT(int, o.cValue) END), 6))"
    )
    return _one(expr, "FROM dbo.AccInformation AS o WHERE o.cSysID = N'AA' AND o.cName IN (N'iStrsQuanDecDgt', N'iNumDecDgt')")


def bom_fixed_sql(bom: str, seq: int) -> str:
    """本用例新建、未审核的物料清单：把行 seq 改成固定用量（FVFlag = 2）。返回改动行数。"""
    return (
        f"SET NOCOUNT ON;\nUPDATE dbo.bom_opcomponent SET FVFlag = 2 WHERE BomId = {safe_id(bom)} AND SortSeq = {safe_seq(seq)}"
        f" AND EXISTS (SELECT 1 FROM dbo.bom_bom AS b WHERE b.BomId = {safe_id(bom)} AND b.Status = 1);\n"
        "SELECT N'ROW:' + CONVERT(varchar(10), @@ROWCOUNT) AS v;\n"
    )


def bom_aux_sql(bom: str, seq: int, unit: str, rate: str) -> str:
    """本用例新建、未审核的物料清单：行 seq 补辅计量单位、换算率，辅用量 = 用量分子 / 换算率（6 位小数）。返回改动行数。"""
    doc = safe_id(bom)
    return (
        f"SET NOCOUNT ON;\nUPDATE dbo.bom_opcomponent SET AuxUnitCode = N'{safe_lot(unit)}', ChangeRate = {safe_rate(rate)},"
        f" AuxBaseQtyN = ROUND(BaseQtyN / {safe_rate(rate)}, 6) WHERE BomId = {doc} AND SortSeq = {safe_seq(seq)}"
        f" AND EXISTS (SELECT 1 FROM dbo.bom_bom AS b WHERE b.BomId = {doc} AND b.Status = 1);\n"
        "SELECT N'ROW:' + CONVERT(varchar(10), @@ROWCOUNT) AS v;\n"
    )


def bom_line_sql(bom: str, seq: int) -> str:
    """物料清单一行：变动用量标志|子件损耗率|辅计量单位|换算率|辅用量分子。"""
    cols = ["CONVERT(varchar(10), ISNULL(c.FVFlag, 0))", _num("c.CompScrap"), "ISNULL(RTRIM(c.AuxUnitCode), N'')",
            _num("c.ChangeRate"), _num("c.AuxBaseQtyN")]
    frm = f"FROM dbo.bom_opcomponent AS c WHERE c.BomId = {safe_id(bom)} AND c.SortSeq = {safe_seq(seq)}"
    return _one(" + N'|' + ".join(cols), frm)


# 子件回读的列（顺序即 alloc_rows_sql 的输出）：行号|存货|变动用量标志|用量分子|用量分母|子件损耗率|母件损耗率|
# 辅用量分子|换算率|数量|件数|订单行数量|订单类别 MoClass。
ALLOC_COLS = (
    "seq", "inv", "fv", "qty_n", "qty_d", "comp_scrap", "parent_scrap", "aux_n", "rate", "qty", "aux_qty", "line_qty", "mo_class",
)


def alloc_rows_sql(mo: str) -> str:
    """生产订单的全部子件（按行号），列见 ALLOC_COLS。原样记进报告目录的 SQL 输出，供文档定稿。"""
    cols = [
        "CONVERT(varchar(10), a.SortSeq)", "RTRIM(a.InvCode)", "CONVERT(varchar(10), ISNULL(a.FVFlag, 0))",
        _num("a.BaseQtyN"), _num("a.BaseQtyD"), _num("a.CompScrap"), _num("a.ParentScrap"), _num("a.AuxBaseQtyN"),
        _num("a.ChangeRate"), _num("a.Qty"), _num("a.AuxQty"), _num("d.Qty"), "CONVERT(varchar(10), ISNULL(d.MoClass, 1))",
    ]
    frm = (
        "FROM dbo.mom_moallocate AS a JOIN dbo.mom_orderdetail AS d ON d.MoDId = a.MoDId"
        f" WHERE d.MoId = {safe_id(mo)} ORDER BY a.SortSeq, a.AllocateId"
    )
    return _one(" + N'|' + ".join(cols), frm)


def mo_line_sql(mo: str) -> str:
    """生产订单唯一一行的 MoDId。"""
    return _one("CONVERT(varchar(20), MIN(d.MoDId))", f"FROM dbo.mom_orderdetail AS d WHERE d.MoId = {safe_id(mo)}")


def made_gone_sql(mo: str, bom: str) -> str:
    """删除后：该 MoId 的表头、明细，该 BomId 的版本、子件行数合计。"""
    m, b = safe_id(mo), safe_id(bom)
    expr = (
        f"CONVERT(varchar(20), (SELECT COUNT(*) FROM dbo.mom_order AS o WHERE o.MoId = {m})"
        f" + (SELECT COUNT(*) FROM dbo.mom_orderdetail AS d WHERE d.MoId = {m})"
        f" + (SELECT COUNT(*) FROM dbo.bom_bom AS x WHERE x.BomId = {b})"
        f" + (SELECT COUNT(*) FROM dbo.bom_opcomponent AS c WHERE c.BomId = {b}))"
    )
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v;\n"


def dec(text: str) -> Decimal:
    """回读的数字列（空串按 0）。"""
    try:
        return Decimal((text or "0").strip())
    except InvalidOperation as exc:
        raise ValueError("回读不是数：" + text) from exc


def made_samples() -> list[tuple[str, str]]:
    return [
        ("made-free-inv", free_inv_sql(["91970001", "91970002"])),
        ("made-inv-flags", inv_flags_sql("91970001", {"bInvQuality": 1, "iMassDate": 30})),
        ("made-inv-units", inv_units_sql("91970001", "ZZ01", "ZY01", "ZX01")),
        ("made-unit-rate", unit_rate_sql("ZX01")),
        ("made-zero-stock", zero_stock_sql("91970001")),
        ("made-shelf-pick", shelf_made_pick_sql()),
        ("made-mo-pick", mo_alloc_pick_sql()),
        ("made-digits", digits_sql()),
        ("made-bom-fixed", bom_fixed_sql("100", 20)),
        ("made-bom-aux", bom_aux_sql("100", 10, "ZX01", "2.345678")),
        ("made-bom-line", bom_line_sql("100", 10)),
        ("made-alloc-rows", alloc_rows_sql("100")),
        ("made-mo-line", mo_line_sql("100")),
        ("made-gone", made_gone_sql("100", "101")),
    ]
