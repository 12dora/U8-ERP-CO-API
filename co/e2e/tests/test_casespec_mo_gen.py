"""生产订单参照销售订单生成（ATO）用例 `mo-generate-ato` / `mo-generate-bad`：登记、固定数据、展开算法、
判定纯函数，以及用假的 _Run 回放整条流程。不连接服务器。"""

from __future__ import annotations

import unittest
from collections.abc import Callable
from decimal import Decimal

from co.e2e import testdata
from co.e2e.casespec import mo_gen_ato as ato
from co.e2e.casespec import ops_run
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.mo_gen_alloc import (
    AtoFixture,
    BomRow,
    alloc_problem,
    cbom_explosion,
    gen_body,
    load_fixture,
    parse_bom,
    so_keys,
    std_explosion,
)
from co.e2e.casespec.mo_gen_ato_sql import MO_COLS, mo_gen_samples
from co.e2e.casespec import mo_gen_bad as bad_case
from co.e2e.casespec.mo_gen_bad import MO_GEN_OPS, mo_gen_cases, offline_bodies, refusal_bodies
from co.e2e.casespec.stock_plus import _Fail, _NotApplicable
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

FX = AtoFixture(500, 501, 502, 900, "91030004", "91030006", "91030001", "91030005", "01", "SD003")
MODEL, OPT_A, OPT_B, CLS, BASE, STD = "91030004", "91030001", "91030002", "91030003", "91030005", "91030006"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _bom(cls_optional: bool = False) -> list[BomRow]:
    one = Decimal(1)
    return [
        BomRow(MODEL, BASE, "3", False, one, one),
        BomRow(MODEL, CLS, "4", cls_optional, one, one),
        BomRow(CLS, OPT_A, "3", True, one, one),
        BomRow(CLS, OPT_B, "3", True, one, one),
    ]


class RegistrationTests(unittest.TestCase):
    def test_cases_are_registered_with_ops(self) -> None:
        for spec in mo_gen_cases():
            with self.subTest(spec.name):
                self.assertEqual(known_names().count(spec.name), 1)
                self.assertTrue(spec.requires_login)
                self.assertIn("/v1/vouchers/generate", spec.routes)
                self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertEqual(set(MO_GEN_OPS), {spec.steps[0].sql_name for spec in mo_gen_cases()})

    def test_cases_do_not_overlap(self) -> None:
        first, second = (auto_tags(spec) for spec in mo_gen_cases())
        self.assertTrue(first & second)
        self.assertIn("new:production_order", first)

    def test_samples_pass_the_guard(self) -> None:
        for name, query in mo_gen_samples():
            with self.subTest(name):
                self.assertEqual(sql_block_reason(query), "")
                self.assertIn("N'ROW:'", query)


class FixtureTests(unittest.TestCase):
    def tearDown(self) -> None:
        testdata.use(support.sample_testdata())

    def test_example_keys_load(self) -> None:
        fx = load_fixture()
        self.assertIsNotNone(fx)
        assert fx is not None
        self.assertEqual((fx.model, fx.std, fx.option, fx.base), (MODEL, STD, OPT_A, BASE))
        self.assertNotEqual(fx.plain, fx.cbom_line)

    def test_missing_keys_mean_no_fixture(self) -> None:
        data = support.sample_testdata()
        del data["ato"]["cbom_id"]
        testdata.use(data)
        self.assertIsNone(load_fixture())
        with self.assertRaises(_NotApplicable):
            ato.fixture()

    def test_bad_values_are_refused(self) -> None:
        data = support.sample_testdata()
        data["ato"]["so_id"] = "0"
        testdata.use(data)
        with self.assertRaises(ValueError):
            load_fixture()


