"""只读路由：字段标签 meta/fields、单据查询 vouchers/search、批量读取 vouchers/load_many、archives/get_many。"""

from __future__ import annotations

import pytest
from tests.support import base_claims
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth
from tests.test_co_update_close_gen import _spec
from u8co_api.co_call import shapeable
from u8co_api.co_routes_lookup import LOOKUP_ROUTES

_FIELDS = "/v1/co/meta/fields"
_SEARCH = "/v1/co/vouchers/search"
_LOAD_MANY = "/v1/co/vouchers/load_many"
_GET_MANY = "/v1/co/archives/get_many"

_HEAD = [
    {"name": "ccuscode", "label": "客户编码", "type": "string", "required": False, "max_length": 20},
    {"name": "cbustype", "label": "业务类型", "type": "enum", "required": True, "enum": [{"code": "a", "name": "a"}]},
]
_LINES = [{"name": "iquantity", "label": "数量", "type": "decimal", "required": True}]
_DOC = {
    "ok": True,
    "type": "sale_order",
    "id": 7,
    "code": "SO0007",
    "head": {"ccuscode": "C001", "cmemo": ""},
    "lines": [{"cinvcode": "01", "iquantity": "1"}],
    "state": {"verified": False},
}
_MISSING = {"id": 8, "error": {"code": "not_found", "message": "单据不存在"}}


class LookupBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        answer = _ANSWERS.get(path)
        if answer is None:
            return super().call(path, payload)
        self.calls.append((path, dict(payload)))
        return answer(payload)


_ARC_FIELDS = [{"name": "name", "label": "名称", "type": None, "required": True}]
_GL_FIELDS = [{"name": "account", "label": "科目", "type": "string", "required": True}]


def _fields_answer(payload: dict) -> dict:
    if "archive" in payload:
        return {"ok": True, "archive": payload["archive"], "fields": _ARC_FIELDS, "fields_revision": "cd" * 32}
    if payload.get("gl"):
        flow = [{"name": "cash_item", "label": "流量项目", "type": "string", "required": False}]
        return {
            "ok": True,
            "gl": True,
            "head": [],
            "lines": _GL_FIELDS,
            "cash_flow": flow,
            "fields_revision": "ef" * 32,
        }
    return {
        "ok": True,
        "type": payload.get("type"),
        "op": payload.get("op", "create"),
        "vt_id": 95,
        "card": "17",
        "vt_source": "card_default",
        "head": _HEAD,
        "lines": _LINES,
        "fields_revision": "ab" * 32,
    }


def _search_answer(payload: dict) -> dict:
    item = {"id": 7, "code": "SO0007", "partner_name": "张三贸易", "cmemo": None}
    return {"ok": True, "type": payload["type"], "items": [item], "next": 7}


_ANSWERS = {
    "/v1/meta/fields": _fields_answer,
    "/v1/vouchers/search": _search_answer,
    "/v1/vouchers/load_many": lambda payload: {"ok": True, "type": payload["type"], "items": [_DOC, _MISSING]},
    "/v1/archives/get_many": lambda payload: {
        "ok": True,
        "archive": payload["archive"],
        "items": [
            {"ok": True, "archive": payload["archive"], "code": "C001", "fields": {"name": "张三贸易"}},
            {"code": "C002", "error": {"code": "no_permission", "message": "无权读取"}},
        ],
    },
}


def _post(path: str, body: dict, query: str = "", fake: LookupBridge | None = None):
    bridge = fake if fake is not None else LookupBridge()
    return _client(bridge).post(path + query, json=body), bridge


# ---- meta/fields ----


def test_fields_forwards_type_and_op() -> None:
    ok, fake = _post(_FIELDS, _auth(type="sale_order", op="update"))
    assert ok.status_code == 200, ok.text
    assert ok.json()["head"] == _HEAD
    assert ok.json()["vt_id"] == 95
    assert ok.json()["vt_source"] == "card_default"
    path, payload = fake.calls[0]
    assert path == "/v1/meta/fields"
    assert payload["type"] == "sale_order"
    assert payload["op"] == "update"
    assert "archive" not in payload and "gl" not in payload


