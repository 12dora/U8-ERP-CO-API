"""只读档案 fa_card（固定资产卡片）的列表条件。以桥为准（ArcFa.ParseList）：只给 fa_card，其他档案 400。"""

from __future__ import annotations

from pydantic import BaseModel, Field, StrictBool

FA_CARD = "fa_card"
# 卡片编号 fa_Cards.sCardNum 最长 20；类别编码按 20、部门编码按 12 个字符（桥 ArcFa.TypeMax / DeptMax）。
FA_CODE_MAX = 20
_CTRL = "\\x00-\\x1f\\x7f\\uFFFE\\uFFFF"
_TRIMMED = rf"^[^\s{_CTRL}](?:[^{_CTRL}]*[^\s{_CTRL}])?$"
FA_RO = (
    "`fa_card` 固定资产卡片：编码是卡片编号，按登录日期所在月的月末取卡片当时的版本和累计折旧",
    "`fa_card` 列表缺省不含已减少的卡片，可按 type_code、dept_code 过滤",
)


class FaListFilters(BaseModel):
    """混入 ArcListIn 的三个条件；省略时不发给桥。"""

    type_code: str | None = Field(
        None,
        pattern=_TRIMMED,
        max_length=20,
        description="只给 fa_card：资产类别编码，含下级类别（按编码前缀匹配）",
    )
    dept_code: str | None = Field(
        None,
        pattern=_TRIMMED,
        max_length=12,
        description="只给 fa_card：使用部门编码，多部门卡片任一部门匹配即可",
    )
    include_disposed: StrictBool | None = Field(
        None, description="只给 fa_card：true 时也列出已减少的卡片。省略或 false 只列在役卡片"
    )


def check_fa_filters(archive: str, values: tuple[object, ...]) -> None:
    """type_code、dept_code、include_disposed 只给 fa_card。"""
    if archive != FA_CARD and any(value is not None for value in values):
        raise ValueError("只有 fa_card 支持 type_code、dept_code、include_disposed")
