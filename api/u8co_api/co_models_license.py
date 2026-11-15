"""健康检查里的许可点数来源（license_source）和按加密包的点数（license_packs）。只做过滤，不访问桥。"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

_OUT = ConfigDict(extra="ignore")
_SOURCES = frozenset(("leases", "tasklog"))
_CODE = re.compile(r"^[A-Z0-9]{2}\Z")
_MAX_PACKS = 32
_MAX_MODULES = 16

SOURCE_HELP = "许可点数的来源：leases（按实际占用的租约数）或 tasklog（按任务日志估算）。旧版桥不报则省略"
PACKS_HELP = (
    "按加密包（两位码）的点数：used 已用、limit 总数（桥不知道时省略）、modules 这个包覆盖的子系统两位码。"
    "最多 32 个包、每包 16 个子系统。没有则省略"
)


class CoLicensePack(BaseModel):
    model_config = _OUT
    used: int = Field(ge=0, description="已用点数")
    limit: int | None = Field(None, ge=0, description="总点数；桥不知道时省略")
    modules: list[str] = Field(description="这个加密包覆盖的子系统两位码，如 SA、GL")


def license_source(value: object) -> str | None:
    """只收 leases、tasklog，其余丢掉。"""
    return value if isinstance(value, str) and value in _SOURCES else None


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _code(value: object) -> bool:
    return isinstance(value, str) and _CODE.fullmatch(value) is not None


def _pack(value: object) -> dict | None:
    # used 必须是非负整数，modules 必须是列表；列表里不合格的码丢掉，最多留 16 个。
    # limit 可以没有（桥不知道总数时省略，这时包照样保留），有就必须是非负整数。
    if not isinstance(value, dict) or not isinstance(value.get("modules"), list):
        return None
    if not _count(value.get("used")):
        return None
    limit = value.get("limit")
    if limit is not None and not _count(limit):
        return None
    modules = [code for code in value["modules"] if _code(code)][:_MAX_MODULES]
    pack: dict = {"used": value["used"], "modules": modules}
    if limit is not None:
        pack["limit"] = limit
    return pack


def license_packs(value: object) -> dict[str, dict] | None:
    """桥报告的各加密包点数。包码只收两位大写字母或数字，不合格的整项丢掉，最多留 32 个包。"""
    if not isinstance(value, dict):
        return None
    kept: dict[str, dict] = {}
    for code, raw in value.items():
        if len(kept) >= _MAX_PACKS:
            break
        pack = _pack(raw)
        if _code(code) and pack is not None:
            kept[code] = pack
    return kept or None
