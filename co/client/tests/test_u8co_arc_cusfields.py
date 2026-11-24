"""客户字段 arc_customer_fields：正文、expect、预演、幂等键、本地校验和命令行。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from co.client.tests.test_u8co_gl_arc import _call
from co.client.tests.test_u8co_update_close_gen import SECRET, _AUTH, _assert_signed, _argv, _run_main, _Running
from co.client.u8co_cli import _parser
from co.client.u8co_cli_assist import KEY_COMMANDS, WRITE_COMMANDS
from co.client.u8co_arc_cus_keys import CUSTOMER_FIELD_KEYS as KEYS
from co.client.u8co_arc_cus_keys import check_customer_values
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_errors import U8CoStateMismatch, error_from
from co.client.u8co_idem import WRITE_ROUTES

_CODE = "C900001"
_ROUTE = "/u8co/v1/archives/customer_fields"
_FIELDS = {"abbrname": "示例简称", "district": "33", "end_date": "2026-10-20", "sa_protocol": "A001"}
_EXPECT = {"abbrname": "示例客户有限公司", "district": "", "end_date": "", "sa_protocol": ""}


class ArcCustomerFieldsTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[str, dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertNotIn("password", sent)
        self.assertEqual(list(sent)[:5], _AUTH)
        return captured["path"], sent

    def test_minimal_body(self) -> None:
        path, sent = self._send(lambda client: client.arc_customer_fields(_call(), _CODE, dict(_FIELDS)))
        self.assertEqual(path, _ROUTE)
        self.assertEqual(list(sent), _AUTH + ["code", "fields"])
        self.assertEqual(sent["code"], _CODE)
        self.assertEqual(sent["fields"], _FIELDS)

    def test_expect_with_empty_strings_and_key(self) -> None:
        path, sent = self._send(
            lambda client: client.arc_customer_fields(
                _call(), _CODE, dict(_FIELDS), expect=dict(_EXPECT), idempotency_key="cusfields-1"
            )
        )
        self.assertEqual(path, _ROUTE)
        self.assertEqual(sent["expect"], _EXPECT)
        self.assertEqual(sent["idempotency_key"], "cusfields-1")

    def test_clearing_values_are_sent(self) -> None:
        fields = {"district": "", "end_date": ""}
        _path, sent = self._send(lambda client: client.arc_customer_fields(_call(), _CODE, fields))
        self.assertEqual(sent["fields"], fields)

    def test_sa_protocol_alone_and_clearing_pass_the_local_check(self) -> None:
        for fields, expect in (({"sa_protocol": "A001"}, {"sa_protocol": ""}), ({"sa_protocol": ""}, {"sa_protocol": "A001"})):
            _path, sent = self._send(
                lambda client, f=fields, e=expect: client.arc_customer_fields(_call(), _CODE, dict(f), expect=dict(e))
            )
            self.assertEqual(sent["fields"], fields)
            self.assertEqual(sent["expect"], expect)

    def test_name_and_tax_no_pass_the_local_check(self) -> None:
        cases = (
            ({"name": "示例客户有限公司", "tax_no": "EXAMPLE-TAX-NO-001"}, {"name": "旧名称", "tax_no": ""}),
            ({"tax_no": ""}, {"tax_no": "EXAMPLE-TAX-NO-001"}),
            ({"name": "名" * 98, "tax_no": "9" * 50}, None),
        )
        for fields, expect in cases:
            _path, sent = self._send(
                lambda client, f=fields, e=expect: client.arc_customer_fields(
                    _call(), _CODE, dict(f), expect=None if e is None else dict(e)
                )
            )
            self.assertEqual(sent["fields"], fields)
            self.assertEqual(sent.get("expect"), expect)

    def test_dry_and_keyed_clients(self) -> None:
        _path, sent = self._send(lambda client: client.dry().arc_customer_fields(_call(), _CODE, dict(_FIELDS)))
        self.assertIs(sent["dry_run"], True)
        _path, sent = self._send(
            lambda client: client.keyed("cusfields-2").arc_customer_fields(_call(), _CODE, dict(_FIELDS))
        )
        self.assertEqual(sent["idempotency_key"], "cusfields-2")

    def test_rejects_bad_requests(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        call = _call()
        bad = (
            (lambda: client.arc_customer_fields(call, "", dict(_FIELDS)), "code"),
            (lambda: client.arc_customer_fields(call, _CODE, {}), "fields"),
            (lambda: client.arc_customer_fields(call, _CODE, ["abbrname"]), "fields"),
            (lambda: client.arc_customer_fields(call, _CODE, {"cusname": "x"}), "cusname"),
            (lambda: client.arc_customer_fields(call, _CODE, {"abbrname": ""}), "abbrname"),
            (lambda: client.arc_customer_fields(call, _CODE, {"abbrname": "  "}), "abbrname"),
            (lambda: client.arc_customer_fields(call, _CODE, {"abbrname": "甲" * 61}), "abbrname"),
            (lambda: client.arc_customer_fields(call, _CODE, {"abbrname": "甲\n乙"}), "abbrname"),
            (lambda: client.arc_customer_fields(call, _CODE, {"district": "1" * 13}), "district"),
            (lambda: client.arc_customer_fields(call, _CODE, {"district": " 33"}), "district"),
            (lambda: client.arc_customer_fields(call, _CODE, {"district": 33}), "district"),
            (lambda: client.arc_customer_fields(call, _CODE, {"sa_protocol": "A" * 21}), "sa_protocol"),
            (lambda: client.arc_customer_fields(call, _CODE, {"sa_protocol": " A001"}), "sa_protocol"),
            (lambda: client.arc_customer_fields(call, _CODE, {"sa_protocol": "A001 "}), "sa_protocol"),
            (lambda: client.arc_customer_fields(call, _CODE, {"sa_protocol": "A\n001"}), "sa_protocol"),
            (lambda: client.arc_customer_fields(call, _CODE, {"sa_protocol": 1}), "sa_protocol"),
            (lambda: client.arc_customer_fields(call, _CODE, {"sa_protocol": "A001"}, expect={"sa_protocol": "A" * 21}), "expect"),
            (lambda: client.arc_customer_fields(call, _CODE, {"sa_protocol": "A001"}, expect={"sa_protocol": " A001"}), "expect.sa_protocol"),
            (lambda: client.arc_customer_fields(call, _CODE, {"sa_protocol": "A001"}, expect={"sa_protocol": "A001 "}), "expect.sa_protocol"),
            (lambda: client.arc_customer_fields(call, _CODE, {"sa_protocol": "A001"}, expect={"sa_protocol": "A\n001"}), "expect.sa_protocol"),
            (lambda: client.arc_customer_fields(call, _CODE, {"district": "33"}, expect={"district": "33 "}), "expect.district"),
            (lambda: client.arc_customer_fields(call, _CODE, {"district": "33"}, expect={"district": "3\t3"}), "expect.district"),
            (lambda: client.arc_customer_fields(call, _CODE, {"end_date": "2026/10/20"}), "end_date"),
            (lambda: client.arc_customer_fields(call, _CODE, {"end_date": "2026-02-30"}), "end_date"),
            (lambda: client.arc_customer_fields(call, _CODE, {"end_date": None}), "end_date"),
            (lambda: client.arc_customer_fields(call, _CODE, {"abbrname": "x"}, expect={"district": ""}), "district"),
            (lambda: client.arc_customer_fields(call, _CODE, {"abbrname": "x"}, expect={"abbrname": 1}), "expect"),
            (lambda: client.arc_customer_fields(call, _CODE, {"abbrname": "x"}, expect={"abbrname": "y" * 61}), "expect"),
            (lambda: client.arc_customer_fields(call, _CODE, {"end_date": "2026-10-20"}, expect={"end_date": "x"}), "end_date"),
            (lambda: client.arc_customer_fields(call, _CODE, {"abbrname": "x"}, idempotency_key="has space"), "idempotency_key"),
            (lambda: client.arc_customer_fields(call, _CODE, {"name": ""}), "name"),
            (lambda: client.arc_customer_fields(call, _CODE, {"name": "  "}), "name"),
            (lambda: client.arc_customer_fields(call, _CODE, {"name": "名" * 99}), "name"),
            (lambda: client.arc_customer_fields(call, _CODE, {"name": "示例\n客户"}), "name"),
            (lambda: client.arc_customer_fields(call, _CODE, {"tax_no": "9" * 51}), "tax_no"),
            (lambda: client.arc_customer_fields(call, _CODE, {"tax_no": 91}), "tax_no"),
            (lambda: client.arc_customer_fields(call, _CODE, {"name": "x"}, expect={"name": "名" * 99}), "expect"),
            (lambda: client.arc_customer_fields(call, _CODE, {"name": "x"}, expect={"tax_no": ""}), "tax_no"),
        )
        for invoke, word in bad:
            with self.subTest(word=word), self.assertRaises(ValueError) as caught:
                invoke()
            self.assertIn(word, str(caught.exception))

    def test_state_mismatch_exposes_current(self) -> None:
        current = {"name": "示例客户有限公司", "tax_no": ""}
        exc = error_from(409, {"code": "state_mismatch", "message": "与 expect 不符", "detail": {"current": current}})
        self.assertIsInstance(exc, U8CoStateMismatch)
        self.assertEqual(exc.current, current)
        person = error_from(409, {"code": "state_mismatch", "message": "当前专管业务员是空", "detail": {"current": {"person": ""}}})
        self.assertEqual(person.current, {"person": ""})
        # 并发修改没有 detail；detail 里的 current 不是对象时同样当没有。
        self.assertIsNone(error_from(409, {"code": "state_mismatch", "message": "客户 C900001 已被他人修改"}).current)
        self.assertIsNone(error_from(409, {"code": "state_mismatch", "message": "m", "detail": {"current": "x"}}).current)

    def test_route_is_a_write(self) -> None:
        self.assertIn("/v1/archives/customer_fields", WRITE_ROUTES)


class CustomerFieldKeyTableTests(unittest.TestCase):
    """键表驱动：每种类型的合法 / 非法值、必填、未知键、expect ⊆ fields、键名区分大小写。"""

    VALID = {
        "text": ("示例文本", "a" * 20, "", " 首尾空白 "),
        "postcode": ("200000", "ab-12", "a" * 20, ""),
        "code": ("A001", "A" * 10, ""),
        "date": ("2026-10-20", "1900-01-01", ""),
        "bit": ("0", "1"),
        "int": ("0", "-5", "2147483647", ""),
        "num": ("0", "5.0", "12.12345678", ""),
    }
    INVALID = {
        "postcode": ("ABC", "a b", "a" * 21, "20\n00", 5),
        "code": (" A001", "A001 ", "A\n001", "A" * 61, 1),
        "date": ("2026/10/20", "2026-02-30", "1899-12-31", "20261020", None),
        "bit": ("", "2", "true", " 1", 1),
        "int": ("1.5", "2147483648", "-2147483649", "1e3", "x"),
        "num": ("1e3", "1.123456789", "abc", "1,5", ".5"),
    }
    RANGES = {
        "discount_rate": (("0", "100", "99.99"), ("-0.1", "100.1")),
        "fcusdiscountrate": (("0", "100"), ("101", "-1")),
        "fadvancepaymentratio": (("50",), ("100.5",)),
        "icustaxrate": (("0", "99.99"), ("100", "-1")),
    }

    def _check(self, fields: object, expect: object = None) -> object:
        found = check_customer_values(fields, "fields", tuple(KEYS), True)
        if expect is not None:
            check_customer_values(expect, "expect", tuple(found), False)
        return found

    def _cases(self, values: dict) -> list[tuple[str, object]]:
        found = []
        for key, spec in KEYS.items():
            for value in values.get(spec.kind, ()):
                blank = isinstance(value, str) and value.strip() == ""
                if blank and spec.required or spec.kind == "text" and len(str(value).strip()) > spec.limit:
                    continue
                found.append((key, value))
        return found

    def test_table_has_the_documented_keys(self) -> None:
        self.assertEqual(len(KEYS), 58)
        self.assertEqual(list(KEYS)[:6], ["abbrname", "district", "end_date", "sa_protocol", "name", "tax_no"])
        self.assertTrue(all(key == key.lower() for key in KEYS))
        self.assertEqual(KEYS["devliver_site"].column, "cCusOAddress")
        self.assertEqual(KEYS["ccustradeccode"].column, "cCusTradeCCode")
        self.assertEqual(KEYS["self_define16"].column, "cCusDefine16")
        self.assertEqual(
            sorted(key for key, spec in KEYS.items() if spec.required), ["abbrname", "ccusexch_name", "name", "sort_code"]
        )
        self.assertEqual(len({spec.column for spec in KEYS.values()}), len(KEYS))

    def test_table_matches_the_bridge_declaration_table(self) -> None:
        path = Path(__file__).resolve().parents[3] / "co" / "bridge" / "src" / "ArcCusCol.cs"
        if not path.exists():
            self.skipTest("没有桥源码")
        row = re.compile(
            r'ArcCusCol\.(?:Text|Ref)\("([a-z0-9_]+)", "(\w+)"|ArcCusCol\.Of\(ArcCusCol\.K\w+, "([a-z0-9_]+)", "(\w+)"'
        )
        bridge = {first or third: second or fourth for first, second, third, fourth in row.findall(path.read_text("utf-8"))}
        bridge.update({f"self_define{n}": f"cCusDefine{n}" for n in range(1, 17)})
        self.assertEqual(bridge, {key: spec.column for key, spec in KEYS.items()})

    def test_every_kind_accepts_valid_values(self) -> None:
        for key, value in self._cases(self.VALID):
            with self.subTest(key=key, value=value):
                self.assertEqual(self._check({key: value}), {key: value})

    def test_every_kind_rejects_invalid_values(self) -> None:
        for key, value in self._cases(self.INVALID):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError) as caught:
                self._check({key: value})
            self.assertIn(key, str(caught.exception))

    def test_text_and_code_limits(self) -> None:
        for key, spec in KEYS.items():
            if spec.kind not in ("text", "code"):
                continue
            with self.subTest(key=key):
                self.assertEqual(self._check({key: "x" * spec.limit}), {key: "x" * spec.limit})
                for bad in ("x" * (spec.limit + 1), "甲\n乙", 7, None):
                    with self.assertRaises(ValueError):
                        self._check({key: bad})

    def test_required_keys_cannot_be_empty_but_expect_can(self) -> None:
        for key in ("abbrname", "name", "ccusexch_name", "sort_code"):
            with self.subTest(key=key):
                for bad in ("", "  "):
                    with self.assertRaises(ValueError):
                        self._check({key: bad})
                self._check({key: "X"}, {key: ""})

    def test_optional_keys_accept_the_clearing_empty_string(self) -> None:
        for key, spec in KEYS.items():
            if not spec.required and spec.kind != "bit":
                with self.subTest(key=key):
                    self.assertEqual(self._check({key: ""}, {key: ""}), {key: ""})

    def test_number_ranges(self) -> None:
        for key, (good, bad) in self.RANGES.items():
            for value in good:
                with self.subTest(key=key, value=value):
                    self._check({key: value})
            for value in bad:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self._check({key: value})
        self._check({"self_define13": "-1234567.12345678", "self_define14": "99999999999"})

    def test_self_define_kinds_follow_the_item_number(self) -> None:
        numbers = (1, 3, 4, 6, 7, 10, 11, 13, 15)
        kinds = [(KEYS[f"self_define{n}"].kind, KEYS[f"self_define{n}"].limit) for n in numbers]
        self.assertEqual(
            kinds,
            [("text", 20), ("text", 20), ("text", 60), ("text", 60), ("text", 120), ("text", 120), ("int", 0), ("num", 0), ("date", 0)],
        )
        self._check({"self_define10": "x" * 120})
        with self.assertRaises(ValueError):
            self._check({"self_define10": "x" * 121})
        with self.assertRaises(ValueError):
            self._check({"self_define13": "1234567890123456"})
        self._check({"self_define13": "1234567.12345678"})
        with self.assertRaises(ValueError):
            self._check({"bcusdomestic": "1"}, {"bcusdomestic": ""})
        with self.assertRaises(ValueError):
            self._check({"self_define17": "x"})

    def test_unknown_and_wrongly_cased_keys_are_refused(self) -> None:
        for key in ("Abbrname", "NAME", "Tax_No", "ccusBankCode", "Self_Define1", "cCusPerson", "bank"):
            with self.subTest(key=key), self.assertRaises(ValueError) as caught:
                self._check({key: "x"})
            self.assertIn(key, str(caught.exception))

    def test_expect_must_be_inside_fields_and_stays_lenient(self) -> None:
        for key, spec in KEYS.items():
            value = next(v for v in self.VALID[spec.kind] if v.strip())
            other = "memo" if key != "memo" else "phone"
            with self.subTest(key=key), self.assertRaises(ValueError):
                self._check({key: value}, {other: ""})
        self._check({"name": "x"}, {"name": " 旧 名称 "})
        self._check({"postcode": "1"}, {"postcode": "ABC 1"})
        self._check({"discount_rate": "1"}, {"discount_rate": "5.00"})
        for fields, expect in (
            ({"discount_rate": "1"}, {"discount_rate": "abc"}),
            ({"seed_date": "2026-01-01"}, {"seed_date": "2026/01/01"}),
            ({"bcusdomestic": "1"}, {"bcusdomestic": "2"}),
            ({"ccusbankcode": "A"}, {"ccusbankcode": "A "}),
            ({"memo": "x"}, {"memo": "y" * 1001}),
        ):
            with self.subTest(expect=expect), self.assertRaises(ValueError):
                self._check(fields, expect)

    def test_end_date_may_not_precede_seed_date(self) -> None:
        with self.assertRaises(ValueError):
            self._check({"seed_date": "2026-05-01", "end_date": "2026-04-30"})
        self._check({"seed_date": "2026-05-01", "end_date": "2026-05-01"})
        self._check({"seed_date": "", "end_date": "2020-01-01"})


class NewKeysSendTests(unittest.TestCase):
    """新增键经客户端整条链发出去（签名、字段原样）。"""

    _send = ArcCustomerFieldsTests._send

    def test_many_new_keys_are_sent_together(self) -> None:
        fields = {
            "contact": "联系人甲", "bank_open": "示例银行", "ccustradeccode": "01", "self_define1": "文本A",
            "bcusoverseas": "1", "discount_rate": "5", "head_corp_code": "", "seed_date": "2026-01-01",
        }
        expect = {"contact": "", "bank_open": ""}
        _path, sent = self._send(lambda client: client.arc_customer_fields(_call(), _CODE, dict(fields), expect=dict(expect)))
        self.assertEqual(sent["fields"], fields)
        self.assertEqual(sent["expect"], expect)

    def test_unknown_key_message_lists_the_whitelist(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        with self.assertRaises(ValueError) as caught:
            client.arc_customer_fields(_call(), _CODE, {"credit_line": "1"})
        self.assertIn("bank_open", str(caught.exception))


class CliTests(unittest.TestCase):
    def _file(self, data: object) -> str:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        self.addCleanup(os.unlink, handle.name)
        with handle:
            json.dump(data, handle, ensure_ascii=False)
        return handle.name

    def test_command_takes_dry_run_and_key(self) -> None:
        self.assertIn("arc-customer-fields", WRITE_COMMANDS)
        self.assertIn("arc-customer-fields", KEY_COMMANDS)
        parsed = _parser().parse_args(
            _argv("arc-customer-fields", "--code", _CODE, "--file", "edit.json", "--dry-run", "--idempotency-key", "k1")
        )
        self.assertEqual((parsed.code, parsed.file), (_CODE, "edit.json"))
        self.assertTrue(parsed.dry_run)
        self.assertEqual(parsed.idempotency_key, "k1")

    def test_main_reads_fields_and_expect_from_the_file(self) -> None:
        seen: dict[str, object] = {}

        def _op(self: U8CoClient, call: U8Call, code: str, fields: dict, **extra: object) -> dict[str, object]:
            del call
            seen["args"] = (code, fields, extra.get("expect"), self._dry)
            return {"ok": True, "code": code, "changed": True, "before": {}, "after": fields}

        path = self._file({"fields": _FIELDS, "expect": _EXPECT})
        argv = _argv("arc-customer-fields", "--code", _CODE, "--file", path, "--dry-run")
        self.assertEqual(_run_main(argv, "arc_customer_fields", _op), 0)
        self.assertEqual(seen["args"], (_CODE, _FIELDS, _EXPECT, True))
        path = self._file({"fields": _FIELDS})
        self.assertEqual(_run_main(_argv("arc-customer-fields", "--code", _CODE, "--file", path), "arc_customer_fields", _op), 0)
        self.assertEqual(seen["args"], (_CODE, _FIELDS, None, False))

    def test_file_with_other_keys_is_refused(self) -> None:
        path = self._file({"fields": _FIELDS, "archive": "customer"})
        argv = _argv("arc-customer-fields", "--code", _CODE, "--file", path)
        with self.assertRaises(SystemExit):
            _run_main(argv, "arc_customer_fields", lambda *_a, **_k: {})


if __name__ == "__main__":
    unittest.main()