def test_fields_forwards_generate_source_archive_and_gl() -> None:
    _, fake = _post(_FIELDS, _auth(type="sale_out", op="generate", source="dispatch"))
    assert fake.calls[0][1]["source"] == "dispatch"
    _, fake = _post(_FIELDS, _auth(archive="customer"))
    assert fake.calls[0][1]["archive"] == "customer"
    _, fake = _post(_FIELDS, _auth(gl=True))
    assert fake.calls[0][1]["gl"] is True


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"type": "sale_order", "archive": "customer"},
        {"type": "sale_order", "gl": True},
        {"archive": "customer", "gl": True},
        {"archive": "customer", "op": "create"},
        {"gl": True, "source": "dispatch"},
        {"type": "sale_out", "op": "generate"},
        {"type": "sale_order", "source": "dispatch"},
        {"type": "sale_order", "op": "create", "source": "dispatch"},
    ],
)
def test_fields_needs_exactly_one_target_and_consistent_op(extra: dict) -> None:
    denied, fake = _post(_FIELDS, _auth(**extra))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


@pytest.mark.parametrize(
    ("extra", "field"),
    [
        ({"type": "nope"}, "type"),
        ({"archive": "nope"}, "archive"),
        ({"gl": False}, "gl"),
        ({"type": "sale_order", "op": "delete"}, "op"),
        ({"type": "sale_order", "x": 1}, "x"),
    ],
)
def test_fields_rejects_bad_values(extra: dict, field: str) -> None:
    denied, fake = _post(_FIELDS, _auth(**extra))
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == field
    assert fake.calls == []


def test_fields_returns_archive_fields_and_gl_groups() -> None:
    ok, _ = _post(_FIELDS, _auth(archive="customer"))
    assert ok.status_code == 200, ok.text
    assert ok.json()["fields"] == _ARC_FIELDS
    assert "head" not in ok.json()
    ok, _ = _post(_FIELDS, _auth(archive="customer"), "?fields=name")
    assert ok.json()["fields"] == [{"name": "name"}]
    ok, _ = _post(_FIELDS, _auth(gl=True))
    assert ok.status_code == 200, ok.text
    assert ok.json()["gl"] is True
    assert ok.json()["lines"] == _GL_FIELDS
    assert ok.json()["cash_flow"][0]["label"] == "流量项目"


def test_fields_openapi_declares_the_bridge_keys() -> None:
    props = _spec()["components"]["schemas"]["MetaFieldsOut"]["properties"]
    for key in ("vt_id", "card", "vt_source", "source", "head", "lines", "cash_flow", "fields", "fields_revision"):
        assert key in props, key


def test_fields_projection_applies_to_the_field_lists() -> None:
    ok, _ = _post(_FIELDS, _auth(type="sale_order"), "?fields=name,label")
    assert ok.status_code == 200, ok.text
    assert ok.json()["head"][0] == {"name": "ccuscode", "label": "客户编码"}
    assert ok.json()["lines"] == [{"name": "iquantity", "label": "数量"}]
    assert ok.json()["fields_revision"] == "ab" * 32


# ---- vouchers/search ----


def test_search_forwards_filters() -> None:
    body = _auth(
        type="sale_order",
        code_like="SO00",
        partner="C001",
        inventory="01",
        date_from="2026-09-01",
        date_to="2026-09-30",
        verified=False,
        limit=20,
        after=3,
    )
    ok, fake = _post(_SEARCH, body)
    assert ok.status_code == 200, ok.text
    assert ok.json()["items"][0]["partner_name"] == "张三贸易"
    assert ok.json()["next"] == 7
    path, payload = fake.calls[0]
    assert path == "/v1/vouchers/search"
    for key in ("code_like", "partner", "inventory", "date_from", "date_to", "limit", "after"):
        assert payload[key] == body[key]
    assert payload["verified"] is False
    assert "closed" not in payload and "warehouse" not in payload


def test_search_forwards_defines_unchanged() -> None:
    # 值原样转给桥（去空格、LIKE 转义、键是否为文本自定义项、该类型有没有表头自定义项都由桥判断）。
    defines = {
        "define1": " HT202609039 ",
        "define10": {"like": "a_b%"},
        "define11": {"prefix": "X"},
        "define2": {"eq": "y"},
    }
    ok, fake = _post(_SEARCH, _auth(type="sale_order", defines=defines))
    assert ok.status_code == 200, ok.text
    path, payload = fake.calls[0]
    assert path == "/v1/vouchers/search"
    assert payload["defines"] == defines


