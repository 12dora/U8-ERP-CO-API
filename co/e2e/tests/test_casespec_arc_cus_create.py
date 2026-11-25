"""客户新增用例 `archive-customer-create` 的登记、分线（只在测试线）、一轮回放和失败路径。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError
from co.e2e.casespec import arc_cus_create as mod
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.arc_write import CORE_HANDLERS
from co.e2e.casespec.runner import _HANDLERS
from co.e2e.casespec.tags import auto_tags
from co.e2e.tests import support

_NAME = "archive-customer-create"
_TODAY = "2026-09-28"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Client:
    """假桥：一张客户表，按路由改；refuse 里的路由回给定的错误。"""

    def __init__(self, taken: tuple[str, ...] = ()) -> None:
        self.taken = set(taken)
        self.rows: dict[str, dict[str, str]] = {}
        self.calls: list[tuple[str, dict]] = []
        self.refuse: dict[str, U8CoError] = {}

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        op = route.rsplit("/", 1)[-1]
        if op in self.refuse:
            raise self.refuse[op]
        if op in ("delete", "update"):
            raise AssertionError("本用例不删、不走 EAI 修改")
        return getattr(self, "_" + op)(fields)

    def _create(self, fields: dict) -> dict:
        made = fields["fields"]
        self.rows[fields["code"]] = {
            "name": made["name"],
            "tax_no": made["tax_reg_code"],
            "person": made["spec_operator"],
            "end_date": "",
        }
        return {"ok": True, "code": fields["code"]}

    def _get(self, fields: dict) -> dict:
        row = self.rows[fields["code"]]
        return {"ok": True, "fields": {"code": fields["code"], "name": row["name"], "tax_reg_code": row["tax_no"]}}

    def _customer_fields(self, fields: dict) -> dict:
        row = self.rows[fields["code"]]
        for key, val in (fields.get("expect") or {}).items():
            if row[key] != val:
                raise U8CoError(409, "state_mismatch", f"{key} 已变化")
        before = dict(row)
        row.update(fields["fields"])
        return {"ok": True, "code": fields["code"], "changed": True, "before": before, "after": dict(row)}

    def _customer_person(self, fields: dict) -> dict:
        row = self.rows[fields["code"]]
        if fields.get("expect") is not None and row["person"] != fields["expect"]:
            raise U8CoError(409, "state_mismatch", "当前专管业务员是" + row["person"])
        before, row["person"] = row["person"], fields["person"]
        return {"ok": True, "code": fields["code"], "before": before, "after": fields["person"]}


class _Sql:
    def __init__(self, free: str = "C199901", created: str = "2026-09-28") -> None:
        self.free = free
        self.created = created
        self.queries: list[str] = []

    def guarded(self, _env: object, query: str, _label: str) -> str:
        self.queries.append(query)
        if "(VALUES" in query:
            return f"ROW:{self.free}\n" if self.free else ""
        if "dCusCreateDatetime, 23" in query:
            return f"ROW:{self.created}\n"
        raise AssertionError("没有料到的 SQL：" + query[:80])


def _env(client: _Client) -> SimpleNamespace:
    op = SimpleNamespace(acc="801", year="2026", code="op001", password="pw")
    return SimpleNamespace(client=client, operator=op, year="2026", today=_TODAY, operator_name="")


def _run(client: _Client, sql: _Sql) -> tuple[str, str]:
    spec = mod.cuscreate_cases()[0]
    with mock.patch.object(mod, "run_guarded", sql.guarded):
        return mod.step_cuscreate(_env(client), spec, spec.steps[0])


def _ops(client: _Client) -> list[str]:
    return [route.rsplit("/", 1)[-1] for route, _body in client.calls]


class CatalogTests(unittest.TestCase):
    def test_case_registered(self) -> None:
        (spec,) = mod.cuscreate_cases()
        self.assertEqual(spec.name, _NAME)
        self.assertEqual(known_names().count(_NAME), 1)
        self.assertTrue(spec.requires_login)
        self.assertIn("archives.customer.codes", spec.data)
        self.assertIn("archives.customer.spec_operator_other", spec.data)
        self.assertIn("arc:customer", auto_tags(spec))
        self.assertIn("/v1/archives/create", spec.routes)
        self.assertIs(CORE_HANDLERS["core_cuscreate"], mod.step_cuscreate)
        self.assertIs(_HANDLERS["core_cuscreate"], mod.step_cuscreate)


class ReplayTests(unittest.TestCase):
    def test_creates_renames_moves_and_stops(self) -> None:
        client = _Client()
        verdict, note = _run(client, _Sql())
        self.assertEqual(verdict, "PASS", note)
        self.assertEqual(_ops(client), ["create", "get", "customer_fields", "customer_person", "customer_fields"])
        made = client.calls[0][1]
        self.assertEqual((made["archive"], made["code"]), ("customer", "C199901"))
        self.assertTrue(made["idempotency_key"].startswith("e2e-cuscreate-"))
        rename = client.calls[2][1]
        self.assertEqual(set(rename["fields"]), {"name", "tax_no"})
        self.assertEqual(rename["expect"], {"name": "CO回归客户C199901", "tax_no": "91000000CO" + "C199901".rjust(8, "0")})
        row = client.rows["C199901"]
        self.assertEqual((row["name"], row["tax_no"]), ("CO回归客户改C199901", "91000000CX" + "C199901".rjust(8, "0")))
        self.assertEqual(client.calls[3][1]["expect"], "EX040001")
        self.assertEqual(row["person"], "EX040002")
        self.assertEqual(row["end_date"], _TODAY)

    def test_stop_date_is_not_before_the_created_day(self) -> None:
        client = _Client()
        verdict, note = _run(client, _Sql(created="2026-10-02"))
        self.assertEqual(verdict, "PASS", note)
        self.assertEqual(client.rows["C199901"]["end_date"], "2026-10-02")

    def test_no_free_code_skips(self) -> None:
        client = _Client()
        verdict, note = _run(client, _Sql(free=""))
        self.assertEqual(verdict, "SKIP", note)
        self.assertEqual(client.calls, [])


class FailureTests(unittest.TestCase):
    def test_create_refused_fails_without_follow_up(self) -> None:
        client = _Client()
        client.refuse["create"] = U8CoError(403, "account_read_only", "该账套只开放读取")
        verdict, note = _run(client, _Sql())
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("新建客户", note)
        self.assertEqual(_ops(client), ["create"])

    def test_failure_after_create_names_the_left_customer(self) -> None:
        client = _Client()
        client.refuse["customer_person"] = U8CoError(409, "u8_rejected", "业务员已停用")
        verdict, note = _run(client, _Sql())
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("换专管业务员", note)
        self.assertIn("已新建 C199901", note)

    def test_get_mismatch_fails(self) -> None:
        client = _Client()
        orig = client._get

        def wrong(fields: dict) -> dict:
            out = orig(fields)
            out["fields"]["tax_reg_code"] = ""
            return out

        client._get = wrong  # type: ignore[method-assign]
        verdict, note = _run(client, _Sql())
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("新客户名称", note)


if __name__ == "__main__":
    unittest.main()
