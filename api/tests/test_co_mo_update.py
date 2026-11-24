"""F3 生产订单修改（vouchers/update，type=production_order）：请求校验、转发的键和生产订单专有的响应字段。"""

from __future__ import annotations

import pytest
from tests.support import make_client
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards
from u8co_api.errors import ApiError

_KIND = "production_order"
_UPDATE = "/v1/co/vouchers/update"
_EDIT = {"op": "update", "line_id": 1000000012, "qty": 2, "due_date": "2026-10-02"}


def _body(head: dict | None = None, lines: list[dict] | None = None) -> dict:
    body = _auth(type=_KIND, id=1000000011)
    if head is not None:
        body["head"] = head
    if lines is not None:
        body["lines"] = lines
    return body


def _updated(payload: dict) -> dict:
    details = [{"line_id": 1000000012, "sort_seq": 1, "inv_code": "P001", "qty": 2, "status": 1, "allocates": 3}]
    return {
        "ok": True,
        "type": payload["type"],
        "id": payload["id"],
        "code": "MO0001",
        "state": {"verified": False, "verifier": "", "verified_at": ""},
        "lines": 1,
        "allocates": 3,
        "details": details,
        "changed": 1,
    }


class _MoBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        return _updated(payload)


def test_mo_update_forwards_exact_keys() -> None:
    _forwards(_UPDATE, "/v1/vouchers/update", _body({"remark": "说明"}, [dict(_EDIT)]), _sealed("type", "id", "head", "lines"))
    _forwards(_UPDATE, "/v1/vouchers/update", _body(lines=[dict(_EDIT)]), _sealed("type", "id", "lines"))


def test_mo_update_keeps_allocates_details_and_changed() -> None:
    fake = _MoBridge()
    lines = [dict(_EDIT, remark="备注", define22="甲", define33="乙")]
    done = _client(fake).post(_UPDATE, json=_body({"remark": "说明"}, lines))
    assert done.status_code == 200, done.text
    body = done.json()
    assert fake.calls[-1][1]["lines"] == lines
    assert (body["lines"], body["allocates"], body["changed"]) == (1, 3, 1)
    assert body["details"][0]["allocates"] == 3


def test_other_update_has_no_mo_fields() -> None:
    done = _client(FakeBridge()).post(_UPDATE, json=_auth(type="sale_order", id=3, head={"cMemo": "x"}))
    assert done.status_code == 200, done.text
    assert not {"allocates", "details", "changed"} & set(done.json())


@pytest.mark.parametrize(
    "body",
    [
        _body(),
        _body({"mo_code": "X"}),
        _body({"remark": "  "}),
        _body({"remark": "X" * 256}),
        _body(lines=[{"op": "add", "qty": 1}]),
        _body(lines=[{"op": "delete", "line_id": 7}]),
        _body(lines=[{"line_id": 7, "qty": 1}]),
        _body(lines=[{"op": "update", "line_id": 7}]),
        _body(lines=[{"op": "update", "line_id": 7, "qty": None}]),
        _body({"remark": None}),
        _body(lines=[{"op": "update", "line_id": "7", "qty": 1}]),
        _body(lines=[{"op": "update", "line_id": True, "qty": 1}]),
        _body(lines=[dict(_EDIT), dict(_EDIT)]),
        _body(lines=[dict(_EDIT, qty=0)]),
        _body(lines=[dict(_EDIT, qty=0.0000001)]),
        _body(lines=[dict(_EDIT, qty="2")]),
        _body(lines=[dict(_EDIT, start_date="2026-10-03")]),
        _body(lines=[dict(_EDIT, due_date="2026-9-30")]),
        _body(lines=[dict(_EDIT, remark="")]),
        _body(lines=[dict(_EDIT, define22="")]),
        _body(lines=[dict(_EDIT, define22="X" * 61)]),
        _body(lines=[dict(_EDIT, define28="X" * 121)]),
        _body(lines=[dict(_EDIT, define26="1")]),
        _body(lines=[dict(_EDIT, wh_code="01")]),
        _body(lines=[dict(_EDIT, inv_code=" ")]),
        _body(lines=[dict(_EDIT, line_id=n) for n in range(1, 52)]),
    ],
)
def test_mo_update_rejects_before_the_bridge(body: dict) -> None:
    fake = _MoBridge()
    denied = _client(fake).post(_UPDATE, json=body)
    assert denied.status_code in (400, 422), denied.text
    assert fake.calls == []


def test_mo_update_accepts_long_defines_and_head_only() -> None:
    fake = _MoBridge()
    done = _client(fake).post(_UPDATE, json=_body(lines=[dict(_EDIT, define28="X" * 120, define22="X" * 60, inv_code="P001")]))
    assert done.status_code == 200, done.text
    head_only = _client(fake).post(_UPDATE, json=_body({"remark": "只改备注"}))
    assert head_only.status_code == 200, head_only.text
    assert fake.calls[-1][1]["head"] == {"remark": "只改备注"}


@pytest.mark.parametrize(
    ("status", "code"),
    [(409, "state_mismatch"), (409, "u8_rejected"), (503, "u8_unavailable"), (504, "outcome_unknown")],
)
def test_mo_update_passes_bridge_errors(status: int, code: str) -> None:
    fake = _MoBridge()
    fake.error = ApiError(status, code, "桥拒绝")
    done = _client(fake).post(_UPDATE, json=_body(lines=[dict(_EDIT)]))
    assert done.status_code == status, done.text
    assert done.json()["error"]["code"] == code


