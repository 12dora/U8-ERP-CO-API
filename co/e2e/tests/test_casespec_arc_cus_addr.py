"""客户收货地址用例 `archive-customer-address` 的登记、分线、SQL 守卫、纯函数和一轮回放。不连接服务器。"""

from __future__ import annotations

import re
import unittest
from collections.abc import Callable
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError
from co.e2e.casespec import arc_cus_addr as mod
from co.e2e.casespec.arc_cus_bodies import (
    addr_body,
    cell_text,
    default_edits,
    default_ids,
    dump_diff,
    dump_problem,
    dump_view,
    edited,
    parse_dump,
    payload_view,
    reply_addr,
    reply_addr_dry,
    row_of,
    sequence,
)
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.arc_write import CORE_HANDLERS
from co.e2e.casespec.runner import _HANDLERS
from co.e2e.casespec.arc_cus_sql import addr_dump_sql, cus_samples
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "archive-customer-address"
_CUS = {
    "cCusCode": "C900001",
    "cCusAbbName": "示例简称",
    "cDCCode": "33",
    "dEndDate": "",
    "cModifyPerson": "h:mp0",
    "dModifyDate": "h:md0",
    "pubufts": "h:1",
    "cCusName": "h:name0",
}
_LIT = r"N'((?:[^']|'')*)'"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _row(rid: int, code: str, name: str | None, unit: str | None, flag: int) -> dict:
    return {
        "id": rid,
        "cCusCode": "C900001",
        "cAddCode": code,
        "cDeliverAdd": name,
        "cDeliverUnit": unit,
        "cLinkPerson": "张三",
        "bDefault": flag,
        "cCusAddressGUID": f"guid-{rid}",
    }


def _rows() -> dict[int, dict]:
    return {
        1: _row(1, "1", "示例车间一", "", 1),  # 空串收货单位：路由清空会写 NULL，比对要当同一个
        2: _row(2, "2", "示例车间二", None, 0),
        3: {**_row(3, "9", "仓库", "收货人", 0), "cCusCode": "C900002"},
    }


def _text(value: object) -> str:
    return "<nil>" if value is None else str(value)


