"""期初余额与附件列表用例（report_openings）的登记和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.report_openings import (
    gl_attachments,
    open_rpt_cases,
    opening_arap,
    opening_gl,
    opening_stock,
    voucher_attachments,
)
from co.e2e.casespec.tags import auto_tags
from co.e2e.tests import support

_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_OPEN = "/v1/reports/opening_balance"
_GL_ATT = "/v1/gl/vouchers/attachments/list"
_ATT = "/v1/vouchers/attachments/list"


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


def _open(module: str, rows: list[dict], **extra: object) -> dict:
    body = {"ok": True, "module": module, "start_date": "2024-12-01", "opening_year": 2024, "posted": True,
            "items": rows, "next": None}
    body.update(extra)
    return body


_AR_ROW = {"partner_code": "C1", "debit": 10.0, "credit": 4.0, "balance_dir": "借", "balance_debit": 6.0,
           "balance_credit": 0.0}
_TRIAL = {"debit": 5.0, "credit": 5.0, "difference": 0.0, "balanced": True}


class OpenCaseTests(unittest.TestCase):
    def test_cases_registered_with_ops_and_tags(self) -> None:
        names = known_names()
        for spec in open_rpt_cases():
            self.assertEqual(names.count(spec.name), 1, spec.name)
            self.assertTrue(spec.requires_login)
            self.assertTrue(spec.routes)
            self.assertTrue(auto_tags(spec))
            self.assertTrue(all(ops_run.has_op(step.sql_name) for step in spec.steps))

    def test_stock_opening(self) -> None:
        row = {"wh_code": "07", "inv_code": "I1", "lines": 1}
        client = _Replay({_OPEN: [_open("stock", [row])]})
        self.assertEqual(opening_stock(_env(client), _SPEC, _STEP)[0], "PASS")
        self.assertEqual(client.calls[0][1]["module"], "stock")
        bad = _Replay({_OPEN: [_open("stock", [row], posted="1")]})
        self.assertEqual(opening_stock(_env(bad), _SPEC, _STEP)[0], "FAIL")
        empty = _Replay({_OPEN: [_open("stock", [])]})
        self.assertEqual(opening_stock(_env(empty), _SPEC, _STEP)[0], "N/A")

    def test_arap_balance_direction(self) -> None:
        pages = [_open("arap", [_AR_ROW], side="ar"), _open("arap", [], side="ap")]
        self.assertEqual(opening_arap(_env(_Replay({_OPEN: pages})), _SPEC, _STEP)[0], "PASS")
        wrong = dict(_AR_ROW, balance_dir="贷")
        pages = [_open("arap", [wrong], side="ar")]
        status, detail = opening_arap(_env(_Replay({_OPEN: pages})), _SPEC, _STEP)
        self.assertEqual(status, "FAIL")
        self.assertIn("余额方向", detail)

    def test_gl_trial_must_balance(self) -> None:
        pages = [_open("gl", []), _open("gl", [], period=12, trial=_TRIAL), _open("gl", [], dim="customer")]
        client = _Replay({_OPEN: pages})
        self.assertEqual(opening_gl(_env(client), _SPEC, _STEP)[0], "PASS")
        self.assertEqual(client.calls[1][1]["fiscal_year"], 2024)
        off = dict(_TRIAL, credit=4.0, balanced=False)
        pages = [_open("gl", []), _open("gl", [], period=12, trial=off), _open("gl", [], dim="customer")]
        status, detail = opening_gl(_env(_Replay({_OPEN: pages})), _SPEC, _STEP)
        self.assertEqual(status, "FAIL")
        self.assertIn("试算", detail)

    def test_gl_attachments(self) -> None:
        key = {"period": 9, "sign": "转", "no": 3}
        payload = {"ok": True, "voucher": dict(key, attachments=2), "items": [], "truncated": False}
        client = _Replay({"/v1/gl/vouchers/list": [{"items": [key]}], _GL_ATT: [payload]})
        self.assertEqual(gl_attachments(_env(client), _SPEC, _STEP)[0], "PASS")
        self.assertEqual(client.calls[1][1]["sign"], "转")
        empty = _Replay({"/v1/gl/vouchers/list": [{"items": []}]})
        self.assertEqual(gl_attachments(_env(empty), _SPEC, _STEP)[0], "N/A")

    def test_voucher_attachments_and_missing(self) -> None:
        listed = {"items": [{"id": 5}]}
        payload = {"ok": True, "type": "sale_order", "id": 5, "items": [{"stored": "file_server"}]}
        missing = U8CoError(404, "not_found", "销售订单不存在")
        client = _Replay({"/v1/vouchers/list": [listed], _ATT: [payload, missing]})
        self.assertEqual(voucher_attachments(_env(client), _SPEC, _STEP)[0], "PASS")
        self.assertEqual(client.calls[-1][1]["id"], 2147483647)
        found = _Replay({"/v1/vouchers/list": [listed], _ATT: [payload, payload]})
        self.assertEqual(voucher_attachments(_env(found), _SPEC, _STEP)[0], "FAIL")


if __name__ == "__main__":
    unittest.main()
