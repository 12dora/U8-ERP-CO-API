"""造数的 seed 配置段（co/e2e/seed/config.py）和造数前预检（preflight.py）：解析、缺省不变、覆盖、不合时的消息。不连接服务器。"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from co.e2e.remote import RemoteResult
from co.e2e.seed import config, data, emit, preflight
from co.e2e.seed.config import SeedOverrides
from co.e2e.seed.context import Link, SeedContext, SeedError
from co.e2e.settings import ConfigError, from_dict
from co.e2e.tests import support


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["\t".join(header), "\t".join("-" * len(name) for name in header)]
    lines += ["\t".join(row) for row in rows]
    return "\n".join(lines) + "\n"


# 与缺省编码相合的编码方案（KEYWORD → CODINGRULE）。
MATCHING = {
    "department": "5",
    "customerclass": "3",
    "vendorclass": "3",
    "inventoryclass": "22222",
    "rd_style": "122",
    "settlestyle": "1",
    "districtclass": "2",
    "positionclass": "2222",
}


class FakeAccount:
    def __init__(self) -> None:
        self.rules = dict(MATCHING)
        self.subjects = {data.GL["expense_account"]: "1", data.GL["payable_account"]: "1"}
        self.cash = [data.GL["cash_flow_item"]]
        self.writes: list[str] = []
        self.posts: list[str] = []

    def sql(self, query: str) -> RemoteResult:
        if "FROM GradeDef_Base" in query:
            return RemoteResult(0, table(["kw", "coding_rule"], [[k, v] for k, v in self.rules.items()]))
        if "FROM code WHERE" in query:
            return RemoteResult(0, table(["code", "leaf"], [[k, v] for k, v in self.subjects.items()]))
        if "FROM fitemss98" in query:
            return RemoteResult(0, table(["code"], [[c] for c in self.cash]))
        self.writes.append(query)
        return RemoteResult(0, "")

    def post(self, path: str, body: dict) -> dict:
        self.posts.append(path)
        return {"ok": True}

    def link(self) -> Link:
        return Link(self.post, self.sql, lambda: None, "op001", "2026-10-01", ["secret-x"])


def _sample(seed: object) -> dict:
    raw = copy.deepcopy(support.sample_dict())
    raw["seed"] = seed
    return raw


class ParseTest(unittest.TestCase):
    def test_absent_or_empty_means_defaults(self) -> None:
        self.assertEqual(from_dict(support.sample_dict()).seed, SeedOverrides())
        self.assertEqual(from_dict(_sample(None)).seed, SeedOverrides())
        self.assertEqual(from_dict(_sample({})).seed, SeedOverrides())
        self.assertEqual(config.values(SeedOverrides()), data.defaults())

    def test_valid_overrides(self) -> None:
        seed = {
            "date": "2025-03-01",
            "codes": {"departments": ["D9001", "D9002", "D9003", "D9004", "D9005"], "rd_style": {"other_in": "209"}},
            "cycle_codes": {"department": ["D9901", "D9902", "D9903"]},
            "gl": {"expense_account": "6602", "bank_account": "100201", "cash_flow_item": "01"},
        }
        parsed = from_dict(_sample(seed)).seed
        self.assertEqual(parsed.get("date"), "2025-03-01")
        self.assertEqual(parsed.get("codes.departments"), ("D9001", "D9002", "D9003", "D9004", "D9005"))
        self.assertEqual(parsed.get("codes.rd_style.other_in"), "209")
        self.assertEqual(parsed.get("cycle_codes.department"), ("D9901", "D9902", "D9903"))
        self.assertEqual(parsed.get("gl.bank_account"), "100201")

    def test_invalid_keys_and_values(self) -> None:
        bad = (
            {"dates": "2025-01-01"},
            {"codes": {"dept": ["D1"]}},
            {"codes": {"rd_style": {"misc_in": "209"}}},
            {"cycle_codes": {"project": ["X1", "X2", "X3"]}},
            {"gl": {"income_account": "6001"}},
            {"date": "2025-02-30"},
            {"codes": {"departments": ["D1", "D2"]}},
            {"codes": {"departments": ["D1", "D1", "D3", "D4", "D5"]}},
            {"codes": {"customer_class": "C'1"}},
            {"gl": {"expense_account": "66A2"}},
            [],
        )
        for seed in bad:
            with self.subTest(seed=seed), self.assertRaises(ConfigError):
                from_dict(_sample(seed))


class ApplyTest(unittest.TestCase):
    def tearDown(self) -> None:
        data.configure(data.defaults())

    def test_overrides_rebuild_derived_tables(self) -> None:
        seed = config.parse(
            {
                "date": "2025-03-01",
                "codes": {
                    "departments": ["D9001", "D9002", "D9003", "D9004", "D9005"],
                    "customer_class": "C1",
                    "inventory_class": ["1", "101"],
                    "rd_style": {"in": "4", "purchase_in": "401", "product_in": "402", "transfer_in": "403",
                                 "other_in": "405"},
                },
                "gl": {"payable_account": "2202"},
            }
        )
        data.configure(config.values(seed))
        self.assertEqual([arc.code for arc in data.DEPARTMENTS], ["D9001", "D9002", "D9003", "D9004", "D9005"])
        self.assertEqual(data.DEPARTMENTS[0].fields["ddepbegindate"], "2025-03-01")
        self.assertEqual({arc.fields["cdept_num"] for arc in data.PERSONS}, {"D9002", "D9003", "D9004"})
        self.assertEqual(data.GL["expense_dept"], "D9001")
        self.assertEqual(data.GL["payable_account"], "2202")
        self.assertEqual(data.CUSTOMERS[0].fields["sort_code"], "C1")
        self.assertEqual((data.INV_CLASS_TOP, data.INV_CLASS), ("1", "101"))
        self.assertEqual(data.BIZ_TYPES[0].fields["rstype_code"], "401")
        self.assertIn("405", [arc.code for arc in data.RD_STYLES])
        self.assertEqual(emit.build()["archives"]["customer"]["sort_code"], "C1")

    def test_defaults_restore_everything(self) -> None:
        before = {name: repr(getattr(data, name)) for name in ("GL", "RD_STYLES", "DEPARTMENTS", "PERSONS", "CUSTOMERS")}
        data.configure(config.values(config.parse({"codes": {"departments": ["A1", "A2", "A3", "A4", "A5"]}})))
        data.configure(data.defaults())
        self.assertEqual({name: repr(getattr(data, name)) for name in before}, before)

    def test_inconsistent_hierarchy_is_refused(self) -> None:
        with mock.patch.object(config, "_loaded_steps", return_value=[]):
            with self.assertRaises(ConfigError):
                config.apply(config.parse({"codes": {"inventory_class": ["01", "0201"]}}))
            with self.assertRaises(ConfigError):
                config.apply(config.parse({"codes": {"rd_style": {"out": "9"}}}))

    def test_refused_after_steps_are_imported(self) -> None:
        with mock.patch.dict("sys.modules", {"co.e2e.seed.steps_fake": object()}):
            config.apply(SeedOverrides())  # 没有覆盖时什么都不做
            with self.assertRaises(ConfigError):
                config.apply(config.parse({"date": "2025-03-01"}))


class PreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.account = FakeAccount()
        self.ctx = SeedContext(Path(self.tmp.name), self.account.link())

    def tearDown(self) -> None:
        self.tmp.cleanup()
        data.configure(data.defaults())

    def test_matching_account_passes_without_writes(self) -> None:
        preflight.check(self.ctx)
        self.assertEqual(self.account.writes, [])
        self.assertEqual(self.account.posts, [])

    def test_mismatch_lists_each_key_once(self) -> None:
        self.account.rules["department"] = "422"
        self.account.rules["rd_style"] = "222"
        self.account.subjects[data.GL["payable_account"]] = "0"
        del self.account.subjects[data.GL["expense_account"]]
        self.account.cash = []
        with self.assertRaises(SeedError) as caught:
            preflight.check(self.ctx)
        text = str(caught.exception)
        self.assertIn("没有写入任何数据", text)
        self.assertEqual(text.count("seed.codes.departments"), 1)
        self.assertIn("seed.codes.rd_style.out", text)
        self.assertIn("seed.gl.expense_account", text)
        self.assertIn("不是末级", text)
        self.assertIn("seed.gl.payable_account", text)
        self.assertIn("seed.gl.cash_flow_item", text)
        self.assertNotIn("seed.codes.customer_class", text)
        self.assertEqual(self.account.writes, [])

    def test_missing_scheme_rows_are_left_to_u8(self) -> None:
        self.account.rules = {}
        preflight.check(self.ctx)

    def test_bank_account_checked_only_when_configured(self) -> None:
        self.assertNotIn("seed.gl.bank_account", [key for key, _code in preflight.subjects()])
        data.configure(config.values(config.parse({"gl": {"bank_account": "100201"}})))
        with self.assertRaises(SeedError) as caught:
            preflight.check(self.ctx)
        self.assertIn("seed.gl.bank_account", str(caught.exception))

    def test_levels(self) -> None:
        self.assertEqual(preflight.levels("22222"), [2, 4, 6, 8, 10])
        self.assertEqual(preflight.levels("1-2-2"), [1, 3, 5])
        self.assertEqual(preflight.levels(""), [])


class BankFixedTest(unittest.TestCase):
    def tearDown(self) -> None:
        data.configure(data.defaults())

    def test_configured_bank_wins_over_chart_pick(self) -> None:
        data.configure(config.values(config.parse({"gl": {"bank_account": "1002"}})))
        ctx = mock.Mock(spec=SeedContext)
        gl = emit.gl_payload(ctx, Path("/nonexistent/td.json"))
        self.assertEqual(gl["bank_account"], "1002")
        ctx.rows.assert_not_called()


if __name__ == "__main__":
    unittest.main()
