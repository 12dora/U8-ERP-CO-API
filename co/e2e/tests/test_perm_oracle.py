"""权限核对（perm_oracle、perm_check*）的离线测试：摘要、应有权限、比对、SQL、登记和用例判定。不连接服务器。"""

from __future__ import annotations

import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e import perm_oracle as po
from co.e2e.cases import Operator
from co.e2e.casespec import ops_run
from co.e2e.casespec import perm_check as rp
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.perm_check_io import NO_EVALUATOR, enabled
from co.e2e.casespec.perm_check_read import allowed, row_key
from co.e2e.casespec.perm_check_sql import columns_sql, nlit, op_sql, perm_samples, stray_users_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = (
    "perm-oracle-all-ops",
    "perm-supervisor-all",
    "perm-empty-ops",
    "perm-wh-control",
    "perm-snapshot-self",
    "perm-eval-denied",
    "perm-read-subset",
)
_SPEC = CaseSpec("perm-x", "x")
_STEP = Step("custom")
_LANE = po.LaneFacts(frozenset({"customer", "warehouse", "code", "user", "gzauth"}), False)


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _all_data() -> dict:
    return {obj: {"all": True} for obj in po.USED}


def _payload(supervisor: bool = False, functions: list[str] | None = None, **data: object) -> dict:
    body = _all_data()
    body.update(data)
    return {
        "ok": True,
        "supervisor": supervisor,
        "functions": ["AS011Q"] if functions is None else functions,
        "objects_on": sorted(_LANE.on),
        "data_admin": [],
        "roles": [],
        "data": body,
        "columns": {},
        "fingerprint": "f" * 64,
    }


def _facts(**codes: list[str]) -> po.OpFacts:
    return po.OpFacts(frozenset(), {obj: po.digest(po.code_key(c) for c in items) for obj, items in codes.items()})


class DigestTests(unittest.TestCase):
    def test_order_case_blank_and_duplicates(self) -> None:
        left = po.digest(po.code_key(c) for c in ["A01", " b02 ", "a01", "", "  "])
        right = po.digest(po.code_key(c) for c in ["B02", "A01"])
        self.assertEqual(left, right)
        self.assertEqual(left.count, 2)

    def test_hash_matches_sql_rule(self) -> None:
        # HASHBYTES('SHA2_256', nvarchar) 前 7 字节按大端转 bigint。
        raw = hashlib.sha256("C001".encode("utf-16-le")).digest()[:7]
        self.assertEqual(po.key_hash("C001"), int.from_bytes(raw, "big"))
        self.assertLess(po.key_hash("C001"), 2**56)

    def test_item_pairs_and_codes_agree(self) -> None:
        pairs = po.shape_of("fitem", {"pairs": [{"class": "00", "code": "p1"}, {"class": "01", "code": "P1"}]})
        codes = po.shape_of("fitem", {"codes": ["00\x1fP1", "01\x1fp1"]})
        self.assertEqual(pairs, codes)
        self.assertEqual(pairs.count, 2)  # type: ignore[union-attr]

    def test_parse_sql_rows(self) -> None:
        lane = po.parse_lane([["S", "Customer "], ["S", "user"], ["G", "True"]])
        self.assertEqual(lane, po.LaneFacts(frozenset({"customer", "user"}), True))
        self.assertFalse(po.parse_lane([["S", "code"], ["G", ""]]).gl_ctl)
        facts = po.parse_op([["A", "Vendor"], ["C", "customer", "2", "12345"], ["C", "hr_hi_person", "1", "7"]])
        self.assertEqual(facts.admin, frozenset({"vendor"}))
        self.assertEqual(facts.sums["customer"], po.Digest(2, 12345))


