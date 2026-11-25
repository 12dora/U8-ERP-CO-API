"""应收 / 应付待实测用例（arap_extra）的登记、分片、SQL 和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arap_voucher import av_cases
from co.e2e.casespec.ap_writeoff_voucher import ap_cases
from co.e2e.casespec.writeoff_prepay_fx import arap_more_cases
from co.e2e.casespec.arap_extra import ARAP_EXTRA_OPS, auto_prepay_run, pz_src, arap_extra_cases
from co.e2e.casespec.arap_extra_sql import head_sql, pz_src_sql, arap_extra_samples, so_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("ar-writeoff-auto-prepay", "ap-writeoff-auto-prepay")
_AUTO = "/v1/arap/writeoff/auto"
_CANCEL = "/v1/arap/writeoff/cancel"
_SQL = "co.e2e.casespec.arap_extra.run_guarded"
_SPEC = CaseSpec("ar-writeoff-auto-prepay", "s")


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


def _env(client: object, bag: dict | None = None) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-02", bag=bag or {})


def _row(cell: str) -> str:
    return "ROW:" + cell + "\n" if cell else ""


_EMPTY = {"ok": True, "dry_run": True, "mode": "plan", "plan": [], "total": 0}
_PLAN = {"ok": True, "dry_run": True, "mode": "plan", "total": 1.0,
         "plan": [{"receipt": {"id": 11, "line_id": 12}, "targets": [{"id": 13, "line_id": 15, "amount": 1.0}]}]}
_NONE = {"ok": True, "batches": [], "total": 0}


def _batch(side: str = "AR", digits: str = "0000000007") -> dict:
    piece = {"cancel_no": "HX" + side + digits, "receipt": {"id": 11, "line_id": 12}, "items": [{"line_id": 15}], "amount": 1.0}
    return {"ok": True, "batches": [piece], "total": 1.0}


def _answers(side: str = "AR") -> list:
    return [_EMPTY, _PLAN, _NONE, _batch(side), {"ok": True}]


_PICK = "11|12|13|14"
_HEAD = "C001|50.00|50.00|50.00|50.00|0"
_HEAD_AFTER = "C001|49.00|49.00|49.00|49.00|0"
_BAL = "50.00|50.00|0|18.08|0"
_BAL_AFTER = "49.00|49.00|0|17.08|0"
_SO = "300|10.00|10.00"
_SO_AFTER = "300|11.00|11.00"
_BATCH = "2|1|0|1.00|1.00"
_GONE = "0|0|0|0.00|0.00"


def _rows(side: str = "AR", head: tuple[str, str] = (_HEAD, _HEAD_AFTER), so: tuple[str, str] = (_SO, _SO_AFTER)) -> list[str]:
    # 挑单、表头；核销前快照；false 执行后快照；核销后快照、核销行；取消后核销行、快照。
    def snap(bal: str, top: str, order: str) -> list[str]:
        return [bal, top] + ([order] if side == "AR" else [])

    rows = [_PICK, head[0]]
    rows += snap(_BAL, head[0], so[0]) + snap(_BAL, head[0], so[0])
    rows += snap(_BAL_AFTER, head[1], so[1]) + [_BATCH]
    rows += [_GONE] + snap(_BAL, head[0], so[0])
    return rows


def _run(side: str, answers: list, rows: list[str]) -> tuple[tuple[str, str], _Replay]:
    client = _Replay(answers)
    with mock.patch(_SQL, side_effect=[_row(cell) for cell in rows]):
        return auto_prepay_run(_env(client), _SPEC, side), client


class CatalogTests(unittest.TestCase):
    def test_cases_registered_with_ops_tags_and_routes(self) -> None:
        names = known_names()
        cases = {spec.name: spec for spec in arap_extra_cases()}
        self.assertEqual(tuple(cases), _NAMES)
        for name, spec in cases.items():
            self.assertEqual(names.count(name), 1, name)
            self.assertEqual(spec.routes, (_AUTO, _CANCEL))
            self.assertTrue(spec.requires_login and spec.optional)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name), name)
        self.assertEqual(set(ARAP_EXTRA_OPS), {"auto_prepay_ar", "auto_prepay_ap", "pz_src"})

    def test_conflicts_with_the_prepay_writeoff_cases(self) -> None:
        plain = {spec.name: auto_tags(spec) for spec in arap_more_cases()}
        for spec in arap_extra_cases():
            other = plain["ar-writeoff-prepay" if spec.name.startswith("ar-") else "ap-writeoff-prepay"]
            self.assertTrue(conflicts(auto_tags(spec), other), spec.name)

    def test_voucher_cases_check_the_source_columns(self) -> None:
        ar = next(spec for spec in av_cases() if spec.name == "ar-voucher")
        ap = next(spec for spec in ap_cases() if spec.name == "ap-voucher")
        for spec, side, key, sign, maker in ((ar, "AR", "av_pzno", "AR", "av_make"), (ap, "AP", "dry_apb_pz", "AR", "dry_pz_make")):
            found = [step for step in spec.steps if step.sql_name == "pz_src"]
            self.assertEqual(len(found), 1, spec.name)
            self.assertEqual(dict(found[0].fields), {"side": side, "key": key, "sign": sign})
            self.assertTrue(ops_run.has_op("pz_src"))
            made = [step for step in spec.steps if step.sql_name == maker and step.status == 200]
            self.assertIn(("making_system", side), made[0].checks, spec.name)
            # 来源列核对在制单（记下外部业务号）之后。
            self.assertLess(spec.steps.index(made[0]), spec.steps.index(found[0]))


class SqlTests(unittest.TestCase):
    def test_reads_pass_the_guard_and_are_in_samples(self) -> None:
        rows = sample_sql()
        for name, query in arap_extra_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn("N'ROW:'", query)
            self.assertIn((name, query), rows)

    def test_shapes(self) -> None:
        head = head_sql(11)
        for part in ("sys.triggers", "N'TR_Ap_CloseBills'", "h.iRAmount_f", "b.iRAmt_f", "h.iID = 11"):
            self.assertIn(part, head)
        self.assertIn("b.AutoID = 15", so_sql(15))
        self.assertIn("COALESCE(NULLIF(b.iSOsID, 0), l.iSOsID)", so_sql(15))
        query = pz_src_sql("AP", "0000000000009", "AR")
        for part in ("g.coutno_id = N'AP0000000000009'", "= N'AP' THEN 0", "coutsign, N'') = N'AR'", "N'bUseYFKM'"):
            self.assertIn(part, query)

    def test_rejects_bad_input(self) -> None:
        for bad in (0, -1, 2147483648, "12", True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                head_sql(bad)  # type: ignore[arg-type]
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                so_sql(bad)  # type: ignore[arg-type]
        for args in (("GL", "1", "AR"), ("AR", "1x", "AR"), ("AR", "", "AR"), ("AR", "1", "GL")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                pz_src_sql(*args)


class AutoPrepayRunTests(unittest.TestCase):
    def test_full_cycle_passes(self) -> None:
        (status, detail), client = _run("AR", _answers(), _rows())
        self.assertEqual(status, "PASS", detail)
        self.assertEqual([route for route, _sent in client.calls], [_AUTO] * 4 + [_CANCEL])
        first = client.calls[0][1]
        self.assertNotIn("include_prepay", first)
        self.assertEqual(first["receipt"], {"type": "ar_receipt", "id": 11, "line_id": 12})
        self.assertEqual(first["targets"], [{"type": "sale_invoice", "id": 13}])
        self.assertEqual(first["partner"], "C001")
        flags = [(sent.get("include_prepay"), sent["dry_run"], sent.get("max_amount")) for _r, sent in client.calls[1:4]]
        self.assertEqual(flags, [(True, True, None), (False, False, 1), (True, False, 1)])
        self.assertEqual(client.calls[4][1]["cancel_no"], "HXAR0000000007")
        self.assertIn("触发器启用", detail)
        self.assertIn("订单行 300", detail)

    def test_ap_side_has_no_order_check(self) -> None:
        (status, detail), client = _run("AP", _answers("AP"), _rows("AP"))
        self.assertEqual(status, "PASS", detail)
        sent = client.calls[0][1]
        self.assertEqual((sent["receipt"]["type"], sent["targets"][0]["type"]), ("ap_payment", "purchase_invoice"))
        self.assertEqual(client.calls[-1][1]["cancel_no"], "HXAP0000000007")
        self.assertNotIn("订单行", detail)

    def test_na_without_data_or_with_hx_rules(self) -> None:
        (status, detail), client = _run("AR", [], [""])
        self.assertEqual((status, client.calls), ("N/A", []))
        self.assertIn("ar_prepay", detail)
        refused = U8CoError(409, "state_mismatch", "账套设置了按规则核销（订单 / 合同 / 存货等），自动核销暂不支持")
        (status, detail), client = _run("AR", [refused], [_PICK, _HEAD])
        self.assertEqual(status, "N/A")
        self.assertEqual(len(client.calls), 1)

    def test_default_plan_must_be_empty(self) -> None:
        (status, detail), client = _run("AR", [_PLAN], [_PICK, _HEAD])
        self.assertEqual(status, "FAIL")
        self.assertIn("空计划", detail)
        self.assertEqual(len(client.calls), 1)

    def test_false_run_that_writes_fails_and_cancels(self) -> None:
        answers = [_EMPTY, _PLAN, _batch(), {"ok": True}]
        (status, detail), client = _run("AR", answers, [_PICK, _HEAD, _BAL, _HEAD, _SO])
        self.assertEqual(status, "FAIL")
        self.assertIn("include_prepay=false", detail)
        route, sent = client.calls[-1]
        self.assertEqual((route, sent["cancel_no"]), (_CANCEL, "HXAR0000000007"))

    def test_header_must_follow_lines_when_trigger_enabled(self) -> None:
        stale = "C001|50.00|50.00|49.00|49.00|0"
        (status, detail), client = _run("AR", _answers(), _rows(head=(_HEAD, stale))[:12])
        self.assertEqual(status, "FAIL")
        self.assertIn("TR_Ap_CloseBills", detail)
        self.assertEqual(client.calls[-1][0], _CANCEL)

    def test_disabled_trigger_is_only_recorded(self) -> None:
        before, after = "C001|50.00|50.00|50.00|50.00|1", "C001|50.00|50.00|49.00|49.00|1"
        (status, detail), _client = _run("AR", _answers(), _rows(head=(before, after)))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("触发器停用", detail)

    def test_order_columns_must_move(self) -> None:
        (status, detail), client = _run("AR", _answers(), _rows(so=(_SO, _SO))[:12])
        self.assertEqual(status, "FAIL")
        self.assertIn("iExchSum", detail)
        self.assertEqual(client.calls[-1][0], _CANCEL)

    def test_invoice_without_order_is_only_recorded(self) -> None:
        none = "0|0.00|0.00"
        (status, detail), _client = _run("AR", _answers(), _rows(so=(none, none)))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("没有来源订单行", detail)

    def test_two_batches_fail_and_cancel_both(self) -> None:
        two = _batch()
        two["batches"] = two["batches"] + [dict(two["batches"][0], cancel_no="HXAR0000000008")]
        answers = [_EMPTY, _PLAN, _NONE, two, {"ok": True}, {"ok": True}]
        rows = [_PICK, _HEAD, _BAL, _HEAD, _SO, _BAL, _HEAD, _SO]
        (status, detail), client = _run("AR", answers, rows)
        self.assertEqual(status, "FAIL")
        self.assertIn("恰好一批", detail)
        cancelled = [sent["cancel_no"] for route, sent in client.calls if route == _CANCEL]
        self.assertEqual(cancelled, ["HXAR0000000007", "HXAR0000000008"])

    def test_not_restored_after_cancel_fails_without_second_cancel(self) -> None:
        rows = _rows()[:-3] + ["50.00|50.00|1|18.08|0", _HEAD, _SO]
        (status, detail), client = _run("AR", _answers(), rows)
        self.assertEqual(status, "FAIL")
        self.assertIn("取消后", detail)
        self.assertEqual([route for route, _sent in client.calls].count(_CANCEL), 1)


class PzSrcTests(unittest.TestCase):
    def _step(self, side: str = "AP", key: str = "dry_apb_pz", sign: str = "AR") -> Step:
        return Step("custom", fields=(("side", side), ("key", key), ("sign", sign)), sql_name="pz_src", id_from="none")

    def _go(self, cell: str, step: Step | None = None) -> tuple[str, str]:
        env = _env(_Replay([]), {"dry_apb_pz": "0000000000009"})
        with mock.patch(_SQL, return_value=_row(cell)) as sql:
            got = pz_src(env, _SPEC, step or self._step())
        if sql.call_count:
            self.assertIn("N'AP0000000000009'", sql.call_args[0][1])
        return got

    def test_pass_records_options(self) -> None:
        status, detail = self._go("3|0|0|1|1|0")
        self.assertEqual(status, "PASS", detail)
        self.assertIn("bUseYSKM=1", detail)

    def test_wrong_source_fails(self) -> None:
        for cell in ("3|1|0|0|-|-", "3|0|2|0|-|-", "1|0|0|0|-|-"):
            with self.subTest(cell=cell):
                self.assertEqual(self._go(cell)[0], "FAIL")
        self.assertEqual(self._go("")[0], "FAIL")

    def test_bad_arguments(self) -> None:
        self.assertEqual(self._go("3|0|0|0|-|-", self._step(key="Bad-Key"))[0], "FAIL")
        with self.assertRaises(ValueError):
            self._go("3|0|0|0|-|-", self._step(key="missing_key"))


if __name__ == "__main__":
    unittest.main()
