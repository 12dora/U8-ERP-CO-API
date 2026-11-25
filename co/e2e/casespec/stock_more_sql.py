"""库存待实测补用例（stock_more）的挑单与回读 SQL。

只读（reopen_sql 除外：只清本用例自建的调拨申请单的关闭人）；标识符是常量，编码只接受 [0-9A-Za-z._-]（safe_lot），
id 只接受正整数（safe_id），卡片号只接受登记过的单据，再拼进 SQL。挑单返回一行 ROW:a|b|…，挑不到返回空。
"""

from __future__ import annotations

from co.e2e.casespec.stock_misc_sql import safe_id
from co.e2e.casespec.stock_plus_sql import _no_free, _sale_inv, _so_template, _template_cols, safe_lot

_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"
# 单据 → （编号卡片, 表头表, 单号列, 表体表）。卡片号同 MetaFieldsVt；表体单号列与表头同名。
_NUMBERED = {
    "shape_change": ("0305", "AssemVouch", "cAVCode", "AssemVouchs"),
    "transfer_request": ("0324", "ST_AppTransVouch", "cTVCode", "ST_AppTransVouchs"),
    "stock_check": ("0307", "CheckVouch", "cCVCode", "CheckVouchs"),
}
# 形态转换审核生成的转换入库（08，卡片 0301 其他入库）、转换出库（09，卡片 0302 其他出库）。
_MADE_CARDS = {"08": "0301", "09": "0302"}


def _blank(col: str) -> str:
    return f"LTRIM(RTRIM(ISNULL({col}, N''))) = N''"


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({expr}, 0)))"


def _day(expr: str) -> str:
    return f"ISNULL(CONVERT(varchar(10), {expr}, 23), N'')"


def _text(expr: str) -> str:
    return f"ISNULL(LTRIM(RTRIM(CONVERT(nvarchar(40), {expr}))), N'')"


# ---- 销售出库改批号后的日期列（`sale-out-batch-dates`） ----


def batch_dates_pick_sql() -> str:
    # 同 stock_plus_sql.batch_sql（不管货位的仓库里两个可用批号），批号 2 优先挑批次属性档案里
    # 属性 10（日期）有值的，好核对日期属性的写法。客户|销售类型|部门|业务员|存货|仓库|批号 1|批号 2|批号 2 有日期属性（1/0）。
    dated = (
        "CASE WHEN EXISTS (SELECT 1 FROM dbo.AA_BatchProperty AS a WHERE a.cInvCode = c.cInvCode"
        " AND a.cBatch = c.cBatch AND a.cBatchProperty10 IS NOT NULL) THEN 1 ELSE 0 END"
    )
    lot = f"""SELECT TOP (1) c.cBatch, {dated} AS dated FROM dbo.CurrentStock AS c
    WHERE c.cWhCode = w.cWhCode AND c.cInvCode = i.cInvCode AND ISNULL(c.cBatch, N'') <> N'' {{extra}}
      AND ISNULL(c.bStopFlag, 0) = 0 AND ISNULL(c.bGSPStop, 0) = 0
      AND ISNULL(c.iQuantity, 0) - ISNULL(c.fStopQuantity, 0) >= 1
      AND LTRIM(RTRIM(c.cBatch)) {_SAFE} AND LEN(c.cBatch) <= 60
    ORDER BY {{order}}"""
    first = lot.format(extra="", order="c.cBatch")
    second = lot.format(extra="AND c.cBatch <> b1.cBatch", order="dated DESC, c.cBatch")
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + {_template_cols()} + N'|' + LTRIM(RTRIM(b1.cBatch)) + N'|' + LTRIM(RTRIM(b2.cBatch))
  + N'|' + CONVERT(varchar(1), b2.dated) AS v
FROM dbo.Warehouse AS w
JOIN dbo.Inventory AS i ON EXISTS (SELECT 1 FROM dbo.CurrentStock AS e WHERE e.cWhCode = w.cWhCode AND e.cInvCode = i.cInvCode)
{_so_template()}
CROSS APPLY ({first}) AS b1
CROSS APPLY ({second}) AS b2
WHERE ISNULL(w.bWhPos, 0) = 0
  AND {_sale_inv(1)}
