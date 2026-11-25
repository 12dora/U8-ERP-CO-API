"""客户字段用例 `archive-customer-fields` 的登记、分线、SQL 守卫、纯函数和一轮回放。不连接服务器。"""

from __future__ import annotations

import unittest
from collections.abc import Callable
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError
from co.e2e.casespec import arc_cus_fields as mod
from co.e2e.casespec.arc_cus_bodies import (
    field_view,
    fields_body,
    load_row,
    merged,
    other_date,
    other_text,
    reply_fields,
    reply_fields_dry,
    row_problem,
    stamp_problem,
)
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.arc_write import CORE_HANDLERS
from co.e2e.casespec.runner import _HANDLERS
from co.e2e.casespec.arc_cus_sql import (
    agreement_alive_sql,
    agreement_count_sql,
    agreement_pick_sql,
    agreement_stopped_sql,
    cus_row_sql,
    cus_samples,
    lit,
)
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "archive-customer-fields"
_COLUMNS = {
    "abbrname": "cCusAbbName",
    "district": "cDCCode",
    "end_date": "dEndDate",
    "sa_protocol": "cCusSAProtocol",
    "name": "cCusName",
    "tax_no": "cCusRegCode",
    "contact": "cCusPerson",
    "bank_open": "cCusBank",
    "ccustradeccode": "cCusTradeCCode",
    "self_define1": "cCusDefine1",
}
_ROW = {
    "cCusCode": "C900001",
    "cCusName": "示例客户有限公司",
    "cCusRegCode": "",
    "cCusAbbName": "示例简称",
    "cDCCode": "",
    "dEndDate": "",
    "cCusSAProtocol": "",
    "cCusPerson": "",
    "cCusBank": "",
    "cCusTradeCCode": "",
    "cTrade": "",
    "cCusDefine1": "",
    "cModifyPerson": "h:mp0",
    "dModifyDate": "h:md0",
    "pubufts": "h:1",
    "dLRDate": "h:lr0",
    "cCusMnemCode": "h:mn0",
    "dCusCreateDatetime": "h:cr0",
}
_STAMPS = ("cModifyPerson", "dModifyDate", "pubufts")
_OPERATOR = "测试员"
_TODAY = "2026-09-28"
# 收付款协议档案：编码 → 停用日期（空 = 没停用）；A000 在登录日之前已停用。
_AGREEMENTS = {"A000": "2026-01-01", "A001": "", "A002": "2026-12-31"}
# 末级所属行业：编码 → 行业名称。
_TRADES = {"01": "行业甲", "02": "行业乙"}
# 自定义项 1 的账套设置：是否建档 | 取自其他档案 | 使用长度 | 定长 | 最大长度 | 编号（缺省不建档、不限长）。
_DEFINE_FREE = "0|0|0|0|0|0|11"
_DEFINE_ARCHIVE = "1|0|0|0|0|0|11"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Store:
    """一户客户 + 地区分类表 + 收付款协议表；SQL 按查询里的关键字应答，路由由 _Client 改它。"""

    def __init__(
        self,
        row: dict[str, str] | None = None,
        districts: dict[str, bool] | None = None,
        agreements: dict[str, str] | None = None,
    ) -> None:
        self.row = dict(row or _ROW)
        self.districts = {"33": True, "30": False} if districts is None else dict(districts)
        self.agreements = dict(_AGREEMENTS) if agreements is None else dict(agreements)
        self.created = "2025-01-01"
        self.trades = dict(_TRADES)
        self.define_setting = _DEFINE_FREE
        self.define_values = {"档案值甲", "档案值乙"}
        # 别的客户已用的简称（简称重复 409 用）；空串表示没有。
        self.other_abbr = "别家简称"
        self.queries: list[str] = []
        self.refuse_stamp = False

    def guarded(self, _env: object, query: str, _label: str) -> str:
        self.queries.append(query)
        return self._reply(query)

    def _reply(self, query: str) -> str:
        if "(VALUES" in query:
            free = [code for code in ("ZZ1", "ZZ2") if code not in self.districts]
            return f"ROW:{free[0]}\n" if free else ""
        if "sys.columns" in query:
            return "ROW:pubufts\nROW:dModifyDate\nROW:cModifyPerson\n"
        if "dCusCreateDatetime, 23" in query:
            return f"ROW:{self.created}\n"
        if "c.cCusAbbName AS v" in query:
            return f"ROW:{self.other_abbr}\n" if self.other_abbr else ""
        if "FOR XML" in query:
            return "".join(f"ROW:{key}\t{val}\n" for key, val in self.row.items())
        if "dbo.DistrictClass" in query:
            return self._district(query)
        if "dbo.AA_Agreement" in query:
            return self._agreement(query)
        return self._more(query)

    def _more(self, query: str) -> str:
        if "dbo.TradeClass" in query:
            return self._trade(query)
        if "dbo.UserDef_Base" in query:
            return f"ROW:{self.define_setting}\n" if self.define_setting else ""
        if "dbo.UserDefine" in query:
            skip = self.row["cCusDefine1"]
            values = sorted(value for value in self.define_values if value != skip)
            return f"ROW:{values[0]}\n" if values else ""
        raise AssertionError("没有料到的 SQL：" + query[:80])

    def _district(self, query: str) -> str:
        if "DELETE FROM" in query:
            gone = [code for code in self.districts if _named(query, code)]
            for code in gone:
                del self.districts[code]
            return f"ROW:{len(gone)}\n"
        if "COUNT(*)" in query:
            return f"ROW:{sum(1 for code in self.districts if _named(query, code))}\n"
        if "ORDER BY" in query:
            skip = self.row["cDCCode"]
            leaves = sorted(code for code, leaf in self.districts.items() if leaf and code != skip)
            return f"ROW:{leaves[0]}\n" if leaves else ""
        for code, leaf in self.districts.items():
            if _named(query, code) and leaf:
                return f"ROW:{code}\n"
        return ""

    def _trade(self, query: str) -> str:
        if "ORDER BY" in query:
            others = sorted(code for code in self.trades if code != self.row["cCusTradeCCode"])
            return f"ROW:{others[0]}\n" if others else ""
        return next((f"ROW:{name}\n" for code, name in self.trades.items() if _named(query, code)), "")

    def alive(self, code: str) -> bool:
        end = self.agreements.get(code)
        return end is not None and (end == "" or end > _TODAY)

    def archive_code(self, value: str) -> str:
        """档案里存的编码（不分大小写匹配），没有回空串。"""
        return next((code for code in self.agreements if code.upper() == value.upper()), "")

    def _agreement(self, query: str) -> str:
        if "COUNT(*)" in query:
            return f"ROW:{sum(1 for code in self.agreements if _named(query, code))}\n"
        if "dEDate IS NOT NULL" in query:
            assert f"<= N'{_TODAY}'" in query, "已停用协议要按登录日挑"
            ended = sorted(code for code, end in self.agreements.items() if end and not self.alive(code))
            return f"ROW:{ended[0]}\n" if ended else ""
        if "ORDER BY" in query:
            assert f"N'{_TODAY}'" in query, "协议挑选要按登录日判停用"
            skip = self.row["cCusSAProtocol"].upper()
            codes = sorted(code for code in self.agreements if self.alive(code) and code.upper() != skip)
            return f"ROW:{codes[0]}\n" if codes else ""
        for code in self.agreements:
            if _named(query.upper(), code.upper()) and self.alive(code):
                return f"ROW:{code}\n"
        return ""

    def view(self) -> dict[str, str]:
        return field_view(load_row(sql_cells_of(self.row)))


