"""/v1/co 路由和只给已认证调用方看的 OpenAPI 文档。"""

import copy
import inspect

from fastapi import Depends, FastAPI, Request

from u8co_api.auth import Caller, caller_key
from u8co_api.co_access import access_note, access_of
from u8co_api.co_call import COMPACT_QUERY, FIELDS_QUERY, View, call_route
from u8co_api.co_dryrun import OPENAPI_FLAG, supports as dry_run_supported
from u8co_api.co_dryrun import document as document_dry_run
from u8co_api.co_idem import idempotency_openapi
from u8co_api.co_meta import META_ACTION, register_meta  # 字段元数据
from u8co_api.co_models import CoHealthOut, ErrorBody
from u8co_api.co_routes_ai import AI_ROUTES  # 档案名称解析、幂等结果查询
from u8co_api.co_routes_arap_proc import ARAP_PROC_ROUTES  # 应收应付处理
from u8co_api.co_routes_arap_proc_list import ARAP_PROC_LIST_ROUTES  # 应收应付处理记录（事件源）
from u8co_api.co_routes_arap_voucher import ARAP_VOUCHER_ROUTES  # 应收 / 应付制单、取消制单
from u8co_api.co_routes_attach import ATTACH_ROUTES  # 凭证、单据附件列表
from u8co_api.co_routes_gl_transfer import GL_TRANSFER_ROUTES  # 期间损益结转、自定义转账（测试账套）
from u8co_api.co_routes_ic import IC_WRITE_ROUTES  # 按卖方单据生成买方单据
from u8co_api.co_routes_mgmt import MGMT_ROUTES  # 经营管理查询（多账套，经营管理权限）
from u8co_api.co_routes_notes_proc import NOTES_PROC_ROUTES  # 票据处理
from u8co_api.co_routes_notes_read import NOTES_READ_ROUTES  # 票据读取
from u8co_api.co_routes_notes_reg import NOTES_REG_ROUTES  # 应收票据登记、删除
from u8co_api.co_routes_openings import OPENING_ROUTES  # 采购期初记账；应收应付期初单据
from u8co_api.co_routes_gl_arc import GL_ARC_ROUTES
from u8co_api.co_routes_perm import PERM_ROUTES  # 有效权限（自己的、指定操作员的）
from u8co_api.co_routes_periods import IA_ROUTES, PERIOD_ROUTES  # 月末结账；存货核算记账、期末处理
from u8co_api.co_routes_lookup import LOOKUP_ROUTES  # 字段标签、单据查询、批量读取
from u8co_api.co_routes_reports import REPORT_ROUTES  # 只读报表
from u8co_api.co_service import ensure_co, run_health
from u8co_api.co_table import ROUTES, TAG_CO
from u8co_api.deps import current_caller as _caller