class ExpectedTests(unittest.TestCase):
    def test_supervisor_sees_all(self) -> None:
        want = po.expected(_LANE, _facts(customer=["C1"]), True)
        self.assertTrue(all(value == po.ALL for value in want.values()))

    def test_controlled_objects(self) -> None:
        lane = po.LaneFacts(frozenset({"customer", "vendor", "code", "person", "gzauth"}), False)
        facts = po.OpFacts(frozenset({"vendor"}), {"customer": po.Digest(1, 9)})
        want = po.expected(lane, facts, False)
        self.assertEqual(want["customer"], po.Digest(1, 9))
        self.assertEqual(want["vendor"], po.ALL)  # 数据权限管理员
        self.assertEqual(want["code"], po.ALL)  # bQryCtlSubj 关着
        self.assertEqual(want["person"], po.EMPTY)  # 受控而没有编码 = 看不到
        self.assertEqual(want["warehouse"], po.ALL)  # 开关关着
        self.assertNotIn("gzauth", want)
        gl = po.expected(po.LaneFacts(frozenset({"code"}), True), po.OpFacts(frozenset(), {}), False)
        self.assertEqual(gl["code"], po.EMPTY)

    def test_person_falls_back_to_hr(self) -> None:
        lane = po.LaneFacts(frozenset({"person"}), False)
        facts = po.OpFacts(frozenset(), {"hr_hi_person": po.Digest(3, 4)})
        self.assertEqual(po.expected(lane, facts, False)["person"], po.Digest(3, 4))


class CompareTests(unittest.TestCase):
    def test_match_and_missing_uncontrolled(self) -> None:
        want = po.expected(_LANE, _facts(customer=["C1", "C2"]), False)
        payload = _payload(customer={"codes": ["c2", "C1"]}, warehouse={"codes": []})
        del payload["data"]["vendor"]
        self.assertEqual(po.compare_data(want, po.actual(payload)), [])

    def test_mismatches_report_counts_only(self) -> None:
        want = po.expected(_LANE, _facts(customer=["SECRET1", "SECRET2"]), False)
        cases = (
            _payload(customer={"all": True}),
            _payload(customer={"codes": ["SECRET1"]}),
            _payload(vendor={"codes": ["SECRET1"]}),
            _payload(customer={"oops": 1}),
        )
        for payload in cases:
            problems = po.compare_data(want, po.actual(payload))
            self.assertTrue(problems)
            self.assertNotIn("SECRET", "".join(problems))
        data = _payload()
        del data["data"]["customer"]
        self.assertIn("customer 受控却没有给出", po.compare_data(want, po.actual(data)))

    def test_flag_invariants(self) -> None:
        facts = po.OpFacts(frozenset(), {})
        self.assertEqual(po.flag_problems(_payload(), _LANE, facts, True), [])
        self.assertIn("本账套有授权的操作员 functions 为空", po.flag_problems(_payload(functions=[]), _LANE, facts, True))
        self.assertEqual(po.flag_problems(_payload(functions=[]), _LANE, facts, False), [])
        bad_on = dict(_payload(), objects_on=["customer"])
        self.assertIn("objects_on 与账套库数据权限开关不一致", po.flag_problems(bad_on, _LANE, facts, True))
        bad_admin = dict(_payload(), data_admin=["vendor"])
        self.assertIn("data_admin 与 AA_holdBusobject 不一致", po.flag_problems(bad_admin, _LANE, facts, True))
        self.assertEqual(po.judge(dict(_payload(), supervisor="yes"), _LANE, facts, True), ["supervisor 不是布尔值"])

    def test_compare_self(self) -> None:
        snap = _payload(customer={"codes": ["A"]})
        self.assertEqual(po.compare_self(snap, json.loads(json.dumps(snap))), [])
        other = dict(snap, fingerprint="0" * 64, functions=["X"])
        self.assertEqual(po.compare_self(snap, other), ["functions 不同", "fingerprint 不同"])

    def test_roles(self) -> None:
        self.assertEqual(po.roles_of({"roles": [" R1 "]}, ["R2"]), (["R1"], ""))
        self.assertEqual(po.roles_of({}, ["R2"])[0], ["R2"])
        self.assertEqual(po.roles_of({}, None), ([], "取不到角色"))
        self.assertTrue(po.roles_problem({"roles": ["R1"]}, ["R1", "R2"]))
        self.assertEqual(po.roles_problem({"roles": ["r1"]}, ["R1"]), "")

    def test_supervisor_roles_not_compared(self) -> None:
        # 桥对账套主管不读角色：roles 为 []、objects_on 为 []、数据全部、columns 为 {}。只比主管标志、数据和 columns。
        sup = dict(_payload(supervisor=True), roles=[], objects_on=[])
        self.assertEqual(po.roles_problem(sup, ["R1"]), "")
        self.assertEqual(po.judge(sup, _LANE, None, True), [])
        self.assertEqual(po.compare_columns(po.expected_columns([], True), sup), [])
        self.assertTrue(po.judge(_payload(supervisor=True, customer={"codes": ["A"]}), _LANE, None, True))
        self.assertTrue(po.compare_columns({}, dict(sup, columns={"0303": ["iPrice"]})))
        # 非主管仍严格比角色。
        self.assertTrue(po.roles_problem(dict(_payload(), roles=[]), ["R1"]))

    def test_summary(self) -> None:
        text = po.summary({"op1": ["a", "b", "c"], "op2": ["d"]}, 5)
        self.assertTrue(text.startswith("2/5 个操作员不一致"))
        self.assertIn("op1：a；b", text)
        self.assertNotIn("；c", text)  # 每个操作员只列前两条