ORDER BY b2.dated DESC, w.cWhCode, i.cInvCode;
"""


def lot_dates_sql(doc: str, line: str) -> str:
    """出库单上挂该发货行、带批号的每一行：批号|生产日期|失效日期|保质期|保质期单位|cExpirationdate|有效期至|
    有效期推算方式|行上属性 10|档案属性 10。日期属性按 ISO（yyyy-mm-ddThh:mi:ss）回读。"""
    attr = (
        "(SELECT TOP (1) CONVERT(varchar(19), x.cBatchProperty10, 126) FROM dbo.AA_BatchProperty AS x"
        " WHERE x.cInvCode = b.cInvCode AND x.cBatch = b.cBatch AND "
        + " AND ".join(f"ISNULL(x.cFree{n}, N'') = ISNULL(b.cFree{n}, N'')" for n in range(1, 11))
        + ")"
    )
    cols = [
        "ISNULL(b.cBatch, N'')", _day("b.dMadeDate"), _day("b.dVDate"), _text("b.iMassDate"), _text("b.cMassUnit"),
        _text("b.cExpirationdate"), _day("b.dExpirationdate"), _text("b.iExpiratDateCalcu"),
        "ISNULL(CONVERT(varchar(19), b.cBatchProperty10, 126), N'')", f"ISNULL({attr}, N'')",
    ]
    frm = (
        f"FROM dbo.rdrecords32 AS b WHERE b.ID = {safe_id(doc)} AND b.iDLsID = {safe_id(line)}"
        " AND ISNULL(b.cBatch, N'') <> N'' ORDER BY b.AutoID"
    )
    return _one(" + N'|' + ".join(cols), frm)


# ---- 保质期存货的无来源采购入库（`purchase-in-shelf`） ----


def shelf_pick_sql() -> str:
    # 不管货位的仓库里已有现存量记录（仓库存货对照不拦）的保质期管理存货：外购、免检、不管序列号、无计量单位组和自由项，
    # 保质期 > 0、单位是年 / 月 / 日（1 / 2 / 3）。供应商、部门、采购类型同 stock_binned_sql.pin_pick_sql。
    # 仓库|存货|供应商|部门|采购类型|保质期|保质期单位。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(w.cWhCode)) + N'|' + LTRIM(RTRIM(i.cInvCode)) + N'|' + LTRIM(RTRIM(r.cVenCode))
  + N'|' + LTRIM(RTRIM(r.cDepCode)) + N'|' + LTRIM(RTRIM(pt.cPTCode)) + N'|' + CONVERT(varchar(10), i.iMassDate)
  + N'|' + CONVERT(varchar(5), i.cMassUnit) AS v
FROM dbo.Warehouse AS w
JOIN dbo.Inventory AS i ON EXISTS (SELECT 1 FROM dbo.CurrentStock AS e WHERE e.cWhCode = w.cWhCode AND e.cInvCode = i.cInvCode)
CROSS APPLY (
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
) AS r
WHERE ISNULL(w.bWhPos, 0) = 0 AND ISNULL(w.bFreeze, 0) = 0
  AND ISNULL(i.bInvQuality, 0) = 1 AND ISNULL(i.bPurchase, 0) = 1 AND ISNULL(i.bPropertyCheck, 0) = 0
  AND ISNULL(i.bSerial, 0) = 0 AND ISNULL(i.iGroupType, 0) = 0 AND {_no_free("i")}
  AND ISNULL(i.iMassDate, 0) > 0 AND ISNULL(i.cMassUnit, 0) IN (1, 2, 3)
  AND LTRIM(RTRIM(w.cWhCode)) {_SAFE} AND LTRIM(RTRIM(i.cInvCode)) {_SAFE} AND LEN(i.cInvCode) <= 60
ORDER BY w.cWhCode, i.cInvCode;
"""


def shelf_rows_sql(doc: str) -> str:
    """采购入库单的每一行：批号|生产日期|失效日期|保质期|保质期单位|cExpirationdate|有效期至|有效期推算方式。"""
    cols = [
        "ISNULL(b.cBatch, N'')", _day("b.dMadeDate"), _day("b.dVDate"), _text("b.iMassDate"), _text("b.cMassUnit"),
        _text("b.cExpirationdate"), _day("b.dExpirationdate"), _text("b.iExpiratDateCalcu"),
    ]
    return _one(" + N'|' + ".join(cols), f"FROM dbo.rdrecords01 AS b WHERE b.ID = {safe_id(doc)} ORDER BY b.AutoID")


