"""字段权限：API 转出的 null 字段和 masked_fields 原样进工具结果，不补值、不丢键。"""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from tests.support import env, make_app

_BODY = {
    "ok": True,
    "type": "sale_out",
    "id": 7,
    "head": {"cCode": "0000000007", "iPrice": None},
    "lines": [{"cInvCode": "A001", "iUnitCost": None}],
    "masked_fields": ["iPrice", "iUnitCost"],
}


class MaskedTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, env(), clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_masked_load_passes_through(self):
        app, send = make_app()
        send.add("POST", "/v1/co/vouchers/load", body=_BODY)
        result = app.call("u8_read", {"route": "vouchers/load", "body": {"type": "sale_out", "id": 7}})
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"], _BODY)
        self.assertEqual(json.loads(result["content"][0]["text"])["masked_fields"], ["iPrice", "iUnitCost"])

    def test_masked_load_many_items_pass_through(self):
        app, send = make_app()
        item = dict(_BODY, masked_fields=["iUnitCost"])
        body = {"ok": True, "type": "sale_out", "items": [item], "masked_fields": ["iUnitCost"]}
        send.add("POST", "/v1/co/vouchers/load_many", body=body)
        result = app.call("u8_read", {"route": "vouchers/load_many", "body": {"type": "sale_out", "ids": [7]}})
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["items"][0]["masked_fields"], ["iUnitCost"])
        self.assertIsNone(result["structuredContent"]["items"][0]["head"]["iPrice"])


if __name__ == "__main__":
    unittest.main()
