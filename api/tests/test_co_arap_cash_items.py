"""制单时指定现金流量项目（cash_items）：arap/voucher 与 arap/process/voucher 原样转给桥（去首尾空格）、
形状不对在 API 层 400、OpenAPI 里有字段说明。桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from u8co_api.co_models_arap_batch import CoArapProcVoucherIn
from u8co_api.co_models_arap_voucher import CASH_ITEMS_MAX, CoArapVoucherIn, check_cash_items

_VOUCHER = "/v1/co/arap/voucher"
_PROC = "/v1/co/arap/process/voucher"
_PJT = "PJTAR000000000001"


def _send(path: str, body: dict) -> dict:
    with _Up() as server:
        response = _wired(server.base_url).post(path, json=_auth(**body))
        assert response.status_code == 200, response.text
        _method, _seen, raw = server.httpd.hits[-1]
    return json.loads(raw)


def test_voucher_forwards_cash_items_trimmed() -> None:
    sent = _send(_VOUCHER, {"flag": "AR", "type": "ar_receipt", "id": 5, "cash_items": {" 660399 ": " 07 "}})
    assert set(sent) == _sealed("flag", "type", "id", "cash_items", "caller")
    assert sent["cash_items"] == {"660399": "07"}


def test_process_voucher_forwards_cash_items() -> None:
    sent = _send(_PROC, {"flag": "AR", "cancel_nos": [_PJT], "cash_items": {"660399": "07", "66029901": "08"}})
    assert set(sent) == _sealed("flag", "cancel_nos", "cash_items", "caller")
    assert sent["cash_items"] == {"660399": "07", "66029901": "08"}


def test_cash_items_absent_is_not_sent() -> None:
    sent = _send(_PROC, {"flag": "AR", "cancel_nos": [_PJT]})
    assert "cash_items" not in sent


_BAD = (
    ["660399"],
    "660399",
    {"660399": 7},
    {"660399": ""},
    {"660399": "0 7"},
    {"660399": "0" * 21},
    {" ": "07"},
    {"6603 99": "07"},
    {"1" * 41: "07"},
    {"6603a": "07", "6603A": "08"},
    {f"6603{i:02d}": "07" for i in range(CASH_ITEMS_MAX + 1)},
)


@pytest.mark.parametrize("value", _BAD)
@pytest.mark.parametrize(
    ("path", "body"),
    [
        (_VOUCHER, {"flag": "AR", "type": "ar_receipt", "id": 5}),
        (_PROC, {"flag": "AR", "cancel_nos": [_PJT]}),
    ],
)
def test_bad_cash_items_are_400_before_the_bridge(path: str, body: dict, value: object) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=_auth(**body, cash_items=value))
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_model_messages_match_the_bridge() -> None:
    with pytest.raises(ValueError, match="cash_items 最多 20 个科目"):
        check_cash_items({f"6603{i:02d}": "07" for i in range(21)})
    with pytest.raises(ValueError, match="不超过 40 位、不含空白的科目编码"):
        check_cash_items({"6603 99": "07"})
    with pytest.raises(ValueError, match="不超过 20 位、不含空白的现金流量项目编码"):
        check_cash_items({"660399": " "})
    with pytest.raises(ValueError, match="重复的科目 6603A"):
        check_cash_items({"6603a": "07", "6603A": "08"})
    assert check_cash_items(None) is None
    assert check_cash_items({}) == {}
    with pytest.raises(ValidationError):
        CoArapVoucherIn.model_validate(
            {**_auth(flag="AR", type="ar_receipt", id=5), "cash_items": {"660399": 7}}
        )
    ok = CoArapProcVoucherIn.model_validate({**_auth(flag="AR", cancel_nos=[_PJT]), "cash_items": {"660399": "07"}})
    assert ok.cash_items == {"660399": "07"}


def test_openapi_documents_cash_items() -> None:
    spec = _spec()
    for path in (_VOUCHER, _PROC):
        op = spec["paths"][path]["post"]
        assert "cash_items" in op["description"], path
        ref = op["requestBody"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
        prop = spec["components"]["schemas"][ref]["properties"]["cash_items"]
        assert "现金流量项目数据来源" in prop["description"], path
