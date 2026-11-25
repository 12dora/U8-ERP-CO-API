"""已审核 / 已领料生产订单修改（mo_edit）的回读 SQL；经 mo_update_sql 的 _READBACK 接线，不另登记。

doc 是新建的 MoId。标识符都是常量，id 只接受正整数再拼进 SQL；挑出的仓库编码只进 bag 和请求正文，不拼进 SQL。
bag 只收数字（preds.store_cell），所以仓库编码按字符 ASCII 码每 3 位一组写进 bag（编码只含 0-9A-Za-z._-），
正文构造器 mo_edit.wh_decode 还原。
"""

from __future__ import annotations

from co.e2e.casespec.mo_close_sql import safe_id

_ALLOC = "FROM dbo.mom_moallocate AS a JOIN dbo.mom_orderdetail AS d ON d.MoDId = a.MoDId WHERE d.MoId = {mo}"
_ALL = "CASE WHEN COUNT(*) > 0 AND SUM(CASE WHEN {cond} THEN 1 ELSE 0 END) = COUNT(*) THEN N'1' ELSE N'0' END"
# 可领料的子件：领用（WIPType=3）、普通件、不是产出品、变动用量（改行数量时子件需求跟着变），存货不批次、无计量单位组；
# 仓库取子件上填的，没填取该存货现存量最多的不管货位、未冻结的仓库，现存量要够领完整个子件数量。
_PICK = (
    "FROM dbo.mom_moallocate AS a JOIN dbo.mom_orderdetail AS d ON d.MoDId = a.MoDId\n"
    "JOIN dbo.Inventory AS i ON i.cInvCode = a.InvCode\n"
    "CROSS APPLY (SELECT TOP (1) LTRIM(RTRIM(c.cWhCode)) AS code, SUM(ISNULL(c.iQuantity, 0)) AS qty\n"
    "  FROM dbo.CurrentStock AS c JOIN dbo.Warehouse AS cw ON cw.cWhCode = c.cWhCode\n"
    "  WHERE c.cInvCode = a.InvCode AND cw.bWhPos = 0 AND ISNULL(cw.bFreeze, 0) = 0\n"
    "    AND (NULLIF(LTRIM(RTRIM(a.WhCode)), N'') IS NULL OR c.cWhCode = a.WhCode)\n"
    "    AND LEN(LTRIM(RTRIM(c.cWhCode))) BETWEEN 1 AND 20 AND LTRIM(RTRIM(c.cWhCode)) NOT LIKE N'%[^0-9A-Za-z._-]%'\n"
    "  GROUP BY c.cWhCode ORDER BY SUM(ISNULL(c.iQuantity, 0)) DESC, c.cWhCode) AS w\n"
    "WHERE d.MoId = {mo} AND a.WIPType = 3 AND a.ProductType = 1 AND ISNULL(a.ByproductFlag, 0) = 0 AND a.FVFlag = 1\n"
    "  AND ISNULL(a.Qty, 0) > 0 AND w.qty >= a.Qty AND ISNULL(i.bInvBatch, 0) = 0 AND ISNULL(i.iGroupType, 0) = 0"
)
# 仓库编码 → 每字符 3 位 ASCII 码（最多 20 个字符）。
_WH_DIGITS = (
    "CONVERT(varchar(80), (SELECT RIGHT('000' + CONVERT(varchar(3), ASCII(SUBSTRING(w.code, n.n, 1))), 3)"
    " FROM (VALUES (1), (2), (3), (4), (5), (6), (7), (8), (9), (10), (11), (12), (13), (14), (15), (16), (17), (18),"
    " (19), (20)) AS n(n) WHERE n.n <= LEN(w.code) ORDER BY n.n FOR XML PATH('')))"
)


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,6), ISNULL({expr}, 0)))"


def _bag_id(bag: dict[str, str], key: str) -> str:
    raw = bag.get(key, "")
    return safe_id(raw) if raw not in ("", "0") else "0"


