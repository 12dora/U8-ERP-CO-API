"""OpenAPI 说明文字的排版：首句说明用途，后接若干「粗体标签 + 列表」小节（接口参考页按 CommonMark 渲染）。

小节按 用法 / 规则 / 限制 / 权限 / 错误 / 相关 的顺序给出，空小节不输出；标签为空时只输出列表。
"""

from __future__ import annotations

from collections.abc import Sequence

Section = tuple[str, Sequence[str]]

# 分页报表共用的一条用法。
PAGE = "按 next 翻页：原样放进 after 读下一页，最后一页 next 为 null"

# 只对测试账套开放的写入（第二级写入）的开关：「限制」小节里的两条（Markdown 列表项，每条以换行结尾）。
TEST_ONLY_GATE = (
    "- 默认关闭：桥未开 enableReplicatedWrites 时 403 feature_disabled\n"
    "- 打开后只对桥配置的测试账套（testAccounts）开放，其他账套 403 test_account_only，不登录 U8\n"
)


def section(label: str, items: Sequence[str]) -> str:
    """一个小节：粗体标签加列表；标签为空时只有列表，列表为空时返回空串。"""
    if not items:
        return ""
    head = f"**{label}**\n" if label else ""
    return head + "\n".join(f"- {item}" for item in items)


def field_doc(lead: str, *sections: Section) -> str:
    """字段说明：首句加小节，不带结尾空行。"""
    parts = [lead, *(section(label, items) for label, items in sections)]
    return "\n\n".join(part for part in parts if part)


def op_doc(lead: str, *sections: Section) -> str:
    """接口说明：格式同字段说明；注册时另起一段追加权限说明（co_routes）。"""
    return field_doc(lead, *sections)
