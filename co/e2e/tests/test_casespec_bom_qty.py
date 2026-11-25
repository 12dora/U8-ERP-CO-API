"""物料清单用量精度用例（bom_qty，`bom-load-qty`）的登记、分片、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import bom_qty as bm
from co.e2e.casespec.catalog import build_cases, known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.bom_qty_sql import (
    MEMO,
    bom_more_samples,
    gone_sql,
    head_sql,
    line_sql,
    orphan_sql,
    safe_date,
    safe_seq,
)
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "bom-load-qty"
_BAG = {"bomw_parent": "P001", "bomw_c1": "C001", "bomw_c2": "C002", "bomw_c3": "C003", "bomw_ver": "20"}
_EFF1 = "2035-01-09"  # 2026-10-02 + 3021 天
_EFF2 = "2035-01-16"  # 2026-10-02 + 3028 天
_LINE10 = "c.SortSeq = 10"
_LINE20 = "c.SortSeq = 20"
_LINE30 = "c.SortSeq = 30"
_HEAD = "b.Status)"
_DIGITS = "iStrsQuanDecDgt"
_AUX = "iGroupType"
_MAX = "MAX(b.BomId), 0)) AS v FROM"
_GONE = "(SELECT COUNT(*) FROM dbo.bom_bom AS b"
_ORPHAN = "b.VersionEffDate = CONVERT"


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

    def bodies(self, route: str) -> list[dict]:
        return [fields for name, fields in self.calls if name == route]


class _Sql:
    """按 SQL 里的特征串依次回答（每个键一个队列），没有匹配就报错。"""

    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]

    def __call__(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.answers:
            if key in query and rows:
                return rows.pop(0)
        raise AssertionError("没有准备回答：" + query[:120])

    def left(self) -> list[str]:
        return [key for key, rows in self.answers if rows]


def _rows(*cells: str) -> list[str]:
    return [f"ROW:{cell}\n" for cell in cells]


def _env(client: object, bag: dict | None = None) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="op001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-02",
                           bag=dict(_BAG) if bag is None else bag)


def _ok(**extra: object) -> dict:
    body: dict = {"ok": True}
    body.update(extra)
    return body


def _load() -> dict:
    return _ok(lines=[{"sort_seq": "10", "base_qty_n": "1.234567000"}, {"sort_seq": "20", "base_qty_n": "1"}])


def _sql(aux: list[str] | None = None, line30: str = "1.000000|1.000000|0|1||" + _EFF1, creator: str = "op001") -> _Sql:
    heads = [f"1|{creator}||{_EFF1}|0.000|1|2", f"1|{creator}|op001|{_EFF1}|0.000|1|2", f"1|{creator}|op001|{_EFF2}|0.000|1|2"]
    if aux:
        heads = [head[:-1] + "3" for head in heads]
    return _Sql([
        (_DIGITS, _rows("2")),
        (_AUX, ["".join(_rows(*aux))] if aux else ["(0 rows)\n"]),
        (_MAX, _rows("1000000050")),
        (_HEAD, _rows(*heads)),
        (_LINE10, _rows(f"1.234567000|3.000000|2.500|1||{_EFF1}", f"2.500000|3.000000|5.000|1||{_EFF1}",
                        f"2.500000|3.000000|5.000|1||{_EFF2}")),
        (_LINE20, _rows(f"1.000000|1.000000|0.000|1||{_EFF1}", f"1.000000|1.000000|0.000|1||{_EFF2}")),
        (_LINE30, _rows(line30) if aux else []),
        (_GONE, _rows("0")),
    ])


def _client(**over: list) -> _Replay:
    answers = {
        "/v1/vouchers/create": [_ok(id=1000000053)],
        "/v1/vouchers/load": [_load()],
        "/v1/vouchers/update": [_ok(), _ok()],
        "/v1/vouchers/delete": [_ok(deleted=True)],
    }
    answers.update(over)
    return _Replay(answers)


def _run(client: _Replay, sql: _Sql, bag: dict | None = None) -> tuple[str, str]:
    with mock.patch.object(bm, "run_guarded", sql):
        return bm.bom_load_qty(_env(client, bag), CaseSpec(_NAME, "t"), Step("custom"))


class RegistrationTests(unittest.TestCase):
    def test_registered_once(self) -> None:
        self.assertEqual(known_names().count(_NAME), 1)
        spec = {spec.name: spec for spec in build_cases({})}[_NAME]
        self.assertTrue(spec.optional and spec.requires_login and spec.routes)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))

    def test_conflicts_with_other_bom_cases(self) -> None:
        cases = {spec.name: spec for spec in build_cases({})}
        for other in ("bom-lifecycle", "bom-create-bad", "mo-create-probe"):
            with self.subTest(other=other):
                self.assertTrue(conflicts(auto_tags(cases[_NAME]), auto_tags(cases[other])))

    def test_samples_pass_the_guard(self) -> None:
        names = {name for name, _query in sample_sql()}
        for name, query in bom_more_samples():
            with self.subTest(sample=name):
                self.assertIn(name, names)
                self.assertEqual(sql_block_reason(query), "")

    def test_sql_rejects_bad_values(self) -> None:
        with self.assertRaises(ValueError):
            head_sql("1;DROP")
        with self.assertRaises(ValueError):
            line_sql("100", 0)
        with self.assertRaises(ValueError):
            safe_seq(True)
        with self.assertRaises(ValueError):
            orphan_sql("100", "2035-01-09' OR 1=1")
        with self.assertRaises(ValueError):
            gone_sql("0")
        self.assertEqual(safe_date(_EFF1), _EFF1)
        self.assertIn(MEMO, head_sql("100"))
        self.assertIn("b.BomId > 0", orphan_sql("0", _EFF1))


class CycleTests(unittest.TestCase):
    def test_full_cycle_passes_and_records_observations(self) -> None:
        client, sql = _client(), _sql()
        status, detail = _run(client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(sql.left(), [])
        for text in ("iStrsQuanDecDgt=2", "1.234567000", "CreateUser 是操作员编码", "表头保持", _EFF2, "已删除"):
            self.assertIn(text, detail)
        created = client.bodies("/v1/vouchers/create")[0]
        self.assertEqual(created["head"]["eff_date"], _EFF1)
        self.assertEqual(created["lines"][0]["base_qty_n"], 1.234567)
        self.assertEqual(created["lines"][0]["comp_scrap"], 2.5)
        first, second = client.bodies("/v1/vouchers/update")
        self.assertNotIn("head", first)
        self.assertEqual(first["lines"], [{"op": "update", "sort_seq": 10, "base_qty_n": 2.5, "comp_scrap": 5.0}])
        self.assertEqual(second["head"], {"eff_date": _EFF2})
        self.assertNotIn("lines", second)
        self.assertEqual(client.bodies("/v1/vouchers/delete")[0]["id"], 1000000053)

    def test_creator_name_is_only_recorded(self) -> None:
        status, detail = _run(_client(), _sql(creator="张三"))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("CreateUser=张三（不是操作员编码）", detail)

    def test_aux_candidate_filled_expects_409_before_edit(self) -> None:
        refused = U8CoError(409, "state_mismatch", "第 30 行带辅计量，用量请在 U8 客户端修改")
        client = _client(**{"/v1/vouchers/update": [refused, _ok(), _ok()]})
        sql = _sql(aux=["C001", "A001"], line30=f"1.000000|1.000000|0|1|BOX|{_EFF1}")
        status, detail = _run(client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("辅计量候选 A001：U8 填了辅计量单位 BOX", detail)
        self.assertIn("辅计量行改用量 409", detail)
        created = client.bodies("/v1/vouchers/create")[0]
        self.assertEqual([line["inv_code"] for line in created["lines"]], ["C001", "C002", "A001"])
        self.assertEqual(client.bodies("/v1/vouchers/update")[0]["lines"][0]["sort_seq"], 30)

    def test_aux_candidate_not_filled_skips_refusal(self) -> None:
        client = _client()
        status, detail = _run(client, _sql(aux=["A001"]))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("未填辅计量单位", detail)
        self.assertEqual(len(client.bodies("/v1/vouchers/update")), 2)

    def test_rounded_qty_fails_and_deletes(self) -> None:
        sql = _sql()
        sql.answers[4] = (_LINE10, _rows(f"1.230000|3.000000|2.500|1||{_EFF1}"))
        client = _client(**{"/v1/vouchers/update": []})
        status, detail = _run(client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("1.230000", detail)
        self.assertIn("已删除 1000000053", detail)
        self.assertEqual(client.calls[-1][0], "/v1/vouchers/delete")

    def test_head_changed_by_line_edit_fails(self) -> None:
        sql = _sql()
        sql.answers[3] = (_HEAD, _rows(f"1|op001||{_EFF1}|0.000|1|2", f"1|op001|op001|{_EFF1}|0.000|0|2"))
        status, detail = _run(_client(**{"/v1/vouchers/update": [_ok()]}), sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("说明保持 0", detail)

    def test_create_refused_looks_for_orphan(self) -> None:
        client = _client(**{"/v1/vouchers/create": [U8CoError(504, "outcome_unknown", "未能回读")]})
        sql = _Sql([(_DIGITS, _rows("2")), (_AUX, ["(0 rows)\n"]), (_MAX, _rows("1000000050")),
                    (_ORPHAN, _rows("1000000051")), (_GONE, _rows("0"))])
        status, detail = _run(client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("已删除残留版本 1000000051", detail)
        self.assertEqual(client.bodies("/v1/vouchers/delete")[0]["id"], 1000000051)

    def test_delete_left_rows_fails(self) -> None:
        sql = _sql()
        sql.answers[-1] = (_GONE, _rows("2"))
        status, detail = _run(_client(), sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("残留 2 行", detail)

    def test_missing_pick_is_na(self) -> None:
        status, _detail = _run(_Replay({}), _Sql([]), bag={"bomw_parent": "P001"})
        self.assertEqual(status, "N/A")


if __name__ == "__main__":
    unittest.main()