class ExplosionTests(unittest.TestCase):
    def test_plain_line_takes_only_the_mandatory_part(self) -> None:
        # 选项类（虚拟件）下的选项都选配：U8 一个也不带，虚拟件本身也不进子件表。
        self.assertEqual(std_explosion(_bom(), MODEL, Decimal(2)), {BASE: Decimal(2)})
        self.assertEqual(std_explosion(_bom(cls_optional=True), MODEL, Decimal(2)), {BASE: Decimal(2)})

    def test_virtual_children_that_are_not_optional_roll_up(self) -> None:
        rows = _bom() + [BomRow(CLS, "X1", "3", False, Decimal(2), Decimal(1))]
        self.assertEqual(std_explosion(rows, MODEL, Decimal(3)), {BASE: Decimal(3), "X1": Decimal(6)})

    def test_loops_are_refused(self) -> None:
        rows = [BomRow("A", "B", "4", False, Decimal(1), Decimal(1)), BomRow("B", "A", "4", False, Decimal(1), Decimal(1))]
        with self.assertRaises(ValueError):
            std_explosion(rows, "A", Decimal(1))

    def test_parse_bom(self) -> None:
        rows = parse_bom([f"{MODEL}|{CLS}|4|0|1.000000|1.000000"])
        self.assertEqual(rows, [BomRow(MODEL, CLS, "4", False, Decimal(1), Decimal(1))])
        with self.assertRaises(ValueError):
            parse_bom(["a|b"])

    def test_configured_line_takes_the_chosen_leaves(self) -> None:
        cells = [f"{BASE}|3|1.000000|1.000000|0", f"{OPT_A}|3|1.000000|1.000000|0", f"{CLS}|4|1|1|0", "X|3|1|1|7"]
        self.assertEqual(cbom_explosion(cells, Decimal(1)), {BASE: Decimal(1), OPT_A: Decimal(1)})

    def test_alloc_problem(self) -> None:
        want = {BASE: Decimal(2)}
        self.assertEqual(alloc_problem(want, {BASE: Decimal("2.000000")}), "")
        self.assertIn("子件应为", alloc_problem(want, {BASE: Decimal(2), OPT_A: Decimal(2)}))
        self.assertIn("数量", alloc_problem(want, {BASE: Decimal(3)}))


class JudgeTests(unittest.TestCase):
    VIEW = {"did": "502", "code": "SO01", "seq": "2", "so_type": "0", "so_did": "", "so_code": "", "so_seq": ""}

    def test_so_keys(self) -> None:
        self.assertEqual(so_keys(self.VIEW, True), ("1", "502", "SO01", "2"))
        self.assertEqual(so_keys(self.VIEW, False), ("0", "", "", ""))

    def _row(self, **changes: str) -> dict[str, str]:
        row = dict(zip(MO_COLS, ["1", "7001", "501", "1", "1", "0", "0", "", "", "", STD, "2.000000", "SD003", "01"]))
        row.update(changes)
        return row

    def test_row_problem(self) -> None:
        want = (501, 2, False)
        self.assertEqual(ato.row_problem(self._row(), FX, self.VIEW, want), "")
        self.assertIn("ord_flag", ato.row_problem(self._row(ord_flag="0"), FX, self.VIEW, want))
        self.assertIn("数量", ato.row_problem(self._row(qty="3"), FX, self.VIEW, want))
        self.assertIn("inv", ato.row_problem(self._row(inv=MODEL), FX, self.VIEW, want))
        cbom = self._row(inv=MODEL, orderdid="502", cbom="900", so_type="1", so_did="502", so_code="SO01", so_seq="2", qty="1")
        self.assertEqual(ato.row_problem(cbom, FX, self.VIEW, (502, 1, True)), "")
        self.assertIn("需求跟踪", ato.row_problem(dict(cbom, so_type="0"), FX, self.VIEW, (502, 1, True)))

    def test_details_problem(self) -> None:
        good = {"lines": 2, "details": [{"source_line_id": 501, "cust_bom": False}, {"source_line_id": 502, "cust_bom": True}]}
        self.assertEqual(ato.details_problem(good, FX), "")
        swapped = dict(good, details=list(reversed(good["details"])))
        self.assertIn("cust_bom", ato.details_problem(swapped, FX))
        self.assertIn("lines 2", ato.details_problem({"lines": 1, "details": []}, FX))

    def test_ready_problem(self) -> None:
        views = {501: {"left": "5", "cbom": "0"}, 502: {"left": "3", "cbom": "900"}}
        self.assertEqual(ato.ready_problem(FX, views), "")
        self.assertIn("没有", ato.ready_problem(FX, {501: views[501]}))
        self.assertIn("不够", ato.ready_problem(FX, {**views, 501: {"left": "2", "cbom": "0"}}))
        self.assertIn("选配主键", ato.ready_problem(FX, {**views, 502: {"left": "3", "cbom": "0"}}))

    def test_ready_problem_takes_a_null_cbom_as_none(self) -> None:
        # 没有选配的行 iCusBomID 是 NULL；回读成空串时不能当成「不是数」。
        views = {501: {"left": "5", "cbom": ""}, 502: {"left": "3", "cbom": "900"}}
        self.assertEqual(ato.ready_problem(FX, views), "")
        self.assertIn("选配主键", ato.ready_problem(FX, {**views, 502: {"left": "3", "cbom": ""}}))

    def test_view_sql_turns_null_cbom_into_zero(self) -> None:
        from co.e2e.casespec.mo_gen_ato_sql import gen_rows_sql

        self.assertIn("ISNULL(v.cBomId, 0)", gen_rows_sql(500))


