"""账套体检 /v1/co/reports/account_readiness 的请求和响应。字段名与桥一致，响应放行桥多给的字段。

体检只读：十项检查都是 SELECT，不写任何数据。问题放在 checks 里，HTTP 仍是 200。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_gl import DATE, PASS, Scalar
from u8co_api.co_models_reports import _real_date

# 桥给的检查项 id，顺序同 docs/getting-started.md。
READINESS_CHECKS = (
    "patches",
    "years",
    "calendar",
    "yearly_config",
    "pu_opening",
    "vendor_extradefine",
    "modules",
    "prior_gl_close",
    "workflow",
    "defaults",
)
READINESS_STATUS = ("ok", "warn", "fail", "unknown")


class ReportReadinessIn(CoAuth):
    as_of: str | None = Field(
        None,
        pattern=DATE,
        description="检查年度和工作日历用的日期 yyyy-MM-dd，缺省为登录日期（date）。另外总会按服务器当天再查一次",
    )

    @field_validator("as_of")
    @classmethod
    def _as_of(cls, value: str | None) -> str | None:
        return _real_date(value)


class ReportReadinessOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    overall: Scalar = Field(
        None,
        description="各项中最差的状态：fail 比 unknown 差，unknown 比 warn 差，warn 比 ok 差",
    )
    as_of: Scalar = Field(None, description="检查用的日期 yyyy-MM-dd")
    server_today: Scalar = Field(None, description="数据库服务器当天 yyyy-MM-dd")
    acc: Scalar = Field(None, description="账套号")
    checks: list[dict[str, Any]] | None = Field(
        None,
        description="检查项：id、title、status（ok、warn、fail、unknown）、detail（计数、日期、缺的编码）、"
        "fix_hint（怎么修）、docs_anchor（getting-started.md 里的小节）、safe（检查本身只读，总为 true）。"
        "id 有 " + "、".join(READINESS_CHECKS),
    )
