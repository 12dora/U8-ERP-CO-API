"""u8co 客户端测试向量、请求头和错误码。只监听 127.0.0.1。"""

from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from co.client.u8co_client import (
    U8Call,
    U8CoAccountNotAllowed,
    U8CoBadRequest,
    U8CoBusy,
    U8CoBusyTimeout,
    U8CoComUnavailable,
    U8CoConnectFailed,
    U8CoError,
    U8CoInternal,
    U8CoOutcomeUnknown,
    U8CoStopping,
    U8CoLoginFailed,
    U8CoNotFound,
    U8CoRejected,
    U8CoAlreadySubmitted,
    U8CoNotCurrentApprover,
    U8CoNotSubmitted,
    U8CoStateMismatch,
    U8CoStockShortage,
    U8CoTransport,
    U8CoUnauthorized,
    U8CoWorkflowDisabled,
    U8CoWorkflowEnabled,
    U8CoWorkflowUnknown,
    SignedRequest,
    U8CoClient,
    VoucherDraft,
    auth_headers,
    body_bytes,
    derive_keys,
    encrypt_password,
    main,
    sign,
)

SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
K_MAC = "a85c2c38ccf25d1c837ec12b0a3707ee6aa584ed7834f8eac3302f81098b6407"
K_ENC = "3d9103ebd3f1448ce0dab668a4ce27c51a2ff0ac0b10c90cc5c300520e3411bc"
IV = bytes.fromhex("0f0e0d0c0b0a09080706050403020100")
PASSWORD = "测试Pass#01"
PASSWORD_ENC = "Dw4NDAsKCQgHBgUEAwIBAK86wvyLgq4AmzNMoENmeZU="
BODY = (
    '{"acc":"998","year":"2026","operator":"op001","password_enc":"'
    + PASSWORD_ENC
    + '","date":"2026-09-26","id":9000000003,"action":"verify"}'
)
BODY_SHA = "98c5d2571d4765701ddcfc1ade19102114b4c8658a0d4b4e3596ece98084c106"
TS = "1790434800"
NONCE = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
SIG = "dfc80957a80e21188add47a0bea3cfc917379139105a48337f91933a43b27a3a"
SALE_PATH = "/u8co/v1/sale-orders/verify"


def make_call(doc_id: int | None = None, action: str = "") -> U8Call:
    return U8Call("998", "2026", "op001", PASSWORD, "2026-09-26", doc_id, action)


def _assert_signed(case: unittest.TestCase, captured: _Capture) -> None:
    signed = SignedRequest(
        captured.method,
        captured.path,
        captured.body,
        _header(captured.headers, "X-U8co-Ts") or "",
        _header(captured.headers, "X-U8co-Nonce") or "",
    )
    case.assertEqual(_header(captured.headers, "X-U8co-Sig"), sign(bytes.fromhex(K_MAC), signed))
    case.assertNotIn(PASSWORD.encode("utf-8"), captured.body)


def _header(headers: dict[str, str], name: str) -> str | None:
    # http.server 不保证请求头大小写和客户端发出去的一致。
    folded = name.lower()
    for key, value in headers.items():
        if key.lower() == folded:
            return value
    return None


