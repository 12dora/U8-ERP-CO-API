"""自建存货的待实测用例（made_inv：`purchase-in-shelf-made`；mo_alloc：`mo-allocate-fixed-scrap` / `mo-allocate-aux`）
的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import stock_more as smore
from co.e2e.casespec import made_inv as made
from co.e2e.casespec import mo_alloc as mo
from co.e2e.casespec import runner
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.made_inv_sql import (
    bom_aux_sql,
    bom_fixed_sql,
    free_inv_sql,
    inv_flags_sql,
    inv_units_sql,
    made_samples,
    safe_rate,
    zero_stock_sql,
)
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("purchase-in-shelf-made", "mo-allocate-fixed-scrap", "mo-allocate-aux")
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_LOT = "E2ESLABCD1234"
_ARC_C = "/v1/archives/create"
_ARC_D = "/v1/archives/delete"
_CREATE = "/v1/vouchers/create"
_UPDATE = "/v1/vouchers/update"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"


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

    def bodies(self, route: str) -> list[dict]:
        return [fields for name, fields in self.calls if name == route]


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
    with mock.patch.object(sp, "run_guarded", sql), mock.patch.object(smore.secrets, "token_hex", lambda _n: "abcd1234"), \
            mock.patch.object(made.secrets, "randbelow", lambda _n: 42), mock.patch.object(mo.secrets, "randbelow", lambda _n: 7):
        return op(_env(client), _SPEC, _STEP)


def _routes(client: _Replay) -> list[str]:
    return [route.rsplit("/", 2)[-2] + "/" + route.rsplit("/", 1)[1] for route, _fields in client.calls]


class RegistryTests(unittest.TestCase):
    def test_registered_with_ops_and_tags(self) -> None:
        names = known_names()
        cases = made.made_cases() + mo.mo_alloc_cases()
        self.assertEqual(tuple(spec.name for spec in cases), _NAMES)
        for spec in cases:
            with self.subTest(case=spec.name):
                self.assertEqual(names.count(spec.name), 1)
                self.assertTrue(spec.requires_login and spec.routes)
                self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
                self.assertIn("arc:inventory", auto_tags(spec))
        self.assertTrue({"stock", "new:purchase_in"} <= auto_tags(cases[0]))
        self.assertTrue({"bom", "new:bom", "new:production_order"} <= auto_tags(cases[1]))
        self.assertTrue({"arc:unit", "arc:unit_group"} <= auto_tags(cases[2]))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in made_samples():
            with self.subTest(sample=name):
                self.assertEqual(sql_block_reason(query), "", name)
                self.assertIn((name, query), rows)
                self.assertIn("ROW:", query)

    def test_sql_inputs_are_checked(self) -> None:
        for bad in (
            lambda: free_inv_sql([]),
            lambda: free_inv_sql(["A'B"]),
            lambda: inv_flags_sql("I1", {"cInvName": 1}),
            lambda: inv_flags_sql("I1", {"bSelf": True}),
            lambda: inv_units_sql("I1", "G;1", "M", "A"),
            lambda: bom_fixed_sql("0", 20),
            lambda: bom_aux_sql("100", 10, "ZX01", "1e3"),
            lambda: safe_rate("0"),
            lambda: zero_stock_sql("I1'; --"),
        ):
            with self.assertRaises(ValueError):
                bad()
        self.assertIn("FVFlag = 2", bom_fixed_sql("100", 20))
        self.assertIn("b.Status = 1", bom_aux_sql("100", 10, "ZX01", "2.345678"))
        self.assertIn("iMassDate = 30", inv_flags_sql("I1", {"iMassDate": 30}))


# ---- purchase-in-shelf-made ----

_SHELF_ROW = _LOT + "|2026-10-02|2026-11-01|30|3|2026-10-31|2026-10-31|1"


def _shelf_client(arc_delete: dict | U8CoError | None = None) -> _Replay:
    return _Replay({
        _ARC_C: [_ok(archive="inventory", code="91970042")],
        _CREATE: [_ok(id=5)],
        _DELETE: [_ok(deleted=True)],
        _ARC_D: [arc_delete or _ok(deleted=True)],
    })


def _shelf_sql(row: str = _SHELF_ROW, pick: str = "ROW:W1|T1|V1|D1|PT1\n") -> _Sql:
    return _Sql([
        ("DELETE FROM dbo.CurrentStock", _rows("1")),
        ("ISNULL(i.bInvQuality, 0) = 0", [pick]),
        ("FROM (VALUES", _rows("91970042")),
        ("UPDATE dbo.Inventory SET bInvBatch", _rows("1")),
        ("bPTHavePO", _rows("0")),
        ("FROM dbo.rdrecords01 AS b", _rows(row)),
        ("c.cBatch = N'" + _LOT, _rows("1|0.0000|2026-11-01|2026-10-31", "1|0.0000||")),
        ("RdRecord01 AS h WHERE h.ID = 5", _rows("0")),
    ])


class ShelfMadeTests(unittest.TestCase):
    def test_made_inventory_used_then_deleted(self) -> None:
        client = _shelf_client()
        sql = _shelf_sql()
        status, detail = _run(made.purchase_in_shelf_made, client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("自建保质期存货 91970042", detail)
        self.assertIn("有效期至 2026-10-31", detail)
        self.assertIn("自建档案 1 个已删除", detail)
        arc = client.bodies(_ARC_C)[0]
        self.assertEqual((arc["archive"], arc["code"], arc["template"]), ("inventory", "91970042", "T1"))
        line = client.bodies(_CREATE)[0]["lines"][0]
        self.assertEqual((line["cinvcode"], line["cbatch"], line["dvdate"]), ("91970042", _LOT, "2026-11-01"))
        self.assertEqual(_routes(client), ["archives/create", "vouchers/create", "vouchers/delete", "archives/delete"])
        flags = next(query for query in sql.seen if "UPDATE dbo.Inventory" in query)
        self.assertIn("bInvQuality = 1", flags)
        self.assertIn("cMassUnit = 3", flags)

    def test_archive_left_is_noted_not_failed(self) -> None:
        client = _shelf_client(U8CoError(409, "u8_rejected", "存货已使用", ""))
        sql = _shelf_sql()
        status, detail = _run(made.purchase_in_shelf_made, client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("留给快照还原", detail)
        self.assertIn("已清零数量现存量 1 行", detail)
        cleared = [query for query in sql.seen if "DELETE FROM dbo.CurrentStock" in query]
        self.assertEqual(len(cleared), 1)
        self.assertIn("cInvCode = N'91970042'", cleared[0])
        self.assertIn("ISNULL(iQuantity, 0) = 0", cleared[0])

    def test_failure_deletes_voucher_then_inventory(self) -> None:
        row = _LOT + "|2026-10-02|2026-11-01|0|3|||"
        client = _shelf_client()
        status, detail = _run(made.purchase_in_shelf_made, client, _shelf_sql(row))
        self.assertEqual(status, "FAIL")
        self.assertIn("存货档案", detail)
        self.assertEqual(_routes(client)[-2:], ["vouchers/delete", "archives/delete"])

    def test_no_template_is_na_without_calls(self) -> None:
        client = _shelf_client()
        status, _detail = _run(made.purchase_in_shelf_made, client, _Sql([("ISNULL(i.bInvQuality, 0) = 0", ["\n"])]))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])


# ---- mo-allocate-fixed-scrap / mo-allocate-aux ----

_FIXED_BEFORE = (
    "10|C1|1|1.000000|3.000000|3.000|0.000|||0.3433||1.000000|1",
    "20|C2|2|2.000000|1.000000|5.000|0.000|||2.1000||1.000000|1",
)
_FIXED_AFTER = (
    "10|C1|1|1.000000|3.000000|3.000|0.000|||1.0300||3.000000|1",
    "20|C2|2|2.000000|1.000000|5.000|0.000|||2.1000||3.000000|1",
)
_AUX_BEFORE = (
    "10|91950007|1|2.345678|1.000000|0.000|0.000|1.000000|2.345678|2.3457|1.00|1.000000|1",
    "20|C2|1|1.000000|1.000000|0.000|0.000|||1.0000||1.000000|1",
)
_AUX_AFTER = (
    "10|91950007|1|2.345678|1.000000|0.000|0.000|1.000000|2.345678|7.0370|3.00|3.000000|1",
    "20|C2|1|1.000000|1.000000|0.000|0.000|||3.0000||3.000000|1",
)


def _block(rows: tuple[str, ...]) -> str:
    return "".join(f"ROW:{row}\n" for row in rows)


def _mo_client(arcs: int) -> _Replay:
    return _Replay({
        _ARC_C: [_ok() for _ in range(arcs)],
        _CREATE: [_ok(id=11, version="10"), _ok(id=21, allocates=2)],
        _VERIFY: [_ok(), _ok()],
        _UPDATE: [_ok(changed=1)],
        _DELETE: [_ok(deleted=True), _ok(deleted=True)],
        _ARC_D: [_ok(deleted=True) for _ in range(arcs)],
    })


def _mo_sql(before: tuple[str, ...], after: tuple[str, ...], extra: list | None = None, gone: str = "0") -> _Sql:
    return _Sql([
        ("DELETE FROM dbo.CurrentStock", _rows("0", "0")),
        # 删除后的回读也含 bom_opcomponent 子句，先于物料清单行的键；挑计量单位组编码也含 FROM (VALUES，先于存货编码的键。
        ("FROM dbo.mom_order AS o", _rows(gone)),
        ("ORDER BY ISNULL(i.bPropertyCheck, 0), i.cInvCode", ["ROW:T1|C1|C2|D1|MT1\n"]),
        ("iNumDecDgt", _rows("4|2")),
        ("ComputationGroup AS x", _rows("ZZ00")),
        ("FROM (VALUES", _rows("91960007", "91950007")),
        ("UPDATE dbo.Inventory SET b", _rows("1", "1")),
        ("SET cGroupCode", _rows("1")),
        ("SET FVFlag = 2", _rows("1")),
        ("FROM dbo.bom_opcomponent AS c WHERE", _rows("1|0.000||||")),
        ("SET AuxUnitCode", _rows("1")),
        ("FROM dbo.mom_moallocate AS a JOIN", [_block(before), _block(after)]),
        ("MIN(d.MoDId)", _rows("77")),
        *(extra or []),
    ])


class MoAllocTests(unittest.TestCase):
    def test_fixed_scrap_cycle(self) -> None:
        client = _mo_client(1)
        sql = _mo_sql(_FIXED_BEFORE, _FIXED_AFTER)
        status, detail = _run(mo.mo_allocate_fixed_scrap, client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("符合算法", detail)
        self.assertIn("自建档案 1 个已删除", detail)
        bom = client.bodies(_CREATE)[0]
        self.assertEqual((bom["type"], bom["head"]["inv_code"]), ("bom", "91960007"))
        self.assertEqual([line["comp_scrap"] for line in bom["lines"]], [3, 5])
        self.assertNotIn("parent_scrap", bom["head"])
        upd = client.bodies(_UPDATE)[0]
        self.assertEqual((upd["id"], upd["lines"]), (21, [{"op": "update", "line_id": 77, "qty": 3}]))
        self.assertEqual(_routes(client), [
            "archives/create", "vouchers/create", "vouchers/verify", "vouchers/create", "vouchers/update",
            "vouchers/delete", "vouchers/verify", "vouchers/delete", "archives/delete",
        ])
        self.assertEqual([body["type"] for body in client.bodies(_DELETE)], ["production_order", "bom"])
        self.assertEqual(client.bodies(_VERIFY)[1]["action"], "unverify")
        self.assertTrue(any("SortSeq = 20" in query and "FVFlag = 2" in query for query in sql.seen))

    def test_aux_cycle_creates_units_and_patches_bom(self) -> None:
        client = _mo_client(5)
        status, detail = _run(mo.mo_allocate_aux, client, _mo_sql(_AUX_BEFORE, _AUX_AFTER))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("SQL 补", detail)
        arcs = [(body["archive"], body["code"]) for body in client.bodies(_ARC_C)]
        self.assertEqual(arcs, [("inventory", "91960007"), ("unit_group", "ZZ00"), ("unit", "ZY00000007"),
                                ("unit", "ZX00000007"), ("inventory", "91950007")])
        self.assertEqual(client.bodies(_ARC_C)[3]["fields"]["changerate"], 2.345678)
        deleted = [(body["archive"], body["code"]) for body in client.bodies(_ARC_D)]
        self.assertEqual(deleted, list(reversed(arcs)))
        line = client.bodies(_CREATE)[0]["lines"][0]
        self.assertEqual((line["inv_code"], line["base_qty_d"]), ("91950007", 1))

    def test_aux_inconsistent_expects_documented_409(self) -> None:
        mixed = (_AUX_BEFORE[0].replace("|1.000000|2.345678|", "|0.426319|2.345678|"), _AUX_BEFORE[1])
        client = _mo_client(5)
        client.answers[_UPDATE] = [U8CoError(409, "state_mismatch", "第 1 行子件 91950007 辅计量用量与主用量不一致（取决于存货的 BOM 展开单位）", "")]
        status, detail = _run(mo.mo_allocate_aux, client, _mo_sql(mixed, mixed))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("辅用量 0.426319", detail)
        self.assertIn("按文档 409", detail)
        self.assertEqual(_routes(client)[-8:-5], ["vouchers/delete", "vouchers/verify", "vouchers/delete"])

    def test_aux_inconsistent_but_changed_or_saved_fails(self) -> None:
        mixed = (_AUX_BEFORE[0].replace("|1.000000|2.345678|", "|0.426319|2.345678|"), _AUX_BEFORE[1])
        changed = (mixed[0], _AUX_BEFORE[1].replace("|1.0000|", "|3.0000|"))
        client = _mo_client(5)
        client.answers[_UPDATE] = [U8CoError(409, "state_mismatch", "辅计量用量与主用量不一致", "")]
        status, detail = _run(mo.mo_allocate_aux, client, _mo_sql(mixed, changed))
        self.assertEqual(status, "FAIL")
        self.assertIn("409 后子件变了", detail)
        status, detail = _run(mo.mo_allocate_aux, _mo_client(5), _mo_sql(mixed, _AUX_AFTER))
        self.assertEqual(status, "FAIL")
        self.assertIn("应 409", detail)

    def test_aux_mixed_rule(self) -> None:
        def row(text: str) -> dict[str, str]:
            return dict(zip(mo.ALLOC_COLS, text.split("|"), strict=True))

        self.assertEqual(mo.aux_mixed([row(_AUX_BEFORE[0]), row(_AUX_BEFORE[1])]), [])
        empty = row(_AUX_BEFORE[0].replace("|1.000000|2.345678|", "||2.345678|"))
        self.assertEqual(mo.aux_mixed([empty])[0][1], "有换算率却没有辅计量用量")
        off = row(_AUX_BEFORE[0].replace("|1.000000|2.345678|", "|0.999990|2.345678|"))
        self.assertEqual(mo.aux_mixed([off])[0][1], "辅计量用量与主用量不一致")

    def test_formula_mismatch_after_update_fails_after_cleanup(self) -> None:
        after = (_FIXED_AFTER[0], _FIXED_AFTER[1].replace("|2.1000|", "|6.3000|"))
        client = _mo_client(1)
        status, detail = _run(mo.mo_allocate_fixed_scrap, client, _mo_sql(_FIXED_BEFORE, after))
        self.assertEqual(status, "FAIL")
        self.assertIn("算法不符", detail)
        self.assertIn("行 20", detail)
        self.assertEqual(client.answers[_DELETE], [])
        self.assertEqual(client.answers[_ARC_D], [])

    def test_static_columns_changed_fails(self) -> None:
        after = (_FIXED_AFTER[0], _FIXED_AFTER[1].replace("|2|2.000000|", "|1|2.000000|"))
        status, detail = _run(mo.mo_allocate_fixed_scrap, _mo_client(1), _mo_sql(_FIXED_BEFORE, after))
        self.assertEqual(status, "FAIL")
        self.assertIn("FVFlag", detail)

    def test_update_refused_cleans_up(self) -> None:
        client = _mo_client(1)
        client.answers[_UPDATE] = [U8CoError(409, "state_mismatch", "第 1 行子件 C2 不能重算", "")]
        status, detail = _run(mo.mo_allocate_fixed_scrap, client, _mo_sql(_FIXED_BEFORE, _FIXED_AFTER))
        self.assertEqual(status, "FAIL")
        self.assertIn("改生产订单数量", detail)
        self.assertEqual(_routes(client)[-4:], ["vouchers/delete", "vouchers/verify", "vouchers/delete", "archives/delete"])

    def test_create_mismatch_is_only_noted(self) -> None:
        before = (_FIXED_BEFORE[0].replace("|0.3433|", "|0.3333|"), _FIXED_BEFORE[1])
        status, detail = _run(mo.mo_allocate_fixed_scrap, _mo_client(1), _mo_sql(before, _FIXED_AFTER))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("新建时与算法不符", detail)

    def test_no_pick_is_na(self) -> None:
        client = _mo_client(0)
        sql = _Sql([("ORDER BY ISNULL(i.bPropertyCheck, 0), i.cInvCode", ["\n"])])
        status, _detail = _run(mo.mo_allocate_aux, client, sql)
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])

    def test_expected_follows_bridge_formula(self) -> None:
        row = dict(zip(mo.ALLOC_COLS, _AUX_AFTER[0].split("|"), strict=True))
        self.assertEqual(mo.expected(row, (4, 2)), (Decimal("7.0370"), Decimal("3.00")))
        fixed = dict(zip(mo.ALLOC_COLS, _FIXED_AFTER[1].split("|"), strict=True))
        self.assertEqual(mo.expected(fixed, (4, 2)), (Decimal("2.1000"), None))
        rework = dict(fixed, fv="1", parent_scrap="50", mo_class="2", line_qty="1")
        self.assertEqual(mo.expected(rework, (4, 2))[0], Decimal("2.1000"))
        self.assertEqual(mo.expected(dict(rework, mo_class="1"), (4, 2))[0], Decimal("4.2000"))


class ObserveWalkTests(unittest.TestCase):
    def _walk(self, observe: bool, details: list[tuple[str, str]]) -> tuple[str, str]:
        spec = CaseSpec("o", "标题", steps=(Step("custom"), Step("custom")), observe=observe)
        env = SimpleNamespace(ctx=SimpleNamespace())
        with mock.patch.object(runner, "stopped", lambda _ctx: False), \
                mock.patch.object(runner, "run_step", mock.Mock(side_effect=details)):
            return runner.walk(env, spec)  # type: ignore[arg-type]

    def test_pass_detail_kept_only_when_observing(self) -> None:
        notes = [("PASS", "观察 1"), ("PASS", "观察 2")]
        self.assertEqual(self._walk(True, list(notes)), ("PASS", "标题；观察 1；观察 2"))
        self.assertEqual(self._walk(False, list(notes)), ("PASS", "标题"))
        self.assertEqual(self._walk(True, [("PASS", "标题"), ("PASS", "")]), ("PASS", "标题"))
        status, detail = self._walk(True, [("PASS", "x" * 900), ("PASS", "y")])
        self.assertEqual((status, len(detail)), ("PASS", runner.OBSERVE_MAX))
        self.assertEqual(self._walk(True, [("PASS", "a"), ("FAIL", "坏")]), ("FAIL", "步骤2 坏"))

    def test_made_cases_observe(self) -> None:
        self.assertTrue(all(spec.observe for spec in made.made_cases() + mo.mo_alloc_cases()))


if __name__ == "__main__":
    unittest.main()
