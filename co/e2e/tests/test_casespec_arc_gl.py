"""总账档案写入用例（凭证类别、币种）的登记和挑选 SQL。不连接服务器。"""

from __future__ import annotations

import unittest
from unittest import mock

from co.e2e.casespec import ops_run, arc_gl
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arc_gl import GLARC_OPS, glarc_cases, glarc_sign
from co.e2e.casespec.arc_gl_sql import currency_free, sign_free, sign_preset
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("archive-voucher-sign-write", "archive-currency-write")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class GlArcCatalogTests(unittest.TestCase):
    def test_cases_registered_with_tags(self) -> None:
        names = known_names()
        cases = {spec.name: spec for spec in glarc_cases()}
        self.assertEqual(tuple(cases), _NAMES)
        for name, spec in cases.items():
            self.assertIn(name, names)
            self.assertTrue(spec.requires_login)
            self.assertTrue(any(tag.startswith("arc:") for tag in spec.tags))
            self.assertIn("/v1/archives/delete", spec.routes)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertEqual(set(GLARC_OPS), {spec.steps[0].sql_name for spec in cases.values()})


class GlArcSqlTests(unittest.TestCase):
    def test_picks_pass_the_guard(self) -> None:
        for query in (sign_free(), sign_preset("转"), sign_preset("记"), currency_free()):
            self.assertEqual(sql_block_reason(query), "", query[:60])
            self.assertIn("N'ROW:'", query.replace("N'ROW:1'", "N'ROW:'"))
        self.assertIn("csign = N'记'", sign_preset("记"))
        for bad in ("Z1", "转'", "现"):
            with self.assertRaises(ValueError):
                sign_preset(bad)


class GlArcSignGateTests(unittest.TestCase):
    """删除闸门试的是测试数据的转账类别字；不是预置类别时不试。"""

    def tearDown(self) -> None:
        support.install()

    def _gate(self, sign: str) -> tuple[list, list]:
        data = support.sample_testdata()
        data["gl"]["sign_transfer"] = sign
        support.install(data=data)
        picks: list = []

        def pick(_env: object, _spec: object, query: str) -> list[str]:
            picks.append(query)
            return ["Z1"] if "VALUES" in query else ["1"]

        with mock.patch.object(arc_gl, "_pick", side_effect=pick), \
                mock.patch.object(arc_gl, "_run", return_value=""), \
                mock.patch.object(arc_gl, "_refused", return_value="") as refused:
            self.assertEqual(glarc_sign(None, CaseSpec("c", "c"), Step("custom"))[0], "PASS")  # type: ignore[arg-type]
        return picks, refused.call_args_list

    def test_preset_sign_from_testdata(self) -> None:
        picks, refused = self._gate("记")
        self.assertIn("csign = N'记'", picks[-1])
        self.assertEqual(refused[0][0][1:], ("voucher_sign", "记"))

    def test_non_preset_sign_skips_the_gate(self) -> None:
        picks, refused = self._gate("现")
        self.assertEqual(len(picks), 1)
        self.assertEqual(refused, [])

    def test_sign_case_checks_the_key(self) -> None:
        cases = {spec.name: spec for spec in glarc_cases()}
        self.assertIn("gl.sign_transfer", cases["archive-voucher-sign-write"].data)


if __name__ == "__main__":
    unittest.main()
