"""生产订单修改替换替代料用例（mo_subs_replace）的登记、分片、挑单 SQL 和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import mo_subs_replace as ms
from co.e2e.casespec import ops_run
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.mo_subs_replace_sql import mo_subs_replace_samples, subs_pick_sql
from co.e2e.casespec.picks import pick_keys
from co.e2e.casespec.samples import sample_sql
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_SPEC = CaseSpec(ms.NAME, "s")
_STEP = Step("custom")
_BAG = {"moc_inv": "P001", "moc_dept": "D01", "moc_type": "1", "moc_bom_from": "2026-01-01", "moc_bom_to": "2099-12-31"}
_ORIG = [{"sequence": 1, "inv_code": "S0", "factor": 1, "replace_flag": False, "eff_begin": "2026-01-01", "eff_end": None}]
_WANT = [{"sequence": 2, "inv_code": "SUB1", "factor": 1.5, "replace_flag": True, "eff_begin": "2026-10-02",
          "eff_end": "2026-11-01", "defines": {"define22": "DEMO-001"}}]


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
        if isinstance(got, Exception):
            raise got
        return got


def _load(subs: list[dict], allocate_id: int = 900) -> dict:
    row = {"MoDId": "11", "AllocateId": str(allocate_id), "SortSeq": "10", "InvCode": "C001", "substitutes": subs}
    return {"ok": True, "allocations": [row]}


def _done(subs: list[dict]) -> dict:
    alloc = {"line_id": 11, "allocate_id": 900, "sort_seq": 10, "inv_code": "C001", "substitutes": subs}
    return {"ok": True, "changed": 1, "allocations": [alloc]}


def _refused(status: int, code: str) -> U8CoError:
    return U8CoError(status, code, "拒绝")


def _happy() -> _Replay:
    bad = _refused(400, "bad_request")
    return _Replay({
        "/v1/vouchers/create": [{"ok": True, "id": 501}],
        "/v1/vouchers/load": [_load(_ORIG), _load(_ORIG), _load(_WANT), _load([]), _load([]), _load([]), _load([]),
                              _load([]), _load(_WANT, 901), _load(_ORIG, 901)],
        "/v1/vouchers/update": [{"ok": True, "dry_run": True}, _done(_WANT), _done([]), _refused(409, "state_mismatch"), bad,
                                bad, bad, _done(_WANT), _done(_ORIG)],
        "/v1/vouchers/delete": [{"ok": True, "deleted": True}],
    })


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-02", bag=dict(_BAG))


def _run(client: _Replay, pick: str = "ROW:SUB1|NOSUB\n") -> tuple[str, str]:
    with mock.patch.object(ms, "run_guarded", lambda _env, _query, _label: pick):
        return ms.mo_subs_replace(_env(client), _SPEC, _STEP)


class RegistrationTests(unittest.TestCase):
    def test_registered_with_op(self) -> None:
        (spec,) = ms.mo_subs_replace_cases()
        self.assertEqual(known_names().count(ms.NAME), 1)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertTrue(spec.optional and "bom" in spec.tags)
        for need in spec.needs:
            self.assertIn(need, set(pick_keys()))

    def test_bridge_change_selects_the_case(self) -> None:
        for path in ("co/bridge/src/MoUpdateSubsWrite.cs", "co/bridge/src/SubstituteReq.cs"):
            self.assertIn(ms.NAME, select_changed([path], list(known_names())))


class SqlTests(unittest.TestCase):
    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in mo_subs_replace_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)

    def test_pick_shape(self) -> None:
        query = subs_pick_sql("P001")
        self.assertIn("i.bBomSub = 1", query)
        self.assertIn("ISNULL(n.bBomSub, 0) = 0", query)
        self.assertEqual(query.count("N'P001'"), 3)
        for bad in ("P'1", "", "A B"):
            with self.assertRaises(ValueError):
                subs_pick_sql(bad)


class CycleTests(unittest.TestCase):
    def test_cycle_passes(self) -> None:
        client = _happy()
        status, detail = _run(client)
        self.assertEqual(status, "PASS", detail)
        updates = [fields for route, fields in client.calls if route.endswith("/update")]
        self.assertTrue(updates[0]["dry_run"])
        line = updates[1]["lines"][0]
        self.assertEqual((line["op"], line["line_id"], set(line)), ("update", 11, {"op", "line_id", "allocations"}))
        alloc = line["allocations"][0]
        self.assertEqual((alloc["sort_seq"], alloc["inv_code"], alloc["substitutes"]), (10, "C001", _WANT))
        self.assertEqual(updates[2]["lines"][0]["allocations"][0]["substitutes"], [])
        self.assertEqual(updates[3]["lines"][0]["allocations"][0]["sort_seq"], 10 + 100000)
        refused = [u["lines"][0]["allocations"][0]["substitutes"][0]["inv_code"] for u in updates[4:7]]
        self.assertEqual(refused, ["C001", "P001", "NOSUB"])
        self.assertEqual(updates[7]["lines"][0]["qty"], 3)
        restored = updates[8]["lines"][0]["allocations"][0]["substitutes"]
        self.assertEqual(restored, [{"sequence": 1, "inv_code": "S0", "factor": 1, "replace_flag": False, "eff_begin": "2026-01-01"}])
        self.assertEqual(client.calls[-1][0], "/v1/vouchers/delete")

    def test_tier_disabled_is_na_and_cleans_up(self) -> None:
        client = _Replay({
            "/v1/vouchers/create": [{"ok": True, "id": 501}],
            "/v1/vouchers/load": [_load(_ORIG)],
            "/v1/vouchers/update": [_refused(403, "feature_disabled")],
            "/v1/vouchers/delete": [{"ok": True, "deleted": True}],
        })
        status, detail = _run(client)
        self.assertEqual(status, "N/A", detail)
        self.assertIn("补偿删除生产订单 501", detail)

    def test_allocate_id_changed_on_substitutes_only_fails(self) -> None:
        client = _happy()
        client.answers["/v1/vouchers/load"][2] = _load(_WANT, 950)
        status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("AllocateId", detail)
        self.assertEqual(client.calls[-1][0], "/v1/vouchers/delete")

    def test_no_substitute_inventory_is_na(self) -> None:
        client = _happy()
        status, _detail = _run(client, pick="")
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
