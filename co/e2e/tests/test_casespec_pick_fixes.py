"""挑单和 N/A 措辞修正：
可选用例与业务缺失的 N/A 不再说「测试账套」；其他出库审核挑单允许货位仓库兜底；材料出库子件没填仓库时按现存量取仓库；
新建客户的行业编码可不配；没有模块生成凭证、没有被引用客户、客户不足一页时记 N/A。不连接服务器。"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from co.e2e import testdata
from co.e2e.casespec import arc_write, gl_voucher
from co.e2e.casespec.gl_voucher_bodies import arc_data, arc_expect
from co.e2e.casespec.gl_voucher_bodies import arc_body as build_arc_body
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arap_gen_sql import mo_alloc_sql
from co.e2e.casespec.sql_st import other_out_open_sql
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_CASESPEC = Path(__file__).resolve().parents[1] / "casespec"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _env(bag: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(year="2026", today="2026-10-02", bag=dict(bag or {}), focus="")


class WordingTests(unittest.TestCase):
    def test_no_test_account_wording_in_na_reasons(self) -> None:
        hits = [
            path.name
            for path in sorted(_CASESPEC.glob("*.py"))
            if "测试账套没有" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(hits, [])


class OtherOutOpenPickCases(unittest.TestCase):
    def test_bin_warehouse_is_fallback(self) -> None:
        sql = other_out_open_sql()
        self.assertNotIn("w.bWhPos = 0", sql)
        self.assertIn("ORDER BY ISNULL(w.bWhPos, 0), r.ID DESC", sql)
        self.assertIn("r.dVeriDate IS NULL", sql)
        self.assertEqual(sql_block_reason(sql), "")


class MaterialOutPickTests(unittest.TestCase):
    def test_blank_allocate_warehouse_falls_back_to_stock(self) -> None:
        sql = mo_alloc_sql()
        self.assertIn("FROM dbo.CurrentStock AS c", sql)
        self.assertIn("COALESCE(NULLIF(LTRIM(RTRIM(a.WhCode)), N''), cs.code)", sql)
        self.assertIn("HAVING SUM(ISNULL(c.iQuantity, 0)) > 0", sql)
        self.assertIn("cw.bWhPos = 0", sql)
        self.assertIn("ORDER BY SUM(ISNULL(c.iQuantity, 0)) DESC, c.cWhCode", sql)
        self.assertIn("JOIN dbo.Warehouse AS w ON w.cWhCode = wc.code", sql)
        self.assertIn("+ N'|' + wc.code AS v", sql)
        self.assertEqual(sql_block_reason(sql), "")

    def test_rows_with_allocate_warehouse_come_first(self) -> None:
        # 子件填了仓库的行排在最前，原来能挑到的挑单不变。
        order = mo_alloc_sql().split("ORDER BY\n", 1)[1]
        self.assertTrue(order.lstrip().startswith("CASE WHEN NULLIF(LTRIM(RTRIM(a.WhCode)), N'') IS NULL THEN 1 ELSE 0 END"))


class CustomerTradeCodeTests(unittest.TestCase):
    _SPEC = CaseSpec("archive-customer-cycle", "s")
    _CREATE = Step("core_arc", "/v1/archives/create", (("archive", "customer"),), sql_name="create")

    def tearDown(self) -> None:
        testdata.use(support.sample_testdata())

    def _fields(self) -> dict:
        env = _env({"core:archive-customer-cycle": "C199901"})
        return build_arc_body(env, self._SPEC, self._CREATE)["fields"]

    def test_trade_code_not_required(self) -> None:
        self.assertNotIn("archives.customer.trade_code", arc_data("customer"))
        self.assertIn("archives.customer.sort_code", arc_data("customer"))

    def test_sent_when_configured(self) -> None:
        self.assertEqual(self._fields()["cCusTradeCCode"], "A")

    def test_omitted_when_absent(self) -> None:
        data = copy.deepcopy(support.sample_testdata())
        del data["archives"]["customer"]["trade_code"]
        testdata.use(data)
        fields = self._fields()
        self.assertNotIn("cCusTradeCCode", fields)
        self.assertEqual(fields["sort_code"], data["archives"]["customer"]["sort_code"])
        get = Step("core_arc", "/v1/archives/get", (("archive", "customer"),), sql_name="get_created")
        self.assertTrue(arc_expect(_env({"core:archive-customer-cycle": "C199901"}), self._SPEC, get))

    def test_invalid_value_still_raises(self) -> None:
        data = copy.deepcopy(support.sample_testdata())
        data["archives"]["customer"]["trade_code"] = ["A"]
        testdata.use(data)
        with self.assertRaises(testdata.DataMissing):
            self._fields()


class AbsentBusinessTests(unittest.TestCase):
    _SPEC = CaseSpec("archive-delete-used", "s")

    def _arc_pick(self, mode: str, out: str) -> tuple[tuple[str, str], dict]:
        env = _env()
        step = Step("core_arcpick", fields=(("archive", "customer"),), sql_name=mode)
        with mock.patch.object(arc_write, "run_guarded", return_value=out):
            got = arc_write.step_arc_pick(env, self._SPEC, step)
        return got, env.bag

    def test_no_used_customer_is_na(self) -> None:
        got, bag = self._arc_pick("used", "v\n-")
        self.assertEqual(got, ("N/A", "账套没有被销售订单、发货单或收款单引用的客户"))
        self.assertEqual(bag, {})

    def test_short_customer_list_is_na(self) -> None:
        got, _bag = self._arc_pick("prefix", "v\n-\nROW:-")
        self.assertEqual(got, ("N/A", "账套客户不足 6 个，无法分页列表"))

    def test_found_values_pass(self) -> None:
        self.assertEqual(self._arc_pick("used", "ROW:C000001")[0], ("PASS", "C000001"))
        self.assertEqual(self._arc_pick("prefix", "ROW:C10")[0], ("PASS", "C10"))

    def test_free_codes_taken_stays_skip(self) -> None:
        got, _bag = self._arc_pick("free", "v\n-")
        self.assertEqual(got, ("SKIP", "账套里候选编码都被占用，或缺依赖档案"))

    def test_no_module_voucher_is_na(self) -> None:
        env = _env()
        step = Step("core_glpick")
        spec = CaseSpec("gl-module-readonly", "s")
        with mock.patch.object(gl_voucher, "run_guarded", return_value="v\n-"):
            self.assertEqual(gl_voucher.step_gl_pick(env, spec, step), ("N/A", "账套没有模块生成的凭证"))
        with mock.patch.object(gl_voucher, "run_guarded", return_value="ROW:bad"):
            self.assertEqual(gl_voucher.step_gl_pick(env, spec, step)[0], "SKIP")
        with mock.patch.object(gl_voucher, "run_guarded", return_value="ROW:9|记|12"):
            self.assertEqual(gl_voucher.step_gl_pick(env, spec, step), ("PASS", "9|记|12"))


if __name__ == "__main__":
    unittest.main()
