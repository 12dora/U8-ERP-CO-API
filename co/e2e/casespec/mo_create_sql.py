"""生产订单新增 / 删除用例的挑单与回读 SQL。

MOC_PACKS 进挑单，has_moc / moc_sql 进 stash / pred 的回读，moc_samples() 进样例 SQL（接线在 wiring 汇总模块）。
标识符都是常量，id 只接受正整数再拼进 SQL；挑出的编码只进请求正文，不拼进 SQL。
"""

from __future__ import annotations

import re

from co.e2e.casespec.mo_close_sql import safe_id

_NUM = re.compile(r"^[0-9]{1,10}\Z")
_INT_MAX = 2147483647

# ---- 挑单 ----


def moc_src_sql(extra: str = "") -> str:
    # extra：附加的 BOM 条件（以 AND 开头、换行结尾的常量，如 mo_update_subs 要求子件有替代料）。
    # 有已审核标准 BOM（至少一个子件）的自制件（不是 ATO 模型、模型：新增生产订单 U8 拒绝），没有自由项、编码不超过 12 位（挑单编码的格式限制）；
    # 最近一张生产订单用过的末级生产部门；第一个生产订单类别。存货|部门|类别|BOM 生效日|BOM 失效日，都不写死。
    # 挑单拿不到 env.today，这里要求 BOM 在服务器今天前后各一天都有效（容下本机与服务器的日期差），
    # 并带回有效期，正文构造器（mo_create.moc_new）再按 env.today（开工日期）核对。
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + RTRIM(bp.InvCode) + N'|' + RTRIM(dep.MDeptCode) + N'|' + RTRIM(t.MotypeCode)
  + N'|' + CONVERT(nvarchar(10), b.VersionEffDate, 23) + N'|' + CONVERT(nvarchar(10), b.VersionEndDate, 23) AS v
FROM dbo.bom_bom AS b
JOIN dbo.bom_parent AS p ON p.BomId = b.BomId
JOIN dbo.bas_part AS bp ON bp.PartId = p.ParentId
JOIN dbo.Inventory AS i ON i.cInvCode = bp.InvCode
CROSS JOIN (SELECT TOP (1) d.MDeptCode FROM dbo.mom_orderdetail AS d
            JOIN dbo.Department AS dp ON dp.cDepCode = d.MDeptCode
            WHERE dp.bDepEnd = 1 AND LEN(d.MDeptCode) <= 12 ORDER BY d.MoDId DESC) AS dep
CROSS JOIN (SELECT TOP (1) m.MotypeCode FROM dbo.mom_motype AS m ORDER BY m.MoTypeId) AS t
WHERE b.BomType = 1 AND b.Status = 3 AND i.bSelf = 1
  AND ISNULL(i.bATOModel, 0) = 0 AND ISNULL(i.bInvModel, 0) = 0
  AND b.VersionEffDate <= DATEADD(day, -1, CONVERT(date, GETDATE()))
  AND b.VersionEndDate >= DATEADD(day, 3, CONVERT(date, GETDATE()))
  AND (i.dEDate IS NULL OR i.dEDate > DATEADD(day, 1, CONVERT(date, GETDATE())))
  AND ISNULL(i.bFree1, 0) = 0 AND ISNULL(i.bFree2, 0) = 0 AND ISNULL(i.bFree3, 0) = 0 AND ISNULL(i.bFree4, 0) = 0
  AND ISNULL(i.bFree5, 0) = 0 AND ISNULL(i.bFree6, 0) = 0 AND ISNULL(i.bFree7, 0) = 0 AND ISNULL(i.bFree8, 0) = 0
  AND ISNULL(i.bFree9, 0) = 0 AND ISNULL(i.bFree10, 0) = 0
  AND LEN(bp.InvCode) <= 12
  AND EXISTS (SELECT 1 FROM dbo.bom_opcomponent AS c WHERE c.BomId = b.BomId)