def _pick(expr: str):
    # 挑本单第一个可领料的子件（按行号）：主键、数量、仓库（编码后）三条 SQL 用同一套条件和顺序。
    def build(doc: str, _bag: dict[str, str]) -> str:
        frm = _PICK.format(mo=safe_id(doc))
        return f"SET NOCOUNT ON;\nSELECT TOP (1) N'ROW:' + {expr} AS v\n{frm}\nORDER BY a.SortSeq, a.AllocateId;\n"

    return build


def _iss(doc: str, _bag: dict[str, str]) -> str:
    # 本单全部子件已领数量合计。
    return _one(_qty("SUM(a.IssQty)"), _ALLOC.format(mo=safe_id(doc)))


def _iss0(doc: str, _bag: dict[str, str]) -> str:
    # 材料出库删掉之后：全部子件已领数量回到 0（U8 按出库行的 iMPoIds 回退，指向新子件才退得回来）。
    expr = "CASE WHEN COUNT(*) > 0 AND SUM(ABS(ISNULL(a.IssQty, 0))) = 0 THEN N'1' ELSE N'0' END"
    return _one(expr, _ALLOC.format(mo=safe_id(doc)))


def _verified(doc: str, _bag: dict[str, str]) -> str:
    # 修改后全部行仍是已审核（Status 3）且有审核人。
    cond = "d.Status = 3 AND NULLIF(LTRIM(RTRIM(d.RelsUser)), N'') IS NOT NULL"
    return _one(_ALL.format(cond=cond), f"FROM dbo.mom_orderdetail AS d WHERE d.MoId = {safe_id(doc)}")


def _maxid(doc: str, _bag: dict[str, str]) -> str:
    return _one("CONVERT(varchar(20), MAX(a.AllocateId))", _ALLOC.format(mo=safe_id(doc)))


def _newids(doc: str, bag: dict[str, str]) -> str:
    # 修改后子件全部是重插的新行：最小 AllocateId 大于修改前的最大值（mou_maxid）。
    old = _bag_id(bag, "mou_maxid")
    expr = f"CASE WHEN COUNT(*) > 0 AND MIN(a.AllocateId) > {old} THEN N'1' ELSE N'0' END"
    return _one(expr, _ALLOC.format(mo=safe_id(doc)))


def _link(prefix: str):
    # 修改后：材料出库单（bag <prefix>out）的每一行都指向本单的新子件（不是领料时的 <prefix>aid）、存货相同。
    def build(doc: str, bag: dict[str, str]) -> str:
        mo, out, aid = safe_id(doc), _bag_id(bag, prefix + "out"), _bag_id(bag, prefix + "aid")
        cond = f"a.AllocateId IS NOT NULL AND a.AllocateId <> {aid} AND a.InvCode = r.cInvCode"
        frm = (
            "FROM dbo.rdrecords11 AS r\nLEFT JOIN dbo.mom_moallocate AS a ON a.AllocateId = r.iMPoIds\n"
            f"  AND a.MoDId IN (SELECT d.MoDId FROM dbo.mom_orderdetail AS d WHERE d.MoId = {mo})\nWHERE r.ID = {out}"
        )
        return _one(_ALL.format(cond=cond), frm)

    return build


def _keep(prefix: str):
    # 领料后、或修改被拒之后：材料出库单的每一行仍指向领料时的子件 <prefix>aid。
    def build(_doc: str, bag: dict[str, str]) -> str:
        out, aid = _bag_id(bag, prefix + "out"), _bag_id(bag, prefix + "aid")
        return _one(_ALL.format(cond=f"r.iMPoIds = {aid}"), f"FROM dbo.rdrecords11 AS r WHERE r.ID = {out}")

    return build


MOE_READBACK = {
    "mou_pick_aid": _pick("CONVERT(varchar(20), a.AllocateId)"),
    "mou_pick_qty": _pick(_qty("a.Qty")),
    "mou_pick_wh": _pick(_WH_DIGITS),
    "mou_iss": _iss,
    "mou_iss0": _iss0,
    "mou_verified": _verified,
    "mou_maxid": _maxid,
    "mou_newids": _newids,
    "moi_link": _link("moi_"),
    "moi_keep": _keep("moi_"),
    "mob_keep": _keep("mob_"),
}
