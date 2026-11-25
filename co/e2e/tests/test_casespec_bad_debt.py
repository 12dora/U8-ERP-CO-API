"""坏账用例（bad_debt）的登记、分片、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import bad_debt as bad
from co.e2e.casespec.catalog import build_cases, known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.bad_debt_sql import (
    NULL,
    PARA_COLS,
    bad_debt_samples,
    bad_marks_sql,
    occur_rows_sql,
    para_delete_sql,
    para_insert_sql,
    para_restore_sql,
    para_sql,
    recover_snap_sql,
)
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("bad-debt-occur-cycle", "bad-debt-recover-cycle", "bad-debt-provision-cycle")
_SPEC = CaseSpec("bad-debt", "s")
_STEP = Step("custom")
_PZ = "AR0000000000007"
_NO = "HZAR0000000000001"
_NO2 = "HZAR0000000000002"
_MASTER = "C0001|D01|P01|112201|100201|01|600101"

# 参数行回读（14 列）：账套只有上一年度的行 → 补建 9 号行；已有本年度行的账套直接改。
_SAVED_OLD = "5|2025|0|~|~|~|~|~|~|~|~|~|~|~"
_ROW_NEW = "9|2026|1|9|0.00|0.00|~|~|~|~|3|0.005000|1231|6701"
_SAVED_OWN = "9|2026|1|9|5.00|~|~|~|~|~|3|0.005000|1231|6701"

_PARA = "OUTER APPLY (SELECT TOP (1) * FROM dbo.Ar_BadPara"
_INSERT = "DECLARE @id int"
_DELETE = "DELETE FROM dbo.Ar_BadPara"
_RESTORE = "UPDATE dbo.Ar_BadPara SET"
_GL = "N'JT' THEN 0"
_MARKS = "p.cPZID = N'"
_CCODE = "g.ccode, N'')"


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
        if isinstance(got, U8CoError):
            raise got
        return got

    def routes(self) -> list[str]:
        return [route.removeprefix("/v1/") for route, _fields in self.calls]

    def bodies(self, route: str) -> list[dict]:
        return [fields for name, fields in self.calls if name == route]


class _Sql:
    """按 SQL 里的特征串依次回答（每个键一个队列），没有匹配就报错。"""

    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]
        self.seen: list[str] = []

    def __call__(self, _env: object, query: str, _label: str) -> str:
        self.seen.append(query)
        for key, rows in self.answers:
            if key in query and rows:
                return "ROW:" + rows.pop(0) + "\n"
        raise AssertionError("没有准备回答：" + query[:120])

    def left(self) -> list[str]:
        return [key for key, rows in self.answers if rows]

    def hits(self, key: str) -> int:
        return sum(1 for query in self.seen if key in query)


def _env(client: object, bag: dict | None = None) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-02", bag=bag or {})


def _ok(**extra: object) -> dict:
    body: dict = {"ok": True}
    body.update(extra)
    return body


def _err(status: int, message: str, code: str = "state_mismatch") -> U8CoError:
    return U8CoError(status, code, message)


def _run(op: str, client: _Replay, sql: _Sql, bag: dict | None = None) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql):
        return bad.BAD_DEBT_OPS[op](_env(client, bag), _SPEC, _STEP)


_UNSET = _err(409, "未设置 2026 年坏账准备参数（应收款管理 › 设置 › 坏账准备）")
_VOUCHERED = _err(409, "已制单，请先取消制单")


def _voucher_sql(lines: list[str], gl: str, marks: tuple[str, str]) -> list[tuple[str, list[str]]]:
    return [(_GL, [gl]), (_CCODE, lines), (_MARKS, list(marks))]


# ---- 坏账发生 ----

_O_BEFORE = "5.00|5.00|100.00|0|0.00"
_O_AFTER = "15.00|15.00|90.00|1|-10.00"


def _occur_sql(back: str = _O_BEFORE, para_end: str = _SAVED_OLD) -> _Sql:
    rows = [
        (_PARA, [_SAVED_OLD, _ROW_NEW, para_end]),
        (_INSERT, ["9"]),
        ("CROSS APPLY (SELECT ISNULL(sb.iNatSum", ["C0001|26|SO1|11|50.00|112201"]),
        ("x.iMoneySum FROM dbo.SaleBillVouchs", [_O_BEFORE, _O_BEFORE, _O_AFTER, back]),
        ("N'9G' THEN 0 ELSE 1", ["1|10.00|10.00|0.00|0|1|1"]),
        ("g.ccus_id, N'')", ["-10.00|1|112201|112201"]),
        (_DELETE, ["1"]),
    ]
    rows += _voucher_sql(["10.00|1|1231|1231"], "2|1|0|10.00|10.00", ("1|1|1|0|2", "1|0|0|0|0"))
    return _Sql(rows)


def _occur_client(mixed: object = None, real: object = None) -> _Replay:
    occur = _ok(cancel_no=_NO, style="9G", amount=10.0, remain_before=0, remain_after=-10.0)
    return _Replay({
        "/v1/arap/bad_debt": [
            _UNSET, _err(409, "销售发票 SO1 行 11 的坏账金额超过可处理余额 50.00"), _ok(), real or occur,
            _ok(cancel_no=_NO2, style="9F", amount=0.5, remain_before=-10.0, remain_after=-9.5),
        ],
        "/v1/arap/process/voucher": [_ok(pz_id=_PZ), mixed or _err(409, "一次制单只能是同一种坏账处理（…）")],
        "/v1/arap/process/cancel": [_VOUCHERED, _ok(), _ok()],
        "/v1/arap/voucher/delete": [_ok()],
    })


class RegistrationTests(unittest.TestCase):
    def test_registered_with_ops_tags_routes(self) -> None:
        names = known_names()
        cases = {spec.name: spec for spec in build_cases({})}
        for name in _NAMES:
            with self.subTest(case=name):
                self.assertEqual(names.count(name), 1)
                spec = cases[name]
                self.assertEqual(spec.steps[0].sql_name, "arap_ctl")
                self.assertTrue(ops_run.has_op(spec.steps[-1].sql_name))
                self.assertTrue(spec.routes)
                self.assertIn("period:close", auto_tags(spec))
                self.assertIn("arap:bad_debt", auto_tags(spec))
        self.assertEqual(cases["bad-debt-recover-cycle"].needs, ("ar_master",))

    def test_conflicts(self) -> None:
        cases = {spec.name: spec for spec in build_cases({})}
        pairs = (
            ("bad-debt-occur-cycle", "bad-debt-recover-cycle"),
            ("bad-debt-occur-cycle", "ar-writeoff"),
            ("bad-debt-recover-cycle", "ar-receipt-cycle"),
            ("bad-debt-provision-cycle", "arap-exchange-gain-cycle"),
        )
        for left, right in pairs:
            with self.subTest(pair=(left, right)):
                self.assertTrue(conflicts(auto_tags(cases[left]), auto_tags(cases[right])))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in bad_debt_samples():
            with self.subTest(sample=name):
                self.assertEqual(sql_block_reason(query), "", name)
                self.assertIn((name, query), rows)
        self.assertIn("iYear = 2026", para_insert_sql(2026))
        self.assertIn("autoid = 100 AND iYear = 2026", para_delete_sql(100, 2026))
        self.assertIn("d.cCancelNo = N'HZAR0000000000001'", occur_rows_sql(_NO, 11, "112201"))

    def test_restore_sql_keeps_nulls(self) -> None:
        saved = dict.fromkeys(PARA_COLS, NULL)
        query = para_restore_sql(9, saved)
        self.assertIn("iJtAmount = NULL, dJtDate = NULL, cCancelNo = NULL, cProcStyle = NULL WHERE autoid = 9", query)
        saved.update(jt_amount="-3.25", jt_date="2026-09-30", cancel_no=_NO, proc_style="9F")
        query = para_restore_sql(9, saved)
        self.assertIn("iJtAmount = -3.25, dJtDate = N'2026-09-30', cCancelNo = N'HZAR0000000000001'", query)

    def test_sql_rejects_bad_values(self) -> None:
        saved = dict.fromkeys(PARA_COLS, NULL)
        bad_cases = (
            lambda: para_sql(26),
            lambda: para_delete_sql(0, 2026),
            lambda: para_restore_sql(9, dict(saved, cancel_no="HZ'1")),
            lambda: para_restore_sql(9, dict(saved, jt_amount="1.234")),
            lambda: para_restore_sql(9, dict(saved, jt_date="2026/09/30")),
            lambda: occur_rows_sql("YCFAP0001", 11, "112201"),
            lambda: occur_rows_sql(_NO, 11, "11'22"),
            lambda: recover_snap_sql(1, "SK'1", "C0001", 9),
            lambda: bad_marks_sql(_NO, "GL0001"),
        )
        for make in bad_cases:
            with self.assertRaises(ValueError):
                make()


class OccurTests(unittest.TestCase):
    def test_cycle_inserts_and_deletes_param_row(self) -> None:
        client, sql = _occur_client(), _occur_sql()
        status, detail = _run("bad_debt_occur", client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.routes(), [
            "arap/bad_debt", "arap/bad_debt", "arap/bad_debt", "arap/bad_debt", "arap/process/voucher",
            "arap/process/cancel", "arap/voucher/delete", "arap/bad_debt", "arap/process/voucher",
            "arap/process/cancel", "arap/process/cancel",
        ])
        probe, over, dry, real, prov = client.bodies("/v1/arap/bad_debt")
        self.assertEqual((probe["action"], probe["dry_run"]), ("provision", True))
        self.assertEqual(over["lines"], [{"type": "26", "id": "SO1", "line_id": 11, "amount": 1050.0}])
        self.assertIs(dry["dry_run"], True)
        self.assertEqual(real["lines"], [{"type": "26", "id": "SO1", "line_id": 11, "amount": 10.0}])
        self.assertNotIn("dry_run", real)
        self.assertEqual(prov["action"], "provision")
        self.assertEqual(client.bodies("/v1/arap/process/voucher")[1]["cancel_nos"], [_NO, _NO2])
        cancels = [body["cancel_no"] for body in client.bodies("/v1/arap/process/cancel")]
        self.assertEqual(cancels, [_NO, _NO2, _NO])
        self.assertEqual(sql.left(), [])

    def test_not_test_account_is_na_without_touching_anything(self) -> None:
        client = _Replay({"/v1/arap/bad_debt": [_err(403, "坏账处理只对配置为测试账套的账套开放", "test_account_only")]})
        sql = _Sql([])
        status, _detail = _run("bad_debt_occur", client, sql)
        self.assertEqual(status, "N/A")
        self.assertEqual(sql.seen, [])

    def test_mixed_voucher_accepted_fails_and_compensates(self) -> None:
        client = _occur_client(mixed=_ok(pz_id="AR0000000000008"))
        client.answers["/v1/arap/voucher/delete"].append(_ok())
        status, detail = _run("bad_debt_occur", client, _occur_sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("一起制单", detail)
        tail = client.calls[-3:]
        self.assertEqual(tail[0], ("/v1/arap/voucher/delete", tail[0][1]))
        self.assertEqual(tail[0][1]["pz_id"], "AR0000000000008")
        self.assertEqual([fields["cancel_no"] for _route, fields in tail[1:]], [_NO2, _NO])

    def test_wrong_rows_fail_cancel_and_delete_param_row(self) -> None:
        client = _occur_client()
        client.answers["/v1/arap/process/cancel"] = [_ok()]
        sql = _occur_sql()
        sql.answers[4] = ("N'9G' THEN 0 ELSE 1", ["1|10.00|10.00|0.00|0|1|0"])
        status, detail = _run("bad_debt_occur", client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("9G 行", detail)
        self.assertEqual(client.routes()[-1], "arap/process/cancel")
        self.assertEqual(sql.hits(_DELETE), 1)

    def test_not_restored_fails(self) -> None:
        status, detail = _run("bad_debt_occur", _occur_client(), _occur_sql(back=_O_AFTER))
        self.assertEqual(status, "FAIL")
        self.assertIn("取消后", detail)

    def test_param_row_left_behind_fails(self) -> None:
        status, detail = _run("bad_debt_occur", _occur_client(), _occur_sql(para_end=_ROW_NEW))
        self.assertEqual(status, "FAIL")
        self.assertIn("参数行没有复原", detail)

    def test_no_param_and_no_accounts_is_na(self) -> None:
        empty = "~|~|0|~|~|~|~|~|~|~|~|~|~|~"
        client = _Replay({"/v1/arap/bad_debt": [_UNSET]})
        sql = _Sql([(_PARA, [empty] * 4), (_INSERT, ["0"])])
        status, detail = _run("bad_debt_occur", client, sql)
        self.assertEqual(status, "N/A", detail)
        self.assertEqual(sql.hits(_DELETE), 0)


# ---- 坏账收回 ----

_R_BEFORE = "100.00|100.00|0|0|0.00|20.00|20.00|0|5.00"
_R_AFTER = "100.00|100.00|3|1|20.00|0.00|20.00|1|25.00"


def _recover_sql(after: str = _R_AFTER) -> _Sql:
    rows = [
        (_PARA, [_SAVED_OWN] * 4),
        ("AND h.cVouchType = N'48'", ["SK1|100201|20.00|1"]),
        ("d.cFlag = N'AR' AND d.cVouchType = N'48'", [_R_BEFORE, _R_BEFORE, after, _R_BEFORE]),
        ("fitemss98", ["0101"]),
        ("N'ROW:' + CONVERT(varchar(12), (SELECT COUNT(*) FROM dbo.Ap_CloseBill", ["0"]),
    ]
    lines = ["20.00|1|100201|100201", "-20.00|1|1231|1231"]
    rows += _voucher_sql(lines, "2|1|0|20.00|20.00", ("1|1|3|0|2", "1|0|0|0|0"))
    return _Sql(rows)


def _recover_client() -> _Replay:
    cash = _err(409, "科目 1231 的现金流量项目无法按数据来源唯一确定，请用 cash_items 指定，或在 U8 客户端制单")
    return _Replay({
        "/v1/arap/bad_debt": [
            _ok(), _err(409, "坏账收回金额须等于收款单金额 20.00"), _ok(),
            _ok(cancel_no=_NO, style="9H", amount=20.0, remain_before=5.0, remain_after=25.0),
        ],
        "/v1/vouchers/create": [_ok(id=501)],
        "/v1/arap/voucher": [_err(409, f"收款单 SK1 已用于坏账收回（处理号 {_NO}），请对坏账收回制单")],
        "/v1/arap/process/voucher": [cash, _ok(pz_id=_PZ)],
        "/v1/arap/process/cancel": [_VOUCHERED, _ok()],
        "/v1/arap/voucher/delete": [_ok()],
        "/v1/vouchers/delete": [_ok(deleted=True)],
    })


class RecoverTests(unittest.TestCase):
    def test_cycle_with_cash_item_retry(self) -> None:
        client, sql = _recover_client(), _recover_sql()
        status, detail = _run("bad_debt_recover", client, sql, {"ar_master": _MASTER})
        self.assertEqual(status, "PASS", detail)
        self.assertIn("1231→0101", detail)
        create = client.bodies("/v1/vouchers/create")[0]
        self.assertEqual((create["type"], create["lines"][0]["iAmt"]), ("ar_receipt", 20.0))
        _probe, wrong, _dry, real = client.bodies("/v1/arap/bad_debt")
        self.assertEqual(wrong["amount"], 21.0)
        self.assertEqual((real["action"], real["customer"], real["receipt"], real["amount"]), ("recover", "C0001", "SK1", 20.0))
        doc = client.bodies("/v1/arap/voucher")[0]
        self.assertEqual((doc["flag"], doc["type"], doc["id"]), ("AR", "ar_receipt", 501))
        retry = client.bodies("/v1/arap/process/voucher")[1]
        self.assertEqual(retry["cash_items"], {"1231": "0101"})
        self.assertEqual(client.routes()[-2:], ["arap/process/cancel", "vouchers/delete"])
        self.assertEqual(client.bodies("/v1/vouchers/delete")[0]["id"], 501)
        self.assertEqual(sql.hits(_RESTORE), 0)
        self.assertEqual(sql.left(), [])

    def test_missing_master_is_na(self) -> None:
        client = _Replay({"/v1/arap/bad_debt": [_ok()]})
        sql = _Sql([(_PARA, [_SAVED_OWN] * 4)])
        status, _detail = _run("bad_debt_recover", client, sql)
        self.assertEqual(status, "N/A")
        self.assertEqual(client.routes(), ["arap/bad_debt"])

    def test_balance_changed_fails_and_compensates(self) -> None:
        client = _recover_client()
        status, detail = _run("bad_debt_recover", client, _recover_sql(after=_R_AFTER.replace("100.00|100.00", "80.00|80.00")),
                              {"ar_master": _MASTER})
        self.assertEqual(status, "FAIL")
        self.assertIn("收回后", detail)
        self.assertEqual(client.routes()[-2:], ["arap/process/cancel", "vouchers/delete"])


# ---- 计提坏账准备 ----

_P_AFTER = "9|2026|1|9|12.50|7.50|2026-10-02|~|HZAR0000000000004|9F|3|0.005000|1231|6701"
_P_BACK = "9|2026|1|9|5.00|~|~|~|HZAR0000000000004|9F|3|0.005000|1231|6701"


def _provision_sql(back: str = _P_BACK) -> _Sql:
    rows = [
        (_PARA, [_SAVED_OWN, _SAVED_OWN, _SAVED_OWN, _P_AFTER, back, back, _SAVED_OWN]),
        (_RESTORE, ["1"]),
    ]
    lines = ["7.50|1|6701|6701", "-7.50|1|1231|1231"]
    rows += _voucher_sql(lines, "2|1|0|7.50|7.50", ("0|0|0|1|2", "0|0|0|0|0"))
    return _Sql(rows)


def _provision_client(amount: float = 7.5) -> _Replay:
    real = _ok(cancel_no="HZAR0000000000004", style="9F", amount=amount, remain_before=5.0, remain_after=5.0 + amount,
               base=2500.0, rate=0.005, target=12.5, style_name="销售收入百分比法")
    return _Replay({
        "/v1/arap/bad_debt": [_ok(), _ok(), real],
        "/v1/arap/process/voucher": [_ok(pz_id=_PZ)],
        "/v1/arap/process/cancel": [_VOUCHERED, _ok()],
        "/v1/arap/voucher/delete": [_ok()],
    })


class ProvisionTests(unittest.TestCase):
    def test_cycle_restores_leftover_columns(self) -> None:
        client, sql = _provision_client(), _provision_sql()
        status, detail = _run("bad_debt_provision", client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.routes(), [
            "arap/bad_debt", "arap/bad_debt", "arap/bad_debt", "arap/process/voucher", "arap/process/cancel",
            "arap/voucher/delete", "arap/process/cancel",
        ])
        restore = next(query for query in sql.seen if _RESTORE in query)
        self.assertIn("cCancelNo = NULL, cProcStyle = NULL WHERE autoid = 9", restore)
        self.assertEqual(sql.left(), [])

    def test_wrong_amount_fails(self) -> None:
        sql = _provision_sql()
        status, detail = _run("bad_debt_provision", _provision_client(amount=7.4), sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("本次计提", detail)

    def test_zero_is_na(self) -> None:
        client = _Replay({"/v1/arap/bad_debt": [_ok(), _err(409, "本次计提金额为 0")]})
        sql = _Sql([(_PARA, [_SAVED_OWN] * 4)])
        status, _detail = _run("bad_debt_provision", client, sql)
        self.assertEqual(status, "N/A")

    def test_already_provisioned_is_na(self) -> None:
        done = _SAVED_OWN.replace("5.00|~|~", "5.00|5.00|2026-03-31")
        client = _Replay({"/v1/arap/bad_debt": [_ok()]})
        status, detail = _run("bad_debt_provision", client, _Sql([(_PARA, [done] * 4)]))
        self.assertEqual(status, "N/A")
        self.assertIn("已计提过", detail)

    def test_cancel_not_resetting_fails(self) -> None:
        status, detail = _run("bad_debt_provision", _provision_client(), _provision_sql(back=_P_AFTER))
        self.assertEqual(status, "FAIL")
        self.assertIn("取消计提后", detail)


if __name__ == "__main__":
    unittest.main()
