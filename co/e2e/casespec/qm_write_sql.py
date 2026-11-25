"""质量单据新增 / 删除用例（qm_write）的挑单与回读 SQL。

QMI_PACKS 进挑单，has_qmi / qmi_sql 进 stash / pred 的回读，qmi_samples() 进样例 SQL（接线在 wiring 汇总模块）。
标识符都是常量，id 只接受正整数再拼进 SQL；挑出的编码只进请求正文，不拼进 SQL。
来料一侧的键以 qmi_ 开头（到货单 → 来料报检单 → 来料检验单），产品一侧以 qmp_ 开头（生产订单 → 产品报检单 → 产品检验单）。
"""

from __future__ import annotations

from co.e2e.casespec.mo_close_sql import safe_id

_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(col: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({col}, 0)))"


def _flag(col: str) -> str:
    return f"CONVERT(varchar(5), CONVERT(int, ISNULL({col}, 0)))"


# ---- 挑单 ----


def _scheme(kind: str, inv: str) -> str:
    # 该存货最近一张检验单（QM03 / QM04）用的检验方案（未停用）。桥缺省也取它，这里显式传，挑不到就不跑。
    return f"""CROSS APPLY (
  SELECT TOP (1) LTRIM(RTRIM(p.CPROJECTCODE)) AS code
  FROM dbo.QMCHECKVOUCHER AS q
  JOIN dbo.QMCHECKPROJECT AS p ON p.ID = q.PROJECTID
  WHERE q.CVOUCHTYPE = N'{kind}' AND q.CINVCODE = {inv} AND ISNULL(p.BSTOPUSE, 0) = 0
    AND LTRIM(RTRIM(p.CPROJECTCODE)) {_SAFE} AND LEN(p.CPROJECTCODE) <= 12
  ORDER BY q.ID DESC
) AS pr"""


def _checker(dept: str) -> str:
    # 检验员：报检部门（报检单的 cInspectDepCode 缺省即来源单据部门）里编码最小的在职人员。
    return f"""CROSS APPLY (
  SELECT TOP (1) LTRIM(RTRIM(s.cPersonCode)) AS code
  FROM dbo.Person AS s
  WHERE s.cDepCode = {dept}
    AND (s.dPInValidDate IS NULL OR s.dPInValidDate > GETDATE())
    AND LTRIM(RTRIM(s.cPersonCode)) {_SAFE} AND LEN(s.cPersonCode) <= 12
  ORDER BY s.cPersonCode
) AS ps"""


def qmi_src_sql() -> str:
    # 已审核、未关闭的普通采购订单行：存货要来料检验（bPropertyCheck = 1，生成的到货行 bGsp = 1）、无单位组、
    # 剩余可到货 >= 2、有单价、订单有部门且部门里有在职人员、该存货有可用的来料检验方案。
    # 采购订单 ID|行 ID|仓库（该存货最常用的采购入库仓库）|检验方案编码|检验员编码。
    return f"""SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + CONVERT(varchar(20), m.POID) + N'|' + CONVERT(varchar(20), d.ID) + N'|' + wh.code
  + N'|' + pr.code + N'|' + ps.code AS v
FROM dbo.PO_Pomain AS m
JOIN dbo.PO_Podetails AS d ON d.POID = m.POID
JOIN dbo.Inventory AS i ON i.cInvCode = d.cInvCode
CROSS APPLY (
  SELECT TOP (1) LTRIM(RTRIM(r.cWhCode)) AS code
  FROM dbo.RdRecord01 AS r
  JOIN dbo.rdrecords01 AS b ON b.ID = r.ID
  JOIN dbo.Warehouse AS w ON w.cWhCode = r.cWhCode
  WHERE b.cInvCode = d.cInvCode
    AND ISNULL(w.bFreeze, 0) = 0
    AND LTRIM(RTRIM(r.cWhCode)) {_SAFE}
  GROUP BY r.cWhCode
  ORDER BY COUNT(*) DESC, r.cWhCode
) AS wh
{_scheme("QM03", "d.cInvCode")}
{_checker("m.cDepCode")}
WHERE LTRIM(RTRIM(ISNULL(m.cVerifier, N''))) <> N''
  AND LTRIM(RTRIM(ISNULL(m.cCloser, N''))) = N''
  AND m.cBusType = N'普通采购'
  AND LTRIM(RTRIM(ISNULL(d.cbCloser, N''))) = N''
  AND ISNULL(d.iQuantity, 0) - ISNULL(d.iArrQTY, 0) >= 2
  AND (CASE WHEN ISNULL(d.bTaxCost, 0) = 1 THEN d.iTaxPrice ELSE d.iUnitPrice END) IS NOT NULL
  AND ISNULL(i.iGroupType, 0) = 0
  AND i.bPropertyCheck = 1
ORDER BY m.POID DESC, d.ID;
"""


