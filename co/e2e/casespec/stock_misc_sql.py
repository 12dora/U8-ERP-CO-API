"""库存零散用例的挑单与回读 SQL：销售出库按行部分生成、有货位记录的其他入库删除。

标识符都是常量；id 只接受正整数，编码只接受 [0-9A-Za-z._-]，再拼进 SQL。
"""

from __future__ import annotations

import re

_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_CODE = re.compile(r"^[0-9A-Za-z._-]{1,20}\Z")
_INT_MAX = 2147483647


def safe_id(raw: str) -> str:
    if _ID.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError("单据 id 不合法")
    return raw


def safe_code(raw: str) -> str:
    if _CODE.fullmatch(raw or "") is None:
        raise ValueError("编码不合法")
    return raw


def _blank(col: str) -> str:
    return f"LTRIM(RTRIM(ISNULL({col}, N''))) = N''"


def _left(alias: str) -> str:
    return f"ISNULL({alias}.iQuantity, 0) - ISNULL({alias}.fOutQuantity, 0)"


# ---- 挑单 ----


def sale_out_part_src_sql() -> str:
    # 已审核、未关闭的蓝字发货单（非期初、不在审批流里）上一行剩余 >= 1，且本单还有别的未出库行或本行剩余 >= 2，
    # 这样只出 1 个一定是部分出库。桥先整单 MakeOutVouch，所以全部未出库行都要能出：有仓库、仓库不管货位、
    # 存货不管批次 / 保质期 / 序列号、不是服务，现存量不小于剩余。发货单 DLID|发货行 iDLsID。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), h.DLID) + N'|' + CONVERT(varchar(20), x.iDLsID) AS v
FROM dbo.DispatchList AS h
JOIN dbo.DispatchLists AS x ON x.DLID = h.DLID
WHERE h.cVouchType = N'05'
  AND ISNULL(h.bReturnFlag, 0) = 0
  AND ISNULL(h.bFirst, 0) = 0
  AND ISNULL(h.iswfcontrolled, 0) = 0
  AND NOT {_blank("h.cVerifier")}
  AND {_blank("h.cCloser")}
  AND {_left("x")} >= 1
  AND ({_left("x")} >= 2
       OR EXISTS (SELECT 1 FROM dbo.DispatchLists AS o
                  WHERE o.DLID = h.DLID AND o.iDLsID <> x.iDLsID AND {_left("o")} > 0))
  AND NOT EXISTS (
    SELECT 1 FROM dbo.DispatchLists AS b
    JOIN dbo.Inventory AS i ON i.cInvCode = b.cInvCode
    LEFT JOIN dbo.Warehouse AS w ON w.cWhCode = b.cWhCode
    WHERE b.DLID = h.DLID
      AND {_left("b")} > 0
      AND (w.cWhCode IS NULL OR w.bWhPos = 1 OR ISNULL(w.bFreeze, 0) = 1
           OR ISNULL(i.bInvBatch, 0) = 1 OR ISNULL(i.bInvQuality, 0) = 1 OR ISNULL(i.bSerial, 0) = 1
           OR ISNULL(i.bService, 0) = 1
           OR ISNULL((SELECT SUM(c.iQuantity) FROM dbo.CurrentStock AS c
                      WHERE c.cWhCode = b.cWhCode AND c.cInvCode = b.cInvCode), 0) < {_left("b")}))
ORDER BY h.DLID DESC, x.iDLsID;
"""


def _no_free(alias: str) -> str:
    return " AND ".join(f"ISNULL({alias}.bFree{i}, 0) = 0" for i in range(1, 11))


def binned_in_src_sql() -> str:
    # 货位管理仓库（bWhPos=1）的末级货位；收发类别、部门取最近一张来源为库存的其他入库；存货取该仓库现存量里
    # 不管批次 / 保质期 / 序列号、无自由项、无计量单位组的。首列是现存量行 AutoID，只作回读前的占位 id。
    # 现存量行 AutoID|仓库|收发类别|部门|存货|货位。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), v.AutoID) + N'|' + w.cWhCode + N'|' + r.cRdCode + N'|'
  + r.cDepCode + N'|' + v.cInvCode + N'|' + p.cPosCode AS v
FROM dbo.Warehouse AS w
JOIN dbo.Position AS p ON p.cWhCode = w.cWhCode AND p.bPosEnd = 1
CROSS APPLY (
  SELECT TOP (1) h.cRdCode, h.cDepCode FROM dbo.RdRecord08 AS h
  JOIN dbo.Rd_Style AS s ON s.cRdCode = h.cRdCode
  WHERE s.bRdFlag = 1 AND s.bRdEnd = 1 AND ISNULL(h.cSource, N'') = N'库存'
    AND NOT {_blank("h.cDepCode")}
  ORDER BY h.ID DESC
) AS r
CROSS APPLY (
  SELECT TOP (1) c.AutoID, c.cInvCode FROM dbo.CurrentStock AS c
  JOIN dbo.Inventory AS i ON i.cInvCode = c.cInvCode
  WHERE c.cWhCode = w.cWhCode
    AND ISNULL(i.bInvBatch, 0) = 0 AND ISNULL(i.bInvQuality, 0) = 0 AND ISNULL(i.bSerial, 0) = 0
    AND ISNULL(i.iGroupType, 0) = 0 AND {_no_free("i")}
    AND LTRIM(RTRIM(c.cInvCode)) NOT LIKE N'%[^0-9A-Za-z._-]%'
  ORDER BY c.cInvCode
) AS v
WHERE w.bWhPos = 1
  AND ISNULL(w.bFreeze, 0) = 0
  AND LTRIM(RTRIM(p.cPosCode)) NOT LIKE N'%[^0-9A-Za-z._-]%'
  AND LTRIM(RTRIM(r.cRdCode)) NOT LIKE N'%[^0-9A-Za-z._-]%'
  AND LTRIM(RTRIM(r.cDepCode)) NOT LIKE N'%[^0-9A-Za-z._-]%'
ORDER BY w.cWhCode, p.cPosCode;
"""


