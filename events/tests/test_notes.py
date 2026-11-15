"""票据数据源（notes：ar_note / ap_note）。走单据轮询；列表经 vouchers/list，空扫描确认经 notes/get。不连网络。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_kinds import KIND_NAMES, NOTE_KINDS
from co.client.u8co_gl_arc import VoucherQuery
from u8co_events.bridge import BridgeLister, DocumentNotFound, Operator, PageRequest, ProofClient, not_found_from
from u8co_events.config import NOTE_TYPES
from u8co_events.poller import Poller
from u8co_events.state import Store

from fakes import FakeLister, doc, make_config

_NOTE_PATH = "/u8co/v1/notes/get"
_OPERATOR = Operator("999", "2026", "op001", "secret")
_CALL = U8Call(acc="999", year="2026", operator="op001", password="secret", date="2026-09-28", doc_id=7)


def _note(doc_id: int, ufts: int, **state: Any) -> dict[str, Any]:
    row = doc(doc_id, ufts, code=f"PJ{doc_id:04d}", amount=1000.0, remainder=1000.0, settle_code="5")
    row.update(state)
    return row


class _Recorder(U8CoClient):
    """只记下 call 的路由和字段，不发请求。"""

    def __init__(self) -> None:
        super().__init__("http://192.0.2.10:8765/u8co", "ab" * 32, timeout=1)
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        self.sent.append((route, dict(fields)))
        return {"ok": True}


# ---- 客户端：票据只是列表类型 ----


def test_note_kinds_stay_out_of_voucher_kinds() -> None:
    assert NOTE_KINDS == NOTE_TYPES
    assert not set(NOTE_KINDS) & set(KIND_NAMES)


@pytest.mark.parametrize("kind", NOTE_KINDS)
def test_client_lists_notes(kind: str) -> None:
    client = _Recorder()
    client.list_vouchers(_CALL, VoucherQuery(kind=kind, keys_only=True, changed_since="12", after=3, limit=20))
    route, fields = client.sent[0]
    assert route == "/v1/vouchers/list"
    assert (fields["type"], fields["keys_only"], fields["changed_since"], fields["after"]) == (kind, True, "12", 3)


def test_client_list_still_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="未知的单据类型"):
        _Recorder().list_vouchers(_CALL, VoucherQuery(kind="gl_voucher"))


@pytest.mark.parametrize("kind", NOTE_KINDS)
def test_client_voucher_load_and_writes_refuse_notes(kind: str) -> None:
    client = _Recorder()
    with pytest.raises(ValueError):
        client.load_voucher(_CALL, kind)
    with pytest.raises(ValueError):
        client.delete_voucher(_CALL, kind)
    assert client.sent == []


# ---- 事件服务的桥调用 ----


def test_bridge_lister_routes_note_load_to_notes_get() -> None:
    client = _Recorder()
    lister = BridgeLister(client, {"999": _OPERATOR})
    lister.load("999", "ap_note", 42)
    route, fields = client.sent[0]
    assert route == _NOTE_PATH
    assert (fields["acc"], fields["operator"], fields["type"], fields["id"]) == ("999", "op001", "ap_note", 42)
    assert fields["password"] == "secret"  # call() 换成 password_enc


def test_bridge_lister_lists_notes_through_vouchers_list() -> None:
    client = _Recorder()
    BridgeLister(client, {"999": _OPERATOR}).list_page("999", PageRequest(type="ar_note", keys_only=True, limit=5))
    route, fields = client.sent[0]
    assert route == "/v1/vouchers/list"
    assert (fields["type"], fields["keys_only"], fields["limit"]) == ("ar_note", True, 5)


def test_notes_get_not_found_is_proof() -> None:
    raw = json.dumps({"ok": False, "code": "not_found", "message": "票据不存在"}, ensure_ascii=False).encode("utf-8")
    assert isinstance(not_found_from(_NOTE_PATH, 404, raw), DocumentNotFound)
    unknown = json.dumps({"ok": False, "code": "not_found", "message": "未知路径"}, ensure_ascii=False).encode("utf-8")
    assert not_found_from(_NOTE_PATH, 404, unknown) is None


def test_proof_client_note_load_end_to_end(monkeypatch) -> None:
    seen: list[tuple[str, dict[str, Any]]] = []
    raw = json.dumps({"ok": False, "code": "not_found", "message": "票据不存在"}, ensure_ascii=False).encode("utf-8")

    def fake_exchange(self, method, path, body, *_rest):
        seen.append((path, json.loads(body.decode("utf-8"))))
        return 404, raw

    monkeypatch.setattr(U8CoClient, "_exchange", fake_exchange)
    client = ProofClient("http://192.0.2.10:8765/u8co", "ab" * 32, timeout=1)
    with pytest.raises(DocumentNotFound):
        BridgeLister(client, {"999": _OPERATOR}).load("999", "ar_note", 9)
    path, body = seen[0]
    assert path == _NOTE_PATH
    assert (body["type"], body["id"]) == ("ar_note", 9)
    assert "password" not in body and body["password_enc"]


# ---- 轮询：票据与单据同一套规则 ----


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 28, 1, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def env(tmp_path):
    path = str(tmp_path / "s.db")
    store = Store(path)
    yield path, store, FakeLister(), _Clock()
    store.close()


def _poller(path: str, store: Store, lister: FakeLister, clock: _Clock, **top: Any) -> Poller:
    account = {"acc": "999", "operator_file": "/run/secrets/op.json", "types": ["sale_order"],
               "sources": ["vouchers", "notes"]}
    return Poller(make_config(path, accounts=[account], **top), store, lister, now=clock)


def _events(store: Store, type_: str) -> list[tuple[int, str]]:
    rows = store.pending_outbox(1000)
    return [(row.doc_id, row.kind) for row in rows if json.loads(row.payload)["type"] == type_]


def test_notes_polled_after_vouchers(env) -> None:
    path, store, lister, clock = env
    _poller(path, store, lister, clock, delete_scan_minutes=0).run_once()
    assert [req.type for _, req in lister.calls] == ["sale_order", "ar_note", "ap_note"]
    assert {(row.account, row.type) for row in store.status()} >= {("999", "ar_note"), ("999", "ap_note")}


def test_note_created_closed_modified(env) -> None:
    path, store, lister, clock = env
    lister.put("ar_note", _note(1, 1))
    poller = _poller(path, store, lister, clock, delete_scan_minutes=0)
    poller.run_once()
    assert store.outbox_size() == 0  # 首轮只记快照
    # 9A 托收结清：余额回写为 0，表头 Ufts 变，列表 closed=1。
    lister.put("ar_note", _note(1, 10, remainder=0.0, closed=True))
    lister.put("ar_note", _note(2, 11))
    poller.run_once()
    assert _events(store, "ar_note") == [(1, "closed"), (2, "created")]
    lister.put("ar_note", _note(2, 12, remainder=400.0))
    poller.run_once()
    assert _events(store, "ar_note")[-1] == (2, "modified")
    assert _events(store, "ap_note") == []


def test_note_empty_scan_confirmed_by_notes_get(env) -> None:
    path, store, lister, clock = env
    lister.put("ar_note", _note(1, 1))
    lister.put("ar_note", _note(2, 2))
    poller = _poller(path, store, lister, clock, delete_scan_minutes=30)
    poller.run_once()
    clock.now = clock.now.replace(hour=2)
    lister.docs["ar_note"].clear()
    poller.run_once()
    assert sorted(_events(store, "ar_note")) == [(1, "deleted"), (2, "deleted")]
    assert [(type_, doc_id) for _, type_, doc_id in lister.loads] == [("ar_note", 1), ("ar_note", 2)]
