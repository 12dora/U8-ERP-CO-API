"""客户字段 arc_customer_fields 的本地预检：一张声明式键表（与 API 的 co_models_arc_cusfields_keys、桥的 ArcCusCol 一致）。

类型写法：text(n) 文本，首尾空白去掉后最长 n；text!(n) 同上且不能为空；code(n) 档案编码，首尾不能有空白、最长 n；
date yyyy-MM-dd；bit 只收 "0" / "1"；num[lo,hi] 小数（区间 [lo,hi] 或 [lo,hi)），num 不限区间；int 32 位整数；
postcode 邮政编码（最长 20，只含 0-9 a-z -）。引用档案是否存在、是否末级等只有桥查得到。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from co.client.u8co_gl_arc import _ymd

_INT32 = 2**31
_MIN_YEAR = 1900
_NUM = re.compile(r"-?\d+(\.\d{1,8})?")
_NUM_ANY = re.compile(r"-?\d+(\.\d+)?")
_MAX_DIGITS = 15
_INT = re.compile(r"-?\d+")
_POSTCODE = re.compile(r"[0-9a-z-]*")
_TEXT = re.compile(r"(text|code)(!?)\((\d+)\)")
_RANGE = re.compile(r"num\[(-?\d+),(-?\d+)([\])])")
_SIMPLE = ("date", "bit", "int", "num", "postcode")


def _define_kind(number: int) -> str:
    """自定义项 1-3 文本 20、4-6 文本 60、7-10 文本 120、11-12 整数、13-14 小数、15-16 日期。"""
    if number <= 3:
        return "text(20)"
    if number <= 6:
        return "text(60)"
    if number <= 10:
        return "text(120)"
    return "int" if number <= 12 else "num" if number <= 14 else "date"


_ROWS: tuple[tuple[str, str, str], ...] = (
    ("abbrname", "cCusAbbName", "text!(60)"),
    ("district", "cDCCode", "code(12)"),
    ("end_date", "dEndDate", "date"),
    ("sa_protocol", "cCusSAProtocol", "code(20)"),
    ("name", "cCusName", "text!(98)"),
    ("tax_no", "cCusRegCode", "text(50)"),
    ("contact", "cCusPerson", "text(50)"),
    ("phone", "cCusPhone", "text(100)"),
    ("mobile", "cCusHand", "text(100)"),
    ("fax", "cCusFax", "text(100)"),
    ("bp", "cCusBP", "text(20)"),
    ("email", "cCusEmail", "text(100)"),
    ("address", "cCusAddress", "text(255)"),
    ("postcode", "cCusPostCode", "postcode"),
    ("legal_man", "cCusLPerson", "text(100)"),
    ("memo", "cMemo", "text(1000)"),
    ("bank_open", "cCusBank", "text(100)"),
    ("bank_acc_number", "cCusAccount", "text(50)"),
    ("ccusbankcode", "cCusBankCode", "code(50)"),
    ("pay_condition", "cCusPayCond", "code(20)"),
    ("ccussscode", "cCusSSCode", "code(20)"),
    ("devliver_site", "cCusOAddress", "text(255)"),
    ("deliver_mode", "cCusOType", "code(10)"),
    ("deli_warehouse", "cCusWhCode", "code(10)"),
    ("ccusexprotocol", "cCusExProtocol", "code(20)"),
    ("ccusotherprotocol", "cCusOtherProtocol", "code(20)"),
    ("ccuscmprotocol", "cCusCMProtocol", "code(20)"),
    ("ccusimagentprotocol", "cCusImAgentProtocol", "code(20)"),
    ("ccusexch_name", "cCusExch_name", "code!(50)"),
    ("sort_code", "cCCCode", "code!(12)"),
    ("ccustradeccode", "cCusTradeCCode", "code(12)"),
    ("head_corp_code", "cCusHeadCode", "code(20)"),
    ("seed_date", "dCusDevDate", "date"),
    *((f"self_define{n}", f"cCusDefine{n}", _define_kind(n)) for n in range(1, 17)),
    ("crelvendor", "cRelVendor", "code(20)"),
    ("bcusdomestic", "bCusDomestic", "bit"),
    ("bcusoverseas", "bCusOverseas", "bit"),
    ("bserviceattribute", "bServiceAttribute", "bit"),
    ("brequestsign", "bRequestSign", "bit"),
    ("discount_rate", "iCusDisRate", "num[0,100]"),
    ("fcusdiscountrate", "fCusDiscountRate", "num[0,100]"),
    ("fadvancepaymentratio", "fAdvancePaymentRatio", "num[0,100]"),
    ("icustaxrate", "iCusTaxRate", "num[0,100)"),
)


@dataclass(frozen=True)
class FieldKey:
    key: str
    column: str
    kind: str  # text / code / date / bit / num / int / postcode
    limit: int = 0
    required: bool = False
    lo: Decimal | None = None
    hi: Decimal | None = None
    hi_open: bool = False


def _parse(row: tuple[str, str, str]) -> FieldKey:
    key, column, spec = row
    text = _TEXT.fullmatch(spec)
    if text is not None:
        return FieldKey(key, column, text[1], int(text[3]), text[2] == "!")
    span = _RANGE.fullmatch(spec)
    if span is not None:
        return FieldKey(
            key,
            column,
            "num",
            lo=Decimal(span[1]),
            hi=Decimal(span[2]),
            hi_open=span[3] == ")",
        )
    if spec not in _SIMPLE:
        raise ValueError(f"未知类型 {spec}")
    return FieldKey(key, column, spec, 20 if spec == "postcode" else 0)


CUSTOMER_FIELD_KEYS: dict[str, FieldKey] = {row[0]: _parse(row) for row in _ROWS}


def _plain(value: object, label: str, limit: int) -> str:
    # 可为空串的文本：不含控制字符，不超过 limit。
    if type(value) is not str:
        raise ValueError(f"{label} 必须是字符串")
    if len(value) > limit:
        raise ValueError(f"{label} 必须是不超过 {limit} 个字符的字符串")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{label} 不能含控制字符")
    return value


def _check_text(spec: FieldKey, value: object, label: str) -> None:
    text = _plain(value, label, 10**6).strip()
    if spec.required and text == "":
        raise ValueError(f"{label} 不能为空")
    _plain(text, label, spec.limit)


def _check_code(spec: FieldKey, value: object, label: str) -> None:
    text = _plain(value, label, spec.limit)
    if spec.required and text == "":
        raise ValueError(f"{label} 不能为空")
    if text != text.strip():
        raise ValueError(f"{label} 首尾不能有空白")


def _check_date(value: object, label: str) -> None:
    text = _ymd(value, label)  # type: ignore[arg-type]
    if int(text[:4]) < _MIN_YEAR:
        raise ValueError(f"{label} 年份不能早于 {_MIN_YEAR}")


def _check_bit(value: object, label: str) -> None:
    if value not in ("0", "1"):
        raise ValueError(f"{label} 只能是 0 或 1")


def _check_int(value: object, label: str) -> None:
    if (
        type(value) is not str
        or _INT.fullmatch(value) is None
        or not -_INT32 <= int(value) < _INT32
    ):
        raise ValueError(f"{label} 必须是 32 位整数")


def _check_num(spec: FieldKey, value: object, label: str) -> None:
    if type(value) is not str or _NUM.fullmatch(value) is None:
        raise ValueError(f"{label} 必须是小数（可带负号，小数最多 8 位）")
    if len(value.replace("-", "").replace(".", "").lstrip("0")) > _MAX_DIGITS:
        raise ValueError(f"{label} 最多 {_MAX_DIGITS} 位有效数字")
    number = Decimal(value)
    if spec.lo is None or spec.hi is None:
        return
    if number < spec.lo or number > spec.hi or (spec.hi_open and number == spec.hi):
        raise ValueError(
            f"{label} 必须在 {spec.lo} 到 {spec.hi} 之间{'（不含上限）' if spec.hi_open else ''}"
        )


def _check_postcode(value: object, label: str) -> None:
    text = _plain(value, label, 10**6).strip()
    if len(text) > 20 or _POSTCODE.fullmatch(text) is None:
        raise ValueError(f"{label} 最长 20 位，只能含 0-9、小写 a-z、-")


def _check_value(spec: FieldKey, value: object, label: str) -> None:
    kind = spec.kind
    if kind == "bit":
        _check_bit(value, label)
    elif kind == "text":
        _check_text(spec, value, label)
    elif kind == "code":
        _check_code(spec, value, label)
    elif value == "":
        return
    elif kind == "postcode":
        _check_postcode(value, label)
    elif kind == "date":
        _check_date(value, label)
    elif kind == "int":
        _check_int(value, label)
    else:
        _check_num(spec, value, label)


def _check_expect(spec: FieldKey, value: object, label: str) -> None:
    # 当前值只核对，不要求写法合规（库里的旧数据可以有首尾空白）；编码类与桥一致，首尾空白同样 400。
    kind = spec.kind
    if kind == "code":
        _check_code(FieldKey(spec.key, spec.column, "code", spec.limit), value, label)
    elif kind in ("text", "postcode"):
        if type(value) is not str or len(value) > spec.limit:
            raise ValueError(f"{label} 必须是不超过 {spec.limit} 个字符的字符串")
    elif kind == "bit":
        _check_bit(value, label)
    elif value == "":
        return
    elif kind == "date":
        _ymd(value, label)  # type: ignore[arg-type]
    elif kind == "int":
        _check_int(value, label)
    elif type(value) is not str or _NUM_ANY.fullmatch(value) is None:
        raise ValueError(f"{label} 必须是小数")


def check_customer_values(
    values: object, label: str, allowed: tuple[str, ...], writing: bool
) -> dict[str, str]:
    """fields（writing=True，至少一项）或 expect 的本地预检；返回副本。"""
    if not isinstance(values, dict):
        raise ValueError(f"{label} 必须是对象")
    if writing and not values:
        raise ValueError(f"{label} 至少要有一项")
    unknown = sorted(str(key) for key in values if key not in allowed)
    if unknown:
        raise ValueError(
            f"{label} 有不允许的键: {', '.join(unknown)}；可用 {'、'.join(allowed)}"
        )
    for key, value in values.items():
        check = _check_value if writing else _check_expect
        check(CUSTOMER_FIELD_KEYS[key], value, f"{label}.{key}")
    if (
        writing
        and values.get("end_date")
        and values.get("seed_date")
        and values["end_date"] < values["seed_date"]
    ):
        raise ValueError("fields.end_date 不能早于 fields.seed_date")
    return dict(values)
