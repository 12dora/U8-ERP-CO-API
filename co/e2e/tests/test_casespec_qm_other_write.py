"""其他报检单新增、其他检验单生单用例（qm_other_write）的登记、标签、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run, qm_tpl
from co.e2e.casespec import qm_other_read as read
from co.e2e.casespec import qm_other_write as qw
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.qm_other_write_sql import (
    adopt_sql,
    defines_sql,
    gone_sql,
    pick_sql,
    qm_other_write_samples,
    state_sql,
)
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_SPEC = CaseSpec("qm-other-cycle", "s")
_STEP = Step("custom")
_TPL_361 = "ROW:CDEFINE10|T|nvarchar\nROW:chdefine16|T|nvarchar\n"
_MADE = "ROW:1|张三|1002|N|0||||||1\n"
_GEN = "ROW:1|张三|1002|1|1|9001||0|0|1002|1\n"
_VERIFIED = "ROW:1|张三|1002|1|1|9001|张三|0|0|1002|1\n"
_DROPPED = "ROW:1|张三|1002|0|0||||||1\n"
_UNVER = "ROW:1||1002|N|0||||||1\n"
# 新增后、报检单弃审、审核、预演生单、生单、检验单审核、弃审、删除检验单。
_HAPPY = [_MADE, _UNVER, _MADE, _MADE, _GEN, _VERIFIED, _GEN, _DROPPED]


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    def __init__(self, answers: dict[str, list]) -> None:
        self.answers = {key: list(val) for key, val in answers.items()}
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers[route].pop(0)
        if isinstance(got, Exception):
            raise got
        return got


class _Sql:
    """按 SQL 里的特征串依次回答（每个键一个队列），没有匹配就报错。"""

    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]

    def __call__(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.answers:
            if key in query and rows:
                return rows.pop(0)
        raise AssertionError("没有准备回答：" + query[:80])


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="803", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-03", bag={})


def _sql(states: list[str], pick: str = "ROW:A01|P01|Q01|P55\n", tpl: str = _TPL_361, gone: str = "ROW:0|0|0|0\n") -> _Sql:
    return _Sql([
        ("QMCHECKPROJECT", [pick]),
        ("VT_ID = 361 AND", [tpl]),
        ("VT_ID = 365 AND", [""]),
        ("QMINSPECTVOUCHER_extradefine AS x", ["ROW:LOT001|原料\n"]),
        ("MAX(h.ID)", ["ROW:7000\n", "ROW:7000\n"]),
        ("h.ID > ", ["ROW:7001\n"]),
        ("SELECT TOP (1) b.AUTOID", states),
        ("FROM dbo.QMCHECKVOUCHERS AS d", [gone]),
    ])


def _ok(**extra: object) -> dict:
    return {"ok": True, **extra}


def _client(create: list | None = None, generate: list | None = None, delete: list | None = None) -> _Replay:
    verify = [_ok(), _ok(), U8CoError(409, "state_mismatch", "已有其他检验单，不能弃审"), _ok(), _ok(), _ok(), _ok()]
    if create and len(create) > 1 and isinstance(create[1], dict) and create[1].get("auto_verify_error"):
        verify = verify[1:]  # 补审核没做时报检单只审核、不先弃审
    return _Replay({
        "/v1/vouchers/create": create or [_ok(dry_run=True), _ok(id=7001, state={"verified": True})],
        "/v1/vouchers/generate": generate or [_ok(dry_run=True), _ok(id=9001), U8CoError(409, "state_mismatch", "已生成")],
        "/v1/vouchers/verify": verify,
        "/v1/vouchers/delete": delete or [_ok(deleted=True), _ok(deleted=True, unverified=True), _ok(deleted=True)],
    })


def _run(client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(qw, "run_guarded", sql), mock.patch.object(qm_tpl, "run_guarded", sql):
        return qw.qm_other_cycle(_env(client), _SPEC, _STEP)


def _steps(client: _Replay) -> list[tuple[str, str, str, bool]]:
    return [(route.rsplit("/", 1)[1], fields.get("type", ""), fields.get("action", ""), fields.get("dry_run", False))
            for route, fields in client.calls]


class RegistrationTests(unittest.TestCase):
    def test_registered_once(self) -> None:
        names = known_names()
        (spec,) = qw.qm_other_write_cases()
        self.assertEqual(spec.name, "qm-other-cycle")
        self.assertEqual(names.count(spec.name), 1)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertTrue(spec.optional)
        self.assertTrue(spec.routes)

    def test_tags_keep_the_read_case_apart(self) -> None:
        (spec,) = qw.qm_other_write_cases()
        tags = auto_tags(spec)
        self.assertTrue({"new:qm_other_inspect", "new:qm_other_check"} <= tags)
        self.assertTrue(conflicts(tags, auto_tags(read.qm_other_cases()[0])))

    def test_bridge_change_selects_the_case(self) -> None:
        got = select_changed(["co/bridge/src/QmOthIns.cs"], list(known_names()))
        self.assertIn("qm-other-cycle", got)


class SqlTests(unittest.TestCase):
    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in qm_other_write_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)

    def test_shapes(self) -> None:
        self.assertIn("c.CVOUCHTYPE = N'QM15'", pick_sql())
        self.assertIn("i.CVOUCHTYPE = N'QM11'", pick_sql())
        both = defines_sql(361, ["CDEFINE10", "chdefine16"])
        self.assertIn("LEFT JOIN dbo.QMINSPECTVOUCHER_extradefine AS x", both)
        self.assertIn("h.cdefine10", both)
        self.assertIn("x.chdefine16", both)
        self.assertNotIn("extradefine", defines_sql(365, ["cdefine3"]))
        self.assertIn("WHERE d.ID = 200", gone_sql("100", "200"))
        self.assertIn("h.ID > 0", adopt_sql("0", "A01"))
        self.assertIn("y.INSPECTID = 100", state_sql("100"))
        bad = (
            lambda: defines_sql(361, ["cinvcode"]),
            lambda: defines_sql(351, ["cdefine1"]),
            lambda: adopt_sql("1", "A'01"),
            lambda: state_sql("1;--"),
        )
        for call in bad:
            with self.assertRaises(ValueError):
                call()


class CycleTests(unittest.TestCase):
    def test_cycle_passes(self) -> None:
        client = _client()
        status, detail = _run(client, _sql(list(_HAPPY)))
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(_steps(client), [
            ("create", "qm_other_inspect", "", True), ("create", "qm_other_inspect", "", False),
            ("verify", "qm_other_inspect", "unverify", False), ("verify", "qm_other_inspect", "verify", False),
            ("generate", "qm_other_check", "", True), ("generate", "qm_other_check", "", False),
            ("generate", "qm_other_check", "", False), ("verify", "qm_other_inspect", "unverify", False),
            ("verify", "qm_other_check", "verify", False), ("verify", "qm_other_check", "unverify", False),
            ("delete", "qm_other_check", "", False), ("delete", "qm_other_inspect", "", False),
        ])
        made = client.calls[1][1]
        self.assertEqual(made["head"], {"cdefine10": "LOT001", "chdefine16": "原料"})
        self.assertEqual(made["lines"], [{"cinvcode": "A01", "quantity": 1}])
        gen = client.calls[5][1]
        self.assertEqual((gen["id"], gen["source_type"], gen["lines"]), (7001, "qm_other_inspect",
                                                                        [{"source_line_id": 1002, "quantity": 1}]))
        self.assertEqual(gen["head"], {"ccheckpersoncode": "P01", "cdepcode": "Q01", "project_code": "P55"})

    def test_no_real_data_is_na(self) -> None:
        client = _client()
        status, detail = _run(client, _sql([], pick=""))
        self.assertEqual(status, "N/A")
        self.assertIn("VT 361", detail)
        self.assertEqual(client.calls, [])

    def test_body_define_required_is_na(self) -> None:
        client = _client()
        status, detail = _run(client, _sql([], tpl="ROW:cbdefine1|B|nvarchar\n"))
        self.assertEqual(status, "N/A")
        self.assertIn("VT 361", detail)
        self.assertEqual(client.calls, [])

    def test_unverified_without_reason_fails_and_cleans_up(self) -> None:
        client = _client(create=[_ok(dry_run=True), _ok(id=7001, state={"verified": False})])
        status, detail = _run(client, _sql([_UNVER]))
        self.assertEqual(status, "FAIL")
        self.assertIn("auto_verify_error", detail)
        self.assertIn("qm_other_inspect 7001 已清理", detail)
        self.assertEqual(_steps(client)[-1], ("delete", "qm_other_inspect", "", False))

    def test_auto_verify_skipped_is_recovered_by_verify(self) -> None:
        made = _ok(id=7001, state={"verified": False}, auto_verify_error="无审核权限，报检单保存为未审核")
        client = _client(create=[_ok(dry_run=True), made])
        status, detail = _run(client, _sql([_UNVER] + list(_HAPPY[2:])))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("无审核权限", detail)
        self.assertEqual(_steps(client)[2], ("verify", "qm_other_inspect", "verify", False))

    def test_second_check_is_cleaned_before_the_inspect(self) -> None:
        client = _client(generate=[_ok(dry_run=True), _ok(id=9001), _ok(id=9002)],
                         delete=[_ok(deleted=True), _ok(deleted=True), _ok(deleted=True)])
        status, detail = _run(client, _sql(list(_HAPPY[:5])))
        self.assertEqual(status, "FAIL")
        self.assertIn("qm_other_check 9002 已清理", detail)
        deletes = [fields["id"] for route, fields in client.calls if route.endswith("/delete")]
        self.assertEqual(deletes, [9002, 9001, 7001])

    def test_unknown_generate_outcome_adopts_and_cleans_up(self) -> None:
        client = _client(generate=[_ok(dry_run=True), U8CoError(504, "outcome_unknown", "结果未知")],
                         delete=[_ok(deleted=True), _ok(deleted=True)])
        status, detail = _run(client, _sql([_MADE, _UNVER, _MADE, _MADE, _GEN]))
        self.assertEqual(status, "FAIL")
        self.assertIn("504", detail)
        self.assertEqual(_steps(client)[-3:], [
            ("verify", "qm_other_check", "unverify", False), ("delete", "qm_other_check", "", False),
            ("delete", "qm_other_inspect", "", False),
        ])

    def test_unknown_create_outcome_adopts_by_max_id(self) -> None:
        client = _client(create=[_ok(dry_run=True), TimeoutError("timed out")], delete=[_ok(deleted=True)])
        status, detail = _run(client, _sql([]))
        self.assertEqual(status, "FAIL")
        self.assertIn("qm_other_inspect 7001 已清理", detail)

    def test_flag_not_back_fails(self) -> None:
        client = _client()
        status, detail = _run(client, _sql(list(_HAPPY[:-1]) + ["ROW:1|张三|1002|1|0||||||1\n"]))
        self.assertEqual(status, "FAIL")
        self.assertIn("BFLAG=1", detail)

    def test_leftovers_fail(self) -> None:
        client = _client()
        status, detail = _run(client, _sql(list(_HAPPY), gone="ROW:0|1|0|0\n"))
        self.assertEqual(status, "FAIL")
        self.assertIn("0|1|0|0", detail)


if __name__ == "__main__":
    unittest.main()
