"""红字采购链路（pu_red_chain）用例的登记、接线、正文和补偿。不连接服务器。"""

from __future__ import annotations

import unittest

from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.env import Env
from co.e2e.casespec.wiring import EDIT_BUILDERS, EDIT_PACKS, has_edit, edit_samples, edit_sql
from co.e2e.casespec.pu_red_chain import (
    REDPUR_BUILDERS,
    REDPUR_CLEANUP,
    redpur_cases,
    rp_in,
    rp_inv,
    rp_ret,
    rpu_in,
    rpu_inv,
    rpu_ret,
    rpu_upd,
)
from co.e2e.casespec.pu_red_chain_sql import REDPUR_PACKS
from co.e2e.casespec.picks import pick_keys
from co.e2e.casespec.runner import _HANDLERS
from co.e2e.casespec.steps import _make
from co.e2e.casespec.tags import need_kind
from co.e2e.tests import support

_NAME = "purchase-in-red-chain"
_UPD_NAME = "purchase-invoice-update-red"
_BAG = {
    "rp_arr": "700", "rp_arrl": "701", "rp_wh": "13", "rp_retl": "801", "rp_inl": "901",
    "rpu_arr": "710", "rpu_arrl": "711", "rpu_wh": "14", "rp_invl": "951",
}


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _env(focus: str) -> Env:
    env = Env(None, None, None, "2026-09-28", "2026", "", dict(_BAG))  # type: ignore[arg-type]
    env.focus = focus
    return env


class RedPurCaseTests(unittest.TestCase):
    def test_registered_once_and_wired(self) -> None:
        for name in (_NAME, _UPD_NAME):
            self.assertEqual(known_names().count(name), 1)
            self.assertIn(name, REDPUR_CLEANUP)
        self.assertEqual([spec.name for spec in redpur_cases()], [_NAME, _UPD_NAME])
        for pack in REDPUR_PACKS:
            self.assertIn(pack, EDIT_PACKS)
        self.assertEqual(need_kind("rp_arr"), "arrival")
        self.assertEqual(need_kind("rpu_arr"), "arrival")

    def test_needs_are_picked_and_steps_resolve(self) -> None:
        keys = set(pick_keys())
        for spec in redpur_cases():
            self.assertTrue(spec.optional and spec.requires_login)
            for need in spec.needs:
                self.assertIn(need, keys)
            for step in spec.steps:
                self.assertIn(step.kind, _HANDLERS)
                if step.kind == "apply":
                    self.assertIs(_make(step.sql_name), REDPUR_BUILDERS[step.sql_name])
                    self.assertIs(EDIT_BUILDERS[step.sql_name], REDPUR_BUILDERS[step.sql_name])
                if step.kind in ("pred", "stash"):
                    self.assertTrue(has_edit(step.sql_name), step.sql_name)
                    self.assertIn("ROW:", edit_sql(step.sql_name, "100", dict(_BAG)))

    def test_update_case_shape(self) -> None:
        spec = redpur_cases()[1]
        self.assertEqual(spec.needs, ("rpu_arr", "rpu_arrl", "rpu_wh"))
        routes = [step.route for step in spec.steps if step.route]
        self.assertIn("/v1/vouchers/update", routes)
        self.assertEqual(sum(route.endswith("/delete") for route in routes), 3)
        self.assertEqual([step.sql_name for step in spec.steps if step.route.endswith("/update")], ["rpu_upd"])
        self.assertIn("update", " ".join(spec.routes))

    def test_every_delete_follows_its_create(self) -> None:
        routes = [(step.route, step.sql_name, step.status) for step in redpur_cases()[0].steps]
        deletes = [i for i, (route, _name, _status) in enumerate(routes) if route.endswith("/delete")]
        self.assertEqual(len(deletes), 3)
        refused = [name for route, name, status in routes if route.endswith("/generate") and status == 409]
        self.assertEqual(refused, ["rp_in_over", "rp_inv_over"])

    def test_samples_present(self) -> None:
        names = [name for name, _sql in edit_samples()]
        self.assertIn("more-rp-arr", names)
        self.assertIn("more-rp-in-red", names)
        self.assertIn("more-rpu-arr", names)
        self.assertIn("more-rp-inv-amt", names)


class RedPurBodyTests(unittest.TestCase):
    def test_return_body(self) -> None:
        body = rp_ret(_env("700"))
        self.assertEqual((body["type"], body["id"], body["source_type"]), ("purchase_return", 700, "arrival"))
        self.assertEqual(body["head"]["cWhCode"], "13")
        self.assertEqual(body["lines"], [{"source_line_id": 701, "quantity": 1}])

    def test_red_in_body(self) -> None:
        body = rp_in(_env("800"))
        self.assertEqual((body["type"], body["id"], body["source_type"]), ("purchase_in", 800, "purchase_return"))
        self.assertEqual(body["lines"], [{"source_line_id": 801, "quantity": 1}])

    def test_red_invoice_body(self) -> None:
        body = rp_inv(_env("900"))
        self.assertEqual((body["type"], body["id"]), ("purchase_invoice", 900))
        self.assertNotIn("source_type", body)
        self.assertTrue(body["head"]["cPBVCode"].startswith("RE2E"))
        self.assertLessEqual(len(body["head"]["cPBVCode"]), 30)
        self.assertEqual(body["lines"], [{"source_line_id": 901, "quantity": 1}])

    def test_update_bodies(self) -> None:
        ret = rpu_ret(_env("710"))
        self.assertEqual(ret["lines"], [{"source_line_id": 711, "quantity": 2}])
        self.assertEqual(ret["head"]["cWhCode"], "14")
        self.assertEqual(rpu_in(_env("800"))["lines"], [{"source_line_id": 801, "quantity": 2}])
        self.assertEqual(rpu_in(_env("800"))["head"]["cWhCode"], "14")
        self.assertEqual(rpu_inv(_env("900"))["lines"], [{"source_line_id": 901, "quantity": 2}])
        body = rpu_upd(_env("990"))
        self.assertEqual((body["type"], body["id"]), ("purchase_invoice", 990))
        self.assertEqual(body["lines"], [{"op": "update", "line_id": 951, "ipbvquantity": 1, "cbmemo": "U8CO-REDINV-UPD"}])
        self.assertEqual(body["head"], {"cpbvmemo": "U8CO-REDINV-UPD"})

    def test_bad_ids_refused(self) -> None:
        env = _env("700")
        env.bag["rp_arrl"] = "x"
        with self.assertRaises(ValueError):
            rp_ret(env)
        env.bag["rp_wh"] = "a b"
        with self.assertRaises(ValueError):
            rp_in(_env("0"))


if __name__ == "__main__":
    unittest.main()
