"""应付审核 / 应收审核用例的登记、接线、挑单 SQL 和补偿。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from co.client.u8co_client import U8CoError

from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import rule_for, select_changed
from co.e2e.casespec.wiring import EDIT_BUILDERS, EDIT_PACKS, has_edit, edit_samples, edit_sql
from co.e2e.casespec.arap_verify import ARV_CLEANUP, arv_cases
from co.e2e.casespec.arap_verify_sql import ARV_BUILDERS, ARV_PACKS, arv_samples
from co.e2e.casespec.picks import pick_keys
from co.e2e.casespec.tags import auto_tags, bind_tags

_NAMES = (
    "ap-invoice-audit",
    "ap-invoice-audit-audited",
    "ap-invoice-audit-unreviewed",
    "ar-invoice-audit",
    "ar-invoice-audit-bad-type",
)


class _Replay:
    def __init__(self, answers: list) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers.pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got


def _env(client: _Replay, bag: dict) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-29", bag=bag)


class ArapVerifyTests(unittest.TestCase):
    def test_names_registered_once(self) -> None:
        names = known_names()
        self.assertEqual(tuple(spec.name for spec in arv_cases()), _NAMES)
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)

    def test_packs_and_readback_are_wired(self) -> None:
        for pack in ARV_PACKS:
            self.assertIn(pack, EDIT_PACKS)
            self.assertIn(pack[0], pick_keys())
        for name in ("aud_pu_auditor", "aud_pu_rows", "aud_sa_auditor", "aud_sa_rows"):
            self.assertTrue(has_edit(name), name)
        self.assertIn("FROM dbo.PurBillVouch AS h WHERE h.PBVID = 42;", edit_sql("aud_pu_auditor", "42", {}))
        self.assertIn("dbo.Ar_Detail AS d", edit_sql("aud_sa_rows", "42", {}))
        with self.assertRaises(ValueError):
            edit_sql("aud_pu_rows", "1;--", {})
        for row in arv_samples():
            self.assertIn(row, edit_samples())

    def test_pick_sql_filters(self) -> None:
        pu, done, raw, inv = (pack[3]() for pack in ARV_PACKS)
        self.assertIn("NOT (LTRIM(RTRIM(ISNULL(m.cVerifier, N''))) = N'')", pu)
        self.assertIn("LTRIM(RTRIM(ISNULL(m.cPBVVerifier, N''))) = N''", pu)
        self.assertIn("dbo.Ap_Detail", pu)
        self.assertIn("b.cClue IS NOT NULL", pu)
        self.assertIn("NOT (LTRIM(RTRIM(ISNULL(m.cPBVVerifier, N''))) = N'')", done)
        self.assertIn("LTRIM(RTRIM(ISNULL(m.cVerifier, N''))) = N''", raw)
        self.assertIn("ORDER BY m.PBVID DESC", raw)
        # 类型不对桥先回 400，负例也只挑专用、普通发票。
        for sql in (pu, done, raw):
            self.assertIn("m.cPBVBillType IN (N'01', N'02')", sql)
        # 正例：非现付、未结算，弃审才做得回来。
        self.assertIn("LTRIM(RTRIM(ISNULL(m.bPayment, N''))) IN (N'', N'0')", pu)
        self.assertIn("m.dSDate IS NULL", pu)
        self.assertIn("dbo.PurSettleVouchs", pu)
        self.assertIn("b.cBAccounter", pu)
        self.assertNotIn("bPayment", done + raw)
        self.assertIn("N'ROW:' + CONVERT(varchar(20), d.DLID) + N'|' + CONVERT(varchar(20), l.iDLsID) AS v", inv)
        self.assertIn("ISNULL(l.fretqtywkp, 0), 4, 1) >= 3", inv)

    def test_invoice_pick_avoids_other_cases(self) -> None:
        inv = ARV_PACKS[3][3]()
        self.assertEqual(ARV_PACKS[3][:3], ("arv_inv", ("arv_inv", "arv_inv_line"), (True, True)))
        # 发票生单（inv_src）和发票改单（sinv_mod_src）挑中的发货单都排除在外：两个 DLID 子查询，不带 ROW 列。
        self.assertIn("d.DLID NOT IN (", inv)
        self.assertEqual(inv.count("ISNULL((SELECT TOP (1)"), 2)
        self.assertEqual(inv.count("N'ROW:'"), 1)
        self.assertEqual(inv.count("SET NOCOUNT ON;"), 1)
        self.assertIn("x.fretqtywkp", inv)
        self.assertTrue(inv.endswith("ORDER BY d.DLID DESC, l.iDLsID;\n"))
        self.assertIn(("verify-arv-inv", inv), arv_samples())

    def test_invoice_body_is_self_contained(self) -> None:
        env = SimpleNamespace(bag={"arv_inv_line": "12"}, focus="34")
        body = ARV_BUILDERS["arv_inv_gen"](env)
        self.assertEqual(body, {"type": "sale_invoice", "id": 34, "lines": [{"source_line_id": 12, "quantity": 1}]})
        with self.assertRaises(ValueError):
            ARV_BUILDERS["arv_inv_gen"](SimpleNamespace(bag={}, focus="34"))
        self.assertIs(EDIT_BUILDERS["arv_inv_gen"], ARV_BUILDERS["arv_inv_gen"])
        ar = arv_cases()[3]
        self.assertEqual(ar.needs, ("arv_inv", "arv_inv_line"))
        # 不再读别的用例写进 bag 的键（inv_qty / inv_src_line）。
        self.assertNotIn("invoice_gen", [step.sql_name for step in ar.steps])
        self.assertIn("arv_inv_gen", [step.sql_name for step in ar.steps if step.kind == "apply"])
        self.assertIn("src:dispatch:55", bind_tags(ar, {"arv_inv": "55", "arv_inv_line": "56"}))
        self.assertIn(ar.name, ARV_CLEANUP)

    def test_steps_and_tags(self) -> None:
        pu, done, _raw, ar, bad = arv_cases()
        actions = [dict(step.fields).get("action") for step in pu.steps if step.kind == "call"]
        self.assertEqual(actions, ["arap_verify", "arap_verify", "arap_unverify"])
        self.assertIn("doc:purchase_invoice:7", bind_tags(pu, {"arv_pu": "7"}))
        self.assertIn("w:purchase_invoice", auto_tags(pu))
        self.assertEqual([(step.status, step.code) for step in done.steps], [(409, "state_mismatch")])
        ar_actions = [dict(step.fields).get("action") for step in ar.steps if step.kind == "call"]
        # 应收弃审一定在弃复之前（U8 拒绝弃复应收已审的发票）。
        self.assertLess(ar_actions.index("arap_unverify"), ar_actions.index("unverify"))
        self.assertIn("new:sale_invoice", auto_tags(ar))
        self.assertEqual((bad.needs, bad.steps[0].status, bad.steps[0].code), ((), 400, "bad_request"))

    def test_cleanup_is_best_effort(self) -> None:
        client = _Replay([{"ok": True}, U8CoError(409, "state_mismatch", "单据未审核")])
        note = ARV_CLEANUP["ar-invoice-audit"](_env(client, {"aud_inv": "9"}), "ar-invoice-audit")
        self.assertIn("arap_unverify 200", note)
        self.assertIn("unverify 409", note)
        self.assertEqual([fields["action"] for _route, fields in client.calls], ["arap_unverify", "unverify"])
        self.assertEqual({fields["id"] for _route, fields in client.calls}, {9})
        empty = ARV_CLEANUP["ap-invoice-audit"](_env(_Replay([]), {}), "ap-invoice-audit")
        self.assertEqual(empty, "没有挑中的单据")

    def test_coverage_rules(self) -> None:
        self.assertEqual(rule_for("co/bridge/src/ArapAudit.cs")[0], "co/bridge/src/ArapAudit*.cs")
        got = select_changed(["co/bridge/src/ArapAuditGate.cs"], list(known_names()))
        for name in _NAMES:
            self.assertIn(name, got)
        self.assertIn("invoice-generate", got)


if __name__ == "__main__":
    unittest.main()
