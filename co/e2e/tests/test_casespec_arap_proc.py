"""应收应付处理用例（arap_proc / arap_proc_more）的登记、分片、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import arap_proc as proc
from co.e2e.casespec.catalog import build_cases, known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arap_proc_more_sql import arap_proc_more_samples, blue_pick_sql, fx_rows_sql, fx_snap_sql
from co.e2e.casespec.arap_proc_sql import arap_proc_samples, gl_net_sql, marks_sql, proc_rows_sql, transfer_pick_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = (
    "arap-transfer-cycle",
    "arap-transfer-cycle-ap",
    "arap-merge-cycle",
    "arap-red-offset-cycle",
    "arap-exchange-gain-cycle",
    "arap-exchange-gain-cycle-ap",
)
_SPEC = CaseSpec("arap-proc", "s")
_STEP = Step("custom")
_PZ = "AR0000000000007"
_PZ_KEY = "d.cGLSign IS NOT NULL"


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


class _Sql:
    """按 SQL 里的特征串依次回答（每个键一个队列），没有匹配就报错。"""

    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]

    def __call__(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.answers:
            if key in query and rows:
                return "ROW:" + rows.pop(0) + "\n"
        raise AssertionError("没有准备回答：" + query[:120])

    def left(self) -> list[str]:
        return [key for key, rows in self.answers if rows]


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-02", bag={})


def _ok(**extra: object) -> dict:
    body: dict = {"ok": True}
    body.update(extra)
    return body


def _run(op: str, client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql):
        return proc.ARAP_PROC_OPS[op](_env(client), _SPEC, _STEP)


# ---- 应收冲应付 / 应付冲应收 ----

_T_PICK = "C0001|SO1|11|S0001|PB1|21"
_T_BEFORE = "5.00|3.00|20.00|30.00|0"
_T_AFTER = "6.00|4.00|19.00|29.00|2"
_GL = "COUNT(DISTINCT CONVERT(varchar(4), g.iperiod)"


def _transfer_sql(flag: str, back: str = _T_BEFORE, after: str = _T_AFTER, dry: str = _T_BEFORE) -> _Sql:
    digest = "应收冲应付 供应商1" if flag == "AR" else "应付冲应收 客户1"
    rows: list[tuple[str, list[str]]] = [
        ("CROSS JOIN p", [_T_PICK]),
        ("y.iOriTotal FROM dbo.PurBillVouchs", [_T_BEFORE, dry, after, back]),
        ("REPLACE(ISNULL(d.cDigest", ["1|0.00|1.00|1|1.00|0.00|0|" + digest]),
    ]
    if flag == "AR":
        rows += [
            (_GL, ["2|1|0|1.00|1.00"]),
            ("g.csup_id, N'')", ["1.00|1|220201|220201"]),
            ("g.ccus_id, N'')", ["-1.00|1|11229901|11229901"]),
            (_PZ_KEY, ["2|2|2|2", "2|0|0|0"]),
        ]
    return _Sql(rows)


def _transfer_client(flag: str, cancel_after_voucher: object = None) -> _Replay:
    no = "YCFAP000000000001" if flag == "AR" else "FCYAR000000000001"
    refused = U8CoError(409, "state_mismatch", "已制单，请先取消制单")
    cancels = [refused if cancel_after_voucher is None else cancel_after_voucher, _ok()] if flag == "AR" else [_ok()]
    return _Replay({
        "/v1/arap/transfer": [_ok(cancel_no=no), _ok(cancel_no=no)],
        "/v1/arap/process/voucher": [_ok(pz_id=_PZ)],
        "/v1/arap/process/cancel": cancels,
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
                self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
                self.assertTrue(spec.routes)
                self.assertIn("period:close", auto_tags(spec))

    def test_conflicts_with_cases_on_same_seed_docs(self) -> None:
        cases = {spec.name: spec for spec in build_cases({})}
        pairs = (
            ("arap-transfer-cycle", "ar-writeoff"),
            ("arap-transfer-cycle-ap", "ap-writeoff"),
            ("arap-merge-cycle", "ar-voucher"),
            ("arap-red-offset-cycle", "sale-red-invoice"),
            ("arap-exchange-gain-cycle", "ar-writeoff-fx"),
            ("arap-exchange-gain-cycle-ap", "ap-writeoff-fx"),
        )
        for left, right in pairs:
            with self.subTest(pair=(left, right)):
                self.assertTrue(conflicts(auto_tags(cases[left]), auto_tags(cases[right])))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in arap_proc_samples() + arap_proc_more_samples():
            with self.subTest(sample=name):
                self.assertEqual(sql_block_reason(query), "", name)
                self.assertIn((name, query), rows)
        self.assertIn("m.iyear = 2026 AND m.iperiod = 10", transfer_pick_sql("2026-10-02"))
        self.assertIn("d.cCancelNo = N'YCFAP000000000001'", proc_rows_sql("YCFAP000000000001", "9I"))
        self.assertIn(">= 12.50", blue_pick_sql("C0001", Decimal("12.5"), "2026-10-02"))

    def test_sql_rejects_bad_values(self) -> None:
        bad = (
            lambda: transfer_pick_sql("2026/10/02"),
            lambda: proc_rows_sql("XXAP0001", "9I"),
            lambda: proc_rows_sql("YCFAP0001", "9X"),
            lambda: marks_sql((), _PZ),
            lambda: marks_sql(("YCFAP0001",), "GL0001"),
            lambda: gl_net_sql(_PZ, "cdept", "D1"),
            lambda: gl_net_sql(_PZ, "ccus_id", "C'1"),
            lambda: blue_pick_sql("C0001", Decimal("1.234"), "2026-10-02"),
            lambda: fx_snap_sql("AR", "C0001", "美元'"),
            lambda: fx_rows_sql("AX", ("SYRAR0001",)),
        )
        for make in bad:
            with self.assertRaises(ValueError):
                make()

    def test_transfer_pick_takes_each_side_independently(self) -> None:
        sql = transfer_pick_sql("2026-10-02")
        self.assertEqual(sql_block_reason(sql), "")
        self.assertEqual(sql.count("SELECT TOP (1) LTRIM(RTRIM("), 2)  # 两侧各取一行
        self.assertNotIn("CROSS JOIN p\n", sql)  # 不做全部销售行 × 全部采购行
        self.assertIn("FROM sp CROSS JOIN pp", sql)  # 两侧各一行
        sale, pur = sql.split("), sp AS (", 1)[1].split("), pp AS (", 1)
        self.assertIn("ORDER BY sh.SBVID DESC", sale)
        self.assertIn("sh.cCusCode = s.dw", sale)
        self.assertIn("g.cVouchType = N'26'", sale)
        self.assertNotIn("ph.", sale)
        self.assertIn("ORDER BY ph.PBVID DESC", pur)
        self.assertIn("ph.cVenCode = p.dw", pur)
        self.assertIn("ISNULL(ph.iNetLock, 0) = 0", pur)
        self.assertIn("g.cVouchType = N'01'", pur)
        self.assertNotIn("sh.", pur)
        self.assertIn("m.iyear = 2026 AND m.iperiod = 10", pur.rsplit("FROM sp CROSS JOIN pp", 1)[1])


class TransferTests(unittest.TestCase):
    def test_ar_cycle_with_voucher(self) -> None:
        client, sql = _transfer_client("AR"), _transfer_sql("AR")
        status, detail = _run("arap_transfer_ar", client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.routes(), [
            "arap/transfer", "arap/transfer", "arap/process/voucher", "arap/process/cancel", "arap/voucher/delete",
            "arap/process/cancel",
        ])
        dry, real = client.calls[0][1], client.calls[1][1]
        self.assertIs(dry["dry_run"], True)
        self.assertNotIn("dry_run", real)
        self.assertEqual(real["ar_lines"], [{"type": "26", "id": "SO1", "line_id": 11, "amount": 1.0}])
        self.assertEqual(real["ap_lines"], [{"type": "01", "id": "PB1", "line_id": 21, "amount": 1.0}])
        self.assertEqual(client.calls[2][1]["cancel_nos"], ["YCFAP000000000001"])
        self.assertEqual(client.calls[4][1]["pz_id"], _PZ)
        self.assertEqual(sql.left(), [])

    def test_ap_cycle_without_voucher(self) -> None:
        client = _transfer_client("AP")
        status, detail = _run("arap_transfer_ap", client, _transfer_sql("AP"))
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.routes(), ["arap/transfer", "arap/transfer", "arap/process/cancel"])
        self.assertEqual(client.calls[2][1]["cancel_no"], "FCYAR000000000001")

    def test_not_restored_fails(self) -> None:
        client = _transfer_client("AP")
        status, detail = _run("arap_transfer_ap", client, _transfer_sql("AP", back="5.00|3.00|20.00|30.00|2"))
        self.assertEqual(status, "FAIL")
        self.assertIn("取消处理后", detail)

    def test_dry_run_that_writes_fails_before_real_call(self) -> None:
        client = _transfer_client("AR")
        status, detail = _run("arap_transfer_ar", client, _transfer_sql("AR", dry="6.00|4.00|19.00|29.00|2"))
        self.assertEqual(status, "FAIL")
        self.assertIn("dry_run", detail)
        self.assertEqual(client.routes(), ["arap/transfer"])

    def test_wrong_totals_fail_and_compensate(self) -> None:
        client = _transfer_client("AR")
        status, detail = _run("arap_transfer_ar", client, _transfer_sql("AR", after="5.00|4.00|19.00|29.00|2"))
        self.assertEqual(status, "FAIL")
        self.assertIn("处理后", detail)
        # 补偿：按处理号取消（第一个取消的回答是 409，记清理失败）。
        self.assertEqual(client.routes()[-1], "arap/process/cancel")
        self.assertEqual(client.calls[-1][1]["cancel_no"], "YCFAP000000000001")

    def test_cancel_while_vouchered_must_be_refused(self) -> None:
        client = _transfer_client("AR", cancel_after_voucher=_ok())
        status, detail = _run("arap_transfer_ar", client, _transfer_sql("AR"))
        self.assertEqual(status, "FAIL")
        self.assertIn("409", detail)
        # 取消处理的清理动作已去掉，只剩取消制单。
        self.assertEqual(client.routes()[-1], "arap/voucher/delete")

    def test_nothing_to_pick_is_na(self) -> None:
        client = _transfer_client("AR")
        status, _detail = _run("arap_transfer_ar", client, _Sql([("CROSS JOIN p", [""])]))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])


class MergeTests(unittest.TestCase):
    def _sql(self, back: str = "5.00|0.00|0") -> _Sql:
        return _Sql([
            ("LTRIM(RTRIM(c.cCusCode)) AS code", ["C0001|SO1|11|C0002"]),
            ("WHERE cProcStyle = N'BZ'", ["5.00|0.00|0", "4.00|1.00|2", back]),
            ("COUNT(DISTINCT d.iBVid)", ["2|-1.00|1.00|1|11"]),
            (_GL, ["2|1|0|1.00|1.00"]),
            ("g.ccus_id, N'')", ["-1.00|1|11229901|11229901", "1.00|1|11229901|11229901"]),
            (_PZ_KEY, ["2|2|2|2", "2|0|0|0"]),
        ])

    def _client(self) -> _Replay:
        return _Replay({
            "/v1/arap/merge": [_ok(cancel_no="BZAR0000000000001")],
            "/v1/arap/process/voucher": [_ok(pz_id=_PZ)],
            "/v1/arap/voucher/delete": [_ok()],
            "/v1/arap/process/cancel": [_ok()],
        })

    def test_cycle(self) -> None:
        client, sql = self._client(), self._sql()
        status, detail = _run("arap_merge", client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.calls[0][1]["lines"], [{"type": "26", "id": "SO1", "line_id": 11, "amount": 1.0}])
        self.assertEqual((client.calls[0][1]["from"], client.calls[0][1]["to"]), ("C0001", "C0002"))
        self.assertEqual(client.routes(), ["arap/merge", "arap/process/voucher", "arap/voucher/delete", "arap/process/cancel"])
        self.assertEqual(sql.left(), [])

    def test_not_restored_fails(self) -> None:
        status, detail = _run("arap_merge", self._client(), self._sql(back="4.00|1.00|0"))
        self.assertEqual(status, "FAIL")
        self.assertIn("取消并账后", detail)


# ---- 红票对冲 ----


def _red_sql(gone: str = "0|0") -> _Sql:
    return _Sql([
        ("fretqtyykp", ["31|41|C0001"]),
        ("MIN(x.iDLsID)", ["52"]),
        ("SELECT MIN(z.AutoID)", ["26|SOX2|62|10.00"]),
        ("ORDER BY sh.SBVID DESC, s.line", ["26|SOX1|71"]),
        ("WHERE cProcStyle = N'9N'", ["0.00|5.00|-10.00|20.00|0", "-10.00|15.00|0.00|10.00|2", "0.00|5.00|-10.00|20.00|0"]),
        ("d.cProcStyle = N'9N'", ["2|10.00|-10.00"]),
        ("FROM dbo.DispatchList AS h WHERE h.DLID", [gone]),
    ])


def _red_client(offset: object) -> _Replay:
    return _Replay({
        "/v1/vouchers/generate": [_ok(id=51), _ok(id=61)],
        "/v1/vouchers/verify": [_ok() for _ in range(6)],
        "/v1/vouchers/delete": [_ok(deleted=True), _ok(deleted=True)],
        "/v1/arap/red_offset": [offset],
        "/v1/arap/process/cancel": [_ok()],
    })


class RedOffsetTests(unittest.TestCase):
    def test_cycle_and_cleanup(self) -> None:
        client, sql = _red_client(_ok(cancel_no="HRAR0000000000001")), _red_sql()
        status, detail = _run("arap_red_offset", client, sql)
        self.assertEqual(status, "PASS", detail)
        body = next(fields for route, fields in client.calls if route == "/v1/arap/red_offset")
        self.assertEqual(body["red"], [{"type": "26", "id": "SOX2", "line_id": 62, "amount": 10.0}])
        self.assertEqual(body["blue"], [{"type": "26", "id": "SOX1", "line_id": 71, "amount": 10.0}])
        self.assertEqual(body["partner"], "C0001")
        tail = [(route.rsplit("/", 1)[1], fields.get("type"), fields.get("action", "")) for route, fields in client.calls[6:]]
        self.assertEqual(tail, [
            ("cancel", None, ""),
            ("verify", "sale_invoice", "arap_unverify"), ("verify", "sale_invoice", "unverify"),
            ("delete", "sale_invoice", ""), ("verify", "sale_return", "unverify"), ("delete", "sale_return", ""),
        ])
        ret = client.calls[0][1]
        self.assertEqual((ret["type"], ret["head"], ret["lines"]), ("sale_return", {"invoiced": True},
                                                                      [{"source_line_id": 41, "quantity": 1}]))
        self.assertEqual(sql.left(), [])

    def test_missing_route_is_na_and_cleans_up(self) -> None:
        client = _red_client(U8CoError(404, "not_found", "未知路径"))
        status, detail = _run("arap_red_offset", client, _red_sql())
        self.assertEqual(status, "N/A", detail)
        self.assertEqual(client.answers["/v1/vouchers/delete"], [])
        self.assertEqual(client.answers["/v1/arap/process/cancel"], [_ok()])

    def test_leftover_docs_fail(self) -> None:
        status, detail = _run("arap_red_offset", _red_client(_ok(cancel_no="HRAR0000000000001")), _red_sql("1|0"))
        self.assertEqual(status, "FAIL")
        self.assertIn("已删除", detail)


# ---- 汇兑损益 ----

_FX_BATCHES = [
    {"cancel_no": "SYRAR0000000001", "partner": "C0001", "type": "R0", "id": "YS1", "diff": 20.0},
    {"cancel_no": "SYRAR0000000002", "partner": "C0001", "type": "48", "id": "SK1", "diff": -5.0},
]


_FX_AFTER = {"AR": "2|162.00|20.00|147.00|20.00|0.00", "AP": "2|122.00|20.00|137.00|20.00|0.00"}


def _fx_sql(side: str, back: str = "0|142.00|20.00|142.00|20.00|0.00") -> _Sql:
    rows: list[tuple[str, list[str]]] = [
        ("MAX(d.iExchRate) AS rate", ["C0001|美元|7.100000" if side == "AR" else "S0001|美元|7.100000"]),
        # 借方单据加 diff、贷方单据减 diff：应收 R0 +20、收款单 -(-5)；应付 P0 -20、付款单 +(-5)。
        ("SUM(v.iRAmount_f)", ["0|142.00|20.00|142.00|20.00|0.00", _FX_AFTER[side], back]),
        ("N'9M' AND d.cCancelNo IN", ["2|15.00|0.00"]),
    ]
    if side == "AR":
        rows += [
            ("6603%", ["660301", "660301"]),
            (_GL, ["2|1|0|15.00|15.00"]),
            ("g.ccode, N'')", ["15.00|1|660301|660301"]),
            (_PZ_KEY, ["2|2|2|2", "2|0|0|0"]),
        ]
    return _Sql(rows)


def _fx_client(side: str, calc: object = None) -> _Replay:
    batches = [dict(item, cancel_no=item["cancel_no"].replace("SYRAR", "SYRAR" if side == "AR" else "SYPAP"),
                    type=item["type"] if side == "AR" else {"R0": "P0", "48": "49"}[item["type"]]) for item in _FX_BATCHES]
    return _Replay({
        "/v1/arap/exchange_gain": [calc if calc is not None else _ok(batches=batches, total=15.0)],
        "/v1/arap/process/voucher": [_ok(pz_id=_PZ)],
        "/v1/arap/voucher/delete": [_ok()],
        "/v1/arap/exchange_gain/cancel": [_ok()],
    })


class ExchangeGainTests(unittest.TestCase):
    def test_ar_cycle_with_voucher(self) -> None:
        client, sql = _fx_client("AR"), _fx_sql("AR")
        status, detail = _run("arap_exgain_ar", client, sql)
        self.assertEqual(status, "PASS", detail)
        calc = client.calls[0][1]
        self.assertEqual((calc["flag"], calc["currency"], calc["rate"], calc["partners"]), ("AR", "美元", 8.1, ["C0001"]))
        self.assertEqual(client.calls[1][1]["pl_code"], "660301")
        self.assertEqual(client.routes(), [
            "arap/exchange_gain", "arap/process/voucher", "arap/voucher/delete", "arap/exchange_gain/cancel",
        ])
        self.assertEqual(client.calls[-1][1]["cancel_nos"], ["SYRAR0000000001", "SYRAR0000000002"])
        self.assertEqual(sql.left(), [])

    def test_ap_cycle(self) -> None:
        client = _fx_client("AP")
        status, detail = _run("arap_exgain_ap", client, _fx_sql("AP"))
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.routes(), ["arap/exchange_gain", "arap/exchange_gain/cancel"])
        self.assertEqual(client.calls[-1][1]["cancel_nos"], ["SYPAP0000000001", "SYPAP0000000002"])

    def test_not_test_account_is_na(self) -> None:
        client = _fx_client("AP", U8CoError(403, "test_account_only", "汇兑损益只对配置为测试账套的账套开放"))
        status, _detail = _run("arap_exgain_ap", client, _fx_sql("AP"))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.routes(), ["arap/exchange_gain"])

    def test_not_restored_fails(self) -> None:
        status, detail = _run("arap_exgain_ap", _fx_client("AP"), _fx_sql("AP", back=_FX_AFTER["AP"]))
        self.assertEqual(status, "FAIL")
        self.assertIn("取消汇兑损益后", detail)


if __name__ == "__main__":
    unittest.main()
