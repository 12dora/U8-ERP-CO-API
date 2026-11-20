"""生产订单参照销售订单生单（type=production_order，source_type=sale_order）：请求校验、转发和响应字段。"""

from __future__ import annotations

import pytest
from fastapi.exceptions import ResponseValidationError
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards
from tests.test_co_update_close_gen import _enum, _spec
from u8co_api.co_gen_mo import check_mo_gen
from u8co_api.errors import ApiError

_KIND = "production_order"
_GEN = "/v1/co/vouchers/generate"
_HEAD = {"mo_type": "1", "mo_code": "MO-T1", "remark": "参照销售订单"}
_LINES = [{"source_line_id": 11, "quantity": 2}, {"source_line_id": 12, "quantity": 1.5, "dept_code": "D01"}]
_QTY_BAD = "quantity 必须是大于 0 且不超过 1000000000000 的数"
_LINE = [{"source_line_id": 1, "quantity": 1}]


def _gen(**extra) -> dict:
    body = {"type": _KIND, "id": 9, "source_type": "sale_order", "head": _HEAD, "lines": _LINES}
    body.update(extra)
    return _auth(**body)


def _cell(row: dict, name: str):
    for key, value in row.items():
        if str(key).lower() == name:
            return value
    raise KeyError(name)


def _mo_generated(payload: dict) -> dict:
    details = [
        {
            "line_id": 30 + i,
            "sort_seq": i + 1,
            "inv_code": "P001",
            "qty": _cell(row, "quantity"),
            "status": 2,
            "allocates": 4,
            "source_line_id": _cell(row, "source_line_id"),
            "cust_bom": True,
        }
        for i, row in enumerate(payload["lines"])
    ]
    return {
        "ok": True,
        "type": payload["type"],
        "id": 1000000011,
        "code": payload["head"].get("mo_code") or "MO0001",
        "state": {"verified": False, "verifier": "", "verified_at": ""},
        "lines": len(details),
        "allocates": sum(item["allocates"] for item in details),
        "details": details,
        "warnings": ["第 1 行按选配结果展开子件"],
        "source_type": "sale_order",
        "source_id": payload["id"],
    }


class _MoGenBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        if path == "/v1/vouchers/generate":
            return _mo_generated(payload)
        return super().call(path, payload)


def test_generate_forwards_exact_keys() -> None:
    _forwards(_GEN, "/v1/vouchers/generate", _gen(), _sealed("type", "id", "source_type", "head", "lines"))


def test_generate_reaches_the_bridge_unchanged() -> None:
    fake = _MoGenBridge()
    done = _client(fake).post(_GEN, json=_gen())
    assert done.status_code == 200, done.text
    sent = fake.calls[-1][1]
    assert (sent["type"], sent["id"], sent["source_type"]) == (_KIND, 9, "sale_order")
    assert sent["head"] == _HEAD
    assert sent["lines"] == _LINES


def test_generate_keeps_details_allocates_source_line_and_cust_bom() -> None:
    fake = _MoGenBridge()
    done = _client(fake).post(_GEN, json=_gen())
    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["code"], body["lines"], body["allocates"]) == ("MO-T1", 2, 8)
    assert (body["source_type"], body["source_id"]) == ("sale_order", 9)
    assert body["details"][0]["source_line_id"] == 11
    assert body["details"][0]["cust_bom"] is True
    assert body["details"][0]["allocates"] == 4
    assert body["warnings"]
    assert set(body) <= {
        "ok", "type", "id", "code", "state", "lines", "allocates", "details", "warnings", "source_type", "source_id",
    }


def test_bridge_body_without_source_keys_fails_the_model() -> None:
    class _Bare(_MoGenBridge):
        def call(self, path: str, payload: dict) -> dict:
            body = super().call(path, payload)
            body.pop("source_type", None)
            body.pop("source_id", None)
            return body

    fake = _Bare()
    with pytest.raises(ResponseValidationError):
        _client(fake).post(_GEN, json=_gen())
    assert fake.calls


def test_source_type_may_be_omitted() -> None:
    fake = _MoGenBridge()
    body = _gen()
    body.pop("source_type")
    done = _client(fake).post(_GEN, json=body)
    assert done.status_code == 200, done.text
    assert fake.calls[-1][1].get("source_type") is None


