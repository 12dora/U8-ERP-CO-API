"""只读例外（U8CO_READONLY_EXCEPTIONS）的客户字段、收货地址（改、增、删）、地区分类新增 / 删除：
闸门、审计、预演、健康摘要。解析规则和 customer_person 在 test_readonly_exceptions。假桥，不访问网络。
"""

from __future__ import annotations

import json

import pytest
from tests.test_co_dryrun import DryBridge
from tests.test_co_gate import FakeBridge, _login
from tests.test_readonly_exceptions import (
    _ADDRESS,
    _ADDRESS_ADD,
    _ADDRESS_DELETE,
    _CREATE,
    _CUSTOMER_OPS,
    _DELETE,
    _FIELDS,
    _ITEM,
    _PERSON,
    _RO,
    _SUMMARY,
    _UPDATE,
    _audit,
    _gated,
    _match,
    _refused,
    _rules,
    _update,
)
from u8co_api.readonly_exceptions import ReadOnlyException, allows, parse_exceptions


def _fields(**over) -> dict:
    body = _login(operator="svc01", code="C900001", fields={"abbrname": "示例简称"}, expect={"abbrname": "旧简称"})
    body.update(over)
    return body


def _address(**over) -> dict:
    body = _login(
        operator="svc01",
        code="C900001",
        address="1",
        fields={"name": "示例车间"},
        expect={"name": "3车间"},
    )
    body.update(over)
    return body


def _address_add(**over) -> dict:
    body = _login(operator="svc01", code="C900001", address="2", fields={"name": "示例车间二"})
    body.update(over)
    return body


def _address_delete(**over) -> dict:
    body = _login(
        operator="svc01",
        code="C900001",
        address="2",
        expect={"name": "示例车间二", "unit": "", "default": False},
    )
    body.update(over)
    return body


def _drop(**over) -> dict:
    body = _login(operator="svc01", archive="district_class", code="33")
    body.update(over)
    return body


def _district(**over) -> dict:
    body = _login(operator="svc01", archive="district_class", code="33", fields={"name": "示例地区"})
    body.update(over)
    return body


def _all_rules() -> tuple:
    items = [{**_ITEM, "op": op} for op in _CUSTOMER_OPS]
    items.extend({**_ITEM, "op": op, "archives": ["district_class"]} for op in ("create", "delete"))
    return parse_exceptions(json.dumps(items), _RO)


@pytest.mark.parametrize(
    ("path", "body", "bridge"),
    (
        (_FIELDS, _fields(), "/v1/archives/customer_fields"),
        (_ADDRESS, _address(), "/v1/archives/customer_address"),
        (_ADDRESS_ADD, _address_add(), "/v1/archives/customer_address_add"),
        (_ADDRESS_DELETE, _address_delete(), "/v1/archives/customer_address_delete"),
        (_CREATE, _district(), "/v1/archives/create"),
        (_DELETE, _drop(), "/v1/archives/delete"),
    ),
)
def test_each_opened_op_is_admitted(path: str, body: dict, bridge: str) -> None:
    fake = FakeBridge()
    ok = _gated(fake, read_only_exceptions=_all_rules()).post(path, json=body)
    assert ok.status_code == 200, ok.text
    assert [call[0] for call in fake.calls] == [bridge]


_OWN = (
    ("customer_person", _PERSON),
    ("customer_fields", _FIELDS),
    ("customer_address", _ADDRESS),
    ("customer_address_add", _ADDRESS_ADD),
    ("customer_address_delete", _ADDRESS_DELETE),
)


@pytest.mark.parametrize("path", [own for _, own in _OWN])
def test_a_rule_admits_only_its_own_op(path: str) -> None:
    bodies = {
        _PERSON: _match(),
        _FIELDS: _fields(),
        _ADDRESS: _address(),
        _ADDRESS_ADD: _address_add(),
        _ADDRESS_DELETE: _address_delete(),
    }
    for op, own in _OWN:
        fake = FakeBridge()
        client = _gated(fake, read_only_exceptions=_rules(op=op))
        response = client.post(path, json=bodies[path])
        if path == own:
            assert response.status_code == 200, response.text
            assert len(fake.calls) == 1
        else:
            _refused(response)
            assert fake.calls == []


def test_create_is_admitted_only_for_listed_archives() -> None:
    fake = FakeBridge()
    client = _gated(fake, read_only_exceptions=_all_rules())
    assert client.post(_CREATE, json=_district()).status_code == 200
    assert len(fake.calls) == 1
    # 其它档案的新增照旧 403，即使请求体里也有 fields。
    _refused(client.post(_CREATE, json=_update(fields={"name": "客户甲", "spec_operator": "EX040001"})))
    _refused(client.post(_CREATE, json=_district(archive="sale_type")))
    _refused(client.post(_CREATE, json=_district(archive="aa_bank", code="ZZ001")))
    assert len(fake.calls) == 1


def test_delete_is_admitted_only_for_listed_archives() -> None:
    fake = FakeBridge()
    client = _gated(fake, read_only_exceptions=_all_rules())
    assert client.post(_DELETE, json=_drop()).status_code == 200
    assert len(fake.calls) == 1
    _refused(client.post(_DELETE, json=_drop(archive="customer")))
    _refused(client.post(_DELETE, json=_drop(archive="sale_type")))
    _refused(client.post(_DELETE, json=_drop(operator="op001")))
    assert len(fake.calls) == 1


