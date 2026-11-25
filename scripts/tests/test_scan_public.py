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
            "C:" + "\\Users\\alice\\x",
            "C:" + "\\\\Users\\\\alice",
            "cd /ho" + "me/alice",
            "/ro" + "ot",
            "/ro" + "ot/.ssh",
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

    def test_pem(self) -> None:
        self.assertEqual(classes("-----BEGIN " + "RSA PRIVATE KEY-----"), ["pem"])

    def test_define_caption_hits(self) -> None:
        samples = ("cDefine" + "2（某某号）", "cbdefine" + "5 (型号)", "`cbdefine" + "1`（某某数量）", "cdefine" + "1 (order number)")
        for sample in samples:
            self.assertEqual(classes(sample), ["define_caption"], sample)

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
        for ip in ("192.0.2.10", "198.51.100.7", "203.0.113.5", "4.2.2.2", "172.32.0.1"):
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
        for text in ("/srv/" + "root/x", "settings.local.json", "threading.local()", "x.Local = 1"):
            self.assertEqual(classes(text), [], text)

    def test_define_caption_allowed(self) -> None:
        samples = (
            "cdefine" + "1（表头自定义项 1）",
            "cdefine" + "28（表体）",
            "chdefine" + "3 (DispatchList_extradefine)",
            "cdefine" + "9（示例标题）",
            "cDefine" + "1",
            "cdefine" + "4（日期型）",
            "`cdefine" + "1`…`16` (cdefine_1, SO_SOMain)",
            "chdefine" + "1 (1–99)",
        )
        for sample in samples:
            self.assertEqual(classes(sample), [], sample)

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
        dirs = ("vendor/lib", "tmp/cache", "docs/samples")
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

    def test_repo_relative_output_from_subdirectory(self) -> None:
        code, out = self.run_main(["scan-public.py"], scan.ROOT / "scripts")
        self.assertEqual((code, out), (0, ""))
        self.assertEqual(scan._targets(["scan-public.py"], scan.ROOT / "scripts")[0][0], "scripts/scan-public.py")


if __name__ == "__main__":
    unittest.main()
