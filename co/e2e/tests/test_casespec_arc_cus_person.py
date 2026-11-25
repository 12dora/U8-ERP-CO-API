"""客户专管业务员用例 `archive-customer-pperson` 的登记、分线、SQL 守卫和一轮回放。不连接服务器。"""

from __future__ import annotations

import re
import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError
from co.e2e.casespec import arc_cus_person as mod
from co.e2e.casespec.arc_cus_person_bodies import (
    changed,
    check_auth,
    listed_exceptions,
    parse_auth,
    parse_row,
    person_body,
    reply_dry,
    reply_write,
    volatile_set,
)
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.arc_write import CORE_HANDLERS
from co.e2e.casespec.runner import _HANDLERS
from co.e2e.casespec.arc_cus_person_sql import customer_row_sql, pperson_samples
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "archive-customer-pperson"
_ROW = {
    "cCusCode": "C900001",
    "cCusName": "客户甲",
    "cCusPPerson": "EX040001",
    "cCusDepart": "D901",
    "cCusAbbName": "甲",
    "cModifyPerson": "old",
    "dModifyDate": "2026-01-01T00:00:00",
    "pubufts": "1",
    "dLRDate": "h:lr0",
    "cCusMnemCode": "h:mn0",
}
_PEOPLE = {"EX040001": "D901", "EX040002": "D902"}
_USABLE = frozenset(_PEOPLE.values())
_EXC = {
    "read_only_exceptions": [
        {
            "accounts": ["801"],
            "operators": ["op001"],
            "type": "archives",
            "op": "customer_person",
        }
    ]
}


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _cells(row: dict[str, str]) -> str:
    return "".join(f"ROW:{key}\t{val}\n" for key, val in row.items())


class _Store:
    def __init__(self, row: dict[str, str] | None = None) -> None:
        primary = dict(row or _ROW)
        self.rows: dict[str, dict[str, str]] = {primary["cCusCode"]: primary}
        self.queries: list[str] = []
        self.auth: dict[str, list[list[str]]] = {}
        self._next_auth = 1

    def add(self, row: dict[str, str]) -> None:
        self.rows[row["cCusCode"]] = dict(row)

    @property
    def row(self) -> dict[str, str]:
        return next(iter(self.rows.values()))

    def sync_auth(self, code: str, dept: str) -> None:
        rows = self.auth.setdefault(code, [])
        zeros = [item for item in rows if item[2] == "0"]
        if not zeros:
            ident = str(self._next_auth)
            self._next_auth += 1
            rows.append([ident, dept, "0"])
            return
        for item in zeros:
            item[1] = dept

    def guarded(self, _env: object, query: str, _label: str) -> str:
        self.queries.append(query)
        return self._reply(query)

    def _reply(self, query: str) -> str:
        if "sys.columns" in query:
            return "ROW:pubufts\nROW:dModifyDate\nROW:cModifyPerson\nROW:ufts\n"
        if "dbo.Customer_Auth" in query:
            return self._auth_sql(query)
        if "UPDATE dbo.Customer" in query:
            return self._dept_write(query)
        if "dbo.Person" in query:
            return self._person_sql(query)
        if "dbo.Department" in query:
            return self._dept_ok(query)
        if "FOR XML" not in query and "dbo.Customer" in query:
            return self._pick_cell()
        return self._row_cells(query)

    def _person_sql(self, query: str) -> str:
        if "dbo.Department" in query:
            return "ROW:EX040002\n"
        if "cDepCode" in query:
            return self._person_dept(query)
        return "ROW:EX040002\n"

    def _named(self, query: str) -> str:
        for code in self.rows:
            if f"N'{code}'" in query:
                return code
        return next(iter(self.rows))

    def _auth_sql(self, query: str) -> str:
        if "UPDATE" in query or "DELETE" in query:
            return self._auth_write(query)
        code = self._named(query)
        rows = self.auth.get(code, [])
        return "".join(f"ROW:{auto}\t{priv}\t{typ}\n" for auto, priv, typ in rows)

    def _auth_write(self, query: str) -> str:
        code = self._named(query)
        rows = self.auth.setdefault(code, [])
        ident = re.search(r"autoId = ([0-9]+)", query)
        if ident is None:
            return "ROW:0\n"
        auto_id = ident.group(1)
        if "DELETE FROM" in query:
            before = len(rows)
            rows[:] = [item for item in rows if not (item[0] == auto_id and item[2] == "0")]
            return f"ROW:{before - len(rows)}\n"
        priv = ""
        hit = re.search(r"Privilege_ID = N'([^']*)'", query)
        if hit:
            priv = hit.group(1)
        n = 0
        for item in rows:
            if item[0] == auto_id and item[2] == "0":
                item[1] = priv
                n += 1
        return f"ROW:{n}\n"

    def _dept_write(self, query: str) -> str:
        for code, row in self.rows.items():
            if f"N'{code}'" not in query:
                continue
            if "cCusDepart = NULL" in query:
                row["cCusDepart"] = ""
            else:
                hit = re.search(r"cCusDepart = N'([^']*)'", query)
                if hit:
                    row["cCusDepart"] = hit.group(1)
            return "ROW:1\n"
        return "ROW:0\n"

    def _person_dept(self, query: str) -> str:
        for code, dept in _PEOPLE.items():
            if f"N'{code}'" in query:
                return f"ROW:{dept}\n"
        return "ROW:\n"

    def _dept_ok(self, query: str) -> str:
        for code in _USABLE:
            if f"N'{code}'" in query:
                return f"ROW:{code}\n"
        return ""

    def _pick_cell(self) -> str:
        for row in self.rows.values():
            if str(row.get("cCusPPerson") or "").strip():
                return f"ROW:{row['cCusCode']}\n"
        return ""

    def _row_cells(self, query: str) -> str:
        for code, row in self.rows.items():
            if f"N'{code}'" in query:
                return _cells(row)
        return _cells(self.row)


