"""销售订单锁定用例（so_lock）的挑单与回读 SQL（采购订单锁定暂不支持，不挑单）。

LOCK_PACKS 进挑单，has_lock / lock_sql 进 stash / pred 的回读，lock_samples() 进样例 SQL（接线在 wiring 汇总模块）。
标识符都是常量，id 只接受正整数再拼进 SQL。
"""

from __future__ import annotations

from co.e2e.casespec.mo_close_sql import safe_id

_BLANK = "LTRIM(RTRIM(ISNULL({col}, N''))) = N''"


def _blank(col: str) -> str:
    return _BLANK.format(col=col)


def _pick(id_col: str, table: str, where: tuple[str, ...]) -> str:
    cond = "\n  AND ".join(where)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), m.{id_col}) AS v
FROM dbo.{table} AS m
WHERE {cond}
ORDER BY m.{id_col} DESC;
"""


def lock_so_sql() -> str:
    # 未审核、未锁定、未关闭（含各行未关闭）、不受审批流控制的销售订单。
    where = (
        _blank("m.cVerifier"), "m.dverifydate IS NULL", _blank("m.cLocker"), _blank("m.cCloser"),
        "ISNULL(m.iswfcontrolled, 0) = 0",
        "NOT EXISTS (SELECT 1 FROM dbo.SO_SODetails AS d WHERE d.ID = m.ID AND NOT (" + _blank("d.cSCloser") + "))",
    )
    return _pick("ID", "SO_SOMain", where)


def lock_sov_sql() -> str:
    # 已审核、未锁定的销售订单：锁定应 409（U8 回「销售订单已经审核!」，桥在调用前拒绝）。
    where = ("NOT (" + _blank("m.cVerifier") + ")", _blank("m.cLocker"))
    return _pick("ID", "SO_SOMain", where)


# 挑单登记（同 returns_misc_sql.CUSTOM_PACKS 的形状）：前导键、按 | 拆开的键、是否必须是正整数 id、SQL。
LOCK_PACKS = (
    ("lock_so", ("lock_so",), (True,), lock_so_sql),
    ("lock_sov", ("lock_sov",), (True,), lock_sov_sql),
)


# ---- 回读 ----


def _locker(table: str, id_col: str, doc: str) -> str:
    # 表头锁定人（去两端空格；未锁定为空串）。
    return (
        "SET NOCOUNT ON;\nSELECT N'ROW:' + ISNULL(MAX(LTRIM(RTRIM(h.cLocker))), N'') AS v\n"
        f"FROM dbo.{table} AS h WHERE h.{id_col} = {safe_id(doc)};\n"
    )


def _so_locker(doc: str, _bag: dict[str, str]) -> str:
    return _locker("SO_SOMain", "ID", doc)


_READBACK = {
    "lock_so_locker": _so_locker,
}


def has_lock(name: str) -> bool:
    return name in _READBACK


def lock_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def lock_samples() -> list[tuple[str, str]]:
    rows = [("lock-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in LOCK_PACKS]
    rows.extend(("lock-" + name.replace("_", "-"), fn("100", {})) for name, fn in _READBACK.items())
    return rows
