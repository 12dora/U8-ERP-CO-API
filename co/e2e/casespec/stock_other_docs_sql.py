"""形态转换单、调拨申请单、盘点单（stock_other_docs）的挑单与回读 SQL。

SM_PACKS 进挑单，has_sm / sm_sql 进 stash / pred 的回读，sm_samples() 进样例 SQL（接线在 wiring 汇总模块）。
标识符都是常量，id 只接受正整数再拼进 SQL；挑出的编码只进请求正文，不拼进 SQL。
存货只挑不管批次、不管保质期、没有序列号、没有自由项、没有单位组的；仓库只挑不管货位、未冻结的。
"""

from __future__ import annotations

from co.e2e.casespec.mo_close_sql import safe_id

_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"


def _code(col: str) -> str:
    return f"LTRIM(RTRIM({col})) {_SAFE} AND LEN(LTRIM(RTRIM({col}))) BETWEEN 1 AND 12"


def _no_free(alias: str) -> str:
    cols = " + ".join(f"ISNULL({alias}.cFree{i}, N'')" for i in range(1, 11))
    flags = " AND ".join(f"ISNULL(i.bFree{i}, 0) = 0" for i in range(1, 11))
    return f"LTRIM(RTRIM({cols})) = N'' AND {flags}"


def _simple_inv() -> str:
    # i 是 Inventory，c 是 CurrentStock。
    return (
        "ISNULL(i.bInvBatch, 0) = 0 AND ISNULL(i.bInvQuality, 0) = 0 AND ISNULL(i.bSerial, 0) = 0"
        f" AND ISNULL(i.iGroupType, 0) = 0 AND i.dEDate IS NULL AND {_no_free('c')} AND {_code('c.cInvCode')}"
    )


def _plain_wh(alias: str) -> str:
    return f"ISNULL({alias}.bWhPos, 0) = 0 AND ISNULL({alias}.bFreeze, 0) = 0 AND {_code(alias + '.cWhCode')}"


def _stock_inv(wh: str, extra: str, order: str) -> str:
    # 某仓库里现存量（扣去待出）不少于 1 的简单存货。
    return f"""SELECT TOP (1) LTRIM(RTRIM(c.cInvCode)) AS inv
  FROM dbo.CurrentStock AS c
  JOIN dbo.Inventory AS i ON i.cInvCode = c.cInvCode
  WHERE c.cWhCode = {wh} AND ISNULL(c.iQuantity, 0) - ISNULL(c.fOutQuantity, 0) >= 1 AND {_simple_inv()}{extra}
  ORDER BY {order}"""


# ---- 挑单 ----


def sm_av_sql() -> str:
    # 形态转换：同一仓库里两种有现存量的简单存货（转换前扣库存，转换后也挑有库存的，免得存货不在该仓库的对照里）、一个末级部门。
    # 仓库|转换前存货|转换后存货|部门。
    first = _stock_inv("w.cWhCode", "", "c.iQuantity DESC, c.cInvCode")
    second = _stock_inv("w.cWhCode", " AND c.cInvCode <> a.inv", "c.cInvCode")
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(w.cWhCode)) + N'|' + a.inv + N'|' + b.inv + N'|' + d.dep AS v
FROM dbo.Warehouse AS w
CROSS APPLY ({first}) AS a
CROSS APPLY ({second}) AS b
CROSS APPLY (
  SELECT TOP (1) LTRIM(RTRIM(p.cDepCode)) AS dep FROM dbo.Department AS p
  WHERE p.bDepEnd = 1 AND p.dDepEndDate IS NULL AND {_code('p.cDepCode')}
  ORDER BY p.cDepCode
) AS d
WHERE {_plain_wh('w')}
ORDER BY w.cWhCode;
"""


def sm_tr_sql() -> str:
    # 调拨申请：两个不同的仓库、调出仓库里有现存量的简单存货（申请单不动库存，存货仍按调出仓库挑）。调出|调入|存货。
    first = _stock_inv("w.cWhCode", "", "c.cInvCode")
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(w.cWhCode)) + N'|' + LTRIM(RTRIM(t.cWhCode)) + N'|' + a.inv AS v
FROM dbo.Warehouse AS w
CROSS APPLY ({first}) AS a
JOIN dbo.Warehouse AS t ON t.cWhCode <> w.cWhCode AND {_plain_wh('t')}
WHERE {_plain_wh('w')}
ORDER BY w.cWhCode, t.cWhCode;
"""


def sm_cv_sql() -> str:
    # 盘点：没有未审核盘点单的仓库（U8 一个仓库只能有一张未审核的）和其中有现存量的简单存货。仓库|存货。
    first = _stock_inv("w.cWhCode", "", "c.cInvCode")
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(w.cWhCode)) + N'|' + a.inv AS v
FROM dbo.Warehouse AS w
CROSS APPLY ({first}) AS a
WHERE {_plain_wh('w')}
  AND NOT EXISTS (
    SELECT 1 FROM dbo.CheckVouch AS k
    WHERE k.cWhCode = w.cWhCode AND LTRIM(RTRIM(ISNULL(k.cAccounter, N''))) = N''
  )
