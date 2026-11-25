"""不良品处理单用例（qm_reject）的登记、标签、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import qm_reject as qr
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.qm_reject_sql import (
    check_pick_sql,
    dispose_pick_sql,
    qm_reject_samples,
    reason_pick_sql,
    reject_state_sql,
)
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("qm-incoming-reject-cycle", "qm-product-reject-cycle", "qm-reject-bad-request")
_SPEC = CaseSpec("qm-incoming-reject-cycle", "s")
_STEP = Step("custom")
_PCODE = qr.PRODUCT.prefix + "1"
_CLEAN = "ROW:0|0|||||||0|0.0000\n"


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
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-02", bag={})


def _made(biz: str = "QM05", verifier: str = "", wf: str = "0", code: str = qr.INCOMING.prefix + "202610020001") -> str:
    ivtid = "355" if biz == "QM05" else "356"
    return f"ROW:1|1|501|{code}|{ivtid}|{biz}|{verifier}|{wf}|1|1.0000\n"


def _picks(
    check: str = "ROW:77|1.0000|I01\n", reason: str = "ROW:01\n", dispose: str = "ROW:Sys01\n", tpl: str = ""
) -> list:
    # 模板必输项（qm_tpl 同一条查询）排在回读之前：它也含 OUTER APPLY。
    return [
        ("FROM dbo.QMCHECKVOUCHER AS c\nWHERE", [check]),
        ("FROM dbo.Reason", [reason]),
        ("FROM dbo.QMSCRAPDISPOSE", [dispose]),
        ("FROM dbo.voucheritems", [tpl]),
    ]


def _sql(states: list[str], **picks: str) -> _Sql:
    return _Sql(_picks(**picks) + [("OUTER APPLY", states)])


_HAPPY = [_CLEAN, _CLEAN, _made(), _made(verifier="张三"), _made(), _CLEAN]


def _conflict() -> U8CoError:
    return U8CoError(409, "state_mismatch", "检验单已生成不良品处理单")


def _client(verify: list | None = None, delete: list | None = None) -> _Replay:
    return _Replay({
        "/v1/vouchers/generate": [{"ok": True, "dry_run": True, "mode": "validate"}, {"ok": True, "id": 501}, _conflict()],
        "/v1/vouchers/verify": verify if verify is not None else [{"ok": True}, {"ok": True}],
        "/v1/vouchers/delete": delete if delete is not None else [{"ok": True, "deleted": True}],
    })


def _run(op, client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(qr, "run_guarded", sql):
        return op(_env(client), _SPEC, _STEP)


class RegistrationTests(unittest.TestCase):
    def test_registered_with_ops(self) -> None:
        names = known_names()
        for spec in qr.qm_reject_cases():
            with self.subTest(case=spec.name):
                self.assertEqual(names.count(spec.name), 1)
                self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
                self.assertTrue(spec.routes)
        self.assertEqual(tuple(spec.name for spec in qr.qm_reject_cases()), _NAMES)

    def test_tags_and_conflicts(self) -> None:
        incoming, product, bad = (auto_tags(spec) for spec in qr.qm_reject_cases())
        self.assertIn("new:qm_incoming_reject", incoming)
        self.assertIn("chain:pu", incoming)
        self.assertIn("new:qm_product_reject", product)
        self.assertIn("chain:mfg", product)
        self.assertTrue(conflicts(product, bad))
        self.assertFalse(conflicts(incoming, product))

    def test_bridge_change_selects_the_cases(self) -> None:
        got = select_changed(["co/bridge/src/QmRejSrc.cs"], list(known_names()))
        for name in _NAMES:
            self.assertIn(name, got)


class SqlTests(unittest.TestCase):
    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in qm_reject_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)

    def test_shapes(self) -> None:
        self.assertIn("seed:qmr_arrival", check_pick_sql("QM03"))
        self.assertIn("seed:qmr_mo", check_pick_sql("QM04"))
        self.assertIn("c.CVOUCHTYPE = N'QM04'", check_pick_sql("QM04"))
        self.assertIn("iReasontype = 1", reason_pick_sql())
        self.assertIn("IDISPOSEFLOW = 2", dispose_pick_sql(2))
        self.assertIn("WHERE c.ID = 100;", reject_state_sql("100"))
        for bad in (lambda: reject_state_sql("0"), lambda: reject_state_sql("1;--"), lambda: dispose_pick_sql(5)):
            with self.assertRaises(ValueError):
                bad()
        with self.assertRaises(KeyError):
            check_pick_sql("QM01")


class CycleTests(unittest.TestCase):
    def test_incoming_cycle_passes(self) -> None:
        client = _client()
        status, detail = _run(qr.qm_incoming_reject_cycle, client, _sql(list(_HAPPY)))
        self.assertEqual(status, "PASS", detail)
        steps = [(route.rsplit("/", 1)[1], fields.get("action", ""), fields.get("dry_run", False)) for route, fields in client.calls]
        self.assertEqual(steps, [
            ("generate", "", True), ("generate", "", False), ("generate", "", False),
            ("verify", "verify", False), ("verify", "unverify", False), ("delete", "", False),
        ])
        line = client.calls[1][1]["lines"][0]
        self.assertEqual(line, {"source_line_id": 77, "quantity": 1, "cscrapdiscode": "Sys01", "creasoncode": "01"})
        self.assertEqual((client.calls[1][1]["type"], client.calls[1][1]["source_type"]),
                         ("qm_incoming_reject", "qm_incoming_check"))

    def test_product_cycle_sends_the_downgrade_inventory(self) -> None:
        client = _client()
        states = [_CLEAN, _CLEAN, _made("QM06", code=_PCODE), _made("QM06", "张三", code=_PCODE),
                  _made("QM06", code=_PCODE), _CLEAN]
        status, detail = _run(qr.qm_product_reject_cycle, client, _sql(states, dispose="ROW:Sys03\n"))
        self.assertEqual(status, "PASS", detail)
        line = client.calls[1][1]["lines"][0]
        self.assertEqual((line["cscrapdiscode"], line["cdiminvcode"]), ("Sys03", "I01"))

    def test_workflow_controlled_skips_direct_audit(self) -> None:
        client = _client(verify=[U8CoError(409, "state_mismatch", "请用 workflow/*")])
        states = [_CLEAN, _CLEAN, _made(wf="1"), _made(wf="1"), _CLEAN]
        status, detail = _run(qr.qm_incoming_reject_cycle, client, _sql(states))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("409", detail)
        self.assertEqual([route.rsplit("/", 1)[1] for route, _f in client.calls][-2:], ["verify", "delete"])

    def test_wrong_code_prefix_fails_and_cleans_up(self) -> None:
        client = _client(verify=[{"ok": True}], delete=[{"ok": True, "deleted": True}])
        states = [_CLEAN, _CLEAN, _made(code="XX1")]
        status, detail = _run(qr.qm_incoming_reject_cycle, client, _sql(states))
        self.assertEqual(status, "FAIL")
        self.assertIn(qr.INCOMING.prefix, detail)
        self.assertIn("已清理", detail)
        tail = [(route.rsplit("/", 1)[1], fields.get("action", "")) for route, fields in client.calls[-2:]]
        self.assertEqual(tail, [("verify", "unverify"), ("delete", "")])

    def test_flag_not_back_after_delete_fails(self) -> None:
        client = _client()
        states = list(_HAPPY[:-1]) + ["ROW:0|1|||||||0|0.0000\n"]
        status, detail = _run(qr.qm_incoming_reject_cycle, client, _sql(states))
        self.assertEqual(status, "FAIL")
        self.assertIn("BREJFLAG=1", detail)

    def test_repeat_generate_must_conflict(self) -> None:
        client = _client(verify=[{"ok": True}], delete=[{"ok": True, "deleted": True}])
        client.answers["/v1/vouchers/generate"][2] = {"ok": True, "id": 502}
        status, detail = _run(qr.qm_incoming_reject_cycle, client, _sql([_CLEAN, _CLEAN, _made()]))
        self.assertEqual(status, "FAIL")
        self.assertIn("409", detail)

    def test_unknown_outcome_adopts_and_cleans_up(self) -> None:
        # 生单 504（或超时、客户端异常）时按检验单回读，U8 已提交的处理单也要弃审、删除。
        for failure in (U8CoError(504, "outcome_unknown", "结果未知"), TimeoutError("timed out")):
            with self.subTest(failure=type(failure).__name__):
                client = _client(verify=[{"ok": True}], delete=[{"ok": True, "deleted": True}])
                client.answers["/v1/vouchers/generate"][1] = failure
                status, detail = _run(qr.qm_incoming_reject_cycle, client, _sql([_CLEAN, _CLEAN, _made()]))
                self.assertEqual(status, "FAIL")
                self.assertIn("已清理", detail)
                tail = [(route.rsplit("/", 1)[1], fields["id"], fields.get("action", "")) for route, fields in client.calls[-2:]]
                self.assertEqual(tail, [("verify", 501, "unverify"), ("delete", 501, "")])

    def test_unknown_outcome_without_doc_has_nothing_to_clean(self) -> None:
        client = _client(verify=[], delete=[])
        client.answers["/v1/vouchers/generate"][1] = U8CoError(504, "outcome_unknown", "结果未知")
        status, detail = _run(qr.qm_incoming_reject_cycle, client, _sql([_CLEAN, _CLEAN, _CLEAN]))
        self.assertEqual(status, "FAIL")
        self.assertIn("504", detail)
        self.assertEqual([route.rsplit("/", 1)[1] for route, _f in client.calls], ["generate", "generate"])

    def test_missing_seed_or_reason_is_na(self) -> None:
        for picks, word in (({"check": ""}, "qmr_"), ({"reason": ""}, "不良原因"), ({"dispose": ""}, "QMSCRAPDISPOSE")):
            with self.subTest(word=word):
                client = _client()
                status, detail = _run(qr.qm_incoming_reject_cycle, client, _sql([], **picks))
                self.assertEqual(status, "N/A")
                self.assertIn(word, detail)
                self.assertEqual(client.calls, [])


class TemplateTests(unittest.TestCase):
    def test_required_head_defines_are_sent(self) -> None:
        client = _client()
        tpl = "ROW:chdefine15|T|nvarchar\nROW:cdefine7|T|float\nROW:CINVCODE|B|nvarchar\n"
        status, detail = _run(qr.qm_incoming_reject_cycle, client, _sql(list(_HAPPY), tpl=tpl))
        self.assertEqual(status, "PASS", detail)
        for _route, sent in client.calls[:3]:
            self.assertEqual(sent["head"], {"chdefine15": "E2E", "cdefine7": "1"})

    def test_no_required_define_sends_no_head(self) -> None:
        client = _client()
        status, detail = _run(qr.qm_incoming_reject_cycle, client, _sql(list(_HAPPY)))
        self.assertEqual(status, "PASS", detail)
        self.assertNotIn("head", client.calls[1][1])

    def test_body_define_required_is_na(self) -> None:
        client = _client()
        status, detail = _run(qr.qm_incoming_reject_cycle, client, _sql([], tpl="ROW:cbdefine1|B|nvarchar\nROW:cdefine3|B|nvarchar\n"))
        self.assertEqual(status, "N/A")
        self.assertIn("VT 355", detail)
        self.assertIn("cbdefine1（表体）、cdefine3（表体）", detail)
        self.assertEqual(client.calls, [])


class BadRequestTests(unittest.TestCase):
    def test_missing_downgrade_inventory_is_400(self) -> None:
        client = _Replay({"/v1/vouchers/generate": [U8CoError(400, "bad_request", "处理方式「降级」须指定处理后存货 cdiminvcode")]})
        status, detail = _run(qr.qm_reject_bad_request, client, _sql([], dispose="ROW:Sys03\n"))
        self.assertEqual(status, "PASS", detail)
        sent = client.calls[0][1]
        self.assertTrue(sent["dry_run"])
        self.assertNotIn("cdiminvcode", sent["lines"][0])
        self.assertEqual(sent["type"], "qm_product_reject")

    def test_accepted_is_fail(self) -> None:
        client = _Replay({"/v1/vouchers/generate": [{"ok": True, "dry_run": True}]})
        status, _detail = _run(qr.qm_reject_bad_request, client, _sql([], dispose="ROW:Sys03\n"))
        self.assertEqual(status, "FAIL")


if __name__ == "__main__":
    unittest.main()
