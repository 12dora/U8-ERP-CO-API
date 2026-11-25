"""应收票据用例（notes / notes_more）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import notes as notes
from co.e2e.casespec.catalog import build_cases, known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.notes_sql import (
    ap_pick_sql,
    ap_snap_sql,
    detail_sql,
    marks_sql,
    note_id_sql,
    note_proc_sql,
    note_sql,
    notes_samples,
    setup_sql,
    split_snap_sql,
    sub_sql,
)
from co.e2e.casespec.notes_util import cash_items
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e import testdata
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = (
    "note-register-cycle",
    "note-settle-cycle",
    "note-discount-cycle",
    "note-endorse-cycle",
    "note-split-settle-cycle",
)
_SPEC = CaseSpec("notes", "s")
_STEP = Step("custom")
_PZ = "AR0000000000007"
_RECEIPT = 55
_SETUP = "C0001|D01|P01|收款人甲|112101|1002990101|611199"
# 回读 SQL 的特征串（每种 SQL 一个，互不包含）。
_K_SETUP = "N'pjyfrzkm'"
_K_NOTE = "N'AR50' + n.cVouchID"
_K_SUB = "s.csbnstart"
_K_DETAIL = "d.iFlag = 3"
_K_GL = "COUNT(DISTINCT CONVERT(varchar(4), g.iperiod)"
_K_MARKS = "s.cPzID = N'"
_K_AP_PICK = "WITH p AS"
_K_AP_SNAP = "y.iOriTotal"
_K_SPLIT_PICK = "r.cavailend - r.cavailstart"
_K_SPLIT_SNAP = "CHECKSUM_AGG"
_K_NOTE_ID = "MAX(n.Auto_ID)"
_K_NOTE_PROC = "ORDER BY s.ID DESC"
_WHOLE = U8CoError(409, "state_mismatch", "整张票据须一次处理全部金额（余额 100.00）")
_NO_CASH = U8CoError(409, "state_mismatch", "科目 660399 的现金流量项目无法按数据来源唯一确定，请用 cash_items 指定，或在 U8 客户端制单")
_GONE_SUB = "0|||0.00|0.00||0||"
_GONE_DETAIL = "0|0|0.00|0.00||||0|0.00|0.00||"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _no(tail: str) -> str:
    return "E2E20261002090000" + tail


def _net(code: str) -> str:
    return f"g.ccode, N''))) = N'{code}'"


def _note(remain: str = "100.00", audited: str = "1") -> str:
    return f"1|1|301|100.00|{remain}|112101|C0001|{_RECEIPT}|1|1|{audited}"


_NOTE_GONE = "0|0||0.00|0.00||||0|0|0"


class _Replay:
    def __init__(self, answers: dict[str, list]) -> None:
        self.answers = {key: list(val) for key, val in answers.items()}
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers[route].pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got

    def routes(self) -> list[str]:
        return [route.removeprefix("/v1/") for route, _fields in self.calls]


class _Sql:
    """按 SQL 里的特征串依次回答（每个键一个队列），没有匹配就报错。"""

    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]

    def __call__(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.answers:
            if key in query and rows:
                return "ROW:" + rows.pop(0) + "\n"
        raise AssertionError("没有准备回答：" + query[:120])

    def left(self) -> list[str]:
        return [key for key, rows in self.answers if rows]


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-02", bag={})


def _ok(**extra: object) -> dict:
    body: dict = {"ok": True}
    body.update(extra)
    return body


def _run(op: str, client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql), mock.patch.object(notes, "new_note_no", _no):
        return notes.NOTES_OPS[op](_env(client), _SPEC, _STEP)


def _created(tail: str) -> dict:
    return _ok(note_no=_no(tail), note_id=9, receipt_id=_RECEIPT, receipt_code="SK1")


class RegistrationTests(unittest.TestCase):
    def test_registered_with_ops_tags_routes(self) -> None:
        names = known_names()
        cases = {spec.name: spec for spec in build_cases({})}
        for name in _NAMES:
            with self.subTest(case=name):
                self.assertEqual(names.count(name), 1)
                spec = cases[name]
                self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
                self.assertTrue(spec.routes)
                self.assertIn("period:close", auto_tags(spec))
                self.assertIn("notes:AR", auto_tags(spec))

    def test_conflicts(self) -> None:
        cases = {spec.name: spec for spec in build_cases({})}
        pairs = (
            ("note-endorse-cycle", "arap-transfer-cycle"),
            ("note-settle-cycle", "ar-receipt-cycle"),
            ("note-split-settle-cycle", "note-settle-cycle"),
            ("note-discount-cycle", "arap-merge-cycle"),
        )
        for left, right in pairs:
            with self.subTest(pair=(left, right)):
                self.assertTrue(conflicts(auto_tags(cases[left]), auto_tags(cases[right])))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in notes_samples():
            with self.subTest(sample=name):
                self.assertEqual(sql_block_reason(query), "", name)
                self.assertIn((name, query), rows)
        self.assertIn("i.iyear = 2026", setup_sql("2026-10-02"))
        self.assertIn("n.cVouchID = N'E2E2026100201'", note_sql("E2E2026100201"))
        self.assertIn("n.cVouchID = N'E2E2026100201'", note_id_sql("E2E2026100201"))
        # 背书整张票据：挑的发票行余额至少是票面。
        self.assertIn(">= 100.00\n", ap_pick_sql("2026-10-02"))
        proc = note_proc_sql("E2E2026100201")
        self.assertIn("n.cVouchID = N'E2E2026100201'", proc)
        self.assertIn(_K_NOTE_PROC, proc)

    def test_sql_rejects_bad_values(self) -> None:
        bad = (
            lambda: setup_sql("2026/10/02"),
            lambda: note_sql("X2026100201"),
            lambda: note_sql("E2E2026'01"),
            lambda: note_id_sql("E2E01"),
            lambda: note_proc_sql("E2E2026'01"),
            lambda: sub_sql("YCFAP0001"),
            lambda: detail_sql("PJJAR"),
            lambda: marks_sql("PJJAR0001", "GL0001"),
            lambda: ap_snap_sql(0, "S0001"),
            lambda: ap_snap_sql(21, "S'1"),
            lambda: split_snap_sql(-1),
        )
        for make in bad:
            with self.assertRaises(ValueError):
                make()


class RegisterTests(unittest.TestCase):
    def _client(self, first: object = None) -> _Replay:
        return _Replay({
            "/v1/notes/create": [first or _ok(dry_run=True), _created("01")],
            "/v1/notes/get": [_ok(head={"code": _no("01"), "close_id": _RECEIPT, "amount": "100.00"})],
            "/v1/notes/delete": [_ok(deleted=True)],
        })

    def test_cycle(self) -> None:
        client = self._client()
        sql = _Sql([(_K_SETUP, [_SETUP]), (_K_NOTE, [_NOTE_GONE, _note(audited="0"), _NOTE_GONE])])
        status, detail = _run("notes_register", client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.routes(), ["notes/create", "notes/create", "notes/get", "notes/delete"])
        dry, real = client.calls[0][1], client.calls[1][1]
        self.assertIs(dry["dry_run"], True)
        self.assertNotIn("dry_run", real)
        self.assertEqual(real["note_no"], _no("01"))
        self.assertEqual((real["amount"], real["settle_code"], real["customer"], real["person"]), (100.0, "301", "C0001", "P01"))
        self.assertEqual((real["sign_date"], real["expire_date"]), ("2026-10-02", "2027-03-31"))
        # 按登记响应的票据主键删除。
        self.assertEqual(client.calls[3][1], {**client.calls[3][1], "flag": "AR", "id": 9})
        self.assertNotIn("note_no", client.calls[3][1])
        self.assertEqual(sql.left(), [])

    def test_wrong_link_fails_and_deletes(self) -> None:
        client = self._client()
        bad = "1|1|301|100.00|100.00||C0001|55|0|1|0"
        sql = _Sql([(_K_SETUP, [_SETUP]), (_K_NOTE, [_NOTE_GONE, bad])])
        status, detail = _run("notes_register", client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("登记后票据", detail)
        self.assertEqual(client.routes()[-1], "notes/delete")

    def test_failed_create_with_note_left_deletes_by_read_id(self) -> None:
        client = _Replay({
            "/v1/notes/create": [_ok(dry_run=True), U8CoError(504, "outcome_unknown", "超时")],
            "/v1/notes/delete": [_ok(deleted=True)],
        })
        sql = _Sql([(_K_SETUP, [_SETUP]), (_K_NOTE, [_NOTE_GONE, _note(audited="0")]), (_K_NOTE_ID, ["77"])])
        status, detail = _run("notes_register", client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("登记票据", detail)
        self.assertEqual(client.calls[-1], ("/v1/notes/delete", {**client.calls[-1][1], "flag": "AR", "id": 77}))
        self.assertEqual(sql.left(), [])

    def test_fallback_delete_by_number_names_it_once(self) -> None:
        client = _Replay({
            "/v1/notes/create": [_ok(dry_run=True), U8CoError(504, "outcome_unknown", "超时")],
            "/v1/notes/delete": [U8CoError(409, "state_mismatch", "收款单已审核")],
        })
        sql = _Sql([(_K_SETUP, [_SETUP]), (_K_NOTE, [_NOTE_GONE, _note(audited="0")]), (_K_NOTE_ID, ["0"])])
        status, detail = _run("notes_register", client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn(f"删除票据（主键未读到），票据号 {_no('01')}", detail)
        self.assertEqual(detail.count(_no("01")), 1)
        self.assertEqual(client.calls[-1][1], {**client.calls[-1][1], "flag": "AR", "note_no": _no("01")})

    def test_cleanup_failure_names_the_note(self) -> None:
        client = self._client()
        client.answers["/v1/notes/delete"] = [U8CoError(409, "state_mismatch", "收款单已审核")]
        bad = "1|1|301|100.00|100.00||C0001|55|0|1|0"
        sql = _Sql([(_K_SETUP, [_SETUP]), (_K_NOTE, [_NOTE_GONE, bad])])
        status, detail = _run("notes_register", client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn(f"删除票据 {_no('01')}，主键 9", detail)
        self.assertNotIn("None", detail)

    def test_missing_route_is_na(self) -> None:
        client = self._client(U8CoError(404, "not_found", "未知路径"))
        status, _detail = _run("notes_register", client, _Sql([(_K_SETUP, [_SETUP])]))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.routes(), ["notes/create"])

    def test_nothing_to_pick_is_na(self) -> None:
        client = self._client()
        status, _detail = _run("notes_register", client, _Sql([(_K_SETUP, [""])]))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])


def _proc_client(prefix: str, tail: str, voucher: object = None, partial: object = _WHOLE) -> _Replay:
    no = prefix + "000000000001"
    style = {"PJJAR": "9A", "PJTAR": "9D"}[prefix]
    return _Replay({
        "/v1/notes/create": [_created(tail)],
        "/v1/vouchers/verify": [_ok(), _ok()],
        "/v1/notes/process": [partial, _ok(dry_run=True), _ok(cancel_no=no)],
        "/v1/notes/get": [_ok(subs=[{"cancel_no": no, "style": style}])],
        "/v1/arap/process/voucher": [voucher or _ok(pz_id=_PZ)],
        "/v1/arap/process/cancel": [U8CoError(409, "state_mismatch", "已制单，请先取消制单"), _ok()],
        "/v1/arap/voucher/delete": [_ok()],
        "/v1/notes/delete": [_ok(deleted=True)],
    })


def _proc_sql(op: str) -> _Sql:
    tail = "02" if op == "settle" else "03"
    discount = op == "discount"
    sub = "1|9D|99.00|1.00|1002990101|某银行|0||" if discount else "1|9A|100.00|0.00|1002990101||0||"
    nets = [(_net("1002990101"), ["99.00|1|1002990101|1002990101" if discount else "100.00|1|1002990101|1002990101"]),
            (_net("112101"), ["-100.00|1|112101|112101"])]
    if discount:
        nets.append((_net("611199"), ["1.00|1|611199|611199"]))
    return _Sql([
        (_K_SETUP, [_SETUP]),
        (_K_NOTE, [_note(audited="0"), _note(), _note(), _note("0.00"), _note(), _NOTE_GONE]),
        (_K_SUB, [sub, _GONE_SUB]),
        (_K_DETAIL, [f"1|1|0.00|100.00|112101|{_no(tail)}|C0001|0|0.00|0.00||", _GONE_DETAIL]),
        (_K_GL, ["3|1|0|99.00|99.00" if discount else "2|1|0|100.00|100.00"]),
        *nets,
        (_K_MARKS, ["1|1|1|2", "1|0|0|0"]),
    ])


class ProcessTests(unittest.TestCase):
    _ROUTES = [
        "notes/create", "vouchers/verify", "notes/process", "notes/process", "notes/process", "notes/get",
        "arap/process/voucher", "arap/process/cancel", "arap/voucher/delete", "arap/process/cancel",
        "vouchers/verify", "notes/delete",
    ]

    def test_settle_cycle(self) -> None:
        client, sql = _proc_client("PJJAR", "02"), _proc_sql("settle")
        status, detail = _run("notes_settle", client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.routes(), self._ROUTES)
        partial, dry, real = client.calls[2][1], client.calls[3][1], client.calls[4][1]
        self.assertEqual((partial["amount"], partial.get("dry_run")), (40.0, None))
        self.assertIs(dry["dry_run"], True)
        self.assertEqual(real, {**real, "op": "settle", "note": _no("02"), "amount": 100.0, "bank_code": "1002990101"})
        self.assertNotIn("expense", real)
        self.assertEqual(client.calls[1][1]["action"], "verify")
        self.assertEqual(client.calls[6][1]["cancel_nos"], ["PJJAR000000000001"])
        self.assertEqual(client.calls[10][1]["action"], "unverify")
        self.assertEqual(sql.left(), [])

    def test_discount_cycle(self) -> None:
        client, sql = _proc_client("PJTAR", "03"), _proc_sql("discount")
        status, detail = _run("notes_discount", client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.routes(), self._ROUTES)
        self.assertEqual((client.calls[2][1]["amount"], client.calls[2][1]["expense"]), (40.0, 1.0))
        self.assertEqual((client.calls[4][1]["amount"], client.calls[4][1]["expense"]), (100.0, 1.0))
        self.assertEqual(sql.left(), [])

    def test_partial_accepted_fails_and_cancels(self) -> None:
        client = _proc_client("PJJAR", "02", partial=_ok(cancel_no="PJJAR000000900005"))
        status, detail = _run("notes_settle", client, _proc_sql("settle"))
        self.assertEqual(status, "FAIL")
        self.assertIn("应 409", detail)
        self.assertEqual(client.routes()[-3:], ["arap/process/cancel", "vouchers/verify", "notes/delete"])
        self.assertEqual(client.calls[-3][1]["cancel_no"], "PJJAR000000900005")

    def test_partial_refused_for_other_reason_fails(self) -> None:
        client = _proc_client("PJJAR", "02", partial=U8CoError(409, "state_mismatch", "收款单未审核"))
        status, detail = _run("notes_settle", client, _proc_sql("settle"))
        self.assertEqual(status, "FAIL")
        self.assertIn("整张票据", detail)
        self.assertEqual(client.routes()[-2:], ["vouchers/verify", "notes/delete"])

    def _unclear(self, partial: object, remain: str, procs: list[str]) -> tuple[_Replay, _Sql]:
        client = _Replay({
            "/v1/notes/create": [_created("02")],
            "/v1/vouchers/verify": [_ok(), _ok()],
            "/v1/notes/process": [partial],
            "/v1/arap/process/cancel": [_ok()],
            "/v1/notes/delete": [_ok(deleted=True)],
        })
        sql = _Sql([
            (_K_SETUP, [_SETUP]),
            (_K_NOTE, [_note(audited="0"), _note(), _note(remain)]),
            (_K_NOTE_PROC, procs),
        ])
        return client, sql

    def test_partial_unknown_outcome_with_balance_moved_cancels(self) -> None:
        client, sql = self._unclear(U8CoError(504, "outcome_unknown", "超时"), "60.00", ["1|PJJAR000000900005"])
        status, detail = _run("notes_settle", client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("部分金额请求结果不明，票据余额已变（数据可能残留，已登记清理）", detail)
        self.assertEqual(client.routes()[-4:], ["notes/process", "arap/process/cancel", "vouchers/verify", "notes/delete"])
        self.assertEqual(client.calls[-3][1]["cancel_no"], "PJJAR000000900005")
        self.assertEqual(sql.left(), [])

    def test_partial_unknown_outcome_without_proc_no_fails(self) -> None:
        client, sql = self._unclear(U8CoError(504, "outcome_unknown", "超时"), "60.00", ["0|"])
        status, detail = _run("notes_settle", client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("留给快照还原", detail)
        self.assertEqual(client.routes()[-3:], ["notes/process", "vouchers/verify", "notes/delete"])
        self.assertEqual(sql.left(), [])

    def test_partial_200_without_proc_no_and_balance_kept_fails(self) -> None:
        client, sql = self._unclear(_ok(), "100.00", [])
        status, detail = _run("notes_settle", client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("实际 200", detail)
        self.assertEqual(client.routes()[-3:], ["notes/process", "vouchers/verify", "notes/delete"])
        self.assertEqual(sql.left(), [])

    def test_discount_without_fee_km_is_na(self) -> None:
        client = _proc_client("PJTAR", "03")
        status, _detail = _run("notes_discount", client, _Sql([(_K_SETUP, ["C0001|D01||甲|112101|1002990101|"])]))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])

    def test_voucher_failure_compensates_in_reverse(self) -> None:
        client = _proc_client("PJJAR", "02", U8CoError(409, "state_mismatch", "缺科目"))
        status, detail = _run("notes_settle", client, _proc_sql("settle"))
        self.assertEqual(status, "FAIL")
        self.assertIn("处理制单", detail)
        # 第一个取消的回答是 409（为已制单准备的），清理失败也照样往下弃审、删票据。
        self.assertEqual(client.routes()[-3:], ["arap/process/cancel", "vouchers/verify", "notes/delete"])


class CashItemTests(unittest.TestCase):
    """处理制单 409「现金流量项目无法按数据来源唯一确定」时带 cash_items（科目取自消息、项目取测试数据）重试一次。"""

    def tearDown(self) -> None:
        testdata.use(support.sample_testdata())

    def test_discount_retries_with_cash_items(self) -> None:
        client, sql = _proc_client("PJTAR", "03"), _proc_sql("discount")
        client.answers["/v1/arap/process/voucher"] = [_NO_CASH, _ok(pz_id=_PZ)]
        status, detail = _run("notes_discount", client, sql)
        self.assertEqual(status, "PASS", detail)
        routes = ProcessTests._ROUTES
        self.assertEqual(client.routes(), routes[:7] + ["arap/process/voucher"] + routes[7:])
        first, again = client.calls[6][1], client.calls[7][1]
        self.assertNotIn("cash_items", first)
        self.assertEqual(again, {**first, "cash_items": {"660399": testdata.value("gl.cash_flow_item")}})
        self.assertIn("指定现金流量项目 660399→", detail)
        self.assertEqual(sql.left(), [])

    def test_other_refusal_is_not_retried(self) -> None:
        client = _proc_client("PJTAR", "03", U8CoError(409, "state_mismatch", "科目 660399 不存在或不是末级"))
        status, detail = _run("notes_discount", client, _proc_sql("discount"))
        self.assertEqual(status, "FAIL")
        self.assertIn("处理制单", detail)
        self.assertEqual(client.routes().count("arap/process/voucher"), 1)

    def test_retry_without_testdata_is_na(self) -> None:
        testdata.use({})
        client = _proc_client("PJTAR", "03", _NO_CASH)
        client.answers["/v1/arap/process/cancel"] = [_ok()]
        status, detail = _run("notes_discount", client, _proc_sql("discount"))
        self.assertEqual(status, "N/A", detail)
        self.assertIn("gl.cash_flow_item", detail)
        self.assertEqual(client.routes().count("arap/process/voucher"), 1)
        self.assertEqual(client.routes()[-3:], ["arap/process/cancel", "vouchers/verify", "notes/delete"])

    def test_cash_items_reads_only_the_refusal(self) -> None:
        self.assertIsNone(cash_items(200, {"message": _NO_CASH.message}))
        self.assertIsNone(cash_items(409, {"message": "科目 660399 不存在"}))
        self.assertEqual(cash_items(409, {"message": _NO_CASH.message}), {"660399": testdata.value("gl.cash_flow_item")})


class MoreTests(unittest.TestCase):
    def test_endorse_cycle(self) -> None:
        no = "PJBAR000000000001"
        client = _Replay({
            "/v1/notes/create": [_created("04")],
            "/v1/vouchers/verify": [_ok(), _ok()],
            "/v1/notes/process": [_WHOLE, _ok(cancel_no=no)],
            "/v1/arap/process/cancel": [_ok()],
            "/v1/notes/delete": [_ok(deleted=True)],
        })
        sql = _Sql([
            (_K_SETUP, [_SETUP]),
            (_K_AP_PICK, ["S0001|PB1|21"]),
            (_K_AP_SNAP, ["5.00|130.00|0", "105.00|30.00|1", "5.00|130.00|0"]),
            (_K_NOTE, [_note(audited="0"), _note(), _note("0.00"), _note(), _NOTE_GONE]),
            (_K_SUB, ["1|9E|100.00|0.00|220201|S0001|0||", _GONE_SUB]),
            (_K_DETAIL, [f"1|1|0.00|100.00|112101|{_no('04')}|C0001|1|100.00|0.00|S0001|01", _GONE_DETAIL]),
        ])
        status, detail = _run("notes_endorse", client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.routes()[2:4], ["notes/process", "notes/process"])
        partial, body = client.calls[2][1], client.calls[3][1]
        self.assertEqual((partial["amount"], partial["ap_lines"][0]["amount"]), (1.0, 1.0))
        self.assertEqual(body["ap_lines"], [{"type": "01", "id": "PB1", "line_id": 21, "amount": 100.0}])
        self.assertEqual((body["op"], body["vendor"], body["amount"]), ("endorse", "S0001", 100.0))
        self.assertEqual(sql.left(), [])

    def _split(self, back: str) -> tuple[_Replay, _Sql]:
        client = _Replay({
            "/v1/notes/process": [_ok(cancel_no="PJJAR000000900009")],
            "/v1/arap/process/cancel": [_ok()],
        })
        before = "15.00|1|15.00|1001|5000|123|2"
        sql = _Sql([
            (_K_SETUP, [_SETUP]),
            (_K_SPLIT_PICK, ["300|1001|5000"]),
            (_K_SPLIT_SNAP, [before, "14.00|1|14.00|1101|5000|999|3", back]),
            (_K_SUB, ["1|9A|1.00|0.00|1002990101||0|1001|1100", _GONE_SUB]),
            (_K_DETAIL, ["1|1|0.00|1.00|112101|NOTE1-1001-1100|C0001|0|0.00|0.00||", _GONE_DETAIL]),
        ])
        return client, sql

    def test_split_cycle(self) -> None:
        client, sql = self._split("15.00|1|15.00|1001|5000|123|2")
        status, detail = _run("notes_split_settle", client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.routes(), ["notes/process", "arap/process/cancel"])
        body = client.calls[0][1]
        self.assertEqual((body["note"], body["sub_start"], body["sub_end"], body["amount"]), (300, 1001, 1100, 1.0))
        self.assertEqual(client.calls[1][1]["cancel_no"], "PJJAR000000900009")
        self.assertEqual(sql.left(), [])

    def test_split_not_restored_fails(self) -> None:
        client, sql = self._split("15.00|2|15.00|1001|5000|456|2")
        status, detail = _run("notes_split_settle", client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("取消后", detail)


if __name__ == "__main__":
    unittest.main()