_ERROR_CODES = (400, 401, 403, 404, 409, 422, 429, 500, 502, 503, 504)
_ERRORS = {code: {"model": ErrorBody} for code in _ERROR_CODES}
_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "trace"}
ACCESS_KEY = "x-u8co-access"
# 全部 POST 路由（注册顺序即文档顺序）。GET 的 health、meta 另外注册。
POST_ROUTES = (
    ROUTES
    + GL_ARC_ROUTES
    + REPORT_ROUTES
    + ATTACH_ROUTES
    + NOTES_READ_ROUTES
    + NOTES_REG_ROUTES
    + NOTES_PROC_ROUTES
    + ARAP_VOUCHER_ROUTES
    + ARAP_PROC_ROUTES
    + ARAP_PROC_LIST_ROUTES
    + OPENING_ROUTES
    + PERIOD_ROUTES
    + IA_ROUTES
    + GL_TRANSFER_ROUTES
    + AI_ROUTES
    + LOOKUP_ROUTES
    + IC_WRITE_ROUTES
    + MGMT_ROUTES
    + PERM_ROUTES
)
_DESCRIPTION = """\
通过 HTTP 调用用友 U8 的业务组件（/v1/co）：单据、凭证、档案和报表的读写与审核。

## 认证与权限

- 每个请求带 `Authorization: Bearer <JWT>`。
- 请求体带账套、操作员编码和口令；口令只用于本次请求，服务不保存、不写入审计。
- 权限声明须为布尔值 true（字符串不算），也可按信任项配置的 scope 授予。

| 权限 | 可调用路径 |
|---|---|
| 读（缺省声明 `u8co_read`） | 只读路径：读取单据、单据列表、现存量、总账凭证读取和列表、档案读取和列表、报表、\
审批状态、审批历史、待办、登录校验、健康检查、档案名称解析、幂等结果查询、字段标签、单据查询、批量读取单据和档案 |
| 写（缺省声明 `u8co_write`） | 全部路径（经营管理路径另需下一行的权限） |
| 经营管理（信任项的 `mgmt_claim` 或 `mgmt_scope`） | `/v1/co/mgmt/*`，以及多账套汇总、合并报表、公司间对账\
（reports/aggregate、consolidation、intercompany_match）；读、写权限都不代替它 |
| 权限评估（信任项写了 `perm_evaluate: true`） | `/v1/co/perm/evaluate`（查别的操作员的权限）；令牌还要有读或写权限 |

- 只有读权限时调用写路径：403 `forbidden`，不会访问桥。
- 缺经营管理权限：403 `mgmt_forbidden`。
- 每个操作的说明末尾写明所需权限；操作上的 `x-u8co-access` 标为 read、write、mgmt 或 perm_evaluate。

## 账套

- {accounts}
- 不在名单里：403 `account_not_allowed`。
- 信任项配置了账套声明时，令牌还必须列出该账套。

## 错误体

`{"error":{"code","message","retryable","field","hint","detail"}}`

| 字段 | 说明 |
|---|---|
| code | 错误码，见下表 |
| message | 错误说明 |
| retryable | 总是有；为 true 时可以原样稍后重试 |
| field | 出错的请求字段路径，只用于 400，如 `lines.0.cinvcode` |
| hint | 简短的修正提示 |
| detail | 桥给的结构化补充，只用于 4xx，如存货核算记账 409 时的 `uncosted`、`uncosted_total` |

### 常见错误码

| 类别 | 错误码 |
|---|---|
| 请求与认证 | bad_request、unauthorized、forbidden、account_not_allowed、login_failed |
| 功能开关 | feature_disabled、test_account_only |
| 单据与业务 | not_found、state_mismatch、u8_rejected、stock_shortage（库存不足） |
| 审批流 | workflow_enabled、workflow_unknown、workflow_disabled（单据未启用审批流） |
| 审批动作 | already_submitted（已经提交）、not_submitted（未提交或没有在途实例）、\
not_current_approver（当前操作员不是待办人） |
| 繁忙与限流 | busy、rate_limited、busy_timeout、stopping |
| 桥与 U8 | unavailable、com_unavailable、u8_unavailable（U8 的服务没有运行，例如生产制造服务 U8MPool）、\
u8_license_full、ia_timeout |
| 结果不确定 | bad_response、outcome_unknown |

## 重试与 outcome_unknown

| 状态与错误码 | 含义 | 处理 |
|---|---|---|
| 504 `outcome_unknown` | 请求已经送出，或桥返回了无法解析的 2xx；审核等写入可能已经执行 | 先核对，不要盲目重试 |
| 502 `bad_response` | 桥的 3xx、4xx 或 5xx 不是可解析的 JSON，错误响应超过 64KiB，或发生了重定向 | — |
| 429 `busy` | 桥的队列满了 | 按 Retry-After（5 秒）重试 |
| 429 `rate_limited` | 本服务的并发或频率限制 | 稍后重试 |
| 503 `busy_timeout`、`stopping` | 桥排队超时，或正在停止 | 按 Retry-After（5 秒）重试 |
| 503 `u8_license_full` | U8 许可点数已满，桥已自己重试过 | 按 Retry-After（60 秒）重试 |
| 503 `ia_timeout` | 存货核算脚本超时，桥已回滚、没有写入 | 按 Retry-After（60 秒）重试 |

- 成功响应最多 8MiB，超过按无法解析的 2xx 处理（504 `outcome_unknown`）。
- 总账凭证新增、修改由 U8 自己提交，外层事务管不到：先用 `/v1/co/gl/vouchers/list` 或 load 核对，再决定是否重试。
- 档案的新增、修改、删除同理，先用 `/v1/co/archives/get` 核对。
- U8CO_ENABLED 关闭时这些路径返回 404；已打开但桥未配置时 503 `unavailable`。
- 健康检查失败一律 503 `unavailable`；健康检查不计入每个调用方的频率。

## 写入分级、预演、幂等

- **写入分级**：结账、存货核算、期初等第二级写入缺省关闭（403 `feature_disabled`），打开后只对测试账套开放\
（403 `test_account_only`），见 [期初记账](#tag/期初记账)、[月末结账](#tag/月末结账)、[存货核算](#tag/存货核算)。
- **预演**：写路由的请求体带 `dry_run: true`，照常校验但什么都不写入，见 [单据新增删除](#tag/单据新增删除)。
- **幂等**：写路径带 `Idempotency-Key` 头，重试原样返回第一次的结果，见 [U8 业务操作](#tag/u8-业务操作) 的幂等结果查询。

### 写入策略

写入策略（U8CO_WRITE_POLICY_FILE，桥上同一份文件）只管写路径，都在登录 U8 之前拒绝，没有写入。

| 状态与错误码 | 含义 | 可重试 |
|---|---|---|
| 503 `write_policy_unavailable` | 策略文件缺失或无效 | 按 Retry-After |
| 503 `write_frozen` | 已冻结 | 按 Retry-After |
| 503 `write_window` | 不在允许写入的时段 | 按 Retry-After |
| 403 `write_not_allowed` | 策略没有放行该账套的这类写入，detail 给出 type、op | 否 |
| 403 `operator_not_allowed` | 操作员不能在此账套写入 | 否 |
| 400 `write_limit` | 行数或金额超过上限，detail 给出 max、actual | 否 |
| 429 `write_quota`（桥） | 账套写入限额 | 是，Retry-After 按窗口算 |
| 503 `u8_license_hold`（桥） | 接口登录已达上限 | 是 |

### 预演

- 写路由的请求体可带 `dry_run: true`：照常登录、加锁、校验，成功返回 DryRunOut（mode 为 rollback 或 validate）。
- 不支持：旧路由 sale-orders/verify、dispatches/verify。
- arap/writeoff/auto 的 dry_run 仍是只算计划。
- 预演不能带 Idempotency-Key（arap/writeoff/auto 只算计划时同样不能带）。
- 支持预演的操作标了 `x-u8co-dry-run`。

### 幂等

- 适用于所有写路径：新增、修改、删除、审核、关闭、锁定、审批、总账凭证各操作、记账、期初记账、档案写入、核销、制单。
- 同一调用方、账套、路径和键只执行一次；重试原样返回第一次的结果，含 504 `outcome_unknown`。
- 4xx 不占用键，可以改正后再用。
- 同一个键换了请求内容：409 `idempotency_mismatch`。
- 桥上的幂等记录读写失败：503 `store_unavailable`。
- 用 `/v1/co/idempotency/get` 按路径和键查回当时的结果。

## 裁剪响应与名称解析

- 每条 POST 路径都收查询参数 `fields`（只返回列出的键）和 `compact`（去掉空值）。
- 只裁剪 head、lines、items、fields 和预演的 docs[].head、docs[].lines。
- 出参是带必填字段的类型化明细的路径（vouchers/close、核销、应收应付制单）不支持，返回 400。
- `/v1/co/archives/resolve` 把名称、简称、助记码解析成档案编码。
"""


