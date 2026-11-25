"""质量单据修改用例（qm_update、qm_other_update）的挑单与回读 SQL。只读；标识符是常量，id 只接受正整数再拼进 SQL。

QMU_PACKS 进挑单（不良原因编码、让步接收核准人、来料检验单审批流），has_qmu / qmu_sql 进 stash / pred 的回读，qmu_samples() 进样例 SQL（接线在 wiring 汇总模块）。
"""

from __future__ import annotations

from co.e2e.casespec.mo_close_sql import safe_id
from co.e2e.casespec.qm_reject_sql import reason_pick_sql


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(col: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({col}, 0)))"


def _check_from(doc: str) -> str:
    return f"FROM dbo.QMCHECKVOUCHER AS q WHERE q.ID = {safe_id(doc)}"


def check_result_sql(doc: str) -> str:
    """检验单：结论|合格|让步|不良（数量 4 位小数）。"""
    expr = (
        "ISNULL(LTRIM(RTRIM(q.CCHKCONCLUSION)), N'') + N'|' + " + _qty("q.FREGQUANTITY") + " + N'|' + "
        + _qty("q.FCONQUANTIY") + " + N'|' + " + _qty("q.FDISQUANTITY")
    )
    return _one(expr, _check_from(doc))


def check_ufts_sql(doc: str) -> str:
    return _one("CONVERT(varchar(20), CONVERT(bigint, q.UFTS))", _check_from(doc))


def check_modifier_sql(doc: str) -> str:
    return _one("ISNULL(LTRIM(RTRIM(q.CMODIFIER)), N'')", _check_from(doc))


def inspect_state_sql(doc: str) -> str:
    """其他报检单：CDEFINE10|UFTS（bigint）|审核人。"""
    expr = (
        "ISNULL(LTRIM(RTRIM(h.CDEFINE10)), N'') + N'|' + CONVERT(varchar(20), CONVERT(bigint, h.UFTS)) + N'|' "
        "+ ISNULL(LTRIM(RTRIM(h.CVERIFIER)), N'')"
    )
    return _one(expr, f"FROM dbo.QMINSPECTVOUCHER AS h WHERE h.ID = {safe_id(doc)}")


def check_state_sql(doc: str) -> str:
    """其他检验单：结论|合格|让步|不良|CDEFINE10|UFTS（bigint）|审核人|修改人。"""
    expr = (
        "ISNULL(LTRIM(RTRIM(q.CCHKCONCLUSION)), N'') + N'|' + " + _qty("q.FREGQUANTITY") + " + N'|' + "
        + _qty("q.FCONQUANTIY") + " + N'|' + " + _qty("q.FDISQUANTITY") + " + N'|' + ISNULL(LTRIM(RTRIM(q.CDEFINE10)), N'')"
        " + N'|' + CONVERT(varchar(20), CONVERT(bigint, q.UFTS)) + N'|' + ISNULL(LTRIM(RTRIM(q.CVERIFIER)), N'')"
        " + N'|' + ISNULL(LTRIM(RTRIM(q.CMODIFIER)), N'')"
    )
    return _one(expr, _check_from(doc))


def item_pick_sql(doc: str) -> str:
    """检验单的第一个检验项目：检验项目编码|检验指标编码（只收安全字符，否则没有行）。"""
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(s.CCHKITEMCODE)) + N'|' + LTRIM(RTRIM(s.CCHKGUIDECODE)) AS v
FROM dbo.QMCHECKVOUCHERS AS s
WHERE s.ID = {safe_id(doc)}
  AND LTRIM(RTRIM(s.CCHKITEMCODE)) NOT LIKE N'%[^0-9A-Za-z._-]%'
  AND LTRIM(RTRIM(s.CCHKGUIDECODE)) NOT LIKE N'%[^0-9A-Za-z._-]%'
ORDER BY s.AUTOID;
"""


def item_value_sql(doc: str) -> str:
    """检验单第一个检验项目的检验值|单项判定。"""
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + ISNULL(LTRIM(RTRIM(s.CCHECKVALUE)), N'') + N'|' + ISNULL(LTRIM(RTRIM(s.CTARGETQJUG)), N'') AS v
FROM dbo.QMCHECKVOUCHERS AS s
WHERE s.ID = {safe_id(doc)}
ORDER BY s.AUTOID;
"""


