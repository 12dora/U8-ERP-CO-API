# 脚本校验、测试账套、写窗口和口令擦除。不连接服务器。
from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path

from co.e2e.cases import load_operator
from co.probe.comprobe import (
    _parser,
    build_body,
    ensure_body,
    launch_body,
    probe_config,
    load_script,
    prepare_script,
    scrub_output,
    script_writes,
    stdin_payload,
    validate_script,
    write_block_reason,
    write_budget,
)
from co.e2e.tests import support


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()

_SNAPS = "UFDATA_801_2024_u8co_e2e_snap,UFMeta_801_u8co_e2e_snap"
_SECRET = "s3cret-value"


def _status(minutes: str = "12", snaps: str = _SNAPS, done: str = "no") -> str:
    return "\n".join([
        "snapshots " + snaps,
        "done " + done,
        "minutes_left " + minutes,
    ])


def _login(**extra: object) -> dict:
    step = {"op": "login", "sub": "SA"}
    step.update(extra)
    return {"steps": [step]}


class ScriptTests(unittest.TestCase):
    def test_login_and_read_steps(self) -> None:
        steps = validate_script({
            "steps": [
                {"op": "login", "sub": "SA", "year": "2024", "operator": "op001"},
                {"op": "sql", "query": "select top 1 ID from SO_SOMain where ID=?", "params": [1]},
                {"op": "dom.blank", "sql": "select '' as editprop, v.* from zpurpoheader v where 1=2", "var": "head"},
                {"op": "lastsql"},
            ],
        })
        self.assertEqual(len(steps), 4)
        self.assertFalse(script_writes(steps))

    def test_unknown_op_and_shape(self) -> None:
        with self.assertRaises(ValueError):
            validate_script({"steps": [{"op": "save"}]})
        with self.assertRaises(ValueError):
            validate_script({"steps": [{"op": "call", "target": "$co"}]})
        with self.assertRaises(ValueError):
            validate_script({"steps": "login"})
        with self.assertRaises(ValueError):
            validate_script({"steps": [{"op": "login", "sub": "SA", "password": _SECRET}]})

    def test_call_args(self) -> None:
        steps = validate_script({
            "steps": [{
                "op": "call",
                "target": "$co",
                "method": "Save",
                "args": ["$head", {"short": 0}, {"int": 1}, {"bool": False}, None],
                "byref": [3],
                "dispatch_null": [4],
                "var": "ret",
            }],
        })
        self.assertTrue(script_writes(steps))
        with self.assertRaises(ValueError):
            validate_script({"steps": [{
                "op": "call", "target": "$co", "method": "Save", "args": ["$head"], "byref": [1],
            }]})
        with self.assertRaises(ValueError):
            validate_script({"steps": [{
                "op": "call", "target": "$co", "method": "Save", "args": [{"short": 1, "int": 2}],
            }]})

    def test_secret_only_as_call_arg(self) -> None:
        call = {"op": "call", "target": "$co", "method": "LoginDB", "args": ["$__secret", {"date": "2026-09-29"}]}
        validate_script({"steps": [call]})
        validate_script({"steps": [dict(call, byref=[0], tolerate=True)]})
        bad = [
            {"op": "sql", "query": "select ? as x", "params": ["$__secret"]},
            {"op": "set", "target": "$co", "prop": "Password", "value": "$__secret"},
            {"op": "dom.set", "dom": "$head", "row": 0, "attr": "cMemo", "value": "$__secret"},
            {"op": "get", "target": "$__secret", "prop": "x"},
            {"op": "call", "target": "$__secret", "method": "X"},
            {"op": "dom.xml", "dom": "$__secret"},
        ]
        for step in bad:
            with self.subTest(op=step["op"]), self.assertRaises(ValueError):
                validate_script({"steps": [step]})

    def test_tolerate_only_on_call_and_bool(self) -> None:
        validate_script({"steps": [{"op": "call", "target": "$co", "method": "X", "tolerate": False}]})
        with self.assertRaises(ValueError):
            validate_script({"steps": [{"op": "call", "target": "$co", "method": "X", "tolerate": 1}]})
        with self.assertRaises(ValueError):
            validate_script({"steps": [{"op": "sql", "query": "select 1", "tolerate": True}]})

    def test_date_arg_shape(self) -> None:
        validate_script({"steps": [{"op": "call", "target": "$co", "method": "X", "args": [{"date": "2026-09-29"}]}]})
        for bad in ("2026/09/29", "20260929", 20260929):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_script({"steps": [{"op": "call", "target": "$co", "method": "X", "args": [{"date": bad}]}]})

    def test_bad_script_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.json"
            path.write_text("[", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_script(path)


class AccountTests(unittest.TestCase):
    def test_only_configured_account(self) -> None:
        validate_script(_login())
        validate_script(_login(acc="801"))
        validate_script(_login(acc=801))
        for bad in ("001", "002", "003", "0801", 90, ""):
            with self.assertRaises(ValueError) as caught:
                validate_script(_login(acc=bad))
            self.assertIn("801", str(caught.exception))
        with self.assertRaises(ValueError):
            validate_script(_login(account="001"))

    def test_prepare_fills_login(self) -> None:
        prepared = prepare_script(_login(account="801"), "2024", "op001")
        login = prepared["steps"][0]
        self.assertEqual(login["acc"], "801")
        self.assertNotIn("account", login)
        self.assertEqual(login["year"], "2024")
        self.assertEqual(login["operator"], "op001")
        self.assertNotIn(_SECRET, json.dumps(prepared))


class GateTests(unittest.TestCase):
    def test_write_ops(self) -> None:
        writes = ("call", "trans.begin", "trans.commit", "trans.rollback", "dom.set")
        for op in writes:
            self.assertTrue(script_writes([{"op": op}]))
        quiet = ("login", "create", "get", "set", "sql", "dom.addrow", "dom.rows", "lastsql")
        for op in quiet:
            self.assertFalse(script_writes([{"op": op}]))

    def test_window(self) -> None:
        call = [{"op": "call"}]
        self.assertEqual(write_block_reason(call, _status("5")), "")
        self.assertEqual(write_block_reason(call, _status("12")), "")
        self.assertIn("5", write_block_reason(call, _status("4")))
        self.assertIn("5", write_block_reason(call, _status("已触发")))
        one = "UFDATA_801_2024_u8co_e2e_snap"
        self.assertIn("快照", write_block_reason(call, _status(snaps=one)))
        self.assertIn("还原", write_block_reason(call, _status(done="yes")))
        read = [{"op": "sql"}, {"op": "set"}]
        self.assertEqual(write_block_reason(read, ""), "")
        self.assertEqual(write_block_reason(read, _status("0", snaps="none", done="yes")), "")


    def test_write_budget(self) -> None:
        # 写步骤只能用到看门狗到点前 3 分钟，最多 900 秒；comprobe.exe 在每个写步骤前核对。
        self.assertEqual(write_budget({"minutes_left": "5"}), 120)
        self.assertEqual(write_budget({"minutes_left": "60"}), 900)
        self.assertEqual(write_budget({"minutes_left": "已触发"}), 60)
        doc = prepare_script({"steps": [{"op": "call", "target": "$a", "method": "M"}]}, "2024", "op001", 120)
        self.assertEqual(doc["write_budget_s"], 120)
        self.assertNotIn("write_budget_s", prepare_script({"steps": []}, "2024", "op001"))
        main = (_HERE / "comprobe" / "ProbeMain.cs").read_text(encoding="utf-8-sig")
        self.assertLess(main.index("CheckBudget(steps[i]"), main.index("Dispatch(run, steps[i])"))
        for op in ("call", "trans.begin", "trans.commit", "dom.set"):
            self.assertIn('"' + op + '"', main[main.index("WriteOps"):])
        self.assertIn('"write_budget_s"', (_HERE / "comprobe" / "ProbeParse.cs").read_text(encoding="utf-8-sig"))


class SecretTests(unittest.TestCase):
    def test_scrub_and_stdin(self) -> None:
        self.assertEqual(scrub_output("see " + _SECRET + " end", _SECRET), "see *** end")
        self.assertEqual(scrub_output("plain", ""), "plain")
        payload = stdin_payload(_SECRET, '{"steps":[]}')
        self.assertTrue(payload.startswith((_SECRET + "\n").encode()))
        self.assertNotIn(_SECRET, launch_body())

    def test_no_password_argument(self) -> None:
        with self.assertRaises(SystemExit):
            _parser().parse_args(["run", "--script", "x.json", "--password", _SECRET])
        args = _parser().parse_args(["run", "--script", "x.json"])
        self.assertEqual(args.script, "x.json")
        self.assertFalse(hasattr(args, "password"))

    def test_credential_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "u8co-test.json"
            body = {"acc": "801", "year": "2024", "operator": "op001", "password": _SECRET}
            path.write_text(json.dumps(body), encoding="utf-8")
            path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
            with self.assertRaises(RuntimeError):
                load_operator(path)
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            operator = load_operator(path)
            self.assertEqual(operator.acc, "801")
            self.assertNotIn(_SECRET, repr(operator))
            path.write_text(json.dumps({**body, "acc": "001"}), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_operator(path)



_HERE = Path(__file__).resolve().parents[1]


class SiteConfigTests(unittest.TestCase):
    def test_bodies_follow_config(self) -> None:
        root = "C:\\ProgramData\\U8Co\\u8co-e2e\\comprobe"
        self.assertIn(root + "\\out\\comprobe.exe", launch_body())
        self.assertIn(root + "\\build.ps1", build_body())
        self.assertIn("'D:\\U8SOFT', 'D:\\'", ensure_body())
        doc = probe_config()
        self.assertEqual(doc["account"], "801")
        self.assertEqual(doc["probeRoot"], root)
        self.assertNotIn("password", json.dumps(doc))

    def test_other_account_config(self) -> None:
        # 别的账套就是另一条调试通道：名字不能停在缺省值上（co/e2e/lane.py），用通道样例。
        support.install(support.lane_config("802"))
        try:
            validate_script(_login(acc="802"))
            with self.assertRaises(ValueError):
                validate_script(_login(acc="801"))
        finally:
            support.install()

    def test_sources_carry_no_site_values(self) -> None:
        paths = list((_HERE / "comprobe").glob("*.cs")) + [_HERE / "comprobe" / "build.ps1"]
        paths += [_HERE / "u8co-probe.ps1", _HERE / "u8co-probe-login.ps1"]
        for path in paths:
            text = path.read_text(encoding="utf-8-sig")
            for needle in ("801", "ProgramData", '"001"', "UFDATA_0"):
                self.assertNotIn(needle, text, path.name)
        login = (_HERE / "u8co-probe-login.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("$DenyAccounts", login)


class ProbeScriptTests(unittest.TestCase):
    def test_lists_passed_once_when_relaunching(self) -> None:
        # pwsh -File 不接受同一参数出现两次：切到 32 位时每个清单只传一次，逗号连成一个字符串，进来后再拆开校验。
        text = (_HERE / "u8co-probe.ps1").read_text(encoding="utf-8-sig")
        body = text[text.index("function Add-ListArgs") : text.index("function Split-AccountList")]
        self.assertNotIn("foreach", body)
        self.assertIn('-join ","', body)
        self.assertIn('Split-AccountList $DenyAccounts "DenyAccounts"', text)
        self.assertIn('Split-AccountList $TestAccountAllowlist "TestAccountAllowlist"', text)
        self.assertLess(text.index("Split-AccountList $DenyAccounts"), text.index("Start-Probe32 $PSBoundParameters"))

    def test_u8_home_reaches_staging(self) -> None:
        probe = (_HERE / "u8co-probe.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('Initialize-Staging $here "" $U8Home', probe)
        build = (_HERE.parent / "bridge" / "build.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('Initialize-Staging $here "" $U8Home', build)
        self.assertIn("[string]$U8Home", build)


if __name__ == "__main__":
    unittest.main()
