"""基础档案数据源：类型 archive:<档案>，键是档案编码。读桥的 archives/list（SQL 只读池，不登录 U8）。

有时间戳的档案（config.RV_ARCHIVES）：每轮按水位（MIN_ACTIVE_ROWVERSION-1）取 changed_since 之后的整行，
指纹是 ufts；按 delete_scan_minutes 用 keys_only 整轮扫编码，快照里有、扫描里没有的发 deleted。
桥返回的水位比已存的小（库被还原）时整轮重读、与全部快照重新对比（含删除），并记下倒退告警。

没有时间戳的档案（config.PLAIN_ARCHIVES）：没有水位，每轮轮询只记一次成功；
到了删除扫描的节奏整表读一遍，按内容（名称、分类、停用状态、停用日期）对比，同时找出删除。
指纹 = sha256(上一次指纹|编码|内容) 取前 16 位十六进制：改回原值也得到新的指纹，event_id 不与先前的事件重复。
没有上一次（新编码，或删掉后又建了同一编码）时用发现时刻 detected_at 代替上一次指纹，
所以「建→删→再建→再删」的四个事件 event_id 都不同。一轮的事件和快照同一事务提交，重试的一轮不会重发已提交的 event_id。

种类：created、modified、disabled（停用日期由空变为有值）、enabled（反过来）、deleted。
停用状态变了只发 disabled / enabled，不再另发 modified。停用的来源：end_date（客户、供应商、仓库、部门、人员、存货），
操作员的 disabled，项目的 closed（项目关闭），固定资产卡片的 disposed（已处置，日期取 disposed_date）。
整轮为空而快照非空时，按编码逐条 archives/get 确认（SQL 读取，不占加密点数），全部是「档案不存在」才删。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from u8co_events.bridge import is_not_found
from u8co_events.config import ARCHIVE_PREFIX, PLAIN_ARCHIVES, RV_ARCHIVES
from u8co_events.diff import Scope
from u8co_events.entities import EntityCommit, EntityEvent, EntitySnap
from u8co_events.source import BridgeContractError, SourceContext, StopRequested, error_text, make_event

log = logging.getLogger("u8co_events")

GET_ROUTE = "/v1/archives/get"
_DIGITS = frozenset("0123456789")
_FP_LEN = 16
# 列表的附加请求字段：固定资产卡片连已处置的一起列，处置当作停用而不是删除。
_EXTRA: Mapping[str, Mapping[str, Any]] = {"fa_card": {"include_disposed": True}}


def archive_of(type_: str) -> str:
    """archive:<档案> → 档案名。不认识的档案说明配置与代码不一致，直接报错。"""
    name = type_[len(ARCHIVE_PREFIX) :] if type_.startswith(ARCHIVE_PREFIX) else ""
    if name not in RV_ARCHIVES and name not in PLAIN_ARCHIVES:
        raise ValueError(f"不认识的档案类型 {type_}")
    return name


def _decimal(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 20 and set(value) <= _DIGITS


def _code(item: Mapping[str, Any]) -> str:
    code = item.get("code")
    if not isinstance(code, str) or not code:
        raise BridgeContractError("档案列表行缺少编码 code")
    return code


def _opt_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def state_of(item: Mapping[str, Any]) -> dict[str, Any]:
    """事件里的 prev / curr：名称、分类编码、停用、停用日期。"""
    end_date = _opt_text(item.get("end_date")) or _opt_text(item.get("disposed_date"))
    disabled = bool(item.get("disabled") or item.get("closed") or item.get("disposed")) or end_date is not None
    return {
        "name": _opt_text(item.get("name")),
        "class_code": _opt_text(item.get("class_code")),
        "disabled": disabled,
        "end_date": end_date,
    }


def _digest(prev_fp: str, code: str, state: Mapping[str, Any]) -> str:
    parts = (prev_fp, code, state["name"] or "", state["class_code"] or "", str(state["end_date"] or ""))
    text = "|".join((*parts, "1" if state["disabled"] else "0"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_FP_LEN]


def snap_of(item: Mapping[str, Any], rowversion: bool, prev: EntitySnap | None, seed: str = "") -> EntitySnap:
    """列表行 → 快照。有时间戳的档案指纹取 ufts（十进制）；没有的按内容链式计算（见模块说明），
    没有 prev 时以 seed（发现时刻）起链。"""
    code = _code(item)
    state = state_of(item)
    if rowversion:
        ufts = item.get("ufts")
        if not _decimal(ufts):
            raise BridgeContractError(f"档案 {code} 的 ufts 不是十进制字符串")
        fingerprint = str(ufts)
    elif prev is not None and dict(prev.state) == state:
        fingerprint = prev.fingerprint
    else:
        fingerprint = _digest(prev.fingerprint if prev is not None else seed, code, state)
    return EntitySnap(key=code, fingerprint=fingerprint, state=state, doc_id=0, code=code)


def change_kinds(prev: EntitySnap, curr: EntitySnap) -> list[str]:
    """同一档案两次快照之间的事件种类。指纹没变就没有事件（只是补字段时静默更新快照）。"""
    if prev.fingerprint == curr.fingerprint:
        return []
    if bool(prev.state.get("disabled")) != bool(curr.state.get("disabled")):
        return ["disabled" if curr.state.get("disabled") else "enabled"]
    return ["modified"]


def changes(
    scope: Scope, items: Sequence[Mapping[str, Any]], known: Mapping[str, EntitySnap], rowversion: bool,
    emit_created: bool,
) -> tuple[list[EntitySnap], list[EntityEvent]]:
    """一批整行对比已知快照，返回（要写入的快照，事件）。emit_created 为假时（首轮）只记快照。"""
    upserts: list[EntitySnap] = []
    events: list[EntityEvent] = []
    for item in items:
        prev = known.get(_code(item))
        curr = snap_of(item, rowversion, prev, scope.detected_at)
        if prev is None:
            upserts.append(curr)
            if emit_created:
                events.append(make_event(scope, "created", None, curr))
            continue
        if prev != curr:
            upserts.append(curr)
        events.extend(make_event(scope, kind, prev, curr) for kind in change_kinds(prev, curr))
    return upserts, events


def deletions(scope: Scope, known: Mapping[str, EntitySnap], seen: set[str]) -> tuple[list[str], list[EntityEvent]]:
    """完整扫描之后：快照里有、扫描里没有的发 deleted。只在整轮成功后调用。"""
    gone = sorted(code for code in known if code not in seen)
    return gone, [make_event(scope, "deleted", known[code], None) for code in gone]


def _page(response: object) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise BridgeContractError("档案列表响应不是成功的 JSON 对象")
    items = response.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise BridgeContractError("档案列表响应的 items 不是对象数组")
    following = response.get("next")
    if following is not None and (not isinstance(following, str) or not following):
        raise BridgeContractError("档案列表响应的 next 不是编码")
    return items, following


class ArchiveSource:
    """一个账套的基础档案。scope.type 是 archive:<档案>。"""

    def __init__(self, ctx: SourceContext) -> None:
        self._ctx = ctx

    def poll(self, scope: Scope) -> None:
        name = archive_of(scope.type)
        if name in RV_ARCHIVES:
            self._poll_rv(scope, name)
            return
        # 没有时间戳：整表比对放在 scan（删除扫描的节奏），轮询只记一次成功，健康检查照常。
        self._ctx.store.commit_entities(scope, EntityCommit())

    def scan(self, scope: Scope) -> None:
        name = archive_of(scope.type)
        if name in RV_ARCHIVES:
            self._scan_keys(scope, name)
        else:
            self._scan_full(scope, name)

    # ---- 有时间戳 ----

    def _poll_rv(self, scope: Scope, name: str) -> None:
        store = self._ctx.store
        mark = store.watermark(scope.acc, scope.type)
        items, first_mark = self._fetch(scope.acc, name, {"changed_since": mark or ""})
        new_mark = _require_mark(first_mark)
        if mark is not None and int(new_mark) < int(mark):
            self._resync(scope, name, mark)
            return
        known = store.entity_snaps(scope.acc, scope.type, [_code(item) for item in items])
        emit_created = mark is not None or self._backfill(scope)
        upserts, events = changes(scope, items, known, True, emit_created)
        store.commit_entities(scope, EntityCommit(upserts=upserts, events=events, watermark=new_mark))
        if events:
            log.info("账套 %s %s：%d 个事件", scope.acc, scope.type, len(events))

    def _resync(self, scope: Scope, name: str, old_mark: str) -> None:
        """水位倒退：整轮读整行，与全部快照重新对比（含删除），连同新水位一起提交。"""
        items, first_mark = self._fetch(scope.acc, name, {})
        new_mark = _require_mark(first_mark)
        upserts, events, gone = self._full_compare(scope, name, items, True, "水位倒退后的整轮读取")
        note = f"{old_mark}->{new_mark}"
        log.error("账套 %s %s 水位倒退（%s），整轮重新对比：%d 个事件", scope.acc, scope.type, note, len(events))
        batch = EntityCommit(
            upserts=upserts, deleted=gone, events=events, watermark=new_mark, scanned=True, reset_note=note
        )
        self._ctx.store.commit_entities(scope, batch)

    def _scan_keys(self, scope: Scope, name: str) -> None:
        """keys_only 整轮扫编码，找出被删的档案。"""
        items, _ = self._fetch(scope.acc, name, {"keys_only": True})
        seen = {_code(item) for item in items}
        known = self._ctx.store.all_entity_snaps(scope.acc, scope.type)
        if not seen and known:
            self._confirm_empty(scope, name, sorted(known), "编码扫描")
        gone, events = deletions(scope, known, seen)
        self._ctx.store.commit_entities(scope, EntityCommit(deleted=gone, events=events, polled=False, scanned=True))
        if gone:
            log.info("账套 %s %s：%d 条档案已删除", scope.acc, scope.type, len(gone))

    # ---- 没有时间戳 ----

    def _scan_full(self, scope: Scope, name: str) -> None:
        """整表读一遍，按内容对比并找出删除。第一次成功之前（没有扫描记录）只记快照，除非 backfill_events。"""
        store = self._ctx.store
        items, _ = self._fetch(scope.acc, name, {})
        emit_created = store.last_scan_at(scope.acc, scope.type) is not None or self._backfill(scope)
        upserts, events, gone = self._full_compare(scope, name, items, emit_created, "整表比对")
        store.commit_entities(scope, EntityCommit(upserts=upserts, deleted=gone, events=events, scanned=True))
        if events:
            log.info("账套 %s %s：%d 个事件", scope.acc, scope.type, len(events))

    # ---- 公共 ----

    def _backfill(self, scope: Scope) -> bool:
        """首轮是否发 created：开了 backfill_events，且这个类型是状态库全新时登记的（后开的档案不回填）。"""
        return self._ctx.cfg.backfill_events and self._ctx.store.backfill_allowed(scope.acc, scope.type)

    def _full_compare(
        self, scope: Scope, name: str, items: list[dict[str, Any]], emit_created: bool, what: str
    ) -> tuple[list[EntitySnap], list[EntityEvent], list[str]]:
        known = self._ctx.store.all_entity_snaps(scope.acc, scope.type)
        seen = {_code(item) for item in items}
        if not seen and known:
            self._confirm_empty(scope, name, sorted(known), what)
        upserts, events = changes(scope, items, known, name in RV_ARCHIVES, emit_created)
        gone, gone_events = deletions(scope, known, seen)
        return upserts, events + gone_events, gone

    def _fetch(self, acc: str, name: str, fields: Mapping[str, Any]) -> tuple[list[dict[str, Any]], object]:
        """读完所有页。返回（全部行，第一页的 watermark）：桥在查询前取水位，之后的变化下一轮还能看到。"""
        rows: list[dict[str, Any]] = []
        first_mark: object = None
        for index, response in enumerate(self._pages(acc, name, fields)):
            if index == 0:
                first_mark = response.get("watermark")
            rows.extend(response["items"])
        return rows, first_mark

    def _pages(self, acc: str, name: str, fields: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
        base = {"archive": name, **_EXTRA.get(name, {}), **{k: v for k, v in fields.items() if v != ""}}
        after: str | None = None
        seen_next: set[str] = set()
        while True:
            request = {**base, "limit": self._ctx.cfg.page_limit}
            if after is not None:
                request["after"] = after
            response = self._ctx.lister.list_archives(acc, request)
            _, following = _page(response)
            yield response
            if following is None:
                return
            if following in seen_next:
                raise BridgeContractError("档案列表响应的 next 没有前进")
            seen_next.add(following)
            after = following

    def _confirm_empty(self, scope: Scope, name: str, codes: list[str], what: str) -> None:
        """空读取的确认：每个编码都是 DocumentNotFound 才返回；否则抛 BridgeContractError，不发删除事件。"""
        head = f"{what}没有返回任何档案"
        limit = self._ctx.cfg.empty_scan_confirm_max
        if limit == 0:
            raise BridgeContractError(f"{head}，未发删除事件")
        if len(codes) > limit:
            raise BridgeContractError(
                f"{head}，快照有 {len(codes)} 条，超过逐条确认上限 empty_scan_confirm_max={limit}，未发删除事件"
            )
        for done, code in enumerate(codes):
            if self._ctx.stopping():
                raise StopRequested(f"{head}，逐条确认到第 {done + 1} 条时收到停止信号")
            try:
                self._ctx.lister.load_sql(scope.acc, GET_ROUTE, {"archive": name, "code": code})
            except Exception as exc:
                if is_not_found(exc):
                    continue
                raise BridgeContractError(
                    f"{head}，逐条确认时读取编码 {code} 出错（{error_text(exc)}），疑似列表或权限问题，未发删除事件"
                ) from exc
            raise BridgeContractError(
                f"{head}，但快照里 {len(codes)} 条中至少 1 条（编码 {code}）仍能读取，疑似列表或权限问题，未发删除事件"
            )
        log.info("账套 %s %s：%s为空，%d 条快照逐条确认都已不存在", scope.acc, scope.type, what, len(codes))


def _require_mark(mark: object) -> str:
    if not _decimal(mark):
        raise BridgeContractError("档案列表响应的 watermark 不是十进制字符串")
    return str(mark)


def build(ctx: SourceContext) -> ArchiveSource:
    return ArchiveSource(ctx)
