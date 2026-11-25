"""到货单关闭 / 打开用例（arrival_close）的回读 SQL。

只读。返回一行 ROW:a|b|…，读不到返回空。id 只接受正整数（safe_id），再拼进 SQL。
挑单沿用 pu_settle_manual_sql.pick_sql（不批次、不保质期、不需来料检验的外购存货）。
"""

from __future__ import annotations

from co.e2e.casespec.stock_misc_sql import safe_id

# close_state_sql、lines_state_sql 的列数。
STATE_COLS = 5
LINE_COLS = 5


def _lines(doc: str, cond: str) -> str:
    where = f"s.ID = {doc}" + (" AND " + cond if cond else "")
    return f"CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.PU_ArrivalVouchs AS s WHERE {where}))"


def close_state_sql(doc: int) -> str:
    """到货单关闭状态：表头 ccloser|表头 dclosedate 有值 1 / 0|行数|有 cbcloser 的行数|有 dlineclosedate 的行数。"""
    arr = safe_id(str(doc))
    expr = (
        "ISNULL(LTRIM(RTRIM(h.ccloser)), N'') + N'|' + CASE WHEN h.dclosedate IS NULL THEN N'0' ELSE N'1' END"
        " + N'|' + " + _lines(arr, "")
        + " + N'|' + " + _lines(arr, "ISNULL(LTRIM(RTRIM(s.cbcloser)), N'') <> N''")
        + " + N'|' + " + _lines(arr, "s.dlineclosedate IS NOT NULL")
    )
    return f"SET NOCOUNT ON;\nSELECT N'ROW:' + {expr} AS v\nFROM dbo.PU_ArrivalVouch AS h WHERE h.ID = {arr};\n"


def po_lines_sql(po: int) -> str:
    """采购订单的行数|第一行 ID|最后一行 ID（arrival-close-lines 建两行订单）。"""
    doc = safe_id(str(po))
    expr = (
        "CONVERT(varchar(10), COUNT(*)) + N'|' + CONVERT(varchar(20), ISNULL(MIN(d.ID), 0))"
        " + N'|' + CONVERT(varchar(20), ISNULL(MAX(d.ID), 0))"
    )
    return f"SET NOCOUNT ON;\nSELECT N'ROW:' + {expr} AS v\nFROM dbo.PO_Podetails AS d WHERE d.POID = {doc};\n"


def arrival_lines_sql(doc: int) -> str:
    """到货单的行数|第一行 Autoid|最后一行 Autoid。"""
    arr = safe_id(str(doc))
    expr = (
        "CONVERT(varchar(10), COUNT(*)) + N'|' + CONVERT(varchar(20), ISNULL(MIN(s.Autoid), 0))"
        " + N'|' + CONVERT(varchar(20), ISNULL(MAX(s.Autoid), 0))"
    )
    return f"SET NOCOUNT ON;\nSELECT N'ROW:' + {expr} AS v\nFROM dbo.PU_ArrivalVouchs AS s WHERE s.ID = {arr};\n"


def lines_state_sql(doc: int, first: int, second: int) -> str:
    """按行关闭的回读：表头 ccloser|第一行有 cbcloser 1 / 0|第一行有 dlineclosedate 1 / 0|第二行同上两列。"""
    arr = safe_id(str(doc))
    one, two = safe_id(str(first)), safe_id(str(second))
    shut = "ISNULL(LTRIM(RTRIM(s.cbcloser)), N'') <> N''"
    expr = (
        "ISNULL(LTRIM(RTRIM(h.ccloser)), N'')"
        + " + N'|' + " + _lines(arr, f"s.Autoid = {one} AND {shut}")
        + " + N'|' + " + _lines(arr, f"s.Autoid = {one} AND s.dlineclosedate IS NOT NULL")
        + " + N'|' + " + _lines(arr, f"s.Autoid = {two} AND {shut}")
        + " + N'|' + " + _lines(arr, f"s.Autoid = {two} AND s.dlineclosedate IS NOT NULL")
    )
    return f"SET NOCOUNT ON;\nSELECT N'ROW:' + {expr} AS v\nFROM dbo.PU_ArrivalVouch AS h WHERE h.ID = {arr};\n"


def arrival_close_samples() -> list[tuple[str, str]]:
    return [
        ("ac-arrival-close-state", close_state_sql(100)),
        ("ac-arrival-close-po-lines", po_lines_sql(100)),
        ("ac-arrival-close-lines", arrival_lines_sql(100)),
        ("ac-arrival-close-line-state", lines_state_sql(100, 101, 102)),
    ]
