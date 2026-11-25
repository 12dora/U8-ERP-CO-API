"""物料清单（bom）用例的挑单与回读 SQL。

BOMW_PACKS 进挑单，has_bomw / bomw_sql 进 stash / pred 的回读，bomw_samples() 进样例 SQL（接线在 wiring 汇总模块）。
标识符都是常量，id 只接受正整数再拼进 SQL；挑出的编码只进请求正文，不拼进 SQL。
"""

from __future__ import annotations

import re

from co.e2e.casespec.mo_close_sql import safe_id

_NUM = re.compile(r"^[0-9]{1,10}\Z")
_INT_MAX = 2147483647
_NO_FREE = " AND ".join(f"ISNULL({{a}}.bFree{i}, 0) = 0" for i in range(1, 11))
_PART_BASE = "COALESCE(" + ", ".join(f"NULLIF({{a}}.Free{i}, N'')" for i in range(1, 11)) + ") IS NULL"
_ALIVE = "({a}.dEDate IS NULL OR {a}.dEDate > DATEADD(day, 1, CONVERT(date, GETDATE())))"

# ---- 挑单 ----


def bomw_src_sql() -> str:
    # 母件：自制、允许 BOM 母件、没有自由项、编码不超过 12 位，最新版本已审核且是它唯一的最大版本，
    # 所有版本的生效日期都早于服务器昨天（新版本用远期生效日期，不会撞日期），没有未审核或停用的版本；
    # 从最新版本里按行号取 3 个不同的子件（允许 BOM 子件、没停用、没有自由项）；
    # 另取一个替代料存货：允许 BOM 子件、没停用、没有自由项、已在某张物料清单里做过子件，不是母件和这 3 个子件。
    # 母件|子件1|子件2|子件3|现有版本号|替代料|替代料 PartId|子件2 的 PartId（替换替代料时用子件2，修改时它的行已删）。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + RTRIM(bp.InvCode) + N'|' + RTRIM(k.c1) + N'|' + RTRIM(k.c2) + N'|' + RTRIM(k.c3)
  + N'|' + CONVERT(nvarchar(10), b.Version) + N'|' + RTRIM(sb.code) + N'|' + CONVERT(nvarchar(20), sb.part)
  + N'|' + CONVERT(nvarchar(20), (SELECT MIN(p2.PartId) FROM dbo.bas_part AS p2 WHERE p2.InvCode = k.c2
      AND {_PART_BASE.format(a="p2")})) AS v
FROM dbo.bom_bom AS b
JOIN dbo.bom_parent AS p ON p.BomId = b.BomId
JOIN dbo.bas_part AS bp ON bp.PartId = p.ParentId
JOIN dbo.Inventory AS i ON i.cInvCode = bp.InvCode
CROSS APPLY (
  SELECT MAX(CASE WHEN z.rn = 1 THEN z.code END) AS c1, MAX(CASE WHEN z.rn = 2 THEN z.code END) AS c2,
    MAX(CASE WHEN z.rn = 3 THEN z.code END) AS c3
  FROM (SELECT cp.InvCode AS code, ROW_NUMBER() OVER (ORDER BY MIN(c.SortSeq), cp.InvCode) AS rn
        FROM dbo.bom_opcomponent AS c
        JOIN dbo.bas_part AS cp ON cp.PartId = c.ComponentId
        JOIN dbo.Inventory AS ci ON ci.cInvCode = cp.InvCode
        WHERE c.BomId = b.BomId AND cp.InvCode <> bp.InvCode AND ci.bBomSub = 1 AND LEN(cp.InvCode) <= 12
          AND (ci.dEDate IS NULL OR ci.dEDate > DATEADD(day, 1, CONVERT(date, GETDATE())))
          AND {_NO_FREE.format(a="ci")}
        GROUP BY cp.InvCode) AS z
) AS k
CROSS APPLY (
  SELECT TOP (1) si.cInvCode AS code, sp.PartId AS part
  FROM dbo.Inventory AS si
  JOIN dbo.bas_part AS sp ON sp.InvCode = si.cInvCode AND {_PART_BASE.format(a="sp")}
  WHERE si.bBomSub = 1 AND LEN(si.cInvCode) <= 12 AND {_ALIVE.format(a="si")} AND {_NO_FREE.format(a="si")}
    AND si.cInvCode NOT IN (bp.InvCode, k.c1, k.c2, k.c3)
    AND EXISTS (SELECT 1 FROM dbo.bom_opcomponent AS oc WHERE oc.ComponentId = sp.PartId)
  ORDER BY si.cInvCode, sp.PartId
) AS sb
WHERE b.BomType = 1 AND b.Status = 3 AND i.bSelf = 1 AND i.bBomMain = 1 AND LEN(bp.InvCode) <= 12
  AND k.c3 IS NOT NULL
  AND (i.dEDate IS NULL OR i.dEDate > DATEADD(day, 1, CONVERT(date, GETDATE())))
  AND {_NO_FREE.format(a="i")}
  AND {_PART_BASE.format(a="bp")}
  AND NOT EXISTS (SELECT 1 FROM dbo.bom_bom AS b2 JOIN dbo.bom_parent AS p2 ON p2.BomId = b2.BomId
                  WHERE p2.ParentId = p.ParentId AND b2.BomId <> b.BomId
                    AND (b2.Version >= b.Version OR b2.Status <> 3
                      OR b2.VersionEffDate >= DATEADD(day, -1, CONVERT(date, GETDATE()))))
  AND b.VersionEffDate < DATEADD(day, -1, CONVERT(date, GETDATE()))
