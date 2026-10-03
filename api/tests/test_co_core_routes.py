"""/v1/co core routes (load, create, delete, workflow, health). The fake bridge is local; the real client signs the body."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from tests.support import write_keys
from tests.test_co_gate import _HEX, _SECRET, FakeBridge, _client, _login, bridge_body
from u8co_api.co_client import CoBridge

_SEALED = {"acc", "year", "operator", "password_enc", "date"}
_PLAIN = {"acc", "operator", "password", "year", "date"}
_KINDS = (
    "sale_order",
    "dispatch",
    "purchase_order",
    "arrival",
    "purchase_in",
    "other_in",
    "other_out",
    "product_in",
    "material_out",
    "sale_out",
    "stock_opening",
    "qm_incoming_check",
    "qm_product_check",
    "qm_incoming_reject",
    "qm_product_reject",
)
_QM = _KINDS[-4:]


def _auth(**extra) -> dict:
    body = _login(year="2026", date="2026-09-27")
    body.update(extra)
    return body


def _qm(**extra) -> dict:
    body = _auth(type="qm_product_check", id=8)
    body.update(extra)
    return body


def _sealed(*names: str) -> set[str]:
    return _SEALED | set(names)


_HEAD = {"cWhCode": "01", "cMemo": "备注", "bGift": False}
_LINES = [{"cInvCode": "A", "iQuantity": 1.5}]


_FORWARD = (
    ("/v1/co/login-check", "/v1/login-check", _auth(), _sealed()),
    ("/v1/co/sale-orders/verify", "/v1/sale-orders/verify", _auth(id=1, action="verify"), _sealed("id", "action")),
    ("/v1/co/dispatches/verify", "/v1/dispatches/verify", _auth(id=1, action="unverify"), _sealed("id", "action")),
    ("/v1/co/vouchers/load", "/v1/vouchers/load", _auth(type="sale_order", id=1), _sealed("type", "id")),
    (
        "/v1/co/vouchers/verify",
        "/v1/vouchers/verify",
        _auth(type="other_in", id=1, action="verify"),
        _sealed("type", "id", "action"),
    ),
    (
        "/v1/co/vouchers/create",
        "/v1/vouchers/create",
        _auth(type="other_in", head=_HEAD, lines=_LINES),
        _sealed("type", "head", "lines"),
    ),
    (
        "/v1/co/vouchers/create",
        "/v1/vouchers/create",
        _auth(type="stock_opening", head=_HEAD, lines=_LINES),
        _sealed("type", "head", "lines"),
    ),
    ("/v1/co/vouchers/delete", "/v1/vouchers/delete", _auth(type="other_out", id=3), _sealed("type", "id")),
    ("/v1/co/workflow/state", "/v1/workflow/state", _qm(), _sealed("type", "id")),
    ("/v1/co/workflow/history", "/v1/workflow/history", _qm(), _sealed("type", "id")),
    ("/v1/co/workflow/tasks", "/v1/workflow/tasks", _auth(), _sealed()),
    ("/v1/co/workflow/tasks", "/v1/workflow/tasks", _auth(type="qm_incoming_check"), _sealed("type")),
    ("/v1/co/workflow/submit", "/v1/workflow/submit", _qm(), _sealed("type", "id")),
    ("/v1/co/workflow/withdraw", "/v1/workflow/withdraw", _qm(), _sealed("type", "id")),
    ("/v1/co/workflow/approve", "/v1/workflow/approve", _qm(), _sealed("type", "id")),
    ("/v1/co/workflow/approve", "/v1/workflow/approve", _qm(opinion="同意"), _sealed("type", "id", "opinion")),
    ("/v1/co/workflow/disagree", "/v1/workflow/disagree", _qm(opinion="不同意"), _sealed("type", "id", "opinion")),
    ("/v1/co/workflow/return", "/v1/workflow/return", _qm(opinion="退回"), _sealed("type", "id", "opinion")),
    ("/v1/co/workflow/abandon", "/v1/workflow/abandon", _qm(), _sealed("type", "id")),
    ("/v1/co/workflow/abandon", "/v1/workflow/abandon", _qm(opinion="弃审"), _sealed("type", "id", "opinion")),
    ("/v1/co/workflow/resubmit", "/v1/workflow/resubmit", _qm(), _sealed("type", "id")),
)

_REJECTS = (
    ("/v1/co/workflow/disagree", _qm()),
    ("/v1/co/workflow/return", _qm(opinion="")),
    ("/v1/co/workflow/submit", _qm(opinion="不要")),
    ("/v1/co/workflow/withdraw", _qm(opinion="不要")),
    ("/v1/co/workflow/resubmit", _qm(opinion="不要")),
    ("/v1/co/workflow/approve", _qm(opinion="x" * 501)),
    ("/v1/co/workflow/state", _qm(type="sale_order")),
    ("/v1/co/workflow/tasks", _auth(type="dispatch")),
    ("/v1/co/vouchers/load", _auth(type="sale_order", id=0)),
    ("/v1/co/vouchers/load", _auth(type="sale_order", id=2147483648)),
    ("/v1/co/vouchers/create", _auth(type="dispatch", head={"a": "b"}, lines=[{"a": "b"}])),
    ("/v1/co/vouchers/delete", _auth(type="qm_product_reject", id=0)),
    ("/v1/co/vouchers/create", _auth(type="other_in", head={"a": {"b": 1}}, lines=[{"a": "b"}])),
    ("/v1/co/vouchers/create", _auth(type="other_in", head={"a": ["x"]}, lines=[{"a": "b"}])),
    ("/v1/co/vouchers/create", _auth(type="other_in", head={"a": "b"}, lines=[])),
    ("/v1/co/vouchers/create", _auth(type="sale_order", head={"a": "b"}, lines=[{"a": "b"}] * 201)),
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self._serve()

    def do_POST(self) -> None:
        self._serve()

    def _serve(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        server = self.server
        assert isinstance(server, _Box)
        server.hits.append((self.command, self.path, raw))
        payload = json.loads(raw) if raw else {}
        reply = bridge_body(self.path.removeprefix("/u8co"), payload)
        body = json.dumps(reply, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _fmt: str, *_args: object) -> None:
        return None


class _Box(ThreadingHTTPServer):
    def __init__(self) -> None:
        self.hits: list[tuple[str, str, bytes]] = []
        super().__init__(("127.0.0.1", 0), _Handler)


class _Up:
    def __init__(self) -> None:
        self.httpd = _Box()
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> _Up:
        self.thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def base_url(self) -> str:
        port = self.httpd.server_address[1]
        return f"http://127.0.0.1:{port}/u8co"


def _wired(base_url: str):
    client = _client()
    client.app.state.co_bridge = CoBridge(base_url, _HEX, timeout=5)
    return client


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_route_forwards_exact_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert method == "POST"
    assert seen == "/u8co" + bridge
    assert set(sent) == write_keys(path, keys)
    assert _SECRET.encode() not in raw
    assert b'"password"' not in raw


def test_create_forwards_scalars_and_tasks_keep_from() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        created = client.post(
            "/v1/co/vouchers/create",
            json=_auth(type="sale_order", head=_HEAD, lines=_LINES),
        )
        assert created.status_code == 200, created.text
        sent = json.loads(server.httpd.hits[-1][2])
        assert sent["head"] == _HEAD
        assert sent["lines"] == _LINES
        tasks = client.post("/v1/co/workflow/tasks", json=_auth())
        assert tasks.status_code == 200, tasks.text
        assert tasks.json()["tasks"][0]["from"] == "审批人乙"


def test_health_reaches_the_bridge() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        ok = client.get("/v1/co/health")
        assert ok.status_code == 200
        assert ok.json()["version"] == "test"
        assert server.httpd.hits[-1][0] == "GET"
        assert server.httpd.hits[-1][1] == "/u8co/v1/health"


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post(path, json=body)
    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


@pytest.mark.parametrize("kind", _KINDS)
def test_load_accepts_every_kind(kind: str) -> None:
    fake = FakeBridge()
    client = _client(fake)
    ok = client.post("/v1/co/vouchers/load", json=_auth(type=kind, id=2147483647))
    assert ok.status_code == 200, ok.text
    path, payload = fake.calls[0]
    assert path == "/v1/vouchers/load"
    assert set(payload) == _PLAIN | {"type", "id"}
    assert payload["type"] == kind
    assert payload["id"] == 2147483647


@pytest.mark.parametrize("kind", ("sale_order", "other_in", "other_out", "stock_opening"))
def test_create_and_delete_accept_three_kinds(kind: str) -> None:
    fake = FakeBridge()
    client = _client(fake)
    created = client.post("/v1/co/vouchers/create", json=_auth(type=kind, head={"a": "b"}, lines=[{"a": 1}]))
    assert created.status_code == 200, created.text
    deleted = client.post("/v1/co/vouchers/delete", json=_auth(type=kind, id=1))
    assert deleted.status_code == 200, deleted.text
    assert fake.calls[0][0] == "/v1/vouchers/create"
    assert fake.calls[1][0] == "/v1/vouchers/delete"


@pytest.mark.parametrize("kind", _QM)
def test_workflow_accepts_only_qm_kinds(kind: str) -> None:
    fake = FakeBridge()
    client = _client(fake)
    ok = client.post("/v1/co/workflow/submit", json=_auth(type=kind, id=1))
    assert ok.status_code == 200, ok.text
    assert fake.calls[0][0] == "/v1/workflow/submit"
    assert set(fake.calls[0][1]) == _PLAIN | {"type", "id", "caller"}
