"""e2e 配置里可选的 seed 段：按测试账套的编码方案和科目表改造数用的编码、科目和日期。

没写 seed 段（或写了空对象）时造数和缺省完全一样。写了的键逐项校验，不认识的键拒绝。
键和缺省值见 data.py 顶部的可覆盖值；改了哪些编码，造数前的预检（preflight.py）按账套的编码方案和科目表核对。

造数步骤模块在导入时就取 data 里的编码，所以 apply() 必须在导入 runner / steps_* 之前调用（__main__ 这样做）。
"""

from __future__ import annotations

import datetime
import re
import sys
from dataclasses import dataclass

from co.e2e import settings_parse as sp
from co.e2e.seed import data
from co.e2e.settings_parse import ConfigError

_CODE = re.compile(r"^[A-Za-z0-9]{1,20}\Z")
_SUBJECT = re.compile(r"^\d{4,30}\Z")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
_TOP = frozenset(("date", "codes", "cycle_codes", "gl"))
_CODE_KEYS = frozenset(
    ("departments", "customer_class", "vendor_class", "inventory_class", "rd_style", "settle_style", "district_class",
     "position")
)
_LIST_SIZES = {"departments": len(data.DEPARTMENT_CODES), "inventory_class": len(data.INVENTORY_CLASS_CODES)}
_GL_KEYS = frozenset(("expense_account", "payable_account", "bank_account", "cash_flow_item"))


@dataclass(frozen=True)
class SeedOverrides:
    # 只存配置里写了的键（点分路径 → 值）；值是字符串或字符串元组。
    items: tuple[tuple[str, object], ...] = ()

    def get(self, key: str, default: object = None) -> object:
        for name, value in self.items:
            if name == key:
                return value
        return default

    def keys(self) -> tuple[str, ...]:
        return tuple(name for name, _value in self.items)


def _code(value: object, label: str) -> str:
    if not isinstance(value, str) or _CODE.fullmatch(value.strip()) is None:
        raise ConfigError(f"配置项 {label} 必须是 1–20 位字母或数字的编码")
    return value.strip()


def _codes(value: object, label: str, size: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) != size:
        raise ConfigError(f"配置项 {label} 必须是 {size} 个编码的数组")
    codes = tuple(_code(item, label) for item in value)
    if len({code.upper() for code in codes}) != size:
        raise ConfigError(f"配置项 {label} 里的编码不能重复")
    return codes


def _date(value: object) -> str:
    text = value.strip() if isinstance(value, str) else ""
    try:
        if _DATE.fullmatch(text) is None:
            raise ValueError(text)
        datetime.date.fromisoformat(text)
    except ValueError:
        raise ConfigError("配置项 seed.date 必须是 YYYY-MM-DD 日期") from None
    return text


def _parse_codes(sec: dict, out: list) -> None:
    sp.known(sec, _CODE_KEYS, "配置项 seed.codes")
    for key, value in sec.items():
        label = "seed.codes." + key
        if key == "rd_style":
            _parse_rd(value, out)
        elif key in _LIST_SIZES:
            out.append(("codes." + key, _codes(value, label, _LIST_SIZES[key])))
        else:
            out.append(("codes." + key, _code(value, label)))


def _parse_rd(value: object, out: list) -> None:
    if not isinstance(value, dict):
        raise ConfigError("配置项 seed.codes.rd_style 必须是对象")
    sp.known(value, frozenset(data.RD), "配置项 seed.codes.rd_style")
    for role, code in value.items():
        out.append(("codes.rd_style." + role, _code(code, "seed.codes.rd_style." + role)))


def _parse_cycle(sec: dict, out: list) -> None:
    sp.known(sec, frozenset(data.CYCLE_CODES), "配置项 seed.cycle_codes")
    for kind, value in sec.items():
        size = len(data.CYCLE_CODES[kind])
        out.append(("cycle_codes." + kind, _codes(value, "seed.cycle_codes." + kind, size)))


