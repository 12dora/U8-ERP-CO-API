"""锁定用例的登记、接线、挑单 SQL 和补偿。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from co.client.u8co_client import U8CoError

from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.wiring import EDIT_PACKS, has_edit, edit_samples, edit_sql
from co.e2e.casespec.so_lock import LOCK_CLEANUP, lock_cases
from co.e2e.casespec.so_lock_sql import LOCK_PACKS, lock_samples
from co.e2e.casespec.picks import pick_keys
from co.e2e.casespec.tags import bind_tags

_NAMES = ("sale-order-lock", "sale-order-lock-verified", "po-lock", "sale-lock-bad-type")


class _Replay:
    def __init__(self, answers: list) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers.pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got


def _env(client: _Replay, bag: dict) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-29", bag=bag)


class SoLockTests(unittest.TestCase):
    def test_names_registered_once(self) -> None:
        names = known_names()
        self.assertEqual(tuple(spec.name for spec in lock_cases()), _NAMES)
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)

    def test_packs_and_readback_are_wired(self) -> None:
        for pack in LOCK_PACKS:
            self.assertIn(pack, EDIT_PACKS)
            self.assertIn(pack[0], pick_keys())
        self.assertTrue(has_edit("lock_so_locker"))
        self.assertFalse(has_edit("lock_po_locker"))
        self.assertIn("FROM dbo.SO_SOMain AS h WHERE h.ID = 42;", edit_sql("lock_so_locker", "42", {}))
        with self.assertRaises(ValueError):
            edit_sql("lock_so_locker", "1;--", {})
        for row in lock_samples():
            self.assertIn(row, edit_samples())

    def test_pick_sql_filters(self) -> None:
        so, sov = (pack[3]() for pack in LOCK_PACKS)
        self.assertIn("m.dverifydate IS NULL", so)
        self.assertIn("LTRIM(RTRIM(ISNULL(m.cLocker, N''))) = N''", so)
        self.assertIn("NOT (LTRIM(RTRIM(ISNULL(m.cVerifier, N''))) = N'')", sov)
        self.assertIn("ORDER BY m.ID DESC", sov)

    def test_tags_bind_the_picked_document(self) -> None:
        so_case, _verified, po_case, bad = lock_cases()
        self.assertIn("doc:sale_order:7", bind_tags(so_case, {"lock_so": "7"}))
        self.assertEqual((po_case.needs, bad.needs), ((), ()))
        self.assertEqual([(step.status, step.code) for step in po_case.steps], [(400, "bad_request")] * 2)
        self.assertEqual([dict(step.fields)["type"] for step in po_case.steps], ["purchase_order"] * 2)

    def test_cleanup_unlocks_best_effort(self) -> None:
        client = _Replay([{"ok": True}])
        note = LOCK_CLEANUP["sale-order-lock"](_env(client, {"lock_so": "9"}), "sale-order-lock")
        self.assertIn("已解锁", note)
        route, fields = client.calls[0]
        self.assertEqual(route, "/v1/vouchers/lock")
        self.assertEqual((fields["type"], fields["id"], fields["action"]), ("sale_order", 9, "unlock"))
        refused = _Replay([U8CoError(409, "state_mismatch", "单据未锁定")])
        note = LOCK_CLEANUP["sale-order-lock"](_env(refused, {"lock_so": "9"}), "sale-order-lock")
        self.assertIn("409", note)
        self.assertEqual(LOCK_CLEANUP["sale-order-lock"](_env(_Replay([]), {}), "sale-order-lock"), "没有挑中的单据")
        self.assertNotIn("po-lock", LOCK_CLEANUP)


if __name__ == "__main__":
    unittest.main()
