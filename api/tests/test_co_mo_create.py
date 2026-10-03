"""Production order create / delete: body checks, MO-only response fields, idempotency and bridge errors."""

from __future__ import annotations

import pytest
from tests.support import make_client
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards
from u8co_api.errors import ApiError

_KIND = "production_order"
_CREATE = "/v1/co/vouchers/create"
_DELETE = "/v1/co/vouchers/delete"
_LINE = {
    "inv_code": "P001",
    "qty": 2,
    "start_date": "2026-09-28",
    "due_date": "2026-09-30",
    "mo_type": "1",
    "dept_code": "D01",
}


def _body(head: dict | None = None, lines: list[dict] | None = None) -> dict:
    return _auth(type=_KIND, head={} if head is None else head, lines=[dict(_LINE)] if lines is None else lines)


def _mo_created(payload: dict) -> dict:
    details = [
        {
            "line_id": 30 + i,
            "sort_seq": i + 1,
            "inv_code": row["inv_code"],
            "qty": row["qty"],
            "status": 2,
            "allocates": 0 if row["inv_code"] == "NOBOM" else 5,
        }
        for i, row in enumerate(payload["lines"])
    ]
    body = {
        "ok": True,
        "type": payload["type"],
        "id": 1000000011,
        "code": payload["head"].get("mo_code", "MO0001"),
        "state": {"verified": False, "verifier": "", "verified_at": ""},
        "lines": len(details),
        "allocates": sum(item["allocates"] for item in details),
        "details": details,
    }
    if any(item["allocates"] == 0 for item in details):
        body["warnings"] = ["第 1 行没有展开子件（存货没有有效的标准 BOM），allocates 为 0"]
    return body


class _MoBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        if path == "/v1/vouchers/create":
            return _mo_created(payload)
        return {"ok": True, "type": payload["type"], "id": payload["id"], "code": "MO0001", "deleted": True}


def test_mo_create_and_delete_forward_exact_keys() -> None:
    _forwards(_CREATE, "/v1/vouchers/create", _body(), _sealed("type", "head", "lines"))
    _forwards(_DELETE, "/v1/vouchers/delete", _auth(type=_KIND, id=9), _sealed("type", "id"))


def test_mo_create_keeps_details_allocates_and_warnings() -> None:
    fake = _MoBridge()
    lines = [dict(_LINE), dict(_LINE, inv_code="NOBOM", wh_code="01", remark="备注")]
    done = _client(fake).post(_CREATE, json=_body({"mo_code": "MO-T1", "remark": "说明"}, lines))
    assert done.status_code == 200, done.text
    body = done.json()
    assert fake.calls[-1][1]["head"] == {"mo_code": "MO-T1", "remark": "说明"}
    assert (body["code"], body["lines"], body["allocates"]) == ("MO-T1", 2, 5)
    assert [item["allocates"] for item in body["details"]] == [5, 0]
    assert body["details"][0] == {
        "line_id": 30,
        "sort_seq": 1,
        "inv_code": "P001",
        "qty": 2,
        "status": 2,
        "allocates": 5,
    }
    assert body["warnings"]


def test_mo_create_without_code_has_no_warnings() -> None:
    done = _client(_MoBridge()).post(_CREATE, json=_body())
    assert done.status_code == 200, done.text
    assert "warnings" not in done.json()


def test_other_create_has_no_mo_fields() -> None:
    done = _client(FakeBridge()).post(_CREATE, json=_auth(type="other_in", head={"a": "b"}, lines=[{"a": 1}]))
    assert done.status_code == 200, done.text
    assert not {"allocates", "details", "warnings"} & set(done.json())


