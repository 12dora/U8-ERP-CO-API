"""BOM (type=bom): create / update / verify / delete body checks, BOM-only response fields and bridge errors."""

from __future__ import annotations

import pytest
from tests.support import make_client
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards
from u8co_api.errors import ApiError

_KIND = "bom"
_CREATE = "/v1/co/vouchers/create"
_UPDATE = "/v1/co/vouchers/update"
_VERIFY = "/v1/co/vouchers/verify"
_DELETE = "/v1/co/vouchers/delete"
_LOAD = "/v1/co/vouchers/load"
_HEAD = {"inv_code": "P001"}
_LINE = {"inv_code": "C001", "base_qty_n": 2}


def _body(head: dict | None = None, lines: list[dict] | None = None) -> dict:
    return _auth(type=_KIND, head=dict(_HEAD) if head is None else head, lines=[dict(_LINE)] if lines is None else lines)


def _edit(head: dict | None = None, lines: list[dict] | None = None) -> dict:
    body = _auth(type=_KIND, id=9)
    if head is not None:
        body["head"] = head
    if lines is not None:
        body["lines"] = lines
    return body


def _saved(payload: dict) -> dict:
    lines = payload.get("lines") or []
    components = [
        {"line_id": 50 + i, "sort_seq": 10 * (i + 1), "inv_code": row.get("inv_code", "C001"), "base_qty_n": 2, "base_qty_d": 1}
        for i, row in enumerate(lines)
    ]
    return {
        "ok": True,
        "type": payload["type"],
        "id": payload.get("id", 1000000053),
        "code": "P001",
        "version": 40,
        "state": {"verified": False, "verifier": "", "verified_at": "", "closed": False},
        "lines": len(components),
        "components": components,
    }


class _BomBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        if path in ("/v1/vouchers/create", "/v1/vouchers/update"):
            return _saved(payload)
        if path == "/v1/vouchers/verify":
            state = {"verified": payload["action"] == "verify", "verifier": "张三", "verified_at": "2026-09-28"}
            return {"ok": True, "type": _KIND, "id": payload["id"], "code": "P001", "action": payload["action"], "state": state}
        return {"ok": True, "type": _KIND, "id": payload["id"], "code": "P001", "deleted": True}


def test_bom_routes_forward_exact_keys() -> None:
    _forwards(_CREATE, "/v1/vouchers/create", _body(), _sealed("type", "head", "lines"))
    _forwards(_UPDATE, "/v1/vouchers/update", _edit({"version_desc": "x"}), _sealed("type", "id", "head"))
    _forwards(_DELETE, "/v1/vouchers/delete", _auth(type=_KIND, id=9), _sealed("type", "id"))
    _forwards(_VERIFY, "/v1/vouchers/verify", _auth(type=_KIND, id=9, action="verify"), _sealed("type", "id", "action"))
    _forwards(_LOAD, "/v1/vouchers/load", _auth(type=_KIND, id=9), _sealed("type", "id"))


def test_bom_create_keeps_version_and_components() -> None:
    fake = _BomBridge()
    lines = [dict(_LINE), {"inv_code": "C002", "base_qty_n": 1.5, "base_qty_d": 1000, "wip_type": 1, "sort_seq": 30}]
    head = {"inv_code": "P001", "version": 40, "version_desc": "说明", "eff_date": "2026-10-01", "parent_scrap": 1.5}
    done = _client(fake).post(_CREATE, json=_body(head, lines))
    assert done.status_code == 200, done.text
    body = done.json()
    assert fake.calls[-1][1]["head"] == head
    assert (body["code"], body["version"], body["lines"]) == ("P001", 40, 2)
    assert body["components"][0] == {"line_id": 50, "sort_seq": 10, "inv_code": "C001", "base_qty_n": 2, "base_qty_d": 1}
    assert not {"allocates", "details", "warnings"} & set(body)


def test_other_create_has_no_bom_fields() -> None:
    done = _client(FakeBridge()).post(_CREATE, json=_auth(type="other_in", head={"a": "b"}, lines=[{"a": 1}]))
    assert done.status_code == 200, done.text
    assert not {"version", "components"} & set(done.json())


@pytest.mark.parametrize(
    "body",
    [
        _body({}),
        _body({"inv_code": "  "}),
        _body({"inv_code": "P001", "code": "X"}),
        _body({"inv_code": "P001", "version": 0}),
        _body({"inv_code": "P001", "version": "40"}),
        _body({"inv_code": "P001", "version": True}),
        _body({"inv_code": "P001", "eff_date": "2026-9-28"}),
        _body({"inv_code": "P001", "parent_scrap": 100}),
        _body({"inv_code": "P001", "version_desc": "X" * 256}),
        _body(lines=[]),
        _body(lines=[dict(_LINE)] * 201),
        _body(lines=[{"inv_code": "C001"}]),
        _body(lines=[{"base_qty_n": 1}]),
        _body(lines=[dict(_LINE, base_qty_n=0)]),
        _body(lines=[dict(_LINE, base_qty_n=0.0000001)]),
        _body(lines=[dict(_LINE, base_qty_d=-1)]),
        _body(lines=[dict(_LINE, comp_scrap=0.0001)]),
        _body(lines=[dict(_LINE, wip_type=6)]),
        _body(lines=[dict(_LINE, op_seq="00001")]),
        _body(lines=[dict(_LINE, wh_code="W" * 11)]),
        _body(lines=[dict(_LINE, sort_seq=10), dict(_LINE, sort_seq=10)]),
        _body(lines=[dict(_LINE, line_id=1)]),
        _body(lines=[dict(_LINE, remark="备\t注")]),
    ],
)
def test_bom_create_rejects_before_the_bridge(body: dict) -> None:
    fake = _BomBridge()
    denied = _client(fake).post(_CREATE, json=body)
    assert denied.status_code in (400, 422), denied.text
    assert fake.calls == []


