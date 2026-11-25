"""票据退回与应付票据用例（notes_return）的登记、改动挑选、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import notes
from co.e2e.casespec import notes_return as more
from co.e2e.casespec.catalog import build_cases, known_names
from co.e2e.casespec.coverage import rule_for, select_changed
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.notes_return_sql import (
    bill_sql,
    close_sql,
    marks_sql,
    note_state_sql,
    notes_more_samples,
    proc_detail_sql,
    proc_sub_sql,
)
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_RET, _SPLIT, _AP = "note-return-cycle", "note-return-split-cycle", "ap-note-cycle"
_SPEC = CaseSpec("notes-more", "s")
_STEP = Step("custom")
_NO = "E2E2026100209000006"
_RECEIPT, _PAY = 55, 66
_CTRL, _KM, _BANK = "11229901", "112101", "1002990101"
_R0, _P0 = "YS2026100200001", "YF2026100200001"
_CLAR, _CLAP, _PJJAP = "CLAR0000000000009", "CLAP0000000000001", "PJJAP0000000000001"
_SETUP = f"C0001|D01|P01|收款人甲|{_KM}|{_BANK}|611199"
_AP_SETUP = f"V0001|D01|P01|220101|{_BANK}"
_LO = 1234560001
_HI = _LO + 9999
_CUT = _LO + 99
# 回读 SQL 的特征串（每种 SQL 一个；按顺序匹配，前面的更具体）。
_K_SETUP = "N'pjyfrzkm'"
_K_AP_SETUP = "N'2201%'"
_K_STATE = "v.cSrcNo = N'"
_K_NOTES = "n.cLink = N'AR50' + n.cVouchID"
_K_CLOSE = "b.iRAmt"
_K_SUB = "ISNULL(s.iIntrest, 0)"
_K_DETAIL = "d.iFlag = 0"
_K_BILL = "Ap_Vouchs AS b"
_K_GL = "COUNT(DISTINCT"
_K_NET = "ISNULL(g.md, 0) - ISNULL(g.mc, 0)"
_K_MARKS = "d.cGLSign IS NOT NULL"
_K_SPLIT = "a.iavailamount_local"
_SUB_GONE = "0|||0.00|0.00||||||0"
_DETAIL_GONE = "0|0|0.00|0.00|||0|0.00|0.00|||0||"
_BILL_GONE = "0|||0|0.00|0.00||||0|0|0||0"
_TODAY = "2026-10-02"
_BY_PROC = U8CoError(409, "state_mismatch", "单据由票据退回等处理生成，不能审核或弃审")
_RANGE = U8CoError(409, "state_mismatch", "分包票据退回须退回全部可用区间（票据 X 可用 100.00，本次 1.00）")
_CUS_DIGEST = "退回客户甲电子承兑"
_VEN_DIGEST = "退回供应商乙电子承兑"
_CLOSE = f"100.00|0.00|1|{_CTRL}|1"
_WHOLE = U8CoError(409, "state_mismatch", "非分包票据须整张票据一次处理全部金额")
_MADE = U8CoError(409, "state_mismatch", "处理已制单，请先取消制单")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


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
    with (
        mock.patch.object(sp, "run_guarded", sql),
        mock.patch.object(notes, "new_note_no", lambda _tail: _NO),
        mock.patch.object(more, "new_note_no", lambda _tail: _NO),
        mock.patch.object(more, "first_serial", lambda: _LO),
    ):
        return more.NOTES_MORE_OPS[op](_env(client), _SPEC, _STEP)


# ---- 应收整张退回 ----


def _notes_row(remain: str = "100.00", audited: str = "0", count: str = "1") -> str:
    if count == "0":
        return "0|0||0.00|0.00||||0|0|0"
    return f"1|1|301|100.00|{remain}|{_KM}|C0001|{_RECEIPT}|1|1|{audited}"


def _state(flag: str, remain: str, subs: str = "0", bills: str = "0", audited: str = "1") -> str:
    km, dw, close = (_KM, "C0001", _RECEIPT) if flag == "AR" else ("220101", "V0001", _PAY)
    return f"1|1|100.00|{remain}|{km}|{dw}|{close}|1|1|{audited}|{subs}|{bills}"


def _back(flag: str, cancel: str = "") -> dict:
    if flag == "AR":
        return _ok(cancel_no=cancel or _CLAR, r0_code=_R0, r0_id=31, note={"partner_name": "客户甲"})
    return _ok(cancel_no=cancel or _CLAP, p0_code=_P0, p0_id=41, note={"partner_name": "供应商乙"})


def _ret_client(proc_first: object = _WHOLE) -> _Replay:
    return _Replay({
        "/v1/notes/create": [_ok(note_no=_NO, note_id=9, receipt_id=_RECEIPT)],
        "/v1/vouchers/verify": [_ok(), _BY_PROC, _BY_PROC, _ok()],
        "/v1/notes/process": [proc_first, _ok(dry_run=True), _back("AR")],
        "/v1/notes/get": [_ok(subs=[{"cancel_no": _CLAR, "style": "9C"}])],
        "/v1/arap/process/voucher": [_ok(pz_id="AR0000000000123")],
        "/v1/arap/process/cancel": [_MADE, _ok()],
        "/v1/arap/voucher/delete": [_ok()],
        "/v1/notes/delete": [_ok(deleted=True)],
    })


def _ret_sql(bill: str | None = None) -> _Sql:
    return _Sql([
        (_K_SETUP, [_SETUP]),
        (_K_STATE, [_state("AR", "100.00"), _state("AR", "0.00", "1", "1"), _state("AR", "100.00")]),
        (_K_NOTES, [_notes_row(), _notes_row(audited="1"), _notes_row(count="0")]),
        (_K_CLOSE, [_CLOSE, _CLOSE]),
        (_K_SUB, [f"1|9C|100.00|0.00|{_CTRL}||R0|{_R0}|||0", _SUB_GONE]),
        (_K_BILL, [bill or f"1|50|{_NO}|1|100.00|0.00|C0001|{_CTRL}|1|0|1|0|{_TODAY}|1", _BILL_GONE]),
        (_K_DETAIL, [f"2|1|100.00|0.00|{_CTRL}|{_R0}|1|0.00|100.00|{_KM}|{_NO}|0|{_CUS_DIGEST}|{_CUS_DIGEST}", _DETAIL_GONE]),
        (_K_GL, ["2|1|0|-100.00|-100.00"]),
        (_K_NET, [f"100.00|1|{_CTRL}|{_CTRL}", f"-100.00|1|{_KM}|{_KM}"]),
        (_K_MARKS, ["2|2|1|2|1", "2|0|0|0|0"]),
    ])


_RET_ROUTES = [
    "notes/create", "vouchers/verify", "notes/process", "notes/process", "notes/process", "vouchers/verify",
    "vouchers/verify", "notes/get",
    "arap/process/voucher", "arap/process/cancel", "arap/voucher/delete", "arap/process/cancel", "vouchers/verify",
    "notes/delete",
]


# ---- 应收分包退回 ----


def _split(remain: str = "100.00", lo: int = _LO, audited: str = "1") -> str:
    return f"1|1|{_LO}|{_HI}|100.00|{remain}|1|{lo}|{_HI}|{remain}|{remain}|1|1|{audited}|0"


def _split_client(partial: object = None) -> _Replay:
    return _Replay({
        "/v1/notes/create": [_ok(note_no=_NO, note_id=9, receipt_id=_RECEIPT, sub_start=_LO, sub_end=_HI)],
        "/v1/notes/get": [_ok(head={"code": _NO, "sub_start": _LO, "sub_end": _HI})],
        "/v1/vouchers/verify": [_ok(), _BY_PROC, _BY_PROC, _ok()],
        "/v1/notes/process": [partial or _RANGE, _back("AR")],
        "/v1/arap/process/cancel": [_ok()],
        "/v1/notes/delete": [_ok(deleted=True)],
    })


def _split_sql(after: str | None = None) -> _Sql:
    ref = f"{_NO}-{_LO}-{_HI}"
    return _Sql([
        (_K_SETUP, [_SETUP]),
        (_K_SPLIT, [
            _split(audited="0"), _split(), after or "1|1|{lo}|{hi}|100.00|0.00|0|||0.00|0.00|1|1|1|0".format(lo=_LO, hi=_HI),
            _split(),
            "0||||0.00|0.00|0|||0.00|0.00|0|0|0|0",
        ]),
        (_K_CLOSE, [_CLOSE]),
        (_K_SUB, [f"1|9C|100.00|0.00|{_CTRL}||R0|{_R0}|{_LO}|{_HI}|0", _SUB_GONE]),
        (_K_BILL, [f"1|50|{_NO}|1|100.00|0.00|C0001|{_CTRL}|1|0|1|0|{_TODAY}|1", _BILL_GONE]),
        (_K_DETAIL, [f"2|1|100.00|0.00|{_CTRL}|{_R0}|1|0.00|100.00|{_KM}|{ref}|0|{_CUS_DIGEST}|{_CUS_DIGEST}", _DETAIL_GONE]),
    ])


# ---- 应付票据 ----


def _ap_client(probe: object = None) -> _Replay:
    gone = U8CoError(404, "not_found", "票据 E2E000000000000 不存在")
    return _Replay({
        "/v1/notes/delete": [probe or gone, _ok(deleted=True)],
        "/v1/notes/create": [_ok(dry_run=True), _ok(note_no=_NO, note_id=12, receipt_id=_PAY)],
        "/v1/vouchers/verify": [_ok(), _BY_PROC, _BY_PROC, _ok()],
        "/v1/notes/process": [_ok(cancel_no=_PJJAP), _back("AP")],
        "/v1/notes/get": [_ok(subs=[{"cancel_no": _PJJAP, "style": "9A"}])],
        "/v1/arap/process/voucher": [_ok(pz_id="AP0000000000077")],
        "/v1/arap/process/cancel": [_MADE, _ok(), _ok()],
        "/v1/arap/voucher/delete": [_ok()],
    })


def _ap_sql() -> _Sql:
    gone = "0|0|0.00|0.00|||0|0|0|0|0|0"
    close = "100.00|100.00|1|220202|1"
    return _Sql([
        (_K_AP_SETUP, [_AP_SETUP]),
        (_K_STATE, [
            gone, _state("AP", "100.00", audited="0"), _state("AP", "100.00"), _state("AP", "0.00", "1"),
            _state("AP", "100.00"), _state("AP", "0.00", "1", "1"), _state("AP", "100.00"), gone,
        ]),
        (_K_CLOSE, [close, close]),
        (_K_SUB, [f"1|9A|100.00|0.00|{_BANK}||48||||0", _SUB_GONE, f"1|9C|100.00|0.00|220202||P0|{_P0}|||0", _SUB_GONE]),
        (_K_BILL, [f"1|50|{_NO}|1|100.00|0.00|V0001|220202|0|0|1|0|{_TODAY}|1", _BILL_GONE]),
        (_K_DETAIL, [
            f"1|0|0.00|0.00|||1|100.00|0.00|220101|{_NO}|0||付供应商乙票据到期结算", _DETAIL_GONE,
            f"2|1|0.00|100.00|220202|{_P0}|1|100.00|0.00|220101|{_NO}|0|{_VEN_DIGEST}|{_VEN_DIGEST}", _DETAIL_GONE,
        ]),
        (_K_GL, ["2|1|0|100.00|100.00"]),
        (_K_NET, ["100.00|1|220101|220101", f"-100.00|1|{_BANK}|{_BANK}"]),
        (_K_MARKS, ["1|1|1|2|0", "1|0|0|0|0"]),
    ])


_AP_ROUTES = [
    "notes/delete", "notes/create", "notes/create", "vouchers/verify", "notes/process", "notes/get",
    "arap/process/voucher", "arap/process/cancel", "arap/voucher/delete", "arap/process/cancel", "notes/process",
    "vouchers/verify", "vouchers/verify", "arap/process/cancel", "vouchers/verify", "notes/delete",
]


class RegistrationTests(unittest.TestCase):
    def test_registered_with_tags_routes(self) -> None:
        cases = {spec.name: spec for spec in build_cases({})}
        for name in (_RET, _SPLIT, _AP):
            with self.subTest(name=name):
                self.assertEqual(known_names().count(name), 1)
                self.assertTrue(ops_run.has_op(cases[name].steps[0].sql_name))
                self.assertIn("/v1/notes/process", cases[name].routes)
        self.assertLessEqual({"notes:AR", "new:ar_bill", "period:close", "gl"}, auto_tags(cases[_RET]))
        self.assertLessEqual({"notes:AP", "new:ap_payment", "new:ap_bill", "period:close"}, auto_tags(cases[_AP]))
        pairs = ((_RET, "note-settle-cycle"), (_SPLIT, "note-split-register-cycle"), (_AP, "ap-payment-cycle"))
        for one, other in pairs:
            with self.subTest(other=other):
                self.assertTrue(conflicts(auto_tags(cases[one]), auto_tags(cases[other])))

    def test_coverage_rules(self) -> None:
        names = list({spec.name for spec in build_cases({})})
        self.assertEqual(rule_for("co/bridge/src/NotesProcReturnSql.cs")[1], ("note-return-", "ap-note-"))
        self.assertEqual(rule_for("co/bridge/src/NotesApSelfTest.cs")[1], ("ap-note-",))
        picked = select_changed(["co/bridge/src/NotesProcReturn.cs"], names)
        self.assertLessEqual({_RET, _SPLIT, _AP}, set(picked))
        self.assertNotIn("note-settle-cycle", picked)
        self.assertIn(_AP, select_changed(["co/bridge/src/NotesReg.cs"], names))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in notes_more_samples():
            with self.subTest(sample=name):
                self.assertEqual(sql_block_reason(query), "", name)
                self.assertIn((name, query), rows)
        self.assertIn("dbo.Ap_Detail", proc_detail_sql("AP", _CLAP))
        self.assertIn("v.cVouchType = N'P0'", bill_sql("AP", _P0))
        self.assertIn("c.cVouchType = N'49'", note_state_sql("AP", _NO))
        self.assertIn("n.cLink = N'AP50'", note_state_sql("AP", _NO))

    def test_sql_rejects_bad_values(self) -> None:
        bad = (
            lambda: note_state_sql("XX", _NO),
            lambda: note_state_sql("AR", "E2E'1"),
            lambda: proc_sub_sql("PJTAR0001"),
            lambda: proc_detail_sql("AR", "CLAR1'"),
            lambda: bill_sql("AR", "YS-1"),
            lambda: close_sql(0),
            lambda: marks_sql("AR", _CLAR, "GL0001"),
        )
        for make in bad:
            with self.assertRaises(ValueError):
                make()


class ReturnTests(unittest.TestCase):
    def test_cycle(self) -> None:
        client, sql = _ret_client(), _ret_sql()
        status, detail = _run("notes_return", client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.routes(), _RET_ROUTES)
        partial, dry, real = (client.calls[i][1] for i in (2, 3, 4))
        self.assertEqual((partial["op"], partial["amount"]), ("return", 40.0))
        self.assertIs(dry["dry_run"], True)
        self.assertNotIn("dry_run", real)
        self.assertEqual((real["flag"], real["note"], real["amount"]), ("AR", _NO, 100.0))
        refused = [client.calls[i][1] for i in (5, 6)]
        self.assertEqual([(c["type"], c["id"], c["action"]) for c in refused], [("ar_bill", 31, "unverify"), ("ar_bill", 31, "verify")])
        self.assertEqual(client.calls[8][1]["cancel_nos"], [_CLAR])
        self.assertEqual(client.calls[11][1]["cancel_no"], _CLAR)
        self.assertEqual(sql.left(), [])

    def test_partial_accepted_fails_and_cancels(self) -> None:
        client = _ret_client(proc_first=_ok(cancel_no="CLAR0000000000008", r0_code=_R0))
        client.answers["/v1/arap/process/cancel"] = [_ok()]
        status, detail = _run("notes_return", client, _ret_sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("应 409", detail)
        self.assertEqual(client.routes()[-4:], ["notes/process", "arap/process/cancel", "vouchers/verify", "notes/delete"])
        self.assertEqual(client.calls[-3][1]["cancel_no"], "CLAR0000000000008")

    def test_wrong_bill_compensates_in_reverse(self) -> None:
        client = _ret_client()
        client.answers["/v1/arap/process/cancel"] = [_ok()]
        status, detail = _run("notes_return", client, _ret_sql(bill=f"1|50|{_NO}|0|100.00|0.00|C0001|{_CTRL}|1|0|1|1|{_TODAY}|1"))
        self.assertEqual(status, "FAIL")
        self.assertIn(_R0, detail)
        self.assertEqual(client.routes()[-3:], ["arap/process/cancel", "vouchers/verify", "notes/delete"])

    def test_missing_r0_code_fails(self) -> None:
        client = _ret_client()
        client.answers["/v1/notes/process"][2] = _ok(cancel_no=_CLAR, r0_id=31, note={"partner_name": "客户甲"})
        client.answers["/v1/arap/process/cancel"] = [_ok()]
        status, detail = _run("notes_return", client, _ret_sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("r0_code", detail)
        self.assertEqual(client.routes()[-3:], ["arap/process/cancel", "vouchers/verify", "notes/delete"])

    def test_split_cycle(self) -> None:
        client, sql = _split_client(), _split_sql()
        status, detail = _run("notes_return_split", client, sql)
        self.assertEqual(status, "PASS", detail)
        partial, proc = client.calls[3][1], client.calls[4][1]
        self.assertEqual(partial, {**partial, "op": "return", "amount": 1.0, "sub_start": _LO, "sub_end": _CUT})
        want = {"op": "return", "note": _NO, "amount": 100.0, "sub_start": _LO, "sub_end": _HI}
        self.assertEqual(proc, {**proc, **want})
        self.assertEqual(client.routes()[-3:], ["arap/process/cancel", "vouchers/verify", "notes/delete"])
        self.assertEqual(sql.left(), [])

    def test_split_wrong_rewrite_fails(self) -> None:
        client = _split_client()
        status, detail = _run("notes_return_split", client, _split_sql(after=_split("0.00", _LO)))
        self.assertEqual(status, "FAIL")
        self.assertIn("可用区间", detail)
        self.assertEqual(client.routes()[-3:], ["arap/process/cancel", "vouchers/verify", "notes/delete"])

    def test_split_partial_accepted_fails_and_cancels(self) -> None:
        client = _split_client(partial=_back("AR", "CLAR0000000000008"))
        status, detail = _run("notes_return_split", client, _split_sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("应 409", detail)
        self.assertEqual(client.routes()[-3:], ["arap/process/cancel", "vouchers/verify", "notes/delete"])
        self.assertEqual(client.calls[-3][1]["cancel_no"], "CLAR0000000000008")

    def test_verify_of_generated_bill_accepted_fails(self) -> None:
        client = _split_client()
        client.answers["/v1/vouchers/verify"][1] = _ok()
        status, detail = _run("notes_return_split", client, _split_sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("单据由票据退回等处理生成", detail)
        self.assertEqual(client.routes()[-3:], ["arap/process/cancel", "vouchers/verify", "notes/delete"])


class ApNoteTests(unittest.TestCase):
    def test_cycle(self) -> None:
        client, sql = _ap_client(), _ap_sql()
        status, detail = _run("notes_ap", client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.routes(), _AP_ROUTES)
        probe, dry, real = client.calls[0][1], client.calls[1][1], client.calls[2][1]
        self.assertEqual((probe["flag"], probe["dry_run"]), ("AP", True))
        self.assertIs(dry["dry_run"], True)
        self.assertEqual((real["flag"], real["vendor"], real["note_km"]), ("AP", "V0001", "220101"))
        self.assertNotIn("customer", real)
        self.assertEqual(client.calls[3][1]["type"], "ap_payment")
        self.assertEqual(client.calls[4][1]["bank_code"], _BANK)
        self.assertEqual(client.calls[6][1]["flag"], "AP")
        self.assertEqual(client.calls[10][1]["op"], "return")
        self.assertEqual(client.calls[11][1], {**client.calls[11][1], "type": "ap_bill", "id": 41, "action": "unverify"})
        self.assertEqual(client.calls[-1][1], {**client.calls[-1][1], "flag": "AP", "id": 12})
        self.assertEqual(sql.left(), [])

    def test_not_test_account_is_na(self) -> None:
        client = _ap_client(probe=U8CoError(403, "test_account_only", "只对测试账套开放"))
        status, _detail = _run("notes_ap", client, _Sql([]))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.routes(), ["notes/delete"])

    def test_feature_disabled_is_na(self) -> None:
        client = _ap_client(probe=U8CoError(403, "feature_disabled", "第二级写入未打开"))
        status, detail = _run("notes_ap", client, _Sql([]))
        self.assertEqual(status, "N/A", detail)
        self.assertEqual(client.routes(), ["notes/delete"])

    def test_nothing_to_pick_is_na(self) -> None:
        client = _ap_client()
        status, _detail = _run("notes_ap", client, _Sql([(_K_AP_SETUP, [""])]))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.routes(), ["notes/delete"])

    def test_register_failure_with_note_left_deletes_by_no(self) -> None:
        client = _ap_client()
        client.answers["/v1/notes/create"][1] = U8CoError(504, "outcome_unknown", "超时")
        sql = _Sql([
            (_K_AP_SETUP, [_AP_SETUP]),
            (_K_STATE, ["0|0|0.00|0.00|||0|0|0|0|0|0", _state("AP", "100.00", audited="0")]),
        ])
        status, detail = _run("notes_ap", client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("504", detail)
        self.assertEqual(client.calls[-1], ("/v1/notes/delete", {**client.calls[-1][1], "flag": "AP", "note_no": _NO}))


if __name__ == "__main__":
    unittest.main()