def shelf_stock_sql(wh: str, inv: str, lot: str) -> str:
    """该仓库、存货、批号的现存量：行数|数量合计|最早失效日期|最早有效期至。"""
    frm = (
        f"FROM dbo.CurrentStock AS c WHERE c.cWhCode = N'{safe_lot(wh)}' AND c.cInvCode = N'{safe_lot(inv)}'"
        f" AND c.cBatch = N'{safe_lot(lot)}'"
    )
    expr = (
        "CONVERT(varchar(10), COUNT(*)) + N'|' + " + _qty("SUM(c.iQuantity)") + " + N'|' + "
        + _day("MIN(c.dVDate)") + " + N'|' + " + _day("MIN(c.dExpirationdate)")
    )
    return _one(expr, frm)


# ---- 货位仓库的盘点单（`stock-check-by-bin`） ----


def _simple_inv(alias: str) -> str:
    return (
        f"ISNULL({alias}.bInvBatch, 0) = 0 AND ISNULL({alias}.bInvQuality, 0) = 0 AND ISNULL({alias}.bSerial, 0) = 0"
        f" AND ISNULL({alias}.iGroupType, 0) = 0 AND ISNULL({alias}.bService, 0) = 0 AND {_no_free(alias)}"
        f" AND LTRIM(RTRIM({alias}.cInvCode)) {_SAFE} AND LEN({alias}.cInvCode) <= 60"
    )


def _stock_total(wh: str, inv: str) -> str:
    return (
        f"(SELECT ISNULL(SUM(c.iQuantity), 0) FROM dbo.CurrentStock AS c WHERE c.cWhCode = {wh}"
        f" AND c.cInvCode = {inv} AND ISNULL(c.cBatch, N'') = N'')"
    )


def _pos_total(wh: str, inv: str) -> str:
    return (
        f"(SELECT ISNULL(SUM(s.iQuantity), 0) FROM dbo.InvPositionSum AS s WHERE s.cWhCode = {wh}"
        f" AND s.cInvCode = {inv} AND ISNULL(s.cBatch, N'') = N'')"
    )


def bin_check_pick_sql() -> str:
    # 货位仓库（bWhPos=1、未冻结、没有未审核的盘点单）里现存量（无批号）>= 1 的简单存货，且货位存量合计等于现存量
    # （货位台账与现存量一致，账面数量才有唯一的对照值）；结存分在多个货位的优先。仓库|存货|有结存的货位数|现存量。
    bins = (
        "(SELECT COUNT(*) FROM (SELECT s.cPosCode FROM dbo.InvPositionSum AS s WHERE s.cWhCode = w.cWhCode"
        " AND s.cInvCode = i.cInvCode AND ISNULL(s.cBatch, N'') = N'' GROUP BY s.cPosCode"
        " HAVING SUM(ISNULL(s.iQuantity, 0)) <> 0) AS k)"
    )
    stock = _stock_total("w.cWhCode", "i.cInvCode")
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(w.cWhCode)) + N'|' + LTRIM(RTRIM(i.cInvCode)) + N'|' + CONVERT(varchar(10), {bins})
  + N'|' + {_qty(stock)} AS v
FROM dbo.Warehouse AS w
JOIN dbo.Inventory AS i ON EXISTS (SELECT 1 FROM dbo.InvPositionSum AS e WHERE e.cWhCode = w.cWhCode AND e.cInvCode = i.cInvCode)
WHERE w.bWhPos = 1 AND ISNULL(w.bFreeze, 0) = 0 AND LTRIM(RTRIM(w.cWhCode)) {_SAFE} AND LEN(w.cWhCode) <= 12
  AND {_simple_inv("i")}
  AND NOT EXISTS (SELECT 1 FROM dbo.CheckVouch AS k WHERE k.cWhCode = w.cWhCode AND {_blank("k.cAccounter")})
  AND {stock} >= 1
  AND ABS({stock} - {_pos_total("w.cWhCode", "i.cInvCode")}) < 0.0001
