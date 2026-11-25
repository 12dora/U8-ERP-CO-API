"""库存单据修改（销售出库、采购入库、产成品入库、材料出库）的挑单和回读 SQL。

挑单沿用对应生单用例的挑单 SQL，只把剩余数量的下限从 1 收紧到能生单 2 个（修改时减 1）。
回读尽量复用已有的回读（按原来的 bag 键传行 id），新写的只有行 id、备注和发货单累计出库。
标识符都是常量，id 只接受正整数再拼进 SQL。
"""

from __future__ import annotations

import re

from co.e2e.casespec.arap_gen_sql import mo_alloc_sql, qm_pin_sql
from co.e2e.casespec.pu_gen_sql import PU_READBACK, qmin_src_sql
from co.e2e.casespec.arap_gen_readback_sql import core_sql
from co.e2e.casespec.flow_pick_sql import po_recv_sql, so_ship_sql
from co.e2e.casespec.flow_read_sql import flow_sql

# 修改时写入的表头备注和行备注；回读按这个文本比对。
MEMO = "CO桥修改"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_INT_MAX = 2147483647


def _doc(raw: str) -> str:
    if _ID.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError("单据 id 不合法")
    return raw


def _tighten(sql: str, pairs: tuple[tuple[str, str], ...]) -> str:
    # 原挑单 SQL 改了写法时直接报错，免得悄悄挑到剩余不够 2 个的单据。
    for old, new in pairs:
        if sql.count(old) != 1:
            raise ValueError("挑单 SQL 已变，需同步库存修改的挑单")
        sql = sql.replace(old, new)
    return sql


# ---- 挑单 ----


def se_so_sql() -> str:
    # 销售订单行剩余可发 >= 2，且该仓库现存量 >= 2：发货 2 个、审核后生成销售出库。订单 ID|行 ID|仓库。
    pairs = (
        ("AND cs.iQuantity >= 1", "AND cs.iQuantity >= 2"),
        ("ISNULL(d.iFHQuantity, 0) >= 1", "ISNULL(d.iFHQuantity, 0) >= 2"),
    )
    return _tighten(so_ship_sql(), pairs)


def se_po_sql() -> str:
    # 采购订单行剩余可入库 >= 2。订单 POID|行 ID|仓库。
    pairs = (("ISNULL(d.iReceivedQTY, 0) >= 1", "ISNULL(d.iReceivedQTY, 0) >= 2"),)
    return _tighten(po_recv_sql(), pairs)


def se_qm_sql() -> str:
    # 来料检验单剩余可入库 >= 2。检验单 ID|仓库。
    pairs = (("ISNULL(q.FsumQuantity, 0) >= 1", "ISNULL(q.FsumQuantity, 0) >= 2"),)
    return _tighten(qmin_src_sql(), pairs)


def se_pq_sql() -> str:
    # 产品检验单：存货无单位组（每次按 1 入库），剩余合格数量 >= 2；订单行离满额还差 3 个以上，
    # 入库 2 个不会让 U8 自动关闭订单行（关了之后删除要 409）。检验单 ID|仓库|数量（恒为 1）。
    pairs = (
        ("ISNULL(i.iGroupType, 0) IN (0, 1)", "ISNULL(i.iGroupType, 0) = 0"),
        ("ISNULL(q.FsumQuantity, 0) >= n.qty", "ISNULL(q.FsumQuantity, 0) >= 2"),
        ("AND d.Status = 3", "AND d.Status = 3 AND ISNULL(d.Qty, 0) - ISNULL(d.QualifiedInQty, 0) >= 3"),
    )
    return _tighten(qm_pin_sql(), pairs)


