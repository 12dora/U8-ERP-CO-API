"""发货单生单表体扩展自定义项（dispatch_line_bdefine）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import dispatch_line_bdefine as m
from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.dispatch_line_bdefine_sql import (
    absent_col_sql,
    cols_sql,
    disp_by_lines_sql,
    disp_count_sql,
    dispatch_bdefine_samples,
    ext_col,
    ext_lines_sql,
    gone_sql,
    kind_of,
    so_lines_sql,
)
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.samples import sample_sql
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_GEN = "/v1/vouchers/generate"
_PICK = "ROW:C900001|01|D01|P01|A01|01\n"
_TODAY = "2026-10-03"
_YESTERDAY = "2026-10-02"
_COLS = "ROW:cbdefine5|nvarchar|120\nROW:cbdefine6|tinyint|0\nROW:cbdefine7|datetime|0\n"
_TEXT = ("E2EABCDA", "E2EABCDB")
# 发货单两行：2 × 1.5 = 3.00 = 2.65 + 0.35（U8 界面口径）。
_AMT = "ROW:41|2.000000|1.500000|3.00|2.65|0.35\nROW:42|2.000000|1.500000|3.00|2.65|0.35\n"


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
        self.queries: list[str] = []

    def __call__(self, _env: object, query: str, _label: str) -> str:
        self.queries.append(query)
        for key, rows in self.answers:
            if key in query and rows:
                return rows.pop(0)
        raise AssertionError("没有准备回答：" + query[:120])


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="op001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today=_TODAY, bag={})


def _ok(**extra: object) -> dict:
    body: dict = {"ok": True}
    body.update(extra)
    return body


def _line(key: str, ident: int, text: str, num: str, day: str) -> dict:
    return {key: ident, "cbDefine5": text, "cbdefine6": num, "cbdefine7": day + " 00:00:00"}


def _two(key: str, first: int, second: int, swap: bool = False) -> list[dict]:
    rows = [_line(key, first, _TEXT[0], "3.000000", _TODAY), _line(key, second, _TEXT[1], "4", _YESTERDAY)]
    if swap:
        rows[0]["cbDefine5"], rows[1]["cbDefine5"] = rows[1]["cbDefine5"], rows[0]["cbDefine5"]
    return rows


def _client(refuse: bool = True, made: dict | None = None, replay: dict | None = None, swap_load: bool = False) -> _Replay:
    gens: list = [U8CoError(400, "bad_request", "账套没有扩展自定义项 cbdefine99（表 DispatchLists_extradefine）")] if refuse else []
    dry_doc = {"type": "dispatch", "id": 9, "lines": _two("iSOsID", 31, 32)}
    gens += [
        _ok(dry_run=True, docs=[{"type": "sale_order", "id": 1}, dry_doc]),
        made or _ok(id=9, lines=2),
        replay or _ok(id=9, lines=2),
    ]
    return _Replay({
        "/v1/vouchers/create": [_ok(id=1)],
        "/v1/vouchers/verify": [_ok() for _ in range(2)],
        _GEN: gens,
        "/v1/vouchers/load": [_ok(lines=_two("iDLsID", 41, 42, swap_load))],
        "/v1/vouchers/delete": [_ok(deleted=True) for _ in range(4)],
    })


def _ext(first: str = _TEXT[0]) -> str:
    return f"ROW:41|31|{first}|3|{_TODAY}\nROW:42|32|{_TEXT[1]}|4|{_YESTERDAY}\n"


_SQL_DEFAULTS = {"ext": "", "cols": _COLS, "absent": "ROW:cbdefine99\n", "stray": "", "gone": "ROW:0|0\n", "amt": _AMT,
                 "so_flags": "ROW:31|1\nROW:32|1\n", "dl_flags": "ROW:41|1\nROW:42|1\n", "option": "ROW:True\n"}
# 账套报价不含税：U8 按无税单价 1.327434 算，2.65 + 0.34 = 2.99，行上 bsaleprice=0。
_UNTAXED = {"option": "ROW:False\n", "so_flags": "ROW:31|0\nROW:32|0\n", "dl_flags": "ROW:41|0\nROW:42|0\n",
            "amt": "ROW:41|2.000000|1.500000|2.99|2.65|0.34\nROW:42|2.000000|1.500000|2.99|2.65|0.34\n"}


def _sql(**over: str) -> _Sql:
    v = {**_SQL_DEFAULTS, **over}
    # 挂订单行的发货单行数：被拒后、预演后各 0，重放后 2；不做缺列拒绝时少一次。
    counts = ["ROW:0\n", "ROW:0\n", "ROW:2\n"] if v["absent"] else ["ROW:0\n", "ROW:2\n"]
    return _Sql([
        ("a.cName = N'bSalePrice'", [v["option"]]),
        ("N'bsaleprice') IS NULL", ["ROW:1\n", "ROW:1\n"]),
        ("CONVERT(int, b.bsaleprice)", [v["so_flags"], v["dl_flags"]]),
        ("LOWER(c.name) IN", [v["cols"]]),
        ("AS v\nFROM dbo.Warehouse AS w", [_PICK]),
        ("ORDER BY d.iSOsID", ["ROW:31\nROW:32\n"]),
        ("FROM (VALUES", [v["absent"]]),
        ("ORDER BY b.DLID, b.iDLsID", [v["stray"] or "ROW:9|41\nROW:9|42\n"]),
        ("N'|' + CASE WHEN OBJECT_ID", [v["gone"]]),
        ("WHERE b.iSOsID IN (31, 32)", counts),
        ("LEFT JOIN dbo.DispatchLists_extradefine", [v["ext"] or _ext()]),
        ("b.iTaxUnitPrice", [v["amt"]]),
    ])


def _run(client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql), mock.patch.object(m.os, "urandom", return_value=bytes((0xAB, 0xCD))):
        return m.dispatch_bdefine(_env(client), _SPEC, _STEP)


def _gens(client: _Replay) -> list[dict]:
    return [fields for route, fields in client.calls if route == _GEN]


class RegistryTests(unittest.TestCase):
    def test_registered_once_with_op_and_routes(self) -> None:
        (spec,) = m.dispatch_bdefine_cases()
        self.assertEqual(spec.name, "dispatch-gen-line-bdefine")
        self.assertEqual(known_names().count(spec.name), 1)
        self.assertTrue(spec.requires_login)
        self.assertIn(_GEN, spec.routes)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertIn("w:dispatch", spec.tags)

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in dispatch_bdefine_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        text = ext_lines_sql("7", [("cbdefine5", "text"), ("cbdefine7", "date")])
        self.assertIn("x.cbdefine5", text)
        self.assertIn("CONVERT(varchar(10), x.cbdefine7, 23)", text)
        self.assertIn("N'cbdefine5', N'cbdefine6', N'cbdefine7'", cols_sql())
        self.assertIn("N'cbdefine99'", absent_col_sql())
        self.assertIn("d.ID = 5", so_lines_sql("5"))
        self.assertIn("b.iSOsID IN (1, 2)", disp_by_lines_sql(["1", "2"]))
        both = gone_sql(["1"], ["41", "42"])
        self.assertIn("b.iDLsID IN (41, 42)", both)
        self.assertIn("x.iDLsID IN (41, 42)", both)
        self.assertNotIn("DispatchLists_extradefine", gone_sql(["1"], []))

    def test_sql_refuses_bad_ids_and_columns(self) -> None:
        calls = (
            lambda: disp_count_sql(["0"]),
            lambda: disp_count_sql([]),
            lambda: disp_by_lines_sql(["1 OR 1=1"]),
            lambda: gone_sql(["1"], ["x"]),
            lambda: so_lines_sql("x"),
            lambda: ext_lines_sql("x", [("cbdefine5", "text")]),
            lambda: ext_lines_sql("7", []),
            lambda: ext_lines_sql("7", [("cbdefine5; --", "text")]),
            lambda: ext_lines_sql("7", [("cbdefine5", "")]),
        )
        for call in calls:
            with self.assertRaises(ValueError):
                call()

    def test_column_names_and_kinds(self) -> None:
        self.assertEqual(ext_col(" cbDefine12 "), "cbdefine12")
        for bad in ("cbdefine0", "cbdefine04", "cbdefine100", "chdefine5", ""):
            self.assertEqual(ext_col(bad), "", bad)
        self.assertEqual(kind_of("NVARCHAR"), "text")
        self.assertEqual(kind_of("float"), "number")
        self.assertEqual(kind_of("smalldatetime"), "date")
        self.assertEqual(kind_of("image"), "")


class ReplayTests(unittest.TestCase):
    def test_pass_with_two_lines_and_three_columns(self) -> None:
        client = _client()
        status, note = _run(client, _sql())
        self.assertEqual(status, "PASS", note)
        self.assertIn("测了 cbdefine5、cbdefine6、cbdefine7", note)
        self.assertNotIn("未测", note)
        self.assertIn("缺列 cbdefine99 400", note)
        self.assertIn("dispatch bsaleprice=1", note)
        gens = _gens(client)
        self.assertEqual(len(gens), 4)
        self.assertEqual(gens[0]["lines"][0]["cbdefine99"], "X")
        want = [
            {"source_line_id": 31, "quantity": 2, "cWhCode": "01", "cbdefine5": _TEXT[0], "cbdefine6": "3", "cbdefine7": _TODAY},
            {"source_line_id": 32, "quantity": 2, "cWhCode": "01", "cbdefine5": _TEXT[1], "cbdefine6": "4",
             "cbdefine7": _YESTERDAY},
        ]
        self.assertEqual(gens[2]["lines"], want)
        self.assertIs(gens[1]["dry_run"], True)
        self.assertNotIn("idempotency_key", gens[1])
        self.assertTrue(gens[2]["idempotency_key"].startswith("e2e-dlbd-"))
        self.assertEqual(gens[3], gens[2])
        deletes = [fields["type"] for route, fields in client.calls if route == "/v1/vouchers/delete"]
        self.assertEqual(deletes, ["dispatch", "sale_order"])

    def test_reports_skipped_columns(self) -> None:
        client = _client()
        sql = _sql(cols="ROW:cbdefine5|varchar|4\n", ext="ROW:41|31|E2EA\nROW:42|32|E2EB\n")
        with mock.patch.object(m, "_check_lines"):
            status, note = _run(client, sql)
        self.assertEqual(status, "PASS", note)
        self.assertIn("测了 cbdefine5", note)
        self.assertIn("账套没有 cbdefine6、cbdefine7，未测", note)
        self.assertEqual([row["cbdefine5"] for row in _gens(client)[2]["lines"]], ["E2EA", "E2EB"])

    def test_not_applicable_without_columns(self) -> None:
        status, note = _run(_Replay({}), _sql(cols=""))
        self.assertEqual(status, "N/A")
        self.assertIn("DispatchLists_extradefine", note)

    def test_skips_refusal_when_every_column_exists(self) -> None:
        status, note = _run(_client(refuse=False), _sql(absent=""))
        self.assertEqual(status, "PASS", note)
        self.assertIn("未做缺列拒绝", note)

    def test_fail_when_lines_swapped_in_store(self) -> None:
        status, note = _run(_client(), _sql(ext=_ext(first=_TEXT[1])))
        self.assertEqual(status, "FAIL")
        self.assertIn("订单行 31 的 cbdefine5", note)

    def test_fail_when_amount_follows_untaxed_price(self) -> None:
        # 按无税单价 1.327434 算：2.65 + 0.34 = 2.99，与 round(2 × 1.5, 2) 差一分。
        amt = "ROW:41|2.000000|1.500000|2.99|2.65|0.34\nROW:42|2.000000|1.500000|3.00|2.65|0.35\n"
        status, note = _run(_client(), _sql(amt=amt))
        self.assertEqual(status, "FAIL")
        self.assertIn("行 41 价税合计 2.99 应为", note)

    def test_pass_when_account_quotes_untaxed(self) -> None:
        status, note = _run(_client(), _sql(**_UNTAXED))
        self.assertEqual(status, "PASS", note)
        self.assertIn("账套报价不含税", note)
        self.assertIn("dispatch bsaleprice=0", note)

    def test_fail_when_untaxed_account_gets_taxed_line(self) -> None:
        status, note = _run(_client(), _sql(**{**_UNTAXED, "dl_flags": "ROW:41|0\nROW:42|1\n"}))
        self.assertEqual(status, "FAIL")
        self.assertIn("行 42 的 bsaleprice 应为 0", note)

    def test_fail_when_flags_have_no_rows(self) -> None:
        status, note = _run(_client(), _sql(dl_flags=""))
        self.assertEqual(status, "FAIL")
        self.assertIn("没有明细行", note)

    def test_fail_when_created_line_not_taxed(self) -> None:
        # 账套报价含税，桥新建的订单行 bsaleprice 仍为 0（以前桥写的行就是这样）。
        status, note = _run(_client(), _sql(so_flags="ROW:31|1\nROW:32|0\n"))
        self.assertEqual(status, "FAIL")
        self.assertIn("行 32 的 bsaleprice 应为 1", note)

    def test_fail_when_tax_price_drifts(self) -> None:
        amt = "ROW:41|2.000000|1.499999|3.00|2.65|0.35\nROW:42|2.000000|1.500000|3.00|2.65|0.35\n"
        status, note = _run(_client(), _sql(amt=amt))
        self.assertEqual(status, "FAIL")
        self.assertIn("含税单价", note)

    def test_fail_when_load_swaps_lines(self) -> None:
        status, note = _run(_client(swap_load=True), _sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("vouchers/load", note)

    def test_fail_when_replay_makes_another_dispatch(self) -> None:
        client = _client(replay=_ok(id=10, lines=2))
        status, note = _run(client, _sql(stray="ROW:9|41\nROW:9|42\nROW:10|43\nROW:10|44\n"))
        self.assertEqual(status, "FAIL")
        self.assertIn("同一张发货单", note)
        deletes = [fields["id"] for route, fields in client.calls if route == "/v1/vouchers/delete"]
        self.assertEqual(sorted(deletes[:2]), [9, 10])

    def test_ambiguous_response_is_reconciled_and_cleaned(self) -> None:
        # 正式生单 504：响应没有 id，发货单其实已落库；清理前按订单行找到并删除，再核对无残留。
        client = _client(made=U8CoError(504, "outcome_unknown", "已保存但未能确定单据标识"))
        sql = _sql()
        status, note = _run(client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("对账找出未登记的发货单 9", note)
        deletes = [fields for route, fields in client.calls if route == "/v1/vouchers/delete"]
        self.assertEqual([(one["type"], one["id"]) for one in deletes], [("dispatch", 9), ("sale_order", 1)])
        self.assertTrue(any("x.iDLsID IN (41, 42)" in query for query in sql.queries))

    def test_fail_when_extension_rows_remain(self) -> None:
        status, note = _run(_client(), _sql(gone="ROW:0|2\n"))
        self.assertEqual(status, "FAIL")
        self.assertIn("清理后仍有残留", note)

    def test_fail_when_dry_docs_lack_the_value(self) -> None:
        client = _client()
        client.answers[_GEN][1]["docs"][1]["lines"][1]["cbdefine6"] = ""
        status, note = _run(client, _sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("预演 docs", note)


if __name__ == "__main__":
    unittest.main()
