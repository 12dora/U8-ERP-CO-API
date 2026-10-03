"""R8 应付审核 / 应收审核：vouchers/verify 的 arap_verify / arap_unverify 只收采购发票、销售发票，响应带 arap_* 状态。"""

from __future__ import annotations

import pytest
from tests.support import make_client
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards
from u8co_api.co_models_arap import ARAP_REFUSED, check_verify_action
from u8co_api.errors import ApiError

_VERIFY = "/v1/co/vouchers/verify"
_LOAD = "/v1/co/vouchers/load"


class _ArapBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        done = payload["action"] == "arap_verify"
        state = {
            "arap_verified": done,
            "arap_verifier": "张三" if done else "",
            "arap_verified_at": "2026-09-29" if done else "",
            "gl_voucher": False,
        }
        return {
            "ok": True,
            "acc": payload["acc"],
            "type": payload["type"],
            "id": payload["id"],
            "action": payload["action"],
            "verified_by": state["arap_verifier"],
            "verified_at": state["arap_verified_at"],
            "state": state,
        }


@pytest.mark.parametrize("kind", ["purchase_invoice", "sale_invoice"])
@pytest.mark.parametrize("action", ["arap_verify", "arap_unverify"])
def test_arap_actions_forward_exact_keys(kind: str, action: str) -> None:
    _forwards(_VERIFY, "/v1/vouchers/verify", _auth(type=kind, id=9, action=action), _sealed("type", "id", "action"))


@pytest.mark.parametrize("kind", ["purchase_invoice", "sale_invoice"])
def test_arap_verify_returns_the_audit_state(kind: str) -> None:
    fake = _ArapBridge()
    done = _client(fake).post(_VERIFY, json=_auth(type=kind, id=9, action="arap_verify"))
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["action"] == "arap_verify"
    assert body["verified_by"] == "张三"
    assert body["verified_at"] == "2026-09-29"
    assert body["state"]["arap_verified"] is True
    assert body["state"]["arap_verifier"] == "张三"
    assert body["state"]["gl_voucher"] is False
    assert fake.calls[-1][1]["action"] == "arap_verify"


def test_arap_unverify_clears_the_audit_state() -> None:
    fake = _ArapBridge()
    done = _client(fake).post(_VERIFY, json=_auth(type="purchase_invoice", id=9, action="arap_unverify"))
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["action"] == "arap_unverify"
    assert body["state"]["arap_verified"] is False
    assert body["state"]["arap_verifier"] == ""


@pytest.mark.parametrize("kind", ["sale_order", "purchase_order", "ap_bill", "ar_receipt", "production_order", "bom"])
@pytest.mark.parametrize("action", ["arap_verify", "arap_unverify"])
def test_arap_actions_refused_for_other_types(kind: str, action: str) -> None:
    fake = _ArapBridge()
    denied = _client(fake).post(_VERIFY, json=_auth(type=kind, id=9, action=action))
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


@pytest.mark.parametrize("action", ["arap", "ARAP_VERIFY", "arap_review", "review", ""])
def test_unknown_actions_refused(action: str) -> None:
    fake = _ArapBridge()
    denied = _client(fake).post(_VERIFY, json=_auth(type="purchase_invoice", id=9, action=action))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_plain_verify_still_accepted_for_invoices() -> None:
    fake = FakeBridge()
    for kind in ("purchase_invoice", "sale_invoice"):
        for action in ("verify", "unverify"):
            done = _client(fake).post(_VERIFY, json=_auth(type=kind, id=9, action=action))
            assert done.status_code == 200, done.text
            assert done.json()["action"] == action


def test_legacy_sale_order_verify_rejects_arap_actions() -> None:
    fake = FakeBridge()
    denied = _client(fake).post("/v1/co/sale-orders/verify", json=_auth(id=1, action="arap_verify"))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_check_verify_action_directly() -> None:
    check_verify_action("purchase_invoice", "arap_verify")
    check_verify_action("sale_invoice", "arap_unverify")
    check_verify_action("sale_order", "verify")
    with pytest.raises(ValueError, match=ARAP_REFUSED):
        check_verify_action("ap_bill", "arap_verify")


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (409, "state_mismatch"),
        (409, "u8_rejected"),
        (409, "workflow_enabled"),
        (403, "no_permission"),
        (404, "not_found"),
        (504, "outcome_unknown"),
    ],
)
def test_arap_passes_bridge_errors(status: int, code: str) -> None:
    fake = _ArapBridge()
    fake.error = ApiError(status, code, "说明")
    denied = _client(fake).post(_VERIFY, json=_auth(type="sale_invoice", id=9, action="arap_verify"))
    assert denied.status_code == status
    assert denied.json()["error"]["code"] == code


def test_load_keeps_arap_state_fields() -> None:
    class _Loaded(FakeBridge):
        def call(self, path: str, payload: dict) -> dict:
            self.calls.append((path, dict(payload)))
            state = {"verified": True, "verifier": "张三", "verified_at": "2026-09-28", "arap_verified": True}
            state.update({"arap_verifier": "张三", "arap_verified_at": "2026-09-29", "gl_voucher": False})
            return {"ok": True, "type": payload["type"], "id": payload["id"], "code": "1", "head": {}, "lines": [], "state": state}

    done = _client(_Loaded()).post(_LOAD, json=_auth(type="purchase_invoice", id=9))
    assert done.status_code == 200, done.text
    state = done.json()["state"]
    assert state["arap_verified"] is True
    assert state["arap_verifier"] == "张三"


def test_openapi_lists_arap_actions() -> None:
    schemas = make_client().get("/v1/openapi.json").json()["components"]["schemas"]
    action = schemas["CoVoucherVerifyIn"]["properties"]["action"]
    assert {"arap_verify", "arap_unverify"} <= set(action["enum"])
    assert "arap_verify" in action["description"]
