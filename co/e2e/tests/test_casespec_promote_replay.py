"""汇兑损益、采购结算的重做用例（exgain_replay、settle_replay）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import exgain_replay as ex
from co.e2e.casespec import replay_prep as prep
from co.e2e.casespec import settle_replay as st
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.exgain_replay_sql import exgain_replay_samples, gl_info_sql
from co.e2e.casespec.replay_prep_sql import closed_sql, replay_prep_samples
from co.e2e.casespec.settle_replay_sql import settle_pick_sql, settle_replay_samples
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_DAY = "2026-08-31"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _env() -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=object(), operator=operator, year="2026", today="2026-10-03", bag={})


class _Fake:
    """按 SQL 特征串依次回答（每个键一个队列，队列只剩一项时重复它）；按路由依次回答请求。"""

    def __init__(self, sql: list[tuple[str, list[list[str]]]], replies: dict[str, list[tuple[int, dict]]]) -> None:
        self.sql_answers = [(key, list(rows)) for key, rows in sql]
        self.replies = {key: list(val) for key, val in replies.items()}
        self.calls: list[tuple[str, dict]] = []

    def sql(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.sql_answers:
            if key in query:
                got = rows.pop(0) if len(rows) > 1 else rows[0]
                return "".join("ROW:" + row + "\n" for row in got)
        raise AssertionError("没有准备回答：" + query[:120])

    def invoke(self, _client: object, route: str, fields: dict) -> tuple[int, dict]:
        self.calls.append((route, dict(fields)))
        queue = self.replies[route]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def sent(self, route: str) -> list[dict]:
        return [fields for got, fields in self.calls if got == route]


def _run(fake: _Fake, ops: dict, op: str) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", fake.sql), mock.patch.object(sp, "invoke", fake.invoke):
        return ops[op](_env(), _SPEC, _STEP)


class RegistrationTests(unittest.TestCase):
    def test_registered(self) -> None:
        for spec in ex.exgain_replay_cases() + st.settle_replay_cases():
            self.assertEqual(known_names().count(spec.name), 1)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
            self.assertTrue(spec.optional and spec.routes)
            self.assertTrue(any(tag.startswith("new:") for tag in auto_tags(spec)))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in exgain_replay_samples() + settle_replay_samples() + replay_prep_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        with self.assertRaises(ValueError):
            gl_info_sql("AR1'; --")
        with self.assertRaises(ValueError):
            settle_pick_sql("other", "2026-10-03")
        with self.assertRaises(ValueError):
            closed_sql(2026, 13)


# ---- 汇兑损益重做 ----

_DAY_ROW = f"{_DAY}|0|1|美元|0|0|1|AR0000000000007|0|3|8"
_CLOSED_DAY = f"{_DAY}|1|1|美元|0|0|1|AR0000000000007|0|3|8"
# 结账标志：期间|pu|sa|st|ia|ar|ap|gl。
_OPEN = ["9|0|0|0|0|0|0|0", "8|0|0|0|0|0|0|0"]
_CLOSED_8 = ["9|0|0|0|0|0|0|0", "8|1|1|1|1|1|1|1"]
# 凭证状态：行数|张数|已记账|作废|年|期|类别|号|审核|签字。
_FREE = "3|1|0|0|2026|8|转|17|0|0"
_POSTED = "3|1|3|0|2026|8|转|17|1|1"
_ROWS = ["C1|27|SI1|1|10.00|0.00|0.00|应收汇兑损益|8|1", "C2|R0|AR1|1|0.00|3.00|0.00|应收汇兑损益|8|1"]
_BALANCES = ["R|R0|AR1|97.00|15.00", "S|27|SI1|0.00|-10.00"]
_GL = ["112201|C1|10.00|0.00|1", "112201|C2|0.00|3.00|1", "660301||0.00|7.00|1"]


def _exgain(day: str = _DAY_ROW, after: list[str] | None = None, cancel: tuple[int, dict] | None = None,
            closed: list[list[str]] | None = None, voucher: list[list[str]] | None = None) -> _Fake:
    sql = [
        ("ORDER BY d.dRegDate DESC", [[day]]),
        ("m.iperiod BETWEEN", closed or [_OPEN]),
        ("g.ccashier", voucher or [[_FREE]]),
        ("MAX(t.blk)", [["SYRAR01|C1|0", "SYRAR02|C2|0", "SYRAR03|C3|1"]]),
        ("a.dVouchDate >", [[]]),
        ("COUNT(DISTINCT d.cCancelNo)", [_ROWS, after or _ROWS]),
        ("N'ROW:R|'", [_BALANCES]),
        ("d.cCancelNo IN (", [["0"]]),
        ("g.ibook", [["3|0|0|1|660301|转|2026-08-31"]]),
        ("GROUP BY g.ccode, g.ccus_id", [_GL]),
    ]
    replies = {
        ex.DROP: [(200, {"ok": True})],
        ex.EXGAIN_CANCEL: [cancel or (200, {"ok": True})],
        ex.EXGAIN: [(200, {"ok": True, "batches": [{"cancel_no": "SYRAR11"}, {"cancel_no": "SYRAR12"}]})],
        ex.VOUCHER: [(200, {"ok": True, "pz_id": "AR0000000000009"})],
        prep.PERIODS: [(200, {"ok": True})],
        prep.GL + "unpost": [(200, {"ok": True})],
        prep.GL + "unverify": [(200, {"ok": True})],
        prep.GL + "unsign": [(200, {"ok": True})],
    }
    return _Fake(sql, replies)


class ExGainReplayTests(unittest.TestCase):
    def test_replays_unblocked_partners_and_revouchers(self) -> None:
        fake = _exgain()
        status, detail = _run(fake, ex.EXGAIN_REPLAY_OPS, "arap_exgain_replay")
        self.assertEqual(status, "PASS", detail)
        routes = [route for route, _fields in fake.calls]
        self.assertEqual(routes, [ex.DROP, ex.EXGAIN_CANCEL, ex.EXGAIN, ex.VOUCHER])
        self.assertEqual(fake.sent(ex.DROP)[0]["pz_id"], "AR0000000000007")
        self.assertEqual(fake.sent(ex.EXGAIN_CANCEL)[0]["cancel_nos"], ["SYRAR01", "SYRAR02"])
        redo = fake.sent(ex.EXGAIN)[0]
        self.assertEqual((redo["partners"], redo["currency"], redo["date"]), (["C1", "C2"], "美元", _DAY))
        self.assertNotIn("rate", redo)
        voucher = fake.sent(ex.VOUCHER)[0]
        self.assertEqual(voucher["cancel_nos"], ["SYRAR11", "SYRAR12", "SYRAR03"])
        self.assertEqual((voucher["pl_code"], voucher["sign"], voucher["voucher_date"]), ("660301", "转", _DAY))
        self.assertIn("跳过", detail)

    def test_row_mismatch_fails(self) -> None:
        changed = [_ROWS[0].replace("10.00", "10.01"), _ROWS[1]]
        status, detail = _run(_exgain(after=changed), ex.EXGAIN_REPLAY_OPS, "arap_exgain_replay")
        self.assertEqual(status, "FAIL")
        self.assertIn("9M 行", detail)

    def test_prepares_closed_period_and_posted_voucher(self) -> None:
        # 准备阶段：应收 8 月已结账（总账同期也结了）→ 先取消总账、再取消应收；凭证已记账、审核、签字 → 取消记账一次后
        # 不再记账，取消签字、取消审核，再取消制单。
        fake = _exgain(day=_CLOSED_DAY, closed=[_CLOSED_8, _OPEN], voucher=[[_POSTED], [_POSTED.replace("|3|0|2026", "|0|0|2026")]])
        status, detail = _run(fake, ex.EXGAIN_REPLAY_OPS, "arap_exgain_replay")
        self.assertEqual(status, "PASS", detail)
        reopened = [(body["module"], body["period"], body["action"]) for body in fake.sent(prep.PERIODS)]
        self.assertEqual(reopened, [("gl", 8, "reopen"), ("ar", 8, "reopen")])
        routes = [route for route, _fields in fake.calls]
        tail = [prep.GL + "unpost", prep.GL + "unsign", prep.GL + "unverify", ex.DROP, ex.EXGAIN_CANCEL, ex.EXGAIN, ex.VOUCHER]
        self.assertEqual(routes[2:], tail)
        self.assertEqual(fake.sent(prep.GL + "unsign")[0]["no"], 17)
        self.assertIn("准备", detail)

    def test_refused_reopen_is_na(self) -> None:
        fake = _exgain(day=_CLOSED_DAY, closed=[_CLOSED_8])
        fake.replies[prep.PERIODS] = [(409, {"ok": False, "code": "state_mismatch", "message": "总账已结账"})]
        status, detail = _run(fake, ex.EXGAIN_REPLAY_OPS, "arap_exgain_replay")
        self.assertEqual(status, "N/A")
        self.assertIn("前置步骤", detail)
        self.assertNotIn(ex.EXGAIN_CANCEL, [route for route, _fields in fake.calls])

    def test_non_test_lane_is_na(self) -> None:
        # 汇兑损益仍只限测试账套：账套未列进 testAccounts 时取消 403，记 N/A。
        refused = (403, {"ok": False, "code": "test_account_only", "message": "汇兑损益只对配置为测试账套的账套开放"})
        status, detail = _run(_exgain(cancel=refused), ex.EXGAIN_REPLAY_OPS, "arap_exgain_replay")
        self.assertEqual(status, "N/A")
        self.assertIn("testAccounts", detail)

    def test_partner_with_later_movement_is_skipped(self) -> None:
        # C2 在登记日期之后还有该币种的往来：桥按当前余额算，不能比，整个跳过（同之后还有核销的 C3）。
        fake = _exgain()
        fake.sql_answers.insert(0, ("a.dVouchDate >", [["C2"]]))
        status, detail = _run(fake, ex.EXGAIN_REPLAY_OPS, "arap_exgain_replay")
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(fake.sent(ex.EXGAIN_CANCEL)[0]["cancel_nos"], ["SYRAR01"])
        self.assertEqual(fake.sent(ex.EXGAIN)[0]["partners"], ["C1"])
        self.assertEqual(fake.sent(ex.VOUCHER)[0]["cancel_nos"], ["SYRAR11", "SYRAR12", "SYRAR02", "SYRAR03"])


# ---- 采购手工结算重做 ----

_HEAD = ["01|普通采购|01|V01|2026-09-30|2"]
_PAIR = ["101|201|2.000000|20.00|2.60|01", "102|202|1.000000|10.00|1.30|01"]
_RD = ["103|0|1.000000|0.00|0.00|", "104|0|-1.000000|0.00|0.00|"]
_RD_STATE = ["101|2.000000|20.00|10.000000", "102|1.000000|10.00|10.000000"]


def _settle(pick: dict[str, str], rd_after: list[str] | None = None, delete: tuple[int, dict] | None = None,
            invoices: list[str] | None = None, closed: list[list[str]] | None = None) -> _Fake:
    invoices = invoices if invoices is not None else ["301|0|||"]
    sql: list[tuple[str, list[list[str]]]] = [
        (needle, [[pick[shape]] if pick.get(shape) else []])
        for shape, needle in (("pair", "s.inv = 1"), ("rd", "s.nr >= 2"), ("inv", "s.ni >= 2"), ("multi", "s.inv >= 2"))
    ]
    sql += [
        ("m.iperiod BETWEEN", closed or [["9|0|0|0|0|0|0|0"]]),
        ("g.ccashier", [[_FREE]]),
        ("WHERE h.PBVID IN (", [invoices]),
        ("FROM dbo.PurSettleVouch AS h WHERE h.PSVID =", [_HEAD]),
        ("l.PSVID = 461", [_PAIR]),
        ("l.PSVID = 901", [_PAIR]),
        ("l.PSVID = 462", [_RD]),
        ("l.PSVID = 902", [_RD]),
        ("FROM dbo.rdrecords01 AS b", [_RD_STATE, rd_after or _RD_STATE]),
        ("FROM dbo.PurBillVouchs AS b INNER JOIN", [["201|2026-09-30|2026-09-30|2026-09-30"]]),
    ]
    replies = {
        "/v1/vouchers/delete": [delete or (200, {"ok": True})],
        "/v1/vouchers/create": [(200, {"ok": True, "id": 901}), (200, {"ok": True, "id": 902})],
        "/v1/vouchers/verify": [(200, {"ok": True})],
        prep.PERIODS: [(200, {"ok": True})],
        prep.DROP: [(200, {"ok": True})],
    }
    return _Fake(sql, replies)


class SettleReplayTests(unittest.TestCase):
    def test_replays_pair_and_rd_offset(self) -> None:
        fake = _settle({"pair": f"461|{_DAY}|2|0|0", "rd": f"462|{_DAY}|2|0|0"})
        status, detail = _run(fake, st.SETTLE_REPLAY_OPS, "settle_man_replay")
        self.assertEqual(status, "PASS", detail)
        self.assertEqual([fields["id"] for fields in fake.sent("/v1/vouchers/delete")], [461, 462])
        pair, rd = fake.sent("/v1/vouchers/create")
        self.assertEqual(pair["head"], {"settle_date": _DAY})
        self.assertEqual(pair["lines"][0], {"quantity": 2.0, "in_line_id": 101, "invoice_line_id": 201, "amount": 20.0})
        self.assertEqual(rd["lines"], [{"quantity": 1.0, "in_line_id": 103}, {"quantity": -1.0, "in_line_id": 104}])
        self.assertIn("跳过红蓝发票对冲", detail)

    def test_prepares_closed_month_and_audited_invoice(self) -> None:
        # 结算月采购已结账 → 取消采购结账（连同库存、存货核算、应付、总账）；发票已应付审核并制单 → 取消应付 8 月结账、
        # 取消制单、取消应付审核，再删除、重做。
        closed = [["9|0|0|0|0|0|0|0", "8|1|1|1|1|1|1|1"], ["9|0|0|0|0|0|0|0"]]
        fake = _settle({"pair": f"461|{_DAY}|2|1|1"}, invoices=["301|1|AP0000000000012|2026|8"], closed=closed)
        status, detail = _run(fake, st.SETTLE_REPLAY_OPS, "settle_man_replay")
        self.assertEqual(status, "PASS", detail)
        reopened = [(body["module"], body["period"]) for body in fake.sent(prep.PERIODS)]
        self.assertEqual(reopened, [("gl", 8), ("ap", 8), ("ia", 8), ("st", 8), ("pu", 8)])
        drop = fake.sent(prep.DROP)[0]
        self.assertEqual((drop["flag"], drop["pz_id"]), ("AP", "AP0000000000012"))
        verify = fake.sent("/v1/vouchers/verify")[0]
        self.assertEqual((verify["type"], verify["id"], verify["action"]), ("purchase_invoice", 301, "arap_unverify"))
        routes = [route for route, _fields in fake.calls]
        self.assertLess(routes.index("/v1/vouchers/verify"), routes.index("/v1/vouchers/delete"))

    def test_missing_gl_voucher_skips_to_next_candidate(self) -> None:
        # 第一张候选的发票带的凭证号在总账里没有（历史不一致）：只读预查后跳过，不做任何写入，换第二张。
        fake = _settle({})
        fake.sql_answers[0] = ("s.inv = 1", [[f"461|{_DAY}|2|0|1", f"463|{_DAY}|2|0|0"]])
        fake.sql_answers[:0] = [
            ("l.PSVID = 461)", [["301|1|AP0000000002279|2026|8"]]),
            ("g.ccashier", [[]]),
            ("l.PSVID = 463;", [_PAIR]),
        ]
        status, detail = _run(fake, st.SETTLE_REPLAY_OPS, "settle_man_replay")
        self.assertEqual(status, "PASS", detail)
        self.assertEqual([fields["id"] for fields in fake.sent("/v1/vouchers/delete")], [463])
        self.assertEqual(fake.sent(prep.PERIODS) + fake.sent("/v1/vouchers/verify") + fake.sent(prep.DROP), [])

    def test_all_candidates_unusable_is_na_with_counts(self) -> None:
        fake = _settle({"pair": f"461|{_DAY}|2|0|1"}, invoices=["301|1|AP0000000002279|2026|8"])
        fake.sql_answers[:0] = [("g.ccashier", [[]])]
        status, detail = _run(fake, st.SETTLE_REPLAY_OPS, "settle_man_replay")
        self.assertEqual(status, "N/A")
        self.assertIn("候选 1 张都不能用：发票的凭证在总账里读不到 1", detail)
        self.assertEqual(fake.sent("/v1/vouchers/delete"), [])

    def test_refused_preparation_skips_to_next_candidate(self) -> None:
        fake = _settle({}, closed=[["9|0|0|0|0|0|0|0", "8|1|1|1|1|1|1|1"]])
        fake.sql_answers[0] = ("s.inv = 1", [[f"461|{_DAY}|2|1|0", f"463|{_DAY}|2|0|0"]])
        fake.sql_answers[:0] = [("l.PSVID = 463;", [_PAIR])]
        fake.replies[prep.PERIODS] = [(409, {"ok": False, "code": "state_mismatch", "message": "存货核算已做期末处理"})]
        status, detail = _run(fake, st.SETTLE_REPLAY_OPS, "settle_man_replay")
        self.assertEqual(status, "PASS", detail)
        self.assertEqual([fields["id"] for fields in fake.sent("/v1/vouchers/delete")], [463])

    def test_receipt_price_mismatch_fails(self) -> None:
        changed = [_RD_STATE[0].replace("20.00", "21.00"), _RD_STATE[1]]
        fake = _settle({"pair": f"461|{_DAY}|2|0|0"}, rd_after=changed)
        status, detail = _run(fake, st.SETTLE_REPLAY_OPS, "settle_man_replay")
        self.assertEqual(status, "FAIL")
        self.assertIn("入库行", detail)

    def test_nothing_to_replay_is_na(self) -> None:
        fake = _settle({})
        status, _detail = _run(fake, st.SETTLE_REPLAY_OPS, "settle_man_replay")
        self.assertEqual(status, "N/A")
        self.assertEqual(fake.calls, [])

    def test_refused_delete_is_skipped(self) -> None:
        refused = (409, {"ok": False, "code": "state_mismatch", "message": "存货核算已记账，先取消记账"})
        fake = _settle({"pair": f"461|{_DAY}|2|0|0"}, delete=refused)
        status, detail = _run(fake, st.SETTLE_REPLAY_OPS, "settle_man_replay")
        self.assertEqual(status, "N/A")
        self.assertIn("存货核算已记账", detail)
        self.assertEqual(fake.sent("/v1/vouchers/create"), [])

    def test_test_account_gate_left_in_fails(self) -> None:
        fake = _settle({"pair": f"461|{_DAY}|2|0|0"})
        fake.replies["/v1/vouchers/create"] = [(403, {"ok": False, "code": "test_account_only", "message": "只对测试账套"})]
        status, detail = _run(fake, st.SETTLE_REPLAY_OPS, "settle_man_replay")
        self.assertEqual(status, "FAIL")
        self.assertIn("test_account_only", detail)


if __name__ == "__main__":
    unittest.main()