class _Store:
    """两个客户的收货地址表 + 一个客户行；SQL 按关键字应答，路由由 _Client 改地址表。"""

    def __init__(self, rows: dict[int, dict] | None = None) -> None:
        self.rows = _rows() if rows is None else rows
        self.customer = dict(_CUS)
        self.pick = "C900001"
        self.used: tuple[str, str] | None = ("C900002", "9")
        self.refuse_insert = False
        self.next_id = 10
        self.queries: list[str] = []

    def of(self, cus: str) -> list[dict]:
        return [row for _rid, row in sorted(self.rows.items()) if row["cCusCode"] == cus]

    def snapshot(self, cus: str = "C900001") -> list[dict]:
        """库里的行；文本列 NULL 与空串当同一个（路由清空写 NULL）。"""
        rows = [dict(row) for row in self.of(cus)]
        for row in rows:
            for col in ("cDeliverAdd", "cDeliverUnit"):
                row[col] = row[col] or ""
        return rows

    def guarded(self, _env: object, query: str, _label: str) -> str:
        self.queries.append(query)
        for key, handler in (
            ("GROUP BY a.cCusCode", self._pick),
            ("dbo.DispatchList", self._used),
            ("INSERT INTO dbo.CusDeliverAdd", self._insert),
            ("DELETE FROM dbo.CusDeliverAdd", self._delete),
            ("UPDATE dbo.CusDeliverAdd", self._update),
            ("COUNT(*)", self._count),
            ("dbo.CusDeliverAdd AS t", self._dump),
            ("dbo.Customer AS t", self._customer),
        ):
            if key in query:
                return handler(query)
        raise AssertionError("没有料到的 SQL：" + query[:80])

    def _pick(self, _query: str) -> str:
        return f"ROW:{self.pick}\n" if self.pick else ""

    def _used(self, _query: str) -> str:
        return f"ROW:{self.used[0]}\t{self.used[1]}\n" if self.used else ""

    def _dump(self, query: str) -> str:
        cus = re.search(r"t\.cCusCode = N'(\w+)'", query).group(1)  # type: ignore[union-attr]
        return "".join(f"ROW:{row['id']}\t{col}\t{_text(val)}\n" for row in self.of(cus) for col, val in row.items())

    def _customer(self, _query: str) -> str:
        return "".join(f"ROW:{key}\t{val}\n" for key, val in self.customer.items())

    def _count(self, query: str) -> str:
        hit = re.search(rf"cCusCode = {_LIT} AND cAddCode = {_LIT}", query)
        assert hit is not None, query
        cus, addr = (item.replace("''", "'") for item in hit.groups())
        return f"ROW:{sum(1 for row in self.of(cus) if row['cAddCode'] == addr)}\n"

    def _insert(self, query: str) -> str:
        if self.refuse_insert:
            return "ROW:0\n"
        cus, addr = re.search(r"VALUES \(N'([^']*)', N'([^']*)'", query).groups()  # type: ignore[union-attr]
        self.rows[self.next_id] = {**_row(self.next_id, addr, "E2E临时地址", "E2E临时单位", 0), "cCusCode": cus}
        self.next_id += 1
        return "ROW:1\n"

    def _delete(self, query: str) -> str:
        cus = re.search(r"cCusCode = N'(\w+)'", query).group(1)  # type: ignore[union-attr]
        codes = re.findall(r"N'(\w+)'", query.split(" IN (", 1)[1])
        gone = [rid for rid, row in self.rows.items() if row["cCusCode"] == cus and row["cAddCode"] in codes]
        for rid in gone:
            del self.rows[rid]
        return f"ROW:{len(gone)}\n"

    def _update(self, query: str) -> str:
        pat = rf"cAddCode = {_LIT},\s+cDeliverAdd = (NULL|{_LIT}),\s+cDeliverUnit = (NULL|{_LIT}),\s+bDefault = (\d)\s+WHERE id = (\d+)"
        hit = re.search(pat, query)
        assert hit is not None, query
        code, _name, name, _u, unit, flag, rid = hit.groups()
        row = self.rows.get(int(rid))
        if row is None:
            return "ROW:0\n"
        row.update(cAddCode=code.replace("''", "'"), bDefault=int(flag))
        row["cDeliverAdd"] = None if _name == "NULL" else name.replace("''", "'")
        row["cDeliverUnit"] = None if _u == "NULL" else unit.replace("''", "'")
        return "ROW:1\n"


def _view(row: dict) -> dict:
    return {
        "code": row["cAddCode"],
        "name": row["cDeliverAdd"] or "",
        "unit": row["cDeliverUnit"] or "",
        "default": bool(row["bDefault"]),
    }


class _Client:
    def __init__(self, store: _Store) -> None:
        self.store = store
        self.calls: list[tuple[str, dict]] = []

    def health(self) -> dict:
        return {}

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        if not route.endswith("/customer_address"):
            raise U8CoError(404, "not_found", "路由不存在")
        return self._address(fields)

    def _find(self, fields: dict) -> tuple[list[dict], dict]:
        code = str(fields["code"])
        rows = self.store.of(code)
        if not rows and code != "C900001":
            raise U8CoError(404, "not_found", "客户不存在")
        for row in rows:
            if row["cAddCode"] == fields["address"]:
                return rows, row
        raise U8CoError(404, "address_not_found", "收货地址不存在")

    def _validate(self, want: dict, expect: dict, row: dict) -> None:
        if not want or set(want) - {"code", "name", "unit", "default"}:
            raise U8CoError(400, "bad_request", "字段不合法")
        if any(key != "code" and key not in expect for key in want):
            raise U8CoError(400, "bad_request", "缺少 expect")
        current = _view(row)
        for key, val in expect.items():
            if current[key] != val:
                raise U8CoError(409, "state_mismatch", f"{key} 已变化")

    def _check_code(self, rows: list[dict], row: dict, new: str) -> None:
        if new == row["cAddCode"]:
            return
        if any(item["cAddCode"] == new for item in rows):
            raise U8CoError(409, "address_code_taken", "目标编码已存在")
        if self.store.used == (row["cCusCode"], row["cAddCode"]):
            raise U8CoError(409, "address_in_use", "收货地址已被单据引用，不能改编码")

    def _address(self, fields: dict) -> dict:
        rows, row = self._find(fields)
        want = fields.get("fields") or {}
        self._validate(want, fields.get("expect") or {}, row)
        new = str(want.get("code", row["cAddCode"]))
        self._check_code(rows, row, new)
        before = [_view(item) for item in rows]
        after = {**row, "cAddCode": new}
        for key, col in (("name", "cDeliverAdd"), ("unit", "cDeliverUnit")):
            if key in want:
                after[col] = want[key] or None
        if "default" in want:
            after["bDefault"] = int(want["default"])
        flag = _view(after) != _view(row)
        out = {"ok": True, "code": fields["code"], "address": fields["address"], "new_address": new, "changed": flag}
        if fields.get("dry_run"):
            return {**out, "dry_run": True, "mode": "validate", "before": before}
        if flag:
            self._apply(rows, row, after)
        return {**out, "before": before, "after": [_view(item) for item in rows]}

    def _apply(self, rows: list[dict], row: dict, after: dict) -> None:
        if after["bDefault"]:
            for item in rows:
                item["bDefault"] = 0
        row.update(after)


