"""用例登记、挑单 SQL 和断言。不连接服务器。"""

from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from co.e2e.casespec.assertutil import (
    code_matches,
    expected_history,
    first_cell,
    history_actions,
    instance_off,
    judge_cell,
    judge_verifier,
    long_opinion,
    multiple_qty,
    sql_cells,
    task_hit,
)
from co.e2e.casespec.bodies import other_in_body, protected_body, sale_body
from co.e2e.casespec import qm_workflow
from co.e2e.casespec.catalog import build_cases, known_names
from co.e2e.casespec.checks import View, match_checks
from co.e2e.casespec.picks import pick_keys
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.select import filter_cases, parse_names, reject_unknown
from co.e2e.casespec.sql_qm import qm_state_sql
from co.e2e.casespec.sql_shape import shape_qm04
from co.e2e.report import render_markdown
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()

_ROOT = Path(__file__).resolve().parents[1]


def _by_name(name: str, bag: dict | None = None):
    for spec in build_cases(bag or {}):
        if spec.name == name:
            return spec
    raise AssertionError(name)


class SelectTests(unittest.TestCase):
    def test_default_is_all_and_subset_keeps_registry_order(self) -> None:
        specs = build_cases({})
        self.assertIsNone(parse_names(""))
        self.assertEqual(filter_cases(specs, None), specs)
        chosen = filter_cases(specs, parse_names(" login, health "))
        self.assertEqual([item.name for item in chosen], ["health", "login"])

    def test_unknown_and_duplicate_names(self) -> None:
        names = known_names()
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(reject_unknown("", names), "")
        self.assertIn("未知用例", reject_unknown("health,missing", names))
        with self.assertRaises(ValueError):
            parse_names("health,health")
        with self.assertRaises(ValueError):
            parse_names(",,")
        with self.assertRaises(ValueError):
            filter_cases(build_cases({}), ("no-such",))

    def test_every_need_is_picked(self) -> None:
        keys = set(pick_keys())
        for spec in build_cases({}):
            for need in spec.needs:
                self.assertIn(need, keys, spec.name)


class SqlTests(unittest.TestCase):
    def test_samples_pass_the_guard(self) -> None:
        for name, sql in sample_sql():
            self.assertEqual(sql_block_reason(sql), "", name)
            self.assertIn("ROW:", sql, name)
            self.assertNotIn(("UFDATA_" + support.foreign_account()).upper(), sql.upper())
            self.assertNotIn("$(", sql)

    def test_shape_predicates_and_rejection(self) -> None:
        sql = shape_qm04("6117", "op001")
        for needle in (
            "UPDATE dbo.Table_Task",
            "SET cUserID = @person",
            "RTRIM(cVoucherType) = N'QM04'",
            "cTK_State = 0",
            "cTaskType IN (1, 4)",
            "ORDER BY cUserID",
            "dbo.UserHrPersonContro",
            "cPsn_Num",
            "cUser_Id = @operator",
            "@@ROWCOUNT",
        ):
            self.assertIn(needle, sql)
        self.assertEqual(sql_block_reason(sql), "")
        with self.assertRaises(ValueError):
            shape_qm04("0", "op001")
        with self.assertRaises(ValueError):
            shape_qm04("6117", "op001'")
        with self.assertRaises(ValueError):
            qm_state_sql("QM05")

    def test_pick_filters(self) -> None:
        text = dict(sample_sql())
        self.assertIn("IsWfControlled = 1", text["qm04"])
        self.assertIn("iVerifyStateNew = 0", text["qm04"])
        self.assertIn("ISNULL(IBATCHCHKRESULT, 0) = 0", text["qm04"])
        self.assertIn("w.bWhPos = 0", text["other-in-master"])
        self.assertIn("s.bRdFlag = 1", text["other-in-master"])
        self.assertIn("s.bRdEnd = 1", text["other-in-master"])
        self.assertIn("bInvBatch", text["other-in-master"])
        self.assertIn("s.bRdFlag = 0", text["other-out-master"])
        self.assertIn("iQuantity >= 2", text["other-out-master"])
        self.assertIn("CardNumber = N'0301'", text["prefix"])
        self.assertIn("Prefix1Rule", text["prefix"])
        self.assertIn("iInvExchRate", text["sale-master"])
        self.assertIn("CVOUCHTYPE = N'QM03'", text["load_qm_incoming_check"])
        self.assertIn("CVOUCHTYPE = N'QM06'", text["load_qm_product_reject"])