def qmp_src_sql() -> str:
    # 已审核（Status 3）、未关闭、要检验（QcFlag = 1）的生产订单行：剩余可报检（Qty − DeclaredQty）>= 2，
    # 生产部门里有在职人员，该存货有可用的产品检验方案。生产订单 MoId|行 MoDId|检验方案编码|检验员编码。
    return f"""SET NOCOUNT ON;
SELECT TOP (1)
  N'ROW:' + CONVERT(varchar(20), d.MoId) + N'|' + CONVERT(varchar(20), d.MoDId) + N'|' + pr.code + N'|' + ps.code AS v
FROM dbo.mom_orderdetail AS d
{_scheme("QM04", "d.InvCode")}
{_checker("d.MDeptCode")}
WHERE d.Status = 3
  AND d.CloseDate IS NULL
  AND d.QcFlag = 1
  AND ISNULL(d.Qty, 0) - ISNULL(d.DeclaredQty, 0) >= 2
ORDER BY d.MoDId DESC;
"""


NO_DEFINE = "-"


_EXTRA = "dbo.QMCHECKVOUCHER_extradefine"
_DEFINE_COLS = ("chdefine11", "chdefine12", "chdefine13")


def _define_cell(col: str) -> str:
    # 列不存在（各账套扩展自定义项个数不同）记 -，用例不带这一项；值为空或含挑单不收的字符时用占位 E2E。
    return (
        f"CASE WHEN COL_LENGTH(N'{_EXTRA}', N'{col}') IS NULL THEN N'{NO_DEFINE}'"
        f" WHEN ISNULL(x.{col}, N'') = N'' OR LEN(x.{col}) > 12 OR x.{col} LIKE N'%[^0-9A-Za-z._-]%'"
        f" THEN N'E2E' ELSE x.{col} END"
    )


def _define_pick(col: str) -> str:
    # 按列名（不分大小写）从整行 XML 里取值：列不存在时不会因列名无效而整批编译失败。
    return (
        f"LTRIM(RTRIM(doc.value(N'(/r/*[lower-case(local-name()) = \"{col}\"])[1]', N'nvarchar(400)')))"
        f" AS {col}"
    )


def _define_sql(kind: str) -> str:
    # 检验单模板把扩展自定义项 chdefine11–13 设为必输（实测 QM03 / QM04）。取最近一张同类检验单的值，
    # 没有或含挑单不收的字符时用占位 E2E。三个值|分隔。账套没定义过扩展自定义项时没有
    # QMCHECKVOUCHER_extradefine 表，返回 -|-|-，用例不带这三项（表名延迟解析，IF 分支不执行就不报错）。
    # 表在但缺某一列时（小账套只定义了部分扩展自定义项）直接写列名会报「列名无效」，且守卫不允许动态 SQL，
    # 所以整行 e.* 转成 XML 再按列名取值，缺的列记 -。
    cells = " + N'|' + ".join(_define_cell(c) for c in _DEFINE_COLS)
    picks = ",\n    ".join(_define_pick(c) for c in _DEFINE_COLS)
    return f"""SET NOCOUNT ON;
SET QUOTED_IDENTIFIER ON;
IF OBJECT_ID(N'{_EXTRA}') IS NULL
  SELECT N'ROW:{NO_DEFINE}|{NO_DEFINE}|{NO_DEFINE}' AS v;
ELSE
SELECT N'ROW:' + {cells} AS v
FROM (SELECT 1 AS k) AS one
CROSS APPLY (
  SELECT (
    SELECT TOP (1) e.*
    FROM dbo.QMCHECKVOUCHER AS q
    JOIN {_EXTRA} AS e ON e.ID = q.ID
    WHERE q.CVOUCHTYPE = N'{kind}'
    ORDER BY q.ID DESC
    FOR XML PATH(N'r'), TYPE
  ) AS doc
) AS d
CROSS APPLY (
  SELECT {picks}
) AS x;
"""


