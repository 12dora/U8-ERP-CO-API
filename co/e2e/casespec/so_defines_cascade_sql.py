"""销售订单表头自定义项写入连带下游（voucher-so-defines 的 cascade 一轮）用例的 SQL：挑单、带销售出库单的整行快照、混合来源单据清单、兜底写回。

下游关联与来源解析与桥（VoucherSoDefinesLinks）相同：链接值 0 当缺省（NULLIF）；发货单 / 退货单 DispatchLists.iSOsID；
销售出库单 rdrecords32.iDLsID → 发货单行；销售发票直接 iSOsID 与经 iDLsID 的发货单行各自解析，不同算冲突，两条都没有时经 iSBVID
一跳到蓝字行。「混合来源」：单据有明细解析出的来源订单不是这张订单（或没有来源：无链接、悬空、冲突）。标识符是常量，拼进 SQL 的 id 先过白名单，值转义成 N'' 字面量。
"""

from __future__ import annotations

from co.e2e.casespec.arc_cus_sql import lit
from co.e2e.casespec.arc_cus_person_sql import NIL
from co.e2e.casespec.so_defines_sql import _id, part_sql

# 快照标签 → 响应 cascade[].type。
DOWN_TYPES = {"DL": "dispatch", "RD": "sale_out", "SB": "sale_invoice"}
# 下游表头：标签 → (表名, 主键列)；兜底写回只写用例写过的自定义项列。
_DOWN = {"DL": ("DispatchList", "DLID"), "RD": ("rdrecord32", "ID"), "SB": ("SaleBillVouch", "SBVID")}
_WRITTEN = ("cdefine1", "cdefine2", "cdefine3")
# 一张订单的下游单据超过这个张数就不挑（桥会 409 too_many_downstream）。
_CAP = 200
_AUDITED = "LTRIM(RTRIM(ISNULL(p.cVerifier, N''))) <> N''"
_UNLOCKED = "(p.cLocker IS NULL OR DATALENGTH(p.cLocker) = 0)"


# 一行发票表体的两条链接（直接 / 经发货单行）各自解析：0 没给，-1 悬空（含发货单行没有订单链接），否则订单 ID；
# 两条都有而不同为冲突（-1）。蓝字一跳只在两条都没给时经 iSBVID 走（排除自指），不再往下跳。
def _hop(direct: str, s_dir: str, dl: str, l_row: str, s_dl: str) -> str:
    a = f"CASE WHEN NULLIF({direct}, 0) IS NULL THEN 0 WHEN {s_dir}.ID IS NULL THEN -1 ELSE {s_dir}.ID END"
    v = f"CASE WHEN NULLIF({dl}, 0) IS NULL THEN 0 WHEN {l_row}.iDLsID IS NULL OR {s_dl}.ID IS NULL THEN -1 ELSE {s_dl}.ID END"
    return f"CASE WHEN ({a}) = 0 THEN ({v}) WHEN ({v}) = 0 THEN ({a}) WHEN ({a}) > 0 AND ({a}) = ({v}) THEN ({a}) ELSE -1 END"


def _source(tag: str, n: str) -> tuple[str, str, str, tuple[str, ...]]:
    """表体行的 (FROM 与 JOIN, 表头主键列, 这一行的来源订单表达式（0 为无来源）, 指向订单的别名)；n 是别名后缀。"""
    if tag == "DL":
        frm = f"dbo.DispatchLists AS l{n} LEFT JOIN dbo.SO_SODetails AS s1{n} ON s1{n}.iSOsID = NULLIF(l{n}.iSOsID, 0)"
        return frm, f"l{n}.DLID", f"ISNULL(s1{n}.ID, 0)", (f"s1{n}",)
    if tag == "RD":
        frm = (
            f"dbo.rdrecords32 AS r{n} LEFT JOIN dbo.DispatchLists AS l{n} ON l{n}.iDLsID = NULLIF(r{n}.iDLsID, 0)"
            f" LEFT JOIN dbo.SO_SODetails AS s2{n} ON s2{n}.iSOsID = NULLIF(l{n}.iSOsID, 0)"
        )
        return frm, f"r{n}.ID", f"ISNULL(s2{n}.ID, 0)", (f"s2{n}",)
    b, bb = f"b{n}", f"bb{n}"
    frm = (
        f"dbo.SaleBillVouchs AS {b} LEFT JOIN dbo.SO_SODetails AS s1{n} ON s1{n}.iSOsID = NULLIF({b}.iSOsID, 0)"
        f" LEFT JOIN dbo.DispatchLists AS l{n} ON l{n}.iDLsID = NULLIF({b}.iDLsID, 0)"
        f" LEFT JOIN dbo.SO_SODetails AS s2{n} ON s2{n}.iSOsID = NULLIF(l{n}.iSOsID, 0)"
        f" LEFT JOIN dbo.SaleBillVouchs AS {bb} ON {bb}.AutoID = NULLIF({b}.iSBVID, 0) AND {bb}.AutoID <> {b}.AutoID"
        f" AND NULLIF({b}.iSOsID, 0) IS NULL AND NULLIF({b}.iDLsID, 0) IS NULL"
        f" LEFT JOIN dbo.SO_SODetails AS s3{n} ON s3{n}.iSOsID = NULLIF({bb}.iSOsID, 0)"
        f" LEFT JOIN dbo.DispatchLists AS l3{n} ON l3{n}.iDLsID = NULLIF({bb}.iDLsID, 0)"
        f" LEFT JOIN dbo.SO_SODetails AS s4{n} ON s4{n}.iSOsID = NULLIF(l3{n}.iSOsID, 0)"
    )
    own = _hop(f"{b}.iSOsID", f"s1{n}", f"{b}.iDLsID", f"l{n}", f"s2{n}")
    blue = _hop(f"{bb}.iSOsID", f"s3{n}", f"{bb}.iDLsID", f"l3{n}", f"s4{n}")
    src = (
        f"CASE WHEN ({own}) > 0 THEN ({own}) WHEN ({own}) < 0 THEN 0 WHEN {bb}.AutoID IS NULL THEN 0"
        f" WHEN ({blue}) > 0 THEN ({blue}) ELSE 0 END"
    )
    return frm, f"{b}.SBVID", src, (f"s1{n}", f"s2{n}", f"s3{n}", f"s4{n}")


