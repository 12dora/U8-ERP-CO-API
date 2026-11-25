"""自动核销用例（writeoff_auto）的挑单 SQL。回读沿用 writeoff_sql 的 hx_remain / hx_arbal / hx_arrows / hx_arcno。

HXA_PACKS 进挑单，hxa_samples() 进样例 SQL（接线在 wiring 汇总模块）。SQL 全是常量，不拼任何输入。
"""

from __future__ import annotations

# 已审核、本币、有未核销余额（≥ 2）的收款单行，和同一客户下已应收审核、本币的销售发票里行号最小的有余额行（余额 ≥ 2）：
# 自动核销限定这张收款单的这一行和这张发票时，先进先出的第一笔就落在这两行上。两边都不受审批流控制，日期不晚于今天，
# 本月应收未结账；客户编码只含字母数字和 ._-、不超过 12 位（挑单 bag 的编码字符集）。
_PICK_AR = """SET NOCOUNT ON;
WITH o AS (
  SELECT d.cCoVouchType AS t, d.cCoVouchID AS c, d.cDwCode AS dw, d.iBVid AS line,
    SUM(ISNULL(d.iDAmount_f, 0) - ISNULL(d.iCAmount_f, 0)) AS v
  FROM dbo.Ar_Detail AS d
  WHERE d.cFlag = N'AR' AND d.cCoVouchType IN (N'26', N'27') AND d.iFlag < 3 AND d.iBVid IS NOT NULL
  GROUP BY d.cCoVouchType, d.cCoVouchID, d.cDwCode, d.iBVid
  HAVING SUM(ISNULL(d.iDAmount_f, 0) - ISNULL(d.iCAmount_f, 0)) > 0
)
SELECT TOP (1) N'ROW:' + h.cDwCode + N'|' + CONVERT(varchar(20), h.iID) + N'|' + CONVERT(varchar(20), b.ID) + N'|'
  + CONVERT(varchar(20), s.SBVID) + N'|' + CONVERT(varchar(20), o.line) AS v
FROM o
INNER JOIN dbo.SaleBillVouch AS s ON s.cSBVCode = o.c AND s.cVouchType = o.t
INNER JOIN dbo.SaleBillVouchs AS sb ON sb.AutoID = o.line AND sb.SBVID = s.SBVID
INNER JOIN dbo.Ap_CloseBill AS h ON h.cDwCode = o.dw AND h.cFlag = N'AR' AND h.cVouchType = N'48'
INNER JOIN dbo.Ap_CloseBills AS b ON b.iID = h.iID
WHERE o.v >= 2 AND b.iRAmt_f >= 2 AND ISNULL(b.bPrePay, 0) = 0
  AND NOT EXISTS (SELECT 1 FROM o AS p WHERE p.t = o.t AND p.c = o.c AND p.dw = o.dw AND p.line < o.line)
  AND LEN(h.cDwCode) <= 12 AND h.cDwCode NOT LIKE N'%[^0-9A-Za-z._-]%'
  AND LTRIM(RTRIM(ISNULL(h.cCheckMan, N''))) <> N'' AND LTRIM(RTRIM(ISNULL(s.cVerifier, N''))) <> N''
  AND ISNULL(h.IsWfControlled, 0) = 0 AND ISNULL(s.iswfcontrolled, 0) = 0
  AND h.cexch_name = (SELECT TOP (1) f.cexch_name FROM dbo.foreigncurrency AS f WHERE f.iotherused = -1)
  AND s.cexch_name = h.cexch_name AND ISNULL(h.iExchRate, 0) = 1 AND ISNULL(s.iExchRate, 0) = 1
  AND h.dVouchDate <= CONVERT(date, GETDATE()) AND s.dDate <= CONVERT(date, GETDATE())
  AND NOT EXISTS (SELECT 1 FROM dbo.GL_mend AS m WHERE m.iyear = YEAR(GETDATE()) AND m.iperiod = MONTH(GETDATE())
    AND ISNULL(m.bflag_AR, 0) <> 0)
ORDER BY h.iID DESC, b.ID;
"""


def hxa_ar_sql() -> str:
    return _PICK_AR


# 挑单登记（同 returns_misc_sql.CUSTOM_PACKS 的形状）：前导键、按 | 拆开的键、是否必须是正整数 id、SQL。客户编码不是 id。
AUTO_KEYS = ("hxa_dw", "hxa_rc", "hxa_rcl", "hxa_inv", "hxa_invl")
HXA_PACKS = (("hxa_dw", AUTO_KEYS, (False, True, True, True, True), hxa_ar_sql),)


def hxa_samples() -> list[tuple[str, str]]:
    return [("verify-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in HXA_PACKS]
