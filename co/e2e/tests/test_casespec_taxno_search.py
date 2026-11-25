"""数电号搜索、核销筛选、字段权限独占（taxno_search、taxno_auth）的登记与回放。不连接服务器。"""

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.cases import Operator
from co.e2e.casespec import ops_run
from co.e2e.casespec import col_auth as ca
from co.e2e.casespec import taxno_search as tn
from co.e2e.casespec import taxno_auth as ta
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import PERM, SEARCH
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.tags import auto_tags, is_exclusive
from co.e2e.testdata import DataMissing
from co.e2e.tests import support

_SEARCH_NAMES = ("vouchers-search-taxno", "vouchers-search-settled-type", "vouchers-search-settled")
_AUTH_NAMES = ("col-auth-mask-sale-invoice-taxno", "col-auth-mask-sale-invoice-amount")
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_TAX = "99000000000000000001"
_CODE = "C900001"
_OP = Operator("801", "2026", "op001", "pw")
_PERM = Operator("801", "2026", "op-perm", "pw-perm")
_FIELD = "cDefine10"
_TAX_PLAN = ta.tax_plan(_FIELD)


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _env() -> SimpleNamespace:
    return SimpleNamespace(client=object(), bag={"perm_cache_s": "0"}, today="2026-09-28", operator=_OP,
                           ctx=SimpleNamespace(needles=[]), year="2026")


def _perm(env: object) -> Operator:
    needles = getattr(getattr(env, "ctx", None), "needles", None)
    if isinstance(needles, list) and _PERM.password not in needles:
        needles.append(_PERM.password)
    return _PERM


class _Search:
    def __init__(self) -> None:
        self.pages: dict[tuple, tuple[int, dict]] = {}
        self.calls: list[dict] = []

    def add(self, body: dict, status: int, payload: dict) -> None:
        key = tuple(sorted((k, str(v)) for k, v in body.items()))
        self.pages[key] = (status, payload)

    def __call__(self, _env: object, route: str, body: dict) -> tuple[int, dict]:
        self.calls.append(dict(body))
        key = tuple(sorted((k, str(v)) for k, v in body.items()))
        if key in self.pages:
            return self.pages[key]
        return 200, {"ok": True, "items": []}


def _run(op: str, search: _Search, **patches: object) -> tuple[str, str]:
    kw = {"send": search, **patches}
    with mock.patch.multiple(tn, **kw):
        return tn.TAXNO_OPS[op](_env(), _SPEC, _STEP)  # type: ignore[arg-type]