def test_bom_update_forwards_sort_seq_rows() -> None:
    fake = _BomBridge()
    lines = [
        {"op": "update", "sort_seq": 10, "base_qty_n": 3},
        {"op": "delete", "sort_seq": 20},
        {"op": "add", "inv_code": "C003", "base_qty_n": 1},
    ]
    done = _client(fake).post(_UPDATE, json=_edit({"eff_date": "2026-10-01"}, lines))
    assert done.status_code == 200, done.text
    assert fake.calls[-1][1]["lines"] == lines
    body = done.json()
    assert body["id"] == 9
    # 修改的响应同新增：桥给的 version、components 不在 API 层丢掉。
    assert (body["version"], body["lines"], len(body["components"])) == (40, 3, 3)
    assert body["components"][2] == {"line_id": 52, "sort_seq": 30, "inv_code": "C003", "base_qty_n": 2, "base_qty_d": 1}


@pytest.mark.parametrize(
    "body",
    [
        _edit(),
        _edit({"inv_code": "P002"}),
        _edit({"version": 50}),
        _edit(lines=[{"op": "update", "line_id": 5, "base_qty_n": 2}]),
        _edit(lines=[{"op": "update", "sort_seq": 10}]),
        _edit(lines=[{"op": "update", "sort_seq": 10, "inv_code": "C009"}]),
        _edit(lines=[{"op": "update", "sort_seq": 10, "remark": " "}]),
        _edit(lines=[{"op": "delete", "sort_seq": 10, "base_qty_n": 2}]),
        _edit(lines=[{"op": "delete", "sort_seq": 10}, {"op": "update", "sort_seq": 10, "base_qty_n": 2}]),
        _edit(lines=[{"op": "add", "base_qty_n": 1}]),
        _edit(lines=[{"op": "move", "sort_seq": 10}]),
    ],
)
def test_bom_update_rejects_before_the_bridge(body: dict) -> None:
    fake = _BomBridge()
    denied = _client(fake).post(_UPDATE, json=body)
    assert denied.status_code in (400, 422), denied.text
    assert fake.calls == []


def test_bom_verify_and_delete() -> None:
    fake = _BomBridge()
    done = _client(fake).post(_VERIFY, json=_auth(type=_KIND, id=9, action="verify"))
    assert done.status_code == 200, done.text
    assert done.json()["state"]["verified"] is True
    gone = _client(fake).post(_DELETE, json=_auth(type=_KIND, id=9))
    assert gone.status_code == 200, gone.text
    assert gone.json()["deleted"] is True


def test_bom_create_takes_an_idempotency_key() -> None:
    fake = _BomBridge()
    done = _client(fake).post(_CREATE, json=_body(), headers={"Idempotency-Key": "bom-2026-0001"})
    assert done.status_code == 200, done.text
    assert fake.calls[-1][1]["idempotency_key"] == "bom-2026-0001"


@pytest.mark.parametrize(
    ("path", "body", "status", "code"),
    [
        (_CREATE, _body(), 409, "u8_rejected"),
        (_CREATE, _body(), 409, "state_mismatch"),
        (_CREATE, _body(), 503, "u8_unavailable"),
        (_CREATE, _body(), 504, "outcome_unknown"),
        (_CREATE, _body(), 403, "no_permission"),
        (_UPDATE, _edit({"version_desc": "x"}), 409, "state_mismatch"),
        (_VERIFY, _auth(type=_KIND, id=9, action="unverify"), 409, "workflow_enabled"),
        (_DELETE, _auth(type=_KIND, id=9), 409, "state_mismatch"),
        (_DELETE, _auth(type=_KIND, id=9), 404, "not_found"),
    ],
)
def test_bom_passes_bridge_errors(path: str, body: dict, status: int, code: str) -> None:
    fake = _BomBridge()
    fake.error = ApiError(status, code, "说明")
    denied = _client(fake).post(path, json=body)
    assert denied.status_code == status
    assert denied.json()["error"]["code"] == code


def test_openapi_lists_bom() -> None:
    schemas = make_client().get("/v1/openapi.json").json()["components"]["schemas"]
    for name in ("CoCreateIn", "CoDeleteIn", "CoVoucherVerifyIn", "CoLoadIn", "CoUpdateIn"):
        assert _KIND in schemas[name]["properties"]["type"]["enum"], name
    assert "物料清单" in schemas["CoCreateIn"]["properties"]["type"]["description"]
    assert {"version", "components"} <= set(schemas["CoCreateOut"]["properties"])
    assert {"version", "components"} <= set(schemas["CoUpdateOut"]["properties"])  # F3 起修改响应是 CoUpdateOut
