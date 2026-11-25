"""销售发货单表头修正（`voucher-head-fix`）用例的 SQL：数据驱动挑单、涉及行的整行快照、兜底写回。

标识符是常量；拼进 SQL 的值先过白名单（_id）或转义成 N'' 字面量（lit）。
挑单只看账套里的数据形态（已审核、下游单据只由这张发货单供货、客户有另一条收货地址、部门里另有有效业务员），
不写死任何单据号、客户或人员编码。SQL 文本里不出现 sqlguard 拒绝的词。
"""

from __future__ import annotations

import re

from co.e2e.casespec.arc_cus_sql import lit
from co.e2e.casespec.arc_cus_person_sql import NIL

_ID = re.compile(r"^[1-9][0-9]{0,18}\Z")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
# 快照里要原值的列（比对、还原）；其余列只给哈希：sqlcmd 不带 -y 时长文本列会被截断。
PLAIN = (
    "caddcode",
    "cPersonCode",
    "cPerson",
    "cDepCode",
    "cCusCode",
    "cDLCode",
    "cCode",
    "cSBVCode",
    "cVouchType",
    "cVouchID",
    "cVerifier",
    "cSOCode",
    "cProcStyle",
    "cCancelNo",
)
# 快照里的表：标签 → (表名, 行键列, 兜底可写的列)。
TABLES = {
    "DL": ("DispatchList", "DLID", ("caddcode", "cPersonCode")),
    "DS": ("DispatchList", "DLID", ()),
    "RD": ("rdrecord32", "ID", ("caddcode", "cPersonCode")),
    "SB": ("SaleBillVouch", "SBVID", ("caddcode", "cPersonCode")),
    "AR": ("Ar_Detail", "Auto_ID", ("cPerson",)),
    "SO": ("SO_SOMain", "ID", ()),
}
# 与桥一致只看失效日期（U8 不按有效期起限制单据）。
_VALID = "(p.dPInValidDate IS NULL OR p.dPInValidDate >= d.dDate)"
# 发货单行 → 下游销售出库单 / 销售发票的表头主键（别名 k）。
_RD_OF = "SELECT r.ID AS k FROM dbo.rdrecords32 AS r INNER JOIN dbo.DispatchLists AS l ON l.iDLsID = r.iDLsID WHERE l.DLID = {id}"
_SB_OF = "SELECT b.SBVID AS k FROM dbo.SaleBillVouchs AS b INNER JOIN dbo.DispatchLists AS l ON l.iDLsID = b.iDLsID WHERE l.DLID = {id}"
# 发票的往来明细：类型和单号同发票表头。
_AR_OF = "a.cVouchType = s.cVouchType AND a.cVouchID = s.cSBVCode"


def _id(value: object) -> str:
    text = str(value)
    if _ID.fullmatch(text) is None:
        raise ValueError("单据 id 不合法")
    return text


def _plain(col: str, limit: int) -> str:
    """列值非空、长度在限内、首尾无空格、不含制表符：写回去能原样还原。"""
    return (
        f"{col} IS NOT NULL AND LEN({col}) BETWEEN 1 AND {limit} AND {col} = LTRIM(RTRIM({col}))"
        f" AND CHARINDEX(NCHAR(9), {col}) = 0"
    )


def _headers(table: str, key: str, src: str, cond: str) -> str:
    """下游表头里存在满足 cond 的行。"""
    return f"EXISTS (SELECT 1 FROM dbo.{table} AS h WHERE h.{key} IN ({src}) AND ({cond}))"


_DIFFERS = (
    "ISNULL(h.caddcode, N'') <> ISNULL(d.caddcode, N'') OR DATALENGTH(h.caddcode) <> DATALENGTH(d.caddcode)"
    " OR ISNULL(h.cPersonCode, N'') <> ISNULL(d.cPersonCode, N'') OR DATALENGTH(h.cPersonCode) <> DATALENGTH(d.cPersonCode)"
)
_IS_NEW = "h.caddcode = na.c OR h.cPersonCode = np.c"
_EMPTY_OR_NEW = "(h.caddcode IS NOT NULL AND LTRIM(RTRIM(h.caddcode)) = N'') OR h.caddcode = na.c"