ORDER BY b.BomId DESC;
"""


# 挑单登记（同 returns_misc_sql.CUSTOM_PACKS 的形状）：前导键、按 | 拆开的键、是否必须是正整数 id、SQL。
BOMW_KEYS = ("bomw_parent", "bomw_c1", "bomw_c2", "bomw_c3", "bomw_ver", "bomw_sub", "bomw_subp", "bomw_c2p")
BOMW_PACKS = (("bomw_parent", BOMW_KEYS, (False,) * 6 + (True, True), bomw_src_sql),)


# ---- 回读（doc 是新建的 BomId）----


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _bomw_status(doc: str, _bag: dict[str, str]) -> str:
    # 版本状态：1 未审核、3 已审核（读不到时 0）。
    return _one(
        "CONVERT(varchar(10), ISNULL(MAX(b.Status), 0))",
        f"FROM dbo.bom_bom AS b WHERE b.BomId = {safe_id(doc)}",
    )


def _bomw_edited(doc: str, _bag: dict[str, str]) -> str:
    # 修改后：只剩两行，行 10 的用量分子是 2，行 20 已删，行 30 是新加的另一个子件。
    expr = (
        "CASE WHEN COUNT(*) = 2 AND COUNT(DISTINCT c.ComponentId) = 2"
        " AND SUM(CASE WHEN c.SortSeq = 10 AND c.BaseQtyN = 2 THEN 1 ELSE 0 END) = 1"
        " AND SUM(CASE WHEN c.SortSeq = 30 THEN 1 ELSE 0 END) = 1"
        " AND SUM(CASE WHEN c.SortSeq = 20 THEN 1 ELSE 0 END) = 0 THEN N'1' ELSE N'0' END"
    )
    return _one(expr, f"FROM dbo.bom_opcomponent AS c WHERE c.BomId = {safe_id(doc)}")


def _bomw_subs(part_key: str, seq: int, factor: int):
    # 替代料回读：整张清单只有一条替代料，挂在行 10 上，物料是 bag 里记下的 PartId，优先级、系数如给定，不替换。
    def build(doc: str, bag: dict[str, str]) -> str:
        part = _bag_num(bag, part_key)
        expr = (
            "CASE WHEN COUNT(*) = 1 AND SUM(CASE WHEN c.SortSeq = 10 AND s.PartId = " + part
            + f" AND s.[Sequence] = {seq} AND s.Factor = {factor} AND ISNULL(s.ReplaceFlag, 0) = 0"
            " THEN 1 ELSE 0 END) = 1 THEN N'1' ELSE N'0' END"
        )
        frm = (
            "FROM dbo.bom_opcomponentsub AS s JOIN dbo.bom_opcomponent AS c ON c.OpComponentId = s.OpComponentId"
            f" WHERE c.BomId = {safe_id(doc)}"
        )
        return _one(expr, frm)

    return build


def _bomw_subs_n(doc: str, _bag: dict[str, str]) -> str:
    # 整张清单的替代料条数（清空后应为 0）。
    frm = (
        "FROM dbo.bom_opcomponentsub AS s JOIN dbo.bom_opcomponent AS c ON c.OpComponentId = s.OpComponentId"
        f" WHERE c.BomId = {safe_id(doc)}"
    )
    return _one("CONVERT(varchar(10), COUNT(*))", frm)


def _bomw_locs(doc: str, _bag: dict[str, str]) -> str:
    # 整张清单的定位符：只在行 10 上、一条（重新加回后）时 1，否则 0。
    frm = (
        "FROM dbo.bom_opcomponentloc AS l JOIN dbo.bom_opcomponent AS c ON c.OpComponentId = l.OpComponentId"
        f" WHERE c.BomId = {safe_id(doc)}"
    )
    return _one("CASE WHEN COUNT(*) = 1 AND MIN(c.SortSeq) = 10 THEN N'1' ELSE N'0' END", frm)


def _bag_num(bag: dict[str, str], key: str) -> str:
    # stash 记下的主键（整数）；没记时按 0（范围为空）。
    raw = bag.get(key, "0")
    if _NUM.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError("回读键不合法：" + key)
    return raw


def _bomw_opts(agg: str, col: str = "OptionsId"):
    # 删除前记下子件选项行 OptionsId（或子件行 OpComponentId）的最小、最大值（ISNULL 为 0）。
    def build(doc: str, _bag: dict[str, str]) -> str:
        expr = f"CONVERT(varchar(20), ISNULL({agg}(c.{col}), 0))"
        return _one(expr, f"FROM dbo.bom_opcomponent AS c WHERE c.BomId = {safe_id(doc)}")

    return build


def _bomw_gone(doc: str, bag: dict[str, str]) -> str:
    # 删除后版本、母件、子件行都没有这个 BomId；删除前记下的子件选项行、子件行上的替代料和定位符也没有残留。
    bom = safe_id(doc)
    lo, hi = _bag_num(bag, "bomw_omin"), _bag_num(bag, "bomw_omax")
    clo, chi = _bag_num(bag, "bomw_cmin"), _bag_num(bag, "bomw_cmax")
    expr = (
        f"CONVERT(varchar(20), (SELECT COUNT(*) FROM dbo.bom_bom AS b WHERE b.BomId = {bom})"
        f" + (SELECT COUNT(*) FROM dbo.bom_parent AS p WHERE p.BomId = {bom})"
        f" + (SELECT COUNT(*) FROM dbo.bom_opcomponent AS c WHERE c.BomId = {bom})"
        f" + (SELECT COUNT(*) FROM dbo.bom_opcomponentopt AS o WHERE o.OptionsId BETWEEN {lo} AND {hi})"
        f" + (SELECT COUNT(*) FROM dbo.bom_opcomponentsub AS s WHERE s.OpComponentId BETWEEN {clo} AND {chi})"
        f" + (SELECT COUNT(*) FROM dbo.bom_opcomponentloc AS l WHERE l.OpComponentId BETWEEN {clo} AND {chi}))"
    )
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v;\n"


def _bomw_src(doc: str, bag: dict[str, str]) -> str:
    # 来源版本：与新建的版本同一母件、版本号是挑单时的最新已审核版本（bomw_ver）。读不到时 0。
    expr = "CONVERT(varchar(20), ISNULL(MAX(s.BomId), 0))"
    frm = (
        "FROM dbo.bom_parent AS p JOIN dbo.bom_parent AS sp ON sp.ParentId = p.ParentId"
        " JOIN dbo.bom_bom AS s ON s.BomId = sp.BomId"
        f" WHERE p.BomId = {safe_id(doc)} AND s.BomType = 1 AND s.Version = {_bag_num(bag, 'bomw_ver')}"
    )
    return _one(expr, frm)


def _bomw_end(_doc: str, bag: dict[str, str]) -> str:
    # 来源版本的失效日期，yyyymmdd 数字（没有时 0）；新建后 stash 成 bomw_send，删除后按它核对（same）。
    expr = "ISNULL(MAX(CONVERT(varchar(8), s.VersionEndDate, 112)), '0')"
    return _one(expr, f"FROM dbo.bom_bom AS s WHERE s.BomId = {_bag_num(bag, 'bomw_src')}")


_READBACK = {
    "bomw_status": _bomw_status,
    "bomw_edited": _bomw_edited,
    "bomw_gone": _bomw_gone,
    "bomw_omin": _bomw_opts("MIN"),
    "bomw_omax": _bomw_opts("MAX"),
    "bomw_cmin": _bomw_opts("MIN", "OpComponentId"),
    "bomw_cmax": _bomw_opts("MAX", "OpComponentId"),
    "bomw_loc_n": _bomw_locs,
    "bomw_src": _bomw_src,
    "bomw_send": _bomw_end,
    "bomw_src_end": _bomw_end,
    "bomw_sub_kept": _bomw_subs("bomw_subp", 1, 1),
    "bomw_sub_swap": _bomw_subs("bomw_c2p", 2, 2),
    "bomw_sub_n": _bomw_subs_n,
}
# 删除前 stash 的键（同名）：_bomw_gone 按它们查选项行残留。
BOMW_STASH = ("bomw_omin", "bomw_omax", "bomw_cmin", "bomw_cmax")
# 新建后、审核前 stash 的键（同名，按顺序）：来源版本的 BomId，再按它取失效日期。
BOMW_SRC_STASH = ("bomw_src", "bomw_send")


def has_bomw(name: str) -> bool:
    return name in _READBACK


def bomw_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def bomw_samples() -> list[tuple[str, str]]:
    rows = [("mfg-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in BOMW_PACKS]
    rows.extend(("mfg-" + name.replace("_", "-"), fn("100", {})) for name, fn in _READBACK.items())
    return rows
