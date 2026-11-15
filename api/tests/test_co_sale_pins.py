"""销售订单新增 / 修改、发货单参照订单生单的落库核对字段（co_sale_pins）。

合法请求原样转给桥，形状不对的 400 且不调桥。
"""

from __future__ import annotations

import pytest
from tests.test_co_core_routes import _auth
from tests.test_co_gate import FakeBridge, _client
from u8co_api.co_gen_sale_invoice import CoInvSource, check_invoice_lines, check_invoice_sources
from u8co_api.co_sale_pins import check_dispatch_gen_pins, check_invoice_line_pins, check_sale_order_pins

_SO_HEAD = {
    "ccuscode": "C900001",
    "cstcode": "01",
    "ddate": "2026-09-27",
    "cgatheringplan": "A01",
    "cAddCode": "D01",
    "cdefine1": "文本A",
    "cdefine9": "DEMO-001",
}
_SO_LINE = {
    "cinvcode": "A01",
    "iquantity": 10,
    "inum": 2,
    "cunitid": "02",
    "cwhcode": "01",
    "cbdefine5": "文本A",
    "cbdefine7": 1.5,
}
_GEN_HEAD = {
    "caddcode": "D01",
    "cdefine1": "文本A",
    "chdefine1": "DEMO-001",
    "chdefine3": 1.5,
    "chdefine4": "x",
    "chdefine12": "文本B",
}
_GEN_LINE = [{"source_line_id": 1, "quantity": 1, "cwhcode": "01"}]
_INV_LINE = [{"source_line_id": 1, "quantity": 1, "cbdefine4": "文本A"}]


def _create(head: dict, line: dict) -> dict:
    return _auth(type="sale_order", head=head, lines=[line])


def test_sale_order_create_passes_pins_through() -> None:
    fake = FakeBridge()
    client = _client(fake)
    sent = client.post("/v1/co/vouchers/create", json=_create(_SO_HEAD, _SO_LINE))
    assert sent.status_code == 200, sent.text
    path, payload = fake.calls[0]
    assert path == "/v1/vouchers/create"
    assert payload["head"] == _SO_HEAD
    assert payload["lines"] == [_SO_LINE]


def test_sale_order_inum_exponent_goes_to_bridge() -> None:
    fake = FakeBridge()
    client = _client(fake)
    line = {**_SO_LINE, "inum": "1E+01"}
    sent = client.post("/v1/co/vouchers/create", json=_create(_SO_HEAD, line))
    assert sent.status_code == 200, sent.text
    assert fake.calls[0][1]["lines"] == [line]


def test_sale_order_update_passes_pins_through() -> None:
    fake = FakeBridge()
    client = _client(fake)
    lines = [
        {"op": "update", "line_id": 5, "cbdefine5": "", "cwhcode": "02"},
        {"op": "add", "cinvcode": "A02", "iquantity": 1, "inum": "0.5", "cbdefine5": 3, "cbdefine7": "DEMO-001"},
        {"op": "delete", "line_id": 6},
    ]
    head = {"cgatheringplan": "", "caddcode": "D02", "cdefine9": "文本A"}
    body = _auth(type="sale_order", id=9, head=head, lines=lines)
    sent = client.post("/v1/co/vouchers/update", json=body)
    assert sent.status_code == 200, sent.text
    _path, payload = fake.calls[0]
    assert payload["head"] == head
    assert payload["lines"] == lines


def test_dispatch_generate_passes_pins_through() -> None:
    fake = FakeBridge()
    client = _client(fake)
    body = _auth(type="dispatch", id=9, head=_GEN_HEAD, lines=_GEN_LINE)
    sent = client.post("/v1/co/vouchers/generate", json=body)
    assert sent.status_code == 200, sent.text
    path, payload = fake.calls[0]
    assert path == "/v1/vouchers/generate"
    assert payload["head"] == _GEN_HEAD


def test_blue_invoice_passes_line_pins_through() -> None:
    fake = FakeBridge()
    client = _client(fake)
    body = _auth(type="sale_invoice", id=9, lines=_INV_LINE)
    sent = client.post("/v1/co/vouchers/generate", json=body)
    assert sent.status_code == 200, sent.text
    assert fake.calls[0][1]["lines"] == _INV_LINE


def test_blue_invoice_sources_pass_line_pins_through() -> None:
    fake = FakeBridge()
    client = _client(fake)
    sources = [
        {"id": 1, "lines": [{"source_line_id": 1, "quantity": 1, "cbdefine4": "文本A"}]},
        {"id": 2, "lines": [{"source_line_id": 2, "quantity": 1}]},
    ]
    body = _auth(type="sale_invoice", sources=sources)
    sent = client.post("/v1/co/vouchers/generate", json=body)
    assert sent.status_code == 200, sent.text
    assert fake.calls[0][1]["sources"] == sources


