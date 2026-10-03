"""「单据不存在」的判定：只认桥错误体里显式的 code=not_found。不连网络。"""

from __future__ import annotations

import json

import pytest
from co.client.u8co_client import U8CoClient
from co.client.u8co_errors import U8CoError, U8CoNotFound
from u8co_events.bridge import DocumentNotFound, ProofClient, is_not_found, not_found_from

_LOAD = "/u8co/v1/vouchers/load"


def _body(**fields) -> bytes:
    return json.dumps({"ok": False, **fields}, ensure_ascii=False).encode("utf-8")


def test_explicit_not_found_is_proof() -> None:
    got = not_found_from(_LOAD, 404, _body(code="not_found", message="单据不存在"))
    assert isinstance(got, DocumentNotFound)
    assert (got.status, got.code, got.message) == (404, "not_found", "单据不存在")
    assert is_not_found(got)


@pytest.mark.parametrize(
    ("path", "status", "raw"),
    [
        (_LOAD, 404, _body(message="单据不存在")),  # 没带 code：客户端会按状态码补成 not_found，不算
        (_LOAD, 404, _body(code="", message="x")),
        (_LOAD, 404, _body(code="not_found", message="未知路径")),  # 路由没登记
        (_LOAD, 404, _body(code="not_found", message=" 未知路径 ")),
        (_LOAD, 404, b"<html>Not Found</html>"),  # 反向代理的 HTML
        (_LOAD, 404, b"[]"),
        (_LOAD, 403, _body(code="not_found")),
        (_LOAD, 200, b'{"ok": true}'),
        ("/u8co/v1/vouchers/list", 404, _body(code="not_found", message="单据不存在")),
        (_LOAD, 404, _body(code="not_found", message="x" * 70000)),
    ],
)
def test_other_answers_are_not_proof(path: str, status: int, raw: bytes) -> None:
    assert not_found_from(path, status, raw) is None


def test_status_derived_not_found_is_not_proof() -> None:
    assert not is_not_found(U8CoNotFound(404, "not_found", "单据不存在"))
    assert not is_not_found(U8CoError(404, "not_found", ""))
    assert not is_not_found(U8CoError(403, "no_permission", "没有权限"))
    assert not is_not_found(ConnectionError("断开"))


def _client(monkeypatch, status: int, raw: bytes) -> ProofClient:
    def fake_exchange(self, method, path, body, *_rest):
        return status, raw

    monkeypatch.setattr(U8CoClient, "_exchange", fake_exchange)
    return ProofClient("http://192.0.2.10:8765/u8co", "ab" * 32, timeout=1)


def test_proof_client_raises_document_not_found(monkeypatch) -> None:
    client = _client(monkeypatch, 404, _body(code="not_found", message="单据不存在"))
    with pytest.raises(DocumentNotFound):
        client._exchange("POST", _LOAD, b"{}", {}, 5.0)


def test_proof_client_passes_other_404_through(monkeypatch) -> None:
    raw = _body(message="单据不存在")
    client = _client(monkeypatch, 404, raw)
    assert client._exchange("POST", _LOAD, b"{}", {}, 5.0) == (404, raw)
