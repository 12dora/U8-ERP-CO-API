"""回读。单据 id 和行 id 只接受正整数，再拼进 SQL。"""

from __future__ import annotations

import re

_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")


def _doc(doc_id: str) -> str:
    if _ID.fullmatch(doc_id or "") is None:
        raise ValueError("单据 id 不合法")
    return doc_id


def _line(bag: dict[str, str], key: str) -> str:
    return _doc(bag.get(key, ""))


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _so_sum(doc: str, _bag: dict[str, str]) -> str:
    safe = _doc(doc)
    expr = "CONVERT(varchar(40), CONVERT(decimal(18,4), SUM(d.iSum)))"
    return _one(expr, f"FROM dbo.SO_SODetails AS d WHERE d.ID = {safe}")


def _so_line(doc: str, _bag: dict[str, str]) -> str:
    safe = _doc(doc)
    expr = "CONVERT(varchar(20), MIN(d.iSOsID))"
    return _one(expr, f"FROM dbo.SO_SODetails AS d WHERE d.ID = {safe}")


def _so_qty(doc: str, bag: dict[str, str]) -> str:
    safe = _doc(doc)
    line = _line(bag, "so_line")
    expr = "CONVERT(varchar(40), CONVERT(decimal(18,4), d.iQuantity))"
    return _one(expr, f"FROM dbo.SO_SODetails AS d WHERE d.ID = {safe} AND d.iSOsID = {line}")


def _so_closer(doc: str, _bag: dict[str, str]) -> str:
    safe = _doc(doc)
    expr = "ISNULL(LTRIM(RTRIM(cCloser)), N'')"
    return _one(expr, f"FROM dbo.SO_SOMain WHERE ID = {safe}")


def _so_open_line(doc: str, _bag: dict[str, str]) -> str:
    safe = _doc(doc)
    expr = "CONVERT(varchar(20), MIN(d.iSOsID))"
    where = f"FROM dbo.SO_SODetails AS d WHERE d.ID = {safe} AND LTRIM(RTRIM(ISNULL(d.cSCloser, N''))) = N''"
    return _one(expr, where)


def _so_fh(doc: str, bag: dict[str, str]) -> str:
    safe = _doc(doc)
    line = _line(bag, "so_ship_line")
    expr = "CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL(d.iFHQuantity, 0)))"
    return _one(expr, f"FROM dbo.SO_SODetails AS d WHERE d.ID = {safe} AND d.iSOsID = {line}")


def _po_sum(doc: str, _bag: dict[str, str]) -> str:
    safe = _doc(doc)
    expr = "CONVERT(varchar(40), CONVERT(decimal(18,4), SUM(d.iSum)))"
    return _one(expr, f"FROM dbo.PO_Podetails AS d WHERE d.POID = {safe}")


def _po_line(doc: str, _bag: dict[str, str]) -> str:
    safe = _doc(doc)
    expr = "CONVERT(varchar(20), MIN(d.ID))"
    return _one(expr, f"FROM dbo.PO_Podetails AS d WHERE d.POID = {safe}")


def _po_closer(doc: str, _bag: dict[str, str]) -> str:
    safe = _doc(doc)
    expr = "ISNULL(LTRIM(RTRIM(cCloser)), N'')"
    return _one(expr, f"FROM dbo.PO_Pomain WHERE POID = {safe}")


def _po_open_line(doc: str, _bag: dict[str, str]) -> str:
    safe = _doc(doc)
    expr = "CONVERT(varchar(20), MIN(d.ID))"
    where = f"FROM dbo.PO_Podetails AS d WHERE d.POID = {safe} AND LTRIM(RTRIM(ISNULL(d.cbCloser, N''))) = N''"
    return _one(expr, where)


def _po_got(doc: str, bag: dict[str, str]) -> str:
    safe = _doc(doc)
    line = _line(bag, "po_recv_line")
    expr = "CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL(d.iReceivedQTY, 0)))"
    return _one(expr, f"FROM dbo.PO_Podetails AS d WHERE d.POID = {safe} AND d.ID = {line}")


