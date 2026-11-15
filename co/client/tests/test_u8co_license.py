"""u8_license_full 映射到 U8CoLicenseFull。不连网络。"""

from __future__ import annotations

import json
import unittest

from co.client.u8co_errors import U8CoBusyTimeout, U8CoError, U8CoLicenseFull, error_from, parse_payload

_MESSAGE = "U8 许可点数已满（子系统 SA），请稍后重试：加密点数已饱和"


class LicenseFullTest(unittest.TestCase):
    def test_error_from_maps_code(self) -> None:
        err = error_from(503, {"ok": False, "code": "u8_license_full", "message": _MESSAGE})
        self.assertIsInstance(err, U8CoLicenseFull)
        self.assertIsInstance(err, U8CoError)
        self.assertNotIsInstance(err, U8CoBusyTimeout)
        self.assertEqual(err.status, 503)
        self.assertEqual(err.code, "u8_license_full")
        self.assertEqual(err.message, _MESSAGE)

    def test_parse_payload_raises(self) -> None:
        raw = json.dumps({"ok": False, "code": "u8_license_full", "message": _MESSAGE}, ensure_ascii=False).encode()
        with self.assertRaises(U8CoLicenseFull) as caught:
            parse_payload(503, raw)
        self.assertEqual(caught.exception.code, "u8_license_full")

    def test_plain_503_is_not_license_full(self) -> None:
        err = error_from(503, {"ok": False})
        self.assertNotIsInstance(err, U8CoLicenseFull)
        self.assertEqual(err.code, "com_unavailable")


if __name__ == "__main__":
    unittest.main()
