"""挑单：应收应付主数据、已制凭证的收款单、生产订单和产品检验单。标识符都是常量，返回值带 ROW: 前缀。"""

from __future__ import annotations

from co.e2e.casespec.pu_gen_sql import arr_src_sql, pinv_src_sql, qmin_src_sql
from co.e2e.casespec.doc_list_sql import ar_list_sql, list_samples, so_list_sql, stock_pair_sql
from co.e2e.casespec.arap_gen_readback_sql import core_read_samples

_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"
_YEAR = "k.iyear = (SELECT MAX(x.iyear) FROM dbo.code AS x)"
_PLAIN = "k.bdept = 0 AND k.bperson = 0 AND k.bcus = 0 AND k.bsup = 0 AND k.bitem = 0"
# 没有历史手工单据时（新建的空账套）退回科目表：受控科目前缀，应收/应付单表体科目按前缀依次找不带辅助核算的末级科目。
# 与造数 seed/steps_docs_arap 的取法一致。
_CTL_PREFIX = {"AR": "1122", "AP": "2202"}
_BILL_PREFIXES = {"AR": ("6001", "6051", "6301", "2241"), "AP": ("6401", "1403", "6602", "2241")}


def _top_km(col: str, frm: str, where: str) -> str:
    # 历史上最常用的末级科目，作为新单据的科目。
    return (
        f"  SELECT TOP (1) LTRIM(RTRIM({col})) AS km\n{frm}"
        f"  JOIN dbo.code AS k ON k.ccode = {col} AND {_YEAR}\n"
        f"  WHERE {where} AND k.bend = 1\n"
        f"  GROUP BY {col} ORDER BY COUNT(*) DESC, {col}\n"
    )


def _code_km(where: str, order: str = "k.ccode") -> str:
    # 科目表里的末级科目（历史单据取不到时的退路）。
    return (
        "  SELECT TOP (1) LTRIM(RTRIM(k.ccode)) AS km\n  FROM dbo.code AS k\n"
        f"  WHERE {_YEAR} AND k.bend = 1 AND {where}\n  ORDER BY {order}\n"
    )


def _first(primary: str, fallback: str, alias: str) -> str:
    # 先取历史最常用的；没有时取退路。账套有历史时结果与旧版一致。
    return (
        "CROSS JOIN (\n  SELECT TOP (1) u.km FROM (\n"
        "    SELECT 0 AS pri, hi.km FROM (\n" + primary + "    ) AS hi\n"
        "    UNION ALL\n"
        "    SELECT 1 AS pri, fb.km FROM (\n" + fallback + "    ) AS fb\n"
        f"  ) AS u ORDER BY u.pri\n) AS {alias}\n"
    )


def _bill_fallback(flag: str) -> str:
    prefixes = _BILL_PREFIXES[flag]
    likes = " OR ".join(f"k.ccode LIKE N'{p}%'" for p in prefixes)
    rank = " ".join(f"WHEN k.ccode LIKE N'{p}%' THEN {n}" for n, p in enumerate(prefixes))
    return _code_km(f"{_PLAIN} AND k.bbank = 0 AND ({likes})", f"CASE {rank} ELSE {len(prefixes)} END, k.ccode")


def _cb_where(flag: str, vouch: str) -> str:
    return f"h.cFlag = N'{flag}' AND h.cVouchType = N'{vouch}' AND ISNULL(h.cCoVouchType, N'') = N''"


def _settle(cb: str) -> str:
    # 结算方式：历史收付款单最常用的、不带票据号的末级结算方式；没有时取编码最小的末级结算方式。
    primary = (
        "  SELECT TOP (1) LTRIM(RTRIM(h.cSSCode)) AS km\n"
        "  FROM dbo.Ap_CloseBill AS h\n"
        "  JOIN dbo.SettleStyle AS s ON s.cSSCode = h.cSSCode AND s.bSSEnd = 1\n"
        f"  WHERE {cb} AND ISNULL(h.cNoteNo, N'') = N''\n"
        "  GROUP BY h.cSSCode ORDER BY COUNT(*) DESC, h.cSSCode\n"
    )
    fallback = (
        "  SELECT TOP (1) LTRIM(RTRIM(s.cSSCode)) AS km\n  FROM dbo.SettleStyle AS s\n"
        "  WHERE s.bSSEnd = 1\n  ORDER BY s.cSSCode\n"
    )
    return _first(primary, fallback, "ss")