@pytest.mark.parametrize(
    "body",
    [
        _body({"code": "X"}),
        _body({"mo_code": "X" * 31}),
        _body({"cMemo": "x"}),
        _body(lines=[]),
        _body(lines=[dict(_LINE)] * 51),
        _body(lines=[dict(_LINE, due_date="2026-09-27")]),
        _body(lines=[dict(_LINE, start_date="2026-9-28")]),
        _body(lines=[dict(_LINE, start_date="2026-02-30")]),
        _body(lines=[dict(_LINE, start_date="2026-W39-1")]),
        _body(lines=[dict(_LINE, start_date="２０２６-09-28")]),
        _body(lines=[dict(_LINE, remark="备\u200b注")]),
        _body(lines=[dict(_LINE, remark="备\t注")]),
        _body({"remark": "说\ue000明"}),
        _body(lines=[dict(_LINE, qty=0)]),
        _body(lines=[dict(_LINE, qty=-1)]),
        _body(lines=[dict(_LINE, qty=True)]),
        _body(lines=[dict(_LINE, qty="2")]),
        _body(lines=[dict(_LINE, qty=0.0000001)]),
        _body(lines=[dict(_LINE, qty=10**12 + 1)]),
        _body(lines=[{k: v for k, v in _LINE.items() if k != "mo_type"}]),
        _body(lines=[{k: v for k, v in _LINE.items() if k != "dept_code"}]),
        _body(lines=[dict(_LINE, inv_code="  ")]),
        _body(lines=[dict(_LINE, inv_code=1001)]),
        _body(lines=[dict(_LINE, cInvCode="A")]),
        _body(lines=[dict(_LINE, wh_code="W" * 11)]),
        _body(lines=[dict(_LINE, sale_order_code="SO1")]),
    ],
)
def test_mo_create_rejects_before_the_bridge(body: dict) -> None:
    fake = _MoBridge()
    denied = _client(fake).post(_CREATE, json=body)
    assert denied.status_code in (400, 422), denied.text
    assert fake.calls == []


def test_mo_create_accepts_fifty_lines_and_six_decimals() -> None:
    fake = _MoBridge()
    lines = [dict(_LINE, qty=1.123456)] * 50
    done = _client(fake).post(_CREATE, json=_body(lines=lines))
    assert done.status_code == 200, done.text
    assert len(fake.calls[-1][1]["lines"]) == 50


def test_mo_create_accepts_wide_space_in_text() -> None:
    fake = _MoBridge()
    done = _client(fake).post(_CREATE, json=_body({"remark": "说\u3000明"}, [dict(_LINE, remark="备 注")]))
    assert done.status_code == 200, done.text
    assert fake.calls[-1][1]["head"]["remark"] == "说\u3000明"


def test_mo_create_takes_an_idempotency_key() -> None:
    fake = _MoBridge()
    done = _client(fake).post(_CREATE, json=_body(), headers={"Idempotency-Key": "mo-2026-0001"})
    assert done.status_code == 200, done.text
    assert fake.calls[-1][1]["idempotency_key"] == "mo-2026-0001"


@pytest.mark.parametrize(
    ("path", "body", "status", "code"),
    [
        (_CREATE, _body(), 409, "u8_rejected"),
        (_CREATE, _body(), 503, "u8_unavailable"),
        (_CREATE, _body(), 504, "outcome_unknown"),
        (_CREATE, _body({"mo_code": "MO0001"}), 409, "state_mismatch"),
        (_CREATE, _body(), 403, "no_permission"),
        (_DELETE, _auth(type=_KIND, id=9), 409, "state_mismatch"),
        (_DELETE, _auth(type=_KIND, id=9), 409, "workflow_enabled"),
        (_DELETE, _auth(type=_KIND, id=9), 404, "not_found"),
    ],
)
def test_mo_passes_bridge_errors(path: str, body: dict, status: int, code: str) -> None:
    fake = _MoBridge()
    fake.error = ApiError(status, code, "说明")
    denied = _client(fake).post(path, json=body)
    assert denied.status_code == status
    assert denied.json()["error"]["code"] == code


def test_mo_delete_forwards_and_returns_deleted() -> None:
    fake = _MoBridge()
    done = _client(fake).post(_DELETE, json=_auth(type=_KIND, id=9))
    assert done.status_code == 200, done.text
    assert done.json()["deleted"] is True


def test_openapi_lists_production_order_create_and_delete() -> None:
    schemas = make_client().get("/v1/openapi.json").json()["components"]["schemas"]
    assert _KIND in schemas["CoCreateIn"]["properties"]["type"]["enum"]
    assert _KIND in schemas["CoDeleteIn"]["properties"]["type"]["enum"]
    assert "生产订单" in schemas["CoCreateIn"]["properties"]["type"]["description"]
    assert {"allocates", "details", "warnings"} <= set(schemas["CoCreateOut"]["properties"])
