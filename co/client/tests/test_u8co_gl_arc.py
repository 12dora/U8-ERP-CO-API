"""响应上限、总账凭证、档案、列表、现存量的正文和命令行解析。只监听 127.0.0.1。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import (
    PASSWORD,
    SECRET,
    _AUTH,
    _argv,
    _assert_signed,
    _JsonFile,
    _run_main,
    _Running,
)
from co.client.u8co_cli import _parser
from co.client.u8co_cli_gl_arc import arc_record, gl_draft, keyset_after, parse_filters
from co.client.u8co_cli import _gen_draft
from co.client.u8co_client import U8Call, U8CoClient, VoucherGen
from co.client.u8co_errors import U8CoRejected, U8CoTransport, parse_payload
from co.client.u8co_gl_arc import (
    ArcQuery,
    ArcRecord,
    GlDraft,
    GlKey,
    GlQuery,
    StockQuery,
    VoucherQuery,
)

_KEY = GlKey(9, "转", 1)
_LINES = [
    {"account": "660202", "digest": "测试", "debit": 100, "dept": "D901"},
    {"account": "100201", "digest": "测试", "credit": 100, "cash_flow": [{"item": "07", "credit": 100}]},
]


def _call() -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-09-27")


def _offline() -> U8CoClient:
    return U8CoClient("http://127.0.0.1:9/u8co", SECRET, timeout=5)


class CapTests(unittest.TestCase):
    def test_large_success_is_accepted(self) -> None:
        raw = b'{"ok":true,"pad":"' + b"x" * 200000 + b'"}'
        self.assertEqual(len(parse_payload(200, raw)["pad"]), 200000)

    def test_over_8_mib_is_bad_response(self) -> None:
        raw = b'{"ok":true,"pad":"' + b"x" * (8 * 1024 * 1024) + b'"}'
        with self.assertRaises(U8CoTransport) as caught:
            parse_payload(200, raw)
        self.assertEqual(caught.exception.code, "bad_response")
        self.assertIn("8 MiB", caught.exception.message)

    def test_error_stays_small(self) -> None:
        raw = b'{"ok":false,"code":"u8_rejected","message":"' + b"x" * 70000 + b'"}'
        with self.assertRaises(U8CoTransport) as caught:
            parse_payload(409, raw)
        self.assertEqual(caught.exception.code, "bad_response")
        with self.assertRaises(U8CoRejected):
            parse_payload(409, b'{"ok":false,"code":"u8_rejected","message":"no"}')


class WireTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[str, dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertNotIn("password", sent)
        self.assertEqual(list(sent)[:5], _AUTH)
        return captured["path"], sent

    def test_gl_key_routes(self) -> None:
        path, sent = self._send(lambda client: client.gl_load(_call(), _KEY))
        self.assertEqual(path, "/u8co/v1/gl/vouchers/load")
        self.assertEqual(list(sent), _AUTH + ["period", "sign", "no"])
        self.assertEqual((sent["period"], sent["sign"], sent["no"]), (9, "转", 1))
        for op in ("void", "unvoid", "verify", "unverify", "sign", "unsign", "delete"):
            path, sent = self._send(lambda client, op=op: client.gl_op(_call(), op, _KEY))
            self.assertEqual(path, "/u8co/v1/gl/vouchers/" + op)
            self.assertEqual(list(sent), _AUTH + ["period", "sign", "no"])

    def test_gl_create_and_update(self) -> None:
        draft = GlDraft({"sign": "转", "date": "2026-09-27"}, _LINES)
        path, sent = self._send(lambda client: client.gl_create(_call(), draft))
        self.assertEqual(path, "/u8co/v1/gl/vouchers/create")
        self.assertEqual(list(sent), _AUTH + ["head", "lines"])
        self.assertEqual(sent["lines"][1]["cash_flow"], [{"item": "07", "credit": 100}])
        path, sent = self._send(lambda client: client.gl_update(_call(), _KEY, GlDraft({"sign": "转"}, _LINES)))
        self.assertEqual(path, "/u8co/v1/gl/vouchers/update")
        self.assertEqual(list(sent), _AUTH + ["period", "sign", "no", "head", "lines"])

    def test_gl_list_optional_fields(self) -> None:
        query = GlQuery(1, 12, sign="转", date_to="2026-09-30", state="unaudited", after="c1", limit=20)
        path, sent = self._send(lambda client: client.gl_list(_call(), query))
        self.assertEqual(path, "/u8co/v1/gl/vouchers/list")
        self.assertEqual(
            list(sent),
            _AUTH + ["period_from", "period_to", "sign", "date_to", "state", "after", "limit"],
        )
        _path, sent = self._send(lambda client: client.gl_list(_call(), GlQuery(3, 3)))
        self.assertEqual(list(sent), _AUTH + ["period_from", "period_to"])

    def test_archive_routes(self) -> None:
        record = ArcRecord("inventory", "A01", {"name": "测试", "spec": None}, "INV0005")
        path, sent = self._send(lambda client: client.arc_create(_call(), record))
        self.assertEqual(path, "/u8co/v1/archives/create")
        self.assertEqual(list(sent), _AUTH + ["archive", "code", "fields", "template"])
        self.assertIsNone(sent["fields"]["spec"])
        update = ArcRecord("customer", "C900001", {"abbrname": "简称"})
        path, sent = self._send(lambda client: client.arc_update(_call(), update))
        self.assertEqual(path, "/u8co/v1/archives/update")
        self.assertEqual(list(sent), _AUTH + ["archive", "code", "fields"])
        for op in ("get", "delete"):
            method = "arc_" + op
            path, sent = self._send(lambda client, m=method: getattr(client, m)(_call(), "person", "op001"))
            self.assertEqual(path, "/u8co/v1/archives/" + op)
            self.assertEqual(list(sent), _AUTH + ["archive", "code"])
        query = ArcQuery("vendor_class", code_prefix="S9", changed_since="12345", limit=500)
        path, sent = self._send(lambda client: client.arc_list(_call(), query))
        self.assertEqual(path, "/u8co/v1/archives/list")
        self.assertEqual(list(sent), _AUTH + ["archive", "code_prefix", "changed_since", "limit"])

    def test_voucher_list_and_stock(self) -> None:
        query = VoucherQuery("ar_bill", {"verified": True, "cus_code": "C1"}, True, "99", 0, 100)
        path, sent = self._send(lambda client: client.list_vouchers(_call(), query))
        self.assertEqual(path, "/u8co/v1/vouchers/list")
        self.assertEqual(list(sent), _AUTH + ["type", "filter", "keys_only", "changed_since", "after", "limit"])
        self.assertEqual(sent["filter"], {"verified": True, "cus_code": "C1"})
        self.assertEqual(sent["after"], 0)
        _path, sent = self._send(lambda client: client.list_vouchers(_call(), VoucherQuery("sale_order")))
        self.assertEqual(list(sent), _AUTH + ["type"])
        path, sent = self._send(lambda client: client.stock_current(_call(), StockQuery(wh="01", after=7, limit=5)))
        self.assertEqual(path, "/u8co/v1/stock/current")
        self.assertEqual(list(sent), _AUTH + ["wh", "after", "limit"])


class SourceTypeTests(unittest.TestCase):
    def test_generate_source_type_body(self) -> None:
        lines = [{"source_line_id": 11, "quantity": 2}]
        head = {"cPBVCode": "0000000001", "cPBVBillType": "01"}
        draft = VoucherGen("purchase_invoice", head, lines, "purchase_in")
        with _Running() as bridge:
            call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-27", 8)
            U8CoClient(bridge.base_url, SECRET, timeout=5).generate_voucher(call, draft)
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/generate")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "source_type", "head", "lines"])
        self.assertEqual(sent["source_type"], "purchase_in")
        self.assertEqual(sent["head"]["cPBVCode"], "0000000001")

    def test_generate_arrival_body(self) -> None:
        lines = [{"source_line_id": 21, "quantity": 3}]
        draft = VoucherGen("arrival", {"cWhCode": "01", "dDate": "2026-09-27"}, lines, "purchase_order")
        with _Running() as bridge:
            call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-27", 4)
            U8CoClient(bridge.base_url, SECRET, timeout=5).generate_voucher(call, draft)
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(list(sent), _AUTH + ["type", "id", "source_type", "head", "lines"])
        self.assertEqual((sent["type"], sent["id"], sent["source_type"]), ("arrival", 4, "purchase_order"))
        self.assertEqual(sent["lines"], lines)
        with _JsonFile({"lines": lines}) as path:
            parsed = _parser().parse_args(_argv("generate", "--type", "arrival", "--id", "4", "--file", path))
            self.assertEqual(_gen_draft(parsed).source_type, "")

    def test_source_type_pairs(self) -> None:
        call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-27", 8)
        lines = [{"source_line_id": 1, "quantity": 1}]
        pairs = (
            ("purchase_in", "sale_order"), ("dispatch", "purchase_order"),
            ("purchase_invoice", "purchase_order"), ("arrival", "purchase_in"),
        )
        for kind, source in pairs:
            with self.assertRaises(ValueError) as caught:
                _offline().generate_voucher(call, VoucherGen(kind, None, lines, source))
            self.assertIn("来源", str(caught.exception))
        gone = _parser().parse_args(_argv("delete", "--type", "purchase_invoice", "--id", "3"))
        self.assertEqual(gone.type, "purchase_invoice")

    def test_cli_source_type(self) -> None:
        with _JsonFile({"lines": [{"source_line_id": 5, "quantity": 1}]}) as path:
            parsed = _parser().parse_args(
                _argv("generate", "--type", "purchase_in", "--id", "9", "--file", path, "--source-type", "qm_incoming_check"),
            )
            draft = _gen_draft(parsed)
            bare = _parser().parse_args(_argv("generate", "--type", "purchase_invoice", "--id", "9", "--file", path))
            plain = _gen_draft(bare)
        self.assertEqual(draft.source_type, "qm_incoming_check")
        self.assertEqual(plain.source_type, "")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _parser().parse_args(
                    _argv("generate", "--type", "purchase_in", "--id", "9", "--file", "x", "--source-type", "no_such_type"),
                )


class ValidationTests(unittest.TestCase):
    def _refused(self, invoke: Callable[[U8CoClient], object], text: str) -> None:
        with self.assertRaises(ValueError) as caught:
            invoke(_offline())
        self.assertIn(text, str(caught.exception))

    def test_gl_rules(self) -> None:
        self._refused(lambda c: c.gl_load(_call(), GlKey(13, "转", 1)), "period")
        self._refused(lambda c: c.gl_load(_call(), GlKey(1, "转账凭证", 1)), "sign")
        self._refused(lambda c: c.gl_load(_call(), GlKey(1, "转", 0)), "no")
        self._refused(lambda c: c.gl_op(_call(), "post", _KEY), "不支持")
        self._refused(lambda c: c.gl_create(_call(), GlDraft({"sign": "转"}, _LINES[:1])), "2 到 200")
        self._refused(lambda c: c.gl_create(_call(), GlDraft({}, _LINES)), "sign")
        self._refused(lambda c: c.gl_update(_call(), _KEY, GlDraft({"date": "2026-09-27"}, _LINES)), "sign")
        bad = [_LINES[0], {"account": "1", "credit": 1, "cash_flow": {"item": "07"}}]
        self._refused(lambda c: c.gl_create(_call(), GlDraft({"sign": "转"}, bad)), "cash_flow")
        nested = [_LINES[0], {"account": "1", "credit": {"x": 1}}]
        self._refused(lambda c: c.gl_create(_call(), GlDraft({"sign": "转"}, nested)), "分录")
        self._refused(lambda c: c.gl_list(_call(), GlQuery(5, 4)), "period_from")
        self._refused(lambda c: c.gl_list(_call(), GlQuery(1, 2, state="open")), "state")
        self._refused(lambda c: c.gl_list(_call(), GlQuery(1, 2, date_from="2026-9-1")), "yyyy-MM-dd")
        self._refused(lambda c: c.gl_list(_call(), GlQuery(1, 2, limit=201)), "limit")

    def test_archive_rules(self) -> None:
        self._refused(lambda c: c.arc_get(_call(), "salary", "01"), "档案类型")
        self._refused(lambda c: c.arc_get(_call(), "customer", ""), "code")
        self._refused(lambda c: c.arc_update(_call(), ArcRecord("customer", "C1", {})), "没有要修改")
        self._refused(lambda c: c.arc_update(_call(), ArcRecord("customer", "C1", {"code": "C2"})), "编码")
        self._refused(lambda c: c.arc_update(_call(), ArcRecord("customer", "C1", {"name": "x"}, "C0")), "template")
        self._refused(lambda c: c.arc_create(_call(), ArcRecord("customer", "C1", {"name": [1]})), "fields")
        self._refused(lambda c: c.arc_list(_call(), ArcQuery("customer", changed_since="0x1")), "changed_since")
        self._refused(lambda c: c.arc_list(_call(), ArcQuery("customer", limit=501)), "limit")

    def test_list_rules(self) -> None:
        self._refused(lambda c: c.list_vouchers(_call(), VoucherQuery("gl")), "单据类型")
        self._refused(lambda c: c.list_vouchers(_call(), VoucherQuery("sale_order", {"verified": "1"})), "布尔")
        self._refused(lambda c: c.list_vouchers(_call(), VoucherQuery("sale_order", limit=0)), "limit")
        self._refused(lambda c: c.list_vouchers(_call(), VoucherQuery("sale_order", after=-1)), "after")
        self._refused(lambda c: c.stock_current(_call(), StockQuery(after=2**31)), "after")
        self._refused(lambda c: c.stock_current(_call(), StockQuery(wh="0" * 21)), "wh")


class CliTests(unittest.TestCase):
    def test_parse_gl_commands(self) -> None:
        parsed = _parser().parse_args(_argv("gl-unsign", "--period", "9", "--sign", "付", "--no", "12"))
        self.assertEqual((parsed.command, parsed.period, parsed.sign, parsed.no), ("gl-unsign", 9, "付", 12))
        listing = _parser().parse_args(
            _argv("gl-list", "--period-from", "1", "--period-to", "3", "--state", "void", "--limit", "10"),
        )
        self.assertEqual((listing.period_from, listing.period_to, listing.state, listing.limit), (1, 3, "void", 10))
        self.assertEqual(listing.sign, "")
        with _JsonFile({"head": {"sign": "转"}, "lines": _LINES}) as path:
            draft = gl_draft(path)
        self.assertEqual(draft.head, {"sign": "转"})
        self.assertEqual(len(draft.lines), 2)

    def test_parse_archive_files(self) -> None:
        with _JsonFile({"fields": {"name": "测试"}, "template": "INV0005"}) as path:
            parsed = _parser().parse_args(_argv("arc-create", "--archive", "inventory", "--code", "A01", "--file", path))
            record = arc_record(parsed, True)
        self.assertEqual(record, ArcRecord("inventory", "A01", {"name": "测试"}, "INV0005"))
        with _JsonFile({"fields": {"name": "x"}, "template": "C0"}) as path:
            parsed = _parser().parse_args(_argv("arc-update", "--archive", "customer", "--code", "C1", "--file", path))
            with self.assertRaises(SystemExit) as caught:
                arc_record(parsed, False)
        self.assertIn("fields", str(caught.exception))
        with _JsonFile({"name": "x"}) as path:
            parsed = _parser().parse_args(_argv("arc-create", "--archive", "customer", "--code", "C1", "--file", path))
            with self.assertRaises(SystemExit):
                arc_record(parsed, True)

    def test_filters_and_cursor(self) -> None:
        self.assertEqual(
            parse_filters(["verified=false", "cus_code=C1", "red=1", "code=A=B"]),
            {"verified": False, "cus_code": "C1", "red": True, "code": "A=B"},
        )
        self.assertIsNone(parse_filters([]))
        for bad in (["verified=yes"], ["cus_code"], ["=x"], ["code=a", "code=b"]):
            with self.assertRaises(SystemExit):
                parse_filters(bad)
        self.assertEqual(keyset_after("42"), 42)
        with self.assertRaises(SystemExit):
            keyset_after("C1")
        self.assertIsNone(keyset_after(None))

    def test_choices_reject_wrong_values(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("arc-get", "--archive", "salary", "--code", "1"))
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("gl-list", "--period-from", "1", "--period-to", "2", "--state", "open"))
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("list", "--type", "gl_voucher"))
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("gl-void", "--period", "1", "--sign", "转"))
        for kind in ("ar_receipt", "ap_payment", "ar_bill", "ap_bill"):
            created = _parser().parse_args(_argv("create", "--type", kind, "--head-json", "{}", "--lines-json", "[]"))
            self.assertEqual(created.type, kind)
        mo = _parser().parse_args(_argv("generate", "--type", "material_out", "--id", "5", "--file", "x"))
        self.assertEqual(mo.type, "material_out")
        gone = _parser().parse_args(_argv("delete", "--type", "product_in", "--id", "5"))
        self.assertEqual(gone.type, "product_in")

    def test_main_wires_gl_and_archives(self) -> None:
        seen: dict[str, Any] = {}

        def _op(self: U8CoClient, call: U8Call, op: str, key: GlKey) -> dict[str, Any]:
            seen["op"] = (op, key)
            return {"ok": True}

        def _create(self: U8CoClient, call: U8Call, draft: GlDraft) -> dict[str, Any]:
            seen["create"] = draft
            return {"ok": True}

        def _update(self: U8CoClient, call: U8Call, record: ArcRecord) -> dict[str, Any]:
            seen["arc"] = record
            return {"ok": True}

        argv = _argv("gl-void", "--period", "9", "--sign", "转", "--no", "3")
        self.assertEqual(_run_main(argv, "gl_op", _op), 0)
        self.assertEqual(seen["op"], ("void", GlKey(9, "转", 3)))
        with _JsonFile({"head": {"sign": "转"}, "lines": _LINES}) as path:
            self.assertEqual(_run_main(_argv("gl-create", "--file", path), "gl_create", _create), 0)
        self.assertEqual(seen["create"].head, {"sign": "转"})
        with _JsonFile({"fields": {"abbrname": "简"}}) as path:
            argv = _argv("arc-update", "--archive", "vendor", "--code", "S900001", "--file", path)
            self.assertEqual(_run_main(argv, "arc_update", _update), 0)
        self.assertEqual(seen["arc"], ArcRecord("vendor", "S900001", {"abbrname": "简"}))

    def test_main_wires_lists(self) -> None:
        seen: dict[str, Any] = {}

        def _list(self: U8CoClient, call: U8Call, query: VoucherQuery) -> dict[str, Any]:
            seen["list"] = query
            return {"ok": True}

        def _stock(self: U8CoClient, call: U8Call, query: StockQuery) -> dict[str, Any]:
            seen["stock"] = query
            return {"ok": True}

        argv = _argv("list", "--type", "ap_payment", "--filter", "verified=true", "--keys-only", "--after", "15")
        self.assertEqual(_run_main(argv, "list_vouchers", _list), 0)
        self.assertEqual(seen["list"], VoucherQuery("ap_payment", {"verified": True}, True, "", 15, None))
        argv = _argv("stock", "--wh", "01", "--inv", "INV0005", "--limit", "50")
        self.assertEqual(_run_main(argv, "stock_current", _stock), 0)
        self.assertEqual(seen["stock"], StockQuery("01", "INV0005", "", None, 50))


if __name__ == "__main__":
    unittest.main()
