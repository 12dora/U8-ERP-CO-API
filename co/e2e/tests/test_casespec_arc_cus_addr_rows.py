"""客户收货地址新增 / 删除用例 `archive-customer-address-rows` 的登记、分线、SQL 守卫、纯函数和一轮回放。不连接服务器。"""

from __future__ import annotations

import re
import unittest
from collections.abc import Callable
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError
from co.e2e.casespec import arc_cus_addr_rows as mod
from co.e2e.casespec.arc_cus_bodies import parse_dump
from co.e2e.casespec.arc_cus_addr_rows_bodies import (
    add_body,
    add_calls,
    added_problem,
    clean_codes,
    delete_body,
    delete_calls,
    expect_of,
    reply_rows,
    without,
)
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.arc_write import CORE_HANDLERS
from co.e2e.casespec.runner import _HANDLERS
from co.e2e.casespec.arc_cus_addr_rows_sql import addr_used_free_sql, cusrows_samples
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "archive-customer-address-rows"
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


def _row(rid: int, code: str, name: str | None, unit: str | None, flag: int, **over: str) -> dict:
    row = {
        "id": rid,
        "cCusCode": "C900001",
        "cAddCode": code,
        "cDeliverAdd": name,
        "cDeliverUnit": unit,
        "cEnglishAdd": None,
        "cLinkPerson": None,
        "bDefault": flag,
        "cCusAddressGUID": f"guid-{rid}",
    }
    row.update(over)
    return row


