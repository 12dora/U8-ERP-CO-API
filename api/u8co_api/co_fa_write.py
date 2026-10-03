"""固定资产写入在 API 层的规则：与桥同文的拒绝和共用的格式检查。
卡片新增 / 撤销本期新增（archives，fa_card）和设备台账新增（archives，equipment）的 fields 检查见 co_fa_card_write。
变动单、资产减少不在 API 里（U8 的 EAI 没有变动单导入样式表），请在 U8 客户端录入。
期间、权限、卡片状态等闸门都在桥里，这里只做格式检查。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from u8co_api.errors import ApiError

FA_CARD = "fa_card"
EQUIPMENT = "equipment"
# 与桥同文的拒绝。
FA_CARD_NO_UPDATE = (
    "固定资产卡片不能直接修改：原值、使用状况等的变化请在 U8 客户端录入变动单（U8 的 EAI 没有变动单导入样式表）"
)
EQ_NO_UPDATE = "设备台账的 U8 官方导入（EAI eqdata）只支持新增，修改请在 U8 客户端处理"
EQ_NO_DELETE = "档案 equipment 不支持删除，请在 U8 客户端处理"
_REFUSED = {"update": {FA_CARD: FA_CARD_NO_UPDATE, EQUIPMENT: EQ_NO_UPDATE}, "delete": {EQUIPMENT: EQ_NO_DELETE}}
AMOUNT_MAX = Decimal("1000000000000")


def bad(message: str, field: str) -> ApiError:
    return ApiError(400, "bad_request", message, field=field)


def refuse_archive_op(data: object, op: str) -> object:
    """修改、删除请求的档案不支持该操作时直接 400，给出与桥相同的说明，而不是笼统的「请求参数无效：archive」。"""
    archive = data.get("archive") if isinstance(data, dict) else None
    text = _REFUSED.get(op, {}).get(archive) if isinstance(archive, str) else None
    if text is not None:
        raise bad(text, "archive")
    return data


def number(value: object) -> Decimal | None:
    """JSON 数字或数字字符串；布尔、空串、解析不了或非有限值返回 None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def whole(value: object) -> int | None:
    """整数或整数字符串（不收布尔、小数）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def text_ok(value: object, limit: int) -> bool:
    return isinstance(value, str) and value.strip() != "" and len(value) <= limit
