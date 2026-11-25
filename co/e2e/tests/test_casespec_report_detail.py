"""明细账报表用例（arap_detail、gl_detail）的登记和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.report_detail import arap_detail, detail_cases, gl_detail
from co.e2e.tests import support

_SPEC = CaseSpec("d", "d")
_STEP = Step("custom")
_BAL = "/v1/reports/arap_balance"
_ARAP = "/v1/reports/arap_detail"
_GLB = "/v1/reports/gl_balance"
_GL = "/v1/reports/gl_detail"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    def __init__(self, answers: dict[str, list]) -> None:
        self.answers = {key: list(val) for key, val in answers.items()}
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers[route].pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-28", bag={})


def _arap_page(rows: list[dict], nxt: object, closing: float = 130.0) -> dict:
    head = {"partner": "C1", "opening": 100.0, "debit": 50.0, "credit": 20.0, "closing": closing}
    return {"ok": True, "side": "ar", "partners": [head], "items": rows, "next": nxt}


def _gl_page(rows: list[dict], nxt: object, **extra: object) -> dict:
    body = {
        "ok": True, "code": "1122", "open_debit": 100.0, "open_credit": 0.0, "total_debit": 50.0,
        "total_credit": 20.0, "close_debit": 130.0, "close_credit": 0.0, "items": rows, "next": nxt,
    }
    body.update(extra)
    return body


_ACCOUNT = {
    "code": "1122", "open_debit": 100.0, "open_credit": 0.0, "period_debit": 50.0, "period_credit": 20.0,
    "close_debit": 130.0, "close_credit": 0.0,
}


class DetailCaseTests(unittest.TestCase):
    def test_cases_registered_with_ops(self) -> None:
        names = known_names()
        for spec in detail_cases():
            self.assertIn(spec.name, names)
            self.assertTrue(spec.requires_login)
            self.assertTrue(all(ops_run.has_op(step.sql_name) for step in spec.steps))

    def test_arap_detail_pass_with_second_page(self) -> None:
        first = [{"partner": "C1", "debit": 50.0, "credit": 0.0, "balance": 150.0}]
        second = [{"partner": "C1", "debit": 0.0, "credit": 20.0, "balance": 130.0}]
        client = _Replay({
            _BAL: [{"ok": True, "items": [{"partner": "C1", "balance": 130.0}]}],
            _ARAP: [_arap_page(first, "tok"), _arap_page(second, None)],
        })
        self.assertEqual(arap_detail(_env(client), _SPEC, _STEP)[0], "PASS")
        detail = client.calls[1][1]
        self.assertEqual((detail["partner"], detail["date_from"], detail["limit"]), ("C1", "2026-01-01", 5))
        self.assertEqual(client.calls[2][1]["after"], "tok")

    def test_arap_detail_failures(self) -> None:
        pick = {"ok": True, "items": [{"partner": "C1", "balance": 130.0}]}
        first = [{"partner": "C1", "debit": 50.0, "credit": 0.0, "balance": 150.0}]
        broken = [{"partner": "C1", "debit": 0.0, "credit": 20.0, "balance": 999.0}]
        cases = (
            ([_arap_page([], None, closing=131.0)], "往来余额"),
            ([_arap_page([{"partner": "C1", "debit": 1.0, "credit": 0.0, "balance": 1.0}], None)], "最后一行"),
            ([_arap_page(first, "t"), _arap_page(broken, None)], "不连续"),
        )
        for pages, text in cases:
            client = _Replay({_BAL: [pick], _ARAP: pages})
            status, detail = arap_detail(_env(client), _SPEC, _STEP)
            self.assertEqual(status, "FAIL")
            self.assertIn(text, detail)
        empty = _Replay({_BAL: [{"ok": True, "items": []}]})
        self.assertEqual(arap_detail(_env(empty), _SPEC, _STEP)[0], "N/A")

    def test_gl_detail_matches_balance(self) -> None:
        first = [{"debit": 50.0, "credit": 0.0, "dir": "借", "balance": 150.0}]
        second = [{"debit": 0.0, "credit": 20.0, "dir": "借", "balance": 130.0}]
        pages = [_gl_page(first, "tok"), _gl_page(second, None)]
        client = _Replay({_GLB: [{"ok": True, "items": [_ACCOUNT]}], _GL: pages})
        self.assertEqual(gl_detail(_env(client), _SPEC, _STEP)[0], "PASS")
        self.assertEqual(client.calls[1][1]["period_to"], 9)
        for extra, text in (({"total_debit": 51.0}, "借方合计"), ({"close_debit": 0.0, "close_credit": 1.0}, "期末")):
            client = _Replay({_GLB: [{"ok": True, "items": [_ACCOUNT]}], _GL: [_gl_page([], None, **extra)]})
            status, detail = gl_detail(_env(client), _SPEC, _STEP)
            self.assertEqual(status, "FAIL")
            self.assertIn(text, detail)
        idle = dict(_ACCOUNT, period_debit=0.0, period_credit=0.0)
        self.assertEqual(gl_detail(_env(_Replay({_GLB: [{"ok": True, "items": [idle]}]})), _SPEC, _STEP)[0], "N/A")


if __name__ == "__main__":
    unittest.main()
