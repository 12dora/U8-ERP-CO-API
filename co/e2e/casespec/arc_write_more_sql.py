"""档案写入用例（arc_write_more）的挑选 SQL。标识符是常量；挑出来的值只收字母数字（SQL 里过滤，Python 再核一遍）。

每条 SQL 输出一行 ROW:<值>|<值>，挑不到就没有 ROW 行（用例记 N/A）。
"""

from __future__ import annotations

import re

_SAFE = re.compile(r"^[0-9A-Za-z]{1,60}\Z")
_ALNUM = "NOT LIKE N'%[^0-9A-Za-z]%'"
# 货位一级编码的候选（编码方案第一级 2 位时）：Z0..Z9、ZA..ZZ，挑第一个空闲的。
_POS_CANDIDATES = [f"Z{c}" for c in "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"]


def safe_part(text: str) -> str:
    if _SAFE.fullmatch(text or "") is None:
        raise ValueError("挑出的编码不合法")
    return text


def _position() -> str:
    # 启用货位管理的仓库 + 空闲的一级货位编码；编码方案第一级不是 2 位时挑不出（N/A）。
    values = ", ".join(f"({i}, N'{code}')" for i, code in enumerate(_POS_CANDIDATES))
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(w.cWhCode)) + N'|' + c.code AS v
FROM dbo.Warehouse AS w
CROSS JOIN (VALUES {values}) AS c(n, code)
WHERE w.bWhPos = 1 AND LTRIM(RTRIM(w.cWhCode)) {_ALNUM}
  AND NOT EXISTS (SELECT 1 FROM dbo.Position AS p WHERE p.cPosCode = c.code)
  AND EXISTS (SELECT 1 FROM dbo.GradeDef_Base AS g
              WHERE g.KEYWORD = N'positionclass' AND g.iYear = 0 AND LEFT(g.CODINGRULE, 1) = N'2')
ORDER BY c.n, w.cWhCode;
"""


def _unit() -> str:
    # 优先固定换算组（已有主计量单位，新单位是辅计量单位），没有就用无换算组。输出 组编码|换算类型。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(g.cGroupCode)) + N'|' + CONVERT(varchar(3), g.iGroupType) AS v
FROM dbo.ComputationGroup AS g
WHERE LTRIM(RTRIM(g.cGroupCode)) {_ALNUM}
  AND (g.iGroupType = 0 OR (g.iGroupType = 1 AND EXISTS (
       SELECT 1 FROM dbo.ComputationUnit AS u WHERE u.cGroupCode = g.cGroupCode AND u.bMainUnit = 1)))
ORDER BY CASE WHEN g.iGroupType = 1 THEN 0 ELSE 1 END, g.cGroupCode;
"""


def _define() -> str:
    # 需要建档、不取自其他档案、不定长、使用长度至少 12（档案值 ZZ + 10 位时间）的自定义项。
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(b.cID)) AS v
FROM dbo.UserDef_Base AS b
WHERE b.bArchive = 1 AND ISNULL(b.cRelArchive, N'') = N'' AND b.iLength >= 12 AND b.bFixLength = 0
  AND LTRIM(RTRIM(b.cID)) NOT LIKE N'%[^0-9]%'
ORDER BY b.cID;
"""


def _contra() -> str:
    # 还没有对照的（客户, 存货）：各取编码最小的 20 个交叉挑。输出 客户|存货。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + c.code + N'|' + i.code AS v
FROM (SELECT TOP (20) LTRIM(RTRIM(cCusCode)) AS code FROM dbo.Customer
      WHERE LTRIM(RTRIM(cCusCode)) {_ALNUM} ORDER BY cCusCode) AS c
CROSS JOIN (SELECT TOP (20) LTRIM(RTRIM(cInvCode)) AS code FROM dbo.Inventory
      WHERE LTRIM(RTRIM(cInvCode)) {_ALNUM} ORDER BY cInvCode) AS i
WHERE NOT EXISTS (SELECT 1 FROM dbo.CusInvContrapose AS x WHERE x.cCusCode = c.code AND x.cInvCode = i.code)
ORDER BY c.code, i.code;
"""


def _guard() -> str:
    # 删除闸门：有下级的货位（桥先 409），已被存货使用的计量单位（桥先 409）。输出 货位|计量单位。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(p.cPosCode)) + N'|' + LTRIM(RTRIM(i.cComUnitCode)) AS v
