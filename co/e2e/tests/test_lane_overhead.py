"""每条线的固定开销：指纹两库并行、脚本一次上传、pick-docs 一次连接。不连接服务器。"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from co.e2e import settings
from co.e2e.bridge_job import upload_scripts
from co.e2e.context import Run
from co.e2e.flow import do_fingerprint
from co.e2e.remote import RemoteError, RemoteResult
from co.e2e.tests import support

_REMOTE = Path(__file__).resolve().parents[1] / "remote"


class FpRemote:
    """两库的 fingerprint.ps1 都进来之后才放行：串行跑会卡在第一库，测试超时失败。"""

    def __init__(self, codes: dict[str, int] | None = None) -> None:
        self.codes = codes or {}
        self.barrier = threading.Barrier(2, timeout=5)
        self.downloads: list[str] = []
        self.uploads: list[tuple[list[str], str]] = []
        self.fail_upload = False

    def run_script(self, name: str, named: dict | None, timeout: int) -> RemoteResult:
        del timeout
        assert name == "fingerprint.ps1" and named is not None
        self.barrier.wait()
        database = named["Database"]
        return RemoteResult(self.codes.get(database, 0), "fingerprint " + database)

    def download(self, remote_win: str, local: Path) -> None:
        self.downloads.append(remote_win)
        database = remote_win.rsplit("\\fp-", 1)[1][: -len(".json")]
        row = {"schema": "dbo", "name": "T", "rows": 1, "checksum": 1, "error": ""}
        local.write_text(json.dumps({"database": database, "tables": [row]}), encoding="utf-8")

    def upload_many(self, paths: list[Path], remote_dir: str) -> None:
        if self.fail_upload:
            raise RemoteError("上传失败", "scp: denied")
        self.uploads.append(([item.name for item in paths], remote_dir))


class LaneOverheadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        support.install()
        self.remote = FpRemote()
        self.ctx = Run(False, Path(self.folder.name), self.remote)  # type: ignore[arg-type]

    def tearDown(self) -> None:
        support.reset()
        self.folder.cleanup()

    def test_fingerprints_run_concurrently_with_one_step_per_database(self) -> None:
        do_fingerprint(self.ctx, "before")
        names = [step["name"] for step in self.ctx.steps]
        want = ["fingerprint-before-" + database for database in settings.conf().databases]
        self.assertEqual(names, want)
        for step in self.ctx.steps:
            self.assertTrue(step["ok"])
            self.assertIsInstance(step["ms"], int)
        self.assertIsNotNone(self.ctx.before)
        self.assertEqual(len(self.remote.downloads), 2)

    def test_fingerprint_failure_stops_at_first_failed_database(self) -> None:
        databases = settings.conf().databases
        self.remote.codes = {databases[1]: 1}
        with self.assertRaises(RuntimeError) as caught:
            do_fingerprint(self.ctx, "snapshot")
        self.assertIn("fingerprint-snapshot-" + databases[1], str(caught.exception))
        self.assertEqual([step["ok"] for step in self.ctx.steps], [True, False])
        self.assertIsNone(self.ctx.reference)

    def test_scripts_upload_in_one_call_with_config_first(self) -> None:
        upload_scripts(self.ctx)
        self.assertEqual(len(self.remote.uploads), 1)
        names, target = self.remote.uploads[0]
        self.assertEqual(target, settings.conf().remote_root)
        self.assertEqual(names[0], "e2e.json")
        self.assertEqual(sorted(names[1:]), sorted(path.name for path in _REMOTE.glob("*.ps1")))
        self.assertEqual([(step["name"], step["ok"]) for step in self.ctx.steps], [("upload-scripts", True)])

    def test_script_upload_failure_is_a_failed_step(self) -> None:
        self.remote.fail_upload = True
        with self.assertRaises(RemoteError):
            upload_scripts(self.ctx)
        self.assertEqual([(step["name"], step["ok"]) for step in self.ctx.steps], [("upload-scripts", False)])

    def test_pick_docs_uses_one_sqlcmd_batch(self) -> None:
        text = (_REMOTE / "pick-docs.ps1").read_text(encoding="utf-8-sig")
        self.assertEqual(text.count("Get-SqlText"), 1)
        self.assertEqual(text.count("SET NOCOUNT ON"), 1)
        for key in ("sale", "dispatch", "verified", "downstream"):
            self.assertIn("N'" + key + "=' + CONVERT(varchar(20)", text)
        self.assertNotIn("Read-FirstId", text)


if __name__ == "__main__":
    unittest.main()