class _AuthBridge:
    """假桥：插入字段权限行后按 mask / mode 决定遮蔽或泄漏。"""

    def __init__(self, plan: ca.Plan, mask: bool = True, supervisor: bool = False, mode: str = "",
                 **db: str) -> None:
        self.plan = plan
        self.mask = mask
        self.supervisor = supervisor
        self.switch = db.get("switch", "1")
        self.existing = db.get("existing", "0")
        self.mode = mode
        self.denied = False
        self.calls: list[str] = []
        self.bodies: list[dict] = []

    def row(self, _env: object, _spec: object, query: str) -> list[str]:
        if "INSERT INTO AA_ColumnAuth" in query:
            self.denied = True
            self.calls.append("insert")
            return ["OK", str(len(self.plan.fields))]
        if "DELETE a FROM AA_ColumnAuth" in query:
            self.denied = False
            self.calls.append("delete")
            return [str(len(self.plan.fields)), "0"]
        if "AA_BusObject_base" in query:
            return ["1", self.switch]
        return [self.existing]

    def send(self, _env: object, route: str, body: dict) -> tuple[int, dict]:
        self.bodies.append(dict(body))
        if route == ca.SNAP:
            return 200, self._snap()
        if route == ca.LOAD:
            return 200, self._load()
        return self._search(body)

    def ok(self, env: object, route: str, body: dict, _what: str) -> dict:
        return self.send(env, route, body)[1]

    def _hide(self) -> bool:
        return self.denied and self.mask

    def _snap(self) -> dict:
        cols = {self.plan.key: list(self.plan.fields)} if self.denied else {}
        return {"supervisor": self.supervisor, "columns": cols}

    def _load(self) -> dict:
        value = None if self._hide() else _TAX
        body: dict = {"head": {"id": 7, "cDefine10": value}, "lines": []}
        if self._hide():
            body["masked_fields"] = ["cDefine10", "tax_invoice_no"]
        return body

    def _sample(self) -> dict:
        row = {"id": 7, "code": _CODE, "tax_invoice_no": _TAX, "amount": "10.00",
               "open_amount": "10.00", "settled": False}
        if self.mode == "no_money":
            row["amount"] = row["open_amount"] = row["settled"] = None
        if self.mode == "no_key":
            row.pop("tax_invoice_no", None)
        if self.mode == "null_tax":
            row["tax_invoice_no"] = None
        if self._hide():
            row["tax_invoice_no"] = None
            row["amount"] = row["open_amount"] = row["settled"] = None
            row["defines"] = {"define10": None}
        return row

    def _search(self, body: dict) -> tuple[int, dict]:
        if self.mode == "empty":
            return 200, {"ok": True, "items": []}
        if self.mode == "cap":
            row = {"id": 7, "code": _CODE, "tax_invoice_no": None, "amount": "10.00",
                   "open_amount": "10.00", "settled": False}
            return 200, {"ok": True, "items": [row], "next": "more"}
        if self._hide() and body.get("settled") is not None and "iNatSum" in self.plan.fields:
            return 403, {"code": "no_permission", "message": tn.MONEY_DENY}
        defs = body.get("defines")
        if self._hide() and isinstance(defs, dict) and "define10" in defs:
            return 403, {"code": "no_permission", "message": ta.define_deny("define10")}
        items = self._items(body)
        payload: dict = {"ok": True, "items": items}
        if self._hide() and items:
            payload["masked_fields"] = ["tax_invoice_no", "cDefine10", "define10",
                                        "amount", "open_amount", "settled"]
        return 200, payload

    def _items(self, body: dict) -> list:
        needle = str(body.get("code_like") or "")
        if self.mode == "gone" and needle == _CODE:
            return []
        if needle == _TAX:
            if self._hide() and self.mode == "hidden":
                return [{"id": 8, "code": "C900002", "tax_invoice_no": None}]
            if self._hide():
                return []
            return [self._sample()]
        if needle and needle != _CODE:
            return []
        return [self._sample()]


def _run_auth(op: str, bridge: _AuthBridge, field: str = _FIELD) -> tuple[str, str]:
    with mock.patch.multiple(ca, row=bridge.row, send=bridge.send, ok=bridge.ok,
                             perm_operator=_perm), \
            mock.patch.object(ta, "_configured_field", return_value=field):
        return tn.TAXNO_OPS[op](_env(), _SPEC, Step("custom", sql_name=op))


class RegistryTests(unittest.TestCase):
    def test_search_registered(self) -> None:
        names = known_names()
        for name in _SEARCH_NAMES:
            self.assertEqual(names.count(name), 1, name)
            self.assertTrue(name.startswith(SEARCH[0]), name)
        for spec in tn.taxno_search_cases():
            self.assertTrue(spec.requires_login and spec.optional and spec.routes, spec.name)
            self.assertFalse(spec.exclusive, spec.name)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name), spec.name)
            self.assertIn("list:sale_invoice", auto_tags(spec))

    def test_auth_registered_exclusive(self) -> None:
        names = known_names()
        for spec in ta.auth_cases():
            self.assertEqual(names.count(spec.name), 1, spec.name)
            self.assertTrue(spec.name.startswith(PERM), spec.name)
            self.assertTrue(spec.requires_login and spec.optional and spec.routes, spec.name)
            self.assertTrue(is_exclusive(spec), spec.name)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name), spec.name)
            self.assertIn("perm", auto_tags(spec))
            self.assertIn("col-auth", auto_tags(spec))
        self.assertEqual(_AUTH_NAMES, tuple(spec.name for spec in ta.auth_cases()))


