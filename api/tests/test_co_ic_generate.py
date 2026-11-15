"""按卖方单据生成买方单据：存货换算、挑公司间采购订单、分配数量、预演缺省、幂等键必填、拒绝其他入库。"""

from __future__ import annotations

from tests.ic_fakes import IcBridge, ic_client, login
from tests.support import audit_line, base_claims

_GEN = "/v1/co/intercompany/generate_buyer"
_KEY = {"Idempotency-Key": "ic-801-0001"}


def _orders() -> IcBridge:
    fake = IcBridge()
    row = {"code": "PO3", "partner": "S900001", "closed": False, "inv_code": "A901"}
    fake.orders["801"] = [
        {"id": 2, "line_id": 21, "code": "PO2", "partner": "S900002", "inv_code": "A901", "qty": "999", "in_qty": "0"},
        {**row, "id": 3, "line_id": 31, "qty": "40", "in_qty": "0", "arrived_qty": "0"},
        {**row, "id": 5, "code": "PO5", "line_id": 51, "qty": "30.000000", "in_qty": "0", "arrived_qty": "30"},
        {**row, "id": 5, "code": "PO5", "line_id": 52, "qty": "30", "in_qty": "5", "arrived_qty": "0"},
    ]
    return fake


def _body(**extra) -> dict:
    body = {
        "login": login("801"),
        "seller_acc": "802",
        "type": "purchase_in",
        "lines": [{"inv_code": "A901", "quantity": 30, "seller_id": 1}, {"inv_code": "A901", "quantity": 20}],
        "date": "2026-08-05",
        "head": {"cwhcode": "01"},
    }
    body.update(extra)
    return body


def _generate_calls(fake: IcBridge) -> list[dict]:
    return [payload for path, payload in fake.calls if path == "/v1/vouchers/generate"]


def test_dry_run_is_the_default_and_returns_the_plan() -> None:
    fake = _orders()
    ok = ic_client(fake).post(_GEN, json=_body())
    assert ok.status_code == 200, ok.text
    data = ok.json()
    assert data["dry_run"] is True and data["mode"] == "rollback"
    plan = data["plan"]
    assert (plan["buyer_acc"], plan["seller_acc"], plan["vendor"], plan["po_id"], plan["po_code"]) == (
        "801", "802", "S900001", 5, "PO5"
    )
    assert [(row["source_line_id"], row["quantity"]) for row in plan["lines"]] == [(51, "30"), (52, "20")]
    assert plan["lines"][0]["seller_inv_codes"] == ["A901"]
    query = next(payload for path, payload in fake.calls if path == "/v1/reports/order_execution")
    assert (query["acc"], query["partner"], query["only_open"], query["type"]) == ("801", "S900001", True, "purchase_order")
    (sent,) = _generate_calls(fake)
    assert (sent["acc"], sent["type"], sent["source_type"], sent["id"], sent["dry_run"]) == (
        "801", "purchase_in", "purchase_order", 5, True
    )
    assert sent["lines"] == [{"source_line_id": 51, "quantity": 30}, {"source_line_id": 52, "quantity": 20}]
    assert sent["head"] == {"cwhcode": "01", "dDate": "2026-08-05"}
    assert "idempotency_key" not in sent


def test_real_generation_needs_an_idempotency_key() -> None:
    fake = _orders()
    denied = ic_client(fake).post(_GEN, json=_body(dry_run=False))
    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "idempotency_required"
    assert fake.calls == []


def test_real_generation_forwards_the_key_and_audits_the_route(capsys) -> None:
    fake = _orders()
    ok = ic_client(fake).post(_GEN, json=_body(dry_run=False), headers=_KEY)
    assert ok.status_code == 200, ok.text
    data = ok.json()
    assert (data["id"], data["code"], data["plan"]["po_id"]) == (77, "0000000077", 5)
    (sent,) = _generate_calls(fake)
    assert sent["idempotency_key"] == "ic-801-0001"
    assert "dry_run" not in sent
    row = audit_line(capsys.readouterr().out, _GEN)
    assert row["action"] == "co:intercompany/generate_buyer#802>801:purchase_in:po5"
    assert row["accs"] == ["801"]
    assert row["dry_run"] is False


def test_retry_with_the_same_key_replays_without_planning(capsys) -> None:
    fake = _orders()
    first = {"ok": True, "type": "purchase_in", "id": 77, "code": "0000000077", "lines": 2}
    fake.idem = {"ok": True, "found": True, "state": "ok", "status": 200, "response": first}
    ok = ic_client(fake).post(_GEN, json=_body(dry_run=False), headers=_KEY)
    assert ok.status_code == 200, ok.text
    data = ok.json()
    assert (data["id"], data["code"], "plan" in data) == (77, "0000000077", False)
    assert fake.paths() == ["/v1/idempotency/get"]
    query = fake.calls[0][1]
    assert (query["acc"], query["route"], query["idempotency_key"]) == ("801", "/u8co/v1/vouchers/generate", "ic-801-0001")
    assert query["caller"]
    row = audit_line(capsys.readouterr().out, _GEN)
    assert row["action"] == "co:intercompany/generate_buyer#802>801:purchase_in:replay"


