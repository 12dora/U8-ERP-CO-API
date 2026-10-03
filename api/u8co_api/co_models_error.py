"""错误响应体 {"error": {...}} 的模型（OpenAPI 用）。co_models 原样再导出 ErrorBody、ErrorDetail。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_FORBID = ConfigDict(extra="forbid")


class ErrorDetail(BaseModel):
    model_config = _FORBID
    code: str = Field(description="错误码")
    message: str = Field(description="中文说明")
    retryable: bool = Field(
        description="能否原样稍后重试（busy、busy_timeout、stopping、u8_license_full、ia_timeout、rate_limited、unavailable、"
        "store_unavailable，以及写入策略的 write_policy_unavailable、write_frozen、write_window、"
        "write_quota、u8_license_hold 为 true；"
        "outcome_unknown、write_not_allowed、operator_not_allowed、write_limit 等其余为 false）"
    )
    field: str | None = Field(
        None, description="出错的请求字段路径，点分、下标从 0 起，如 lines.0.cinvcode。只用于 400，未知时省略"
    )
    hint: str | None = Field(None, description="简短的中文修正提示。401、500 和没有提示时省略")
    detail: dict[str, Any] | None = Field(
        None,
        description="桥给的结构化补充，只用于 4xx（401 除外），没有时省略。例如存货核算记账 409 时的 "
        "uncosted（[{wh, inv, batch}]，最多 20 项）和 uncosted_total；写入策略 403 write_not_allowed 的 type、op，"
        "400 write_limit 的 max、actual",
    )


class ErrorBody(BaseModel):
    model_config = _FORBID
    error: ErrorDetail = Field(description="错误")