# 挑单登记（同 returns_misc_sql.CUSTOM_PACKS 的形状）：前导键、按 | 拆开的键、是否必须是正整数 id、SQL。
STOCK_MISC_PACKS = (
    ("spo_src", ("spo_src", "spo_line"), (True, True), sale_out_part_src_sql),
    (
        "bin_src",
        ("bin_src", "bin_wh", "bin_rd", "bin_dep", "bin_inv", "bin_pos"),
        (True, False, False, False, False, False),
        binned_in_src_sql,
    ),
)


# ---- 回读 ----


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({expr}, 0)))"


def _count(frm: str) -> str:
    return _one("CONVERT(varchar(20), COUNT(*))", frm)


def _spo_out(doc: str, bag: dict[str, str]) -> str:
    # doc 是发货单 DLID：请求行的累计出库数量。
    line = safe_id(bag.get("spo_line", ""))
    return _one(_qty("x.fOutQuantity"), f"FROM dbo.DispatchLists AS x WHERE x.DLID = {safe_id(doc)} AND x.iDLsID = {line}")


def _spo_rest(doc: str, bag: dict[str, str]) -> str:
    # doc 是发货单 DLID：其余行累计出库数量合计（只有一行时是 0）。
    line = safe_id(bag.get("spo_line", ""))
    frm = f"FROM dbo.DispatchLists AS x WHERE x.DLID = {safe_id(doc)} AND x.iDLsID <> {line}"
    return _one(_qty("SUM(x.fOutQuantity)"), frm)


def _spo_rows(doc: str, _bag: dict[str, str]) -> str:
    # doc 是发货单 DLID：指向本发货单各行的销售出库行数。部分出库后只应多出请求的那 1 行。
    frm = f"FROM dbo.rdrecords32 AS s JOIN dbo.DispatchLists AS x ON x.iDLsID = s.iDLsID WHERE x.DLID = {safe_id(doc)}"
    return _count(frm)


def _spo_link(doc: str, bag: dict[str, str]) -> str:
    # doc 是新销售出库单 ID：只有一行、挂请求的发货行、数量 1 时是 1，否则 0。
    safe = safe_id(doc)
    line = safe_id(bag.get("spo_line", ""))
    rows = f"(SELECT COUNT(*) FROM dbo.rdrecords32 AS s WHERE s.ID = {safe})"
    hit = f"(SELECT COUNT(*) FROM dbo.rdrecords32 AS s WHERE s.ID = {safe} AND s.iDLsID = {line} AND s.iQuantity = 1)"
    expr = f"CASE WHEN {rows} = 1 AND {hit} = 1 THEN N'1' ELSE N'0' END"
    return _one(expr, f"FROM dbo.RdRecord32 AS h WHERE h.ID = {safe}")


def _bin_psum(doc: str, bag: dict[str, str]) -> str:
    # doc 只作占位（挑单的现存量行或新入库单 ID）：该仓库、货位、存货的货位存量（无批次、无自由项）。
    safe_id(doc)
    wh = safe_code(bag.get("bin_wh", ""))
    pos = safe_code(bag.get("bin_pos", ""))
    inv = safe_code(bag.get("bin_inv", ""))
    frm = (
        f"FROM dbo.InvPositionSum AS p WHERE p.cWhCode = N'{wh}' AND p.cPosCode = N'{pos}'"
        f" AND p.cInvCode = N'{inv}' AND ISNULL(p.cBatch, N'') = N''"
    )
    return _one(_qty("SUM(p.iQuantity)"), frm)


def _bin_pos(doc: str, _bag: dict[str, str]) -> str:
    # doc 是其他入库单 ID：InvPosition 里该单据的行数。
    return _count(f"FROM dbo.InvPosition AS p WHERE p.RdID = {safe_id(doc)} AND p.cvouchtype = N'08'")


def _bin_head(doc: str, _bag: dict[str, str]) -> str:
    return _count(f"FROM dbo.RdRecord08 AS h WHERE h.ID = {safe_id(doc)}")


_READBACK = {
    "spo_out": _spo_out,
    "spo_rest": _spo_rest,
    "spo_rows": _spo_rows,
    "spo_link": _spo_link,
    "bin_psum": _bin_psum,
    "bin_pos": _bin_pos,
    "bin_head": _bin_head,
}


def has_stock_misc(name: str) -> bool:
    return name in _READBACK


def stock_misc_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def stock_misc_samples() -> list[tuple[str, str]]:
    bag = {"spo_line": "100", "bin_wh": "01", "bin_pos": "A01", "bin_inv": "0001"}
    rows = [("edit-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in STOCK_MISC_PACKS]
    rows.extend(("edit-" + name.replace("_", "-"), fn("100", bag)) for name, fn in _READBACK.items())
    return rows