def _refs(flag: str, vouch: str, aux: str, bill: str) -> str:
    # 受控科目、银行科目、结算方式、应收/应付单表体科目，各取测试账套手工单据里最常用的一个；
    # 账套里还没有这类单据时按科目表、结算方式档案取（新建的空账套）。
    cb = _cb_where(flag, vouch)
    rmb = " AND h.cexch_name = N'人民币'"
    body = "  FROM dbo.Ap_CloseBills AS b\n  JOIN dbo.Ap_CloseBill AS h ON h.iID = b.iID\n"
    head = "  FROM dbo.Ap_CloseBill AS h\n"
    vouchs = "  FROM dbo.Ap_Vouchs AS b\n  JOIN dbo.Ap_Vouch AS h ON h.cLink = b.cLink\n"
    bill_where = f"h.cFlag = N'{flag}' AND h.cVouchType = N'{bill}' AND {_PLAIN}"
    # 退路先取本系统受控（cother = AR / AP，同桥 ArapCoRefs）的科目；都不受控时照旧取前缀下第一个，
    # 由用例第一步（arap_ctl）记 N/A。
    ctl_fb = _code_km(
        f"k.{aux} = 1 AND k.ccode LIKE N'{_CTL_PREFIX[flag]}%'",
        f"CASE WHEN UPPER(LTRIM(RTRIM(ISNULL(k.cother, N'')))) = N'{flag}' THEN 0 ELSE 1 END, k.ccode",
    )
    bank_fb = _code_km(f"k.bbank = 1 AND {_PLAIN}", "CASE WHEN k.ccode LIKE N'1002%' THEN 0 ELSE 1 END, k.ccode")
    return (
        _first(_top_km("b.cKm", body, f"{cb}{rmb} AND b.iType = 0 AND k.{aux} = 1"), ctl_fb, "ctl")
        + _first(_top_km("h.cCode", head, f"{cb}{rmb} AND k.bbank = 1"), bank_fb, "bank")
        + _settle(cb)
        + _first(_top_km("b.cCode", vouchs, bill_where), _bill_fallback(flag), "bill")
    )


def _unused(col: str, detail: str, flag: str) -> str:
    # 往来单位从未出现在应收/应付明细和单据里，审核时不会自动核销到别的单据。
    return (
        f"  AND NOT EXISTS (SELECT 1 FROM {detail} AS a WHERE a.cDwCode = {col})\n"
        f"  AND NOT EXISTS (SELECT 1 FROM dbo.Ap_CloseBill AS a WHERE a.cDwCode = {col} AND a.cFlag = N'{flag}')\n"
        f"  AND NOT EXISTS (SELECT 1 FROM dbo.Ap_Vouch AS a WHERE a.cDwCode = {col} AND a.cFlag = N'{flag}')\n"
    )


def ar_master_sql() -> str:
    # 客户|部门|业务员|应收科目|银行科目|结算方式|应收单表体科目
    return (
        "SET NOCOUNT ON;\n"
        "SELECT TOP (1)\n"
        "  N'ROW:' + LTRIM(RTRIM(c.cCusCode)) + N'|' + LTRIM(RTRIM(c.cCusDepart)) + N'|'\n"
        "  + LTRIM(RTRIM(c.cCusPPerson)) + N'|' + ctl.km + N'|' + bank.km + N'|' + ss.km + N'|' + bill.km AS v\n"
        "FROM dbo.Customer AS c\n"
        "JOIN dbo.Department AS dep ON dep.cDepCode = c.cCusDepart AND dep.bDepEnd = 1\n"
        "JOIN dbo.Person AS p ON p.cPersonCode = c.cCusPPerson\n"
        + _refs("AR", "48", "bcus", "R0")
        + "WHERE c.dEndDate IS NULL\n"
        f"  AND LTRIM(RTRIM(c.cCusCode)) {_SAFE}\n"
        f"  AND LTRIM(RTRIM(c.cCusDepart)) {_SAFE}\n"
        f"  AND LTRIM(RTRIM(c.cCusPPerson)) {_SAFE}\n"
        + _unused("c.cCusCode", "dbo.Ar_Detail", "AR")
        + "ORDER BY c.cCusCode;\n"
    )


