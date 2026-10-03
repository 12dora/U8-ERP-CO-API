"""把 CO 桥的失败映射成 ApiError。消息里不带任何密钥。"""

from __future__ import annotations

import json
import re

from u8co_api.errors import (
    BRIDGE_NOT_FOUND_HINT,
    ApiError,
    bad_gateway,
    conflict,
    timeout_error,
    unavailable,
    unprocessable,
)

_STATUS_BY_CODE = {
    "bad_request": 400,
    "account_not_allowed": 403,
    "test_account_only": 403,
    "feature_disabled": 403,  # 桥的第二级写入总开关 enableReplicatedWrites 未打开
    "not_found": 404,
    "state_mismatch": 409,
    "u8_rejected": 409,
    "workflow_enabled": 409,
    "workflow_unknown": 409,
    "workflow_disabled": 409,
    "already_submitted": 409,
    "not_submitted": 409,
    "not_current_approver": 409,
    "stock_shortage": 409,
    "login_failed": 422,
    "busy": 429,
    "internal": 502,
    "bad_response": 502,
    "busy_timeout": 503,
    "stopping": 503,
    "com_unavailable": 503,
    "u8_unavailable": 503,
    "u8_license_full": 503,
    "ia_timeout": 503,  # 存货核算脚本超时，已回滚
    "outcome_unknown": 504,
    # 写入策略：都在登录和执行之前拒绝
    "write_policy_unavailable": 503,
    "write_frozen": 503,
    "write_window": 503,
    "write_not_allowed": 403,
    "operator_not_allowed": 403,
    "write_limit": 400,
    "write_quota": 429,
    "u8_license_hold": 503,
}

# 可以稍后重试的码，值写进 Retry-After（秒）。许可点数已满时桥自己已重试过，等久一点。
_RETRY_AFTER = {
    "u8_license_full": 60,
    "ia_timeout": 60,
    "u8_license_hold": 30,
    "write_quota": 60,
    # 冻结、时段之外要等运维改策略或到点，等久一点；策略不可用多半是文件正在替换。
    "write_frozen": 300,
    "write_window": 300,
    "write_policy_unavailable": 30,
    "busy": 5,
    "busy_timeout": 5,
    "stopping": 5,
}

_BY_STATUS = {
    400: "bad_request",
    403: "account_not_allowed",
    404: "not_found",
    409: "u8_rejected",
    422: "login_failed",
    429: "busy",
    500: "internal",
    502: "bad_response",
    503: "com_unavailable",
    504: "outcome_unknown",
}


# 桥体里的 field / hint 只在合法时透传，否则静默丢掉。
_FIELD = re.compile(r"[A-Za-z0-9_.\-]{1,200}")
_HINT_MAX = 300
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# 桥体里的 detail（对象）只给 4xx（401 除外）透传，序列化成 UTF-8 后超过这个字节数（32 KiB）就丢掉。
_DETAIL_MAX = 32768
# 桥的 message 透传前去掉控制字符、截到 300 字符；拒绝类（403）和冲突类（409）还把一串编码
# （5 个及以上用逗号、顿号、分号隔开的编码）折成「首项 等 N 项」：不把操作员看不到的客户、科目、单号清单原样回给调用方。
_MESSAGE_MAX = 300
_CODE_LIST = re.compile(r"[A-Za-z0-9_.\-]{1,40}(?:\s*[,，、;；]\s*[A-Za-z0-9_.\-]{1,40}){4,}")
_LIST_SEP = re.compile(r"\s*[,，、;；]\s*")
# 消息里折叠编码清单（5 项以上）的状态：拒绝（403）和冲突（409，如「被以下单据引用」的单号清单）。
_FOLD_STATUSES = frozenset((403, 409))


class _AfterSend(Exception):
    """The request bytes already left this process."""


def to_api_error(status: int, body: dict | None) -> ApiError:
    if status == 401:
        return unavailable("CO 桥认证失败")
    if not isinstance(body, dict):
        return _unparsed(status)
    code = _code(body, status)
    mapped = _status_for(code, status)
    error = _make(mapped, code, _message(body, mapped))
    error.retry_after = _retry_after(code, body)
    if error.status == 400:
        error.field = _field(body)
    if status not in (401, 500) and error.status not in (401, 500):
        error.hint = _hint(body)
    if error.hint is None and code == "not_found":
        error.hint = BRIDGE_NOT_FOUND_HINT
    if 400 <= error.status < 500 and error.status != 401:
        error.detail = _detail(body, error.status)
    return error


