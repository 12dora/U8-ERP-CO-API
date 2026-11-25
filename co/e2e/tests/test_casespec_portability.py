"""不同账套间的用例可移植性修正：销售订单挑单避开已关闭、应付审核登记行按发票行数、
质量单据模板必输的自定义项（qm_tpl）、没有使用应收 / 应付的账套记 N/A（arap_ctl）。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import arap_ctl, ops_run, qm_tpl
from co.e2e.casespec.catalog import build_cases
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.wiring import has_edit, edit_sql
from co.e2e.casespec.so_lock_sql import lock_so_sql
from co.e2e.casespec.qm_write import qmi_qm01, qmp_qm02, qmp_qm04
from co.e2e.casespec.qm_write_sql import TPL_TABLES, tpl_required_sql
from co.e2e.casespec.arap_verify import arv_cases
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.arap_gen_sql import arap_ctl_sql
from co.e2e.casespec.sql_sa import sale_open_sql
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_SPEC = CaseSpec("ap-payment-cycle", "s")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _env(bag: dict | None = None, year: str = "2026") -> SimpleNamespace:
    return SimpleNamespace(year=year, today="2026-10-02", bag=dict(bag or {}), focus="31")


def _cases() -> dict[str, CaseSpec]:
    return {spec.name: spec for spec in build_cases({})}


class SalePickTests(unittest.TestCase):
    def test_sale_open_skips_closed_orders(self) -> None:
        sql = sale_open_sql()
        self.assertIn("LTRIM(RTRIM(ISNULL(m.cCloser, N''))) = N''", sql)
        self.assertIn("FROM dbo.SO_SODetails AS d", sql)
        self.assertIn("LTRIM(RTRIM(ISNULL(d.cSCloser, N''))) <> N''", sql)
        self.assertEqual(sql_block_reason(sql), "")

    def test_lock_pick_skips_closed_lines(self) -> None:
        sql = lock_so_sql()
        self.assertIn("NOT EXISTS (SELECT 1 FROM dbo.SO_SODetails AS d WHERE d.ID = m.ID", sql)
        self.assertIn("d.cSCloser", sql)


class ApAuditRowsTests(unittest.TestCase):
    def test_rows_compare_with_invoice_lines(self) -> None:
        pu = arv_cases()[0]
        self.assertEqual((pu.steps[0].kind, pu.steps[0].sql_name, pu.steps[0].code), ("stash", "aud_pu_lines", "aud_pu_lines"))
        preds = [(step.sql_name, step.code) for step in pu.steps if step.kind == "pred"]
        self.assertIn(("aud_pu_rows", "same:aud_pu_lines"), preds)
        self.assertIn(("aud_pu_rows", "eq:0"), preds)
        self.assertNotIn(("aud_pu_rows", "eq:1"), preds)
        # 0 行的发票会让 same: 空过：stash 之后、审核之前先断言行数大于 0。
        self.assertEqual((pu.steps[1].kind, pu.steps[1].sql_name, pu.steps[1].code), ("pred", "aud_pu_lines", "gt0"))
        self.assertEqual(pu.steps[2].kind, "call")

    def test_lines_readback_is_wired(self) -> None:
        self.assertTrue(has_edit("aud_pu_lines"))
        sql = edit_sql("aud_pu_lines", "42", {})
        self.assertIn("FROM dbo.PurBillVouchs AS b WHERE b.PBVID = 42;", sql)
        with self.assertRaises(ValueError):
            edit_sql("aud_pu_lines", "1;--", {})


class ArapCtlTests(unittest.TestCase):
    def test_sql_shape_and_guard(self) -> None:
        sql = arap_ctl_sql("AP", 2026)
        self.assertIn("k.iyear = 2026 AND k.bend = 1", sql)
        self.assertIn("ISNULL(k.cother, N'')))) = N'AP'", sql)
        for bad in (lambda: arap_ctl_sql("GL", 2026), lambda: arap_ctl_sql("AR", 26)):
            with self.assertRaises(ValueError):
                bad()
        rows = dict(sample_sql())
        for flag in ("ar", "ap"):
            self.assertEqual(sql_block_reason(rows["core-arap-ctl-" + flag]), "")

    def _run(self, answer: str, side: str = "AP", year: str = "2026") -> tuple[str, str]:
        step = arap_ctl.ctl_step(side)
        with mock.patch.object(arap_ctl, "run_guarded", return_value=answer) as run:
            got = arap_ctl.arap_ctl(_env(year=year), _SPEC, step)
        if year.isdigit():
            self.assertIn(f"N'{side}'", run.call_args[0][1])
        return got

    def test_no_controlled_account_is_na(self) -> None:
        status, detail = self._run("ROW:0\n")
        self.assertEqual(status, "N/A")
        self.assertIn("账套没有应付受控科目，未使用应付款管理", detail)
        status, detail = self._run("ROW:0\n", side="AR")
        self.assertIn("账套没有应收受控科目，未使用应收款管理", detail)

    def test_controlled_accounts_pass_and_bad_input_fails(self) -> None:
        self.assertEqual(self._run("ROW:3\n")[0], "PASS")
        self.assertEqual(self._run("")[0], "FAIL")
        self.assertEqual(self._run("ROW:3\n", year="")[0], "FAIL")
        with self.assertRaises(ValueError):
            arap_ctl.ctl_step("GL")

    def test_every_arap_master_case_starts_with_the_check(self) -> None:
        self.assertTrue(ops_run.has_op(arap_ctl.OP))
        found = []
        for spec in build_cases({}):
            sides = {arap_ctl.NEED_SIDE[need] for need in spec.needs if need in arap_ctl.NEED_SIDE}
            if not sides:
                continue
            found.append(spec.name)
            first = spec.steps[0]
            with self.subTest(case=spec.name):
                self.assertEqual((first.kind, first.sql_name), ("custom", arap_ctl.OP))
                self.assertIn(dict(first.fields)["side"], sides)
        for name in ("ap-payment-cycle", "ap-bill-edit", "dry-ap-payment", "dry-ar-receipt", "dry-ar-voucher", "ap-voucher"):
            self.assertIn(name, found)


class QmTplTests(unittest.TestCase):
    def test_sql_mirrors_the_bridge_query(self) -> None:
        sql = tpl_required_sql(354)
        self.assertIn("FROM dbo.voucheritems AS v", sql)
        self.assertIn("WHERE v.VT_ID = 354 AND v.IsNull = 1", sql)
        self.assertIn("OBJECT_ID(N'dbo.QMCHECKVOUCHER_extradefine', N'U')", sql)
        with self.assertRaises(ValueError):
            tpl_required_sql(357)
        rows = sample_sql()
        for vt in TPL_TABLES:
            name = "more-tpl-" + str(vt)
            self.assertIn((name, tpl_required_sql(vt)), rows)
            self.assertEqual(sql_block_reason(tpl_required_sql(vt)), "")

    def test_split_rows_by_section_scope_and_type(self) -> None:
        cells = ["chdefine15|T|nvarchar", "CDEFINE7|T|float", "cdefine6|T|datetime", "CINVCODE|B|nvarchar", "DDATE|T|datetime"]
        fill, stuck = qm_tpl.split_rows(cells, 354, "2026-10-02")
        self.assertEqual(fill, {"chdefine15": "E2E", "cdefine7": "1", "cdefine6": "2026-10-02"})
        self.assertEqual(stuck, [])
        # 报检单表头不收 chdefine；表体自定义项都带不了。
        fill, stuck = qm_tpl.split_rows(["chdefine15|T|nvarchar", "cdefine28|B|nvarchar", "cdefine1|T|"], 352, "2026-10-02")
        self.assertEqual(fill, {"cdefine1": "E2E"})
        self.assertEqual(stuck, ["chdefine15", "cdefine28（表体）"])

    def test_pack_round_trip_and_merge_keeps_picked_values(self) -> None:
        fill = {"chdefine15": "E2E", "chdefine11": "E2E", "cdefine7": "1"}
        self.assertEqual(qm_tpl.unpack(qm_tpl.pack(fill)), fill)
        self.assertEqual(qm_tpl.unpack("x=1;cdefine99x=2;"), {})
        head = qm_tpl.merge_head({"chDefine11": "A01"}, fill)
        self.assertEqual(head, {"chDefine11": "A01", "chdefine15": "E2E", "cdefine7": "1"})

    def _op(self, cells: str, vt: int = 354, key: str = "qmp_t4") -> tuple[tuple[str, str], dict]:
        env = _env()
        with mock.patch.object(qm_tpl, "run_guarded", return_value=cells):
            got = qm_tpl.qm_tpl(env, CaseSpec("qm02-create-delete", "s"), qm_tpl.tpl_step(vt, key))
        return got, env.bag

    def test_op_records_fill_or_marks_na(self) -> None:
        (status, detail), bag = self._op("ROW:chdefine15|T|nvarchar\nROW:CINVCODE|T|nvarchar\n")
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(bag["qmp_t4"], "chdefine15=E2E")
        (status, _detail), bag = self._op("")
        self.assertEqual((status, bag["qmp_t4"]), ("PASS", ""))
        (status, detail), bag = self._op("ROW:chdefine15|T|nvarchar\n", vt=352, key="qmp_t2")
        self.assertEqual(status, "N/A")
        self.assertIn("VT 352", detail)
        self.assertIn("chdefine15", detail)
        self.assertNotIn("qmp_t2", bag)
        self.assertTrue(ops_run.has_op(qm_tpl.OP))
        with self.assertRaises(ValueError):
            qm_tpl.tpl_step(350, "k")

    def test_builders_merge_the_recorded_defines(self) -> None:
        bag = {
            "qmp_q2l": "8", "qmp_psn": "P01", "qmp_proj": "J01", "qmp_d11": "A01", "qmp_d12": "-", "qmp_d13": "-",
            "qmp_t4": "chdefine11=E2E;chdefine15=E2E", "qmp_mod": "9", "qmp_t2": "cdefine3=E2E",
            "qmi_arrl": "5", "qmi_t1": "",
        }
        env = _env(bag)
        check = qmp_qm04(env)
        self.assertEqual(check["head"]["chDefine11"], "A01")
        self.assertNotIn("chdefine11", check["head"])
        self.assertEqual(check["head"]["chdefine15"], "E2E")
        self.assertEqual(qmp_qm02(env)["head"], {"cdefine3": "E2E"})
        self.assertNotIn("head", qmi_qm01(env))

    def test_quality_cases_read_the_template_before_writing(self) -> None:
        cases = _cases()
        want = {
            "qm01-create-delete": [("351", "qmi_t1"), ("353", "qmi_t3")],
            "qm02-create-delete": [("352", "qmp_t2"), ("354", "qmp_t4")],
            "dry-qm-product": [("352", "qmp_t2"), ("354", "qmp_t4")],
            "dry-qm-incoming": [("351", "qmi_t1")],
        }
        for name, tpl in want.items():
            spec = cases[name]
            lead = spec.steps[: len(tpl)]
            with self.subTest(case=name):
                self.assertEqual([(step.kind, step.sql_name) for step in lead], [("custom", qm_tpl.OP)] * len(tpl))
                self.assertEqual([(dict(step.fields)["vt"], dict(step.fields)["key"]) for step in lead], tpl)
                self.assertTrue(all(isinstance(step, Step) for step in spec.steps))


if __name__ == "__main__":
    unittest.main()
