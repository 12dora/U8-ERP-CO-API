"""列表行和快照对比，得出事件。纯函数，不读库、不连桥。

事件种类：created、modified、verified、unverified、closed、opened、workflow、deleted。
审核和关闭状态变了就发对应的事件，不再另发 modified；两种状态同时变了发两条。
workflow：质量单据的审批状态（wf_state）或当前审核人（current_auditor）变了；与审核事件可以同时发。
升级前的快照没有这两个字段（wf_known 为假），第一次看到时只补上，不发事件；
升级后第一轮另有一次整轮静默补齐（refill），免得单据下一次审批变化因快照不知道旧值而只发 modified。
event_id 由 账套|类型|id|种类|ufts 算出，同一变化重算得到同一个值，消费方按它去重。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

KINDS = ("created", "modified", "verified", "unverified", "closed", "opened", "workflow", "deleted")


@dataclass(frozen=True)
class Snapshot:
    """一张单据上一次看到的样子。ufts 是桥返回的十进制字符串。"""

    id: int
    code: str | None
    ufts: str
    verified: bool
    closed: bool
    red: bool
    verifier: str | None
    closer: str | None
    # 列表行带不带审批流字段（只有质量单据带）。为假时 wf_state、current_auditor 是「不知道」，不参与对比。
    wf_known: bool = False
    wf_state: int | None = None
    current_auditor: str | None = None

    def state(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "verified": self.verified,
            "closed": self.closed,
            "red": self.red,
            "verifier": self.verifier,
            "closer": self.closer,
        }
        if self.wf_known:
            base["wf_state"] = self.wf_state
            base["current_auditor"] = self.current_auditor
        return base

    def without_wf(self) -> Snapshot:
        return replace(self, wf_known=False, wf_state=None, current_auditor=None)


@dataclass(frozen=True)
class Scope:
    """一次对比的范围：哪个账套、哪种单据、什么时候发现的。"""

    acc: str
    type: str
    detected_at: str


def utc_text(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def event_id(scope: Scope, doc_id: int | str, kind: str, ufts: str) -> str:
    # 附加数据源传文本键、指纹（source.make_event）；单据传整数 id、ufts，算法相同。
    text = "|".join((scope.acc, scope.type, str(doc_id), kind, ufts))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def payload_text(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _opt_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _wf_state(doc_id: int, value: object) -> int | None:
    # 桥把 iVerifyStateNew 按字符串给出（"-1"、"0"、"1"、"2"），NULL 为 null。
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError(f"列表行 {doc_id} 的 wf_state 不是整数")
    try:
        return int(str(value).strip())
    except ValueError:
        raise ValueError(f"列表行 {doc_id} 的 wf_state 不是整数") from None


def snapshot_of(row: dict[str, Any]) -> Snapshot:
    """把桥的一行（完整行）换成快照。缺 id 或 ufts 的行说明桥的契约变了，直接报错。"""
    doc_id = row.get("id")
    ufts = row.get("ufts")
    if isinstance(doc_id, bool) or not isinstance(doc_id, int):
        raise ValueError("列表行缺少整数 id")
    if ufts is None or not str(ufts).strip():
        raise ValueError(f"列表行 {doc_id} 缺少 ufts")
    return Snapshot(
        id=doc_id,
        code=_opt_text(row.get("code")),
        ufts=str(ufts).strip(),
        verified=bool(row.get("verified")),
        closed=bool(row.get("closed")),
        red=bool(row.get("red")),
        verifier=_opt_text(row.get("verifier")),
        closer=_opt_text(row.get("closer")),
        wf_known="wf_state" in row or "current_auditor" in row,
        wf_state=_wf_state(doc_id, row.get("wf_state")),
        current_auditor=_opt_text(row.get("current_auditor")),
    )


def _event(scope: Scope, kind: str, prev: Snapshot | None, curr: Snapshot | None) -> dict[str, Any]:
    base = curr if curr is not None else prev
    assert base is not None
    return {
        "event_id": event_id(scope, base.id, kind, base.ufts),
        "account": scope.acc,
        "type": scope.type,
        "id": base.id,
        "code": base.code,
        "kind": kind,
        "ufts": base.ufts,
        "detected_at": scope.detected_at,
        "prev": None if prev is None else prev.state(),
        "curr": None if curr is None else curr.state(),
    }


def change_kinds(prev: Snapshot, curr: Snapshot) -> list[str]:
    """同一张单据两次快照之间的事件种类。没有变化返回空表。"""
    kinds: list[str] = []
    if prev.verified != curr.verified:
        kinds.append("verified" if curr.verified else "unverified")
    if prev.closed != curr.closed:
        kinds.append("closed" if curr.closed else "opened")
    if _wf_changed(prev, curr):
        kinds.append("workflow")
    if not kinds and _differs(prev, curr):
        kinds.append("modified")
    return kinds


def _wf_changed(prev: Snapshot, curr: Snapshot) -> bool:
    if not (prev.wf_known and curr.wf_known):
        return False
    return (prev.wf_state, prev.current_auditor) != (curr.wf_state, curr.current_auditor)


def _differs(prev: Snapshot, curr: Snapshot) -> bool:
    # 有一边不知道审批流字段（升级前的快照，或桥还没升级）时只比其他字段，补上或丢掉这两个字段不算修改。
    if prev.wf_known and curr.wf_known:
        return prev != curr
    return prev.without_wf() != curr.without_wf()


def changes(
    scope: Scope, rows: list[dict[str, Any]], known: dict[int, Snapshot], emit_created: bool
) -> tuple[list[Snapshot], list[dict[str, Any]]]:
    """一轮增量行对比已知快照。返回（要写入的快照，要进发件箱的事件）。

    emit_created 为 False 时（首轮回填）只记快照，不发 created。
    """
    upserts: list[Snapshot] = []
    events: list[dict[str, Any]] = []
    for row in rows:
        curr = snapshot_of(row)
        prev = known.get(curr.id)
        if prev is None:
            upserts.append(curr)
            if emit_created:
                events.append(_event(scope, "created", None, curr))
            continue
        kinds = change_kinds(prev, curr)
        if prev != curr:
            upserts.append(curr)
        events.extend(_event(scope, kind, prev, curr) for kind in kinds)
    return upserts, events


def deletions(scope: Scope, known: dict[int, Snapshot], seen: set[int]) -> tuple[list[int], list[dict[str, Any]]]:
    """整轮主键扫描后：快照里有、扫描里没有的就是被删了。只在整轮扫描成功后调用。"""
    gone = sorted(doc_id for doc_id in known if doc_id not in seen)
    events = [_event(scope, "deleted", known[doc_id], None) for doc_id in gone]
    return gone, events


def refill(rows: list[dict[str, Any]], known: dict[int, Snapshot]) -> list[Snapshot]:
    """状态库升级后的一次性静默补齐：已有快照不知道审批流字段、而这一行带着时，只补这两个字段。

    其他字段（含 ufts）保持快照原值，之后的增量对比照常按它们发 modified、审核、关闭事件。不产生事件。
    """
    filled: list[Snapshot] = []
    for row in rows:
        curr = snapshot_of(row)
        prev = known.get(curr.id)
        if prev is None or prev.wf_known or not curr.wf_known:
            continue
        filled.append(replace(prev, wf_known=True, wf_state=curr.wf_state, current_auditor=curr.current_auditor))
    return filled
