"""应收 / 应付核销用例（writeoff）的挑单与回读 SQL。

HX_PACKS 进挑单，has_hx / hx_sql 进 stash / pred 的回读（核销号取自响应，见 writeoff_run），hx_samples() 进样例 SQL
（接线在 wiring 汇总模块）。
标识符都是常量，id 只接受正整数再拼进 SQL。
"""

from __future__ import annotations

from co.e2e.casespec.mo_close_sql import safe_id

# 每一侧：明细表、收付款单类型、发票类型、余额表达式、发票表头、发票表体、表头主键、表体主键、单号列、类型列、
# 审核人列、汇率列、日期列、结账列。
_AR = {
    "detail": "Ar_Detail",
    "flag": "AR",
    "rtype": "48",
    "types": "N'26', N'27'",
    "open": "ISNULL(d.iDAmount_f, 0) - ISNULL(d.iCAmount_f, 0)",
    "head": "SaleBillVouch",
    "body": "SaleBillVouchs",
    "hid": "SBVID",
    "bid": "AutoID",
    "code": "cSBVCode",
    "vtype": "cVouchType",
    "auditor": "cVerifier",
    "rate": "iExchRate",
    "date": "dDate",
    "mend": "bflag_AR",
}
_AP = {
    "detail": "Ap_Detail",
    "flag": "AP",
    "rtype": "49",
    "types": "N'01', N'02'",
    "open": "ISNULL(d.iCAmount_f, 0) - ISNULL(d.iDAmount_f, 0)",
    "head": "PurBillVouch",
    "body": "PurBillVouchs",
    "hid": "PBVID",
    "bid": "ID",
    "code": "cPBVCode",
    "vtype": "cPBVBillType",
    "auditor": "cPBVVerifier",
    "rate": "cExchRate",
    "date": "dPBVDate",
    "mend": "bflag_AP",
}

_PICK = """SET NOCOUNT ON;
WITH o AS (
  SELECT d.cCoVouchType AS t, d.cCoVouchID AS c, d.cDwCode AS dw, d.iBVid AS line
  FROM dbo.{detail} AS d
  WHERE d.cFlag = N'{flag}' AND d.cCoVouchType IN ({types}) AND d.iFlag < 3
  GROUP BY d.cCoVouchType, d.cCoVouchID, d.cDwCode, d.iBVid
  HAVING SUM({open}) >= 2
)
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), h.iID) + N'|' + CONVERT(varchar(20), b.ID) + N'|'
  + CONVERT(varchar(20), s.{hid}) + N'|' + CONVERT(varchar(20), o.line) AS v
FROM o
INNER JOIN dbo.{head} AS s ON s.{code} = o.c AND s.{vtype} = o.t
INNER JOIN dbo.{body} AS sb ON sb.{bid} = o.line AND sb.{hid} = s.{hid}
INNER JOIN dbo.Ap_CloseBill AS h ON h.cDwCode = o.dw AND h.cFlag = N'{flag}' AND h.cVouchType = N'{rtype}'
INNER JOIN dbo.Ap_CloseBills AS b ON b.iID = h.iID
WHERE b.iRAmt_f >= 2 AND ISNULL(b.bPrePay, 0) = 0
  AND LTRIM(RTRIM(ISNULL(h.cCheckMan, N''))) <> N'' AND LTRIM(RTRIM(ISNULL(s.{auditor}, N''))) <> N''
  AND h.cexch_name = (SELECT TOP (1) f.cexch_name FROM dbo.foreigncurrency AS f WHERE f.iotherused = -1)
  AND s.cexch_name = h.cexch_name AND ISNULL(h.iExchRate, 0) = 1 AND ISNULL(s.{rate}, 0) = 1
  AND h.dVouchDate <= CONVERT(date, GETDATE()) AND s.{date} <= CONVERT(date, GETDATE())
  AND NOT EXISTS (SELECT 1 FROM dbo.GL_mend AS m WHERE m.iyear = YEAR(GETDATE()) AND m.iperiod = MONTH(GETDATE())
    AND ISNULL(m.{mend}, 0) <> 0)
ORDER BY h.iID DESC, b.ID;
"""


def hx_ar_sql() -> str:
    # 已审核、本币、有未核销余额（≥ 2）的收款单行，和同一客户下已应收审核、本币、该行余额 ≥ 2 的销售发票行；
    # 两张单据的日期不晚于今天，本月应收未结账。都 ≥ 2：核销 1 之后两边都还有余额，超额那一步只会因发票余额不足被拒。
    return _PICK.format(**_AR)


def hx_ap_sql() -> str:
    # 同上，付款单行对采购发票行（应付）。
    return _PICK.format(**_AP)


# 挑单登记（同 returns_misc_sql.CUSTOM_PACKS 的形状）：前导键、按 | 拆开的键、是否必须是正整数 id、SQL。
AR_KEYS = ("hx_rc", "hx_rcl", "hx_inv", "hx_invl")
AP_KEYS = ("hx_pc", "hx_pcl", "hx_pinv", "hx_pinvl")
HX_PACKS = (
    ("hx_rc", AR_KEYS, (True, True, True, True), hx_ar_sql),
    ("hx_pc", AP_KEYS, (True, True, True, True), hx_ap_sql),
)


# ---- 回读 ----


def _remain(doc: str, _bag: dict[str, str]) -> str:
    # 收付款单该行的未核销余额（原币，两位小数）。
    return (
        "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(40), CONVERT(decimal(28, 2), ISNULL(MAX(b.iRAmt_f), 0))) AS v\n"
        f"FROM dbo.Ap_CloseBills AS b WHERE b.ID = {safe_id(doc)};\n"
    )


def _balance(side: dict[str, str], doc: str) -> str:
    # 发票该行（iBVid）的未核销余额，与桥的口径相同。
    return (
        f"SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(40), CONVERT(decimal(28, 2), ISNULL(SUM({side['open']}), 0)))"
        f" AS v\nFROM dbo.{side['detail']} AS d WHERE d.cFlag = N'{side['flag']}' AND d.cCoVouchType IN ({side['types']})"
        f" AND d.iFlag < 3 AND d.iBVid = {safe_id(doc)};\n"
    )


def _rows(side: dict[str, str], doc: str) -> str:
    # 发票该行上的核销行（cProcStyle = 9P）条数。
    return (
        "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(12), COUNT(d.Auto_ID)) AS v\n"
        f"FROM dbo.{side['detail']} AS d WHERE d.cProcStyle = N'9P' AND d.cCoVouchType IN ({side['types']})"
        f" AND d.iBVid = {safe_id(doc)};\n"
    )


def _ar_balance(doc: str, _bag: dict[str, str]) -> str:
    return _balance(_AR, doc)


def _ap_balance(doc: str, _bag: dict[str, str]) -> str:
    return _balance(_AP, doc)


def _ar_rows(doc: str, _bag: dict[str, str]) -> str:
    return _rows(_AR, doc)


def _ap_rows(doc: str, _bag: dict[str, str]) -> str:
    return _rows(_AP, doc)


_READBACK = {
    "hx_remain": _remain,
    "hx_arbal": _ar_balance,
    "hx_aprbal": _ap_balance,
    "hx_arrows": _ar_rows,
    "hx_aprows": _ap_rows,
}


def has_hx(name: str) -> bool:
    return name in _READBACK


def hx_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def hx_samples() -> list[tuple[str, str]]:
    rows = [("wo-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in HX_PACKS]
    rows.extend(("wo-" + name.replace("_", "-"), fn("100", {})) for name, fn in _READBACK.items())
    return rows
