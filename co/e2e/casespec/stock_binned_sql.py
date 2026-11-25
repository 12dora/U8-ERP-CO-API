"""库存待实测用例（stock_binned）的挑单与回读 SQL（含材料出库、产成品入库带货位）；另含销售出库改批号后批次属性的回读（stock_plus 的
sale-out-batch-override 引用）。

只读；标识符是常量，编码只接受 [0-9A-Za-z._-]（safe_lot），id 只接受正整数（safe_id），再拼进 SQL。
挑单返回一行 ROW:a|b|…，挑不到返回空。
"""

from __future__ import annotations

from co.e2e.casespec.stock_misc_sql import safe_id

_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"
# 单据短码 → 表头表（只用于删除后的表头行数）。
_HEADS = {"01": "RdRecord01", "09": "RdRecord09", "10": "RdRecord10", "11": "RdRecord11"}
_LOT_PROPS = 10


def _blank(col: str) -> str:
    return f"LTRIM(RTRIM(ISNULL({col}, N''))) = N''"


def _no_free(alias: str) -> str:
    return " AND ".join(f"ISNULL({alias}.bFree{i}, 0) = 0" for i in range(1, 11))


def _bin_base(extra: str) -> str:
    # 货位仓库（bWhPos=1、未冻结）的末级货位上，无批号结存 >= 1 的普通存货（不管批次 / 保质期 / 序列号、不是服务、
    # 无计量单位组和自由项）；现存量（同仓库、无批号）可用量也 >= 1。种子数据的货位仓 01 / 货位 01 提供。
    avail = (
        "ISNULL((SELECT SUM(CASE WHEN ISNULL(c.bStopFlag, 0) = 1 OR ISNULL(c.bGSPStop, 0) = 1 THEN 0"
        " ELSE ISNULL(c.iQuantity, 0) - ISNULL(c.fStopQuantity, 0) END) FROM dbo.CurrentStock AS c"
        " WHERE c.cWhCode = w.cWhCode AND c.cInvCode = i.cInvCode AND ISNULL(c.cBatch, N'') = N''), 0)"
    )
    return f"""FROM dbo.Warehouse AS w
JOIN dbo.Position AS p ON p.cWhCode = w.cWhCode AND p.bPosEnd = 1
JOIN dbo.Inventory AS i ON EXISTS (
  SELECT 1 FROM dbo.InvPositionSum AS s
  WHERE s.cWhCode = w.cWhCode AND s.cPosCode = p.cPosCode AND s.cInvCode = i.cInvCode AND ISNULL(s.cBatch, N'') = N''
  GROUP BY s.cInvCode HAVING SUM(ISNULL(s.iQuantity, 0)) >= 1)
{extra}
WHERE w.bWhPos = 1 AND ISNULL(w.bFreeze, 0) = 0
  AND ISNULL(i.bInvBatch, 0) = 0 AND ISNULL(i.bInvQuality, 0) = 0 AND ISNULL(i.bSerial, 0) = 0
  AND ISNULL(i.bService, 0) = 0 AND ISNULL(i.iGroupType, 0) = 0 AND {_no_free("i")}
  AND LTRIM(RTRIM(w.cWhCode)) {_SAFE} AND LTRIM(RTRIM(p.cPosCode)) {_SAFE}
  AND LTRIM(RTRIM(i.cInvCode)) {_SAFE} AND LEN(i.cInvCode) <= 60
  AND {avail} >= 1"""


def _cols(*names: str) -> str:
    return " + N'|' + ".join(f"LTRIM(RTRIM({name}))" for name in names)


# ---- 挑单 ----


def pin_pick_sql() -> str:
    # 无来源采购入库：货位仓库、末级货位、外购且免检的普通存货；本币供应商和部门取最近一张普通采购入库单，
    # 采购类型优先默认的（同 stock_plus_sql.red_pin_sql）。仓库|货位|存货|供应商|部门|采购类型。
    extra = f"""CROSS APPLY (
  SELECT TOP (1) t.cPTCode FROM dbo.PurchaseType AS t
  WHERE NOT {_blank("t.cPTCode")} AND LTRIM(RTRIM(t.cPTCode)) {_SAFE}
  ORDER BY ISNULL(t.bDefault, 0) DESC, t.cPTCode
) AS pt
CROSS APPLY (
  SELECT TOP (1) h.cVenCode, h.cDepCode FROM dbo.RdRecord01 AS h
  JOIN dbo.Vendor AS v ON v.cVenCode = h.cVenCode
  WHERE h.cBusType = N'普通采购' AND ISNULL(v.cVenExch_name, N'人民币') IN (N'', N'人民币')
    AND NOT {_blank("h.cDepCode")}
    AND LTRIM(RTRIM(h.cVenCode)) {_SAFE} AND LTRIM(RTRIM(h.cDepCode)) {_SAFE}
  ORDER BY h.ID DESC
) AS r"""
    cols = _cols("w.cWhCode", "p.cPosCode", "i.cInvCode", "r.cVenCode", "r.cDepCode", "pt.cPTCode")
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + {cols} AS v
{_bin_base(extra)}
  AND ISNULL(i.bPurchase, 0) = 1 AND ISNULL(i.bPropertyCheck, 0) = 0