def _f(group: int, key: str, fld: str, func: str = "N") -> list[str]:
    return ["F", str(group), key, fld, func]


class ColumnTests(unittest.TestCase):
    def test_user_union_roles(self) -> None:
        cells = [_f(0, "0303", "iPrice"), _f(1, "0303", "iUnitCost"), _f(1, "24", "B;isum"), ["C", "x", "1", "2"]]
        want = po.expected_columns(cells, False)
        self.assertEqual(want, {"0303": frozenset({"IPRICE", "IUNITCOST"}), "24": frozenset({"ISUM"})})

    def test_user_allow_cancels_role_only(self) -> None:
        cells = [_f(0, "0303", "iPrice", "R"), _f(1, "0303", "IPRICE"), _f(1, "0303", "iUnitCost"),
                 _f(0, "24", "isum"), _f(1, "24", "isum")]
        want = po.expected_columns(cells, False)
        self.assertEqual(want["0303"], frozenset({"IUNITCOST"}))  # 本人非 N 抵消角色 N
        self.assertEqual(want["24"], frozenset({"ISUM"}))  # 本人 N 角色抵消不了
        role_allow = [_f(1, "88", "iOrderAmt", ""), _f(0, "88", "iOrderAmt")]
        self.assertEqual(po.expected_columns(role_allow, False), {"88": frozenset({"IORDERAMT"})})

    def test_supervisor_empty(self) -> None:
        self.assertEqual(po.expected_columns([_f(0, "0303", "iPrice")], True), {})
        self.assertEqual(po.expected_columns([], False), {})

    def test_compare(self) -> None:
        want = {"0303": frozenset({"IPRICE", "IUNITCOST"})}
        good = {"columns": {"0303": ["iUnitCost", "iprice"]}}
        self.assertEqual(po.compare_columns(want, good), [])
        self.assertEqual(po.compare_columns({}, {"columns": {}}), [])
        self.assertEqual(po.compare_columns(want, {}), ["columns 缺失或不是对象"])
        self.assertIn("不是非空字符串数组", po.compare_columns(want, {"columns": {"0303": []}})[0])
        wrong = po.compare_columns(want, {"columns": {"0303": ["iPrice"], "24": ["isum"]}})
        self.assertEqual(len(wrong), 1)
        self.assertIn("0303 多 无、少 ['IUNITCOST']", wrong[0])
        self.assertIn("24 多 ['ISUM']", wrong[0])

    def test_compare_self_columns(self) -> None:
        snap = _payload()
        other = dict(snap, columns={"0303": ["iPrice"]})
        self.assertEqual(po.compare_self(snap, other), ["columns 不同"])

    def test_sql(self) -> None:
        text = columns_sql("op-001", ["R1"])
        self.assertIn("ROW:F|", text)
        self.assertIn("b.iAuthType = 1 AND b.bAuthControl = 1", text)
        self.assertIn("a.isUserGroup = 1 AND a.cUserId IN (N'R1')", text)
        self.assertNotIn("isUserGroup = 1", columns_sql("op-001", []))
        self.assertEqual(sql_block_reason(text), "")

    def test_check_uses_columns(self) -> None:
        listed = [{"code": "op-1", "state": 0, "disabled": False, "roles": []}]
        common = {"evaluator": lambda _env: _EV, "lane_facts": lambda *_a: _LANE, "operators": lambda *_a: listed,
                  "op_facts": lambda *_a: _facts(), "col_rows": lambda *_a: [_f(0, "0303", "iPrice")]}
        good = _payload(warehouse={"codes": []}, customer={"codes": []})
        good["columns"] = {"0303": ["iPrice"]}
        with mock.patch.multiple(rp, evaluate=lambda *_a: good, **common):
            self.assertEqual(rp.PERM_OPS["perm_all_ops"](_env(), _SPEC, _STEP)[0], "PASS")
        leaked = dict(good, columns={})
        with mock.patch.multiple(rp, evaluate=lambda *_a: leaked, **common):
            status, detail = rp.PERM_OPS["perm_all_ops"](_env(), _SPEC, _STEP)
        self.assertEqual(status, "FAIL")
        self.assertIn("columns", detail)


