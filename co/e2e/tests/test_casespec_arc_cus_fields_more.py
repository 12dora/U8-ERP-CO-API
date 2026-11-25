"""客户字段用例 `archive-customer-fields` 的扩展键：联系人 + 开户银行、所属行业、自定义项 1 的写入、改回、
被拒绝的请求、跳过说明和路由出错时的判定。沿用 test_casespec_arc_cus_fields 的假桥。不连接服务器。"""

from __future__ import annotations

import unittest
from collections.abc import Callable

from co.e2e.casespec.arc_cus_fields_more_sql import (
    define_setting_sql,
    define_value_sql,
    more_samples,
    trade_name_sql,
    trade_pick_sql,
)
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support
from co.e2e.tests.test_casespec_arc_cus_fields import (
    _DEFINE_ARCHIVE,
    _ROW,
    _bodies,
    _Client,
    _run,
    _same_but_stamps,
    _Store,
)


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class MoreKeysTests(unittest.TestCase):
    """联系人 + 开户银行、所属行业、自定义项 1：写入、改回、被拒绝的请求和跳过说明。"""

    def _values(self, client: _Client, key: str) -> list[str]:
        return [body["fields"][key] for body in _bodies(client) if key in body["fields"]]

    def test_extra_keys_are_written_and_restored(self) -> None:
        store = _Store()
        client = _Client(store)
        verdict, note = _run(client, store)
        self.assertEqual((verdict, note), ("PASS", "customer C900001 fields"))
        both = [body for body in _bodies(client) if tuple(body["fields"]) == ("contact", "bank_open")]
        self.assertEqual(
            [body["fields"] for body in both],
            [{"contact": "E2E联系人", "bank_open": "E2E银行"}, {"contact": "", "bank_open": ""}],
        )
        self.assertEqual(both[0]["expect"], {"contact": "", "bank_open": ""})
        self.assertEqual(both[1]["expect"], {"contact": "E2E联系人", "bank_open": "E2E银行"})
        self.assertEqual(self._values(client, "ccustradeccode"), ["01", "", "ZZNOTRD9"])  # 设、改回、不存在
        self.assertEqual(self._values(client, "self_define1"), ["E2E定义", ""])
        self.assertTrue(_same_but_stamps(store.row, _ROW))

    def test_existing_values_are_written_back(self) -> None:
        row = {**_ROW, "cCusPerson": "联系人甲", "cCusBank": "示例银行", "cCusTradeCCode": "02", "cTrade": "行业乙"}
        store = _Store(row)
        client = _Client(store)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertEqual(self._values(client, "ccustradeccode"), ["01", "02", "ZZNOTRD9"])
        self.assertEqual((store.row["cCusPerson"], store.row["cCusBank"]), ("联系人甲", "示例银行"))
        self.assertEqual((store.row["cCusTradeCCode"], store.row["cTrade"]), ("02", "行业乙"))

    def test_archive_typed_define_takes_an_archive_value(self) -> None:
        store = _Store()
        store.define_setting = _DEFINE_ARCHIVE
        client = _Client(store)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertEqual(self._values(client, "self_define1"), [sorted(store.define_values)[0], "", "ZZ不存在档案值"])
        self.assertEqual(store.row["cCusDefine1"], "")

    def test_missing_data_skips_only_those_steps(self) -> None:
        cases = (
            ("trades", {}, "没有可换的末级所属行业"),
            ("define_setting", "", "没有客户自定义项 1 的设置"),
            ("define_setting", "1|1|0|0|0|0|11", "取自其他档案"),
            ("define_setting", "0|0|1|0|0|0|11", "必输项"),
        )
        for attr, value, text in cases:
            store = _Store()
            setattr(store, attr, value)
            client = _Client(store)
            verdict, note = _run(client, store)
            self.assertEqual(verdict, "PASS", note)
            self.assertIn(text, note)
            self.assertIn(("contact", "bank_open"), [tuple(body["fields"]) for body in _bodies(client)])
            self.assertTrue(_same_but_stamps(store.row, _ROW))
        store = _Store({**_ROW, "cCusTradeCCode": "01", "cTrade": "旧名称"})
        verdict, note = _run(_Client(store), store)
        self.assertEqual(verdict, "PASS", note)
        self.assertIn("行业名称与所属行业档案不一致", note)
        self.assertEqual((store.row["cCusTradeCCode"], store.row["cTrade"]), ("01", "旧名称"))
        store = _Store({**_ROW, "cCusDefine1": "已有值"})
        verdict, note = _run(_Client(store), store)
        self.assertEqual(verdict, "PASS", note)
        self.assertIn("自定义项 1 现值非空", note)

    def test_responses_carry_only_the_requested_keys(self) -> None:
        store = _Store()
        client = _Client(store)
        _run(client, store)
        for route, body in client.calls:
            if route.endswith("/customer_fields") and "fields" in body and "expect" in body:
                self.assertLessEqual(set(body["expect"]), set(body["fields"]))

    def test_sql_samples_name_the_setting_and_archive_tables(self) -> None:
        for name, sql in more_samples():
            self.assertEqual(sql_block_reason(sql), "", name)
        self.assertIn("NCHAR(23458) + NCHAR(25143)", define_setting_sql())
        self.assertIn("N'cCusDefine1'", define_setting_sql())
        for column in ("bCheckVal", "iDataSource", "cRelArchive", "bInput", "bFixLength", "iMaxLen"):
            self.assertIn(column, define_setting_sql())
        self.assertNotIn("bArchive", define_setting_sql())  # 自动建档不影响路由校验
        self.assertIn("N'文本A'", define_value_sql("11", "文本A"))
        self.assertIn("bTradeCEnd = 1", trade_pick_sql(""))
        self.assertNotIn("cTradeCCode)) <> N'", trade_pick_sql(""))  # 现值为空：不排除任何编码
        self.assertIn("cTradeCCode)) <> N'01'", trade_pick_sql("01"))
        for bad in (lambda: define_value_sql("1;2", ""), lambda: trade_name_sql("a'b")):
            with self.assertRaises(ValueError):
                bad()


class BrokenExtraRouteTests(unittest.TestCase):
    def _broken(self, patch: Callable) -> tuple[str, str, _Store]:
        store = _Store()
        client = _Client(store)
        orig = client._fields

        def wrapped(fields: dict) -> dict:
            return patch(store, orig, fields)

        client._fields = wrapped  # type: ignore[method-assign]
        verdict, note = _run(client, store)
        return verdict, note, store

    def test_full_before_after_is_caught(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            out = orig(fields)
            if out["changed"] and not fields.get("dry_run"):
                out["before"] = {**store.view(), **out["before"]}  # 旧桥：总带全部键
            return out

        verdict, note, store = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("before", note)

    def test_trade_name_not_written_with_the_code_fails(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            out = orig(fields)
            if not fields.get("dry_run") and fields.get("fields", {}).get("ccustradeccode") == "01":
                store.row["cTrade"] = ""
            return out

        verdict, note, store = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("cTrade", note)
        self.assertEqual(store.row["cCusTradeCCode"], "")


if __name__ == "__main__":
    unittest.main()