def test_null_pin_values_are_accepted() -> None:
    check_sale_order_pins(
        {**_SO_HEAD, "cdefine1": None, "caddcode": None},
        [{**_SO_LINE, "cbdefine5": None, "inum": None}],
    )
    check_sale_order_pins(
        {"cdefine9": None, "cgatheringplan": None},
        [{"op": "update", "line_id": 5, "cbdefine5": None, "cwhcode": None}],
        edit=True,
    )
    check_dispatch_gen_pins({**_GEN_HEAD, "chdefine1": None, "cdefine1": None, "caddcode": None})
    check_invoice_line_pins({"source_line_id": 1, "quantity": 1, "cbdefine4": None}, "lines.0")
    check_invoice_lines("sale_invoice", "dispatch", [{"source_line_id": 1, "quantity": 1, "cbdefine4": None}])


def test_red_invoice_rejects_line_pins() -> None:
    fake = FakeBridge()
    client = _client(fake)
    body = _auth(
        type="sale_invoice",
        id=9,
        source_type="sale_return",
        lines=[{"source_line_id": 1, "quantity": 1, "cbdefine4": "文本A"}],
    )
    denied = client.post("/v1/co/vouchers/generate", json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


_REJECTS = (
    ("/v1/co/vouchers/create", _create({**_SO_HEAD, "caddcode": 12}, _SO_LINE), "head.caddcode"),
    ("/v1/co/vouchers/create", _create({**_SO_HEAD, "cgatheringplan": "  "}, _SO_LINE), "head.cgatheringplan"),
    ("/v1/co/vouchers/create", _create({**_SO_HEAD, "cgatheringplan": "A" * 61}, _SO_LINE), "head.cgatheringplan"),
    ("/v1/co/vouchers/create", _create(_SO_HEAD, {**_SO_LINE, "cwhcode": True}), "lines.0.cwhcode"),
    ("/v1/co/vouchers/create", _create(_SO_HEAD, {**_SO_LINE, "cunitid": ""}), "lines.0.cunitid"),
    ("/v1/co/vouchers/create", _create(_SO_HEAD, {**_SO_LINE, "inum": 0}), "lines.0.inum"),
    ("/v1/co/vouchers/create", _create(_SO_HEAD, {**_SO_LINE, "inum": "两件"}), "lines.0.inum"),
    ("/v1/co/vouchers/create", _create(_SO_HEAD, {**_SO_LINE, "inum": True}), "lines.0.inum"),
    ("/v1/co/vouchers/create", _create(_SO_HEAD, {**_SO_LINE, "cbdefine5": "型" * 121}), "lines.0.cbdefine5"),
    ("/v1/co/vouchers/create", _create(_SO_HEAD, {**_SO_LINE, "CBDEFINE5": False}), "lines.0.cbdefine5"),
    ("/v1/co/vouchers/create", _create({**_SO_HEAD, "cdefine17": "文本A"}, _SO_LINE), "head.cdefine17"),
    ("/v1/co/vouchers/create", _create(_SO_HEAD, {**_SO_LINE, "cbdefine07": "文本A"}), "lines.0.cbdefine07"),
    (
        "/v1/co/vouchers/update",
        _auth(type="sale_order", id=9, lines=[{"op": "update", "line_id": 5, "cwhcode": 1}]),
        "lines.0.cwhcode",
    ),
    (
        "/v1/co/vouchers/generate",
        _auth(type="dispatch", id=9, head={"caddcode": ""}, lines=_GEN_LINE),
        "head.caddcode",
    ),
    (
        "/v1/co/vouchers/generate",
        _auth(type="dispatch", id=9, head={"chdefine2": True}, lines=_GEN_LINE),
        "head.chdefine2",
    ),
    (
        "/v1/co/vouchers/generate",
        _auth(type="dispatch", id=9, head={"chdefine4": "长" * 121}, lines=_GEN_LINE),
        "head.chdefine4",
    ),
    (
        "/v1/co/vouchers/generate",
        _auth(type="dispatch", id=9, head={"chdefine0": "文本A"}, lines=_GEN_LINE),
        "head.chdefine0",
    ),
    (
        "/v1/co/vouchers/generate",
        _auth(type="dispatch", id=9, head={"chdefine07": "文本A"}, lines=_GEN_LINE),
        "head.chdefine07",
    ),
)


@pytest.mark.parametrize(("path", "body", "field"), _REJECTS)
def test_bad_pin_shape_is_rejected_before_bridge(path: str, body: dict, field: str) -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []
    if path.endswith("/generate"):
        with pytest.raises(ValueError, match=field.replace(".", r"\.")):
            check_dispatch_gen_pins(body.get("head"))
        return
    with pytest.raises(ValueError, match=field.replace(".", r"\.")):
        check_sale_order_pins(body.get("head"), body.get("lines"), edit=path.endswith("/update"))


def test_delete_rows_are_not_checked() -> None:
    fake = FakeBridge()
    client = _client(fake)
    body = _auth(type="sale_order", id=9, lines=[{"op": "delete", "line_id": 6}])
    assert client.post("/v1/co/vouchers/update", json=body).status_code == 200


def test_messages_name_the_rule() -> None:
    with pytest.raises(ValueError, match="必须是字符串编码"):
        check_sale_order_pins({"caddcode": 1}, None)
    with pytest.raises(ValueError, match="不能为空"):
        check_sale_order_pins(None, [{"cwhcode": " "}])
    with pytest.raises(ValueError, match="大于 0"):
        check_sale_order_pins(None, [{"op": "add", "inum": -1}], edit=True)
    with pytest.raises(ValueError, match="最多 120"):
        check_dispatch_gen_pins({"chdefine1": "x" * 121})
    with pytest.raises(ValueError, match="不是有效的自定义项"):
        check_sale_order_pins({"cdefine17": "文本A"}, None)
    with pytest.raises(ValueError, match="不是有效的自定义项"):
        check_dispatch_gen_pins({"chdefine0": "文本A"})
    with pytest.raises(ValueError, match="不是有效的自定义项"):
        check_dispatch_gen_pins({"chdefine07": 1.5})
    with pytest.raises(ValueError, match=r"lines\.0\.cbdefine0 不是有效的自定义项"):
        check_invoice_line_pins({"cbdefine0": "文本A"}, "lines.0")
    # 修改时空串是清空；不是约定字段的键不归这里管。null 与省略相同。
    check_sale_order_pins({"caddcode": ""}, [{"op": "update", "line_id": 1, "cwhcode": ""}], edit=True)
    check_sale_order_pins({"cmemo": 1, "inum": "x"}, [{"cmemo": True}])
    check_sale_order_pins({"cdefine9": "文本A"}, [{"cbdefine7": "DEMO-001"}])
    check_sale_order_pins({"cdefine1": None}, [{"cbdefine5": None, "inum": None}])
    check_dispatch_gen_pins(None)
    check_dispatch_gen_pins({"chdefine12": 1.5, "cdefine9": "DEMO-001"})
    check_dispatch_gen_pins({"chdefine1": None, "cdefine1": None})
    check_invoice_line_pins({"cbdefine4": "文本A"}, "lines.0")
    check_invoice_line_pins({"cbdefine4": None}, "lines.0")


_INV_BAD_KEYS = ("cbdefine0", "cbdefine07", "cbdefine100")


def _inv_line(**pins: object) -> list[dict]:
    return [{"source_line_id": 1, "quantity": 1, **pins}]


def _no_row(_row: dict, _seen: set) -> None:
    return


@pytest.mark.parametrize("key", _INV_BAD_KEYS)
def test_blue_invoice_rejects_invalid_line_pins(key: str) -> None:
    fake = FakeBridge()
    body = _auth(type="sale_invoice", id=9, lines=_inv_line(**{key: "文本A"}))
    denied = _client(fake).post("/v1/co/vouchers/generate", json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []
    field = f"lines.0.{key}"
    with pytest.raises(ValueError, match=field.replace(".", r"\.") + " 不是有效的自定义项"):
        check_invoice_lines("sale_invoice", "dispatch", body["lines"])


@pytest.mark.parametrize("key", _INV_BAD_KEYS)
def test_blue_invoice_sources_reject_invalid_line_pins(key: str) -> None:
    fake = FakeBridge()
    sources = [
        {"id": 1, "lines": _inv_line(cbdefine4="文本A")},
        {"id": 2, "lines": _inv_line(**{key: "文本A"})},
    ]
    body = _auth(type="sale_invoice", sources=sources)
    denied = _client(fake).post("/v1/co/vouchers/generate", json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []
    field = f"sources.1.lines.0.{key}"
    parsed = [CoInvSource.model_validate(one) for one in sources]
    with pytest.raises(ValueError, match=field.replace(".", r"\.") + " 不是有效的自定义项"):
        check_invoice_sources("sale_invoice", "dispatch", (None, None), parsed, _no_row)
