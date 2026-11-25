"""空账套造数（co/e2e/seed）：sqlcmd 输出解析、审批人提取、幂等、拒绝条件、测试数据文件。不连接服务器。"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from co.e2e.remote import RemoteResult
from co.e2e.seed import data, emit, steps_people
from co.e2e.seed.base import create_archive, ensure_archives
from co.e2e.seed.context import Link, SeedContext, SeedError, parse_table, refusal
from co.e2e.seed.runner import ALL_STEPS, run, select
from co.e2e.seed.steps_inventory import flags_sql, template_sql
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


_QUOTED = re.compile(r"N'([^']*)'")


def table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["\t".join(header), "\t".join("-" * len(name) for name in header)]
    lines += ["\t".join(row) for row in rows]
    return "\n".join(lines) + "\n"


# XQuery 取回的审批人行（按文档顺序）；编码都是编的。第一行是提交人占位，列名大小写不定。
_ROWS = [
    ("QM04", "员工[|hr_hi_person.cpsn_num| = '$<StartUser>']"),
    ("QM04", "员工[|hr_hi_Person.cPsn_Num| = 'P0001' OR |hr_hi_Person.cPsn_Num| = 'P0002']"),
    ("QM04", "员工[|HR_HI_PERSON.CPSN_NUM| = 'P0003']"),
    ("QM03", "员工[|hr_hi_Person.cPsn_Num| = 'P0002' OR |hr_hi_Person.cPsn_Num| = 'P0004']"),
]


class FakeServer:
    def __init__(self) -> None:
        self.existing: set[str] = set()
        self.posts: list[tuple[str, dict]] = []
        self.execs: list[str] = []
        self.linked = ""
        self.releases: list[tuple[str, str]] = list(_ROWS)
        self.reply: dict | None = None
        self.bank = ""

    def post(self, path: str, body: dict) -> dict:
        self.posts.append((path, body))
        if self.reply is not None:
            return self.reply
        self.existing.add(body["code"])
        return {"ok": True, "archive": body["archive"], "code": body["code"]}

    def _releases(self) -> str:
        return table(["biz", "performer"], [list(row) for row in self.releases])

    def sql(self, query: str) -> RemoteResult:
        if "Table_WorkFlowRelease" in query:
            return RemoteResult(0, self._releases())
        if "FROM UserHrPersonContro" in query and "INSERT" not in query:
            return RemoteResult(0, table(["code"], [[self.linked]] if self.linked else []))
        if "FROM code WHERE" in query:
            return RemoteResult(0, table(["code"], [[self.bank]] if self.bank else []))
        if query.lstrip().startswith("SET NOCOUNT ON;\nSELECT"):
            found = [[code] for code in _QUOTED.findall(query) if code in self.existing]
            return RemoteResult(0, table(["code"], found))
        self.execs.append(query)
        return RemoteResult(0, "")

    def link(self) -> Link:
        return Link(self.post, self.sql, lambda: None, "op001", "2026-10-01", ["secret-x"])


class SeedCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.server = FakeServer()
        self.ctx = SeedContext(self.dir, self.server.link())

    def tearDown(self) -> None:
        self.tmp.cleanup()


class ParseTableTest(unittest.TestCase):
    def test_rows_and_null(self) -> None:
        text = "﻿code\tname\n----\t----\nC0001\t客户A\nC0002\tNULL\n\n(2 rows affected)\n"
        self.assertEqual(parse_table(text), [{"code": "C0001", "name": "客户A"}, {"code": "C0002", "name": ""}])

    def test_empty_and_short_rows(self) -> None:
        self.assertEqual(parse_table("code\n----\n"), [])
        self.assertEqual(parse_table(""), [])
        self.assertEqual(parse_table("a\tb\n-\t-\nx\n"), [{"a": "x", "b": ""}])

    def test_tab_only_row_is_a_row(self) -> None:
        self.assertEqual(parse_table("a\tb\n-\t-\n\t\nx\ty\n\n"), [{"a": "", "b": ""}, {"a": "x", "b": "y"}])

    def test_stops_at_affected_line(self) -> None:
        self.assertEqual(parse_table("code\n----\nA\n(1 行受影响)\nB\n"), [{"code": "A"}])


class PerformerTest(unittest.TestCase):
    def _rows(self) -> list[dict[str, str]]:
        return parse_table(table(["biz", "performer"], [list(row) for row in _ROWS]))

    def test_all_codes_skip_placeholders(self) -> None:
        self.assertEqual(steps_people.performer_codes(self._rows()), ["P0001", "P0002", "P0003", "P0004"])

    def test_row_codes(self) -> None:
        self.assertEqual(steps_people.row_codes("员工[|hr_hi_person.cpsn_num| = '$<StartUser>']"), [])
        self.assertEqual(steps_people.row_codes("|hr_hi_Person.cPsn_Num| = &apos;P0009&apos;"), ["P0009"])


class PeopleStepTest(SeedCase):
    def test_creates_missing_performers_in_workflow_dept(self) -> None:
        self.server.existing.add("P0002")
        new, existed = steps_people.STEPS[0].apply(self.ctx)
        self.assertEqual((new, existed), (3, 1))
        created = [body for _path, body in self.server.posts]
        self.assertEqual([body["code"] for body in created], ["P0001", "P0003", "P0004"])
        self.assertTrue(all(body["fields"]["cdept_num"] == data.DEPT_WORKFLOW for body in created))

    def test_links_operator_to_dedicated_person(self) -> None:
        self.assertEqual(steps_people.STEPS[1].apply(self.ctx), (1, 0))
        sql = self.server.execs[-1]
        self.assertIn("N'" + data.OPERATOR_PERSON + "'", sql)
        self.assertIn("N'op001'", sql)
        self.assertIn("INSERT INTO UserHrPersonContro", sql)
        self.assertNotIn("N'P0001'", sql)
        self.assertEqual(self.ctx.state["operator_person"], data.OPERATOR_PERSON)

    def test_dedicated_person_is_no_performer(self) -> None:
        self.assertNotIn(data.OPERATOR_PERSON, [code for _biz, text in _ROWS for code in steps_people.row_codes(text)])
        self.assertIn(data.OPERATOR_PERSON, [arc.code for arc in data.PERSONS])

    def test_link_to_performer_is_moved(self) -> None:
        self.server.linked = "P0001"
        self.assertEqual(steps_people.STEPS[1].probe(self.ctx).missing, ("op001→" + data.OPERATOR_PERSON + "（现为 P0001）",))
        self.assertEqual(steps_people.STEPS[1].apply(self.ctx), (1, 0))
        self.assertIn("UPDATE UserHrPersonContro SET cPsn_Num = N'" + data.OPERATOR_PERSON + "'", self.server.execs[-1])

    def test_existing_dedicated_link_is_kept(self) -> None:
        self.server.linked = data.OPERATOR_PERSON.lower()
        self.assertEqual(steps_people.STEPS[1].apply(self.ctx), (0, 1))
        self.assertEqual(self.server.execs, [])

    def test_link_sql_passes_guard(self) -> None:
        text = steps_people.LINK_SET_SQL.format(operator="N'op001'", person="N'" + data.OPERATOR_PERSON + "'")
        self.assertEqual(sql_block_reason("SET NOCOUNT ON;\n" + text), "")


class IdempotencyTest(SeedCase):
    def test_skips_existing_codes(self) -> None:
        self.server.existing.update({"C0001", "C0003"})
        new, existed = ensure_archives(self.ctx, data.CUSTOMERS)
        self.assertEqual((new, existed), (len(data.CUSTOMERS) - 2, 2))
        posted = [body["code"] for _path, body in self.server.posts]
        self.assertNotIn("C0001", posted)
        self.assertEqual(ensure_archives(self.ctx, data.CUSTOMERS), (0, len(data.CUSTOMERS)))

    def test_conflict_counts_as_existing(self) -> None:
        self.server.reply = {"ok": False, "status": 409, "code": "conflict", "message": "档案编码已存在"}
        self.assertFalse(create_archive(self.ctx, data.DEPARTMENTS[0]))

    def test_failure_carries_saved_call(self) -> None:
        self.server.reply = {"ok": False, "status": 400, "code": "bad_request", "message": "未知字段 x", "field": "x"}
        with self.assertRaises(SeedError) as caught:
            create_archive(self.ctx, data.DEPARTMENTS[0])
        self.assertTrue(Path(caught.exception.saved).is_file())
        self.assertIn("未知字段", str(caught.exception))

    def test_calls_saved_without_secret(self) -> None:
        self.server.reply = {"ok": False, "status": 500, "message": "secret-x leaked"}
        with self.assertRaises(SeedError):
            create_archive(self.ctx, data.DEPARTMENTS[0])
        text = Path(self.ctx.last_call).read_text(encoding="utf-8")
        self.assertNotIn("secret-x", text)

    def test_run_stops_at_failure_and_keeps_state(self) -> None:
        self.server.reply = {"ok": False, "status": 400, "message": "不行"}
        self.assertEqual(run(self.ctx, select("units,rd_styles")), 1)
        state = json.loads((self.dir / "seed-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["failed"], "units")
        self.assertEqual(len(self.server.posts), 1)

    def test_run_records_steps(self) -> None:
        self.assertEqual(run(self.ctx, select("units")), 0)
        state = json.loads((self.dir / "seed-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["steps"]["units"]["new"], 2)


class SelectTest(unittest.TestCase):
    def test_unknown_step(self) -> None:
        with self.assertRaises(SeedError):
            select("units,nope")

    def test_order_follows_canonical(self) -> None:
        self.assertEqual([step.name for step in select("vendors,units")], ["units", "vendors"])
        self.assertEqual(len({step.name for step in ALL_STEPS}), len(ALL_STEPS))


class RefusalTest(unittest.TestCase):
    def test_refusals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            good = {"owner": "session", "report_dir": tmp}
            seedable = SimpleNamespace(seedable=True, account="801")
            self.assertIn("没有打开的会话", refusal(None, seedable))
            self.assertIn("不是 session", refusal(dict(good, owner="run"), seedable))
            self.assertIn("seedable", refusal(good, SimpleNamespace(account="802")))
            self.assertIn("seedable", refusal(good, SimpleNamespace(seedable=False, account="802")))
            self.assertIn("会话目录", refusal(dict(good, report_dir=tmp + "/none"), seedable))
            self.assertEqual(refusal(good, seedable), "")

    def test_only_the_flag_decides_not_the_account_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            good = {"owner": "session", "report_dir": tmp}
            for code in ("801", "802", "998"):
                self.assertEqual(refusal(good, SimpleNamespace(seedable=True, account=code)), "")
                self.assertIn("account.seedable", refusal(good, SimpleNamespace(seedable=False, account=code)))


class SqlTextTest(unittest.TestCase):
    def test_seed_sql_passes_guard(self) -> None:
        for text in (template_sql(), flags_sql(), steps_people.RELEASES_SQL):
            self.assertEqual(sql_block_reason("SET NOCOUNT ON;\n" + text), "")
        self.assertNotIn("{{", template_sql())
        self.assertIn(data.INV_TEMPLATE, template_sql())


class EmitTest(SeedCase):
    def setUp(self) -> None:
        super().setUp()
        self.target = self.dir / "testdata-seedacct.json"
        self.cfg = SimpleNamespace(account="seedacct", lane="", testdata_file=str(self.target))

    def test_refuses_taken_cycle_codes(self) -> None:
        self.server.existing.add(data.CYCLE_CODES["department"][0])
        with self.assertRaises(SeedError):
            emit.emit(self.ctx, self.target, self.cfg)

    def test_refuses_default_or_untagged_path(self) -> None:
        default = Path(emit.DEFAULT_TESTDATA).expanduser()
        self.assertIn("缺省路径", emit.target_refusal(default, SimpleNamespace(account="u8co", lane="")))
        self.assertIn("通道名或账套号", emit.target_refusal(self.dir / "td.json", self.cfg))
        lane = SimpleNamespace(account="999", lane="lanex")
        self.assertEqual(emit.target_refusal(self.dir / "lanex" / "td.json", lane), "")
        with self.assertRaises(SeedError):
            emit.emit(self.ctx, self.dir / "td.json", self.cfg)

    def test_merges_keeps_unknown_keys_and_first_backup(self) -> None:
        original = {"extra": {"k": "v"}, "archives": {"customer": {"note": "x", "sort_code": "OLD"}}}
        self.target.write_text(json.dumps(original), encoding="utf-8")
        self.assertEqual(emit.emit(self.ctx, self.target, self.cfg), (1, 0))
        written = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(written["extra"], {"k": "v"})
        self.assertEqual(written["archives"]["customer"]["note"], "x")
        self.assertEqual(written["archives"]["customer"]["sort_code"], "C10")
        backup = self.dir / "testdata-seedacct.json.bak"
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), original)
        self.assertEqual(emit.emit(self.ctx, self.target, self.cfg), (0, 1))
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), original)
        self.assertIn(str(backup), emit.reminder(self.ctx.state))

    def test_writes_example_keys(self) -> None:
        emit.emit(self.ctx, self.target, self.cfg)
        written = json.loads(self.target.read_text(encoding="utf-8"))
        example = support.sample_testdata()
        # ato.* 由造数步骤 ato（steps_docs_ato）写，不在这一步（test_seed_ato 核对）。
        self.assertEqual(set(written), set(example) - {"ato"})
        for kind, node in example["archives"].items():
            self.assertEqual(set(written["archives"][kind]), set(node))
        self.assertEqual(set(written["stock"]), set(example["stock"]))
        self.assertEqual(written["stock"]["material_out_rd"], data.MATERIAL_OUT_RD)
        self.assertEqual(written["stock"]["product_in_rd"], data.PRODUCT_IN_RD)
        self.assertFalse((self.dir / "testdata-seedacct.json.bak").exists())
        self.assertIn("删掉", emit.reminder(self.ctx.state))

    def test_bank_account_is_picked_from_the_lane_chart(self) -> None:
        self.server.bank = "100201"
        self.target.write_text(json.dumps({"gl": {"bank_account": "100200"}}), encoding="utf-8")
        emit.emit(self.ctx, self.target, self.cfg)
        written = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(written["gl"]["bank_account"], "100201")
        self.assertEqual(written["gl"]["expense_account"], data.GL["expense_account"])

    def test_lane_bank_account_is_kept_when_the_chart_has_none(self) -> None:
        self.target.write_text(json.dumps({"gl": {"bank_account": "100200"}}), encoding="utf-8")
        emit.emit(self.ctx, self.target, self.cfg)
        written = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(written["gl"]["bank_account"], "100200")

    def test_placeholder_bank_account_is_the_last_resort(self) -> None:
        emit.emit(self.ctx, self.target, self.cfg)
        written = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(written["gl"]["bank_account"], data.GL["bank_account"])


if __name__ == "__main__":
    unittest.main()
