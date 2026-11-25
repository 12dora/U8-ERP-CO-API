"""用例（生单单据修改、按行生单、有货位删除、收付款和应收应付修改、生产订单行关闭）的登记和接线。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from co.e2e.casespec import ops_run
from co.e2e.casespec.assertutil import EITHER
from co.e2e.casespec.catalog import build_cases, known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.wiring import EDIT_BUILDERS, EDIT_PACKS, has_edit, edit_samples, edit_sql, wiring_cases
from co.e2e.casespec.picks import pick_keys
from co.e2e.casespec.runner import _HANDLERS
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.steps import _make
from co.e2e.tests import support


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


_NAMES = (
    "dispatch-update",
    "sale-return-update",
    "sale-invoice-update",
    "arrival-update",
    "purchase-return-update",
    "purchase-invoice-update",
    "sale-out-update",
    "purchase-in-po-update",
    "purchase-in-qm-update",
    "product-in-update",
    "material-out-update",
    "sale-out-partial",
    "binned-other-in-delete",
    "ar-receipt-edit",
    "ap-payment-edit",
    "ar-bill-edit",
    "ap-bill-edit",
    "purchase-return-po-cycle",
    "production-order-close-cycle",
)


def _by_name(name: str) -> CaseSpec:
    for spec in wiring_cases():
        if spec.name == name:
            return spec
    raise AssertionError(name)


class WiringNameTests(unittest.TestCase):
    def test_names_registered_once(self) -> None:
        names = known_names()
        self.assertEqual(tuple(spec.name for spec in wiring_cases()), _NAMES)
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)
        self.assertNotIn("generate-sale-out-lines", names)
        self.assertTrue(_by_name("production-order-close-cycle").optional)

    def test_needs_are_picked_and_steps_resolve(self) -> None:
        keys = set(pick_keys())
        for spec in wiring_cases():
            for need in spec.needs:
                self.assertIn(need, keys, spec.name)
            for step in spec.steps:
                self.assertIn(step.kind, _HANDLERS, spec.name)
                self._resolvable(spec, step)

    def _resolvable(self, spec: CaseSpec, step: Step) -> None:
        if step.kind in ("create", "apply"):
            self.assertTrue(callable(_make(step.sql_name)), spec.name)
        if step.kind in ("stash", "pred") and has_edit(step.sql_name):
            self.assertIn("ROW:", edit_sql(step.sql_name, "100", _bag()))
        if step.kind == "custom":
            self.assertTrue(ops_run.has_op(step.sql_name), spec.name)

    def test_every_writer_deletes_what_it_made(self) -> None:
        for spec in wiring_cases():
            made = [step for step in spec.steps if step.kind == "create" or step.route.endswith("/generate")]
            made = [step for step in made if step.status == 200]
            if not made:
                continue
            last = max(spec.steps.index(step) for step in made)
            later = [step for step in spec.steps[last:] if step.route.endswith("/delete") and step.status in (200, EITHER)]
            self.assertTrue(later, spec.name)


class WiringWiringTests(unittest.TestCase):
    def test_packs_and_builders_are_wired(self) -> None:
        keys = set(pick_keys())
        for _lead, names, flags, sql in EDIT_PACKS:
            self.assertEqual(len(names), len(flags))
            self.assertIn("ROW:", sql())
            for name in names:
                self.assertIn(name, keys)
        for name in EDIT_BUILDERS:
            self.assertIs(_make(name), EDIT_BUILDERS[name])

    def test_stock_edit_mfg_bodies_carry_rd(self) -> None:
        stock = support.sample_testdata()["stock"]
        env = SimpleNamespace(bag={"se_pq_wh": "06", "se_mo_wh": "06", "se_mo_line": "55"}, focus="77", today="2026-10-01")
        made = EDIT_BUILDERS["se_pq_gen"](env)
        self.assertEqual((made["type"], made["head"]["cRdCode"]), ("product_in", stock["product_in_rd"]))
        out = EDIT_BUILDERS["se_mo_gen"](env)
        self.assertEqual((out["type"], out["head"]["cRdCode"]), ("material_out", stock["material_out_rd"]))
        cases = {spec.name: spec for spec in wiring_cases()}
        self.assertEqual(cases["product-in-update"].data, ("stock.product_in_rd",))
        self.assertEqual(cases["material-out-update"].data, ("stock.material_out_rd",))

    def test_samples_reach_sample_sql(self) -> None:
        text = dict(sample_sql())
        for name, sql in edit_samples():
            self.assertEqual(text[name], sql, name)
        self.assertEqual(len(build_cases({})), len(known_names()))
        with self.assertRaises(ValueError):
            edit_sql("nope", "100", {})


def _bag() -> dict[str, str]:
    lines = ("disp_mod_line", "sret_mod_line", "sinv_mod_line", "arr_mod_line", "pret_mod_line", "pinv_mod_line", "se_so_line", "se_po_line")
    bag = {key: "100" for key in lines + ("se_mo_line", "spo_line", "prpo_line", "mo_line")}
    bag.update({"bin_wh": "01", "bin_pos": "A01", "bin_inv": "0001"})
    return bag
