"""/v1/co/arap/writeoff/auto：应收 / 应付自动核销。桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line as _audit_line
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec

_PATH = "/v1/co/arap/writeoff/auto"
_FULL = {
    "flag": "AR",
    "partner": "C001",
    "date_to": "2026-09-01",
    "receipt": {"type": "ar_receipt", "id": 5, "line_id": 7},
    "targets": [{"type": "sale_invoice", "id": 9}, {"type": "ar_bill", "id": 12}],
    "max_amount": 1.5,
    "dry_run": False,
}


def _ask(**extra) -> dict:
    body = {"flag": "AR", "partner": "C001"}
    body.update(extra)
    return _auth(**body)


def test_auto_forwards_exact_sealed_keys() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(_PATH, json=_auth(**_FULL))
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert method == "POST"
    assert seen == "/u8co/v1/arap/writeoff/auto"
    assert set(sent) == _sealed(*_FULL, "caller")
    for key, value in _FULL.items():
        assert sent[key] == value, key
    assert _SECRET.encode() not in raw
    body = response.json()
    assert (body["flag"], body["partner"]) == ("AR", "C001")
    assert body["batches"][0]["cancel_no"].startswith("HXAR")


def test_auto_minimal_body_and_dry_run() -> None:
    fake = FakeBridge()
    ok = _client(fake).post(_PATH, json=_ask(flag="AP", partner="V001", dry_run=True))
    assert ok.status_code == 200, ok.text
    path, payload = fake.calls[-1]
    assert path == "/v1/arap/writeoff/auto"
    assert (payload["flag"], payload["partner"], payload["dry_run"]) == ("AP", "V001", True)
    for key in ("date_to", "receipt", "targets", "max_amount"):
        assert key not in payload, key
    body = ok.json()
    assert body["dry_run"] is True
    assert body["plan"][0]["targets"][0]["amount"] == 1
    assert "batches" not in body


@pytest.mark.parametrize(
    "body",
    (
        _auth(flag="AR"),
        _ask(partner=""),
        _ask(partner=" C001"),
        _ask(partner="C" * 21),
        _ask(flag="ar"),
        _ask(flag="AR", receipt={"type": "ap_payment", "id": 1}),
        _ask(receipt={"type": "ar_receipt", "id": 0}),
        _ask(receipt={"type": "ar_receipt", "id": 1, "code": "SK1"}),
        _ask(targets=[]),
        _ask(targets=[{"type": "purchase_invoice", "id": 1}]),
        _ask(targets=[{"type": "sale_invoice", "id": 1, "line_id": 2}]),
        _ask(targets=[{"type": "sale_invoice", "id": 1}, {"type": "sale_invoice", "id": 1}]),
        _ask(targets=[{"type": "sale_invoice", "id": i} for i in range(1, 202)]),
        _ask(max_amount=0),
        _ask(max_amount=1.234),
        _ask(max_amount="1"),
        _ask(dry_run="true"),
        _ask(include_prepay="true"),
        _ask(include_prepay=1),
        _ask(date_to="2026/09/01"),
        _ask(date="2026-09-01", date_to="2026-09-02"),
        _ask(id=1),
    ),
)
def test_auto_rejects_bad_bodies_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code in (400, 422), denied.text
    assert fake.calls == []


def test_auto_forwards_include_prepay_only_when_given() -> None:
    fake = FakeBridge()
    client = _client(fake)
    assert client.post(_PATH, json=_ask(include_prepay=True)).status_code == 200
    assert fake.calls[-1][1]["include_prepay"] is True
    assert client.post(_PATH, json=_ask()).status_code == 200
    assert "include_prepay" not in fake.calls[-1][1]


def test_auto_is_audited_with_flag_and_partner(capsys: pytest.CaptureFixture[str]) -> None:
    ok = _client().post(_PATH, json=_ask())
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert _audit_line(out, _PATH)["action"] == "co:arap/writeoff/auto:auto#AR:C001"


def test_auto_is_in_openapi_as_a_write_route() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["summary"] == "应收 / 应付自动核销"
    assert operation["operationId"] == "coArapWriteoffAuto"
    text = operation["description"]
    assert "权限：写" in text
    for phrase in ("partner", "先进先出", "dry_run", "include_prepay", "同币种", "20 行收付款单、200 行单据", "iHxRule", "AutoCancel", "arap/writeoff/cancel"):
        assert phrase in text, phrase
