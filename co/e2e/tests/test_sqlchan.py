"""SQL 工人协议、门禁缓存和回退。不连接服务器。"""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from collections.abc import Callable
from pathlib import Path

from co.e2e.kitpack import pack_bridge
from co.e2e.mux import mux_options, prepare_control_path
from co.e2e.remote import RemoteResult
from co.e2e.sqlchan import (
    GATE_SECONDS,
    SqlChannel,
    WorkerDown,
    encode_request,
    gate_due,
    parse_response,
    server_should_check,
)
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()

_REMOTE = Path(__file__).resolve().parents[1] / "remote"
_SAFE = "SELECT TOP 1 ID FROM SO_SOMain"


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class MemoryTransport:
    def __init__(self, replies: list[str], fail: bool = False, up: bool = True) -> None:
        self.replies = list(replies)
        self.sent: list[str] = []
        self.fail = fail
        self.up = up
        self.closed = False

    def alive(self) -> bool:
        return self.up and not self.closed

    def send(self, line: str) -> str:
        if self.fail:
            raise WorkerDown("dead")
        self.sent.append(line)
        if not self.replies:
            raise WorkerDown("eof")
        return self.replies.pop(0)

    def close(self) -> None:
        self.closed = True
        self.up = False


def _unused() -> RemoteResult:
    raise AssertionError("不应回退")


def _remember(seen: list[str]) -> Callable[[], RemoteResult]:
    def run() -> RemoteResult:
        seen.append("legacy")
        return RemoteResult(0, "ROW:legacy")

    return run


def _ok(text: str) -> str:
    return json.dumps({"ok": True, "code": 0, "out": text}, ensure_ascii=True)


class ProtocolTests(unittest.TestCase):
    def test_request_stays_one_line(self) -> None:
        line = encode_request("UFMeta_801", "SELECT N'张'\nSELECT 2", False)
        self.assertNotIn("\n", line)
        body = json.loads(line)
        self.assertEqual(body["db"], "UFMeta_801")
        self.assertEqual(body["sql"], "SELECT N'张'\nSELECT 2")
        self.assertIs(body["recheck"], False)

    def test_response_keeps_row_text(self) -> None:
        reply = parse_response('{"ok":true,"code":0,"out":"ROW:\\u5f20\\n"}')
        self.assertTrue(reply.ok)
        self.assertEqual(reply.code, 0)
        self.assertEqual(reply.out, "ROW:张\n")

    def test_parse_rejects_bad_shape(self) -> None:
        with self.assertRaises(ValueError):
            parse_response("[]")
        with self.assertRaises(ValueError):
            parse_response('{"ok":true,"code":true,"out":"x"}')
        with self.assertRaises(ValueError):
            parse_response('{"ok":true,"code":0}')


class GuardTests(unittest.TestCase):
    def test_blocked_sql_is_not_sent(self) -> None:
        transport = MemoryTransport([])
        query = "SELECT * FROM " + support.foreign_data_db() + ".dbo.SO_SOMain"
        channel = SqlChannel(transport, lambda: 0.0)
        with self.assertRaises(RuntimeError) as caught:
            channel.execute("UFDATA_801_2024", query, _unused)
        self.assertEqual(str(caught.exception), sql_block_reason(query))
        self.assertEqual(transport.sent, [])

    def test_same_guard_on_later_requests(self) -> None:
        transport = MemoryTransport([_ok("ROW:1"), _ok("ROW:2")])
        channel = SqlChannel(transport, lambda: 0.0)
        channel.execute("UFDATA_801_2024", "SELECT 1", _unused)
        with self.assertRaises(RuntimeError):
            channel.execute("UFDATA_801_2024", "EXEC sp_help", _unused)
        channel.execute("UFDATA_801_2024", "SELECT 2", _unused)
        sent = [json.loads(item)["sql"] for item in transport.sent]
        self.assertEqual(sent, ["SELECT 1", "SELECT 2"])

    def test_other_database_is_refused(self) -> None:
        transport = MemoryTransport([])
        channel = SqlChannel(transport, lambda: 0.0)
        with self.assertRaises(RuntimeError) as caught:
            channel.execute("master", _SAFE, _unused)
        self.assertIn("名单以外", str(caught.exception))
        self.assertEqual(transport.sent, [])

    def test_dead_channel_still_blocks(self) -> None:
        transport = MemoryTransport([], up=False)
        channel = SqlChannel(transport, lambda: 0.0)
        with self.assertRaises(RuntimeError):
            channel.execute("UFDATA_801_2024", "SHUTDOWN", _unused)
        self.assertEqual(transport.sent, [])
        self.assertFalse(transport.closed)


