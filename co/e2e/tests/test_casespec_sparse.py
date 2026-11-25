"""小账套：account.sparse_samples 把「必需挑单没挑到」记 N/A；检验单扩展自定义项挑单在缺列的账套上不报错。
不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e import settings
from co.e2e import settings_parse as sp
from co.e2e.casespec import picks, runner
from co.e2e.casespec.model import CaseSpec
from co.e2e.casespec.qm_write_sql import NO_DEFINE, QMI_PACKS, qmi_def_sql, qmp_def_sql
from co.e2e.settings import ConfigError, from_dict
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_SPEC = CaseSpec("sparse-probe", "挑单探针", needs=("so_ship", "so_ship_line"))


def _sparse_lane(on: bool) -> settings.E2EConfig:
    data = support.lane_dict("802")
    data["account"]["sparse_samples"] = on
    return from_dict(data)


def _env(bag: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(bag=dict(bag or {}), ctx=SimpleNamespace(fail=mock.Mock()))


class SparseConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        support.reset()

    def test_default_false(self) -> None:
        self.assertFalse(from_dict(support.sample_dict()).sparse_samples)
        self.assertFalse(support.lane_config().sparse_samples)

    def test_lane_may_enable(self) -> None:
        self.assertTrue(_sparse_lane(True).sparse_samples)

    def test_allowed_without_lane(self) -> None:
        data = support.sample_dict()
        data["account"]["sparse_samples"] = True
        self.assertTrue(from_dict(data).sparse_samples)

    def test_must_be_boolean(self) -> None:
        data = support.lane_dict("802")
        data["account"]["sparse_samples"] = "yes"
        with self.assertRaises(ConfigError):
            from_dict(data)


class SparseJudgeTests(unittest.TestCase):
    def tearDown(self) -> None:
        support.reset()

    def _judge(self, on: bool, bag: dict, spec: CaseSpec = _SPEC) -> tuple[str, str]:
        support.install(_sparse_lane(on))
        return runner.judge_one(_env(bag), spec)

    def test_reference_lane_still_skips(self) -> None:
        self.assertEqual(self._judge(False, {}), ("SKIP", "没有符合条件的单据"))

    def test_sparse_lane_reports_na(self) -> None:
        self.assertEqual(self._judge(True, {"so_ship": "5"}), ("N/A", runner.SPARSE_NA))
        self.assertIn("sparse_samples", runner.SPARSE_NA)

    def test_pick_sql_error_stays_skip(self) -> None:
        bag = {picks.PICK_FAILED: "so_ship,so_ship_line"}
        self.assertEqual(self._judge(True, bag), ("SKIP", "没有符合条件的单据"))
        # 别的挑单出错不影响本用例。
        self.assertEqual(self._judge(True, {picks.PICK_FAILED: "po_recv"}), ("N/A", runner.SPARSE_NA))

    def test_optional_case_keeps_its_text(self) -> None:
        spec = CaseSpec("sparse-opt", "可选", needs=("so_ship",), optional=True)
        self.assertEqual(self._judge(True, {}, spec), ("N/A", "账套没有这类单据（可选用例）"))

    def test_login_and_testdata_gaps_unchanged(self) -> None:
        spec = CaseSpec("sparse-login", "登录", needs=("so_ship",), requires_login=True)
        self.assertEqual(self._judge(True, {}, spec), ("SKIP", "登录未成功"))
        spec = CaseSpec("sparse-data", "数据", needs=("so_ship",), data=("no_such.key",))
        status, _detail = self._judge(True, {}, spec)
        self.assertEqual(status, "SKIP")

    def test_present_samples_run_the_steps(self) -> None:
        support.install(_sparse_lane(True))
        bag = {"so_ship": "5", "so_ship_line": "6"}
        with mock.patch.object(runner, "walk", return_value=("PASS", "ok")) as walk:
            self.assertEqual(runner.judge_one(_env(bag), _SPEC), ("PASS", "ok"))
        walk.assert_called_once()


class PickFailedTests(unittest.TestCase):
    def test_error_marks_every_key_of_the_query(self) -> None:
        env = _env()
        bag: dict[str, str] = {}
        with mock.patch.object(picks, "run_guarded", side_effect=RuntimeError("pick SQL 失败")):
            self.assertEqual(picks._cells(env, "SELECT 1", bag, ("a", "b")), [])
            self.assertEqual(picks._cells(env, "SELECT 1", bag, ("b", "c")), [])
        self.assertEqual(bag[picks.PICK_FAILED], "a,b,c")
        self.assertEqual(picks.pick_failed(bag), {"a", "b", "c"})
        self.assertEqual(env.ctx.fail.call_count, 2)

    def test_empty_result_is_not_an_error(self) -> None:
        bag: dict[str, str] = {}
        with mock.patch.object(picks, "run_guarded", return_value=""):
            self.assertEqual(picks._cells(_env(), "SELECT 1", bag, ("a",)), [])
        self.assertEqual(picks.pick_failed(bag), set())

    def test_fill_packs_records_failed_pack(self) -> None:
        env = _env()
        bag: dict[str, str] = {}
        with mock.patch.object(picks, "run_guarded", side_effect=RuntimeError("boom")):
            picks._fill_packs(env, bag, {"so_ship"})
        self.assertIn("so_ship_wh", picks.pick_failed(bag))
        self.assertNotIn("so_ship", bag)


class DefineColumnsTests(unittest.TestCase):
    def test_columns_never_named_directly_on_extradefine(self) -> None:
        for sql in (qmi_def_sql(), qmp_def_sql()):
            self.assertEqual(sql_block_reason(sql), "")
            # 列名只出现在 COL_LENGTH 的字符串和 XML 路径里；表上只取 e.*，缺列时不会「列名无效」。
            self.assertNotIn("e.chdefine", sql)
            self.assertIn("SELECT TOP (1) e.*", sql)
            self.assertIn("FOR XML PATH(N'r'), TYPE", sql)
            for col in ("chdefine11", "chdefine12", "chdefine13"):
                self.assertIn(f"COL_LENGTH(N'dbo.QMCHECKVOUCHER_extradefine', N'{col}') IS NULL THEN N'{NO_DEFINE}'", sql)
                self.assertIn(f'lower-case(local-name()) = "{col}"', sql)
            self.assertIn(f"N'ROW:{NO_DEFINE}|{NO_DEFINE}|{NO_DEFINE}'", sql)
            self.assertIn("THEN N'E2E'", sql)
        self.assertIn("N'QM03'", qmi_def_sql())
        self.assertIn("N'QM04'", qmp_def_sql())

    def test_missing_column_value_is_accepted_by_the_pack(self) -> None:
        # 缺列记 -，挑单仍收（非 id 键按编码字符集校验），用例不带这一项。
        flags = {lead: flag for lead, _keys, flag, _sql in QMI_PACKS}
        self.assertTrue(all(picks._ok_part(part, f) for part, f in zip(("A01", "E2E", NO_DEFINE), flags["qmi_d11"])))


if __name__ == "__main__":
    unittest.main()