def _oi_line(doc: str, _bag: dict[str, str]) -> str:
    safe = _doc(doc)
    expr = "CONVERT(varchar(20), MIN(b.AutoID))"
    return _one(expr, f"FROM dbo.rdrecords08 AS b WHERE b.ID = {safe}")


def _oi_qty(doc: str, bag: dict[str, str]) -> str:
    safe = _doc(doc)
    line = _line(bag, "oi_line")
    expr = "CONVERT(varchar(40), CONVERT(decimal(18,4), b.iQuantity))"
    return _one(expr, f"FROM dbo.rdrecords08 AS b WHERE b.ID = {safe} AND b.AutoID = {line}")


def _tv_line(doc: str, _bag: dict[str, str]) -> str:
    safe = _doc(doc)
    expr = "CONVERT(varchar(20), MIN(s.autoID))"
    return _one(expr, f"FROM dbo.TransVouchs AS s WHERE s.ID = {safe}")


def _tv_rows(doc: str, _bag: dict[str, str]) -> str:
    # 修改后应有两行（原行改数量、新增一行），且每行都带调拨单号。
    safe = _doc(doc)
    expr = "CONVERT(varchar(12), COUNT(s.autoID))"
    return _one(expr, f"FROM dbo.TransVouchs AS s WHERE s.ID = {safe} AND LTRIM(RTRIM(ISNULL(s.cTVCode, N''))) <> N''")


def _inv_from(doc: str, bag: dict[str, str]) -> str:
    safe = _doc(doc)
    line = _line(bag, "inv_src_line")
    return f"FROM dbo.DispatchLists AS d WHERE d.DLID = {safe} AND d.iDLsID = {line}"


def _inv_settle(doc: str, bag: dict[str, str]) -> str:
    expr = "CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL(d.iSettleQuantity, 0)))"
    return _one(expr, _inv_from(doc, bag))


def _inv_qty(doc: str, bag: dict[str, str]) -> str:
    # 与挑单同一截断，保证请求数量不超过剩余可开票数量。
    expr = (
        "CONVERT(varchar(40), CONVERT(decimal(18,4), "
        "ROUND(ISNULL(d.iQuantity, 0) - ISNULL(d.iSettleQuantity, 0), 4, 1)))"
    )
    return _one(expr, _inv_from(doc, bag))


def _inv_checker(doc: str, _bag: dict[str, str]) -> str:
    safe = _doc(doc)
    expr = "ISNULL(LTRIM(RTRIM(cChecker)), N'')"
    return _one(expr, f"FROM dbo.SaleBillVouch WHERE SBVID = {safe}")


def _inv_gone(doc: str, _bag: dict[str, str]) -> str:
    safe = _doc(doc)
    expr = "CONVERT(varchar(12), COUNT(SBVID))"
    return _one(expr, f"FROM dbo.SaleBillVouch WHERE SBVID = {safe}")


def _out_rows(doc: str, _bag: dict[str, str]) -> str:
    safe = _doc(doc)
    expr = "CONVERT(varchar(12), COUNT(r.ID))"
    return _one(expr, f"FROM dbo.rdrecord32 AS r WHERE r.cDLCode = {safe}")


def _so_line_state(doc: str, bag: dict[str, str]) -> str:
    safe = _doc(doc)
    line = _line(bag, "sale_line")
    expr = "ISNULL(LTRIM(RTRIM(d.cSCloser)), N'') + N'|' + ISNULL(LTRIM(RTRIM(m.cCloser)), N'')"
    frm = (
        "FROM dbo.SO_SODetails AS d JOIN dbo.SO_SOMain AS m ON m.ID = d.ID "
        f"WHERE d.ID = {safe} AND d.iSOsID = {line}"
    )
    return _one(expr, frm)