class SqlTests(unittest.TestCase):
    def test_samples_pass_guard(self) -> None:
        names = [name for name, _sql in sample_sql()]
        for name, text in perm_samples():
            self.assertIn(name, names)
            self.assertEqual(sql_block_reason(text), "", name)

    def test_literals(self) -> None:
        self.assertEqual(nlit(" O'B "), "N'O''B'")
        for bad in ("", "a\nb", "x" * 41, "$(x)"):
            with self.assertRaises(ValueError):
                nlit(bad)
        with self.assertRaises(ValueError):
            stray_users_sql(0)

    def test_principals(self) -> None:
        with_roles = op_sql("op-001", ["R1"])
        self.assertIn("a.isUserGroup = 1 AND a.cUserId IN (N'R1')", with_roles)
        self.assertIn("h.isUserGroup = 0 AND h.cUserId = N'op-001'", with_roles)
        self.assertNotIn("isUserGroup = 1", op_sql("op-001", []))
        self.assertIn("LIKE N'%R%'", with_roles)
        self.assertIn("N'hr_hi_person'", with_roles)


class RegistryTests(unittest.TestCase):
    def test_registered_once_with_tags(self) -> None:
        names = known_names()
        for spec in rp.perm_cases():
            self.assertIn(spec.name, _NAMES)
            self.assertEqual(names.count(spec.name), 1, spec.name)
            self.assertTrue(spec.requires_login and spec.optional and spec.routes, spec.name)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name), spec.name)
            self.assertIn("perm", auto_tags(spec))
        reader = {spec.name: spec for spec in rp.perm_cases()}["perm-read-subset"]
        self.assertIn("arc-read:customer", auto_tags(reader))

    def test_settings_key(self) -> None:
        self.assertEqual(support.make_config().perm_evaluator_file, "")
        cfg = support.make_config(account__perm_evaluator_file="~/lane/evaluator.json")
        self.assertTrue(cfg.perm_evaluator_file.endswith("lane/evaluator.json"))
        self.assertNotIn("~", cfg.perm_evaluator_file)

    def test_read_helpers(self) -> None:
        self.assertIsNone(allowed("customer", {"all": True}))
        self.assertEqual(allowed("customer", {"codes": [" c1 "]}), {"C1"})
        self.assertEqual(allowed("fitem", {"pairs": [{"class": "00", "code": "p"}]}), {"00\x1fP"})
        self.assertEqual(row_key("fitem", {"code": "00:p"}), "00\x1fP")
        self.assertEqual(row_key("customer", {"code": "c1"}), "C1")
        items = [{"code": "a", "state": 0, "disabled": False, "roles": ["R"]}, {"code": "b", "state": 1, "disabled": True}]
        self.assertEqual(enabled(items), {"a": ["R"]})


_EV = Operator("801", "2024", "op-eval", "pw-eval")


def _env() -> SimpleNamespace:
    return SimpleNamespace(operator=Operator("801", "2024", "op-test", "pw"), year="2024", today="2026-09-28",
                           ctx=SimpleNamespace(needles=[]), client=object())