def test_generate_takes_an_idempotency_key() -> None:
    fake = _MoGenBridge()
    done = _client(fake).post(_GEN, json=_gen(), headers={"Idempotency-Key": "mo-gen-2026-0001"})
    assert done.status_code == 200, done.text
    assert fake.calls[-1][1]["idempotency_key"] == "mo-gen-2026-0001"


def test_empty_optional_fields_reach_the_bridge() -> None:
    fake = _MoGenBridge()
    body = _gen(
        head={"mo_type": "1", "mo_code": "", "remark": "  "},
        lines=[{"source_line_id": 7, "quantity": 1, "dept_code": ""}],
    )
    done = _client(fake).post(_GEN, json=body)
    assert done.status_code == 200, done.text
    assert fake.calls


_REJECTS = (
    _gen(head={"mo_type": "1", "cMemo": "x"}),
    _gen(head={}),
    _gen(head={"mo_code": "MO1"}),
    _gen(head={"mo_type": ""}),
    _gen(head={"mo_type": "ABC"}),
    _gen(head={"mo_type": 1}),
    _gen(head={"mo_type": "1", "mo_code": "X" * 31}),
    _gen(head={"mo_type": "1", "remark": "x" * 256}),
    _gen(head={"mo_type": "1", "remark": 1}),
    _gen(lines=None),
    _gen(lines=[]),
    _gen(lines=[{"source_line_id": n, "quantity": 1} for n in range(1, 202)]),
    _gen(lines=[{"source_line_id": 7}]),
    _gen(lines=[{"source_line_id": 7, "quantity": 0}]),
    _gen(lines=[{"source_line_id": 7, "quantity": -1}]),
    _gen(lines=[{"source_line_id": 7, "quantity": True}]),
    _gen(lines=[{"source_line_id": 7, "quantity": "2"}]),
    _gen(lines=[{"source_line_id": 7, "quantity": 0.0000001}]),
    _gen(lines=[{"source_line_id": 7, "quantity": 10**12 + 1}]),
    _gen(lines=[{"source_line_id": 7, "quantity": 1.1234567}]),
    _gen(lines=[{"source_line_id": 7, "quantity": 1, "cInvCode": "A"}]),
    _gen(lines=[{"source_line_id": 7, "quantity": 1}, {"source_line_id": 7, "quantity": 1}]),
    _gen(lines=[{"source_line_id": "7", "quantity": 1}]),
    _gen(lines=[{"source_line_id": 0, "quantity": 1}]),
    _gen(lines=[{"source_line_id": 7, "quantity": 1, "dept_code": "D" * 13}]),
    _gen(lines=[{"source_line_id": 7, "quantity": 1, "dept_code": 1}]),
    _gen(source_type="purchase_order"),
    _auth(type=_KIND, id=9, lines=_LINES),
)


