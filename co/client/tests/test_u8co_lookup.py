"""字段标签 meta_fields、单据搜索 search、批量读取 load_many / get_many 与对应命令行。只监听 127.0.0.1。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from typing import Any

from co.client.tests.test_u8co_update_close_gen import _AUTH, SECRET, _argv, _assert_signed, make_call, _run_main, _Running
from co.client.u8co_cli import _parser
from co.client.u8co_cli_lookup import parse_codes, parse_ids, search_query
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_lookup import FieldsTarget, SearchQuery, fields_body, get_many_body, load_many_body, search_body


def _sent(bridge: _Running) -> dict[str, Any]:
    return json.loads(bridge.capture["body"].decode("utf-8"))


class MetaFieldsTests(unittest.TestCase):
    def test_wire_body(self) -> None:
        with _Running() as bridge:
            client = U8CoClient(bridge.base_url, SECRET, timeout=5)
            client.meta_fields(make_call(), FieldsTarget(kind="sale_order"))
            _assert_signed(self, bridge.capture)
            self.assertEqual(bridge.capture["path"], "/u8co/v1/meta/fields")
            self.assertEqual(list(_sent(bridge)), _AUTH + ["type"])
            client.meta_fields(make_call(), FieldsTarget(kind="dispatch", op="generate", source="sale_order"))
            sent = _sent(bridge)
            self.assertEqual((sent["type"], sent["op"], sent["source"]), ("dispatch", "generate", "sale_order"))
            client.meta_fields(make_call(), FieldsTarget(gl=True))
            self.assertIs(_sent(bridge)["gl"], True)

    def test_exactly_one_target(self) -> None:
        self.assertEqual(fields_body(make_call(), FieldsTarget(archive="customer"))["archive"], "customer")
        bad = (
            FieldsTarget(),
            FieldsTarget(kind="sale_order", archive="customer"),
            FieldsTarget(kind="sale_order", gl=True),
            FieldsTarget(kind="no_such_kind"),
            FieldsTarget(archive="no_such_archive"),
            FieldsTarget(kind="sale_order", op="delete"),
            FieldsTarget(kind="dispatch", op="generate"),
            FieldsTarget(kind="dispatch", source="sale_order"),
            FieldsTarget(archive="customer", op="update"),
            FieldsTarget(gl=True, op="create"),
        )
        for target in bad:
            with self.subTest(target=target), self.assertRaises(ValueError):
                fields_body(make_call(), target)


class SearchTests(unittest.TestCase):
    def test_wire_body(self) -> None:
        query = SearchQuery("sale_order", code_like="SO", partner="C001", date_from="2026-09-01", date_to="2026-09-30",
                            verified=True, after=10, limit=5)
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).search(make_call(), query)
            sent = _sent(bridge)
            self.assertEqual(bridge.capture["path"], "/u8co/v1/vouchers/search")
            self.assertEqual(list(sent), _AUTH + ["type", "code_like", "partner", "date_from", "date_to", "verified",
                                                  "after", "limit"])
            self.assertIs(sent["verified"], True)

    def test_defaults_omitted_and_false_kept(self) -> None:
        fields = search_body(make_call(), SearchQuery("purchase_order", closed=False))
        self.assertEqual(list(fields), ["acc", "year", "operator", "password", "date", "type", "closed"])
        self.assertIs(fields["closed"], False)

    def test_rejects_bad_values(self) -> None:
        bad = (
            SearchQuery("no_such_kind"),
            SearchQuery("sale_order", code_like="x" * 41),
            SearchQuery("sale_order", partner="x" * 61),
            SearchQuery("sale_order", date_from="2026-13-01"),
            SearchQuery("sale_order", date_from="2026-09-30", date_to="2026-09-01"),
            SearchQuery("sale_order", verified=1),  # type: ignore[arg-type]
            SearchQuery("sale_order", limit=201),
            SearchQuery("sale_order", limit=0),
            SearchQuery("sale_order", after=-1),
        )
        for query in bad:
            with self.subTest(query=query), self.assertRaises(ValueError):
                search_body(make_call(), query)


class SearchDefinesTests(unittest.TestCase):
    def test_defines_sent_as_given(self) -> None:
        defines = {"define1": "HT202601001", "define10": {"like": "a_b"}, "define11": {"prefix": "X"}}
        fields = search_body(make_call(), SearchQuery("sale_order", defines=defines))
        self.assertEqual(fields["defines"], defines)
        self.assertNotIn("defines", search_body(make_call(), SearchQuery("sale_order")))

    def test_defines_rejected(self) -> None:
        bad = (
            {},
            "HT001",
            {f"define{n}": "x" for n in (1, 2, 3, 8, 9)},
            {"define17": "x"},
            {"define4": "x"},
            {"define01": "x"},
            {"define1": ""},
            {"define1": "  "},
            {"define1": " \u3000\u00a0"},
            {"define1\x00": "x"},
            {"define1": "x" * 121},
            {"define1": "a\x01"},
            {"define1": 5},
            {"define1": {"regex": "x"}},
            {"define1": {"eq": "x", "like": "y"}},
            {"define1": {"like": 5}},
        )
        for defines in bad:
            with self.subTest(defines=defines), self.assertRaises(ValueError):
                search_body(make_call(), SearchQuery("sale_order", defines=defines))  # type: ignore[arg-type]

    def test_cli_define_flags(self) -> None:
        argv = _argv("search", "--type", "sale_order", "--define", "define1=HT202601001",
                     "--define-like", "define10=a=b", "--define-prefix", "define11=X")
        query = search_query(_parser().parse_args(argv))
        self.assertEqual(query.defines, {"define1": "HT202601001", "define10": {"like": "a=b"},
                                         "define11": {"prefix": "X"}})
        plain = search_query(_parser().parse_args(_argv("search", "--type", "sale_order")))
        self.assertIsNone(plain.defines)
        twice = ("--define", "define1=a", "--define-like", "define1=b")
        for flags in (("--define", "define1"), ("--define", "=x"), twice):
            with self.subTest(flags=flags), self.assertRaises(SystemExit):
                search_query(_parser().parse_args(_argv("search", "--type", "sale_order", *flags)))


class BatchTests(unittest.TestCase):
    def test_wire_bodies(self) -> None:
        with _Running() as bridge:
            client = U8CoClient(bridge.base_url, SECRET, timeout=5)
            client.load_many(make_call(), "sale_order", [3, 1])
            self.assertEqual(bridge.capture["path"], "/u8co/v1/vouchers/load_many")
            self.assertEqual(list(_sent(bridge)), _AUTH + ["type", "ids"])
            self.assertEqual(_sent(bridge)["ids"], [3, 1])
            client.get_many(make_call(), "customer", ["C001", "C002"])
            self.assertEqual(bridge.capture["path"], "/u8co/v1/archives/get_many")
            self.assertEqual(_sent(bridge)["codes"], ["C001", "C002"])

    def test_rejects_bad_batches(self) -> None:
        for ids in ([], [1, 1], [0], [True], ["1"], list(range(1, 22)), "1,2", [{}]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                load_many_body(make_call(), "sale_order", ids)
        with self.assertRaises(ValueError):
            load_many_body(make_call(), "no_such_kind", [1])
        self.assertEqual(len(load_many_body(make_call(), "purchase_invoice", list(range(1, 21)))["ids"]), 20)
        for codes in ([], ["A", "A"], [""], [1], ["x" * 61], [{}]):
            with self.subTest(codes=codes), self.assertRaises(ValueError):
                get_many_body(make_call(), "customer", codes)
        with self.assertRaises(ValueError):
            get_many_body(make_call(), "no_such_archive", ["A"])


class CliTests(unittest.TestCase):
    def test_parse_helpers(self) -> None:
        self.assertEqual(parse_ids("3, 1"), [3, 1])
        self.assertEqual(parse_codes("C001,C002"), ["C001", "C002"])
        for bad in ("", "1,,2", "a", "-1"):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                parse_ids(bad)
        with self.assertRaises(SystemExit):
            parse_codes("A,,B")

    def test_search_query_from_flags(self) -> None:
        parsed = _parser().parse_args(_argv("search", "--type", "sale_order", "--partner", "C001", "--verified", "false",
                                            "--limit", "5"))
        query = search_query(parsed)
        self.assertEqual((query.kind, query.partner, query.verified, query.closed, query.limit),
                         ("sale_order", "C001", False, None, 5))

    def test_meta_fields_needs_one_target(self) -> None:
        for extra in ((), ("--type", "sale_order", "--gl")):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                _parser().parse_args(_argv("meta-fields", *extra))

    def test_main_wires_commands(self) -> None:
        seen: dict[str, Any] = {}

        def _fields(self: U8CoClient, call: U8Call, target: FieldsTarget) -> dict[str, Any]:
            seen["fields"] = target
            return {"ok": True}

        def _many(self: U8CoClient, call: U8Call, kind: str, ids: object) -> dict[str, Any]:
            seen["many"] = (kind, ids)
            return {"ok": True, "items": []}

        def _codes(self: U8CoClient, call: U8Call, archive: str, codes: object) -> dict[str, Any]:
            seen["codes"] = (archive, codes)
            return {"ok": True, "items": []}

        self.assertEqual(_run_main(_argv("meta-fields", "--archive", "customer"), "meta_fields", _fields), 0)
        self.assertEqual(seen["fields"], FieldsTarget(archive="customer"))
        self.assertEqual(_run_main(_argv("load-many", "--type", "sale_order", "--ids", "7,8"), "load_many", _many), 0)
        self.assertEqual(seen["many"], ("sale_order", [7, 8]))
        argv = _argv("arc-get-many", "--archive", "customer", "--codes", "C1,C2")
        self.assertEqual(_run_main(argv, "get_many", _codes), 0)
        self.assertEqual(seen["codes"], ("customer", ["C1", "C2"]))


if __name__ == "__main__":
    unittest.main()