def _ids(tag: str, owner: str) -> str:
    """owner（订单 id 或相关列）的下游单据主键子查询：表体有任一条链接指向 owner（同桥的发现，冲突行也算）。"""
    frm, head, _src, refs = _source(tag, "")
    hit = " OR ".join(f"{alias}.ID = {owner}" for alias in refs)
    return f"SELECT {head} FROM {frm} WHERE ({hit})"


def _mixed(tag: str, owner: str) -> str:
    """owner 的下游单据里有外来明细（解析出的来源订单不是 owner，或没有来源）的那些：输出列 v 是主键。"""
    frm, head, src, _refs = _source(tag, "2")
    return f"SELECT DISTINCT {head} AS v FROM {frm} WHERE {head} IN ({_ids(tag, owner)}) AND ({src}) <> {owner}"


def _count(owner: str) -> str:
    return " + ".join(f"(SELECT COUNT(DISTINCT x.v) FROM ({_ids(tag, owner)}) AS x(v))" for tag in _DOWN)


# 挑单类别 → 条件。pure：有发货单和发票、下游全部只来自这张订单；mixed：至少一张下游单据有外来明细。
def _conds(kind: str) -> tuple[str, ...]:
    shipped = f"EXISTS ({_ids('DL', 'p.ID')})"
    billed = f"EXISTS ({_ids('SB', 'p.ID')})"
    if kind == "pure":
        return (shipped, billed, *(f"NOT EXISTS ({_mixed(tag, 'p.ID')})" for tag in _DOWN))
    return (shipped, "(" + " OR ".join(f"EXISTS ({_mixed(tag, 'p.ID')})" for tag in _DOWN) + ")")


def cascade_pick_sql(kind: str, skip: tuple[str, ...] = ()) -> str:
    """已审核、未锁定、下游不超过上限的销售订单（kind 为 pure 或 mixed）；输出 订单 id。skip 是要避开的订单 id。"""
    where = "".join(f"  AND {cond}\n" for cond in _conds(kind))
    avoid = f"  AND p.ID NOT IN ({', '.join(_id(item) for item in skip)})\n" if skip else ""
    return (
        "SET NOCOUNT ON;\n"
        "SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), p.ID) AS v\n"
        "FROM dbo.SO_SOMain AS p\n"
        f"WHERE {_AUDITED}\n  AND {_UNLOCKED}\n{where}"
        f"  AND {_count('p.ID')} <= {_CAP}\n{avoid}"
        "ORDER BY p.ID;\n"
    )


def cascade_snapshot_sql(so_id: str) -> str:
    """订单表头、订单行和三类下游表头的逐列快照（格式同 so_defines_sql.snapshot_sql；RD 是销售出库单表头）。"""
    one = _id(so_id)
    parts = [part_sql("SO", f"t.ID = {one}"), part_sql("SD", f"t.ID = {one}")]
    for tag, (_table, key) in _DOWN.items():
        parts.append(part_sql(tag, f"t.{key} IN ({_ids(tag, one)})"))
    return "SET NOCOUNT ON;\nSET QUOTED_IDENTIFIER ON;\n" + "\nUNION ALL\n".join(parts) + ";\n"


def mixed_sql(so_id: str) -> str:
    """订单的下游单据里有外来明细的：每张一行 ``ROW:标签<TAB>主键``。"""
    one = _id(so_id)
    parts = [f"SELECT N'ROW:{tag}' + NCHAR(9) + CONVERT(varchar(20), m.v) AS v FROM ({_mixed(tag, one)}) AS m" for tag in _DOWN]
    return "SET NOCOUNT ON;\n" + "\nUNION ALL\n".join(parts) + ";\n"


def down_restore_sql(tag: str, row_id: str, cols: dict[str, str]) -> str:
    """兜底：把下游表头用例写过的自定义项列按快照值写回（测试账套上用）；只认 cdefine1–3。"""
    table, key = _DOWN[tag]
    sets = [f"{name} = " + ("NULL" if value == NIL else lit(value)) for name, value in cols.items() if name.lower() in _WRITTEN]
    if not sets:
        raise ValueError("没有可写回的列")
    return (
        f"SET NOCOUNT ON;\nUPDATE dbo.{table} SET " + ", ".join(sets) + f"\nWHERE {key} = {_id(row_id)};\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def sodc_samples() -> list[tuple[str, str]]:
    return [
        ("core-sodc-pick-pure", cascade_pick_sql("pure", ("1000000001",))),
        ("core-sodc-pick-mixed", cascade_pick_sql("mixed")),
        ("core-sodc-snapshot", cascade_snapshot_sql("1000000001")),
        ("core-sodc-mixed", mixed_sql("1000000001")),
        ("core-sodc-restore", down_restore_sql("RD", "1000000002", {"cDefine1": NIL, "cDefine2": "X1"})),
    ]
