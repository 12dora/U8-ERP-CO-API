"""客户字段 /v1/co/archives/customer_fields 的键表：一张声明式表驱动校验、帮助文字和字段说明。

键、列、类型写法与桥（ArcCusCol）一致：
text(n) 文本，首尾空白去掉后最长 n；text!(n) 同上且不能为空；code(n) 档案编码，首尾不能有空白、最长 n；
date yyyy-MM-dd；bit 只收 "0" / "1"；num[lo,hi] 小数（区间 [lo,hi] 或 [lo,hi)），num 不限区间；int 32 位整数；
postcode 邮政编码（text(20)，只含 0-9 a-z -）。引用档案是否存在、是否末级等只有桥查得到。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from u8co_api.co_models_arc_cus import check_text, check_ymd

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


# (键, 列, 类型, 引用 / 说明)。顺序即帮助文字里的键序；响应里的键序由桥的声明表决定。
_ROWS: tuple[tuple[str, str, str, str], ...] = (
    ("abbrname", "cCusAbbName", "text!(60)", "不分大小写唯一"),
    ("district", "cDCCode", "code(12)", "DistrictClass 末级"),
    ("end_date", "dEndDate", "date", "不早于 seed_date，未给 seed_date 时不早于建档日期"),
    ("sa_protocol", "cCusSAProtocol", "code(20)", "AA_Agreement 未停用，按 iLZYJ 过滤"),
    ("name", "cCusName", "text!(98)", "账套开启客户名称唯一（RptCusName=1）时不可重复"),
    ("tax_no", "cCusRegCode", "text(50)", ""),
    ("contact", "cCusPerson", "text(50)", ""),
    ("phone", "cCusPhone", "text(100)", ""),
    ("mobile", "cCusHand", "text(100)", ""),
    ("fax", "cCusFax", "text(100)", ""),
    ("bp", "cCusBP", "text(20)", ""),
    ("email", "cCusEmail", "text(100)", ""),
    ("address", "cCusAddress", "text(255)", ""),
    ("postcode", "cCusPostCode", "postcode", ""),
    ("legal_man", "cCusLPerson", "text(100)", ""),
    ("memo", "cMemo", "text(1000)", ""),
    ("bank_open", "cCusBank", "text(100)", ""),
    ("bank_acc_number", "cCusAccount", "text(50)", ""),
    ("ccusbankcode", "cCusBankCode", "code(50)", "AA_Bank"),
    ("pay_condition", "cCusPayCond", "code(20)", "PayCondition"),
    ("ccussscode", "cCusSSCode", "code(20)", "SettleStyle"),
    ("devliver_site", "cCusOAddress", "text(255)", "发货地址（键名沿用 EAI 拼写）"),
    ("deliver_mode", "cCusOType", "code(10)", "ShippingChoice"),
    ("deli_warehouse", "cCusWhCode", "code(10)", "Warehouse"),
    ("ccusexprotocol", "cCusExProtocol", "code(20)", "AA_Agreement，iLZYJ 为 2 或 3"),
    ("ccusotherprotocol", "cCusOtherProtocol", "code(20)", "AA_Agreement，iLZYJ 为 5"),
    ("ccuscmprotocol", "cCusCMProtocol", "code(20)", "AA_Agreement，iLZYJ 为 5、6、8、9"),
    ("ccusimagentprotocol", "cCusImAgentProtocol", "code(20)", "AA_Agreement，iLZYJ 为 12"),
    ("ccusexch_name", "cCusExch_name", "code!(50)", "币种名称（foreigncurrency.cexch_name），必填"),
    ("sort_code", "cCCCode", "code!(12)", "CustomerClass 末级，必填"),
    ("ccustradeccode", "cCusTradeCCode", "code(12)", "TradeClass 末级，同时写行业名称 cTrade"),
    ("head_corp_code", "cCusHeadCode", "code(20)", "已有客户；空串 = 本客户"),
    ("seed_date", "dCusDevDate", "date", "发展日期；end_date 不得早于它"),
    *((f"self_define{n}", f"cCusDefine{n}", _define_kind(n), "自定义项") for n in range(1, 17)),
    ("crelvendor", "cRelVendor", "code(20)", "Vendor；一个供应商只能关联一个客户"),
    ("bcusdomestic", "bCusDomestic", "bit", "国内"),
    ("bcusoverseas", "bCusOverseas", "bit", "国外"),
    ("bserviceattribute", "bServiceAttribute", "bit", ""),
    ("brequestsign", "bRequestSign", "bit", ""),
    ("discount_rate", "iCusDisRate", "num[0,100]", "折扣率 %"),
    ("fcusdiscountrate", "fCusDiscountRate", "num[0,100]", "现金折扣率 %"),
    ("fadvancepaymentratio", "fAdvancePaymentRatio", "num[0,100]", "预付款比例 %"),
    ("icustaxrate", "iCusTaxRate", "num[0,100)", "税率 %"),
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
    ref: str = ""

    @property
    def spec(self) -> str:
        """类型的写法（同表里的类型列）。"""
        bang = "!" if self.required else ""
        if self.kind in ("text", "code"):
            return f"{self.kind}{bang}({self.limit})"
        if self.lo is not None and self.hi is not None:
            return f"num[{self.lo},{self.hi}{')' if self.hi_open else ']'}"
        return self.kind


def _parse(row: tuple[str, str, str, str]) -> FieldKey:
    key, column, spec, ref = row
    text = _TEXT.fullmatch(spec)
    if text is not None:
        return FieldKey(key, column, text[1], int(text[3]), text[2] == "!", ref=ref)
    span = _RANGE.fullmatch(spec)
    if span is not None:
        return FieldKey(key, column, "num", lo=Decimal(span[1]), hi=Decimal(span[2]), hi_open=span[3] == ")", ref=ref)
    if spec not in _SIMPLE:
        raise ValueError(f"未知类型 {spec}")
    return FieldKey(key, column, spec, 20 if spec == "postcode" else 0, ref=ref)


KEYS: dict[str, FieldKey] = {row[0]: _parse(row) for row in _ROWS}
FIELD_KEYS = tuple(KEYS)


def _check_num(spec: FieldKey, value: str, label: str) -> None:
    if _NUM.fullmatch(value) is None:
        raise ValueError(f"{label} 必须是小数（可带负号，小数最多 8 位）")
    if len(value.replace("-", "").replace(".", "").lstrip("0")) > _MAX_DIGITS:
        raise ValueError(f"{label} 最多 {_MAX_DIGITS} 位有效数字")
    number = Decimal(value)
    if spec.lo is None or spec.hi is None:
        return
    if number < spec.lo or number > spec.hi or (spec.hi_open and number == spec.hi):
        raise ValueError(f"{label} 必须在 {spec.spec[3:]} 内")


def _check_date(value: str, label: str) -> None:
    check_ymd(value, label)
    if int(value[:4]) < _MIN_YEAR:
        raise ValueError(f"{label} 年份不能早于 {_MIN_YEAR}")


def _check_bit(value: str, label: str) -> None:
    if value not in ("0", "1"):
        raise ValueError(f"{label} 只能是 0 或 1")


def _check_text(spec: FieldKey, value: str, label: str) -> None:
    if spec.required and value.strip() == "":
        raise ValueError(f"{label} 不能为空")
    check_text(value.strip(), label, spec.limit, trimmed=False)


def _check_code(spec: FieldKey, value: str, label: str) -> None:
    if value == "" and spec.required:
        raise ValueError(f"{label} 不能为空")
    check_text(value, label, spec.limit)


def check_value(spec: FieldKey, value: str, label: str) -> None:
    """写入值：空串表示清空（必填键、bit 不能空）。"""
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
        _check_postcode(value.strip(), label)
    elif kind == "date":
        _check_date(value, label)
    elif kind == "int":
        _check_int(value, label)
    else:
        _check_num(spec, value, label)


def _check_postcode(value: str, label: str) -> None:
    if len(value) > 20 or _POSTCODE.fullmatch(value) is None:
        raise ValueError(f"{label} 最长 20 位，只能含 0-9、小写 a-z、-")


def _check_int(value: str, label: str) -> None:
    if _INT.fullmatch(value) is None or not -_INT32 <= int(value) < _INT32:
        raise ValueError(f"{label} 必须是 32 位整数")


def check_expect_value(spec: FieldKey, value: str, label: str) -> None:
    """expect 的当前值：只核对，写法宽松（库里的旧数据可以有首尾空白），编码类与桥一致。"""
    kind = spec.kind
    if kind == "code":
        check_text(value, label, spec.limit)
    elif kind in ("text", "postcode"):
        if len(value) > spec.limit:
            raise ValueError(f"{label} 最长 {spec.limit} 个字符")
    elif kind == "bit":
        _check_bit(value, label)
    elif value == "":
        return
    elif kind == "date":
        check_ymd(value, label)
    elif kind == "int":
        _check_int(value, label)
    elif _NUM_ANY.fullmatch(value) is None:
        raise ValueError(f"{label} 必须是小数")


def check_key_names(values: dict[str, str], label: str, allowed: tuple[str, ...]) -> None:
    unknown = sorted(set(values) - set(allowed))
    if unknown:
        raise ValueError(f"{label} 有不认识的键: {', '.join(unknown)}；可用 {'、'.join(allowed)}")


def check_date_order(fields: dict[str, str]) -> None:
    """同一请求里 end_date 与 seed_date 都给了值时，end_date 不得早于 seed_date。"""
    end, seed = fields.get("end_date", ""), fields.get("seed_date", "")
    if end and seed and end < seed:
        raise ValueError("fields.end_date 不能早于 fields.seed_date")


def _is_define(spec: FieldKey) -> bool:
    return spec.key.startswith("self_define")


def _describe(spec: FieldKey) -> str:
    extra = f"，{spec.ref}" if spec.ref else ""
    if spec.kind in ("text", "code"):
        return f"{spec.key}→{spec.column}（最长 {spec.limit}{'，必填' if spec.required else ''}{extra}）"
    return f"{spec.key}→{spec.column}（{spec.spec}{extra}）"


_GROUPS = (
    ("文本（首尾空白去掉，写空串清空；必填的键不能为空）", ("text",)),
    ("邮政编码（只含 0-9、小写 a-z、-，最长 20，写空串清空）", ("postcode",)),
    ("档案编码（首尾不能有空白，不分大小写，写入库里存的编码；写空串清空，必填的键不能为空）", ("code",)),
    ("日期 yyyy-MM-dd（年份不早于 1900，写空串清空）", ("date",)),
    ("0 / 1（不能清空）", ("bit",)),
    ("数值（小数最多 8 位，写空串清空）", ("num", "int")),
)


def key_help_lines() -> tuple[str, ...]:
    """按类型分组列出全部键 → 列，用于帮助文字。"""
    lines = []
    for title, kinds in _GROUPS:
        found = [_describe(spec) for spec in KEYS.values() if spec.kind in kinds and not _is_define(spec)]
        lines.append(f"{title}：{'；'.join(found)}")
    lines.append(
        "自定义项 self_define1…self_define16→cCusDefine1…cCusDefine16：1-3 文本（最长 20），4-6 文本（最长 60），"
        "7-10 文本（最长 120），"
        "11-12 整数，13-14 小数，15-16 日期；另按账套的用户自定义设置校验（档案型自定义项的值须存在于该档案）"
    )
    return tuple(lines)
