"""基础档案写入的 fields 校验：编码标签（与桥 ArcKind.CodeTag 一致）、总账档案（桥 ArcGlKinds.Check）
和汇率（桥 ArcExchWrite.Check）的格式。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Any

_CURRENCY = "currency"
_SIGN = "voucher_sign"
_GL = frozenset({_CURRENCY, _SIGN})
# 币种符号 cexch_code 最长 4；凭证类别字最长 2；凭证类别名称最长 30。
_SYMBOL_MAX = 4
_SIGN_MAX = 2
_SIGN_NAME_MAX = 30


def check_code_tag(archive: str, fields: dict[str, Any], creating: bool) -> None:
    """编码只能用顶层 code。币种例外（桥 ArcKind.CodeTag）：顶层 code 是币种名称，fields 的 code 是币种符号，
    新增必填、不能修改；名称 name 就是编码，不能放进 fields。凭证类别的编码标签是 type。"""
    tags = {tag.lower() for tag in fields}
    if archive == _SIGN and "type" in tags:
        raise ValueError("凭证类别字就是顶层 code，不能放进 fields（type）")
    if archive != _CURRENCY:
        if "code" in tags:
            raise ValueError("编码只能用顶层 code，不能放进 fields")
        return
    if "name" in tags:
        raise ValueError("币种名称就是顶层 code，不能放进 fields")
    if not creating and "code" in tags:
        raise ValueError("不能修改币种符号 code，请删除后重新新增")
    symbol = next((value for key, value in fields.items() if key.lower() == "code"), None)
    if creating and not (isinstance(symbol, str) and symbol.strip() and len(symbol) <= _SYMBOL_MAX):
        raise ValueError("新增币种必须在 fields 给 code（币种符号，1 到 4 个字符）")


def check_gl_write(archive: str, code: str, fields: dict[str, Any], creating: bool) -> None:
    """总账档案写入的格式（库里的状态由桥查）：凭证类别新增编码最长 2、必须给 type_name，修改只收 type_name。
    两类都不收 template（在调用方检查）。"""
    if archive != _SIGN:
        return
    if creating:
        if len(code) > _SIGN_MAX:
            raise ValueError("凭证类别字 code 最长 2 个字符")
        if "type_name" not in {tag.lower() for tag in fields}:
            raise ValueError("新增凭证类别必须给 type_name（类别名称）")
    elif any(tag.lower() != "type_name" for tag in fields):
        raise ValueError("凭证类别只能修改名称 type_name")
    name = next((value for key, value in fields.items() if key.lower() == "type_name"), None)
    if name is not None and not (isinstance(name, str) and name.strip() and len(name) <= _SIGN_NAME_MAX):
        raise ValueError("type_name 必须是 1 到 30 个字符且不能全是空白")


def is_gl_archive(archive: str) -> bool:
    return archive in _GL


_EXCH = "exchange_rate"
_RATE = "rate"
_ADJUST = "adjust_rate"
_RATE_MAX = Decimal(1000000000)


def _rate_ok(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return False
    return number.is_finite() and Decimal(0) < number <= _RATE_MAX


def _exch_day_ok(parts: list[str]) -> bool:
    """新增浮动汇率：日是 yyyy-mm-dd，落在编码的年度、期间（自然月）里。"""
    try:
        day = dt.date.fromisoformat(parts[3])
    except ValueError:
        return False
    return len(parts[3]) == 10 and day.year == int(parts[1]) and day.month == int(parts[2])


def check_exch_write(archive: str, code: str, fields: dict[str, Any], creating: bool) -> None:
    """汇率写入的格式（库里的状态由桥查）：编码 <币种>:<年度>:<期间>（固定汇率，fields 收 rate 记账汇率、
    adjust_rate 调整汇率）或 <币种>:<年度>:<期间>:<日>（浮动汇率，只收 rate）；汇率大于 0、不超过 1000000000；
    新增一次只写一种汇率，浮动汇率的日写成 yyyy-mm-dd。"""
    if archive != _EXCH:
        return
    parts = code.split(":")
    if len(parts) not in (3, 4):
        raise ValueError(
            "写入汇率时 code 必须写成 <币种>:<年度>:<期间>（固定汇率）或 <币种>:<年度>:<期间>:<日>（浮动汇率）"
        )
    lowered = {tag.lower(): value for tag, value in fields.items()}
    unknown = sorted(tag for tag in lowered if tag not in (_RATE, _ADJUST))
    if unknown:
        raise ValueError("汇率只收 rate、adjust_rate，未知字段 " + ", ".join(unknown))
    bad = sorted(tag for tag, value in lowered.items() if not _rate_ok(value))
    if bad:
        raise ValueError(f"字段 {bad[0]} 必须是大于 0、不超过 1000000000 的数")
    if len(parts) == 4 and _ADJUST in lowered:
        raise ValueError("浮动汇率没有调整汇率 adjust_rate")
    if creating and len(lowered) != 1:
        raise ValueError("新增一次只写一种汇率：fields 给 rate 或 adjust_rate 之一")
    if creating and len(parts) == 4 and not _exch_day_ok(parts):
        raise ValueError("新增浮动汇率时 code 写成 <币种>:<年度>:<期间>:<yyyy-mm-dd>，日期在该年度、期间内")