class TaxnoTests(unittest.TestCase):
    def test_hit_and_na(self) -> None:
        search = _Search()
        search.add({"type": tn.KIND, "limit": tn.PAGE}, 200,
                   {"ok": True, "items": [{"code": _CODE, "tax_invoice_no": _TAX}]})
        search.add({"type": tn.KIND, "code_like": _TAX, "limit": 200}, 200,
                   {"ok": True, "items": [{"code": _CODE, "tax_invoice_no": _TAX}]})
        self.assertEqual(_run("taxno_search_op", search)[0], "PASS")
        empty = _Search()
        empty.add({"type": tn.KIND, "limit": tn.PAGE}, 200, {"ok": True, "items": []})
        status, detail = _run("taxno_search_op", empty)
        self.assertEqual(status, "N/A")
        self.assertIn("销售发票", detail)

    def test_pages_until_tax_number(self) -> None:
        search = _Search()
        search.add({"type": tn.KIND, "limit": tn.PAGE}, 200,
                   {"ok": True, "items": [{"code": _CODE}], "next": "p2"})
        search.add({"type": tn.KIND, "limit": tn.PAGE, "after": "p2"}, 200,
                   {"ok": True, "items": [{"code": "C900002", "tax_invoice_no": _TAX}]})
        search.add({"type": tn.KIND, "code_like": _TAX, "limit": 200}, 200,
                   {"ok": True, "items": [{"code": "C900002", "tax_invoice_no": _TAX}]})
        self.assertEqual(_run("taxno_search_op", search)[0], "PASS")
        self.assertEqual(len(search.calls), 3)

    def test_no_number_is_na(self) -> None:
        search = _Search()
        search.add({"type": tn.KIND, "limit": tn.PAGE}, 200,
                   {"ok": True, "items": [{"code": _CODE}]})
        status, detail = _run("taxno_search_op", search)
        self.assertEqual(status, "N/A")
        self.assertIn("数电号", detail)
        self.assertNotIn("发票都没有号码", detail)
        self.assertNotIn("全账套", detail)

    def test_missing_key_fails_when_configured(self) -> None:
        search = _Search()
        search.add({"type": tn.KIND, "limit": tn.PAGE}, 200,
                   {"ok": True, "items": [{"code": _CODE}]})
        with mock.patch.object(ta, "_configured_field", return_value="cDefine10"):
            status, detail = _run("taxno_search_op", search)
        self.assertEqual(status, "FAIL")
        self.assertIn("tax_invoice_no", detail)
        self.assertNotIn("发票都没有号码", detail)
        self.assertNotIn("全账套", detail)
        mixed = _Search()
        mixed.add({"type": tn.KIND, "limit": tn.PAGE}, 200,
                  {"ok": True, "items": [{"code": _CODE}, {"code": "C900002", "tax_invoice_no": _TAX}]})
        with mock.patch.object(ta, "_configured_field", return_value="cDefine10"):
            status, detail = _run("taxno_search_op", mixed)
        self.assertEqual(status, "FAIL")
        self.assertIn("tax_invoice_no", detail)

    def test_null_key_is_na_sample(self) -> None:
        search = _Search()
        search.add({"type": tn.KIND, "limit": tn.PAGE}, 200,
                   {"ok": True, "items": [{"code": _CODE, "tax_invoice_no": None}]})
        with mock.patch.object(ta, "_configured_field", return_value="cDefine10"):
            status, detail = _run("taxno_search_op", search)
        self.assertEqual(status, "N/A")
        self.assertIn("样本中没有数电号", detail)
        self.assertNotIn("发票都没有号码", detail)
        self.assertNotIn("全账套", detail)

    def test_scan_cap_is_na_not_whole_book(self) -> None:
        search = _Search()
        for index in range(tn.PAGES):
            body: dict = {"type": tn.KIND, "limit": tn.PAGE}
            if index:
                body["after"] = f"p{index}"
            search.add(body, 200, {"ok": True, "items": [{"code": _CODE, "tax_invoice_no": None}],
                                   "next": f"p{index + 1}"})
        with mock.patch.object(ta, "_configured_field", return_value="cDefine10"):
            status, detail = _run("taxno_search_op", search)
        self.assertEqual(status, "N/A")
        self.assertIn("扫描上限内未找到样本", detail)
        self.assertNotIn("全账套", detail)
        self.assertEqual(len(search.calls), tn.PAGES)

    def test_mismatch_fails_without_echoing_the_number(self) -> None:
        search = _Search()
        search.add({"type": tn.KIND, "limit": tn.PAGE}, 200,
                   {"ok": True, "items": [{"code": _CODE, "tax_invoice_no": _TAX}]})
        search.add({"type": tn.KIND, "code_like": _TAX, "limit": 200}, 200,
                   {"ok": True, "items": [{"code": _CODE, "tax_invoice_no": "99000000000000000002"}]})
        status, detail = _run("taxno_search_op", search)
        self.assertEqual(status, "FAIL")
        self.assertNotIn(_TAX, detail)
        self.assertNotIn("99000000000000000002", detail)


