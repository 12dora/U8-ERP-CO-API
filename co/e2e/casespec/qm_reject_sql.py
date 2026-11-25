"""不良品处理单用例（qm_reject）的挑单与回读 SQL。只读；标识符是常量，主键只接受正整数（safe_id）再拼进 SQL。

来源检验单只挑造数步骤 qmr_*（seed/steps_docs_qm_rej）建的那两张：来料检验单按到货单备注 seed:qmr_arrival、
产品检验单按生产订单行备注 seed:qmr_mo 找回。qm_reject_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

from co.e2e.casespec.mo_close_sql import safe_id
from co.e2e.casespec.stock_misc_sql import safe_code

_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"
ARRIVAL_MARK = "seed:qmr_arrival"
MO_MARK = "seed:qmr_mo"
# 检验单类型 → 只认造数建的来源（到货单 / 生产订单行的备注）。
_SEED_LINK = {
    "QM03": (
        "EXISTS (SELECT 1 FROM dbo.QMINSPECTVOUCHERS AS b JOIN dbo.PU_ArrivalVouchs AS a ON a.Autoid = b.SOURCEAUTOID"
        " JOIN dbo.PU_ArrivalVouch AS h ON h.ID = a.ID WHERE b.ID = c.INSPECTID AND h.cMemo = N'" + ARRIVAL_MARK + "')"
    ),
    "QM04": (
        "EXISTS (SELECT 1 FROM dbo.QMINSPECTVOUCHERS AS b JOIN dbo.mom_orderdetail AS d ON d.MoId = b.IPROORDERID"
        " WHERE b.ID = c.INSPECTID AND d.Remark = N'" + MO_MARK + "')"
    ),
}
# 处理流程（QMSCRAPDISPOSE.IDISPOSEFLOW）：0 拒收、2 降级。
FLOWS = (0, 2)


def _qty(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({expr}, 0)))"


def check_pick_sql(biz: str) -> str:
    """一行：<检验单 ID>|<不良数量>|<存货编码>。已审核、BREJFLAG=0、不良数量 > 0、还没有不良品处理单；挑不到时没有行。"""
    link = _SEED_LINK[biz]
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), c.ID) + N'|' + {_qty("c.FDISQUANTITY")}
  + N'|' + LTRIM(RTRIM(c.CINVCODE)) AS v
FROM dbo.QMCHECKVOUCHER AS c
WHERE c.CVOUCHTYPE = N'{biz}'
  AND LTRIM(RTRIM(ISNULL(c.CVERIFIER, N''))) <> N''
  AND ISNULL(c.BREJFLAG, 0) = 0
  AND ISNULL(c.FDISQUANTITY, 0) > 0
  AND LTRIM(RTRIM(c.CINVCODE)) {_SAFE}
  AND NOT EXISTS (SELECT 1 FROM dbo.QMREJECTVOUCHER AS r WHERE r.CHECKID = c.ID)
  AND {link}
ORDER BY c.ID DESC;
"""


def reason_pick_sql() -> str:
    """一行：<不良原因编码>（Reason，iReasontype=1 质量管理类，01 优先）；没有时没有行。"""
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(n.cReasonCode)) AS v
FROM dbo.Reason AS n
WHERE n.iReasontype = 1 AND LTRIM(RTRIM(n.cReasonCode)) {_SAFE}
ORDER BY CASE WHEN LTRIM(RTRIM(n.cReasonCode)) = N'01' THEN 0 ELSE 1 END, n.cReasonCode;
"""


def dispose_pick_sql(flow: int) -> str:
    """一行：<处理方式编码>（QMSCRAPDISPOSE，处理流程为 flow，编码最小的；拒收是 Sys01、降级是 Sys03）。"""
    if flow not in FLOWS:
        raise ValueError("处理流程只能是 0 或 2")
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(p.CSCRAPDISCODE)) AS v
FROM dbo.QMSCRAPDISPOSE AS p
WHERE p.IDISPOSEFLOW = {flow} AND LTRIM(RTRIM(p.CSCRAPDISCODE)) {_SAFE}
ORDER BY p.CSCRAPDISCODE;
"""