class BodyTests(unittest.TestCase):
    def test_generate_body(self) -> None:
        body = gen_body(FX, [(501, 2), (502, 1)], dry=True)
        self.assertEqual(body["type"], "production_order")
        self.assertEqual(body["source_type"], "sale_order")
        self.assertEqual(body["id"], 500)
        self.assertEqual(body["head"], {"mo_type": "01"})
        self.assertEqual([row["source_line_id"] for row in body["lines"]], [501, 502])
        self.assertTrue(all(row["dept_code"] == "SD003" for row in body["lines"]))
        self.assertIs(body["dry_run"], True)
        self.assertNotIn("dry_run", gen_body(FX, [(501, 1)]))

    def test_offline_bodies(self) -> None:
        bad = offline_bodies(FX, 601)
        self.assertEqual([status for _l, _b, status, _t in bad], [400, 400, 400, 400])
        no_type, unknown, dup, foreign = (body for _l, body, _s, _t in bad)
        self.assertEqual(no_type["head"], {})
        self.assertIn("cfree1", unknown["lines"][0])
        self.assertEqual([row["source_line_id"] for row in dup["lines"]], [501, 501])
        self.assertEqual((foreign["id"], foreign["lines"][0]["source_line_id"]), (500, 601))

    def test_refusal_bodies_skip_only_the_missing_pick(self) -> None:
        bads, skipped = refusal_bodies(FX, (600, 601), (700, 701))
        self.assertEqual([(body["id"], status) for _l, body, status, _t in bads], [(600, 409), (700, 409)])
        self.assertEqual(bads[1][3], ("销售订单未审核",))
        self.assertEqual(skipped, [])
        bads, skipped = refusal_bodies(FX, (600, 601), None)
        self.assertEqual([label for label, _b, _s, _t in bads], ["非 ATO 模型"])
        self.assertEqual(len(skipped), 1)


class FakeRun:
    """回放 mo-generate-ato：生成前后两行的已下达数量、生产订单、子件；删除后数量不退回。"""

    def __init__(self, over_status: int = 409) -> None:
        self.imo = {501: Decimal(0), 502: Decimal(0)}
        self.mo = 0
        self.undo: list[tuple[str, dict]] = []
        self.sent: list[dict] = []
        self.over_status = over_status
        self.no_open = False
        self.over_flag = "0"

    def send(self, route: str, extra: dict) -> tuple[int, dict]:
        self.sent.append(extra)
        if route.endswith("delete"):
            return 200, {"deleted": True}
        if extra.get("dry_run"):
            return 200, {"dry_run": True, "mode": "validate"}
        if len(extra["lines"]) == 1:
            return self.over_status, {"code": "state_mismatch", "message": "第 1 行：超过可生单数量 3"}
        self.mo = 8000
        self.imo = {501: Decimal(2), 502: Decimal(1)}
        details = [{"source_line_id": 501, "cust_bom": False}, {"source_line_id": 502, "cust_bom": True}]
        return 200, {"id": 8000, "code": "MO1", "lines": 2, "allocates": 3, "details": details}

    def ok(self, route: str, extra: dict, what: str) -> dict:
        status, payload = self.send(route, extra)
        if status != 200:
            raise _Fail(what)
        return payload

    def drop(self, kind: str, doc: int) -> None:
        self.undo.append(("/v1/vouchers/delete", {"type": kind, "id": doc}))

    def settle(self, count: int) -> None:
        for _ in range(count):
            self.ok(*self.undo.pop(), "删除")

    def cell(self, query: str) -> str:
        cells = self.cells(query)
        return cells[0] if cells else ""

    def pick(self, query: str, count: int) -> list[str] | None:
        parts = self.cell(query).split("|") if self.cell(query) else []
        return parts if len(parts) == count else None

    def _replies(self) -> list[tuple[str, Callable[[], list[str]]]]:
        """按查询里的标记回放（按顺序取第一个命中的）。"""
        return [
            ("V_SA_GenOrdData_ForMO", lambda: ["501|5.000000|0|0|0||||SO01|1", "502|3.000000|0|900|0||||SO01|2"]),
            ("iMoQuantity", lambda: [f"{line}|{qty}" for line, qty in sorted(self.imo.items())]),
            ("MAX(o.MoId)", lambda: [str(self.mo or 7999)]),
            ("RlsOverPlanFlag", lambda: [self.over_flag]),
            ("MAX(d.MoDId)", lambda: ["7002" if self.mo else "7000"]),
            ("SO_SODetails AS d WHERE d.ID <>", lambda: ["601"]),
            ("bATOModel, 0) = 0", lambda: ["600|602"]),
            ("cVerifier, N'') = N''", lambda: [] if self.no_open else ["700|701"]),
            ("o.MoId >", lambda: [str(self.mo)] if self.mo else []),
            ("mom_moallocate", lambda: [f"7001|{BASE}|2.000000", f"7002|{BASE}|1.000000", f"7002|{OPT_A}|1.000000"]),
            ("mom_orderdetail AS d LEFT JOIN", lambda: [
                f"1|7001|501|1|1|0|0||||{STD}|2.000000|SD003|01",
                f"2|7002|502|1|1|900|1|502|SO01|2|{MODEL}|1.000000|SD003|01",
            ]),
            ("bom_cbomcomponent", lambda: [f"{BASE}|3|1.000000|1.000000|0", f"{OPT_A}|3|1.000000|1.000000|0"]),
            (f"N'{STD}'", lambda: [f"{STD}|{BASE}|3|0|1|1"]),
        ]

    def cells(self, query: str) -> list[str]:
        for marker, reply in self._replies():
            if marker in query:
                return reply()
        raise AssertionError("没有回放的查询：" + query[:80])


