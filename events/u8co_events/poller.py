"""轮询：按（账套, 类型）调 vouchers/list，与快照对比，事件写进发件箱。

一轮增量：从水位开始按 next 读完所有页，再在一个事务里写事件、更新快照、推进水位。
中途任何一页失败，这一轮什么都不写，水位不动，下一轮从原水位重读。

删除检测：按 delete_scan_minutes 做一次 keys_only 整轮主键扫描，快照里有、扫描里没有的发 deleted。
扫描中途失败就整轮作废，绝不在不完整的扫描上发删除事件。
整轮一张都没扫到、快照里却有单据时，先逐张 vouchers/load 确认（最多 empty_scan_confirm_max 张）：
全部是桥明确回答的单据不存在（bridge.DocumentNotFound）才当作都删了；读到第一张还在的、或读取出别的错，
就当作列表或权限出了问题，立即停下、整轮作废。每读一张都要登录 U8 子系统（占加密点数），所以读到一张还在就不再往下读；
收到停止信号也立即停下（不记错误）。

水位倒退（桥返回的水位比已存的小：库被还原到较早的备份、凭证指向了别的库）：
不信任增量，改为整轮读取全部完整行与快照重新对比（状态对比 + 确定的 event_id，重复的也无害），
连同删除、新水位一起提交，并在状态里记下倒退告警。整轮读取一张都没有而快照里有单据时，同样逐张确认（同上限、同规则），
确认都不存在才提交；否则这一轮作废、水位不动。

轮询端只写发件箱，不发布。发件箱积压到 outbox_high_water 时暂停轮询（水位不动，不会漏）。

附加数据源（sources 里的 arap_process、archives、gl，见 source.py）在每个账套的单据类型之后轮到，
走同一套停止信号、积压暂停、错误记录和删除扫描节奏。notes（票据）就是两种单据类型，走上面的单据轮询。
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from u8co_events.bridge import Lister, PageRequest, SourceLister, is_not_found
from u8co_events.config import AccountConf, Config
from u8co_events.diff import Scope, changes, deletions, refill, utc_text
from u8co_events.source import (
    SOURCE_MODULES,
    BridgeContractError,
    Source,
    SourceContext,
    SourceFactory,
    StopRequested,
    default_factory,
    error_text,
)
from u8co_events.state import Resync, Store

log = logging.getLogger("u8co_events")

_WATERMARK = re.compile(r"^\d{1,20}\Z")
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


Step = Callable[[Scope], None]


def _items(response: object) -> tuple[list[dict[str, Any]], int | None]:
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise BridgeContractError("列表响应不是成功的 JSON 对象")
    items = response.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise BridgeContractError("列表响应的 items 不是对象数组")
    following = response.get("next")
    if following is not None and (isinstance(following, bool) or not isinstance(following, int)):
        raise BridgeContractError("列表响应的 next 不是整数")
    return items, following


def _watermark(response: dict[str, Any]) -> str:
    mark = response.get("watermark")
    if not isinstance(mark, str) or _WATERMARK.fullmatch(mark) is None:
        raise BridgeContractError("列表响应的 watermark 不是十进制字符串")
    return mark


def _id_of(item: dict[str, Any]) -> int:
    doc_id = item.get("id")
    if isinstance(doc_id, bool) or not isinstance(doc_id, int):
        raise BridgeContractError("列表行缺少整数 id")
    return doc_id


class Poller:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        lister: Lister,
        now: Callable[[], datetime] | None = None,
        factories: Mapping[str, SourceFactory] | None = None,
    ) -> None:
        """factories：数据源名 → 构造函数；缺省按 source.SOURCE_MODULES 导入（测试里传假的）。"""
        self._cfg = cfg
        self._store = store
        self._lister = lister
        self._now = now or (lambda: datetime.now(UTC))
        self._stop: threading.Event | None = None
        # 水位倒退后空读取的逐张确认：按（账套, 类型）记上次确认的时刻和失败原因（只在内存里，重启后先确认一次）。
        self._resync_tries: dict[tuple[str, str], tuple[datetime, str]] = {}
        # 每个账套开着的附加数据源：（该数据源的类型, 实现）。
        self._sources: dict[str, list[tuple[tuple[str, ...], Source]]] = {}
        for account in cfg.accounts:
            for type_ in account.all_types():
                store.register(account.acc, type_)
            self._sources[account.acc] = self._build_sources(account, factories)

    def _build_sources(
        self, account: AccountConf, factories: Mapping[str, SourceFactory] | None
    ) -> list[tuple[tuple[str, ...], Source]]:
        built: list[tuple[tuple[str, ...], Source]] = []
        for name in account.sources:
            if name not in SOURCE_MODULES:
                continue
            factory = factories[name] if factories is not None and name in factories else default_factory(name)
            ctx = SourceContext(self._cfg, account, self._store, self._source_lister(), self._now, self._stopping)
            built.append((account.source_types(name), factory(ctx)))
        return built

    def _source_lister(self) -> SourceLister:
        # 附加数据源要的是 SourceLister；单据轮询只用 Lister 的两个方法。真正的 BridgeLister 两者都实现。
        return cast(SourceLister, self._lister)

    def _stopping(self) -> bool:
        return self._stop is not None and self._stop.is_set()

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.run_once(stop)
            except Exception:
                # 状态库本身出错（磁盘满、IO 错）也只记日志，下一轮再试；已提交的数据不受影响。
                log.exception("轮询一轮失败")
            stop.wait(self._cfg.poll_interval_seconds)

    def run_once(self, stop: threading.Event | None = None) -> None:
        self._stop = stop
        for account in self._cfg.accounts:
            steps: list[tuple[str, Step, Step]] = [(t, self.poll_type, self.scan_type) for t in account.list_types()]
            for types, source in self._sources.get(account.acc, ()):
                steps.extend((t, source.poll, source.scan) for t in types)
            for type_, poll, scan in steps:
                if self._stopping() or self._backlogged():
                    return
                scope = Scope(account.acc, type_, utc_text(self._now()))
                if self._guarded(poll, scope) and self._scan_due(scope):
                    self._guarded(scan, replace(scope, detected_at=utc_text(self._now())), scan=True)

    def poll_type(self, scope: Scope) -> None:
        """一个类型的一轮增量。首轮（还没有水位）只记快照，除非 backfill_events。"""
        if self._store.wf_refill_pending(scope.acc, scope.type):
            self._try_refill(scope)
        mark = self._store.watermark(scope.acc, scope.type)
        rows, first_mark = self._fetch(scope.acc, PageRequest(type=scope.type, changed_since=mark or ""))
        if mark is not None and int(first_mark) < int(mark):
            self._resync(scope, mark)
            return
        known = self._store.snapshots(scope.acc, scope.type, [_id_of(row) for row in rows])
        emit_created = mark is not None or self._cfg.backfill_events
        upserts, events = changes(scope, rows, known, emit_created)
        self._store.commit_cycle(scope, first_mark, upserts, events)
        if events:
            log.info("账套 %s %s：%d 个事件", scope.acc, scope.type, len(events))

    def _fetch(self, acc: str, request: PageRequest) -> tuple[list[dict[str, Any]], str]:
        rows: list[dict[str, Any]] = []
        first_mark = ""
        for response in self._pages(acc, request):
            if not first_mark:
                # 水位取第一页的：桥在查询前取，所以之后的变化下一轮一定还能看到。
                first_mark = _watermark(response)
            rows.extend(response["items"])
        return rows, first_mark

    def _try_refill(self, scope: Scope) -> None:
        """补齐失败不挡这一轮增量：记下错误，待办标记留着，下一轮再补。"""
        try:
            self._refill(scope)
        except Exception as exc:
            text = error_text(exc)
            log.warning("账套 %s %s 升级后补齐失败，下一轮重做：%s", scope.acc, scope.type, text)
            self._store.record_error(scope, f"补齐审批流字段失败：{text}")

    def _refill(self, scope: Scope) -> None:
        """状态库从第 1、2 版升级后的一次性静默补齐：整轮读完整行，只给旧快照补审批流字段，不发事件、不动水位。

        不补的话，升级后一张没动过的质量单据第一次审批变化时快照不知道旧值，只能发 modified。失败则下一轮重做。
        桥返回了行却没有一行带审批流字段（桥还是旧版），不算补齐：待办标记留着，下一轮再补。
        """
        rows, _ = self._fetch(scope.acc, PageRequest(type=scope.type))
        if rows and not any("wf_state" in row or "current_auditor" in row for row in rows):
            log.warning("账套 %s %s：桥返回的行没有审批流字段，暂不补齐，下一轮再试", scope.acc, scope.type)
            return
        filled = refill(rows, self._store.all_snapshots(scope.acc, scope.type))
        self._store.commit_refill(scope, filled)
        log.info("账套 %s %s：升级后补齐 %d 张快照的审批流字段", scope.acc, scope.type, len(filled))

    def _resync(self, scope: Scope, old_mark: str) -> None:
        """水位倒退：整轮读完整行，与全部快照重新对比，含删除。只有整轮成功才提交。"""
        rows, new_mark = self._fetch(scope.acc, PageRequest(type=scope.type))
        known = self._store.all_snapshots(scope.acc, scope.type)
        seen = {_id_of(row) for row in rows}
        if not seen and known:
            # 库还原到这类单据还没有的时候（测试账套还原后常见）会一直读空：逐张确认都不存在才提交，否则水位不动。
            self._confirm_resync(scope, sorted(known))
            log.info("账套 %s %s：水位倒退后整轮读取为空，%d 张快照逐张确认都已不存在", scope.acc, scope.type, len(known))
        upserts, events = changes(scope, rows, known, emit_created=True)
        gone, gone_events = deletions(scope, known, seen)
        note = f"{old_mark}->{new_mark}"
        log.error(
            "账套 %s %s 水位倒退（%s），整轮重新对比：%d 个事件，%d 张删除",
            scope.acc,
            scope.type,
            note,
            len(events),
            len(gone),
        )
        self._store.commit_resync(scope, Resync(new_mark, upserts, gone, events + gone_events, note))

    def _confirm_resync(self, scope: Scope, ids: list[int]) -> None:
        """水位倒退后空读取的确认，按 delete_scan_minutes 节流（0 时按 30 分钟）：每次 load 都要登录 U8、占加密点数，
        列表或权限问题没解决前不能每轮（poll_interval_seconds）都读。两次确认之间照旧报上次的原因，不调 load。"""
        key = (scope.acc, scope.type)
        now = self._now()
        last = self._resync_tries.get(key)
        minutes = self._cfg.delete_scan_minutes if self._cfg.delete_scan_minutes > 0 else 30
        if last is not None and now - last[0] < timedelta(minutes=minutes):
            raise BridgeContractError(f"{last[1]}（上次逐张确认于 {utc_text(last[0])}，{minutes:g} 分钟内不再确认）")
        try:
            self._confirm_empty(scope, ids, "水位倒退后的整轮读取")
        except BridgeContractError as exc:
            self._resync_tries[key] = (now, str(exc))
            raise
        self._resync_tries.pop(key, None)

    def scan_type(self, scope: Scope) -> None:
        """整轮 keys_only 扫描，找出被删的单据。只有整轮都成功才提交。"""
        seen: set[int] = set()
        pages = 0
        for response in self._pages(scope.acc, PageRequest(type=scope.type, keys_only=True)):
            pages += 1
            seen.update(_id_of(item) for item in response["items"])
        known = self._store.all_snapshots(scope.acc, scope.type)
        if pages == 0:
            raise BridgeContractError("主键扫描没有返回任何单据，未发删除事件")
        if not seen and known:
            # 一张都没扫到却有快照：可能真的都删了（测试账套上建了又删），也可能是账套或权限出了问题。逐张确认。
            self._confirm_empty(scope, sorted(known), "主键扫描")
            log.info("账套 %s %s：主键扫描为空，%d 张快照逐张确认都已不存在", scope.acc, scope.type, len(known))
        gone, events = deletions(scope, known, seen)
        self._store.commit_scan(scope, gone, events)
        if gone:
            log.info("账套 %s %s：%d 张单据已删除", scope.acc, scope.type, len(gone))

    def _confirm_empty(self, scope: Scope, ids: list[int], what: str) -> None:
        """空读取的确认：每个快照 id 都是 DocumentNotFound 才返回；否则抛 BridgeContractError，不发删除事件。

        读到第一张还在的就停（每张 load 都要登录 U8 子系统）；每张之前看一次停止信号。
        """
        head = f"{what}没有返回任何单据"
        limit = self._cfg.empty_scan_confirm_max
        if limit == 0:
            raise BridgeContractError(f"{head}，未发删除事件")
        if len(ids) > limit:
            raise BridgeContractError(
                f"{head}，快照有 {len(ids)} 张，超过逐张确认上限 empty_scan_confirm_max={limit}，未发删除事件"
            )
        for done, doc_id in enumerate(ids):
            if self._stopping():
                raise StopRequested(f"{head}，逐张确认到第 {done + 1} 张时收到停止信号")
            try:
                self._lister.load(scope.acc, scope.type, doc_id)
            except Exception as exc:
                if is_not_found(exc):
                    continue
                raise BridgeContractError(
                    f"{head}，逐张确认时读取 id={doc_id} 出错（{error_text(exc)}），疑似列表或权限问题，未发删除事件"
                ) from exc
            raise BridgeContractError(
                f"{head}，但快照里 {len(ids)} 张中至少 1 张（id={doc_id}）仍能读取，疑似列表或权限问题，未发删除事件"
            )

    def _pages(self, acc: str, request: PageRequest) -> Iterator[dict[str, Any]]:
        after: int | None = None
        while True:
            response = self._lister.list_page(acc, replace(request, after=after, limit=self._cfg.page_limit))
            _, following = _items(response)
            yield response
            if following is None:
                return
            if after is not None and following <= after:
                raise BridgeContractError("列表响应的 next 没有前进")
            after = following

    def _guarded(self, step: Step, scope: Scope, scan: bool = False) -> bool:
        try:
            step(scope)
        except StopRequested as exc:
            log.info("账套 %s %s：%s，这一轮作废", scope.acc, scope.type, exc)
            return False
        except Exception as exc:
            text = error_text(exc)
            what = "删除扫描" if scan else "轮询"
            log.warning("账套 %s %s %s失败：%s", scope.acc, scope.type, what, text)
            self._store.record_error(scope, text, scan=scan)
            return False
        return True

    def _scan_due(self, scope: Scope) -> bool:
        minutes = self._cfg.delete_scan_minutes
        if minutes <= 0:
            return False
        last = self._store.last_scan_at(scope.acc, scope.type)
        if last is None:
            return True
        try:
            then = datetime.strptime(last, _TIME_FORMAT).replace(tzinfo=UTC)
        except ValueError:
            return True
        return self._now() - then >= timedelta(minutes=minutes)

    def _backlogged(self) -> bool:
        size = self._store.outbox_size()
        if size < self._cfg.outbox_high_water:
            return False
        log.warning("发件箱积压 %d 条，达到上限 %d，本轮暂停轮询", size, self._cfg.outbox_high_water)
        return True