class _Client:
    def __init__(self, store: _Store, health: dict) -> None:
        self.store = store
        self._health = health
        self.calls: list[tuple[str, dict]] = []

    def health(self) -> dict:
        return self._health

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        op = route.rsplit("/", 1)[-1]
        if op == "update":
            return self._update(fields)
        if op == "customer_person":
            return self._person(fields)
        raise U8CoError(404, "not_found", "路由不存在")

    def _update(self, fields: dict) -> dict:
        del fields
        if listed_exceptions(self._health):
            raise U8CoError(403, "account_read_only", "该账套只开放读取")
        raise U8CoError(500, "busy", "未配置例外的通道不应调用 archives/update")

    def _shift(self, current: str, person: str, dept_before: str) -> str:
        old_dept = _PEOPLE.get(current, "")
        new_dept = _PEOPLE.get(person, "")
        if not new_dept or new_dept not in _USABLE:
            return ""
        if dept_before and dept_before != old_dept:
            return ""
        return new_dept if new_dept != dept_before else ""

    def _person(self, fields: dict) -> dict:
        code = str(fields.get("code") or "")
        person = str(fields.get("person") or "")
        row = self.store.rows.get(code)
        if person == "ZZNOSUCH":
            raise U8CoError(409, "u8_rejected", "业务员不存在：ZZNOSUCH")
        if row is None:
            raise U8CoError(404, "not_found", "档案不存在")
        current = str(row.get("cCusPPerson") or "").strip()
        if "expect" in fields and str(fields.get("expect") or "").strip() != current:
            raise U8CoError(409, "state_mismatch", "专管业务员已变化")
        before = current or None
        dept_before = str(row.get("cCusDepart") or "").strip()
        target = self._shift(current, person, dept_before)
        if fields.get("dry_run"):
            return {
                "ok": True,
                "dry_run": True,
                "mode": "validate",
                "code": code,
                "before": before,
                "dept_before": dept_before or None,
                "dept_note": None,
            }
        if person != current:
            row["cCusPPerson"] = person
            row["cModifyPerson"] = "op001"
            row["dModifyDate"] = "2026-10-18"
            row["pubufts"] = str(int(row.get("pubufts") or "1") + 1)
            if target:
                row["cCusDepart"] = target
                self.store.sync_auth(code, target)
        dept_after = str(row.get("cCusDepart") or "").strip()
        return {
            "ok": True,
            "code": code,
            "before": before,
            "after": person,
            "dept_before": dept_before or None,
            "dept_after": dept_after or None,
            "dept_note": None,
        }


