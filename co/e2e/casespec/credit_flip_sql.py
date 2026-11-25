"""客户信用「信用余额控制用余额表」选项用例（credit_flip）的 SQL：选项 AccInformation SA / bUseBanlaceTable 的读和临时改写。

写语句只改这一行：带原值作条件，回 @@ROWCOUNT 供核对；拼进去的只有校验过的短字母数字串。
credit_flip_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

import re

_VALUE = re.compile(r"^[A-Za-z0-9]{0,10}\Z")
_OPTION = "cSysID = N'SA' AND cName = N'bUseBanlaceTable'"
_TEXT = "ISNULL(LTRIM(RTRIM(CONVERT(nvarchar(20), cValue))), N'')"


def _value(text: str) -> str:
    if type(text) is not str or _VALUE.fullmatch(text) is None:
        raise ValueError("选项值只能是 10 个以内的字母数字")
    return text


def option_sql() -> str:
    """一行：<选项行数>|<选项值（去空格）>。"""
    return (
        "SET NOCOUNT ON;\n"
        f"SELECT N'ROW:' + CONVERT(varchar(4), COUNT(*)) + N'|' + ISNULL(MAX({_TEXT}), N'') AS v\n"
        f"FROM dbo.AccInformation WHERE {_OPTION};\n"
    )


def option_set_sql(new: str, old: str) -> str:
    """把选项从 old 改成 new（只改值等于 old 的那一行）；一行：<改动行数>。"""
    return (
        "SET NOCOUNT ON;\n"
        f"UPDATE dbo.AccInformation SET cValue = N'{_value(new)}' WHERE {_OPTION} AND {_TEXT} = N'{_value(old)}';\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def credit_flip_samples() -> list[tuple[str, str]]:
    return [
        ("credit-option", option_sql()),
        ("credit-option-set", option_set_sql("False", "True")),
    ]