FROM dbo.Position AS p
CROSS JOIN (SELECT TOP (1) cComUnitCode FROM dbo.Inventory
            WHERE cComUnitCode IS NOT NULL AND LTRIM(RTRIM(cComUnitCode)) {_ALNUM} ORDER BY cInvCode) AS i
WHERE p.bPosEnd = 0 AND LTRIM(RTRIM(p.cPosCode)) {_ALNUM}
  AND EXISTS (SELECT 1 FROM dbo.Position AS s WHERE s.cPosCode LIKE p.cPosCode + N'_%')
ORDER BY p.cPosCode;
"""


# 之后可写的七类：挑第一个空闲的新编码（ZZ 打头；一级编码只有 1 位的分级档案只能用 Z、Y、X）。
# 分级档案的候选按编码方案第一级的长度过滤（GradeDef_Base），新档案都建成一级。
_GRADED = {"settle_style": "settlestyle", "rd_style": "rd_style", "district_class": "districtclass"}
_TABLE = {
    "unit_group": ("ComputationGroup", "cGroupCode"),
    "settle_style": ("SettleStyle", "cSSCode"),
    "rd_style": ("Rd_Style", "cRdCode"),
    "purchase_type": ("PurchaseType", "cPTCode"),
    "sale_type": ("SaleType", "cSTCode"),
    "district_class": ("DistrictClass", "cDCCode"),
    "aa_bank": ("AA_Bank", "cBankCode"),
    "pay_agreement": ("AA_Agreement", "cCode"),
}
# 采购类型要入库类别（收发标志 1）、销售类型要出库类别（0）：U8 档案设置可能设为必输，挑一个末级的一并输出。
_RD_FLAG = {"purchase_type": 1, "sale_type": 0}
_CANDIDATES = [
    *"ZYX",
    *(f"Z{c}" for c in "ZYXWVUTSR"),
    *(f"ZZ{n}" for n in range(10)),
    *(f"ZZ{n:02d}" for n in range(10)),
    *(f"ZZ{n:03d}" for n in range(10)),
]
_LENGTH = {"purchase_type": "2", "sale_type": "2", "aa_bank": "5", "unit_group": "4", "pay_agreement": "4"}


def _length(archive: str) -> str:
    if archive in _GRADED:
        return (
            "(SELECT TOP (1) CONVERT(int, LEFT(g.CODINGRULE, 1)) FROM dbo.GradeDef_Base AS g"
            f" WHERE g.KEYWORD = N'{_GRADED[archive]}' AND g.iYear = 0)"
        )
    return _LENGTH[archive]


def _free(archive: str) -> str:
    # 输出 空闲编码，采购 / 销售类型另加 |末级收发类别（挑不到收发类别就没有 ROW 行）。
    table, key = _TABLE[archive]
    values = ", ".join(f"({i}, N'{code}')" for i, code in enumerate(_CANDIDATES))
    value, rd = "c.code", ""
    if archive in _RD_FLAG:
        value = "c.code + N'|' + r.code"
        rd = f"""
CROSS JOIN (SELECT TOP (1) LTRIM(RTRIM(cRdCode)) AS code FROM dbo.Rd_Style
            WHERE bRdEnd = 1 AND bRdFlag = {_RD_FLAG[archive]} AND LTRIM(RTRIM(cRdCode)) {_ALNUM}
            ORDER BY cRdCode) AS r"""
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + {value} AS v
FROM (VALUES {values}) AS c(n, code){rd}
WHERE LEN(c.code) = {_length(archive)}
  AND NOT EXISTS (SELECT 1 FROM dbo.{table} AS x WHERE x.{key} = c.code)
ORDER BY c.n;
"""


def _guard_style() -> str:
    # 删除闸门：有下级的收发类别、已有计量单位的计量单位组（桥先 409）。输出 收发类别|计量单位组。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(r.cRdCode)) + N'|' + LTRIM(RTRIM(u.cGroupCode)) AS v
FROM dbo.Rd_Style AS r
CROSS JOIN (SELECT TOP (1) cGroupCode FROM dbo.ComputationUnit
            WHERE LTRIM(RTRIM(cGroupCode)) {_ALNUM} ORDER BY cGroupCode) AS u