class CaseTests(unittest.TestCase):
    def _patch(self, **values: object):
        return mock.patch.multiple(rp, **values)

    def test_no_evaluator_is_na(self) -> None:
        with self._patch(evaluator=lambda _env: None, lane_facts=lambda *_a: _LANE):
            for op in ("perm_all_ops", "perm_supervisor_all", "perm_empty_ops", "perm_eval_denied"):
                self.assertEqual(rp.PERM_OPS[op](_env(), _SPEC, _STEP), ("N/A", NO_EVALUATOR))

    def test_all_ops_pass_and_fail(self) -> None:
        listed = [{"code": "op-1", "state": 0, "disabled": False, "roles": []}]
        facts = _facts(customer=["C1"])
        good = _payload(customer={"codes": ["C1"]}, warehouse={"codes": []})
        common = {"evaluator": lambda _env: _EV, "lane_facts": lambda *_a: _LANE, "operators": lambda *_a: listed,
                  "op_facts": lambda *_a: facts, "col_rows": lambda *_a: []}
        with self._patch(evaluate=lambda *_a: good, **common):
            status, detail = rp.PERM_OPS["perm_all_ops"](_env(), _SPEC, _STEP)
        self.assertEqual(status, "PASS", detail)
        leaked = _payload(customer={"all": True})
        with self._patch(evaluate=lambda *_a: leaked, **common):
            status, detail = rp.PERM_OPS["perm_all_ops"](_env(), _SPEC, _STEP)
        self.assertEqual(status, "FAIL")
        self.assertIn("op-1", detail)
        self.assertNotIn("C1", detail)

    def test_eval_denied(self) -> None:
        with self._patch(evaluator=lambda _env: _EV, call_as=lambda *_a: (403, {"code": "no_permission"})):
            self.assertEqual(rp.PERM_OPS["perm_eval_denied"](_env(), _SPEC, _STEP)[0], "PASS")
        with self._patch(evaluator=lambda _env: _EV, call_as=lambda *_a: (200, {})):
            self.assertEqual(rp.PERM_OPS["perm_eval_denied"](_env(), _SPEC, _STEP)[0], "FAIL")
        same = Operator("801", "2024", "OP-TEST", "pw")
        with self._patch(evaluator=lambda _env: same):
            self.assertEqual(rp.PERM_OPS["perm_eval_denied"](_env(), _SPEC, _STEP)[0], "N/A")

    def test_empty_ops(self) -> None:
        def call(_env: object, _op: object, _route: str, body: dict) -> tuple[int, dict]:
            if body["subject"] == "op-stray":
                return 200, _payload(functions=[], customer={"codes": []}, warehouse={"codes": []})
            return 404, {"code": "not_found"}

        values = {"evaluator": lambda _env: _EV, "lane_facts": lambda *_a: _LANE, "operators": lambda *_a: [],
                  "stray_codes": lambda *_a: (["op-stray", "op-gone"], 0), "op_facts": lambda *_a: _facts(),
                  "call_as": call, "col_rows": lambda *_a: []}
        with self._patch(**values):
            status, detail = rp.PERM_OPS["perm_empty_ops"](_env(), _SPEC, _STEP)
        self.assertEqual(status, "PASS", detail)
        self.assertIn("1/2", detail)

    def test_snapshot_self_supervisor_ignores_roles(self) -> None:
        listed = [{"code": "op-test", "state": 0, "disabled": False, "roles": ["R1"]}]
        sup = dict(_payload(supervisor=True), roles=[], objects_on=[])
        common = {"evaluator": lambda _env: None, "lane_facts": lambda *_a: _LANE, "operators": lambda *_a: listed}
        with self._patch(snapshot=lambda *_a: sup, **common):
            status, detail = rp.PERM_OPS["perm_snapshot_self"](_env(), _SPEC, _STEP)
        self.assertEqual(status, "PASS", detail)
        plain = dict(_payload(customer={"codes": []}, warehouse={"codes": []}), roles=[])
        with self._patch(snapshot=lambda *_a: plain, op_facts=lambda *_a: _facts(), col_rows=lambda *_a: [], **common):
            status, detail = rp.PERM_OPS["perm_snapshot_self"](_env(), _SPEC, _STEP)
        self.assertEqual(status, "FAIL")
        self.assertIn("角色不在响应 roles 里", detail)

    def test_warehouse_off_is_na(self) -> None:
        off = po.LaneFacts(frozenset({"customer"}), False)
        with self._patch(lane_facts=lambda *_a: off):
            self.assertEqual(rp.PERM_OPS["perm_warehouse"](_env(), _SPEC, _STEP)[0], "N/A")


if __name__ == "__main__":
    unittest.main()