def qmi_def_sql() -> str:
    return _define_sql("QM03")


def qmp_def_sql() -> str:
    return _define_sql("QM04")


# 质量单据模板（VT）→ 表头表。自定义项 cdefine 在表头表，扩展自定义项 chdefine 在 <表头表>_extradefine。
TPL_TABLES = {
    351: "QMINSPECTVOUCHER",
    352: "QMINSPECTVOUCHER",
    353: "QMCHECKVOUCHER",
    354: "QMCHECKVOUCHER",
    355: "QMREJECTVOUCHER",
    356: "QMREJECTVOUCHER",
    # 其他报检单、其他检验单（qm_other_write）。
    361: "QMINSPECTVOUCHER",
    365: "QMCHECKVOUCHER",
}


def tpl_required_sql(vt: int) -> str:
    """U8 单据模板上设为必输的字段（同桥 QmSql.RequiredSql：voucheritems.IsNull=1），每行 <字段名>|<T/B>|<列类型>。

    列类型按字段名在表头表或其 _extradefine 表里找（sys.columns），找不到为空（桥自己填的非列字段等）。
    """
    table = TPL_TABLES.get(vt) if type(vt) is int else None
    if table is None:
        raise ValueError("不是质量单据模板 VT")
    return f"""SET NOCOUNT ON;
SELECT DISTINCT N'ROW:' + LTRIM(RTRIM(v.FieldName)) + N'|' + UPPER(LTRIM(RTRIM(ISNULL(v.CardSection, N''))))
  + N'|' + ISNULL(t.kind, N'') AS v
FROM dbo.voucheritems AS v
OUTER APPLY (
  SELECT TOP (1) TYPE_NAME(c.system_type_id) AS kind
  FROM sys.columns AS c
  WHERE c.object_id IN (OBJECT_ID(N'dbo.{table}', N'U'), OBJECT_ID(N'dbo.{table}_extradefine', N'U'))
    AND c.name = v.FieldName
) AS t
WHERE v.VT_ID = {vt} AND v.IsNull = 1
ORDER BY 1;
"""


# 挑单登记（同 returns_misc_sql.CUSTOM_PACKS 的形状）：前导键、按 | 拆开的键、是否必须是正整数 id、SQL。
QMI_KEYS = ("qmi_po", "qmi_pol", "qmi_wh", "qmi_proj", "qmi_psn")
QMP_KEYS = ("qmp_mo", "qmp_mod", "qmp_proj", "qmp_psn")
QMI_PACKS = (
    ("qmi_po", QMI_KEYS, (True, True, False, False, False), qmi_src_sql),
    ("qmi_d11", ("qmi_d11", "qmi_d12", "qmi_d13"), (False,) * 3, qmi_def_sql),
    ("qmp_mo", QMP_KEYS, (True, True, False, False), qmp_src_sql),
    ("qmp_d11", ("qmp_d11", "qmp_d12", "qmp_d13"), (False,) * 3, qmp_def_sql),
)


# ---- 回读 ----


def _arr_flags(doc: str, _bag: dict[str, str]) -> str:
    # 新到货单（本用例只有一行）：bGsp|bInspect。要检验的存货应是 1|0（bInspect 是「已报检」，由报检单写）。
    expr = _flag("MIN(s.bGsp)") + " + N'|' + " + _flag("MAX(s.bInspect)")
    return _one(expr, f"FROM dbo.PU_ArrivalVouchs AS s WHERE s.ID = {safe_id(doc)}")


