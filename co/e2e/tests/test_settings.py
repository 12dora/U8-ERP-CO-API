"""e2e 配置、服务器端 e2e.json 和测试数据。不连接服务器。"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from co.e2e import settings, testdata
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.runner import run_one
from co.e2e.context import Run
from co.e2e.remote import ensure_body, fixed_call, idle_body, repo_root
from co.e2e.settings import ConfigError, from_dict, remote_doc
from co.e2e.tests import support


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _with(section: str, key: str, value: object) -> dict:
    data = copy.deepcopy(support.sample_dict())
    data.setdefault(section, {})[key] = value
    return data


def _without(section: str, key: str) -> dict:
    data = copy.deepcopy(support.sample_dict())
    data[section].pop(key, None)
    return data


class ConfigTests(unittest.TestCase):
    def test_perm_operator_file_optional(self) -> None:
        cfg = from_dict(support.sample_dict())
        self.assertTrue(cfg.perm_operator_file.endswith("perm-operator.json"))
        self.assertNotIn("~", cfg.perm_operator_file)
        self.assertTrue(support.lane_config().perm_operator_file.endswith("perm-operator.json"))
        self.assertEqual(from_dict(_without("account", "perm_operator_file")).perm_operator_file, "")
        self.assertEqual(from_dict(_with("account", "perm_operator_file", "")).perm_operator_file, "")
        expanded = from_dict(_with("account", "perm_operator_file", "~/lane/perm-operator.json"))
        self.assertTrue(expanded.perm_operator_file.endswith("lane/perm-operator.json"))
        self.assertNotIn("~", expanded.perm_operator_file)

    def test_example_loads_with_defaults(self) -> None:
        cfg = from_dict(support.sample_dict())
        self.assertEqual(cfg.databases, ("UFDATA_801_2024", "UFMeta_801"))
        self.assertEqual(cfg.snapshots, ("UFDATA_801_2024_u8co_e2e_snap", "UFMeta_801_u8co_e2e_snap"))
        self.assertEqual(cfg.revert_task, "u8co-e2e-revert")
        self.assertEqual(cfg.sql_target, "localhost,1433")
        self.assertEqual(cfg.bridge_path, "/u8co")
        self.assertEqual(cfg.protected, ("D:\\U8SOFT", "D:\\"))
        self.assertEqual(cfg.foreign_probe, support.foreign_account())
        self.assertEqual(cfg.ssh_target, "u8co-ops@192.0.2.10")

    def test_required_keys(self) -> None:
        for section, key in (("server", "host"), ("account", "code"), ("account", "year"), ("account", "denied")):
            with self.assertRaises(ConfigError, msg=key):
                from_dict(_without(section, key))

    def test_denylist_rules(self) -> None:
        with self.assertRaises(ConfigError):
            from_dict(_with("account", "denied", ["801"]))
        with self.assertRaises(ConfigError):
            from_dict(_with("account", "denied", "001"))
        with self.assertRaises(ConfigError):
            from_dict(_with("account", "denied", ["1"]))
        empty = from_dict(_with("account", "denied", []))
        self.assertEqual(empty.denied, ())
        self.assertEqual(empty.foreign_probe, "998")
        with self.assertRaises(ConfigError):
            from_dict(_with("account", "foreign_probe", "801"))

    def test_database_names_bound_to_account(self) -> None:
        with self.assertRaises(ConfigError):
            from_dict(_with("account", "data_db", support.foreign_data_db()))
        with self.assertRaises(ConfigError):
            from_dict(_with("account", "meta_db", support.foreign_meta_db()))
        self.assertEqual(from_dict(_with("account", "data_db", "UFDATA_801_2025")).data_db, "UFDATA_801_2025")

    def test_paths_are_narrow(self) -> None:
        bad = ("C:\\", "C:\\Program Data\\x", "C:\\a\\..\\b", "relative\\x", "C:\\a\\b'c", "D:\\U8SOFT\\e2e")
        for value in bad:
            with self.assertRaises(ConfigError, msg=value):
                from_dict(_with("server", "remote_root", value))
        with self.assertRaises(ConfigError):
            from_dict(_with("bridge", "root", "D:\\Apps\\u8co"))
        with self.assertRaises(ConfigError):
            from_dict(_with("bridge", "root", "C:\\ProgramData\\U8Co\\u8co-e2e\\bridge"))

    def test_u8_home_on_work_drive_protects_only_home(self) -> None:
        cfg = from_dict(_with("server", "u8_home", "C:\\U8SOFT"))
        self.assertEqual(cfg.protected, ("C:\\U8SOFT",))
        with self.assertRaises(ConfigError):
            from_dict(_with("server", "u8_home", "C:\\ProgramData"))

    def test_names_and_ports(self) -> None:
        for section, key, value in (
            ("server", "host", "a b"),
            ("server", "ssh_user", "u;x"),
            ("snapshot", "suffix", "snap"),
            ("snapshot", "task_prefix", "a b"),
            ("bridge", "service_name", "a b"),
            ("bridge", "port", 0),
            ("bridge", "url", "ftp://192.0.2.10/u8co"),
            ("bridge", "url", "http://192.0.2.10:18089"),
            ("sql", "port", True),
            ("server", "timezone", "Nowhere/Zone"),
        ):
            with self.assertRaises(ConfigError, msg=f"{section}.{key}"):
                from_dict(_with(section, key, value))
        self.assertEqual(from_dict(_with("sql", "port", 0)).sql_target, "localhost")

    def test_bridge_url_defaults_to_host(self) -> None:
        cfg = from_dict(_without("bridge", "url"))
        self.assertEqual(cfg.bridge_url, "http://192.0.2.10:18089/u8co")

    def test_bridge_url_matches_listen_prefix(self) -> None:
        # 与桥的 listenPrefix 一致：只收 http，端口写明且等于 bridge.port，路径固定为 /u8co。
        for value in (
            "https://192.0.2.10:18089/u8co",
            "http://192.0.2.10/u8co",
            "http://192.0.2.10:18090/u8co",
            "http://192.0.2.10:18089/api",
            "http://192.0.2.10:18089/u8co/v1",
        ):
            with self.assertRaises(ConfigError, msg=value):
                from_dict(_with("bridge", "url", value))
        cfg = from_dict(_with("bridge", "url", "http://bridge.example.com:18089/u8co/"))
        self.assertEqual(cfg.bridge_url, "http://bridge.example.com:18089/u8co")
        self.assertEqual(cfg.bridge_path, "/u8co")
        with self.assertRaises(ConfigError):
            from_dict(_with("bridge", "port", 18090))

    def test_unknown_keys_rejected(self) -> None:
        for section, key in (("server", "u8home"), ("bridge", "prefix"), ("account", "deny"), ("watchdog", "minutes")):
            with self.assertRaises(ConfigError, msg=f"{section}.{key}") as caught:
                from_dict(_with(section, key, "x"))
            self.assertIn(repr(key), str(caught.exception))
        data = copy.deepcopy(support.sample_dict())
        data["brige"] = {}
        with self.assertRaises(ConfigError) as caught:
            from_dict(data)
        self.assertIn("'brige'", str(caught.exception))

    def test_load_uses_env_path(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "e2e.json"
            with mock.patch.dict(os.environ, {settings.ENV_PATH: str(path)}):
                self.assertEqual(settings.config_path(), path)
                with self.assertRaises(ConfigError):
                    settings.load()
                path.write_text(json.dumps(support.sample_dict()), encoding="utf-8")
                self.assertEqual(settings.load().account, "801")


class SeedableTests(unittest.TestCase):
    def test_default_false(self) -> None:
        self.assertFalse(from_dict(support.sample_dict()).seedable)
        self.assertFalse(support.lane_config().seedable)
        self.assertFalse(from_dict(_with("account", "seedable", False)).seedable)

    def test_lane_may_be_seedable(self) -> None:
        data = support.lane_dict("802")
        data["account"]["seedable"] = True
        self.assertTrue(from_dict(data).seedable)

    def test_seedable_without_lane_tag_allowed(self) -> None:
        cfg = from_dict(_with("account", "seedable", True))
        self.assertTrue(cfg.seedable)
        self.assertEqual(cfg.lane, "")

    def test_sparse_without_lane_tag_allowed(self) -> None:
        cfg = from_dict(_with("account", "sparse_samples", True))
        self.assertTrue(cfg.sparse_samples)
        self.assertEqual(cfg.lane, "")

    def test_denied_account_rejected(self) -> None:
        data = support.lane_dict("802")
        data["account"]["seedable"] = True
        data["account"]["denied"] = ["001", "802"]
        with self.assertRaises(ConfigError):
            from_dict(data)

    def test_remote_doc_carries_flag(self) -> None:
        self.assertIs(remote_doc(from_dict(support.sample_dict()))["Seedable"], False)
        data = support.lane_dict("802")
        data["account"]["seedable"] = True
        self.assertIs(remote_doc(from_dict(data))["Seedable"], True)

    def test_must_be_boolean(self) -> None:
        data = support.lane_dict("802")
        data["account"]["seedable"] = "yes"
        with self.assertRaises(ConfigError):
            from_dict(data)


class RemoteDocTests(unittest.TestCase):
    def test_remote_doc_has_no_local_paths(self) -> None:
        doc = remote_doc(settings.conf())
        text = json.dumps(doc)
        denied = [support.foreign_account(index) for index in range(3)]
        self.assertEqual(doc["DeniedDbs"], [name + code for code in denied for name in ("UFDATA_", "UFMeta_")])
        self.assertEqual(doc["SqlServer"], "localhost,1433")
        self.assertEqual(doc["Protected"], ["D:\\U8SOFT", "D:\\"])
        self.assertGreaterEqual(doc["MinFreeBytes"], 2 * 1024 * 1024 * 1024)
        for needle in ("secret", "operator", "/nonexistent", "~"):
            self.assertNotIn(needle, text)

    def test_bodies_follow_config(self) -> None:
        body = ensure_body()
        self.assertIn("'C:\\ProgramData\\U8Co\\u8co-e2e\\kit\\bridge\\src'", body)
        self.assertIn("'D:\\'", body)
        self.assertIn("u8co-e2e-revert", idle_body())
        build = "& 'C:\\ProgramData\\U8Co\\u8co-e2e\\kit\\bridge\\build.ps1' -U8Home 'D:\\U8SOFT'"
        if any((repo_root() / "co" / "bridge" / "private").glob("*.cs")):
            build += " -ExtraSourceDir 'C:\\ProgramData\\U8Co\\u8co-e2e\\kit\\bridge\\private'"
        self.assertEqual(fixed_call("build"), build)
        support.install(support.make_config(bridge__root="C:\\U8Co\\bridge", server__u8_home="E:\\U8SOFT"))
        try:
            self.assertIn("--root 'C:\\U8Co\\bridge'", fixed_call("check"))
            self.assertIn(" -U8Home 'E:\\U8SOFT'", fixed_call("build"))
        finally:
            support.install()

    def test_ensure_locks_root(self) -> None:
        # e2e 目录在上传脚本之前锁好：断开继承，只给 SYSTEM、Administrators 和计划任务账户，上级目录不许联接点和可删改的 ACE。
        body = ensure_body()
        self.assertNotIn("@", body.replace("@(", "").replace("@grants", "").replace("@()", "").replace("@{", ""))
        self.assertIn("/inheritance:r", body)
        self.assertIn("'*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'", body)
        self.assertIn("/setowner '*S-1-5-32-544'", body)
        self.assertIn("ReparsePoint", body)
        self.assertIn("Assert-Ancestor $dir", body)
        self.assertIn("$name = ''", body)
        support.install(support.make_config(server__windows_principal="HOST1\\u8co-test"))
        try:
            self.assertIn("$name = 'HOST1\\u8co-test'", ensure_body())
        finally:
            support.install()

    def test_idle_checks_existing_remote_config(self) -> None:
        # 服务器上旧 e2e.json 的计划任务和快照还在时不覆盖。
        body = idle_body()
        self.assertIn("'u8co-e2e-revert', 'u8co-e2e-s4u-probe'", body)
        self.assertIn("\\e2e.json", body)
        self.assertIn("[string]$doc.RevertTask, [string]$doc.ProbeTask", body)
        self.assertIn("$db + $suffix", body)
        self.assertIn("-S 'localhost,1433'", body)
        self.assertIn("& 'C:\\Program Files\\Microsoft SQL Server", body)
        self.assertNotIn("@SQL", body)

    def test_remote_common_checks_root_lock(self) -> None:
        common = (Path(__file__).resolve().parents[1] / "remote" / "common.ps1").read_text(encoding="utf-8-sig")
        head = common[: common.index("$script:Cfg = Read-E2EConfig")]
        self.assertIn("function Assert-RootLocked", head)
        self.assertIn("AreAccessRulesProtected", head)
        self.assertLess(head.index("Assert-RootLocked (["), head.index("ReadAllText($path)"))


class _Ctx:
    def __init__(self) -> None:
        self.tests: list[tuple[str, str, str]] = []

    def add_test(self, name: str, status: str, detail: str, extra: dict | None = None) -> None:
        self.tests.append((name, status, detail))


class TestDataTests(unittest.TestCase):
    def tearDown(self) -> None:
        support.install()

    def test_lookup_and_missing(self) -> None:
        self.assertEqual(testdata.value("gl.cash_flow_item"), "07")
        self.assertEqual(testdata.values("archives.customer.codes")[0], "C199901")
        with self.assertRaises(testdata.DataMissing):
            testdata.value("gl.no_such_key")
        self.assertEqual(testdata.first_missing(("gl.cash_flow_item", "stock.nope")), "stock.nope")
        testdata.use({"gl": {"cash_flow_item": "0'7"}})
        with self.assertRaises(testdata.DataMissing):
            testdata.value("gl.cash_flow_item")

    def test_gl_signs_default_override_and_reject(self) -> None:
        testdata.use({})
        self.assertEqual((testdata.gl_sign(testdata.SIGN_TRANSFER), testdata.gl_sign(testdata.SIGN_BANK)), ("转", "付"))
        self.assertEqual(testdata.value(testdata.SIGN_BANK), "付")
        self.assertEqual(testdata.first_missing((testdata.SIGN_TRANSFER, testdata.SIGN_BANK)), "")
        testdata.use({"gl": {"sign_transfer": " 记 ", "sign_bank": "记"}})
        self.assertEqual((testdata.gl_sign(testdata.SIGN_TRANSFER), testdata.gl_sign(testdata.SIGN_BANK)), ("记", "记"))
        for bad in ("转'", "Z1", "转账凭证", "", 7):
            testdata.use({"gl": {"sign_transfer": bad}})
            with self.assertRaises(testdata.DataMissing):
                testdata.gl_sign(testdata.SIGN_TRANSFER)
            self.assertEqual(testdata.first_missing((testdata.SIGN_TRANSFER,)), testdata.SIGN_TRANSFER)
        with self.assertRaises(ValueError):
            testdata.gl_sign("gl.cash_flow_item")

    def test_missing_file_means_empty(self) -> None:
        self.assertEqual(testdata.load_file(Path("/nonexistent/testdata.json")), {})

    def test_case_without_data_is_skipped(self) -> None:
        testdata.use({})
        ctx = _Ctx()
        env = type("E", (), {"ctx": ctx, "bag": {"login": "1"}})()
        spec = CaseSpec("gl-x", "总账", (), (Step("core_gl"),), True, data=("gl.expense_account",))
        run_one(env, spec)  # type: ignore[arg-type]
        self.assertEqual(ctx.tests[0][1], "SKIP")
        self.assertIn("gl.expense_account", ctx.tests[0][2])

    def test_run_context_type(self) -> None:
        self.assertTrue(hasattr(Run, "add_test"))


if __name__ == "__main__":
    unittest.main()
