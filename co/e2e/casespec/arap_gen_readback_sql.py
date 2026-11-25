"""新建和生单的回读。单据 id 和 bag 里的明细 id 只接受正整数，再拼进 SQL。"""

from __future__ import annotations

import re

from co.e2e.casespec.pu_gen_sql import PU_READBACK

_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")

# kind → (表头, 主键, cFlag, cVouchType, 应收/应付明细表)
ARAP = {
    "ar_receipt": ("dbo.Ap_CloseBill", "iID", "AR", "48", "dbo.Ar_Detail"),
    "ap_payment": ("dbo.Ap_CloseBill", "iID", "AP", "49", "dbo.Ap_Detail"),
    "ar_bill": ("dbo.Ap_Vouch", "Auto_ID", "AR", "R0", "dbo.Ar_Detail"),
    "ap_bill": ("dbo.Ap_Vouch", "Auto_ID", "AP", "P0", "dbo.Ap_Detail"),
}


def _doc(doc_id: str) -> str:
    if _ID.fullmatch(doc_id or "") is None:
        raise ValueError("单据 id 不合法")
    return doc_id


def _line(bag: dict[str, str], key: str) -> str:
    return _doc(bag.get(key, ""))


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _where(kind: str, doc: str) -> str:
    _table, col, flag, vouch, _detail = ARAP[kind]
    safe = _doc(doc)
    return f"WHERE h.{col} = {safe} AND h.cFlag = N'{flag}' AND h.cVouchType = N'{vouch}'"


def _head(kind: str, doc: str) -> str:
    return f"FROM {ARAP[kind][0]} AS h " + _where(kind, doc)


def _arap_checker(kind: str, doc: str) -> str:
    return _one("ISNULL(LTRIM(RTRIM(h.cCheckMan)), N'')", _head(kind, doc))


def _arap_amount(kind: str, doc: str) -> str:
    return _one("CONVERT(varchar(40), CONVERT(decimal(18,2), h.iAmount))", _head(kind, doc))


def _arap_voucher(kind: str, doc: str) -> str:
    col = "h.cPzID" if ARAP[kind][0] == "dbo.Ap_CloseBill" else "h.cPZid"
    return _one(f"ISNULL(LTRIM(RTRIM({col})), N'')", _head(kind, doc))


def _arap_detail(kind: str, doc: str) -> str:
    # 审核写进应收/应付明细的行（cVouchType+cVouchID 是本单），弃审后应删光。
    table, _col, _flag, _vouch, detail = ARAP[kind]
    expr = "CONVERT(varchar(12), COUNT(a.Auto_ID))"
    frm = (
        f"FROM {detail} AS a JOIN {table} AS h\n"
        "  ON a.cVouchType = h.cVouchType AND a.cVouchID = h.cVouchID AND a.cFlag = h.cFlag\n"
        + _where(kind, doc)
    )
    return _one(expr, frm)


def _arap_gone(kind: str, doc: str) -> str:
    table, col, _flag, _vouch, _detail = ARAP[kind]
    safe = _doc(doc)
    body = f" + (SELECT COUNT(*) FROM dbo.Ap_CloseBills AS b WHERE b.iID = {safe})" if col == "iID" else ""
    expr = f"CONVERT(varchar(12), (SELECT COUNT(*) FROM {table} AS h WHERE h.{col} = {safe}){body})"
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v;\n"


def _mo_iss(doc: str, bag: dict[str, str]) -> str:
    safe = _doc(doc)
    line = _line(bag, "mo_alloc_line")
    expr = "CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL(a.IssQty, 0)))"
    frm = (
        "FROM dbo.mom_moallocate AS a JOIN dbo.mom_orderdetail AS d ON d.MoDId = a.MoDId\n"
        f"WHERE d.MoId = {safe} AND a.AllocateId = {line}"
    )
    return _one(expr, frm)


def _mo_out_link(doc: str, bag: dict[str, str]) -> str:
    safe = _doc(doc)
    line = _line(bag, "mo_alloc_line")
    expr = "CONVERT(varchar(12), COUNT(s.AutoID))"
    return _one(expr, f"FROM dbo.rdrecords11 AS s WHERE s.ID = {safe} AND s.iMPoIds = {line}")


def _pin_qin(doc: str, _bag: dict[str, str]) -> str:
    safe = _doc(doc)
    expr = "CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL(d.QualifiedInQty, 0)))"
    frm = f"FROM dbo.QMCHECKVOUCHER AS q JOIN dbo.mom_orderdetail AS d ON d.MoDId = q.SOURCEAUTOID\nWHERE q.ID = {safe}"
    return _one(expr, frm)


def _pin_fsum(doc: str, _bag: dict[str, str]) -> str:
    safe = _doc(doc)
    expr = "CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL(q.FsumQuantity, 0)))"
    return _one(expr, f"FROM dbo.QMCHECKVOUCHER AS q WHERE q.ID = {safe}")


def _pin_link(doc: str, bag: dict[str, str]) -> str:
    safe = _doc(doc)
    check = _line(bag, "qm_pin")
    expr = "CONVERT(varchar(12), COUNT(s.AutoID))"
    return _one(expr, f"FROM dbo.rdrecords10 AS s WHERE s.ID = {safe} AND s.iCheckIdBaks = {check}")


def _mo_count(doc: str, cond: str) -> str:
    safe = _doc(doc)
    expr = "CONVERT(varchar(12), COUNT(d.MoDId))"
    return _one(expr, f"FROM dbo.mom_orderdetail AS d WHERE d.MoId = {safe} AND {cond}")


def _mo_released(doc: str, _bag: dict[str, str]) -> str:
    return _mo_count(doc, "d.Status = 3")


def _mo_unreleased(doc: str, _bag: dict[str, str]) -> str:
    return _mo_count(doc, "d.Status <> 3")


def _arap_reads() -> dict:
    found = {}
    for kind in ARAP:
        found[kind + "_chk"] = lambda doc, _bag, k=kind: _arap_checker(k, doc)
        found[kind + "_amt"] = lambda doc, _bag, k=kind: _arap_amount(k, doc)
        found[kind + "_pz"] = lambda doc, _bag, k=kind: _arap_voucher(k, doc)
        found[kind + "_det"] = lambda doc, _bag, k=kind: _arap_detail(k, doc)
        found[kind + "_gone"] = lambda doc, _bag, k=kind: _arap_gone(k, doc)
    return found


_READBACK = {
    "mo_iss": _mo_iss,
    "mo_out_link": _mo_out_link,
    "pin_qin": _pin_qin,
    "pin_fsum": _pin_fsum,
    "pin_link": _pin_link,
    "mo_released": _mo_released,
    "mo_unreleased": _mo_unreleased,
}
_READBACK.update(_arap_reads())
_READBACK.update(PU_READBACK)


def has_core(name: str) -> bool:
    return name in _READBACK


def core_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def core_read_samples() -> list[tuple[str, str]]:
    bag = {"mo_alloc_line": "100", "qm_pin": "100", "pinv_line": "100", "arr_line": "100"}
    return [("core-" + name.replace("_", "-"), core_sql(name, "100", bag)) for name in _READBACK]
