"""总账凭证数据源（sources 含 gl）：类型 gl_voucher，调桥的 gl/vouchers/digest。

GL_accvouch 没有 rowversion，不能按水位取增量。每一轮都读完扫描期间（桥缺省取登录年度的未结账期间，
再加最近 gl_closed_periods 个已结账期间）的全部凭证摘要，与快照逐张对比：
新增、删除、审核、出纳签字、记账、作废、修改。每轮都是完整扫描，所以每轮都可以发删除事件、记 scanned。

- 键 {年度}|{期间}|{凭证类别字}|{凭证号}，code {凭证类别字}-{凭证号}，id 为 0。
- 指纹是链式的：sha256(上一次的指纹|这一次的状态)。审核后弃审再审核，状态回到原样，指纹也不同，
  event_id 不会与上一次审核重复而被消费方当作重复事件丢掉。
- 水位文本 {ident}|{年度}|{期间,...}[|{epoch}]：ident 是桥返回的 IDENT_CURRENT('GL_accvouch')，只在库被还原时倒退，
  据此记倒退告警（MAX(i_id) 在删掉最新一张凭证时也会变小，不能用）；年度和期间是上一轮扫描的范围；
  epoch 是发现还原的次数，混进新凭证的指纹：还原后自增号回退，重做的凭证 i_id、内容都可能与还原前相同，
  带上 epoch 它的 created 才不会与还原前的 event_id 重复。epoch 为 0 时不写。
- 扫描范围以外的快照（期间结账后滑出范围、换了年度）静默删掉；新进入范围的期间（反结账、换年度、改配置）
  只记快照，不发 created。首轮同单据：只记快照，除非 backfill_events。
- 整轮一张都没有而范围内有快照时，逐张调 gl/vouchers/load（SQL，不登录 U8）确认都不存在才发删除。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from u8co_events.bridge import is_not_found
from u8co_events.diff import Scope
from u8co_events.entities import EntityCommit, EntityEvent, EntitySnap
from u8co_events.source import BridgeContractError, SourceContext, StopRequested, error_text, make_event

log = logging.getLogger(__name__)

TYPE = "gl_voucher"
LOAD_ROUTE = "/v1/gl/vouchers/load"
DIGEST_MAX = 500
_CURSOR = re.compile(r"^(\d{1,10})\.(\d{1,10})\.(\d{1,10})\Z")
_TEXT_FIELDS = (("maker", "maker"), ("checker", "checker"), ("cashier", "cashier"), ("bookkeeper", "bookkeeper"))


@dataclass(frozen=True)
class Mark:
    """上一轮提交的水位：库的自增计数和扫描范围。"""

    ident: int
    year: int
    periods: tuple[int, ...]
    epoch: int = 0

    def text(self) -> str:
        text = f"{self.ident}|{self.year}|{','.join(str(p) for p in self.periods)}"
        return text if self.epoch <= 0 else f"{text}|{self.epoch}"

    def covers(self, year: int, period: int) -> bool:
        return year == self.year and period in self.periods


def parse_mark(text: str | None) -> Mark | None:
    """解析存下的水位；没有或认不出（手工改过）时返回 None，按首轮处理。"""
    if not text:
        return None
    parts = text.split("|")
    if len(parts) not in (3, 4):
        return None
    try:
        periods = tuple(int(p) for p in parts[2].split(",")) if parts[2] else ()
        epoch = int(parts[3]) if len(parts) == 4 else 0
        return Mark(int(parts[0]), int(parts[1]), periods, epoch)
    except ValueError:
        return None


@dataclass(frozen=True)
class Digest:
    """一轮完整摘要：年度、实际扫描的期间、ident（第一页的）、全部凭证行。"""

    year: int
    periods: tuple[int, ...]
    ident: int
    items: list[dict[str, Any]] = field(default_factory=list)

    def mark(self, epoch: int = 0) -> Mark:
        return Mark(self.ident, self.year, self.periods, epoch)


@dataclass(frozen=True)
class Plan:
    """对比结果。gone 是要发删除事件的快照（空扫描时先逐张确认），dropped 是静默删掉的键。"""

    upserts: list[EntitySnap]
    events: list[EntityEvent]
    gone: list[EntitySnap]
    dropped: list[str]
    # 这一轮的还原次数（见模块说明），写进水位。
    epoch: int = 0


# ---- 纯函数：响应校验、状态、对比 ----


def _int(value: object, what: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise BridgeContractError(f"总账凭证摘要的 {what} 不是 {low} 到 {high} 的整数")
    return value


def _decimal(value: object, what: str) -> int:
    text = value.strip() if isinstance(value, str) else ""
    if not text.isdigit():
        raise BridgeContractError(f"总账凭证摘要的 {what} 不是十进制数字串")
    return int(text)


def _opt_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _money(value: object, key: str) -> int | float | str:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise BridgeContractError(f"总账凭证 {key} 的 debit_total 不是数字")
    return value


def cursor_key(text: object) -> tuple[int, int, int]:
    match = _CURSOR.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise BridgeContractError("总账凭证摘要的 next 不是「期间.类别序号.凭证号」")
    return int(match[1]), int(match[2]), int(match[3])


def check_page(response: object) -> Digest:
    """校验一页响应，返回这一页的 Digest（items 为本页）。"""
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise BridgeContractError("总账凭证摘要的响应不是成功的 JSON 对象")
    year = _int(response.get("fiscal_year"), "fiscal_year", 1900, 9999)
    raw = response.get("periods")
    if not isinstance(raw, list):
        raise BridgeContractError("总账凭证摘要的 periods 不是数组")
    periods = tuple(_int(p, "periods", 1, 12) for p in raw)
    items = response.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise BridgeContractError("总账凭证摘要的 items 不是对象数组")
    ident = _decimal(response.get("ident"), "ident")
    return Digest(year, periods, ident, items)


def key_of(year: int, period: int, sign: str, no: int) -> str:
    return f"{year}|{period}|{sign}|{no}"


def state_of(year: int, item: Mapping[str, Any]) -> dict[str, Any]:
    """完整行 → 事件里的 prev / curr。digest 是桥算的内容指纹（不含制单日期，所以日期另列）。"""
    period = _int(item.get("period"), "period", 1, 12)
    no = _int(item.get("no"), "no", 1, 2_147_483_647)
    sign = _opt_text(item.get("sign"))
    digest = _opt_text(item.get("fingerprint"))
    if sign is None or digest is None:
        raise BridgeContractError(f"总账凭证摘要 {year} 年 {period} 期 {no} 号缺少 sign 或 fingerprint")
    state: dict[str, Any] = {
        "year": year,
        "period": period,
        "sign": sign,
        "no": no,
        "date": _opt_text(item.get("date")),
    }
    for name, src in _TEXT_FIELDS:
        state[name] = _opt_text(item.get(src))
    state["posted"] = bool(item.get("posted"))
    state["void"] = bool(item.get("void"))
    state["debit_total"] = _money(item.get("debit_total"), key_of(year, period, sign, no))
    state["lines"] = _int(item.get("lines"), "lines", 0, 2_147_483_647)
    state["digest"] = digest
    return state


def _canonical(state: Mapping[str, Any]) -> str:
    return json.dumps(dict(state), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def snap_of(state: Mapping[str, Any], prev: EntitySnap | None, epoch: int = 0) -> EntitySnap:
    """链式指纹：新键 sha256(状态)（库被还原过时 sha256(#epoch|状态)），已有键 sha256(上一次的指纹|状态)。"""
    if prev is not None:
        head = prev.fingerprint + "|"
    else:
        head = "" if epoch <= 0 else f"#{epoch}|"
    fingerprint = hashlib.sha256((head + _canonical(state)).encode("utf-8")).hexdigest()
    key = key_of(state["year"], state["period"], state["sign"], state["no"])
    return EntitySnap(
        key=key, fingerprint=fingerprint, state=dict(state), doc_id=0, code=f"{state['sign']}-{state['no']}"
    )


def change_kinds(prev: Mapping[str, Any], curr: Mapping[str, Any]) -> list[str]:
    """同一张凭证两次状态之间的事件种类；有状态类事件时不再发 modified。没变化返回空表。"""
    kinds: list[str] = []
    if bool(prev.get("checker")) != bool(curr.get("checker")):
        kinds.append("audited" if curr.get("checker") else "unaudited")
    if bool(prev.get("cashier")) != bool(curr.get("cashier")):
        kinds.append("signed" if curr.get("cashier") else "unsigned")
    if curr.get("posted") and not prev.get("posted"):
        kinds.append("posted")
    if curr.get("void") and not prev.get("void"):
        kinds.append("voided")
    if not kinds and dict(prev) != dict(curr):
        kinds.append("modified")
    return kinds


def _period_of(snap: EntitySnap) -> tuple[int, int]:
    state = snap.state
    year, period = state.get("year"), state.get("period")
    if isinstance(year, int) and isinstance(period, int):
        return year, period
    parts = snap.key.split("|")
    return int(parts[0]), int(parts[1])


def restored(mark: Mark | None, digest: Digest) -> bool:
    """ident 比上一轮的小：库被还原。"""
    return mark is not None and digest.ident < mark.ident


def plan(scope: Scope, digest: Digest, known: Mapping[str, EntitySnap], mark: Mark | None, backfill: bool) -> Plan:
    """一轮完整摘要对比全部快照。mark 是上一轮的水位（None 为首轮）；backfill 只管首轮发不发 created。

    发现还原时 epoch 加一，混进这一轮起新凭证的指纹（见模块说明）。
    """
    epoch = 0 if mark is None else mark.epoch + (1 if restored(mark, digest) else 0)
    out = Plan([], [], [], [], epoch)
    seen: set[str] = set()
    for item in digest.items:
        state = state_of(digest.year, item)
        key = key_of(state["year"], state["period"], state["sign"], state["no"])
        if key in seen:
            raise BridgeContractError(f"总账凭证摘要里 {key} 出现了两次")
        seen.add(key)
        quiet = _quiet(mark, backfill, digest.year, state["period"])
        _compare(scope, out, known.get(key), state, quiet)
    for key in sorted(known.keys() - seen):
        year, period = _period_of(known[key])
        if year == digest.year and period in digest.periods:
            out.gone.append(known[key])
        else:
            out.dropped.append(key)
    out.events.extend(make_event(scope, "deleted", snap, None) for snap in out.gone)
    return out


def _quiet(mark: Mark | None, backfill: bool, year: int, period: int) -> bool:
    # 首轮看 backfill；之后新进入扫描范围的期间（反结账、换年度）只记快照。
    if mark is None:
        return not backfill
    return not mark.covers(year, period)


def _compare(scope: Scope, out: Plan, prev: EntitySnap | None, state: dict[str, Any], quiet: bool) -> None:
    if prev is None:
        curr = snap_of(state, None, out.epoch)
        out.upserts.append(curr)
        if not quiet:
            out.events.append(make_event(scope, "created", None, curr))
        return
    if dict(prev.state) == state:
        return
    curr = snap_of(state, prev)
    out.upserts.append(curr)
    out.events.extend(make_event(scope, kind, prev, curr) for kind in change_kinds(prev.state, state))


# ---- 轮询 ----


class GlSource:
    """一个账套的总账凭证数据源。每轮完整摘要，scan 只补记一次 scanned。"""

    def __init__(self, ctx: SourceContext) -> None:
        self._ctx = ctx

    def poll(self, scope: Scope) -> None:
        ctx = self._ctx
        mark = parse_mark(ctx.store.watermark(scope.acc, scope.type))
        digest = self.fetch(scope.acc)
        known = ctx.store.all_entity_snaps(scope.acc, scope.type)
        backfill = ctx.cfg.backfill_events and ctx.store.backfill_allowed(scope.acc, scope.type)
        result = plan(scope, digest, known, mark, backfill)
        if result.gone and not digest.items:
            self._confirm_empty(scope, result.gone)
        note = None
        if mark is not None and restored(mark, digest):
            note = f"{mark.ident}->{digest.ident}"
            log.error("账套 %s 总账凭证自增计数倒退（%s），疑似库被还原，已整轮重新对比", scope.acc, note)
        batch = EntityCommit(
            upserts=result.upserts,
            deleted=[snap.key for snap in result.gone] + result.dropped,
            events=result.events,
            watermark=digest.mark(result.epoch).text(),
            scanned=True,
            reset_note=note,
        )
        ctx.store.commit_entities(scope, batch)
        if result.events:
            log.info("账套 %s 总账凭证：%d 个事件", scope.acc, len(result.events))

    def scan(self, scope: Scope) -> None:
        # 每轮 poll 都是完整扫描并已记 scanned；这里只在到期时补记一次。
        self._ctx.store.commit_entities(scope, EntityCommit(polled=False, scanned=True))

    def fetch(self, acc: str) -> Digest:
        """读完全部页。第一页由桥定年度和期间，之后各页原样带上，结账发生在翻页中途也不会换范围。"""
        ctx = self._ctx
        limit = min(ctx.cfg.page_limit, DIGEST_MAX)
        fields: dict[str, Any] = {"closed_periods": ctx.cfg.gl_closed_periods, "limit": limit}
        first: Digest | None = None
        items: list[dict[str, Any]] = []
        after: tuple[int, int, int] | None = None
        while True:
            if ctx.stopping():
                raise StopRequested("总账凭证摘要翻页时收到停止信号")
            response = ctx.lister.list_gl_digest(acc, fields)
            page = check_page(response)
            if first is None:
                first = page
            elif (page.year, page.periods) != (first.year, first.periods):
                raise BridgeContractError("总账凭证摘要翻页时年度或期间变了")
            items.extend(page.items)
            following = response.get("next")
            if following is None:
                return Digest(first.year, first.periods, first.ident, items)
            cursor = cursor_key(following)
            if after is not None and cursor <= after:
                raise BridgeContractError("总账凭证摘要的 next 没有前进")
            after = cursor
            fields = {"fiscal_year": first.year, "periods": list(first.periods), "after": following, "limit": limit}

    def _confirm_empty(self, scope: Scope, gone: list[EntitySnap]) -> None:
        """整轮为空：逐张 gl/vouchers/load 确认，都是 DocumentNotFound 才返回，否则抛 BridgeContractError。"""
        head = "总账凭证摘要没有返回任何凭证"
        limit = self._ctx.cfg.empty_scan_confirm_max
        if limit == 0:
            raise BridgeContractError(f"{head}，未发删除事件")
        if len(gone) > limit:
            raise BridgeContractError(
                f"{head}，快照有 {len(gone)} 张，超过逐张确认上限 empty_scan_confirm_max={limit}，未发删除事件"
            )
        for done, snap in enumerate(gone):
            if self._ctx.stopping():
                raise StopRequested(f"{head}，逐张确认到第 {done + 1} 张时收到停止信号")
            fields = {"period": snap.state["period"], "sign": snap.state["sign"], "no": snap.state["no"]}
            try:
                self._ctx.lister.load_sql(scope.acc, LOAD_ROUTE, fields)
            except Exception as exc:
                if is_not_found(exc):
                    continue
                raise BridgeContractError(
                    f"{head}，逐张确认时读取 {snap.code}（{snap.key}）出错（{error_text(exc)}），"
                    "疑似列表或权限问题，未发删除事件"
                ) from exc
            raise BridgeContractError(
                f"{head}，但快照里 {len(gone)} 张中至少 1 张（{snap.code}，{snap.key}）仍能读取，"
                "疑似列表或权限问题，未发删除事件"
            )
        log.info("账套 %s 总账凭证：摘要为空，%d 张快照逐张确认都已不存在", scope.acc, len(gone))


def build(ctx: SourceContext) -> GlSource:
    return GlSource(ctx)