def describe(accounts: tuple[str, ...]) -> str:
    if accounts:
        line = "允许的账套（环境变量 U8CO_ACCOUNTS）：" + "、".join(accounts) + "。"
    else:
        line = "账套白名单（环境变量 U8CO_ACCOUNTS）为空，所有业务调用都返回 403。"
    return _DESCRIPTION.replace("{accounts}", line)


def register_co_routes(app: FastAPI) -> None:
    for route in POST_ROUTES:
        _add_post(app, route)
    # 字段元数据（GET，不带账套和口令）。
    register_meta(app, _ERRORS, co_slot(META_ACTION))
    app.add_api_route(
        "/v1/co/health",
        co_health,
        methods=["GET"],
        response_model=CoHealthOut,
        responses=_ERRORS,
        summary="CO 桥健康检查",
        description="检查 CO 桥是否可达。\n\n**错误**\n- 桥不可达：503 `unavailable`。\n\n" + access_note("co:health"),
        tags=[TAG_CO],
        operation_id="coHealth",
        response_model_exclude_none=True,
        dependencies=[Depends(co_health_slot)],
        openapi_extra={ACCESS_KEY: access_of("co:health")},
    )
    app.add_api_route(
        "/v1/openapi.json",
        co_openapi,
        methods=["GET"],
        responses=_ERRORS,
        summary="CO 接口 OpenAPI",
        description="任意已认证调用方都可以读取，不要求读写权限。只包含 /v1/co。",
        tags=[TAG_CO],
        operation_id="coOpenApi",
        dependencies=[Depends(_caller)],
    )


def co_slot(action: str):
    # 每条路由一个依赖：先按读写分级检查权限，再占在途名额。
    # 调用方名额按「信任项:客户端」计：同一发行者下的多个机器客户端各算各的，互不挤占。
    # 令牌有 sub 时再按 sub 分开（auth.caller_key），同一客户端下的用户各算各的。
    def slot(request: Request, caller: Caller = Depends(_caller)):
        ensure_co(request, caller, action)
        state = request.app.state
        with state.co_global_limit.slot("co"):
            with state.co_limit.slot(caller_key(caller)):
                yield

    return slot


