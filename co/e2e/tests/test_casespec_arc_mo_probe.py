"""档案写入附加核对（arc_probe）、操作员读取核对（operator_probe）、生产订单新增探查（mo_probe）和
生产订单修改的子件算法核对（mo_update 的 mou_prec / mou_formula）：登记、接线、SQL 形状和回放。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import arc_probe as arc
from co.e2e.casespec import mo_probe as mo
from co.e2e.casespec import operator_probe as ua
from co.e2e.casespec.catalog import build_cases, known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.wiring import has_edit
from co.e2e.casespec.mo_update import mou_cases
from co.e2e.casespec.mo_update_sql import mou_sql
from co.e2e.casespec.arc_probe_sql import arc_probe_samples, define_count_sql, person_sql, safe_cjk, safe_user
from co.e2e.casespec.mo_probe_sql import mo_probe_samples, new_mo_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_ARC = "/v1/archives/"
_PROBED = {
    "archive-position-write": "position",
    "archive-purchase-type-write": "purchase_type",
    "archive-sale-type-write": "sale_type",
    "archive-user-define-write": "user_define",
}


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    """按路由依次回放：值是 dict 原样返回，是 U8CoError 就抛出。记录调用。"""

    def __init__(self, answers: dict[str, list]) -> None:
        self.answers = {key: list(val) for key, val in answers.items()}
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers[route].pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got

    def routes(self) -> list[str]:
        return [route for route, _fields in self.calls]


class _Sql:
    """按 SQL 里的特征串依次回答（每个键一个队列），没有匹配就报错。"""

    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]

    def __call__(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.answers:
            if key in query and rows:
                return rows.pop(0)
        raise AssertionError("没有准备回答：" + query[:80])


def _rows(*cells: str) -> list[str]:
    return [f"ROW:{cell}\n" for cell in cells]


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="op001", password="pw")
    bag = {"moc_inv": "P001", "moc_dept": "D01", "moc_type": "1"}
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-28", bag=bag)


def _ok(**extra: object) -> dict:
    body: dict = {"ok": True}
    body.update(extra)
    return body


def _cases() -> dict[str, CaseSpec]:
    return {spec.name: spec for spec in build_cases({})}


def _arc_run(archive: str, client: _Replay, sql: _Sql) -> tuple[str, str]:
    step = Step("custom", fields=(("archive", archive),), sql_name="arc_probe", id_from="none")
    with mock.patch.object(arc, "run_guarded", sql):
        return arc.arc_probe(_env(client), CaseSpec("archive-x-write", "t"), step)


class WiringTests(unittest.TestCase):
    def test_probe_steps_follow_the_write_cycles(self) -> None:
        cases = _cases()
        for name, archive in _PROBED.items():
            steps = cases[name].steps
            self.assertEqual([step.sql_name for step in steps], ["arc_more_cycle", "arc_probe"], name)
            self.assertEqual(dict(steps[1].fields), {"archive": archive})
        self.assertEqual(len(cases["archive-unit-write"].steps), 1)
        ops = [step.sql_name for step in cases["archive-read-operator"].steps]
        self.assertEqual(ops, ["archive_read", "operator_probe"])
        self.assertEqual(len(cases["archive-read-role"].steps), 1)
        for name in ("arc_probe", "operator_probe", "mo_probe"):
            self.assertTrue(ops_run.has_op(name), name)

    def test_mo_probe_registered_and_clashes_with_bom_cases(self) -> None:
        self.assertEqual(known_names().count("mo-create-probe"), 1)
        cases = _cases()
        spec = cases["mo-create-probe"]
        self.assertTrue(spec.optional and spec.requires_login and spec.routes)
        self.assertTrue(conflicts(auto_tags(spec), auto_tags(cases["mo-create-delete"])))

    def test_mou_case_checks_precision_and_formula_after_doubling(self) -> None:
        steps = mou_cases()[0].steps
        names = [(step.kind, step.sql_name) for step in steps]
        double = next(i for i, step in enumerate(steps) if step.sql_name == "mou_double")
        delete = next(i for i, step in enumerate(steps) if step.route.endswith("/delete"))
        for key in ("mou_prec", "mou_formula"):
            at = names.index(("pred", key))
            self.assertTrue(double < at < delete, key)
            self.assertEqual(steps[at].code, "eq:1")
        for key in ("mou_prec", "mou_formula", "mou_nfixed", "mou_nscrap", "mou_naux"):
            self.assertTrue(has_edit(key), key)
        formula = mou_sql("mou_formula", "5", {})
        for part in ("CompScrap", "ParentScrap", "AuxBaseQtyN", "MoClass", "FVFlag", "iNumDecDgt", "d.MoId = 5"):
            self.assertIn(part, formula)
        self.assertIn("ROUND(a.Qty, g.dg)", mou_sql("mou_prec", "5", {}))
        self.assertIn("ISNULL(SUM(", mou_sql("mou_nfixed", "5", {}))
        with self.assertRaises(ValueError):
            mou_sql("mou_formula", "1; DROP", {})

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in arc_probe_samples() + mo_probe_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
        self.assertIn("d.MoId > 0", new_mo_sql("0", "A1"))
        with self.assertRaises(ValueError):
            safe_cjk("档'")
        with self.assertRaises(ValueError):
            safe_user("a'b")
        with self.assertRaises(ValueError):
            define_count_sql("1002", "abc")
        self.assertIn("(N'zhang.san')", person_sql(["zhang.san"]))


class PositionProbeTests(unittest.TestCase):
    def _sql(self, nested: str = "0|1") -> _Sql:
        return _Sql([("GradeDef_Base AS x", _rows("01|ZZ|ZZ01")), ("bPosEnd), -1)", _rows("1|-", nested, "1|-", "-|-"))])

    def _client(self) -> _Replay:
        return _Replay({_ARC + "create": [_ok(), _ok()], _ARC + "delete": [_ok(deleted=True), _ok(deleted=True)]})

    def test_child_flips_parent_end_flag_and_both_are_deleted(self) -> None:
        client = self._client()
        status, detail = _arc_run("position", client, self._sql())
        self.assertEqual(status, "PASS", detail)
        codes = [(route.rsplit("/", 1)[1], fields["code"]) for route, fields in client.calls]
        self.assertEqual(codes, [("create", "ZZ"), ("create", "ZZ01"), ("delete", "ZZ01"), ("delete", "ZZ")])
        self.assertEqual(client.calls[1][1]["fields"]["warehouse_code"], "01")

    def test_parent_left_as_leaf_fails_after_cleanup(self) -> None:
        client = self._client()
        status, detail = _arc_run("position", client, self._sql(nested="1|1"))
        self.assertEqual(status, "FAIL")
        self.assertIn("改成 0", detail)
        self.assertEqual(client.routes().count(_ARC + "delete"), 2)

    def test_no_second_level_skips(self) -> None:
        status, detail = _arc_run("position", _Replay({}), _Sql([("GradeDef_Base AS x", ["(0 rows)\n"])]))
        self.assertEqual(status, "PASS")
        self.assertIn("跳过", detail)


class TypeDefaultProbeTests(unittest.TestCase):
    def test_u8_clears_other_default_and_it_is_restored(self) -> None:
        client = _Replay({
            _ARC + "create": [_ok()],
            _ARC + "update": [_ok(), _ok()],
            _ARC + "delete": [_ok(deleted=True)],
        })
        sql = _Sql([("Rd_Style", _rows("ZZ|101")), ("FOR XML PATH", _rows("CG", "ZZ", "ZZ", "CG", "CG"))])
        status, detail = _arc_run("purchase_type", client, sql)
        self.assertEqual(status, "PASS", detail)
        updates = [(fields["code"], fields["fields"]) for route, fields in client.calls if route.endswith("update")]
        self.assertEqual(updates, [("ZZ", {"bdefau": 1}), ("CG", {"bdefau": 1})])
        self.assertEqual(client.calls[-1][1]["code"], "ZZ")

    def test_two_defaults_fail_but_flags_are_restored(self) -> None:
        client = _Replay({
            _ARC + "create": [_ok()],
            _ARC + "update": [_ok(), _ok()],
            _ARC + "delete": [_ok(deleted=True)],
        })
        sql = _Sql([("Rd_Style", _rows("ZZ|201")), ("FOR XML PATH", _rows("XS", "XS,ZZ", "XS,ZZ", "XS,ZZ", "XS"))])
        status, detail = _arc_run("sale_type", client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("没有清掉", detail)
        updates = [(fields["code"], fields["fields"]) for route, fields in client.calls if route.endswith("update")]
        self.assertEqual(updates, [("ZZ", {"bdefau": 1}), ("ZZ", {"bdefau": 0})])
        self.assertEqual(client.routes()[-1], _ARC + "delete")

    def test_defaults_not_restored_fail(self) -> None:
        client = _Replay({_ARC + "create": [_ok()], _ARC + "update": [_ok(), U8CoError(409, "u8_rejected", "x")],
                          _ARC + "delete": [_ok(deleted=True)]})
        sql = _Sql([("Rd_Style", _rows("ZZ|101")), ("FOR XML PATH", _rows("CG", "ZZ", "ZZ", "ZZ"))])
        status, detail = _arc_run("purchase_type", client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("没有恢复", detail)


class DefineProbeTests(unittest.TestCase):
    def test_cjk_value_at_full_length_is_stored_whole(self) -> None:
        client = _Replay({
            _ARC + "create": [U8CoError(400, "bad_request", "档案值超过使用长度"), _ok()],
            _ARC + "delete": [_ok(deleted=True)],
        })
        sql = _Sql([("UserDef_Base AS b", _rows("1002|12")), ("dbo.UserDefine AS u", _rows("1|1", "0|0"))])
        status, detail = _arc_run("user_define", client, sql)
        self.assertEqual(status, "PASS", detail)
        over, exact = (fields["code"].split(":", 1)[1] for _route, fields in client.calls[:2])
        self.assertEqual((len(over), len(exact)), (13, 12))
        self.assertEqual(safe_cjk(exact), exact)
        self.assertEqual(client.calls[-1][1]["code"], "1002:" + exact)

    def test_u8_refusing_full_length_fails_with_byte_hint(self) -> None:
        client = _Replay({_ARC + "create": [U8CoError(400, "bad_request", "超长"), U8CoError(409, "u8_rejected", "长度超出")]})
        sql = _Sql([("UserDef_Base AS b", _rows("1002|20"))])
        status, detail = _arc_run("user_define", client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("按字节", detail)

    def test_truncation_fails_after_delete(self) -> None:
        client = _Replay({
            _ARC + "create": [U8CoError(400, "bad_request", "超长"), _ok()],
            _ARC + "delete": [_ok(deleted=True)],
        })
        sql = _Sql([("UserDef_Base AS b", _rows("1002|12")), ("dbo.UserDefine AS u", _rows("0|1", "0|0"))])
        status, detail = _arc_run("user_define", client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("截断", detail)
        self.assertEqual(client.routes()[-1], _ARC + "delete")


class UaProbeTests(unittest.TestCase):
    def _run(self, items: list[dict], persons: str, me: object) -> tuple[str, str]:
        client = _Replay({_ARC + "list": [_ok(items=items, next=None)], _ARC + "get": [me]})
        with mock.patch.object(ua, "run_guarded", return_value=persons):
            return ua.ua_probe(_env(client), CaseSpec("archive-read-operator", "t"), Step("custom"))

    def test_states_and_persons_match(self) -> None:
        items = [
            {"code": "op001", "state": 0, "disabled": False, "person_code": "P01"},
            {"code": "zhang.san", "state": 2, "disabled": True, "person_code": None},
        ]
        me = _ok(fields={"code": "op001", "state": 0, "disabled": False})
        status, detail = self._run(items, "ROW:op001|P01\nROW:zhang.san|\n", me)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("0×1、2×1", detail)

    def test_disabled_must_follow_state(self) -> None:
        items = [{"code": "op001", "state": 1, "disabled": False, "person_code": None}]
        status, detail = self._run(items, "ROW:op001|\n", _ok(fields={}))
        self.assertEqual(status, "FAIL")
        self.assertIn("不符", detail)

    def test_person_must_match_account_db(self) -> None:
        items = [{"code": "op001", "state": 0, "disabled": False, "person_code": "P02"}]
        status, detail = self._run(items, "ROW:op001|P01\n", _ok(fields={}))
        self.assertEqual(status, "FAIL")
        self.assertIn("P01", detail)


class MoProbeTests(unittest.TestCase):
    def _run(self, client: _Replay, sql: _Sql) -> tuple[str, str]:
        with mock.patch.object(mo, "run_guarded", sql):
            return mo.mo_probe(_env(client), CaseSpec("mo-create-probe", "t"), Step("custom"))

    def _sql(self, rows: str = "1|1|0") -> _Sql:
        return _Sql([
            ("ISNULL(i.bSelf, 0) = 0", _rows("B001")),
            ("i.bSelf = 1", _rows("S001")),
            ("MAX(o.MoId)", _rows("900")),
            ("COUNT(DISTINCT d.MoId)", _rows("0")),
            ("dbo.mom_order AS o WHERE o.MoId", _rows(rows, "0|0|0")),
        ])

    def test_bought_refused_and_bomless_created_then_deleted(self) -> None:
        client = _Replay({
            "/v1/vouchers/create": [U8CoError(400, "bad_request", "存货 B001 不是自制件"), _ok(id=901)],
            "/v1/vouchers/delete": [_ok(deleted=True)],
        })
        status, detail = self._run(client, self._sql())
        self.assertEqual(status, "PASS", detail)
        self.assertIn("子件 0 行", detail)
        lines = [fields["lines"][0] for route, fields in client.calls if route.endswith("create")]
        self.assertEqual([(line["inv_code"], line["dept_code"], line["mo_type"]) for line in lines],
                         [("B001", "D01", "1"), ("S001", "D01", "1")])
        self.assertEqual(client.calls[-1][1]["id"], 901)

    def test_bomless_refused_by_u8_is_recorded(self) -> None:
        client = _Replay({
            "/v1/vouchers/create": [U8CoError(400, "bad_request", "不是自制件"), U8CoError(409, "u8_rejected", "没有物料清单")],
        })
        status, detail = self._run(client, self._sql())
        self.assertEqual(status, "PASS", detail)
        self.assertIn("U8 拒绝", detail)

    def test_bought_accepted_fails_and_is_deleted(self) -> None:
        client = _Replay({"/v1/vouchers/create": [_ok(id=902)], "/v1/vouchers/delete": [_ok(deleted=True)]})
        sql = _Sql([
            ("ISNULL(i.bSelf, 0) = 0", _rows("B001")),
            ("i.bSelf = 1", ["(0 rows)\n"]),
            ("MAX(o.MoId)", _rows("900")),
            ("dbo.mom_order AS o WHERE o.MoId", _rows("0|0|0")),
        ])
        status, detail = self._run(client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("应 400", detail)
        self.assertEqual(client.routes()[-1], "/v1/vouchers/delete")

    def test_nothing_picked_is_na(self) -> None:
        sql = _Sql([("ISNULL(i.bSelf, 0) = 0", ["(0 rows)\n"]), ("i.bSelf = 1", ["(0 rows)\n"])])
        self.assertEqual(self._run(_Replay({}), sql)[0], "N/A")


if __name__ == "__main__":
    unittest.main()