WHERE r.bRdEnd = 0 AND LTRIM(RTRIM(r.cRdCode)) {_ALNUM}
  AND EXISTS (SELECT 1 FROM dbo.Rd_Style AS s WHERE s.cRdCode LIKE r.cRdCode + N'_%')
ORDER BY r.cRdCode;
"""


_FIELDS = "N'I_id', N'citemcode', N'citemname', N'bclose', N'citemccode', N'iotherused'"
_CODE = "LTRIM(RTRIM(a.citem_id))"


def _project() -> str:
    # 项目删除（arc_project_delete）：被辅助账 GL_accass 引用的项目，所在大类要能被桥写（同 ArcProjectSql 的 ClassSql、
    # StructSql、ExtraSql：fitemss<大类> 和分类表都在，没有额外栏目或子表，没有额外的非空无缺省列）。输出 大类|项目编码。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(a.citem_class)) + N'|' + {_CODE} AS v
FROM dbo.GL_accass AS a
JOIN dbo.fitem AS f ON f.citem_class = a.citem_class
WHERE f.ctable = N'fitemss' + f.citem_class AND LEN(f.citem_class) <= 2 AND f.citem_class {_ALNUM}
  AND OBJECT_ID(N'dbo.' + f.ctable, N'U') IS NOT NULL AND OBJECT_ID(N'dbo.' + f.ctable + N'class', N'U') IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM dbo.fitemstructure AS s WHERE s.citem_class = f.citem_class
      AND (ISNULL(s.iSubItem, 0) <> 0 OR s.cfield_name NOT IN ({_FIELDS})))
  AND NOT EXISTS (SELECT 1 FROM sys.columns AS c WHERE c.object_id = OBJECT_ID(N'dbo.' + f.ctable, N'U')
      AND c.is_nullable = 0 AND c.is_identity = 0 AND c.is_computed = 0 AND c.default_object_id = 0
      AND c.system_type_id <> 189 AND c.name NOT IN (N'citemcode', N'citemname', N'citemccode'))
  AND LEN({_CODE}) BETWEEN 1 AND 60 AND {_CODE} {_ALNUM}
ORDER BY a.citem_class, a.citem_id;
"""


def _guard_agreement() -> str:
    # 删除闸门：被客户档案的销售协议栏引用的收付款协议（桥先 409）。输出 协议编码。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(a.cCode)) AS v
FROM dbo.AA_Agreement AS a
WHERE LTRIM(RTRIM(a.cCode)) {_ALNUM}
  AND EXISTS (SELECT 1 FROM dbo.Customer AS c WHERE c.cCusSAProtocol = a.cCode)
ORDER BY a.cCode;
"""


def _guard_agreement_doc() -> str:
    # 删除闸门：只被销售订单的收付款协议栏（SO_SOMain.cgatheringplan）引用、客户和供应商档案都没用的协议（桥先 409）。输出 协议编码。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(a.cCode)) AS v
FROM dbo.AA_Agreement AS a
WHERE LTRIM(RTRIM(a.cCode)) {_ALNUM}
  AND EXISTS (SELECT 1 FROM dbo.SO_SOMain AS m WHERE m.cgatheringplan = a.cCode)
  AND NOT EXISTS (SELECT 1 FROM dbo.Customer AS c WHERE a.cCode IN (c.cCusSAProtocol, c.cCusExProtocol, c.cCusOtherProtocol,
                  c.cCusCMProtocol, c.cCusImAgentProtocol))
  AND NOT EXISTS (SELECT 1 FROM dbo.Vendor AS v WHERE a.cCode IN (v.cVenPUOMProtocol, v.cVenIMProtocol, v.cVenCMProtocol,
                  v.cVenOtherProtocol))
ORDER BY a.cCode;
"""


_PICKS = {
    "position": _position,
    "project": _project,
    "unit": _unit,
    "user_define": _define,
    "customer_inventory": _contra,
    "guard": _guard,
    "guard_style": _guard_style,
    "guard_agreement": _guard_agreement,
    "guard_agreement_doc": _guard_agreement_doc,
    **{archive: (lambda a=archive: _free(a)) for archive in _TABLE},
}
ARC_MORE_PICKS = tuple(_PICKS)


def arc_more_pick_sql(name: str) -> str:
    if name not in _PICKS:
        raise ValueError("未知的挑选 " + name)
    return _PICKS[name]()
