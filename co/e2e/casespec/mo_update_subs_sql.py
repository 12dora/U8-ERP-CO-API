"""生产订单修改保留替代料（mo_update_subs）的挑单与回读 SQL。

挑单同 moc_src_sql，另要求标准 BOM 至少有一个子件带替代料（bom_opcomponentsub），键 mos_*，不写死编码。
MOS_PACKS 进挑单（wiring），MOS_READBACK 并进 mo_update_sql 的 _READBACK（stash / pred 和样例 SQL），
挑单样例 mos_samples 随 mou_samples。
标识符都是常量，id 只接受正整数再拼进 SQL；doc 是新建的 MoId。
只读，mos_seed 除外：本用例新建的订单上一行替代料都没有时，照 BOM 给它的一个子件插一行（只动这张订单）。
"""

from __future__ import annotations

from co.e2e.casespec.mo_close_sql import safe_id
from co.e2e.casespec.mo_create_sql import moc_src_sql

_HAS_SUBS = (
    "  AND EXISTS (SELECT 1 FROM dbo.bom_opcomponent AS c JOIN dbo.bom_opcomponentsub AS x"
    " ON x.OpComponentId = c.OpComponentId WHERE c.BomId = b.BomId)\n"
)


def mos_src_sql() -> str:
    return moc_src_sql(_HAS_SUBS)


# 挑单登记（同 MOC_PACKS 的形状）：前导键、按 | 拆开的键、是否必须是正整数 id、SQL。
MOS_KEYS = ("mos_inv", "mos_dept", "mos_type", "mos_bom_from", "mos_bom_to")
MOS_PACKS = (("mos_inv", MOS_KEYS, (False,) * len(MOS_KEYS), mos_src_sql),)

# 本单子件上的替代料。
_SUBS = (
    "FROM dbo.mom_moallocatesub AS s JOIN dbo.mom_moallocate AS a ON a.AllocateId = s.AllocateId"
    " JOIN dbo.mom_orderdetail AS d ON d.MoDId = a.MoDId"
)
# 修改前后应相同的替代料内容（不含主键 MoallocateSubId 和子件 AllocateId）。
_SUB_COLS = (
    "a.SortSeq, a.InvCode, s.[Sequence], s.PartId, s.Factor, s.EffBegDate, s.EffEndDate, s.ReplaceFlag,"
    " s.Define22, s.Define23, s.Define24, s.Define25, s.Define26, s.Define27, s.Define28, s.Define29, s.Define30,"
    " s.Define31, s.Define32, s.Define33, s.Define34, s.Define35, s.Define36, s.Define37"
)


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _scnt(doc: str, _bag: dict[str, str]) -> str:
    # 本单替代料行数。
    return _one("CONVERT(varchar(20), COUNT(*))", _SUBS + f" WHERE d.MoId = {safe_id(doc)}")


def _digest(doc: str, _bag: dict[str, str]) -> str:
    # 替代料内容的校验和（非负整数），改前改后应相同。
    expr = f"CONVERT(varchar(20), CONVERT(bigint, ISNULL(CHECKSUM_AGG(BINARY_CHECKSUM({_SUB_COLS})), 0)) + 2147483648)"
    return _one(expr, _SUBS + f" WHERE d.MoId = {safe_id(doc)}")


def _amax(doc: str, _bag: dict[str, str]) -> str:
    # 本单子件的最大 AllocateId（修改前记下，修改后的替代料应全在比它大的新子件上）。
    frm = f"FROM dbo.mom_moallocate AS a JOIN dbo.mom_orderdetail AS d ON d.MoDId = a.MoDId WHERE d.MoId = {safe_id(doc)}"
    return _one("CONVERT(varchar(20), ISNULL(MAX(a.AllocateId), 0))", frm)


def _moved(doc: str, bag: dict[str, str]) -> str:
    # 修改后：有替代料，且全部挂在 AllocateId 大于修改前最大值的新子件上（U8 重插了子件，桥写回到新行）。
    raw = bag.get("mos_amax", "") or "0"
    amax = "0" if raw == "0" else safe_id(raw)
    expr = f"CASE WHEN COUNT(*) > 0 AND MIN(s.AllocateId) > {amax} THEN N'1' ELSE N'0' END"
    return _one(expr, _SUBS + f" WHERE d.MoId = {safe_id(doc)}")


def _seed(doc: str, _bag: dict[str, str]) -> str:
    # 夹具：新建后订单上没有替代料（U8 新增没有按 BOM 带出）时，取本单第一个在 BOM 上有替代料的子件，照 BOM 插一行，
    # 保证修改路径总有替代料可保留。返回之后本单的替代料行数。主键 MoallocateSubId 没有缺省值，显式 NEWID()。
    mo = safe_id(doc)
    where = f"WHERE d.MoId = {mo}"
    return (
        "SET NOCOUNT ON;\n"
        f"IF NOT EXISTS (SELECT 1 {_SUBS} {where})\n"
        "INSERT INTO dbo.mom_moallocatesub (MoallocateSubId, AllocateId, [Sequence], PartId, Factor, EffBegDate, EffEndDate,"
        " ReplaceFlag)\n"
        "SELECT TOP (1) NEWID(), a.AllocateId, x.[Sequence], x.PartId, x.Factor, x.EffBegDate, x.EffEndDate, x.ReplaceFlag\n"
        "FROM dbo.mom_moallocate AS a JOIN dbo.mom_orderdetail AS d ON d.MoDId = a.MoDId\n"
        "JOIN dbo.bom_opcomponentsub AS x ON x.OpComponentId = a.OpComponentId\n"
        f"{where} ORDER BY a.AllocateId, x.[Sequence];\n"
        f"SELECT N'ROW:' + CONVERT(varchar(20), COUNT(*)) AS v\n{_SUBS} {where};\n"
    )


def _bom_match(doc: str, _bag: dict[str, str]) -> str:
    # 新增后：U8 展开子件时带出了替代料，且每一行都是该子件 OpComponentId 在 BOM 上的替代料（同一替代存货）。
    expr = "CASE WHEN COUNT(*) > 0 AND COUNT(b.ComponentSubId) = COUNT(*) THEN N'1' ELSE N'0' END"
    frm = (
        _SUBS + "\nOUTER APPLY (SELECT TOP (1) x.ComponentSubId FROM dbo.bom_opcomponentsub AS x"
        " WHERE x.OpComponentId = a.OpComponentId AND x.PartId = s.PartId) AS b\n"
        f"WHERE d.MoId = {safe_id(doc)}"
    )
    return _one(expr, frm)


MOS_READBACK = {
    "mos_scnt": _scnt,
    # 新建后（夹具之前）的替代料行数：只记录，看 U8 新增是否按 BOM 带出了替代料。
    "mos_created": _scnt,
    "mos_seed": _seed,
    "mos_digest": _digest,
    "mos_amax": _amax,
    "mos_moved": _moved,
    "mos_bom_match": _bom_match,
}


def mos_samples() -> list[tuple[str, str]]:
    return [("mos-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in MOS_PACKS]