def _env(client: _Client, acc: str = "801", operator: str = "op001") -> SimpleNamespace:
    op = SimpleNamespace(acc=acc, year="2026", code=operator, password="pw")
    return SimpleNamespace(client=client, operator=op, year="2026", today="2026-09-28")


def _run(client: _Client, store: _Store) -> tuple[str, str]:
    spec = mod.pperson_cases()[0]
    with mock.patch.object(mod, "run_guarded", store.guarded):
        return mod.step_pperson(_env(client), spec, spec.steps[0])


def _ops(client: _Client) -> list[str]:
    return [route.rsplit("/", 1)[-1] for route, _body in client.calls]


def _bodies(client: _Client, op: str) -> list[dict]:
    return [body for route, body in client.calls if route.endswith("/" + op)]


class CatalogTests(unittest.TestCase):
    def test_case_registered(self) -> None:
        (spec,) = mod.pperson_cases()
        self.assertEqual(spec.name, _NAME)
        self.assertEqual(known_names().count(_NAME), 1)
        self.assertTrue(spec.requires_login)
        self.assertEqual(spec.data, ("archives.customer.existing",))
        self.assertEqual(spec.tags, frozenset({"arc:customer"}))
        self.assertIn("arc:customer", auto_tags(spec))
        self.assertIn("/v1/archives/customer_person", spec.routes)
        self.assertIn("/v1/archives/update", spec.routes)
        self.assertEqual(spec.steps[0].kind, "core_pperson")
        self.assertIs(CORE_HANDLERS["core_pperson"], mod.step_pperson)
        self.assertIs(_HANDLERS["core_pperson"], mod.step_pperson)


class SqlTests(unittest.TestCase):
    def test_samples_pass_the_guard(self) -> None:
        for name, sql in pperson_samples():
            self.assertEqual(sql_block_reason(sql), "", name)
            self.assertIn("ROW:", sql, name)
            self.assertNotIn("$(", sql)

    def test_row_sql_is_select_star(self) -> None:
        text = dict(pperson_samples())
        self.assertEqual(text["core-pperson-row"], customer_row_sql("C900001"))
        self.assertIn("SELECT t.*", text["core-pperson-row"])
        self.assertIn("HASHBYTES", text["core-pperson-row"])
        self.assertIn("dbo.Customer", text["core-pperson-row"])
        self.assertIn("N'cCusPPerson'", text["core-pperson-row"])
        self.assertIn("N'cCusDepart'", text["core-pperson-row"])
        self.assertIn("dbo.Person", text["core-pperson-other"])
        self.assertIn("dbo.Department", text["core-pperson-other"])
        self.assertIn("cCusPPerson", text["core-pperson-held"])
        self.assertIn("sys.columns", text["core-pperson-volatile"])
        self.assertIn("timestamp", text["core-pperson-volatile"])
        self.assertNotIn("crevisor", text["core-pperson-volatile"].lower())
        self.assertIn("dbo.Customer_Auth", text["core-pperson-auth"])
        self.assertIn("Privilege_Type", text["core-pperson-auth"])
        self.assertIn("cDepCode", text["core-pperson-dept"])
        self.assertIn("bDepEnd", text["core-pperson-usable"])
        self.assertIn("UPDATE dbo.Customer", text["core-pperson-restore-dept"])
        self.assertIn("UPDATE dbo.Customer_Auth", text["core-pperson-restore-auth"])
        self.assertIn("DELETE FROM dbo.Customer_Auth", text["core-pperson-delete-auth"])