def test_openapi_lists_production_order_update() -> None:
    schemas = make_client().get("/v1/openapi.json").json()["components"]["schemas"]
    assert _KIND in schemas["CoUpdateIn"]["properties"]["type"]["enum"]
    assert {"allocates", "details", "changed"} <= set(schemas["CoUpdateOut"]["properties"])
    assert "changed" not in schemas["CoGenerateOut"]["properties"]
    assert {"allocates", "details", "warnings"} <= set(schemas["CoGenerateOut"]["properties"])


_SUB = {"inv_code": "DEMO-002", "sequence": 2, "factor": 1.5, "eff_begin": "2026-01-01"}
_SUB["defines"] = {"define22": "文本A"}


def _allocs(*subs: dict, seq: int = 10, inv: str = "DEMO-001", line_id: int = 7) -> dict:
    alloc = {"sort_seq": seq, "inv_code": inv, "substitutes": list(subs)}
    return {"op": "update", "line_id": line_id, "allocations": [alloc]}


class _SubsBridge(_MoBridge):
    def call(self, path: str, payload: dict) -> dict:
        body = super().call(path, payload)
        sub = dict(_SUB, replace_flag=False, eff_end=None)
        body["allocations"] = [
            {"line_id": 7, "allocate_id": 900, "sort_seq": 10, "inv_code": "DEMO-001", "substitutes": [sub]}
        ]
        return body


def test_mo_update_forwards_allocations_and_returns_them() -> None:
    fake = _SubsBridge()
    lines = [_allocs(_SUB), dict(_allocs(seq=20, line_id=8), qty=2)]
    done = _client(fake).post(_UPDATE, json=_body(lines=lines))
    assert done.status_code == 200, done.text
    assert fake.calls[-1][1]["lines"] == lines
    changed = done.json()["allocations"][0]
    assert (changed["allocate_id"], changed["substitutes"][0]["inv_code"]) == (900, "DEMO-002")


@pytest.mark.parametrize(
    "line",
    [
        {"op": "update", "line_id": 7, "allocations": []},
        {"op": "update", "line_id": 7, "allocations": {"sort_seq": 10}},
        {"op": "update", "line_id": 7, "allocations": [{"sort_seq": 10, "inv_code": "DEMO-001"}]},
        {"op": "update", "line_id": 7, "allocations": [{"sort_seq": "10", "inv_code": "A", "substitutes": []}]},
        {"op": "update", "line_id": 7, "allocations": [dict(_allocs()["allocations"][0], allocate_id=1)]},
        {"op": "update", "line_id": 7, "substitutes": []},
        _allocs(_SUB, dict(_SUB)),
        _allocs(dict(_SUB, factor=0)),
        _allocs(dict(_SUB, sequence=10001)),
        _allocs(dict(_SUB, eff_begin="2026-02-01", eff_end="2026-01-01")),
        _allocs(dict(_SUB, defines={"define21": "x"})),
        _allocs(dict(_SUB, defines={"define34": 1.5})),
        _allocs(dict(_SUB, defines={"define36": "2026/01/01"})),
        _allocs(dict(_SUB, defines={"define22": "X" * 61})),
        _allocs(*[dict(_SUB, inv_code=f"S{n}") for n in range(51)]),
        {"op": "update", "line_id": 7, "allocations": [_allocs()["allocations"][0]] * 2},
    ],
)
def test_mo_update_rejects_bad_allocations(line: dict) -> None:
    fake = _MoBridge()
    denied = _client(fake).post(_UPDATE, json=_body(lines=[line]))
    assert denied.status_code in (400, 422), denied.text
    assert fake.calls == []


@pytest.mark.parametrize(
    ("sub", "key"),
    [
        (dict(_SUB, eff_begin="1752-12-31"), "eff_begin"),
        (dict(_SUB, defines={"define36": "1700-01-01 00:00:00"}), "define36"),
    ],
)
def test_mo_update_rejects_dates_before_datetime_min(sub: dict, key: str) -> None:
    fake = _MoBridge()
    denied = _client(fake).post(_UPDATE, json=_body(lines=[_allocs(sub)]))
    assert denied.status_code in (400, 422), denied.text
    assert key in denied.text and "1753-01-01" in denied.text
    assert fake.calls == []
    edge = _client(fake).post(_UPDATE, json=_body(lines=[_allocs(dict(_SUB, eff_begin="1753-01-01"))]))
    assert edge.status_code == 200, edge.text


def test_mo_update_accepts_clear_and_typed_defines() -> None:
    fake = _MoBridge()
    defines = {"define26": 1.5, "define34": 3, "define36": "2026-01-02 08:30:00", "define28": "X" * 120}
    lines = [_allocs(), {"op": "update", "line_id": 8, "allocations": [{"sort_seq": 0, "inv_code": "C", "substitutes": [
        dict(_SUB, defines=defines)]}]}]
    done = _client(fake).post(_UPDATE, json=_body(lines=lines))
    assert done.status_code == 200, done.text
    assert fake.calls[-1][1]["lines"] == lines


def test_openapi_lists_changed_allocations() -> None:
    schemas = make_client().get("/v1/openapi.json").json()["components"]["schemas"]
    assert "allocations" in schemas["CoUpdateOut"]["properties"]
    keys = {"line_id", "allocate_id", "sort_seq", "inv_code", "substitutes"}
    assert keys <= set(schemas["CoMoSubsChanged"]["properties"])
