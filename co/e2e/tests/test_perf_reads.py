"""读路由性能测量（co/e2e/perf_reads*）：翻页、计时记录、各组参数、轮询模拟和输出。全部离线，用假客户端。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from co.client.u8co_client import U8Call, U8CoClient, U8CoError
from co.e2e import perf_reads
from co.e2e.perf_reads_groups import (
    LIST_TYPES,
    NO_TS_ARCHIVES,
    TS_ARCHIVES,
    PerfCtx,
    aging_query,
    gl_year,
    run_archives,
    run_lists,
    run_process,
)
from co.e2e.perf_reads_poll import PRIME_GROUP, run_deletescan, run_poll
from co.e2e.perf_reads_probe import MeteredClient, Probe, params_text, walk
from co.e2e.perf_reads_report import build_doc, render_markdown, write_outputs
from co.e2e.settings_parse import ConfigError
from co.e2e.tests import support

_PASSWORD = "pw-placeholder-123"
_SECRET = "ab" * 32


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.01
        return self.now


def _paged(after: object, first_next: object, mark: str = "5000") -> dict:
    # 两页：首页带 next，第二页没有。
    if after is None:
        return {"ok": True, "items": [{"id": 1}, {"id": 2}], "next": first_next, "watermark": mark}
    return {"ok": True, "items": [{"id": 3}], "next": None, "watermark": mark}


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.fail_archive = ""

    def health(self) -> dict:
        self.calls.append(("health", None))
        return {"ok": True, "logins_opened": 4, "logins_reused": 0, "logins_closed": 4}

    def list_vouchers(self, call: U8Call, query) -> dict:
        self.calls.append(("list", query))
        if query.changed_since or query.limit == 1:
            return {"ok": True, "items": [], "next": None, "watermark": "5000"}
        return _paged(query.after, 10)

    def arc_list(self, call: U8Call, query) -> dict:
        self.calls.append(("arc", query))
        if query.archive == self.fail_archive:
            raise U8CoError(403, "forbidden", "无权读取 " + _PASSWORD)
        if query.changed_since or query.limit == 1:
            return {"ok": True, "items": [], "next": None, "watermark": "6000"}
        return _paged(query.after or None, "C010", "6000")

    def search(self, call: U8Call, query) -> dict:
        self.calls.append(("search", query))
        return {"ok": True, "items": [{"id": 1}], "next": None}

    def report_arap_aging(self, call: U8Call, query) -> dict:
        self.calls.append(("aging", query))
        return {"ok": True, "items": [], "next": None}

    def gl_digest(self, call: U8Call, query) -> dict:
        self.calls.append(("gl", query))
        if query.after is None:
            return {"ok": True, "periods": [8, 9], "items": [{"no": 1}], "next": "9.1.1"}
        return {"ok": True, "periods": [8, 9], "items": [{"no": 2}], "next": None}

    def arap_process_list(self, call: U8Call, query) -> dict:
        self.calls.append(("proc", query))
        return _paged(query.after, 77, "12000") if query.limit != 1 else {"ok": True, "items": [], "next": None, "watermark": "300"}

    def arap_process_digest(self, call: U8Call, query) -> dict:
        self.calls.append(("procd", query))
        return {"ok": True, "items": [], "next": None}

    def of(self, kind: str) -> list:
        return [query for name, query in self.calls if name == kind]


def _ctx(client: _FakeClient | None = None, repeat: int = 1) -> PerfCtx:
    probe = Probe(client or _FakeClient(), needles=[_PASSWORD, _SECRET], clock=_Clock())
    call = U8Call("803", "2024", "op001", _PASSWORD, "2026-10-02")
    return PerfCtx(probe, call, "2026-10-02", repeat=repeat)


class ProbeTests(unittest.TestCase):
    def test_records_success_with_items_and_bytes(self) -> None:
        probe = Probe(_FakeClient(), clock=_Clock())
        payload = probe.call("vouchers/list", "type=x", lambda: {"ok": True, "items": [1, 2]})
        self.assertEqual(payload, {"ok": True, "items": [1, 2]})
        record = probe.records[0]
        self.assertTrue(record.ok)
        self.assertEqual((record.status, record.items), (200, 2))
        self.assertGreater(record.bytes, 0)
        self.assertGreater(record.ms, 0)

    def test_error_is_recorded_and_scrubbed(self) -> None:
        probe = Probe(_FakeClient(), needles=[_PASSWORD], clock=_Clock())

        def boom() -> dict:
            raise U8CoError(503, "u8_license_full", "点数已满 " + _PASSWORD)

        self.assertIsNone(probe.call("health", "", boom))
        record = probe.records[0]
        self.assertFalse(record.ok)
        self.assertEqual((record.status, record.code), (503, "u8_license_full"))
        self.assertNotIn(_PASSWORD, record.message)

    def test_client_validation_error(self) -> None:
        probe = Probe(_FakeClient(), clock=_Clock())

        def bad() -> dict:
            raise ValueError("limit 必须是 1 到 500 的整数")

        probe.call("vouchers/list", "", bad)
        self.assertEqual(probe.records[0].code, "client_invalid")

    def test_metered_client_counts_bytes(self) -> None:
        client = MeteredClient("http://203.0.113.5:18090/u8co", _SECRET)
        with mock.patch.object(U8CoClient, "_exchange", return_value=(200, b'{"ok":true}')):
            probe = Probe(client, clock=_Clock())
            probe.call("health", "", client.health)
        self.assertEqual((client.last_status, client.last_bytes), (200, 11))
        self.assertEqual(probe.records[0].bytes, 11)

    def test_params_text(self) -> None:
        self.assertEqual(params_text(type="a", keys_only=True, after=None, changed_since="", red=False, limit=5), "type=a keys_only limit=5")


class WalkTests(unittest.TestCase):
    def test_follows_next_until_null(self) -> None:
        probe = Probe(_FakeClient(), clock=_Clock())
        pages = walk(probe, "r", str, lambda after: _paged(after, 10))
        self.assertEqual(len(pages), 2)
        self.assertEqual([item.params for item in probe.records], ["None", "10"])

    def test_stops_when_cursor_repeats(self) -> None:
        probe = Probe(_FakeClient(), clock=_Clock())
        pages = walk(probe, "r", str, lambda after: {"ok": True, "items": [], "next": 7})
        self.assertEqual(len(pages), 2)

    def test_stops_on_error(self) -> None:
        probe = Probe(_FakeClient(), clock=_Clock())

        def page(after: object) -> dict:
            if after is not None:
                raise U8CoError(500, "internal", "x")
            return _paged(after, 10)

        self.assertEqual(len(walk(probe, "r", str, page)), 1)
        self.assertFalse(probe.records[-1].ok)


class GroupTests(unittest.TestCase):
    def test_lists_cover_every_type(self) -> None:
        ctx = _ctx()
        run_lists(ctx)
        queries = ctx.client.of("list")
        self.assertEqual(len(queries), 4 * len(LIST_TYPES))  # 首页、两页 keys_only、changed_since
        self.assertEqual({q.kind for q in queries}, set(LIST_TYPES))
        self.assertIn("ar_note", {q.kind for q in queries})
        since = [q for q in queries if q.changed_since]
        self.assertTrue(all(q.changed_since == "5000" and q.limit == 500 for q in since))
        self.assertEqual(ctx.marks["list:other_out"], "5000")

    def test_archives_skip_changed_since_without_rowversion(self) -> None:
        client = _FakeClient()
        client.fail_archive = "operator"
        ctx = _ctx(client)
        run_archives(ctx)
        since = {q.archive for q in client.of("arc") if q.changed_since}
        self.assertIn("position", since)
        self.assertFalse(since & set(NO_TS_ARCHIVES))
        failed = [r for r in ctx.probe.records if not r.ok]
        self.assertEqual(len(failed), 2)  # 首页和 keys_only 首页各一次
        self.assertNotIn(_PASSWORD, failed[0].message)

    def test_gl_second_page_keeps_periods(self) -> None:
        ctx = _ctx()
        gl_year(ctx, 2026)
        first, second = ctx.client.of("gl")
        self.assertEqual((first.fiscal_year, first.closed_periods, first.periods), (2026, 1, None))
        self.assertEqual((second.periods, second.after, second.closed_periods), ([8, 9], "9.1.1", None))

    def test_aging_matches_monthly_job(self) -> None:
        query = aging_query("ar", "2026-10-02")
        self.assertEqual((query.basis, query.group_by, query.overdue_only), ("due", "partner_person", True))
        self.assertEqual((query.buckets, query.limit, query.default_credit_days), ((30, 60, 90, 180), 1000, 60))

    def test_process_walks_both_sides(self) -> None:
        ctx = _ctx()
        run_process(ctx)
        increments = ctx.client.of("proc")
        self.assertEqual([q.flag for q in increments], ["AR", "AR", "AP", "AP"])
        self.assertTrue(all(q.changed_since == "0" for q in increments))
        self.assertEqual([q.fiscal_year for q in ctx.client.of("procd")], [2026, 2026])
        self.assertEqual(ctx.marks["proc:AR"], "12000")


class PollTests(unittest.TestCase):
    def test_poll_primes_and_repeats(self) -> None:
        ctx = _ctx(repeat=2)
        run_poll(ctx)
        primes = [r for r in ctx.probe.records if r.group == PRIME_GROUP]
        self.assertEqual(len(primes), len(LIST_TYPES) + len(TS_ARCHIVES) + 2)
        note = ctx.notes["poll"]
        self.assertEqual(len(note["rounds_ms"]), 2)
        self.assertEqual(note["skipped"], [])
        since = [q.changed_since for q in ctx.client.of("proc") if q.limit != 1]
        self.assertEqual(since[:2], ["0", "0"])  # 水位 300 回退 500 后夹到 0
        self.assertEqual(len(ctx.client.of("gl")), 4)

    def test_deletescan_counts_keys(self) -> None:
        ctx = _ctx()
        run_deletescan(ctx)
        self.assertEqual(ctx.notes["deletescan"]["keys"], 3 * (len(LIST_TYPES) + len(TS_ARCHIVES)))
        self.assertTrue(all(q.keys_only for q in ctx.client.of("list") + ctx.client.of("arc")))


class CliTests(unittest.TestCase):
    def test_parse_only(self) -> None:
        self.assertEqual(perf_reads.parse_only("poll,lists"), ("lists", "poll"))
        self.assertEqual(perf_reads.parse_only(""), perf_reads.GROUPS)
        with self.assertRaises(ConfigError):
            perf_reads.parse_only("lists,bogus")

    def test_missing_config_exits_2(self) -> None:
        with mock.patch.object(perf_reads.settings, "conf", side_effect=ConfigError("找不到 e2e 配置")), mock.patch("sys.stderr"):
            code = perf_reads.main([])
        self.assertEqual(code, 2)

    def test_load_setup_uses_conf(self) -> None:
        cfg = support.lane_config("803")
        operator = mock.Mock(year="2024", code="op001", password=_PASSWORD)
        with mock.patch.object(perf_reads.settings, "conf", return_value=cfg), mock.patch.object(
            perf_reads, "load_operator", return_value=operator
        ), mock.patch.object(perf_reads, "read_service_secret", return_value=_SECRET):
            setup = perf_reads.load_setup()
        self.assertEqual((setup.call.acc, setup.call.year, setup.call.operator), ("803", "2024", "op001"))
        self.assertEqual(setup.secret, _SECRET)

    def test_bad_repeat_exits_2(self) -> None:
        with mock.patch("sys.stderr"):
            self.assertEqual(perf_reads.main(["--repeat", "0"]), 2)

    def test_main_writes_outputs_without_secrets(self) -> None:
        cfg = support.lane_config("803")
        call = U8Call("803", "2024", "op001", _PASSWORD, "2026-10-02")
        setup = perf_reads.Setup(cfg, call, _SECRET)
        client = _FakeClient()
        client.fail_archive = "role"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            perf_reads, "_prepare", return_value=(perf_reads.GROUPS, setup, client)
        ), mock.patch("builtins.print"):
            code = perf_reads.main(["--out", tmp, "--repeat", "2"])
            data = (Path(tmp) / "perf.json").read_text(encoding="utf-8")
            text = (Path(tmp) / "perf.md").read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        doc = json.loads(data)
        self.assertEqual(doc["failed"], 2)
        self.assertEqual(set(perf_reads.GROUPS) - set(doc["groups"]), set())
        self.assertEqual(doc["meta"]["logins"]["opened"], 0)
        for blob in (data, text):
            self.assertNotIn(_PASSWORD, blob)
            self.assertNotIn(_SECRET, blob)
        self.assertIn("## 失败", text)


class ReportTests(unittest.TestCase):
    def test_render_and_write(self) -> None:
        ctx = _ctx()
        ctx.probe.group = "search"
        ctx.probe.call("vouchers/search", "type=a|b", lambda: {"ok": True, "items": []})
        doc = build_doc({"acc": "803", "logins": {}}, ctx.probe.records, {"search": 12.5, "gl": 1.0}, {})
        self.assertEqual(doc["groups"]["search"]["wall_ms"], 12.5)
        self.assertEqual(doc["groups"]["gl"]["calls"], 0)
        self.assertIn("type=a\\|b", render_markdown(doc))
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_outputs(Path(tmp) / "perf-x", doc)
            self.assertTrue(all(path.is_file() for path in paths))


if __name__ == "__main__":
    unittest.main()