def test_replay_with_different_content_is_a_mismatch() -> None:
    fake = _orders()
    first = {"ok": True, "type": "arrival", "id": 77, "source_type": "purchase_order", "source_id": 5}
    fake.idem = {"ok": True, "found": True, "state": "ok", "status": 200, "response": first}
    denied = ic_client(fake).post(_GEN, json=_body(dry_run=False), headers=_KEY)
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "idempotency_mismatch"
    fake.idem["response"] = {**first, "type": "purchase_in"}
    other_po = ic_client(fake).post(_GEN, json=_body(dry_run=False, po_id=3), headers=_KEY)
    assert other_po.status_code == 409
    same = ic_client(fake).post(_GEN, json=_body(dry_run=False, po_id=5), headers=_KEY)
    assert same.status_code == 200, same.text
    assert _generate_calls(fake) == []


def test_retry_after_unknown_outcome_is_not_regenerated() -> None:
    fake = _orders()
    fake.idem = {"ok": True, "found": True, "state": "outcome_unknown"}
    denied = ic_client(fake).post(_GEN, json=_body(dry_run=False), headers=_KEY)
    assert denied.status_code == 504
    assert denied.json()["error"]["code"] == "outcome_unknown"
    assert _generate_calls(fake) == []


def test_in_flight_record_falls_through_to_the_bridge() -> None:
    fake = _orders()
    fake.idem = {"ok": True, "found": True, "state": "in_flight", "status": None}
    ok = ic_client(fake).post(_GEN, json=_body(dry_run=False), headers=_KEY)
    assert ok.status_code == 200, ok.text
    assert ok.json()["plan"]["po_id"] == 5
    assert len(_generate_calls(fake)) == 1


def test_dry_run_with_a_key_is_refused() -> None:
    fake = _orders()
    denied = ic_client(fake).post(_GEN, json=_body(), headers=_KEY)
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == "dry_run"
    assert _generate_calls(fake) == []


def test_arrival_uses_the_arrived_quantity() -> None:
    fake = _orders()
    lines = [{"inv_code": "A901", "quantity": 40}]
    ok = ic_client(fake).post(_GEN, json=_body(type="arrival", head=None, lines=lines))
    assert ok.status_code == 200, ok.text
    data = ok.json()
    # 订单 5 的行 51 已全部到货，行 52 只剩 30：只有订单 3 够 40。
    assert data["plan"]["po_id"] == 3
    assert [(row["source_line_id"], row["quantity"]) for row in data["plan"]["lines"]] == [(31, "40")]
    (sent,) = _generate_calls(fake)
    assert sent["type"] == "arrival" and sent["head"] == {"dDate": "2026-08-05"}


def test_given_po_must_cover_the_need() -> None:
    fake = _orders()
    denied = ic_client(fake).post(_GEN, json=_body(po_id=3))
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "ic_no_open_po"
    query = next(payload for path, payload in fake.calls if path == "/v1/reports/order_execution")
    assert query["ids"] == [3] and "partner" not in query
    ok = ic_client(_orders()).post(_GEN, json=_body(po_id=5))
    assert ok.status_code == 200, ok.text


def test_no_open_po_is_409() -> None:
    fake = _orders()
    lines = [{"inv_code": "A901", "quantity": 100}]
    denied = ic_client(fake).post(_GEN, json=_body(lines=lines))
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "ic_no_open_po"
    assert _generate_calls(fake) == []


def test_unmapped_inventory_is_409_with_the_codes() -> None:
    fake = _orders()
    lines = [{"inv_code": "ZZ01", "quantity": 1}, {"inv_code": "A901", "quantity": 1}]
    denied = ic_client(fake).post(_GEN, json=_body(lines=lines))
    assert denied.status_code == 409
    error = denied.json()["error"]
    assert error["code"] == "ic_inventory_unmapped"
    assert error["detail"] == {"codes": ["ZZ01"]}
    assert fake.calls == []


def test_other_in_is_refused() -> None:
    fake = _orders()
    denied = ic_client(fake).post(_GEN, json=_body(type="other_in"))
    assert denied.status_code == 400
    assert fake.calls == []


def test_read_only_caller_cannot_generate() -> None:
    fake = _orders()
    denied = ic_client(fake, claims=base_claims(u8co_read=True)).post(_GEN, json=_body())
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"
    assert fake.calls == []


def test_seller_account_must_be_allowed_and_in_the_group() -> None:
    fake = _orders()
    assert ic_client(fake, co_accounts=("801",)).post(_GEN, json=_body()).status_code == 403
    mismatch = ic_client(fake, co_accounts=("801", "998")).post(_GEN, json=_body(seller_acc="998"))
    assert mismatch.status_code == 400
    assert mismatch.json()["error"]["code"] == "ic_group_mismatch"
    assert fake.calls == []
