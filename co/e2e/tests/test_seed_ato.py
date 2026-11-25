"""造数 ato（seed/steps_docs_ato、steps_docs_ato_sql）：存货标志、物料清单和销售订单正文、选配结果 SQL、步骤顺序、
用假服务器回放整步（第二次全部跳过）和写出的测试数据键。不连接服务器。"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from co.e2e.remote import RemoteResult
from co.e2e.seed import data
from co.e2e.seed import steps_docs_ato as ato
from co.e2e.seed.context import Link, SeedContext, SeedError
from co.e2e.seed.runner import ALL_STEPS, DOC_STEPS
from co.e2e.seed.steps_docs_ato_sql import CbomIds, CbomPart, Stamp, cbom_sql, id_sql, id_steps, parse_ids, repair_sql
from co.e2e.seed.steps_inventory import flags_sql
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support
from co.e2e.tests.test_seed import SeedCase, table

_CODE = re.compile(r"InvCode = N'([^']+)'")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _one(name: str, value: object) -> RemoteResult:
    return RemoteResult(0, table([name], [[str(value)]]))


class FakeAtoServer:
    """桥：档案、物料清单、销售订单新建和审核；SQL：按查询文本回答。选配写入后行上有选配主键。"""

    def __init__(self) -> None:
        self.inventories: set[str] = set()
        self.boms: dict[str, int] = {}
        self.flags: dict[int, int] = {}
        self.probes: list[list[dict]] = []
        self.next_id = 1000000001
        self.cbom_at_post = -1
        self.so = 0
        self.verified: set[int] = set()
        self.cbom = 0
        self.optional = 2
        self.posts: list[tuple[str, dict]] = []
        self.execs: list[str] = []

    def post(self, path: str, body: dict) -> dict:
        self.posts.append((path, body))
        if path.endswith("archives/create"):
            self.inventories.add(body["code"])
            return {"ok": True}
        if path.endswith("verify"):
            self.verified.add(body["id"])
            return {"ok": True}
        if path.endswith("delete"):
            self.boms = {code: bom for code, bom in self.boms.items() if bom != body["id"]}
            return {"ok": True}
        if body["type"] == "bom":
            bom = 3000 + len(self.posts)
            self.boms[body["head"]["inv_code"]] = bom
            self.flags[bom] = self.optional
            return {"ok": True, "id": bom}
        self.so = 4001
        return {"ok": True, "id": self.so}

    def _exec(self, query: str) -> RemoteResult:
        self.execs.append(query)
        if "INSERT INTO bom_cbom " in query:
            self.cbom = int(re.findall(r"SET @cbom = (\d+)", query)[0])
            self.cbom_at_post = len(self.posts)
        return RemoteResult(0, "")

    def probe(self, steps: list[dict]) -> tuple[int, str]:
        # sp_GetID：bom_cbom 和 bom_cbomcomponent 都从 next_id 起发号（新账套两边计数相同）。
        self.probes.append(steps)
        count = int(re.findall(r"N'bom_cbom', (\d+),", steps[1]["query"])[0])
        head, top = self.next_id, self.next_id + count - 1
        self.next_id = top + 1
        row = {"r1": "0", "r2": "0", "r3": "0", "head": str(head), "c1": str(top), "c2": str(top)}
        lines = [{"ok": True, "i": 0, "op": "login"}, {"ok": True, "i": 1, "op": "sql", "count": 1, "rows": [row]}]
        return 0, "\n".join(json.dumps(line) for line in lines)

    def sql(self, query: str) -> RemoteResult:  # noqa: C901 - 按查询分派的回放表
        if "BEGIN TRANSACTION" in query or "UPDATE Inventory" in query or "DELETE FROM bom_cbom" in query:
            return self._exec(query)
        if "FROM Inventory WHERE cInvCode IN" in query:
            return RemoteResult(0, table(["code"], [[code] for code in sorted(self.inventories)]))
        if "OptionalFlag" in query:
            return _one("n", self.flags.get(int(re.findall(r"BomId = (\d+)", query)[0]), 0))
        if "JOIN bom_parent" in query:
            bom = self.boms.get(_CODE.findall(query)[0])
            return RemoteResult(0, table(["id"], [[str(bom)]] if bom else []))
        if "WHERE BomId =" in query or "FROM SO_SOMain WHERE ID =" in query:
            doc = int(re.findall(r"= (\d+)", query)[0])
            return _one("n", 1 if doc in self.verified else 0)
        if "FROM SO_SOMain WHERE cMemo" in query:
            return RemoteResult(0, table(["id"], [[str(self.so)]] if self.so else []))
        if "cInvCode AS inv" in query:
            rows = [["5001", data.ATO_STD], ["5002", data.ATO_MODEL]] if self.so else []
            return RemoteResult(0, table(["id", "inv"], rows))
        if "FROM SO_SODetails WHERE ID" in query:
            return RemoteResult(0, table(["id"], [["5001"], ["5002"]] if self.so else []))
        if "iCusBomID, 0) AS id" in query:
            return RemoteResult(0, table(["id", "n"], [[str(self.cbom), "2" if self.cbom else "0"]]))
        if "FROM bas_part WHERE" in query:
            return _one("id", 6000 + int(_CODE.findall(query)[0][-1]))
        if "OpComponentId AS id" in query:
            return _one("id", 7001)
        if "mom_motype" in query:
            return _one("code", "01")
        raise AssertionError("没有回放的查询：" + query[:120])

    def link(self) -> Link:
        return Link(self.post, self.sql, lambda: None, "op001", "2026-11-20", ["secret-x"], self.probe)


class AtoStepTests(SeedCase):
    def setUp(self) -> None:
        super().setUp()
        self.server = FakeAtoServer()
        self.ctx = SeedContext(self.dir, self.server.link())
        self.target = self.dir / "testdata-seedacct.json"
        self.cfg = SimpleNamespace(account="seedacct", lane="", testdata_file=str(self.target))

    def test_builds_everything_then_skips_on_rerun(self) -> None:
        self.assertEqual(ato.apply(self.ctx, self.cfg), (11, 0))
        kinds = [body.get("archive") or body.get("type") for path, body in self.server.posts if "create" in path]
        self.assertEqual(kinds, ["inventory"] * 6 + ["bom", "bom", "bom", "sale_order"])
        # 销售订单在写选配之后才审核：ATO 模型件的行没有选配 U8 不让审核。
        so_verify = next(i for i, (path, body) in enumerate(self.server.posts) if path.endswith("verify") and body["id"] == 4001)
        self.assertEqual(self.server.cbom_at_post, so_verify)
        self.assertEqual(self.server.cbom, 1000000001)
        written = json.loads(self.target.read_text(encoding="utf-8"))["ato"]
        self.assertEqual(set(written), set(support.sample_testdata()["ato"]))
        self.assertEqual((written["so_id"], written["line_plain"], written["line_cbom"]), (4001, 5001, 5002))
        self.assertEqual((written["cbom_id"], written["model"], written["option"]), (1000000001, data.ATO_MODEL, data.ATO_OPTION_A))
        self.assertEqual(written["std"], data.ATO_STD)
        self.assertEqual((written["mo_type"], written["dept"]), ("01", data.DEPT_PURCHASE))
        posts = len(self.server.posts)
        self.assertEqual(ato.apply(self.ctx, self.cfg), (0, 11))
        self.assertEqual(len(self.server.posts), posts)
        self.assertEqual(sum("INSERT INTO bom_cbom " in q for q in self.server.execs), 1)
        self.assertEqual(len(self.server.probes), 1)
        self.assertEqual(self.server.probes[0][0], {"op": "login", "sub": "SA"})
        self.assertEqual(ato.probe(self.ctx).missing, ())

    def test_saved_order_with_cbom_is_audited_on_rerun(self) -> None:
        # 上次停在「选配已写、订单未审核」：重跑不再取号写选配，只补审核。
        self.server.post("v1/vouchers/create", {"type": "sale_order"})
        self.server.cbom = 1000000050
        self.server.posts.clear()
        new, _existed = ato.apply(self.ctx, self.cfg)
        self.assertEqual(self.server.probes, [])
        self.assertIn(("v1/vouchers/verify", {"type": "sale_order", "id": 4001, "action": "verify"}), self.server.posts)
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8"))["ato"]["cbom_id"], 1000000050)
        self.assertEqual(new, 9)  # 存货 6、物料清单 3；订单和选配都已在

    def test_wrong_order_lines_are_refused(self) -> None:
        self.server.post("v1/vouchers/create", {"type": "sale_order"})
        original = self.server.sql

        def old_lines(query: str) -> RemoteResult:
            if "cInvCode AS inv" in query:
                return RemoteResult(0, table(["id", "inv"], [["5001", data.ATO_MODEL], ["5002", data.ATO_MODEL]]))
            return original(query)

        self.ctx.link.sql = old_lines
        with self.assertRaises(SeedError) as caught:
            ato.apply(self.ctx, self.cfg)
        self.assertIn("删掉这张订单", str(caught.exception))

    def test_probe_on_an_empty_lane_lists_everything(self) -> None:
        found = ato.probe(self.ctx)
        self.assertEqual(found.existing, ())
        self.assertEqual(len(found.missing), 12)

    def test_stops_when_the_bridge_drops_the_optional_flag(self) -> None:
        self.server.optional = 0
        with self.assertRaises(SeedError) as caught:
            ato.apply(self.ctx, self.cfg)
        self.assertIn("选配标志", str(caught.exception))
        self.assertEqual(self.server.so, 0)
        bad = self.server.boms[data.ATO_CLASS]
        self.assertNotIn(bad, self.server.verified)
        # 桥修好后重跑：没有选配标志的旧单删掉重建，再往下走完。
        self.server.optional = 2
        self.assertEqual(ato.apply(self.ctx, self.cfg)[0], 5)  # 存货已在；三张物料清单、订单、选配
        self.assertIn(("v1/vouchers/delete", {"type": "bom", "id": bad}), self.server.posts)
        self.assertNotEqual(self.server.boms[data.ATO_CLASS], bad)

    def test_dangling_cbom_pointer_is_repaired_first(self) -> None:
        ato.apply(self.ctx, self.cfg)
        repair = [q for q in self.server.execs if "DELETE FROM bom_cbom" in q]
        self.assertEqual(len(repair), 1)
        self.assertLess(self.server.execs.index(repair[0]), next(i for i, q in enumerate(self.server.execs) if "INSERT INTO bom_cbom " in q))

    def test_refuses_the_reference_testdata_path(self) -> None:
        cfg = SimpleNamespace(account="seedacct", lane="", testdata_file=str(Path.home() / "x.json"))
        with self.assertRaises(SeedError):
            ato.apply(self.ctx, cfg)


class AtoBodyTests(unittest.TestCase):
    def test_option_class_bom_marks_both_options(self) -> None:
        body = ato.class_bom_body()
        self.assertEqual(body["head"]["inv_code"], data.ATO_CLASS)
        self.assertEqual([line["inv_code"] for line in body["lines"]], [data.ATO_OPTION_A, data.ATO_OPTION_B])
        self.assertTrue(all(line["optional"] is True and line["mutex_rule"] == 1 for line in body["lines"]))

    def test_model_bom_has_the_base_and_the_virtual_class(self) -> None:
        base, cls = ato.model_bom_body()["lines"]
        self.assertEqual((base["inv_code"], base["wip_type"]), (data.ATO_BASE, 3))
        self.assertEqual((cls["inv_code"], cls["wip_type"]), (data.ATO_CLASS, 4))
        self.assertNotIn("optional", cls)

    def test_sale_order_has_a_plain_ato_line_and_a_model_line(self) -> None:
        body = ato.ato_so_body("2026-11-20")
        self.assertEqual(body["head"]["cmemo"], "seed:ato")
        self.assertEqual([(line["cinvcode"], line["iquantity"]) for line in body["lines"]], [(data.ATO_STD, 5), (data.ATO_MODEL, 3)])
        self.assertEqual(body["lines"][0]["dpremodate"], "2026-11-27")

    def test_plain_ato_item_has_a_standard_bom_without_options(self) -> None:
        (line,) = ato.std_bom_body()["lines"]
        self.assertEqual((line["inv_code"], line["wip_type"]), (data.ATO_BASE, 3))
        self.assertNotIn("optional", line)
        flags = {code: f for code, _n, f in data.ATO_ROLES}[data.ATO_STD]
        self.assertEqual((flags["bATOModel"], flags["bInvModel"], flags["bProductBill"]), (1, 0, 1))

    def test_flags(self) -> None:
        roles = {code: flags for code, _name, flags in data.ATO_ROLES}
        cls, model = roles[data.ATO_CLASS], roles[data.ATO_MODEL]
        self.assertEqual(cls["bCheckItem"], 1)
        self.assertEqual((cls["bSelf"], cls["bPurchase"], cls["bProxyForeign"]), (0, 0, 0))
        self.assertEqual((model["bATOModel"], model["bInvModel"], model["bSelf"], model["bProductBill"]), (1, 1, 1, 1))
        self.assertEqual(sql_block_reason("SET NOCOUNT ON;\n" + flags_sql(data.ATO_ROLES)), "")
        self.assertFalse(set(roles) & {code for code, _n, _f in data.INVENTORY_ROLES})


class CbomSqlTests(unittest.TestCase):
    def test_sql_passes_the_guard_and_writes_two_leaves(self) -> None:
        parts = [CbomPart(11, 21, "06"), CbomPart(12, 22, "06")]
        ids = CbomIds(1000000007, (1000000008, 1000000009))
        text = cbom_sql(5002, 10, parts, ids, Stamp("op001", "2026-11-20"))
        self.assertEqual(sql_block_reason("SET NOCOUNT ON;\n" + text), "")
        self.assertIn("ISNULL(iCusBomID, 0) = 0", text)
        self.assertIn("SET @cbom = 1000000007;", text)
        self.assertIn("(@cbom, 1000000008, 10, N'0000', 11,", text)
        self.assertIn("(@cbom, 1000000009, 20, N'0000', 12,", text)
        self.assertNotIn("UA_Identity", text)
        self.assertIn("CONVERT(datetime, N'2026-11-20', 120)", text)
        self.assertNotIn(", 23)", text)
        self.assertIn("busecusbom = 1 WHERE iSOsID = 5002", text)
        self.assertEqual(sql_block_reason(repair_sql(5002)), "")

    def test_refuses_empty_and_bad_values(self) -> None:
        one = [CbomPart(11, 21, "06")]
        with self.assertRaises(ValueError):
            cbom_sql(5002, 10, [], CbomIds(1, ()), Stamp("op001", "2026-11-20"))
        with self.assertRaises(ValueError):
            cbom_sql(5002, 10, one, CbomIds(1, (2, 3)), Stamp("op001", "2026-11-20"))
        with self.assertRaises(SeedError):
            cbom_sql(0, 10, one, CbomIds(1, (2,)), Stamp("op001", "2026-11-20"))
        with self.assertRaises(SeedError):
            cbom_sql(5002, 10, one, CbomIds(1, (2,)), Stamp("de'mo", "2026-11-20"))


class IdProbeTests(unittest.TestCase):
    def test_script_uses_u8_sp_getid_for_both_tables(self) -> None:
        steps = id_steps(2)
        self.assertEqual(steps[0], {"op": "login", "sub": "SA"})
        text = steps[1]["query"]
        self.assertIn("EXEC @r1 = sp_GetID @rid, @acc, N'bom_cbom', 2,", text)
        self.assertIn("EXEC @r2 = sp_GetID @rid, @acc, N'bom_cbomcomponent', 2,", text)
        self.assertIn("cName = N'cID'", text)
        with self.assertRaises(SeedError):
            id_sql(0)

    def _lines(self, **row: str) -> list[dict]:
        base = {"r1": "0", "r2": "0", "r3": "0", "head": "1000000005", "c1": "1000000009", "c2": "1000000009"}
        base.update(row)
        return [{"ok": True, "op": "login"}, {"ok": True, "op": "sql", "rows": [base]}]

    def test_parse_takes_the_top_child_ids(self) -> None:
        self.assertEqual(parse_ids(self._lines(), 2), CbomIds(1000000005, (1000000008, 1000000009)))

    def test_parse_refuses_failures_and_uneven_counters(self) -> None:
        for bad in ({"r2": "-1"}, {"c2": "1000000010"}, {"head": ""}):
            with self.subTest(bad), self.assertRaises(SeedError):
                parse_ids(self._lines(**bad), 2)
        with self.assertRaises(SeedError):
            parse_ids([{"ok": True, "op": "sql", "rows": []}], 2)


class AtoOrderTests(unittest.TestCase):
    def test_runs_after_the_documents_and_before_period_close(self) -> None:
        names = [step.name for step in ALL_STEPS]
        self.assertNotIn("ato", [step.name for step in DOC_STEPS])
        self.assertEqual(names[-2:], ["ato", "close_prior_months"])


if __name__ == "__main__":
    unittest.main()