def _only_this(kind: str) -> str:
    """下游 kind（sale_out / invoice）的表头只由这张发货单供货：没有来自别的发货单、或没有来源的行。"""
    table, key, head, other = ("rdrecords32", "ID", "r", "r2") if kind == "sale_out" else ("SaleBillVouchs", "SBVID", "b", "b2")
    return (
        f"NOT EXISTS (SELECT 1 FROM dbo.{table} AS {head} INNER JOIN dbo.DispatchLists AS l ON l.iDLsID = {head}.iDLsID"
        f" AND l.DLID = d.DLID INNER JOIN dbo.{table} AS {other} ON {other}.{key} = {head}.{key}"
        f" LEFT JOIN dbo.DispatchLists AS l2 ON l2.iDLsID = {other}.iDLsID WHERE l2.DLID IS NULL OR l2.DLID <> d.DLID)"
    )


def _count_one(src: str) -> str:
    return f"(SELECT COUNT(DISTINCT x.k) FROM ({src}) AS x) = 1"


def _skip(ids: tuple[str, ...]) -> str:
    return f"  AND d.DLID NOT IN ({', '.join(_id(item) for item in ids)})\n" if ids else ""


def _applies(sibling: bool) -> str:
    """na / np：新地址、新业务员。缺省取客户另一条收货地址、部门里另一个有效业务员；sibling 取同发票另一张发货单的值。"""
    if sibling:
        one = (
            "(SELECT TOP (1) o.{col} AS c FROM dbo.SaleBillVouchs AS b1 INNER JOIN dbo.DispatchLists AS l1"
            " ON l1.iDLsID = b1.iDLsID INNER JOIN dbo.DispatchList AS o ON o.DLID = l1.DLID"
            " WHERE b1.SBVID IN (" + _SB_OF.format(id="d.DLID") + ") AND o.DLID <> d.DLID ORDER BY o.DLID)"
        )
        return f"CROSS APPLY {one.format(col='caddcode')} AS na\nCROSS APPLY {one.format(col='cPersonCode')} AS np\n"
    return (
        "CROSS APPLY (SELECT TOP (1) a.cAddCode AS c FROM dbo.CusDeliverAdd AS a\n"
        f"  WHERE a.cCusCode = d.cCusCode AND {_plain('a.cAddCode', 30)} AND a.cAddCode <> ISNULL(d.caddcode, N'')\n"
        "  ORDER BY a.id) AS na\n"
        "CROSS APPLY (SELECT TOP (1) p.cPersonCode AS c FROM dbo.Person AS p\n"
        f"  WHERE p.cDepCode = d.cDepCode AND {_plain('p.cPersonCode', 20)} AND p.cPersonCode <> ISNULL(d.cPersonCode, N'')\n"
        f"  AND {_VALID}\n"
        "  ORDER BY p.cPersonCode) AS np\n"
    )


def _reversible_addr() -> str:
    """原收货地址可回滚：为空，或在该客户的收货地址档案里（否则桥拒绝正向修改：改了之后写不回去）。"""
    return (
        "(d.caddcode IS NULL OR LTRIM(RTRIM(d.caddcode)) = N'' OR EXISTS (SELECT 1 FROM dbo.CusDeliverAdd AS ra"
        " WHERE ra.cCusCode = d.cCusCode AND ra.cAddCode = d.caddcode))"
    )


def _reversible_person() -> str:
    """原业务员可回滚：在 Person 里、在发货日期之前未失效、部门等于发货单部门。"""
    return (
        "EXISTS (SELECT 1 FROM dbo.Person AS rp WHERE rp.cPersonCode = d.cPersonCode AND rp.cDepCode = d.cDepCode"
        " AND (rp.dPInValidDate IS NULL OR rp.dPInValidDate >= d.dDate))"
    )


def _pick(where: str, order: str, addr_null: bool = False, sibling: bool = False) -> str:
    """候选发货单的公共部分：已审核，值能原样写回、也能回滚，有新地址、新业务员；输出 发货单 id、新地址、新业务员。"""
    addr = "d.caddcode IS NULL" if addr_null else _plain("d.caddcode", 30)
    return (
        "SET NOCOUNT ON;\n"
        "SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), d.DLID) + NCHAR(9) + na.c + NCHAR(9) + np.c AS v\n"
        "FROM dbo.DispatchList AS d\n"
        + _applies(sibling)
        + "WHERE d.cVerifier IS NOT NULL AND LTRIM(RTRIM(d.cVerifier)) <> N''\n"
        f"  AND {addr} AND {_plain('d.cPersonCode', 20)}\n"
        f"  AND {_reversible_addr()} AND {_reversible_person()}\n"
        f"{where}ORDER BY {order};\n"
    )


