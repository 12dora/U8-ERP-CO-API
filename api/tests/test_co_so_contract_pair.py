"""销售订单合同号回写的成对设置（mode=pair）：请求校验、转发给桥的字段、cascade 列表放行和 OpenAPI。

假桥，不访问网络。
"""

from __future__ import annotations

import json

import pytest
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_so_contract import _BRIDGE, _ID, _PATH, _SoBridge
from tests.test_co_update_close_gen import _spec
from u8co_api.co_bridge import to_api_error
from u8co_api.errors import ApiError

_NO = "KH-2026-0001"
_INTERNAL = "HT202601001"
_BLANK = {"contract_no": None, "internal_contract_no": None}
_CASCADE = [
    {
        "type": "dispatch",
        "id": 1000000011,
        "code": "FH0001",
        "result": "updated",
        "before": {"contract_no": "HT202601001", "internal_contract_no": None},
    },
    {
        "type": "sale_invoice",
        "id": 1000000021,
        "code": "FP0001",
        "result": "skipped",
        "reason": "mixed_sources",
        "before": {
            "contract_no": "HT202601009",
            "internal_contract_no": "KH-2026-0009",
        },
    },
]


class _PairBridge(_SoBridge):
    def call(self, path: str, payload: dict) -> dict:
        data = super().call(path, payload)
        data["current"] = {
            "contract_no": payload["contract_no"],
            "internal_contract_no": payload["internal_contract_no"],
        }
        if payload.get("cascade") is True:
            data["cascade"] = [dict(item) for item in _CASCADE]
        return data


def _pair(**over) -> dict:
    body = _auth(
        type="sale_order",
        id=_ID,
        mode="pair",
        contract_no=_NO,
        internal_contract_no=_INTERNAL,
        expect=dict(_BLANK),
    )
    body.update(over)
    return body


def test_pair_forwards_both_values_and_expect() -> None:
    fake = _PairBridge()
    ok = _client(fake).post(_PATH, json=_pair())
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == _BRIDGE
    assert (sent["mode"], sent["contract_no"], sent["internal_contract_no"]) == (
        "pair",
        _NO,
        _INTERNAL,
    )
    assert sent["expect"] == {"contract_no": "", "internal_contract_no": ""}
    assert "cascade" not in sent
    assert "customer_contract_no" not in sent
    assert ok.json()["current"] == {
        "contract_no": _NO,
        "internal_contract_no": _INTERNAL,
    }
    assert "cascade" not in ok.json()


def test_pair_cascade_is_forwarded_and_list_reaches_the_caller() -> None:
    fake = _PairBridge()
    ok = _client(fake).post(_PATH, json=_pair(cascade=True))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["cascade"] is True
    assert ok.json()["cascade"] == _CASCADE


def test_pair_cascade_false_is_not_forwarded() -> None:
    fake = _PairBridge()
    assert _client(fake).post(_PATH, json=_pair(cascade=False)).status_code == 200
    assert "cascade" not in fake.calls[-1][1]


def test_pair_sealed_keys() -> None:
    with _Up() as server:
        _wired(server.base_url).post(_PATH, json=_pair(cascade=True))
        _method, _seen, raw = server.httpd.hits[-1]
    keys = (
        "type",
        "id",
        "mode",
        "contract_no",
        "internal_contract_no",
        "expect",
        "cascade",
        "caller",
        "caller_trust",
    )
    assert set(json.loads(raw)) == _sealed(*keys)
    assert set(json.loads(raw)["expect"]) == {"contract_no", "internal_contract_no"}


def test_classic_expect_keeps_its_two_keys() -> None:
    fake = _SoBridge()
    body = _auth(
        type="sale_order",
        id=_ID,
        contract_no=_NO,
        expect={"contract_no": None, "customer_contract_no": None},
    )
    assert _client(fake).post(_PATH, json=body).status_code == 200
    sent = fake.calls[-1][1]
    assert sent["expect"] == {"contract_no": "", "customer_contract_no": ""}
    assert "mode" not in sent


def test_pair_dry_run_is_forwarded() -> None:
    fake = _PairBridge()
    ok = _client(fake).post(_PATH, json=_pair(cascade=True, dry_run=True))
    assert ok.status_code == 200, ok.text
    sent = fake.calls[-1][1]
    assert (sent["dry_run"], sent["cascade"]) == (True, True)


_CLASSIC = {"contract_no": None, "customer_contract_no": None}
_REJECTS = (
    _pair(mode="swap"),
    _pair(mode=None),
    _pair(mode="PAIR"),
    _pair(internal_contract_no=None),
    _pair(internal_contract_no=""),
    _pair(internal_contract_no="无"),
    _pair(internal_contract_no=" HT1"),
    _pair(internal_contract_no="H" * 121),
    _pair(internal_contract_no=5),
    _pair(customer_contract_no="KH1"),
    _pair(customer_contract_no=None),
    _pair(replace=True),
    _pair(replace=False),
    _pair(cascade="true"),
    _pair(cascade=1),
    _pair(cascade=None),
    _pair(expect=dict(_CLASSIC)),
    _pair(expect={"contract_no": None}),
    _pair(expect={**_BLANK, "customer_contract_no": None}),
    _pair(expect={"contract_no": None, "internal_contract_no": "J\n1"}),
    _auth(
        type="sale_order",
        id=_ID,
        mode="pair",
        internal_contract_no=_INTERNAL,
        expect=dict(_BLANK),
    ),
    _auth(
        type="sale_order",
        id=_ID,
        contract_no=_NO,
        internal_contract_no=_INTERNAL,
        expect=dict(_CLASSIC),
    ),
    _auth(type="sale_order", id=_ID, contract_no=_NO, cascade=True, expect=dict(_CLASSIC)),
    _auth(type="sale_order", id=_ID, contract_no=_NO, cascade=False, expect=dict(_CLASSIC)),
    _auth(type="sale_order", id=_ID, contract_no=_NO, expect=dict(_BLANK)),
    _auth(
        type="sale_order",
        id=_ID,
        contract_no=_NO,
        expect={**_CLASSIC, "internal_contract_no": None},
    ),
)


@pytest.mark.parametrize("body", _REJECTS)
def test_invalid_pair_body_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_pair_mismatch_detail_reaches_the_caller() -> None:
    bridge = {
        "ok": False,
        "code": "state_mismatch",
        "message": "销售订单表头与 expect 不符",
        "detail": {"current": {"contract_no": "HT202601009", "internal_contract_no": None}},
    }
    fake = _PairBridge()
    fake.error = to_api_error(409, bridge)
    denied = _client(fake).post(_PATH, json=_pair())
    assert denied.status_code == 409
    assert denied.json()["error"]["detail"] == {"current": {"contract_no": "HT202601009", "internal_contract_no": None}}


def test_too_many_downstream_passes_through() -> None:
    fake = _PairBridge()
    fake.error = ApiError(409, "too_many_downstream", "下游单据超过 200 张")
    denied = _client(fake).post(_PATH, json=_pair(cascade=True))
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "too_many_downstream"


def test_openapi_describes_pair_mode() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    for word in (
        "mode",
        "pair",
        "internal_contract_no",
        "cascade",
        "mixed_sources",
        "too_many_downstream",
        "内部合同号",
    ):
        assert word in operation["description"]
