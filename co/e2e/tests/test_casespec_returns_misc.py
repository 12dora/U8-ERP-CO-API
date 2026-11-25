"""用例（只读报表和档案、新单据类型、退货、请购单、不良品入库、幂等键）的登记、正文、SQL 和自定义步骤。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError
from co.e2e.casespec import gl_idem, readonly, ops_run
from co.e2e.casespec.assertutil import EITHER
from co.e2e.casespec.returns_misc_bodies import (
    CUSTOM_BUILDERS,
    product_in_reject_gen,
    product_in_reject_part,
    purchase_in_nosrc,
    purchase_requisition_new,
    purchase_requisition_upd,
    purchase_return_gen,
    sale_return_gen,
)
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.returns_misc import returns_misc_cases
from co.e2e.casespec.picks import pick_keys
from co.e2e.casespec.preds import judge_pred
from co.e2e.casespec.runner import _HANDLERS
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.returns_misc_readback_sql import has_custom, custom_sql
from co.e2e.casespec.steps import _make
from co.e2e.tests import support


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


_NAMES = (
    "meta-fields",
    "report-close-status",
    "report-gl-balance",
    "report-gl-aux-balance",
    "report-arap-balance",
    "report-arap-aging",
    "report-bom",
    "archive-read-account",
    "archive-read-unit",
    "archive-read-unit-group",
    "archive-read-settle-style",
    "archive-read-voucher-sign",
    "archive-read-currency",
    "archive-read-bank",
    "archive-read-project",
    "archive-read-position",
    "archive-read-rd-style",
    "archive-read-purchase-type",
    "archive-read-sale-type",
    "archive-read-district-class",
    "archive-read-trade-class",
    "archive-read-aa-bank",
    "archive-read-customer-address",
    "archive-read-user-define",
    "archive-read-customer-inventory",
    "archive-read-pay-agreement",
    "archive-read-customer-invoice-company",
    "archive-read-exchange-rate",
    "archive-read-fa-card",
    "archive-read-operator",
    "archive-read-role",
    "list-sale-return",
    "list-purchase-return",
    "list-purchase-requisition",
    "list-dispatch",
    "list-arrival",
    "list-stock-opening",
    "purchase-invoice-review",
    "purchase-return-cycle",
    "sale-return-cycle",
    "purchase-requisition-cycle",
    "product-in-from-reject",
    "purchase-in-nosrc-havepo",
    "idempotency-gl-create",
)
_BAG = {
    "sr_line": "1000000012",
    "pr_line": "1000000013",
    "qmrej_line": "78",
    "qmrej_wh": "10",
    "qmrej_qty": "10625.0000",
    "qmrej_part": "10624.0000",
    "pinsrc_wh": "05",
    "pinsrc_ven": "S000001",
    "pinsrc_dep": "EX050",
    "pinsrc_inv": "INV0004",
    "bom_parent": "INV0012",
    "currency_name": "美元",
}


def _by_name(name: str) -> CaseSpec:
    for spec in returns_misc_cases():
        if spec.name == name:
            return spec
    raise AssertionError(name)


def _env(client: object = None, focus: str = "1000000011") -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(
        client=client, operator=operator, year="2026", today="2026-09-28", bag=dict(_BAG),
        made={}, focus=focus, last_code="", operator_name="甲", sql_n=0,
    )


class Replay:
    """按路由后缀回放响应；值是 dict 就原样返回，是 U8CoError 就抛出。记录每次调用。"""

    def __init__(self, answers: dict[str, list]) -> None:
        self.answers = {key: list(val) for key, val in answers.items()}
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers[route].pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got

    def meta(self) -> dict:
        got = self.answers["meta"].pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got


class ReturnsMiscNameTests(unittest.TestCase):
    def test_names_and_resolvable_steps(self) -> None:
        names = known_names()
        for name in _NAMES:
            self.assertIn(name, names)
        keys = set(pick_keys())
        for spec in returns_misc_cases():
            for need in spec.needs:
                self.assertIn(need, keys, spec.name)
            for step in spec.steps:
                self.assertIn(step.kind, _HANDLERS, spec.name)
                self._resolvable(spec, step)
        self.assertFalse(_by_name("meta-fields").requires_login)
        self.assertTrue(_by_name("product-in-from-reject").optional)
        self.assertTrue(_by_name("purchase-in-nosrc-havepo").optional)
        self.assertEqual(_by_name("archive-read-currency").needs, ("currency_name",))
        self.assertEqual(_by_name("report-bom").needs, ("bom_parent",))

    def _resolvable(self, spec: CaseSpec, step: Step) -> None:
        if step.kind in ("create", "apply"):
            self.assertTrue(callable(_make(step.sql_name)), spec.name)
        if step.kind in ("stash", "pred") and has_custom(step.sql_name):
            self.assertIn("ROW:", custom_sql(step.sql_name, "100", _BAG))
        if step.kind == "custom":
            self.assertTrue(ops_run.has_op(step.sql_name), spec.name)
            if step.sql_name == "refuse":
                self.assertIn(dict(step.fields)["builder"], CUSTOM_BUILDERS)

    def test_every_writer_deletes_what_it_made(self) -> None:
        for spec in returns_misc_cases():
            made = [step for step in spec.steps if step.kind == "create" or step.route.endswith("/generate")]
            made = [step for step in made if step.kind != "custom"]
            if not made:
                continue
            last = max(spec.steps.index(step) for step in made)
            later = [step for step in spec.steps[last:] if step.route.endswith("/delete") and step.status in (200, EITHER)]
            self.assertTrue(later, spec.name)

    def test_return_cases_clean_up_before_asserting(self) -> None:
        sale = _by_name("sale-return-cycle")
        self.assertEqual((sale.steps[0].sql_name, dict(sale.steps[0].fields)), ("room", {"read": "sr_room", "min": "2"}))
        delete = next(i for i, step in enumerate(sale.steps) if step.route.endswith("/delete"))
        cmps = [(i, dict(step.fields)["pred"]) for i, step in enumerate(sale.steps) if step.sql_name == "bag_compare"]
        self.assertTrue(all(i > delete for i, _pred in cmps))
        self.assertEqual([pred for _i, pred in cmps], [
            "shift:2:sr_ret", "shift:2:sr_wkp", "shift:2:sr_soret", "shift:-2:sr_sofh", "eq:1", "eq:1",
        ])
        before = [step for step in sale.steps[:delete] if step.kind == "pred"]
        self.assertEqual(before, [])
        pur = _by_name("purchase-return-cycle")
        delete = next(i for i, step in enumerate(pur.steps) if step.route.endswith("/delete"))
        self.assertEqual([step for step in pur.steps[:delete] if step.kind == "pred"], [])
        cmps = [dict(step.fields)["pred"] for step in pur.steps[delete:] if step.sql_name == "bag_compare"]
        self.assertEqual(cmps, ["plus1:pr_ret", "plus1:pr_poret", "shift:-1:pr_parr"])
        verbs = [dict(step.fields).get("action") for step in pur.steps if step.route.endswith("/verify")]
        self.assertEqual(verbs, ["verify", "unverify"])

    def test_requisition_and_reject_order(self) -> None:
        req = _by_name("purchase-requisition-cycle")
        self.assertEqual(req.data, ("purchase_requisition.dept", "purchase_requisition.inventory"))
        acts = [(dict(step.fields).get("action"), step.status) for step in req.steps if step.kind == "call"]
        acts = [item for item in acts if item[0]]
        self.assertEqual(acts, [("close", 409), ("verify", 200), ("close", 200), ("open", 200), ("unverify", 200)])
        rej = _by_name("product-in-from-reject")
        first = rej.steps[0]
        self.assertEqual((first.kind, first.sql_name, first.status, first.code), ("custom", "refuse", 409, "state_mismatch"))
        self.assertEqual(dict(first.fields)["text"], "须一次入库全部")
        self.assertEqual(rej.steps[-1].sql_name, "reject_drop")
        idem = _by_name("idempotency-gl-create")
        self.assertEqual([step.sql_name for step in idem.steps[:5]],
                         ["idem_bad", "idem_ok", "idem_again", "idem_count", "idem_mismatch"])
        self.assertEqual(idem.steps[-1].kind, "core_glsql")


class ReturnsMiscBodyTests(unittest.TestCase):
    def test_return_and_reject_bodies(self) -> None:
        env = _env()
        self.assertEqual(sale_return_gen(env), {
            "type": "sale_return", "id": 1000000011, "head": {"invoiced": False},
            "lines": [{"source_line_id": 1000000012, "quantity": 2}],
        })
        got = purchase_return_gen(env)
        self.assertEqual(got["type"], "purchase_return")
        self.assertNotIn("source_type", got)
        self.assertEqual(got["lines"], [{"source_line_id": 1000000013, "quantity": 1}])
        env.focus = "78"
        full = product_in_reject_gen(env)
        self.assertEqual(full["source_type"], "qm_product_reject")
        self.assertEqual(full["head"]["cWhCode"], "10")
        self.assertEqual(full["head"]["cRdCode"], support.sample_testdata()["stock"]["product_in_rd"])
        self.assertEqual(full["lines"], [{"source_line_id": 78, "quantity": 10625}])
        self.assertEqual(product_in_reject_part(env)["lines"][0]["quantity"], 10624)
        env.bag["qmrej_part"] = "0"
        with self.assertRaises(ValueError):
            product_in_reject_part(env)
        env.bag["sr_line"] = "1;x"
        with self.assertRaises(ValueError):
            sale_return_gen(env)

    def test_requisition_and_nosrc_bodies(self) -> None:
        env = _env()
        made = purchase_requisition_new(env)
        self.assertEqual(made["head"]["cdepcode"], "EX050")
        self.assertEqual(made["lines"], [{"cinvcode": "INV0004", "fquantity": 5, "drequirdate": "2026-10-12"}])
        env.focus = "41"
        self.assertEqual(purchase_requisition_upd(env), {"type": "purchase_requisition", "id": 41, "head": {"cmemo": "CO桥回归改"}})
        body = purchase_in_nosrc(env)
        self.assertEqual(body["head"]["cvencode"], "S000001")
        self.assertEqual(body["lines"], [{"cinvcode": "INV0004", "iquantity": 1}])
        env.bag["pinsrc_ven"] = "S1'"
        with self.assertRaises(ValueError):
            purchase_in_nosrc(env)

    def test_shift_predicate(self) -> None:
        bag = {"a": "3.0000"}
        self.assertEqual(judge_pred("5.0000", "shift:2:a", bag, "")[0], "PASS")
        self.assertEqual(judge_pred("1.0000", "shift:-2:a", bag, "")[0], "PASS")
        self.assertEqual(judge_pred("4.0000", "shift:2:a", bag, "")[0], "FAIL")
        self.assertEqual(judge_pred("5", "shift:x:a", bag, "")[0], "FAIL")
        self.assertEqual(judge_pred("5", "shift:2", bag, "")[0], "FAIL")


class ReturnsMiscSqlTests(unittest.TestCase):
    def test_pick_and_readback_sql(self) -> None:
        text = dict(sample_sql())
        self.assertIn("bReturnFlag", text["custom-sr-src"])
        self.assertIn("ISNULL(x.iRetQuantity, 0) END >= 2", text["custom-sr-src"])
        self.assertIn("fretqtywkp", text["custom-sr-src"])
        # 避开发票生单用例挑的发货单；订单行未关闭。
        self.assertIn("h.DLID <> ISNULL((", text["custom-sr-src"])
        self.assertIn("fretqtywkp, 0) = 0", text["custom-sr-src"])
        self.assertIn("so.cSCloser", text["custom-sr-src"])
        self.assertIn("fOutQuantity", text["custom-sr-room"])
        self.assertIn("cFree10", text["custom-pr-src"])
        self.assertIn("iOriTaxCost", text["custom-pr-src"])
        self.assertIn("d.cbCloser", text["custom-pr-src"])
        self.assertIn("fPoRetQuantity, 0) >= 1", text["custom-pr-src"])
        self.assertIn("UPPER(LTRIM(RTRIM(o.cValue))) IN (N'TRUE', N'1')", text["custom-pinsrc"])
        self.assertIn("LEN(LTRIM(RTRIM(s.cInvCode))) <= 12", text["custom-pinsrc"])
        self.assertIn("iRejectIds = 100", text["custom-qmrej-used"])
        self.assertIn("-2 THEN N'1'", text["custom-sr-neg"])
        self.assertIn("fValidInQuan", text["custom-pr-src"])
        self.assertIn("iBillType, 0) = 0", text["custom-pr-src"])
        self.assertIn("IDISPOSEFLOW = 2", text["custom-qmrej"])
        self.assertIn("iRejectIds = s.AUTOID", text["custom-qmrej"])
        self.assertIn("bPTHavePO", text["custom-pinsrc"])
        self.assertIn("bom_parent", text["custom-bom-parent"])
        self.assertIn("foreigncurrency", text["custom-currency-name"])
        self.assertIn("iCorID = 100", text["custom-sr-cor"])
        self.assertIn("iFHQuantity", text["custom-sr-sofh"])
        self.assertIn("fPoRetQuantity", text["custom-pr-poret"])
        self.assertIn("cVerifier", text["custom-pinv-rev"])
        self.assertIn("BFLAG", text["custom-qmrej-flag"])
        with self.assertRaises(ValueError):
            custom_sql("sr_ret", "100", {"sr_line": "1 OR 1=1"})
        with self.assertRaises(ValueError):
            custom_sql("pr_ret", "0", {"pr_line": "1"})
        with self.assertRaises(ValueError):
            custom_sql("nope", "100", {})
        self.assertIn("N'CO幂等20260928120000ab12'", gl_idem.count_sql("2026", "CO幂等20260928120000ab12"))
        with self.assertRaises(ValueError):
            gl_idem.count_sql("2026", "CO幂等120000")
        with self.assertRaises(ValueError):
            gl_idem.count_sql("2026", "x'")


def _report(name: str, extra: dict) -> dict:
    base = {"ok": True, "fiscal_year": 2026, "items": [], "next": None}
    base.update(extra)
    return base


class ReturnsMiscReadTests(unittest.TestCase):
    def test_meta(self) -> None:
        kinds = [{"name": kind} for kind in ("sale_order",) + readonly.NEW_KINDS]
        archives = [{"name": name} for name in readonly.RO_ARCHIVES]
        good = {"ok": True, "revision": "ab" * 32, "features": {}, "kinds": kinds, "archives": archives}
        env = _env(Replay({"meta": [good, dict(good, kinds=kinds[:1]), U8CoError(401, "bad_signature", "签名")]}))
        self.assertEqual(readonly.meta(env, CaseSpec("m", "m"), Step("custom"))[0], "PASS")
        status, detail = readonly.meta(env, CaseSpec("m", "m"), Step("custom"))
        self.assertEqual(status, "FAIL")
        self.assertIn("sale_return", detail)
        self.assertEqual(readonly.meta(env, CaseSpec("m", "m"), Step("custom"))[0], "FAIL")

    def test_report_bodies_and_shapes(self) -> None:
        env = _env()
        self.assertEqual(readonly.report_body(env, "gl_balance")["period_to"], 9)
        self.assertEqual(readonly.report_body(env, "bom"), {"parent": "INV0012", "levels": 2})
        body = readonly.report_body(env, "arap_aging")
        rows = [{"partner": "S1", "aging": [0.0, 1.0, 0.0], "prepaid": 0.0, "overdue": 1.0,
                 "max_overdue_days": 10, "oldest_overdue_date": "2026-09-18"}]
        pay = _report("arap_aging", {"side": "ap", "as_of": "2026-09-28", "basis": "due", "buckets": [{}, {}, {}], "items": rows})
        self.assertEqual(readonly.report_problem("arap_aging", pay, body), "")
        pay["items"] = [dict(rows[0], max_overdue_days=None, oldest_overdue_date=None)]
        self.assertEqual(readonly.report_problem("arap_aging", pay, body), "")
        pay["items"] = [dict(rows[0], max_overdue_days=None)]
        self.assertIn("max_overdue_days", readonly.report_problem("arap_aging", pay, body))
        pay["items"] = [dict(rows[0], max_overdue_days=0, oldest_overdue_date="2026-09-28")]
        self.assertIn("正整数", readonly.report_problem("arap_aging", pay, body))
        pay["items"] = [dict(rows[0], oldest_overdue_date="2026-09-01")]
        self.assertIn("报表日期", readonly.report_problem("arap_aging", pay, body))
        pay["items"] = rows
        pay["buckets"] = [{}, {}]
        self.assertIn("账龄", readonly.report_problem("arap_aging", pay, body))
        gl = _report("gl_balance", {"period_from": 1, "period_to": 9, "items": [{"code": "1001", "grade": 2, "close_dir": "借"}]})
        self.assertIn("grade", readonly.report_problem("gl_balance", gl, {}))
        bom = {"ok": True, "parent": "P", "as_of": "d", "levels": 2, "bom_id": 1, "version": 10, "truncated": False,
               "items": [{"level": 3, "component": "C", "qty": 1.0}]}
        self.assertIn("层级", readonly.report_problem("bom", bom, {"parent": "P"}))
        self.assertIn("parent", readonly.report_problem("bom", dict(bom, parent="Q"), {"parent": "P"}))
        self.assertIn("响应缺", readonly.report_problem("close_status", {"ok": True}, {}))

    def test_empty_gl_balance_is_na_off_the_reference_lane(self) -> None:
        empty = _report("gl_balance", {"period_from": 1, "period_to": 9})
        step = Step("custom", fields=(("name", "gl_balance"),), sql_name="report")
        with mock.patch.object(readonly, "expect_ready", return_value=False):
            env = _env(Replay({"/v1/reports/gl_balance": [empty]}))
            self.assertEqual(readonly.report(env, CaseSpec("r", "r"), step)[0], "N/A")
        with mock.patch.object(readonly, "expect_ready", return_value=True):
            env = _env(Replay({"/v1/reports/gl_balance": [empty]}))
            status, detail = readonly.report(env, CaseSpec("r", "r"), step)
            self.assertEqual(status, "FAIL")
            self.assertIn("没有行", detail)

    def test_report_step_sends_login_fields(self) -> None:
        pay = _report("arap_balance", {"side": "ar", "as_of": "2026-09-28", "items": [{"partner": "C1", "balance": 1.0}]})
        client = Replay({"/v1/reports/arap_balance": [pay]})
        env = _env(client)
        step = Step("custom", fields=(("name", "arap_balance"),), sql_name="report")
        self.assertEqual(readonly.report(env, CaseSpec("r", "r"), step)[0], "PASS")
        sent = client.calls[0][1]
        self.assertEqual((sent["acc"], sent["side"], sent["limit"]), ("801", "ar", 5))

    def test_archive_read_project_and_currency(self) -> None:
        listed = {"ok": True, "archive": "project", "items": [{"code": "98:01"}], "next": None}
        got = {"ok": True, "archive": "project", "code": "98:01", "project_class": "98", "fields": {"citemname": "x"}}
        client = Replay({"/v1/archives/list": [listed, dict(listed)], "/v1/archives/get": [got, dict(got, project_class="97")]})
        env = _env(client)
        step = Step("custom", fields=(("archive", "project"),), sql_name="archive_read")
        self.assertEqual(readonly.archive_read(env, CaseSpec("a", "a"), step)[0], "PASS")
        self.assertEqual(readonly.archive_read(env, CaseSpec("a", "a"), step)[0], "FAIL")
        cur_list = {"ok": True, "archive": "currency", "items": [{"code": "欧元"}], "next": None}
        cur_get = {"ok": True, "archive": "currency", "code": "美元", "fields": {"cexch_code": "USD"}}
        client = Replay({"/v1/archives/list": [cur_list], "/v1/archives/get": [cur_get]})
        step = Step("custom", fields=(("archive", "currency"),), sql_name="archive_read")
        self.assertEqual(readonly.archive_read(_env(client), CaseSpec("a", "a"), step)[0], "PASS")
        self.assertEqual(client.calls[1][1]["code"], "美元")
        empty = {"ok": True, "archive": "bank", "items": [], "next": None}
        step = Step("custom", fields=(("archive", "bank"),), sql_name="archive_read")
        got = readonly.archive_read(_env(Replay({"/v1/archives/list": [empty]})), CaseSpec("a", "a"), step)
        self.assertEqual(got[0], "N/A")

    def test_archive_read_exchange_rate_checks_date_lookup(self) -> None:
        item = {"code": "美元:2026:8", "currency": "美元", "year": 2026, "period": 8, "rate": 6.7898, "mode": "fixed"}
        listed = {"ok": True, "archive": "exchange_rate", "items": [item], "next": None}
        got = {"ok": True, "archive": "exchange_rate", "code": "美元:2026:8", "fields": dict(item)}
        by_date = dict(got, code="美元:2026-08-15", rate_mode="fixed")
        wrong = dict(by_date, fields=dict(item, rate=7.0))
        filtered = dict(listed, fiscal_year=2026)
        client = Replay(
            {
                "/v1/archives/list": [listed, filtered, dict(listed), dict(filtered)],
                "/v1/archives/get": [got, by_date, dict(got), wrong],
            }
        )
        step = Step("custom", fields=(("archive", "exchange_rate"),), sql_name="archive_read")
        self.assertEqual(readonly.archive_read(_env(client), CaseSpec("a", "a"), step)[0], "PASS")
        sent = client.calls[2][1]
        self.assertEqual((sent["currency"], sent["fiscal_year"]), ("美元", 2026))
        self.assertEqual(client.calls[3][1]["code"], "美元:2026-08-15")
        self.assertEqual(readonly.archive_read(_env(client), CaseSpec("a", "a"), step)[0], "FAIL")

    def test_archive_read_exchange_rate_checks_filters(self) -> None:
        item = {"code": "美元:2026:8", "currency": "美元", "year": 2026, "period": 8, "rate": 6.7898, "mode": "floating"}
        listed = {"ok": True, "archive": "exchange_rate", "items": [item], "next": None}
        got = {"ok": True, "archive": "exchange_rate", "code": "美元:2026:8", "fields": dict(item)}
        mixed = dict(listed, fiscal_year=2026, items=[item, dict(item, currency="欧元")])
        no_echo = dict(listed, fiscal_year=2025)
        client = Replay({"/v1/archives/list": [listed, mixed, dict(listed), no_echo], "/v1/archives/get": [got, dict(got)]})
        step = Step("custom", fields=(("archive", "exchange_rate"),), sql_name="archive_read")
        self.assertIn("混入", readonly.archive_read(_env(client), CaseSpec("a", "a"), step)[1])
        self.assertIn("回显", readonly.archive_read(_env(client), CaseSpec("a", "a"), step)[1])

    def test_archive_read_fa_card(self) -> None:
        card = {"code": "00001", "disposed": False, "type_code": "2001"}
        fields = dict(card, asset_num="A1", original_value=10.0, accumulated_depreciation=4.0, net_value=6.0, depts=[])
        listed = {"ok": True, "archive": "fa_card", "items": [card], "next": None, "watermark": None,
                  "fiscal_year": 2026, "as_of": "2026-09-30"}
        got = {"ok": True, "archive": "fa_card", "code": "00001", "fields": fields}
        other = dict(listed, items=[dict(card, type_code="30")])
        gone = dict(listed, items=[dict(card, disposed=True)])
        client = Replay(
            {
                "/v1/archives/list": [listed, listed, listed, other, gone],
                "/v1/archives/get": [got, got, dict(got)],
            }
        )
        step = Step("custom", fields=(("archive", "fa_card"),), sql_name="archive_read")
        self.assertEqual(readonly.archive_read(_env(client), CaseSpec("a", "a"), step)[0], "PASS")
        self.assertEqual(client.calls[2][1]["type_code"], "2001")
        self.assertIn("类别", readonly.archive_read(_env(client), CaseSpec("a", "a"), step)[1])
        self.assertIn("已减少", readonly.archive_read(_env(client), CaseSpec("a", "a"), step)[1])
        bare = dict(got, fields={"asset_num": "A1"})
        client = Replay({"/v1/archives/list": [dict(listed, as_of=None), listed], "/v1/archives/get": [got, bare]})
        self.assertIn("as_of", readonly.archive_read(_env(client), CaseSpec("a", "a"), step)[1])
        self.assertIn("卡片缺", readonly.archive_read(_env(client), CaseSpec("a", "a"), step)[1])

    def test_archive_read_operator_and_role(self) -> None:
        user = {"code": "A01", "name": "王小明", "disabled": False, "roles": ["R01"], "person_code": None}
        listed = {"ok": True, "archive": "operator", "items": [user], "next": None, "watermark": None}
        got = {"ok": True, "archive": "operator", "code": "A01", "fields": user}
        leaky = dict(got, fields=dict(user, cPassword="x"))
        client = Replay({"/v1/archives/list": [listed, listed, listed, listed], "/v1/archives/get": [got, leaky]})
        step = Step("custom", fields=(("archive", "operator"),), sql_name="archive_read")
        self.assertEqual(readonly.archive_read(_env(client), CaseSpec("a", "a"), step)[0], "PASS")
        self.assertIn("口令", readonly.archive_read(_env(client), CaseSpec("a", "a"), step)[1])
        role = {"code": "R01", "name": "会计", "members": "A01"}
        rlist = {"ok": True, "archive": "role", "items": [role], "next": None, "watermark": None}
        rgot = {"ok": True, "archive": "role", "code": "R01", "fields": role}
        denied = U8CoError(403, "no_permission", "没有角色查询权限")
        client = Replay({"/v1/archives/list": [rlist, rlist, denied], "/v1/archives/get": [rgot]})
        step = Step("custom", fields=(("archive", "role"),), sql_name="archive_read")
        self.assertIn("members", readonly.archive_read(_env(client), CaseSpec("a", "a"), step)[1])
        self.assertEqual(readonly.archive_read(_env(client), CaseSpec("a", "a"), step)[0], "N/A")

    def test_voucher_list(self) -> None:
        good = {"ok": True, "type": "purchase_requisition", "items": [], "next": None, "watermark": "25395299"}
        client = Replay({"/v1/vouchers/list": [good, dict(good, watermark=None)]})
        step = Step("custom", fields=(("type", "purchase_requisition"),), sql_name="voucher_list")
        self.assertEqual(readonly.voucher_list(_env(client), CaseSpec("l", "l"), step)[0], "PASS")
        self.assertEqual(readonly.voucher_list(_env(client), CaseSpec("l", "l"), step)[0], "FAIL")

    def test_voucher_list_blue_red_split(self) -> None:
        def page(kind: str, red: bool) -> dict:
            return {"ok": True, "type": kind, "items": [{"id": 1, "red": red}], "next": None, "watermark": "9"}

        client = Replay({"/v1/vouchers/list": [page("dispatch", False), page("dispatch", True), page("sale_return", True)]})
        blue = Step("custom", fields=(("type", "dispatch"),), sql_name="voucher_list")
        red = Step("custom", fields=(("type", "sale_return"),), sql_name="voucher_list")
        self.assertEqual(readonly.voucher_list(_env(client), CaseSpec("l", "l"), blue)[0], "PASS")
        self.assertEqual(readonly.voucher_list(_env(client), CaseSpec("l", "l"), blue)[0], "FAIL")
        self.assertEqual(readonly.voucher_list(_env(client), CaseSpec("l", "l"), red)[0], "PASS")


class ReturnsMiscWriteStepTests(unittest.TestCase):
    def test_refuse_checks_message(self) -> None:
        msg = "参照不良品处理单须一次入库全部处理后数量，本行应为 10625"
        client = Replay({"/v1/vouchers/generate": [U8CoError(409, "state_mismatch", msg), U8CoError(409, "state_mismatch", "别的")]})
        env = _env(client, "78")
        spec = CaseSpec("x", "x", ("qmrej",))
        env.bag["qmrej"] = "78"
        step = _by_name("product-in-from-reject").steps[0]
        self.assertEqual(ops_run.step_custom(env, spec, step)[0], "PASS")
        self.assertEqual(client.calls[0][1]["lines"][0]["quantity"], 10624)
        self.assertEqual(ops_run.step_custom(env, spec, step)[0], "FAIL")

    def test_refuse_deletes_unexpected_document(self) -> None:
        made = {"ok": True, "type": "product_in", "id": 901, "code": "RK1"}
        client = Replay({"/v1/vouchers/generate": [made], "/v1/vouchers/delete": [{"ok": True, "deleted": True}]})
        env = _env(client, "78")
        env.bag["qmrej"] = "78"
        step = _by_name("product-in-from-reject").steps[0]
        self.assertEqual(ops_run.step_custom(env, CaseSpec("x", "x", ("qmrej",)), step)[0], "FAIL")
        self.assertEqual(client.calls[1], ("/v1/vouchers/delete", client.calls[1][1]))
        self.assertEqual((client.calls[1][1]["type"], client.calls[1][1]["id"]), ("product_in", 901))

    def test_room_and_bag_compare(self) -> None:
        env = _env()
        spec = CaseSpec("s", "s", ("sr_src",))
        env.bag["sr_src"] = "1000000011"
        step = _by_name("sale-return-cycle").steps[0]
        with mock.patch.object(ops_run, "run_guarded", return_value="ROW:1.0000\n"):
            self.assertEqual(ops_run.step_custom(env, spec, step)[0], "SKIP")
        with mock.patch.object(ops_run, "run_guarded", return_value="ROW:5.0000\n"):
            self.assertEqual(ops_run.step_custom(env, spec, step)[0], "PASS")
        env.bag.update({"sr_sofh": "10.0000", "sr_sofh_gen": "8.0000"})
        cmp = Step("custom", fields=(("got", "sr_sofh_gen"), ("pred", "shift:-2:sr_sofh")), sql_name="bag_compare")
        self.assertEqual(ops_run.step_custom(env, spec, cmp)[0], "PASS")
        env.bag["sr_sofh_gen"] = "10.0000"
        self.assertEqual(ops_run.step_custom(env, spec, cmp)[0], "FAIL")

    def test_reject_drop(self) -> None:
        spec = CaseSpec("r", "r", ("qmrej",))
        step = _by_name("product-in-from-reject").steps[-1]
        client = Replay({
            "/v1/vouchers/delete": [{"ok": True, "deleted": True}, U8CoError(409, "state_mismatch", "生产订单行已关闭")],
            "/v1/vouchers/load": [U8CoError(404, "not_found", "单据不存在")],
        })
        env = _env(client)
        env.made["r"] = "901"
        env.bag["qmrej"] = "78"
        with mock.patch.object(ops_run, "run_guarded", return_value="ROW:0\n") as run:
            self.assertEqual(ops_run.step_custom(env, spec, step)[0], "PASS")
        self.assertIn("iRejectIds = 78", run.call_args_list[0][0][1])
        self.assertIn("s.ID = 78", run.call_args_list[1][0][1])
        self.assertEqual(ops_run.step_custom(env, spec, step)[0], "PASS")
        client = Replay({
            "/v1/vouchers/delete": [{"ok": True, "deleted": True}],
            "/v1/vouchers/load": [U8CoError(404, "not_found", "单据不存在")],
        })
        env = _env(client)
        env.made["r"] = "901"
        env.bag["qmrej"] = "78"
        with mock.patch.object(ops_run, "run_guarded", return_value="ROW:1\n"):
            self.assertEqual(ops_run.step_custom(env, spec, step)[0], "FAIL")

    def test_idempotency_flow(self) -> None:
        made = {"ok": True, "period": 9, "sign": "转", "no": 3}
        answers = [
            U8CoError(400, "bad_request", "借贷不平"),
            made,
            dict(made),
            U8CoError(409, "idempotency_mismatch", "内容不同"),
        ]
        client = Replay({"/v1/gl/vouchers/create": answers})
        env = _env(client)
        spec = CaseSpec("idempotency-gl-create", "x")
        for op in ("idem_bad", "idem_ok", "idem_again", "idem_mismatch"):
            self.assertEqual(ops_run.step_custom(env, spec, Step("custom", sql_name=op, id_from="none"))[0], "PASS", op)
        keys = {fields["idempotency_key"] for _route, fields in client.calls}
        self.assertEqual(len(keys), 1)
        self.assertEqual({fields["head"]["sign"] for _route, fields in client.calls}, {"转"})
        self.assertEqual(env.bag["core:idempotency-gl-create"], "9|转|3")
        debits = [fields["lines"][0]["debit"] for _route, fields in client.calls]
        credits = [fields["lines"][1]["credit"] for _route, fields in client.calls]
        self.assertEqual((debits, credits), ([1.0, 1.0, 1.0, 2.0], [2.0, 1.0, 1.0, 2.0]))
        self.assertEqual(client.calls[1][1]["lines"][0]["account"], "6602")

    def test_idempotency_replay_mismatch_fails(self) -> None:
        client = Replay({"/v1/gl/vouchers/create": [
            U8CoError(400, "bad_request", "借贷不平"),
            {"ok": True, "period": 9, "sign": "转", "no": 3},
            {"ok": True, "period": 9, "sign": "转", "no": 4},
        ]})
        env = _env(client)
        spec = CaseSpec("i", "i")
        for op in ("idem_bad", "idem_ok"):
            ops_run.step_custom(env, spec, Step("custom", sql_name=op, id_from="none"))
        status, detail = ops_run.step_custom(env, spec, Step("custom", sql_name="idem_again", id_from="none"))
        self.assertEqual(status, "FAIL")
        self.assertIn("另一张", detail)

    def test_idempotency_count(self) -> None:
        env = _env()
        env.bag["custom_dig:i"] = "CO幂等20260928120000ab12"
        with mock.patch.object(gl_idem, "run_guarded", return_value="ROW:1\n") as run:
            self.assertEqual(gl_idem.idem_count(env, CaseSpec("i", "i"), Step("custom"))[0], "PASS")
        self.assertIn("iyear = 2026", run.call_args[0][1])
        with mock.patch.object(gl_idem, "run_guarded", return_value="ROW:2\n"):
            self.assertEqual(gl_idem.idem_count(env, CaseSpec("i", "i"), Step("custom"))[0], "FAIL")


if __name__ == "__main__":
    unittest.main()