def _named(query: str, code: str) -> bool:
    return "N'" + code + "'" in query


def _differs(current: dict[str, str], key: str, val: object) -> bool:
    """expect 比对：sa_protocol 不分大小写（同桥），其余逐字。"""
    if key == "sa_protocol":
        return current.get(key, "").upper() != str(val).upper()
    return current.get(key) != val


def sql_cells_of(row: dict[str, str]) -> list[str]:
    return [f"{key}\t{val}".strip() for key, val in row.items()]


class _Client:
    def __init__(self, store: _Store, readonly_create: bool = False, readonly_delete: bool = False) -> None:
        self.store = store
        self.readonly_create = readonly_create
        self.readonly_delete = readonly_delete
        self.calls: list[tuple[str, dict]] = []

    def health(self) -> dict:
        return {}

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        op = route.rsplit("/", 1)[-1]
        if op == "customer_fields":
            return self._fields(fields)
        if op == "create":
            return self._create(fields)
        if op == "delete":
            return self._delete(fields)
        raise U8CoError(404, "not_found", "路由不存在")

    def _create(self, fields: dict) -> dict:
        if self.readonly_create:
            raise U8CoError(403, "account_read_only", "该账套只开放读取")
        self.store.districts[str(fields["code"])] = True
        return {"ok": True, "code": fields["code"]}

    def _delete(self, fields: dict) -> dict:
        if self.readonly_delete:
            raise U8CoError(403, "account_read_only", "该账套只开放读取")
        self.store.districts.pop(str(fields["code"]), None)
        return {"ok": True, "deleted": True}

    def _protocol(self, fields: dict, current: dict[str, str]) -> None:
        """与桥同口径：空串清空；与现值相同不查；否则必须在档案里且没停用，写档案里的编码。"""
        value = fields.get("sa_protocol")
        if not value:
            return
        if str(value).upper() == current["sa_protocol"].upper():
            fields["sa_protocol"] = current["sa_protocol"]  # 与现值相同：不查，按现值算（no-op）
            return
        code = self.store.archive_code(str(value))
        if not code:
            raise U8CoError(409, "sa_protocol_invalid", f"收付款协议不存在：{value}")
        if not self.store.alive(code):
            raise U8CoError(409, "sa_protocol_invalid", f"收付款协议已停用：{value}")
        fields["sa_protocol"] = code

    def _check(self, fields: dict) -> None:
        store = self.store
        if not fields or set(fields) - set(_COLUMNS):
            raise U8CoError(400, "bad_request", "字段不合法")
        for key in ("abbrname", "name"):
            if key in fields and not str(fields[key]).strip():
                raise U8CoError(400, "bad_request", "简称、名称不能为空")
        if store.other_abbr and fields.get("abbrname") == store.other_abbr:
            raise U8CoError(409, "abbrname_taken", "简称已被其他客户使用")
        district = fields.get("district")
        if district and not store.districts.get(str(district)):
            raise U8CoError(409, "district_invalid", "地区分类不存在或不是末级")
        day = fields.get("end_date")
        if day and day < store.created:
            raise U8CoError(400, "bad_request", "停用日期早于建档日")
        trade = fields.get("ccustradeccode")
        if trade and trade not in store.trades:
            raise U8CoError(409, "ccustradeccode_invalid", "所属行业不存在或不是末级")
        define = fields.get("self_define1")
        if define and store.define_setting == _DEFINE_ARCHIVE and define not in store.define_values:
            raise U8CoError(409, "self_define1_invalid", "自定义项 1 的值不在自定义项档案里")

    def _fields(self, fields: dict) -> dict:
        store = self.store
        if fields.get("code") != store.row["cCusCode"]:
            raise U8CoError(404, "not_found", "客户不存在")
        current = store.view()
        want = dict(fields.get("fields") or {})
        self._check(want)
        self._protocol(want, current)
        for key, val in (fields.get("expect") or {}).items():
            if _differs(current, key, val):
                error = U8CoError(409, "state_mismatch", f"{key} 已变化")
                error.detail = {"current": {name: current[name] for name in want}}
                raise error
        target = {**current, **want}
        flag = any(target[key] != current[key] for key in want)
        out = {
            "ok": True,
            "code": fields["code"],
            "changed": flag,
            "before": {key: current[key] for key in want},  # 只含请求的键
            "after": {key: target[key] for key in want},
        }
        if fields.get("dry_run"):
            return {**out, "dry_run": True, "mode": "validate"}
        if flag:
            self._write(target, want)
        return out

    def _write(self, target: dict[str, str], want: dict[str, str]) -> None:
        row = self.store.row
        row["cCusAbbName"] = target["abbrname"]
        row["cDCCode"] = target["district"]
        row["dEndDate"] = target["end_date"] + "T00:00:00" if target["end_date"] else ""
        row["cCusSAProtocol"] = target["sa_protocol"]
        row["cCusName"] = target["name"]
        row["cCusRegCode"] = target["tax_no"]
        row["cCusPerson"] = target["contact"]
        row["cCusBank"] = target["bank_open"]
        row["cCusTradeCCode"] = target["ccustradeccode"]
        if "ccustradeccode" in want:  # 行业名称只在请求写行业编码时随它一起写
            row["cTrade"] = self.store.trades.get(target["ccustradeccode"], "")
        row["cCusDefine1"] = target["self_define1"]
        row["cModifyPerson"] = "h:" + _OPERATOR
        row["dModifyDate"] = "h:md1"
        row["pubufts"] = "h:" + str(int(row["pubufts"][2:]) + 1)