ORDER BY w.cWhCode;
"""


# 挑单登记（同 returns_misc_sql.CUSTOM_PACKS 的形状）：前导键、按 | 拆开的键、是否必须是正整数 id、SQL。
SM_PACKS = (
    ("sm_av", ("sm_av", "sm_av_from", "sm_av_to", "sm_av_dep"), (False, False, False, False), sm_av_sql),
    ("sm_tr", ("sm_tr", "sm_tr_in", "sm_tr_inv"), (False, False, False), sm_tr_sql),
    ("sm_cv", ("sm_cv", "sm_cv_inv"), (False, False), sm_cv_sql),
)


# ---- 回读 ----


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _count(table: str, col: str, doc: str) -> str:
    return _one("CONVERT(varchar(20), COUNT(*))", f"FROM dbo.{table} AS t WHERE t.{col} = {safe_id(doc)}")


def _av_rows(doc: str, _bag: dict[str, str]) -> str:
    return _count("AssemVouchs", "ID", doc)


def _av_line(doc: str, _bag: dict[str, str]) -> str:
    # 转换后那一行（修改只改它的备注，不动数量，免得前后数量不等被 U8 拒绝）。
    frm = f"FROM dbo.AssemVouchs AS t WHERE t.ID = {safe_id(doc)} AND t.bAVType = N'转换后'"
    return _one("CONVERT(varchar(20), MIN(t.autoID))", frm)


def _av_memo(doc: str, _bag: dict[str, str]) -> str:
    frm = (
        "FROM dbo.AssemVouch AS h JOIN dbo.AssemVouchs AS t ON t.ID = h.ID "
        f"WHERE h.ID = {safe_id(doc)} AND h.cAVMemo = N'CO桥回归改' AND t.cbMemo = N'CO桥回归改'"
    )
    return _one("CONVERT(varchar(20), COUNT(*))", frm)


def _av_kids(doc: str, _bag: dict[str, str]) -> str:
    # 审核生成的其他出入库单：表头 cBusCode = 形态转换单号、cSource = 形态转换。
    code = f"(SELECT h.cAVCode FROM dbo.AssemVouch AS h WHERE h.ID = {safe_id(doc)} AND h.cVouchType = N'15')"
    part = "(SELECT COUNT(*) FROM dbo.{t} AS r WHERE r.cSource = N'形态转换' AND r.cBusCode = " + code + ")"
    expr = "CONVERT(varchar(20), " + part.format(t="RdRecord08") + " + " + part.format(t="RdRecord09") + ")"
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v;\n"


def _av_gone(doc: str, _bag: dict[str, str]) -> str:
    return _count("AssemVouch", "ID", doc)


def _tr_rows(doc: str, _bag: dict[str, str]) -> str:
    return _count("ST_AppTransVouchs", "ID", doc)


def _tr_line(doc: str, _bag: dict[str, str]) -> str:
    return _one("CONVERT(varchar(20), MIN(t.autoID))", f"FROM dbo.ST_AppTransVouchs AS t WHERE t.ID = {safe_id(doc)}")


def _tr_qty(doc: str, _bag: dict[str, str]) -> str:
    expr = "CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL(SUM(t.iTVQuantity), 0)))"
    return _one(expr, f"FROM dbo.ST_AppTransVouchs AS t WHERE t.ID = {safe_id(doc)}")


def _tr_gone(doc: str, _bag: dict[str, str]) -> str:
    return _count("ST_AppTransVouch", "ID", doc)


def _cv_book(doc: str, _bag: dict[str, str]) -> str:
    # 账面数量按现存量填：每行 iCVQuantity 与该仓库该存货（同批号、同自由项）的现存量合计相等的行数。
    # 与桥一样按批号和自由项 1–10 逐项相等（空按空串）。
    frees = "".join(f" AND ISNULL(c.cFree{i}, N'') = ISNULL(t.cFree{i}, N'')" for i in range(1, 11))
    stock = (
        "(SELECT ISNULL(SUM(c.iQuantity), 0) FROM dbo.CurrentStock AS c "
        "WHERE c.cWhCode = h.cWhCode AND c.cInvCode = t.cInvCode AND ISNULL(c.cBatch, N'') = ISNULL(t.cCVBatch, N'')"
        + frees + ")"
    )
    frm = (
        "FROM dbo.CheckVouch AS h JOIN dbo.CheckVouchs AS t ON t.ID = h.ID "
        f"WHERE h.ID = {safe_id(doc)} AND ABS(ISNULL(t.iCVQuantity, 0) - {stock}) < 0.0001"
    )
    return _one("CONVERT(varchar(20), COUNT(*))", frm)


def _cv_gone(doc: str, _bag: dict[str, str]) -> str:
    return _count("CheckVouch", "ID", doc)


_READBACK = {
    "sm_av_rows": _av_rows,
    "sm_av_line": _av_line,
    "sm_av_memo": _av_memo,
    "sm_av_kids": _av_kids,
    "sm_av_gone": _av_gone,
    "sm_tr_rows": _tr_rows,
    "sm_tr_line": _tr_line,
    "sm_tr_qty": _tr_qty,
    "sm_tr_gone": _tr_gone,
    "sm_cv_book": _cv_book,
    "sm_cv_gone": _cv_gone,
}


def has_sm(name: str) -> bool:
    return name in _READBACK


def sm_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def sm_samples() -> list[tuple[str, str]]:
    rows = [("sdoc-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in SM_PACKS]
    rows.extend(("sdoc-" + name.replace("_", "-"), fn("100", {})) for name, fn in _READBACK.items())
    return rows