ORDER BY w.cWhCode, p.cPosCode, i.cInvCode;
"""


def oout_pick_sql() -> str:
    # 其他出库：货位仓库、末级货位上有结存的普通存货；收发类别（出库、末级）和部门取最近一张来源为库存的其他出库单。
    # 仓库|货位|存货|收发类别|部门。
    extra = f"""CROSS APPLY (
  SELECT TOP (1) h.cRdCode, h.cDepCode FROM dbo.RdRecord09 AS h
  JOIN dbo.Rd_Style AS s ON s.cRdCode = h.cRdCode
  WHERE s.bRdFlag = 0 AND s.bRdEnd = 1 AND ISNULL(h.cSource, N'') = N'库存'
    AND NOT {_blank("h.cDepCode")}
    AND LTRIM(RTRIM(h.cRdCode)) {_SAFE} AND LTRIM(RTRIM(h.cDepCode)) {_SAFE}
  ORDER BY h.ID DESC
) AS r"""
    cols = _cols("w.cWhCode", "p.cPosCode", "i.cInvCode", "r.cRdCode", "r.cDepCode")
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + {cols} AS v
{_bin_base(extra)}
ORDER BY w.cWhCode, p.cPosCode, i.cInvCode;
"""


def mout_pick_sql() -> str:
    # 无来源材料出库：货位仓库、末级货位上有结存的生产耗用（bComsume=1）普通存货；部门取最近一张材料出库单的。
    # 选项 ST.ballowAddnewVouch（领料必有订单，原文）|仓库|货位|存货|部门。
    extra = f"""CROSS APPLY (
  SELECT TOP (1) h.cDepCode FROM dbo.RdRecord11 AS h
  WHERE NOT {_blank("h.cDepCode")} AND LTRIM(RTRIM(h.cDepCode)) {_SAFE}
  ORDER BY h.ID DESC
) AS r"""
    opt = (
        "ISNULL((SELECT TOP (1) LTRIM(RTRIM(o.cValue)) FROM dbo.AccInformation AS o"
        " WHERE o.cSysID = N'ST' AND o.cName = N'ballowAddnewVouch'), N'')"
    )
    cols = _cols("w.cWhCode", "p.cPosCode", "i.cInvCode", "r.cDepCode")
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + {opt} + N'|' + {cols} AS v
{_bin_base(extra)}
  AND ISNULL(i.bComsume, 0) = 1
ORDER BY w.cWhCode, p.cPosCode, i.cInvCode;
"""


def mo_bin_pick_sql() -> str:
    # 产成品入库参照生产订单进货位仓（同 product-in-from-mo 的订单行：已审核、不走审批流、不需检验、剩余 >= 2，
    # 母件是普通存货）；仓库、货位取货位仓库（bWhPos=1、未冻结）的末级货位，入库不要结存。
    # MoId|MoDId|仓库|货位|存货。
    quality = (
        "UPPER(ISNULL((SELECT TOP (1) LTRIM(RTRIM(o.cValue)) FROM dbo.AccInformation AS o"
        " WHERE o.cSysID = N'ST' AND o.cName = N'bQuality'), N'')) IN (N'TRUE', N'1')"
    )
    cols = _cols("CONVERT(varchar(20), d.MoId)", "CONVERT(varchar(20), d.MoDId)", "w.cWhCode", "p.cPosCode", "i.cInvCode")
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + {cols} AS v
FROM dbo.mom_orderdetail AS d
JOIN dbo.Inventory AS i ON i.cInvCode = d.InvCode
JOIN dbo.Warehouse AS w ON w.bWhPos = 1 AND ISNULL(w.bFreeze, 0) = 0
JOIN dbo.Position AS p ON p.cWhCode = w.cWhCode AND p.bPosEnd = 1
WHERE d.Status = 3 AND ISNULL(d.IsWFControlled, 0) = 0
  AND (ISNULL(d.QcFlag, 0) = 0 OR NOT ({quality})) AND d.Qty - ISNULL(d.QualifiedInQty, 0) >= 2
  AND ISNULL(i.bInvBatch, 0) = 0 AND ISNULL(i.bInvQuality, 0) = 0 AND ISNULL(i.bSerial, 0) = 0
  AND ISNULL(i.iGroupType, 0) = 0 AND {_no_free("i")}
  AND LTRIM(RTRIM(w.cWhCode)) {_SAFE} AND LTRIM(RTRIM(p.cPosCode)) {_SAFE}
  AND LTRIM(RTRIM(i.cInvCode)) {_SAFE} AND LEN(i.cInvCode) <= 60
ORDER BY d.MoDId DESC, w.cWhCode, p.cPosCode;
"""