def _env(client: _Client, operator_name: str = "") -> SimpleNamespace:
    op = SimpleNamespace(acc="801", year="2026", code="op001", password="pw")
    return SimpleNamespace(client=client, operator=op, year="2026", today=_TODAY, operator_name=operator_name)


def _run(client: _Client, store: _Store) -> tuple[str, str]:
    spec = mod.cusfields_cases()[0]
    with mock.patch.object(mod, "run_guarded", store.guarded):
        return mod.step_cusfields(_env(client), spec, spec.steps[0])


def _bodies(client: _Client, op: str = "customer_fields") -> list[dict]:
    return [body for route, body in client.calls if route.endswith("/" + op)]


def _same_but_stamps(first: dict[str, str], second: dict[str, str]) -> bool:
    return {k: v for k, v in first.items() if k not in _STAMPS} == {k: v for k, v in second.items() if k not in _STAMPS}


class CatalogTests(unittest.TestCase):
    def test_case_registered(self) -> None:
        (spec,) = mod.cusfields_cases()
        self.assertEqual(spec.name, _NAME)
        self.assertEqual(known_names().count(_NAME), 1)
        self.assertTrue(spec.requires_login)
        self.assertEqual(spec.data, ("archives.customer.existing",))
        self.assertIn("arc:customer", auto_tags(spec))
        self.assertIn("arc:write", auto_tags(spec))
        self.assertIn("/v1/archives/customer_fields", spec.routes)
        self.assertEqual(spec.steps[0].kind, "core_cusfields")
        self.assertIs(CORE_HANDLERS["core_cusfields"], mod.step_cusfields)
        self.assertIs(_HANDLERS["core_cusfields"], mod.step_cusfields)