def single_pick_sql(with_ar: bool = True) -> str:
    """已审核发货单，恰有一张销售出库单、一张销售发票，两者只由它供货、表头与它一致；输出 发货单 id、另一地址、另一业务员。

    with_ar 为真时还要求发票有业务员等于发货单业务员的往来明细行（挑不到再用 False 重挑）。
    """
    rd, sb = _RD_OF.format(id="d.DLID"), _SB_OF.format(id="d.DLID")
    ar = f"FROM dbo.Ar_Detail AS a INNER JOIN dbo.SaleBillVouch AS s ON {_AR_OF} WHERE s.SBVID IN ({sb}) AND "
    where = (
        f"  AND {_count_one(rd)} AND {_count_one(sb)}\n"
        f"  AND {_only_this('sale_out')} AND {_only_this('invoice')}\n"
        f"  AND NOT {_headers('rdrecord32', 'ID', rd, _DIFFERS)} AND NOT {_headers('SaleBillVouch', 'SBVID', sb, _DIFFERS)}\n"
        f"  AND NOT {_headers('rdrecord32', 'ID', rd, _IS_NEW)} AND NOT {_headers('SaleBillVouch', 'SBVID', sb, _IS_NEW)}\n"
        f"  AND NOT EXISTS (SELECT 1 {ar}a.cPerson = np.c)\n"
    )
    if with_ar:
        where += f"  AND EXISTS (SELECT 1 {ar}a.cPerson = d.cPersonCode)\n"
    return _pick(where, "d.DLID DESC")


def _merged_invoice(others_differ: bool = True) -> str:
    """发货单的某张销售发票合并了 2 张以上发货单、行都有来源；others_differ 为真时另要求有来源发货单的地址和业务员都不是新值。"""
    other = (
        "EXISTS (SELECT 1 FROM dbo.SaleBillVouchs AS b4 INNER JOIN dbo.DispatchLists AS l4 ON l4.iDLsID = b4.iDLsID"
        " INNER JOIN dbo.DispatchList AS o ON o.DLID = l4.DLID WHERE b4.SBVID = b1.SBVID AND o.DLID <> d.DLID AND {cond})"
    )
    tail = ""
    if others_differ:
        addr = other.format(cond="ISNULL(o.caddcode, N'') <> na.c")
        person = other.format(cond="ISNULL(o.cPersonCode, N'') <> np.c")
        tail = f" AND {addr} AND {person}"
    return (
        "EXISTS (SELECT 1 FROM dbo.SaleBillVouchs AS b1 INNER JOIN dbo.DispatchLists AS l1 ON l1.iDLsID = b1.iDLsID"
        " AND l1.DLID = d.DLID"
        " WHERE (SELECT COUNT(DISTINCT l2.DLID) FROM dbo.SaleBillVouchs AS b2 INNER JOIN dbo.DispatchLists AS l2"
        " ON l2.iDLsID = b2.iDLsID WHERE b2.SBVID = b1.SBVID) >= 2"
        " AND NOT EXISTS (SELECT 1 FROM dbo.SaleBillVouchs AS b3 WHERE b3.SBVID = b1.SBVID AND (b3.iDLsID IS NULL"
        " OR b3.iDLsID = 0 OR NOT EXISTS (SELECT 1 FROM dbo.DispatchLists AS l3 WHERE l3.iDLsID = b3.iDLsID)))"
        f"{tail})"
    )


def merged_pick_sql(skip: tuple[str, ...] = ()) -> str:
    """已审核发货单，只有一张销售发票、且它合并了 2 张以上发货单；下游表头都还不是新值。skip 是要避开的发货单 id。"""
    rd, sb = _RD_OF.format(id="d.DLID"), _SB_OF.format(id="d.DLID")
    where = (
        f"  AND {_count_one(sb)} AND {_merged_invoice()}\n"
        f"  AND NOT {_headers('rdrecord32', 'ID', rd, _IS_NEW)} AND NOT {_headers('SaleBillVouch', 'SBVID', sb, _IS_NEW)}\n"
    ) + _skip(skip)
    return _pick(where, "d.DLID DESC")


