"""销售订单表头自定义项写入（`voucher-so-defines`）用例的 SQL：数据驱动挑单、列目录、涉及行的整行快照、兜底写回。

标识符是常量；拼进 SQL 的值先过白名单（_id、_IDENT）或转义成 N'' 字面量（lit）。
挑单只看账套里的数据形态（已审核、cDefine1 / cDefine2 的当前值、是否关闭、是否已有下游发货单），
不写死任何单据号、客户或人员编码。列目录与桥（VoucherSoDefinesSql.ColSql）同一口径。SQL 文本里不出现 sqlguard 拒绝的词。
"""

from __future__ import annotations

import re

from co.e2e.casespec.arc_cus_sql import lit
from co.e2e.casespec.arc_cus_person_sql import NIL

_ID = re.compile(r"^[1-9][0-9]{0,18}\Z")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
# 快照里要原值的列（比对、还原）；其余列只给哈希：sqlcmd 不带 -y 时长文本列会被截断。
# 桥写入时盖的修改人、修改日期、修改时间（VoucherSoDefinesSql 的 cModifier、dModDate、dmodifysystime）也取原值，用来核对和还原。
STAMPS = ("cModifier", "dModDate", "dmodifysystime")
# 用例写的表头列：两列一起写（cdefine1、cdefine2）和单列写（cdefine3）。
WRITTEN = ("cdefine1", "cdefine2", "cdefine3")
PLAIN = ("cDefine1", "cDefine2", "cDefine3", "cSOCode", "cVerifier", "cCloser", "cLocker", "ufts", *STAMPS)
# 快照里的表：标签 → (表名, 行键列)。SO 订单表头、SD 订单行、DL 下游发货单表头、SB 下游发票表头、
# RD 下游销售出库单表头（只在连带下游的快照里，so_defines_cascade_sql）。
TABLES = {
    "SO": ("SO_SOMain", "ID"),
    "SD": ("SO_SODetails", "iSOsID"),
    "DL": ("DispatchList", "DLID"),
    "SB": ("SaleBillVouch", "SBVID"),
    "RD": ("rdrecord32", "ID"),
}
# 兜底写回只动这些列：用例写的自定义项、锁定人和桥盖的修改人、修改日期、修改时间列；行版本列写不了。
_NOT_WRITABLE = frozenset({"ufts", "pubufts"})
_REAL_LIMIT = 40
# 「无」：本用例的 empty_values 里的那个空值标记。
_NONE_MARK = "N'无'"
_DEFINES = ", ".join(f"N'cDefine{index}'" for index in range(1, 17))


def _id(value: object) -> str:
    text = str(value)
    if _ID.fullmatch(text) is None:
        raise ValueError("订单 id 不合法")
    return text


def _blank(col: str) -> str:
    """列当前是空：NULL、空串或恰好一个「无」（不含空白，写回去能原样还原）。"""
    return f"({col} IS NULL OR DATALENGTH({col}) = 0 OR ({col} = {_NONE_MARK} AND DATALENGTH({col}) = 2))"


def _real(col: str) -> str:
    """列值是一个干净的非空值：长度在限内、首尾无空白、不含制表符和换行、不是「无」。"""
    return (
        f"{col} IS NOT NULL AND LEN({col}) BETWEEN 1 AND {_REAL_LIMIT} AND {col} = LTRIM(RTRIM({col}))"
        f" AND CHARINDEX(NCHAR(9), {col}) = 0 AND CHARINDEX(NCHAR(10), {col}) = 0 AND CHARINDEX(NCHAR(13), {col}) = 0"
        f" AND {col} <> {_NONE_MARK}"
    )


_AUDITED = "LTRIM(RTRIM(ISNULL(m.cVerifier, N''))) <> N''"
_UNLOCKED = "(m.cLocker IS NULL OR DATALENGTH(m.cLocker) = 0)"
_OPEN = "LTRIM(RTRIM(ISNULL(m.cCloser, N''))) = N''"
_CLOSED = "LTRIM(RTRIM(ISNULL(m.cCloser, N''))) <> N''"
_SHIPPED = (
    "EXISTS (SELECT 1 FROM dbo.DispatchLists AS l INNER JOIN dbo.SO_SODetails AS d ON d.iSOsID = l.iSOsID"
    " WHERE d.ID = m.ID)"
)