@pytest.mark.parametrize("body", _REJECTS)
def test_generate_rejects_before_the_bridge(body: dict) -> None:
    fake = _MoGenBridge()
    denied = _client(fake).post(_GEN, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_missing_mo_type_message() -> None:
    with pytest.raises(ValueError, match="缺少字段 mo_type"):
        check_mo_gen({}, _LINE)
    with pytest.raises(ValueError, match="缺少字段 mo_type"):
        check_mo_gen({"mo_type": "  "}, _LINE)
    with pytest.raises(ValueError, match="缺少字段 mo_type"):
        check_mo_gen({"mo_type": None}, _LINE)


def test_unknown_and_duplicate_messages() -> None:
    with pytest.raises(ValueError, match="不能设置字段 cMemo"):
        check_mo_gen({"mo_type": "1", "cMemo": "x"}, _LINE)
    with pytest.raises(ValueError, match="不能设置字段 cInvCode"):
        check_mo_gen({"mo_type": "1"}, [{"source_line_id": 1, "quantity": 1, "cInvCode": "A"}])
    with pytest.raises(ValueError, match="来源明细重复"):
        check_mo_gen({"mo_type": "1"}, [{"source_line_id": 7, "quantity": 1}, {"source_line_id": 7, "quantity": 2}])


def test_length_and_type_messages() -> None:
    with pytest.raises(ValueError, match="mo_type 不能超过 2 个字符"):
        check_mo_gen({"mo_type": "ABC"}, _LINE)
    with pytest.raises(ValueError, match="mo_type 必须是字符串"):
        check_mo_gen({"mo_type": 1}, _LINE)
    with pytest.raises(ValueError, match="mo_code 不能超过 30 个字符"):
        check_mo_gen({"mo_type": "1", "mo_code": "X" * 31}, _LINE)
    with pytest.raises(ValueError, match="remark 必须是字符串"):
        check_mo_gen({"mo_type": "1", "remark": 1}, _LINE)
    with pytest.raises(ValueError, match="dept_code 不能超过 12 个字符"):
        check_mo_gen({"mo_type": "1"}, [{"source_line_id": 1, "quantity": 1, "dept_code": "D" * 13}])
    with pytest.raises(ValueError, match="dept_code 必须是字符串"):
        check_mo_gen({"mo_type": "1"}, [{"source_line_id": 1, "quantity": 1, "dept_code": 1}])


def test_optional_null_and_empty_are_omitted() -> None:
    empty = [{"source_line_id": 1, "quantity": 1, "dept_code": ""}]
    check_mo_gen({"mo_type": "1", "mo_code": "", "remark": ""}, empty)
    absent = [{"source_line_id": 1, "quantity": 1, "dept_code": None}]
    check_mo_gen({"mo_type": "1", "mo_code": None, "remark": None}, absent)
    check_mo_gen({"mo_type": "1", "mo_code": "  ", "remark": "  "}, _LINE)


def test_source_line_id_messages() -> None:
    with pytest.raises(ValueError, match="source_line_id 必须是整数"):
        check_mo_gen({"mo_type": "1"}, [{"source_line_id": "7", "quantity": 1}])
    with pytest.raises(ValueError, match="source_line_id 无效"):
        check_mo_gen({"mo_type": "1"}, [{"source_line_id": 0, "quantity": 1}])
    with pytest.raises(ValueError, match="lines 的每一行必须是对象"):
        check_mo_gen({"mo_type": "1"}, [1])


def test_quantity_matches_line_qty() -> None:
    for qty in (0, -1, True, "2", float("nan"), float("inf"), 10**12 + 1):
        with pytest.raises(ValueError, match=_QTY_BAD):
            check_mo_gen({"mo_type": "1"}, [{"source_line_id": 1, "quantity": qty}])
    with pytest.raises(ValueError, match=_QTY_BAD):
        check_mo_gen({"mo_type": "1"}, [{"source_line_id": 1}])
    with pytest.raises(ValueError, match="quantity 最多 6 位小数"):
        check_mo_gen({"mo_type": "1"}, [{"source_line_id": 1, "quantity": 1.1234567}])


def test_generate_keys_are_case_insensitive() -> None:
    fake = _MoGenBridge()
    client = _client(fake)
    ok = client.post(
        _GEN,
        json=_gen(head={"MO_TYPE": "1"}, lines=[{"Source_Line_Id": 5, "Quantity": 1, "DEPT_CODE": "D01"}]),
    )
    assert ok.status_code == 200, ok.text
    count = len(fake.calls)
    dup = client.post(_GEN, json=_gen(head={"mo_type": "1", "Mo_Type": "2"}))
    assert dup.status_code == 400, dup.text
    dup_line = client.post(_GEN, json=_gen(lines=[{"source_line_id": 5, "Source_Line_Id": 6, "quantity": 1}]))
    assert dup_line.status_code == 400, dup_line.text
    assert len(fake.calls) == count


def test_generate_passes_bridge_errors() -> None:
    fake = _MoGenBridge()
    fake.error = ApiError(504, "outcome_unknown", "部分行已生成")
    denied = _client(fake).post(_GEN, json=_gen())
    assert denied.status_code == 504
    assert denied.json()["error"]["code"] == "outcome_unknown"


def test_openapi_lists_production_order_generate() -> None:
    spec = _spec()
    schemas = spec["components"]["schemas"]
    assert _KIND in schemas["CoGenerateIn"]["properties"]["type"]["enum"]
    assert "sale_order" in _enum(spec, "CoGenerateIn", "source_type")
    assert "生产订单" in schemas["CoGenerateIn"]["properties"]["type"]["description"]
    assert {"allocates", "details", "warnings"} <= set(schemas["CoGenerateOut"]["properties"])
    assert {"source_line_id", "cust_bom"} <= set(schemas["CoMoCreatedLine"]["properties"])
