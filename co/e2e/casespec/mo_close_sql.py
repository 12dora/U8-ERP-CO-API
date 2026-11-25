"""生产订单关闭 / 打开用例的挑单与回读 SQL。

接线（picks、steps_sql 的回读、samples）由调用方统一做：MO_PACKS 进挑单，has_mo / mo_sql 进 stash / pred 的回读，
mo_samples() 进样例 SQL。标识符都是常量，id 只接受正整数再拼进 SQL。
"""

from __future__ import annotations

import re

_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_INT_MAX = 2147483647


def safe_id(raw: str) -> str:
    if _ID.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError("单据 id 不合法")
    return raw


# ---- 挑单 ----


def mo_close_src_sql() -> str:
    # 已审核未关闭（Status=3）、不受审批流控制、没有在制品（车间工序结存全为 0，同 Usp_MO_Close 的判断）、
    # 没有占用现存量的未领子件（打开时 U8 要把预留加回现存量，挑干净的行）。订单 MoId|订单行 MoDId。
    # 本月已做成本计算时关闭会 409（存储过程 Errno 101），这里查不到（只读账号没有 CaF_IsCalculate 的执行权）。
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), d.MoId) + N'|' + CONVERT(varchar(20), d.MoDId) AS v
FROM dbo.mom_orderdetail AS d
WHERE d.Status = 3
  AND ISNULL(d.IsWFControlled, 0) = 0
  AND d.CloseUser IS NULL
  AND NOT EXISTS (SELECT 1 FROM dbo.sfc_morouting AS s
                  JOIN dbo.sfc_moroutingdetail AS s1 ON s1.MoRoutingId = s.MoRoutingId
                  WHERE s.MoDId = d.MoDId
                    AND s1.BalQualifiedQty + s1.BalRefusedQty + s1.BalMachiningQty + s1.BalDeclareQty <> 0)
  AND NOT EXISTS (SELECT 1 FROM dbo.mom_moallocate AS a
                  WHERE a.MoDId = d.MoDId AND a.QmFlag = 1 AND a.Qty - a.IssQty > 0)
ORDER BY d.MoDId DESC;
"""


# 挑单登记（同 returns_misc_sql.CUSTOM_PACKS 的形状）：前导键、按 | 拆开的键、是否必须是正整数 id、SQL。
MO_PACKS = (("mo_src", ("mo_src", "mo_line"), (True, True), mo_close_src_sql),)


# ---- 回读（doc 是 MoId，行是 bag 的 mo_line）----


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _line_from(doc: str, bag: dict[str, str]) -> str:
    return f"FROM dbo.mom_orderdetail AS d WHERE d.MoId = {safe_id(doc)} AND d.MoDId = {safe_id(bag.get('mo_line', ''))}"


def _mo_closed(doc: str, bag: dict[str, str]) -> str:
    # 关闭后：Status=4，CloseUser（操作员编码）和 CloseDate 都有值。
    expr = (
        "CASE WHEN d.Status = 4 AND LTRIM(RTRIM(ISNULL(d.CloseUser, N''))) <> N'' AND d.CloseDate IS NOT NULL"
        " THEN N'1' ELSE N'0' END"
    )
    return _one(expr, _line_from(doc, bag))


def _mo_open(doc: str, bag: dict[str, str]) -> str:
    # 打开后（及关闭前）：Status=3，CloseUser、CloseDate 为空。
    expr = "CASE WHEN d.Status = 3 AND d.CloseUser IS NULL AND d.CloseDate IS NULL THEN N'1' ELSE N'0' END"
    return _one(expr, _line_from(doc, bag))


def _mo_others(doc: str, bag: dict[str, str]) -> str:
    # 同一订单其他行的状态校验和（整数，same 按数值比），前后相同说明只动了选中的行。
    frm = (
        f"FROM dbo.mom_orderdetail AS d WHERE d.MoId = {safe_id(doc)} AND d.MoDId <> {safe_id(bag.get('mo_line', ''))}"
    )
    expr = "CONVERT(varchar(20), ISNULL(CHECKSUM_AGG(CHECKSUM(d.MoDId, d.Status, d.CloseUser)), 0))"
    return _one(expr, frm)


_READBACK = {
    "mo_closed": _mo_closed,
    "mo_open": _mo_open,
    "mo_others": _mo_others,
}


def has_mo(name: str) -> bool:
    return name in _READBACK


def mo_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def mo_samples() -> list[tuple[str, str]]:
    bag = {"mo_line": "100"}
    rows = [("edit-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in MO_PACKS]
    rows.extend(("edit-" + name.replace("_", "-"), fn("100", bag)) for name, fn in _READBACK.items())
    return rows