def ap_master_sql() -> str:
    # 供应商|应付科目|银行科目|结算方式|应付单表体科目
    return (
        "SET NOCOUNT ON;\n"
        "SELECT TOP (1)\n"
        "  N'ROW:' + LTRIM(RTRIM(v.cVenCode)) + N'|' + ctl.km + N'|' + bank.km + N'|' + ss.km + N'|' + bill.km AS v\n"
        "FROM dbo.Vendor AS v\n"
        + _refs("AP", "49", "bsup", "P0")
        + "WHERE v.dEndDate IS NULL\n"
        f"  AND LTRIM(RTRIM(v.cVenCode)) {_SAFE}\n"
        + _unused("v.cVenCode", "dbo.Ap_Detail", "AP")
        + "ORDER BY v.cVenCode;\n"
    )


def arap_ctl_sql(flag: str, year: int) -> str:
    # 登录年度科目表里本系统受控（cother = AR / AP）的末级科目个数。0 表示账套没有使用应收 / 应付款管理。
    if flag not in _CTL_PREFIX:
        raise ValueError("flag 只能是 AR 或 AP")
    if type(year) is not int or not 1000 <= year <= 9999:
        raise ValueError("年度不对")
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), COUNT(*)) AS v\nFROM dbo.code AS k\n"
        f"WHERE k.iyear = {year} AND k.bend = 1 AND UPPER(LTRIM(RTRIM(ISNULL(k.cother, N'')))) = N'{flag}';\n"
    )


def ar_voucher_sql() -> str:
    # 已审核、已制凭证的手工收款单：弃审和删除都应被拒绝。
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), h.iID) AS v
FROM dbo.Ap_CloseBill AS h
WHERE h.cFlag = N'AR'
  AND h.cVouchType = N'48'
  AND LTRIM(RTRIM(ISNULL(h.cPzID, N''))) <> N''
  AND LTRIM(RTRIM(ISNULL(h.cCheckMan, N''))) <> N''
  AND ISNULL(h.cCoVouchType, N'') = N''
ORDER BY h.iID DESC;
"""


def mo_alloc_sql() -> str:
    # 已审核生产订单的领用子件：MoId|AllocateId|仓库。子件不批次、无计量单位组，仓库不管货位。
    # 子件没填仓库时取该存货有现存量的不管货位仓库（现存量多的优先）；填了仓库的子件排在前面，挑单与原来一致。
    # 优先已有领料的订单，和 mo_free_sql 选中的订单错开。
    return f"""SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + CONVERT(varchar(20), m.MoId) + N'|' + CONVERT(varchar(20), a.AllocateId) + N'|' + wc.code AS v
FROM dbo.mom_order AS m
JOIN dbo.mom_orderdetail AS d ON d.MoId = m.MoId
JOIN dbo.mom_moallocate AS a ON a.MoDId = d.MoDId
JOIN dbo.Inventory AS i ON i.cInvCode = a.InvCode
OUTER APPLY (
  SELECT TOP (1) LTRIM(RTRIM(c.cWhCode)) AS code
  FROM dbo.CurrentStock AS c
  JOIN dbo.Warehouse AS cw ON cw.cWhCode = c.cWhCode
  WHERE NULLIF(LTRIM(RTRIM(a.WhCode)), N'') IS NULL
    AND c.cInvCode = a.InvCode
    AND cw.bWhPos = 0
    AND ISNULL(cw.bFreeze, 0) = 0
    AND LTRIM(RTRIM(c.cWhCode)) {_SAFE}
  GROUP BY c.cWhCode
  HAVING SUM(ISNULL(c.iQuantity, 0)) > 0
  ORDER BY SUM(ISNULL(c.iQuantity, 0)) DESC, c.cWhCode
) AS cs
CROSS APPLY (SELECT COALESCE(NULLIF(LTRIM(RTRIM(a.WhCode)), N''), cs.code) AS code) AS wc
JOIN dbo.Warehouse AS w ON w.cWhCode = wc.code
WHERE d.Status = 3
  AND ISNULL(d.IsWFControlled, 0) = 0
  AND a.WIPType = 3
  AND a.ProductType = 1
  AND ISNULL(a.ByproductFlag, 0) = 0
  AND ISNULL(a.Qty, 0) > 0
  AND ISNULL(i.bInvBatch, 0) = 0
  AND ISNULL(i.iGroupType, 0) = 0
  AND w.bWhPos = 0
  AND ISNULL(w.bFreeze, 0) = 0
  AND wc.code {_SAFE}