""" + extra + """ORDER BY b.BomId DESC;
"""


# 挑单登记（同 returns_misc_sql.CUSTOM_PACKS 的形状）：前导键、按 | 拆开的键、是否必须是正整数 id、SQL。
MOC_KEYS = ("moc_inv", "moc_dept", "moc_type", "moc_bom_from", "moc_bom_to")
MOC_PACKS = (("moc_inv", MOC_KEYS, (False,) * len(MOC_KEYS), moc_src_sql),)


# ---- 回读（doc 是新建的 MoId）----


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _moc_open(doc: str, _bag: dict[str, str]) -> str:
    # 新建后（及弃审后）：全部行未审核（Status 1 或 2，U8API 新增的是 2）。
    expr = (
        "CASE WHEN COUNT(*) > 0 AND SUM(CASE WHEN d.Status IN (1, 2) THEN 1 ELSE 0 END) = COUNT(*)"
        " THEN N'1' ELSE N'0' END"
    )
    return _one(expr, f"FROM dbo.mom_orderdetail AS d WHERE d.MoId = {safe_id(doc)}")


def _moc_alloc(doc: str, _bag: dict[str, str]) -> str:
    # U8 按标准 BOM 展开的子件行数。
    frm = f"FROM dbo.mom_moallocate AS a JOIN dbo.mom_orderdetail AS d ON d.MoDId = a.MoDId WHERE d.MoId = {safe_id(doc)}"
    return _one("CONVERT(varchar(20), COUNT(*))", frm)


def _bag_num(bag: dict[str, str], key: str) -> str:
    # stash 记下的主键（整数）；没记时按 0（范围为空）。
    raw = bag.get(key, "0")
    if _NUM.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError("回读键不合法：" + key)
    return raw


def _moc_ids(what: str):
    # 删除前记下的主键：明细 MoDId、子件 AllocateId 的最小、最大值（ISNULL 为 0）。
    col, frm = {
        "d": ("d.MoDId", "FROM dbo.mom_orderdetail AS d WHERE d.MoId = {mo}"),
        "a": ("a.AllocateId", "FROM dbo.mom_moallocate AS a JOIN dbo.mom_orderdetail AS d ON d.MoDId = a.MoDId WHERE d.MoId = {mo}"),
    }[what[0]]
    agg = "MIN" if what.endswith("min") else "MAX"

    def build(doc: str, _bag: dict[str, str]) -> str:
        return _one(f"CONVERT(varchar(20), ISNULL({agg}({col}), 0))", frm.format(mo=safe_id(doc)))

    return build


def _moc_gone(doc: str, bag: dict[str, str]) -> str:
    # 删除后表头、明细、日程（mom_morder）都没有这张订单，删除前记下的明细（本用例只有一行，min = max）
    # 和子件主键范围在 mom_moallocate、mom_moallocatesub 里也没有残留。
    mo = safe_id(doc)
    d_lo, d_hi = _bag_num(bag, "moc_dmin"), _bag_num(bag, "moc_dmax")
    a_lo, a_hi = _bag_num(bag, "moc_amin"), _bag_num(bag, "moc_amax")
    expr = (
        f"CONVERT(varchar(20), (SELECT COUNT(*) FROM dbo.mom_order AS o WHERE o.MoId = {mo})"
        f" + (SELECT COUNT(*) FROM dbo.mom_orderdetail AS d WHERE d.MoId = {mo})"
        f" + (SELECT COUNT(*) FROM dbo.mom_morder AS m WHERE m.MoId = {mo})"
        f" + (SELECT COUNT(*) FROM dbo.mom_moallocate AS a WHERE a.MoDId IN ({d_lo}, {d_hi})"
        f" OR a.AllocateId BETWEEN {a_lo} AND {a_hi})"
        f" + (SELECT COUNT(*) FROM dbo.mom_moallocatesub AS s WHERE s.AllocateId BETWEEN {a_lo} AND {a_hi}))"
    )
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v;\n"


_READBACK = {
    "moc_open": _moc_open,
    "moc_alloc": _moc_alloc,
    "moc_gone": _moc_gone,
    "moc_dmin": _moc_ids("dmin"),
    "moc_dmax": _moc_ids("dmax"),
    "moc_amin": _moc_ids("amin"),
    "moc_amax": _moc_ids("amax"),
}
# 删除前 stash 的键（同名）：_moc_gone 按它们查子件残留。
MOC_STASH = ("moc_dmin", "moc_dmax", "moc_amin", "moc_amax")


def has_moc(name: str) -> bool:
    return name in _READBACK


def moc_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def moc_samples() -> list[tuple[str, str]]:
    rows = [("mfg-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in MOC_PACKS]
    rows.extend(("mfg-" + name.replace("_", "-"), fn("100", {})) for name, fn in _READBACK.items())
    return rows