def merged_new_pick_sql(skip: tuple[str, ...] = ()) -> str:
    """同一类发票，但另外的来源发货单已经全是新值（新值取它的）、发票表头还是这张发货单的旧值：改完全部一致，发票会被更新。

    新地址须是这张发货单客户的收货地址，新业务员须是发货单部门里在发货日期有效的人。
    """
    rd, sb = _RD_OF.format(id="d.DLID"), _SB_OF.format(id="d.DLID")
    same = (
        "NOT EXISTS (SELECT 1 FROM dbo.SaleBillVouchs AS b5 INNER JOIN dbo.DispatchLists AS l5 ON l5.iDLsID = b5.iDLsID"
        " INNER JOIN dbo.DispatchList AS o2 ON o2.DLID = l5.DLID WHERE b5.SBVID IN (" + sb + ") AND o2.DLID <> d.DLID"
        " AND (ISNULL(o2.caddcode, N'') <> na.c OR ISNULL(o2.cPersonCode, N'') <> np.c))"
    )
    valid = (
        f"na.c IS NOT NULL AND np.c IS NOT NULL AND na.c <> ISNULL(d.caddcode, N'') AND np.c <> ISNULL(d.cPersonCode, N'')\n"
        f"  AND EXISTS (SELECT 1 FROM dbo.CusDeliverAdd AS a WHERE a.cCusCode = d.cCusCode AND a.cAddCode = na.c"
        f" AND {_plain('a.cAddCode', 30)})\n"
        f"  AND EXISTS (SELECT 1 FROM dbo.Person AS p WHERE p.cPersonCode = np.c AND p.cDepCode = d.cDepCode"
        f" AND {_plain('p.cPersonCode', 20)} AND {_VALID})"
    )
    where = (
        f"  AND {valid}\n  AND {_count_one(sb)} AND {_merged_invoice(False)} AND {same}\n"
        f"  AND NOT {_headers('SaleBillVouch', 'SBVID', sb, _DIFFERS)}\n"
        f"  AND NOT {_headers('rdrecord32', 'ID', rd, _IS_NEW)}\n"
    ) + _skip(skip)
    return _pick(where, "d.DLID DESC", sibling=True)


def already_new_pick_sql(skip: tuple[str, ...] = ()) -> str:
    """像 single_pick_sql（一张出库单、一张发票，只由它供货），但出库单表头已经是新地址和新业务员（发票仍是旧值）。"""
    rd, sb = _RD_OF.format(id="d.DLID"), _SB_OF.format(id="d.DLID")
    where = (
        f"  AND {_count_one(rd)} AND {_count_one(sb)}\n"
        f"  AND {_only_this('sale_out')} AND {_only_this('invoice')}\n"
        f"  AND NOT {_headers('SaleBillVouch', 'SBVID', sb, _DIFFERS)}\n"
        f"  AND {_headers('rdrecord32', 'ID', rd, 'h.caddcode = na.c AND h.cPersonCode = np.c')}\n"
    ) + _skip(skip)
    return _pick(where, "d.DLID DESC")


def null_pick_sql(with_chain: bool = True) -> str:
    """已审核发货单，地址是 NULL；下游表头地址不是空串、也不等于新地址（清空时才能原样回到 NULL）。输出同 single_pick_sql。

    with_chain 为真时还要求有下游销售出库单（挑不到再用 False 重挑）。
    """
    rd, sb = _RD_OF.format(id="d.DLID"), _SB_OF.format(id="d.DLID")
    where = (
        f"  AND NOT {_headers('rdrecord32', 'ID', rd, _EMPTY_OR_NEW)}\n"
        f"  AND NOT {_headers('SaleBillVouch', 'SBVID', sb, _EMPTY_OR_NEW)}\n"
    )
    if with_chain:
        where += f"  AND EXISTS ({rd})\n"
    return _pick(where, "d.DLID DESC", True)


def missing_id_sql() -> str:
    """一个不存在的发货单 id（现有最大值再加 1000）。"""
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(20), ISNULL(MAX(DLID), 0) + 1000) AS v FROM dbo.DispatchList;\n"


def person_count_sql(code: str) -> str:
    return (
        "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(12), COUNT(*)) AS v\n"
        f"FROM dbo.Person WHERE cPersonCode = {lit(code)};\n"
    )


