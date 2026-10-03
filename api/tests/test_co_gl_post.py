"""R8 总账记账 /v1/co/gl/vouchers/post：请求校验、转发给桥的字段、响应放行、审计和 OpenAPI。桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from u8co_api.errors import ApiError

_PATH = "/v1/co/gl/vouchers/post"
_BRIDGE = "/v1/gl/vouchers/post"
_TWO = [{"sign": "转", "no": 3}, {"sign": "收", "no": 12}]


class _PostBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        state = {"posted": True, "poster": "张三", "verified": True, "checker": "审核人", "void": False}
        posted = [{"sign": v["sign"], "no": v["no"], "state": dict(state), "extra": "kept"} for v in payload["vouchers"]]
        year = payload.get("fiscal_year", 2026)
        return {
            "ok": True,
            "period": payload["period"],
            "fiscal_year": year,
            "posted": posted,
            "local_txn": True,
            "bridge_note": "kept",
        }


def test_post_forwards_exact_fields_and_keeps_bridge_fields() -> None:
    fake = _PostBridge()
    ok = _client(fake).post(_PATH, json=_auth(period=9, vouchers=_TWO))
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == _BRIDGE
    assert sent["vouchers"] == _TWO
    body = ok.json()
    assert body["fiscal_year"] == 2026
    assert body["bridge_note"] == "kept"
    assert body["local_txn"] is True
    assert [item["no"] for item in body["posted"]] == [3, 12]
    assert body["posted"][0]["state"]["poster"] == "张三"
    assert body["posted"][0]["extra"] == "kept"


def test_post_sends_exact_sealed_keys() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        client.post(_PATH, json=_auth(period=9, vouchers=_TWO))
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert (method, seen) == ("POST", "/u8co" + _BRIDGE)
    assert set(sent) == _sealed("period", "vouchers", "caller")
    assert sent["vouchers"] == _TWO
    assert _SECRET not in raw.decode("utf-8")


def test_post_forwards_fiscal_year_only_when_given() -> None:
    fake = _PostBridge()
    ok = _client(fake).post(_PATH, json=_auth(period=12, vouchers=_TWO[:1], fiscal_year=2025))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["fiscal_year"] == 2025
    assert ok.json()["fiscal_year"] == 2025


_MANY = [{"sign": "转", "no": n} for n in range(1, 202)]

_REJECTS = (
    _auth(vouchers=_TWO),
    _auth(period=9),
    _auth(period=0, vouchers=_TWO),
    _auth(period=13, vouchers=_TWO),
    _auth(period=9, vouchers=[]),
    _auth(period=9, vouchers=_MANY),
    _auth(period=9, vouchers=[{"sign": "转", "no": 3}, {"sign": "转", "no": 3}]),
    _auth(period=9, vouchers=[{"sign": "转", "no": 0}]),
    _auth(period=9, vouchers=[{"sign": "转", "no": 32768}]),
    _auth(period=9, vouchers=[{"sign": "转账凭", "no": 3}]),
    _auth(period=9, vouchers=[{"sign": "转", "no": 3, "period": 9}]),
    _auth(period=9, vouchers=[{"sign": "转"}]),
    _auth(period=9, vouchers=_TWO, fiscal_year=1899),
    _auth(period=9, vouchers=_TWO, sign="转"),
    _auth(period=9, vouchers=_TWO, no=3),
)


@pytest.mark.parametrize("body", _REJECTS)
def test_post_invalid_body_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_post_audit_names_period_and_first_voucher(capsys) -> None:
    ok = _client(_PostBridge()).post(_PATH, json=_auth(period=9, vouchers=_TWO))
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert audit_line(out, _PATH)["action"] == "co:gl/vouchers/post:post#9-转-3+1"


def test_post_bridge_conflict_passes_through() -> None:
    fake = _PostBridge()
    fake.error = ApiError(409, "state_mismatch", "凭证 2026年9期 转-3：凭证未审核")
    denied = _client(fake).post(_PATH, json=_auth(period=9, vouchers=_TWO[:1]))
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "state_mismatch"


def test_post_bridge_retryable_passes_through() -> None:
    fake = _PostBridge()
    fake.error = ApiError(503, "u8_unavailable", "记账事务被中止（死锁），已回滚，未写入，可以稍后重试")
    denied = _client(fake).post(_PATH, json=_auth(period=9, vouchers=_TWO[:1]))
    assert denied.status_code == 503
    assert denied.json()["error"]["code"] == "u8_unavailable"


def test_post_is_in_openapi_as_write() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["summary"] == "记账"
    assert operation["operationId"] == "coGlVoucherPost"
    assert operation["tags"] == ["总账凭证"]
    assert "权限：写" in operation["description"]
    assert "取消记账" in operation["description"]
    assert "503" in operation["description"]