def _env(client: _Client) -> SimpleNamespace:
    op = SimpleNamespace(acc="801", year="2026", code="op001", password="pw")
    return SimpleNamespace(client=client, operator=op, year="2026", today="2026-09-28", operator_name="")


def _run(client: _Client, store: _Store) -> tuple[str, str]:
    spec = mod.cusaddr_cases()[0]
    with mock.patch.object(mod, "run_guarded", store.guarded):
        return mod.step_cusaddr(_env(client), spec, spec.steps[0])


def _bodies(client: _Client) -> list[dict]:
    return [body for _route, body in client.calls]


class CatalogTests(unittest.TestCase):
    def test_case_registered(self) -> None:
        (spec,) = mod.cusaddr_cases()
        self.assertEqual(spec.name, _NAME)
        self.assertEqual(known_names().count(_NAME), 1)
        self.assertTrue(spec.requires_login)
        self.assertEqual(spec.data, ())
        self.assertIn("arc:customer", auto_tags(spec))
        self.assertIn("arc:write", auto_tags(spec))
        self.assertEqual(spec.routes, ("/v1/archives/customer_address",))
        self.assertEqual(spec.steps[0].kind, "core_cusaddr")
        self.assertIs(CORE_HANDLERS["core_cusaddr"], mod.step_cusaddr)
        self.assertIs(_HANDLERS["core_cusaddr"], mod.step_cusaddr)


class SqlTests(unittest.TestCase):
    def test_samples_pass_the_guard(self) -> None:
        for name, sql in cus_samples():
            self.assertEqual(sql_block_reason(sql), "", name)
            self.assertIn("ROW:", sql, name)
            self.assertNotIn("$(", sql)

    def test_dump_sql_reads_every_column_of_the_customer(self) -> None:
        sql = addr_dump_sql("C900001")
        self.assertIn("SELECT t.* FROM dbo.CusDeliverAdd AS t", sql)
        self.assertIn("t.cCusCode = N'C900001'", sql)
        self.assertNotIn("HASHBYTES", sql)
        with self.assertRaises(ValueError):
            addr_dump_sql("C9'00001")