# 挑单登记（同 returns_misc_sql.CUSTOM_PACKS 的形状）：前导键、按 | 拆开的键、是否必须是正整数 id、SQL。
# 材料出库允许超领，直接沿用材料出库生单的挑单。
SE_PACKS = (
    ("se_so", ("se_so", "se_so_line", "se_so_wh"), (True, True, False), se_so_sql),
    ("se_po", ("se_po", "se_po_line", "se_po_wh"), (True, True, False), se_po_sql),
    ("se_qm", ("se_qm", "se_qm_wh"), (True, False), se_qm_sql),
    ("se_pq", ("se_pq", "se_pq_wh", "se_pq_qty"), (True, False, False), se_pq_sql),
    ("se_mo", ("se_mo", "se_mo_line", "se_mo_wh"), (True, True, False), mo_alloc_sql),
)


# ---- 回读 ----


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(col: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({col}, 0)))"


def _first_line(table: str):
    def build(doc: str, _bag: dict[str, str]) -> str:
        return _one("CONVERT(varchar(20), MIN(b.AutoID))", f"FROM dbo.{table} AS b WHERE b.ID = {_doc(doc)}")

    return build


def _memo(head: str, body: str):
    # 表头备注和每一行的行备注都是 MEMO 时 1，否则 0。
    def build(doc: str, _bag: dict[str, str]) -> str:
        safe = _doc(doc)
        rows = f"(SELECT COUNT(*) FROM dbo.{body} AS b WHERE b.ID = {safe})"
        hit = f"(SELECT COUNT(*) FROM dbo.{body} AS b WHERE b.ID = {safe} AND b.cbMemo = N'{MEMO}')"
        expr = f"CASE WHEN h.cMemo = N'{MEMO}' AND {rows} > 0 AND {rows} = {hit} THEN N'1' ELSE N'0' END"
        return _one(expr, f"FROM dbo.{head} AS h WHERE h.ID = {safe}")

    return build


def _se_dout(doc: str, _bag: dict[str, str]) -> str:
    # doc 是发货单 DLID：全部行累计出库数量之和。
    return _one(_qty("SUM(s.fOutQuantity)"), f"FROM dbo.DispatchLists AS s WHERE s.DLID = {_doc(doc)}")


def _se_sofh(doc: str, bag: dict[str, str]) -> str:
    line = _doc(bag.get("se_so_line", ""))
    return _one(_qty("d.iFHQuantity"), f"FROM dbo.SO_SODetails AS d WHERE d.ID = {_doc(doc)} AND d.iSOsID = {line}")


def _se_po_got(doc: str, bag: dict[str, str]) -> str:
    return flow_sql("po_got", doc, {"po_recv_line": bag.get("se_po_line", "")})


def _se_mo_iss(doc: str, bag: dict[str, str]) -> str:
    return core_sql("mo_iss", doc, {"mo_alloc_line": bag.get("se_mo_line", "")})


_READBACK = {
    "se_sofh": _se_sofh,
    "se_dout": _se_dout,
    "se_line32": _first_line("rdrecords32"),
    "se_memo32": _memo("rdrecord32", "rdrecords32"),
    "se_po_got": _se_po_got,
    "se_line01": _first_line("rdrecords01"),
    "se_memo01": _memo("RdRecord01", "rdrecords01"),
    "se_qm_fsum": PU_READBACK["qmin_fsum"],
    "se_qm_valid": PU_READBACK["qmin_valid"],
    "se_qm_po": PU_READBACK["qmin_po"],
    "se_pq_qin": lambda doc, bag: core_sql("pin_qin", doc, bag),
    "se_pq_fsum": lambda doc, bag: core_sql("pin_fsum", doc, bag),
    "se_line10": _first_line("rdrecords10"),
    "se_memo10": _memo("rdrecord10", "rdrecords10"),
    "se_mo_iss": _se_mo_iss,
    "se_line11": _first_line("rdrecords11"),
    "se_memo11": _memo("rdrecord11", "rdrecords11"),
}


def has_stock_edit(name: str) -> bool:
    return name in _READBACK


def stock_edit_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def stock_edit_samples() -> list[tuple[str, str]]:
    bag = {"se_so_line": "100", "se_po_line": "100", "se_mo_line": "100"}
    rows = [("edit-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in SE_PACKS]
    rows.extend(("edit-" + name.replace("_", "-"), stock_edit_sql(name, "100", bag)) for name in _READBACK)
    return rows
