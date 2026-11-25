"""生产订单修改（mo_update）的回读 SQL。挑单沿用 MOC_PACKS（moc_inv / moc_dept / moc_type），不另挑。

has_mou / mou_sql 进 stash / pred 的回读，mou_samples() 进样例 SQL（接线在 wiring 汇总模块）。
标识符都是常量，id 只接受正整数再拼进 SQL；doc 是新建的 MoId。
"""

from __future__ import annotations

from co.e2e.casespec.mo_update_subs_sql import MOS_READBACK, mos_samples
from co.e2e.casespec.mo_close_sql import safe_id
from co.e2e.casespec.mo_edit_sql import MOE_READBACK

# 修改用例写进备注的固定文本（正文构造器用同一个常量）。
MEMO_UPD = "u8co e2e 生产订单修改"
_ALLOC = "FROM dbo.mom_moallocate AS a JOIN dbo.mom_orderdetail AS d ON d.MoDId = a.MoDId WHERE d.MoId = {mo}"


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,6), ISNULL({expr}, 0)))"


def _line(doc: str, _bag: dict[str, str]) -> str:
    # 本用例只有一行：它的 MoDId（修改正文的 line_id）。
    return _one("CONVERT(varchar(20), MIN(d.MoDId))", f"FROM dbo.mom_orderdetail AS d WHERE d.MoId = {safe_id(doc)}")


def _line_qty(doc: str, _bag: dict[str, str]) -> str:
    return _one(_qty("SUM(d.Qty)"), f"FROM dbo.mom_orderdetail AS d WHERE d.MoId = {safe_id(doc)}")


def _acnt(doc: str, _bag: dict[str, str]) -> str:
    return _one("CONVERT(varchar(20), COUNT(*))", _ALLOC.format(mo=safe_id(doc)))


def _asum(doc: str, _bag: dict[str, str]) -> str:
    return _one(_qty("SUM(a.Qty)"), _ALLOC.format(mo=safe_id(doc)))


def _memo(doc: str, _bag: dict[str, str]) -> str:
    # 第一次修改后：全部行备注是 MEMO_UPD，完工日期比开工晚 3 天（新建时是 2 天），行仍未审核。
    expr = (
        "CASE WHEN COUNT(*) > 0 AND SUM(CASE WHEN d.Remark = N'" + MEMO_UPD + "'"
        " AND DATEDIFF(day, m.StartDate, m.DueDate) = 3 AND d.Status IN (1, 2) THEN 1 ELSE 0 END) = COUNT(*)"
        " THEN N'1' ELSE N'0' END"
    )
    frm = f"FROM dbo.mom_orderdetail AS d JOIN dbo.mom_morder AS m ON m.MoDId = d.MoDId WHERE d.MoId = {safe_id(doc)}"
    return _one(expr, frm)


def _scaled(doc: str, _bag: dict[str, str]) -> str:
    # 第二次修改（数量翻倍）后：每个子件数量 = 基本用量分子 / 分母 × 行数量，按账套存货数量小数位四舍五入。
    expr = (
        "CASE WHEN COUNT(*) > 0 AND SUM(CASE WHEN ABS(a.Qty - ROUND(a.BaseQtyN / NULLIF(a.BaseQtyD, 0) * d.Qty, g.dg))"
        " < 0.000001 THEN 1 ELSE 0 END) = COUNT(*) THEN N'1' ELSE N'0' END"
    )
    frm = (
        "FROM dbo.mom_moallocate AS a JOIN dbo.mom_orderdetail AS d ON d.MoDId = a.MoDId\n"
        "CROSS JOIN (SELECT ISNULL(MAX(CONVERT(int, o.cValue)), 6) AS dg FROM dbo.AccInformation AS o"
        " WHERE o.cSysID = N'AA' AND o.cName = N'iStrsQuanDecDgt') AS g\n"
        f"WHERE d.MoId = {safe_id(doc)}"
    )
    return _one(expr, frm)


# 账套的存货数量小数位 dg、件数小数位 nd（读不到按 6）。
_DIGITS = (
    "CROSS JOIN (SELECT ISNULL(MAX(CASE WHEN o.cName = N'iStrsQuanDecDgt' THEN CONVERT(int, o.cValue) END), 6) AS dg,"
    " ISNULL(MAX(CASE WHEN o.cName = N'iNumDecDgt' THEN CONVERT(int, o.cValue) END), 6) AS nd"
    " FROM dbo.AccInformation AS o WHERE o.cSysID = N'AA' AND o.cName IN (N'iStrsQuanDecDgt', N'iNumDecDgt')) AS g"
)
# 子件按 U8 Moallocate.SetQty 算的未舍入数量：变动用量（FVFlag=1）= 行数量 × 单位用量，固定用量 = 单位用量；
# 单位用量 = (有换算率 ? AuxBaseQtyN × 换算率 : BaseQtyN) / BaseQtyD × (1 + 子件损耗率/100)，
# 变动用量且不是返工订单（MoClass≠2）时再除以 (1 − 母件损耗率/100)。
_RAW = (
    "(CASE WHEN ISNULL(a.ChangeRate, 0) <> 0 THEN a.AuxBaseQtyN * a.ChangeRate ELSE a.BaseQtyN END)"
    " / NULLIF(a.BaseQtyD, 0) * (1 + ISNULL(a.CompScrap, 0) / 100)"
    " / (CASE WHEN a.FVFlag = 1 AND ISNULL(d.MoClass, 1) <> 2 THEN NULLIF(1 - ISNULL(a.ParentScrap, 0) / 100, 0) ELSE 1 END)"
    " * (CASE WHEN a.FVFlag = 1 THEN d.Qty ELSE 1 END)"
)
_ALLOC_G = "FROM dbo.mom_moallocate AS a JOIN dbo.mom_orderdetail AS d ON d.MoDId = a.MoDId\n" + _DIGITS + "\nWHERE d.MoId = {mo}"