class PureTests(unittest.TestCase):
    def _dump(self) -> dict:
        return parse_dump(
            [
                "1\tcAddCode\t1",
                "1\tcDeliverAdd\t示例车间一",
                "1\tcDeliverUnit",
                "1\tbDefault\t1",
                "2\tcAddCode\t2",
                "2\tcDeliverAdd\t<nil>",
                "2\tcDeliverUnit\t<nil>",
                "2\tbDefault\t0",
                "noise",
            ]
        )

    def test_parse_and_view(self) -> None:
        dump = self._dump()
        self.assertEqual(dump["1"]["cDeliverUnit"], "")
        self.assertEqual(dump_view(dump), [("1", "示例车间一", "", True), ("2", "", "", False)])
        self.assertEqual(cell_text(dump, "2", "cDeliverAdd"), "")
        self.assertEqual(default_ids(dump), ["1"])
        self.assertEqual(row_of(dump, "2"), "2")
        self.assertEqual(row_of(dump, "7"), "")

    def test_diff_treats_null_and_empty_alike_for_text_columns_only(self) -> None:
        dump = self._dump()
        other = edited(dump, {"2": {"cDeliverUnit": ""}, "1": {"bDefault": "True"}})
        self.assertEqual(dump_diff(dump, other), [])
        self.assertEqual(dump_diff(dump, edited(dump, {"1": {"cAddCode": "x"}})), ["1.cAddCode"])
        self.assertEqual(dump_diff(dump, edited(dump, {"1": {"cLinkPerson": "<nil>"}})), ["1.cLinkPerson"])
        self.assertEqual(dump_diff(dump, {"1": dump["1"]}), ["2:缺失"])
        self.assertEqual(dump_diff({"1": dump["1"]}, dump), ["2:新增"])
        self.assertIn("1.bDefault", dump_problem(dump, edited(dump, {"1": {"bDefault": "0"}})))
        self.assertEqual(dump_problem(dump, edited(dump, {})), "")

    def test_default_edits_and_sequence(self) -> None:
        dump = self._dump()
        self.assertEqual(default_edits(dump, "2", True), {"1": {"bDefault": "0"}, "2": {"bDefault": "1"}})
        self.assertEqual(default_edits(dump, "2", False), {"2": {"bDefault": "0"}})
        self.assertEqual(sequence("新", "旧", False), ["新", "旧"])
        self.assertEqual(sequence("新", "旧", True), ["新", "", "旧"])
        self.assertEqual(sequence("新", "", True), ["新", ""])

    def test_bodies(self) -> None:
        dry = addr_body("C900001", "1", {"name": "x"}, {"name": "y"}, True)
        self.assertTrue(dry["dry_run"])
        self.assertNotIn("idempotency_key", dry)
        real = addr_body("C900001", "1", {"code": "2"})
        self.assertTrue(real["idempotency_key"].startswith("e2e-cusaddr-"))
        self.assertNotIn("expect", real)

    def test_reply_checks(self) -> None:
        dump = self._dump()
        rows = [
            {"code": "1", "name": "示例车间一", "unit": "", "default": True},
            {"code": "2", "name": None, "unit": None, "default": False},
        ]
        good = {"ok": True, "code": "C900001", "address": "1", "new_address": "1", "changed": True, "before": rows, "after": rows}
        self.assertEqual(payload_view(rows), dump_view(dump))
        self.assertEqual(reply_addr(good, ("C900001", "1"), True, (dump, dump)), "")
        self.assertIn("changed", reply_addr(good, ("C900001", "1"), False, (dump, dump)))
        self.assertIn("new_address", reply_addr(good, ("C900001", "9"), True, (dump, dump)))
        self.assertIn("after", reply_addr({**good, "after": rows[:1]}, ("C900001", ""), True, (dump, dump)))
        self.assertIn("before", reply_addr({**good, "before": "x"}, ("C900001", ""), True, (dump, dump)))
        dry = {"ok": True, "code": "C900001", "dry_run": True, "mode": "validate", "before": rows}
        self.assertEqual(reply_addr_dry(dry, "C900001", dump), "")
        self.assertIn("dry_run", reply_addr_dry({**dry, "dry_run": False}, "C900001", dump))


