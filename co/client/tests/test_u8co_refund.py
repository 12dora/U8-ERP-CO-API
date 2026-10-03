"""供应商退款（ap_refund）、客户退款（ar_refund）：客户端类型名单。"""

from __future__ import annotations

import unittest

from co.client.u8co_kinds import (
    ARAP_KINDS,
    CREATABLE_KINDS,
    DELETABLE_KINDS,
    KIND_NAMES,
    REFUND_KINDS,
    UPDATABLE_KINDS,
    VERIFIABLE_KINDS,
    require_verify,
)


class RefundKindTests(unittest.TestCase):
    def test_refunds_follow_receipts(self) -> None:
        self.assertEqual(REFUND_KINDS, ("ap_refund", "ar_refund"))
        self.assertEqual(KIND_NAMES[-len(ARAP_KINDS):], ARAP_KINDS)
        for name in REFUND_KINDS:
            self.assertIn(name, ARAP_KINDS)
            self.assertEqual(KIND_NAMES.count(name), 1)
            for group in (CREATABLE_KINDS, DELETABLE_KINDS, UPDATABLE_KINDS, VERIFIABLE_KINDS):
                self.assertIn(name, group)

    def test_no_arap_audit_action(self) -> None:
        for name in REFUND_KINDS:
            require_verify(5, "verify", name)
            with self.assertRaises(ValueError):
                require_verify(5, "arap_verify", name)


if __name__ == "__main__":
    unittest.main()
