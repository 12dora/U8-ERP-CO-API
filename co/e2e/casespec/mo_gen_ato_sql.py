"""生产订单参照销售订单生成（ATO）用例的挑单与回读 SQL（只读）。

id 只接受正整数（safe_id），编码只接受 [0-9A-Za-z._-]（safe_lot）；挑单、回读每行 ROW:a|b|…，挑不到为空。
"""

from __future__ import annotations

from co.e2e.casespec.mo_close_sql import safe_id
from co.e2e.casespec.stock_plus_sql import safe_lot

_HEAD = "SET NOCOUNT ON;\n"
_AUDITED = (
    "((ISNULL(m.cChanger, N'') = N'' AND ISNULL(m.cVerifier, N'') <> N'')"
    " OR (ISNULL(m.cChanger, N'') <> N'' AND ISNULL(m.cChangeVerifier, N'') <> N''))"
)


def _txt(expr: str) -> str:
    return f"ISNULL(CONVERT(nvarchar(40), {expr}), N'')"


def _join(*exprs: str) -> str:
    return " + N'|' + ".join(exprs)


def gen_rows_sql(so: int) -> str:
    """U8 生产订单生成列表（V_SA_GenOrdData_ForMO）里这张销售订单的行：
    iSOsID|可生单数量|已下达数量|选配主键|需求跟踪方式|跟踪 id|跟踪单号|跟踪行号|订单号|行号。"""
    cols = _join(
        _txt("v.OrderDId"), _txt("v.UnGenQty"), _txt("v.MoQty"), _txt("ISNULL(v.cBomId, 0)"), _txt("v.SoType"), _txt("v.SoDId"),
        _txt("v.SoCode"), _txt("v.SoSeq"), _txt("v.OrderCode"), _txt("v.OrderSeq"),
    )
    return (
        f"{_HEAD}SELECT N'ROW:' + {cols} AS v FROM dbo.V_SA_GenOrdData_ForMO AS v"
        f" WHERE v.OrderId = {safe_id(str(so))} ORDER BY v.OrderDId;\n"
    )


def imo_sql(lines: list[int]) -> str:
    """销售订单行的已下达生产数量：iSOsID|iMoQuantity。"""
    ids = ", ".join(safe_id(str(line)) for line in lines)
    return (
        f"{_HEAD}SELECT N'ROW:' + {_join(_txt('d.iSOsID'), _txt('ISNULL(d.iMoQuantity, 0)'))} AS v"
        f" FROM dbo.SO_SODetails AS d WHERE d.iSOsID IN ({ids}) ORDER BY d.iSOsID;\n"
    )


def max_modid_sql() -> str:
    """当前最大的 MoDId（没有为 0）：被拒或预演之后不能有新的生产订单行。"""
    return f"{_HEAD}SELECT N'ROW:' + CONVERT(varchar(20), ISNULL(MAX(d.MoDId), 0)) AS v FROM dbo.mom_orderdetail AS d;\n"


def over_plan_sql() -> str:
    """U8「允许超计划下达」（mom_parameter.RlsOverPlanFlag，没有行或 NULL 按 1，同桥的 MoGenSql.OverSql）：1|0。"""
    return (
        f"{_HEAD}SELECT N'ROW:' + CONVERT(varchar(10), ISNULL((SELECT TOP (1) CONVERT(int, p.RlsOverPlanFlag)"
        " FROM dbo.mom_parameter AS p), 1)) AS v;\n"
    )


def other_line_sql(so: int) -> str:
    """别的销售订单里的一行（不在 so 上的来源行）：iSOsID。"""
    return (
        f"{_HEAD}SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), d.iSOsID) AS v FROM dbo.SO_SODetails AS d"
        f" WHERE d.ID <> {safe_id(str(so))} ORDER BY d.iSOsID DESC;\n"
    )


def new_mo_sql(after: str) -> str:
    """MoId 大于 after 的生产订单主键，按主键排。"""
    floor = safe_id(after) if after != "0" else "0"
    return f"{_HEAD}SELECT N'ROW:' + CONVERT(varchar(20), o.MoId) AS v FROM dbo.mom_order AS o WHERE o.MoId > {floor} ORDER BY o.MoId;\n"


MO_COLS = (
    "seq", "modid", "orderdid", "order_type", "ord_flag", "cbom", "so_type", "so_did", "so_code", "so_seq",
    "inv", "qty", "dept", "mo_type",
)


def mo_rows_sql(mo: int) -> str:
    """生产订单各行（按行号）：列见 MO_COLS。"""
    cols = _join(
        _txt("d.SortSeq"), _txt("d.MoDId"), _txt("d.OrderDId"), _txt("d.OrderType"), _txt("CONVERT(int, d.OrdFlag)"),
        _txt("d.CustBomId"), _txt("d.SoType"), _txt("d.SoDId"), _txt("d.SoCode"), _txt("d.SoSeq"),
        "RTRIM(d.InvCode)", _txt("d.Qty"), _txt("RTRIM(d.MDeptCode)"), _txt("RTRIM(t.MotypeCode)"),
    )
    return (
        f"{_HEAD}SELECT N'ROW:' + {cols} AS v FROM dbo.mom_orderdetail AS d"
        " LEFT JOIN dbo.mom_motype AS t ON t.MoTypeId = d.MoTypeId"
        f" WHERE d.MoId = {safe_id(str(mo))} ORDER BY d.SortSeq, d.MoDId;\n"
    )