def transport_error(exc: BaseException) -> ApiError:
    if isinstance(exc, _AfterSend):
        return timeout_error("已送出请求但没有收到结果", "outcome_unknown")
    return unavailable("连不上 CO 桥")


def _unparsed(status: int) -> ApiError:
    # 2xx 解析不了，写操作可能已经落地。其它状态的乱响应则还没承诺成功。
    if 200 <= status < 300:
        return timeout_error("CO 桥响应无法解析", "outcome_unknown")
    return bad_gateway("CO 桥响应无法解析", "bad_response")


def _code(body: dict, status: int) -> str:
    code = body.get("code")
    if isinstance(code, str) and code:
        return code
    return _BY_STATUS.get(status, "internal")


def _message(body: dict, status: int = 400) -> str:
    message = body.get("message")
    if isinstance(message, str) and message:
        return clip_message(message, fold=status in _FOLD_STATUSES)
    return "CO 桥返回错误"


def clip_message(message: str, fold: bool = True) -> str:
    """桥错误消息的外传形式：无控制字符、（fold 时）编码清单折叠、最多 300 字符（超出以「…」结尾）。"""
    text = _CONTROL.sub(" ", message).strip()
    if fold:
        text = _CODE_LIST.sub(_fold_codes, text)
    if len(text) > _MESSAGE_MAX:
        text = text[: _MESSAGE_MAX - 1] + "…"
    return text or "CO 桥返回错误"


def _fold_codes(found: re.Match) -> str:
    items = _LIST_SEP.split(found.group(0))
    return f"{items[0]} 等 {len(items)} 项"


def _field(body: dict) -> str | None:
    field = body.get("field")
    if isinstance(field, str) and _FIELD.fullmatch(field):
        return field
    return None


def _hint(body: dict) -> str | None:
    hint = body.get("hint")
    if not isinstance(hint, str):
        return None
    hint = hint.strip()
    if not hint or len(hint) > _HINT_MAX or _CONTROL.search(hint):
        return None
    return hint


def _detail(body: dict, status: int = 400) -> dict | None:
    detail = body.get("detail")
    if not isinstance(detail, dict) or not detail:
        return None
    if status == 403:
        # 拒绝类（403）只透传标量（如写入策略的 type、op），不透传列表、对象，免得带出编码清单。
        detail = {key: _safe(value) for key, value in detail.items() if _scalar(value)}
        if not detail:
            return None
    try:
        text = json.dumps(detail, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return None
    if len(text.encode("utf-8")) > _DETAIL_MAX:
        return None
    return detail


def _scalar(value: object) -> bool:
    return value is None or isinstance(value, (str, bool, int, float))


def _safe(value: object) -> object:
    return clip_message(value) if isinstance(value, str) else value


def _retry_after(code: str, body: dict) -> int | None:
    # 写入限额由桥按窗口算出等待秒数，放在 detail.retry_after_seconds；读不到时用表里的缺省。
    fallback = _RETRY_AFTER.get(code)
    if code != "write_quota":
        return fallback
    detail = body.get("detail")
    wait = detail.get("retry_after_seconds") if isinstance(detail, dict) else None
    if isinstance(wait, int) and not isinstance(wait, bool) and 1 <= wait <= 86400:
        return wait
    return fallback


def default_retry_after(code: str) -> int | None:
    """错误码的缺省 Retry-After 秒数（本服务自己拒绝时也用，如写入策略的 write_frozen）；不可重试的码为 None。"""
    return _RETRY_AFTER.get(code)


def _status_for(code: str, status: int) -> int:
    mapped = _STATUS_BY_CODE.get(code)
    if mapped is not None:
        return mapped
    if code.startswith("workflow_"):
        return 409
    if 400 <= status <= 599:
        return status
    return 502


def _make(status: int, code: str, message: str) -> ApiError:
    if status == 409:
        return conflict(message, code)
    if status == 422:
        return unprocessable(message, code)
    if status == 502:
        return bad_gateway(message, code)
    if status == 503:
        return unavailable(message, code)
    if status == 504:
        return timeout_error(message, code)
    return ApiError(status, code, message)
