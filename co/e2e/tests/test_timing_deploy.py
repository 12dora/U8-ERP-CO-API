"""耗时记录、跳过编译部署的判定、源码哈希、空表指纹。全部离线，不连服务器。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from co.e2e import bridge_job, timing
from co.e2e.casespec import runner
from co.e2e.casespec.model import CaseSpec
from co.e2e.context import Run
from co.e2e.deployskip import PROBE_FAILED_NOTE, SKIP_NOTE, parse_probe, why_deploy
from co.e2e.fingerprint import diff_fingerprints, normalize
from co.e2e.kitpack import kit_files, source_hash
from co.e2e.remote import RemoteResult
from co.e2e.report import assemble, render_markdown
from co.e2e.tests import support

_REMOTE = Path(__file__).resolve().parents[1] / "remote"
_REPO = Path(__file__).resolve().parents[3]
_HASH = "a" * 64


class _Ctx:
    def __init__(self) -> None:
        self.tests: list[tuple] = []

    def add_test(self, name: str, status: str, detail: str, extra: dict | None = None) -> None:
        self.tests.append((name, status, detail, extra))


def _clock(*values: float):
    return mock.patch.object(timing, "monotonic", side_effect=list(values))


class RunnerTimingTests(unittest.TestCase):
    def test_run_one_records_duration_with_fake_clock(self) -> None:
        ctx = _Ctx()
        env = type("E", (), {"ctx": ctx, "bag": {}})()
        spec = CaseSpec("sale-out-partial", "销售出库部分")
        with _clock(10.0, 12.5), mock.patch.object(runner, "judge_one", return_value=("PASS", "ok")):
            runner.run_one(env, spec)  # type: ignore[arg-type]
        self.assertEqual(ctx.tests, [("销售出库部分", "PASS", "ok", {"case": "sale-out-partial", "duration_ms": 2500})])

    def test_emit_uses_recorded_durations(self) -> None:
        ctx = _Ctx()
        env = type("E", (), {"ctx": ctx})()
        specs = [CaseSpec("a-1", "甲"), CaseSpec("b-2", "乙")]
        runner._emit(env, specs, {"a-1": ("PASS", "甲"), "b-2": ("SKIP", "缺")}, {"a-1": 40})  # type: ignore[arg-type]
        self.assertEqual(ctx.tests[0][3], {"case": "a-1", "duration_ms": 40})
        self.assertEqual(ctx.tests[1][3], {"case": "b-2", "duration_ms": None})

    def test_phase_records_even_on_error(self) -> None:
        holder = type("H", (), {"phases": {}})()
        with _clock(1.0, 1.25), self.assertRaises(RuntimeError):
            with timing.phase(holder, "deploy"):
                raise RuntimeError("x")
        self.assertEqual(holder.phases["deploy"]["ms"], 250)
        self.assertIn("start", holder.phases["deploy"])

    def test_run_context_keeps_case_and_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = Run(False, Path(tmp), None)  # type: ignore[arg-type]
            ctx.add_test("甲", "PASS", "ok", {"case": "a-1", "duration_ms": 7})
            ctx.add_test("乙", "PASS", "ok")
            ctx.add_step("deploy-skip", True, SKIP_NOTE)
        self.assertEqual((ctx.tests[0]["case"], ctx.tests[0]["duration_ms"]), ("a-1", 7))
        self.assertIsNone(ctx.tests[1]["duration_ms"])
        self.assertEqual(ctx.steps[0]["note"], SKIP_NOTE)
        self.assertTrue(ctx.steps[0]["at"].endswith("Z"))


def _tests() -> list[dict]:
    rows = []
    for index in range(20):
        case = ("sale-" if index % 2 else "gl-") + str(index)
        rows.append({"name": f"用例{index}", "status": "PASS", "detail": "ok", "case": case, "duration_ms": 1000 * index})
    rows.append({"name": "旧", "status": "PASS", "detail": "ok"})
    return rows


class ReportTimingTests(unittest.TestCase):
    def setUp(self) -> None:
        support.install()

    def tearDown(self) -> None:
        support.reset()

    def test_markdown_has_column_and_section(self) -> None:
        phases = {"prep": {"ms": 60000}, "deploy": {"ms": 5000}, "cases": {"ms": 190000}, "revert": {"ms": 30000}}
        text = render_markdown({"tests": _tests(), "phases": phases, "steps": [{"name": "deploy-skip", "note": SKIP_NOTE}]})
        self.assertIn("| 用例 | 结果 | 说明 | 耗时 |", text)
        self.assertIn("| 用例19 | PASS | ok | 19.0 秒 |", text)
        self.assertIn("| 旧 | PASS | ok |  |", text)
        self.assertIn("## 耗时", text)
        self.assertIn("总计（各阶段相加）：285.0 秒", text)
        self.assertIn("编译部署 5.0 秒", text)
        self.assertIn("用例合计：20 条，190.0 秒", text)
        self.assertIn("### 最慢的 15 条用例", text)
        self.assertIn("| 用例19 | sale-19 | 19.0 秒 |", text)
        self.assertNotIn("| 用例4 | gl-4 |", text)
        self.assertIn("| sale | 10 | 100.0 秒 |", text)
        self.assertIn("| gl | 10 | 90.0 秒 |", text)
        self.assertIn(SKIP_NOTE, text)

    def test_no_section_without_timings(self) -> None:
        text = render_markdown({"tests": [{"name": "健康检查", "status": "PASS", "detail": "ok"}]})
        self.assertNotIn("## 耗时", text)

    def test_json_carries_phases_and_durations(self) -> None:
        report = assemble({"dry_run": True, "tests": _tests(), "phases": {"prep": {"ms": 3}}})
        self.assertEqual(report["phases"], {"prep": {"ms": 3}})
        self.assertEqual(report["tests"][3]["duration_ms"], 3000)

    def test_family_is_prefix_before_dash(self) -> None:
        self.assertEqual(timing.family("report-gl-balance"), "report")
        self.assertEqual(timing.family("health"), "health")
        self.assertEqual(timing.family(""), "（未知）")


def _tree(root: Path, newline: str, extra: str = "", sql: str = "") -> None:
    src = root / "co" / "bridge" / "src"
    src.mkdir(parents=True)
    if sql:
        ia = root / "co" / "bridge" / "sql" / "ia"
        ia.mkdir(parents=True)
        (ia / "post.sql").write_text(sql, encoding="utf-8", newline="")
        (ia / "notes.txt").write_text("x", encoding="utf-8", newline="")
    (src / "B.cs").write_text(f"class B {{{newline}}}{newline}{extra}", encoding="utf-8", newline="")
    (src / "A.cs").write_text(f"class A {{{newline}}}{newline}", encoding="utf-8", newline="")
    (root / "co" / "bridge" / "build.ps1").write_text(f"# build{newline}", encoding="utf-8", newline="")
    (root / "co" / "bridge" / "u8co-bridge.exe.config").write_text(f"<configuration/>{newline}", encoding="utf-8", newline="")
    (root / "co" / "SafePath.ps1").write_text(f"# safe{newline}", encoding="utf-8", newline="")


class SourceHashTests(unittest.TestCase):
    def _hash(self, newline: str, extra: str = "", sql: str = "") -> str:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, newline, extra, sql)
            return source_hash(root)

    def test_sql_scripts_in_kit_and_hash(self) -> None:
        # 存货核算脚本嵌进 exe：只改脚本也要重新部署，所以脚本在套件里、也进哈希；sql 下别的文件不进。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, "\n", sql="SELECT 1;\n")
            names = [name for _path, name in kit_files(root)]
        self.assertIn("bridge/sql/ia/post.sql", names)
        self.assertNotIn("bridge/sql/ia/notes.txt", names)
        self.assertEqual(names, sorted(names))
        self.assertNotEqual(self._hash("\n"), self._hash("\n", sql="SELECT 1;\n"))
        self.assertNotEqual(self._hash("\n", sql="SELECT 1;\n"), self._hash("\n", sql="SELECT 2;\n"))

    def test_sql_depth_rule(self) -> None:
        # 只认 sql/<模块>/*.sql：与 build.ps1、kit-extract.ps1（只清理一层模块目录）一致，别的深度会留下旧脚本。
        for rel in ("sql/top.sql", "sql/ia/deep/x.sql", "sql/ia/UPPER.SQL"):
            with self.subTest(rel=rel), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _tree(root, "\n", sql="SELECT 1;\n")
                bad = root / "co" / "bridge" / rel
                bad.parent.mkdir(parents=True, exist_ok=True)
                bad.write_text("SELECT 1;", encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    kit_files(root)

    def test_repo_kit_has_every_sql_script(self) -> None:
        names = {name for _path, name in kit_files(_REPO)}
        for script in ("post_tables", "post", "unpost", "period_end", "period_end_cancel", "close", "unclose", "qc_keep", "qc_recover"):
            with self.subTest(script=script):
                self.assertIn("bridge/sql/ia/" + script + ".sql", names)

    def test_repo_sql_scripts_have_no_go_line(self) -> None:
        # 桥把每个脚本整批交给服务器，GO 只有 sqlcmd 认得。
        for path, name in kit_files(_REPO):
            if not name.endswith(".sql"):
                continue
            lines = [line.strip().upper() for line in path.read_text(encoding="utf-8-sig").splitlines()]
            with self.subTest(name=name):
                self.assertNotIn("GO", lines)

    def test_hash_is_deterministic_over_raw_bytes(self) -> None:
        lf = self._hash("\n")
        self.assertRegex(lf, r"^[0-9a-f]{64}$")
        self.assertEqual(lf, self._hash("\n"))
        # 原样字节：换行不同就是不同的源码。
        self.assertNotEqual(lf, self._hash("\r\n"))

    def test_content_change_changes_hash(self) -> None:
        self.assertNotEqual(self._hash("\n"), self._hash("\n", "// x"))

    def test_repo_hash_is_stable(self) -> None:
        self.assertEqual(source_hash(), source_hash())


class DecisionTests(unittest.TestCase):
    def test_parse_probe(self) -> None:
        text = f"noise\nbinpath ok\nsrc_sha256 {_HASH}\nconfig ok\nhealth ok\nbinpath bad\n"
        self.assertEqual(parse_probe(text), {"binpath": "ok", "src_sha256": _HASH, "config": "ok", "health": "ok"})
        self.assertEqual(parse_probe(""), {})

    def test_skip_only_when_everything_matches(self) -> None:
        good = {"binpath": "ok", "src_sha256": _HASH, "config": "ok", "health": "ok"}
        self.assertEqual(why_deploy(_HASH, good, False), "")
        self.assertTrue(why_deploy(_HASH, good, True))
        self.assertTrue(why_deploy("", good, False))
        self.assertTrue(why_deploy(_HASH, {}, False))
        self.assertTrue(why_deploy(_HASH, {**good, "src_sha256": "none"}, False))
        self.assertTrue(why_deploy(_HASH, {**good, "src_sha256": "b" * 64}, False))
        self.assertTrue(why_deploy(_HASH, {**good, "binpath": "bad"}, False))
        self.assertTrue(why_deploy(_HASH, {**good, "health": "skipped"}, False))
        # 已安装的 exe 对已安装配置的 --check-config 没过（或没跑）时不跳过。
        self.assertTrue(why_deploy(_HASH, {**good, "config": "bad"}, False))
        self.assertTrue(why_deploy(_HASH, {k: v for k, v in good.items() if k != "config"}, False))


class _Remote:
    def __init__(self, probe: RemoteResult) -> None:
        self.probe = probe
        self.calls: list[tuple[str, dict | None]] = []

    def run_script(self, name: str, named: dict | None, timeout: int) -> RemoteResult:
        self.calls.append((name, named))
        if name == "bridge-hash.ps1":
            return self.probe
        return RemoteResult(0, name + " ok")

    def run_fixed(self, command: str, timeout: int) -> RemoteResult:
        self.calls.append((command, None))
        return RemoteResult(0, command + " ok")

    def upload(self, local: Path, remote: str) -> None:
        self.calls.append(("upload", None))


class DeployServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        support.install()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()
        support.reset()

    def _deploy(self, probe: RemoteResult, force: bool = False, local: str = _HASH) -> tuple[bool, Run, list[str]]:
        remote = _Remote(probe)
        ctx = Run(False, Path(self.tmp.name), remote)  # type: ignore[arg-type]
        ctx.force_deploy = force
        with (
            mock.patch.object(bridge_job, "local_hash", return_value=local),
            mock.patch.object(bridge_job, "pack_bridge"),
            mock.patch.object(bridge_job, "load_service_secret"),
            mock.patch.object(bridge_job, "wait_until_healthy"),
        ):
            skipped = bridge_job.deploy_service(ctx)
        self.calls = remote.calls
        return skipped, ctx, [name for name, _ in remote.calls]

    def test_matching_hash_skips_build_and_redeploy(self) -> None:
        skipped, ctx, names = self._deploy(RemoteResult(0, f"binpath ok\nsrc_sha256 {_HASH}\nconfig ok\nhealth ok\n"))
        self.assertTrue(skipped)
        self.assertEqual(names, ["bridge-hash.ps1"])
        self.assertEqual(ctx.steps[-1]["note"], SKIP_NOTE)

    def test_missing_hash_deploys_and_passes_hash(self) -> None:
        skipped, _ctx, names = self._deploy(RemoteResult(0, "binpath ok\nsrc_sha256 none\nhealth skipped\n"))
        self.assertFalse(skipped)
        self.assertEqual(names, ["bridge-hash.ps1", "upload", "kit-extract.ps1", "build", "selftest", "check", "service-redeploy.ps1"])
        self.assertIn(("service-redeploy.ps1", {"SrcHash": _HASH}), self.calls)

    def test_probe_failure_fails_open(self) -> None:
        skipped, ctx, names = self._deploy(RemoteResult(1, f"binpath ok\nsrc_sha256 {_HASH}\nconfig ok\nhealth ok\n"))
        self.assertFalse(skipped)
        self.assertIn("service-redeploy.ps1", names)
        probe = [step for step in ctx.steps if step["name"] == "bridge-hash"][0]
        self.assertEqual((probe["ok"], probe["note"]), (False, PROBE_FAILED_NOTE))

    def test_no_local_hash_does_not_probe(self) -> None:
        skipped, _ctx, names = self._deploy(RemoteResult(0, f"binpath ok\nsrc_sha256 {_HASH}\nconfig ok\nhealth ok\n"), local="")
        self.assertFalse(skipped)
        self.assertNotIn("bridge-hash.ps1", names)
        self.assertIn("service-redeploy.ps1", names)

    def test_force_deploy_does_not_probe(self) -> None:
        skipped, _ctx, names = self._deploy(RemoteResult(0, f"binpath ok\nsrc_sha256 {_HASH}\nconfig ok\nhealth ok\n"), force=True)
        self.assertFalse(skipped)
        self.assertNotIn("bridge-hash.ps1", names)
        self.assertIn("service-redeploy.ps1", names)


class RemoteScriptContracts(unittest.TestCase):
    def test_redeploy_writes_hash_only_after_health(self) -> None:
        text = (_REMOTE / "service-redeploy.ps1").read_text(encoding="utf-8")
        body = text[text.index("function Update-InstalledBridge") :]
        self.assertLess(body.index("Remove-SrcHash"), body.index("Copy-KitExe"))
        self.assertLess(body.index("Test-ServiceHealth"), body.index("Write-SrcHash"))
        self.assertIn("bridgelib.ps1", text)
        self.assertNotIn("secret.hex", text)

    def test_probe_is_read_only(self) -> None:
        text = (_REMOTE / "bridge-hash.ps1").read_text(encoding="utf-8")
        for word in ("Copy(", "Delete(", "WriteAll", "Stop-Service", "Start-Service", "service-stop", "service-start"):
            self.assertNotIn(word, text)
        # 跳过部署时用已安装的 exe 核对已安装的配置，结果在输出里。
        self.assertIn("--check-config --root $root", text)
        self.assertIn('Write-Host ("config " + $config)', text)

    def test_fingerprint_skips_checksum_for_empty_tables(self) -> None:
        text = (_REMOTE / "fingerlib.ps1").read_text(encoding="utf-8")
        self.assertIn("EXISTS (SELECT 1 FROM", text)
        self.assertLess(text.index("IF @has = 0"), text.index("CHECKSUM_AGG(BINARY_CHECKSUM(*))"))


def _doc(rows: int | None, checksum: int | None) -> dict:
    table = {"schema": "dbo", "name": "T", "rows": rows, "checksum": checksum, "error": ""}
    return {"databases": {name: {"tables": [dict(table)]} for name in support.make_config().databases}}


class EmptyTableFingerprintTests(unittest.TestCase):
    def setUp(self) -> None:
        support.install()

    def tearDown(self) -> None:
        support.reset()

    def test_empty_tables_compare_equal(self) -> None:
        self.assertEqual(diff_fingerprints(_doc(0, None), _doc(0, None)), [])
        self.assertEqual(normalize(_doc(0, None))["databases"][support.make_config().data_db]["totals"]["checksum_xor"], 0)

    def test_rows_appearing_in_empty_table_are_reported(self) -> None:
        self.assertTrue(diff_fingerprints(_doc(0, None), _doc(1, 12345)))


if __name__ == "__main__":
    unittest.main()
