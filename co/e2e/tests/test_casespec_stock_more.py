"""库存待实测补用例（stock_more）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import stock_more as m
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_more_sql import (
    body_code_sql,
    head_number_sql,
    made_number_sql,
    reopen_sql,
    shelf_stock_sql,
    stock_more_samples,
)
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("sale-out-batch-dates", "purchase-in-shelf", "stock-check-by-bin", "shape-change-number", "transfer-closer")
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_LOT = "E2ESLABCD1234"


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
    """按 SQL 里的特征串依次回答（每个键一个队列，按登记顺序找第一个还有回答的键），没有匹配就报错。"""

    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]
        self.seen: list[str] = []

    def __call__(self, _env: object, query: str, _label: str) -> str:
        self.seen.append(query)
        for key, rows in self.answers:
            if key in query and rows:
                return rows.pop(0)
        raise AssertionError("没有准备回答：" + query[:120])


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-02", bag={})


def _ok(**extra: object) -> dict:
    body: dict = {"ok": True}
    body.update(extra)
    return body


def _rows(*values: str) -> list[str]:
    return [f"ROW:{value}\n" for value in values]


def _run(op, client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql), mock.patch.object(m, "td", lambda key: "RD-" + key.rsplit(".", 1)[-1]), \
            mock.patch.object(m.secrets, "token_hex", lambda _n: "abcd1234"):
        return op(_env(client), _SPEC, _STEP)


def _order(client: _Replay) -> list[tuple[str, str, str]]:
    return [(route.rsplit("/", 1)[1], fields["type"], fields.get("action", "")) for route, fields in client.calls]


class RegistryTests(unittest.TestCase):
    def test_registered_with_ops_and_stock_tag(self) -> None:
        names = known_names()
        cases = m.stock_more_cases()
        self.assertEqual(tuple(spec.name for spec in cases), _NAMES)
        split = next(case for case in sp.stockplus_cases() if case.name == "sale-out-bin-split")
        for spec in cases:
            with self.subTest(case=spec.name):
                self.assertEqual(names.count(spec.name), 1)
                self.assertTrue(spec.requires_login and spec.routes)
                self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
                self.assertIn("stock", auto_tags(spec))
                self.assertTrue(conflicts(auto_tags(spec), auto_tags(split)))
        self.assertEqual(cases[4].data, ("stock.transfer_out_rd", "stock.transfer_in_rd"))
        self.assertIn("opt:havepo", cases[1].tags)

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in stock_more_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        self.assertIn("v.CardNumber = N'0305'", head_number_sql("shape_change", "7"))
        self.assertIn("v.CardNumber = N'0302'", made_number_sql("09", "7"))
        self.assertIn("dbo.RdRecord09 AS r", made_number_sql("09", "7"))
        self.assertIn("b.cTVCode", body_code_sql("transfer_request", "7"))
        # 流水号连着日期种子（AB20261002 + 1397）：按种子出现在单号里、单号以 cNumber 结尾核对，不把整段数字当流水。
        number = head_number_sql("stock_check", "7")
        self.assertIn("CHARINDEX(", number)
        self.assertIn("RIGHT(x.code, LEN(", number)
        self.assertNotIn("REVERSE(", number)
        self.assertIn("WHERE ID = 7", reopen_sql("7"))
        bad = (
            lambda: head_number_sql("transfer", "1"), lambda: made_number_sql("10", "1"), lambda: body_code_sql("x", "1"),
            lambda: reopen_sql("0"), lambda: shelf_stock_sql("01", "I1", "A'B"),
        )
        for call in bad:
            with self.assertRaises(ValueError):
                call()

    def test_valid_until_by_unit(self) -> None:
        self.assertEqual(m._valid_until("2026-10-02", 30, "3"), "2026-11-01")
        self.assertEqual(m._valid_until("2026-01-31", 1, "2"), "2026-02-28")
        self.assertEqual(m._valid_until("2024-02-29", 1, "1"), "2025-02-28")


# ---- sale-out-batch-dates ----


def _sale_client() -> _Replay:
    return _Replay({
        "/v1/vouchers/create": [_ok(id=1)],
        "/v1/vouchers/verify": [_ok(), _ok(), _ok(), _ok()],
        "/v1/vouchers/generate": [_ok(id=2), _ok(id=3)],
        "/v1/vouchers/delete": [_ok(deleted=True), _ok(deleted=True), _ok(deleted=True)],
    })


def _sale_sql(dates: str) -> _Sql:
    return _Sql([
        ("AS dated", ["ROW:C1|ST|D1|P1|I1|W1|L1|L2|1\n"]),
        ("SO_SODetails", _rows("11")),
        ("x.DLID", _rows("21")),
        ("p.RdsID = b.AutoID", _rows("L2||1.0000|0")),
        ("iExpiratDateCalcu", _rows(dates)),
        ("DispatchLists AS x WHERE x.iDLsID", _rows("1.0000", "0.0000")),
        ("p.RdID = 3", _rows("0")),
    ])


class BatchDatesTests(unittest.TestCase):
    def test_dates_cleared_and_property_matches(self) -> None:
        client = _sale_client()
        sql = _sale_sql("L2" + "|" * 8 + "2026-01-05T00:00:00|2026-01-05T00:00:00")
        status, detail = _run(m.sale_out_batch_dates, client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("2026-01-05T00:00:00", detail)
        self.assertEqual(client.calls[2][1]["lines"][0]["cBatch"], "L1")
        self.assertEqual(client.calls[4][1]["lines"], [{"source_line_id": 21, "quantity": 1, "cbatch": "L2"}])
        self.assertEqual(_order(client)[-4:], [
            ("verify", "dispatch", "unverify"), ("delete", "dispatch", ""),
            ("verify", "sale_order", "unverify"), ("delete", "sale_order", ""),
        ])

    def test_no_dated_archive_is_noted(self) -> None:
        status, detail = _run(m.sale_out_batch_dates, _sale_client(), _sale_sql("L2" + "|" * 9))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("未覆盖", detail)

    def test_dates_left_fail_after_cleanup(self) -> None:
        client = _sale_client()
        status, detail = _run(m.sale_out_batch_dates, client, _sale_sql("L2|2026-01-01|2027-01-01" + "|" * 7))
        self.assertEqual(status, "FAIL")
        self.assertIn("没被清空", detail)
        self.assertEqual(client.answers["/v1/vouchers/delete"], [])

    def test_property_mismatch_fails(self) -> None:
        status, detail = _run(m.sale_out_batch_dates, _sale_client(), _sale_sql("L2" + "|" * 8 + "2026-01-05T00:00:00|"))
        self.assertEqual(status, "FAIL")
        self.assertIn("属性 10", detail)


# ---- purchase-in-shelf ----


def _shelf_client() -> _Replay:
    return _Replay({"/v1/vouchers/create": [_ok(id=5)], "/v1/vouchers/delete": [_ok(deleted=True)]})


def _shelf_sql(row: str, left: str = "1|0.0000||", havepo: list[str] | None = None) -> _Sql:
    return _Sql([
        ("ISNULL(i.iMassDate, 0) > 0", ["ROW:W1|I1|V1|D1|PT1|30|3\n"]),
        ("bPTHavePO", havepo or _rows("0")),
        ("FROM dbo.rdrecords01 AS b", _rows(row)),
        ("c.cBatch = N'" + _LOT, _rows("1|0.0000|2026-11-01|2026-10-31", left)),
        ("RdRecord01 AS h WHERE h.ID = 5", _rows("0")),
    ])


_SHELF_ROW = _LOT + "|2026-10-02|2026-11-01|30|3|2026-10-31|2026-10-31|1"


class ShelfTests(unittest.TestCase):
    def test_u8_fill_is_printed(self) -> None:
        client = _shelf_client()
        status, detail = _run(m.purchase_in_shelf, client, _shelf_sql(_SHELF_ROW))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("有效期至 2026-10-31", detail)
        line = client.calls[0][1]["lines"][0]
        self.assertEqual((line["cbatch"], line["dmadedate"], line["dvdate"]), (_LOT, "2026-10-02", "2026-11-01"))
        self.assertNotIn("dexpirationdate", line)
        self.assertEqual(_order(client), [("create", "purchase_in", ""), ("delete", "purchase_in", "")])

    def test_empty_expiry_is_consistent(self) -> None:
        row = _LOT + "|2026-10-02|2026-11-01|30|3|||"
        status, detail = _run(m.purchase_in_shelf, _shelf_client(), _shelf_sql(row))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("有效期至 空", detail)

    def test_inconsistent_text_expiry_fails(self) -> None:
        row = _LOT + "|2026-10-02|2026-11-01|30|3|2026-11-30|2026-10-31|1"
        client = _shelf_client()
        status, detail = _run(m.purchase_in_shelf, client, _shelf_sql(row))
        self.assertEqual(status, "FAIL")
        self.assertIn("cExpirationdate", detail)
        self.assertEqual(client.answers["/v1/vouchers/delete"], [])

    def test_mass_not_from_archive_fails(self) -> None:
        row = _LOT + "|2026-10-02|2026-11-01|0|3|||"
        status, detail = _run(m.purchase_in_shelf, _shelf_client(), _shelf_sql(row))
        self.assertEqual(status, "FAIL")
        self.assertIn("存货档案", detail)

    def test_left_stock_fails(self) -> None:
        status, detail = _run(m.purchase_in_shelf, _shelf_client(), _shelf_sql(_SHELF_ROW, "1|1.0000||"))
        self.assertEqual(status, "FAIL")
        self.assertIn("现存量", detail)

    def test_no_pick_is_na(self) -> None:
        client = _shelf_client()
        status, _detail = _run(m.purchase_in_shelf, client, _Sql([("ISNULL(i.iMassDate, 0) > 0", ["\n"])]))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])

    def test_havepo_flipped_and_restored(self) -> None:
        sql = _shelf_sql(_SHELF_ROW, havepo=_rows("1", "True", "1", "1"))
        status, detail = _run(m.purchase_in_shelf, _shelf_client(), sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("已写回", detail)


# ---- stock-check-by-bin ----


def _check_client() -> _Replay:
    return _Replay({"/v1/vouchers/create": [_ok(id=9)], "/v1/vouchers/delete": [_ok(deleted=True)]})


def _check_sql(rows: str = "I1|5.0000|", number: str = "PD0009|1|1|/9", body: str = "1|1") -> _Sql:
    return _Sql([
        ("CheckVouch AS k", ["ROW:W1|I1|2|5.0000\n"]),
        ("FOR XML PATH", _rows("5.0000|5.0000|A01:3.0000,A02:2.0000")),
        ("CheckVouchs AS t WHERE", _rows(rows)),
        ("VoucherHistory", _rows(number)),
        ("SELECT COUNT(*) FROM dbo.CheckVouchs AS b", _rows(body)),
        ("CheckVouch AS h WHERE h.ID = 9", _rows("0")),
    ])


class BinCheckTests(unittest.TestCase):
    def test_book_equals_bins(self) -> None:
        client = _check_client()
        status, detail = _run(m.stock_check_by_bin, client, _check_sql())
        self.assertEqual(status, "PASS", detail)
        self.assertIn("A01:3.0000,A02:2.0000", detail)
        self.assertEqual(client.calls[0][1]["lines"], [{"cInvCode": "I1", "iCVCQuantity": 1}])
        self.assertEqual(_order(client), [("create", "stock_check", ""), ("delete", "stock_check", "")])

    def test_book_differs_fails_after_delete(self) -> None:
        client = _check_client()
        status, detail = _run(m.stock_check_by_bin, client, _check_sql(rows="I1|3.0000|"))
        self.assertEqual(status, "FAIL")
        self.assertIn("账面数量 3.0000", detail)
        self.assertEqual(client.answers["/v1/vouchers/delete"], [])

    def test_body_code_missing_fails(self) -> None:
        status, detail = _run(m.stock_check_by_bin, _check_client(), _check_sql(body="1|0"))
        self.assertEqual(status, "FAIL")
        self.assertIn("表体单号", detail)

    def test_number_not_from_card_fails(self) -> None:
        status, detail = _run(m.stock_check_by_bin, _check_client(), _check_sql(number="PD0009|0|0|"))
        self.assertEqual(status, "FAIL")
        self.assertIn("0307", detail)


# ---- shape-change-number ----


def _shape_client() -> _Replay:
    return _Replay({
        "/v1/vouchers/create": [_ok(id=4)],
        "/v1/vouchers/verify": [_ok(), _ok()],
        "/v1/vouchers/delete": [_ok(deleted=True)],
    })


def _shape_sql(numbers: tuple[str, ...]) -> _Sql:
    return _Sql([
        ("Department AS p", ["ROW:W1|I1|I2|D1\n"]),
        ("VoucherHistory", _rows(*numbers)),
        ("SELECT COUNT(*) FROM dbo.AssemVouchs AS b", _rows("2|2")),
        ("SELECT COUNT(*) FROM dbo.RdRecord08", _rows("2", "0")),
        ("AssemVouch AS t WHERE t.ID = 4", _rows("0")),
    ])


class ShapeNumberTests(unittest.TestCase):
    def test_numbers_match_cards(self) -> None:
        client = _shape_client()
        status, detail = _run(m.shape_change_number, client, _shape_sql(("EF01|1|1|/1", "AB202610021397|1|1|/1397", "CD01|2|1|/1")))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("转换入库 AB202610021397（流水 /1397）（0301）", detail)
        self.assertEqual(_order(client), [
            ("create", "shape_change", ""), ("verify", "shape_change", "verify"),
            ("verify", "shape_change", "unverify"), ("delete", "shape_change", ""),
        ])

    def test_generated_number_mismatch_fails_after_cleanup(self) -> None:
        client = _shape_client()
        status, detail = _run(m.shape_change_number, client, _shape_sql(("EF01|1|1|/1", "AB01|1|1|/1", "CD01|2|0|")))
        self.assertEqual(status, "FAIL")
        self.assertIn("0302", detail)
        self.assertEqual(client.answers["/v1/vouchers/delete"], [])


# ---- transfer-closer ----


def _tr_client() -> _Replay:
    return _Replay({
        "/v1/vouchers/create": [_ok(id=7)],
        "/v1/vouchers/verify": [_ok(), _ok()],
        "/v1/vouchers/generate": [_ok(id=8)],
        "/v1/vouchers/delete": [_ok(deleted=True), _ok(deleted=True)],
    })


def _tr_sql(states: tuple[str, str, str], link: str = "SQ01|1|1", reopen: str = "1") -> _Sql:
    return _Sql([
        ("bOverTransRequestTransfer", ["ROW:W1|W2|I1\n"]),
        ("VoucherHistory", _rows("SQ01|1|1|/1")),
        ("SELECT COUNT(*) FROM dbo.ST_AppTransVouchs AS b", _rows("1|1")),
        ("iTvSumQuantity", _rows(*states)),
        ("cTranRequestCode", _rows(link)),
        ("UPDATE dbo.ST_AppTransVouchs", _rows(reopen)),
    ])


_OPEN = ("SQ01|31|2.0000|0.0000||", "SQ01|31|2.0000|2.0000||", "SQ01|31|2.0000|0.0000||")


class TransferCloserTests(unittest.TestCase):
    def test_open_after_full_generation_is_reported(self) -> None:
        client = _tr_client()
        sql = _tr_sql(_OPEN)
        status, detail = _run(m.transfer_closer, client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("行关闭人 空", detail)
        self.assertFalse(any("UPDATE" in query for query in sql.seen))
        head = client.calls[0][1]["head"]
        self.assertEqual((head["cORdCode"], head["cIRdCode"]), ("RD-transfer_out_rd", "RD-transfer_in_rd"))
        self.assertEqual(client.calls[2][1]["lines"], [{"source_line_id": 31, "quantity": 2}])
        self.assertEqual(_order(client), [
            ("create", "transfer_request", ""), ("verify", "transfer_request", "verify"),
            ("generate", "transfer", ""), ("delete", "transfer", ""),
            ("verify", "transfer_request", "unverify"), ("delete", "transfer_request", ""),
        ])

    def test_auto_closed_is_reopened_before_cleanup(self) -> None:
        states = ("SQ01|31|2.0000|0.0000||", "SQ01|31|2.0000|2.0000|张三|", "SQ01|31|2.0000|0.0000|张三|")
        sql = _tr_sql(states)
        status, detail = _run(m.transfer_closer, _tr_client(), sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("行关闭人 张三", detail)
        self.assertIn("用例清空", detail)
        self.assertTrue(any("UPDATE" in query for query in sql.seen))

    def test_reopen_failure_fails(self) -> None:
        states = ("SQ01|31|2.0000|0.0000||", "SQ01|31|2.0000|2.0000|张三|", "SQ01|31|2.0000|0.0000|张三|")
        status, detail = _run(m.transfer_closer, _tr_client(), _tr_sql(states, reopen="0"))
        self.assertEqual(status, "FAIL")
        self.assertIn("清关闭人失败", detail)

    def test_request_code_missing_fails_after_cleanup(self) -> None:
        client = _tr_client()
        status, detail = _run(m.transfer_closer, client, _tr_sql(_OPEN, link="|1|1"))
        self.assertEqual(status, "FAIL")
        self.assertIn("cTranRequestCode", detail)
        self.assertEqual(client.answers["/v1/vouchers/delete"], [])


if __name__ == "__main__":
    unittest.main()