ORDER BY
  CASE WHEN NULLIF(LTRIM(RTRIM(a.WhCode)), N'') IS NULL THEN 1 ELSE 0 END,
  CASE WHEN EXISTS (SELECT 1 FROM dbo.rdrecords11 AS s WHERE s.iMPoIds = a.AllocateId) THEN 0 ELSE 1 END,
  m.MoId DESC, a.AllocateId;
"""


def qm_pin_sql() -> str:
    # 已审核、来源生产订单的产品检验单，剩余合格数量够入库：检验单 ID|仓库|数量。
    # 有固定换算率的存货按一件（换算率）入库，否则入 1。仓库取该存货最常用的产成品入库仓库。
    return f"""SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + CONVERT(varchar(20), q.ID) + N'|' + wh.code + N'|'
  + CONVERT(varchar(40), CONVERT(decimal(18,4), n.qty)) AS v
FROM dbo.QMCHECKVOUCHER AS q
JOIN dbo.mom_orderdetail AS d ON d.MoDId = q.SOURCEAUTOID AND d.MoId = q.SOURCEID
JOIN dbo.Inventory AS i ON i.cInvCode = q.CINVCODE
CROSS APPLY (
  SELECT CASE WHEN ISNULL(i.iGroupType, 0) = 0 THEN CONVERT(decimal(18,4), 1)
    ELSE CONVERT(decimal(18,4), ISNULL(NULLIF(q.FCHANGRATE, 0), 1)) END AS qty
) AS n
CROSS APPLY (
  SELECT TOP (1) LTRIM(RTRIM(r.cWhCode)) AS code
  FROM dbo.rdrecord10 AS r
  JOIN dbo.rdrecords10 AS s ON s.ID = r.ID
  JOIN dbo.Warehouse AS w ON w.cWhCode = r.cWhCode
  WHERE s.cInvCode = q.CINVCODE
    AND w.bWhPos = 0
    AND ISNULL(w.bFreeze, 0) = 0
    AND LTRIM(RTRIM(r.cWhCode)) {_SAFE}
  GROUP BY r.cWhCode
  ORDER BY COUNT(*) DESC, r.cWhCode
) AS wh
WHERE q.CVOUCHTYPE = N'QM04'
  AND q.CSOURCE = N'生产订单'
  AND LTRIM(RTRIM(ISNULL(q.CVERIFIER, N''))) <> N''
  AND q.DVERIFYDATE IS NOT NULL
  AND ISNULL(q.BPROINFLAG, 0) = 0
  AND ISNULL(q.BMERGECHECKFLAG, 0) = 0
  AND LTRIM(RTRIM(ISNULL(q.CBATCH, N''))) = N''
  AND d.Status = 3
  AND ISNULL(i.bInvBatch, 0) = 0
  AND ISNULL(i.iGroupType, 0) IN (0, 1)
  AND ISNULL(q.FREGQUANTITY, 0) - ISNULL(q.FsumQuantity, 0) >= n.qty
