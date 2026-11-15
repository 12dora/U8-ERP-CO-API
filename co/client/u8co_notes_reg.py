"""票据登记、删除（应收票据、应付票据）：/v1/notes/create、/v1/notes/delete。混入 U8CoClient。

登记写票据，再生成对应的收款单（应付票据是付款单，都未审核）；删除只收还没处理的票据，连同它的收付款单一起删。
给 sub_start / sub_end 时登记分包票据（子票区间每个号 0.01 元，张数 × 0.01 须等于票面）。
flag 缺省 AR（应收票据，往来单位给 customer）；AP 是应付票据（往来单位给 vendor，customer 写 None，应付票据的
pjkm 未设置时须给 note_km），第二级写入，默认关闭（桥 enableReplicatedWrites，
否则 403 feature_disabled），打开后只对测试账套开放，其他账套 403 test_account_only。
预演用 client.dry()（validate 模式，只做检查），幂等键用 client.keyed(key)。
这里只查请求的形状；日期与登录日期、期间、票据号是否已存在、档案等由桥再查一遍。
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from co.client.u8co_notes_proc import _serial
from co.client.u8co_gl_arc import _common

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

NOTE_CREATE_ROUTE = "/v1/notes/create"
NOTE_DELETE_ROUTE = "/v1/notes/delete"
_ID_MAX = 2147483647
_AMOUNT_MAX = 1000000000000
# 可选文本字段及长度上限（同桥 NotesRegReq）；None 不发。
_OPTIONAL = (
    ("drawer", 60),
    ("drawer_bank", 100),
    ("person", 20),
    ("receive_bank", 100),
    ("receive_account", 50),
    ("km", 40),
    ("note_km", 40),
    ("digest", 120),
)
_FLAGS = ("AR", "AP")


@dataclass(frozen=True)
class NoteRegistration:
    """一张要登记的票据。日期是 yyyy-MM-dd；amount 是本位币票面金额，最多两位小数。
    sub_start / sub_end 成对给出时是分包票据的子票区间，(止号 − 起号 + 1) × 0.01 须等于 amount。
    flag 为 AR（缺省）时给 customer；为 AP（应付票据）时 customer 写 None、给 vendor。"""

    note_no: str
    settle_code: str
    amount: float
    sign_date: str
    receipt_date: str
    expire_date: str
    customer: str | None
    dept: str
    receiver: str
    drawer: str | None = None
    drawer_bank: str | None = None
    person: str | None = None
    receive_bank: str | None = None
    receive_account: str | None = None
    km: str | None = None
    digest: str | None = None
    sub_start: int | None = None
    sub_end: int | None = None
    flag: str = "AR"
    vendor: str | None = None
    note_km: str | None = None


def _flag(value: object) -> str:
    if value not in _FLAGS:
        raise ValueError("flag 只能是 AR（应收票据）或 AP（应付票据）")
    return str(value)


def _partner(fields: dict[str, Any], ask: NoteRegistration) -> None:
    # 应收票据给 customer、应付票据给 vendor，另一个必须是 None（同桥 NotesRegReq）。
    need, other = ("vendor", "customer") if ask.flag == "AP" else ("customer", "vendor")
    if getattr(ask, other) is not None:
        raise ValueError(f"{other} 只用于{'应付票据（flag AP）' if other == 'vendor' else '应收票据（flag AR）'}")
    fields[need] = _text(getattr(ask, need), need, 20)


def _text(value: object, name: str, max_len: int) -> str:
    if type(value) is not str or not value.strip() or len(value.strip()) > max_len:
        raise ValueError(f"{name} 必须是 1 到 {max_len} 个字符的字符串")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{name} 不能含控制字符")
    return value


def _day(value: object, name: str) -> datetime.date:
    if type(value) is not str or len(value) != 10:
        raise ValueError(f"{name} 必须是 yyyy-MM-dd")
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} 必须是 yyyy-MM-dd") from None


def _amount(value: object) -> float:
    if type(value) not in (int, float) or not 0 < value <= _AMOUNT_MAX:
        raise ValueError("amount 必须大于 0、不超过 1000000000000")
    if Decimal(str(value)).as_tuple().exponent < -2:
        raise ValueError("amount 最多两位小数")
    return value


def _range(fields: dict[str, Any], ask: NoteRegistration) -> None:
    if ask.sub_start is None and ask.sub_end is None:
        return
    if ask.sub_start is None or ask.sub_end is None:
        raise ValueError("sub_start 与 sub_end 要同时给出")
    start, end = _serial(ask.sub_start, "sub_start"), _serial(ask.sub_end, "sub_end")
    if start > end:
        raise ValueError("sub_start 不能大于 sub_end")
    if Decimal(end - start + 1) / 100 != Decimal(str(ask.amount)):
        raise ValueError("子票区间与票面金额不符（区间每个号 0.01 元，张数 × 0.01 须等于 amount）")
    fields["sub_start"], fields["sub_end"] = start, end


def note_create_fields(call: U8Call, ask: NoteRegistration) -> dict[str, Any]:
    # 键顺序：公共字段、flag、必填字段、可选字段、sub_start、sub_end。
    fields = _common(call)
    fields["flag"] = _flag(ask.flag)
    fields["note_no"] = _text(ask.note_no, "note_no", 60)
    fields["settle_code"] = _text(ask.settle_code, "settle_code", 3)
    fields["amount"] = _amount(ask.amount)
    sign = _day(ask.sign_date, "sign_date")
    if _day(ask.receipt_date, "receipt_date") < sign:
        raise ValueError("收票日期不能早于签发日期")
    if _day(ask.expire_date, "expire_date") < sign:
        raise ValueError("到期日不能早于签发日期")
    for name in ("sign_date", "receipt_date", "expire_date"):
        fields[name] = getattr(ask, name)
    _partner(fields, ask)
    fields["dept"] = _text(ask.dept, "dept", 12)
    fields["receiver"] = _text(ask.receiver, "receiver", 50)
    for name, max_len in _OPTIONAL:
        value = getattr(ask, name)
        if value is not None:
            fields[name] = _text(value, name, max_len)
    _range(fields, ask)
    return fields


def note_delete_fields(call: U8Call, key: int | str, flag: str = "AR") -> dict[str, Any]:
    # key 是整数时按 id（AP_Note.Auto_ID），是字符串时按票据号。
    fields = _common(call)
    fields["flag"] = _flag(flag)
    if type(key) is int:
        if not 1 <= key <= _ID_MAX:
            raise ValueError("票据 id 必须是 1 到 2147483647 的整数")
        fields["id"] = key
        return fields
    fields["note_no"] = _text(key, "note_no", 60)
    return fields


class U8CoNotesRegMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def note_create(self, call: U8Call, ask: NoteRegistration) -> dict[str, Any]:
        """登记票据并生成收款单（应付票据是付款单），返回 note_id、note_no、receipt_id、receipt_code。任何一步失败整笔回滚；
        提交后回读不到是 504 outcome_unknown（写明票据号，先查询再处理）。"""
        return self.call(NOTE_CREATE_ROUTE, note_create_fields(call, ask))

    def note_delete(self, call: U8Call, key: int | str, flag: str = "AR") -> dict[str, Any]:
        """删除还没处理的票据及其收付款单，返回 deleted。flag 为 AP 时是应付票据。已处理、单据已审核等由桥返回 409。"""
        return self.call(NOTE_DELETE_ROUTE, note_delete_fields(call, key, flag))
