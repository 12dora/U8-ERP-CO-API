"""错误码到异常子类的对应（error_from）。不连接任何服务。"""

from __future__ import annotations

import unittest

from co.client.u8co_errors import (
    U8CoAccountNotAllowed,
    U8CoFeatureDisabled,
    U8CoIaTimeout,
    U8CoTestAccountOnly,
    error_from,
)


class TestAccountOnlyTests(unittest.TestCase):
    def test_code_maps_to_subclass(self) -> None:
        err = error_from(403, {"ok": False, "code": "test_account_only", "message": "月末结账只对配置为测试账套的账套开放"})
        self.assertIs(type(err), U8CoTestAccountOnly)
        self.assertIsInstance(err, U8CoAccountNotAllowed)
        self.assertEqual(err.status, 403)
        self.assertEqual(err.code, "test_account_only")
        self.assertIn("测试账套", err.message)

    def test_plain_account_not_allowed_is_not_the_subclass(self) -> None:
        err = error_from(403, {"ok": False, "code": "account_not_allowed", "message": "账套不在名单里"})
        self.assertIs(type(err), U8CoAccountNotAllowed)


class FeatureDisabledTests(unittest.TestCase):
    def test_code_maps_to_subclass(self) -> None:
        body = {"ok": False, "code": "feature_disabled", "message": "第二级写入未打开", "hint": "桥设 enableReplicatedWrites"}
        err = error_from(403, body)
        self.assertIs(type(err), U8CoFeatureDisabled)
        self.assertEqual((err.status, err.code), (403, "feature_disabled"))
        self.assertIn("enableReplicatedWrites", err.hint)

    def test_not_confused_with_test_account_only(self) -> None:
        err = error_from(403, {"ok": False, "code": "feature_disabled", "message": "m"})
        self.assertNotIsInstance(err, U8CoTestAccountOnly)
        self.assertNotIsInstance(err, U8CoAccountNotAllowed)


class IaTimeoutTests(unittest.TestCase):
    def test_code_maps_to_subclass(self) -> None:
        err = error_from(503, {"ok": False, "code": "ia_timeout", "message": "存货核算处理超时，已回滚"})
        self.assertIs(type(err), U8CoIaTimeout)
        self.assertEqual((err.status, err.code), (503, "ia_timeout"))


class DetailTests(unittest.TestCase):
    _UNCOSTED = {"uncosted": [{"wh": "01", "inv": "A001", "batch": ""}], "uncosted_total": 1}

    def test_detail_kept_on_4xx(self) -> None:
        err = error_from(409, {"ok": False, "code": "state_mismatch", "message": "m", "detail": self._UNCOSTED})
        self.assertEqual(err.detail, self._UNCOSTED)
        self.assertIn("uncosted_total", err.describe())

    def test_detail_dropped_when_missing_bad_or_not_4xx(self) -> None:
        self.assertIsNone(error_from(409, {"code": "u8_rejected", "message": "m"}).detail)
        for status, detail in ((409, []), (409, "文字"), (409, {}), (500, {"k": 1}), (504, {"k": 1})):
            with self.subTest(status=status, detail=detail):
                self.assertIsNone(error_from(status, {"code": "x", "message": "m", "detail": detail}).detail)


if __name__ == "__main__":
    unittest.main()
