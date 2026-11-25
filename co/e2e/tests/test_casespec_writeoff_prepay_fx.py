"""预收 / 预付行核销、外币核销用例（writeoff_prepay_fx）的登记、SQL 和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec
from co.e2e.casespec.writeoff import hx_cases
from co.e2e.casespec.writeoff_sql import hx_ar_sql
from co.e2e.casespec.writeoff_prepay_fx import ARAP_MORE_OPS, arap_more_cases, fx_run, prepay_run
from co.e2e.casespec.writeoff_prepay_fx_sql import arap_more_samples, balance_sql, batch_sql, fx_pick_sql, prepay_pick_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("ar-writeoff-prepay", "ap-writeoff-prepay", "ar-writeoff-fx", "ap-writeoff-fx")
_ROUTE = "/v1/arap/writeoff"
_CANCEL = "/v1/arap/writeoff/cancel"
_SQL = "co.e2e.casespec.writeoff_prepay_fx.run_guarded"
_SPEC = CaseSpec("ar-writeoff-prepay", "s")
_OK = {"ok": True}


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    def __init__(self, answers: list) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers.pop(0) if self.answers else {"ok": True}
        if isinstance(got, U8CoError):
            raise got
        return got


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-02", bag={})


def _row(cell: str) -> str:
    return "ROW:" + cell + "\n" if cell else ""


def _written(side: str, digits: str = "0000000007") -> dict:
    return {"ok": True, "cancel_no": "HX" + side + digits}


# 预收行：挑单、核销前余额、核销后余额、核销行、取消后核销行、取消后余额。
_PREPAY_ROWS = ["11|12|13|14", "50.00|50.00|0|18.08|0", "49.00|49.00|0|17.08|0", "2|1|0|1.00|1.00", "0|0|0|0.00|0.00",
                "50.00|50.00|0|18.08|0"]
_FX_ROWS = ["21|22|23|7.100000", "20.00|142.00|0|20.00|0", "19.00|134.90|0|19.00|0", "2|0|2|1.00|7.10", "0|0|0|0.00|0.00",
            "20.00|142.00|0|20.00|0"]


def _run(fn, side: str, answers: list, rows: list[str]) -> tuple[tuple[str, str], _Replay]:
    client = _Replay(answers)
    with mock.patch(_SQL, side_effect=[_row(cell) for cell in rows]):
        return fn(_env(client), _SPEC, side), client


class ArapMoreCatalogTests(unittest.TestCase):
    def test_cases_registered_with_ops_tags_and_routes(self) -> None:
        names = known_names()
        cases = {spec.name: spec for spec in arap_more_cases()}
        self.assertEqual(tuple(cases), _NAMES)
        for name, spec in cases.items():
            self.assertEqual(names.count(name), 1, name)
            self.assertEqual(spec.routes, (_ROUTE, _CANCEL))
            self.assertTrue(spec.requires_login and spec.optional)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name), name)
        self.assertEqual(set(ARAP_MORE_OPS), {"hxm_prepay_ar", "hxm_prepay_ap", "hxm_fx_ar", "hxm_fx_ap"})

    def test_conflicts_with_the_plain_writeoff_cases(self) -> None:
        plain = {spec.name: auto_tags(spec) for spec in hx_cases()}
        for spec in arap_more_cases():
            other = plain["ar-writeoff" if spec.name.startswith("ar-") else "ap-writeoff"]
            self.assertTrue(conflicts(auto_tags(spec), other), spec.name)

    def test_changed_writeoff_files_pick_the_family(self) -> None:
        catalog = list(known_names())
        for path in ("co/bridge/src/ArapWriteoffPlan.cs", "co/bridge/src/ArapUnwriteoffPlan.cs"):
            picked = select_changed([path], catalog)
            for name in _NAMES:
                self.assertIn(name, picked, path)


class ArapMoreSqlTests(unittest.TestCase):
    def test_reads_pass_the_guard_and_are_in_samples(self) -> None:
        rows = sample_sql()
        for name, query in arap_more_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn("N'ROW:'", query)
            self.assertIn((name, query), rows)

    def test_prepay_pick_only_flips_the_prepay_filter(self) -> None:
        pick = prepay_pick_sql("AR")
        self.assertIn("ISNULL(b.bPrePay, 0) = 1", pick)
        self.assertNotIn("ISNULL(b.bPrePay, 0) = 0", pick)
        self.assertEqual(pick, hx_ar_sql().replace("ISNULL(b.bPrePay, 0) = 0", "ISNULL(b.bPrePay, 0) = 1"))
        self.assertIn("dbo.Ap_Detail", prepay_pick_sql("AP"))

    def test_fx_pick_shape(self) -> None:
        for side, detail, bill, mend in (("AR", "Ar_Detail", "R0", "bflag_AR"), ("AP", "Ap_Detail", "P0", "bflag_AP")):
            query = fx_pick_sql(side)
            for part in (detail, f"N'{bill}'", mend, "h.cexch_name <> (SELECT", "NOT IN (0, 1)", "v.iExchRate = h.iExchRate",
                         "ISNULL(b.bPrePay, 0) = 0"):
                self.assertIn(part, query, side)

    def test_readbacks(self) -> None:
        self.assertIn("d.iBVid = 14", balance_sql("AR", 12, ("invoice", 14)))
        self.assertIn("v.Auto_ID = 23 AND v.cFlag = N'AP'", balance_sql("AP", 22, ("bill", 23)))
        self.assertIn("d.cCancelNo = N'HXAP0000000007'", batch_sql("AP", "0000000007"))

    def test_rejects_bad_input(self) -> None:
        for bad in (0, -1, 2147483648, "12", True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                balance_sql("AR", bad, ("invoice", 1))  # type: ignore[arg-type]
        for call in (lambda: balance_sql("AR", 1, ("order", 1)), lambda: batch_sql("AR", "12x"), lambda: fx_pick_sql("GL"),
                     lambda: batch_sql("AR", "")):
            with self.assertRaises(ValueError):
                call()


class PrepayRunTests(unittest.TestCase):
    def test_full_cycle_passes(self) -> None:
        (status, detail), client = _run(prepay_run, "AR", [_written("AR"), _OK], _PREPAY_ROWS)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual([route for route, _sent in client.calls], [_ROUTE, _CANCEL])
        sent = client.calls[0][1]
        self.assertEqual(sent["receipt"], {"type": "ar_receipt", "id": 11, "line_id": 12})
        self.assertEqual(sent["items"], [{"type": "sale_invoice", "id": 13, "line_id": 14, "amount": 1}])
        self.assertEqual(client.calls[1][1]["cancel_no"], "HXAR0000000007")

    def test_ap_side_uses_payment_and_purchase_invoice(self) -> None:
        (status, detail), client = _run(prepay_run, "AP", [_written("AP"), _OK], _PREPAY_ROWS)
        self.assertEqual(status, "PASS", detail)
        sent = client.calls[0][1]
        self.assertEqual((sent["receipt"]["type"], sent["items"][0]["type"]), ("ap_payment", "purchase_invoice"))
        self.assertEqual(client.calls[1][1]["flag"], "AP")

    def test_na_without_data(self) -> None:
        (status, detail), client = _run(prepay_run, "AR", [], [""])
        self.assertEqual(status, "N/A")
        self.assertIn("ar_prepay", detail)
        self.assertEqual(client.calls, [])

    def test_missing_prepay_self_row_fails_and_cancels(self) -> None:
        rows = _PREPAY_ROWS[:3] + ["2|0|0|1.00|1.00"]
        (status, detail), client = _run(prepay_run, "AR", [_written("AR"), _OK], rows)
        self.assertEqual(status, "FAIL", detail)
        self.assertIn("bPrePay", detail)
        route, sent = client.calls[-1]
        self.assertEqual((route, sent["flag"], sent["cancel_no"]), (_CANCEL, "AR", "HXAR0000000007"))

    def test_refused_writeoff_fails_without_cancel(self) -> None:
        refused = U8CoError(409, "u8_rejected", "核销后余额不符")
        (status, detail), client = _run(prepay_run, "AR", [refused], _PREPAY_ROWS[:2])
        self.assertEqual(status, "FAIL")
        self.assertIn("u8_rejected", detail)
        self.assertEqual(len(client.calls), 1)

    def test_not_restored_after_cancel_fails(self) -> None:
        rows = _PREPAY_ROWS[:5] + ["50.00|50.00|1|18.08|0"]
        (status, detail), _client = _run(prepay_run, "AR", [_written("AR"), _OK, U8CoError(404, "not_found", "x")], rows)
        self.assertEqual(status, "FAIL")
        self.assertIn("取消后", detail)
        self.assertIn("已不存在", detail)


class FxRunTests(unittest.TestCase):
    def test_full_cycle_passes(self) -> None:
        (status, detail), client = _run(fx_run, "AR", [_written("AR"), _OK], _FX_ROWS)
        self.assertEqual(status, "PASS", detail)
        sent = client.calls[0][1]
        self.assertEqual(sent["receipt"], {"type": "ar_receipt", "id": 21, "line_id": 22})
        self.assertEqual(sent["items"], [{"type": "ar_bill", "id": 23, "amount": 1}])
        self.assertIn("7.1", detail)

    def test_bridge_refusal_is_na_with_the_text(self) -> None:
        refused = U8CoError(400, "bad_request", "外币核销暂不支持")
        (status, detail), client = _run(fx_run, "AP", [refused], _FX_ROWS[:2])
        self.assertEqual(status, "N/A")
        self.assertIn("外币核销暂不支持", detail)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][1]["items"][0]["type"], "ap_bill")

    def test_other_400_fails(self) -> None:
        refused = U8CoError(400, "bad_request", "items[].line_id 应收单按整单核销")
        (status, _detail), _client = _run(fx_run, "AR", [refused], _FX_ROWS[:2])
        self.assertEqual(status, "FAIL")

    def test_local_amount_must_follow_the_rate(self) -> None:
        for batch in ("2|0|2|1.00|1.00", "2|0|1|1.00|7.10", "1|0|1|1.00|7.10"):
            with self.subTest(batch=batch):
                rows = _FX_ROWS[:3] + [batch]
                (status, _detail), client = _run(fx_run, "AR", [_written("AR"), _OK], rows)
                self.assertEqual(status, "FAIL")
                self.assertEqual(client.calls[-1][0], _CANCEL)

    def test_exchange_loss_rows_fail(self) -> None:
        rows = [_FX_ROWS[0], _FX_ROWS[1], "19.00|134.90|0|19.00|1"]
        (status, detail), _client = _run(fx_run, "AR", [_written("AR"), _OK], rows)
        self.assertEqual(status, "FAIL")
        self.assertIn("9M", detail)

    def test_na_without_data_and_bad_cancel_no(self) -> None:
        (status, _detail), client = _run(fx_run, "AR", [], [""])
        self.assertEqual((status, client.calls), ("N/A", []))
        (status, detail), client = _run(fx_run, "AR", [{"ok": True, "cancel_no": "HXAP01"}], _FX_ROWS[:2])
        self.assertEqual(status, "FAIL")
        self.assertIn("快照还原", detail)
        self.assertEqual(len(client.calls), 1)


if __name__ == "__main__":
    unittest.main()
