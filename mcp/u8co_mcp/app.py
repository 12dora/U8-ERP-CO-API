"""工具的执行：注入 U8 凭据、调 /v1/co、把结果和错误变成 MCP 工具结果。

工具失败（HTTP 错误、网络错误、参数不对、配置问题）都是 isError 的工具结果，不是 JSON-RPC 错误。
错误结果：{"status": HTTP 状态（本地失败为 0）, "error": {"code", "message", "retryable", ...}}。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import time
import uuid

from u8co_mcp import describe as desc
from u8co_mcp import mgmt, person
from u8co_mcp.auth import TokenError, TokenProvider
from u8co_mcp.config import Config, ConfigError, read_password, read_secret_file
from u8co_mcp.http import Api, Response, Send, TransportError, make_transport, scrub
from u8co_mcp.schema import validate
from u8co_mcp.tools import GET_ROUTES, load_guide, load_routes, route_entry, tool_list

log = logging.getLogger("u8co_mcp")

FORBIDDEN_BODY_KEYS = ("acc", "operator", "password", "password_enc")
_KEY = re.compile(r"^[\x21-\x7e]{1,128}\Z")
_META_TTL_S = 60
_RETRYABLE_STATUS = (429, 502, 503)
_GATEWAY_STATUS = (502, 503, 504)
_SELECTORS = ("route", "type", "archive", "gl")
INCOMING_TOOLS = ("u8_guide",)
_CHECK_FIRST = "写入可能已生效：先用 u8_idempotency_get 或读取核对，不要直接重试"


class UnknownTool(Exception):
    """协议层按 -32602 回。"""


class ToolFailure(Exception):
    def __init__(self, payload: dict):
        super().__init__(payload.get("error", {}).get("code", "error"))
        self.payload = payload


def local_failure(code: str, message: str, retryable: bool = False, hint: str = "", field: str = "") -> ToolFailure:
    error = {"code": code, "message": message, "retryable": retryable}
    if field:
        error["field"] = field
    if hint:
        error["hint"] = hint
    return ToolFailure({"status": 0, "error": error})


def tool_result(obj: dict, is_error: bool = False) -> dict:
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return {"content": [{"type": "text", "text": text}], "structuredContent": obj, "isError": is_error}


class App:
    def __init__(self, config: Config | None, send: Send | None = None, config_error: str = "", **hooks):
        self.config = config
        self.config_error = config_error
        self.today = hooks.get("today", dt.date.today)
        self.new_key = hooks.get("new_key", lambda: str(uuid.uuid4()))
        self.clock = hooks.get("clock", time.monotonic)
        self.api = None
        self.tokens = None
        if config is not None and send is None:
            try:
                send = make_transport(config.ca_file)
            except ConfigError as exc:
                # ca_file 不可用：照常启动，工具返回 config_error。
                log.error("配置不可用：%s", exc)
                config, self.config, self.config_error = None, None, str(exc)
        if config is not None:
            self.tokens = TokenProvider(config.token, send, config.timeout_s, self.clock)
            self.api = Api(config.base_url, config.timeout_s, send, self.tokens, config.long_timeout_s)
            if config.http is not None:
                self.api.user_header = config.http.user_header
        self._secrets: set[str] = set()
        self._openapi: dict | None = None
        # (取到的时间, 令牌指纹, meta)。incoming 方式下各用户的令牌不同，缓存只给同一个令牌用。
        self._meta: tuple[float, str, dict] | None = None

    @property
    def read_only(self) -> bool:
        # 配置读不出来时按只读列工具：不暴露写工具。
        return self.config is None or self.config.read_only

    @property
    def incoming(self) -> bool:
        return self.tokens is not None and self.tokens.incoming

    def _base_tools(self) -> list[dict]:
        # incoming（HTTP 方式，多人共用）只提供指南和经营管理工具：u8 段的登录是共用的，不能按调用者的令牌
        # 让每个人都以它读写 U8；无论 read_only 怎么配都一样。
        tools = tool_list(self.read_only)
        return [t for t in tools if t["name"] in INCOMING_TOOLS] if self.incoming else tools

    def list_tools(self) -> list[dict]:
        return self._base_tools() + self._mgmt_tools()

    def _mgmt_tools(self, strict: bool = False) -> list[dict]:
        """配置启用了经营管理查询且当前令牌带对应声明时，返回 u8_mgmt_* 工具。

        列工具时（strict=False）拿不到令牌按没有处理；调用时（strict=True）照常抛出，结果里是 token_failed。"""
        cfg = self.config.mgmt if self.config is not None else None
        if cfg is None or not cfg.enabled or self.tokens is None:
            return []
        try:
            token = self.tokens.token()
        except (ConfigError, TokenError) as exc:
            if strict:
                raise
            log.warning("取令牌失败，不列经营管理工具：%s", self._clean(str(exc)))
            return []
        return mgmt.tool_list(cfg) if mgmt.allowed(token, cfg) else []

    def call(self, name: str, args: object) -> dict:
        try:
            tool = self._find_tool(name)
        except ToolFailure as failure:
            return tool_result(failure.payload, True)
        except (ConfigError, TokenError) as exc:
            return tool_result(self._local_error(exc).payload, True)
        args = {} if args is None else args
        problem = validate(args, tool["inputSchema"])
        if problem:
            return tool_result(local_failure("bad_arguments", problem).payload, True)
        try:
            return tool_result(self._dispatch(name, args))
        except ToolFailure as failure:
            return tool_result(failure.payload, True)
        except (ConfigError, TokenError) as exc:
            return tool_result(self._local_error(exc).payload, True)
        except Exception as exc:  # 兜底：不把异常细节（可能含请求内容）回给模型
            log.error("工具 %s 内部错误：%s", name, type(exc).__name__)
            return tool_result(local_failure("internal_error", "MCP 服务内部错误，详见 stderr 日志").payload, True)

    def _find_tool(self, name: str) -> dict:
        # 只在调经营管理工具时检查令牌声明，其余工具不取令牌。
        is_mgmt = name in mgmt.ROUTES
        tools = self._mgmt_tools(strict=True) if is_mgmt else self._base_tools()
        tool = next((t for t in tools if t["name"] == name), None)
        if tool is not None:
            return tool
        if self.incoming and not is_mgmt:
            raise UnknownTool(name)
        if name == "u8_write" or (is_mgmt and self.config is None):
            raise self._read_only_failure()
        if is_mgmt:
            raise _mgmt_unavailable()
        raise UnknownTool(name)

    def _local_error(self, exc: Exception) -> ToolFailure:
        code = "config_error" if isinstance(exc, ConfigError) else "token_failed"
        return local_failure(code, self._clean(str(exc)))

    def _read_only_failure(self) -> ToolFailure:
        if self.config is None:
            return local_failure("config_error", self.config_error or "没有可用的配置")
        return local_failure("read_only", "当前配置 read_only=true，不能调用写路由")

    def _dispatch(self, name: str, args: dict) -> dict:
        if name == "u8_guide":
            return {"guide": load_guide()}
        if name == "u8_describe" and not any(args.get(k) for k in _SELECTORS + ("op", "source")):
            return desc.catalog(list(load_routes()))
        if self.api is None:
            raise local_failure("config_error", self.config_error or "没有可用的配置")
        if name in mgmt.ROUTES:
            return self._mgmt(name, args)
        handler = {
            "u8_describe": self._describe,
            "u8_read": self._read,
            "u8_write": self._write,
            "u8_resolve": self._resolve,
            "u8_idempotency_get": self._idem_get,
        }[name]
        return handler(args)

    # ---- 各工具 ----

    def _read(self, args: dict) -> dict:
        route = args["route"]
        body = args.get("body")
        if route in GET_ROUTES:
            if body:
                raise local_failure("bad_arguments", f"{route} 是 GET，不带 body")
            return self._call(route)
        query = {}
        if args.get("fields"):
            query["fields"] = args["fields"]
        if args.get("compact", True):
            query["compact"] = "true"
        return self._call(route, self._body(body or {}), query=query)

    def _write(self, args: dict) -> dict:
        route = args["route"]
        body = dict(args["body"])
        dry_run = _dry_run(args, body)
        # 写路由都支持幂等键；正式写入没给键就自动生成，预演（含自动核销的计划模式）从不带键。
        key = args.get("idempotency_key")
        if key is not None and dry_run:
            raise local_failure("bad_arguments", "预演不能带 idempotency_key")
        if key is not None and not _KEY.match(key):
            raise local_failure("bad_arguments", "idempotency_key 必须是 1 到 128 个可见 ASCII 字符，不能含空格")
        if key is None and not dry_run:
            key = self.new_key()
        if dry_run:
            body["dry_run"] = True
        headers = {"Idempotency-Key": key} if key else None
        try:
            data = self._call(route, self._body(body), headers=headers, write=not dry_run)
        except ToolFailure as failure:
            if key:
                failure.payload["idempotency_key"] = key
            raise
        return dict(data, idempotency_key=key) if key else data

    def _resolve(self, args: dict) -> dict:
        body = {"items": args["items"]}
        for name in ("limit", "include_disabled"):
            if name in args:
                body[name] = args[name]
        return self._call("archives/resolve", self._body(body))

    def _idem_get(self, args: dict) -> dict:
        body = {"path": "/v1/co/" + args["route"], "key": args["key"]}
        return self._call("idempotency/get", self._body(body))

    def _mgmt(self, name: str, args: dict) -> dict:
        assert self.config is not None and self.config.mgmt is not None
        granted = mgmt.token_accounts(self.tokens.token(), self.config.mgmt) if self.tokens is not None else None
        if self.incoming or self.config.mgmt.person_proxy is not None:
            return self._mgmt_as_person(name, args, granted)
        try:
            route, body, passwords = mgmt.build_body(self.config.mgmt, name, args, self.today(), granted)
        except mgmt.MgmtArgsError as exc:
            raise local_failure("bad_arguments", str(exc), field=exc.field) from None
        self._secrets.update(passwords)
        return self._call(route, body)

    def _mgmt_as_person(self, name: str, args: dict, granted: frozenset[str] | None) -> dict:
        """incoming 方式：一次请求经身份绑定服务以调用者本人的 U8 操作员查询全部账套；多账套全有或全无，
        身份绑定服务任一账套不通过就整体失败，本服务不按账套拆分、不拼接结果。"""
        assert self.config is not None and self.config.mgmt is not None and self.api is not None
        proxy = self.config.mgmt.person_proxy
        if proxy is None or not self.incoming:
            # 配置解析已保证 incoming 必有 person_proxy；这里再兜底，绝不退回共用登录。
            raise local_failure("config_error", "经营管理查询没有配置身份绑定服务（mgmt.person_proxy）")
        try:
            route, accs, body = mgmt.person_request(self.config.mgmt, name, args, granted)
        except mgmt.MgmtArgsError as exc:
            raise local_failure("bad_arguments", str(exc), field=exc.field) from None
        service_token = read_secret_file(proxy.token_file, "身份绑定服务令牌")
        self._secrets.add(service_token)
        tokens = (service_token, self.tokens.token())
        try:
            return person.call(proxy, self.api.send, tokens, (accs, route, body))
        except person.ProxyFailure as failure:
            error = {k: self._clean(v) if isinstance(v, str) else v for k, v in failure.payload["error"].items()}
            raise ToolFailure({"status": failure.payload["status"], "error": dict(error, accounts=accs)}) from None

    def _describe(self, args: dict) -> dict:
        given = _describe_selector(args)
        if given == "route":
            entry = route_entry(args["route"])
            if entry is None:
                raise local_failure("bad_arguments", "没有这个路由，不带参数调用 u8_describe 看全部路由")
            return desc.describe_route(self._openapi_doc(), entry, entry["access"] == "write")
        meta = self._meta_doc()
        if given == "gl":
            return self._with_fields(desc.describe_gl(meta), {"gl": True})
        if given == "type":
            found = desc.describe_kind(meta, args["type"])
            what, pool = "单据类型", "kinds"
            ask = {k: args[k] for k in ("type", "op", "source") if args.get(k)}
        else:
            found = desc.describe_archive(meta, args["archive"])
            what, pool = "档案类型", "archives"
            ask = {"archive": args["archive"]}
        if found is None:
            raise local_failure("bad_arguments", f"没有这个{what}，可用：{'、'.join(desc.names(meta, pool))}")
        return self._with_fields(found, ask)

    def _with_fields(self, out: dict, ask: dict) -> dict:
        """并上 meta/fields（当前账套模板的字段中文名、类型、必填、枚举）。失败时带 fields_error，其余照常返回。"""
        try:
            fields = self._call("meta/fields", self._body(ask))
        except ToolFailure as failure:
            return dict(out, fields_error=failure.payload)
        except (ConfigError, TokenError) as exc:  # 例如没有 U8 口令：meta 不需要登录，fields 需要
            return dict(out, fields_error=self._local_error(exc).payload)
        out = dict(out, fields={k: v for k, v in fields.items() if k != "ok"})
        return desc.fit_fields(out)

    # ---- 公共 ----

    def _body(self, body: dict) -> dict:
        bad = sorted(k for k in body if str(k).lower() in FORBIDDEN_BODY_KEYS)
        if bad:
            raise local_failure("bad_arguments", f"body 不能带 {', '.join(bad)}：账套、操作员和口令由本服务注入")
        assert self.config is not None and self.config.u8 is not None
        u8 = self.config.u8
        password = read_password(u8)
        self._secrets.add(password)
        out: dict = {"date": self.today().isoformat()}
        if u8.year:
            out["year"] = u8.year
        out.update(body)
        out.update({"acc": u8.acc, "operator": u8.operator, "password": password})
        return out

    def _call(self, route: str, body: dict | None = None, query=None, headers=None, write: bool = False) -> dict:
        method = "GET" if route in GET_ROUTES else "POST"
        assert self.api is not None
        try:
            resp = self.api.request(method, "/v1/co/" + route, body, query, headers)
        except TransportError as exc:
            raise self._transport_failure(exc, write) from None
        data = resp.json()
        if 200 <= resp.status < 300:
            if isinstance(data, dict):
                return data
            if write:
                raise _http_failure(resp, _unknown("服务返回了无法解析的 2xx 响应"))
            error = {"code": "bad_response", "message": "服务返回了无法解析的 2xx 响应", "retryable": False}
            raise _http_failure(resp, error)
        error = _api_error(resp, data, write)
        # 纵深防御：接口错误文本里如果带出了口令或令牌，也换成 ***。
        raise _http_failure(resp, {k: self._clean(v) if isinstance(v, str) else v for k, v in error.items()})

    def _transport_failure(self, exc: TransportError, write: bool) -> ToolFailure:
        message = self._clean(str(exc))
        if write and exc.sent:
            return ToolFailure({"status": 0, "error": _unknown(f"请求可能已送达但没有收到响应（{message}）")})
        return local_failure("unavailable", f"连不上 U8 CO 接口（{message}）", retryable=True)

    def _clean(self, text: str) -> str:
        secrets = set(self._secrets)
        if self.tokens is not None:
            secrets |= self.tokens.secrets()
        return scrub(text, secrets)

    def _openapi_doc(self) -> dict:
        if self._openapi is None:
            assert self.api is not None
            try:
                resp = self.api.request("GET", "/v1/openapi.json")
            except TransportError as exc:
                raise self._transport_failure(exc, False) from None
            data = resp.json()
            if resp.status != 200 or not isinstance(data, dict):
                raise _http_failure(resp, _api_error(resp, data))
            self._openapi = data
        return self._openapi

    def _meta_doc(self) -> dict:
        now = self.clock()
        owner = self._token_mark()
        if self._meta is not None and now - self._meta[0] < _META_TTL_S and self._meta[1] == owner:
            return self._meta[2]
        meta = self._call("meta")
        self._meta = (now, owner, meta)
        return meta

    def _token_mark(self) -> str:
        if self.tokens is None or not self.tokens.incoming:
            return ""
        return hashlib.sha256(self.tokens.token().encode("utf-8")).hexdigest()


def _api_error(resp: Response, data: object, write: bool = False) -> dict:
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error
    if write and resp.status in _GATEWAY_STATUS:
        # 反向代理的错误页可能掩盖了已经完成的写入。
        return _unknown(f"HTTP {resp.status}，响应不是接口的错误格式")
    retryable = resp.status in _RETRYABLE_STATUS
    return {"code": "bad_response", "message": f"HTTP {resp.status}，响应不是接口的错误格式", "retryable": retryable}


def _mgmt_unavailable() -> ToolFailure:
    return local_failure(
        "mgmt_forbidden",
        "无权查询经营管理数据：配置里没有启用 mgmt，或令牌没有经营管理权限",
        hint="在 MCP 配置里加 mgmt 段，并让身份提供方给本客户端的令牌加上 mgmt.claim 声明或 mgmt.scope",
    )


def _describe_selector(args: dict) -> str:
    """route / type / archive / gl 只能给一个；type=gl 等同 gl=true。op、source 只配合 type。"""
    if args.get("type") == "gl":
        args = dict(args, type=None, gl=True)
    given = [k for k in _SELECTORS if args.get(k)]
    if len(given) > 1:
        raise local_failure("bad_arguments", "route、type、archive、gl 一次只给一个")
    extra = [k for k in ("op", "source") if args.get(k)]
    if extra and given != ["type"]:
        raise local_failure("bad_arguments", "op、source 只能和 type 一起用", field=extra[0])
    if (args.get("op") == "generate") != bool(args.get("source")):
        message = "source 只和 op=generate 一起用，且 op=generate 必须给 source"
        raise local_failure("bad_arguments", message, field="source")
    return given[0]


def _unknown(message: str) -> dict:
    return {"code": "outcome_unknown", "message": message, "retryable": False, "hint": _CHECK_FIRST}


def _dry_run(args: dict, body: dict) -> bool:
    """参数 dry_run 和 body 里的 dry_run 等价（arap/writeoff/auto 的计划模式也走这里），都必须是 JSON 布尔值。
    取出 body 里的 dry_run；两处都给了但不一致时拒绝，免得把预演当成正式写入。"""
    arg = args.get("dry_run")
    if "dry_run" not in body:
        return arg is True
    value = body.pop("dry_run")
    if not isinstance(value, bool):
        raise local_failure("bad_arguments", "body.dry_run 必须是 true 或 false", field="body.dry_run")
    if arg is not None and arg != value:
        raise local_failure("bad_arguments", "dry_run 参数和 body.dry_run 不一致", field="body.dry_run")
    return value


def _http_failure(resp: Response, error: dict) -> ToolFailure:
    payload: dict = {"status": resp.status, "error": error}
    retry_after = resp.headers.get("retry-after", "")
    if retry_after.isdigit():
        payload["retry_after"] = int(retry_after)
    return ToolFailure(payload)
