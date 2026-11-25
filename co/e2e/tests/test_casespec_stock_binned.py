"""库存待实测用例（stock_binned）的登记、SQL 形状和回放核对；以及 sale-out-batch-override 批次属性、
sale-red-invoice 开票数量时点与 iVTid、sale-invoice-advance 发货单生成时点的附加断言。不连接服务器。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import srcless as sl
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import stock_binned as sb
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.wiring import has_edit, edit_sql
from co.e2e.casespec.sale_red_invoice import rs_cases
from co.e2e.casespec.stock_binned_sql import head_rows_sql, lot_prop_sql, pos_rows_sql, stock_binned_samples
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("binned-purchase-in-delete", "binned-other-out-delete", "binned-material-out-delete", "binned-product-in-delete")
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_PIN_PICK = "ROW:01|A01|I1|V1|D1|PT1\n"
_OUT_PICK = "ROW:01|A01|I1|102|D1\n"
_MOUT_PICK = "ROW:0|01|A01|I1|D1\n"
_MO_PICK = "ROW:100|200|01|A01|P1\n"


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
    """按 SQL 里的特征串依次回答（每个键一个队列），没有匹配就报错。"""

    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]

    def __call__(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.answers:
            if key in query and rows:
                return rows.pop(0)
        raise AssertionError("没有准备回答：" + query[:80])


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-02", bag={})


def _ok(**extra: object) -> dict:
    body: dict = {"ok": True}
    body.update(extra)
    return body


def _rows(*values: str) -> list[str]:
    return [f"ROW:{value}\n" for value in values]


def _pin_client() -> _Replay:
    return _Replay({
        "/v1/vouchers/create": [_ok(id=5)],
        "/v1/vouchers/verify": [_ok(), _ok()],
        "/v1/vouchers/delete": [_ok(deleted=True)],
    })


def _pin_sql(psum: tuple, rows: tuple, head: str = "0", havepo: str = "0") -> _Sql:
    return _Sql([
        ("dbo.PurchaseType", [_PIN_PICK]),
        ("bPTHavePO", _rows(havepo)),
        ("InvPositionSum AS p WHERE", _rows(*psum)),
        ("c.cWhCode = N'", _rows("20.0000", "20.0000")),
        ("InvPosition AS p WHERE p.RdID", _rows(*rows)),
        ("h.ID = 5", _rows(head)),
    ])


def _out_client() -> _Replay:
    return _Replay({"/v1/vouchers/create": [_ok(id=6)], "/v1/vouchers/delete": [_ok(deleted=True)]})


def _out_sql(psum: tuple = ("10.0000", "9.0000", "10.0000"), rows: tuple = ("1", "0")) -> _Sql:
    return _Sql([
        ("Rd_Style", [_OUT_PICK]),
        ("InvPositionSum AS p WHERE", _rows(*psum)),
        ("c.cWhCode = N'", _rows("20.0000", "20.0000")),
        ("InvPosition AS p WHERE p.RdID", _rows(*rows)),
        ("h.ID = 6", _rows("0")),
    ])


def _run(op, client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql), mock.patch.object(sb, "td", lambda key: "RD-" + key.rsplit(".", 1)[-1]):
        return op(_env(client), _SPEC, _STEP)


def _bin_sql(pick: tuple[str, str], doc: str, psum: tuple, rows: tuple) -> _Sql:
    return _Sql([
        pick,
        ("InvPositionSum AS p WHERE", _rows(*psum)),
        ("c.cWhCode = N'", _rows("20.0000", "20.0000")),
        ("InvPosition AS p WHERE p.RdID", _rows(*rows)),
        (f"h.ID = {doc}", _rows("0")),
    ])


def _order(client: _Replay) -> list[tuple[str, str, str]]:
    return [(route.rsplit("/", 1)[1], fields["type"], fields.get("action", "")) for route, fields in client.calls]


class StockBinnedRegistryTests(unittest.TestCase):
    def test_registered_with_ops_and_stock_tag(self) -> None:
        names = known_names()
        self.assertEqual(tuple(spec.name for spec in sb.stock_binned_cases()), _NAMES)
        split = next(case for case in sp.stockplus_cases() if case.name == "sale-out-bin-split")
        for spec in sb.stock_binned_cases():
            self.assertEqual(names.count(spec.name), 1, spec.name)
            self.assertTrue(spec.requires_login and spec.routes)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
            self.assertIn("stock", auto_tags(spec))
            self.assertTrue(conflicts(auto_tags(spec), auto_tags(split)))
        self.assertIn("opt:havepo", sb.stock_binned_cases()[0].tags)

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in stock_binned_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        self.assertIn("p.cvouchtype = N'01'", pos_rows_sql("01", "7"))
        self.assertIn("dbo.RdRecord09 AS h WHERE h.ID = 7", head_rows_sql("09", "7"))
        self.assertIn("CONVERT(varchar(19), b.cBatchProperty10, 126)", lot_prop_sql("1", "2"))
        for bad in (lambda: pos_rows_sql("08", "1"), lambda: head_rows_sql("01", "0"), lambda: lot_prop_sql("1", "x")):
            with self.assertRaises(ValueError):
                bad()


class BinnedPurchaseInTests(unittest.TestCase):
    def test_written_at_save_then_rolled_back(self) -> None:
        client = _pin_client()
        sql = _pin_sql(("10.0000", "11.0000", "11.0000", "10.0000"), ("1", "1", "0"))
        status, detail = _run(sb.binned_purchase_in, client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("保存时", detail)
        body = client.calls[0][1]
        self.assertEqual(body["lines"], [{"cinvcode": "I1", "iquantity": 1, "iunitcost": 1, "itaxrate": 13, "cposition": "A01"}])
        self.assertEqual((body["head"]["cwhcode"], body["head"]["cptcode"]), ("01", "PT1"))
        self.assertEqual(_order(client), [
            ("create", "purchase_in", ""), ("verify", "purchase_in", "verify"),
            ("verify", "purchase_in", "unverify"), ("delete", "purchase_in", ""),
        ])

    def test_written_at_verify_is_reported(self) -> None:
        sql = _pin_sql(("10.0000", "10.0000", "11.0000", "10.0000"), ("0", "1", "0"))
        status, detail = _run(sb.binned_purchase_in, _pin_client(), sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("审核时", detail)

    def test_missing_after_verify_fails_after_cleanup(self) -> None:
        client = _pin_client()
        status, detail = _run(sb.binned_purchase_in, client, _pin_sql(("10.0000", "10.0000", "10.0000"), ("0", "0")))
        self.assertEqual(status, "FAIL")
        self.assertIn("审核后", detail)
        self.assertEqual(client.answers["/v1/vouchers/delete"], [])

    def test_position_stock_not_back_fails(self) -> None:
        sql = _pin_sql(("10.0000", "11.0000", "11.0000", "11.0000"), ("1", "1", "0"))
        status, detail = _run(sb.binned_purchase_in, _pin_client(), sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("回到", detail)

    def test_havepo_on_unflippable_is_na_without_calls(self) -> None:
        client = _pin_client()
        sql = _Sql([("dbo.PurchaseType", [_PIN_PICK]), ("bPTHavePO", _rows("1", ""))])
        status, _detail = _run(sb.binned_purchase_in, client, sql)
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])

    def test_havepo_on_flips_then_restores(self) -> None:
        client = _pin_client()
        sql = _pin_sql(("10.0000", "11.0000", "11.0000", "10.0000"), ("1", "1", "0"))
        sql.answers[1] = ("bPTHavePO", _rows("1", "True", "1", "1"))
        status, detail = _run(sb.binned_purchase_in, client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("已写回", detail)
        self.assertEqual(sql.answers[1][1], [])

    def test_havepo_restore_failure_fails(self) -> None:
        sql = _pin_sql(("10.0000", "11.0000", "11.0000", "10.0000"), ("1", "1", "0"))
        sql.answers[1] = ("bPTHavePO", _rows("1", "True", "1", "0"))
        status, detail = _run(sb.binned_purchase_in, _pin_client(), sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("写回", detail)

    def test_verify_refused_still_deletes(self) -> None:
        client = _Replay({
            "/v1/vouchers/create": [_ok(id=5)],
            "/v1/vouchers/verify": [U8CoError(409, "u8_rejected", "货位不能为空")],
            "/v1/vouchers/delete": [_ok(deleted=True)],
        })
        status, detail = _run(sb.binned_purchase_in, client, _pin_sql(("10.0000", "11.0000"), ("1",)))
        self.assertEqual(status, "FAIL")
        self.assertIn("审核采购入库", detail)
        self.assertEqual(_order(client)[-1], ("delete", "purchase_in", ""))


class BinnedOtherOutTests(unittest.TestCase):
    def test_create_delete_and_rollback(self) -> None:
        client = _out_client()
        status, detail = _run(sb.binned_other_out, client, _out_sql())
        self.assertEqual(status, "PASS", detail)
        head = client.calls[0][1]["head"]
        self.assertEqual((head["cwhcode"], head["crdcode"], head["cdepcode"]), ("01", "102", "D1"))
        self.assertEqual(client.calls[0][1]["lines"], [{"cinvcode": "I1", "iquantity": 1, "cposition": "A01"}])
        self.assertEqual(_order(client), [("create", "other_out", ""), ("delete", "other_out", "")])

    def test_missing_position_record_fails_after_delete(self) -> None:
        client = _out_client()
        status, detail = _run(sb.binned_other_out, client, _out_sql(psum=("10.0000", "10.0000"), rows=("0",)))
        self.assertEqual(status, "FAIL")
        self.assertIn("InvPosition", detail)
        self.assertEqual(client.answers["/v1/vouchers/delete"], [])

    def test_nothing_to_pick_is_na(self) -> None:
        client = _out_client()
        status, _detail = _run(sb.binned_other_out, client, _Sql([("Rd_Style", [""])]))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])


class BinnedMaterialOutTests(unittest.TestCase):
    def _client(self) -> _Replay:
        return _Replay({"/v1/vouchers/create": [_ok(id=7)], "/v1/vouchers/delete": [_ok(deleted=True)]})

    def test_create_with_position_and_rollback(self) -> None:
        client = self._client()
        sql = _bin_sql(("ballowAddnewVouch", [_MOUT_PICK]), "7", ("10.0000", "9.0000", "10.0000"), ("1", "0"))
        status, detail = _run(sb.binned_material_out, client, sql)
        self.assertEqual(status, "PASS", detail)
        body = client.calls[0][1]
        self.assertEqual(body["type"], "material_out")
        self.assertEqual((body["head"]["cwhcode"], body["head"]["crdcode"], body["head"]["cdepcode"]),
                         ("01", "RD-material_out_rd", "D1"))
        self.assertEqual(body["lines"], [{"cinvcode": "I1", "iquantity": 1, "cposition": "A01"}])
        self.assertEqual(_order(client), [("create", "material_out", ""), ("delete", "material_out", "")])
        self.assertIn("RdRecord11", head_rows_sql("11", "7"))

    def test_no_position_record_fails_after_delete(self) -> None:
        client = self._client()
        sql = _bin_sql(("ballowAddnewVouch", [_MOUT_PICK]), "7", ("10.0000", "10.0000"), ("0",))
        status, detail = _run(sb.binned_material_out, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("保存后", detail)
        self.assertEqual(client.answers["/v1/vouchers/delete"], [])

    def test_option_on_is_na_without_calls(self) -> None:
        client = self._client()
        status, _detail = _run(sb.binned_material_out, client, _Sql([("ballowAddnewVouch", ["ROW:True|01|A01|I1|D1\n"])]))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])


class BinnedProductInTests(unittest.TestCase):
    def _client(self) -> _Replay:
        return _Replay({"/v1/vouchers/generate": [_ok(id=8)], "/v1/vouchers/delete": [_ok(deleted=True)]})

    def test_generate_with_position_and_rollback(self) -> None:
        client = self._client()
        sql = _bin_sql(("mom_orderdetail", [_MO_PICK]), "8", ("0.0000", "1.0000", "0.0000"), ("1", "0"))
        status, detail = _run(sb.binned_product_in, client, sql)
        self.assertEqual(status, "PASS", detail)
        body = client.calls[0][1]
        self.assertEqual((body["type"], body["id"], body["source_type"]), ("product_in", 100, "production_order"))
        self.assertEqual(body["head"]["crdcode"], "RD-product_in_rd")
        self.assertEqual(body["lines"], [{"source_line_id": 200, "quantity": 1, "cposition": "A01"}])
        self.assertEqual(_order(client), [("generate", "product_in", ""), ("delete", "product_in", "")])

    def test_stock_not_back_fails(self) -> None:
        sql = _bin_sql(("mom_orderdetail", [_MO_PICK]), "8", ("0.0000", "1.0000", "1.0000"), ("1", "0"))
        status, detail = _run(sb.binned_product_in, self._client(), sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("回到", detail)

    def test_nothing_to_pick_is_na(self) -> None:
        client = self._client()
        status, _detail = _run(sb.binned_product_in, client, _Sql([("mom_orderdetail", [""])]))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])


class BatchPropertyTests(unittest.TestCase):
    def _check(self, *cells: str) -> str:
        run = SimpleNamespace(cells=lambda _query: list(cells))
        return sp._lot_prop_problem(run, 3, "21")  # type: ignore[arg-type]

    def test_matching_or_absent_archive_passes(self) -> None:
        self.assertEqual(self._check("LOT2|0|2026-01-01T00:00:00|2026-01-01T00:00:00|1"), "")
        self.assertEqual(self._check("LOT2|0|||0"), "")

    def test_mismatch_and_missing_rows_fail(self) -> None:
        self.assertIn("不一致 2 项", self._check("LOT2|2|2026-01-02T00:00:00|2026-01-01T00:00:00|1"))
        self.assertIn("回读不到", self._check())
        self.assertIn("格式不对", self._check("LOT2|0"))


class RedInvoiceTimingTests(unittest.TestCase):
    def test_kp_checked_after_save_verify_unverify_and_vt(self) -> None:
        steps = rs_cases()[0].steps
        keep = next(i for i, step in enumerate(steps) if step.kind == "keep" and step.code == "rs_inv2")

        def label(step: Step) -> str:
            if step.kind == "pred":
                return f"{step.sql_name} {step.code}"
            return step.route.rsplit("/", 1)[-1] + " " + dict(step.fields).get("action", step.id_from)

        got = [label(step) for step in steps[keep + 1:]]
        self.assertEqual(got[:8], [
            "rs_kp shift:-1:rs_kp", "rs_vt eq:1", "verify verify", "rs_kp shift:-1:rs_kp",
            "delete bag:rs_inv2", "verify unverify", "rs_kp shift:-1:rs_kp", "delete bag:rs_inv2",
        ])
        self.assertIn("rs_kp same:rs_kp", got[8:])
        self.assertTrue(has_edit("rs_vt"))
        query = edit_sql("rs_vt", "100", {})
        self.assertIn("h.SBVID = 100", query)
        self.assertEqual(sql_block_reason(query), "")


class AdvanceInvoiceTimingTests(unittest.TestCase):
    def test_no_dispatch_after_save_fails(self) -> None:
        answers = [_ok(id=7), _ok(), _ok(deleted=True), U8CoError(404, "not_found", "单据不存在")]
        client = SimpleNamespace(calls=[])

        def call(route: str, fields: dict) -> dict:
            client.calls.append(route)
            got = answers.pop(0)
            if isinstance(got, U8CoError):
                raise got
            return got

        client.call = call
        replies = iter(["ROW:FALSE|C1|XS|10|INV1|0.00", "", ""])
        spec = next(case for case in sl.srcless_cases() if case.name == "sale-invoice-advance")
        with mock.patch.object(sl, "run_guarded", side_effect=lambda *_a: next(replies)):
            got = sl.srcless_create(_env(client), spec, spec.steps[0])
        self.assertEqual(got[0], "FAIL")
        self.assertIn("复核前", got[1])
        self.assertIn("/v1/vouchers/delete", client.calls)


if __name__ == "__main__":
    unittest.main()