# 样本类别 → 条件。plain：已审核、未关闭、没有发货单；closed：已关闭；shipped：未关闭、已有发货单；
# filled：cDefine1 已有一个干净的值（写入照样覆盖，调用方给对 expect 即可），cDefine2 为空。
_KINDS = {
    "plain": (_blank("m.cDefine1"), _blank("m.cDefine2"), _OPEN, "NOT " + _SHIPPED),
    "plain_any": (_blank("m.cDefine1"), _blank("m.cDefine2"), _OPEN),
    "closed": (_blank("m.cDefine1"), _blank("m.cDefine2"), _CLOSED),
    "shipped": (_blank("m.cDefine1"), _blank("m.cDefine2"), _OPEN, _SHIPPED),
    "filled": (_real("m.cDefine1"), _blank("m.cDefine2"), _OPEN),
}


def _skip(ids: tuple[str, ...]) -> str:
    return f"  AND m.ID NOT IN ({', '.join(_id(item) for item in ids)})\n" if ids else ""


def pick_sql(kind: str, skip: tuple[str, ...] = ()) -> str:
    """已审核、未锁定的销售订单（kind 见 _KINDS）；输出 订单 id。skip 是要避开的订单 id。取最早的一张：新订单可能被别的用例删掉。"""
    where = "".join(f"  AND {cond}\n" for cond in _KINDS[kind])
    return (
        "SET NOCOUNT ON;\n"
        "SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), m.ID) AS v\n"
        "FROM dbo.SO_SOMain AS m\n"
        f"WHERE {_AUDITED}\n  AND {_UNLOCKED}\n"
        f"{where}{_skip(skip)}"
        "ORDER BY m.ID;\n"
    )


def missing_id_sql() -> str:
    """一个不存在的订单 id（现有最大值再加 1000）。"""
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(20), ISNULL(MAX(ID), 0) + 1000) AS v FROM dbo.SO_SOMain;\n"


def columns_sql() -> str:
    """SO_SOMain 的 cDefine1–16 列目录，每列一行 ``ROW:列名(小写)<TAB>文本列 1/0<TAB>列宽<TAB>自定义项长度``。

    与桥同一口径：文本列 = char / nchar / varchar / nvarchar；列宽 nchar / nvarchar 按字符、max 为 0（不限）；
    自定义项长度取 UserDef_Base（单据头、cDicDbName 为列名）的 iLength，没有为 0。
    """
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + LOWER(c.name) + NCHAR(9)\n"
        "  + CASE WHEN TYPE_NAME(c.system_type_id) IN (N'char', N'nchar', N'varchar', N'nvarchar') THEN N'1' ELSE N'0' END + NCHAR(9)\n"
        "  + CONVERT(varchar(12), CASE WHEN c.max_length < 0 THEN 0"
        " WHEN TYPE_NAME(c.system_type_id) IN (N'nchar', N'nvarchar') THEN c.max_length / 2 ELSE c.max_length END) + NCHAR(9)\n"
        "  + CONVERT(varchar(12), ISNULL(u.iLength, 0)) AS v\n"
        "FROM sys.columns AS c\n"
        "OUTER APPLY (SELECT TOP (1) b.iLength FROM dbo.UserDef_Base AS b WHERE b.cClass = N'单据头' AND b.cDicDbName = c.name) AS u\n"
        f"WHERE c.object_id = OBJECT_ID(N'dbo.SO_SOMain', N'U') AND c.name IN ({_DEFINES})\n"
        "ORDER BY c.column_id;\n"
    )


def _part(tag: str, where: str) -> str:
    table, key = TABLES[tag]
    plain = ", ".join(f"N'{name.lower()}'" for name in PLAIN)
    name = "m.value(N'local-name(.)', N'nvarchar(128)')"
    text = "REPLACE(REPLACE(REPLACE(m.value(N'.', N'nvarchar(200)'), NCHAR(9), N'\\t'), NCHAR(13), N'\\r'), NCHAR(10), N'\\n')"
    return (
        f"SELECT N'ROW:{tag}' + NCHAR(9) + n.value(N'(rowkey)[1]', N'nvarchar(40)') + NCHAR(9) + {name} + NCHAR(9) + CASE\n"
        f"  WHEN m.value(N'(@*:nil)[1]', N'nvarchar(5)') = N'true' THEN N'{NIL}'\n"
        f"  WHEN LOWER({name}) IN ({plain}) THEN {text}\n"
        "  ELSE N'h:' + CONVERT(nvarchar(64), HASHBYTES('SHA2_256', m.value(N'.', N'nvarchar(max)')), 2)\n"
        "END AS v\n"
        f"FROM (SELECT (SELECT t.{key} AS rowkey, t.* FROM dbo.{table} AS t WHERE {where} "
        "ORDER BY t." + key + " FOR XML RAW(N'r'), ELEMENTS XSINIL, TYPE) AS x) AS s\n"
        "CROSS APPLY x.nodes(N'/r') AS q(n)\n"
        "CROSS APPLY n.nodes(N'*') AS c(m)\n"
        f"WHERE {name} <> N'rowkey'"
    )


