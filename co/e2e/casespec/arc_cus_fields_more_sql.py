"""客户字段 `archive-customer-fields` 扩展键的 SQL：所属行业、自定义项 1 的账套设置与档案值。

标识符是常量；拼进 SQL 的编码先过白名单（safe_code），档案值转义成 N'' 字面量（lit）。
中文表值（客户）用 NCHAR 码点写，不依赖 sqlcmd 的代码页。SQL 文本里不出现 sqlguard 拒绝的词。
"""

from __future__ import annotations

import re

from co.e2e.casespec.gl_voucher_sql import safe_code

_ALNUM = "NOT LIKE N'%[^0-9A-Za-z]%'"
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_ID = re.compile(r"^[0-9]{1,9}\Z")
# 「客户」：自定义项设置表 UserDef_Base.cClass 的取值。
_CLASS_CUSTOMER = "NCHAR(23458) + NCHAR(25143)"
DEFINE_COLUMN = "cCusDefine1"
DEFINE_SEP = "|"


def _lit(text: str) -> str:
    if _CTRL.search(text or ""):
        raise ValueError("值含控制字符")
    return "N'" + (text or "").replace("'", "''") + "'"


def trade_pick_sql(skip: str) -> str:
    """末级所属行业里挑一个编码（不是 skip）。"""
    try:
        other = (
            f"\n  AND LTRIM(RTRIM(t.cTradeCCode)) <> N'{safe_code(skip)}'"
            if skip
            else ""
        )
    except ValueError:
        other = ""
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(t.cTradeCCode)) AS v
FROM dbo.TradeClass AS t
WHERE t.bTradeCEnd = 1 AND LTRIM(RTRIM(t.cTradeCCode)) {_ALNUM}
  AND LTRIM(RTRIM(ISNULL(t.cTradeCName, N''))) <> N''{other}
ORDER BY t.cTradeCCode;
"""


def trade_name_sql(code: str) -> str:
    """行业编码对应的行业名称（末级）：写所属行业时桥同时写这个名称。"""
    safe = safe_code(code)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(ISNULL(t.cTradeCName, N''))) AS v
FROM dbo.TradeClass AS t
WHERE t.cTradeCCode = N'{safe}' AND t.bTradeCEnd = 1;
"""


def define_setting_sql() -> str:
    """客户自定义项 1 的账套设置：要求合法性检查 | 取自其他档案 | 必输 | 使用长度 | 定长 | 最大长度 | 自定义项编号。

    取自其他档案：iDataSource=1 或设置了关联档案（cRelArchive）；自动建档（bArchive）不影响路由校验，不取。
    """
    sep = f"N'{DEFINE_SEP}'"
    related = "CASE WHEN ISNULL(b.iDataSource, 0) = 1 OR ISNULL(b.cRelArchive, N'') <> N'' THEN 1 ELSE 0 END"
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(nvarchar(20), ISNULL(b.bCheckVal, 0)) + {sep}
  + CONVERT(nvarchar(20), {related}) + {sep} + CONVERT(nvarchar(20), ISNULL(b.bInput, 0)) + {sep}
  + CONVERT(nvarchar(20), ISNULL(b.iLength, 0)) + {sep}
  + CONVERT(nvarchar(20), ISNULL(b.bFixLength, 0)) + {sep} + CONVERT(nvarchar(20), ISNULL(b.iMaxLen, 0)) + {sep}
  + CONVERT(nvarchar(20), b.cID) AS v
FROM dbo.UserDef_Base AS b
WHERE b.cClass = {_CLASS_CUSTOMER} AND b.cDicDbName = N'{DEFINE_COLUMN}';
"""


def define_value_sql(define_id: str, skip: str) -> str:
    """自定义项档案里挑一个值（不是 skip）。"""
    if _ID.fullmatch(define_id or "") is None:
        raise ValueError("自定义项编号不合法")
    other = f"\n  AND u.cValue <> {_lit(skip)}" if skip else ""
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(u.cValue)) AS v
FROM dbo.UserDefine AS u
WHERE u.cID = N'{define_id}' AND LEN(LTRIM(RTRIM(u.cValue))) BETWEEN 1 AND 20
  AND u.cValue = LTRIM(RTRIM(u.cValue)){other}
ORDER BY u.cValue;
"""


def more_samples() -> list[tuple[str, str]]:
    return [
        ("core-cus-trade-pick", trade_pick_sql("01")),
        ("core-cus-trade-pick-blank", trade_pick_sql("")),
        ("core-cus-trade-name", trade_name_sql("01")),
        ("core-cus-define-setting", define_setting_sql()),
        ("core-cus-define-value", define_value_sql("11", "文本A")),
    ]