ORDER BY {bins} DESC, w.cWhCode, i.cInvCode;
"""


def bin_sums_sql(wh: str, inv: str) -> str:
    """该仓库、存货（无批号）：现存量合计|货位存量合计|各货位明细（货位:数量，逗号分隔）。"""
    w, i = f"N'{safe_lot(wh)}'", f"N'{safe_lot(inv)}'"
    inner = (
        "SELECT N',' + LTRIM(RTRIM(s.cPosCode)) + N':' + " + _qty("SUM(s.iQuantity)")
        + f" FROM dbo.InvPositionSum AS s WHERE s.cWhCode = {w} AND s.cInvCode = {i} AND ISNULL(s.cBatch, N'') = N''"
        " GROUP BY s.cPosCode HAVING SUM(ISNULL(s.iQuantity, 0)) <> 0 ORDER BY s.cPosCode FOR XML PATH('')"
    )
    expr = (
        _qty(_stock_total(w, i)) + " + N'|' + " + _qty(_pos_total(w, i))
        + " + N'|' + ISNULL(STUFF((" + inner + "), 1, 1, N''), N'')"
    )
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v;\n"


def check_rows_sql(doc: str) -> str:
    """盘点单的每一行：存货|账面数量|货位。"""
    expr = "LTRIM(RTRIM(t.cInvCode)) + N'|' + " + _qty("t.iCVQuantity") + " + N'|' + " + _text("t.cPosition")
    return _one(expr, f"FROM dbo.CheckVouchs AS t WHERE t.ID = {safe_id(doc)} ORDER BY t.autoID")


def check_gone_sql(doc: str) -> str:
    return _one("CONVERT(varchar(10), COUNT(*))", f"FROM dbo.CheckVouch AS h WHERE h.ID = {safe_id(doc)}")


# ---- 单号：编号卡片的流水、表体单号列 ----


def _card_where(card: str) -> str:
    # 编号规则是「前缀 + 种子（日期等，桥 BillNo.FillSeeds 按 yyyymmdd / yyyymm / yyyy 填）+ 定长流水」，单号末尾的数字段
    # 连着日期种子（如 AB202601010001），不能整段当流水。对得上 = 该卡片的流水行种子 cSeed 为空或出现在单号里，
    # 且单号以该行 cNumber（去前导 0、非空）结尾。流水位数离线定不了，不再要求中间只有补位的 0。
    number = "LTRIM(RTRIM(CONVERT(nvarchar(40), v.cNumber)))"
    plain = f"SUBSTRING({number}, PATINDEX(N'%[^0]%', {number} + N'.'), 40)"
    seed = "LTRIM(RTRIM(ISNULL(CONVERT(nvarchar(250), v.cSeed), N'')))"
    return (
        f"FROM dbo.VoucherHistory AS v WHERE v.CardNumber = N'{card}' AND {number} NOT LIKE N'%[^0-9]%'"
        f" AND {plain} <> N'' AND RIGHT(x.code, LEN({plain})) = {plain}"
        f" AND ({seed} = N'' OR CHARINDEX({seed}, x.code) > 0)"
    )


def _number_sql(card: str, code_expr: str) -> str:
    rows = f"(SELECT COUNT(*) FROM dbo.VoucherHistory AS v WHERE v.CardNumber = N'{card}')"
    where = _card_where(card)
    hit = f"(SELECT COUNT(*) {where})"
    shown = (
        f"ISNULL((SELECT TOP (1) LTRIM(RTRIM(CONVERT(nvarchar(250), ISNULL(v.cSeed, N'')))) + N'/'"
        f" + LTRIM(RTRIM(CONVERT(nvarchar(40), v.cNumber))) {where}), N'')"
    )
    expr = (
        "x.code + N'|' + CONVERT(varchar(10), " + rows + ") + N'|' + CONVERT(varchar(10), " + hit + ") + N'|' + " + shown
    )
    return f"SET NOCOUNT ON;\nSELECT N'ROW:' + {expr} AS v\nFROM (SELECT ISNULL(LTRIM(RTRIM(({code_expr}))), N'') AS code) AS x;\n"


def _numbered(kind: str) -> tuple[str, str, str, str]:
    if kind not in _NUMBERED:
        raise ValueError("单据类型没有登记编号卡片")
    return _NUMBERED[kind]


def number_card(kind: str) -> str:
    return _numbered(kind)[0]


def made_card(rd: str) -> str:
    if rd not in _MADE_CARDS:
        raise ValueError("生成单短码不合法")
    return _MADE_CARDS[rd]


def head_number_sql(kind: str, doc: str) -> str:
    """单号|该卡片的流水行数|对得上的流水行数|对上的那行「种子/cNumber」。"""
    card, table, col, _body = _numbered(kind)
    return _number_sql(card, f"SELECT h.{col} FROM dbo.{table} AS h WHERE h.ID = {safe_id(doc)}")


def made_number_sql(rd: str, av_doc: str) -> str:
    """形态转换单审核生成的转换入库（08）/ 转换出库（09）：同 head_number_sql 的四列。"""
    card = made_card(rd)
    code = f"SELECT a.cAVCode FROM dbo.AssemVouch AS a WHERE a.ID = {safe_id(av_doc)} AND a.cVouchType = N'15'"
    expr = (
        f"SELECT TOP (1) r.cCode FROM dbo.RdRecord{rd} AS r WHERE r.cSource = N'形态转换'"
        f" AND r.cBusCode = ({code}) ORDER BY r.ID DESC"
    )
    return _number_sql(card, expr)


def body_code_sql(kind: str, doc: str) -> str:
    """表体行数|表体单号列等于表头单号的行数。"""
    _card, table, col, body = _numbered(kind)
    same = (
        f"(SELECT COUNT(*) FROM dbo.{body} AS b JOIN dbo.{table} AS h ON h.ID = b.ID"
        f" WHERE b.ID = {safe_id(doc)} AND LTRIM(RTRIM(ISNULL(b.{col}, N''))) = LTRIM(RTRIM(h.{col})))"
    )
    total = f"(SELECT COUNT(*) FROM dbo.{body} AS b WHERE b.ID = {safe_id(doc)})"
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(10), " + total + ") + N'|' + CONVERT(varchar(10), " + same + ") AS v;\n"


# ---- 调拨申请单行的关闭（transfer-closer） ----


def req_state_sql(req: str) -> str:
    """调拨申请单：单号|第 1 行 autoID|核准数量|累计调拨|行关闭人|表头关闭人。"""
    expr = (
        "LTRIM(RTRIM(h.cTVCode)) + N'|' + CONVERT(varchar(20), b.autoID) + N'|' + " + _qty("b.iTvChkQuantity")
        + " + N'|' + " + _qty("b.iTvSumQuantity") + " + N'|' + " + _text("b.cBCloser") + " + N'|' + " + _text("h.cCloser")
    )
    frm = (
        "FROM dbo.ST_AppTransVouch AS h JOIN dbo.ST_AppTransVouchs AS b ON b.ID = h.ID"
        f" WHERE h.ID = {safe_id(req)} AND b.autoID = (SELECT MIN(m.autoID) FROM dbo.ST_AppTransVouchs AS m WHERE m.ID = h.ID)"
    )
    return _one(expr, frm)


def tv_state_sql(tv: str) -> str:
    """调拨单：表头 cTranRequestCode|表体行数|带 iTRIds 的行数。"""
    expr = (
        _text("h.cTranRequestCode") + " + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.TransVouchs AS t WHERE t.ID = h.ID))"
        " + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.TransVouchs AS t WHERE t.ID = h.ID AND ISNULL(t.iTRIds, 0) <> 0))"
    )
    return _one(expr, f"FROM dbo.TransVouch AS h WHERE h.ID = {safe_id(tv)}")


def reopen_sql(req: str) -> str:
    """清掉本用例自建的调拨申请单的行关闭人和表头关闭人（U8 自动关闭时，删完下游后才能弃审、删除）。返回改动行数。"""
    doc = safe_id(req)
    return f"""SET NOCOUNT ON;
