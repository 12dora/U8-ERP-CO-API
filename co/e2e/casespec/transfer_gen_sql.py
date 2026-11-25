"""调拨单参照调拨申请单（transfer_gen）的挑单与回读 SQL。

TG_PACKS 进挑单，has_tg / tg_sql 进 stash / pred 的回读，tg_samples() 进样例 SQL（接线在 wiring 汇总模块）。
tg_kids_sql 给补偿用：列出指向该申请单各行（iTRIds）的调拨单主键。标识符都是常量，id 只接受正整数再拼进 SQL。
"""

from __future__ import annotations

from co.e2e.casespec.mo_close_sql import safe_id
from co.e2e.casespec.stock_other_docs_sql import sm_tr_sql


def tg_tr_sql() -> str:
    # 同 transfer-request-cycle 的挑单（两个不管货位的仓库、调出仓有现存量的简单存货），另要求账套没开
    # 「允许超调拨申请调拨」（ST.bOverTransRequestTransfer），用例里的超量生单才会 409。调出|调入|存货。
    base = sm_tr_sql()
    guard = (
        "WHERE NOT EXISTS (SELECT 1 FROM dbo.AccInformation AS o WHERE o.cSysID = N'ST'"
        " AND o.cName = N'bOverTransRequestTransfer' AND o.cValue = N'True')\n  AND "
    )
    if base.count("\nWHERE ") != 1:
        raise ValueError("挑单 SQL 形状变了：sm_tr_sql")
    return base.replace("\nWHERE ", "\n" + guard, 1)


# 挑单登记（同 returns_misc_sql.CUSTOM_PACKS 的形状）：前导键、按 | 拆开的键、是否必须是正整数 id、SQL。
TG_PACKS = (("tg_tr", ("tg_tr", "tg_tr_in", "tg_tr_inv"), (False, False, False), tg_tr_sql),)


# ---- 回读 ----


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({expr}, 0)))"


def _req_line(doc: str, _bag: dict[str, str]) -> str:
    return _one("CONVERT(varchar(20), MIN(b.autoID))", f"FROM dbo.ST_AppTransVouchs AS b WHERE b.ID = {safe_id(doc)}")


def _chk(doc: str, _bag: dict[str, str]) -> str:
    return _one(_qty("SUM(b.iTvChkQuantity)"), f"FROM dbo.ST_AppTransVouchs AS b WHERE b.ID = {safe_id(doc)}")


def _sum(doc: str, _bag: dict[str, str]) -> str:
    return _one(_qty("SUM(ISNULL(b.iTvSumQuantity, 0))"), f"FROM dbo.ST_AppTransVouchs AS b WHERE b.ID = {safe_id(doc)}")


def _link(doc: str, _bag: dict[str, str]) -> str:
    frm = f"FROM dbo.TransVouchs AS t WHERE t.ID = {safe_id(doc)} AND ISNULL(t.iTRIds, 0) <> 0"
    return _one("CONVERT(varchar(20), COUNT(*))", frm)


def tg_kids_sql(req: str) -> str:
    # 指向申请单各行的调拨单主键，逗号分隔；没有时为空串。用 FOR XML PATH 拼接（不依赖 SQL Server 2017 的 STRING_AGG）。
    inner = (
        "SELECT N',' + CONVERT(varchar(20), k.ID) FROM (SELECT DISTINCT t.ID FROM dbo.TransVouchs AS t "
        "JOIN dbo.ST_AppTransVouchs AS b ON b.autoID = t.iTRIds "
        f"WHERE b.ID = {safe_id(req)}) AS k ORDER BY k.ID FOR XML PATH('')"
    )
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + ISNULL(STUFF((" + inner + "), 1, 1, N''), N'') AS v;\n"


_READBACK = {
    "tg_reql": _req_line,
    "tg_chk": _chk,
    "tg_sum": _sum,
    "tg_link": _link,
}


def has_tg(name: str) -> bool:
    return name in _READBACK


def tg_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def tg_samples() -> list[tuple[str, str]]:
    rows = [("trgen-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in TG_PACKS]
    rows.extend(("trgen-" + name.replace("_", "-"), fn("100", {})) for name, fn in _READBACK.items())
    rows.append(("trgen-tg-kids", tg_kids_sql("100")))
    return rows
