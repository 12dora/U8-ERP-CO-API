"""到货单关闭 / 打开用例（arrival_close：arrival-close、arrival-close-lines）的登记、标签、SQL 形状和回放核对。
不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import arrival_close as ac
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arrival_close_sql import arrival_close_samples, close_state_sql, lines_state_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_TODAY = "2026-10-03"
_REFUSED = (409, {"ok": False, "code": "state_mismatch", "message": "单据已关闭"})


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    """模拟桥：到货单的审核、关闭状态随请求变化；回读按状态回答。lazy_open 为真时打开后行关闭人不清（模拟缺陷）。"""

    def __init__(self) -> None:
        self.pick = "ROW:01|V01|D01|INV-1|01\n"
        self.calls: list[tuple[str, dict]] = []
        self.verified = False
        self.closed = False
        self.lazy_open = False
        self.dry_writes = False

    def call(self, route: str, fields: dict) -> tuple[int, dict]:
        self.calls.append((route, dict(fields)))
        doc = 700 + len(self.calls)
        if route.endswith("/close"):
            return self._close(fields)
        if route.endswith("/verify") and fields.get("type") == "arrival":
            self.verified = fields.get("action") == "verify"
        if route.endswith("/create") or route.endswith("/generate"):
            return 200, {"ok": True, "id": doc}
        return 200, {"ok": True}

    def _close(self, fields: dict) -> tuple[int, dict]:
        shut = fields["action"] == "close"
        if shut and (not self.verified or self.closed):
            return _REFUSED
        if fields.get("dry_run"):
            self.closed = self.closed or self.dry_writes
            return 200, {"ok": True, "dry_run": True, "mode": "rollback"}
        self.closed = shut
        who = "演示" if shut else ""
        return 200, {"ok": True, "type": "arrival", "id": fields["id"], "action": fields["action"], "closed": shut,
                     "closed_by": who, "closed_at": "2026-10-03" if shut else "", "lines": [{"line_id": 901, "closed": shut}]}

    def sql(self, query: str) -> str:
        if "FROM dbo.PU_ArrivalVouch AS h" in query:
            if self.closed:
                return "ROW:演示|1|1|1|1\n"
            return "ROW:|0|1|1|1\n" if self.lazy_open and self.calls[-1][1].get("action") == "open" else "ROW:|0|1|0|0\n"
        answers = (
            ("FROM dbo.Warehouse AS w", self.pick),
            ("bPTHavePO", "ROW:1\n"),
            ("FROM dbo.PO_Podetails AS d", "ROW:801\n"),
        )
        for needle, answer in answers:
            if needle in query:
                return answer
        raise AssertionError("没有准备回答：" + query[:80])


def _env() -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=object(), operator=operator, year="2026", today=_TODAY, bag={})


class _LineReplay(_Replay):
    """按行的到货单：两行订单 801 / 802、两行到货单 901 / 902，按行记关闭状态。
    sweep 为真时 U8 关第一行顺带关了第二行（模拟缺陷）；lost 为真时下一次关闭 / 打开已落库但响应 504。"""

    def __init__(self) -> None:
        super().__init__()
        self.shut: set[int] = set()
        self.sweep = False
        self.lost = False

    def _close(self, fields: dict) -> tuple[int, dict]:
        close = fields["action"] == "close"
        ids = fields.get("line_ids") or [901, 902]
        if close and set(ids) & self.shut:
            return _REFUSED
        if close:
            self.shut |= set(ids) | ({902} if self.sweep else set())
        else:
            self.shut -= set(ids)
        if self.lost:
            self.lost = False
            return 504, {"ok": False, "code": "outcome_unknown", "message": "回读失败"}
        lines = [{"line_id": line, "closed": line in self.shut} for line in (901, 902)]
        return 200, {"ok": True, "type": "arrival", "id": fields["id"], "action": fields["action"], "closed": False,
                     "closed_by": "", "closed_at": "", "lines": lines}

    def sql(self, query: str) -> str:
        if "MAX(d.ID)" in query:
            return "ROW:2|801|802\n"
        if "MAX(s.Autoid)" in query:
            return "ROW:2|901|902\n"
        if "s.Autoid = 901" in query:
            one = "1" if 901 in self.shut else "0"
            two = "1" if 902 in self.shut else "0"
            return f"ROW:|{one}|{one}|{two}|{two}\n"
        if "FROM dbo.PU_ArrivalVouch AS h" in query:
            n = str(len(self.shut))
            return f"ROW:|0|2|{n}|{n}\n"
        return super().sql(query)


def _run(client: _Replay, op: str = "arrival_close") -> tuple[str, str]:
    def invoke(_client: object, route: str, fields: dict) -> tuple[int, dict]:
        return client.call(route, fields)

    def sql(_env: object, query: str, _label: str) -> str:
        return client.sql(query)

    with mock.patch.object(sp, "run_guarded", sql), mock.patch.object(sp, "invoke", invoke):
        return ac.ARRIVAL_CLOSE_OPS[op](_env(), _SPEC, _STEP)  # type: ignore[arg-type]


def _routes(client: _Replay) -> list[str]:
    out = []
    for route, fields in client.calls:
        name = route.rsplit("/", 1)[1]
        extra = fields.get("action", "") if name in ("close", "verify") else ""
        out.append(f"{name}:{fields.get('type')}" + (":" + extra if extra else ""))
    return out


class RegistrationTests(unittest.TestCase):
    def test_registered_with_ops_and_tags(self) -> None:
        specs = ac.arrival_close_cases()
        self.assertEqual([spec.name for spec in specs], ["arrival-close", "arrival-close-lines"])
        for spec in specs:
            self.assertEqual(known_names().count(spec.name), 1)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
            self.assertTrue(spec.optional and spec.routes)
            self.assertIn("/v1/vouchers/close", spec.routes)
            self.assertTrue({"new:purchase_order", "new:arrival", "w:arrival", "new:purchase_return"} <= auto_tags(spec))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in arrival_close_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        query = close_state_sql(77)
        for column in ("h.ccloser", "h.dclosedate", "s.cbcloser", "s.dlineclosedate", "h.ID = 77", "s.ID = 77"):
            self.assertIn(column, query)
        with self.assertRaises(ValueError):
            close_state_sql(0)
        query = lines_state_sql(77, 31, 32)
        for part in ("h.ID = 77", "s.Autoid = 31", "s.Autoid = 32", "s.cbcloser", "s.dlineclosedate"):
            self.assertIn(part, query)
        with self.assertRaises(ValueError):
            lines_state_sql(77, 31, 0)


class FlowTests(unittest.TestCase):
    def test_happy_path(self) -> None:
        client = _Replay()
        status, detail = _run(client)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(_routes(client), [
            "create:purchase_order", "verify:purchase_order:verify", "generate:arrival", "close:arrival:close",
            "verify:arrival:verify", "close:arrival:close", "close:arrival:close", "close:arrival:close",
            "close:arrival:open", "verify:arrival:unverify", "delete:arrival",
            "verify:purchase_order:unverify", "delete:purchase_order",
        ])
        dry, real = client.calls[5][1], client.calls[6][1]
        self.assertTrue(dry["dry_run"])
        self.assertNotIn("dry_run", real)
        self.assertNotIn("line_ids", real)
        gen = client.calls[2][1]
        self.assertEqual((gen["id"], gen["lines"]), (701, [{"source_line_id": 801, "quantity": 2}]))

    def test_dry_run_that_writes_fails(self) -> None:
        client = _Replay()
        client.dry_writes = True
        status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("预演后", detail)
        self.assertEqual(_routes(client)[-4:], ["verify:arrival:unverify", "delete:arrival",
                                                "verify:purchase_order:unverify", "delete:purchase_order"])

    def test_open_not_cleared_fails(self) -> None:
        client = _Replay()
        client.lazy_open = True
        status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("打开后", detail)
        self.assertEqual(_routes(client)[-5:-4], ["close:arrival:open"])

    def test_no_pick_is_na_without_writes(self) -> None:
        client = _Replay()
        client.pick = "\n"
        status, _detail = _run(client)
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])


class LinesFlowTests(unittest.TestCase):
    def test_happy_path(self) -> None:
        client = _LineReplay()
        status, detail = _run(client, "arrival_close_lines")
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(_routes(client), [
            "create:purchase_order", "verify:purchase_order:verify", "generate:arrival", "verify:arrival:verify",
            "close:arrival:close", "close:arrival:close", "close:arrival:open",
            "verify:arrival:unverify", "delete:arrival", "verify:purchase_order:unverify", "delete:purchase_order",
        ])
        order = client.calls[0][1]
        self.assertEqual(len(order["lines"]), 2)
        gen = client.calls[2][1]
        self.assertEqual((gen["id"], [line["source_line_id"] for line in gen["lines"]]), (701, [801, 802]))
        self.assertEqual([client.calls[i][1].get("line_ids") for i in (4, 5, 6)], [[901], [901], [901]])

    def test_other_line_swept_fails_and_reopens(self) -> None:
        client = _LineReplay()
        client.sweep = True
        status, detail = _run(client, "arrival_close_lines")
        self.assertEqual(status, "FAIL")
        self.assertIn("只关第一行", detail)
        # 清理先整单打开（不带 line_ids），再弃审、删除。
        self.assertEqual(_routes(client)[5], "close:arrival:open")
        self.assertNotIn("line_ids", client.calls[5][1])
        self.assertEqual(client.shut, set())

    def test_lost_close_response_still_reopens(self) -> None:
        client = _LineReplay()
        client.lost = True
        status, detail = _run(client, "arrival_close_lines")
        self.assertEqual(status, "FAIL")
        self.assertIn("504", detail)
        self.assertEqual(_routes(client)[4:6], ["close:arrival:close", "close:arrival:open"])
        self.assertEqual(client.shut, set())


if __name__ == "__main__":
    unittest.main()
