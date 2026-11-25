"""退货申请单写入循环（return_apply_cycle）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import return_apply_cycle as m
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.return_apply_cycle_sql import (
    apply_lines_sql,
    cum_sql,
    link_sql,
    ret_line_sql,
    return_apply_cycle_samples,
)
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "sale-return-apply-cycle"
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
# 键按顺序找第一个还有回答的：挑单、累计列、申请行、退货单指回申请单的行数、指回申请单的退货行。
_PICK = "TOP (1) N'ROW:'"
_CUM = "d.fretqtyykp"
_LINES = "CONVERT(varchar(20), a.AutoID)"
_LINK = "COUNT(*)"
_RET = "x.iDLsID) AS v"
_LOCKED = "参照退货申请单生成的退货行暂不支持修改数量或删除，请删除退货单后重新生成"
_BASE = "ROW:0.0000|0.0000|0.0000|0.0000|3.0000\n"


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


class _Sql:
    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]

    def __call__(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.answers:
            if key in query and rows:
                return rows.pop(0)
        raise AssertionError("没有准备回答：" + query[:120])


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-03", bag={})


def _run(client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql):
        return m.return_apply_cycle(_env(client), _SPEC, _STEP)


def _locked() -> U8CoError:
    return U8CoError(409, "state_mismatch", _LOCKED)


def _client(unverify: object, ret_edits: tuple = ()) -> _Replay:
    edits = list(ret_edits) or [_locked(), _locked()]
    return _Replay({
        "/v1/vouchers/create": [{"ok": True, "id": 21}],
        "/v1/vouchers/update": [{"ok": True}, *edits],
        "/v1/vouchers/verify": [{"ok": True}, unverify, {"ok": True}],
        "/v1/vouchers/generate": [{"ok": True, "id": 31}],
        "/v1/vouchers/delete": [{"ok": True, "deleted": True}, {"ok": True, "deleted": True}],
    })


def _sql(after: str = _BASE, fret: str = "-1.0000") -> _Sql:
    lines = ["ROW:11|-2.0000|0.0000|77\n", "ROW:11|-1.0000|0.0000|77\n", f"ROW:11|-1.0000|{fret}|77\n",
             "ROW:11|-1.0000|-1.0000|77\n", "ROW:11|-1.0000|0.0000|77\n"]
    answers = [(_PICK, ["ROW:9|77|5\n"]), (_CUM, [_BASE, after]), (_LINES, lines), (_LINK, ["ROW:1\n"])]
    return _Sql([*answers, (_RET, ["ROW:41\n"])])


def _order(client: _Replay) -> list[tuple[str, str, str]]:
    return [(route.rsplit("/", 1)[1], fields["type"], fields.get("action", "")) for route, fields in client.calls]


class RegistryTests(unittest.TestCase):
    def test_registered_with_op_and_tags(self) -> None:
        cases = m.return_apply_cycle_cases()
        self.assertEqual([spec.name for spec in cases], [_NAME])
        self.assertEqual(known_names().count(_NAME), 1)
        self.assertTrue(ops_run.has_op(cases[0].steps[0].sql_name))
        tags = auto_tags(cases[0])
        for tag in ("new:sale_return_apply", "w:sale_return_apply", "new:sale_return"):
            self.assertIn(tag, tags)

    def test_changed_bridge_files_select_the_case(self) -> None:
        for path in ("co/bridge/src/ReturnsApplyEdit.cs", "co/bridge/src/SaleGenApplyRet.cs"):
            self.assertIn(_NAME, select_changed([path], [_NAME, "sale-verify"]), path)

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in return_apply_cycle_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
        self.assertIn("WHERE x.DLID = 7 AND m.ID = 8", link_sql("7", "8"))
        self.assertIn("WHERE x.DLID = 7 AND a.ID = 8", ret_line_sql("7", "8"))
        for call in (lambda: cum_sql("0"), lambda: apply_lines_sql("x"), lambda: link_sql("7 OR 1=1", "8")):
            with self.assertRaises(ValueError):
                call()


class CycleTests(unittest.TestCase):
    def test_pass_and_cleanup_order(self) -> None:
        client = _client(U8CoError(409, "state_mismatch", "退货申请单已有退货单，请先删除退货单"))
        status, detail = _run(client, _sql())
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(_order(client), [
            ("create", "sale_return_apply", ""), ("update", "sale_return_apply", ""),
            ("verify", "sale_return_apply", "verify"), ("generate", "sale_return", ""),
            ("update", "sale_return", ""), ("update", "sale_return", ""),
            ("verify", "sale_return_apply", "unverify"), ("delete", "sale_return", ""),
            ("verify", "sale_return_apply", "unverify"), ("delete", "sale_return_apply", ""),
        ])
        generate = client.calls[3][1]
        self.assertEqual((generate["id"], generate["source_type"]), (21, "sale_return_apply"))
        self.assertEqual(generate["lines"], [{"source_line_id": 11, "quantity": 1}])
        edits = [fields for route, fields in client.calls if route.endswith("update") and fields["type"] == "sale_return"]
        self.assertEqual([fields["id"] for fields in edits], [31, 31])
        self.assertEqual(edits[0]["lines"], [{"op": "update", "line_id": 41, "iQuantity": 0.5}])
        self.assertEqual(edits[1]["lines"], [{"op": "delete", "line_id": 41}])

    def test_na_without_dispatch_line(self) -> None:
        status, _detail = _run(_client({"ok": True}), _Sql([(_PICK, ["\n"])]))
        self.assertEqual(status, "N/A")

    def test_unverify_with_return_must_409(self) -> None:
        status, detail = _run(_client({"ok": True}), _sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("409", detail)

    def test_app_linked_return_line_edit_must_409(self) -> None:
        # 挂着申请单行的退货行：改数量放行或删行 409 但不是申请单关联的原因，都记 FAIL，并照常清理。
        unverify = {"ok": True}
        other = U8CoError(409, "state_mismatch", "单据已审核")
        for edits, what in (([{"ok": True}], "改数量"), ([_locked(), other], "删行")):
            with self.subTest(what=what):
                client = _client(unverify, tuple(edits))
                status, detail = _run(client, _sql())
                self.assertEqual(status, "FAIL")
                self.assertIn(what, detail)
                self.assertEqual(_order(client)[-2:], [("verify", "sale_return_apply", "unverify"),
                                                       ("delete", "sale_return_apply", "")])

    def test_fretqty_not_written_back_fails(self) -> None:
        client = _client(U8CoError(409, "state_mismatch", "已有退货单"))
        status, detail = _run(client, _sql(fret="0.0000"))
        self.assertEqual(status, "FAIL")
        self.assertIn("fretqty", detail)

    def test_cumulative_drift_fails(self) -> None:
        client = _client(U8CoError(409, "state_mismatch", "已有退货单"))
        status, detail = _run(client, _sql(after="ROW:1.0000|1.0000|0.0000|1.0000|2.0000\n"))
        self.assertEqual(status, "FAIL")
        self.assertIn("累计列", detail)


if __name__ == "__main__":
    unittest.main()
