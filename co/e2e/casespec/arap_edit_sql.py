"""应收应付修改的回读。单据 id 只接受正整数，再拼进 SQL；表名、列名来自下面的常量。"""

from __future__ import annotations

import re

from co.e2e.casespec.arap_edit_bodies import DIGEST_UPD, KINDS

_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")

# kind → (表头, 主键, cFlag, cVouchType)
_HEAD = {
    "ar_receipt": ("dbo.Ap_CloseBill", "iID", "AR", "48"),
    "ap_payment": ("dbo.Ap_CloseBill", "iID", "AP", "49"),
    "ar_bill": ("dbo.Ap_Vouch", "Auto_ID", "AR", "R0"),
    "ap_bill": ("dbo.Ap_Vouch", "Auto_ID", "AP", "P0"),
}


def _doc(doc_id: str) -> str:
    if _ID.fullmatch(doc_id or "") is None:
        raise ValueError("单据 id 不合法")
    return doc_id


def _close(kind: str) -> bool:
    return _HEAD[kind][0] == "dbo.Ap_CloseBill"


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _where(kind: str, doc: str) -> str:
    _table, col, flag, vouch = _HEAD[kind]
    return f"WHERE h.{col} = {_doc(doc)} AND h.cFlag = N'{flag}' AND h.cVouchType = N'{vouch}'"


def _body_from(kind: str, doc: str) -> str:
    # 收付款单表体按 iID 挂表头，应收/应付单表体按 cLink。
    table = _HEAD[kind][0]
    if _close(kind):
        return f"FROM dbo.Ap_CloseBills AS b JOIN {table} AS h ON h.iID = b.iID " + _where(kind, doc)
    return f"FROM dbo.Ap_Vouchs AS b JOIN {table} AS h ON h.cLink = b.cLink " + _where(kind, doc)


def _line(kind: str, doc: str) -> str:
    col = "b.ID" if _close(kind) else "b.Auto_ID"
    return _one(f"CONVERT(varchar(20), MIN({col}))", _body_from(kind, doc))


def _count(kind: str, doc: str) -> str:
    return _one("CONVERT(varchar(12), COUNT(*))", _body_from(kind, doc))


def _body_sum(kind: str, doc: str) -> str:
    col = "b.iAmt" if _close(kind) else "b.iAmount"
    return _one(f"CONVERT(varchar(40), CONVERT(decimal(18,2), SUM({col})))", _body_from(kind, doc))


def _remain(kind: str, doc: str) -> str:
    frm = f"FROM {_HEAD[kind][0]} AS h " + _where(kind, doc)
    return _one("CONVERT(varchar(40), CONVERT(decimal(18,2), h.iRAmount))", frm)


def _digest(kind: str, doc: str) -> str:
    frm = f"FROM {_HEAD[kind][0]} AS h " + _where(kind, doc)
    return _one(f"CASE WHEN h.cDigest = N'{DIGEST_UPD}' THEN N'1' ELSE N'0' END", frm)


def _modifier(kind: str, doc: str) -> str:
    frm = f"FROM {_HEAD[kind][0]} AS h " + _where(kind, doc)
    return _one("ISNULL(LTRIM(RTRIM(h.cmodifier)), N'')", frm)


_READERS = {
    "_edit_line": _line,
    "_edit_cnt": _count,
    "_edit_sum": _body_sum,
    "_edit_ramt": _remain,
    "_edit_dig": _digest,
    "_edit_mod": _modifier,
}


def _reads() -> dict:
    found = {}
    for kind in KINDS:
        for suffix, reader in _READERS.items():
            found[kind + suffix] = lambda doc, _bag, k=kind, r=reader: r(k, doc)
    return found


_READBACK = _reads()


def has_edit_arap(name: str) -> bool:
    return name in _READBACK


def edit_arap_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def edit_arap_samples() -> list[tuple[str, str]]:
    return [("edit-" + name.replace("_", "-"), edit_arap_sql(name, "100", {})) for name in _READBACK]