class SettledTests(unittest.TestCase):
    def test_sale_order_400(self) -> None:
        search = _Search()
        search.add({"type": "sale_order", "settled": False, "limit": 1}, 400,
                   {"code": "bad_request", "field": "settled", "message": tn.SETTLED_DENY})
        self.assertEqual(_run("settled_type", search)[0], "PASS")

    def test_open_rows(self) -> None:
        search = _Search()
        search.add({"type": tn.KIND, "limit": tn.PAGE}, 200,
                   {"ok": True, "items": [{"settled": False, "open_amount": "1.00"}]})
        search.add({"type": tn.KIND, "settled": False, "limit": tn.PAGE}, 200,
                   {"ok": True, "items": [{"settled": False, "open_amount": "1.00"}]})
        search.add({"type": tn.KIND, "settled": True, "limit": tn.PAGE}, 200,
                   {"ok": True, "items": [{"settled": True, "open_amount": "0.00"}]})
        self.assertEqual(_run("settled_filter", search)[0], "PASS")

    def test_no_invoices_is_na(self) -> None:
        search = _Search()
        search.add({"type": tn.KIND, "limit": tn.PAGE}, 200, {"ok": True, "items": []})
        status, detail = _run("settled_filter", search)
        self.assertEqual(status, "N/A")
        self.assertIn("销售发票", detail)

    def test_wrong_status_fails(self) -> None:
        search = _Search()
        search.add({"type": tn.KIND, "limit": tn.PAGE}, 200,
                   {"ok": True, "items": [{"settled": False, "open_amount": "1.00"}]})
        search.add({"type": tn.KIND, "settled": False, "limit": tn.PAGE}, 200,
                   {"ok": True, "items": [{"settled": True, "open_amount": "0.00"}]})
        self.assertEqual(_run("settled_filter", search)[0], "FAIL")

    def test_settled_403_is_na(self) -> None:
        search = _Search()
        search.add({"type": tn.KIND, "limit": tn.PAGE}, 200,
                   {"ok": True, "items": [{"settled": False, "open_amount": "1.00"}]})
        search.add({"type": tn.KIND, "settled": False, "limit": tn.PAGE}, 403,
                   {"code": "no_permission", "message": tn.MONEY_DENY})
        status, detail = _run("settled_filter", search)
        self.assertEqual(status, "N/A")
        self.assertIn("无权", detail)