class ReplayTests(unittest.TestCase):
    def test_passes_and_restores_every_row(self) -> None:
        store = _Store()
        before = store.snapshot()
        owner = store.snapshot("C900002")
        client = _Client(store)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertEqual(store.snapshot(), before)
        self.assertEqual(store.snapshot("C900002"), owner)
        self.assertEqual(store.customer, _CUS)
        self.assertNotIn("跳过", note)
        self.assertTrue(any("DELETE FROM dbo.CusDeliverAdd" in query for query in store.queries))

    def test_covers_each_field_and_error(self) -> None:
        store = _Store()
        client = _Client(store)
        _run(client, store)
        bodies = _bodies(client)
        self.assertTrue(bodies[0]["dry_run"])
        keys = [tuple(body["fields"]) for body in bodies]
        for want in (("name",), ("unit",), ("default",), ("code",)):
            self.assertIn(want, keys)
        units = [body["fields"]["unit"] for body in bodies if "unit" in body["fields"]]
        self.assertEqual(units, ["E2E改单位", ""])
        flags = [body["fields"]["default"] for body in bodies if "default" in body["fields"]]
        self.assertEqual(flags, [True, True, True])  # 切换、改回、以及缺 expect 的 400
        codes = [body for body in bodies if "code" in body["fields"]]
        self.assertEqual(len(codes), 4)  # 临时地址改过去、改回、撞已有编码、被引用编码
        self.assertTrue(all("expect" not in body for body in codes))
        self.assertEqual(codes[-1]["code"], "C900002")
        self.assertEqual(codes[-1]["address"], "9")
        self.assertEqual(codes[-2]["fields"]["code"], "1")
        self.assertTrue(any(body["code"] == "ZZNOCUS01" for body in bodies))
        self.assertTrue(any(body["address"].startswith("ZZNOA") for body in bodies))
        self.assertTrue(all("idempotency_key" in body for body in bodies[1:]))

    def test_single_address_customer_skips_default_flip(self) -> None:
        rows = {1: _row(1, "1", "示例车间一", None, 1)}
        store = _Store(rows)
        store.used = None
        client = _Client(store)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertIn("默认地址切换", note)
        self.assertIn("被发货单引用", note)
        flags = [body["fields"]["default"] for body in _bodies(client) if "default" in body["fields"]]
        self.assertEqual(flags, [True])  # 只剩缺 expect 的 400
        self.assertEqual(len(store.of("C900001")), 1)

    def test_no_default_row_is_restored(self) -> None:
        store = _Store()
        store.rows[1]["bDefault"] = 0
        before = store.snapshot()
        verdict, note = _run(_Client(store), store)
        self.assertEqual(verdict, "PASS", note)
        self.assertEqual(store.snapshot(), before)

    def test_refused_insert_skips_recode(self) -> None:
        store = _Store()
        store.refuse_insert = True
        client = _Client(store)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertIn("改编码", note)
        self.assertEqual(len([b for b in _bodies(client) if b["fields"].get("code") == "1"]), 0)
        self.assertEqual(len(store.of("C900001")), 2)

    def test_not_applicable_without_addresses(self) -> None:
        store = _Store()
        store.pick = ""
        verdict, note = _run(_Client(store), store)
        self.assertEqual(verdict, "N/A", note)


class BrokenRouteTests(unittest.TestCase):
    def _broken(self, patch: Callable) -> tuple[str, str, _Store, list[dict]]:
        store = _Store()
        before = store.snapshot()
        client = _Client(store)
        orig = client._address

        def wrapped(fields: dict) -> dict:
            return patch(store, orig, fields)

        client._address = wrapped  # type: ignore[method-assign]
        verdict, note = _run(client, store)
        return verdict, note, store, before

    def test_default_that_keeps_other_defaults_fails(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            out = orig(fields)
            if fields.get("fields", {}).get("default") is True and out["changed"]:
                store.rows[1]["bDefault"] = 1
            return out

        verdict, note, store, before = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("默认地址设为 True", note)
        self.assertEqual(store.snapshot(), before)

    def test_unreverted_rename_is_repaired_by_sql_and_reported(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            if fields.get("fields", {}).get("name") == "示例车间一":
                return {"ok": True, "code": "C900001", "changed": False}  # 改回被吞掉
            return orig(fields)

        verdict, note, store, before = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("changed", note)
        self.assertEqual(store.snapshot(), before)
        self.assertTrue(any("UPDATE dbo.CusDeliverAdd" in query for query in store.queries))

    def test_dry_run_that_writes_fails(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            out = orig(fields)
            if fields.get("dry_run"):
                store.rows[1]["cDeliverAdd"] = "LEAKED"
            return out

        verdict, note, _store, _before = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("cDeliverAdd", note)

    def test_wrong_before_block_fails(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            out = orig(fields)
            if "before" in out and not fields.get("dry_run") and fields["fields"].get("unit") == "E2E改单位":
                out["before"] = out["before"][:1]
            return out

        verdict, note, store, before = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("before", note)
        self.assertEqual(store.snapshot(), before)

    def test_missing_expect_must_be_400(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            if "expect" not in fields and fields["fields"] == {"name": "E2E改名示例车间一"}:
                return orig({**fields, "expect": {"name": "示例车间一"}})
            return orig(fields)

        verdict, note, _store, _before = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("缺 expect", note)

    def test_referenced_code_must_be_409(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            if fields["code"] == "C900002":
                store.used = None
            return orig(fields)

        verdict, note, store, _before = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("被单据引用的编码", note)

    def test_customer_row_must_stay_untouched(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            out = orig(fields)
            if out["changed"] and not fields.get("dry_run"):
                store.customer["pubufts"] = "h:2"
            return out

        verdict, note, store, before = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("客户行", note)
        self.assertEqual(store.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
