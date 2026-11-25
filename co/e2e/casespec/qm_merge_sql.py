"""产品检验单合并检验用例（qm_merge）的挑单与回读 SQL。

只读；id 只接受正整数（safe_id）再拼进 SQL，挑出的编码只进请求正文。挑单返回一行 ROW:a|b|…，挑不到返回空。
"""

from __future__ import annotations

from co.e2e.casespec.mo_close_sql import safe_id
from co.e2e.casespec.qm_write_sql import _checker, _scheme


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({expr}, 0)))"


def _open(alias: str) -> str:
    return (
        f"{alias}.Status = 3 AND {alias}.CloseDate IS NULL AND {alias}.QcFlag = 1"
        f" AND ISNULL({alias}.Qty, 0) - ISNULL({alias}.DeclaredQty, 0) >= 1"
    )


def _same_free() -> str:
    # 缺省合并规则 0：自由项相同。
    return " AND ".join(f"ISNULL(a.Free{i}, N'') = ISNULL(b.Free{i}, N'')" for i in range(1, 11))


def pick_sql() -> str:
    # 两条已审核、未关闭、要检验、剩余可报检 >= 1 的生产订单行：同一存货、同一辅计量单位、同自由项（合并检验的缺省依据），
    # 存货不管批次；该存货有可用的产品检验方案，第一条行的生产部门里有在职人员（检验员）。
    # 订单 A MoId|行 A MoDId|订单 B MoId|行 B MoDId|检验方案编码|检验员编码。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), a.MoId) + N'|' + CONVERT(varchar(20), a.MoDId)
  + N'|' + CONVERT(varchar(20), b.MoId) + N'|' + CONVERT(varchar(20), b.MoDId) + N'|' + pr.code + N'|' + ps.code AS v
FROM dbo.mom_orderdetail AS a
JOIN dbo.mom_orderdetail AS b
  ON b.InvCode = a.InvCode AND b.MoDId < a.MoDId AND ISNULL(b.AuxUnitCode, N'') = ISNULL(a.AuxUnitCode, N'')
JOIN dbo.Inventory AS i ON i.cInvCode = a.InvCode
{_scheme("QM04", "a.InvCode")}
{_checker("a.MDeptCode")}
WHERE {_open("a")} AND {_open("b")} AND ISNULL(i.bInvBatch, 0) = 0 AND {_same_free()}
ORDER BY a.MoDId DESC, b.MoDId DESC;
"""


def line_sql(inspect: str) -> str:
    """报检单（本用例一张一行）的表体 AUTOID。"""
    return _one("CONVERT(varchar(20), MIN(s.AUTOID))", f"FROM dbo.QMINSPECTVOUCHERS AS s WHERE s.ID = {safe_id(inspect)}")


def sources_sql(first: str, second: str) -> str:
    """两条来源报检行：累计检验数量|检验完毕标志|累计检验数量|检验完毕标志。"""

    def part(line: str) -> str:
        ref = safe_id(line)
        return (
            _qty(f"(SELECT MAX(s.FSUMCHECKQTY) FROM dbo.QMINSPECTVOUCHERS AS s WHERE s.AUTOID = {ref})")
            + " + N'|' + CONVERT(varchar(5), ISNULL((SELECT MAX(CONVERT(int, s.BFLAG)) FROM dbo.QMINSPECTVOUCHERS AS s"
            + f" WHERE s.AUTOID = {ref}), 0))"
        )

    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + part(first) + " + N'|' + " + part(second) + " AS v;\n"


def merged_sql(check: str, first: str, second: str) -> str:
    """合并检验单：BMERGECHECKFLAG|合并明细行数|明细数量合计|表头数量|指向两条来源的明细行数。"""
    ref = safe_id(check)
    lines = f"{safe_id(first)}, {safe_id(second)}"
    expr = (
        "CONVERT(varchar(5), ISNULL(MAX(CONVERT(int, q.BMERGECHECKFLAG)), 0))"
        f" + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.QMMergeCheckDetail AS m WHERE m.ID = {ref}))"
        f" + N'|' + {_qty(f'(SELECT SUM(m.FQUANTITY) FROM dbo.QMMergeCheckDetail AS m WHERE m.ID = {ref})')}"
        f" + N'|' + {_qty('MAX(q.FQUANTITY)')}"
        " + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.QMMergeCheckDetail AS m"
        f" WHERE m.ID = {ref} AND m.INSPECTAUTOID IN ({lines})))"
    )
    return _one(expr, f"FROM dbo.QMCHECKVOUCHER AS q WHERE q.ID = {ref}")


def gone_sql(check: str) -> str:
    """删除后：检验单表头、检验项目、合并明细还剩几行（应为 0）。"""
    ref = safe_id(check)
    expr = (
        f"CONVERT(varchar(20), (SELECT COUNT(*) FROM dbo.QMCHECKVOUCHER AS h WHERE h.ID = {ref})"
        f" + (SELECT COUNT(*) FROM dbo.QMCHECKVOUCHERS AS s WHERE s.ID = {ref})"
        f" + (SELECT COUNT(*) FROM dbo.QMMergeCheckDetail AS m WHERE m.ID = {ref}))"
    )
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v;\n"


def qm_merge_samples() -> list[tuple[str, str]]:
    return [
        ("qm-merge-pick", pick_sql()),
        ("qm-merge-line", line_sql("100")),
        ("qm-merge-sources", sources_sql("100", "101")),
        ("qm-merge-merged", merged_sql("200", "100", "101")),
        ("qm-merge-gone", gone_sql("200")),
    ]
