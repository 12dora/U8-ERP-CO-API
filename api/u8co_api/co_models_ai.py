"""名称解析（archives/resolve）和幂等结果查询（idempotency/get）的请求、响应模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from u8co_api.co_idem import IDEMPOTENT_API_PATHS
from u8co_api.co_models import CoAuth

_FORBID = ConfigDict(extra="forbid")
_PASS = ConfigDict(extra="allow")

# 能 list 的档案去掉两列主键的（客户收货地址、客户存货对照、客户 / 供应商银行、联系人、自定义项）、汇率和固定资产卡片。
ResolveArchive = Literal[
    "customer",
    "vendor",
    "inventory",
    "department",
    "person",
    "warehouse",
    "customer_class",
    "vendor_class",
    "inventory_class",
    "account",
    "unit",
    "unit_group",
    "settle_style",
    "voucher_sign",
    "currency",
    "bank",
    "project",
    "position",
    "rd_style",
    "purchase_type",
    "sale_type",
    "district_class",
    "trade_class",
    "aa_bank",
    "operator",
    "role",
    "reason",
]

RESOLVE_SUMMARY = "按名称解析档案编码"
RESOLVE_HELP = (
    "只读。把名称、简称、助记码或编码解析成档案编码，一次最多 20 项。按层级匹配，第一个有结果的层级胜出："
    "code 编码完全相同，name 名称完全相同，abbr 简称完全相同（客户、供应商），mnemonic 助记码（存货另有 add_code 代码），"
    "contains 名称包含（客户、供应商也查简称；存货按「名称 + 规格」查，所以「示例存货X1」能找到名称示例存货、规格 X1）。"
    "status：exact 前四层恰好一条；ambiguous 胜出层级不止一条；partial 只有包含匹配且恰好一条；none 没有。"
    "match 只在 exact、partial 时给出；candidates 最多 limit 条，more 表示还有更多。"
    "已停用的档案（客户、供应商、存货、部门、仓库的停用日期不晚于登录日期，科目、项目已关闭，操作员已停用）"
    "缺省不返回，include_disabled 为 true 时返回并标 disabled。科目只返回末级。"
    "权限同各档案的 list（含记录级数据权限）。"
)

IDEM_SUMMARY = "查询幂等键的结果"
IDEM_HELP = (
    "只读。按原请求的路径和 Idempotency-Key 查桥上的幂等记录，调用方和账套与原请求相同才查得到。"
    "found 为 false 表示没有记录或已过期（成功的记录保留 24 小时，结果不明的 72 小时）。"
    "state：ok 已成功（response 是当时的成功响应）；outcome_unknown 结果不明（response 是当时的错误，按本服务的错误格式）；"
    "in_flight 还在执行（没有 response）。status 是原请求本服务会返回的 HTTP 状态。"
    "收到 504 outcome_unknown 后先用本接口核对，再决定是否重试。"
)

# 任一写路由的 API 路径，取自 co_idem（由 co_access 的写动作推出），不另列。
IdemPath = Literal[IDEMPOTENT_API_PATHS]  # type: ignore[valid-type]


class ResolveItemIn(BaseModel):
    model_config = _FORBID
    archive: ResolveArchive = Field(..., description="档案类型（两列主键的档案、汇率、固定资产卡片、设备台账除外）")
    q: str = Field(..., min_length=1, max_length=200, description="要解析的名称、简称、助记码或编码，去掉首尾空白后 1 到 100 个字符")

    @field_validator("q")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        text = value.strip()
        if not 1 <= len(text) <= 100:
            raise ValueError("q 去掉首尾空白后必须是 1 到 100 个字符")
        return text


class ResolveIn(CoAuth):
    items: list[ResolveItemIn] = Field(..., min_length=1, max_length=20, description="要解析的项，1 到 20 项")
    limit: int = Field(5, ge=1, le=20, description="每项最多返回的候选数，1 到 20，缺省 5")
    include_disabled: StrictBool = Field(False, description="true 时也返回已停用、已关闭的档案（标 disabled）")


class ResolveCandidate(BaseModel):
    model_config = _PASS
    code: str = Field(description="档案编码（项目是 <项目大类>:<项目编码>）")
    name: str | None = Field(None, description="名称")
    match: str | None = Field(None, description="命中的层级：code、name、abbr、mnemonic、add_code、contains")
    abbr: str | None = Field(None, description="简称（客户、供应商）")
    spec: str | None = Field(None, description="规格型号（存货）")
    unit: str | None = Field(None, description="主计量单位编码（存货）")
    class_code: str | None = Field(None, description="分类编码")
    disabled: bool | None = Field(None, description="已停用或已关闭（只在 include_disabled 时出现）")


class ResolveResult(BaseModel):
    model_config = _PASS
    archive: str = Field(description="档案类型（原样）")
    q: str = Field(description="查询词（去掉首尾空白）")
    status: Literal["exact", "ambiguous", "partial", "none"] = Field(description="解析结果")
    match: ResolveCandidate | None = Field(None, description="唯一命中（exact、partial）")
    candidates: list[ResolveCandidate] = Field(default_factory=list, description="胜出层级的候选，最多 limit 条")
    more: bool = Field(False, description="胜出层级的命中多于 limit 条")


class ResolveOut(BaseModel):
    model_config = _PASS
    ok: bool = Field(description="总是 true")
    results: list[ResolveResult] = Field(description="与 items 一一对应")


class IdemGetIn(CoAuth):
    path: IdemPath = Field(..., description="原请求的路径（任一写路由，如 /v1/co/vouchers/verify）")
    key: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[\x21-\x7e]+$",
        description="原请求的 Idempotency-Key，1 到 128 个可见 ASCII 字符",
    )


class IdemGetOut(BaseModel):
    model_config = _PASS
    ok: bool = Field(description="总是 true")
    found: bool = Field(description="是否有这条记录（过期的算没有）")
    state: Literal["ok", "outcome_unknown", "in_flight"] | None = Field(None, description="记录状态；没找到时省略")
    status: int | None = Field(None, description="原请求本服务返回（或会返回）的 HTTP 状态")
    created_utc: str | None = Field(None, description="记录创建时间（UTC）")
    response: dict[str, Any] | None = Field(
        None, description="当时的响应：成功是原响应体，失败是 {\"error\": {...}}；in_flight 时省略"
    )
