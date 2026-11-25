"""应付审核 / 应收审核用例（arap_verify）的挑单与回读 SQL。

ARV_PACKS 进挑单，ARV_BUILDERS 进生单正文，has_arv / arv_sql 进 stash / pred 的回读，arv_samples() 进样例 SQL
（接线在 wiring 汇总模块）。标识符都是常量，id 只接受正整数再拼进 SQL。
"""

from __future__ import annotations

import re

from co.e2e.casespec.env import Env
from co.e2e.casespec.mo_close_sql import safe_id
from co.e2e.casespec.sale_edit_sql import inv_src_sql as sinv_mod_src_sql
from co.e2e.casespec.flow_pick_sql import invoice_src_body, invoice_src_sql

_BLANK = "LTRIM(RTRIM(ISNULL({col}, N''))) = N''"


def _blank(col: str) -> str:
    return _BLANK.format(col=col)


def _pick(where: tuple[str, ...]) -> str:
    cond = "\n  AND ".join(where)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), m.PBVID) AS v
FROM dbo.PurBillVouch AS m
WHERE {cond}
ORDER BY m.PBVID DESC;
"""


# 本单没有任何应付明细（原始行，或作为对方单据）、表体没有 cClue：应付审核前的干净状态。
_NO_DETAIL = (
    "NOT EXISTS (SELECT 1 FROM dbo.PurBillVouchs AS b WHERE b.PBVID = m.PBVID AND b.cClue IS NOT NULL)",
    "NOT EXISTS (SELECT 1 FROM dbo.Ap_Detail AS d WHERE d.cVouchType = m.cPBVBillType AND d.cVouchID = m.cPBVCode)",
    "NOT EXISTS (SELECT 1 FROM dbo.Ap_Detail AS d WHERE d.cCoVouchType = m.cPBVBillType AND d.cCoVouchID = m.cPBVCode)",
)
# 只收专用、普通发票：别的类型桥先按类型拒绝（400），到不了审核状态的检查。
_TYPE = "m.cPBVBillType IN (N'01', N'02')"
_PLAIN = (_TYPE, "ISNULL(m.IsWfControlled, 0) = 0", "ISNULL(m.iNetLock, 0) = 0")
# 不是现付（bPayment 是文本列，实测全是 NULL；现付发票应付审核会连带写付款 / 核销明细，弃审被拒），
# 没有采购结算（表头 dSDate、表体结算日期 / 记账人 / 结算单行，同桥 PuInv.SettleSql）：保证弃审能做回来。
_UNSETTLED = (
    "LTRIM(RTRIM(ISNULL(m.bPayment, N''))) IN (N'', N'0')",
    "m.dSDate IS NULL",
    "NOT EXISTS (SELECT 1 FROM dbo.PurBillVouchs AS b WHERE b.PBVID = m.PBVID AND (b.dSDate IS NOT NULL"
    " OR LTRIM(RTRIM(ISNULL(b.cBAccounter, N''))) <> N'' OR EXISTS (SELECT 1 FROM dbo.PurSettleVouchs AS s WHERE s.iBsID = b.ID)))",
)


def arv_pu_sql() -> str:
    # 已采购复核、未应付审核、没有应付明细、非现付、未结算的采购发票：审核 → 弃审往返。
    where = ("NOT (" + _blank("m.cVerifier") + ")", _blank("m.cPBVVerifier")) + _PLAIN + _UNSETTLED + _NO_DETAIL
    return _pick(where)


def arv_pudone_sql() -> str:
    # 已应付审核的采购发票：再审核 409（桥在调用 U8 之前拒绝，不动单据）。
    where = ("NOT (" + _blank("m.cPBVVerifier") + ")", _TYPE, "ISNULL(m.IsWfControlled, 0) = 0")
    return _pick(where)


def arv_puraw_sql() -> str:
    # 未采购复核的采购发票：应付审核 409（同上，不动单据）。
    where = (_blank("m.cVerifier"), _blank("m.cPBVVerifier"), _TYPE, "ISNULL(m.IsWfControlled, 0) = 0")
    return _pick(where)


# 挑单语句里的 ROW 列换成发货单 DLID，当子查询用（只认本仓库挑单 SQL 的写法，对不上就在导入时报错）。
_ROW_COL = re.compile(r"N'ROW:' \+ CONVERT\(varchar\(20\), (\w+)\.DLID\) \+ N'\|' \+ CONVERT\(varchar\(20\), \w+\.iDLsID\) AS v")


def _dlid_of(sql: str) -> str:
    body, hits = _ROW_COL.subn(r"\1.DLID", sql.replace("SET NOCOUNT ON;\n", "", 1).rstrip().removesuffix(";"))
    if hits != 1:
        raise RuntimeError("发货单挑单 SQL 的写法变了")
    return "ISNULL((" + body + "), 0)"


def arv_inv_sql() -> str:
    # 应收审核自己挑发货行、只开 1 个：条件同发票生单挑单（invoice_src_body），剩余可开票（减未开票退货、按 4 位截断）
    # >= 3（退货生单可能先退掉 2 个），避开发票生单（inv_src）和发票改单（sinv_mod_src）挑中的发货单。
    # 单跑、并行都不依赖别的用例留下的数量。发货单 DLID|发货行 iDLsID。
    others = _dlid_of(invoice_src_sql()) + ",\n    " + _dlid_of(sinv_mod_src_sql())
    return (
        "SET NOCOUNT ON;\n"
        "SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), d.DLID) + N'|' + CONVERT(varchar(20), l.iDLsID) AS v\n"
        + invoice_src_body()
        + "  AND ROUND(ISNULL(l.iQuantity, 0) - ISNULL(l.iSettleQuantity, 0) - ISNULL(l.fretqtywkp, 0), 4, 1) >= 3\n"
        + "  AND d.DLID NOT IN (\n    " + others + "\n  )\n"
        + "ORDER BY d.DLID DESC, l.iDLsID;\n"
    )


def arv_inv_gen(env: Env) -> dict:
    # 发货单（挑单的 arv_inv）参照生成销售发票，开 1 个。
    line = {"source_line_id": int(safe_id(env.bag.get("arv_inv_line", ""))), "quantity": 1}
    return {"type": "sale_invoice", "id": int(safe_id(env.focus)), "lines": [line]}


ARV_BUILDERS = {"arv_inv_gen": arv_inv_gen}


# 挑单登记（同 returns_misc_sql.CUSTOM_PACKS 的形状）：前导键、按 | 拆开的键、是否必须是正整数 id、SQL。
ARV_PACKS = (
    ("arv_pu", ("arv_pu",), (True,), arv_pu_sql),
    ("arv_pudone", ("arv_pudone",), (True,), arv_pudone_sql),
    ("arv_puraw", ("arv_puraw",), (True,), arv_puraw_sql),
    ("arv_inv", ("arv_inv", "arv_inv_line"), (True, True), arv_inv_sql),
)


# ---- 回读 ----


def _auditor(table: str, col: str, id_col: str, doc: str) -> str:
    # 应付 / 应收审核人（去两端空格；未审核为空串）。
    return (
        f"SET NOCOUNT ON;\nSELECT N'ROW:' + ISNULL(MAX(LTRIM(RTRIM(h.{col}))), N'') AS v\n"
        f"FROM dbo.{table} AS h WHERE h.{id_col} = {safe_id(doc)};\n"
    )


# 明细表、表头表、表头的类型列、单号列、主键列。
_PU_ROWS = ("Ap_Detail", "PurBillVouch", "cPBVBillType", "cPBVCode", "PBVID")
_SA_ROWS = ("Ar_Detail", "SaleBillVouch", "cVouchType", "cSBVCode", "SBVID")


def _rows(names: tuple[str, str, str, str, str], doc: str) -> str:
    # 本单的审核登记行（cProcStyle = cVouchType）条数。
    detail, table, vtype, code, id_col = names
    return (
        "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(12), COUNT(d.Auto_ID)) AS v\n"
        f"FROM dbo.{detail} AS d INNER JOIN dbo.{table} AS h ON d.cVouchType = h.{vtype} AND d.cVouchID = h.{code}\n"
        f"WHERE h.{id_col} = {safe_id(doc)} AND d.cProcStyle = d.cVouchType;\n"
    )


def _pu_auditor(doc: str, _bag: dict[str, str]) -> str:
    return _auditor("PurBillVouch", "cPBVVerifier", "PBVID", doc)


def _sa_auditor(doc: str, _bag: dict[str, str]) -> str:
    return _auditor("SaleBillVouch", "cVerifier", "SBVID", doc)


def _pu_rows(doc: str, _bag: dict[str, str]) -> str:
    return _rows(_PU_ROWS, doc)


def _sa_rows(doc: str, _bag: dict[str, str]) -> str:
    return _rows(_SA_ROWS, doc)


def _pu_lines(doc: str, _bag: dict[str, str]) -> str:
    # 采购发票表体行数：应付审核按发票行各登记一条 Ap_Detail，登记行数应等于它。
    return (
        "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(12), COUNT(b.ID)) AS v\n"
        f"FROM dbo.PurBillVouchs AS b WHERE b.PBVID = {safe_id(doc)};\n"
    )


_READBACK = {
    "aud_pu_auditor": _pu_auditor,
    "aud_pu_rows": _pu_rows,
    "aud_pu_lines": _pu_lines,
    "aud_sa_auditor": _sa_auditor,
    "aud_sa_rows": _sa_rows,
}


def has_arv(name: str) -> bool:
    return name in _READBACK


def arv_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def arv_samples() -> list[tuple[str, str]]:
    rows = [("verify-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in ARV_PACKS]
    rows.extend(("verify-" + name.replace("_", "-"), fn("100", {})) for name, fn in _READBACK.items())
    return rows