class SqlTests(unittest.TestCase):
    def test_samples_pass_the_guard(self) -> None:
        for name, sql in cus_samples():
            self.assertEqual(sql_block_reason(sql), "", name)
            self.assertIn("ROW:", sql, name)
            self.assertNotIn("$(", sql)

    def test_row_sql_reads_the_four_columns(self) -> None:
        sql = cus_row_sql("C900001")
        for column in _COLUMNS.values():
            self.assertIn(f"N'{column}'", sql)
        self.assertIn("N'cModifyPerson'", sql)  # 修改人读原值，才能和操作员姓名比
        self.assertNotIn("N'cCusPPerson'", sql)
        self.assertIn("HASHBYTES", sql)

    def test_agreement_sql_checks_the_login_day(self) -> None:
        pick = agreement_pick_sql("A001", "2026-09-28")
        self.assertIn("dbo.AA_Agreement", pick)
        self.assertIn("N'2026-09-28'", pick)
        self.assertIn("UPPER(N'A001')", pick)
        self.assertNotIn("<>", agreement_pick_sql("", "2026-09-28"))
        self.assertNotIn("<>", agreement_pick_sql("a-b", "2026-09-28"))  # 不是字母数字：挑出的码本来就不等于它
        self.assertIn("N'2026-09-28'", agreement_alive_sql("A001", "2026-09-28"))
        self.assertIn("COUNT(*)", agreement_count_sql("ZZNOPROT"))
        self.assertIn("<= N'2026-09-28'", agreement_stopped_sql("2026-09-28"))
        for sql in (pick, agreement_alive_sql("A001", "2026-09-28"), agreement_stopped_sql("2026-09-28")):
            self.assertIn("a.iLZYJ = 1", sql)  # 销售默认协议只收销售协议，桥对其他用途 409 sa_protocol_invalid
        for bad in (lambda: agreement_pick_sql("", "2026-9-28"), lambda: agreement_alive_sql("A'1", "2026-09-28")):
            with self.assertRaises(ValueError):
                bad()

    def test_literal_escapes_and_refuses_control_chars(self) -> None:
        self.assertEqual(lit("a'b"), "N'a''b'")
        with self.assertRaises(ValueError):
            lit("a\nb")


