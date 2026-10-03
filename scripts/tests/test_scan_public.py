"""scripts/scan-public.py 的正反例。命中样本都在运行时拼出来，免得本文件被扫描器自己命中。"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scan-public.py"
_SPEC = importlib.util.spec_from_file_location("scan_public", SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
scan = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(scan)

DOT = "."
AT = "@"


def classes(text: str, path: str = "a.py") -> list[str]:
    return [hit.split(":")[2] for hit in scan.scan_text(path, text)]


class PositiveTest(unittest.TestCase):
    def test_private_ipv4(self) -> None:
        for ip in ("10" + ".1.2.3", "172" + ".16.0.1", "172" + ".31.255.254", "192" + ".168.21.2"):
            self.assertEqual(classes(f"host = '{ip}'"), ["rfc1918"], ip)

    def test_cgnat(self) -> None:
        for ip in ("100" + ".64.0.1", "100" + ".127.255.254"):
            self.assertEqual(classes(f"peer {ip}"), ["cgnat"], ip)

    def test_windows_hostname(self) -> None:
        self.assertEqual(classes("server " + "WIN" + "-AB12CD34"), ["hostname"])
        for name in ("DESK" + "TOP-AB12CD3", "LAP" + "TOP-X1Y2", "在WIN" + "-AB12上"):
            self.assertEqual(classes(name), ["hostname"], name)

    def test_mdns_hostname(self) -> None:
        self.assertEqual(classes("ssh alices-mbp" + ".local"), ["hostname"])

    def test_paths(self) -> None:
        samples = (
            "/ho" + "me/alice/x",
            "/Us" + "ers/alice/x",
            "/data/" + "tmp/x",
            "C:" + "\\Users\\alice\\x",
            "C:" + "\\\\Users\\\\alice",
            "cd /ho" + "me/alice",
            "/ro" + "ot",
            "/ro" + "ot/.ssh",
            "/data/" + "dev/u8co",
            "/data/" + "dev",
            "/data/" + "tmp",
        )
        for sample in samples:
            self.assertEqual(classes(sample), ["path"], sample)

    def test_email(self) -> None:
        self.assertEqual(classes("mail alice" + AT + "corp" + DOT + "cn"), ["email"])
        for mail in ("noreply" + AT + "corp" + DOT + "cn", "a.noreply.b" + AT + "corp" + DOT + "cn"):
            self.assertEqual(classes(mail), ["email"], mail)

    def test_mobile(self) -> None:
        self.assertEqual(classes("电话 139" + "12345678。"), ["mobile"])
        for phone in ("+86139" + "12345678", "86139" + "12345678", "+86 139" + "12345678", "139-" + "1234-5678",
                      "139 " + "1234 5678"):
            self.assertEqual(classes(f"电话 {phone}"), ["mobile"], phone)

    def test_ufdata_account(self) -> None:
        for acc in ("123", "456", "700"):
            self.assertEqual(classes("Initial Catalog=UF" + f"DATA_{acc}_2024"), ["ufdata"], acc)
        for name in ("uf" + "data_123_2024", "UF" + "DATA_123", "UF" + "Meta_123", "ufmeta_" + "123"):
            self.assertEqual(classes(f"db {name}"), ["ufdata"], name)

    def test_round_tags_in_all_files(self) -> None:
        self.assertEqual(classes("见 R" + "21 的说明", "docs/x.md"), ["round"])
        self.assertEqual(classes("第" + "二批起开放", "README.md"), ["round"])
        samples = ("R" + "21起不再只限", "在R" + "20里", "见 R" + "7.5", "R" + "9b 的结论", "第" + "五期", "第" + "十二期起")
        for path in ("docs/x.md", "a.py", "b.cs", "c.json"):
            for sample in samples:
                self.assertEqual(classes(sample, path), ["round"], (path, sample))
        self.assertEqual(classes('"""R' + '21 说明"""', "api/tests/t.py"), ["round"])

    def test_round_tags_in_file_names(self) -> None:
        for path in ("api/tests/test_r" + "21_leak.py", "api/tests/test_co_r" + "75_x.py", "a/b_r" + "9b.py",
                     "api/tests/test_u8co_r" + "20_gl.py", "x/R" + "12/a.md", "c/d-r" + "7.5-e.txt", "a/r" + "1_b.py",
                     "api/tests/test_co_errors_r" + "9.py", "api/tests/test_co_ph" + "ase75_glarc.py",
                     "api/u8co_api/co_gen_p" + "4.py", "docs/P" + "2/a.md", "x/Ph" + "ase2.md"):
            self.assertEqual(scan.name_hits(path)[0].split(":")[2], "roundname", path)
        hits = scan.name_hits("/tmp/x/test_r" + "21_a.py")
        self.assertEqual(len(hits), 1)

    def test_site_test_and_year_book_prose(self) -> None:
        for sample in ("在 80" + "1 实测通过", "80" + "3实测", "记在 2024 " + "年度库", "2025" + "年度库"):
            self.assertEqual(classes(sample, "a.cs"), ["prose"], sample)

    def test_demo_operator(self) -> None:
        demo = "de" + "mo"
        for sample in (f'"operator": "{demo}"', f'operator="{demo}"', f"--operator {demo}", f"--operator={demo}",
                       f"cMaker = {demo}", f"verifier: '{demo}'", f'OperatorCode = "{demo}";', f"操作员：{demo}"):
            self.assertEqual(classes(sample), ["operator"], sample)

    def test_demo_sql_literal_and_client_id(self) -> None:
        demo = "de" + "mo"
        for sample in (f"WHERE cMaker = N'{demo}'", f"values (n'{demo}', 1)", f'client_id = "{demo}"',
                       f'"client_id": "{demo}-web"', f"CLIENT_ID={demo}", f"clientId: '{demo}-cli'"):
            self.assertEqual(classes(sample), ["operator"], sample)

    def test_licence_tokens(self) -> None:
        samples = ("ListAll" + "SubSys", "Unlock" + "Info2(x)", "GetSubsys" + "Group", "RPC" + "Call", "getall" + "licenses",
                   "BF" + "Dispatch", "Credit" + "Identity", "返回 #EM" + "PTY#", "#ERR" + "OR#", "Dog" + "Cus", "Dog" + "ID=1",
                   "rpc" + "call")
        for sample in samples:
            self.assertEqual(classes(sample, "a.cs"), ["licence"], sample)

    def test_internal_ports(self) -> None:
        for port in ("1809" + "5", "1809" + "6", "1809" + "7"):
            self.assertEqual(classes(f"http://localhost:{port}/v1"), ["port"], port)
            self.assertEqual(classes(f"端口 {port}", "docs/x.md"), ["port"], port)

    def test_rollout_cadence_prose(self) -> None:
        self.assertEqual(classes("先灰度，隔天" + "再开放", "docs/x.md"), ["prose"])

    def test_private_case_names(self) -> None:
        for sample in ("用例 " + "purchase-in-red", "见用例`" + "sale-out-chain`", "流程（purchase-in" + "-red-chain）"):
            self.assertEqual(classes(sample, "docs/x.md"), ["casename"], sample)

    def test_prose_terms(self) -> None:
        samples = ("在参考" + "账套上", "生产" + "账套副本", "三个" + "账套都有", "U8" + "助手", "http://u8" + "assistant:8000",
                   "U8" + "Assistant", "https://auth.example.com/application" + "/o/token/")
        for sample in samples:
            self.assertEqual(classes(sample, "docs/x.md"), ["prose"], sample)
            self.assertEqual(classes(sample, "a.cs"), ["prose"], sample)

    def test_private_paths(self) -> None:
        samples = ("见 co/e" + "2e/README.md", "import co.e" + "2e.x", "co/pro" + "be/x.cs", "com" + "probe.exe",
                   "scripts/la" + "ne/a.sh", "scripts/acc" + "ount/b", "co/bridge/pri" + "vate/x.cs")
        for sample in samples:
            self.assertEqual(classes(sample, "a.yml"), ["private"], sample)

    def test_pem_and_token_files(self) -> None:
        self.assertEqual(classes("-----BEGIN " + "RSA PRIVATE KEY-----"), ["pem"])
        self.assertEqual(classes("see leak-" + "tokens.txt"), ["tokenfile"])
        self.assertEqual(classes("see oss-" + "replace.txt"), ["tokenfile"])

    def test_output_format_and_snippet_length(self) -> None:
        line = "x" * 200 + " 10" + ".0.0.1 " + "y" * 200
        hits = scan.scan_text("dir/f.txt", "ok\n" + line)
        self.assertEqual(len(hits), 1)
        path, lineno, name, snippet = hits[0].split(":", 3)
        self.assertEqual((path, lineno, name), ("dir/f.txt", "2", "rfc1918"))
        self.assertLessEqual(len(snippet.strip()), 80)
        self.assertIn("10" + ".0.0.1", snippet)


class NegativeTest(unittest.TestCase):
    def test_documentation_and_public_ips(self) -> None:
        for ip in ("192.0.2.10", "198.51.100.7", "203.0.113.5", "4.2.2.2", "172.32.0.1", "100.63.0.1", "100.128.0.1"):
            self.assertEqual(classes(f"ip {ip}"), [], ip)

    def test_versions_are_not_ips(self) -> None:
        self.assertEqual(classes("v1.10.2.3a and 999.168.1.1"), [])

    def test_allowed_emails(self) -> None:
        for mail in ("ops" + AT + "example.com", "a" + AT + "u8co.example.org", "x" + AT + "users.noreply.github.com"):
            self.assertEqual(classes(mail), [], mail)

    def test_placeholder_mobile_and_hashes(self) -> None:
        self.assertEqual(classes("手机 13800000000"), [])
        self.assertEqual(classes("手机 +86 13800000000"), [])
        self.assertEqual(classes("sha256:ab13912345678cd 2139123456789"), [])

    def test_allowed_accounts(self) -> None:
        for acc in ("801", "802", "803", "998", "999"):
            self.assertEqual(classes(f"UFDATA_{acc}_2024"), [], acc)
            self.assertEqual(classes(f"ufmeta_{acc}"), [], acc)

    def test_similar_paths_and_hosts(self) -> None:
        for text in ("/da" + "ta/devices", "/srv/" + "root/x", "settings.local.json", "threading.local()", "x.Local = 1"):
            self.assertEqual(classes(text), [], text)

    def test_single_digit_and_numeric_periods(self) -> None:
        for path in ("docs/x.md", "a.py", "b.cs"):
            self.assertEqual(classes("R0 到 R9 是单据类型", path), [], path)
            self.assertEqual(classes("第 1 期、第 0 期、第12期的余额", path), [], path)
            self.assertEqual(classes("AR20 PR21 R2 x_R21 R210", path), [], path)

    def test_plain_file_names(self) -> None:
        for path in ("api/tests/test_co_gl_transfer.py", "co/bridge/src/R2Writer.cs", "a/pr21.py",
                     "a/r210.py", "docs/u8-notes.md", "a/rr20_b.py", "a/p12.py", "docs/phases.md", "a/rp1x.py"):
            self.assertEqual(scan.name_hits(path), [], path)

    def test_generic_terms(self) -> None:
        for text in ("正式账套", "测试账套", "示例账套", "co/client/x.py", "co/bridge", "scripts/docs/a.sh",
                     "https://auth.example.com/oauth/token", "internal error"):
            self.assertEqual(classes(text, "docs/x.md"), [], text)

    def test_near_misses_of_new_rules(self) -> None:
        demo = "de" + "mo"
        samples = ("账套 801 示例", "实测结果", "年度库按年拆分", '"operator": "op001"', f"{demo}_mode = True",
                   f"# U8 自带 {demo} 操作员", f"operator={demo}x", "用例覆盖红字", "（a-b）说明",
                   "foo(purchase-in-red-chain)", "用例 Purchase", "读写分级（x-u8co-access）")
        for sample in samples:
            self.assertEqual(classes(sample, "docs/x.md"), [], sample)

    def test_near_misses_of_tokenless_rules(self) -> None:
        demo = "de" + "mo"
        samples = (f"N'{demo}x'", f"AN'{demo}'", f'client_id = "{demo}x"', f'client_id = "acme-{demo}"',
                   f'client_name = "{demo}"', "port 18094 18098 180950 1809", "a18095", "Dog house", "HotDogID",
                   "#EMPTY", "ERROR", "RPC call", "隔天再说", "co/bridge/src/x.cs", "co/bridge/privacy.md")
        for sample in samples:
            self.assertEqual(classes(sample, "docs/x.md"), [], sample)

    def test_allow_mark_skips_line(self) -> None:
        text = "host 10" + ".0.0.1  # scan-public: allow"
        self.assertEqual(scan.scan_text("a.py", text), [])


    def test_binary_file_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.bin"
            path.write_bytes(b"\x00\x01" + ("10" + ".0.0.1").encode())
            self.assertIsNone(scan.read_text(path))


class FileTest(unittest.TestCase):
    def run_main(self, argv: list[str], cwd: Path) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = scan.main(argv, cwd)
        return code, out.getvalue()

    def test_non_utf8_text_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.ps1"
            path.write_bytes("# 说明".encode("gb18030"))
            code, out = self.run_main([str(path)], Path(tmp))
        self.assertEqual(code, 1)
        self.assertIn(":0:encoding:", out)

    def test_utf16_with_bom_is_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.sql"
            path.write_bytes(("-- host 10" + ".0.0.1\n").encode("utf-16"))
            self.assertIn(":1:rfc1918:", self.run_main([str(path)], Path(tmp))[1])

    def test_utf16_without_bom_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.sql"
            path.write_bytes("select 1".encode("utf-16-le"))
            self.assertIn(":0:encoding:", self.run_main([str(path)], Path(tmp))[1])

    def test_paths_resolve_against_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "sub"
            sub.mkdir()
            (sub / "x.py").write_text("host = '10" + ".0.0.1'\n", encoding="utf-8")
            code, out = self.run_main(["x.py"], sub)
        self.assertEqual(code, 1)
        self.assertIn(":1:rfc1918:", out)

    def test_missing_explicit_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self.run_main(["missing.py"], Path(tmp))
        self.assertEqual(code, 1)
        self.assertIn(":0:unreadable:", out)

    def test_no_directory_is_skipped(self) -> None:
        self.assertFalse(hasattr(scan, "EXCLUDE_DIRS"))
        self.assertFalse(hasattr(scan, "excluded"))
        dirs = ("co/" + "e2e", "co/" + "probe", "scripts/" + "lane", "scripts/" + "account", "internal")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in dirs:
                (root / name).mkdir(parents=True)
                (root / name / "x.py").write_text("host = '10" + ".0.0.1'\n", encoding="utf-8")
            argv = [f"{name}/x.py" for name in dirs]
            code, out = self.run_main(argv, root)
        self.assertEqual(code, 1)
        for name in dirs:
            self.assertIn(f"{name}/x.py:1:rfc1918:", out, name)

    def test_binary_file_name_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ("logo_r" + "21.png")
            path.write_bytes(b"\x00\x01\x02")
            code, out = self.run_main([str(path)], Path(tmp))
        self.assertEqual(code, 1)
        self.assertIn(":0:roundname:", out)

    def test_repo_relative_output_from_subdirectory(self) -> None:
        code, out = self.run_main(["scan-public.py"], scan.ROOT / "scripts")
        self.assertEqual((code, out), (0, ""))
        self.assertEqual(scan._targets(["scan-public.py"], scan.ROOT / "scripts")[0][0], "scripts/scan-public.py")


if __name__ == "__main__":
    unittest.main()