def yielder_pick_sql() -> str:
    """让步接收核准人（CYIELDERCODE，人员编码）：照抄最近一张有让步数量的检验单的核准人（仍在职）；没有时取编码最小的在职人员。
    实测产品检验单有让步数量时缺了报「'让步接收核准人'不能为空」。模板必输的自定义项另由模板决定，与核准人无关。"""
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + x.v AS v
FROM (
  SELECT 0 AS o, a.v FROM (
    SELECT TOP (1) LTRIM(RTRIM(q.CYIELDERCODE)) AS v
    FROM dbo.QMCHECKVOUCHER AS q
    JOIN dbo.Person AS s ON s.cPersonCode = q.CYIELDERCODE
    WHERE q.CVOUCHTYPE IN (N'QM03', N'QM04') AND ISNULL(q.FCONQUANTIY, 0) > 0
      AND (s.dPInValidDate IS NULL OR s.dPInValidDate > GETDATE())
      AND LTRIM(RTRIM(q.CYIELDERCODE)) NOT LIKE N'%[^0-9A-Za-z._-]%' AND LEN(q.CYIELDERCODE) <= 12
    ORDER BY q.ID DESC
  ) AS a
  UNION ALL
  SELECT 1 AS o, b.v FROM (
    SELECT TOP (1) LTRIM(RTRIM(s.cPersonCode)) AS v
    FROM dbo.Person AS s
    WHERE (s.dPInValidDate IS NULL OR s.dPInValidDate > GETDATE())
      AND LTRIM(RTRIM(s.cPersonCode)) NOT LIKE N'%[^0-9A-Za-z._-]%' AND LEN(s.cPersonCode) <= 12
    ORDER BY s.cPersonCode
  ) AS b
) AS x
ORDER BY x.o;
"""


def incoming_flow_sql() -> str:
    """一行：QM03（账套发布了来料检验单的提交审批流）；没有时没有行，提交审批的用例记 N/A。口径同桥的审批流判断。
    没有审批流表（Table_WorkFlowRelease / AuditBizObjects）的账套也没有行，不报错。"""
    return """SET NOCOUNT ON;
IF OBJECT_ID(N'dbo.Table_WorkFlowRelease') IS NOT NULL AND OBJECT_ID(N'dbo.AuditBizObjects') IS NOT NULL
  SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(r.cBizObjectId)) AS v
  FROM dbo.Table_WorkFlowRelease AS r
  JOIN dbo.AuditBizObjects AS o ON o.cBizObjectId = r.cBizObjectId
  WHERE r.Status = 0 AND o.srcTable = N'QMCHECKVOUCHER' AND r.cBizObjectId = N'QM03'
    AND r.cBizEventId = o.cBizObjectId + N'.Submit';
"""


# 挑单登记（同 returns_misc_sql.CUSTOM_PACKS 的形状）：前导键、按 | 拆开的键、是否必须是正整数 id、SQL。
QMU_PACKS = (
    ("qmu_rsn", ("qmu_rsn",), (False,), reason_pick_sql),
    ("qmu_ylr", ("qmu_ylr",), (False,), yielder_pick_sql),
    ("qmu_wf3", ("qmu_wf3",), (False,), incoming_flow_sql),
)


def _res(doc: str, _bag: dict[str, str]) -> str:
    return check_result_sql(doc)


def _ufts(doc: str, _bag: dict[str, str]) -> str:
    return check_ufts_sql(doc)


def _mod(doc: str, _bag: dict[str, str]) -> str:
    return check_modifier_sql(doc)


_READBACK = {
    "qmu_q3_res": _res,
    "qmu_q3_ufts": _ufts,
    "qmu_q3_mod": _mod,
    "qmu_q4_res": _res,
    "qmu_q4_ufts": _ufts,
    "qmu_q4_mod": _mod,
}


def has_qmu(name: str) -> bool:
    return name in _READBACK


def qmu_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def qmu_samples() -> list[tuple[str, str]]:
    rows = [(name.replace("_", "-"), fn("100", {})) for name, fn in _READBACK.items()]
    rows += [("qmu-oth-inspect", inspect_state_sql("100")), ("qmu-oth-check", check_state_sql("100"))]
    rows += [("qmu-item-pick", item_pick_sql("100")), ("qmu-item-value", item_value_sql("100"))]
    rows += [("qmu-ylr", yielder_pick_sql()), ("qmu-wf3", incoming_flow_sql())]
    return rows