def other_dept_person_sql(dlid: str) -> str:
    """在发货单日期有效、但不在发货单部门的业务员。"""
    return (
        "SET NOCOUNT ON;\nSELECT TOP (1) N'ROW:' + p.cPersonCode AS v\n"
        "FROM dbo.DispatchList AS d INNER JOIN dbo.Person AS p ON p.cDepCode <> d.cDepCode\n"
        f"WHERE d.DLID = {_id(dlid)} AND {_plain('p.cPersonCode', 20)} AND {_VALID}\n"
        "ORDER BY p.cPersonCode;\n"
    )


def _part(tag: str, where: str) -> str:
    table, key, _cols = TABLES[tag]
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


def snapshot_sql(dlid: str) -> str:
    """涉及行的逐列快照，每列一行 ``ROW:标签<TAB>行键<TAB>列名<TAB>值``：NULL 为 ``<nil>``，非 PLAIN 的列给 ``h:`` + SHA-256。

    标签：DL 发货单表头、DS 同发票的其他发货单、RD 下游销售出库单、SB 下游销售发票、AR 发票自己的往来明细（含调整行）、
    SO 发货单行所属的销售订单表头（应原样不动）。
    """
    one = _id(dlid)
    rd, sb = _RD_OF.format(id=one), _SB_OF.format(id=one)
    sibling = (
        "SELECT l9.DLID FROM dbo.SaleBillVouchs AS b9 INNER JOIN dbo.DispatchLists AS l9 ON l9.iDLsID = b9.iDLsID"
        f" WHERE b9.SBVID IN ({sb})"
    )
    ar_where = (
        f"t.cVouchID IN (SELECT s0.cSBVCode FROM dbo.SaleBillVouch AS s0 WHERE s0.SBVID IN ({sb}))"
        " AND EXISTS (SELECT 1 FROM dbo.SaleBillVouch AS s WHERE s.cVouchType = t.cVouchType"
        f" AND s.cSBVCode = t.cVouchID AND s.SBVID IN ({sb}))"
    )
    so_where = f"t.cSOCode IN (SELECT l.cSOCode FROM dbo.DispatchLists AS l WHERE l.DLID = {one} AND l.cSOCode IS NOT NULL)"
    parts = (
        _part("DL", f"t.DLID = {one}"),
        _part("DS", f"t.DLID <> {one} AND t.DLID IN ({sibling})"),
        _part("RD", f"t.ID IN ({rd})"),
        _part("SB", f"t.SBVID IN ({sb})"),
        _part("AR", ar_where),
        _part("SO", so_where),
    )
    return "SET NOCOUNT ON;\nSET QUOTED_IDENTIFIER ON;\n" + "\nUNION ALL\n".join(parts) + ";\n"


def reset_sql(tag: str, key: str, cols: dict[str, str]) -> str:
    """兜底：按行键把目标列写回快照值（路由还原不了时在测试账套上用）；不在可写列表里的列忽略。"""
    table, key_col, allowed = TABLES[tag]
    ok = {item.lower() for item in allowed}
    sets = [
        f"{name} = " + ("NULL" if value == NIL else lit(value))
        for name, value in cols.items()
        if name.lower() in ok and _IDENT.fullmatch(name) is not None
    ]
    if not sets:
        raise ValueError("没有可写回的列")
    return (
        f"SET NOCOUNT ON;\nUPDATE dbo.{table} SET " + ", ".join(sets) + f"\nWHERE {key_col} = {_id(key)};\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def hf_samples() -> list[tuple[str, str]]:
    return [
        ("core-hf-single", single_pick_sql()),
        ("core-hf-merged", merged_pick_sql(("1000000001",))),
        ("core-hf-merged-new", merged_new_pick_sql(("1000000001", "1000000002"))),
        ("core-hf-already-new", already_new_pick_sql(("1000000001",))),
        ("core-hf-null", null_pick_sql()),
        ("core-hf-missing", missing_id_sql()),
        ("core-hf-person-count", person_count_sql("P900001")),
        ("core-hf-other-dept", other_dept_person_sql("1000000001")),
        ("core-hf-snapshot", snapshot_sql("1000000001")),
        ("core-hf-reset", reset_sql("DL", "1000000001", {"caddcode": NIL, "cPersonCode": "P900001"})),
        ("core-hf-reset-ar", reset_sql("AR", "55", {"cPerson": "P900001"})),
    ]