def test_search_leaves_define_keys_to_the_bridge() -> None:
    # 不认识的键（define17、日期列 define4）由桥 400（field 为 defines.<键>、带 hint），API 不拦。
    ok, fake = _post(_SEARCH, _auth(type="sale_order", defines={"define17": "x", "define4": "y"}))
    assert ok.status_code == 200, ok.text
    assert fake.calls[0][1]["defines"] == {"define17": "x", "define4": "y"}


def test_search_omits_defines_when_absent() -> None:
    ok, fake = _post(_SEARCH, _auth(type="sale_order"))
    assert ok.status_code == 200, ok.text
    assert "defines" not in fake.calls[0][1]


def test_search_compact_works_on_items() -> None:
    ok, _ = _post(_SEARCH, _auth(type="sale_order"), "?compact=true")
    assert ok.status_code == 200, ok.text
    assert "cmemo" not in ok.json()["items"][0]


@pytest.mark.parametrize(
    ("extra", "field"),
    [
        ({"code_like": ""}, "code_like"),
        ({"code_like": "a" * 41}, "code_like"),
        ({"partner": "a\x01"}, "partner"),
        ({"limit": 0}, "limit"),
        ({"limit": 201}, "limit"),
        ({"after": -1}, "after"),
        ({"date_from": "2026-9-1"}, "date_from"),
        ({"date_to": "2026-02-30"}, "date_to"),
        ({"verified": "yes"}, "verified"),
        ({"closed": 1}, "closed"),
        ({"filter": {}}, "filter"),
        ({"defines": {}}, "defines"),
        ({"defines": "HT202609039"}, "defines"),
        ({"defines": {f"define{n}": "x" for n in (1, 2, 3, 8, 9)}}, "defines"),
        ({"defines": {"define1": ""}}, "defines.define1"),
        ({"defines": {"define1": "   "}}, "defines.define1"),
        ({"defines": {"define1": " \u3000\u00a0"}}, "defines.define1"),
        ({"defines": {"define1": "x" * 121}}, "defines.define1"),
        ({"defines": {"define1": "a\x01"}}, "defines.define1"),
        ({"defines": {"define1": 5}}, "defines.define1"),
        ({"defines": {"define1": None}}, "defines.define1"),
        ({"defines": {"define1": {"regex": "x"}}}, "defines.define1"),
        ({"defines": {"define1": {"eq": "x", "like": "y"}}}, "defines.define1"),
        ({"defines": {"define1": {"like": 5}}}, "defines.define1"),
        ({"defines": {"define1": {}}}, "defines.define1"),
    ],
)
def test_search_rejects_bad_values(extra: dict, field: str) -> None:
    denied, fake = _post(_SEARCH, _auth(type="sale_order", **extra))
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == field
    assert fake.calls == []


def test_search_rejects_a_reversed_date_range_and_needs_type() -> None:
    denied, fake = _post(_SEARCH, _auth(type="sale_order", date_from="2026-09-02", date_to="2026-09-01"))
    assert denied.status_code == 400
    denied, fake = _post(_SEARCH, _auth(code_like="SO"), fake=fake)
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == "type"
    assert fake.calls == []


# ---- vouchers/load_many ----


def test_load_many_forwards_ids_and_keeps_item_errors() -> None:
    ok, fake = _post(_LOAD_MANY, _auth(type="sale_order", ids=[7, 8]))
    assert ok.status_code == 200, ok.text
    assert ok.json()["items"] == [_DOC, _MISSING]
    path, payload = fake.calls[0]
    assert path == "/v1/vouchers/load_many"
    assert payload["ids"] == [7, 8]


def test_load_many_projects_only_the_top_level_item_keys() -> None:
    # id、code、ok、error 总是保留（co_shape），items[].head 不裁剪。
    ok, _ = _post(_LOAD_MANY, _auth(type="sale_order", ids=[7, 8]), "?fields=head")
    assert ok.status_code == 200, ok.text
    first, second = ok.json()["items"]
    assert first == {"ok": True, "id": 7, "code": "SO0007", "head": _DOC["head"]}
    assert second == _MISSING