class GateTests(unittest.TestCase):
    def test_cache_expires_at_30_seconds(self) -> None:
        self.assertEqual(GATE_SECONDS, 30.0)
        self.assertTrue(gate_due(None, 0.0))
        self.assertFalse(gate_due(1000.0, 1029.9))
        self.assertTrue(gate_due(1000.0, 1030.0))
        self.assertFalse(server_should_check(29.9, False))
        self.assertTrue(server_should_check(30.0, False))
        self.assertTrue(server_should_check(0.0, True))
        self.assertTrue(server_should_check(None, False))

    def test_channel_rechecks_after_window(self) -> None:
        clock = Clock(1000.0)
        transport = MemoryTransport([_ok("ROW:1"), _ok("ROW:2"), _ok("ROW:3")])
        channel = SqlChannel(transport, clock)
        channel.execute("UFDATA_801_2024", "SELECT 1", _unused)
        clock.now = 1029.0
        channel.execute("UFDATA_801_2024", "SELECT 2", _unused)
        clock.now = 1030.0
        channel.execute("UFDATA_801_2024", "SELECT 3", _unused)
        flags = [json.loads(item)["recheck"] for item in transport.sent]
        self.assertEqual(flags, [True, False, True])


class FallbackTests(unittest.TestCase):
    def test_dead_worker_uses_legacy_and_stays_there(self) -> None:
        transport = MemoryTransport([], fail=True)
        seen: list[str] = []
        channel = SqlChannel(transport, lambda: 0.0)
        first = channel.execute("UFDATA_801_2024", "SELECT 1", _remember(seen))
        second = channel.execute("UFDATA_801_2024", "SELECT 2", _remember(seen))
        self.assertEqual(first.out, "ROW:legacy")
        self.assertEqual(second.out, "ROW:legacy")
        self.assertEqual(seen, ["legacy", "legacy"])
        self.assertEqual(transport.sent, [])
        self.assertTrue(channel.dead)

    def test_offline_transport_skips_send(self) -> None:
        transport = MemoryTransport([_ok("ROW:no")], up=False)
        seen: list[str] = []
        channel = SqlChannel(transport, lambda: 0.0)
        result = channel.execute("UFDATA_801_2024", "SELECT 1", _remember(seen))
        self.assertEqual(result.out, "ROW:legacy")
        self.assertEqual(transport.sent, [])
        self.assertEqual(seen, ["legacy"])

    def test_bad_frame_falls_back(self) -> None:
        transport = MemoryTransport(["not-json"])
        seen: list[str] = []
        channel = SqlChannel(transport, lambda: 0.0)
        result = channel.execute("UFDATA_801_2024", "SELECT 1", _remember(seen))
        self.assertEqual(result.out, "ROW:legacy")
        self.assertEqual(len(transport.sent), 1)
        self.assertTrue(transport.closed)
        self.assertTrue(channel.dead)

    def test_sql_error_keeps_the_worker(self) -> None:
        transport = MemoryTransport(['{"ok":false,"code":1,"out":"sqlcmd 退出码 1"}'])
        channel = SqlChannel(transport, lambda: 0.0)
        result = channel.execute("UFDATA_801_2024", "SELECT 1", _unused)
        self.assertEqual(result.code, 1)
        self.assertIn("sqlcmd", result.out)
        self.assertFalse(channel.dead)
        self.assertFalse(transport.closed)


class PackTests(unittest.TestCase):
    def test_archive_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "bridge-src.zip"
            pack_bridge(dest)
            with zipfile.ZipFile(dest) as archive:
                names = archive.namelist()
        self.assertIn("bridge/build.ps1", names)
        self.assertIn("bridge/u8co-bridge.exe.config", names)
        self.assertIn("SafePath.ps1", names)
        self.assertIn("bridge/sql/ia/post.sql", names)  # 存货核算脚本由 build.ps1 嵌进 exe
        sources = [item for item in names if item.startswith("bridge/src/") and item.endswith(".cs")]
        self.assertGreater(len(sources), 0)
        # 私有源码（不随公开源码发布）在时也进套件，源码哈希随之变化。
        private = sorted((Path(__file__).resolve().parents[2] / "bridge" / "private").glob("*.cs"))
        for path in private:
            self.assertIn("bridge/private/" + path.name, names)
        for name in names:
            self.assertNotIn("..", name.split("/"))
            self.assertFalse(name.startswith("/"))

    def test_worker_and_extract_contracts(self) -> None:
        worker = (_REMOTE / "sql-worker.ps1").read_text(encoding="utf-8")
        self.assertIn("Assert-LiveWindow", worker)
        self.assertIn("Assert-SqlGuard", worker)
        self.assertIn("Assert-AllowedDb", worker)
        self.assertIn("-lt 30", worker)
        self.assertIn("-x -X1", worker)
        self.assertIn("-f 65001", worker)
        self.assertIn("ReadLine", worker)
        extract = (_REMOTE / "kit-extract.ps1").read_text(encoding="utf-8")
        self.assertIn("Expand-Archive -LiteralPath", extract)
        self.assertIn("Assert-KitPath", extract)
        self.assertIn("GetFiles", extract)
        self.assertIn("File]::Delete", extract)
        self.assertNotIn("-Recurse", extract)

    def test_mux_directory_is_private(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            path = prepare_control_path(Path(tmp))
            folder = Path(path).parent
            self.assertTrue(folder.name.startswith("u8co-mux-"))
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            opts = mux_options(path)
        self.assertIn("ControlMaster=auto", opts)
        self.assertIn("ControlPersist=120", opts)
        self.assertIn("ControlPath=" + path, opts)

    def test_long_control_path_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deep = Path(tmp) / ("n" * 180)
            with self.assertRaises(RuntimeError):
                prepare_control_path(deep)
