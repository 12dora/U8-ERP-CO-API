"""退货申请单只读核对（return_apply）的登记、标签和回放核对。不连接服务器。写入循环见 test_casespec_return_apply_cycle。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import ops_run
from co.e2e.casespec import return_apply as ra
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.tags import auto_tags
from co.e2e.tests import support

_NAMES = ("list-sale-return-apply", "load-sale-return-apply", "sale-return-apply-write-refused")
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _row(doc: int, qty: str = "-300", verified: bool = True) -> dict:
    return {"id": doc, "code": f"{doc:012d}", "ufts": str(doc), "line_count": "1", "quantity": qty,
            "verified": verified, "closed": False}


class _Replay:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.lines = [{"AutoID": "11", "iQuantity": "-300", "iDLsID": "77"}]
        self.state = {"verified": True, "closed": False}
        self.edges = [{"from_type": "dispatch", "from_id": 9, "to_type": ra.KIND, "to_id": 5, "lines": 1}]
        self.refuse = (400, {"ok": False, "code": "bad_request", "message": "该单据类型不支持关闭"})
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, _env: object, route: str, body: dict) -> tuple[int, dict]:
        self.calls.append((route, dict(body)))
        if route.endswith(("/close", "/generate")):
            return self.refuse
        if route.endswith("/doc_trace"):
            nodes = [{"type": ra.KIND, "id": body["id"]}, {"type": "dispatch", "id": 9}]
            return 200, {"ok": True, "nodes": nodes, "edges": self.edges, "omitted": 0}
        if route.endswith("/list"):
            keys = ("id", "code", "ufts")
            items = [{k: row[k] for k in keys} for row in self.rows] if body.get("keys_only") else self.rows
            return 200, {"ok": True, "items": items}
        if route.endswith("/search"):
            return 200, {"ok": True, "items": [row for row in self.rows if body["code_like"] in row["code"]]}
        row = next(row for row in self.rows if row["id"] == body["id"])
        return 200, {"ok": True, "code": row["code"], "lines": self.lines, "state": self.state}


def _run(replay: _Replay, op: str) -> tuple[str, str]:
    env = SimpleNamespace(client=object(), bag={})
    with mock.patch.object(ra, "send", replay):
        return ra.RETURN_APPLY_OPS[op](env, _SPEC, _STEP)  # type: ignore[arg-type]


class RegistryTests(unittest.TestCase):
    def test_registered_once(self) -> None:
        names = known_names()
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)
        for spec in (spec for spec in ra.return_apply_cases() if spec.name in _NAMES):
            self.assertTrue(spec.requires_login and spec.optional and spec.routes, spec.name)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name), spec.name)
            self.assertIn("list:" + ra.KIND, auto_tags(spec))

    def test_changed_bridge_file_selects_cases(self) -> None:
        picked = select_changed(["co/bridge/src/ReturnsApplyRead.cs"], list(_NAMES) + ["sale-verify"])
        for name in _NAMES:
            self.assertIn(name, picked)


class ReadTests(unittest.TestCase):
    def test_list_na_then_pass(self) -> None:
        replay = _Replay()
        self.assertEqual(_run(replay, "return_apply_list")[0], "N/A")
        replay.rows = [_row(5), _row(6, verified=False)]
        status, detail = _run(replay, "return_apply_list")
        self.assertEqual(status, "PASS", detail)
        self.assertIn(("/v1/vouchers/search", {"type": ra.KIND, "code_like": "000000000005", "limit": 20}), replay.calls)

    def test_list_positive_quantity_fails(self) -> None:
        replay = _Replay()
        replay.rows = [_row(5, qty="300")]
        status, detail = _run(replay, "return_apply_list")
        self.assertEqual(status, "FAIL")
        self.assertIn("quantity", detail)

    def test_load_pass(self) -> None:
        replay = _Replay()
        self.assertEqual(_run(replay, "return_apply_load")[0], "N/A")
        replay.rows = [_row(5)]
        status, detail = _run(replay, "return_apply_load")
        self.assertEqual(status, "PASS", detail)
        self.assertIn(("/v1/reports/doc_trace", {"type": ra.KIND, "id": 5, "depth": 1}), replay.calls)

    def test_load_state_mismatch_fails(self) -> None:
        replay = _Replay()
        replay.rows = [_row(5, verified=False)]
        status, detail = _run(replay, "return_apply_load")
        self.assertEqual(status, "FAIL")
        self.assertIn("state.verified", detail)

    def test_load_missing_dispatch_edge_fails(self) -> None:
        replay = _Replay()
        replay.rows = [_row(5)]
        replay.edges = []
        status, detail = _run(replay, "return_apply_load")
        self.assertEqual(status, "FAIL")
        self.assertIn("发货单", detail)

    def test_write_refused(self) -> None:
        replay = _Replay()
        self.assertEqual(_run(replay, "return_apply_write_refused")[0], "PASS")
        routes = [route for route, _body in replay.calls]
        self.assertEqual(routes, ["/v1/vouchers/close", "/v1/vouchers/generate"])
        replay.refuse = (200, {"ok": True})
        self.assertEqual(_run(replay, "return_apply_write_refused")[0], "FAIL")

    def test_bridge_error_raises(self) -> None:
        def broken(_env: object, _route: str, _body: dict) -> tuple[int, dict]:
            return 403, {"ok": False, "code": "no_permission", "message": "没有权限"}

        env = SimpleNamespace(client=object(), bag={})
        with mock.patch.object(ra, "send", broken), self.assertRaises(ValueError):
            ra.list_read(env, _SPEC, _STEP)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
