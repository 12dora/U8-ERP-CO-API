"""字段权限遮蔽（col_auth、col_auth_sql）的离线测试：取值遍历、判定、SQL、登记，以及用假桥走完插入到删除。

不连接服务器：row / send / ok 换成按 SQL 文本和路由应答的假桥；桥权限缓存关闭（perm_cache_s 为 0）时用例不等待。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from co.e2e.cases import Operator
from co.e2e.casespec import ops_run
from co.e2e.casespec import perm_cache as pc
from co.e2e.casespec import col_auth as ca
from co.e2e.casespec import sqlexec
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import PERM
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.col_auth_sql import (
    col_auth_samples,
    delete_sql,
    existing_sql,
    insert_sql,
    switch_on_sql,
    switch_restore_sql,
    switch_sql,
)
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.steps import auth_fields
from co.e2e.casespec.tags import is_exclusive
from co.e2e.context import Run
from co.e2e.remote import RemoteResult
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_SPEC = CaseSpec("col-auth-x", "x")
_PERM = Operator("801", "2024", "op-perm", "pw-perm")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _env(cache: str | None = "0") -> SimpleNamespace:
    """cache 是健康检查 perm_cache_s 的记录值；None 表示第 0 波没记过，用例自己取健康检查。"""
    bag = {} if cache is None else {"perm_cache_s": cache}
    return SimpleNamespace(operator=Operator("801", "2024", "op-test", "pw"), year="2024", today="2026-09-28",
                           ctx=SimpleNamespace(needles=[]), client=object(), bag=bag)


def _perm(env: object) -> Operator:
    needles = getattr(getattr(env, "ctx", None), "needles", None)
    if isinstance(needles, list) and _PERM.password not in needles:
        needles.append(_PERM.password)
    return _PERM


class _Bridge:
    """假桥：插入字段权限行后按 mask 决定是否遮蔽；记下插入、删除。"""

    def __init__(self, plan: ca.Plan, mask: bool = True, supervisor: bool = False, switch: str = "1",
                 existing: str = "0") -> None:
        self.plan = plan
        self.mask = mask
        self.supervisor = supervisor
        self.switch = switch
        self.existing = existing
        self.rowcount = "1"
        self.denied = False
        self.calls: list[str] = []
        self.queries: list[str] = []

    def _bus(self, query: str) -> list[str]:
        if "UPDATE AA_BusObject_base" not in query:
            return [self.rowcount, self.switch]
        if "SET bAuthControl = 1" in query:
            self.calls.append("switch_on")
            return ["1", "1"]
        self.calls.append("switch_restore")
        return ["1"]

    def row(self, _env: object, _spec: object, query: str) -> list[str]:
        self.queries.append(query)
        if "INSERT INTO AA_ColumnAuth" in query:
            self.denied = True
            self.calls.append("insert")
            return ["OK", str(len(self.plan.fields))]
        if "DELETE a FROM AA_ColumnAuth" in query:
            self.denied = False
            self.calls.append("delete")
            return [str(len(self.plan.fields)), "0"]
        if "AA_BusObject_base" in query:
            return self._bus(query)
        return [self.existing]

    def send(self, _env: object, route: str, _body: dict) -> tuple[int, dict]:
        return 200, self.payload(route)

    def ok(self, _env: object, route: str, _body: dict, _what: str) -> dict:
        return self.payload(route)

    def _hide(self) -> bool:
        return self.denied and self.mask

    def payload(self, route: str) -> dict:
        if route == ca.SNAP:
            cols = {self.plan.key: sorted(self.plan.fields)} if self.denied else {}
            return {"supervisor": self.supervisor, "columns": cols}
        if route in (ca.LIST, ca.SEARCH):
            return {"items": [{"id": 7, "code": "0001"}]}
        if route == ca.SALES:
            cogs = None if self._hide() else 12.5
            body = {"sa_enabled": True, "items": [{"cogs": cogs, "qty": 3, "revenue": 20}], "totals": {"cogs": cogs}}
            if self._hide():
                body["masked_fields"] = ["cogs"]
            return body
        value = None if self._hide() else 1.25
        line = {name: value for name in self.plan.fields}
        line.update(cInvCode="I001", iQuantity=2)
        body = {"head": {"id": 7}, "lines": [line, dict(line)]}
        if self._hide():
            body["masked_fields"] = sorted(self.plan.fields)
        return body


def _run(op: str, bridge: _Bridge, env: SimpleNamespace | None = None) -> tuple[str, str]:
    target = env or _env()
    with mock.patch.multiple(ca, row=bridge.row, send=bridge.send, ok=bridge.ok,
                             perm_operator=_perm):
        return ca.COL_AUTH_OPS[op](target, _SPEC, Step("custom", sql_name=op))


class HelperTests(unittest.TestCase):
    def test_values_nested_case_insensitive(self) -> None:
        payload = {"items": [{"lines": [{"IPRICE": None}, {"iprice": 3}]}], "iUnitCost": None}
        names = frozenset({"IPRICE", "IUNITCOST"})
        found = ca.values_of(payload, names)
        self.assertEqual(sorted(found["IPRICE"], key=str), [3, None])
        self.assertEqual(ca.shown(payload, names), frozenset({"IPRICE"}))
        self.assertEqual(ca.masked_of({"masked_fields": ["iPrice"]}), frozenset({"IPRICE"}))
        self.assertEqual(ca.masked_of({}), frozenset())

    def test_mask_problem(self) -> None:
        names = frozenset({"IPRICE"})
        self.assertEqual(ca.mask_problem({"lines": [{"iPrice": None}], "masked_fields": ["iPrice"]}, names, names, "x"), "")
        self.assertIn("仍返回", ca.mask_problem({"lines": [{"iPrice": 1}]}, names, names, "x"))
        self.assertIn("masked_fields 缺", ca.mask_problem({"lines": [{"iPrice": None}]}, names, names, "x"))

    def test_columns_problem(self) -> None:
        plan = ca.PLANS["col_auth_purchase_in"]
        self.assertEqual(ca.columns_problem({"columns": {"24": ["iPrice", "IORIMONEY", "B;isum"]}}, plan), "")
        self.assertIn("缺 ['ISUM']", ca.columns_problem({"columns": {"24": ["iPrice", "iorimoney"]}}, plan))
        self.assertEqual(ca.columns_problem({}, plan), "perm/snapshot 缺 columns")


class SqlTests(unittest.TestCase):
    def test_samples_pass_guard(self) -> None:
        names = [name for name, _sql in sample_sql()]
        for name, text in col_auth_samples():
            self.assertIn(name, names)
            self.assertEqual(sql_block_reason(text), "", name)

    def test_only_own_rows(self) -> None:
        fields = ("iPrice", "iUnitCost")
        for text in (existing_sql("op-1", "0303", fields), delete_sql("op-1", "0303", fields)):
            self.assertIn("a.isUserGroup = 0 AND a.cUserId = N'op-1' AND a.cKey = N'0303'", text)
            self.assertIn("a.cFld IN (N'iPrice', N'iUnitCost')", text)
        self.assertIn("a.cFuncID = N'N'", delete_sql("op-1", "0303", fields))
        text = insert_sql("op-1", "0303", fields)
        self.assertIn("SELECT N'op-1', N'0303', f.fld, 0, N'N'", text)
        self.assertIn("WHERE NOT EXISTS", text)
        self.assertIn("ROLLBACK", text)
        self.assertIn("b.iAuthType = 1", switch_sql("VCH_01"))
        self.assertIn("UPDATE AA_BusObject_base SET bAuthControl = 1", switch_on_sql("07"))
        self.assertIn("SET bAuthControl = 0", switch_restore_sql("24", 0))
        self.assertIn("cBusObId = N'07'", switch_on_sql("07"))

    def test_bad_literals(self) -> None:
        with self.assertRaises(ValueError):
            insert_sql("op-1", "03'03", ("iPrice",))
        with self.assertRaises(ValueError):
            insert_sql("op-1", "0303", ("iPrice;x",))
        with self.assertRaises(ValueError):
            delete_sql("op-1", "0303", ())
        with self.assertRaises(ValueError):
            existing_sql("", "0303", ("iPrice",))
        with self.assertRaises(ValueError):
            switch_restore_sql("0303", 2)


class FlowTests(unittest.TestCase):
    def test_voucher_pass_and_cleanup(self) -> None:
        for op in ("col_auth_sale_out", "col_auth_purchase_in"):
            bridge = _Bridge(ca.PLANS[op])
            status, detail = _run(op, bridge)
            self.assertEqual(status, "PASS", detail)
            self.assertEqual(bridge.calls, ["insert", "delete"])
            self.assertFalse(bridge.denied)

    def test_leak_fails_and_still_cleans(self) -> None:
        bridge = _Bridge(ca.PLANS["col_auth_sale_out"], mask=False)
        status, detail = _run("col_auth_sale_out", bridge)
        self.assertEqual(status, "FAIL")
        self.assertIn("仍返回", detail)
        self.assertEqual(bridge.calls, ["insert", "delete"])

    def test_cogs(self) -> None:
        bridge = _Bridge(ca.PLANS["col_auth_mgmt_cogs"])
        status, detail = _run("col_auth_mgmt_cogs", bridge)
        self.assertEqual(status, "PASS", detail)
        leak = _Bridge(ca.PLANS["col_auth_mgmt_cogs"], mask=False)
        self.assertEqual(_run("col_auth_mgmt_cogs", leak)[0], "FAIL")
        self.assertFalse(leak.denied)

    def test_not_applicable(self) -> None:
        plan = ca.PLANS["col_auth_sale_out"]
        missing = _Bridge(plan)
        missing.rowcount = "0"
        for bridge, text in (
            (missing, ca.NO_SWITCH_ROW),
            (_Bridge(plan, supervisor=True), "账套主管"),
            (_Bridge(plan, existing="2"), "已有字段权限行"),
        ):
            status, detail = _run("col_auth_sale_out", bridge)
            self.assertEqual(status, "N/A")
            self.assertIn(text, detail)
            self.assertEqual(bridge.calls, [])

    def test_perm_cache_not_off_is_na_and_writes_nothing(self) -> None:
        for cache in ("60", "3600", ""):
            bridge = _Bridge(ca.PLANS["col_auth_sale_out"])
            status, detail = _run("col_auth_sale_out", bridge, _env(cache))
            self.assertEqual((status, detail), ("N/A", "桥权限缓存未关闭（permCacheSeconds≠0）"), cache)
            self.assertEqual(bridge.calls, [])
            self.assertEqual(bridge.queries, [])

    def test_perm_cache_read_from_health_when_not_recorded(self) -> None:
        for payload, want in (({"perm_cache_s": 0}, "PASS"), ({"perm_cache_s": 60}, "N/A"), ({"ok": True}, "N/A")):
            env = _env(None)
            env.client = SimpleNamespace(health=lambda p=payload: p)
            status, detail = _run("col_auth_sale_out", _Bridge(ca.PLANS["col_auth_sale_out"]), env)
            self.assertEqual(status, want, detail)
            self.assertIn("perm_cache_s", env.bag)

    def test_perm_cache_health_failure_is_na_and_not_recorded(self) -> None:
        def broken() -> dict:
            raise OSError("down")

        env = _env(None)
        env.client = SimpleNamespace(health=broken)
        bridge = _Bridge(ca.PLANS["col_auth_sale_out"])
        status, detail = _run("col_auth_sale_out", bridge, env)
        self.assertEqual((status, detail), ("N/A", pc.NOT_OFF))
        self.assertEqual((bridge.calls, env.bag), ([], {}))

    def test_remember_only_integers(self) -> None:
        env = _env(None)
        for payload, want in (({"perm_cache_s": 0}, "0"), ({"perm_cache_s": 60}, "60"), ({"perm_cache_s": True}, ""),
                              ({"perm_cache_s": "0"}, ""), ({}, ""), (None, "")):
            pc.remember(env, payload)
            self.assertEqual(env.bag["perm_cache_s"], want, payload)

    def test_no_sleep_left_in_case(self) -> None:
        self.assertFalse(hasattr(ca, "_wait") or hasattr(ca, "WAIT_S") or hasattr(ca, "time"))

    def test_perm_operator_unset_is_na(self) -> None:
        bridge = _Bridge(ca.PLANS["col_auth_sale_out"])
        with mock.patch.multiple(ca, row=bridge.row, send=bridge.send, ok=bridge.ok,
                                 perm_operator=lambda _e: None):
            status, detail = ca.COL_AUTH_OPS["col_auth_sale_out"](_env(), _SPEC, Step("custom", sql_name="col_auth_sale_out"))
        self.assertEqual(status, "N/A")
        self.assertEqual(detail, ca.NO_PERM_OP)
        self.assertEqual(bridge.calls, [])

    def test_switch_off_updates_then_restores(self) -> None:
        env = _env()
        bridge = _Bridge(ca.PLANS["col_auth_sale_out"], switch="0")
        status, detail = _run("col_auth_sale_out", bridge, env)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(bridge.calls, ["switch_on", "insert", "delete", "switch_restore"])
        self.assertTrue(any("N'op-perm'" in q and "INSERT INTO AA_ColumnAuth" in q for q in bridge.queries))
        self.assertFalse(any("N'op-test'" in q and "INSERT INTO AA_ColumnAuth" in q for q in bridge.queries))
        self.assertTrue(any("SET bAuthControl = 0" in q for q in bridge.queries))
        self.assertIn("pw-perm", env.ctx.needles)
        self.assertEqual(env.operator.code, "op-test")
        self.assertEqual(auth_fields(env)["password"], "pw")

    def test_switch_off_restores_on_failure(self) -> None:
        bridge = _Bridge(ca.PLANS["col_auth_sale_out"], mask=False, switch="0")
        status, detail = _run("col_auth_sale_out", bridge)
        self.assertEqual(status, "FAIL")
        self.assertIn("仍返回", detail)
        self.assertEqual(bridge.calls, ["switch_on", "insert", "delete", "switch_restore"])


class RestoreTests(unittest.TestCase):
    def test_delete_sends_sql_when_stop_already_set(self) -> None:
        env = _env()
        env.ctx.stop = threading.Event()
        env.ctx.stop.set()
        seen: list[str] = []

        def fake_row(_env: object, _spec: object, query: str) -> list[str]:
            from co.e2e.casespec.gate import stopped
            seen.append(query)
            self.assertFalse(stopped(_env.ctx))
            return ["2", "0"]

        with mock.patch.object(ca, "row", fake_row):
            problem = ca._Case(env, _SPEC, ca.PLANS["col_auth_sale_out"]).delete()
        self.assertEqual(problem, "")
        self.assertTrue(any("DELETE a FROM AA_ColumnAuth" in query for query in seen))
        self.assertTrue(env.ctx.stop.is_set())

    def test_delete_checks_remaining_rows_when_stopped(self) -> None:
        env = _env()
        env.ctx.stop = threading.Event()
        env.ctx.stop.set()

        def fake_row(_env: object, _spec: object, query: str) -> list[str]:
            return ["1", "1"]

        with mock.patch.object(ca, "row", fake_row):
            problem = ca._Case(env, _SPEC, ca.PLANS["col_auth_sale_out"]).delete()
        self.assertIn("应剩 0 行", problem)
        self.assertIn("1", problem)

    def test_undo_skips_field_check_when_stopped(self) -> None:
        env = _env()
        env.ctx.stop = threading.Event()
        env.ctx.stop.set()
        called: list[int] = []
        bridge = _Bridge(ca.PLANS["col_auth_sale_out"])
        with mock.patch.object(ca, "row", bridge.row):
            note = ca._undo(ca._Case(env, _SPEC, ca.PLANS["col_auth_sale_out"]), lambda: called.append(1) or "")
        self.assertEqual(note, "")
        self.assertEqual(bridge.calls, ["delete"])
        self.assertEqual(called, [])

    def test_run_restores_when_stop_set_before_undo(self) -> None:
        env = _env()
        env.ctx.stop = threading.Event()
        bridge = _Bridge(ca.PLANS["col_auth_sale_out"])

        def check() -> str:
            env.ctx.stop.set()
            return "ok"

        def again() -> str:
            raise AssertionError("中断后不应再核对字段恢复")

        with mock.patch.multiple(ca, row=bridge.row, send=bridge.send, ok=bridge.ok):
            status, detail = ca._run(ca._Case(env, _SPEC, ca.PLANS["col_auth_sale_out"]), check, again)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(bridge.calls, ["insert", "delete"])
        self.assertFalse(bridge.denied)

    def _live_env(self) -> Env:
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(folder, ignore_errors=True))
        ctx = Run(False, folder, object())
        return Env(ctx, object(), Operator("801", "2024", "op-test", "pw"), "2026-09-28", "2024", "2026-09-28")

    def test_delete_goes_through_guarded_when_stop_already_set(self) -> None:
        env = self._live_env()
        env.ctx.stop.set()
        seen: list[str] = []

        def fake_query(_env: object, query: str, _slot: str) -> RemoteResult:
            from co.e2e.casespec.gate import stopped
            seen.append(query)
            self.assertFalse(stopped(_env.ctx))
            return RemoteResult(0, "ROW:2|0\n")

        with mock.patch.object(sqlexec, "_query", fake_query):
            problem = ca._Case(env, _SPEC, ca.PLANS["col_auth_sale_out"]).delete()
        self.assertEqual(problem, "")
        self.assertTrue(any("DELETE a FROM AA_ColumnAuth" in query for query in seen))
        self.assertTrue(env.ctx.stop.is_set())
        self.assertFalse(env.ctx.cleanup_closed.is_set())

    def test_delete_skips_guarded_when_cleanup_closed(self) -> None:
        env = self._live_env()
        env.ctx.stop.set()
        env.ctx.cleanup_closed.set()
        seen: list[str] = []

        def fake_query(_env: object, query: str, _slot: str) -> RemoteResult:
            seen.append(query)
            raise AssertionError("已进入快照还原，不应再发 SQL")

        with mock.patch.object(sqlexec, "_query", fake_query):
            problem = ca._Case(env, _SPEC, ca.PLANS["col_auth_sale_out"]).delete()
        self.assertEqual(problem, "已进入快照还原，临时字段权限行留给还原")
        self.assertEqual(seen, [])

    def test_switch_off_restores_when_stop_set(self) -> None:
        env = _env()
        env.ctx.stop = threading.Event()
        bridge = _Bridge(ca.PLANS["col_auth_sale_out"], switch="0")
        case = ca._Case(env, _SPEC, ca.PLANS["col_auth_sale_out"])
        case.op = _PERM.code
        case.switch_was = 0

        def check() -> str:
            env.ctx.stop.set()
            return "ok"

        def again() -> str:
            raise AssertionError("中断后不应再核对字段恢复")

        with mock.patch.multiple(ca, row=bridge.row, send=bridge.send, ok=bridge.ok):
            status, detail = ca._run(case, check, again)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(bridge.calls, ["switch_on", "insert", "delete", "switch_restore"])
        self.assertTrue(env.ctx.stop.is_set())

    def test_switch_restore_skips_sql_when_cleanup_closed(self) -> None:
        env = self._live_env()
        env.ctx.stop.set()
        env.ctx.cleanup_closed.set()
        seen: list[str] = []

        def fake_query(_env: object, query: str, _slot: str) -> RemoteResult:
            seen.append(query)
            raise AssertionError("已进入快照还原，不应再发 SQL")

        case = ca._Case(env, _SPEC, ca.PLANS["col_auth_sale_out"])
        case.switch_was = 0
        with mock.patch.object(sqlexec, "_query", fake_query):
            note = ca._undo(case, lambda: "x")
        self.assertIn("快照还原", note)
        self.assertEqual(seen, [])


class PermOperatorTests(unittest.TestCase):
    def test_empty_config_is_none(self) -> None:
        support.install(support.make_config(account__perm_operator_file=""))
        self.addCleanup(support.install)
        self.assertIsNone(ca.perm_operator(_env()))

    def test_loads_0600_needles_and_keeps_lane_operator(self) -> None:
        folder = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(folder, ignore_errors=True))
        path = Path(folder) / "perm.json"
        path.write_text(json.dumps({"acc": "801", "year": "2024", "operator": "op-perm", "password": "pw-perm"}),
                        encoding="utf-8")
        os.chmod(path, 0o600)
        support.install(support.make_config(account__perm_operator_file=str(path)))
        self.addCleanup(support.install)
        env = _env()
        op = ca.perm_operator(env)
        self.assertEqual(op.code, "op-perm")
        self.assertIn("pw-perm", env.ctx.needles)
        self.assertEqual(env.operator.code, "op-test")
        self.assertEqual(env.operator.password, "pw")
        os.chmod(path, 0o644)
        with self.assertRaises(RuntimeError) as caught:
            ca.perm_operator(_env())
        self.assertIn("过宽", str(caught.exception))

    def test_send_uses_call_as_with_perm_operator(self) -> None:
        env = _env()
        seen: list[str] = []

        def fake_call_as(_env: object, op: Operator, route: str, _extra: dict) -> tuple[int, dict]:
            seen.append(op.code)
            self.assertEqual(op.password, "pw-perm")
            self.assertEqual(route, ca.SNAP)
            return 200, {"supervisor": False}

        with mock.patch.object(ca, "perm_operator", _perm):
            with mock.patch.object(ca, "call_as", fake_call_as):
                status, payload = ca.send(env, ca.SNAP, {})
        self.assertEqual(status, 200)
        self.assertEqual(payload["supervisor"], False)
        self.assertEqual(seen, ["op-perm"])
        self.assertIn("pw-perm", env.ctx.needles)
        self.assertEqual(auth_fields(env)["operator"], "op-test")


class RegistryTests(unittest.TestCase):
    def test_registered(self) -> None:
        names = known_names()
        for spec in ca.col_auth_cases():
            self.assertEqual(names.count(spec.name), 1, spec.name)
            self.assertTrue(spec.name.startswith(PERM))
            self.assertTrue(spec.requires_login and spec.optional and spec.routes, spec.name)
            self.assertTrue(is_exclusive(spec), spec.name)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name), spec.name)


if __name__ == "__main__":
    unittest.main()