class _Capture:
    def __init__(self) -> None:
        self.method = ""
        self.path = ""
        self.headers: dict[str, str] = {}
        self.body = b""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self._serve()

    def do_POST(self) -> None:
        self._serve()

    def _serve(self) -> None:
        server = self.server
        assert isinstance(server, _Bridge)
        length = int(self.headers.get("Content-Length", "0"))
        server.capture.method = self.command
        server.capture.path = self.path
        server.capture.headers = {key: value for key, value in self.headers.items()}
        server.capture.body = self.rfile.read(length) if length else b""
        payload = server.payload
        self.send_response(server.reply_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        return


class _Bridge(ThreadingHTTPServer):
    def __init__(self, status: int, payload: bytes) -> None:
        self.capture = _Capture()
        self.reply_status = status
        self.payload = payload
        super().__init__(("127.0.0.1", 0), _Handler)


class _Running:
    def __init__(self, status: int, payload: bytes) -> None:
        self.httpd = _Bridge(status, payload)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> _Running:
        self.thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def base_url(self) -> str:
        port = self.httpd.server_address[1]
        return f"http://127.0.0.1:{port}/u8co"


def _vector_request() -> SignedRequest:
    return SignedRequest("POST", SALE_PATH, BODY.encode("ascii"), TS, NONCE)


class VectorTests(unittest.TestCase):
    def test_derive_keys(self) -> None:
        k_mac, k_enc = derive_keys(SECRET)
        self.assertEqual(k_mac.hex(), K_MAC)
        self.assertEqual(k_enc.hex(), K_ENC)

    def test_secret_must_be_lowercase_hex(self) -> None:
        with self.assertRaises(ValueError):
            derive_keys(SECRET.upper())

    def test_encrypt_password(self) -> None:
        k_enc = bytes.fromhex(K_ENC)
        self.assertEqual(encrypt_password(k_enc, PASSWORD, IV), PASSWORD_ENC)

    def test_random_iv_changes_ciphertext(self) -> None:
        k_enc = bytes.fromhex(K_ENC)
        self.assertNotEqual(encrypt_password(k_enc, PASSWORD), encrypt_password(k_enc, PASSWORD))

    def test_body_and_signature(self) -> None:
        call = U8Call("998", "2026", "op001", "不进正文", "2026-09-26", 9000000003, "verify")
        raw = body_bytes(call, PASSWORD_ENC)
        self.assertEqual(raw.decode("ascii"), BODY)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), BODY_SHA)
        self.assertEqual(sign(bytes.fromhex(K_MAC), _vector_request()), SIG)

    def test_auth_headers(self) -> None:
        headers = auth_headers(bytes.fromhex(K_MAC), _vector_request())
        self.assertEqual(headers["X-U8co-Ts"], TS)
        self.assertEqual(headers["X-U8co-Nonce"], NONCE)
        self.assertEqual(headers["X-U8co-Sig"], SIG)