def _all_rows(cond: str, doc: str) -> str:
    expr = f"CASE WHEN COUNT(*) > 0 AND SUM(CASE WHEN {cond} THEN 1 ELSE 0 END) = COUNT(*) THEN N'1' ELSE N'0' END"
    return _one(expr, _ALLOC_G.format(mo=safe_id(doc)))


def _prec(doc: str, _bag: dict[str, str]) -> str:
    # 每个子件数量的小数位不超过存货数量小数位，件数（有换算率时）不超过件数小数位。
    cond = (
        "a.Qty = ROUND(a.Qty, g.dg) AND (ISNULL(a.ChangeRate, 0) = 0 OR ISNULL(a.AuxQty, 0) = ROUND(ISNULL(a.AuxQty, 0), g.nd))"
    )
    return _all_rows(cond, doc)


def _formula(doc: str, _bag: dict[str, str]) -> str:
    # 每个子件数量与未舍入数量相差不超过半个最小单位（与 _prec 一起即「按存货数量小数位四舍五入」）；件数同理按件数小数位。
    cond = (
        f"ABS(a.Qty - {_RAW}) <= 0.5 * POWER(CONVERT(float, 10), -g.dg) + 0.000001"
        f" AND (ISNULL(a.ChangeRate, 0) = 0 OR ABS(ISNULL(a.AuxQty, 0) - {_RAW} / a.ChangeRate)"
        " <= 0.5 * POWER(CONVERT(float, 10), -g.nd) + 0.000001)"
    )
    return _all_rows(cond, doc)


def _mix(cond: str):
    # 本单子件里某一类的行数：固定用量、带损耗（子件或母件损耗率非 0）、有换算率。只记录，供判断这次覆盖了哪些情形。
    def build(doc: str, _bag: dict[str, str]) -> str:
        return _one(f"CONVERT(varchar(20), ISNULL(SUM(CASE WHEN {cond} THEN 1 ELSE 0 END), 0))", _ALLOC.format(mo=safe_id(doc)))

    return build


def _digest(doc: str, _bag: dict[str, str]) -> str:
    # 子件里 U8 重插会动、桥要写回或守住的列（不含数量；UpperMoQty 写回原值，所以在内）的校验和，改前改后应相同。非负整数。
    cols = (
        "a.SortSeq, a.InvCode, a.OpComponentId, a.VirOpComponentIds, a.cSubSysBarCode, a.SoDId, a.WhCode,"
        " a.BaseQtyN, a.BaseQtyD, a.EndDemDate, a.StartDemDate, a.UpperMoQty, a.ChangeRate, a.ParentScrap, a.QcFlag,"
        " a.Offset, a.SoType, a.SoCode, a.DemandCode, a.CostWIPRel, a.TransQty, a.RequisitionQty, a.CostItemCode, s.n"
    )
    expr = f"CONVERT(varchar(20), CONVERT(bigint, ISNULL(CHECKSUM_AGG(BINARY_CHECKSUM({cols})), 0)) + 2147483648)"
    frm = (
        "FROM dbo.mom_moallocate AS a JOIN dbo.mom_orderdetail AS d ON d.MoDId = a.MoDId\n"
        "OUTER APPLY (SELECT COUNT(*) AS n FROM dbo.mom_moallocatesub AS x WHERE x.AllocateId = a.AllocateId) AS s\n"
        f"WHERE d.MoId = {safe_id(doc)}"
    )
    return _one(expr, frm)


def _gone(doc: str, bag: dict[str, str]) -> str:
    # 删除后：表头、明细没有了，删除前记下的行（mou_line）下也没有子件。
    mo = safe_id(doc)
    raw = bag.get("mou_line", "")
    line = safe_id(raw) if raw not in ("", "0") else "0"
    expr = (
        f"CONVERT(varchar(20), (SELECT COUNT(*) FROM dbo.mom_order AS o WHERE o.MoId = {mo})"
        f" + (SELECT COUNT(*) FROM dbo.mom_orderdetail AS d WHERE d.MoId = {mo})"
        f" + (SELECT COUNT(*) FROM dbo.mom_moallocate AS a WHERE a.MoDId = {line}))"
    )
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v;\n"


_READBACK = {
    "mou_line": _line,
    "mou_qty": _line_qty,
    "mou_acnt": _acnt,
    "mou_asum": _asum,
    "mou_memo": _memo,
    "mou_scaled": _scaled,
    "mou_gone": _gone,
    "mou_digest": _digest,
    "mou_prec": _prec,
    "mou_formula": _formula,
    "mou_nfixed": _mix("a.FVFlag <> 1"),
    "mou_nscrap": _mix("(ISNULL(a.CompScrap, 0) <> 0 OR ISNULL(a.ParentScrap, 0) <> 0)"),
    "mou_naux": _mix("ISNULL(a.ChangeRate, 0) <> 0"),
}
# 已审核 / 已领料的修改（mo_edit）的回读也从这里接线。
_READBACK.update(MOE_READBACK)
# 保留替代料（mo_update_subs）的回读同样接在这里。
_READBACK.update(MOS_READBACK)


def has_mou(name: str) -> bool:
    return name in _READBACK


def mou_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def mou_samples() -> list[tuple[str, str]]:
    return [("more-" + name.replace("_", "-"), fn("100", {})) for name, fn in _READBACK.items()] + mos_samples()