@pytest.mark.parametrize(
    ("ids", "field"),
    [([], "ids"), (list(range(1, 22)), "ids"), ([1, 1], "ids"), ([0], "ids.0"), ([2147483648], "ids.0")],
)
def test_load_many_rejects_bad_ids(ids: list, field: str) -> None:
    denied, fake = _post(_LOAD_MANY, _auth(type="sale_order", ids=ids))
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == field
    assert fake.calls == []


def test_load_many_accepts_twenty_ids_and_leaves_the_com_cap_to_the_bridge() -> None:
    ok, fake = _post(_LOAD_MANY, _auth(type="production_order", ids=list(range(1, 21))))
    assert ok.status_code == 200, ok.text
    assert len(fake.calls[0][1]["ids"]) == 20


def test_load_many_audits_type_and_ids() -> None:
    from u8co_api.co_models_lookup import LoadManyIn

    body = LoadManyIn(**_auth(type="sale_order", ids=[3, 4]))
    assert body.audit_ref() == "sale_order:3,4"


# ---- archives/get_many ----


def test_get_many_forwards_codes_and_keeps_item_errors() -> None:
    ok, fake = _post(_GET_MANY, _auth(archive="customer", codes=["C001", "C002"]))
    assert ok.status_code == 200, ok.text
    items = ok.json()["items"]
    assert items[0]["fields"] == {"name": "张三贸易"}
    assert items[1]["error"]["code"] == "no_permission"
    path, payload = fake.calls[0]
    assert path == "/v1/archives/get_many"
    assert payload["codes"] == ["C001", "C002"]


def test_get_many_accepts_the_single_get_code_forms() -> None:
    ok, _ = _post(_GET_MANY, _auth(archive="exchange_rate", codes=["美元:2026-09-01", "美元:2026:9"]))
    assert ok.status_code == 200, ok.text
    ok, _ = _post(_GET_MANY, _auth(archive="project", codes=["01:P001"]))
    assert ok.status_code == 200, ok.text


@pytest.mark.parametrize(
    ("extra", "field"),
    [
        ({"archive": "customer", "codes": []}, "codes"),
        ({"archive": "customer", "codes": [f"C{i}" for i in range(21)]}, "codes"),
        ({"archive": "customer", "codes": ["C001", "C001"]}, "codes"),
        ({"archive": "customer", "codes": ["c001", "C001"]}, "codes"),
        ({"archive": "customer", "codes": [" C001"]}, "codes.0"),
        ({"archive": "customer", "codes": ["C" * 31]}, "codes"),
        ({"archive": "project", "codes": ["P001"]}, "codes"),
        ({"archive": "nope", "codes": ["C001"]}, "archive"),
    ],
)
def test_get_many_rejects_bad_codes(extra: dict, field: str) -> None:
    denied, fake = _post(_GET_MANY, _auth(**extra))
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == field
    assert fake.calls == []


# ---- 共同 ----


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (_FIELDS, _auth(archive="customer")),
        (_SEARCH, _auth(type="dispatch")),
        (_LOAD_MANY, _auth(type="bom", ids=[1])),
        (_GET_MANY, _auth(archive="inventory", codes=["01"])),
    ],
)
def test_read_only_callers_may_call_the_new_routes(path: str, body: dict) -> None:
    fake = LookupBridge()
    ok = _client(fake, claims=base_claims(u8co_read=True)).post(path, json=body)
    assert ok.status_code == 200, ok.text
    assert len(fake.calls) == 1


def test_openapi_describes_the_new_read_routes() -> None:
    spec = _spec()
    want = {
        _FIELDS: "coMetaFields",
        _SEARCH: "coVoucherSearch",
        _LOAD_MANY: "coVoucherLoadMany",
        _GET_MANY: "coArchiveGetMany",
    }
    for path, operation_id in want.items():
        operation = spec["paths"][path]["post"]
        assert operation["operationId"] == operation_id
        assert operation["x-u8co-access"] == "read"
        assert "权限：只读" in operation["description"]
        assert "x-u8co-dry-run" not in operation
    assert {route.path for route in LOOKUP_ROUTES} == set(want)
    assert all(shapeable(route.out_model) for route in LOOKUP_ROUTES)
