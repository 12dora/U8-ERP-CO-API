"""调桥的 vouchers/list（以及确认空扫描时的 vouchers/load）。签名和口令加密沿用 co/client，不另写一套。

列表走桥的只读连接池，不登录 U8，但请求仍要带公共字段（账套、年度、操作员、口令、日期）。
vouchers/load 只在主键扫描（或水位倒退后的整轮读取）一张都没读到、快照里却有单据时逐张确认用（见 poller._confirm_empty）。
票据（notes 数据源的 ar_note / ap_note）同样走 vouchers/list，确认改走 notes/get（SQL 只读，不登录 U8）。
附加数据源（应收应付处理、基础档案、总账凭证）走 SourceLister 的四个方法，都是桥的只读 SQL 路由，不登录 U8。
「单据不存在」只认桥自己的错误体：确认用的读取路由（_PROOF_PATHS）、HTTP 404、
JSON 里显式写着 code=not_found、且不是「未知路径」。
没带 code 的 404（反向代理、别的网关）、未知路径的 404 都不算删除的证据（见 DocumentNotFound）。
操作员凭证文件是 JSON 对象：{"acc", "year", "operator", "password"}，权限必须是 0600。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from co.client.u8co_arap_proc_list import ProcDigestQuery, ProcListQuery
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_errors import U8CoNotFound
from co.client.u8co_gl_digest import GlDigestQuery
from co.client.u8co_gl_arc import ArcQuery, VoucherQuery

from u8co_events.config import NOTE_TYPES, Config, ConfigError, read_private, read_secret

_OPERATOR = re.compile(r"^[A-Za-z0-9_-]{1,20}\Z")
_YEAR = re.compile(r"^\d{4}\Z")


@dataclass(frozen=True)
class PageRequest:
    """一页列表请求。changed_since 为空表示不按水位筛（首轮回填或主键扫描）。"""

    type: str
    changed_since: str = ""
    after: int | None = None
    keys_only: bool = False
    limit: int = 500


class Lister(Protocol):
    def list_page(self, acc: str, request: PageRequest) -> dict[str, Any]:
        """返回桥的响应 {"ok", "type", "items", "next", "watermark"}；失败抛异常。"""
        ...

    def load(self, acc: str, type_: str, doc_id: int) -> dict[str, Any]:
        """读取一张单据，返回桥的成功响应；单据不存在抛 DocumentNotFound（见 is_not_found），其他失败也抛异常。"""
        ...


class SourceLister(Lister, Protocol):
    """附加数据源用的桥调用。fields 是路由自己的请求字段，公共字段（账套、年度、操作员、口令、日期）由实现补上。
    返回桥的成功响应（dict）；失败抛异常（U8CoError 等）。"""

    def list_process(self, acc: str, fields: Mapping[str, Any]) -> dict[str, Any]:
        """POST /v1/arap/process/list：应收应付处理行（按 Auto_ID 增量）或按批次的摘要。"""
        ...

    def list_archives(self, acc: str, fields: Mapping[str, Any]) -> dict[str, Any]:
        """POST /v1/archives/list：档案一页（changed_since / after / keys_only）。"""
        ...

    def list_gl_digest(self, acc: str, fields: Mapping[str, Any]) -> dict[str, Any]:
        """POST /v1/gl/vouchers/digest：总账凭证逐张的指纹摘要。"""
        ...

    def load_sql(self, acc: str, route: str, fields: Mapping[str, Any]) -> dict[str, Any]:
        """按键读一条（走 SQL 只读池，不登录 U8），给空扫描逐条确认用。route 必须在 _PROOF_PATHS 里（去掉 /u8co 前缀）；
        不存在抛 DocumentNotFound。"""
        ...


_LOAD_PATH = "/u8co/v1/vouchers/load"
# 能证明「不存在」的读取路由：只有这些路由的显式 not_found 才算删除的证据。附加数据源接入时在这里登记。
_PROOF_PATHS = frozenset((_LOAD_PATH, "/u8co/v1/archives/get", "/u8co/v1/gl/vouchers/load"))
# 票据（ar_note / ap_note）不是单据类型，vouchers/load 不收；空扫描确认走 notes/get（SQL 只读池，不登录 U8）。
_NOTE_PATH = "/u8co/v1/notes/get"
_PROOF_PATHS = _PROOF_PATHS | {_NOTE_PATH}
# 桥对没登记的路由也回 404 not_found，message 固定是这句（HttpServer / Dispatch）。
_UNKNOWN_ROUTE = "未知路径"
_ERROR_LIMIT = 65536


class DocumentNotFound(U8CoNotFound):
    """桥明确回答的「单据不存在」：确认用的读取路由（vouchers/load 等）的 HTTP 404，
    JSON 错误体里显式带 code=not_found，且不是未知路径。"""


def not_found_from(path: str, status: int, raw: bytes) -> DocumentNotFound | None:
    """按原始响应判断是不是桥明确说的单据不存在；不是就返回 None，交给客户端照常解析（照常抛错）。"""
    if path not in _PROOF_PATHS or status != 404 or len(raw) > _ERROR_LIMIT:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    # code 缺省时客户端会按状态码补成 not_found，这里只认错误体里自己写的。
    if not isinstance(payload, dict) or payload.get("code") != "not_found":
        return None
    message = payload.get("message")
    text = message.strip() if isinstance(message, str) else ""
    if text == _UNKNOWN_ROUTE:
        return None
    return DocumentNotFound(404, "not_found", text)


def is_not_found(exc: BaseException) -> bool:
    """只有 DocumentNotFound 才算单据不存在。按状态码推出来的 not_found、未知路径、403、503、连不上……都不算。"""
    return isinstance(exc, DocumentNotFound)


class ProofClient(U8CoClient):
    """同 U8CoClient，只是 vouchers/load 的 404 先看原始错误体：桥明确说单据不存在时抛 DocumentNotFound。"""

    def _exchange(
        self, method: str, path: str, body: bytes | None, headers: dict[str, str], timeout: float
    ) -> tuple[int, bytes]:
        status, raw = super()._exchange(method, path, body, headers, timeout)
        missing = not_found_from(path, status, raw)
        if missing is not None:
            raise missing
        return status, raw


@dataclass(frozen=True)
class Operator:
    acc: str
    year: str
    operator: str
    password: str = field(repr=False)


def load_operator(path: str, acc: str) -> Operator:
    try:
        data = json.loads(read_private(path, "操作员凭证"))
    except json.JSONDecodeError:
        raise ConfigError(f"操作员凭证文件 {path} 不是 JSON") from None
    if not isinstance(data, dict):
        raise ConfigError(f"操作员凭证文件 {path} 不是对象")
    if str(data.get("acc") or "") != acc:
        raise ConfigError(f"操作员凭证文件 {path} 的账套不是 {acc}")
    year = str(data.get("year") or "")
    code = str(data.get("operator") or "")
    password = data.get("password")
    if _YEAR.fullmatch(year) is None:
        raise ConfigError(f"操作员凭证文件 {path} 的年度必须是 4 位数字")
    if _OPERATOR.fullmatch(code) is None:
        raise ConfigError(f"操作员凭证文件 {path} 的操作员编码含不安全字符")
    if not isinstance(password, str) or password == "":
        raise ConfigError(f"操作员凭证文件 {path} 缺少口令")
    return Operator(acc, year, code, password)


def login_date(year: str, today: date) -> str:
    # 公共字段要一个日期。当年用今天，其他年度用该年最后一天，避免跨年度的日期被桥拒绝。
    if str(today.year) == year:
        return today.isoformat()
    return f"{year}-12-31"


class BridgeLister:
    def __init__(self, client: U8CoClient, operators: dict[str, Operator]) -> None:
        self._client = client
        self._operators = operators

    def list_page(self, acc: str, request: PageRequest) -> dict[str, Any]:
        call = self._call(acc)
        query = VoucherQuery(
            kind=request.type,
            keys_only=request.keys_only,
            changed_since=request.changed_since,
            after=request.after,
            limit=request.limit,
        )
        return self._client.list_vouchers(call, query)

    def load(self, acc: str, type_: str, doc_id: int) -> dict[str, Any]:
        if type_ in NOTE_TYPES:
            return self._client.call(_NOTE_PATH, self._note_fields(acc, type_, doc_id))
        return self._client.load_voucher(self._call(acc, doc_id), type_)

    def _note_fields(self, acc: str, type_: str, doc_id: int) -> dict[str, Any]:
        op = self._operators[acc]
        return {
            "acc": op.acc,
            "year": op.year,
            "operator": op.operator,
            "password": op.password,
            "date": login_date(op.year, date.today()),
            "type": type_,
            "id": doc_id,
        }

    # ---- 附加数据源（未接入的数据源先抛 NotImplementedError，轮询记进该类型的 last_error） ----

    def list_process(self, acc: str, fields: Mapping[str, Any]) -> dict[str, Any]:
        # digest=true 走摘要，其余字段原样进查询对象（不认识的字段直接 TypeError，是调用方的错）。
        values = dict(fields)
        if values.pop("digest", False):
            return self._client.arap_process_digest(self._call(acc), ProcDigestQuery(**values))
        return self._client.arap_process_list(self._call(acc), ProcListQuery(**values))

    def list_archives(self, acc: str, fields: Mapping[str, Any]) -> dict[str, Any]:
        # 字段按 ArcQuery 的名字给（archive、changed_since、after、limit、keys_only……），由客户端校验后发出。
        return self._client.arc_list(self._call(acc), ArcQuery(**fields))

    def list_gl_digest(self, acc: str, fields: Mapping[str, Any]) -> dict[str, Any]:
        # 字段按 GlDigestQuery 的名字给（fiscal_year、periods、closed_periods、after、limit、keys_only），
        # 由客户端校验后发出。
        return self._client.gl_digest(self._call(acc), GlDigestQuery(**fields))

    def load_sql(self, acc: str, route: str, fields: Mapping[str, Any]) -> dict[str, Any]:
        # 只发能证明「不存在」的读取路由；公共字段在前，路由字段在后（键顺序参与签名）。
        if "/u8co" + route not in _PROOF_PATHS:
            raise ValueError(f"{route} 不是确认用的读取路由")
        call = self._call(acc)
        body: dict[str, Any] = {
            "acc": call.acc,
            "year": call.year,
            "operator": call.operator,
            "password": call.password,
            "date": call.date,
        }
        body.update(fields)
        return self._client.call(route, body)

    def _call(self, acc: str, doc_id: int | None = None) -> U8Call:
        op = self._operators[acc]
        return U8Call(
            acc=op.acc,
            year=op.year,
            operator=op.operator,
            password=op.password,
            date=login_date(op.year, date.today()),
            doc_id=doc_id,
        )


def build_lister(cfg: Config) -> BridgeLister:
    secret = read_secret(cfg.bridge.secret_file)
    try:
        client = ProofClient(cfg.bridge.base_url, secret, timeout=cfg.bridge.timeout_seconds)
    except ValueError as exc:
        raise ConfigError(f"桥的地址或密钥不对：{exc}") from None
    operators = {account.acc: load_operator(account.operator_file, account.acc) for account in cfg.accounts}
    return BridgeLister(client, operators)
