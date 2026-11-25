"""采购入库生单带货位（qm_binned_in）：参照来料检验单入库到删除、货位校验 400 的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import qm_binned_in as qbi
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.qm_binned_in_sql import bin_wh_sql, check_sum_sql, line_pos_sql, qm_bin_pick_sql, qm_bin_samples
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "binned-qm-purchase-in-delete"
_BAD = "purchase-in-gen-position-bad"
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_PICK = "ROW:100|01|A01|I1\n"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    def __init__(self, answers: dict[str, list] | None = None) -> None:
        self.answers = answers or {
            "/v1/vouchers/generate": [{"ok": True, "id": 5}],
            "/v1/vouchers/verify": [{"ok": True}, {"ok": True}],
            "/v1/vouchers/delete": [{"ok": True, "deleted": True}],
        }
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers[route].pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got


class _Sql:
    """按 SQL 里的特征串依次回答（每个键一个队列），没有匹配就报错。"""

    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]

    def __call__(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.answers:
            if key in query and rows:
                return rows.pop(0)
        raise AssertionError("没有准备回答：" + query[:80])


def _rows(*values: str) -> list[str]:
    return [f"ROW:{value}\n" for value in values]


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-02", bag={})


def _sql(line: str = "1|A01|100", fsum: tuple = ("3.0000", "4.0000", "3.0000"),
         psum: tuple = ("10.0000", "11.0000", "11.0000", "10.0000"), rows: tuple = ("1", "1", "0")) -> _Sql:
    return _Sql([
        ("ORDER BY q.ID DESC", [_PICK]),
        ("InvPositionSum AS p WHERE", _rows(*psum)),
        ("c.cWhCode = N'", _rows("20.0000", "20.0000")),
        ("MAX(q.FsumQuantity)", _rows(*fsum)),
        ("rdrecords01 AS b WHERE b.ID = 5", _rows(line)),
        ("InvPosition AS p WHERE p.RdID", _rows(*rows)),
        ("h.ID = 5", _rows("0")),
    ])


def _run(client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql):
        return qbi.binned_qm_purchase_in(_env(client), _SPEC, _STEP)  # type: ignore[arg-type]


def _order(client: _Replay) -> list[tuple[str, str]]:
    return [(route.rsplit("/", 1)[1], fields.get("action", "")) for route, fields in client.calls]


class QmBinRegistryTests(unittest.TestCase):
    def test_registered_with_op_and_stock_tag(self) -> None:
        names = known_names()
        self.assertEqual([spec.name for spec in qbi.qm_bin_cases()], [_NAME, _BAD])
        self.assertEqual(names.count(_NAME), 1)
        self.assertEqual(names.count(_BAD), 1)
        bad = qbi.qm_bin_cases()[1]
        self.assertTrue(ops_run.has_op(bad.steps[0].sql_name))
        self.assertIn("probe", bad.tags)
        spec = qbi.qm_bin_cases()[0]
        self.assertTrue(spec.requires_login and spec.routes)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertTrue({"stock", "new:purchase_in"} <= auto_tags(spec))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in qm_bin_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        self.assertIn("w.bWhPos = 1", qm_bin_pick_sql())
        self.assertIn("ZZNOWH1", bin_wh_sql())
        self.assertIn("b.ID = 7", line_pos_sql("7"))
        self.assertIn("q.ID = 7 AND q.CVOUCHTYPE = N'QM03'", check_sum_sql("7"))
        for bad in (lambda: line_pos_sql("x"), lambda: check_sum_sql("0")):
            with self.assertRaises(ValueError):
                bad()


class BinnedQmPurchaseInTests(unittest.TestCase):
    def test_generate_with_position_then_rolled_back(self) -> None:
        client = _Replay()
        status, detail = _run(client, _sql())
        self.assertEqual(status, "PASS", detail)
        self.assertIn("保存时", detail)
        body = client.calls[0][1]
        self.assertEqual((body["type"], body["id"], body["source_type"]), ("purchase_in", 100, "qm_incoming_check"))
        self.assertEqual(body["head"]["cwhcode"], "01")
        self.assertEqual(body["lines"], [{"source_line_id": 100, "quantity": 1, "cposition": "A01"}])
        self.assertEqual(_order(client), [("generate", ""), ("verify", "verify"), ("verify", "unverify"), ("delete", "")])

    def test_written_at_verify_is_reported(self) -> None:
        status, detail = _run(_Replay(), _sql(psum=("10.0000", "10.0000", "11.0000", "10.0000"), rows=("0", "1", "0")))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("审核时", detail)

    def test_line_without_position_fails_after_cleanup(self) -> None:
        client = _Replay()
        status, detail = _run(client, _sql(line="1||100"))
        self.assertEqual(status, "FAIL")
        self.assertIn("货位 A01", detail)
        self.assertEqual(client.answers["/v1/vouchers/delete"], [])

    def test_check_sum_not_back_fails(self) -> None:
        status, detail = _run(_Replay(), _sql(fsum=("3.0000", "4.0000", "4.0000")))
        self.assertEqual(status, "FAIL")
        self.assertIn("删除后检验单累计入库数", detail)

    def test_nothing_to_pick_is_na_without_calls(self) -> None:
        client = _Replay()
        status, _detail = _run(client, _Sql([("ORDER BY q.ID DESC", [""])]))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])


def _err(field: str) -> U8CoError:
    return U8CoError(400, "bad_request", "货位或仓库不对", field)


_POS = "lines.0.cposition"
_FIELDS = (_POS, _POS, "head.cwhcode", _POS, _POS, "head.cwhcode")


def _bad_sql(qm: str = _PICK, po: str = "ROW:200|201|02\n") -> _Sql:
    return _Sql([
        ("ZZNOWH1", ["ROW:01|A01|ZZNOWH1\n"]),
        ("ORDER BY q.ID DESC", [qm]),
        ("ORDER BY m.POID DESC", [po]),
    ])


def _bad_run(client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql):
        return qbi.purchase_in_position_bad(_env(client), _SPEC, _STEP)  # type: ignore[arg-type]


class PositionBadTests(unittest.TestCase):
    def test_all_refused_with_fields(self) -> None:
        client = _Replay({"/v1/vouchers/generate": [_err(field) for field in _FIELDS]})
        status, detail = _bad_run(client, _bad_sql())
        self.assertEqual(status, "PASS", detail)
        bodies = [fields for _route, fields in client.calls]
        self.assertEqual(len(bodies), 6)
        self.assertEqual((bodies[0]["source_type"], bodies[0]["head"]["cwhcode"]), ("qm_incoming_check", "01"))
        self.assertNotIn("cposition", bodies[0]["lines"][0])
        self.assertEqual(bodies[2]["head"]["cwhcode"], "ZZNOWH1")
        self.assertEqual((bodies[3]["id"], bodies[3]["head"]["cwhcode"]), (200, "01"))
        self.assertEqual((bodies[4]["head"]["cwhcode"], bodies[4]["lines"][0]["cposition"]), ("02", "A01"))

    def test_saved_is_deleted_and_fails(self) -> None:
        answers = [{"ok": True, "id": 9}] + [_err(field) for field in _FIELDS[1:]]
        client = _Replay({"/v1/vouchers/generate": answers, "/v1/vouchers/delete": [{"ok": True, "deleted": True}]})
        status, detail = _bad_run(client, _bad_sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("实际保存了 9", detail)
        self.assertEqual(client.calls[-1][0], "/v1/vouchers/delete")

    def test_wrong_field_fails(self) -> None:
        client = _Replay({"/v1/vouchers/generate": [_err("lines") for _ in _FIELDS]})
        status, detail = _bad_run(client, _bad_sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("field=lines.0.cposition", detail)

    def test_only_po_source_and_no_bin_warehouse(self) -> None:
        client = _Replay({"/v1/vouchers/generate": [_err(field) for field in _FIELDS[3:]]})
        status, detail = _bad_run(client, _bad_sql(qm=""))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("采购订单的 3 个", detail)
        status, _detail = _bad_run(_Replay({}), _Sql([("ZZNOWH1", [""])]))
        self.assertEqual(status, "N/A")


if __name__ == "__main__":
    unittest.main()
