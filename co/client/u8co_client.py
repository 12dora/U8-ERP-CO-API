"""Linux 调用 u8co。口令只留在内存里，签名覆盖原始请求体字节。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from co.client.u8co_errors import (
    U8CoAccountNotAllowed,
    U8CoAlreadySubmitted,
    U8CoBadRequest,
    U8CoBusy,
    U8CoBusyTimeout,
    U8CoComUnavailable,
    U8CoConnectFailed,
    U8CoError,
    U8CoInternal,
    U8CoLicenseFull,
    U8CoLoginFailed,
    U8CoNotCurrentApprover,
    U8CoNotFound,
    U8CoNotSubmitted,
    U8CoOutcomeUnknown,
    U8CoRejected,
    U8CoStateMismatch,
    U8CoStockShortage,
    U8CoStopping,
    U8CoTransport,
    U8CoUnauthorized,
    U8CoWorkflowDisabled,
    U8CoWorkflowEnabled,
    U8CoWorkflowUnknown,
    _RESPONSE_LIMIT,
    error_from,
    parse_payload,
)
from co.client.u8co_kinds import (
    CLOSABLE_KINDS,
    CREATABLE_KINDS,
    DELETABLE_KINDS,
    GENERATABLE_KINDS,
    KIND_NAMES,
    UPDATABLE_KINDS,
    VERIFIABLE_KINDS,
    WORKFLOW_KINDS,
    _ALL_KINDS,
    _CLOSABLE,
    _CREATABLE,
    _DELETABLE,
    _GENERATABLE,
    _UPDATABLE,
    _VERIFIABLE,
    _WORKFLOW,
    check_line_ids,
    check_source,
    require_verify,
)
from co.client.u8co_idem import key_fields, with_key  # 幂等键
from co.client.u8co_qm import qm_head  # 检验单表头的 items
from co.client.u8co_gl_arc import U8CoP4Mixin
from co.client.u8co_reports import U8CoReportsMixin  # 只读报表
from co.client.u8co_meta import U8CoMetaMixin  # 字段元数据
from co.client.u8co_lock import U8CoLockMixin  # 销售订单 / 采购订单锁定、解锁
from co.client.u8co_openings import U8CoOpeningsMixin  # 期初记账
from co.client.u8co_periods import U8CoPeriodsMixin  # 月末结账
from co.client.u8co_ia import U8CoIaMixin, is_long_call  # 存货核算记账、期末处理；长时操作的读超时
from co.client.u8co_rows import _create_rows, _gen_lines, _present_lines, _present_row, _row  # 表头、表体行的校验
from co.client.u8co_assist import U8CoR9Mixin, dry_fields  # 写预演、名称解析、幂等结果查询
from co.client.u8co_lookup import U8CoLookupMixin  # 字段标签、单据搜索、批量读取
from co.client.u8co_arap_proc import U8CoArapProcMixin  # 应收应付处理：转账、并账、红票对冲、汇兑损益、取消、制单
from co.client.u8co_gl_digest import U8CoGlDigestMixin  # 总账凭证摘要（事件源）
from co.client.u8co_gl_unpost import U8CoGlUnpostMixin  # 总账取消记账（测试账套）
from co.client.u8co_gl_reverse import U8CoGlReverseMixin  # 总账红字冲销
from co.client.u8co_gl_transfer import U8CoGlTransferMixin  # 期间损益结转、自定义转账（测试账套）
from co.client.u8co_reports_mgmt_gl import U8CoMgmtGlMixin  # 经营管理报表（总账口径）
from co.client.u8co_reports_mgmt_sa import U8CoMgmtSaMixin  # 经营管理报表（销售分析、往来账期）
from co.client.u8co_notes_read import U8CoNotesReadMixin  # 票据读取
from co.client.u8co_notes_reg import U8CoNotesRegMixin  # 应收票据登记、删除
from co.client.u8co_notes_proc import U8CoNotesProcMixin  # 票据处理
from co.client.u8co_arap_proc_list import U8CoArapProcListMixin  # 应收应付处理记录（事件源）
from co.client.u8co_arap_bad import U8CoArapBadMixin  # 应收坏账发生、收回、计提

# 与 Windows 桥约定的标签。改一个字节，两边的测试向量就会对不上。
_MAC_LABEL = b"u8co/v1/mac"
_ENC_LABEL = b"u8co/v1/enc"
_SECRET = re.compile(r"^[0-9a-f]{64}\Z")
_ROUTE = re.compile(r"^/v1/[a-z0-9][a-z0-9/_-]{0,80}\Z")


@dataclass(frozen=True)
class U8Call:
    """一次业务调用。参数收在这里，避免每个方法超过 5 个参数。"""

    acc: str
    year: str
    operator: str
    password: str = field(repr=False)
    date: str
    doc_id: int | None = None
    action: str = ""


@dataclass(frozen=True)
class VoucherDraft:
    """新建单据。head 是对象，lines 是 1 到 200 行。"""

    kind: str
    head: Any
    lines: Any


@dataclass(frozen=True)
class VoucherEdit:
    """修改单据。head 和 lines 至少有一项非空。"""

    kind: str
    head: Any
    lines: Any


@dataclass(frozen=True)
class VoucherGen:
    """参照生单。kind 是目标单据，来源 id 在 U8Call.doc_id。销售出库不带 lines。"""

    kind: str
    head: Any
    lines: Any
    source_type: str = ""


@dataclass(frozen=True)
class SignedRequest:
    method: str
    path: str
    body: bytes
    ts: str
    nonce: str


def derive_keys(secret_hex: str) -> tuple[bytes, bytes]:
    # 只接受小写，避免同一份密钥因大小写不同派生出两套结果。
    if _SECRET.fullmatch(secret_hex) is None:
        raise ValueError("共享密钥必须是 64 位小写十六进制")
    secret = bytes.fromhex(secret_hex)
    k_mac = hmac.new(secret, _MAC_LABEL, hashlib.sha256).digest()
    k_enc = hmac.new(secret, _ENC_LABEL, hashlib.sha256).digest()
    return k_mac, k_enc


def encrypt_password(k_enc: bytes, password: str, iv: bytes | None = None) -> str:
    if len(k_enc) != 32:
        raise ValueError("加密密钥必须是 32 字节")
    if iv is None:
        iv = os.urandom(16)
    if len(iv) != 16:
        raise ValueError("IV 必须是 16 字节")
    padder = padding.PKCS7(128).padder()
    padded = padder.update(password.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(k_enc), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(iv + ciphertext).decode("ascii")


def _base(call: U8Call) -> dict[str, Any]:
    return {
        "acc": call.acc,
        "year": call.year,
        "operator": call.operator,
        "password": call.password,
        "date": call.date,
    }


def _auth_fields(call: U8Call) -> dict[str, Any]:
    # 键顺序固定。基本正文是公共字段，然后 id，然后 action。
    fields = _base(call)
    if call.doc_id is not None:
        fields["id"] = call.doc_id
    if call.action:
        fields["action"] = call.action
    return fields


def _encode(fields: dict[str, Any], password_enc: str) -> bytes:
    payload: dict[str, Any] = {}
    for key, value in fields.items():
        if key == "password":
            payload["password_enc"] = password_enc
            continue
        payload[key] = value
    # 桥按原始字节验签名，分隔符不能带空格，也不能再排一次序。
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def body_bytes(call: U8Call, password_enc: str) -> bytes:
    # 键顺序固定，桥按原始字节验签名，不能再排一次序。
    return _encode(_auth_fields(call), password_enc)


def canonical(req: SignedRequest) -> bytes:
    digest = hashlib.sha256(req.body).hexdigest()
    text = "\n".join((req.method, req.path, req.ts, req.nonce, digest))
    return text.encode("ascii")


def sign(k_mac: bytes, req: SignedRequest) -> str:
    return hmac.new(k_mac, canonical(req), hashlib.sha256).hexdigest()


def auth_headers(k_mac: bytes, req: SignedRequest) -> dict[str, str]:
    return {
        "X-U8co-Ts": req.ts,
        "X-U8co-Nonce": req.nonce,
        "X-U8co-Sig": sign(k_mac, req),
    }


class U8CoClient(
    U8CoP4Mixin, U8CoReportsMixin, U8CoMetaMixin, U8CoLockMixin, U8CoOpeningsMixin, U8CoPeriodsMixin, U8CoIaMixin,
    U8CoR9Mixin, U8CoLookupMixin, U8CoArapProcMixin, U8CoGlDigestMixin, U8CoNotesReadMixin,
    U8CoArapProcListMixin, U8CoNotesRegMixin, U8CoNotesProcMixin, U8CoArapBadMixin, U8CoGlUnpostMixin,
    U8CoGlReverseMixin, U8CoMgmtGlMixin, U8CoMgmtSaMixin, U8CoGlTransferMixin,
):
    def __init__(self, base_url: str, secret_hex: str, timeout: float = 90, long_timeout: float = 1000) -> None:
        # long_timeout：存货核算记账、期末处理和经过存货核算的月末结账的超时（u8co_ia.is_long_call），不小于 timeout。
        parts = urllib.parse.urlsplit(base_url.rstrip("/"))
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("base_url 必须以 http:// 或 https:// 开头并带主机名")
        if parts.path != "/u8co" or parts.query or parts.fragment:
            raise ValueError("base_url 的路径必须是 /u8co，不能带查询串")
        self._parts = parts
        self._timeout = timeout
        self._long_timeout = max(long_timeout, timeout)
        self._k_mac, self._k_enc = derive_keys(secret_hex)

    def health(self) -> dict[str, Any]:
        return self._call("GET", "/v1/health", None, signed=False)

    def login_check(self, call: U8Call) -> dict[str, Any]:
        return self.call("/v1/login-check", _auth_fields(call))

    def verify_sale_order(self, call: U8Call) -> dict[str, Any]:
        require_verify(call.doc_id, call.action)
        return self.call("/v1/sale-orders/verify", _auth_fields(call))

    def verify_dispatch(self, call: U8Call) -> dict[str, Any]:
        require_verify(call.doc_id, call.action)
        return self.call("/v1/dispatches/verify", _auth_fields(call))

    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        # 各业务方法共用。password 换成 password_enc，其余键保持调用方的顺序。
        path = _route(route)
        if not isinstance(fields, dict):
            raise ValueError("fields 必须是字典")
        if self._idem_key is not None:
            fields = key_fields(path, fields, self._idem_key)  # client.keyed(key) 得到的客户端，写调用都带幂等键
        if self._dry:
            fields = dry_fields(path, fields)  # client.dry() 得到的客户端，写调用都带 dry_run
        timeout = self._long_timeout if is_long_call(path, fields) else self._timeout
        body = _encode(fields, encrypt_password(self._k_enc, _take_password(fields)))
        return self._call("POST", path, body, signed=True, timeout=timeout)

    def load_voucher(self, call: U8Call, kind: str) -> dict[str, Any]:
        return self.call("/v1/vouchers/load", _typed(call, kind, _ALL_KINDS))

    def verify_voucher(self, call: U8Call, kind: str) -> dict[str, Any]:
        require_verify(call.doc_id, call.action, kind)
        fields = _typed(call, kind, _VERIFIABLE)
        fields["action"] = call.action
        return self.call("/v1/vouchers/verify", fields)

    def create_voucher(self, call: U8Call, draft: VoucherDraft, idempotency_key: str | None = None) -> dict[str, Any]:
        _kind(draft.kind, _CREATABLE)
        fields = _base(call)
        fields["type"] = draft.kind
        fields["head"] = _row(draft.head)
        fields["lines"] = _create_rows(draft.kind, draft.lines)
        return self.call("/v1/vouchers/create", with_key(fields, idempotency_key))

    def delete_voucher(self, call: U8Call, kind: str) -> dict[str, Any]:
        return self.call("/v1/vouchers/delete", _typed(call, kind, _DELETABLE))

    def update_voucher(self, call: U8Call, draft: VoucherEdit) -> dict[str, Any]:
        fields = _typed(call, draft.kind, _UPDATABLE)
        head = qm_head(draft.kind, draft.head, _present_row)  # 检验单修改的 head 可带 items
        lines = _present_lines(draft.lines)
        if head is None and lines is None:
            raise ValueError("没有要修改的内容")
        if head is not None:
            fields["head"] = head
        if lines is not None:
            fields["lines"] = lines
        return self.call("/v1/vouchers/update", fields)

    def close_voucher(self, call: U8Call, kind: str, action: str, line_ids: object = None) -> dict[str, Any]:
        if action not in ("close", "open"):
            raise ValueError("action 必须是 close 或 open")
        fields = _typed(call, kind, _CLOSABLE)
        fields["action"] = action
        if line_ids is not None:
            fields["line_ids"] = check_line_ids(line_ids)
        return self.call("/v1/vouchers/close", fields)

    def generate_voucher(self, call: U8Call, draft: VoucherGen, idempotency_key: str | None = None) -> dict[str, Any]:
        fields = _typed(call, draft.kind, _GENERATABLE)
        if draft.source_type:
            fields["source_type"] = check_source(draft.kind, draft.source_type)
        head = qm_head(draft.kind, draft.head, _present_row)
        if head is not None:
            fields["head"] = head
        lines = _gen_lines(draft.kind, draft.lines, draft.source_type)
        if lines is not None:
            fields["lines"] = lines
        return self.call("/v1/vouchers/generate", with_key(fields, idempotency_key))

    def workflow_state(self, call: U8Call, kind: str) -> dict[str, Any]:
        return self.call("/v1/workflow/state", _typed(call, kind, _WORKFLOW))

    def workflow_history(self, call: U8Call, kind: str) -> dict[str, Any]:
        return self.call("/v1/workflow/history", _typed(call, kind, _WORKFLOW))

    def workflow_tasks(self, call: U8Call, kind: str = "") -> dict[str, Any]:
        fields = _base(call)
        if kind:
            _kind(kind, _ALL_KINDS)
            fields["type"] = kind
        return self.call("/v1/workflow/tasks", fields)

    def workflow_submit(self, call: U8Call, kind: str) -> dict[str, Any]:
        return self.call("/v1/workflow/submit", _typed(call, kind, _WORKFLOW))

    def workflow_withdraw(self, call: U8Call, kind: str) -> dict[str, Any]:
        return self.call("/v1/workflow/withdraw", _typed(call, kind, _WORKFLOW))

    def workflow_approve(self, call: U8Call, kind: str, opinion: str | None = None) -> dict[str, Any]:
        return self.call("/v1/workflow/approve", _wf_fields(call, kind, opinion, False))

    def workflow_disagree(self, call: U8Call, kind: str, opinion: str) -> dict[str, Any]:
        return self.call("/v1/workflow/disagree", _wf_fields(call, kind, opinion, True))

    def workflow_return(self, call: U8Call, kind: str, opinion: str) -> dict[str, Any]:
        return self.call("/v1/workflow/return", _wf_fields(call, kind, opinion, True))

    def workflow_abandon(self, call: U8Call, kind: str, opinion: str | None = None) -> dict[str, Any]:
        return self.call("/v1/workflow/abandon", _wf_fields(call, kind, opinion, False))

    def workflow_resubmit(self, call: U8Call, kind: str) -> dict[str, Any]:
        return self.call("/v1/workflow/resubmit", _typed(call, kind, _WORKFLOW))

    def _call(
        self, method: str, suffix: str, body: bytes | None, signed: bool, timeout: float | None = None
    ) -> dict[str, Any]:
        path = "/u8co" + suffix
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        if signed:
            stamp = str(int(time.time()))
            nonce = os.urandom(16).hex()
            signed_req = SignedRequest(method, path, body or b"", stamp, nonce)
            headers.update(auth_headers(self._k_mac, signed_req))
        status, raw = self._exchange(method, path, body, headers, timeout or self._timeout)
        return parse_payload(status, raw)

    def _exchange(
        self, method: str, path: str, body: bytes | None, headers: dict[str, str], timeout: float
    ) -> tuple[int, bytes]:
        # 先连上再发送。连接失败可以重试；发出去之后超时则结果未知。
        conn = self._connection(timeout)
        sent = False
        try:
            self._connect(conn)
            sent = True
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response.status, response.read(_RESPONSE_LIMIT + 1)
        except (http.client.HTTPException, TimeoutError, OSError) as exc:
            raise self._transport(sent, exc) from exc
        finally:
            conn.close()

    def _connection(self, timeout: float) -> http.client.HTTPConnection:
        # http.client 不走 http_proxy，也不会把 X-U8co-* 的大小写改掉。
        port = self._parts.port or (443 if self._parts.scheme == "https" else 80)
        host = self._parts.hostname or ""
        if self._parts.scheme == "https":
            return http.client.HTTPSConnection(host, port, timeout=timeout)
        return http.client.HTTPConnection(host, port, timeout=timeout)

    def _connect(self, conn: http.client.HTTPConnection) -> None:
        conn.connect()

    def _transport(self, sent: bool, _exc: Exception) -> U8CoError:
        if sent:
            return U8CoOutcomeUnknown(0, "outcome_unknown", "已送出请求但没有收到结果")
        return U8CoConnectFailed(0, "connect_failed", "连不上 u8co")


def _route(route: str) -> str:
    text = route.strip()
    if text.startswith("/u8co/"):
        text = text[len("/u8co") :]
    elif not text.startswith("/"):
        text = "/" + text
    if "//" in text or _ROUTE.fullmatch(text) is None:
        raise ValueError("route 必须是 /v1/ 下的路径")
    return text


def _take_password(fields: dict[str, Any]) -> str:
    password = fields.get("password")
    if type(password) is not str or password == "":
        raise ValueError("缺少操作员口令")
    if "password_enc" in fields:
        raise ValueError("不要自行传入 password_enc")
    return password


def _kind(kind: str, allowed: frozenset[str]) -> None:
    if kind in allowed:
        return
    if kind in _ALL_KINDS:
        raise ValueError("该单据类型不支持此操作")
    raise ValueError("未知的单据类型")


def _typed(call: U8Call, kind: str, allowed: frozenset[str]) -> dict[str, Any]:
    if call.doc_id is None or call.doc_id <= 0:
        raise ValueError("单据 id 必须是正整数")
    _kind(kind, allowed)
    fields = _base(call)
    fields["type"] = kind
    fields["id"] = call.doc_id
    return fields


def _opinion_text(opinion: str | None, required: bool) -> str:
    if opinion is None:
        if required:
            raise ValueError("需要填写审批意见")
        return ""
    if type(opinion) is not str:
        raise ValueError("审批意见必须是字符串")
    if len(opinion) > 500:
        raise ValueError("审批意见最长 500 字")
    if required and opinion == "":
        raise ValueError("需要填写审批意见")
    return opinion


def _wf_fields(call: U8Call, kind: str, opinion: str | None, required: bool) -> dict[str, Any]:
    fields = _typed(call, kind, _WORKFLOW)
    text = _opinion_text(opinion, required)
    if text:
        fields["opinion"] = text
    return fields


def main(argv: list[str] | None = None) -> int:
    # 命令行放在 u8co_cli.py。这里再导出，python3 -m co.client.u8co_client 保持不变。
    from co.client.u8co_cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