def _parse_gl(sec: dict, out: list) -> None:
    sp.known(sec, _GL_KEYS, "配置项 seed.gl")
    for key, value in sec.items():
        label = "seed.gl." + key
        if key == "cash_flow_item":
            out.append(("gl." + key, _code(value, label)))
            continue
        if not isinstance(value, str) or _SUBJECT.fullmatch(value.strip()) is None:
            raise ConfigError(f"配置项 {label} 必须是 4–30 位数字的科目编码")
        out.append(("gl." + key, value.strip()))


def _object(data_: dict, key: str) -> dict:
    value = data_.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"配置项 seed.{key} 必须是对象")
    return value


def parse(raw: object) -> SeedOverrides:
    """e2e 配置顶层的 seed 段。没写或为 null 时返回空的 SeedOverrides（全用缺省）。"""
    if raw is None:
        return SeedOverrides()
    if not isinstance(raw, dict):
        raise ConfigError("配置项 seed 必须是对象")
    sp.known(raw, _TOP, "配置项 seed")
    out: list[tuple[str, object]] = []
    if "date" in raw and raw["date"] is not None:
        out.append(("date", _date(raw["date"])))
    _parse_codes(_object(raw, "codes"), out)
    _parse_cycle(_object(raw, "cycle_codes"), out)
    _parse_gl(_object(raw, "gl"), out)
    return SeedOverrides(tuple(out))


# ---- 应用到 data ----

_LOADED_PREFIX = ("co.e2e.seed.runner", "co.e2e.seed.steps_")


def _loaded_steps() -> list[str]:
    return sorted(name for name in sys.modules if name.startswith(_LOADED_PREFIX))


def _consistency(rd: dict[str, str], inv_class: tuple[str, str]) -> list[str]:
    problems = []
    top, leaf = inv_class
    if not (leaf.startswith(top) and len(leaf) > len(top)):
        problems.append("seed.codes.inventory_class 的第二个编码必须以第一个开头且更长")
    for role, code in rd.items():
        parent = data.RD_PARENT.get(role)
        if parent and not (code.startswith(rd[parent]) and len(code) > len(rd[parent])):
            problems.append(f"seed.codes.rd_style.{role} 必须以 {parent}（{rd[parent]}）开头且更长")
    return problems


_SIMPLE = {
    "date": "SEED_DATE",
    "codes.departments": "DEPARTMENT_CODES",
    "codes.customer_class": "CUSTOMER_CLASS",
    "codes.vendor_class": "VENDOR_CLASS",
    "codes.inventory_class": "INVENTORY_CLASS_CODES",
    "codes.settle_style": "SETTLE_STYLE",
    "codes.district_class": "DISTRICT",
    "codes.position": "POSITION",
}


def values(seed: SeedOverrides) -> dict:
    """缺省值（data.defaults）合并配置的覆盖值，交给 data.configure。"""
    out = data.defaults()
    for key, name in _SIMPLE.items():
        out[name] = seed.get(key, out[name])
    for key, value in seed.items:
        head, _, rest = key.partition(".")
        if key.startswith("codes.rd_style."):
            out["RD"][key.rsplit(".", 1)[1]] = value
        elif head == "cycle_codes":
            out["CYCLE_CODES"][rest] = value
        elif head == "gl":
            out["GL_BASE"][rest] = value
    out["BANK_FIXED"] = "gl.bank_account" in seed.keys()
    return out


def apply(seed: SeedOverrides | None) -> None:
    """把配置的覆盖值写进 data 并重算档案表。没有覆盖时什么都不做。"""
    if seed is None or not seed.items:
        return
    loaded = _loaded_steps()
    if loaded:
        raise ConfigError("造数配置要在导入造数步骤之前应用，已导入：" + "、".join(loaded[:3]))
    merged = values(seed)
    problems = _consistency(merged["RD"], merged["INVENTORY_CLASS_CODES"])
    if problems:
        raise ConfigError("；".join(problems))
    data.configure(merged)