class ParseTests(unittest.TestCase):
    def test_row_cells_and_nil(self) -> None:
        got = parse_row(["cCusCode\th:AB12", "cCusPPerson\t<nil>", "cCusAbbName\t甲", "noise"])
        self.assertEqual(got, {"cCusCode": "h:AB12", "cCusPPerson": "", "cCusAbbName": "甲"})

    def test_receipt_date_and_mnemonic_count(self) -> None:
        ignore = volatile_set([])
        before = {"dLRDate": "h:0", "cCusMnemCode": ""}
        after = {"dLRDate": "h:1", "cCusMnemCode": "h:1"}
        self.assertEqual(changed(before, after, ignore), ["cCusMnemCode", "dLRDate"])
        self.assertEqual(changed({"cCusMnemCode": "h:0"}, {"cCusMnemCode": "h:1"}, ignore), ["cCusMnemCode"])

    def test_volatile_diff_and_health_list(self) -> None:
        ignore = volatile_set(["pubufts", "cModifyPerson"])
        before = {"cCusPPerson": "A", "dLRDate": "h:0", "pubufts": "1", "cModifyPerson": "old"}
        after = {"cCusPPerson": "B", "dLRDate": "h:0", "pubufts": "2", "cModifyPerson": "new"}
        self.assertEqual(changed(before, after, ignore), ["cCusPPerson"])
        self.assertTrue(listed_exceptions(_EXC))
        self.assertTrue(listed_exceptions({"api_read_only_exceptions": _EXC["read_only_exceptions"]}))
        self.assertFalse(listed_exceptions({}))
        self.assertFalse(listed_exceptions({"read_only_exceptions": []}))
        payload = {"ok": True, "code": "C900001", "before": "A", "after": "B"}
        self.assertEqual(reply_write(payload, "C900001", "A", "B"), "")
        self.assertIn("before", reply_write(payload, "C900001", "X", "B"))
        moved = {**payload, "dept_before": "D901", "dept_after": "D902"}
        self.assertEqual(reply_write(moved, "C900001", "A", "B", ("D901", "D902")), "")
        self.assertIn("dept_after", reply_write(moved, "C900001", "A", "B", ("D901", "D9")))
        dry = {"ok": True, "dry_run": True, "mode": "validate", "code": "C900001", "before": "A"}
        self.assertEqual(reply_dry(dry, "C900001", "A"), "")
        self.assertEqual(person_body("C900001", "EX040002", "EX040001", True)["dry_run"], True)
        self.assertEqual(parse_auth(["1\tD901\t0", "2\tX\t1"]), [("1", "D901", "0"), ("2", "X", "1")])
        self.assertEqual(check_auth([("1", "D901", "0")], [("1", "D902", "0")], "D902", True), "")
        self.assertIn("不应变化", check_auth([("1", "D901", "0")], [("1", "D902", "0")], "D902", False))
        self.assertIn("缺少部门行", check_auth([], [], "D902", True))


