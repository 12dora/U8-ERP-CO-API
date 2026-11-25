"""快照窗口里的改派和回读。标识符只来自这里的常量表，id 只接受正整数。"""

from __future__ import annotations

import re

_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_OP = re.compile(r"^[A-Za-z0-9_-]{1,20}\Z")

_READ = {
    "sale": ("dbo.SO_SOMain", "ID", "cVerifier", "dverifydate"),
    "dispatch": ("dbo.DispatchList", "DLID", "cVerifier", "dverifydate"),
    "rd09": ("dbo.RdRecord09", "ID", "cHandler", "dVeriDate"),
    "rd08": ("dbo.RdRecord08", "ID", "cHandler", "dVeriDate"),
    "po": ("dbo.PO_Pomain", "POID", "cVerifier", "cAuditDate"),
    "arrival": ("dbo.PU_ArrivalVouch", "ID", "cverifier", "cAuditDate"),
}

_CODE = {
    "rd08": ("dbo.RdRecord08", "ID", "cCode"),
    "rd09": ("dbo.RdRecord09", "ID", "cCode"),
    "sale": ("dbo.SO_SOMain", "ID", "cSOCode"),
    "po": ("dbo.PO_Pomain", "POID", "cPOID"),
}


def _doc(doc_id: str) -> str:
    if _ID.fullmatch(doc_id) is None:
        raise ValueError("单据 id 不合法")
    return doc_id


def _oper(operator: str) -> str:
    if _OP.fullmatch(operator) is None:
        raise ValueError("操作员编码不合法")
    return operator


def shape_qm04(doc_id: str, operator: str) -> str:
    # 恰好改派当前节点 cUserID 最小的一条待办到测试操作员对应的人员。
    safe_id = _doc(doc_id)
    safe_op = _oper(operator)
    return f"""SET NOCOUNT ON;
DECLARE @id int = {safe_id};
DECLARE @operator nvarchar(20) = N'{safe_op}';
DECLARE @person nvarchar(40);
SELECT TOP 1 @person = cPsn_Num
FROM dbo.UserHrPersonContro
WHERE cUser_Id = @operator;
UPDATE dbo.Table_Task
SET cUserID = @person
WHERE @person IS NOT NULL
  AND cVoucherID = @id
  AND RTRIM(cVoucherType) = N'QM04'
  AND cTK_State = 0
  AND cTaskType IN (1, 4)
  AND cUserID = (
    SELECT TOP 1 cUserID
    FROM dbo.Table_Task
    WHERE cVoucherID = @id
      AND RTRIM(cVoucherType) = N'QM04'
      AND cTK_State = 0
      AND cTaskType IN (1, 4)
    ORDER BY cUserID
  );
SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;
"""


def pending_qm04(doc_id: str) -> str:
    safe_id = _doc(doc_id)
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(12), COUNT(*)) AS v
FROM dbo.Table_Task
WHERE cVoucherID = N'{safe_id}'
  AND RTRIM(cVoucherType) = N'QM04'
  AND cTK_State = 0
  AND cTaskType IN (1, 4);
"""


def running_qm04(doc_id: str) -> str:
    safe_id = _doc(doc_id)
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(12), COUNT(*)) AS v
FROM dbo.WF_ActiveFlow
WHERE VoucherId = N'{safe_id}'
  AND RTRIM(cBizObjectId) = N'QM04'
  AND FlagCode = 0;
"""


def read_sql(name: str, doc_id: str) -> str:
    table, col, left, right = _READ[name]
    safe_id = _doc(doc_id)
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + ISNULL(LTRIM(RTRIM(" + left + ")), N'') + N'|' +"
        " ISNULL(CONVERT(varchar(19), " + right + ", 120), N'') AS v\n"
        f"FROM {table} WHERE {col} = {safe_id};\n"
    )


def code_sql(name: str, doc_id: str) -> str:
    table, col, code = _CODE[name]
    safe_id = _doc(doc_id)
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + ISNULL(LTRIM(RTRIM(" + code + ")), N'') AS v\n"
        f"FROM {table} WHERE {col} = {safe_id};\n"
    )


def named_sql(name: str, doc_id: str, operator: str) -> str:
    if name == "shape":
        return shape_qm04(doc_id, operator)
    if name == "pending":
        return pending_qm04(doc_id)
    if name == "running":
        return running_qm04(doc_id)
    raise ValueError("未知 SQL")