class PureTests(unittest.TestCase):
    def test_row_and_view(self) -> None:
        cells = ["cCusAbbName\t甲", "dEndDate\t2026-10-20T00:00:00", "cDCCode\t<nil>", "cCusSAProtocol\tA001", "noise"]
        cells += ["cCusName\t示例客户", "cCusRegCode\t<nil>"]
        row = load_row(cells)
        want = {"abbrname": "甲", "district": "", "end_date": "2026-10-20", "sa_protocol": "A001", "name": "示例客户", "tax_no": ""}
        want.update({"contact": "", "bank_open": "", "ccustradeccode": "", "self_define1": ""})
        self.assertEqual(field_view(row), want)
        self.assertEqual(field_view({"cCusSAProtocol": ""})["sa_protocol"], "")
        self.assertEqual(merged({"cDCCode": "1"}, {"cdccode": "2", "new": "3"}), {"cDCCode": "2", "new": "3"})

    def test_text_and_date_choice(self) -> None:
        self.assertEqual(other_text("甲", "E2E", 60), "E2E甲")
        self.assertEqual(len(other_text("x" * 60, "E2E", 60)), 60)
        self.assertNotEqual(other_text("x" * 60, "E2E", 60), "x" * 60)
        self.assertEqual(other_date("", "2026-09-28", "2025-01-01"), "2026-09-28")
        self.assertEqual(other_date("2026-09-28", "2026-09-28", "-"), "2026-09-29")
        self.assertEqual(other_date("", "2026-09-28", "2026-12-31"), "2026-12-31")

    def test_body_flags(self) -> None:
        dry = fields_body("C900001", {"abbrname": "x"}, {"abbrname": "y"}, True)
        self.assertTrue(dry["dry_run"])
        self.assertNotIn("idempotency_key", dry)
        real = fields_body("C900001", {"abbrname": "x"})
        self.assertTrue(real["idempotency_key"].startswith("e2e-cusfields-"))
        self.assertNotIn("expect", real)

    def test_row_problem_and_stamps(self) -> None:
        prev = dict(_ROW)
        cur = {**prev, "cCusAbbName": "乙", "pubufts": "h:2", "cModifyPerson": "h:x"}
        ignore = frozenset({"cModifyPerson", "dModifyDate", "pubufts"})
        self.assertEqual(row_problem(prev, {"cCusAbbName": "乙"}, cur, ignore), "")
        self.assertIn("dLRDate", row_problem(prev, {"cCusAbbName": "乙"}, {**cur, "dLRDate": "h:9"}, ignore))
        self.assertIn("cCusAbbName", row_problem(prev, {}, cur, ignore))
        self.assertEqual(stamp_problem(prev, cur, ""), "")
        self.assertIn("pubufts", stamp_problem(prev, prev, ""))
        self.assertIn("cModifyPerson", stamp_problem(prev, {**cur, "cModifyPerson": "other"}, "我"))

    def test_reply_checks(self) -> None:
        before = {"abbrname": "甲", "district": "", "end_date": "", "sa_protocol": "", "name": "示例客户", "tax_no": ""}
        after = {**before, "district": "33"}
        good = {"ok": True, "code": "C900001", "changed": True, "before": before, "after": after}
        self.assertEqual(reply_fields(good, "C900001", (before, after), True), "")
        self.assertIn("changed", reply_fields(good, "C900001", (before, after), False))
        self.assertIn("after", reply_fields({**good, "after": before}, "C900001", (before, after), True))
        self.assertIn("before", reply_fields({**good, "before": None}, "C900001", (before, after), True))
        four = {key: val for key, val in before.items() if key not in ("name", "tax_no")}
        self.assertIn("before", reply_fields({**good, "before": four}, "C900001", (before, after), True))  # 旧桥少名称、税号
        wrong = {**good, "after": {**after, "sa_protocol": "A001"}}
        self.assertIn("after", reply_fields(wrong, "C900001", (before, after), True))
        dry = {"ok": True, "code": "C900001", "dry_run": True, "mode": "validate", "before": before}
        self.assertEqual(reply_fields_dry(dry, "C900001", before), "")
        self.assertIn("dry_run", reply_fields_dry({**dry, "mode": "real"}, "C900001", before))