ORDER BY q.ID DESC;
"""


def mo_free_sql() -> str:
    # 可以弃审再审核的生产订单：各行已审核，没有领料、入库、报检、申请、子订单和成本（U8 弃审的前提）。
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), m.MoId) AS v
FROM dbo.mom_order AS m
WHERE EXISTS (SELECT 1 FROM dbo.mom_orderdetail AS d WHERE d.MoId = m.MoId)
  AND NOT EXISTS (
    SELECT 1 FROM dbo.mom_orderdetail AS d
    WHERE d.MoId = m.MoId
      AND (d.Status <> 3 OR ISNULL(d.IsWFControlled, 0) <> 0 OR ISNULL(d.SfcFlag, 0) <> 0
        OR ISNULL(d.DeclaredQty, 0) <> 0 OR ISNULL(d.QualifiedInQty, 0) <> 0)
  )
  AND NOT EXISTS (
    SELECT 1 FROM dbo.mom_moallocate AS a
    JOIN dbo.mom_orderdetail AS d ON d.MoDId = a.MoDId
    WHERE d.MoId = m.MoId
      AND (ISNULL(a.IssQty, 0) <> 0 OR ISNULL(a.DeclaredQty, 0) <> 0 OR ISNULL(a.TransAppQty, 0) <> 0
        OR ISNULL(a.PickingQty, 0) <> 0 OR ISNULL(a.RequisitionQty, 0) <> 0
        OR ISNULL(a.ReplenishQty, 0) <> 0 OR ISNULL(a.ReplenishApplyQty, 0) <> 0)
  )
  AND NOT EXISTS (
    SELECT 1 FROM dbo.rdrecords11 AS s
    JOIN dbo.mom_moallocate AS a ON a.AllocateId = s.iMPoIds
    JOIN dbo.mom_orderdetail AS d ON d.MoDId = a.MoDId
    WHERE d.MoId = m.MoId
  )
  AND NOT EXISTS (
    SELECT 1 FROM dbo.rdrecords10 AS s
    JOIN dbo.mom_orderdetail AS d ON d.MoDId = s.iMPoIds
    WHERE d.MoId = m.MoId
  )
  AND NOT EXISTS (SELECT 1 FROM dbo.QMCHECKVOUCHER AS q WHERE q.SOURCEID = m.MoId AND q.CSOURCE = N'生产订单')
  AND NOT EXISTS (SELECT 1 FROM dbo.mom_orderdetail AS c WHERE c.SourceMoId = m.MoId)
  AND NOT EXISTS (
    SELECT 1 FROM dbo.ca_batchmap AS b
    JOIN dbo.ca_amoct AS a ON a.cppid = b.irealcoid AND a.cbatch = b.cbatch
    WHERE b.cMocode = m.MoCode AND ISNULL(a.iTotalAmo, 0) <> 0
  )
ORDER BY m.MoId DESC;
"""


def mo_audited_sql() -> str:
    # 各行已审核、已有领料的生产订单：再审核应被拒绝（409 state_mismatch），不改库。
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), m.MoId) AS v
FROM dbo.mom_order AS m
WHERE EXISTS (SELECT 1 FROM dbo.mom_orderdetail AS d WHERE d.MoId = m.MoId)
  AND NOT EXISTS (SELECT 1 FROM dbo.mom_orderdetail AS d WHERE d.MoId = m.MoId AND d.Status <> 3)
  AND EXISTS (
    SELECT 1 FROM dbo.rdrecords11 AS s
    JOIN dbo.mom_moallocate AS a ON a.AllocateId = s.iMPoIds
    JOIN dbo.mom_orderdetail AS d ON d.MoDId = a.MoDId
    WHERE d.MoId = m.MoId
  )
ORDER BY m.MoId DESC;
"""


# 挑单登记（picks.py 读取）。单个 id、整段文本、以及按 | 拆成多个键的组合。
CORE_IDS = (
    ("ar_voucher", ar_voucher_sql),
    ("mo_free", mo_free_sql),
    ("mo_audited", mo_audited_sql),
)
CORE_TEXTS = (
    ("ar_master", ar_master_sql),
    ("ap_master", ap_master_sql),
    ("so_list", so_list_sql),
    ("ar_list", ar_list_sql),
    ("stock_pair", stock_pair_sql),
)
CORE_PACKS = (
    ("mo_alloc", ("mo_alloc", "mo_alloc_line", "mo_alloc_wh"), (True, True, False), mo_alloc_sql),
    ("qm_pin", ("qm_pin", "qm_pin_wh", "qm_pin_qty"), (True, False, False), qm_pin_sql),
    ("pinv_src", ("pinv_src", "pinv_line"), (True, True), pinv_src_sql),
    ("qmin_src", ("qmin_src", "qmin_wh"), (True, False), qmin_src_sql),
    ("arr_src", ("arr_src", "arr_line", "arr_wh", "arr_over"), (True, True, False, False), arr_src_sql),
)


def core_keys() -> tuple[str, ...]:
    keys = [key for key, _sql in CORE_IDS + CORE_TEXTS]
    for _lead, names, _flags, _sql in CORE_PACKS:
        keys.extend(names)
    return tuple(keys)


def core_samples() -> list[tuple[str, str]]:
    rows = [("core-" + key.replace("_", "-"), sql()) for key, sql in CORE_IDS + CORE_TEXTS]
    rows.extend(("core-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in CORE_PACKS)
    rows.extend(list_samples())
    rows.extend(core_read_samples())
    rows.extend(("core-arap-ctl-" + flag.lower(), arap_ctl_sql(flag, 2026)) for flag in _CTL_PREFIX)
    return rows
