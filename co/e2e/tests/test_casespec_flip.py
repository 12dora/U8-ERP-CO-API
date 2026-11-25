"""临时改选项或造数据、跑完原样恢复的一组用例（flip 及其下的 openings_pu_unpost、exch_floating、credit_flip、
price_list、so_lock_other）：登记、标签、分片、SQL 形状和回放核对。不连接服务器。

重点核对恢复设计：改动之后无论步骤成败、抛不抛异常，恢复步骤都执行。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import credit_flip as credit
from co.e2e.casespec import exch_floating as exch
from co.e2e.casespec import flip
from co.e2e.casespec import flip_util as util
from co.e2e.casespec import so_lock_other as lock
from co.e2e.casespec import price_list as price
from co.e2e.casespec import openings_pu_unpost as pu
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.exch_floating_sql import mode_set_sql
from co.e2e.casespec.so_lock_other_sql import append_sql, clear_sql, lock_as_other_sql, strip_sql
from co.e2e.casespec.price_list_sql import MARK, customer_insert_sql, delete_sql, ids_sql, vendor_insert_sql
from co.e2e.casespec.openings_pu_unpost_sql import pu_unpost_state_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = (
    "openings-post-pu-unpost-refused",
    "openings-post-pu-unpost",
    "sale-order-lock-other",
    "report-price-list-live",
    "archive-exchange-rate-floating",
    "report-customer-credit-flip",
)
_EXCLUSIVE = {"archive-exchange-rate-floating", "report-customer-credit-flip"}
_SPEC = CaseSpec("flip", "s")
_STEP = Step("custom")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    def __init__(self, answers: list) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers.pop(0) if self.answers else {"ok": True}
        if isinstance(got, BaseException):
            raise got
        return got


class _Sql:
    """按 SQL 里的特征串依次回答（每个键一个队列，先列的键优先）；记下每条命中的键。"""

    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]
        self.hits: list[str] = []

    def __call__(self, _env: object, query: str, _label: str) -> str:
        self.check(query)
        for key, rows in self.answers:
            if key in query and rows:
                self.hits.append(key)
                got = rows.pop(0)
                if isinstance(got, BaseException):
                    raise got
                return got
        raise AssertionError("没有准备回答：" + query[:160])

    @staticmethod
    def check(query: str) -> None:
        if sql_block_reason(query):
            raise AssertionError("SQL 过不了守卫：" + query[:160])


def _rows(*cells: str) -> str:
    return "".join(f"ROW:{cell}\n" for cell in cells)


def _env(client: object, name: str = "张三") -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-02", bag={}, operator_name=name)


def _run(op, client: _Replay, sql: _Sql, name: str = "张三") -> tuple[str, str]:
    with mock.patch.object(util, "run_guarded", sql), mock.patch.object(lock, "run_guarded", sql):
        return op(_env(client, name), _SPEC, _STEP)


# ---- 登记 ----


class CatalogTests(unittest.TestCase):
    def test_registered_with_ops(self) -> None:
        names = known_names()
        cases = {spec.name: spec for spec in flip.flip_cases()}
        self.assertEqual(set(cases), set(_NAMES))
        for name, spec in cases.items():
            with self.subTest(case=name):
                self.assertEqual(names.count(name), 1)
                self.assertTrue(spec.requires_login)
                self.assertTrue(spec.routes)
                self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
                self.assertEqual(spec.exclusive, name in _EXCLUSIVE)
        self.assertEqual(set(flip.FLIP_OPS), {spec.steps[0].sql_name for spec in cases.values()})

    def test_tags(self) -> None:
        cases = {spec.name: spec for spec in flip.flip_cases()}
        for name in ("report-price-list-live", "report-customer-credit-flip"):
            tags = auto_tags(cases[name])
            self.assertIn("wave:2", tags)
            self.assertTrue(any(tag.startswith("rpt:") for tag in tags))
        self.assertIn("opening:pu", auto_tags(cases["openings-post-pu-unpost"]))
        self.assertIn("chain:sale", auto_tags(cases["sale-order-lock-other"]))

    def test_samples_pass_the_guard_and_are_in_samples(self) -> None:
        rows = sample_sql()
        samples = flip.flip_samples()
        self.assertEqual(len({name for name, _q in samples}), len(samples))
        for name, query in samples:
            with self.subTest(sample=name):
                self.assertEqual(sql_block_reason(query), "")
                self.assertIn("N'ROW:", query)
                self.assertIn((name, query), rows)


# ---- SQL 形状 ----


class SqlShapeTests(unittest.TestCase):
    def test_pu_state_reads_the_four_refusals(self) -> None:
        sql = pu_unpost_state_sql()
        for part in ("N'dPUStartDate'", "g.iperiod = 0", "g.iperiod >= d.m", "bflag_IA", "FROM dbo.PurBillVouch", "g.iperiod < d.m"):
            self.assertIn(part, sql)

    def test_option_writes_need_old_value(self) -> None:
        sql = mode_set_sql("False", "True")
        self.assertIn("cName = N'iXchgRateStl'", sql)
        self.assertIn("= N'True';", sql)
        self.assertIn("@@ROWCOUNT", sql)
        for bad in (("x'y", "True"), ("False", "a b"), ("False", "x" * 11)):
            with self.assertRaises(ValueError):
                mode_set_sql(*bad)

    def test_price_sql_marks_and_branches_on_identity(self) -> None:
        sql = customer_insert_sql("C0001", "01", "I0001", "2026-10-02")
        for part in ("COLUMNPROPERTY(OBJECT_ID(N'dbo.SA_CusUPrice'), N'AutoID', 'IsIdentity') = 1", "SET XACT_ABORT ON",
                     "ROLLBACK TRANSACTION", "N'ROW:ERR|'", "CONVERT(date, N'2026-09-22', 23)", f"N'{MARK}'", "(2, NULL, N'01'"):
            self.assertIn(part, sql)
        self.assertNotIn("(2, N''", customer_insert_sql("C0001", "", "I0001", "2026-10-02"))
        self.assertIn("N'Autoid'", vendor_insert_sql("S0001", "I0001", "2026-10-02"))
        self.assertEqual(delete_sql().count(f"WHERE cMemo = N'{MARK}';"), 3)
        self.assertEqual(ids_sql().count("UNION ALL"), 2)
        for bad in (("C'1", "", "I1", "2026-10-02"), ("C1", "", "I1", "2026-13-01")):
            with self.assertRaises(ValueError):
                customer_insert_sql(*bad)

    def test_lock_sql_guards_state(self) -> None:
        self.assertIn("ISNULL(cLocker, N''))) = N''", lock_as_other_sql(5))
        self.assertIn("BETWEEN 1 AND 19", append_sql(5))
        self.assertIn("LIKE N'%X'", strip_sql(5))
        self.assertIn("cLocker = NULL", clear_sql(5))
        with self.assertRaises(ValueError):
            clear_sql(0)

    def test_nliteral(self) -> None:
        self.assertEqual(util.nliteral("O'Neil", 10), "N'O''Neil'")
        for bad in ("", "a\nb", "x" * 11):
            with self.assertRaises(ValueError):
                util.nliteral(bad, 10)


# ---- 采购期初取消记账 ----

_PU = "dPUStartDate"
_REFUSED = U8CoError(409, "state_mismatch", pu.HAS_INVOICE)


def _pu_state(zero: str = "1", closed: int = 0, ia: str = "0", invoices: int = 3, before: int = 9) -> str:
    return _rows(f"2026-10-01|{zero}|{closed}|{ia}|{invoices}|{before}")


class PuUnpostTests(unittest.TestCase):
    def test_expected_refusal_follows_bridge_order(self) -> None:
        state = pu.parse_state(["2026-10-01", "1", "2", "1", "3", "9"])
        self.assertEqual(pu.expected_refusal(state), pu.CLOSED)
        self.assertEqual(pu.expected_refusal(pu.parse_state(["2026-10-01", "1", "0", "1", "3", "9"])), pu.IA_POSTED)
        self.assertEqual(pu.expected_refusal(pu.parse_state(["2026-10-01", "1", "0", "0", "3", "9"])), pu.HAS_INVOICE)
        self.assertEqual(pu.expected_refusal(pu.parse_state(["2026-10-01", "1", "0", "0", "0", "9"])), "")
        self.assertEqual(pu.expected_refusal(pu.parse_state(["2026-10-01", "0", "0", "0", "0", "0"])), pu.NOT_POSTED)
        with self.assertRaises(ValueError):
            pu.parse_state(["2026-10-01"])

    def test_refused_dry_and_real(self) -> None:
        client = _Replay([_REFUSED, _REFUSED])
        sql = _Sql([(_PU, [_pu_state(), _pu_state(), _pu_state()])])
        status, detail = _run(pu.pu_unpost_refused, client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertTrue(client.calls[0][1]["dry_run"])
        self.assertNotIn("dry_run", client.calls[1][1])
        self.assertEqual(client.calls[1][1]["action"], "unpost")

    def test_refused_wrong_text_fails(self) -> None:
        client = _Replay([U8CoError(409, "state_mismatch", pu.CLOSED)])
        sql = _Sql([(_PU, [_pu_state(), _pu_state()])])
        status, detail = _run(pu.pu_unpost_refused, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn(pu.HAS_INVOICE, detail)

    def test_refused_but_bridge_unposted_is_reposted(self) -> None:
        client = _Replay([_REFUSED, {"ok": True, "posted": False}, {"ok": True, "posted": True}])
        sql = _Sql([(_PU, [_pu_state(), _pu_state(zero="0", before=0), _pu_state(zero="0", before=0), _pu_state()])])
        status, _detail = _run(pu.pu_unpost_refused, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertEqual(client.calls[-1][1]["action"], "post")

    def test_cycle_na_when_invoices_exist(self) -> None:
        client = _Replay([])
        status, detail = _run(pu.pu_unpost_cycle, client, _Sql([(_PU, [_pu_state()])]))
        self.assertEqual(status, "N/A")
        self.assertIn("采购发票 3 张", detail)
        self.assertEqual(client.calls, [])

    def test_cycle_unposts_and_reposts(self) -> None:
        dry = {"ok": True, "dry_run": True, "detail": {"opening": {"posted": False}}}
        client = _Replay([dry, {"ok": True, "posted": False, "module": "pu", "opening_year": 2026}, {"ok": True, "posted": True}])
        states = [_pu_state(invoices=0), _pu_state(invoices=0), _pu_state(zero="0", invoices=0, before=0),
                  _pu_state(invoices=0), _pu_state(invoices=0)]
        status, detail = _run(pu.pu_unpost_cycle, client, _Sql([(_PU, states)]))
        self.assertEqual(status, "PASS", detail)
        self.assertEqual([sent["action"] for _r, sent in client.calls], ["unpost", "unpost", "post"])

    def test_cycle_failure_reposts(self) -> None:
        dry = {"ok": True, "dry_run": True, "detail": {"opening": {"posted": False}}}
        client = _Replay([dry, {"ok": True, "posted": False, "module": "pu", "opening_year": 2026}, {"ok": True, "posted": True}])
        states = [_pu_state(invoices=0), _pu_state(invoices=0), _pu_state(zero="0", invoices=0, before=4),
                  _pu_state(zero="0", invoices=0, before=4), _pu_state(invoices=0)]
        status, detail = _run(pu.pu_unpost_cycle, client, _Sql([(_PU, states)]))
        self.assertEqual(status, "FAIL")
        self.assertIn("仍置 4 个期间", detail)
        self.assertEqual(client.calls[-1][1]["action"], "post")

    def test_not_test_account(self) -> None:
        client = _Replay([U8CoError(403, "test_account_only", "期初记账只对配置为测试账套的账套开放")])
        status, detail = _run(pu.pu_unpost_refused, client, _Sql([(_PU, [_pu_state()])]))
        self.assertEqual(status, "N/A")
        self.assertIn("testAccounts", detail)


# ---- 浮动汇率 ----

_SET = "UPDATE dbo.AccInformation"


def _exch_sql(rows_after: list[str], mode: list[str] | None = None) -> _Sql:
    return _Sql([
        (_SET, [_rows("1"), _rows("1")]),
        ("iXchgRateStl", mode or [_rows("1|True"), _rows("1|True")]),
        ("IS NOT NULL;", [_rows("美元")]),
        ("BETWEEN 1 AND 12", [_rows(*[f"{p}|{1 if p == 11 else 0}|{1 if p < 10 else 0}" for p in range(1, 13)])]),
        ("ORDER BY e.i_id", rows_after),
    ])


class ExchFloatingTests(unittest.TestCase):
    def test_helpers(self) -> None:
        self.assertTrue(exch.is_fixed("True") and exch.is_fixed("1") and exch.is_fixed(" "))
        self.assertFalse(exch.is_fixed("False") or exch.is_fixed("0"))
        self.assertEqual((exch.floating_value("1"), exch.floating_value("True"), exch.floating_value("")), ("0", "False", "False"))
        day = "2026-12-05"
        self.assertEqual([exch.cdate_form(c, day) for c in (day, "5", "05", "x")], ["yyyy-mm-dd", "日数", "两位日数", ""])
        cells = [["10", "0", "0"], ["11", "0", "0"], ["12", "1", "0"]]
        self.assertEqual(exch.free_period(cells, 10), 11)
        self.assertEqual(exch.free_period(cells, 11), 0)

    def test_flip_create_read_delete_restore(self) -> None:
        client = _Replay([
            {"ok": True, "rate_mode": "floating", "items": []},
            {"ok": True, "code": "美元:2026:12:5"},
            {"ok": True, "rate_mode": "floating", "fields": {"rate": 6.5432, "mode": "floating", "day": "5"}},
            {"ok": True, "deleted": True},
        ])
        sql = _exch_sql([_rows("77|5|6.543200"), "", ""])
        status, detail = _run(exch.exch_floating, client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("'5'（日数）", detail)
        self.assertEqual(client.calls[1][1]["code"], "美元:2026:12:2026-12-05")
        self.assertEqual(client.calls[2][1]["code"], "美元:2026-12-05")
        self.assertEqual(client.calls[3][1]["code"], "美元:2026:12:5")
        self.assertEqual(sql.hits.count(_SET), 2)

    def test_create_failure_still_restores_option(self) -> None:
        client = _Replay([{"ok": True, "rate_mode": "floating"}, U8CoError(409, "u8_rejected", "导入失败")])
        sql = _exch_sql(["", ""])
        status, detail = _run(exch.exch_floating, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("导入失败", detail)
        self.assertEqual(sql.hits.count(_SET), 2)
        self.assertEqual(sql.hits[-1], "iXchgRateStl")

    def test_already_floating_leaves_option(self) -> None:
        client = _Replay([{"ok": True, "rate_mode": "floating"}, {"ok": True, "code": "美元:2026:12:2026-12-05"},
                          {"ok": True, "rate_mode": "floating", "fields": {"rate": 6.5432, "mode": "floating", "day": "2026-12-05"}},
                          {"ok": True, "deleted": True}])
        sql = _exch_sql([_rows("77|2026-12-05|6.543200"), "", ""], mode=[_rows("1|False")])
        status, detail = _run(exch.exch_floating, client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertNotIn(_SET, sql.hits)
        self.assertIn("本来就是浮动汇率", detail)

    def test_no_option_row_is_na(self) -> None:
        client = _Replay([])
        status, _detail = _run(exch.exch_floating, client, _Sql([("iXchgRateStl", [_rows("0|")])]))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])


# ---- 信用余额表选项 ----

_ITEM = {"code": "C0001", "controlled": True, "credit_line": 1000, "order": 10, "dispatch": 0, "invoice": 0, "ar": 5,
         "expense": 0, "used": 15, "available": 985}
_HEAD = {"ok": True, "check_point": "save", "formula": {"order": True}, "credit_control": True, "ar_enabled": True}


def _report(balance: bool, item: dict | None = None) -> dict:
    return dict(_HEAD, balance_table=balance, items=[dict(item or _ITEM)])


class CreditFlipTests(unittest.TestCase):
    def test_helpers(self) -> None:
        self.assertEqual([credit.flipped(v) for v in ("1", "0", "True", "false", "")], ["0", "1", "False", "True", "True"])
        self.assertTrue(credit.is_on("TRUE") and not credit.is_on("0"))

    def test_flip_same_numbers_and_restore(self) -> None:
        client = _Replay([_report(True), _report(False)])
        sql = _Sql([(_SET, [_rows("1"), _rows("1")]), ("bUseBanlaceTable", [_rows("1|1"), _rows("1|1")])])
        status, detail = _run(credit.credit_flip, client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("1→0", detail)
        self.assertEqual(sql.hits, ["bUseBanlaceTable", _SET, _SET, "bUseBanlaceTable"])

    def test_changed_numbers_fail_and_restore(self) -> None:
        client = _Replay([_report(True), _report(False, dict(_ITEM, order=12, used=17))])
        sql = _Sql([(_SET, [_rows("1"), _rows("1")]), ("bUseBanlaceTable", [_rows("1|True"), _rows("1|True")])])
        status, detail = _run(credit.credit_flip, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("C0001", detail)
        self.assertEqual(sql.hits.count(_SET), 2)

    def test_echo_mismatch_before_flip_never_writes(self) -> None:
        client = _Replay([_report(False)])
        sql = _Sql([("bUseBanlaceTable", [_rows("1|1")])])
        status, _detail = _run(credit.credit_flip, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertEqual(sql.hits, ["bUseBanlaceTable"])


# ---- 价格表 ----

_IDS = _rows("customer|11|114.000000", "customer|12|110.000000", "customer|13|99.000000", "inventory|21|10.000000",
             "inventory|22|8.000000", "vendor|31|50.000000", "vendor|32|40.000000")


def _price_sql(insert: str = "OK|3") -> _Sql:
    return _Sql([
        ("DECLARE @n int", [_rows("0|0"), _rows("7|0")]),
        ("UNION ALL", [_IDS]),
        ("INSERT INTO dbo.SA_CusUPrice", [_rows(insert)]),
        ("INSERT INTO dbo.SA_InvUPrice", [_rows("OK|2")]),
        ("INSERT INTO dbo.Ven_Inv_Price", [_rows("OK|2")]),
        ("CROSS JOIN", [_rows("C0001|01|I0001|S0001|人民币")]),
    ])


def _common(**extra: object) -> dict:
    base = {"inv_code": "I0001", "currency": "人民币", "start_date": "2026-09-22", "end_date": None, "memo": MARK,
            "invalid": False}
    base.update(extra)
    return base


def _price_good() -> list:
    cus = _common(id=11, customer="C0001", customer_class=None, min_qty=2, tax_included=True, promotion=False,
                  quote=120, discount_rate=95, price=114, min_price=100)
    cls = _common(id=12, customer=None, customer_class="01", tax_included=False, promotion=True, quote=115,
                  discount_rate=96, price=110, min_price=None)
    old = _common(id=13, start_date="2026-09-02", end_date="2026-10-01", price=99)
    inv = _common(id=21, min_qty=1, tax_included=False, promotion=False,
                  levels=[{"level": 1, "price": 10, "tax_price": 11.3}, {"level": 3, "price": 9, "tax_price": None}])
    ven = _common(id=31, vendor="S0001", min_qty=1, max_qty=100, tax_included=True, price=50, tax_price=56.5,
                  tax_rate=13, supply_type=0, promotion=False)
    return [
        {"ok": True, "kind": "customer", "items": [cus, cls]},
        {"ok": True, "kind": "customer", "items": [cus, cls, old]},
        {"ok": True, "kind": "inventory", "items": [inv]},
        {"ok": True, "kind": "vendor", "items": [ven]},
        {"ok": True, "kind": "vendor", "items": [ven, {"id": 32}]},
    ]


class PriceListTests(unittest.TestCase):
    def test_helpers(self) -> None:
        self.assertEqual(price.field_problem({"a": 1.0, "b": None}, {"a": 1, "b": None}), "")
        self.assertIn("a 应为", price.field_problem({"a": 2}, {"a": 1}))
        self.assertEqual(price.levels_problem([{"level": 1, "price": 10, "tax_price": 11.3},
                                               {"level": 3, "price": 9.0, "tax_price": None}]), "")
        self.assertIn("levels", price.levels_problem([]))
        self.assertEqual(price.shift("2026-10-02", -10), "2026-09-22")

    def test_insert_report_delete(self) -> None:
        client = _Replay(_price_good())
        sql = _price_sql()
        status, detail = _run(price.price_list_live, client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(sql.hits[-1], "DECLARE @n int")
        asks = [sent for _r, sent in client.calls]
        self.assertEqual((asks[0]["customer"], asks[0]["inv"], asks[1]["all_dates"]), ("C0001", "I0001", True))
        self.assertEqual((asks[3]["vendor"], asks[4]["all_dates"]), ("S0001", True))

    def test_insert_error_fails_and_cleans(self) -> None:
        client = _Replay([])
        sql = _price_sql("ERR|列名 bsales 无效。")
        status, detail = _run(price.price_list_live, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("bsales", detail)
        self.assertEqual(sql.hits[-1], "DECLARE @n int")
        self.assertEqual(client.calls, [])

    def test_expired_row_in_default_report_fails(self) -> None:
        answers = _price_good()
        answers[0]["items"].append({"id": 13})
        status, detail = _run(price.price_list_live, _Replay(answers), _price_sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("已失效的客户价格", detail)


# ---- 别人锁定 ----

_LOCKER = "varchar(4), LEN("


def _lock_sql(lockers: list[str]) -> _Sql:
    return _Sql([
        ("m.ID", [_rows("501")]),
        (_LOCKER, [_rows(cell) for cell in lockers]),
        ("N'E2E-OTHER' WHERE", [_rows("1")]),
        ("+ N'X' WHERE", [_rows("1")]),
        ("LEFT(LTRIM", [_rows("1")]),
        ("cLocker = NULL", [_rows("1")]),
    ])


def _lock_good(name: str = "张三") -> list:
    return [
        U8CoError(409, "state_mismatch", "单据已被 E2E-OTHER 锁定"),
        U8CoError(409, "state_mismatch", "单据由 E2E-OTHER 锁定"),
        U8CoError(409, "state_mismatch", "单据已被 E2E-OTHER 锁定"),
        U8CoError(409, "state_mismatch", "单据已被 E2E-OTHER 锁定"),
        {"ok": True, "locked": True, "locker": name},
        U8CoError(409, "state_mismatch", f"单据由 {name}X 锁定"),
        {"ok": True, "locked": False, "locker": ""},
    ]


class LockOtherTests(unittest.TestCase):
    def test_stored(self) -> None:
        self.assertEqual(lock.stored(" 张三 "), "张三")
        self.assertEqual(lock.stored("a" * 19 + " b"), "a" * 19)
        self.assertEqual(lock.stored("x" * 25), "x" * 20)

    def test_other_locker_refused_and_width_rule(self) -> None:
        client = _Replay(_lock_good())
        sql = _lock_sql(["E2E-OTHER|9", "张三|2", "|0", "|0"])
        status, detail = _run(lock.lock_other, client, sql)
        self.assertEqual(status, "PASS", detail)
        routes = [(route, sent.get("action"), sent.get("dry_run")) for route, sent in client.calls]
        self.assertEqual(routes[:4], [(lock.LOCK, "lock", None), (lock.LOCK, "unlock", None), (lock.VERIFY, "verify", True),
                                      (lock.DELETE, None, True)])
        self.assertIn("多一个字符即视为别人", detail)
        self.assertEqual(sql.hits.count("cLocker = NULL"), 1)

    def test_gate_missing_fails_and_clears(self) -> None:
        answers = _lock_good()
        answers[2] = {"ok": True, "dry_run": True}
        client = _Replay(answers)
        sql = _lock_sql(["E2E-OTHER|9", "|0"])
        status, detail = _run(lock.lock_other, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("预演审核", detail)
        self.assertEqual(sql.hits[-3:], [_LOCKER, "cLocker = NULL", _LOCKER])

    def test_long_name_checks_truncation(self) -> None:
        name = "很长的操作员姓名" * 3
        answers = _lock_good(name)
        del answers[5]
        client = _Replay(answers)
        sql = _lock_sql(["E2E-OTHER|9", name[:20] + "|20", "|0", "|0"])
        status, detail = _run(lock.lock_other, client, sql, name)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("截到 20 个字符", detail)
        self.assertNotIn("+ N'X' WHERE", sql.hits)

    def test_no_order_is_na(self) -> None:
        status, _detail = _run(lock.lock_other, _Replay([]), _Sql([("m.ID", [""])]))
        self.assertEqual(status, "N/A")


if __name__ == "__main__":
    unittest.main()
