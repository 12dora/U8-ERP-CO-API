"""档案写入附加核对（arc_probe）与操作员读取核对（operator_probe）的挑选和回读 SQL。

只读；标识符是常量。编码只接受字母数字（arc_write_more_sql.safe_part），自定义项档案值只接受本用例生成的汉字串（safe_cjk），
操作员编码只接受 [0-9A-Za-z_.-]（safe_user），再拼进 SQL。每条 SQL 输出 ROW:<值>[|<值>]，挑不到就没有 ROW 行。
"""

from __future__ import annotations

import re

from co.e2e.casespec.arc_write_more_sql import safe_part

_ALNUM = "NOT LIKE N'%[^0-9A-Za-z]%'"
_CJK = re.compile(r"^[一-鿿]{1,400}\Z")
_USER = re.compile(r"^[0-9A-Za-z_.-]{1,20}\Z")
# 上级货位一级编码的候选（编码方案第一级 2 位）：从 ZZ 倒着挑，与 archive-position-write 的 Z0 起挑错开。
_PARENTS = [f"Z{c}" for c in "ZYXWVUTSRQPNMLKJHGFEDCBA"]
# 采购类型、销售类型：表和编码列。
TYPE_TABLE = {"purchase_type": ("PurchaseType", "cPTCode"), "sale_type": ("SaleType", "cSTCode")}


def safe_cjk(text: str) -> str:
    if _CJK.fullmatch(text or "") is None:
        raise ValueError("自定义项档案值不合法")
    return text


def safe_user(text: str) -> str:
    if _USER.fullmatch(text or "") is None:
        raise ValueError("操作员编码不合法")
    return text


def _one(expr: str, frm: str = "") -> str:
    tail = "\n" + frm if frm else ""
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v" + tail + ";\n"


def pos_pick_sql() -> str:
    """启用货位管理的仓库 | 空闲的一级货位编码（及其下没有任何货位）| 它的第一个二级编码。编码方案第一级要 2 位、有第二级。"""
    values = ", ".join(f"({i}, N'{code}')" for i, code in enumerate(_PARENTS))
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(w.cWhCode)) + N'|' + c.code + N'|' + c.code + REPLICATE(N'0', g.n2 - 1) + N'1' AS v
FROM dbo.Warehouse AS w
CROSS JOIN (VALUES {values}) AS c(n, code)
CROSS JOIN (SELECT TOP (1) CONVERT(int, SUBSTRING(x.CODINGRULE, 2, 1)) AS n2 FROM dbo.GradeDef_Base AS x
            WHERE x.KEYWORD = N'positionclass' AND x.iYear = 0 AND LEFT(x.CODINGRULE, 1) = N'2'
              AND SUBSTRING(x.CODINGRULE, 2, 1) LIKE N'[1-9]') AS g
WHERE w.bWhPos = 1 AND LTRIM(RTRIM(w.cWhCode)) {_ALNUM}
  AND NOT EXISTS (SELECT 1 FROM dbo.Position AS p WHERE p.cPosCode LIKE c.code + N'%')
ORDER BY c.n, w.cWhCode;
"""


def _pos_flag(code: str) -> str:
    return (
        "ISNULL((SELECT CONVERT(varchar(3), ISNULL(CONVERT(int, p.bPosEnd), -1)) FROM dbo.Position AS p"
        f" WHERE p.cPosCode = N'{safe_part(code)}'), N'-')"
    )


def pos_flags_sql(parent: str, child: str) -> str:
    """上级、下级货位的末级标志 bPosEnd：1 / 0，NULL 记 -1，没有这条货位记 -。输出 上级|下级。"""
    return _one(_pos_flag(parent) + " + N'|' + " + _pos_flag(child))


def defaults_sql(archive: str) -> str:
    """采购类型（销售类型）里默认标志 bDefault = 1 的编码，按编码排序、逗号分隔；没有为空串。"""
    table, key = TYPE_TABLE[archive]
    inner = (
        f"SELECT N',' + LTRIM(RTRIM(x.{key})) FROM dbo.{table} AS x WHERE x.bDefault = 1"
        f" ORDER BY x.{key} FOR XML PATH('')"
    )
    return _one(f"ISNULL(STUFF(({inner}), 1, 1, N''), N'')")


def define_pick_sql() -> str:
    """需要建档、不取自其他档案、不定长、使用长度 12 到 200 的自定义项。输出 自定义项号|使用长度。"""
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(b.cID)) + N'|' + CONVERT(varchar(6), b.iLength) AS v
FROM dbo.UserDef_Base AS b
WHERE b.bArchive = 1 AND ISNULL(b.cRelArchive, N'') = N'' AND b.iLength BETWEEN 12 AND 200
  AND ISNULL(b.bFixLength, 0) = 0 AND LTRIM(RTRIM(b.cID)) NOT LIKE N'%[^0-9]%'
ORDER BY b.cID;
"""


def define_count_sql(cid: str, value: str) -> str:
    """该自定义项下档案值正好等于 value 的行数 | 以 value 前 10 个字开头的行数（看 U8 是否截断）。"""
    cid, value = safe_part(cid), safe_cjk(value)
    base = f"FROM dbo.UserDefine AS u WHERE u.cID = N'{cid}' AND u.cValue"
    exact = f"(SELECT COUNT(*) {base} = N'{value}')"
    prefix = f"(SELECT COUNT(*) {base} LIKE N'{value[:10]}%')"
    return _one(f"CONVERT(varchar(10), {exact}) + N'|' + CONVERT(varchar(10), {prefix})")


def person_sql(codes: list[str]) -> str:
    """每个操作员在账套库人员对照 UserHrPersonContro 里编码最小的人员（同桥 ArcUa 的 OUTER APPLY），没有为空串。
    一行一个：ROW:<操作员>|<人员>。"""
    values = ", ".join(f"(N'{safe_user(code)}')" for code in codes)
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + u.code + N'|' + ISNULL((SELECT TOP (1) RTRIM(x.cPsn_Num) FROM dbo.UserHrPersonContro AS x
    WHERE x.cUser_Id = u.code ORDER BY x.cPsn_Num), N'') AS v
FROM (VALUES {values}) AS u(code)
ORDER BY u.code;
"""


def arc_probe_samples() -> list[tuple[str, str]]:
    return [
        ("pos-pick", pos_pick_sql()),
        ("pos-flags", pos_flags_sql("ZZ", "ZZ01")),
        ("pt-defaults", defaults_sql("purchase_type")),
        ("st-defaults", defaults_sql("sale_type")),
        ("define-pick", define_pick_sql()),
        ("define-count", define_count_sql("1002", "档案值零一")),
        ("ua-person", person_sql(["demo", "zhang.san"])),
    ]