# ---- 回读 ----


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _short(vt: str) -> str:
    if vt not in _HEADS:
        raise ValueError("单据短码不合法")
    return vt


def pos_rows_sql(vt: str, doc: str) -> str:
    """该单据（RdID + 短码）在货位台账 InvPosition 里的行数。"""
    frm = f"FROM dbo.InvPosition AS p WHERE p.RdID = {safe_id(doc)} AND p.cvouchtype = N'{_short(vt)}'"
    return _one("CONVERT(varchar(10), COUNT(*))", frm)


def head_rows_sql(vt: str, doc: str) -> str:
    """单据表头行数（删除后应为 0）。"""
    return _one("CONVERT(varchar(10), COUNT(*))", f"FROM dbo.{_HEADS[_short(vt)]} AS h WHERE h.ID = {safe_id(doc)}")


def _prop_diff(n: int) -> str:
    # 6..9 是文本（空串与 NULL 同看），1..5 是数、10 是日期：相等或都为 NULL 才算一致。
    col = f"cBatchProperty{n}"
    if 6 <= n <= 9:
        return f"CASE WHEN ISNULL(a.{col}, N'') = ISNULL(b.{col}, N'') THEN 0 ELSE 1 END"
    return f"CASE WHEN a.{col} = b.{col} OR (a.{col} IS NULL AND b.{col} IS NULL) THEN 0 ELSE 1 END"


def lot_prop_sql(doc: str, line: str) -> str:
    """销售出库单上挂该发货行、带批号的每一行：批号|与批次属性档案不一致的属性个数|行上属性 10|档案属性 10|档案有无（1/0）。

    档案 AA_BatchProperty 按存货、批号、自由项 1..10 对应；档案没有该批号时属性应全空（数为 NULL、文本为空）。
    属性 10 是日期，按 ISO（yyyy-mm-ddThh:mi:ss）回读，核对桥按该格式写入的日期 U8 原样落库（u8-notes §8）。
    """
    cols = ", ".join(f"x.cBatchProperty{n}" for n in range(1, _LOT_PROPS + 1))
    free = " AND ".join(f"ISNULL(x.cFree{n}, N'') = ISNULL(b.cFree{n}, N'')" for n in range(1, 11))
    diff = " + ".join(_prop_diff(n) for n in range(1, _LOT_PROPS + 1))
    expr = (
        "ISNULL(b.cBatch, N'') + N'|' + CONVERT(varchar(5), " + diff + ") + N'|'"
        " + ISNULL(CONVERT(varchar(19), b.cBatchProperty10, 126), N'') + N'|'"
        " + ISNULL(CONVERT(varchar(19), a.cBatchProperty10, 126), N'') + N'|'"
        " + CASE WHEN a.cInvCode IS NULL THEN N'0' ELSE N'1' END"
    )
    frm = f"""FROM dbo.rdrecords32 AS b
OUTER APPLY (
  SELECT TOP (1) x.cInvCode, {cols} FROM dbo.AA_BatchProperty AS x
  WHERE x.cInvCode = b.cInvCode AND x.cBatch = b.cBatch AND {free}
) AS a
WHERE b.ID = {safe_id(doc)} AND b.iDLsID = {safe_id(line)} AND ISNULL(b.cBatch, N'') <> N''
ORDER BY b.AutoID"""
    return _one(expr, frm)


def stock_binned_samples() -> list[tuple[str, str]]:
    return [
        ("pin-pick", pin_pick_sql()),
        ("oout-pick", oout_pick_sql()),
        ("mout-pick", mout_pick_sql()),
        ("mo-bin-pick", mo_bin_pick_sql()),
        ("pos-rows", pos_rows_sql("01", "100")),
        ("head-rows", head_rows_sql("09", "100")),
        ("lot-prop", lot_prop_sql("100", "101")),
    ]


def havepo_value_sql() -> str:
    # 采购选项「普通业务必有订单」的原值（原样记下，结束时写回）。
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + ISNULL(o.cValue, N'') AS v FROM dbo.AccInformation AS o
WHERE o.cSysID = N'PU' AND o.cName = N'bPTHavePO';
"""


def set_havepo_sql(value: str) -> str:
    # 临时改「普通业务必有订单」（只在测试账套快照里）：value 只能是 True / False / 1 / 0 这类短值。
    if value not in ("True", "False", "TRUE", "FALSE", "true", "false", "1", "0"):
        raise ValueError("选项值不能拼进 SQL")
    return f"""SET NOCOUNT ON;
UPDATE dbo.AccInformation SET cValue = N'{value}' WHERE cSysID = N'PU' AND cName = N'bPTHavePO';
SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;
"""
