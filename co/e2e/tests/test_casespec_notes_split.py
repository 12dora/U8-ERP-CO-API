"""分包票据登记用例（notes_split）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import notes_split as split
from co.e2e.casespec.catalog import build_cases, known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.notes_split_sql import split_reg_samples, split_state_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "note-split-register-cycle"
_SPEC = CaseSpec("notes-split", "s")
_STEP = Step("custom")
_RECEIPT = 55
_NO = "E2E2026100209000005"
_LO = 1234560001
_HI = _LO + 9999
_CUT = _LO + 99
_PNO = "PJJAR000000900011"
_SETUP = "C0001|D01|P01|收款人甲|112101|1002990101|611199"
# 回读 SQL 的特征串（每种 SQL 一个，互不包含）。
_K_SETUP = "N'pjyfrzkm'"
_K_STATE = "a.iavailamount_local"
_K_SUB = "s.csbnstart"
_K_DETAIL = "d.iFlag = 3"
_GONE = "0||||0.00|0.00|0|||0.00|0.00|0|0|0|0"
_GONE_SUB = "0|||0.00|0.00||0||"
_GONE_DETAIL = "0|0|0.00|0.00||||0|0.00|0.00||"
_MISMATCH = U8CoError(400, "bad_request", "子票区间与金额不符：区间 9999 张 = 99.99 元，票面 100.00")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _state(remain: str = "100.00", lo: int = _LO, audited: str = "0") -> str:
    return f"1|1|{_LO}|{_HI}|100.00|{remain}|1|{lo}|{_HI}|{remain}|{remain}|1|1|{audited}|0"


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


def _run(client: _Replay, sql: _Sql) -> tuple[str, str]:
    with (
        mock.patch.object(sp, "run_guarded", sql),
        mock.patch.object(split, "new_note_no", lambda _tail: _NO),
        mock.patch.object(split, "first_serial", lambda: _LO),
    ):
        return split.SPLIT_REG_OPS["notes_split_register"](_env(client), _SPEC, _STEP)


def _client(first: object = _MISMATCH, head: dict | None = None) -> _Replay:
    created = _ok(note_no=_NO, note_id=9, receipt_id=_RECEIPT, receipt_code="SK1", sub_start=_LO, sub_end=_HI)
    return _Replay({
        "/v1/notes/create": [first, _ok(dry_run=True), created],
        "/v1/notes/get": [_ok(head=head or {"code": _NO, "sub_start": _LO, "sub_end": str(_HI)})],
        "/v1/vouchers/verify": [_ok(), _ok()],
        "/v1/notes/process": [_ok(cancel_no=_PNO)],
        "/v1/arap/process/cancel": [_ok()],
        "/v1/notes/delete": [_ok(deleted=True)],
    })


def _sql(settled: str | None = None) -> _Sql:
    return _Sql([
        (_K_SETUP, [_SETUP]),
        (_K_STATE, [
            _GONE, _GONE, _state(), _state(audited="1"),
            settled or _state("99.00", _CUT + 1, "1"), _state(audited="1"), _GONE,
        ]),
        (_K_SUB, [f"1|9A|1.00|0.00|1002990101||0|{_LO}|{_CUT}", _GONE_SUB]),
        (_K_DETAIL, [f"1|1|0.00|1.00|112101|{_NO}-{_LO}-{_CUT}|C0001|0|0.00|0.00||", _GONE_DETAIL]),
    ])


_ROUTES = [
    "notes/create", "notes/create", "notes/create", "notes/get", "vouchers/verify", "notes/process",
    "arap/process/cancel", "vouchers/verify", "notes/delete",
]


class RegistrationTests(unittest.TestCase):
    def test_registered_with_op_tags_routes(self) -> None:
        cases = {spec.name: spec for spec in build_cases({})}
        self.assertEqual(known_names().count(_NAME), 1)
        spec = cases[_NAME]
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertIn("/v1/notes/process", spec.routes)
        tags = auto_tags(spec)
        self.assertLessEqual({"notes:AR", "period:close", "new:ar_receipt"}, tags)
        for other in ("note-register-cycle", "note-settle-cycle", "note-split-settle-cycle", "ar-receipt-cycle"):
            with self.subTest(other=other):
                self.assertTrue(conflicts(tags, auto_tags(cases[other])))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in split_reg_samples():
            with self.subTest(sample=name):
                self.assertEqual(sql_block_reason(query), "", name)
                self.assertIn((name, query), rows)
        query = split_state_sql(_NO, _LO, _HI)
        self.assertIn(f"N'{_NO}-{_LO}-{_HI}'", query)
        self.assertIn(f"a.cNoteLink = N'AR50{_NO}'", query)

    def test_sql_rejects_bad_values(self) -> None:
        bad = (
            lambda: split_state_sql("X2026100201", 1, 1),
            lambda: split_state_sql("E2E2026'01", 1, 1),
            lambda: split_state_sql(_NO, 0, 1),
            lambda: split_state_sql(_NO, 2, 1),
            lambda: split_state_sql(_NO, 1, 10**15),
            lambda: split_state_sql(_NO, True, 1),
        )
        for make in bad:
            with self.assertRaises(ValueError):
                make()

    def test_first_serial_shape(self) -> None:
        with mock.patch.object(split.time, "time", lambda: 1790000000.5):
            first = split.first_serial()
        self.assertEqual(first % 10000, 1)
        self.assertLessEqual(first + 9999, 999999999999999)


class CycleTests(unittest.TestCase):
    def test_cycle(self) -> None:
        client, sql = _client(), _sql()
        status, detail = _run(client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.routes(), _ROUTES)
        refused, dry, real = (client.calls[i][1] for i in range(3))
        self.assertEqual((refused["sub_start"], refused["sub_end"]), (_LO, _HI - 1))
        self.assertIs(dry["dry_run"], True)
        self.assertNotIn("dry_run", real)
        self.assertEqual((real["note_no"], real["amount"], real["sub_start"], real["sub_end"]), (_NO, 100.0, _LO, _HI))
        proc = client.calls[5][1]
        want = {"op": "settle", "note": _NO, "amount": 1.0, "sub_start": _LO, "sub_end": _CUT, "bank_code": "1002990101"}
        self.assertEqual(proc, {**proc, **want})
        self.assertEqual(client.calls[6][1]["cancel_no"], _PNO)
        self.assertEqual(client.calls[7][1]["action"], "unverify")
        self.assertEqual(client.calls[8][1], {**client.calls[8][1], "flag": "AR", "id": 9})
        self.assertEqual(sql.left(), [])

    def test_mismatch_accepted_fails_and_deletes(self) -> None:
        client = _client(first=_ok(note_no=_NO, note_id=7, receipt_id=_RECEIPT))
        status, detail = _run(client, _sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("应 400", detail)
        self.assertEqual(client.routes(), ["notes/create", "notes/delete"])
        self.assertEqual(client.calls[-1][1]["id"], 7)

    def test_mismatch_refused_for_other_reason_fails(self) -> None:
        client = _client(first=U8CoError(409, "state_mismatch", "票据号已存在"))
        status, detail = _run(client, _sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("bad_request", detail)
        self.assertEqual(client.routes(), ["notes/create"])

    def test_refused_but_written_fails(self) -> None:
        sql = _Sql([(_K_SETUP, [_SETUP]), (_K_STATE, [_state()]), ("MAX(n.Auto_ID)", ["77"])])
        client = _client()
        status, detail = _run(client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("被拒的登记后", detail)
        self.assertEqual(client.routes(), ["notes/create", "notes/delete"])
        self.assertEqual(client.calls[-1][1]["id"], 77)
        self.assertEqual(sql.left(), [])

    def test_unknown_outcome_written_records_drop(self) -> None:
        sql = _Sql([(_K_SETUP, [_SETUP]), (_K_STATE, [_state()]), ("MAX(n.Auto_ID)", ["77"])])
        client = _client(first=U8CoError(504, "outcome_unknown", "超时"))
        status, detail = _run(client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("结果不明（504）的登记后", detail)
        self.assertEqual(client.routes(), ["notes/create", "notes/delete"])
        self.assertEqual(client.calls[-1][1]["id"], 77)
        self.assertEqual(sql.left(), [])

    def test_unknown_outcome_nothing_written_fails_without_cleanup(self) -> None:
        sql = _Sql([(_K_SETUP, [_SETUP]), (_K_STATE, [_GONE])])
        client = _client(first=U8CoError(504, "outcome_unknown", "超时"))
        status, detail = _run(client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("应 400 bad_request，实际 504", detail)
        self.assertEqual(client.routes(), ["notes/create"])
        self.assertEqual(sql.left(), [])

    def test_wrong_head_range_fails_and_cleans(self) -> None:
        client = _client(head={"code": _NO, "sub_start": None, "sub_end": None})
        status, detail = _run(client, _sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("子票区间不符", detail)
        self.assertEqual(client.routes()[-1], "notes/delete")

    def test_wrong_rewrite_compensates_in_reverse(self) -> None:
        client = _client()
        status, detail = _run(client, _sql(settled=_state("99.00", _LO, "1")))
        self.assertEqual(status, "FAIL")
        self.assertIn("可用区间", detail)
        self.assertEqual(client.routes()[-3:], ["arap/process/cancel", "vouchers/verify", "notes/delete"])

    def test_missing_route_is_na(self) -> None:
        client = _client(first=U8CoError(404, "not_found", "未知路径"))
        status, _detail = _run(client, _Sql([(_K_SETUP, [_SETUP])]))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.routes(), ["notes/create"])

    def test_nothing_to_pick_is_na(self) -> None:
        client = _client()
        status, _detail = _run(client, _Sql([(_K_SETUP, [""])]))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