class AssertTests(unittest.TestCase):
    def test_sql_cells_ignore_noise(self) -> None:
        text = "Changed database context.\nv\n-\nROW:15\n\n(1 rows affected)\n"
        self.assertEqual(sql_cells(text), ["15"])
        self.assertEqual(first_cell("ROW:张三|2026-09-27 00:00:00\n"), "张三|2026-09-27 00:00:00")

    def test_verifier_prefix_and_quantity(self) -> None:
        self.assertEqual(judge_verifier("操作员甲|2026-09-27 01:02:03", "verified", "操作员甲")[0], "PASS")
        self.assertEqual(judge_verifier("|", "cleared", "操作员甲")[0], "PASS")
        self.assertEqual(judge_verifier("别人|2026-09-27", "verified_any", "操作员甲")[0], "PASS")
        self.assertEqual(judge_cell("1", "1")[0], "PASS")
        self.assertEqual(judge_cell("2", "$gt0")[0], "PASS")
        self.assertEqual(judge_cell("0", "$gt0")[0], "FAIL")
        self.assertTrue(code_matches("QZ20260927001", "P:QZ", "2026-09-27"))
        self.assertTrue(code_matches("20260927001", "P:", "2026-09-27"))
        self.assertFalse(code_matches("XX", "P:QZ", "2026-09-27"))
        self.assertEqual(multiple_qty("2.50"), "2.5")
        self.assertEqual(multiple_qty("0"), "1")
        self.assertEqual(multiple_qty(""), "1")
        self.assertEqual(len(long_opinion()), 501)

    def test_workflow_payload_helpers(self) -> None:
        state = {"wf": {"status": "not_submitted", "instance": None, "pending": [{}, {}]}}
        self.assertTrue(instance_off(state))
        self.assertFalse(instance_off({"wf": {"instance": {"running": True}}}))
        history = {"history": [{"action": n} for n in (0, 1, 7, 6, 8, 1, 1, 7, 2)]}
        self.assertEqual(history_actions(history), expected_history())
        tasks = {"tasks": [{"id": 6117, "task_type": 5}, {"id": 1, "task_type": 1}]}
        self.assertTrue(task_hit(tasks, "6117", "5"))
        self.assertFalse(task_hit(tasks, "6117", "1"))
        view = View("6117", "P:QZ", "2026-09-27", "操作员甲")
        status, detail = match_checks(history, (("history#actions", expected_history()),), view)
        self.assertEqual(status, "PASS", detail)
        status, detail = match_checks(
            {"wf": {"pending": [1, 2]}, "code": "QZ20260927001", "head": {"cCode": "1"}},
            (("wf.pending#len", "2"), ("code#prefix", "yes"), ("head#nonempty", "yes"), ("wf.verifier", "$opname")),
            view,
        )
        self.assertEqual(status, "FAIL", detail)


