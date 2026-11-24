"""存货核算制单 /v1/co/ia/voucher、取消制单 /v1/co/ia/voucher/delete：请求校验、转发的字段、响应放行、审计、
写入分类和 OpenAPI。桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_update_close_gen import _spec
from u8co_api.co_access import ACCESS, WRITE
from u8co_api.errors import ApiError
from u8co_api.write_class import RULES

_PATH = "/v1/co/ia/voucher"
_DROP = "/v1/co/ia/voucher/delete"
_PZ = "2026IA0000000000001"
_IDS = [{"type": "09", "id": 1001}, {"type": "3201", "id": 1002}]


class _VoucherBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        if path.endswith("/delete"):
            voucher = {"year": 2026, "period": 9, "sign": "转", "no": 31}
            return {"ok": True, "acc": "801", "pz_id": payload["pz_id"], "deleted": True, "voucher": voucher}
        if payload.get("dry_run") is True:
            detail = {"vouchers": [{"pz_id": _PZ, "lines": [{"account": "1403", "debit": 800}]}], "skipped": []}
            head = {"ok": True, "dry_run": True, "mode": "validate", "action": "ia_voucher", "docs": []}
            return dict(head, detail=detail)
        line = {"entry": 1, "account": "1403", "digest": "其他出库单", "debit": 0, "credit": 800}
        line["bill_code"] = "DEMO-001"
        voucher = {
            "pz_id": _PZ,
            "making_system": "IA",
            "year": 2026,
            "period": 9,
            "sign": "转",
            "no": 31,
            "num": "转-0031",
            "date": "2026-09-30",
            "bills": [{"type": "09", "id": 1001, "code": "DEMO-001", "rows": 2}],
            "lines": [line],
            "bridge_extra": "kept",
        }
        skipped = [{"type": "08", "id": 7, "code": "DEMO-002", "reason": "调拨、形态转换业务的制单暂不支持"}]
        return {"ok": True, "acc": "801", "merge": "per_bill", "count": 1, "vouchers": [voucher], "skipped": skipped}


def test_forwards_ids_and_keeps_bridge_fields() -> None:
    fake = _VoucherBridge()
    ok = _client(fake).post(_PATH, json=_auth(ids=_IDS, merge="all", sign="转", voucher_date="2026-09-30"))
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == "/v1/ia/voucher"
    assert sent["ids"] == _IDS and sent["merge"] == "all" and sent["sign"] == "转"
    assert "month" not in sent and "types" not in sent and "digest" not in sent
    body = ok.json()
    assert body["vouchers"][0]["pz_id"] == _PZ
    assert body["vouchers"][0]["bridge_extra"] == "kept"
    assert body["skipped"][0]["code"] == "DEMO-002"


def test_month_with_types() -> None:
    fake = _VoucherBridge()
    ok = _client(fake).post(_PATH, json=_auth(month=9, types=["09", "11"], digest="文本A"))
    assert ok.status_code == 200, ok.text
    sent = fake.calls[-1][1]
    assert sent["month"] == 9 and sent["types"] == ["09", "11"] and sent["digest"] == "文本A"


def test_sends_exact_sealed_keys() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        client.post(_PATH, json=_auth(month=9))
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert (method, seen) == ("POST", "/u8co/v1/ia/voucher")
    assert set(sent) == _sealed("month", "caller", "caller_trust")
    assert _SECRET not in raw.decode("utf-8")


_ELEVEN = [{"type": "09", "id": i} for i in range(1, 12)]
_REJECTS = (
    (_PATH, _auth()),
    (_PATH, _auth(month=9, ids=_IDS)),
    (_PATH, _auth(month=13)),
    (_PATH, _auth(month="9")),
    (_PATH, _auth(ids=_IDS, types=["09"])),
    (_PATH, _auth(ids=[{"type": "sale_out", "id": 1}])),
    (_PATH, _auth(ids=[{"type": "09", "id": 0}])),
    (_PATH, _auth(ids=[{"type": "09", "id": 1, "x": 1}])),
    (_PATH, _auth(ids=[{"type": "09", "id": 1}, {"type": "09", "id": 1}])),
    (_PATH, _auth(ids=_ELEVEN)),
    (_PATH, _auth(month=9, types=["x9"])),
    (_PATH, _auth(month=9, merge="one")),
    (_PATH, _auth(month=9, digest=" ")),
    (_PATH, _auth(month=9, voucher_date="2026/09/30")),
    (_DROP, _auth()),
    (_DROP, _auth(pz_id="AP0000000000001")),
    (_DROP, _auth(pz_id="2026IA00000000001")),
)


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_eleven_bills_merged_is_fine() -> None:
    fake = _VoucherBridge()
    ok = _client(fake).post(_PATH, json=_auth(ids=_ELEVEN, merge="all"))
    assert ok.status_code == 200, ok.text


def test_dry_run_returns_the_plan() -> None:
    fake = _VoucherBridge()
    ok = _client(fake).post(_PATH, json=_auth(month=9, dry_run=True))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["dry_run"] is True
    body = ok.json()
    assert body["mode"] == "validate"
    assert body["detail"]["vouchers"][0]["pz_id"] == _PZ


def test_delete_forwards_pz_id() -> None:
    fake = _VoucherBridge()
    ok = _client(fake).post(_DROP, json=_auth(pz_id=_PZ))
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == "/v1/ia/voucher/delete" and sent["pz_id"] == _PZ
    assert ok.json()["voucher"]["no"] == 31


def test_audit_names_the_selection(capsys) -> None:
    ok = _client(_VoucherBridge()).post(_PATH, json=_auth(ids=_IDS, merge="all"))
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert audit_line(out, _PATH)["action"].endswith("#ids:09-1001,3201-1002")
    _client(_VoucherBridge()).post(_DROP, json=_auth(pz_id=_PZ))
    assert audit_line(capsys.readouterr().out, _DROP)["action"].endswith("#" + _PZ)


@pytest.mark.parametrize(
    ("status", "code"),
    (
        (403, "test_account_only"),
        (403, "feature_disabled"),
        (409, "state_mismatch"),
        (409, "u8_rejected"),
        (504, "outcome_unknown"),
    ),
)
def test_bridge_errors_pass_through(status: int, code: str) -> None:
    fake = _VoucherBridge()
    fake.error = ApiError(status, code, "存货核算制单只对配置为测试账套的账套开放")
    denied = _client(fake).post(_PATH, json=_auth(month=9))
    assert denied.status_code == status
    assert denied.json()["error"]["code"] == code


def test_write_class_and_access() -> None:
    assert RULES["ia/voucher"] == ("ia", "voucher")
    assert RULES["ia/voucher/delete"] == ("ia", "other")
    assert ACCESS["co:ia/voucher"] == WRITE and ACCESS["co:ia/voucher/delete"] == WRITE


@pytest.mark.parametrize(
    ("path", "summary", "operation"),
    ((_PATH, "存货核算制单", "coIaVoucher"), (_DROP, "取消存货核算制单", "coIaVoucherDelete")),
)
def test_in_openapi_as_write(path: str, summary: str, operation: str) -> None:
    op = _spec()["paths"][path]["post"]
    assert op["summary"] == summary
    assert op["operationId"] == operation
    assert op["tags"] == ["存货核算"]
    assert "权限：写" in op["description"]
    assert "test_account_only" in op["description"]
    assert "feature_disabled" in op["description"]
