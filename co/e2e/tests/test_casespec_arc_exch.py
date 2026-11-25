"""汇率、供应商联系人写入用例的登记、分线和挑选 SQL。不连接服务器。"""

from __future__ import annotations

import unittest

from co.e2e.casespec import ops_run
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.arc_partner import PARTNER_OPS, partner_cases
from co.e2e.casespec.arc_partner_sql import contact_customer
from co.e2e.casespec.arc_exch import EXCH_OPS, exch_cases
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("archive-exchange-rate-write", "archive-vendor-contact-write")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class ArcExchCatalogTests(unittest.TestCase):
    def test_cases_registered_with_tags(self) -> None:
        names = known_names()
        cases = {spec.name: spec for spec in [*exch_cases(), *partner_cases()] if spec.name in _NAMES}
        self.assertEqual(set(cases), set(_NAMES))
        for name, spec in cases.items():
            self.assertIn(name, names)
            self.assertTrue(spec.requires_login)
            self.assertTrue(any(tag.startswith("arc:") for tag in spec.tags))
            self.assertIn("/v1/archives/delete", spec.routes)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertIn("partner_ven_contact_write", PARTNER_OPS)
        self.assertEqual(set(EXCH_OPS), {"exch_write"})

    def test_read_case_stays_read_only(self) -> None:
        read = next(spec for spec in partner_cases() if spec.name == "archive-vendor-contact-read")
        self.assertTrue(all(tag.startswith("arc-read:") for tag in read.tags))


class ArcExchSqlTests(unittest.TestCase):
    def test_vendor_pick_passes_the_guard(self) -> None:
        query = contact_customer("vendor")
        self.assertEqual(sql_block_reason(query), "", query[:60])
        self.assertIn("dbo.Vendor", query)
        self.assertIn("N'ROW:'", query)


if __name__ == "__main__":
    unittest.main()
