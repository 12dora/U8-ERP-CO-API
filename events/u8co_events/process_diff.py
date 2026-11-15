"""应收应付处理事件的纯函数部分：桥的字段映射、批次摘要、快照状态（事件的 prev / curr）与比对。不读库、不连桥。

桥的字段名只在「桥的字段」一节出现，桥改名时只改那里。用法见 process.py。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from u8co_events.entities import EntitySnap
from u8co_events.source import BridgeContractError

FLAGS: Mapping[str, str] = {"ar_process": "AR", "ap_process": "AP"}
STYLE_NAMES: Mapping[str, str] = {
    "9P": "核销",
    "9I": "应收冲应付",
    "9J": "应付冲应收",
    "BZ": "并账",
    "9N": "红票对冲",
    "9M": "汇兑损益",
    "9A": "票据托收",
    "9C": "票据退回",
    "9D": "票据贴现",
    "9E": "票据背书",
    "9F": "坏账",
    "9G": "坏账",
    "9H": "坏账",
    "9K": "应收冲应收",
    "9L": "应付冲应付",
    "XJ": "现结",
}
# 事件里 docs 最多列这么多行；rows 是批次的总行数。
DOCS_MAX = 200
_CENT = Decimal("0.01")

Period = tuple[int, int]


def style_name(style: str) -> str:
    return STYLE_NAMES.get(style, style)


def batch_key(style: str, code: str) -> str:
    return f"{style}|{code}"


# ---- 桥的字段（字段名只在这一节出现；桥改名时只改这里） ----


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value != "" else None


def money(value: object) -> str:
    """原币金额规范成两位小数的字符串（-0.00 记作 0.00），比对和求和都用它。"""
    if value is None:
        return "0.00"
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise BridgeContractError(f"应收应付处理的金额不是数字：{value!r}") from None
    if not amount.is_finite():
        raise BridgeContractError(f"应收应付处理的金额不是数字：{value!r}")
    return str(amount.quantize(_CENT) + 0)


def _fiscal_year(item: Mapping[str, Any]) -> int | None:
    """会计年度：桥给了 fiscal_year 就用它（登记日期在上一年、期间在下一年的行，如年初对上年末单据做的红票对冲）；
    旧桥没有这个字段时退回登记日期的年份。"""
    fiscal = item.get("fiscal_year")
    if _is_int(fiscal):
        return int(fiscal)
    reg = item.get("reg_date")
    return int(reg[:4]) if isinstance(reg, str) and reg[:4].isdigit() else None


def row_from_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """明细一行（keys_only 时只有 id、style、code 有值）。"""
    rid, style, code = item.get("id"), item.get("style") or "", item.get("code")
    if not _is_int(rid) or not isinstance(style, str) or not isinstance(code, str) or code == "":
        raise BridgeContractError("应收应付处理行缺少整数 id 或 style、code")
    period = item.get("period")
    year = _fiscal_year(item)
    return {
        "id": rid,
        "key": batch_key(style, code),
        "style": style,
        "code": code,
        "doc": {
            "type": item.get("vouch_type"),
            "id": item.get("vouch_id"),
            "line_id": item.get("line_id"),
            "debit_f": money(item.get("debit_f")),
            "credit_f": money(item.get("credit_f")),
        },
        "partner": _text(item.get("partner")),
        "pz": _text(item.get("pz_id")),
        "gl_sign": _text(item.get("gl_sign")),
        "gl_no": item.get("gl_no") if _is_int(item.get("gl_no")) else None,
        "period": (year, period) if year is not None and _is_int(period) else None,
    }


@dataclass(frozen=True)
class Batch:
    """摘要里的一批（可能由几个期间的摘要合并而来）。"""

    style: str
    code: str
    min_id: int
    max_id: int
    pz: str | None
    debit: str
    credit: str
    rows: int
    partners: tuple[str, ...]
    period: Period

    @property
    def key(self) -> str:
        return batch_key(self.style, self.code)

    def merge(self, other: Batch) -> Batch:
        return Batch(
            self.style,
            self.code,
            min(self.min_id, other.min_id),
            max(self.max_id, other.max_id),
            max(self.pz or "", other.pz or "") or None,
            money(Decimal(self.debit) + Decimal(other.debit)),
            money(Decimal(self.credit) + Decimal(other.credit)),
            self.rows + other.rows,
            tuple(sorted(set(self.partners) | set(other.partners))),
            min(self.period, other.period),
        )


def batch_from_item(item: Mapping[str, Any], period: Period) -> Batch:
    """period 是请求的（年度, 期间）；摘要行带了 fiscal_year 时年度以它为准。"""
    style, code = item.get("style") or "", item.get("code")
    fiscal = item.get("fiscal_year")
    if _is_int(fiscal):
        period = (int(fiscal), period[1])
    min_id, max_id, rows = item.get("min_id"), item.get("max_id"), item.get("rows")
    if not isinstance(style, str) or not isinstance(code, str) or code == "":
        raise BridgeContractError("应收应付处理摘要缺少 style、code")
    if not (_is_int(min_id) and _is_int(max_id) and _is_int(rows)):
        raise BridgeContractError("应收应付处理摘要的 min_id、max_id、rows 不是整数")
    partners = item.get("partners") or []
    if not isinstance(partners, list):
        raise BridgeContractError("应收应付处理摘要的 partners 不是数组")
    return Batch(
        style,
        code,
        min_id,
        max_id,
        _text(item.get("pz")),
        money(item.get("sum_d_f")),
        money(item.get("sum_c_f")),
        rows,
        tuple(sorted({p for p in partners if isinstance(p, str) and p})),
        period,
    )


def checked(response: object) -> dict[str, Any]:
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise BridgeContractError("应收应付处理列表响应不是成功的 JSON 对象")
    items = response.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise BridgeContractError("应收应付处理列表响应的 items 不是对象数组")
    return response


def number_field(response: Mapping[str, Any], key: str) -> int:
    value = response.get(key)
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or len(value) > 20:
        raise BridgeContractError(f"应收应付处理列表响应的 {key} 不是十进制字符串")
    return int(value)


def _period(value: object) -> Period:
    if not isinstance(value, dict) or not _is_int(value.get("year")) or not _is_int(value.get("period")):
        raise BridgeContractError("应收应付处理列表响应的期间不是 {year, period}")
    return (value["year"], value["period"])


def covered_periods(response: Mapping[str, Any]) -> tuple[Period, ...]:
    """要做摘要比对的期间：未结账期间，加最近一个已结账期间（结账前一刻的制单、取消）。"""
    opened = response.get("open_periods")
    if not isinstance(opened, list):
        raise BridgeContractError("应收应付处理列表响应的 open_periods 不是数组")
    found = {_period(item) for item in opened}
    if response.get("last_closed") is not None:
        found.add(_period(response["last_closed"]))
    return tuple(sorted(found))


# ---- 快照状态（事件的 prev / curr）与比对，纯函数 ----


def state_from_rows(flag: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """一批的明细行 → 状态。行按 Auto_ID 升序。"""
    head = rows[0]
    pz = max((row["pz"] or "" for row in rows), default="") or None
    gl = next((row for row in rows if row["gl_sign"] or row["gl_no"]), head)
    period = head["period"]
    return {
        "flag": flag,
        "style": head["style"],
        "style_name": style_name(head["style"]),
        "partners": sorted({row["partner"] for row in rows if row["partner"]}),
        "docs": [row["doc"] for row in rows[:DOCS_MAX]],
        "voucher_id": pz,
        "gl_sign": gl["gl_sign"],
        "gl_no": gl["gl_no"],
        "rows": len(rows),
        "min_id": head["id"],
        "max_id": rows[-1]["id"],
        "debit_f": money(sum(Decimal(row["doc"]["debit_f"]) for row in rows)),
        "credit_f": money(sum(Decimal(row["doc"]["credit_f"]) for row in rows)),
        "year": period[0] if period else None,
        "period": period[1] if period else None,
    }


def state_from_batch(flag: str, batch: Batch, base: Mapping[str, Any] | None) -> dict[str, Any]:
    """以摘要的合计为准；docs 和总账字段取自 base（明细行或旧状态），凭证号变了而 base 不知道新凭证时总账字段为 null。"""
    base = base or {}
    same = base.get("voucher_id") == batch.pz
    return {
        "flag": flag,
        "style": batch.style,
        "style_name": style_name(batch.style),
        "partners": list(batch.partners),
        "docs": list(base.get("docs") or []),
        "voucher_id": batch.pz,
        "gl_sign": base.get("gl_sign") if same else None,
        "gl_no": base.get("gl_no") if same else None,
        "rows": batch.rows,
        "min_id": batch.min_id,
        "max_id": batch.max_id,
        "debit_f": batch.debit,
        "credit_f": batch.credit,
        "year": batch.period[0],
        "period": batch.period[1],
    }


def change_kinds(prev: EntitySnap, batch: Batch) -> tuple[str, ...]:
    """已知批次与这一轮摘要比，得出事件种类。制单类变化不再另发 modified。

    处理号相同、最小 Auto_ID 却变了：原批次已取消、同号又做了一批，发 cancelled + processed。
    """
    state = prev.state
    if state.get("min_id") != batch.min_id:
        return ("cancelled", "processed")
    old = state.get("voucher_id") or None
    if old != batch.pz:
        return ("vouchered",) if batch.pz else ("unvouchered",)
    if (state.get("debit_f"), state.get("credit_f"), state.get("rows")) != (batch.debit, batch.credit, batch.rows):
        return ("modified",)
    return ()


def first_fingerprint(min_id: int, epoch: int = 0) -> str:
    """新批次的指纹：最小 Auto_ID；库被还原过（epoch > 0）时加「#还原次数」。

    还原后自增号回退、处理号重发，重做的批次与还原前的最小 Auto_ID 相同，带上 epoch 才不会与还原前的 event_id 重复。
    同一 epoch 内重算得到同一个值（期间反结账后重现的批次 event_id 不变，消费方按 event_id 去重）。
    """
    return str(min_id) if epoch <= 0 else f"{min_id}#{epoch}"


def next_fingerprint(prev: str, batch: Batch, epoch: int = 0) -> str:
    """之后每变一次是「最小 Auto_ID|凭证号|第几次变化」，库被还原过时再加「|还原次数」。

    带上次数，取消制单后又制成同一个凭证号时 event_id 也不同（消费方按 event_id 去重）。
    """
    parts = prev.split("|")
    count = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 0
    text = f"{batch.min_id}|{batch.pz or ''}|{count + 1}"
    return text if epoch <= 0 else f"{text}|{epoch}"


def snap_period(snap: EntitySnap) -> Period | None:
    year, period = snap.state.get("year"), snap.state.get("period")
    return (year, period) if _is_int(year) and _is_int(period) else None


def make_snap(key: str, state: Mapping[str, Any], fingerprint: str, code: str) -> EntitySnap:
    return EntitySnap(key=key, fingerprint=fingerprint, state=dict(state), doc_id=int(state["min_id"]), code=code)


def group_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(rows, key=lambda r: r["id"]):
        groups.setdefault(row["key"], []).append(row)
    return groups


@dataclass(frozen=True)
class ProcMark:
    """存下的水位文本 {mark}|{ident}|{epoch}|{base}。

    mark：已推进到的 Auto_ID（回看窗口之后）；ident：上一轮桥返回的 IDENT_CURRENT，只在库被还原时变小；
    epoch：发现还原的次数，混进之后的指纹；base：最近一次静默重记（首轮或还原）时的 mark，
    摘要里出现快照没有、最小 Auto_ID 不大于它的批次算历史批次（静默），大于它的是晚提交的批次（发 processed）。
    """

    mark: int
    ident: int | None
    epoch: int
    base: int

    def text(self) -> str:
        ident = "" if self.ident is None else str(self.ident)
        return f"{self.mark}|{ident}|{self.epoch}|{self.base}"

    def restored(self, ident: int) -> bool:
        """ident 比上一轮的小（旧格式没有 ident 时比 mark）：库被还原。"""
        return ident < (self.mark if self.ident is None else self.ident)


def parse_mark(text: str | None) -> ProcMark | None:
    """解析存下的水位；没有或认不出（手工改过）时返回 None，按首轮处理。旧格式只有 mark。"""
    if not text:
        return None
    parts = text.split("|")
    if not all(part.isascii() and (part.isdigit() or part == "") for part in parts) or parts[0] == "":
        return None
    if len(parts) == 1:
        return ProcMark(int(parts[0]), None, 0, int(parts[0]))
    if len(parts) != 4 or parts[2] == "" or parts[3] == "":
        return None
    return ProcMark(int(parts[0]), int(parts[1]) if parts[1] else None, int(parts[2]), int(parts[3]))


def lagged_mark(watermark: int, ident: int, lag: int, floor: int) -> int:
    """可以记下的水位：max(0, watermark − max(lag, ident − watermark))，且不低于 floor（上一轮的水位）。"""
    return max(floor, watermark - max(lag, ident - watermark), 0)