class ClientTests(unittest.TestCase):
    def test_health_has_no_signature(self) -> None:
        with _Running(200, b'{"ok":true,"version":"1"}') as bridge:
            payload = U8CoClient(bridge.base_url, SECRET, timeout=5).health()
            self.assertEqual(payload["version"], "1")
            self.assertEqual(bridge.httpd.capture.method, "GET")
            self.assertEqual(bridge.httpd.capture.path, "/u8co/v1/health")
            self.assertIsNone(_header(bridge.httpd.capture.headers, "X-U8co-Sig"))

    def test_login_check_signs_raw_body(self) -> None:
        body = '{"ok":true,"operator":"op001","operator_name":"张三"}'.encode()
        with _Running(200, body) as bridge:
            call = U8Call("998", "2026", "op001", PASSWORD, "2026-09-26")
            payload = U8CoClient(bridge.base_url, SECRET, timeout=5).login_check(call)
            captured = bridge.httpd.capture
            self.assertEqual(payload["operator_name"], "张三")
            self.assertNotIn(PASSWORD.encode("utf-8"), captured.body)
            self.assertIn(b"password_enc", captured.body)
            self.assertNotIn(b'"id"', captured.body)
            signed = SignedRequest(
                captured.method,
                captured.path,
                captured.body,
                _header(captured.headers, "X-U8co-Ts") or "",
                _header(captured.headers, "X-U8co-Nonce") or "",
            )
            self.assertEqual(_header(captured.headers, "X-U8co-Sig"), sign(bytes.fromhex(K_MAC), signed))
            self.assertEqual(captured.path, "/u8co/v1/login-check")

    def test_sale_order_path(self) -> None:
        ok = b'{"ok":true,"acc":"998","id":3,"action":"verify","verified_by":"op001","verified_at":"t"}'
        with _Running(200, ok) as bridge:
            call = U8Call("998", "2026", "op001", PASSWORD, "2026-09-26", 3, "verify")
            U8CoClient(bridge.base_url, SECRET, timeout=5).verify_sale_order(call)
            self.assertEqual(bridge.httpd.capture.path, SALE_PATH)

    def _expect(self, status: int, code: str, kind: type[U8CoError]) -> None:
        raw = json.dumps({"ok": False, "code": code, "message": "说明"}, ensure_ascii=False).encode()
        with _Running(status, raw) as bridge:
            call = U8Call("998", "2026", "op001", PASSWORD, "2026-09-26")
            with self.assertRaises(kind) as caught:
                U8CoClient(bridge.base_url, SECRET, timeout=5).login_check(call)
        self.assertEqual(caught.exception.status, status)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.message, "说明")
        if kind is U8CoError:
            self.assertIs(type(caught.exception), U8CoError)

    def test_bad_request(self) -> None:
        self._expect(400, "bad_request", U8CoBadRequest)

    def test_unauthorized(self) -> None:
        self._expect(401, "unauthorized", U8CoUnauthorized)

    def test_account_not_allowed(self) -> None:
        self._expect(403, "account_not_allowed", U8CoAccountNotAllowed)

    def test_not_found(self) -> None:
        self._expect(404, "not_found", U8CoNotFound)

    def test_state_mismatch(self) -> None:
        self._expect(409, "state_mismatch", U8CoStateMismatch)

    def test_u8_rejected(self) -> None:
        self._expect(409, "u8_rejected", U8CoRejected)

    def test_workflow_enabled(self) -> None:
        self._expect(409, "workflow_enabled", U8CoWorkflowEnabled)

    def test_login_failed(self) -> None:
        self._expect(422, "login_failed", U8CoLoginFailed)

    def test_busy(self) -> None:
        self._expect(429, "busy", U8CoBusy)

    def test_internal(self) -> None:
        self._expect(500, "internal", U8CoInternal)

    def test_com_unavailable(self) -> None:
        self._expect(503, "com_unavailable", U8CoComUnavailable)

    def test_busy_timeout(self) -> None:
        self._expect(503, "busy_timeout", U8CoBusyTimeout)

    def test_stopping(self) -> None:
        self._expect(503, "stopping", U8CoStopping)

    def test_outcome_unknown_status(self) -> None:
        self._expect(504, "outcome_unknown", U8CoOutcomeUnknown)

    def test_workflow_unknown(self) -> None:
        self._expect(409, "workflow_unknown", U8CoWorkflowUnknown)

    def test_password_hidden_in_repr(self) -> None:
        call = U8Call("998", "2026", "op001", "secret-value", "2026-09-26")
        self.assertNotIn("secret-value", repr(call))

    def test_connect_failed(self) -> None:
        client = U8CoClient("http://127.0.0.1:1/u8co", SECRET, timeout=2)
        with self.assertRaises(U8CoConnectFailed) as caught:
            client.health()
        self.assertEqual(caught.exception.code, "connect_failed")

    def test_stall_is_outcome_unknown(self) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]

        def _hold() -> None:
            conn, _addr = sock.accept()
            time.sleep(2)
            conn.close()

        thread = threading.Thread(target=_hold, daemon=True)
        thread.start()
        try:
            client = U8CoClient(f"http://127.0.0.1:{port}/u8co", SECRET, timeout=0.4)
            with self.assertRaises(U8CoOutcomeUnknown) as caught:
                client.health()
            self.assertEqual(caught.exception.code, "outcome_unknown")
        finally:
            sock.close()

    def test_unknown_code_stays_base_error(self) -> None:
        self._expect(500, "other", U8CoError)

    def test_redirect_is_not_followed(self) -> None:
        with _Running(302, b"") as bridge:
            with self.assertRaises(U8CoTransport) as caught:
                U8CoClient(bridge.base_url, SECRET, timeout=5).health()
        self.assertEqual(caught.exception.code, "bad_response")

    def test_password_argument_is_refused(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            main(["health", "--base-url", "http://127.0.0.1/u8co", "--password", "secret-value"])
        self.assertNotIn("secret-value", str(caught.exception))
        self.assertIn("口令", str(caught.exception))

    def test_sale_order_body_keeps_legacy_order(self) -> None:
        ok = b'{"ok":true,"acc":"998","id":3,"action":"verify","verified_by":"op001","verified_at":"t"}'
        with _Running(200, ok) as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).verify_sale_order(make_call(3, "verify"))
            sent = json.loads(bridge.httpd.capture.body.decode("utf-8"))
            _assert_signed(self, bridge.httpd.capture)
        self.assertEqual(list(sent), ["acc", "year", "operator", "password_enc", "date", "id", "action"])
        self.assertEqual(sent["id"], 3)
        self.assertEqual(sent["action"], "verify")
        self.assertNotIn("password", sent)

    def test_generic_call_preserves_field_order(self) -> None:
        with _Running(200, b'{"ok":true,"type":"other_in","id":9,"code":"c"}') as bridge:
            fields = {
                "acc": "998",
                "year": "2026",
                "operator": "op001",
                "password": PASSWORD,
                "date": "2026-09-26",
                "type": "other_in",
                "id": 9,
            }
            payload = U8CoClient(bridge.base_url, SECRET, timeout=5).call("/u8co/v1/vouchers/load", fields)
            captured = bridge.httpd.capture
            sent = json.loads(captured.body.decode("utf-8"))
            _assert_signed(self, captured)
        self.assertEqual(payload["code"], "c")
        self.assertEqual(captured.path, "/u8co/v1/vouchers/load")
        self.assertEqual(list(sent), ["acc", "year", "operator", "password_enc", "date", "type", "id"])
        self.assertNotIn("password", sent)

    def test_load_and_submit_routes(self) -> None:
        with _Running(200, b'{"ok":true,"type":"qm_product_check","id":6117}') as bridge:
            client = U8CoClient(bridge.base_url, SECRET, timeout=5)
            client.load_voucher(make_call(6117), "qm_product_check")
            self.assertEqual(bridge.httpd.capture.path, "/u8co/v1/vouchers/load")
        with _Running(200, b'{"ok":true,"action":"submit"}') as bridge:
            client = U8CoClient(bridge.base_url, SECRET, timeout=5)
            client.workflow_submit(make_call(6117), "qm_product_check")
            sent = json.loads(bridge.httpd.capture.body.decode("utf-8"))
        self.assertEqual(bridge.httpd.capture.path, "/u8co/v1/workflow/submit")
        self.assertEqual(sent["type"], "qm_product_check")
        self.assertEqual(sent["id"], 6117)
        self.assertNotIn("opinion", sent)

    def test_create_voucher_and_optional_opinion(self) -> None:
        ok = b'{"ok":true,"type":"other_in","id":1,"code":"n"}'
        draft = VoucherDraft("other_in", {"cWhName": "成品仓"}, [{"cInvCode": "A", "iQuantity": 1}])
        with _Running(200, ok) as bridge:
            payload = U8CoClient(bridge.base_url, SECRET, timeout=5).create_voucher(make_call(), draft)
            raw = bridge.httpd.capture.body
            sent = json.loads(raw.decode("utf-8"))
        self.assertEqual(payload["code"], "n")
        self.assertEqual(bridge.httpd.capture.path, "/u8co/v1/vouchers/create")
        self.assertEqual(sent["head"]["cWhName"], "成品仓")
        self.assertEqual(sent["lines"][0]["iQuantity"], 1)
        self.assertIn("成品仓".encode("utf-8"), raw)
        self.assertNotIn("id", sent)
        with _Running(200, b'{"ok":true,"action":"approve"}') as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).workflow_approve(make_call(6117), "qm_product_check", "同意")
            approved = json.loads(bridge.httpd.capture.body.decode("utf-8"))
        self.assertEqual(bridge.httpd.capture.path, "/u8co/v1/workflow/approve")
        self.assertEqual(approved["opinion"], "同意")

    def test_tasks_omit_empty_type(self) -> None:
        with _Running(200, b'{"ok":true,"tasks":[]}') as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).workflow_tasks(make_call())
            sent = json.loads(bridge.httpd.capture.body.decode("utf-8"))
        self.assertEqual(bridge.httpd.capture.path, "/u8co/v1/workflow/tasks")
        self.assertNotIn("type", sent)

    def test_disagree_requires_opinion(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        with self.assertRaises(ValueError) as caught:
            client.workflow_disagree(make_call(6117), "qm_product_check", "")
        self.assertIn("审批意见", str(caught.exception))

    def test_not_submitted(self) -> None:
        self._expect(409, "not_submitted", U8CoNotSubmitted)

    def test_already_submitted(self) -> None:
        self._expect(409, "already_submitted", U8CoAlreadySubmitted)

    def test_not_current_approver(self) -> None:
        self._expect(409, "not_current_approver", U8CoNotCurrentApprover)

    def test_workflow_disabled(self) -> None:
        self._expect(409, "workflow_disabled", U8CoWorkflowDisabled)

    def test_stock_shortage(self) -> None:
        self._expect(409, "stock_shortage", U8CoStockShortage)


if __name__ == "__main__":
    unittest.main()