class ReplayTests(unittest.TestCase):
    def test_passes_and_restores_the_row(self) -> None:
        store = _Store()
        client = _Client(store)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertTrue(_same_but_stamps(store.row, _ROW))
        bodies = _bodies(client)
        self.assertTrue(bodies[0]["dry_run"])
        self.assertNotIn("idempotency_key", bodies[0])
        self.assertTrue(bodies[1]["idempotency_key"].startswith("e2e-cusfields-"))
        self.assertEqual(bodies[1]["expect"], {"abbrname": "示例简称"})
        self.assertEqual(bodies[1]["fields"], {"abbrname": "E2E简称示例简称"})
        self.assertEqual((bodies[3]["fields"], bodies[3]["expect"]), ({"district": "33"}, {"district": ""}))
        self.assertEqual(bodies[-1]["fields"], {"abbrname": "示例简称", "sa_protocol": ""})
        self.assertEqual(bodies[-1]["expect"], {"abbrname": "E2E简称示例简称", "sa_protocol": "A001"})
        self.assertEqual(note, "customer C900001 fields")
        self.assertEqual([body["fields"] for body in bodies if body["code"] == "ZZNOCUS01"], [{"abbrname": "E2E"}])
        self.assertEqual(_bodies(client, "create"), [])

    def test_covers_every_field_and_error(self) -> None:
        store = _Store()
        client = _Client(store)
        _run(client, store)
        keys = [tuple(body["fields"]) for body in _bodies(client)]
        for want in (("abbrname",), ("district",), ("end_date",), ("sa_protocol",), ("name",), ("tax_no",), ("nickname",)):
            self.assertIn(want, keys)
        self.assertIn(("name", "tax_no"), keys)  # 名称 expect 不符：两键都在 fields，detail.current 也是两键
        names = [body["fields"]["name"] for body in _bodies(client) if "name" in body["fields"]]
        self.assertEqual(names, ["E2E名称示例客户有限公司", "E2E不符", "示例客户有限公司"])
        taxes = [body["fields"]["tax_no"] for body in _bodies(client) if body["fields"] == {"tax_no": body["fields"].get("tax_no")}]
        self.assertEqual(taxes, ["E2ETAX", ""])
        taken = [body for body in _bodies(client) if body["fields"] == {"abbrname": "别家简称"}]
        self.assertEqual(len(taken), 1)
        protocols = [body["fields"]["sa_protocol"] for body in _bodies(client) if "sa_protocol" in body["fields"]]
        # 设 → 小写重发 → 清空 → 已停用 → 不存在 → 小写设 → 还原
        self.assertEqual(protocols, ["A001", "a001", "", "A000", "ZZNOPROT", "a001", ""])
        sets = [body["expect"]["sa_protocol"] for body in _bodies(client) if body["fields"].get("sa_protocol") == ""]
        self.assertEqual(sets, ["A001", "A001"])
        self.assertTrue(any("COUNT(*)" in query and "AA_Agreement" in query for query in store.queries))
        districts = [body["fields"]["district"] for body in _bodies(client) if "district" in body["fields"]]
        self.assertEqual(districts, ["33", "", "ZZNODC9"])
        days = [body["fields"]["end_date"] for body in _bodies(client) if "end_date" in body["fields"]]
        self.assertEqual(days, ["2026-09-28", "", "1900-01-01"])

    def test_existing_values_are_written_back(self) -> None:
        row = {**_ROW, "cDCCode": "30", "dEndDate": "2026-01-05T00:00:00"}
        store = _Store(row, {"33": True, "30": True})
        client = _Client(store)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertEqual(store.row["cDCCode"], "30")
        self.assertEqual(store.row["dEndDate"][:10], "2026-01-05")
        self.assertEqual(store.row["cCusAbbName"], "示例简称")

    def test_existing_protocol_is_written_back(self) -> None:
        store = _Store({**_ROW, "cCusSAProtocol": "A002"})
        client = _Client(store)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertEqual(store.row["cCusSAProtocol"], "A002")
        bodies = [body for body in _bodies(client) if "sa_protocol" in body["fields"]]
        protocols = [(body["fields"], body.get("expect")) for body in bodies]
        self.assertEqual(protocols[0], ({"sa_protocol": "A001"}, {"sa_protocol": "A002"}))
        self.assertEqual(protocols[1], ({"sa_protocol": "a001"}, {"sa_protocol": "A001"}))
        self.assertEqual(protocols[2], ({"sa_protocol": ""}, {"sa_protocol": "A001"}))
        self.assertEqual(protocols[5], ({"sa_protocol": "a001"}, {"sa_protocol": ""}))
        restore = protocols[-1]  # 还原走路由，和简称一起写回
        self.assertEqual((restore[0]["sa_protocol"], restore[1]["sa_protocol"]), ("A002", "A001"))
        self.assertTrue(_same_but_stamps(store.row, {**_ROW, "cCusSAProtocol": "A002"}))

    def test_stopped_agreements_are_skipped(self) -> None:
        store = _Store(agreements={"A000": "2026-09-28", "A003": ""})  # 停用日 = 登录日也算停用
        client = _Client(store)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        protocols = [body["fields"]["sa_protocol"] for body in _bodies(client) if "sa_protocol" in body["fields"]]
        self.assertEqual(protocols[0], "A003")
        self.assertEqual(protocols[3], "A000")  # 已停用的拿来测 409

    def test_digits_only_protocol_skips_the_lower_set_and_no_stopped_skips_its_409(self) -> None:
        store = _Store(agreements={"01": ""})
        client = _Client(store)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        protocols = [body["fields"]["sa_protocol"] for body in _bodies(client) if "sa_protocol" in body["fields"]]
        self.assertEqual(protocols, ["01", "01", "", "ZZNOPROT"])
        self.assertTrue(_same_but_stamps(store.row, _ROW))

    def _skipped(self, store: _Store) -> tuple[_Client, str]:
        client = _Client(store)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertIn("跳过协议步骤", note)
        self.assertFalse([body for body in _bodies(client) if "sa_protocol" in body["fields"]])
        keys = [tuple(body["fields"]) for body in _bodies(client)]
        for want in (("abbrname",), ("district",), ("end_date",), ("nickname",)):
            self.assertIn(want, keys)  # 其余三键照测
        return client, note

    def test_no_usable_agreement_skips_only_the_protocol_steps(self) -> None:
        for agreements in ({}, {"A000": "2026-01-01"}):
            store = _Store(agreements=agreements)
            _client, note = self._skipped(store)
            self.assertIn("没有可用的收付款协议", note)
            self.assertEqual(store.agreements, agreements)  # 不新建协议档案
            self.assertFalse(any("INSERT" in query for query in store.queries))
            self.assertTrue(_same_but_stamps(store.row, _ROW))

    def test_unrestorable_current_protocol_skips_only_the_protocol_steps(self) -> None:
        for orig in ("A000", "a001", "ZZGONE", "A-1"):  # 已停用、大小写与档案不一致、不在档案里、字符不在白名单
            store = _Store({**_ROW, "cCusSAProtocol": orig})
            _client, note = self._skipped(store)
            self.assertIn("现有的收付款协议", note)
            self.assertEqual(store.row["cCusSAProtocol"], orig)
            self.assertTrue(_same_but_stamps(store.row, {**_ROW, "cCusSAProtocol": orig}))

    def test_protocol_is_picked_before_the_district(self) -> None:
        store = _Store(districts={"30": False}, agreements={})
        client, _note = self._skipped(store)
        first_protocol = next(i for i, query in enumerate(store.queries) if "AA_Agreement" in query)
        first_district = next(i for i, query in enumerate(store.queries) if "DistrictClass" in query)
        self.assertLess(first_protocol, first_district)
        self.assertEqual(len(_bodies(client, "create")), 1)
        self.assertEqual(store.districts, {"30": False})

    def test_creates_and_drops_a_district_when_none_is_a_leaf(self) -> None:
        store = _Store(districts={"30": False})
        client = _Client(store)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        (made,) = _bodies(client, "create")
        self.assertEqual((made["archive"], made["code"]), ("district_class", "ZZ1"))
        self.assertEqual(len(_bodies(client, "delete")), 1)
        self.assertEqual(store.districts, {"30": False})
        self.assertTrue(_same_but_stamps(store.row, _ROW))

    def test_readonly_delete_falls_back_to_sql(self) -> None:
        store = _Store(districts={})
        client = _Client(store, readonly_delete=True)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertEqual(store.districts, {})
        self.assertTrue(any("DELETE FROM dbo.DistrictClass" in query for query in store.queries))

    def test_not_applicable_cases(self) -> None:
        store = _Store(districts={})
        verdict, note = _run(_Client(store, readonly_create=True), store)
        self.assertEqual(verdict, "N/A", note)
        self.assertTrue(_same_but_stamps(store.row, _ROW))
        blank = _Store({**_ROW, "cCusAbbName": ""})
        verdict, note = _run(_Client(blank), blank)
        self.assertEqual(verdict, "N/A", note)
        loose = _Store({**_ROW, "cDCCode": "30"})
        verdict, note = _run(_Client(loose), loose)
        self.assertEqual(verdict, "N/A", note)
        self.assertEqual(loose.row["cDCCode"], "30")

    def test_blank_name_skips_only_the_name_steps(self) -> None:
        store = _Store({**_ROW, "cCusName": ""})
        client = _Client(store)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertIn("跳过名称步骤", note)
        self.assertFalse([body for body in _bodies(client) if "name" in body["fields"]])
        self.assertIn(("tax_no",), [tuple(body["fields"]) for body in _bodies(client)])
        self.assertTrue(_same_but_stamps(store.row, {**_ROW, "cCusName": ""}))

    def test_existing_tax_no_is_written_back(self) -> None:
        store = _Store({**_ROW, "cCusRegCode": "EXAMPLE-TAX-NO-001"})
        client = _Client(store)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        taxes = [body["fields"]["tax_no"] for body in _bodies(client) if tuple(body["fields"]) == ("tax_no",)]
        self.assertEqual(taxes, ["E2ETAXEXAMPLE-TAX-NO-001", "EXAMPLE-TAX-NO-001"])
        self.assertEqual(store.row["cCusRegCode"], "EXAMPLE-TAX-NO-001")

    def test_no_other_abbr_skips_the_taken_step(self) -> None:
        store = _Store()
        store.other_abbr = ""
        client = _Client(store)
        verdict, note = _run(client, store)
        self.assertEqual(verdict, "PASS", note)
        self.assertEqual(note, "customer C900001 fields")

    def test_missing_customer_fails(self) -> None:
        store = _Store()
        store.row = {}
        verdict, note = _run(_Client(store), store)
        self.assertEqual(verdict, "FAIL", note)


