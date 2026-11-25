"""客户字段 `archive-customer-fields` 的扩展键步骤：联系人 + 开户银行（一次改两键）、所属行业（联动行业名称）、
自定义项 1（按账套设置取值）。

只负责挑数据、排步骤；请求和整行核对由 arc_cus_fields 的 _apply 做。每一步改完再改回原值，
挑不到数据（没有别的末级行业、自定义项取自其他档案、现值写不回去）时只跳过那一步并给出说明。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from co.e2e.casespec.arc_cus_bodies import other_text
from co.e2e.casespec.arc_cus_fields_more_sql import (
    DEFINE_SEP,
    define_setting_sql,
    define_value_sql,
    trade_name_sql,
    trade_pick_sql,
)

NO_TRADE = "ZZNOTRD9"
NO_DEFINE_VALUE = "ZZ不存在档案值"
_DEFINE_TEXT = 20
_FIXED_FILL = ("E", "F")


@dataclass(frozen=True)
class Write:
    """一次写入：fields 是请求的键 → 值，edits 是预期整行变化的列 → 值。"""

    label: str
    fields: dict[str, str]
    edits: dict[str, str]


@dataclass(frozen=True)
class Reject:
    """应被拒绝且行不变的请求。"""

    label: str
    fields: dict[str, str]
    want: tuple[int, str]


@dataclass
class Picks:
    steps: list[Write] = field(default_factory=list)
    rejects: list[Reject] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _contact_bank(orig: dict[str, str]) -> list[Write]:
    contact = other_text(orig["contact"], "E2E联系人", 50)
    bank = other_text(orig["bank_open"], "E2E银行", 100)
    return [
        Write(
            "联系人与开户银行同改",
            {"contact": contact, "bank_open": bank},
            {"cCusPerson": contact, "cCusBank": bank},
        ),
        Write(
            "联系人与开户银行改回",
            {"contact": orig["contact"], "bank_open": orig["bank_open"]},
            {"cCusPerson": orig["contact"], "cCusBank": orig["bank_open"]},
        ),
    ]


def _trade(
    ask: Callable[[str], str], orig: dict[str, str], row: dict[str, str], picks: Picks
) -> None:
    """所属行业：写行业编码时桥同时写行业名称；现有行业名称与档案对不上时写不回去，跳过。"""
    now = orig["ccustradeccode"]
    try:
        name_now = ask(trade_name_sql(now)) if now else ""
        code = ask(trade_pick_sql(now))
        name = ask(trade_name_sql(code)) if code else ""
    except ValueError:
        picks.notes.append("客户现有的所属行业编码不是字母数字，跳过所属行业步骤")
        return
    stored = str(row.get("cTrade", "")).strip()
    if not code or not name:
        picks.notes.append("账套没有可换的末级所属行业，跳过所属行业步骤")
    elif stored != name_now or (now and not name_now):
        picks.notes.append("客户现有的行业名称与所属行业档案不一致，跳过所属行业步骤")
    else:
        picks.steps += [
            Write(
                "设所属行业",
                {"ccustradeccode": code},
                {"cCusTradeCCode": code, "cTrade": name},
            ),
            Write(
                "所属行业改回",
                {"ccustradeccode": now},
                {"cCusTradeCCode": now, "cTrade": name_now},
            ),
        ]
    if ask(trade_name_sql(NO_TRADE)) == "":
        picks.rejects.append(
            Reject(
                "不存在的所属行业",
                {"ccustradeccode": NO_TRADE},
                (409, "ccustradeccode_invalid"),
            )
        )


def _define_text(now: str, length: int, fixed: bool, longest: int) -> str:
    """不要求合法性检查的自定义项：长度取使用长度、最大长度、列宽里最小的正数，定长时必须等长；与现值不同。"""
    cap = min(item for item in (_DEFINE_TEXT, length, longest) if item > 0)
    if fixed and length > 0:
        return next(fill * length for fill in _FIXED_FILL if fill * length != now)
    return other_text(now, "E2E定义", cap)


def _missing_value(length: int, fixed: bool, longest: int) -> str:
    """不会在自定义项档案里、又满足使用长度的值（长度检查先于档案检查）。"""
    cap = min(item for item in (_DEFINE_TEXT, length, longest) if item > 0)
    size = length if fixed and length > 0 else min(cap, len(NO_DEFINE_VALUE))
    return (NO_DEFINE_VALUE * size)[:size]


def _define(ask: Callable[[str], str], orig: dict[str, str], picks: Picks) -> None:
    """自定义项 1：要求合法性检查的值必须在自定义项档案里（其余按使用长度）；必输、取自其他档案的项本路由不改。"""
    parts = ask(define_setting_sql()).split(DEFINE_SEP)
    if len(parts) != 7:
        picks.notes.append("账套没有客户自定义项 1 的设置，跳过自定义项步骤")
        return
    checked, related, required, length, fixed, longest, define_id = parts
    now = orig["self_define1"]
    if checked == "1" and related == "1":
        picks.notes.append("客户自定义项 1 的值取自其他档案，跳过自定义项步骤")
        return
    if required == "1" and not now:
        picks.notes.append("客户自定义项 1 是必输项且现值为空，改后清不回去，跳过自定义项步骤")
        return
    if checked == "1":
        value = ask(define_value_sql(define_id, now)) if not now else ""
        picks.rejects.append(
            Reject(
                "不在自定义项档案里的值",
                {"self_define1": _missing_value(int(length), fixed == "1", int(longest))},
                (409, "self_define1_invalid"),
            )
        )
        if not value:
            picks.notes.append(
                "自定义项 1 没有可用的档案值或现值非空，跳过自定义项写入"
            )
            return
    elif now:
        picks.notes.append("客户自定义项 1 现值非空，跳过自定义项写入")
        return
    else:
        value = _define_text(now, int(length), fixed == "1", int(longest))
    picks.steps += [
        Write("设自定义项 1", {"self_define1": value}, {"cCusDefine1": value}),
        Write("自定义项 1 改回", {"self_define1": now}, {"cCusDefine1": now}),
    ]


def pick_more(
    ask: Callable[[str], str], orig: dict[str, str], row: dict[str, str]
) -> Picks:
    """扩展键的步骤、否定请求和跳过说明。ask 跑一条 SQL、回第一格。"""
    picks = Picks(steps=_contact_bank(orig))
    _trade(ask, orig, row, picks)
    _define(ask, orig, picks)
    return picks