def reject_state_sql(check: str) -> str:
    """一行：<处理单张数>|<检验单 BREJFLAG>|<最新处理单 ID>|<单号>|<IVTID>|<单据类型>|<审核人>|<IsWfControlled>
    |<表体行数>|<表体数量合计>。没有处理单时第 3 到 8 段为空、行数 0。"""
    doc = safe_id(check)
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.QMREJECTVOUCHER AS x WHERE x.CHECKID = c.ID))
  + N'|' + CONVERT(varchar(10), CONVERT(int, ISNULL(c.BREJFLAG, 0)))
  + N'|' + ISNULL(CONVERT(varchar(20), r.ID), N'')
  + N'|' + ISNULL(LTRIM(RTRIM(r.CREJECTCODE)), N'')
  + N'|' + ISNULL(CONVERT(varchar(10), r.IVTID), N'')
  + N'|' + ISNULL(LTRIM(RTRIM(r.CVOUCHTYPE)), N'')
  + N'|' + ISNULL(LTRIM(RTRIM(r.CVERIFIER)), N'')
  + N'|' + ISNULL(CONVERT(varchar(10), CONVERT(int, r.IsWfControlled)), N'')
  + N'|' + CONVERT(varchar(10), ISNULL(s.n, 0))
  + N'|' + {_qty("s.q")} AS v
FROM dbo.QMCHECKVOUCHER AS c
OUTER APPLY (
  SELECT TOP (1) y.ID, y.CREJECTCODE, y.IVTID, y.CVOUCHTYPE, y.CVERIFIER, y.IsWfControlled
  FROM dbo.QMREJECTVOUCHER AS y WHERE y.CHECKID = c.ID ORDER BY y.ID DESC
) AS r
OUTER APPLY (
  SELECT COUNT(*) AS n, SUM(z.FQUANTITY) AS q FROM dbo.QMREJECTVOUCHERS AS z WHERE z.ID = r.ID
) AS s
WHERE c.ID = {doc};
"""


def reject_line_sql(reject: str) -> str:
    """一行：<表体 AUTOID>|<处理后数量>|<BFLAG>。处理单的第一行表体（生单只有 1 行）；没有时没有行。"""
    doc = safe_id(reject)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), s.AUTOID)
  + N'|' + {_qty("ISNULL(s.FDIMQUANTITY, s.FQUANTITY)")}
  + N'|' + CONVERT(varchar(10), CONVERT(int, ISNULL(s.BFLAG, 0))) AS v
FROM dbo.QMREJECTVOUCHERS AS s
WHERE s.ID = {doc}
ORDER BY s.AUTOID;
"""


def reject_wh_sql(inv: str) -> str:
    """一行：<仓库编码>。该存货最常用的采购入库仓库（未冻结、不管货位）；没有入库记录时取编码最小的未冻结、不管货位的仓库。"""
    code = safe_code(inv)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(w.cWhCode)) AS v
FROM dbo.Warehouse AS w
WHERE ISNULL(w.bFreeze, 0) = 0 AND ISNULL(w.bWhPos, 0) = 0 AND LTRIM(RTRIM(w.cWhCode)) {_SAFE}
ORDER BY CASE WHEN EXISTS (
    SELECT 1 FROM dbo.RdRecord01 AS r JOIN dbo.rdrecords01 AS b ON b.ID = r.ID
    WHERE r.cWhCode = w.cWhCode AND b.cInvCode = N'{code}') THEN 0 ELSE 1 END, w.cWhCode;
"""


def reject_in_state_sql(line: str) -> str:
    """一行：<入库行数>|<入库数量合计>|<入库单 ID>|<入库单 cSource>|<处理单行 BFLAG>。按处理单表体 AUTOID（iRejectIds）汇总。"""
    auto = safe_id(line)
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(10), COUNT(b.AutoID))
  + N'|' + {_qty("SUM(b.iQuantity)")}
  + N'|' + ISNULL(CONVERT(varchar(20), MAX(b.ID)), N'')
  + N'|' + ISNULL(LTRIM(RTRIM(MAX(h.cSource))), N'')
  + N'|' + CONVERT(varchar(10), (SELECT CONVERT(int, ISNULL(MAX(CONVERT(int, z.BFLAG)), 0))
      FROM dbo.QMREJECTVOUCHERS AS z WHERE z.AUTOID = {auto})) AS v
FROM dbo.rdrecords01 AS b
LEFT JOIN dbo.RdRecord01 AS h ON h.ID = b.ID
WHERE b.iRejectIds = {auto};
"""


def qm_reject_samples() -> list[tuple[str, str]]:
    return [
        ("qm-reject-check-qm03", check_pick_sql("QM03")),
        ("qm-reject-check-qm04", check_pick_sql("QM04")),
        ("qm-reject-reason", reason_pick_sql()),
        ("qm-reject-dispose", dispose_pick_sql(2)),
        ("qm-reject-state", reject_state_sql("100")),
        ("qm-reject-line", reject_line_sql("100")),
        ("qm-reject-wh", reject_wh_sql("INV-1")),
        ("qm-reject-in-state", reject_in_state_sql("100")),
    ]
