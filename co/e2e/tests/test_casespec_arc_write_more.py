"""档案写入用例（货位、计量单位、自定义项档案、客户存货对照，及之后可写的七类）的登记、挑选 SQL 和一轮回放。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.arc_write_more import arc_more_alias_dup, arc_more_cases, arc_more_cycle, arc_more_guard
from co.e2e.casespec.arc_inv_sub import arc_more_sub_ignored
from co.e2e.casespec.arc_project_delete import NAME as PROJ_NAME
from co.e2e.casespec.arc_project_delete import arc_more_project, proj_cases
from co.e2e.casespec.arc_write_more_sql import ARC_MORE_PICKS, arc_more_pick_sql, safe_part
from co.e2e.casespec.runner import _HANDLERS
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = (
    "archive-position-write",
    "archive-unit-write",
    "archive-user-define-write",
    "archive-customer-inventory-write",
    "archive-unit-group-write",
    "archive-settle-style-write",
    "archive-rd-style-write",
    "archive-purchase-type-write",
    "archive-sale-type-write",
    "archive-district-class-write",
    "archive-aa-bank-write",
    "archive-pay-agreement-write",
    "archive-rw7-delete-guard",
    "archive-rw8-delete-guard",
    "archive-inventory-sub-ignored",
    "archive-user-define-alias-dup",
    "archive-pay-agreement-delete-guard",
    "archive-pay-agreement-delete-guard-doc",
)
_GET = "/v1/archives/get"
_NOT_FOUND = U8CoError(404, "not_found", "档案不存在")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    """按路由回放：值是 dict 原样返回，是 U8CoError 就抛出。记录调用的路由和正文。"""

    def __init__(self, answers: dict[str, list]) -> None:
        self.answers = {key: list(val) for key, val in answers.items()}
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers[route].pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got


def _env(client: _Replay) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-28")


def _spec(name: str):
    return next(spec for spec in arc_more_cases() + proj_cases() if spec.name == name)


def _run(op, name: str, client: _Replay, sql_out: str) -> tuple[str, str]:
    spec = _spec(name)
    with mock.patch("co.e2e.casespec.arc_write_more.run_guarded", return_value=sql_out):
        return op(_env(client), spec, spec.steps[0])


class ArcMoreCaseTests(unittest.TestCase):
    def test_names_registered_once_and_ops_resolve(self) -> None:
        names = known_names()
        self.assertEqual(tuple(spec.name for spec in arc_more_cases()), _NAMES)
        for spec in arc_more_cases():
            self.assertEqual(names.count(spec.name), 1, spec.name)
            self.assertTrue(spec.requires_login)
            for step in spec.steps:
                self.assertIn(step.kind, _HANDLERS, spec.name)
                self.assertTrue(ops_run.has_op(step.sql_name), spec.name)

    def test_titles_say_which_kinds_use_sql(self) -> None:
        titles = {spec.name: spec.title for spec in arc_more_cases()}
        for name in ("archive-user-define-write", "archive-customer-inventory-write"):
            self.assertTrue(titles[name].endswith("（新增、删除走 EAI，修改走受控 SQL）"), titles[name])
        self.assertTrue(titles["archive-pay-agreement-write"].endswith("（受控 SQL）"))
        self.assertIn("修改", titles["archive-unit-write"])

    def test_pick_sql_passes_the_guard(self) -> None:
        for name in ARC_MORE_PICKS:
            sql = arc_more_pick_sql(name)
            self.assertEqual(sql_block_reason(sql), "", name)
            self.assertIn("ROW:", sql, name)
        with self.assertRaises(ValueError):
            arc_more_pick_sql("customer")
        with self.assertRaises(ValueError):
            safe_part("01'")


class ArcMoreReplayTests(unittest.TestCase):
    def test_unit_cycle_creates_reads_updates_deletes(self) -> None:
        client = _Replay(
            {
                "/v1/archives/create": [{"ok": True}],
                "/v1/archives/update": [{"ok": True}],
                "/v1/archives/delete": [{"ok": True, "deleted": True}],
                _GET: [
                    {"ok": True, "fields": {"cComUnitName": "u8co e2e 单位"}},
                    {"ok": True, "fields": {"cComUnitName": "u8co e2e 单位改"}},
                    _NOT_FOUND,
                ],
            }
        )
        status, detail = _run(arc_more_cycle, "archive-unit-write", client, "ROW:02|1\n")
        self.assertEqual(status, "PASS", detail)
        created = client.calls[0][1]
        self.assertEqual(created["fields"], {"name": "u8co e2e 单位", "group_code": "02", "changerate": 10})
        self.assertTrue(created["code"].startswith("ZZ"))
        self.assertEqual(client.calls[2][1]["fields"]["changerate"], 20)

    def test_pair_code_and_cleanup_on_failure(self) -> None:
        client = _Replay(
            {
                "/v1/archives/create": [{"ok": True}],
                "/v1/archives/delete": [{"ok": True, "deleted": True}],
                _GET: [{"ok": True, "fields": {"cCusInvName": "别的名字"}}],
            }
        )
        status, detail = _run(arc_more_cycle, "archive-customer-inventory-write", client, "ROW:C001|1001\n")
        self.assertEqual(status, "FAIL")
        self.assertIn("已补偿删除 C001:1001", detail)
        self.assertEqual(client.calls[0][1]["code"], "C001:1001")
        self.assertEqual(client.calls[-1][0], "/v1/archives/delete")

    def test_user_define_update_is_read_back(self) -> None:
        client = _Replay(
            {
                "/v1/archives/create": [{"ok": True}],
                "/v1/archives/update": [{"ok": True}],
                "/v1/archives/delete": [{"ok": True, "deleted": True}],
                _GET: [{"ok": True, "fields": {"cAlias": "ZA"}}, {"ok": True, "fields": {"cAlias": "ZB"}}, _NOT_FOUND],
            }
        )
        status, detail = _run(arc_more_cycle, "archive-user-define-write", client, "ROW:1002\n")
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.calls[2][1]["fields"], {"alias": "ZB"})

    def test_user_define_update_that_changes_nothing_fails(self) -> None:
        client = _Replay(
            {
                "/v1/archives/create": [{"ok": True}],
                "/v1/archives/update": [{"ok": True}],
                "/v1/archives/delete": [{"ok": True, "deleted": True}],
                _GET: [{"ok": True, "fields": {"cAlias": "ZA"}}, {"ok": True, "fields": {"cAlias": "ZA"}}],
            }
        )
        status, detail = _run(arc_more_cycle, "archive-user-define-write", client, "ROW:1002\n")
        self.assertEqual(status, "FAIL")
        self.assertIn("cAlias", detail)

    def test_pay_agreement_cycle_uses_column_labels(self) -> None:
        client = _Replay(
            {
                "/v1/archives/create": [{"ok": True}],
                "/v1/archives/update": [{"ok": True}],
                "/v1/archives/delete": [{"ok": True, "deleted": True}],
                _GET: [
                    {"ok": True, "fields": {"cName": "u8co e2e 协议"}},
                    {"ok": True, "fields": {"cName": "u8co e2e 协议改"}},
                    _NOT_FOUND,
                ],
            }
        )
        status, detail = _run(arc_more_cycle, "archive-pay-agreement-write", client, "ROW:ZZ00\n")
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.calls[0][1]["code"], "ZZ00")
        self.assertEqual(client.calls[0][1]["fields"], {"cName": "u8co e2e 协议", "iLZYJ": 0, "iPLNum": 3, "dblZQNum": 30})
        self.assertEqual(client.calls[2][1]["fields"], {"cName": "u8co e2e 协议改", "dblZQNum": 45})

    def test_alias_dup_is_refused_and_cleaned_up(self) -> None:
        refused = U8CoError(409, "state_mismatch", "代码已被档案值 ZZ0 使用")
        client = _Replay(
            {
                "/v1/archives/create": [{"ok": True}, {"ok": True}],
                "/v1/archives/update": [refused],
                "/v1/archives/delete": [{"ok": True, "deleted": True}, {"ok": True, "deleted": True}],
                _GET: [{"ok": True, "fields": {"cAlias": "F0102030405"}}],
            }
        )
        spec = _spec("archive-user-define-alias-dup")
        with mock.patch("co.e2e.casespec.arc_write_more.run_guarded", return_value="ROW:1002\n"):
            with mock.patch("co.e2e.casespec.arc_write_more._stamp", return_value="ZZ0102030405"):
                status, detail = arc_more_alias_dup(_env(client), spec, spec.steps[0])
        self.assertEqual(status, "PASS", detail)
        creates = [fields for route, fields in client.calls if route == "/v1/archives/create"]
        self.assertEqual([c["code"] for c in creates], ["1002:ZZ0102030405", "1002:ZY0102030405"])
        self.assertEqual([c["fields"] for c in creates], [{"alias": "E0102030405"}, {"alias": "F0102030405"}])
        update = next(fields for route, fields in client.calls if route == "/v1/archives/update")
        self.assertEqual((update["code"], update["fields"]), ("1002:ZY0102030405", {"alias": "E0102030405"}))

    def test_alias_dup_accepted_update_fails(self) -> None:
        client = _Replay(
            {
                "/v1/archives/create": [{"ok": True}, {"ok": True}],
                "/v1/archives/update": [{"ok": True}],
                "/v1/archives/delete": [{"ok": True}, {"ok": True}],
            }
        )
        spec = _spec("archive-user-define-alias-dup")
        with mock.patch("co.e2e.casespec.arc_write_more.run_guarded", return_value="ROW:1002\n"):
            status, detail = arc_more_alias_dup(_env(client), spec, spec.steps[0])
        self.assertEqual(status, "FAIL")
        self.assertIn("改成重复的代码", detail)

    def test_sub_ignored_expects_502_and_deletes(self) -> None:
        ignored = U8CoError(502, "u8_fields_ignored", "档案已保存，但 U8 未写入以下字段：bbatchproperty1")
        ignored.detail = {"fields": {"bbatchproperty1": {"sent": "1", "stored": "0"}}}
        client = _Replay(
            {
                "/v1/archives/create": [ignored],
                "/v1/archives/delete": [{"ok": True, "deleted": True}],
                _GET: [_NOT_FOUND],
            }
        )
        spec = _spec("archive-inventory-sub-ignored")
        with mock.patch("co.e2e.casespec.arc_inv_sub.run_guarded", return_value="ROW:ZZINV1\n"):
            with mock.patch("co.e2e.casespec.arc_inv_sub.free_code_sql", return_value="SELECT 1"):
                with mock.patch("co.e2e.casespec.arc_inv_sub.td", return_value="TPL"):
                    status, detail = arc_more_sub_ignored(_env(client), spec, spec.steps[0])
        self.assertEqual(status, "PASS", detail)
        sent = client.calls[0][1]
        self.assertEqual((sent["code"], sent["template"], sent["fields"]["bbatchproperty1"]), ("ZZINV1", "TPL", 1))
        self.assertEqual(sent["fields"]["bbondedinv"], 1)

    def test_sub_ignored_is_not_applicable_when_u8_wrote_it(self) -> None:
        client = _Replay({"/v1/archives/create": [{"ok": True}], "/v1/archives/delete": [{"ok": True}], _GET: [_NOT_FOUND]})
        spec = _spec("archive-inventory-sub-ignored")
        with mock.patch("co.e2e.casespec.arc_inv_sub.run_guarded", return_value="ROW:ZZINV1\n"):
            with mock.patch("co.e2e.casespec.arc_inv_sub.free_code_sql", return_value="SELECT 1"):
                with mock.patch("co.e2e.casespec.arc_inv_sub.td", return_value="TPL"):
                    status, _detail = arc_more_sub_ignored(_env(client), spec, spec.steps[0])
        self.assertEqual(status, "N/A")

    def test_pay_agreement_doc_guard_pick(self) -> None:
        sql = arc_more_pick_sql("guard_agreement_doc")
        self.assertIn("SO_SOMain", sql)
        self.assertIn("cgatheringplan", sql)
        self.assertIn("NOT EXISTS", sql)
        refused = U8CoError(409, "state_mismatch", "收付款协议已被使用（SO_SOMain），不能删除")
        client = _Replay({"/v1/archives/delete": [refused], _GET: [{"ok": True}]})
        status, detail = _run(arc_more_guard, "archive-pay-agreement-delete-guard-doc", client, "ROW:003\n")
        self.assertEqual(status, "PASS", detail)

    def test_pay_agreement_pick_and_guard(self) -> None:
        free = arc_more_pick_sql("pay_agreement")
        self.assertIn("AA_Agreement", free)
        self.assertIn("LEN(c.code) = 4", free)
        self.assertIn("cCusSAProtocol", arc_more_pick_sql("guard_agreement"))
        refused = U8CoError(409, "state_mismatch", "收付款协议已被使用（Customer），不能删除")
        client = _Replay({"/v1/archives/delete": [refused], _GET: [{"ok": True}]})
        status, detail = _run(arc_more_guard, "archive-pay-agreement-delete-guard", client, "ROW:001\n")
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.calls[0][1]["archive"], "pay_agreement")
        status, _detail = _run(arc_more_guard, "archive-pay-agreement-delete-guard", _Replay({}), "")
        self.assertEqual(status, "N/A")

    def test_sale_type_cycle_sends_picked_out_type(self) -> None:
        client = _Replay(
            {
                "/v1/archives/create": [{"ok": True}],
                "/v1/archives/update": [{"ok": True}],
                "/v1/archives/delete": [{"ok": True, "deleted": True}],
                _GET: [
                    {"ok": True, "fields": {"cSTName": "u8co e2e 销售"}},
                    {"ok": True, "fields": {"cSTName": "u8co e2e 销售改"}},
                    _NOT_FOUND,
                ],
            }
        )
        status, detail = _run(arc_more_cycle, "archive-sale-type-write", client, "ROW:ZZ|101\n")
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.calls[0][1]["code"], "ZZ")
        self.assertEqual(client.calls[0][1]["fields"], {"name": "u8co e2e 销售", "rstype_code": "101"})

    def test_unit_group_is_created_with_fixed_conversion(self) -> None:
        client = _Replay({"/v1/archives/create": [U8CoError(409, "state_mismatch", "x")], "/v1/archives/delete": [_NOT_FOUND]})
        status, _detail = _run(arc_more_cycle, "archive-unit-group-write", client, "ROW:ZZ01\n")
        self.assertEqual(status, "FAIL")
        self.assertEqual(client.calls[0][1]["fields"], {"name": "u8co e2e 组", "type": 1})

    def test_type_pick_sql_joins_leaf_rd_style_with_matching_flag(self) -> None:
        self.assertIn("bRdEnd = 1 AND bRdFlag = 1", arc_more_pick_sql("purchase_type"))
        self.assertIn("bRdEnd = 1 AND bRdFlag = 0", arc_more_pick_sql("sale_type"))
        self.assertNotIn("Rd_Style", arc_more_pick_sql("aa_bank"))
        self.assertNotIn("currency", ARC_MORE_PICKS)

    def test_rd_style_cycle_uses_picked_code_and_flag(self) -> None:
        client = _Replay(
            {
                "/v1/archives/create": [{"ok": True}],
                "/v1/archives/update": [{"ok": True}],
                "/v1/archives/delete": [{"ok": True, "deleted": True}],
                _GET: [
                    {"ok": True, "fields": {"cRdName": "u8co e2e 收发"}},
                    {"ok": True, "fields": {"cRdName": "u8co e2e 收发改"}},
                    _NOT_FOUND,
                ],
            }
        )
        status, detail = _run(arc_more_cycle, "archive-rd-style-write", client, "ROW:Z\n")
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.calls[0][1]["code"], "Z")
        self.assertEqual(client.calls[0][1]["fields"], {"name": "u8co e2e 收发", "rsflag": 1})

    def test_guard_style_uses_rd_style_and_unit_group(self) -> None:
        refused = U8CoError(409, "state_mismatch", "不能删除")
        client = _Replay({"/v1/archives/delete": [refused, refused], _GET: [{"ok": True}, {"ok": True}]})
        status, detail = _run(arc_more_guard, "archive-rw8-delete-guard", client, "ROW:1|01\n")
        self.assertEqual(status, "PASS", detail)
        deleted = [fields for route, fields in client.calls if route == "/v1/archives/delete"]
        self.assertEqual([(f["archive"], f["code"]) for f in deleted], [("rd_style", "1"), ("unit_group", "01")])

    def test_nothing_picked_is_not_applicable(self) -> None:
        status, _detail = _run(arc_more_cycle, "archive-position-write", _Replay({}), "")
        self.assertEqual(status, "N/A")

    def test_guard_expects_state_mismatch(self) -> None:
        refused = U8CoError(409, "state_mismatch", "不能删除")
        client = _Replay({"/v1/archives/delete": [refused, refused], _GET: [{"ok": True}, {"ok": True}]})
        status, detail = _run(arc_more_guard, "archive-rw7-delete-guard", client, "ROW:1A|01\n")
        self.assertEqual(status, "PASS", detail)
        client = _Replay({"/v1/archives/delete": [{"ok": True, "deleted": True}]})
        status, _detail = _run(arc_more_guard, "archive-rw7-delete-guard", client, "ROW:1A|01\n")
        self.assertEqual(status, "FAIL")


class ProjectDeleteTests(unittest.TestCase):
    _LIST = {"ok": True, "items": [{"code": "00:99000001", "class_code": "01"}]}

    def test_registered_and_resolves(self) -> None:
        self.assertEqual(known_names().count(PROJ_NAME), 1)
        self.assertTrue(ops_run.has_op(proj_cases()[0].steps[0].sql_name))
        self.assertIn("GL_accass", arc_more_pick_sql("project"))

    def test_deletes_new_item_and_refuses_referenced(self) -> None:
        refused = U8CoError(409, "state_mismatch", "项目已被使用（GL_accass），不能删除")
        client = _Replay(
            {
                "/v1/archives/list": [self._LIST],
                "/v1/archives/create": [{"ok": True}],
                "/v1/archives/delete": [{"ok": True, "deleted": True}, refused],
                _GET: [{"ok": True, "fields": {"citemname": "u8co e2e 项目"}}, _NOT_FOUND, {"ok": True}],
            }
        )
        status, detail = _run(arc_more_project, PROJ_NAME, client, "ROW:00|99000001\n")
        self.assertEqual(status, "PASS", detail)
        created = client.calls[1][1]
        self.assertRegex(created["code"], r"^00:ZZ\d{10}$")
        self.assertEqual(created["fields"], {"name": "u8co e2e 项目", "citemccode": "01"})
        self.assertEqual(client.calls[-2][1]["code"], "00:99000001")
        self.assertEqual(client.calls[-1][0], _GET)

    def test_referenced_delete_must_be_refused(self) -> None:
        client = _Replay(
            {
                "/v1/archives/list": [self._LIST],
                "/v1/archives/create": [{"ok": True}],
                "/v1/archives/delete": [{"ok": True, "deleted": True}, {"ok": True, "deleted": True}],
                _GET: [{"ok": True, "fields": {"citemname": "u8co e2e 项目"}}, _NOT_FOUND],
            }
        )
        status, detail = _run(arc_more_project, PROJ_NAME, client, "ROW:00|99000001\n")
        self.assertEqual(status, "FAIL")
        self.assertIn("409", detail)

    def test_nothing_picked_or_no_items_is_not_applicable(self) -> None:
        self.assertEqual(_run(arc_more_project, PROJ_NAME, _Replay({}), "")[0], "N/A")
        client = _Replay({"/v1/archives/list": [{"ok": True, "items": []}]})
        self.assertEqual(_run(arc_more_project, PROJ_NAME, client, "ROW:00|99000001\n")[0], "N/A")


if __name__ == "__main__":
    unittest.main()