def _po_line_state(doc: str, bag: dict[str, str]) -> str:
    safe = _doc(doc)
    line = _line(bag, "po_one")
    expr = "ISNULL(LTRIM(RTRIM(d.cbCloser)), N'') + N'|' + ISNULL(LTRIM(RTRIM(m.cCloser)), N'')"
    frm = (
        "FROM dbo.PO_Podetails AS d JOIN dbo.PO_Pomain AS m ON m.POID = d.POID "
        f"WHERE d.POID = {safe} AND d.ID = {line}"
    )
    return _one(expr, frm)


def _so_other(doc: str, bag: dict[str, str]) -> str:
    safe = _doc(doc)
    line = _line(bag, "so_line")
    expr = "CONVERT(varchar(20), MIN(d.iSOsID))"
    frm = f"FROM dbo.SO_SODetails AS d WHERE d.ID = {safe} AND d.iSOsID <> {line}"
    return _one(expr, frm)


def _so_back(doc: str, bag: dict[str, str]) -> str:
    safe = _doc(doc)
    line = _line(bag, "so_line")
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(12), (
    SELECT COUNT(x.iSOsID) FROM dbo.SO_SODetails AS x WHERE x.ID = {safe}
  )) + N'|'
  + CONVERT(varchar(40), CONVERT(decimal(18,4), d.iQuantity)) + N'|'
  + CASE WHEN d.iSum > 0 THEN N'1' ELSE N'0' END + N'|'
  + ISNULL(LTRIM(RTRIM(m.cmodifier)), N'') AS v
FROM dbo.SO_SOMain AS m
JOIN dbo.SO_SODetails AS d ON d.ID = m.ID AND d.iSOsID = {line}
WHERE m.ID = {safe};
"""


def _oi_price(doc: str, _bag: dict[str, str]) -> str:
    safe = _doc(doc)
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(12), CASE
  WHEN COUNT(b.AutoID) > 0
   AND SUM(CASE WHEN b.iUnitCost IS NOT NULL
     AND ABS(CONVERT(decimal(18,4), b.iPrice) - ROUND(b.iQuantity * b.iUnitCost, 2)) < 0.001
     THEN 1 ELSE 0 END) = COUNT(b.AutoID)
  THEN 1 ELSE 0 END) AS v
FROM dbo.rdrecords08 AS b
WHERE b.ID = {safe};
"""


def _tv_kids(doc: str, _bag: dict[str, str]) -> str:
    safe = _doc(doc)
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(12),
  (SELECT COUNT(a.ID) FROM dbo.RdRecord09 AS a
   JOIN dbo.TransVouch AS t ON t.cTVCode = a.cBusCode
   WHERE t.ID = {safe} AND a.cSource = N'调拨')
  + (SELECT COUNT(b.ID) FROM dbo.RdRecord08 AS b
   JOIN dbo.TransVouch AS t ON t.cTVCode = b.cBusCode
   WHERE t.ID = {safe} AND b.cSource = N'调拨')) AS v;
"""


_READBACK = {
    "so_sum": _so_sum,
    "so_line": _so_line,
    "so_qty": _so_qty,
    "so_other": _so_other,
    "so_back": _so_back,
    "so_closer": _so_closer,
    "so_open_line": _so_open_line,
    "so_line_state": _so_line_state,
    "so_fh": _so_fh,
    "po_sum": _po_sum,
    "po_line": _po_line,
    "po_closer": _po_closer,
    "po_open_line": _po_open_line,
    "po_line_state": _po_line_state,
    "po_got": _po_got,
    "oi_line": _oi_line,
    "oi_qty": _oi_qty,
    "oi_price": _oi_price,
    "tv_line": _tv_line,
    "tv_rows": _tv_rows,
    "tv_kids": _tv_kids,
    "out_rows": _out_rows,
    "inv_settle": _inv_settle,
    "inv_qty": _inv_qty,
    "inv_checker": _inv_checker,
    "inv_gone": _inv_gone,
}


def flow_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def read_samples(bag: dict[str, str]) -> list[tuple[str, str]]:
    return [("flow-" + name.replace("_", "-"), flow_sql(name, "100", bag)) for name in _READBACK]
