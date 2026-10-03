"""u8co 错误。code 对得上就用子类，对不上就保留原来的 code。"""

from __future__ import annotations

import json
from typing import Any

# 成功响应可以很大（列表、整张单据），错误响应只该有 code 和 message。
_RESPONSE_LIMIT = 8 * 1024 * 1024
_ERROR_LIMIT = 65536
_FIELD_LIMIT = 200
_HINT_LIMIT = 300


class U8CoError(Exception):
    # field 是出错字段的请求体路径（如 lines.0.cinvcode），hint 是桥给的修正提示；桥没给时为空串。
    # detail 是桥给的结构化补充（只在 4xx，如存货核算记账拒绝时的 uncosted、uncosted_total），由 error_from 赋值；
    # 没有时为 None。
    detail: dict | None = None

    def __init__(self, status: int, code: str, message: str, field: str = "", hint: str = "") -> None:
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message
        self.field = field
        self.hint = hint

    def describe(self) -> str:
        """命令行输出：code: message，有字段、提示时附上。"""
        text = f"{self.code}: {self.message}"
        if self.field:
            text += f"（字段 {self.field}）"
        if self.hint:
            text += f"\n提示：{self.hint}"
        if self.detail:
            text += "\n详情：" + json.dumps(self.detail, ensure_ascii=False)
        return text


class U8CoBadRequest(U8CoError):
    pass


class U8CoUnauthorized(U8CoError):
    pass


class U8CoAccountNotAllowed(U8CoError):
    pass


class U8CoTestAccountOnly(U8CoAccountNotAllowed):
    """403 test_account_only：只对桥 testAccounts 里的测试账套开放的第二级写入（月末结账等）。"""


class U8CoFeatureDisabled(U8CoError):
    """403 feature_disabled：桥没打开第二级写入总开关 enableReplicatedWrites（缺省关闭），与账套无关。"""


class U8CoNotFound(U8CoError):
    pass


class U8CoStateMismatch(U8CoError):
    pass


class U8CoRejected(U8CoError):
    pass


class U8CoWorkflowEnabled(U8CoError):
    pass


class U8CoLoginFailed(U8CoError):
    pass


class U8CoBusy(U8CoError):
    pass


class U8CoInternal(U8CoError):
    pass


class U8CoComUnavailable(U8CoError):
    pass


class U8CoTransport(U8CoError):
    pass


class U8CoConnectFailed(U8CoTransport):
    pass


class U8CoOutcomeUnknown(U8CoError):
    pass


class U8CoBusyTimeout(U8CoError):
    pass


class U8CoStopping(U8CoError):
    pass


class U8CoLicenseFull(U8CoError):
    """U8 许可点数已满（503 u8_license_full）。桥已自己重试过，隔一分钟左右再试。"""


class U8CoIaTimeout(U8CoError):
    """存货核算脚本超时（503 ia_timeout）：桥已回滚，没有写入；稍后重试，或调大桥的 iaCommandSeconds。"""


class U8CoWorkflowUnknown(U8CoError):
    pass


class U8CoWorkflowDisabled(U8CoError):
    pass


class U8CoNotSubmitted(U8CoError):
    pass


class U8CoAlreadySubmitted(U8CoError):
    pass


class U8CoNotCurrentApprover(U8CoError):
    pass


class U8CoStockShortage(U8CoError):
    pass


_BY_CODE: dict[str, type[U8CoError]] = {
    "bad_request": U8CoBadRequest,
    "unauthorized": U8CoUnauthorized,
    "account_not_allowed": U8CoAccountNotAllowed,
    "test_account_only": U8CoTestAccountOnly,
    "feature_disabled": U8CoFeatureDisabled,
    "not_found": U8CoNotFound,
    "state_mismatch": U8CoStateMismatch,
    "u8_rejected": U8CoRejected,
    "workflow_enabled": U8CoWorkflowEnabled,
    "login_failed": U8CoLoginFailed,
    "busy": U8CoBusy,
    "busy_timeout": U8CoBusyTimeout,
    "stopping": U8CoStopping,
    "u8_license_full": U8CoLicenseFull,
    "ia_timeout": U8CoIaTimeout,
    "outcome_unknown": U8CoOutcomeUnknown,
    "workflow_unknown": U8CoWorkflowUnknown,
    "workflow_disabled": U8CoWorkflowDisabled,
    "not_submitted": U8CoNotSubmitted,
    "already_submitted": U8CoAlreadySubmitted,
    "not_current_approver": U8CoNotCurrentApprover,
    "stock_shortage": U8CoStockShortage,
    "internal": U8CoInternal,
    "com_unavailable": U8CoComUnavailable,
}

_BY_STATUS = {
    400: "bad_request",
    401: "unauthorized",
    403: "account_not_allowed",
    404: "not_found",
    409: "u8_rejected",
    422: "login_failed",
    429: "busy",
    500: "internal",
    503: "com_unavailable",
    504: "outcome_unknown",
}


def error_from(status: int, data: dict[str, Any]) -> U8CoError:
    code = data.get("code")
    if not isinstance(code, str) or code == "":
        code = _BY_STATUS.get(status, "internal")
    message = data.get("message")
    if not isinstance(message, str):
        message = ""
    field, hint = _text(data, "field", _FIELD_LIMIT), _text(data, "hint", _HINT_LIMIT)
    detail = data.get("detail")
    if not isinstance(detail, dict) or not detail or not 400 <= status < 500:
        detail = None
    error = _BY_CODE.get(code, U8CoError)(status, code, message, field, hint)
    error.detail = detail
    return error


def _text(data: dict[str, Any], key: str, limit: int) -> str:
    # 桥的 field / hint 只在已知时出现；不是字符串或超长就当没有。
    value = data.get(key)
    if not isinstance(value, str) or len(value) > limit:
        return ""
    return value


def parse_payload(status: int, raw: bytes) -> dict[str, Any]:
    # 重定向不跟随：签名绑在原来的 path 上。
    if 300 <= status < 400:
        raise U8CoTransport(status, "bad_response", "u8co 不应重定向")
    if len(raw) > _RESPONSE_LIMIT:
        raise U8CoTransport(status, "bad_response", "响应超过 8 MiB")
    if status >= 400 and len(raw) > _ERROR_LIMIT:
        raise U8CoTransport(status, "bad_response", "错误响应超过 64 KiB")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise U8CoTransport(status, "bad_response", "响应不是 UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise U8CoTransport(status, "bad_response", "响应 JSON 不是对象")
    if status >= 400 or payload.get("ok") is not True:
        raise error_from(status, payload)
    return payload