class FakeBadRun(FakeRun):
    """mo-generate-bad：按正文回桥的拒绝；no_open 时挑不到未审核订单。"""

    def send(self, route: str, extra: dict) -> tuple[int, dict]:
        self.sent.append(extra)
        line = extra["lines"][0]
        if not extra["head"]:
            return 400, {"message": "缺少字段 mo_type"}
        if "cfree1" in line:
            return 400, {"message": "不能设置字段 cfree1"}
        if len(extra["lines"]) == 2:
            return 400, {"message": "来源明细重复"}
        if extra["id"] == 600:
            return 409, {"message": "第 1 行：存货不是 ATO 模型，请用 vouchers/create 新增生产订单"}
        if extra["id"] == 700:
            return 409, {"message": "销售订单未审核"}
        if line["source_line_id"] == 601:
            return 400, {"message": "第 1 行：明细行不存在"}
        return 200, {"id": 9000}


class BadFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        testdata.use(dict(support.sample_testdata(), ato={
            "so_id": 500, "line_plain": 501, "line_cbom": 502, "cbom_id": 900, "model": MODEL, "std": STD, "option": OPT_A,
            "base": BASE, "mo_type": "01", "dept": "SD003"}))

    def tearDown(self) -> None:
        testdata.use(support.sample_testdata())

    def test_all_refusals(self) -> None:
        run = FakeBadRun()
        status, note = bad_case._flow(run)  # type: ignore[arg-type]
        self.assertEqual(status, "PASS")
        self.assertEqual(len(run.sent), 6)
        self.assertIn("未审核 409", note)

    def test_missing_unaudited_pick_skips_only_that_check(self) -> None:
        run = FakeBadRun()
        run.no_open = True
        status, note = bad_case._flow(run)  # type: ignore[arg-type]
        self.assertEqual(status, "PASS")
        self.assertEqual(len(run.sent), 5)
        self.assertIn("略过", note)


class FlowTests(unittest.TestCase):
    def setUp(self) -> None:
        testdata.use(dict(support.sample_testdata(), ato={
            "so_id": 500, "line_plain": 501, "line_cbom": 502, "cbom_id": 900, "model": MODEL, "std": STD, "option": OPT_A,
            "base": BASE, "mo_type": "01", "dept": "SD003"}))

    def tearDown(self) -> None:
        testdata.use(support.sample_testdata())

    def test_full_flow_passes_and_records_the_write_back(self) -> None:
        run = FakeRun()
        status, note = ato._flow(run)  # type: ignore[arg-type]
        self.assertEqual(status, "PASS")
        self.assertIn("超量 4 个 409", note)
        self.assertIn("未退回", note)
        self.assertEqual(run.undo, [])
        over = run.sent[2]
        self.assertEqual(over["lines"], [{"source_line_id": 501, "quantity": 4, "dept_code": "SD003"}])

    def test_over_quantity_must_be_refused(self) -> None:
        with self.assertRaises(_Fail):
            ato._flow(FakeRun(over_status=400))  # type: ignore[arg-type]

    def test_over_quantity_is_only_a_dry_run_when_the_account_allows_it(self) -> None:
        run = FakeRun(over_status=500)
        run.over_flag = "1"
        status, note = ato._flow(run)  # type: ignore[arg-type]
        self.assertEqual(status, "PASS")
        self.assertIs(run.sent[2]["dry_run"], True)
        self.assertEqual(run.sent[2]["lines"][0]["quantity"], 4)
        self.assertIn("允许超计划下达，预演 200", note)

    def test_over_problem(self) -> None:
        ok_dry = {"dry_run": True, "mode": "validate"}
        self.assertEqual(ato.over_problem(True, 200, ok_dry), "")
        self.assertIn("预演", ato.over_problem(True, 409, {"message": "第 1 行：超过可生单数量 3"}))
        self.assertEqual(ato.over_problem(False, 409, {"message": "第 1 行：超过可生单数量 3"}), "")
        self.assertIn("409", ato.over_problem(False, 200, {"id": 1}))


if __name__ == "__main__":
    unittest.main()