def co_health_slot(request: Request, caller: Caller = Depends(_caller)):
    # 健康检查占用全局在途名额，但不计入调用方每分钟次数。
    ensure_co(request, caller, "co:health")
    with request.app.state.co_global_limit.slot("co"):
        yield


def co_health(request: Request, caller: Caller = Depends(_caller)) -> CoHealthOut:
    return run_health(request, caller)


def co_openapi(request: Request) -> dict:
    return co_document(request.app)


def co_document(app: FastAPI) -> dict:
    document = copy.deepcopy(app.openapi())
    paths = {path: item for path, item in document.get("paths", {}).items() if path.startswith("/v1/co/")}
    _stamp_security(paths)
    document["paths"] = paths
    document["components"] = {
        "schemas": _keep_schemas(document, paths),
        "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}},
    }
    document["security"] = [{"bearerAuth": []}]
    document_dry_run(paths, document["components"]["schemas"])
    if not str(document.get("openapi", "")).startswith("3.1"):
        document["openapi"] = "3.1.0"
    info = document.setdefault("info", {})
    info["description"] = describe(app.state.settings.accounts)
    _strip_examples(document)
    return document


def _add_post(app: FastAPI, route) -> None:
    app.add_api_route(
        route.path,
        _endpoint(route),
        methods=["POST"],
        response_model=route.out_model,
        response_model_exclude_none=not route.keep_null,
        response_model_exclude_unset=route.keep_null,
        responses=_ERRORS,
        summary=route.summary,
        description=route.description + "\n\n" + access_note(route.action),
        tags=[route.tag],
        operation_id=route.operation_id,
        dependencies=[Depends(co_slot(route.action))],
        openapi_extra=_openapi_extra(route),
    )


def _openapi_extra(route) -> dict:
    # 读写分级（x-u8co-access）；支持预演的写路由标 x-u8co-dry-run（co_dryrun 据此改写 200 响应）；
    # 写路由在文档里补 Idempotency-Key 头（co_idem）。
    extra: dict = {ACCESS_KEY: access_of(route.action)}
    if dry_run_supported(route):
        extra[OPENAPI_FLAG] = True
    extra.update(idempotency_openapi(route.bridge_path) or {})
    return extra


def _endpoint(route):
    def endpoint(
        body,
        request: Request,
        fields: str | None = FIELDS_QUERY,
        compact: bool = COMPACT_QUERY,
        caller: Caller = Depends(_caller),
    ):
        return call_route(route, request, caller, body, View(fields, compact))

    endpoint.__name__ = route.operation_id
    _annotate_body(endpoint, route.body_model)
    return endpoint


def _annotate_body(endpoint, model) -> None:
    # 局部变量不能写进注解。注册前把 body 标成具体模型。
    signature = inspect.signature(endpoint)
    params = []
    for name, param in signature.parameters.items():
        if name == "body":
            param = param.replace(annotation=model)
        params.append(param)
    endpoint.__signature__ = signature.replace(parameters=params)
    endpoint.__annotations__["body"] = model
    endpoint.__annotations__.pop("return", None)


def _stamp_security(paths: dict) -> None:
    for item in paths.values():
        for method, operation in item.items():
            if method in _METHODS and isinstance(operation, dict):
                operation["security"] = [{"bearerAuth": []}]


def _keep_schemas(document: dict, paths: dict) -> dict:
    schemas = (document.get("components") or {}).get("schemas") or {}
    needed: set[str] = set()
    _names(paths, needed)
    seen: set[str] = set()
    while needed - seen:
        name = next(iter(needed - seen))
        seen.add(name)
        _names(schemas.get(name, {}), needed)
    return {name: schemas[name] for name in sorted(needed) if name in schemas}


def _names(node: object, found: set[str]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            found.add(ref.removeprefix("#/components/schemas/"))
        for value in node.values():
            _names(value, found)
    elif isinstance(node, list):
        for item in node:
            _names(item, found)


def _strip_examples(node: object) -> None:
    if isinstance(node, dict):
        _drop_password_key(node.get("examples"))
        _seal_password(node)
        for value in node.values():
            _strip_examples(value)
    elif isinstance(node, list):
        for item in node:
            _strip_examples(item)


def _drop_password_key(examples: object) -> None:
    if not isinstance(examples, list):
        return
    for item in examples:
        if isinstance(item, dict):
            item.pop("password", None)


def _seal_password(schema: dict) -> None:
    props = schema.get("properties")
    if not isinstance(props, dict):
        return
    secret = props.get("password")
    if not isinstance(secret, dict):
        return
    secret["writeOnly"] = True
    secret.pop("example", None)
    secret.pop("examples", None)
    secret.pop("default", None)