def test_create_rule_alone_does_not_open_delete_or_update() -> None:
    fake = FakeBridge()
    only_create = _rules(op="create", archives=["district_class"])
    client = _gated(fake, read_only_exceptions=only_create)
    _refused(client.post(_DELETE, json=_drop()))
    _refused(client.post(_UPDATE, json=_district(fields={"name": "改名"})))
    assert fake.calls == []
    only_delete = _rules(op="delete", archives=["district_class"])
    _refused(_gated(fake, read_only_exceptions=only_delete).post(_CREATE, json=_district()))
    assert fake.calls == []


def test_update_stays_refused_with_all_rules() -> None:
    fake = FakeBridge()
    _refused(_gated(fake, read_only_exceptions=_all_rules()).post(_UPDATE, json=_district(fields={"name": "改名"})))
    assert fake.calls == []


def test_create_rule_checks_account_and_operator() -> None:
    fake = FakeBridge()
    client = _gated(fake, read_only_exceptions=_all_rules())
    _refused(client.post(_CREATE, json=_district(operator="op001")))
    assert fake.calls == []


def test_allows_matches_the_path_and_the_archive() -> None:
    rules = _all_rules()
    district = {"acc": "803", "operator": "svc01", "archive": "district_class"}
    assert allows(rules, "co:archives/create", district) is True
    assert allows(rules, "co:archives/create", {**district, "archive": "customer"}) is False
    assert allows(rules, "co:archives/create", {"acc": "803", "operator": "svc01"}) is False
    assert allows(rules, "co:archives/create", {**district, "archive": ["district_class"]}) is False
    assert allows(rules, "co:archives/update", district) is False
    assert allows(rules, "co:archives/delete", district) is True
    assert allows(rules, "co:archives/delete", {**district, "archive": "customer"}) is False
    assert allows(rules, "co:archives/delete", {"acc": "803", "operator": "svc01"}) is False
    assert allows(rules, "co:archives/customer_fields", district) is True
    assert allows(rules, "co:archives/customer_address", district) is True
    assert allows(rules, "co:archives/customer_address_add", district) is True
    assert allows(rules, "co:archives/customer_address_delete", district) is True
    assert allows(rules, "co:archives/customer_address_x", district) is False
    assert allows(_rules(op="customer_address"), "co:archives/customer_address_add", district) is False
    assert allows(_rules(op="customer_address_add"), "co:archives/customer_address", district) is False
    assert allows(_rules(op="customer_address_add"), "co:archives/customer_address_delete", district) is False
    assert allows(rules, "co:archives/customer_fields/x", district) is False
    assert allows(rules, "co:vouchers/create", district) is False
    assert allows(rules, "archives/create", district) is False
    assert allows(_rules(op="customer_fields"), "co:archives/customer_address", district) is False
    assert allows(_rules(op="create", archives=["district_class"]), "co:archives/customer_person", district) is False


def test_matching_new_ops_record_the_exception_outcome(capsys) -> None:
    client = _gated(FakeBridge(), read_only_exceptions=_all_rules())
    for path, body, op in (
        (_FIELDS, _fields(), "customer_fields"),
        (_ADDRESS, _address(), "customer_address"),
        (_ADDRESS_ADD, _address_add(), "customer_address_add"),
        (_ADDRESS_DELETE, _address_delete(), "customer_address_delete"),
        (_CREATE, _district(), "create"),
        (_DELETE, _drop(), "delete"),
    ):
        assert client.post(path, json=body).status_code == 200
        line = _audit(capsys.readouterr().out, path)
        assert line["outcome"] == "read_only_exception"
        assert line["read_only_exception"] is True
        assert (line["type"], line["op"]) == ("archives", op)


def test_dry_run_of_new_ops_passes_the_gate() -> None:
    fake = DryBridge()
    client = _gated(fake, read_only_exceptions=_all_rules())
    bodies = (
        (_FIELDS, _fields()),
        (_ADDRESS, _address()),
        (_ADDRESS_ADD, _address_add()),
        (_ADDRESS_DELETE, _address_delete()),
        (_CREATE, _district()),
        (_DELETE, _drop()),
    )
    for path, body in bodies:
        assert client.post(path, json={**body, "dry_run": True}).status_code == 200
    assert [call[1]["dry_run"] for call in fake.calls] == [True] * len(bodies)


def test_health_summary_has_archives_only_for_create_and_delete() -> None:
    data = _gated(read_only_exceptions=_all_rules()).get("/v1/co/health").json()
    summaries = data["api_read_only_exceptions"]
    assert [item["op"] for item in summaries] == [*_CUSTOMER_OPS, "create", "delete"]
    assert [item.get("archives") for item in summaries] == [None] * 5 + [["district_class"]] * 2
    assert all("archives" not in item for item in summaries[:5])


class _CreateExBridge(FakeBridge):
    def health(self) -> dict:
        summary = {**_SUMMARY, "op": "create", "archives": ["district_class"]}
        return {"ok": True, "version": "test", "read_only_exceptions": [summary]}


def test_health_keeps_the_bridge_archives_key() -> None:
    data = _gated(_CreateExBridge()).get("/v1/co/health").json()
    assert data["read_only_exceptions"][0]["archives"] == ["district_class"]


@pytest.mark.parametrize("op", ("create", "delete"))
@pytest.mark.parametrize("archives", (None, ()))
def test_rule_without_archives_never_covers_create_or_delete(op: str, archives: tuple | None) -> None:
    # 解析器要求这两个 op 必须带 archives；对象被直接构造成没有时也不能放行。
    rule = ReadOnlyException(("803",), ("svc01",), "archives", op, archives)
    payload = {"acc": "803", "operator": "svc01", "archive": "district_class"}
    assert rule.covers(payload) is False
    assert allows((rule,), f"co:archives/{op}", payload) is False
