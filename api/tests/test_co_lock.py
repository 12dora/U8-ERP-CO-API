"""/v1/co/vouchers/lock：销售订单 / 采购订单锁定与解锁。桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line as _audit_line
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec

_PATH = "/v1/co/vouchers/lock"


@pytest.mark.parametrize(
    ("kind", "action"),
    (("sale_order", "lock"), ("sale_order", "unlock")),
)
def test_lock_forwards_exact_sealed_keys(kind: str, action: str) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(_PATH, json=_auth(type=kind, id=4, action=action))
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert method == "POST"
    assert seen == "/u8co/v1/vouchers/lock"
    assert set(sent) == _sealed("type", "id", "action", "caller")
    assert sent["type"] == kind
    assert sent["action"] == action
    assert _SECRET.encode() not in raw
    body = response.json()
    assert body["locked"] is (action == "lock")
    assert body["locker"] == ("张三" if action == "lock" else "")


@pytest.mark.parametrize(
    "body",
    (
        _auth(type="dispatch", id=1, action="lock"),
        _auth(type="sale_invoice", id=1, action="unlock"),
        _auth(type="sale_order", id=1, action="close"),
        _auth(type="sale_order", id=1),
        _auth(type="sale_order", action="lock"),
        _auth(type="sale_order", id=0, action="lock"),
        _auth(type="sale_order", id=2147483648, action="lock"),
        _auth(type="sale_order", id=1, action="lock", line_ids=[1]),
        # 采购订单锁定暂不支持（DoLock 实测一律拒绝）。
        _auth(type="purchase_order", id=1, action="lock"),
        _auth(type="purchase_order", id=1, action="unlock"),
    ),
)
def test_lock_rejects_bad_bodies_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_lock_is_404_when_disabled() -> None:
    fake = FakeBridge()
    denied = _client(fake, co_enabled=False).post(_PATH, json=_auth(type="sale_order", id=1, action="lock"))
    assert denied.status_code == 404
    assert fake.calls == []


def test_lock_rejects_an_account_outside_the_allowlist() -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=_auth(type="sale_order", id=1, action="lock", acc="001"))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []


def test_lock_is_audited_with_action_and_id(capsys: pytest.CaptureFixture[str]) -> None:
    ok = _client().post(_PATH, json=_auth(type="sale_order", id=12, action="unlock"))
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert _audit_line(out, _PATH)["action"] == "co:vouchers/lock:unlock#12"


def test_lock_is_in_openapi_as_a_write_route() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["summary"] == "锁定或解锁单据"
    assert operation["operationId"] == "coVoucherLock"
    assert operation["tags"] == ["单据新增删除"]
    assert "权限：写" in operation["description"]


class _AlreadyBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        body = super().call(path, payload)
        body["already"] = True
        return body


def test_lock_passes_already_through() -> None:
    fake = _AlreadyBridge()
    ok = _client(fake).post(_PATH, json=_auth(type="sale_order", id=4, action="lock"))
    assert ok.status_code == 200, ok.text
    assert ok.json()["already"] is True
    assert ok.json()["locked"] is True