class CaseShapeTests(unittest.TestCase):
    def test_smoke_routes_and_dispatch_order(self) -> None:
        sale = _by_name("sale-verify")
        self.assertEqual(sale.steps[0].route, "/v1/sale-orders/verify")
        self.assertEqual(dict(sale.steps[0].fields)["action"], "verify")
        first = _by_name("dispatch-verify", {"dispatch_mode": "unverify-first"})
        actions = [dict(step.fields)["action"] for step in first.steps if step.kind == "call"]
        self.assertEqual(actions, ["unverify", "verify"])
        account = _by_name("account-foreign")
        self.assertEqual([step.kind for step in account.steps], ["accforeign", "audit"])
        load = _by_name("account-foreign-load")
        self.assertEqual([step.kind for step in load.steps], ["callforeign", "audit"])

    def test_workflow_cycle_order(self) -> None:
        spec = _by_name("wf-qm04-cycle")
        self.assertEqual(spec.steps[-1].sql_name, qm_workflow.REST_OP)
        steps = spec.steps + qm_workflow.rest_steps()
        shapes = [step for step in steps if step.sql_name == "shape"]
        self.assertEqual(len(shapes), 5)
        self.assertTrue(all(step.code == "1" for step in shapes))
        self.assertEqual(steps[-1].route, "/v1/workflow/history")
        self.assertEqual(steps[-1].checks[0][1], expected_history())
        withdraw = _by_name("wf-qm04-withdraw")
        self.assertEqual(withdraw.needs, ("qm04_b",))
        self.assertEqual(withdraw.steps[-1].sql_name, "running")
        other = _by_name("wf-other-in-type")
        self.assertEqual(other.steps[0].status, 400)
        self.assertEqual(dict(other.steps[0].fields)["type"], "other_in")

    def test_create_bodies(self) -> None:
        env = type("E", (), {})()
        env.today = "2026-09-27"
        env.bag = {
            "other_in_master": "01|101|D01|INV01",
            "sale_master": "C01|ST|D01|P01|INV|2.5|10|13|2026-09-28",
        }
        made = other_in_body(env)
        self.assertEqual(made["head"]["cMemo"], "CO桥回归")
        self.assertNotIn("cMaker", made["head"])
        self.assertEqual(made["lines"][0]["iQuantity"], 2)
        sold = sale_body(env)
        qty = Decimal(str(sold["lines"][0]["iQuantity"]))
        self.assertEqual(qty % Decimal("2.5"), 0)
        self.assertEqual(sold["head"]["cBusType"], "普通销售")
        self.assertEqual(sold["head"]["cexch_name"], "人民币")
        self.assertIn("cMaker", protected_body(env)["head"])
        created = _by_name("other-in-cycle")
        self.assertIn(("code#prefix", "yes"), created.steps[0].checks)
        self.assertEqual(_by_name("create-protected").steps[0].status, 400)
        self.assertEqual(_by_name("opinion-too-long").steps[0].code, "bad_request")
        self.assertEqual(len([spec for spec in build_cases({}) if spec.name.startswith("load-")]), 27)


class RedeployTests(unittest.TestCase):
    def test_script_copies_config_and_waits(self) -> None:
        text = (_ROOT / "remote" / "service-redeploy.ps1").read_text(encoding="utf-8")
        self.assertIn('$script:Exe + ".config"', text)
        self.assertIn('$script:Exe + ".config.prev"', text)
        self.assertIn('$script:Exe + ".prev"', text)
        # 只认本通道的桥进程（路径或停之前记下的进程号），不被别的通道的同名 exe 卡住。查找和结束在 bridgelib.ps1。
        lib = (_ROOT / "remote" / "bridgelib.ps1").read_text(encoding="utf-8")
        self.assertIn("Win32_Process", lib)
        self.assertIn("$script:OldPid", lib)
        self.assertIn("CreationDate", lib)
        self.assertNotIn("Get-Process", text + lib)
        self.assertNotIn("Stop-Process -Name", text + lib)
        self.assertIn("function Wait-BridgeExit", lib)
        # 60 秒不退先按路径结束并确认退出，之后才换 exe；旧进程还活着时不启动服务。
        body = text[text.index("function Update-InstalledBridge") :]
        self.assertLess(body.index("Save-ServicePid"), body.index("service-stop.ps1"))
        self.assertLess(body.index("Stop-BridgeLeftover 60"), body.index("Copy-KitExe"))
        start = text[text.index("function Start-Installed") : text.index("function Restore-One")]
        self.assertLess(start.index("Test-BridgeAlive"), start.index("service-start.ps1"))
        self.assertIn("Test-Protected $Full", text)
        self.assertIn("Assert-NoReparse", text)
        self.assertNotIn("ProgramData", text)
        self.assertNotIn("secret.hex", text)
        # 套件改为打成一个包上传，exe.config 必须在包里。
        pack = (_ROOT / "kitpack.py").read_text(encoding="utf-8")
        self.assertIn("u8co-bridge.exe.config", pack)

    def test_report_is_a_table(self) -> None:
        text = render_markdown({"tests": [{"name": "健康检查", "status": "PASS", "detail": "ok"}], "cases": "health"})
        self.assertIn("| 健康检查 | PASS | ok |", text)
        self.assertIn("用例：health", text)


if __name__ == "__main__":
    unittest.main()
