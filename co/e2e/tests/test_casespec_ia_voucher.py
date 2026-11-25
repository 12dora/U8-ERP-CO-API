"""存货核算制单用例（ia_voucher）的登记、分片、SQL 形状和纯函数核对。不连接服务器。"""

from __future__ import annotations

import json
import unittest

from co.e2e.casespec import ia_voucher as iv
from co.e2e.casespec import ia_voucher_sql as q
from co.e2e.casespec import ops_run
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, is_exclusive
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "ia-voucher-post-cycle"
_PZ = "2026IA0000000000001"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _voucher(lines: int = 2, rows: int = 3, types: tuple[str, ...] = ("01",)) -> dict:
    line = {"account": "1403", "debit": 0, "credit": 0}
    bills = [{"type": t, "id": i + 1, "rows": rows if i == 0 else 0} for i, t in enumerate(types)]
    return {"pz_id": _PZ, "bills": bills, "lines": [dict(line) for _ in range(lines)]}


class IaVoucherCatalogTests(unittest.TestCase):
    def test_case_registered_exclusive(self) -> None:
        self.assertEqual(known_names().count(_NAME), 1)
        (spec,) = iv.ia_voucher_cases()
        self.assertEqual(spec.name, _NAME)
        self.assertTrue(spec.requires_login)
        self.assertTrue(is_exclusive(spec))
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertLessEqual({"ia:post", "period:close", "gl"}, set(auto_tags(spec)))
        self.assertEqual(spec.routes, (iv.POST_ROUTE, iv.VOUCHER_ROUTE, iv.DELETE_ROUTE, iv.LOAD_ROUTE))


class IaVoucherSqlTests(unittest.TestCase):
    def test_samples_pass_the_guard(self) -> None:
        for name, query in q.ia_voucher_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn("N'ROW:'", query)
            self.assertIn((name, query), sample_sql())

    def test_bills_sql_reads_costed_in_bills(self) -> None:
        query = q.bills_sql(2026, 10)
        for text in ("RdRecords01", "RdRecords08", "RdRecords10", "iMonth = 10", "cPZID IS NULL", "iAInPrice IS NOT NULL",
                     "LEFT(ISNULL(s.cBusType, N''), 2) NOT IN (N'调拨', N'转换', N'组装', N'拆卸')"):
            self.assertIn(text, query)
        for text in ("RdRecords32", "bSaleType", "N'销售出库单'", "RdRecords09", "cBusCode", "N'调拨', N'转换'"):
            self.assertIn(text, query)
        for year, month in ((26, 1), (2026, 0), (2026, 13), ("2026", 1)):
            with self.subTest(year=year, month=month), self.assertRaises(ValueError):
                q.bills_sql(year, month)  # type: ignore[arg-type]

    def test_pz_id_is_checked_before_sql(self) -> None:
        self.assertIn(_PZ, q.marks_sql(_PZ))
        for table in ("RdRecord08", "RdRecord09", "RdRecord32"):
            self.assertIn(table, q.marks_sql(_PZ))
            self.assertIn(table, q.left_sql(_PZ))
        for bad in ("AP0000000000001", "2026IA1", "2026IA0000000000001'--"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                q.left_sql(bad)


class IaVoucherCheckTests(unittest.TestCase):
    def test_pick_ids(self) -> None:
        self.assertEqual(iv.pick_ids(["01|12", "10|7"]), [{"type": "01", "id": 12}, {"type": "10", "id": 7}])
        with self.assertRaises(ValueError):
            iv.pick_ids(["01"])

    def test_plan_problem(self) -> None:
        good = {"dry_run": True, "mode": "validate", "action": "ia_voucher",
                "detail": {"vouchers": [{"pz_id": _PZ, "lines": [{"debit": 5, "credit": 0}, {"debit": 0, "credit": 5}]}]}}
        self.assertEqual(iv.plan_problem(good), "")
        bad = json.loads(json.dumps(good))
        bad["detail"]["vouchers"][0]["lines"][1]["credit"] = 4
        self.assertIn("借", iv.plan_problem(bad))
        self.assertIn("validate", iv.plan_problem(dict(good, mode="rollback")))

    def test_marks_problem(self) -> None:
        self.assertEqual(iv.marks_problem("2|2|5.00|5.00|1|3|2|1", _voucher()), "")
        self.assertNotEqual(iv.marks_problem("2|1|5.00|5.00|1|3|2|1", _voucher()), "")
        self.assertNotEqual(iv.marks_problem("2|2|5.00|4.00|1|3|2|1", _voucher()), "")
        self.assertNotEqual(iv.marks_problem("2|2|5.00|5.00|1|2|2|1", _voucher()), "")
        self.assertNotEqual(iv.marks_problem("2|2|5.00|5.00|1|3|2|0", _voucher()), "")
        self.assertNotEqual(iv.marks_problem("2|2|5.00|5.00|1|3|2", _voucher()), "")
        self.assertEqual(iv.marks_problem("2|2|5.00|5.00|1|3|2|2", _voucher(types=("08", "09"))), "")
        self.assertNotEqual(iv.marks_problem("0|0|0.00|0.00|0|0|0|0", _voucher(lines=0, rows=0)), "")

    def test_extra_bills(self) -> None:
        main, extra = iv.split_ids([{"type": "01", "id": 1}, {"type": "32", "id": 2}, {"type": "09", "id": 3}])
        self.assertEqual(main, [{"type": "01", "id": 1}])
        self.assertEqual([i["type"] for i in extra], ["32", "09"])
        self.assertEqual(iv.pair_problem({"type": "09", "id": 3}, _voucher(types=("08", "09"))), "")
        self.assertIn("08 和 09", iv.pair_problem({"type": "09", "id": 3}, _voucher(types=("09",))))
        self.assertEqual(iv.pair_problem({"type": "32", "id": 2}, _voucher(types=("32",))), "")
        note = iv.coverage({"32": "已测"})
        self.assertIn("销售出库 32：已测", note)
        self.assertIn("调拨 / 形态转换：N/A", note)


if __name__ == "__main__":
    unittest.main()
