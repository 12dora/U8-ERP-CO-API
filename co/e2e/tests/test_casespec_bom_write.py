"""物料清单（bom）用例的登记、接线和正文。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from co.client.u8co_client import U8CoError

from co.e2e.casespec.assertutil import EITHER, dig
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.env import Env
from co.e2e.casespec.wiring import EDIT_BUILDERS, EDIT_PACKS, has_edit, edit_samples, edit_sql
from co.e2e.casespec.bom_write import (
    BOMW_BUILDERS,
    BOMW_CLEANUP,
    bomw_bad_inv,
    bomw_cases,
    bomw_clear,
    bomw_dup_ver,
    bomw_edit,
    bomw_new,
    bomw_readd,
    bomw_swap,
)
from co.e2e.casespec.bom_write_sql import BOMW_KEYS, BOMW_PACKS, BOMW_SRC_STASH, BOMW_STASH, bomw_samples, bomw_sql
from co.e2e.casespec.picks import pick_keys
from co.e2e.casespec.runner import _CLEANUP, _HANDLERS
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.steps import _make
from co.e2e.tests import support

_NAMES = ("bom-lifecycle", "bom-create-bad")
_BAG = {
    "bomw_parent": "P001",
    "bomw_c1": "C001",
    "bomw_c2": "C002",
    "bomw_c3": "C003",
    "bomw_ver": "20",
    "bomw_sub": "S001",
    "bomw_subp": "700",
    "bomw_c2p": "702",
}


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _env(focus: str = "", **bag: str) -> Env:
    env = Env(None, None, None, "2026-09-28", "2026", "", dict(_BAG, **bag))  # type: ignore[arg-type]
    env.focus = focus
    return env


class _Replay:
    """按路由回放：值是 dict 原样返回，是 U8CoError 就抛出。记录调用的路由和正文。"""

    def __init__(self, answers: dict[str, list]) -> None:
        self.answers = {key: list(val) for key, val in answers.items()}
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers[route].pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got


def _cleanup_env(client: _Replay, made: str) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-28", made={"bom-lifecycle": made})


class BomWriteCaseTests(unittest.TestCase):
    def test_names_registered_once(self) -> None:
        names = known_names()
        self.assertEqual(tuple(spec.name for spec in bomw_cases()), _NAMES)
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)
        self.assertTrue(all(spec.optional and spec.requires_login for spec in bomw_cases()))

    def test_needs_are_picked_and_steps_resolve(self) -> None:
        keys = set(pick_keys())
        for spec in bomw_cases():
            for need in spec.needs:
                self.assertIn(need, keys, spec.name)
            for step in spec.steps:
                self.assertIn(step.kind, _HANDLERS, spec.name)
                if step.kind in ("create", "apply"):
                    self.assertIs(_make(step.sql_name), BOMW_BUILDERS[step.sql_name])
                if step.kind in ("pred", "stash"):
                    self.assertTrue(has_edit(step.sql_name), step.sql_name)
                    self.assertIn("ROW:", edit_sql(step.sql_name, "100", {}))

    def test_lifecycle_order(self) -> None:
        good, bad = bomw_cases()
        routes = [step.route for step in good.steps]
        deletes = [step for step in good.steps if step.route.endswith("/delete")]
        self.assertEqual([(step.status, step.code) for step in deletes], [(409, "state_mismatch"), (200, "")])
        self.assertLess(routes.index("/v1/vouchers/update"), routes.index("/v1/vouchers/verify"))
        self.assertLess(routes.index("/v1/vouchers/verify"), routes.index("/v1/vouchers/delete"))
        self.assertNotIn(EITHER, [step.status for step in good.steps])
        wanted = [("create", 400, "bad_request"), ("create", 409, "state_mismatch")]
        self.assertEqual([(step.kind, step.status, step.code) for step in bad.steps], wanted)

    def test_option_ids_are_stashed_after_the_update_and_before_the_first_delete(self) -> None:
        good = bomw_cases()[0]
        kinds = [(step.kind, step.sql_name, step.code) for step in good.steps]
        update = next(i for i, step in enumerate(good.steps) if step.route.endswith("/update"))
        first_delete = next(i for i, step in enumerate(good.steps) if step.route.endswith("/delete"))
        stashed = [i for i, (kind, name, key) in enumerate(kinds) if kind == "stash" and name == key and name in BOMW_STASH]
        self.assertEqual([kinds[i][1] for i in stashed], list(BOMW_STASH))
        self.assertLess(update, min(stashed))
        self.assertLess(max(stashed), first_delete)


    def test_source_end_date_is_stashed_before_verify_and_checked_last(self) -> None:
        good = bomw_cases()[0]
        kinds = [(step.kind, step.sql_name, step.code) for step in good.steps]
        verify = next(i for i, step in enumerate(good.steps) if step.route.endswith("/verify"))
        src = [i for i, (kind, name, key) in enumerate(kinds) if kind == "stash" and name in BOMW_SRC_STASH and name == key]
        self.assertEqual([kinds[i][1] for i in src], list(BOMW_SRC_STASH))
        self.assertLess(max(src), verify)
        self.assertEqual(kinds[-1], ("pred", "bomw_src_end", "same:bomw_send"))


class BomWriteBodyTests(unittest.TestCase):
    def test_new_body_uses_picked_codes_and_future_date(self) -> None:
        body = bomw_new(_env())
        self.assertEqual(body["type"], "bom")
        self.assertNotIn("version", body["head"])
        self.assertEqual(body["head"]["inv_code"], "P001")
        self.assertGreater(body["head"]["eff_date"], "2034-01-01")
        self.assertEqual([line["inv_code"] for line in body["lines"]], ["C001", "C002"])
        self.assertEqual([line["sort_seq"] for line in body["lines"]], [10, 20])
        self.assertEqual(body["lines"][0]["substitutes"], [{"inv_code": "S001"}])
        self.assertNotIn("substitutes", body["lines"][1])

    def test_substitutes_kept_then_swapped_then_cleared(self) -> None:
        # 修改（改用量、删行、加行）不送替代料：桥保留现有的。随后替换成子件 2，再清空。
        self.assertFalse(any("substitutes" in line for line in bomw_edit(_env("1000000053"))["lines"]))
        swap = bomw_swap(_env("1000000053"))
        sub = {"inv_code": "C002", "sequence": 2, "factor": 2}
        self.assertEqual(swap["lines"], [{"op": "update", "sort_seq": 10, "substitutes": [sub]}])
        cleared = bomw_clear(_env("1000000053"))["lines"]
        self.assertEqual(cleared, [{"op": "update", "sort_seq": 10, "substitutes": []}])
        good = bomw_cases()[0]
        preds = [step.sql_name for step in good.steps if step.kind == "pred"]
        wanted = ["bomw_status", "bomw_sub_kept", "bomw_edited", "bomw_sub_kept", "bomw_sub_swap", "bomw_sub_n"]
        self.assertEqual(preds[:6], wanted)
        with self.assertRaises(ValueError):
            bomw_swap(_env("x"))
        readd = bomw_readd(_env("1000000053"))["lines"][0]
        self.assertEqual((readd["substitutes"], readd["locators"]), ([{"inv_code": "S001"}], [{"loc": "DEMO-001"}]))
        # 加回在审核之前：审核、删除时清单上带着替代料和定位符。
        names = [step.sql_name for step in good.steps]
        self.assertLess(names.index("bomw_readd"), [step.route for step in good.steps].index("/v1/vouchers/verify"))
        self.assertIn("bomw_loc_n", preds)

    def test_dig_indexes_lists(self) -> None:
        body = {"lines": [{"locators": [{"loc": "DEMO-001"}]}]}
        self.assertEqual(dig(body, "lines.0.locators.0.loc"), "DEMO-001")
        self.assertIsNone(dig(body, "lines.1.locators"))
        self.assertIsNone(dig(body, "lines.x"))

    def test_edit_body_updates_deletes_and_adds(self) -> None:
        body = bomw_edit(_env("1000000053"))
        self.assertEqual((body["type"], body["id"]), ("bom", 1000000053))
        self.assertEqual([line["op"] for line in body["lines"]], ["update", "delete", "add"])
        self.assertEqual(body["lines"][2]["inv_code"], "C003")
        self.assertNotIn("sort_seq", body["lines"][2])
        with self.assertRaises(ValueError):
            bomw_edit(_env("x"))

    def test_bad_bodies(self) -> None:
        self.assertNotIn(bomw_bad_inv(_env())["lines"][1]["inv_code"], _BAG.values())
        self.assertEqual(bomw_dup_ver(_env())["head"]["version"], 20)
        # 拒绝用例不用生命周期用例的生效日期，残留版本不会让它先撞上日期 409。
        lifecycle = bomw_new(_env())["head"]["eff_date"]
        self.assertNotEqual(bomw_bad_inv(_env())["head"]["eff_date"], lifecycle)
        self.assertNotEqual(bomw_dup_ver(_env())["head"]["eff_date"], lifecycle)


class BomWriteCleanupTests(unittest.TestCase):
    def test_compensation_unverifies_then_deletes(self) -> None:
        client = _Replay(
            {
                "/v1/vouchers/load": [{"ok": True}],
                "/v1/vouchers/verify": [U8CoError(409, "state_mismatch", "单据未审核")],
                "/v1/vouchers/delete": [{"ok": True, "deleted": True}],
            }
        )
        note = BOMW_CLEANUP["bom-lifecycle"](_cleanup_env(client, "1000000053"), "bom-lifecycle")
        self.assertIn("已补偿删除", note)
        self.assertEqual([route for route, _ in client.calls], ["/v1/vouchers/load", "/v1/vouchers/verify", "/v1/vouchers/delete"])
        self.assertEqual(client.calls[1][1]["action"], "unverify")
        self.assertEqual((client.calls[2][1]["type"], client.calls[2][1]["id"]), ("bom", 1000000053))

    def test_compensation_skips_gone_or_missing(self) -> None:
        client = _Replay({"/v1/vouchers/load": [U8CoError(404, "not_found", "单据不存在")]})
        self.assertIn("已不在", BOMW_CLEANUP["bom-lifecycle"](_cleanup_env(client, "9"), "bom-lifecycle"))
        empty = _Replay({})
        self.assertEqual(BOMW_CLEANUP["bom-lifecycle"](_cleanup_env(empty, ""), "bom-lifecycle"), "没有新建的物料清单")
        self.assertEqual(empty.calls, [])

    def test_runner_knows_both_cleanups(self) -> None:
        self.assertIs(_CLEANUP["bom-lifecycle"], BOMW_CLEANUP["bom-lifecycle"])
        self.assertIn("mo-create-delete", _CLEANUP)


class BomWriteSqlTests(unittest.TestCase):
    def test_pick_returns_parent_components_and_version(self) -> None:
        self.assertEqual(BOMW_PACKS[0][1], BOMW_KEYS)
        self.assertEqual(len(BOMW_PACKS[0][2]), len(BOMW_KEYS))
        sql = BOMW_PACKS[0][3]()
        self.assertIn("k.c3 IS NOT NULL", sql)
        self.assertIn("ISNULL(ci.bFree10, 0) = 0", sql)

    def test_gone_counts_options_by_stashed_ids(self) -> None:
        bag = {"bomw_omin": "500", "bomw_omax": "504"}
        sql = bomw_sql("bomw_gone", "100", bag)
        self.assertIn("o.OptionsId BETWEEN 500 AND 504", sql)
        with self.assertRaises(ValueError):
            bomw_sql("bomw_gone", "100", dict(bag, bomw_omin="1;DROP"))
        with self.assertRaises(ValueError):
            bomw_sql("bomw_status", "0", {})

    def test_substitute_readback_uses_stashed_part_ids(self) -> None:
        self.assertEqual(BOMW_PACKS[0][2][-2:], (True, True))
        self.assertIn("sb.part", BOMW_PACKS[0][3]())
        kept = bomw_sql("bomw_sub_kept", "100", _BAG)
        self.assertIn("s.PartId = 700", kept)
        self.assertIn("c.BomId = 100", kept)
        self.assertIn("s.PartId = 702", bomw_sql("bomw_sub_swap", "100", _BAG))
        self.assertIn("COUNT(*)", bomw_sql("bomw_sub_n", "100", {}))
        with self.assertRaises(ValueError):
            bomw_sql("bomw_sub_kept", "100", dict(_BAG, bomw_subp="1 OR 1=1"))
        gone = bomw_sql("bomw_gone", "100", {"bomw_cmin": "900", "bomw_cmax": "905"})
        self.assertIn("bom_opcomponentsub AS s WHERE s.OpComponentId BETWEEN 900 AND 905", gone)
        self.assertIn("bom_opcomponentloc AS l WHERE l.OpComponentId BETWEEN 900 AND 905", gone)

    def test_source_version_readback(self) -> None:
        sql = bomw_sql("bomw_src", "100", {"bomw_ver": "20"})
        self.assertIn("p.BomId = 100", sql)
        self.assertIn("s.Version = 20", sql)
        self.assertIn("s.BomId = 1000000052", bomw_sql("bomw_src_end", "100", {"bomw_src": "1000000052"}))
        self.assertIn("VersionEndDate, 112", bomw_sql("bomw_send", "100", {"bomw_src": "7"}))
        with self.assertRaises(ValueError):
            bomw_sql("bomw_src", "100", {"bomw_ver": "20 OR 1=1"})


class BomWriteWiringTests(unittest.TestCase):
    def test_packs_builders_and_samples_are_wired(self) -> None:
        for pack in BOMW_PACKS:
            self.assertIn(pack, EDIT_PACKS)
            self.assertIn("ROW:", pack[3]())
        for name in BOMW_BUILDERS:
            self.assertIs(EDIT_BUILDERS[name], BOMW_BUILDERS[name])
        text = dict(sample_sql())
        mine = dict(edit_samples())
        for name, sql in bomw_samples():
            self.assertEqual(text[name], sql, name)
            self.assertEqual(mine[name], sql, name)


if __name__ == "__main__":
    unittest.main()
