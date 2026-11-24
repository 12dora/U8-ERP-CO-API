"""销售侧档案：收付款协议 pay_agreement（AA_Agreement，可新增、修改、删除，受控 SQL）、
客户开票单位 customer_invoice_company（sa_invoicecustomers，只读）；自定义项档案、客户存货对照修改的字段范围。

get、list 都按表列名返回；开票单位的编码是两段 <客户编码>:<开票单位编码>（桥 ArcPair），表没有 rowversion。
写入的格式在这里先校验（桥 ArcAgreement、ArcPairEdit 同一套规则），库里的状态由桥查。
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

PAY_AGREEMENT = "pay_agreement"
INVOICE_COMPANY = "customer_invoice_company"
# 协议编码 AA_Agreement.cCode nvarchar(10)；开票单位的两段都是客户编码（最长 20）。
SA_CODE_MAX = {PAY_AGREEMENT: 10, INVOICE_COMPANY: 41}
SA_PAIR = {INVOICE_COMPANY: (20, 20, "<客户编码>:<开票单位编码>")}
# 开票单位表没有 rowversion，列表不支持 changed_since。
SA_NO_UFTS = frozenset({INVOICE_COMPANY})

_INT_MAX = 2147483647
_DECIMAL_MAX = 999999999999999999
# fields 的标签就是表列名（不分大小写）；条款列都是整数（dblZQNum 是 decimal(18,0)）。
_TERMS = ("iLZYJ", "iLZFS", "iMon", "iDay1", "iDay2", "iDay3", "iLZJZ", "iPLNum", "iZQ", "iGDSZR1", "iGDSZR2", "iGDSZR3")
_AGREEMENT_TAGS = {tag.lower(): tag for tag in ("cName", "cMemo", *_TERMS, "dblZQNum", "dEDate")}
# 自定义项档案、客户存货对照的修改只收这些标签（编码两段不能改，桥 ArcPairEdit）。
_EDIT_TAGS = {
    "user_define": ("alias", "barcode"),
    "customer_inventory": (
        "ccusinvcode", "ccusinvname", "iteststyle", "idtmethod", "fdtrate", "fdtnum", "cdtunit", "idtstyle",
        "idtlevel", "cdtaql", "boutinvdt", "crulecode",
    ),
}
_WHOLE = re.compile(r"[+-]?\d{1,19}")


def _whole(value: Any, limit: int) -> bool:
    """整数：JSON 整数或十进制整数字符串（不收小数、布尔），绝对值不超过 limit（桥 ArcCol.Whole）。"""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return abs(value) <= limit
    return isinstance(value, str) and _WHOLE.fullmatch(value.strip()) is not None and abs(int(value)) <= limit


def _day_ok(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _agreement_field(tag: str, value: Any) -> str | None:
    """一个协议字段不合格时返回原因，合格返回 None。"""
    if tag == "cName":
        ok = isinstance(value, str) and 0 < len(value.strip()) <= 60
        return None if ok else "cName 必须是 1 到 60 个字符且不能全是空白"
    if tag == "cMemo":
        return None if isinstance(value, str) and len(value.strip()) <= 255 else "cMemo 最长 255 个字符"
    if tag == "dEDate":
        day = value.strip() if isinstance(value, str) else None
        return None if day is not None and (day == "" or _day_ok(day)) else "dEDate 必须是 yyyy-mm-dd 日期，空串清除停用日期"
    limit = _DECIMAL_MAX if tag == "dblZQNum" else _INT_MAX
    return None if _whole(value, limit) else f"{tag} 必须是整数"


def check_sa_write(archive: str, fields: dict[str, Any], template: str | None, creating: bool) -> None:
    """收付款协议写入的格式（桥 ArcAgreement.Check）；自定义项档案、客户存货对照的修改只收可改的标签（桥 ArcPairEdit）。"""
    if archive in _EDIT_TAGS and not creating:
        allowed = set(_EDIT_TAGS[archive])
        unknown = sorted(tag for tag in fields if tag.lower() not in allowed)
        if unknown:
            raise ValueError(f"{archive} 修改只能写 {', '.join(_EDIT_TAGS[archive])}，未知字段 " + ", ".join(unknown))
        return
    if archive != PAY_AGREEMENT:
        return
    if template is not None:
        raise ValueError("收付款协议不支持 template")
    canon = {_AGREEMENT_TAGS.get(tag.lower()): value for tag, value in fields.items()}
    if None in canon:
        bad = sorted(tag for tag in fields if tag.lower() not in _AGREEMENT_TAGS)
        raise ValueError("pay_agreement 只能写 " + "、".join(_AGREEMENT_TAGS.values()) + "，未知字段 " + ", ".join(bad))
    if creating and "cName" not in canon:
        raise ValueError("新增收付款协议必须给 cName（协议名称）")
    for tag, value in canon.items():
        problem = _agreement_field(str(tag), value)
        if problem is not None:
            raise ValueError(problem)