class BrokenRouteTests(unittest.TestCase):
    def _broken(self, patch: Callable) -> tuple[str, str, _Store]:
        store = _Store()
        client = _Client(store)
        orig = client._fields

        def wrapped(fields: dict) -> dict:
            return patch(store, orig, fields)

        client._fields = wrapped  # type: ignore[method-assign]
        verdict, note = _run(client, store)
        return verdict, note, store

    def test_extra_column_is_caught_and_value_restored(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            out = orig(fields)
            if not fields.get("dry_run") and "abbrname" in fields.get("fields", {}):
                store.row["dLRDate"] = "h:changed"
            return out

        verdict, note, store = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("dLRDate", note)
        self.assertEqual(store.row["cCusAbbName"], "示例简称")

    def test_noop_that_stamps_the_row_fails(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            out = orig(fields)
            if out["changed"] is False and not fields.get("dry_run"):
                store.row["pubufts"] = "h:99"
            return out

        verdict, note, _store = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("pubufts", note)

    def test_dry_run_that_writes_fails(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            out = orig(fields)
            if fields.get("dry_run"):
                store.row["cDCCode"] = "33"
            return out

        verdict, note, _store = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("预演不应改行", note)

    def test_wrong_response_blocks_fail(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            out = orig(fields)
            if out["changed"] and fields.get("fields", {}).get("district") == "33":
                out["after"] = {**out["after"], "district": "WRONG"}
            return out

        verdict, note, store = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("after", note)
        self.assertEqual(store.row["cDCCode"], "")

    def test_ignored_expect_fails(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            if fields.get("expect") == {"abbrname": "ZZ预期不符"}:
                return orig({**fields, "expect": {}})
            return orig(fields)

        verdict, note, store = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("expect 不符", note)
        self.assertEqual(store.row["cCusAbbName"], "示例简称")

    def test_district_invalid_must_be_409(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            if fields.get("fields", {}).get("district") == "ZZNODC9":
                raise U8CoError(400, "bad_request", "参数无效")
            return orig(fields)

        verdict, note, _store = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("不存在的地区", note)

    def test_sa_protocol_invalid_must_be_409(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            if fields.get("fields", {}).get("sa_protocol") == "ZZNOPROT":
                raise U8CoError(400, "bad_request", "参数无效")
            return orig(fields)

        verdict, note, _store = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("不存在的收付款协议", note)

    def test_stopped_protocol_must_be_409(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            if fields.get("fields", {}).get("sa_protocol") == "A000":
                store.agreements["A000"] = ""  # 桥没拦停用：照写
            return orig(fields)

        verdict, note, store = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("已停用的收付款协议", note)
        self.assertEqual(store.row["cCusSAProtocol"], "")  # 还原走路由

    def test_case_noop_that_writes_fails(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            was = store.row["cCusSAProtocol"]
            out = orig(fields)
            if fields.get("fields", {}).get("sa_protocol") == "a001" and was == "A001":
                store.row["cCusSAProtocol"] = "a001"  # 桥把小写当成新值写了
                store.row["pubufts"] = "h:99"
            return out

        verdict, note, store = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("小写重发收付款协议", note)
        self.assertEqual(store.row["cCusSAProtocol"], "")

    def test_case_noop_reporting_changed_fails(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            out = orig(fields)
            if fields.get("fields", {}).get("sa_protocol") == "a001" and out["changed"] is False:
                out["changed"] = True
            return out

        verdict, note, _store = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("小写重发收付款协议", note)

    def test_lower_set_must_store_the_archive_code(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            was = store.row["cCusSAProtocol"]
            out = orig(fields)
            if fields.get("fields", {}).get("sa_protocol") == "a001" and was == "":
                store.row["cCusSAProtocol"] = "a001"  # 桥存了请求原样，没换成档案编码
            return out

        verdict, note, store = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("小写设收付款协议", note)
        self.assertEqual(store.row["cCusSAProtocol"], "")

    def test_protocol_written_to_wrong_column_fails(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            out = orig(fields)
            if not fields.get("dry_run") and fields.get("fields", {}).get("sa_protocol"):
                store.row["cCusSAProtocol"] = ""
                store.row["dEndDate"] = "2026-09-28T00:00:00"
            return out

        verdict, note, store = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("设收付款协议", note)
        self.assertEqual(store.row["dEndDate"], "")

    def test_state_mismatch_without_current_fails(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            try:
                return orig(fields)
            except U8CoError as exc:
                exc.detail = None  # 旧桥：409 只有消息
                raise

        verdict, note, store = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("detail.current", note)
        self.assertEqual(store.row["cCusName"], "示例客户有限公司")

    def test_abbr_taken_must_be_409(self) -> None:
        def patch(store: _Store, orig: Callable, fields: dict) -> dict:
            if fields.get("fields", {}).get("abbrname") == store.other_abbr:
                store.other_abbr = ""  # 桥没查重：照写
            return orig(fields)

        verdict, note, store = self._broken(patch)
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("简称与别的客户重复", note)
        self.assertEqual(store.row["cCusAbbName"], "示例简称")  # 还原走路由

    def test_operator_name_is_checked_when_known(self) -> None:
        store = _Store()
        spec = mod.cusfields_cases()[0]
        with mock.patch.object(mod, "run_guarded", store.guarded):
            verdict, note = mod.step_cusfields(_env(_Client(store), "别人"), spec, spec.steps[0])
        self.assertEqual(verdict, "FAIL", note)
        self.assertIn("cModifyPerson", note)
        store = _Store()
        with mock.patch.object(mod, "run_guarded", store.guarded):
            verdict, note = mod.step_cusfields(_env(_Client(store), "h:" + _OPERATOR), spec, spec.steps[0])
        self.assertEqual(verdict, "PASS", note)


if __name__ == "__main__":
    unittest.main()
