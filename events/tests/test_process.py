"""应收应付处理事件（process.py）：增量与回看窗口、期间摘要比对、首轮、水位倒退、空摘要确认。假桥，不连网络。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from co.client.u8co_arap_proc_list import ProcDigestQuery, ProcListQuery
from u8co_events.bridge import BridgeLister, Operator
from u8co_events.diff import Scope
from u8co_events.entities import EntitySnap
from u8co_events.process import ProcessSource
from u8co_events.process_diff import (
    Batch,
    ProcMark,
    batch_from_item,
    change_kinds,
    first_fingerprint,
    lagged_mark,
    money,
    next_fingerprint,
    parse_mark,
    row_from_item,
    style_name,
)
from u8co_events.source import BridgeContractError, SourceContext, StopRequested
from u8co_events.state import Store

from fakes import make_config

SEP = (2026, 9)
OCT = (2026, 10)
AUG = (2026, 8)
JUL = (2026, 7)


class FakeProc:
    """按 arap/process/list 的契约回答。行都已提交；「在途事务」用后加一个较小的 id 模拟。"""

    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, Any]]] = {"AR": [], "AP": []}
        self.open = [SEP, OCT]
        self.last_closed: tuple[int, int] | None = AUG
        self.ident: int | None = None
        # 模拟摘要出问题：摘要一批都不返回，明细照常。
        self.digest_hidden = False
        self.calls: list[dict[str, Any]] = []

    def add(self, rid: int, code: str, *, flag: str = "AR", style: str = "9P", period=SEP, **extra: Any) -> None:
        row = {
            "id": rid,
            "flag": flag,
            "style": style,
            "code": code,
            "vouch_type": "R0",
            "vouch_id": f"D{rid}",
            "co_vouch_type": None,
            "co_vouch_id": None,
            "partner": "C001",
            "dept": None,
            "person": None,
            "line_id": rid * 10,
            "debit_f": "0.00",
            "credit_f": "100.00",
            "pz_id": None,
            "gl_sign": None,
            "gl_no": None,
            "reg_date": f"{period[0]}-{period[1]:02d}-15",
            "period": period[1],
            "row_flag": 1,
        }
        row.update(extra)
        self.rows[flag].append(row)

    def voucher(self, code: str, pz: str | None, flag: str = "AR") -> None:
        for row in self.rows[flag]:
            if row["code"] == code:
                row.update(pz_id=pz, gl_sign="转" if pz else None, gl_no=7 if pz else None)

    def drop(self, code: str, flag: str = "AR") -> None:
        self.rows[flag] = [row for row in self.rows[flag] if row["code"] != code]

    def list_process(self, acc: str, fields: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(fields))
        flag = fields["flag"]
        rows = sorted(self.rows[flag], key=lambda r: r["id"])
        base = {
            "ok": True,
            "flag": flag,
            "open_periods": [{"year": y, "period": p} for y, p in self.open],
            "last_closed": None if self.last_closed is None else dict(zip(("year", "period"), self.last_closed)),
        }
        if fields.get("digest"):
            return {**base, **self._digest(flag, rows, fields)}
        mark = max((r["id"] for r in rows), default=0)
        low = max(int(fields.get("changed_since") or 0), fields.get("after") or 0)
        selected = [r for r in rows if low < r["id"] <= mark]
        limit = fields["limit"]
        page = selected[:limit]
        if fields.get("keys_only"):
            items = [{k: r[k] for k in ("id", "flag", "style", "code")} for r in page]
        else:
            items = [dict(r) for r in page]
        return {
            **base,
            "items": items,
            "next": page[-1]["id"] if len(selected) > limit else None,
            "watermark": str(mark),
            "ident": str(self.ident if self.ident is not None else mark),
        }

    def _digest(self, flag: str, rows: list[dict[str, Any]], fields: dict[str, Any]) -> dict[str, Any]:
        year, periods = fields["fiscal_year"], fields["periods"]
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for r in [] if self.digest_hidden else rows:
            if r.get("fiscal_year", int(r["reg_date"][:4])) == year and r["period"] in periods:
                groups.setdefault((r["style"], r["code"]), []).append(r)
        keys = sorted(groups)
        if fields.get("after"):
            keys = [k for k in keys if "|".join(k) > fields["after"]]
        page = keys[: fields["limit"]]
        items = [_digest_item(flag, k, groups[k]) for k in page]
        following = "|".join(page[-1]) if len(keys) > fields["limit"] else None
        return {"digest": True, "periods": [{"year": year, "period": p} for p in periods], "items": items, "next": following}


def _digest_item(flag: str, key: tuple[str, str], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "flag": flag,
        "style": key[0],
        "code": key[1],
        "min_id": min(r["id"] for r in rows),
        "max_id": max(r["id"] for r in rows),
        "pz": max(r["pz_id"] or "" for r in rows) or None,
        "sum_d_f": f"{sum(Decimal(r['debit_f']) for r in rows):.2f}",
        "sum_c_f": f"{sum(Decimal(r['credit_f']) for r in rows):.2f}",
        "rows": len(rows),
        "partners": sorted({r["partner"] for r in rows if r["partner"]}),
    }


class Env:
    def __init__(self, tmp_path, **top: Any) -> None:
        self.store = Store(str(tmp_path / "s.db"))
        self.bridge = FakeProc()
        self.stop = False
        accounts = [{"acc": "999", "operator_file": "/x", "sources": ["arap_process"]}]
        cfg = make_config(str(tmp_path / "s.db"), accounts=accounts, auto_id_lag=50, **top)
        ctx = SourceContext(
            cfg,
            cfg.accounts[0],
            self.store,
            self.bridge,
            now=lambda: datetime(2026, 10, 2, tzinfo=UTC),
            stopping=lambda: self.stop,
        )
        for type_ in ("ar_process", "ap_process"):
            self.store.register("999", type_)
        self.source = ProcessSource(ctx)
        self.seen = 0

    def poll(self, type_: str = "ar_process") -> list[dict[str, Any]]:
        """跑一轮，返回这一轮新进发件箱的事件。"""
        self.source.poll(Scope("999", type_, "2026-10-02T01:00:00Z"))
        rows = self.store.pending_outbox(10_000)
        fresh = [json.loads(row.payload) for row in rows[self.seen :]]
        self.seen = len(rows)
        return fresh

    def snaps(self, type_: str = "ar_process") -> dict[str, EntitySnap]:
        return self.store.all_entity_snaps("999", type_)

    def mark(self, type_: str = "ar_process") -> str | None:
        """水位文本里的 mark 部分（完整文本见 raw_mark）。"""
        text = self.raw_mark(type_)
        return None if text is None else text.split("|")[0]

    def raw_mark(self, type_: str = "ar_process") -> str | None:
        return self.store.watermark("999", type_)


@pytest.fixture
def env(tmp_path):
    made: list[Env] = []

    def build(**top: Any) -> Env:
        made.append(Env(tmp_path, **top))
        return made[-1]

    yield build
    for item in made:
        item.store.close()


def _seed(bridge: FakeProc) -> None:
    bridge.add(990, "HX000", period=JUL)
    bridge.add(1000, "HX001")
    bridge.add(1001, "HX001", partner="C002", debit_f="100.00", credit_f="0.00")
    bridge.add(1002, "HX002", period=OCT)


def _kinds(events: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(ev["kind"], ev["code"]) for ev in events]


# ---- 首轮 ----


def test_first_run_snapshots_without_events(env) -> None:
    e = env()
    _seed(e.bridge)
    assert e.poll() == []
    assert set(e.snaps()) == {"9P|HX000", "9P|HX001", "9P|HX002"}
    assert e.mark() == "952"
    # 首轮只读一行拿水位，再从 mark 起读窗口。
    assert e.bridge.calls[0] == {"flag": "AR", "changed_since": "0", "open_only": False, "keys_only": True, "limit": 1}
    assert e.poll() == []
    assert e.store.status({("999", "ar_process")})[0].last_scan_at is not None


def test_first_run_backfill_emits_processed(env) -> None:
    e = env(backfill_events=True)
    _seed(e.bridge)
    events = e.poll()
    assert sorted(_kinds(events)) == [("processed", "HX000"), ("processed", "HX001"), ("processed", "HX002")]
    hx1 = next(ev for ev in events if ev["code"] == "HX001")
    assert len(hx1["curr"]["docs"]) == 2


def test_first_run_lag_follows_ident_gap(env) -> None:
    e = env()
    _seed(e.bridge)
    e.bridge.ident = 1202
    e.poll()
    assert e.mark() == "802"


# ---- 新批次与回看窗口 ----


def test_new_batch_emits_processed_with_state(env) -> None:
    e = env()
    _seed(e.bridge)
    e.poll()
    e.bridge.add(1010, "HX010", style="9I", partner="C003", credit_f="30.50")
    e.bridge.add(1011, "HX010", style="9I", partner="C001", credit_f="0.00", debit_f="30.50")
    events = e.poll()
    assert _kinds(events) == [("processed", "HX010")]
    ev = events[0]
    assert (ev["type"], ev["id"], ev["ufts"], ev["prev"]) == ("ar_process", 1010, "1010", None)
    curr = ev["curr"]
    assert curr["style_name"] == "应收冲应付"
    assert curr["partners"] == ["C001", "C003"]
    assert (curr["debit_f"], curr["credit_f"], curr["rows"]) == ("30.50", "30.50", 2)
    assert curr["docs"][0] == {"type": "R0", "id": "D1010", "line_id": 10100, "debit_f": "0.00", "credit_f": "30.50"}
    assert (curr["voucher_id"], curr["year"], curr["period"], curr["min_id"], curr["max_id"]) == (None, 2026, 9, 1010, 1011)
    # 窗口每轮重读，已知的批次不重发。
    assert e.poll() == []
    assert e.mark() == "961"


def test_in_flight_lower_id_is_caught_by_lag_window(env) -> None:
    e = env()
    _seed(e.bridge)
    e.poll()
    e.bridge.add(1030, "HX030")
    assert _kinds(e.poll()) == [("processed", "HX030")]
    # 在途事务先占了 1020，晚于 1030 提交。
    e.bridge.add(1020, "HX020")
    assert _kinds(e.poll()) == [("processed", "HX020")]


def test_watermark_never_moves_back_on_ident_spike(env) -> None:
    e = env()
    _seed(e.bridge)
    e.bridge.add(1100, "HX100")
    e.poll()
    assert e.mark() == "1050"
    e.bridge.ident = 3000
    e.poll()
    assert e.mark() == "1050"


def test_old_closed_period_batch_is_pruned_after_window(env) -> None:
    e = env()
    _seed(e.bridge)
    e.poll()
    assert "9P|HX000" in e.snaps()
    e.bridge.add(1100, "HX100")
    assert _kinds(e.poll()) == [("processed", "HX100")]
    assert "9P|HX000" not in e.snaps()


# ---- 摘要比对 ----


def test_voucher_kinds_and_distinct_event_ids(env) -> None:
    e = env()
    _seed(e.bridge)
    e.poll()
    e.bridge.voucher("HX001", "0012")
    first = e.poll()
    assert _kinds(first) == [("vouchered", "HX001")]
    assert (first[0]["prev"]["voucher_id"], first[0]["curr"]["voucher_id"]) == (None, "0012")
    assert (first[0]["curr"]["gl_sign"], first[0]["curr"]["gl_no"], first[0]["ufts"]) == ("转", 7, "1000|0012|1")
    e.bridge.voucher("HX001", None)
    second = e.poll()
    assert _kinds(second) == [("unvouchered", "HX001")]
    assert (second[0]["curr"]["gl_sign"], second[0]["ufts"]) == (None, "1000||2")
    e.bridge.voucher("HX001", "0012")
    third = e.poll()
    assert _kinds(third) == [("vouchered", "HX001")]
    assert len({first[0]["event_id"], third[0]["event_id"]}) == 2
    assert e.poll() == []


def test_cancelled_removes_snapshot(env) -> None:
    e = env()
    _seed(e.bridge)
    e.poll()
    e.bridge.drop("HX002")
    events = e.poll()
    assert _kinds(events) == [("cancelled", "HX002")]
    assert (events[0]["curr"], events[0]["prev"]["period"], events[0]["id"]) == (None, 10, 1002)
    assert "9P|HX002" not in e.snaps()
    assert e.poll() == []


def test_amount_change_is_modified(env) -> None:
    e = env()
    _seed(e.bridge)
    e.poll()
    e.bridge.rows["AR"][-1]["credit_f"] = "120.00"
    events = e.poll()
    assert _kinds(events) == [("modified", "HX002")]
    assert (events[0]["prev"]["credit_f"], events[0]["curr"]["credit_f"]) == ("100.00", "120.00")


def test_reused_cancel_no_is_cancelled_then_processed(env) -> None:
    e = env()
    _seed(e.bridge)
    e.poll()
    e.bridge.drop("HX002")
    e.bridge.add(1040, "HX002", period=OCT)
    events = e.poll()
    assert _kinds(events) == [("cancelled", "HX002"), ("processed", "HX002")]
    assert (events[0]["ufts"], events[1]["ufts"]) == ("1002", "1040")


def test_batch_committed_after_increment_waits_for_next_round(env) -> None:
    e = env()
    _seed(e.bridge)
    e.poll()
    original = e.bridge.list_process

    def late_commit(acc: str, fields: dict[str, Any]) -> dict[str, Any]:
        # 增量读完、摘要之前，又提交了一批。
        if fields.get("digest") and not any(r["code"] == "HX050" for r in e.bridge.rows["AR"]):
            e.bridge.add(1050, "HX050")
        return original(acc, fields)

    e.bridge.list_process = late_commit
    assert e.poll() == []
    assert "9P|HX050" not in e.snaps()
    assert _kinds(e.poll()) == [("processed", "HX050")]


# ---- 空摘要确认、水位倒退 ----


def test_empty_digest_with_rows_still_listed_aborts(env) -> None:
    e = env()
    _seed(e.bridge)
    e.poll()
    e.bridge.digest_hidden = True
    with pytest.raises(BridgeContractError, match="仍能读到"):
        e.poll()
    assert e.store.outbox_size() == 0
    assert len(e.snaps()) == 3


def test_empty_digest_confirmed_gone_cancels(env) -> None:
    e = env()
    _seed(e.bridge)
    e.poll()
    e.bridge.drop("HX001")
    e.bridge.drop("HX002")
    assert sorted(_kinds(e.poll())) == [("cancelled", "HX001"), ("cancelled", "HX002")]


def test_empty_digest_over_confirm_limit_aborts(env) -> None:
    e = env(empty_scan_confirm_max=1)
    _seed(e.bridge)
    e.poll()
    e.bridge.drop("HX001")
    e.bridge.drop("HX002")
    with pytest.raises(BridgeContractError, match="上限"):
        e.poll()


def test_empty_digest_confirm_honours_stop(env) -> None:
    e = env()
    _seed(e.bridge)
    e.poll()
    e.bridge.drop("HX001")
    e.bridge.drop("HX002")
    e.stop = True
    with pytest.raises(StopRequested):
        e.poll()


def test_identity_regression_resets_quietly(env) -> None:
    e = env()
    _seed(e.bridge)
    e.poll()
    # 库被还原：自增号回到 500 一带，HX001、HX002 都不在了，有一批快照里没有的老批次（只记快照，不发 processed）。
    e.bridge.rows["AR"] = []
    e.bridge.add(501, "HX009")
    events = e.poll()
    assert sorted(_kinds(events)) == [("cancelled", "HX001"), ("cancelled", "HX002")]
    st = e.store.status({("999", "ar_process")})[0]
    assert st.watermark_reset == "952->451"
    assert e.raw_mark() == "451|501|1|451"
    assert "9P|HX009" in e.snaps()
    assert e.poll() == []


def test_small_restore_is_detected_and_recreated_batch_gets_new_ids(env) -> None:
    e = env()
    _seed(e.bridge)
    e.poll()
    e.bridge.add(1010, "HX010")
    first = e.poll()
    assert [(ev["kind"], ev["ufts"]) for ev in first] == [("processed", "1010")]
    # 还原到 HX010 之前：只回退了 8 个号，远小于回看窗口，也要认出来。
    e.bridge.drop("HX010")
    events = e.poll()
    assert _kinds(events) == [("cancelled", "HX010")]
    assert e.store.status({("999", "ar_process")})[0].watermark_reset == "960->952"
    assert e.raw_mark() == "952|1002|1|952"
    # 还原后重做：同一个 Auto_ID、同一个处理号，event_id 不能与还原前的重复。
    e.bridge.add(1010, "HX010")
    again = e.poll()
    assert [(ev["kind"], ev["ufts"]) for ev in again] == [("processed", "1010#1")]
    assert again[0]["event_id"] != first[0]["event_id"]
    e.bridge.voucher("HX010", "0012")
    assert [(ev["kind"], ev["ufts"]) for ev in e.poll()] == [("vouchered", "1010|0012|1|1")]


def test_late_batch_after_window_is_processed_old_batch_is_quiet(env) -> None:
    e = env()
    _seed(e.bridge)
    e.poll()
    e.bridge.add(1100, "HX100")
    assert _kinds(e.poll()) == [("processed", "HX100")]
    assert e.mark() == "1050"
    # 在途超过回看窗口才提交的批次（1003，首轮之后）补发 processed；首轮之前的老批次（940）只记快照。
    e.bridge.add(1003, "HX003")
    e.bridge.add(940, "HX094")
    events = e.poll()
    assert [(ev["kind"], ev["code"], ev["ufts"]) for ev in events] == [("processed", "HX003", "1003")]
    assert events[0]["curr"]["docs"][0]["id"] == "D1003"
    assert {"9P|HX003", "9P|HX094"} <= set(e.snaps())
    assert e.poll() == []


def test_voucher_made_between_window_and_digest_is_not_lost(env) -> None:
    e = env()
    _seed(e.bridge)
    e.poll()
    e.bridge.add(1060, "HX060")
    original = e.bridge.list_process

    def voucher_before_digest(acc: str, fields: dict[str, Any]) -> dict[str, Any]:
        if fields.get("digest"):
            e.bridge.voucher("HX060", "0099")
        return original(acc, fields)

    e.bridge.list_process = voucher_before_digest
    events = e.poll()
    assert _kinds(events) == [("processed", "HX060")]
    assert events[0]["curr"]["voucher_id"] is None
    assert e.snaps()["9P|HX060"].state["voucher_id"] is None
    e.bridge.list_process = original
    events = e.poll()
    assert _kinds(events) == [("vouchered", "HX060")]
    assert events[0]["curr"]["voucher_id"] == "0099"


def test_fiscal_year_from_bridge_wins_over_reg_date(env) -> None:
    e = env()
    e.bridge.open, e.bridge.last_closed = [(2027, 1)], (2026, 12)
    e.poll()
    # 年初对上年末单据做的红票对冲：登记日期 2026-12，期间是 2027 年 1 期。
    e.bridge.add(1100, "HX9N", style="9N", period=(2027, 1), reg_date="2026-12-20", fiscal_year=2027)
    events = e.poll()
    assert _kinds(events) == [("processed", "HX9N")]
    assert (events[0]["curr"]["year"], events[0]["curr"]["period"]) == (2027, 1)
    e.bridge.voucher("HX9N", "0001")
    assert _kinds(e.poll()) == [("vouchered", "HX9N")]


def test_backfill_skips_process_enabled_later(tmp_path) -> None:
    store = Store(str(tmp_path / "s.db"))
    store.register("999", "sale_order")
    store.commit_cycle(Scope("999", "sale_order", "2026-10-02T01:00:00Z"), "1", [], [])
    store.close()
    e = Env(tmp_path, backfill_events=True)
    try:
        _seed(e.bridge)
        assert e.poll() == []
        assert len(e.snaps()) == 3
    finally:
        e.store.close()


def test_ap_side_reads_ap_ledger(env) -> None:
    e = env()
    e.bridge.add(2000, "FK001", flag="AP", style="9J")
    e.poll("ap_process")
    e.bridge.add(2001, "FK002", flag="AP", style="9J")
    events = e.poll("ap_process")
    assert [(ev["type"], ev["kind"], ev["curr"]["flag"]) for ev in events] == [("ap_process", "processed", "AP")]
    assert {call["flag"] for call in e.bridge.calls} == {"AP"}


def test_bad_watermark_is_contract_error(env) -> None:
    e = env()
    e.bridge.list_process = lambda acc, fields: {"ok": True, "items": [], "next": None, "watermark": "x"}
    with pytest.raises(BridgeContractError, match="watermark"):
        e.poll()


# ---- 纯函数 ----


def _batch(**over: Any) -> Batch:
    base = dict(
        style="9P", code="HX1", min_id=10, max_id=11, pz=None, debit="1.00", credit="1.00", rows=2, partners=(), period=SEP
    )
    base.update(over)
    return Batch(**base)


def test_money_normalises() -> None:
    assert (money("-0.004"), money("1e2"), money(None), money(Decimal("3.456"))) == ("0.00", "100.00", "0.00", "3.46")
    with pytest.raises(BridgeContractError):
        money("abc")
    with pytest.raises(BridgeContractError):
        money("NaN")


def test_change_kinds() -> None:
    state = {"min_id": 10, "voucher_id": None, "debit_f": "1.00", "credit_f": "1.00", "rows": 2}
    prev = EntitySnap(key="9P|HX1", fingerprint="10", state=state)
    assert change_kinds(prev, _batch()) == ()
    assert change_kinds(prev, _batch(pz="P1", credit="2.00")) == ("vouchered",)
    assert change_kinds(prev, _batch(rows=3)) == ("modified",)
    assert change_kinds(prev, _batch(min_id=12)) == ("cancelled", "processed")
    done = EntitySnap(key="9P|HX1", fingerprint="10|P1|1", state={**state, "voucher_id": "P1"})
    assert change_kinds(done, _batch()) == ("unvouchered",)


def test_fingerprint_and_lag_helpers() -> None:
    assert next_fingerprint("10", _batch(pz="P1")) == "10|P1|1"
    assert next_fingerprint("10|P1|1", _batch()) == "10||2"
    assert (first_fingerprint(10), first_fingerprint(10, 2)) == ("10", "10#2")
    assert next_fingerprint("10#2", _batch(pz="P1"), 2) == "10|P1|1|2"
    assert next_fingerprint("10|P1|1|2", _batch(), 2) == "10||2|2"
    assert lagged_mark(1000, 1000, 500, 0) == 500
    assert lagged_mark(1000, 1800, 500, 0) == 200
    assert lagged_mark(1000, 1800, 500, 300) == 300
    assert lagged_mark(100, 100, 500, 0) == 0
    assert (style_name("9N"), style_name("ZZ")) == ("红票对冲", "ZZ")


def test_proc_mark_parse() -> None:
    mark = ProcMark(952, 1002, 1, 900)
    assert mark.text() == "952|1002|1|900"
    assert parse_mark(mark.text()) == mark
    assert parse_mark("952") == ProcMark(952, None, 0, 952)
    assert parse_mark("952||0|952") == ProcMark(952, None, 0, 952)
    assert parse_mark(None) is None
    for bad in ("", "x", "1|2", "1|2|3", "1|2||4", "|2|3|4", "1|-2|3|4"):
        assert parse_mark(bad) is None
    assert ProcMark(952, 1002, 0, 952).restored(1001)
    assert not ProcMark(952, 1002, 0, 952).restored(1002)
    assert ProcMark(952, None, 0, 952).restored(951)


def test_fiscal_year_fields() -> None:
    base = {"id": 1, "style": "9N", "code": "HX1", "reg_date": "2026-12-20", "period": 1}
    assert row_from_item(base)["period"] == (2026, 1)
    assert row_from_item({**base, "fiscal_year": 2027})["period"] == (2027, 1)
    item = {"style": "9N", "code": "HX1", "min_id": 1, "max_id": 1, "rows": 1}
    assert batch_from_item(item, (2026, 1)).period == (2026, 1)
    assert batch_from_item({**item, "fiscal_year": 2027}, (2026, 1)).period == (2027, 1)


def test_batch_merge_across_periods() -> None:
    merged = _batch(partners=("C2",)).merge(_batch(min_id=8, max_id=20, pz="P", debit="2.50", partners=("C1",), period=AUG))
    assert (merged.min_id, merged.max_id, merged.pz, merged.debit, merged.rows) == (8, 20, "P", "3.50", 4)
    assert (merged.partners, merged.period) == (("C1", "C2"), AUG)


# ---- BridgeLister ----


class FakeClient:
    def __init__(self) -> None:
        self.got: list[tuple[str, Any]] = []

    def arap_process_list(self, call, query):
        self.got.append(("list", query))
        return {"ok": True}

    def arap_process_digest(self, call, query):
        self.got.append(("digest", query))
        return {"ok": True}


def test_bridge_lister_list_process_routes_by_digest() -> None:
    client = FakeClient()
    lister = BridgeLister(client, {"999": Operator("999", "2026", "op001", "pw")})
    lister.list_process("999", {"flag": "AR", "changed_since": "5", "open_only": False, "limit": 2, "after": 7})
    lister.list_process("999", {"flag": "AP", "digest": True, "fiscal_year": 2026, "periods": [9], "limit": 500})
    assert client.got == [
        ("list", ProcListQuery(flag="AR", changed_since="5", after=7, limit=2, open_only=False)),
        ("digest", ProcDigestQuery(flag="AP", fiscal_year=2026, periods=[9], limit=500)),
    ]
