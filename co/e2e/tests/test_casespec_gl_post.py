"""总账记账用例（gl-post 等）的登记、标签和回读 SQL。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import ops_run, gl_post
from co.e2e.casespec.gl_voucher_bodies import GL_BODIES, judge
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.gl_post import GLPOST_OPS, glpost_cases
from co.e2e.casespec.gl_post_sql import first_open_sql, glpost_samples, others_sql, posted_sql, prior_open_sql
from co.e2e.casespec.gl_voucher_sql import gl_key
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("gl-post", "gl-post-closed-period", "gl-post-bad-body")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class GlPostCatalogTests(unittest.TestCase):
    def test_cases_registered_with_tags(self) -> None:
        names = known_names()
        cases = {spec.name: spec for spec in glpost_cases()}
        self.assertEqual(tuple(cases), _NAMES)
        for name in _NAMES:
            self.assertIn(name, names)
        self.assertIn("gl", cases["gl-post"].tags)
        self.assertIn("new:gl:转", cases["gl-post"].tags)
        self.assertIn("gl", cases["gl-post-closed-period"].tags)
        self.assertEqual(cases["gl-post-bad-body"].tags, frozenset(("probe",)))
        self.assertFalse(cases["gl-post-bad-body"].requires_login)
        for name in ("gl-post", "gl-post-closed-period"):
            self.assertTrue(cases[name].requires_login)
            self.assertTrue(ops_run.has_op(cases[name].steps[0].sql_name))
        self.assertEqual(set(GLPOST_OPS), {"gl_post", "gl_post_closed"})

    def test_bridge_and_api_files_pick_the_gl_family(self) -> None:
        catalog = list(known_names())
        for path in ("co/bridge/src/GlPostTx.cs", "api/u8co_api/co_models_gl_post.py"):
            picked = select_changed([path], catalog)
            self.assertIn("gl-post", picked, path)


class GlPostSqlTests(unittest.TestCase):
    def test_reads_pass_the_guard(self) -> None:
        for name, query in glpost_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn("N'ROW:'", query)

    def test_values_are_whitelisted(self) -> None:
        key = gl_key(9, "转", 3)
        self.assertIn("iyear = 2026 AND iperiod = 9 AND csign = N'转' AND ino_id = 3", posted_sql("2026", key))
        self.assertIn("NOT (iyear = 2026", others_sql("2026", key))
        self.assertIn("iyear = 2025 AND iperiod BETWEEN 1 AND 12", prior_open_sql("2026"))
        with self.assertRaises(ValueError):
            prior_open_sql("2026 OR 1=1")
        with self.assertRaises(ValueError):
            first_open_sql("2026; --")
        with self.assertRaises(ValueError):
            gl_key(9, "转'", 3)


class GlSignTests(unittest.TestCase):
    """凭证类别字来自测试数据 gl.sign_transfer / gl.sign_bank，缺省 转 / 付。"""

    def tearDown(self) -> None:
        support.install()

    def _signs(self) -> tuple[str, str, str]:
        env = SimpleNamespace(today="2026-09-28")
        transfer = GL_BODIES["create_transfer"](env, "")["head"]["sign"]
        bank = GL_BODIES["create_bank"](env, "")["head"]["sign"]
        with mock.patch.object(gl_post, "_first_open", return_value=9), \
                mock.patch.object(gl_post, "_send", return_value=(409, {"code": "state_mismatch"})) as send:
            got = gl_post.gl_post_closed(env, CaseSpec("c", "c"), Step("custom"))
        self.assertEqual(got[0], "PASS")
        self.assertEqual(send.call_args[0][2]["period"], 8)
        return transfer, bank, send.call_args[0][2]["vouchers"][0]["sign"]

    def test_default_signs(self) -> None:
        self.assertEqual(self._signs(), ("转", "付", "转"))

    def test_single_sign_account(self) -> None:
        data = support.sample_testdata()
        data["gl"].update({"sign_transfer": "记", "sign_bank": "记"})
        support.install(data=data)
        self.assertEqual(self._signs(), ("记", "记", "记"))
        env = SimpleNamespace(focus="", today="2026-09-28", operator_name="甲", bag={})
        made = (("sign", "$td:gl.sign_transfer"),)
        self.assertEqual(judge(env, {"sign": "记"}, made, "k")[0], "PASS")
        self.assertEqual(judge(env, {"sign": "转"}, made, "k")[0], "FAIL")

    def test_closed_period_case_checks_the_sign_key(self) -> None:
        closed = {spec.name: spec for spec in glpost_cases()}["gl-post-closed-period"]
        self.assertIn("gl.sign_transfer", closed.data)


if __name__ == "__main__":
    unittest.main()
