"""wf-qm04-cycle 第一次同意后的分支（wf_qm04_rest）。不连接服务器。"""

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import qm_workflow as qw
from co.e2e.casespec.model import CaseSpec, Step

_SPEC = CaseSpec("wf-qm04-cycle", "产品检验单审批闭环", ("qm04_a",))


def _env() -> SimpleNamespace:
    return SimpleNamespace(ctx=SimpleNamespace(stop=threading.Event()))


class _Fake:
    """假步骤：pending 返回给定待办数，call 记录路由并按 fail 指定的路由失败。"""

    def __init__(self, pending: str, fail: str = "") -> None:
        self.pending = pending
        self.fail = fail
        self.routes: list[str] = []

    def sql(self, _env: object, _spec: object, step: Step) -> tuple[str, str]:
        if self.pending == "err":
            return "FAIL", "SQL 出错"
        if step.code == "$gt0":
            return ("PASS", self.pending) if self.pending != "0" else ("FAIL", "实际 0")
        return ("PASS", self.pending) if self.pending == step.code else ("FAIL", "实际 " + self.pending)

    def call(self, _env: object, _spec: object, step: Step) -> tuple[str, str]:
        self.routes.append(step.route)
        if step.route == self.fail:
            return "FAIL", "状态不符"
        return "PASS", ""


def _run(fake: _Fake) -> tuple[str, str]:
    with mock.patch.dict(qw._RUN, {"call": fake.call, "sql": fake.sql}), \
            mock.patch.multiple(qw, step_call=fake.call, step_sql=fake.sql):
        return qw.wf_qm04_rest(_env(), _SPEC, Step("custom", sql_name=qw.REST_OP))  # type: ignore[arg-type]


class RestTests(unittest.TestCase):
    def test_pending_runs_full_path(self) -> None:
        fake = _Fake("1")
        status, _detail = _run(fake)
        self.assertEqual(status, "PASS")
        want = [step.route for step in qw.rest_steps() if step.kind == "call"]
        self.assertEqual(fake.routes, want)
        self.assertEqual(fake.routes[-1], "/v1/workflow/history")

    def test_no_pending_checks_approved_and_skips(self) -> None:
        fake = _Fake("0")
        status, detail = _run(fake)
        self.assertEqual(status, "PASS")
        self.assertEqual(fake.routes, ["/v1/workflow/state"])
        self.assertIn("跳过", detail)

    def test_no_pending_but_not_approved_fails(self) -> None:
        fake = _Fake("0", fail="/v1/workflow/state")
        status, detail = _run(fake)
        self.assertEqual(status, "FAIL")
        self.assertIn("应已通过", detail)

    def test_pending_sql_error_fails(self) -> None:
        fake = _Fake("err")
        status, detail = _run(fake)
        self.assertEqual(status, "FAIL")
        self.assertIn("待办数", detail)
        self.assertEqual(fake.routes, [])

    def test_full_path_failure_names_step(self) -> None:
        fake = _Fake("2", fail="/v1/workflow/disagree")
        status, detail = _run(fake)
        self.assertEqual(status, "FAIL")
        self.assertTrue(detail.startswith("后续步骤"), detail)

    def test_registered_and_cycle_ends_with_rest(self) -> None:
        self.assertIs(qw.QM_WF_OPS[qw.REST_OP], qw.wf_qm04_rest)
        spec = qw.wf_cases()[0]
        self.assertEqual(spec.steps[-1].sql_name, qw.REST_OP)
        self.assertIn("/v1/workflow/disagree", spec.routes)


if __name__ == "__main__":
    unittest.main()