def _rows() -> dict[int, dict]:
    return {
        1: _row(1, "1", "示例车间", "", 1),  # 空串收货单位：比对要和 NULL 当同一个
        2: _row(2, "2", "示例车间二", None, 0),
        3: _row(3, "9", "仓库", "收货人", 0, cCusCode="C900002"),
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
    """按合同实现 customer_address_add / customer_address_delete，和 customer_address 里只改默认标志的那部分。"""

    def __init__(self, store: _Store) -> None:
        self.store = store
        self.calls: list[tuple[str, dict]] = []

    def health(self) -> dict:
        return {}

    def call(self, route: str, fields: dict) -> dict:
        op = route.rsplit("/", 1)[-1]
        self.calls.append((op, dict(fields)))
        handler = {
            "customer_address_add": self._add,
            "customer_address_delete": self._delete,
            "customer_address": self._move,
        }.get(op)
        if handler is None:
            raise U8CoError(404, "not_found", "路由不存在")
        return handler(fields)

    def _rows(self, fields: dict) -> tuple[str, list[dict]]:
        code = str(fields["code"])
        rows = self.store.of(code)
        if not rows and code != "C900001":
            raise U8CoError(404, "not_found", "客户不存在")
        return code, rows

    def _head(self, fields: dict, rows: list[dict], flag: bool) -> dict:
        return {"ok": True, "code": fields["code"], "address": fields["address"], "changed": flag}

    def _add(self, fields: dict) -> dict:
        code, rows = self._rows(fields)
        addr, body = str(fields.get("address", "")), fields.get("fields") or {}
        name = body.get("name")
        if not addr or not isinstance(name, str) or not 1 <= len(name) <= 255 or set(body) - {"name", "unit", "default"}:
            raise U8CoError(400, "bad_request", "字段不合法")
        want = {"code": addr, "name": name, "unit": body.get("unit") or "", "default": bool(body.get("default", False))}
        before = [_view(row) for row in rows]
        same = next((row for row in rows if row["cAddCode"] == addr), None)
        if same is not None and _view(same) != want:
            raise U8CoError(409, "address_code_taken", "地址编码已存在")
        if same is None and any(row["cDeliverAdd"] == name for row in rows):
            raise U8CoError(409, "address_name_taken", "地址名称已存在")
        out = self._head(fields, rows, same is None)
        if fields.get("dry_run"):
            return {**out, "dry_run": True, "mode": "validate", "before": before}
        if same is None:
            self._insert(code, rows, want)
        return {**out, "before": before, "after": [_view(row) for row in self.store.of(code)]}

    def _insert(self, code: str, rows: list[dict], want: dict) -> None:
        if want["default"]:
            for row in rows:
                row["bDefault"] = 0
        rid = self.store.next_id
        self.store.next_id += 1
        new = _row(rid, want["code"], want["name"], want["unit"] or None, int(want["default"]), cCusCode=code)
        self.store.rows[rid] = new

    def _delete(self, fields: dict) -> dict:
        code, rows = self._rows(fields)
        want = fields.get("expect")
        if not isinstance(want, dict) or set(want) != {"name", "unit", "default"}:
            raise U8CoError(400, "bad_request", "expect 要给名称、收货单位、默认标志")
        row = next((item for item in rows if item["cAddCode"] == fields["address"]), None)
        if row is None:
            raise U8CoError(404, "address_not_found", "收货地址不存在")
        if any(_view(row)[key] != val for key, val in want.items()):
            raise U8CoError(409, "state_mismatch", "地址已变化")
        if self.store.used == (code, row["cAddCode"]):
            raise U8CoError(409, "address_in_use", "收货地址已被单据引用")
        if row["bDefault"] and len(rows) > 1:
            raise U8CoError(409, "address_is_default", "默认地址不能删")
        before = [_view(item) for item in rows]
        out = self._head(fields, rows, True)
        if fields.get("dry_run"):
            return {**out, "dry_run": True, "mode": "validate", "before": before}
        self.store.rows.pop(row["id"])
        return {**out, "before": before, "after": [_view(item) for item in self.store.of(code)]}

    def _move(self, fields: dict) -> dict:
        code, rows = self._rows(fields)
        row = next((item for item in rows if item["cAddCode"] == fields["address"]), None)
        if row is None:
            raise U8CoError(404, "address_not_found", "收货地址不存在")
        flag = bool((fields.get("fields") or {})["default"])
        if _view(row)["default"] != (fields.get("expect") or {})["default"]:
            raise U8CoError(409, "state_mismatch", "默认标志已变化")
        before = [_view(item) for item in rows]
        if flag:
            for item in rows:
                item["bDefault"] = 0
        row["bDefault"] = int(flag)
        out = self._head(fields, rows, before != [_view(item) for item in rows])
        return {**out, "new_address": row["cAddCode"], "before": before, "after": [_view(item) for item in rows]}


def _env(client: _Client) -> SimpleNamespace:
    op = SimpleNamespace(acc="801", year="2026", code="op001", password="pw")
    return SimpleNamespace(client=client, operator=op, year="2026", today="2026-09-28", operator_name="")


def _run(client: _Client, store: _Store) -> tuple[str, str]:
    spec = mod.cusrows_cases()[0]
    with mock.patch.object(mod, "run_guarded", store.guarded):
        return mod.step_cusrows(_env(client), spec, spec.steps[0])


def _ops(client: _Client) -> list[str]:
    return [op for op, _body in client.calls]


class CatalogTests(unittest.TestCase):
    def test_case_registered(self) -> None:
        (spec,) = mod.cusrows_cases()
        self.assertEqual(spec.name, _NAME)
        self.assertEqual(known_names().count(_NAME), 1)
        self.assertTrue(spec.requires_login)
        self.assertEqual(spec.data, ())
        self.assertEqual(
            spec.routes,
            (
                "/v1/archives/customer_address_add",
                "/v1/archives/customer_address_delete",
                "/v1/archives/customer_address",
            ),
        )
        self.assertEqual(spec.steps[0].kind, "core_cusrows")
        self.assertIs(CORE_HANDLERS["core_cusrows"], mod.step_cusrows)
        self.assertIs(_HANDLERS["core_cusrows"], mod.step_cusrows)

    def test_tags_serialise_with_the_other_address_cases(self) -> None:
        (spec,) = mod.cusrows_cases()
        tags = auto_tags(spec)
        for tag in ("arc:customer", "arc:customer_address", "arc:write"):
            self.assertIn(tag, tags)
        self.assertTrue(conflicts(tags, frozenset({"arc:customer_address"})))


class SqlTests(unittest.TestCase):
    def test_samples_pass_the_guard(self) -> None:
        for name, sql in cusrows_samples():
            self.assertEqual(sql_block_reason(sql), "", name)
            self.assertIn("ROW:", sql, name)

    def test_used_pick_prefers_non_default(self) -> None:
        sql = addr_used_free_sql()
        self.assertIn("ORDER BY a.bDefault, a.id", sql)
        self.assertIn("dbo.DispatchList", sql)


class PureTests(unittest.TestCase):
    def _dump(self) -> dict:
        return parse_dump(
            [
                "1\tcCusCode\tC900001",
                "1\tcAddCode\t1",
                "1\tcDeliverAdd\t示例车间",
                "1\tcDeliverUnit",
                "1\tbDefault\t1",
                "2\tcCusCode\tC900001",
                "2\tcAddCode\t2",
                "2\tcDeliverAdd\t示例车间二",
                "2\tcDeliverUnit\t<nil>",
                "2\tbDefault\t0",
            ]
        )

    def _new_row(self, flag: str = "0", **over: str) -> dict:
        row = {
            "cCusCode": "C900001",
            "cAddCode": "E2Hab",
            "cDeliverAdd": "新地址",
            "cDeliverUnit": "新单位",
            "bDefault": flag,
            "cEnglishAdd": "<nil>",
            "cLinkPerson": "<nil>",
            "cCusAddressGUID": "guid-x",
        }
        row.update(over)
        return row

    def test_bodies(self) -> None:
        dry = add_body("C900001", "A1", {"name": "x"}, True)
        self.assertTrue(dry["dry_run"])
        self.assertNotIn("idempotency_key", dry)
        real = add_body("C900001", "A1", {"name": "x"})
        self.assertTrue(real["idempotency_key"].startswith("e2e-cusaddradd-"))
        self.assertNotIn("expect", real)
        gone = delete_body("C900001", "A1", {"name": "x", "unit": "", "default": False})
        self.assertTrue(gone["idempotency_key"].startswith("e2e-cusaddrdel-"))
        self.assertEqual(gone["expect"]["default"], False)
        self.assertNotIn("expect", delete_body("C900001", "A1", None))

    def test_expect_of_and_without(self) -> None:
        dump = self._dump()
        self.assertEqual(expect_of(dump, "1"), {"name": "示例车间", "unit": "", "default": True})
        self.assertEqual(expect_of(dump, "2"), {"name": "示例车间二", "unit": "", "default": False})
        self.assertEqual(list(without(dump, "1")), ["2"])
        self.assertEqual(list(dump), ["1", "2"])

    def test_added_problem(self) -> None:
        prev = self._dump()
        want = {"name": "新地址", "unit": "新单位", "default": False}
        now = {**prev, "7": self._new_row()}
        self.assertEqual(added_problem(prev, now, ("C900001", "E2Hab"), want), "")
        self.assertIn("没有出现", added_problem(prev, prev, ("C900001", "E2Hab"), want))
        self.assertIn("cDeliverAdd", added_problem(prev, now, ("C900001", "E2Hab"), {**want, "name": "别的"}))
        self.assertIn("bDefault", added_problem(prev, now, ("C900001", "E2Hab"), {**want, "default": True}))
        self.assertIn("cCusCode", added_problem(prev, now, ("C900002", "E2Hab"), want))
        bad_guid = {**prev, "7": self._new_row(cCusAddressGUID="<nil>")}
        self.assertIn("GUID", added_problem(prev, bad_guid, ("C900001", "E2Hab"), want))
        bad_link = {**prev, "7": self._new_row(cLinkPerson="张三")}
        self.assertIn("NULL", added_problem(prev, bad_link, ("C900001", "E2Hab"), want))
        touched = {**now, "2": {**now["2"], "cDeliverAdd": "改了"}}
        self.assertIn("2.cDeliverAdd", added_problem(prev, touched, ("C900001", "E2Hab"), want))

    def test_added_default_clears_the_others(self) -> None:
        prev = self._dump()
        want = {"name": "新地址", "unit": "新单位", "default": True}
        cleared = {"1": {**prev["1"], "bDefault": "0"}, "2": prev["2"], "7": self._new_row("1")}
        self.assertEqual(added_problem(prev, cleared, ("C900001", "E2Hab"), want), "")
        kept = {**cleared, "1": prev["1"]}
        self.assertIn("1.bDefault", added_problem(prev, kept, ("C900001", "E2Hab"), want))

    def test_clean_codes(self) -> None:
        self.assertTrue(clean_codes(self._dump()))
        dump = self._dump()
        dump["2"]["cAddCode"] = " 2"
        self.assertFalse(clean_codes(dump))
        dump["2"]["cAddCode"] = "x" * 31
        self.assertFalse(clean_codes(dump))

    def test_reply_rows(self) -> None:
        dump = self._dump()
        rows = [
            {"code": "1", "name": "示例车间", "unit": "", "default": True},
            {"code": "2", "name": "示例车间二", "unit": None, "default": False},
        ]
        good = {"ok": True, "code": "C900001", "address": "2", "changed": False, "before": rows, "after": rows}
        self.assertEqual(reply_rows(good, ("C900001", "2"), False, (dump, dump)), "")
        self.assertIn("address", reply_rows(good, ("C900001", "9"), False, (dump, dump)))
        self.assertIn("changed", reply_rows(good, ("C900001", "2"), True, (dump, dump)))
        self.assertIn("after", reply_rows({**good, "after": rows[:1]}, ("C900001", "2"), False, (dump, dump)))

    def test_rejection_catalogues(self) -> None:
        addrs = {"a": "E2Ha", "b": "E2Hb", "g": "E2Hg"}
        names = {"a": "名甲", "b": "名乙"}
        calls = add_calls("C900001", addrs, names)
        wants = {call.label: call.want for call in calls}
        self.assertEqual(wants["同编码不同名称"], (409, "address_code_taken"))
        self.assertEqual(wants["名称已被本户另一地址占用"], (409, "address_name_taken"))
        self.assertEqual(wants["客户不存在"], (404, "not_found"))
        self.assertEqual(wants["缺名称"][0], 400)
        self.assertTrue(all(call.op == "customer_address_add" for call in calls))
        taken = [call for call in calls if call.want[1] == "address_code_taken"]
        self.assertEqual({call.body["address"] for call in taken}, {"E2Ha"})
        exp = {"name": "名乙", "unit": "", "default": True}
        dels = delete_calls("C900001", addrs, exp)
        wants = {call.label: call.want for call in dels}
        self.assertEqual(wants["默认地址且有其他地址"], (409, "address_is_default"))
        self.assertEqual(wants["expect 名称不符"], (409, "state_mismatch"))
        self.assertEqual(wants["地址不存在"], (404, "address_not_found"))
        self.assertEqual(wants["缺 expect"][0], 400)
        self.assertEqual(dict(next(c for c in dels if c.label == "地址不存在").body)["address"], "E2Hg")
        self.assertNotIn("expect", next(c for c in dels if c.label == "缺 expect").body)
        self.assertEqual(next(c for c in dels if c.label == "expect 默认标志不符").body["expect"]["default"], False)


class ReplayTests(unittest.TestCase):
    def test_passes_and_restores_every_row(self) -> None:
        store = _Store()
        before = store.snapshot()
        owner = store.snapshot("C900002")
        client = _Client(store)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertEqual(store.snapshot(), before)
        self.assertEqual([row["id"] for row in store.of("C900001")], [1, 2])
        self.assertEqual(store.snapshot("C900002"), owner)
        self.assertEqual(store.customer, _CUS)
        self.assertNotIn("跳过", note)
        self.assertFalse(any("DELETE FROM" in query for query in store.queries))  # 路由自己删干净，不靠兜底 SQL

    def test_covers_add_delete_and_error_paths(self) -> None:
        store = _Store()
        client = _Client(store)
        _run(client, store)
        ops = _ops(client)
        self.assertGreaterEqual(ops.count("customer_address_add"), 12)
        self.assertGreaterEqual(ops.count("customer_address_delete"), 12)
        self.assertEqual(ops.count("customer_address"), 1)
        bodies = [body for _op, body in client.calls]
        self.assertTrue(bodies[0]["dry_run"])
        self.assertTrue(any(body.get("dry_run") and "expect" in body for body in bodies))  # 预演删除
        adds = [body for op, body in client.calls if op == "customer_address_add" and not body.get("dry_run")]
        self.assertEqual([b["address"] for b in adds[:2]], [adds[0]["address"]] * 2)  # 新增 + 重复新增同一条
        self.assertTrue(any(body["fields"].get("default") is True for body in adds))
        self.assertTrue(any(body["code"] == "ZZNOCUS01" for body in bodies))
        deletes = [body for op, body in client.calls if op == "customer_address_delete"]
        self.assertTrue(any(body["code"] == "C900002" and body["address"] == "9" for body in deletes))  # 被引用
        self.assertTrue(all("idempotency_key" in body for body in bodies if not body.get("dry_run")))

    def test_customer_without_default_row_is_restored(self) -> None:
        store = _Store()
        store.rows[1]["bDefault"] = 0
        before = store.snapshot()
        client = _Client(store)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertEqual(store.snapshot(), before)
        move = [body for op, body in client.calls if op == "customer_address"]
        self.assertEqual(move[0]["fields"], {"default": False})  # 没有原默认地址：取消新地址的默认

    def test_default_is_moved_back_to_the_original_row(self) -> None:
        store = _Store()
        client = _Client(store)
        _run(client, store)
        move = [body for op, body in client.calls if op == "customer_address"]
        self.assertEqual((move[0]["address"], move[0]["fields"]), ("1", {"default": True}))

    def test_single_address_customer(self) -> None:
        store = _Store({1: _row(1, "1", "示例车间", None, 1)})
        store.used = None
        before = store.snapshot()
        verdict, note = _run(_Client(store), store)
        self.assertEqual(verdict, "PASS", note)
        self.assertIn("没有被发货单引用", note)
        self.assertEqual(store.snapshot(), before)

    def test_used_default_address_is_skipped(self) -> None:
        store = _Store()
        store.rows[3]["bDefault"] = 1
        store.rows[4] = _row(4, "8", "备用", None, 0, cCusCode="C900002")
        verdict, note = _run(_Client(store), store)
        self.assertEqual(verdict, "PASS", note)
        self.assertIn("拒绝原因不唯一", note)

    def test_not_applicable_cases(self) -> None:
        store = _Store()
        store.pick = ""
        self.assertEqual(_run(_Client(store), store)[0], "N/A")
        store = _Store()
        store.rows[2]["bDefault"] = 1
        verdict, note = _run(_Client(store), store)
        self.assertEqual(verdict, "N/A", note)
        self.assertIn("默认地址不唯一", note)
        store = _Store()
        store.rows[2]["cAddCode"] = " 2"
        verdict, note = _run(_Client(store), store)
        self.assertEqual(verdict, "N/A", note)
        self.assertIn("不合规", note)


class BrokenRouteTests(unittest.TestCase):
    def _broken(self, name: str, patch: Callable) -> tuple[str, str, _Store, list[dict]]:
        store = _Store()
        before = store.snapshot()
        client = _Client(store)
        orig = getattr(client, name)

        def wrapped(fields: dict) -> dict:
            return patch(store, orig, fields)

        setattr(client, name, wrapped)
        verdict, note = _run(client, store)
        return verdict, note, store, before

    def test_default_add_that_keeps_other_defaults_fails(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            out = orig(fields)
            if fields.get("fields", {}).get("default") and not fields.get("dry_run") and out["changed"]:
                store.rows[1]["bDefault"] = 1
            return out

        verdict, note, store, before = self._broken("_add", patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("新增地址 b", note)
        self.assertEqual(store.snapshot(), before)

    def test_new_row_without_guid_fails(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            out = orig(fields)
            if out["changed"] and not fields.get("dry_run"):
                store.rows[store.next_id - 1]["cCusAddressGUID"] = None
            return out

        verdict, note, store, before = self._broken("_add", patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("GUID", note)
        self.assertEqual(store.snapshot(), before)

    def test_repeat_add_that_duplicates_fails(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            seen = [row for row in store.of("C900001") if row["cAddCode"] == fields.get("address")]
            if seen and not fields.get("dry_run") and fields["fields"].get("default") is None and not fields["fields"].get("name", "").endswith("~"):
                out = orig(fields)
                store.rows[store.next_id] = {**seen[0], "id": store.next_id}
                store.next_id += 1
                return {**out, "changed": True}
            return orig(fields)

        verdict, note, store, before = self._broken("_add", patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertEqual(store.snapshot(), before)

    def test_dry_run_that_writes_fails(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            out = orig(fields)
            if fields.get("dry_run"):
                store.rows[1]["cDeliverAdd"] = "LEAKED"
            return out

        verdict, note, _store, _before = self._broken("_add", patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("预演新增", note)

    def test_delete_of_default_with_siblings_must_be_409(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            row = next((r for r in store.of(fields["code"]) if r["cAddCode"] == fields["address"]), None)
            exact = row is not None and fields.get("expect") == {"name": row["cDeliverAdd"] or "", "unit": row["cDeliverUnit"] or "", "default": True}
            if exact and len(store.of(fields["code"])) > 1 and not fields.get("dry_run"):
                store.rows.pop(row["id"])
                return {"ok": True, "code": fields["code"], "address": fields["address"], "changed": True, "before": [], "after": []}
            return orig(fields)

        verdict, note, _store, _before = self._broken("_delete", patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("默认地址且有其他地址", note)

    def test_delete_that_ignores_expect_fails(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            exp = fields.get("expect") or {}
            if str(exp.get("name", "")).endswith("~"):
                row = next(r for r in store.of(fields["code"]) if r["cAddCode"] == fields["address"])
                return orig({**fields, "expect": {**exp, "name": row["cDeliverAdd"]}})
            return orig(fields)

        verdict, note, store, before = self._broken("_delete", patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("expect 名称不符", note)

    def test_delete_of_referenced_address_must_be_409(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            if fields["code"] == "C900002":
                store.used = None
            return orig(fields)

        verdict, note, _store, _before = self._broken("_delete", patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("被单据引用的地址", note)

    def test_customer_row_must_stay_untouched(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            out = orig(fields)
            if out["changed"] and not fields.get("dry_run"):
                store.customer["pubufts"] = "h:2"
            return out

        verdict, note, store, before = self._broken("_add", patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("客户行", note)
        self.assertEqual(store.snapshot(), before)

    def test_left_over_temp_rows_are_removed_by_sql(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            if fields["code"] == "C900001" and fields.get("expect") and fields["expect"].get("name", "").startswith("E2E新增a"):
                return {"ok": True, "code": "C900001", "address": fields["address"], "changed": False, "before": [], "after": []}
            return orig(fields)

        verdict, note, store, before = self._broken("_delete", patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertEqual(store.snapshot(), before)
        self.assertTrue(any("DELETE FROM dbo.CusDeliverAdd" in query for query in store.queries))


if __name__ == "__main__":
    unittest.main()