def part_sql(tag: str, where: str) -> str:
    """一张表的逐列快照片段（格式见 snapshot_sql），给连带下游的快照（so_defines_cascade_sql）复用。"""
    return _part(tag, where)


def snapshot_sql(so_id: str) -> str:
    """涉及行的逐列快照，每列一行 ``ROW:标签<TAB>行键<TAB>列名<TAB>值``：NULL 为 ``<nil>``，非 PLAIN 的列给 ``h:`` + SHA-256。

    标签：SO 订单表头、SD 订单行、DL 订单行下游的发货单表头、SB 订单行下游的销售发票表头（后两者应原样不动）。
    """
    one = _id(so_id)
    lines = f"SELECT d0.iSOsID FROM dbo.SO_SODetails AS d0 WHERE d0.ID = {one}"
    dispatch = f"SELECT l.DLID FROM dbo.DispatchLists AS l WHERE l.iSOsID IN ({lines})"
    invoice = f"SELECT b.SBVID FROM dbo.SaleBillVouchs AS b WHERE b.iSOsID IN ({lines})"
    parts = (
        _part("SO", f"t.ID = {one}"),
        _part("SD", f"t.ID = {one}"),
        _part("DL", f"t.DLID IN ({dispatch})"),
        _part("SB", f"t.SBVID IN ({invoice})"),
    )
    return "SET NOCOUNT ON;\nSET QUOTED_IDENTIFIER ON;\n" + "\nUNION ALL\n".join(parts) + ";\n"


def writable(col: str) -> bool:
    """兜底写回允许的列：用例写的自定义项、锁定人、桥盖的修改人 / 日期 / 时间列（行版本列除外）。"""
    low = col.lower()
    if low in _NOT_WRITABLE or _IDENT.fullmatch(col) is None:
        return False
    return low in WRITTEN or low == "clocker" or low in {name.lower() for name in STAMPS}


def restore_sql(so_id: str, cols: dict[str, str]) -> str:
    """兜底：按订单 id 把列写回快照值（路由只写不清空，测试账套上用）；不在可写范围的列忽略。"""
    sets = [f"{name} = " + ("NULL" if value == NIL else lit(value)) for name, value in cols.items() if writable(name)]
    if not sets:
        raise ValueError("没有可写回的列")
    return (
        "SET NOCOUNT ON;\nUPDATE dbo.SO_SOMain SET " + ", ".join(sets) + f"\nWHERE ID = {_id(so_id)};\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def locker_sql(so_id: str, value: str | None) -> str:
    """把表头锁定人写成 value（None 写 NULL）：用例借它模拟别的操作员锁定，再按起始值写回；回 @@ROWCOUNT。"""
    return (
        "SET NOCOUNT ON;\nUPDATE dbo.SO_SOMain SET cLocker = " + ("NULL" if value is None else lit(value))
        + f"\nWHERE ID = {_id(so_id)};\nSELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def sod_samples() -> list[tuple[str, str]]:
    return [
        *((f"core-sod-pick-{kind}", pick_sql(kind, ("1000000001",))) for kind in _KINDS),
        ("core-sod-missing", missing_id_sql()),
        ("core-sod-columns", columns_sql()),
        ("core-sod-snapshot", snapshot_sql("1000000001")),
        ("core-sod-locker", locker_sql("1000000001", "E2E-OTHER")),
        ("core-sod-locker-null", locker_sql("1000000001", None)),
        ("core-sod-restore", restore_sql("1000000001", {"cDefine1": NIL, "cDefine2": "X1", "cDefine3": "X3", "cModifier": "u"})),
    ]
