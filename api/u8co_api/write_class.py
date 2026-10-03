"""写入分类（写入策略）：每条写路由映射到唯一的（type, op），与桥 WriteClass.cs 同一张表。

type：单据类路由（vouchers/*、workflow/*、旧版审核路由）取单据类型名；不带 type 的路由族用族名：
gl、archives、arap、notes、openings、periods、ia。
op：封闭词表（write_policy_parse.OP_NAMES）。按 action 区分的路由逐值映射，未知或缺少的 action 记 other。
取消类操作（取消核销、取消制单、取消处理、取消汇兑损益、取消记账）一律记 other：放行 writeoff / voucher / process / post
不连带放行取消。co_access 里的每条写路由都要在这里登记一行（tests/test_write_policy_class.py 逐条核对）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# 一行规则：(type, op)。type 为 None 表示取请求体的 type；op 为 dict 时按请求体 action 映射，否则是固定的 op。
_VERIFY = {"verify": "verify", "unverify": "unverify", "arap_verify": "verify", "arap_unverify": "unverify"}
_PLAIN_VERIFY = {"verify": "verify", "unverify": "unverify"}
_POST = {"post": "post", "unpost": "other"}
_WORKFLOW = ("submit", "withdraw", "approve", "disagree", "return", "abandon", "resubmit")

RULES: dict[str, tuple[str | None, str | dict[str, str]]] = {
    # 单据：审核 / 弃审（应收应付的发票复核也记 verify / unverify）、新增、修改、删除、关闭 / 打开、生单、锁定 / 解锁。
    "vouchers/verify": (None, _VERIFY),
    "vouchers/create": (None, "create"),
    "vouchers/update": (None, "update"),
    "vouchers/delete": (None, "delete"),
    "vouchers/close": (None, {"close": "close", "open": "open"}),
    "vouchers/generate": (None, "generate"),
    "vouchers/lock": (None, {"lock": "lock", "unlock": "unlock"}),
    # 公司间生成买方单据（只在 API）：type 取请求体的买方单据类型，记 generate；
    # 实际写入走买方账套的 vouchers/generate，写入策略按那一次调用检查，桥 WriteClass.cs 没有这一行。
    "intercompany/generate_buyer": (None, "generate"),
    # 审批流：七个操作都记 workflow，type 是单据类型。
    **{"workflow/" + name: (None, "workflow") for name in _WORKFLOW},
    # 旧版审核路由。
    "sale-orders/verify": ("sale_order", _PLAIN_VERIFY),
    "dispatches/verify": ("dispatch", _PLAIN_VERIFY),
    # 总账凭证：新增、修改、删除、审核、取消审核、记账照名；作废、取消作废、出纳签字、取消签字、取消记账记 other。
    **{"gl/vouchers/" + name: ("gl", name) for name in ("create", "update", "delete", "verify", "unverify", "post")},
    **{"gl/vouchers/" + name: ("gl", "other") for name in ("void", "unvoid", "sign", "unsign", "unpost")},
    # 红字冲销：填制一张红字凭证，功能权限同填制，记 create。
    "gl/vouchers/reverse": ("gl", "create"),
    # 期间损益结转、自定义转账：按定义生成结转凭证，记 voucher（同制单）；放行手工填制不连带放行自动转账。
    "gl/transfer/pnl": ("gl", "voucher"),
    "gl/transfer/custom": ("gl", "voucher"),
    # 档案：新增、修改、删除。
    "archives/create": ("archives", "create"),
    "archives/update": ("archives", "update"),
    "archives/delete": ("archives", "delete"),
    # 应收 / 应付：核销、自动核销 writeoff；制单、处理制单 voucher；
    # 并账、转账、红票对冲、汇兑损益、坏账 process；取消类 other。
    "arap/writeoff": ("arap", "writeoff"),
    "arap/writeoff/auto": ("arap", "writeoff"),
    "arap/writeoff/cancel": ("arap", "other"),
    "arap/voucher": ("arap", "voucher"),
    "arap/process/voucher": ("arap", "voucher"),
    "arap/voucher/delete": ("arap", "other"),
    "arap/merge": ("arap", "process"),
    "arap/transfer": ("arap", "process"),
    "arap/red_offset": ("arap", "process"),
    "arap/exchange_gain": ("arap", "process"),
    "arap/bad_debt": ("arap", "process"),
    "arap/process/cancel": ("arap", "other"),
    "arap/exchange_gain/cancel": ("arap", "other"),
    # 票据：登记、删除、处理。
    "notes/create": ("notes", "create"),
    "notes/delete": ("notes", "delete"),
    "notes/process": ("notes", "process"),
    # 期初、月末结账、存货核算（都按 action 映射）。
    "openings/post": ("openings", _POST),
    "openings/arap": (
        "openings",
        {"create": "create", "delete": "delete", "verify": "verify", "unverify": "unverify"},
    ),
    "periods/close": ("periods", {"close": "close", "reopen": "open"}),
    "ia/post": ("ia", _POST),
    "ia/period_end": ("ia", {"run": "close", "cancel": "open"}),
}

# 固定资产卡片的新增、撤销按类型 fa_card 分类（不归入 archives，与桥 WriteClass.cs 相同）。
_OWN_ARCHIVES = {("archives", "create"): frozenset(("fa_card",)), ("archives", "delete"): frozenset(("fa_card",))}

# 金额上限只对这些收付款类单据生效（与桥 WritePolicyEval.AmountTypes 相同）。
AMOUNT_TYPES = frozenset(("ar_receipt", "ap_payment", "ar_refund", "ap_refund"))
# 行数上限只对这些操作生效。
LINE_OPS = frozenset(("create", "update", "generate"))
_AMOUNT_OPS = frozenset(("create", "update"))
# .NET NumberStyles.Float 收的写法（ASCII）：可选符号、数字和小数点、可选指数。
# .NET decimal 的最大值：桥按 decimal 解析和累加，超出即解析失败或溢出，这里同样置 bad。
_DECIMAL_MAX = Decimal("79228162514264337593543950335")
_FLOAT = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", re.ASCII)


@dataclass(frozen=True)
class WriteInfo:
    """一笔写请求的分类结果。lines 是新增、修改、生单的明细行数；amount 只对收付款类单据的新增、修改有值。"""

    acc: str
    type: str
    op: str
    operator: str
    lines: int = 0
    amount: Decimal | None = None
    # 给了但解析不了的金额或汇率（配置了金额上限时按超过上限拒绝）。
    amount_bad: bool = False


def rule_ops(route: str) -> tuple[str, ...]:
    """规则里出现的全部 op（固定值和 action 映射的值），测试核对都在词表里。"""
    found = RULES.get(route)
    if found is None:
        return ()
    op = found[1]
    return tuple(op.values()) if isinstance(op, dict) else (op,)


def classify(action: str, payload: dict) -> WriteInfo | None:
    """按审计动作（co:<路径>）和桥请求体分类；不是登记过的写路由返回 None。"""
    found = RULES.get(action.removeprefix("co:"))
    if found is None:
        return None
    fixed_type, op_rule = found
    type_ = fixed_type if fixed_type is not None else _str(payload.get("type"))
    op = op_rule if isinstance(op_rule, str) else op_rule.get(_str(payload.get("action")), "other")
    if _str(payload.get("archive")) in _OWN_ARCHIVES.get((type_, op), ()):
        type_ = _str(payload.get("archive"))
    amount, amount_bad = _amount(type_, op, payload)
    return WriteInfo(
        acc=_str(payload.get("acc")),
        type=type_,
        op=op,
        operator=_str(payload.get("operator")),
        lines=_lines(op, payload),
        amount=amount,
        amount_bad=amount_bad,
    )


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _lines(op: str, payload: dict) -> int:
    lines = payload.get("lines")
    if op not in LINE_OPS or not isinstance(lines, list):
        return 0
    return len(lines)


def _amount(type_: str, op: str, payload: dict) -> tuple[Decimal | None, bool]:
    # 表体 iAmt（本币）合计；某行只给原币 iAmt_f 时按表头 iExchRate（缺省 1）折算。收付款单表头没有金额列。
    # 一行金额都没有（如只改表头）金额为 None，不查金额上限。字段名不区分大小写。金额上限按本次请求的明细行计。
    # 第二项 bad（配置了金额上限时按超过上限拒绝）：给了但按写入方（桥 ArapReq）的规则解析不了的金额或汇率；
    # 同一字段有多个只差大小写的键；超出 .NET decimal 范围的值、合计或折算；修改时某行只给原币
    # （修改不收汇率，写入方按单据已存的汇率折算，这里无从得知）。
    lines = payload.get("lines")
    if op not in _AMOUNT_OPS or type_ not in AMOUNT_TYPES or not isinstance(lines, list):
        return None, False
    rate_cell, bad = _cell(payload.get("head"), "iexchrate")
    rate, rate_bad = _number(rate_cell)
    bad = bad or rate_bad
    rate = Decimal(1) if rate is None else rate
    total: Decimal | None = None
    for row in lines:
        amount, row_bad = _line_amount(row, rate, op == "update")
        bad = bad or row_bad
        if amount is None:
            continue
        # 与桥逐行累加的 decimal 溢出一致：中途超出范围即整笔不认。
        try:
            total = amount if total is None else total + amount
            if abs(total) > _DECIMAL_MAX:
                return None, True
        except ArithmeticError:
            return None, True
    return total, bad


def _line_amount(row: object, rate: Decimal, update: bool) -> tuple[Decimal | None, bool]:
    # 本币、原币都解析（任一解析不了即 bad）；有本币取本币，否则原币 × 汇率（修改时只给原币置 bad）。
    local_cell, local_dup = _cell(row, "iamt")
    foreign_cell, foreign_dup = _cell(row, "iamt_f")
    local, local_bad = _number(local_cell)
    foreign, foreign_bad = _number(foreign_cell)
    bad = local_dup or foreign_dup or local_bad or foreign_bad
    if local is not None:
        return local, bad
    if foreign is None:
        return None, bad
    # 折算超出范围时返回的值也超出范围，由 _amount 按溢出处理。
    try:
        return foreign * rate, bad or update
    except ArithmeticError:
        return None, True


def _cell(row: object, key: str) -> tuple[object, bool]:
    # 返回 (第一个匹配的值, 是否有多个只差大小写的键)。
    if not isinstance(row, dict):
        return None, False
    found = [value for name, value in row.items() if isinstance(name, str) and name.lower() == key]
    return (found[0] if found else None), len(found) > 1


def _number(value: object) -> tuple[Decimal | None, bool]:
    # 与桥 ArapReq 相同：转成文本（None 为空、布尔 1 / 0、数字按原样），去空白后为空算没给；
    # 其余按 .NET NumberStyles.Float 的写法解析（可带符号、小数点、指数，不收千分位逗号、下划线、全角数字）。
    # 返回 (值, bad)：没给 (None, False)；解析不了 (None, True)。
    if value is None:
        return None, False
    if isinstance(value, bool):
        text = "1" if value else "0"
    elif isinstance(value, (int, float, Decimal)):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        return None, True
    if not text:
        return None, False
    if not _FLOAT.fullmatch(text):
        return None, True
    try:
        number = Decimal(text)
        # 超出 decimal 上下文指数范围时 abs() 抛 Overflow：一律按解析不了处理。
        if not number.is_finite() or abs(number) > _DECIMAL_MAX:
            return None, True
    except ArithmeticError:
        return None, True
    return number, False
