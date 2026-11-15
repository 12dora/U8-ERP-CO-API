"""固定资产写入（客户端）：卡片新增、撤销本期新增（archives，fa_card）和设备台账（archives，equipment）。
细的校验（必填字段、金额范围、期间闸门）在 API 和桥；变动单、资产减少（处置）不在 API 里
（U8 的 EAI 没有变动单导入样式表），请在 U8 客户端录入。"""

from __future__ import annotations

# 设备台账（EQ_EQData）：get、list（支持 changed_since）、新增（code 是设备编码，最长 30）；不能修改、删除。
EQ_ARCHIVE = "equipment"
EQ_CODE_LIMIT = 30
# 可新增的固定资产档案；fa_card 另可撤销本期新增（code 是卡片编号），新增的 code 是资产编号。
FA_WRITE_ARCHIVES = ("fa_card", EQ_ARCHIVE)
FA_DELETE_ARCHIVES = ("fa_card",)
# 不能修改的说明（与桥同文）。
NO_UPDATE_TEXT = {
    "fa_card": "固定资产卡片不能直接修改：原值、使用状况等的变化请在 U8 客户端录入变动单（U8 的 EAI 没有变动单导入样式表）",
    EQ_ARCHIVE: "设备台账的 U8 官方导入（EAI eqdata）只支持新增，修改请在 U8 客户端处理",
}
# 不收 template 的固定资产档案。
NO_TEMPLATE_ARCHIVES = FA_WRITE_ARCHIVES
