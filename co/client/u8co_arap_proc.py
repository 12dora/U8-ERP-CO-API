"""应收应付处理：应收冲应付 / 应付冲应收（/v1/arap/transfer）、并账（/v1/arap/merge）、红票对冲（/v1/arap/red_offset）、
取消处理（/v1/arap/process/cancel）、处理制单（/v1/arap/process/voucher）、汇兑损益及取消（/v1/arap/exchange_gain[/cancel]，第二级写入，
默认关闭（桥 enableReplicatedWrites，否则 403 feature_disabled），打开后仅测试账套）。
混入 U8CoClient。

处理的日期就是登录日期 call.date。每次处理返回处理号 cancel_no（YCFAP / FCYAR 转账、BZAR / BZAP 并账、HRAR / HPAP 红票对冲、
SYRAR / SYPAP 汇兑损益），取消、制单都按它。预演用 client.dry()（桥 rollback 模式；处理制单为 validate），幂等键用 client.keyed(key)。
这里只查请求的形状（字段、类型、条数）；单据类型、余额、期间、处理号前缀与 flag 的对应等由桥再查一遍。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

ARAP_TRANSFER_ROUTE = "/v1/arap/transfer"
ARAP_MERGE_ROUTE = "/v1/arap/merge"
ARAP_RED_OFFSET_ROUTE = "/v1/arap/red_offset"
ARAP_PROC_CANCEL_ROUTE = "/v1/arap/process/cancel"
ARAP_PROC_VOUCHER_ROUTE = "/v1/arap/process/voucher"
ARAP_EXGAIN_ROUTE = "/v1/arap/exchange_gain"
ARAP_EXGAIN_CANCEL_ROUTE = "/v1/arap/exchange_gain/cancel"
ARAP_PROC_ROUTES = (
    ARAP_TRANSFER_ROUTE, ARAP_MERGE_ROUTE, ARAP_RED_OFFSET_ROUTE, ARAP_PROC_CANCEL_ROUTE,
    ARAP_PROC_VOUCHER_ROUTE, ARAP_EXGAIN_ROUTE, ARAP_EXGAIN_CANCEL_ROUTE,
)
ARAP_FLAGS = ("AR", "AP")
# 每一侧单据行的上限、处理制单一次的批次号上限、汇兑损益的往来单位 / 处理号上限（同桥）。
LINES_MAX = 50
VOUCHER_NOS_MAX = 50
EXGAIN_LIST_MAX = 200
CASH_ITEMS_MAX = 20
_LINE_KEYS = ("type", "id", "line_id", "amount")
_CANCEL_NO = re.compile(r"^[A-Z]{4,5}[0-9]{1,20}\Z")


@dataclass(frozen=True)
class ArapTransfer:
    """应收冲应付（flag=AR）/ 应付冲应收（flag=AP）。ar_lines、ap_lines 各 1 到 50 项 {type, id, line_id?, amount}：
    应收一侧 26 / 27 / R0 / 48，应付一侧 01 / 02 / P0 / 49；id 是单据号，amount 是原币正数，两侧合计必须相等。
    currency 缺省本位币；digest 缺省用 U8 的摘要。"""

    flag: str
    customer: str
    vendor: str
    ar_lines: Any
    ap_lines: Any
    currency: str | None = None
    digest: str | None = None


@dataclass(frozen=True)
class ArapMerge:
    """并账：把 lines 里单据（行）在 source 名下的余额并到 target。lines 1 到 50 项 {type, id, line_id?, amount?}，
    应收 26 / 27 / R0，应付 01 / 02 / P0；amount 省略即并入全部余额。请求里是 from、to。"""

    flag: str
    source: str
    target: str
    lines: Any
    digest: str | None = None


@dataclass(frozen=True)
class ArapRedOffset:
    """红票对冲：同一往来单位 partner 的红字单据 red 与蓝字单据 blue 对冲，两侧各 1 到 50 项 {type, id, line_id?, amount}，
    合计必须相等。currency 缺省本位币。"""

    flag: str
    partner: str
    red: Any
    blue: Any
    currency: str | None = None
    digest: str | None = None


@dataclass(frozen=True)
class ProcVoucher:
    """处理制单：同一类处理的 1 到 50 个处理号合成一张转账凭证。sign 缺省「转」，voucher_date 缺省取各批次最晚的处理日期，
    pl_code 只用于汇兑损益（必填，汇兑损益科目编码）；expense_code 只用于应收票据贴现（PJTAR，可省略，取基本科目设置）。
    cash_items 是 {科目编码: 现金流量项目编码}，最多 20 个：按数据来源推不出现金流量项目（409）时指定。"""

    flag: str
    cancel_nos: Any
    sign: str | None = None
    voucher_date: str | None = None
    digest: str | None = None
    pl_code: str | None = None
    expense_code: str | None = None
    cash_items: Any = None


@dataclass(frozen=True)
class ExchangeGain:
    """汇兑损益：currency 外币名称；rate 缺省取该期调整汇率；partners 缺省全部往来单位；settle_cleared 缺省 true
    （原币已结清、只剩本币尾差的单据一并结清）。"""

    flag: str
    currency: str
    rate: float | None = None
    partners: Any = None
    settle_cleared: bool | None = None


def _auth(call: U8Call) -> dict[str, Any]:
    # 键顺序同其他路由：公共字段在前。password 由 call() 换成 password_enc。
    return {
        "acc": call.acc,
        "year": call.year,
        "operator": call.operator,
        "password": call.password,
        "date": call.date,
    }


def _flag(flag: object) -> str:
    if flag not in ARAP_FLAGS:
        raise ValueError("flag 只能是 AR 或 AP")
    return str(flag)


def _text(value: object, name: str) -> str:
    if type(value) is not str or value.strip() == "":
        raise ValueError(f"{name} 必须是非空字符串")
    return value


def _put(fields: dict[str, Any], name: str, value: object) -> None:
    # 可选的文本字段：None 不发。
    if value is not None:
        fields[name] = _text(value, name)


def _is_num(value: object) -> bool:
    return type(value) in (int, float)


def _line(row: object, name: str, need_amount: bool) -> dict[str, Any]:
    if not isinstance(row, dict) or not set(row) <= set(_LINE_KEYS):
        raise ValueError(f"{name} 的每一项只能有 type、id、line_id、amount")
    out = {"type": _text(row.get("type"), f"{name}[].type"), "id": _text(row.get("id"), f"{name}[].id")}
    line_id = row.get("line_id")
    if line_id is not None:
        if type(line_id) is not int or line_id < 1:
            raise ValueError(f"{name}[].line_id 必须是正整数（发票行主键）")
        out["line_id"] = line_id
    amount = row.get("amount")
    if amount is None and not need_amount:
        return out
    if not _is_num(amount) or amount <= 0:
        raise ValueError(f"{name}[].amount 必须是大于 0 的数（原币）")
    out["amount"] = amount
    return out


def _lines(rows: object, name: str, need_amount: bool = True) -> list[dict[str, Any]]:
    if isinstance(rows, (str, bytes, dict)) or not isinstance(rows, (list, tuple)) or not 1 <= len(rows) <= LINES_MAX:
        raise ValueError(f"{name} 必须是 1 到 {LINES_MAX} 项")
    return [_line(row, name, need_amount) for row in rows]


def _codes(values: object, name: str, most: int, shape: re.Pattern[str] | None = None) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)) or not 1 <= len(values) <= most:
        raise ValueError(f"{name} 必须是 1 到 {most} 项")
    out = [_text(value, name) for value in values]
    if shape is not None and any(shape.fullmatch(value) is None for value in out):
        raise ValueError(f"{name} 里必须是处理号（字母前缀后接数字，如 YCFAP000000000001）")
    if len(set(out)) != len(out):
        raise ValueError(f"{name} 有重复项")
    return out


def transfer_fields(call: U8Call, ask: ArapTransfer) -> dict[str, Any]:
    # 键顺序：公共字段、flag、customer、vendor、currency、ar_lines、ap_lines、digest。值为 None 的可选字段不发。
    fields = _auth(call)
    fields["flag"] = _flag(ask.flag)
    fields["customer"] = _text(ask.customer, "customer")
    fields["vendor"] = _text(ask.vendor, "vendor")
    _put(fields, "currency", ask.currency)
    fields["ar_lines"] = _lines(ask.ar_lines, "ar_lines")
    fields["ap_lines"] = _lines(ask.ap_lines, "ap_lines")
    _put(fields, "digest", ask.digest)
    return fields


def merge_fields(call: U8Call, ask: ArapMerge) -> dict[str, Any]:
    fields = _auth(call)
    fields["flag"] = _flag(ask.flag)
    fields["from"] = _text(ask.source, "from")
    fields["to"] = _text(ask.target, "to")
    if fields["from"].strip().casefold() == fields["to"].strip().casefold():
        raise ValueError("from 与 to 不能是同一个往来单位")
    fields["lines"] = _lines(ask.lines, "lines", need_amount=False)
    _put(fields, "digest", ask.digest)
    return fields


def red_offset_fields(call: U8Call, ask: ArapRedOffset) -> dict[str, Any]:
    fields = _auth(call)
    fields["flag"] = _flag(ask.flag)
    fields["partner"] = _text(ask.partner, "partner")
    _put(fields, "currency", ask.currency)
    fields["red"] = _lines(ask.red, "red")
    fields["blue"] = _lines(ask.blue, "blue")
    _put(fields, "digest", ask.digest)
    return fields


def proc_cancel_fields(call: U8Call, flag: str, cancel_no: str) -> dict[str, Any]:
    fields = _auth(call)
    fields["flag"] = _flag(flag)
    if type(cancel_no) is not str or _CANCEL_NO.fullmatch(cancel_no) is None:
        raise ValueError("cancel_no 必须是处理号（字母前缀后接数字，如 YCFAP000000000001）")
    fields["cancel_no"] = cancel_no
    return fields


def cash_items_field(value: object) -> dict[str, str]:
    """cash_items 的形状：非空对象，最多 20 项，键（科目编码）和值（项目编码）都是不含空白的非空字符串。长度、项目是否存在由桥查。"""
    if not isinstance(value, dict) or not 1 <= len(value) <= CASH_ITEMS_MAX:
        raise ValueError(f"cash_items 必须是 1 到 {CASH_ITEMS_MAX} 项的 {{科目编码: 现金流量项目编码}}")
    for key, item in value.items():
        if any(type(text) is not str or text.strip() == "" or any(ch.isspace() for ch in text.strip()) for text in (key, item)):
            raise ValueError("cash_items 的科目编码和项目编码必须是不含空白的非空字符串")
    return {key.strip(): item.strip() for key, item in value.items()}


def proc_voucher_fields(call: U8Call, ask: ProcVoucher) -> dict[str, Any]:
    # 键顺序：公共字段、flag、cancel_nos、sign、voucher_date、digest、pl_code、expense_code、cash_items。
    fields = _auth(call)
    fields["flag"] = _flag(ask.flag)
    fields["cancel_nos"] = _codes(ask.cancel_nos, "cancel_nos", VOUCHER_NOS_MAX, _CANCEL_NO)
    for name in ("sign", "voucher_date", "digest", "pl_code", "expense_code"):
        _put(fields, name, getattr(ask, name))
    if ask.cash_items is not None:
        fields["cash_items"] = cash_items_field(ask.cash_items)
    return fields


def exchange_gain_fields(call: U8Call, ask: ExchangeGain) -> dict[str, Any]:
    # 键顺序：公共字段、flag、currency、rate、partners、settle_cleared。
    fields = _auth(call)
    fields["flag"] = _flag(ask.flag)
    fields["currency"] = _text(ask.currency, "currency")
    if ask.rate is not None:
        if not _is_num(ask.rate) or ask.rate <= 0:
            raise ValueError("rate 必须是大于 0 的数")
        fields["rate"] = ask.rate
    if ask.partners is not None:
        fields["partners"] = _codes(ask.partners, "partners", EXGAIN_LIST_MAX)
    if ask.settle_cleared is not None:
        if type(ask.settle_cleared) is not bool:
            raise ValueError("settle_cleared 必须是布尔值")
        fields["settle_cleared"] = ask.settle_cleared
    return fields


def exchange_gain_cancel_fields(call: U8Call, flag: str, cancel_nos: object) -> dict[str, Any]:
    # cancel_nos 为 None：按登记日期（call.date）取消当天全部未制单的汇兑损益。
    fields = _auth(call)
    fields["flag"] = _flag(flag)
    if cancel_nos is not None:
        fields["cancel_nos"] = _codes(cancel_nos, "cancel_nos", EXGAIN_LIST_MAX, _CANCEL_NO)
    return fields


class U8CoArapProcMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def arap_transfer(self, call: U8Call, ask: ArapTransfer) -> dict[str, Any]:
        """应收冲应付 / 应付冲应收，返回 cancel_no、style、ar_rows、ap_rows、amount。单据未审核、余额不足、币种不一致、
        期间已结账等由桥返回 409。"""
        return self.call(ARAP_TRANSFER_ROUTE, transfer_fields(call, ask))

    def arap_merge(self, call: U8Call, ask: ArapMerge) -> dict[str, Any]:
        """并账，返回 cancel_no、rows。单据不属于 from、余额不足、期间已结账等由桥返回 409。"""
        return self.call(ARAP_MERGE_ROUTE, merge_fields(call, ask))

    def arap_red_offset(self, call: U8Call, ask: ArapRedOffset) -> dict[str, Any]:
        """红票对冲，返回 cancel_no、red_rows、blue_rows、amount。U8 拒绝时 409 u8_rejected。"""
        return self.call(ARAP_RED_OFFSET_ROUTE, red_offset_fields(call, ask))

    def arap_process_cancel(self, call: U8Call, flag: str, cancel_no: str) -> dict[str, Any]:
        """取消一批应收冲应付 / 应付冲应收 / 并账 / 红票对冲 / 应收票据处理（PJJAR / PJTAR / PJBAR / CLAR），按处理号；
        应付票据的结算、退回（PJJAP / CLAP）仅测试账套（403 feature_disabled / test_account_only）。
        已制单（先取消制单）、期间已结账、之后还有处理等由桥返回 409。汇兑损益用 arap_exchange_gain_cancel。"""
        return self.call(ARAP_PROC_CANCEL_ROUTE, proc_cancel_fields(call, flag, cancel_no))

    def arap_process_voucher(self, call: U8Call, ask: ProcVoucher) -> dict[str, Any]:
        """处理制单：同一类处理的批次合成一张转账凭证。取消制单用 arap/voucher/delete。汇兑损益批次、坏账批次、
        应付票据批次（PJJAP / CLAP）的制单仅测试账套（403 feature_disabled / test_account_only）。"""
        return self.call(ARAP_PROC_VOUCHER_ROUTE, proc_voucher_fields(call, ask))

    def arap_exchange_gain(self, call: U8Call, ask: ExchangeGain) -> dict[str, Any]:
        """汇兑损益（登记日期 call.date），每个往来单位、单据一个处理号；返回 rate、batches、total。仅测试账套
        （403 feature_disabled / test_account_only）；本期没有调整汇率又没给 rate 时 409。"""
        return self.call(ARAP_EXGAIN_ROUTE, exchange_gain_fields(call, ask))

    def arap_exchange_gain_cancel(self, call: U8Call, flag: str, cancel_nos: object = None) -> dict[str, Any]:
        """取消汇兑损益：给 cancel_nos（1 到 200 个 SYRAR / SYPAP 处理号），或省略即取消登记日期 call.date 当天全部未制单的。
        仅测试账套（403 feature_disabled / test_account_only）；已制单、期间已结账、之后还有处理时 409。"""
        return self.call(ARAP_EXGAIN_CANCEL_ROUTE, exchange_gain_cancel_fields(call, flag, cancel_nos))