class AuthTests(unittest.TestCase):
    def test_perm_cache_not_off_is_na_and_writes_nothing(self) -> None:
        for cache in ("60", ""):
            bridge = _AuthBridge(_TAX_PLAN)
            env = _env()
            env.bag["perm_cache_s"] = cache
            with mock.patch.multiple(ca, row=bridge.row, send=bridge.send, ok=bridge.ok, perm_operator=_perm), \
                    mock.patch.object(ta, "_configured_field", return_value=_FIELD):
                status, detail = tn.TAXNO_OPS["col_auth_sale_invoice_taxno"](
                    env, _SPEC, Step("custom", sql_name="col_auth_sale_invoice_taxno"))
            self.assertEqual((status, detail), ("N/A", "桥权限缓存未关闭（permCacheSeconds≠0）"))
            self.assertEqual(bridge.calls, [])

    def test_taxno_pass_and_cleanup(self) -> None:
        bridge = _AuthBridge(_TAX_PLAN)
        status, detail = _run_auth("col_auth_sale_invoice_taxno", bridge)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(bridge.calls, ["insert", "delete"])
        self.assertFalse(bridge.denied)
        self.assertNotIn(_TAX, detail)

    def test_taxno_leak_fails_and_still_cleans(self) -> None:
        bridge = _AuthBridge(_TAX_PLAN, mask=False)
        status, detail = _run_auth("col_auth_sale_invoice_taxno", bridge)
        self.assertEqual(status, "FAIL")
        self.assertIn("tax_invoice_no", detail)
        self.assertNotIn(_TAX, detail)
        self.assertEqual(bridge.calls, ["insert", "delete"])

    def test_hidden_column_match_fails(self) -> None:
        bridge = _AuthBridge(_TAX_PLAN, mode="hidden")
        status, detail = _run_auth("col_auth_sale_invoice_taxno", bridge)
        self.assertEqual(status, "FAIL")
        self.assertIn("隐藏列", detail)
        self.assertNotIn(_TAX, detail)
        self.assertEqual(bridge.calls, ["insert", "delete"])

    def test_zero_hits_is_na_never_pass(self) -> None:
        bridge = _AuthBridge(_TAX_PLAN, mode="gone")
        status, detail = _run_auth("col_auth_sale_invoice_taxno", bridge)
        self.assertEqual(status, "N/A")
        self.assertIn("找不到", detail)
        self.assertNotEqual(status, "PASS")
        self.assertEqual(bridge.calls, ["insert", "delete"])

    def test_no_sample_is_na_without_insert(self) -> None:
        bridge = _AuthBridge(_TAX_PLAN, mode="empty")
        status, detail = _run_auth("col_auth_sale_invoice_taxno", bridge)
        self.assertEqual(status, "N/A")
        self.assertIn("数电号", detail)
        self.assertEqual(bridge.calls, [])

    def test_missing_tax_key_fails_without_insert(self) -> None:
        bridge = _AuthBridge(_TAX_PLAN, mode="no_key")
        status, detail = _run_auth("col_auth_sale_invoice_taxno", bridge)
        self.assertEqual(status, "FAIL")
        self.assertIn("tax_invoice_no", detail)
        self.assertNotIn("发票都没有号码", detail)
        self.assertEqual(bridge.calls, [])

    def test_null_tax_is_na_sample(self) -> None:
        bridge = _AuthBridge(_TAX_PLAN, mode="null_tax")
        status, detail = _run_auth("col_auth_sale_invoice_taxno", bridge)
        self.assertEqual(status, "N/A")
        self.assertIn("样本中没有数电号", detail)
        self.assertEqual(bridge.calls, [])

    def test_scan_cap_is_na_without_insert(self) -> None:
        bridge = _AuthBridge(_TAX_PLAN, mode="cap")
        status, detail = _run_auth("col_auth_sale_invoice_taxno", bridge)
        self.assertEqual(status, "N/A")
        self.assertIn("扫描上限内未找到样本", detail)
        self.assertNotIn("全账套", detail)
        self.assertEqual(bridge.calls, [])

    def test_lookup_does_not_request_defines(self) -> None:
        self.assertFalse(hasattr(ta, "_defines_leaked"))
        bridge = _AuthBridge(_TAX_PLAN)
        status, detail = _run_auth("col_auth_sale_invoice_taxno", bridge)
        self.assertEqual(status, "PASS", detail)
        lookups = [body for body in bridge.bodies if body.get("code_like") == _CODE]
        self.assertTrue(lookups)
        self.assertTrue(all("defines" not in body for body in lookups))
        self.assertTrue(any(isinstance(body.get("defines"), dict) and "define10" in body["defines"]
                            for body in bridge.bodies))

    def test_unconfigured_column_na(self) -> None:
        for field, text in (("", "taxno.tax_invoice_no_field"), ("x; DROP", "不是合法列名")):
            bridge = _AuthBridge(_TAX_PLAN)
            status, detail = _run_auth("col_auth_sale_invoice_taxno", bridge, field)
            self.assertEqual(status, "N/A")
            self.assertIn(text, detail)
            self.assertEqual(bridge.calls, [])

    def test_other_tax_column_uses_configured_plan(self) -> None:
        plan = ta.tax_plan("cTaxBillCode")
        bridge = _AuthBridge(plan)
        status, detail = _run_auth("col_auth_sale_invoice_taxno", bridge, "cTaxBillCode")
        self.assertEqual(status, "PASS", detail)
        self.assertIn("cTaxBillCode", detail)
        self.assertEqual(bridge.calls, ["insert", "delete"])
        self.assertFalse(any("defines" in body for body in bridge.bodies))

    def test_tax_mask_keeps_api_name(self) -> None:
        self.assertEqual(ta.tax_mask("cDefine10"), frozenset({"TAX_INVOICE_NO", "CDEFINE10", "DEFINE10"}))
        self.assertEqual(ta.tax_mask("cTaxBillCode"), frozenset({"TAX_INVOICE_NO", "CTAXBILLCODE"}))

    def test_amount_pass_and_leak(self) -> None:
        bridge = _AuthBridge(ta.MONEY_PLAN)
        status, detail = _run_auth("col_auth_sale_invoice_amount", bridge)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(bridge.calls, ["insert", "delete"])
        leak = _AuthBridge(ta.MONEY_PLAN, mask=False)
        status, detail = _run_auth("col_auth_sale_invoice_amount", leak)
        self.assertEqual(status, "FAIL")
        self.assertIn("金额", detail)
        self.assertEqual(leak.calls, ["insert", "delete"])

    def test_amount_no_invoices_na(self) -> None:
        bridge = _AuthBridge(ta.MONEY_PLAN, mode="empty")
        status, detail = _run_auth("col_auth_sale_invoice_amount", bridge)
        self.assertEqual(status, "N/A")
        self.assertIn("销售发票", detail)
        self.assertEqual(bridge.calls, [])

    def test_not_applicable(self) -> None:
        plan = _TAX_PLAN
        for bridge, text in (
            (_AuthBridge(plan, supervisor=True), "账套主管"),
            (_AuthBridge(plan, existing="2"), "已有字段权限行"),
        ):
            status, detail = _run_auth("col_auth_sale_invoice_taxno", bridge)
            self.assertEqual(status, "N/A")
            self.assertIn(text, detail)
            self.assertEqual(bridge.calls, [])

    def test_data_missing_reason(self) -> None:
        self.assertIn("taxno.tax_invoice_no_field", DataMissing("taxno.tax_invoice_no_field").reason())

    def test_restore_deletes_when_stop_already_set(self) -> None:
        env = _env()
        env.ctx.stop = threading.Event()
        env.ctx.stop.set()
        bridge = _AuthBridge(_TAX_PLAN)
        with mock.patch.object(ca, "row", bridge.row):
            problem = ca._Case(env, _SPEC, _TAX_PLAN).delete()
        self.assertEqual(problem, "")
        self.assertEqual(bridge.calls, ["delete"])
        leftover = _AuthBridge(_TAX_PLAN)

        def leftover_row(_env: object, _spec: object, query: str) -> list[str]:
            leftover.row(_env, _spec, query)
            return ["1", "1"]

        with mock.patch.object(ca, "row", leftover_row):
            problem = ca._Case(env, _SPEC, _TAX_PLAN).delete()
        self.assertIn("应剩 0 行", problem)
        self.assertEqual(leftover.calls, ["delete"])


if __name__ == "__main__":
    unittest.main()
