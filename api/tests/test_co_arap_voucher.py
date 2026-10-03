"""/v1/co/arap/voucher 与 /v1/co/arap/voucher/delete：应收 / 应付制单和取消制单。桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line as _audit_line
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec

_PATH = "/v1/co/arap/voucher"
_DROP = "/v1/co/arap/voucher/delete"
_PZ = "AR0000000004751"


def test_voucher_forwards_exact_sealed_keys() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        body = _auth(flag="AR", type="sale_invoice", id=9, sign="转", voucher_date="2026-09-24", digest="销售")
        response = client.post(_PATH, json=body)
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert method == "POST"
    assert seen == "/u8co/v1/arap/voucher"
    assert set(sent) == _sealed("flag", "type", "id", "sign", "voucher_date", "digest", "caller")
    assert (sent["flag"], sent["type"], sent["id"], sent["voucher_date"]) == ("AR", "sale_invoice", 9, "2026-09-24")
    assert _SECRET.encode() not in raw


def test_voucher_omits_optional_fields_and_keeps_bridge_extras() -> None:
    fake = FakeBridge()
    ok = _client(fake).post(_PATH, json=_auth(flag="AP", type="ap_payment", id=3))
    assert ok.status_code == 200, ok.text
    path, payload = fake.calls[-1]
    assert path == "/v1/arap/voucher"
    assert "sign" not in payload and "voucher_date" not in payload and "digest" not in payload
    body = ok.json()
    assert body["pz_id"] == "AP0000000000001"
    assert body["voucher"]["num"] == "转-0172"
    assert [line["entry"] for line in body["lines"]] == [1, 2, 3]
    # 桥多给的字段不丢。
    assert body["lines"][2]["memo"] == "x"


@pytest.mark.parametrize(
    "body",
    (
        _auth(flag="AR", type="purchase_invoice", id=1),
        _auth(flag="AP", type="ar_receipt", id=1),
        _auth(flag="AP", type="ar_refund", id=1),
        _auth(flag="AR", type="ap_refund", id=1),
        _auth(flag="ar", type="sale_invoice", id=1),
        _auth(flag="AR", type="sale_order", id=1),
        _auth(flag="AR", type="sale_invoice", id=0),
        _auth(flag="AR", type="sale_invoice", id=2147483648),
        _auth(flag="AR", type="sale_invoice", id=1, voucher_date="2026/09/24"),
        _auth(flag="AR", type="sale_invoice", id=1, sign="转账凭"),
        _auth(flag="AR", type="sale_invoice", id=1, digest="x" * 121),
        _auth(flag="AR", type="sale_invoice"),
        _auth(type="sale_invoice", id=1),
        _auth(flag="AR", type="sale_invoice", id=1, pz_id=_PZ),
    ),
)
def test_voucher_rejects_bad_bodies_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code in (400, 422), denied.text
    assert fake.calls == []


@pytest.mark.parametrize(("flag", "kind"), (("AP", "ap_refund"), ("AR", "ar_refund")))
def test_refund_voucher_reaches_the_bridge(flag: str, kind: str) -> None:
    fake = FakeBridge()
    ok = _client(fake).post(_PATH, json=_auth(flag=flag, type=kind, id=5))
    assert ok.status_code == 200, ok.text
    path, payload = fake.calls[-1]
    assert path == "/v1/arap/voucher"
    assert (payload["flag"], payload["type"], payload["id"]) == (flag, kind, 5)


def _refs(kind: str, *ids: int) -> list[dict]:
    return [{"type": kind, "id": one} for one in ids]


def test_merge_forwards_ids_and_fills_the_type() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(_PATH, json=_auth(flag="AR", ids=_refs("ar_receipt", 7, 3)))
        assert response.status_code == 200, response.text
        sent = json.loads(server.httpd.hits[-1][2])
    assert set(sent) == _sealed("flag", "type", "ids", "caller")
    assert sent["type"] == "ar_receipt"
    assert sent["ids"] == _refs("ar_receipt", 7, 3)


@pytest.mark.parametrize(
    "body",
    (
        _auth(flag="AR", type="ar_receipt", id=1, ids=_refs("ar_receipt", 1, 2)),
        _auth(flag="AR", ids=_refs("ar_receipt", 1)),
        _auth(flag="AR", ids=_refs("ar_receipt", *range(1, 22))),
        _auth(flag="AR", ids=_refs("ar_receipt", 1, 1)),
        _auth(flag="AR", ids=[{"type": "ar_receipt", "id": 1}, {"type": "ar_bill", "id": 2}]),
        _auth(flag="AR", type="ar_bill", ids=_refs("ar_receipt", 1, 2)),
        _auth(flag="AP", ids=_refs("ar_receipt", 1, 2)),
        _auth(flag="AR", ids=[{"type": "ar_receipt", "id": 1, "x": 1}, {"type": "ar_receipt", "id": 2}]),
        _auth(flag="AR", ids=[1, 2]),
    ),
)
def test_merge_rejects_bad_ids_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code in (400, 422), denied.text
    assert fake.calls == []


def test_merge_is_audited_with_the_ids(capsys: pytest.CaptureFixture[str]) -> None:
    ok = _client().post(_PATH, json=_auth(flag="AR", ids=_refs("ar_receipt", 7, 3)))
    assert ok.status_code == 200, ok.text
    assert _audit_line(capsys.readouterr().out, _PATH)["action"] == "co:arap/voucher:voucher#ids:7,3"


def test_voucher_is_audited_with_the_document_id(capsys: pytest.CaptureFixture[str]) -> None:
    ok = _client().post(_PATH, json=_auth(flag="AR", type="sale_invoice", id=9))
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert _audit_line(out, _PATH)["action"] == "co:arap/voucher:voucher#9"


def test_drop_forwards_exact_sealed_keys() -> None:
    fake = FakeBridge()
    ok = _client(fake).post(_DROP, json=_auth(flag="AR", pz_id=_PZ))
    assert ok.status_code == 200, ok.text
    path, payload = fake.calls[-1]
    assert path == "/v1/arap/voucher/delete"
    assert (payload["flag"], payload["pz_id"]) == ("AR", _PZ)
    with _Up() as server:
        _wired(server.base_url).post(_DROP, json=_auth(flag="AR", pz_id=_PZ))
        sent = json.loads(server.httpd.hits[-1][2])
    assert set(sent) == _sealed("flag", "pz_id", "caller")
    body = ok.json()
    assert (body["pz_id"], body["deleted"], body["voucher"]["no"]) == (_PZ, True, 172)


@pytest.mark.parametrize(
    "body",
    (
        _auth(flag="AP", pz_id=_PZ),
        _auth(flag="AR", pz_id="AP1"),
        _auth(flag="AR", pz_id="AR"),
        _auth(flag="AR", pz_id="GL0000000004229"),
        _auth(flag="AR", pz_id="AR1 "),
        _auth(flag="AR", pz_id="AR" + "1" * 21),
        _auth(flag="AR", pz_id=4751),
        _auth(flag="AR"),
        _auth(flag="AR", pz_id=_PZ, id=1),
    ),
)
def test_drop_rejects_bad_bodies_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_DROP, json=body)
    assert denied.status_code in (400, 422), denied.text
    assert fake.calls == []


def test_drop_is_audited_with_the_pz_id(capsys: pytest.CaptureFixture[str]) -> None:
    ok = _client().post(_DROP, json=_auth(flag="AR", pz_id=_PZ))
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _audit_line(out, _DROP)["action"] == "co:arap/voucher/delete:delete#" + _PZ


def test_routes_are_in_openapi_as_write_routes() -> None:
    paths = _spec()["paths"]
    made = paths[_PATH]["post"]
    assert made["summary"] == "应收 / 应付制单"
    assert made["operationId"] == "coArapVoucher"
    assert "权限：写" in made["description"]
    for phrase in ("合并制单", "ids", "U8PzInsert", "Ap_CancelNo", "voucher_date", "凭证已生成，不能重复制单。"):
        assert phrase in made["description"], phrase
    dropped = paths[_DROP]["post"]
    assert dropped["operationId"] == "coArapVoucherDelete"
    assert "此凭证已记账" in dropped["description"]