def alloc_sql(mo: int) -> str:
    """生产订单子件：MoDId|子件存货|数量。"""
    return (
        f"{_HEAD}SELECT N'ROW:' + {_join(_txt('a.MoDId'), 'RTRIM(a.InvCode)', _txt('a.Qty'))} AS v"
        " FROM dbo.mom_moallocate AS a JOIN dbo.mom_orderdetail AS d ON d.MoDId = a.MoDId"
        f" WHERE d.MoId = {safe_id(str(mo))} ORDER BY a.MoDId, a.SortSeq, a.AllocateId;\n"
    )


def std_bom_sql(model: str) -> str:
    """模型和模型子件今天有效的已审核标准物料清单行：母件|子件|WIPType|选配|分子|分母（parse_bom）。"""
    code = safe_lot(model)
    under = (
        "SELECT c2.ComponentId FROM dbo.bom_opcomponent AS c2 JOIN dbo.bom_parent AS p2 ON p2.BomId = c2.BomId"
        f" JOIN dbo.bas_part AS m ON m.PartId = p2.ParentId WHERE m.InvCode = N'{code}'"
    )
    cols = _join(
        "RTRIM(pp.InvCode)", "RTRIM(cp.InvCode)", _txt("ISNULL(o.WIPType, 3)"), _txt("CONVERT(int, ISNULL(o.OptionalFlag, 0))"),
        _txt("c.BaseQtyN"), _txt("c.BaseQtyD"),
    )
    return (
        f"{_HEAD}SELECT N'ROW:' + {cols} AS v FROM dbo.bom_bom AS b"
        " JOIN dbo.bom_parent AS p ON p.BomId = b.BomId JOIN dbo.bas_part AS pp ON pp.PartId = p.ParentId"
        " JOIN dbo.bom_opcomponent AS c ON c.BomId = b.BomId JOIN dbo.bas_part AS cp ON cp.PartId = c.ComponentId"
        " LEFT JOIN dbo.bom_opcomponentopt AS o ON o.OptionsId = c.OptionsId"
        f" WHERE b.BomType = 1 AND b.Status = 3 AND (pp.InvCode = N'{code}' OR pp.PartId IN ({under}))"
        " AND b.VersionEffDate <= CONVERT(date, GETDATE()) AND b.VersionEndDate > CONVERT(date, GETDATE())"
        " ORDER BY pp.InvCode, c.SortSeq;\n"
    )


def cbom_rows_sql(cbom: int) -> str:
    """选配结果的子件行：子件|WIPType|分子|分母|pSequence（cbom_explosion）。"""
    cols = _join(
        "RTRIM(bp.InvCode)", _txt("ISNULL(c.WIPType, 3)"), _txt("c.BaseQtyN"), _txt("c.BaseQtyD"), _txt("ISNULL(c.PSequence, 0)")
    )
    return (
        f"{_HEAD}SELECT N'ROW:' + {cols} AS v FROM dbo.bom_cbomcomponent AS c"
        " JOIN dbo.bas_part AS bp ON bp.PartId = c.ComponentId"
        f" WHERE c.CustBomId = {safe_id(str(cbom))} ORDER BY c.Sequence;\n"
    )


def plain_line_sql(so: int) -> str:
    """别的已审核销售订单里一行未关闭、存货不是 ATO 模型的行：ID|iSOsID。"""
    return (
        f"{_HEAD}SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), m.ID) + N'|' + CONVERT(varchar(20), d.iSOsID) AS v"
        " FROM dbo.SO_SOMain AS m JOIN dbo.SO_SODetails AS d ON d.ID = m.ID"
        " JOIN dbo.Inventory AS i ON i.cInvCode = d.cInvCode"
        f" WHERE m.ID <> {safe_id(str(so))} AND {_AUDITED} AND ISNULL(d.cSCloser, N'') = N''"
        " AND ISNULL(i.bATOModel, 0) = 0 ORDER BY m.ID DESC, d.iSOsID;\n"
    )


def open_so_sql() -> str:
    """一张未审核（也没有变更）的销售订单和它的一行，存货是 ATO 模型的行优先：ID|iSOsID。"""
    return (
        f"{_HEAD}SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), m.ID) + N'|' + CONVERT(varchar(20), d.iSOsID) AS v"
        " FROM dbo.SO_SOMain AS m JOIN dbo.SO_SODetails AS d ON d.ID = m.ID"
        " JOIN dbo.Inventory AS i ON i.cInvCode = d.cInvCode"
        " WHERE ISNULL(m.cVerifier, N'') = N'' AND ISNULL(m.cChanger, N'') = N''"
        " ORDER BY CASE WHEN ISNULL(i.bATOModel, 0) = 1 THEN 0 ELSE 1 END, m.ID DESC, d.iSOsID;\n"
    )


def mo_gen_samples() -> list[tuple[str, str]]:
    return [
        ("mo-gen-rows", gen_rows_sql(100)),
        ("mo-gen-imo", imo_sql([100, 101])),
        ("mo-gen-new", new_mo_sql("100")),
        ("mo-gen-mo-rows", mo_rows_sql(100)),
        ("mo-gen-alloc", alloc_sql(100)),
        ("mo-gen-std-bom", std_bom_sql("91030004")),
        ("mo-gen-cbom", cbom_rows_sql(100)),
        ("mo-gen-plain-line", plain_line_sql(100)),
        ("mo-gen-open-so", open_so_sql()),
        ("mo-gen-max-modid", max_modid_sql()),
        ("mo-gen-over-plan", over_plan_sql()),
        ("mo-gen-other-line", other_line_sql(100)),
    ]
