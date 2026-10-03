"""错误响应体：{"error": {"code", "message", "retryable", "field"?, "hint"?, "detail"?}}。"""

from __future__ import annotations

# 可以原样稍后重试的错误码（请求没有执行，或执行前就被拒绝）。其余一律 retryable=false，
# outcome_unknown 尤其不能直接重试：写入可能已生效。
RETRYABLE_CODES = frozenset(
    {
        "busy",
        "busy_timeout",
        "stopping",
        "u8_license_full",
        "rate_limited",
        "unavailable",
        "store_unavailable",
        "ia_timeout",  # 存货核算脚本超时：提交之前回滚，没有写入
        # 写入策略：都在登录和执行之前拒绝，没有写入
        "write_policy_unavailable",
        "write_frozen",
        "write_window",
        "write_quota",
        "u8_license_hold",
    }
)

_WAIT = "稍后重试（见 Retry-After）"
_NO_PERMISSION = "当前操作员（令牌）没有这项权限"

# 没有更具体的提示时按错误码给的默认提示。401 和 500 不给提示。
DEFAULT_HINTS = {
    "busy": _WAIT,
    "busy_timeout": _WAIT,
    "stopping": _WAIT,
    "u8_license_full": "U8 许可点数已满，60 秒后重试",
    "ia_timeout": "已回滚，没有写入：稍后重试，或让管理员调大桥的 iaCommandSeconds",
    "write_quota": "账套写入限额已满，按 Retry-After 重试",
    "u8_license_hold": "接口占用的 U8 登录已达上限，稍后重试",
    "write_policy_unavailable": "写入策略文件缺失或无效，所有写入暂停：请管理员检查策略文件",
    "write_frozen": "写入已被管理员冻结，解冻后再试",
    "write_window": "不在允许写入的时段：到允许的时段再试",
    "write_not_allowed": "写入策略没有放行这个账套的这类写入（见 detail 的 type、op），请管理员调整策略",
    "operator_not_allowed": "写入策略不允许这个操作员在此账套写入，换一个操作员或请管理员调整策略",
    "write_limit": "行数或金额超过写入策略的上限（见 detail 的 max、actual），拆成几笔再写",
    "rate_limited": "降低调用频率，按 Retry-After 重试",
    "unavailable": "稍后重试",
    "store_unavailable": "稍后重试",
    "outcome_unknown": "写入可能已生效：先用 load/list（或 idempotency/get）核对，不要直接重试",
    "idempotency_mismatch": "同一个 Idempotency-Key 只能用于同样的请求内容",
    "login_failed": "检查账套、年度、操作员和口令",
    "account_not_allowed": "这个账套不在允许范围内，换一个账套",
    "test_account_only": "结账、核算、期初、坏账、票据、汇兑等第二级写入只对 CO 桥 testAccounts 里的测试账套开放",
    "feature_disabled": "第二级写入默认关闭：桥设 enableReplicatedWrites 并配置 testAccounts",
    "no_permission": _NO_PERMISSION,
    "forbidden": _NO_PERMISSION,
    "state_mismatch": "先 load 看单据当前状态",
}

# 只给桥返回的 not_found（单据、档案不存在）；本服务自己的 404（路径不存在、接口未启用）不带提示。
BRIDGE_NOT_FOUND_HINT = "核对类型、id 或编码"


def is_retryable(code: str) -> bool:
    return code in RETRYABLE_CODES


def default_hint(status: int, code: str) -> str | None:
    if status in (401, 500):
        return None
    return DEFAULT_HINTS.get(code)


class ApiError(Exception):
    # retry_after：可以稍后重试的错误写进 Retry-After 响应头（秒），其余为 None。
    # field：出错的请求字段路径（点分，如 lines.0.cinvcode），只用于 400。
    # hint：简短的中文修正提示，构造后按需赋值；None 时按错误码取默认提示。
    # detail：桥给的结构化补充（只用于 4xx，如存货核算拒绝时列出的存货），构造后按需赋值。
    def __init__(
        self, status: int, code: str, message: str, retry_after: int | None = None, *, field: str | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after
        self.field = field
        self.hint: str | None = None
        self.detail: dict | None = None


def bad_request(message: str, code: str = "bad_request", field: str | None = None) -> ApiError:
    return ApiError(400, code, message, field=field)


def unauthorized(message: str = "缺少或无效的访问令牌", code: str = "unauthorized") -> ApiError:
    return ApiError(401, code, message)


def forbidden(message: str, code: str = "forbidden") -> ApiError:
    return ApiError(403, code, message)


def not_found(message: str, code: str = "not_found") -> ApiError:
    return ApiError(404, code, message)


def conflict(message: str, code: str = "conflict") -> ApiError:
    return ApiError(409, code, message)


def unprocessable(message: str, code: str = "unprocessable") -> ApiError:
    return ApiError(422, code, message)


def rate_limited(message: str = "请求过于频繁", code: str = "rate_limited") -> ApiError:
    return ApiError(429, code, message)


def bad_gateway(message: str, code: str = "bad_gateway") -> ApiError:
    return ApiError(502, code, message)


def unavailable(message: str = "服务暂时不可用", code: str = "unavailable") -> ApiError:
    return ApiError(503, code, message)


def timeout_error(message: str = "请求超时", code: str = "timeout") -> ApiError:
    return ApiError(504, code, message)