UPDATE dbo.ST_AppTransVouchs SET cBCloser = NULL WHERE ID = {doc} AND cBCloser IS NOT NULL;
DECLARE @n int = @@ROWCOUNT;
UPDATE dbo.ST_AppTransVouch SET cCloser = NULL WHERE ID = {doc} AND cCloser IS NOT NULL;
SELECT N'ROW:' + CONVERT(varchar(12), @n + @@ROWCOUNT) AS v;
"""


def stock_more_samples() -> list[tuple[str, str]]:
    return [
        ("stm-batch-dates-pick", batch_dates_pick_sql()),
        ("stm-lot-dates", lot_dates_sql("100", "101")),
        ("stm-shelf-pick", shelf_pick_sql()),
        ("stm-shelf-rows", shelf_rows_sql("100")),
        ("stm-shelf-stock", shelf_stock_sql("01", "0001", "E2ESL01")),
        ("stm-bin-check-pick", bin_check_pick_sql()),
        ("stm-bin-sums", bin_sums_sql("01", "0001")),
        ("stm-check-rows", check_rows_sql("100")),
        ("stm-check-gone", check_gone_sql("100")),
        ("stm-number-av", head_number_sql("shape_change", "100")),
        ("stm-number-08", made_number_sql("08", "100")),
        ("stm-body-code", body_code_sql("stock_check", "100")),
        ("stm-req-state", req_state_sql("100")),
        ("stm-tv-state", tv_state_sql("100")),
        ("stm-reopen", reopen_sql("100")),
    ]
