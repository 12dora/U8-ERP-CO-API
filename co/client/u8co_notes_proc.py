"""票据处理：/v1/notes/process。混入 U8CoClient。

op 为 settle 托收 / 结算（9A，处理号 PJJAR…）、discount 贴现（9D，PJTAR…）、endorse 背书冲应付（9E，PJBAR…）、
return 退回（9C，CLAR…，另生成一张应收单，响应带 r0_id、r0_code）。flag 缺省 AR；flag AP（应付票据）只收
settle（PJJAP…）和 return（CLAP…，生成应付单，响应带 p0_id、p0_code），第二级写入，默认关闭（桥 enableReplicatedWrites，
否则 403 feature_disabled），打开后只对测试账套开放（其他账套 403 test_account_only）。
处理日期就是登录日期 call.date。返回的处理号用于取消（arap_process_cancel）
和制单（arap_process_voucher，贴现可给 expense_code）。预演用 client.dry()（rollback 模式），幂等键用 client.keyed(key)。
这里只查请求的形状；票据状态、余额、子票区间是否可用、科目、期间等由桥再查一遍。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from co.client.u8co_arap_proc import _lines
from co.client.u8co_gl_arc import _common

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

NOTE_PROCESS_ROUTE = "/v1/notes/process"
NOTE_OPS = ("settle", "discount", "endorse", "return")
# 应付票据只开放的 op（同桥 NotesProcReq.ApNotesOps）。
AP_NOTE_OPS = ("settle", "return")
_ID_MAX = 2147483647
_SERIAL_MAX = 999999999999999
_AMOUNT_MAX = 1000000000000
_AP_TYPES = ("01", "02", "P0")


@dataclass(frozen=True)
class NoteProcess:
    """一次票据处理。note 是票据号（字符串）或票据主键 Auto_ID（整数）；amount 省略时背书取 ap_lines 合计，其余取票据余额。
    sub_start / sub_end 是分包票据的子票区间（成对，每个号 0.01 元）。结算、贴现须给 bank_code；贴现另收 expense、
    interest（金额，≥0）和 rate（%）；背书须给 vendor 和 ap_lines（1 到 50 项 {type, id, line_id?, amount}，01 / 02 / P0）；
    退回只收公共字段（note、amount、sub_start / sub_end、digest）。flag 为 AP 时是应付票据，只收 settle、return。"""

    op: str
    note: int | str
    amount: float | None = None
    sub_start: int | None = None
    sub_end: int | None = None
    bank_code: str | None = None
    bank_name: str | None = None
    expense: float | None = None
    interest: float | None = None
    rate: float | None = None
    vendor: str | None = None
    ap_lines: Any = None
    digest: str | None = None
    flag: str = "AR"


def _text(value: object, name: str, max_len: int) -> str:
    if type(value) is not str or not value.strip() or len(value.strip()) > max_len:
        raise ValueError(f"{name} 必须是 1 到 {max_len} 个字符的字符串")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{name} 不能含控制字符")
    return value


def _money(value: object, name: str, zero: bool) -> float:
    low_ok = type(value) in (int, float) and (value >= 0 if zero else value > 0)
    if not low_ok or value > _AMOUNT_MAX:
        raise ValueError(f"{name} 必须{'不小于' if zero else '大于'} 0、不超过 1000000000000")
    if Decimal(str(value)).as_tuple().exponent < -2:
        raise ValueError(f"{name} 最多两位小数")
    return value


def _serial(value: object, name: str) -> int:
    if type(value) is not int or not 1 <= value <= _SERIAL_MAX:
        raise ValueError(f"{name} 必须是 1 到 999999999999999 的整数")
    return value


def _note(value: object) -> int | str:
    if type(value) is int:
        if not 1 <= value <= _ID_MAX:
            raise ValueError("票据主键必须是 1 到 2147483647 的整数")
        return value
    return _text(value, "note", 120)


def _range(fields: dict[str, Any], ask: NoteProcess) -> None:
    if ask.sub_start is None and ask.sub_end is None:
        return
    if ask.sub_start is None or ask.sub_end is None:
        raise ValueError("sub_start 与 sub_end 要同时给出")
    start, end = _serial(ask.sub_start, "sub_start"), _serial(ask.sub_end, "sub_end")
    if start > end:
        raise ValueError("sub_start 不能大于 sub_end")
    if ask.amount is not None and Decimal(end - start + 1) / 100 != Decimal(str(ask.amount)):
        raise ValueError("amount 与子票区间的金额不一致（区间每个号 0.01 元）")
    fields["sub_start"], fields["sub_end"] = start, end


def _bank(fields: dict[str, Any], ask: NoteProcess) -> None:
    fields["bank_code"] = _text(ask.bank_code, "bank_code", 40)
    if ask.bank_name is not None:
        fields["bank_name"] = _text(ask.bank_name, "bank_name", 100)


def _discount(fields: dict[str, Any], ask: NoteProcess) -> None:
    for name in ("expense", "interest"):
        value = getattr(ask, name)
        if value is not None:
            fields[name] = _money(value, name, zero=True)
    if ask.rate is not None:
        rate = ask.rate
        if type(rate) not in (int, float) or not 0 <= rate <= 100 or Decimal(str(rate)).as_tuple().exponent < -6:
            raise ValueError("rate 必须在 0 到 100 之间、最多六位小数")
        fields["rate"] = rate


def _endorse(fields: dict[str, Any], ask: NoteProcess) -> None:
    fields["vendor"] = _text(ask.vendor, "vendor", 20)
    lines = _lines(ask.ap_lines, "ap_lines")
    if any(line["type"] not in _AP_TYPES for line in lines):
        raise ValueError("ap_lines[].type 只能是 01、02、P0（付款单 49 不支持背书）")
    total = sum((Decimal(str(line["amount"])) for line in lines), Decimal(0))
    if ask.amount is not None and Decimal(str(ask.amount)) != total:
        raise ValueError("ap_lines 的金额合计必须等于背书金额 amount")
    fields["ap_lines"] = lines


def _only(ask: NoteProcess) -> None:
    # 各 op 专用的字段，其他 op 给了就报错（同桥）。
    used = {
        "bank_code": ("settle", "discount"),
        "bank_name": ("settle", "discount"),
        "expense": ("discount",),
        "interest": ("discount",),
        "rate": ("discount",),
        "vendor": ("endorse",),
        "ap_lines": ("endorse",),
    }
    for name, ops in used.items():
        if ask.op not in ops and getattr(ask, name) is not None:
            raise ValueError(f"{name} 不用于 op {ask.op}")


def note_process_fields(call: U8Call, ask: NoteProcess) -> dict[str, Any]:
    # 键顺序：公共字段、flag、op、note、amount、sub_start、sub_end、按 op 的字段、digest。值为 None 的可选字段不发。
    if ask.op not in NOTE_OPS:
        raise ValueError("op 只能是 settle（托收 / 结算）、discount（贴现）、endorse（背书）或 return（退回）")
    if ask.flag not in ("AR", "AP"):
        raise ValueError("flag 只能是 AR（应收票据）或 AP（应付票据）")
    if ask.flag == "AP" and ask.op not in AP_NOTE_OPS:
        raise ValueError("应付票据只支持结算（settle）、退回（return），贴现、背书请在 U8 客户端处理")
    _only(ask)
    fields = _common(call)
    fields["flag"] = ask.flag
    fields["op"] = ask.op
    fields["note"] = _note(ask.note)
    if ask.amount is not None:
        fields["amount"] = _money(ask.amount, "amount", zero=False)
    _range(fields, ask)
    if ask.op == "endorse":
        _endorse(fields, ask)
    elif ask.op != "return":
        _bank(fields, ask)
    if ask.op == "discount":
        _discount(fields, ask)
    if ask.digest is not None:
        fields["digest"] = _text(ask.digest, "digest", 120)
    return fields


class U8CoNotesProcMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def note_process(self, call: U8Call, ask: NoteProcess) -> dict[str, Any]:
        """处理一张票据，返回处理号 cancel_no、style、note、amount、remaining（贴现另有 net，背书另有 ap_rows，
        退回另有生成的应收单 r0_id、r0_code，应付票据是应付单 p0_id、p0_code）。
        票据已处理完、余额不足、收款单未审核、期间已结账等由桥返回 409。"""
        return self.call(NOTE_PROCESS_ROUTE, note_process_fields(call, ask))
