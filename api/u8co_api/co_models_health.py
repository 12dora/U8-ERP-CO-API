"""/v1/co/health 的出参：缺省桥的状态与许可点数，以及按账套分流的各路由桥（U8CO_BRIDGE_ROUTES_FILE）。"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from u8co_api.co_models_license import PACKS_HELP, SOURCE_HELP, CoLicensePack, license_packs, license_source

_OUT = ConfigDict(extra="ignore")
_LICENSE_STATES = frozenset(("ok", "near", "full", "unknown"))
_LICENSE_SUB = re.compile(r"^[A-Z]{2}\Z")
_LICENSE_KEYS = ("used", "limit", "full_24h")

ROUTES_HELP = (
    "按账套分流的桥（U8CO_BRIDGE_ROUTES_FILE），按文件里的顺序各一项。没配分流时省略。"
    "顶层的 ok、version、license* 只描述缺省桥"
)


def _license_counts(value: object) -> dict[str, int] | None:
    # 只留 used、limit、full_24h 里的非负整数，别的键和值都丢掉；一个都不剩就整项丢掉。
    if not isinstance(value, dict):
        return None
    counts = {key: value[key] for key in _LICENSE_KEYS if _count(value.get(key))}
    return counts or None


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def license_detail(value: object) -> dict[str, dict[str, int]] | None:
    """桥报告的各子系统许可点数。键只收两位大写字母的子系统码，其余一律丢掉。"""
    if not isinstance(value, dict):
        return None
    kept: dict[str, dict[str, int]] = {}
    for sub, raw in value.items():
        counts = _license_counts(raw)
        if isinstance(sub, str) and _LICENSE_SUB.fullmatch(sub) and counts is not None:
            kept[sub] = counts
    return kept or None


_POLICY_STATES = frozenset(("off", "ok", "invalid", "missing", "unknown"))
WRITE_POLICY_HELP = (
    "缺省桥的写入策略（桥 config.json 的 writePolicyFile）：state 为 off（未配置）、ok、invalid（新内容无效，仍按上一份"
    "有效策略）、missing（文件不存在，写入全部拒绝）。旧版桥不报则省略"
)
API_POLICY_HELP = "本服务自己的写入策略（U8CO_WRITE_POLICY_FILE），形状同 write_policy。没配置时省略"


READ_ONLY_HELP = "缺省桥的只读账套（桥 config.json 的 readOnlyAccounts），这些账套的写路由一律 403。没配或旧版桥不报则省略"
REPLICATED_HELP = (
    "桥的第二级写入总开关（桥 config.json 的 enableReplicatedWrites，缺省 false）：false 时结账、核算、期初等"
    "第二级写入一律 403 feature_disabled；true 时仍只对 testAccounts 开放。旧版桥不报则省略"
)
API_READ_ONLY_HELP = "本服务的只读账套（U8CO_READONLY_ACCOUNTS），这些账套的写路由在访问桥之前 403 account_read_only。没配时省略"
_ACC = re.compile(r"^[0-9]{3}\Z")


def read_only_of(value: object) -> list[str] | None:
    """只读账套列表：只留三位数字的账套号；不是列表或一个不剩时整项省略。"""
    if not isinstance(value, list):
        return None
    kept = [item for item in value if isinstance(item, str) and _ACC.fullmatch(item)]
    return kept or None


def replicated_of(value: object) -> bool | None:
    """桥报告的第二级写入总开关：只收布尔值，别的一律省略。"""
    return value if isinstance(value, bool) else None


class CoWritePolicyFreeze(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    global_: bool = Field(False, alias="global", description="是否冻结全部账套")
    accounts: list[str] = Field(default_factory=list, description="冻结的账套号")


class CoWritePolicyHealth(BaseModel):
    model_config = _OUT
    state: str = Field(description="off、ok、invalid、missing；读不到时 unknown")
    version: int | None = Field(None, description="当前有效策略的 version；没有有效策略时为 null")
    loaded_at: str | None = Field(None, description="当前有效策略的加载时间（UTC，ISO 8601）")
    freeze: CoWritePolicyFreeze | None = Field(None, description="冻结情况")
    window_open: bool | None = Field(None, description="此刻是否在允许写入的时段内（含 denyDates）")

    @field_validator("state")
    @classmethod
    def _state(cls, value: str) -> str:
        if value not in _POLICY_STATES:
            raise ValueError("unknown state")
        return value


def write_policy_of(value: object) -> CoWritePolicyHealth | None:
    """桥或本服务报告的写入策略对象；形状不对时整项丢掉（健康检查不因此失败）。"""
    if not isinstance(value, dict):
        return None
    try:
        return CoWritePolicyHealth.model_validate(value)
    except ValidationError:
        return None


class CoRouteHealth(BaseModel):
    model_config = _OUT
    route: str = Field(description="路由名：routes[序号]，序号从 0 起，与审计行的 bridge 字段一致")
    accounts: list[str] = Field(description="这个桥服务的账套号")
    ok: bool = Field(description="这个桥是否可用")
    version: str | None = Field(None, description="桥报告的版本；不可用时省略")
    error: str | None = Field(None, description="不可用时的错误码（如 unavailable）；可用时省略")
    write_policy: CoWritePolicyHealth | None = Field(None, description="这个桥的写入策略，形状同顶层 write_policy")
    replicated_writes: bool | None = Field(None, description="这个桥的第二级写入总开关，含义同顶层 replicated_writes")

    @field_validator("replicated_writes", mode="before")
    @classmethod
    def _replicated(cls, value: object) -> bool | None:
        return replicated_of(value)

    @field_validator("write_policy", mode="before")
    @classmethod
    def _write_policy(cls, value: object) -> CoWritePolicyHealth | None:
        return write_policy_of(value)


class CoHealthOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="桥是否可用")
    version: str = Field(description="桥报告的版本")
    license: str | None = Field(
        None,
        description="U8 许可点数：ok、near（将满）、full（已满）、unknown（读不到）。旧版桥不报则省略",
    )
    license_detail: dict[str, dict[str, int]] | None = Field(
        None,
        description="按子系统（SA、GL 等两位码）的许可点数：used 已用、limit 总数、full_24h 近 24 小时已满次数。没有则省略",
    )

    license_source: str | None = Field(None, description=SOURCE_HELP)
    license_packs: dict[str, CoLicensePack] | None = Field(None, description=PACKS_HELP)
    routes: list[CoRouteHealth] | None = Field(None, description=ROUTES_HELP)
    write_policy: CoWritePolicyHealth | None = Field(None, description=WRITE_POLICY_HELP)
    api_write_policy: CoWritePolicyHealth | None = Field(None, description=API_POLICY_HELP)
    read_only_accounts: list[str] | None = Field(None, description=READ_ONLY_HELP)
    api_read_only_accounts: list[str] | None = Field(None, description=API_READ_ONLY_HELP)
    replicated_writes: bool | None = Field(None, description=REPLICATED_HELP)

    @field_validator("replicated_writes", mode="before")
    @classmethod
    def _replicated(cls, value: object) -> bool | None:
        return replicated_of(value)

    @field_validator("write_policy", "api_write_policy", mode="before")
    @classmethod
    def _write_policy(cls, value: object) -> CoWritePolicyHealth | None:
        return write_policy_of(value)

    @field_validator("read_only_accounts", "api_read_only_accounts", mode="before")
    @classmethod
    def _read_only(cls, value: object) -> list[str] | None:
        return read_only_of(value)

    @field_validator("license", mode="before")
    @classmethod
    def _license(cls, value: object) -> str | None:
        return value if isinstance(value, str) and value in _LICENSE_STATES else None

    @field_validator("license_detail", mode="before")
    @classmethod
    def _license_detail(cls, value: object) -> dict[str, dict[str, int]] | None:
        return license_detail(value)

    @field_validator("license_source", mode="before")
    @classmethod
    def _license_source(cls, value: object) -> str | None:
        return license_source(value)

    @field_validator("license_packs", mode="before")
    @classmethod
    def _license_packs(cls, value: object) -> dict[str, dict] | None:
        return license_packs(value)
