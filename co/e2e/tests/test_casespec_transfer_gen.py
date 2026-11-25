"""调拨单参照调拨申请单（transfer_gen）用例的登记、接线、正文和补偿。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import transfer_gen
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.env import Env
from co.e2e.casespec.wiring import EDIT_BUILDERS, EDIT_PACKS, has_edit, edit_samples, edit_sql
from co.e2e.casespec.transfer_gen import TG_BUILDERS, TG_CLEANUP, tg_cases, tg_gen, tg_gen_over, tg_req_new
from co.e2e.casespec.transfer_gen_sql import TG_PACKS, tg_kids_sql, tg_tr_sql
from co.e2e.casespec.picks import pick_keys
from co.e2e.casespec.runner import _CLEANUP, _HANDLERS
from co.e2e.casespec.steps import _make
from co.e2e.tests import support

_NAME = "transfer-from-request"
_BAG = {"tg_tr": "06", "tg_tr_in": "10", "tg_tr_inv": "A003", "tg_reql": "702"}


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _env(focus: str = "", client: object = None, **extra: str) -> Env:
    operator = SimpleNamespace(acc="801", year="2026", code="op001", password="x")
    env = Env(None, client, operator, "2026-09-28", "2026", "", dict(_BAG, **extra))  # type: ignore[arg-type]
    env.focus = focus
    return env


class _Client:
    def __init__(self) -> None:
        self.sent: list = []

    def call(self, route: str, fields: dict) -> dict:
        self.sent.append((route, fields))
        return {"ok": True, "deleted": True}


class TransferFromRequestCaseTests(unittest.TestCase):
    def test_registered_once_and_wired(self) -> None:
        self.assertEqual(known_names().count(_NAME), 1)
        self.assertIs(_CLEANUP[_NAME], TG_CLEANUP[_NAME])
        for pack in TG_PACKS:
            self.assertIn(pack, EDIT_PACKS)
        self.assertIn(_NAME, select_changed(["co/bridge/src/StockGenTr.cs"], [_NAME]))

    def test_steps_resolve(self) -> None:
        keys = set(pick_keys())
        spec = tg_cases()[0]
        self.assertTrue(spec.optional and spec.requires_login)
        for need in spec.needs:
            self.assertIn(need, keys)
        for step in spec.steps:
            self.assertIn(step.kind, _HANDLERS)
            if step.kind in ("apply", "create"):
                self.assertIs(_make(step.sql_name), TG_BUILDERS[step.sql_name])
                self.assertIs(EDIT_BUILDERS[step.sql_name], TG_BUILDERS[step.sql_name])
            if step.kind in ("pred", "stash"):
                self.assertTrue(has_edit(step.sql_name), step.sql_name)
                self.assertIn("ROW:", edit_sql(step.sql_name, "100", dict(_BAG)))
        self.assertEqual(spec.data, ("stock.transfer_out_rd", "stock.transfer_in_rd"))
        refused = [step.sql_name for step in spec.steps if step.route.endswith("/generate") and step.status == 409]
        self.assertEqual(refused, ["tg_gen_over"])

    def test_sql(self) -> None:
        self.assertIn("bOverTransRequestTransfer", tg_tr_sql())
        self.assertIn("iTRIds", tg_kids_sql("5"))
        names = [name for name, _sql in edit_samples()]
        self.assertIn("trgen-tg-kids", names)


class TransferFromRequestBodyTests(unittest.TestCase):
    def test_bodies(self) -> None:
        req = tg_req_new(_env())
        self.assertEqual(req["lines"][0]["iTvChkQuantity"], 2)
        self.assertEqual((req["head"]["cOWhCode"], req["head"]["cIWhCode"]), ("06", "10"))
        sample = support.sample_testdata()["stock"]
        self.assertEqual((req["head"]["cORdCode"], req["head"]["cIRdCode"]), (sample["transfer_out_rd"], sample["transfer_in_rd"]))
        gen = tg_gen(_env("900"))
        self.assertEqual((gen["type"], gen["id"], gen["source_type"]), ("transfer", 900, "transfer_request"))
        self.assertEqual(gen["lines"], [{"source_line_id": 702, "quantity": 1}])
        self.assertEqual(tg_gen_over(_env("900"))["lines"][0]["quantity"], 3)
        with self.assertRaises(ValueError):
            tg_gen(_env("x"))


class TransferFromRequestCleanupTests(unittest.TestCase):
    def test_nothing_made(self) -> None:
        self.assertEqual(TG_CLEANUP[_NAME](_env(), _NAME), "没有新建的单据")

    def test_transfers_first_then_request(self) -> None:
        client = _Client()
        env = _env(client=client, tg_req="50")
        with mock.patch.object(transfer_gen, "_kids", return_value=["61", "62"]):
            TG_CLEANUP[_NAME](env, _NAME)
        order = [(route.rsplit("/", 1)[-1], fields["type"], fields["id"]) for route, fields in client.sent]
        self.assertEqual(
            order,
            [
                ("delete", "transfer", 61),
                ("delete", "transfer", 62),
                ("verify", "transfer_request", 50),
                ("delete", "transfer_request", 50),
            ],
        )


if __name__ == "__main__":
    unittest.main()