def _arr_line(doc: str, _bag: dict[str, str]) -> str:
    return _one("CONVERT(varchar(20), MIN(s.Autoid))", f"FROM dbo.PU_ArrivalVouchs AS s WHERE s.ID = {safe_id(doc)}")


def _arr_insp(doc: str, _bag: dict[str, str]) -> str:
    # 到货行累计报检数量|已报检标志。
    expr = _qty("SUM(s.fInspectQuantity)") + " + N'|' + " + _flag("MAX(s.bInspect)")
    return _one(expr, f"FROM dbo.PU_ArrivalVouchs AS s WHERE s.ID = {safe_id(doc)}")


def _insp_line(doc: str, _bag: dict[str, str]) -> str:
    return _one("CONVERT(varchar(20), MIN(s.AUTOID))", f"FROM dbo.QMINSPECTVOUCHERS AS s WHERE s.ID = {safe_id(doc)}")


def _insp_sum(doc: str, _bag: dict[str, str]) -> str:
    # 报检单行累计检验数量|检验完毕标志（BFLAG）。
    expr = _qty("SUM(s.FSUMCHECKQTY)") + " + N'|' + " + _flag("MAX(CONVERT(int, s.BFLAG))")
    return _one(expr, f"FROM dbo.QMINSPECTVOUCHERS AS s WHERE s.ID = {safe_id(doc)}")


def _insp_maker(doc: str, _bag: dict[str, str]) -> str:
    return _one("ISNULL(MAX(h.CMAKER), N'')", f"FROM dbo.QMINSPECTVOUCHER AS h WHERE h.ID = {safe_id(doc)}")


def _insp_gone(doc: str, _bag: dict[str, str]) -> str:
    # 删除后：报检单表头、表体和引用它的检验单都没有了。
    ref = safe_id(doc)
    expr = (
        f"CONVERT(varchar(20), (SELECT COUNT(*) FROM dbo.QMINSPECTVOUCHER AS h WHERE h.ID = {ref})"
        f" + (SELECT COUNT(*) FROM dbo.QMINSPECTVOUCHERS AS s WHERE s.ID = {ref})"
        f" + (SELECT COUNT(*) FROM dbo.QMCHECKVOUCHER AS q WHERE q.INSPECTID = {ref}))"
    )
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v;\n"


def _mo_decl(doc: str, bag: dict[str, str]) -> str:
    # 生产订单行累计报检数量（DeclaredQty，U8 在产品报检单保存 / 删除时写回）。doc 是 MoId。
    frm = f"FROM dbo.mom_orderdetail AS d WHERE d.MoId = {safe_id(doc)} AND d.MoDId = {safe_id(bag.get('qmp_mod', ''))}"
    return _one(_qty("MAX(d.DeclaredQty)"), frm)


_READBACK = {
    "qmi_arr_flags": _arr_flags,
    "qmi_arrl": _arr_line,
    "qmi_arr_insp": _arr_insp,
    "qmi_q1l": _insp_line,
    "qmi_q1_sum": _insp_sum,
    "qmi_q1_maker": _insp_maker,
    "qmi_q1_gone": _insp_gone,
    "qmp_decl": _mo_decl,
    "qmp_q2l": _insp_line,
    "qmp_q2_sum": _insp_sum,
    "qmp_q2_maker": _insp_maker,
    "qmp_q2_gone": _insp_gone,
}


def has_qmi(name: str) -> bool:
    return name in _READBACK


def qmi_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def qmi_samples() -> list[tuple[str, str]]:
    rows = [("more-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in QMI_PACKS]
    bag = {"qmp_mod": "100"}
    rows.extend(("more-" + name.replace("_", "-"), fn("100", bag)) for name, fn in _READBACK.items())
    rows.extend(("more-tpl-" + str(vt), tpl_required_sql(vt)) for vt in TPL_TABLES)
    return rows
