"""无来源新增、产成品入库参照生产订单用例的登记、正文、挑单 SQL 和自定义步骤。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError
from co.e2e.casespec import srcless as sl
from co.e2e.casespec import srcless_sql as sql
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.ops_run import has_op
from co.e2e.casespec.tags import auto_tags
from co.e2e.tests import support

_NAMES = (
    "dispatch-nosrc", "sale-invoice-advance", "sale-return-create-free", "arrival-nosrc", "purchase-return-create-free",
    "material-out-nosrc",
    "product-in-from-mo", "product-in-mo-inspect",
)



_RED_LOADED = {
    "ok": True, "head": {"bReturnFlag": "1", "cVouchType": "05"}, "lines": [{"iQuantity": "-1.0000000000", "iCorID": "0"}],
}


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
        got = self.answers.pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got


def _env(client: _Replay) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-29", bag={})


def _spec(name: str):
    return next(spec for spec in sl.srcless_cases() if spec.name == name)


def _rows(*cells: str):
    answers = iter(["ROW:" + cell if cell else "" for cell in cells])
    return mock.patch.object(sl, "run_guarded", side_effect=lambda *_a: next(answers))


class SrclessTests(unittest.TestCase):
    def test_names_ops_and_tags(self) -> None:
        names = known_names()
        self.assertEqual(tuple(spec.name for spec in sl.srcless_cases()), _NAMES)
        for spec in sl.srcless_cases():
            self.assertEqual(names.count(spec.name), 1, spec.name)
            self.assertTrue(spec.optional)
            self.assertTrue(has_op(spec.steps[0].sql_name))
            self.assertTrue(auto_tags(spec), spec.name)

    def test_pick_sql_reads_the_option(self) -> None:
        self.assertIn("o.cName = N'bMustSO_ptxs'", sql.sale_pick_sql())
        self.assertIn("o.cName = N'bPTHavePO'", sql.arrival_pick_sql())
        self.assertIn("o.cName = N'ballowAddnewVouch'", sql.material_pick_sql())
        self.assertIn("ISNULL(d.QcFlag, 0) = 1", sql.mo_pick_sql(True))
        self.assertIn(">= 2", sql.mo_pick_sql(False))
        self.assertIn("d.MoDId = 42;", sql.mo_qty_sql(42))
        with self.assertRaises(ValueError):
            sql.mo_qty_sql("1;--")  # type: ignore[arg-type]

    def test_forbidden_option_expects_409_with_name(self) -> None:
        refused = U8CoError(409, "state_mismatch", "账套设置了普通销售必有订单（SA.bMustSO_ptxs），不能无来源录入发货单")
        client = _Replay([refused])
        with _rows("TRUE|C1|XS|10|INV1|8.90"):
            got = sl.srcless_create(_env(client), _spec("dispatch-nosrc"), _spec("dispatch-nosrc").steps[0])
        self.assertEqual(got[0], "PASS", got)
        route, fields = client.calls[0]
        self.assertEqual(route, "/v1/vouchers/create")
        self.assertEqual(fields["head"]["ccuscode"], "C1")
        self.assertEqual(fields["lines"][0], {"cwhcode": "10", "cinvcode": "INV1", "iquantity": 1, "itaxunitprice": 8.9})

    def test_forbidden_but_created_is_deleted_and_fails(self) -> None:
        client = _Replay([{"ok": True, "id": 5}, {"ok": True, "deleted": True}])
        spec = _spec("material-out-nosrc")
        with _rows("true|14|INV2|D01"):
            got = sl.srcless_create(_env(client), spec, spec.steps[0])
        self.assertEqual(got[0], "FAIL")
        self.assertEqual(client.calls[1][0], "/v1/vouchers/delete")
        head = client.calls[0][1]["head"]
        self.assertEqual((head["cwhcode"], head["crdcode"]), ("14", support.sample_testdata()["stock"]["material_out_rd"]))
        self.assertEqual(head["cdepcode"], "D01")

    def test_rd_testdata_declared(self) -> None:
        self.assertEqual(_spec("material-out-nosrc").data, ("stock.material_out_rd",))
        for name in ("product-in-from-mo", "product-in-mo-inspect"):
            self.assertEqual(_spec(name).data, ("stock.product_in_rd",), name)
        free = ("dispatch-nosrc", "sale-invoice-advance", "sale-return-create-free", "arrival-nosrc",
                "purchase-return-create-free")
        for name in free:
            self.assertEqual(_spec(name).data, (), name)

    def test_open_option_runs_the_happy_path(self) -> None:
        answers = [{"ok": True, "id": 7, "dispatch_id": 70}, {"ok": True}, {"ok": True, "deleted": True}]
        client = _Replay(answers + [U8CoError(404, "not_found", "单据不存在")])
        spec = _spec("sale-invoice-advance")
        with _rows("FALSE|C1|XS|10|INV1|0.00", "70", ""):
            got = sl.srcless_create(_env(client), spec, spec.steps[0])
        self.assertEqual(got[0], "PASS", got)
        self.assertNotIn("itaxunitprice", client.calls[0][1]["lines"][0])
        self.assertEqual([call[0] for call in client.calls[1:]], ["/v1/vouchers/load", "/v1/vouchers/delete", "/v1/vouchers/load"])

    def test_arrival_sends_department_and_purchase_type(self) -> None:
        self.assertIn("p.bDepEnd = 1", sql.arrival_pick_sql())
        self.assertIn("dbo.PurchaseType", sql.arrival_pick_sql())
        refused = U8CoError(409, "state_mismatch", "账套设置了普通业务必有订单（PU.bPTHavePO），不能无来源录入到货单")
        client = _Replay([refused])
        spec = _spec("arrival-nosrc")
        with _rows("1|V1|INV3||D01|PT1"):
            got = sl.srcless_create(_env(client), spec, spec.steps[0])
        self.assertEqual(got[0], "PASS", got)
        head = client.calls[0][1]["head"]
        self.assertEqual((head["cvencode"], head["cdepcode"], head["cptcode"]), ("V1", "D01", "PT1"))
        self.assertNotIn("cwhcode", client.calls[0][1]["lines"][0])

    def test_purchase_return_ignores_the_option(self) -> None:
        # 选项值为真也直接新建：新建 → 读取 → 删除 → 读取 404；正文是红字用的蓝字同款（数量取正，桥写负）。
        answers = [{"ok": True, "id": 8}, {"ok": True}, {"ok": True, "deleted": True}]
        client = _Replay(answers + [U8CoError(404, "not_found", "单据不存在")])
        spec = _spec("purchase-return-create-free")
        with _rows("1|V1|INV3||D01|PT1"):
            got = sl.srcless_create(_env(client), spec, spec.steps[0])
        self.assertEqual(got[0], "PASS", got)
        route, fields = client.calls[0]
        self.assertEqual((route, fields["type"]), ("/v1/vouchers/create", "purchase_return"))
        self.assertEqual(fields["lines"], [{"cinvcode": "INV3", "iquantity": 1, "ioritaxcost": 1}])
        self.assertEqual([call[0] for call in client.calls[1:]], ["/v1/vouchers/load", "/v1/vouchers/delete", "/v1/vouchers/load"])
        self.assertEqual({"new:arrival", "new:purchase_return", "w:purchase_return"} <= set(spec.tags), True)

    def test_sale_return_ignores_the_option(self) -> None:
        # 选项值为真也直接新建（红字不受 SA.bMustSO_ptxs 约束）：新建 → 读取 → 删除 → 读取 404；数量取正，桥写负。
        answers = [{"ok": True, "id": 9}, _RED_LOADED, {"ok": True, "deleted": True}]
        client = _Replay(answers + [U8CoError(404, "not_found", "单据不存在")])
        spec = _spec("sale-return-create-free")
        with _rows("TRUE|C1|XS|10|INV1|0.00|D01"):
            got = sl.srcless_create(_env(client), spec, spec.steps[0])
        self.assertEqual(got[0], "PASS", got)
        route, fields = client.calls[0]
        self.assertEqual((route, fields["type"]), ("/v1/vouchers/create", "sale_return"))
        self.assertEqual(fields["head"]["cdepcode"], "D01")
        self.assertEqual(fields["lines"], [{"cwhcode": "10", "cinvcode": "INV1", "iquantity": 1}])
        self.assertEqual([call[0] for call in client.calls[1:]], ["/v1/vouchers/load", "/v1/vouchers/delete", "/v1/vouchers/load"])
        self.assertTrue({"new:dispatch", "new:sale_return", "w:sale_return"} <= set(spec.tags))

    def test_sale_return_loaded_shape(self) -> None:
        # 读取的形态不对（蓝字数量、挂了订单行）判 FAIL，单据照样删除。
        bad = {"ok": True, "head": {"bReturnFlag": "1", "cVouchType": "05"}, "lines": [{"iQuantity": "1", "iSOsID": "5"}]}
        for loaded, word in ((bad, "数量"), ({"ok": True, "head": {"cVouchType": "05"}, "lines": []}, "红字")):
            client = _Replay([{"ok": True, "id": 9}, loaded, {"ok": True, "deleted": True}, U8CoError(404, "not_found", "x")])
            spec = _spec("sale-return-create-free")
            with _rows("TRUE|C1|XS|10|INV1|0.00|D01"):
                got = sl.srcless_create(_env(client), spec, spec.steps[0])
            self.assertEqual(got[0], "FAIL", got)
            self.assertIn(word, got[1])
            self.assertEqual(client.calls[2][0], "/v1/vouchers/delete")
        linked = {"ok": True, "head": {"breturnflag": "True", "cvouchtype": "05"},
                  "lines": [{"iquantity": "-1.000000", "isosid": "5"}]}
        self.assertIn("isosid", sl._return_shape(linked))
        self.assertEqual(sl._return_shape(_RED_LOADED), "")

    def test_no_master_data_is_na(self) -> None:
        spec = _spec("arrival-nosrc")
        with _rows(""):
            got = sl.srcless_create(_env(_Replay([])), spec, spec.steps[0])
        self.assertEqual(got[0], "N/A")

    def test_product_in_from_mo_checks_qualified_qty(self) -> None:
        client = _Replay([{"ok": True, "id": 9}, {"ok": True, "deleted": True}, U8CoError(404, "not_found", "x")])
        spec = _spec("product-in-from-mo")
        with _rows("100|200|10|3.000000", "3.000000", "4.000000", "3.000000"):
            got = sl.product_in_mo(_env(client), spec, spec.steps[0])
        self.assertEqual(got[0], "PASS", got)
        route, fields = client.calls[0]
        self.assertEqual(route, "/v1/vouchers/generate")
        self.assertEqual((fields["id"], fields["source_type"]), (100, "production_order"))
        self.assertEqual(fields["lines"], [{"source_line_id": 200, "quantity": 1}])
        self.assertEqual(fields["head"]["crdcode"], support.sample_testdata()["stock"]["product_in_rd"])

    def test_inspect_line_expects_409(self) -> None:
        client = _Replay([U8CoError(409, "state_mismatch", "生产订单行需要检验（QcFlag=1，库存选项 ST.bQuality）")])
        spec = _spec("product-in-mo-inspect")
        with _rows("100|200|10|0.000000"):
            got = sl.product_in_mo_inspect(_env(client), spec, spec.steps[0])
        self.assertEqual(got[0], "PASS", got)


if __name__ == "__main__":
    unittest.main()