class ReplayTests(unittest.TestCase):
    def test_gated_lane_passes_and_restores(self) -> None:
        store = _Store()
        client = _Client(store, _EXC)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertEqual(store.row["cCusPPerson"], "EX040001")
        self.assertEqual(store.row["cCusDepart"], "D901")
        self.assertEqual(store.row["cCusAbbName"], "甲")
        self.assertEqual(store.row["dLRDate"], "h:lr0")
        self.assertEqual(store.row["cCusMnemCode"], "h:mn0")
        self.assertEqual(store.auth.get("C900001", []), [])
        self.assertTrue(any("DELETE FROM dbo.Customer_Auth" in query for query in store.queries))
        people = _bodies(client, "customer_person")
        self.assertTrue(people[0].get("dry_run"))
        self.assertEqual(people[0]["person"], "EX040002")
        self.assertEqual(people[0]["expect"], "EX040001")
        self.assertTrue(people[1]["idempotency_key"].startswith("e2e-pperson-"))
        self.assertEqual(people[1]["person"], "EX040002")
        self.assertNotIn("dry_run", people[1])
        self.assertEqual(people[2]["expect"], "EX040001")
        self.assertEqual(people[3]["person"], "ZZNOSUCH")
        self.assertEqual(people[4]["code"], "ZZNOCUS01")
        self.assertEqual(people[-1]["person"], "EX040001")
        self.assertEqual(people[-1]["expect"], "EX040002")
        updates = _bodies(client, "update")
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["fields"], {"spec_operator": "EX040002"})
        self.assertEqual(set(_ops(client)), {"customer_person", "update"})

    def test_open_lane_skips_archives_update(self) -> None:
        store = _Store()
        client = _Client(store, {})
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertEqual(store.row["cCusPPerson"], "EX040001")
        self.assertEqual(store.row["cCusDepart"], "D901")
        self.assertEqual(_bodies(client, "update"), [])
        people = _bodies(client, "customer_person")
        self.assertTrue(people[0].get("dry_run"))
        self.assertEqual(people[-1]["person"], "EX040001")

    def test_unknown_person_must_be_409(self) -> None:
        store = _Store()
        client = _Client(store, _EXC)
        orig = client._person

        def boom(fields: dict) -> dict:
            if fields.get("person") == "ZZNOSUCH":
                raise U8CoError(400, "bad_request", "参数无效")
            return orig(fields)

        client._person = boom  # type: ignore[method-assign]
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("未知业务员", note)
        self.assertEqual(store.row["cCusPPerson"], "EX040001")

    def test_expect_mismatch_must_be_409(self) -> None:
        store = _Store()
        client = _Client(store, _EXC)
        orig = client._person

        def boom(fields: dict) -> dict:
            if fields.get("expect") == "EX040001" and fields.get("person") == "EX040001":
                if not fields.get("dry_run") and store.row["cCusPPerson"] == "EX040002":
                    return orig({**fields, "expect": "EX040002"})
            return orig(fields)

        client._person = boom  # type: ignore[method-assign]
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("expect 不符", note)
        self.assertEqual(store.row["cCusPPerson"], "EX040001")

    def test_restore_after_write_error(self) -> None:
        store = _Store()
        client = _Client(store, _EXC)
        orig = client._person

        def boom(fields: dict) -> dict:
            if fields.get("person") == "EX040002" and not fields.get("dry_run"):
                orig(fields)
                raise U8CoError(500, "busy", "超时")
            return orig(fields)

        client._person = boom  # type: ignore[method-assign]
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("改专管业务员", note)
        self.assertEqual(store.row["cCusPPerson"], "EX040001")

    def test_restore_after_failed_write_check(self) -> None:
        store = _Store()
        client = _Client(store, _EXC)
        orig = client._person

        def boom(fields: dict) -> dict:
            payload = orig(fields)
            if fields.get("person") == "EX040002" and not fields.get("dry_run"):
                return {**payload, "after": "WRONG"}
            return payload

        client._person = boom  # type: ignore[method-assign]
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "FAIL")
        self.assertIn("after", note)
        self.assertEqual(store.row["cCusPPerson"], "EX040001")

    def test_picks_customer_with_person_when_empty(self) -> None:
        store = _Store({**_ROW, "cCusPPerson": ""})
        store.add({**_ROW, "cCusCode": "C900002", "cCusPPerson": "EX040001"})
        client = _Client(store, {})
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertEqual(store.rows["C900001"]["cCusPPerson"], "")
        self.assertEqual(store.rows["C900002"]["cCusPPerson"], "EX040001")
        self.assertEqual(store.rows["C900002"]["cCusDepart"], "D901")
        codes = {body["code"] for body in _bodies(client, "customer_person")}
        self.assertIn("C900002", codes)
        self.assertNotIn("C900001", codes)

    def test_inconsistent_dept_is_left_alone(self) -> None:
        store = _Store({**_ROW, "cCusDepart": "D999"})
        client = _Client(store, {})
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertEqual(store.row["cCusDepart"], "D999")
        self.assertEqual(store.auth.get("C900001", []), [])
        self.assertFalse(any("DELETE FROM dbo.Customer_Auth" in query for query in store.queries))

    def test_empty_dept_follows_and_restores(self) -> None:
        store = _Store({**_ROW, "cCusDepart": ""})
        client = _Client(store, {})
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertEqual(store.row["cCusDepart"], "")
        self.assertEqual(store.auth.get("C900001", []), [])
        self.assertTrue(any("DELETE FROM dbo.Customer_Auth" in query for query in store.queries))
        self.assertTrue(any("UPDATE dbo.Customer SET cCusDepart = NULL" in query for query in store.queries))

    def test_dry_run_must_not_write(self) -> None:
        store = _Store()
        client = _Client(store, {})
        orig = client._person

        def leak(fields: dict) -> dict:
            out = orig(fields)
            if fields.get("dry_run"):  # 预演照常返回 200，却偷偷改了行
                store.row["cCusPPerson"] = "LEAKED"
                store.row["dLRDate"] = "h:changed"
            return out

        client._person = leak  # type: ignore[method-assign]
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("预演不应改行", note)


if __name__ == "__main__":
    unittest.main()
