# MCP 服务

`mcp/` 是一个 MCP（Model Context Protocol）服务 `u8co-mcp`：AI 客户端（Claude Code、Claude Desktop、Cursor 等）把它作为子进程启动，经标准输入输出说 MCP；它再经 HTTPS 调 API 服务的 `/v1/co/*`。AI 代理因此能用几个通用工具读写 U8，而不用自己拼 HTTP 请求、管令牌和口令。

```
AI 客户端 --stdio (MCP)--> u8co-mcp（本机） --HTTPS + Bearer--> API 服务 --> 桥 --> U8
```

- 只用 Python 标准库（3.10 及以上），没有第三方依赖。
- 不直接连桥，不需要桥的共享密钥；API 服务的全部检查（令牌读写分级、账套白名单、限流、审计）照常生效。
- 账套、操作员、口令来自本机配置，注入每个请求；AI 代理看不到口令，也不能换成别的操作员。

## 1. 安装

在仓库根目录：

```bash
uv tool install ./mcp          # 或 pipx install ./mcp
which u8co-mcp
```

装好后命令是 `u8co-mcp`。不想安装时也可以 `python3 -m u8co_mcp`（在 `mcp/` 目录下或把它加进 `PYTHONPATH`）。

## 2. 配置

1. 在身份提供方为 MCP 单独登记一个 client credentials 客户端（例如 `mcp-client`），在 API 的信任配置里给它需要的权限：只查询就只给读权限（`configuration.md`「OIDC 信任配置」）。
2. 在 U8 里给它用的操作员最小的功能权限和数据权限。
3. 写本机配置和密钥文件：

```bash
mkdir -p ~/.config/u8co
cp mcp/mcp.example.json ~/.config/u8co/mcp.json      # 改 base_url、token、u8
printf '%s' '<客户端密钥>' > ~/.config/u8co/mcp-client-secret
printf '%s' '<U8 口令>'   > ~/.config/u8co/mcp-u8-password
chmod 600 ~/.config/u8co/mcp-client-secret ~/.config/u8co/mcp-u8-password
```

键的含义、令牌的三种来源（`client_credentials`、`file`、`env`）和权限要求见 [配置参考](configuration.md) 的 MCP 一节。口令也可以放在环境变量 `U8CO_MCP_PASSWORD` 里。只读场景在配置里写 `"read_only": true`，写工具就不出现。存货核算记账、期末处理和经过存货核算的月末结账要跑几分钟，按 `long_timeout_s`（缺省 1000 秒）等，见配置参考。

4. 自检：

```bash
u8co-mcp --check
```

它读配置、取令牌、调一次 `GET /v1/co/health`，结果写到标准错误。配置有错时服务照样能启动（客户端里每次工具调用都返回 `config_error`，便于看到原因），所以接入客户端之前先用 `--check` 确认。`u8co-mcp --version` 打印版本。

## 3. 接入 AI 客户端

各客户端的配置都是「启动哪个命令、带哪些环境变量」。下面假定 `u8co-mcp` 在 `PATH` 上，配置文件在缺省位置；不在缺省位置时加环境变量 `U8CO_MCP_CONFIG`。

### Claude Code

```bash
claude mcp add u8co --scope user -- u8co-mcp
# 配置文件不在缺省位置时：
claude mcp add u8co --scope user --env U8CO_MCP_CONFIG=$HOME/.config/u8co/mcp.json -- u8co-mcp
```

`--scope user` 对本机所有项目生效；只给一个项目用时换成 `--scope project`（写进项目的 `.mcp.json`，里面不要放口令）。`claude mcp list` 查看是否连上。

### Claude Desktop

编辑 `claude_desktop_config.json`（macOS 在 `~/Library/Application Support/Claude/`，Windows 在 `%APPDATA%\Claude\`），然后重启 Claude Desktop：

```json
{"mcpServers": {
  "u8co": {"command": "u8co-mcp", "env": {"U8CO_MCP_CONFIG": "~/.config/u8co/mcp.json"}}
}}
```

图形界面启动的程序不一定继承终端的 `PATH`，`command` 找不到时写 `u8co-mcp` 的绝对路径（`which u8co-mcp`）。

### Cursor

在 `~/.cursor/mcp.json`（全局）或项目里的 `.cursor/mcp.json` 写同样的结构：

```json
{"mcpServers": {"u8co": {"command": "u8co-mcp"}}}
```

其他支持 stdio MCP 的客户端照此配置：命令 `u8co-mcp`，没有参数。

### 托管（HTTP 方式，多人共用）

AI 平台在服务端替多个用户调工具时，不在各人电脑上起子进程，而是把 `u8co-mcp` 部署成一个 HTTP 服务：

```
AI 平台 --HTTP (MCP Streamable HTTP) + 用户的 Bearer--> u8co-mcp（托管）
        --HTTPS + 服务令牌 + X-U8co-Caller-Token: 用户的 Bearer--> 身份绑定服务（你自己运行）
        --HTTPS + 用户的 Bearer + 该用户本人绑定的 U8 登录--> API 服务
```

```bash
u8co-mcp --http --host 0.0.0.0 --port 8095       # 配置里 token 为 {"type": "incoming"}
```

- 协议：`POST /mcp`（`http.path`），一条 JSON-RPC 消息（或批量）一个请求；含请求时回 `200 application/json`，只有通知时回 HTTP 202 Accepted。无状态，不发 `Mcp-Session-Id`，不开 SSE 流（`GET` / `DELETE /mcp` 回 405）。`GET /healthz` 不要令牌，给健康检查用。
- 令牌和工具：`token.type` 必须是 `incoming`，且配置 `read_only: true`，否则不启动（退出码 2）。只提供 `u8_guide` 和（令牌有经营管理权限时的）`u8_mgmt_*`；`u8_read`、`u8_describe`、`u8_resolve`、`u8_write`、`u8_idempotency_get` 用的是 `u8` 段那个共用的 U8 登录，不能让每个用户都借它读写，所以无论怎么配都不列出，调用回 `-32602`（`u8` 段可以省略）。每个请求的 `Authorization: Bearer` 原样放在 `X-U8co-Caller-Token` 头里转给身份绑定服务，本服务不持有客户端密钥，也不持有任何 U8 口令；没有令牌回 401，JWT 已过期回 401（`invalid_token`）。工具列表、经营管理门控都按本次请求的令牌算，所以同一个服务对不同用户列出的工具可以不同。
- 连接：HTTP/1.1 长连接。凡不是 2xx 的响应都带 `Connection: close` 并关闭连接（请求体可能没读，不能复用）；不接受 `Transfer-Encoding`（分块上传），回 411 并关闭，请求体必须带 `Content-Length`，上限 256 KiB（超过回 413）。同时最多处理 32 个连接，超出时回 503（`Retry-After: 1`）；套接字读写 30 秒超时，空闲长连接 30 秒后关闭。收到 `SIGTERM` 后停止接受新连接，等在途请求最多 25 秒再退出（Compose 示例里 `init: true`、`stop_grace_period: 30s`）。
- 经营管理的缺省账套：工具没给 `accounts` 时，取配置的账套与令牌账套声明（`mgmt.accounts_claim`，缺省 `u8co_accs`）的交集；令牌没有这个声明时取配置的全部账套，由身份绑定服务和 API 判断。
- 经营管理按人查询：托管方式下不能配置共用的 U8 登录（`mgmt.accounts` 里有 `operator` 或 `password_file` 时不启动），必须配置 `mgmt.person_proxy`。每次工具调用只发一次请求给身份绑定服务（多账套也一次，带全部账套），由它验证调用者令牌、找到调用者本人在各账套绑定的 U8 操作员，再以这些操作员的登录一次调 API（多账套合并照常在 API 里做），结果只含这些操作员的功能权限和数据权限允许的数据。多账套全有或全无：任一账套没有绑定或无权，整个调用失败；API 部分账套失败（`complete` 为 `false`）时本服务也按失败处理（`incomplete`），不返回已取到的账套数据。工具列表仍按令牌的经营管理声明决定（只影响显示），能不能查以身份绑定服务和 API 的判断为准。
- 审计：令牌 `sub` 是 UUID 时，调 API 另带 `X-U8co-User`（`http.user_header`）。这个头只记审计，不是授权；授权仍看令牌本身的声明。经营管理查询的审计由身份绑定服务和 API 记录（调用者、账套、路由、结果），本服务的日志不写令牌和请求体。
- 平台一侧：每个用户各自授权（按用户的 OAuth 连接），令牌由身份提供方按该用户的权限签发；不要用一个共享令牌加用户头冒充多人。
- 部署：`mcp/Dockerfile`（只用标准库，构建不联网）、`mcp/compose.example.yml`、`mcp/mcp.http.example.json`。对外放在 TLS 反向代理后面；只给服务端调用时 `http.allowed_origins` 留空，带 `Origin` 头的请求一律拒绝。键的说明见 [配置参考](configuration.md) 的「HTTP 方式」。

#### 身份绑定服务

`mgmt.person_proxy` 指向的服务由部署方自行实现和运行，本服务只按下面的协议调用它。每次工具调用一次请求，单账套写 `acc`，多账套写 `accs`（二者恰好一个）：

```
POST {person_proxy.url}
Authorization: Bearer <person_proxy.token_file 里的服务令牌>
X-U8co-Caller-Token: <调用者本次请求的 Bearer 令牌，原样>
Content-Type: application/json; charset=utf-8

{"acc": "801", "route": "reports/mgmt/pnl", "body": {"fiscal_year": 2026, "period_from": 1, "period_to": 3}}
{"accs": ["801", "802"], "route": "reports/mgmt/pnl", "body": {"fiscal_year": 2026, "period_from": 1, "period_to": 3}}
```

- `route` 取 `reports/mgmt/overview`、`reports/mgmt/pnl`、`reports/mgmt/sales`、`reports/mgmt/arap_terms`、`reports/mgmt/cash_stock`，依次对应 API 的 `mgmt/overview`、`mgmt/pnl`、`mgmt/sales`、`mgmt/arap`、`mgmt/cash_stock`。`body` 是工具参数去掉 `accounts` 后的 API 请求体（`consolidate` 有就照传），从不含 `logins`、`acc`、`accs`、`operator`、`password`（服务端见到这些键应回 400 `forbidden_field`）。
- 服务端应当：用服务令牌认证本服务（常量时间比较）；验证调用者令牌的签名、签发方、受众和有效期，并检查经营管理声明和账套声明含每个账套；按令牌 `sub` 找到调用者在每个账套的 U8 操作员绑定，缺任何一个就整体拒绝；只放行上面这几个路由；以调用者令牌为 Bearer、把各账套本人的登录（`year` 取 `fiscal_year`，`date` 本年取今天、往年取 `period_to` 的月末）作为 `logins` 一次调 API；不记录口令和报表数字。
- 响应：`200 {"ok": true, "data": <API 的 JSON>}`；没有绑定 `409 {"error": {"code": "binding_missing", "accs_missing": ["802"]}}`（`accs_missing` 可省略，只列账套号）；无权 `403 {"error": {"code": "mgmt_forbidden"}}`（经营管理声明或账套不符）或 `403 {"error": {"code": "no_permission"}}`（操作员缺功能权限）；API 失败时 502 / 504。本服务不回显服务端的错误消息，只按错误码给出固定的说明。

## 4. 工具

路由名是 API 路径去掉 `/v1/co/` 前缀，例如 `vouchers/create`、`reports/gl_balance`、`health`、`meta`（后两个是 GET）。全部路由及其读写分级在包内的 `routes.json` 里，与 API 的路由表一致（有测试核对）。

| 工具 | 参数 | 作用 |
| --- | --- | --- |
| `u8_guide` | 无 | 返回给代理看的简短操作指南（中文）：先解析、再查字段、先预演、再写入、写后核对；504 的处理；幂等键；`fields` / `compact` |
| `u8_describe` | `route?`、`type?`（配 `op?`、`source?`）、`archive?`、`gl?` | 四个选择一次只给一个。`route`：该路由的请求 JSON Schema（取自 API 的 `/v1/openapi.json`，展开引用，去掉 `acc`、`operator`、`password`，约 12 KB 封顶）、说明和读写分级；`type`：meta 里该单据类型的条目（操作、可写字段、必填项、生单来源、预演模式）加 `field_refs`，`op`（`create` 缺省、`update`、`generate`）和 `source`（`generate` 必填）选字段所属的操作；`archive`：meta 里该档案的条目；`gl=true`（或 `type=gl`）：meta 的 `gl` 和 `gl_field_refs`；不带参数：路由目录（路由、读写、说明）。`type`、`archive`、`gl` 另调 `meta/fields`（`api-reference.md`），把本账套模板里的字段中文名、类型、必填、枚举放进 `fields`；这一步失败时照样返回 meta 的内容，另带 `fields_error`（同工具错误的形状）。结果超过大小上限时按步骤删减（先去掉重复的可写字段表，再截枚举、去枚举，再只留字段名和中文名，最后从最长的字段列表尾部删项并标 `fields_truncated`），做过的步骤列在 `trimmed` 里 |
| `u8_read` | `route`（读路由）、`body?`、`fields?`、`compact?`（缺省 `true`） | 调读路由，包括 `meta/fields`、`vouchers/search`、`vouchers/load_many`、`archives/get_many`。`fields`、`compact` 转成查询参数（`api-reference.md`） |
| `u8_write` | `route`（写路由）、`body`、`dry_run?`（缺省 `false`）、`idempotency_key?` | 调写路由。`dry_run: true` 做预演（`architecture.md`「预演」）；`body` 里也可以写 `dry_run`，但必须是 JSON 布尔值，与参数 `dry_run` 不一致时拒绝（`arap/writeoff/auto` 的计划模式也这样传）。每个正式写入（不是预演）没给键时都自动生成一个（UUID），包括审核、删除、核销和自动核销的执行；预演和自动核销的计划模式从不带键。结果（成功或失败）里带用到的 `idempotency_key`，重试时用同一个。`read_only` 时没有这个工具 |
| `u8_resolve` | `items`（`[{archive, q}]`）、`limit?`、`include_disabled?` | 名称 → 编码，调 `archives/resolve`（`api-reference.md`） |
| `u8_idempotency_get` | `route`（任一写路由）、`key` | 按幂等键查第一次请求的结果，调 `idempotency/get`（`architecture.md`「幂等键」） |
| `u8_mgmt_overview`、`u8_mgmt_pnl`、`u8_mgmt_sales`、`u8_mgmt_arap`、`u8_mgmt_cash_stock` | `fiscal_year`、`period_to`、`period_from?`（缺省等于 `period_to`）、`accounts?`（配置里的账套，1 到 3 个；缺省取配置的全部账套，令牌带账套声明 `mgmt.accounts_claim` 时只取其中有的，超过 3 个时必须给）、`consolidate?`（只有 2 个及以上账套时才合并，缺省合并），以及各报表的参数：利润表 `include_unposted?`、`dims?`；销售 `group_by?`（不含 `period`）、`top?`；往来 `side?`（`ar` / `ap`，没有 `both`）、`as_of?`、`buckets?`、`default_credit_days?`。概览、资金存货没有额外参数（用 API 的缺省） | 经营管理查询，分别调 `mgmt/overview`、`mgmt/pnl`、`mgmt/sales`、`mgmt/arap`、`mgmt/cash_stock`（`api-reference.md`；没有 `mgmt/meta` 的工具）。只在配置了 `mgmt`、`enabled` 不为 `false`，且令牌带经营管理声明（`claim` 为 `true`，或令牌的 `scope` / `scp` 含配置的 `scope`）时出现（配置参考）。stdio 方式下 `logins` 由配置的各账套登录注入（`year` 取 `fiscal_year`，`date` 本年取今天、往年取 `period_to` 的月末）；托管方式下不注入任何登录，经身份绑定服务以调用者本人的 U8 操作员一次执行，多账套全有或全无（上文「身份绑定服务」）；没有权限时调用返回 `mgmt_forbidden` |

`body` 就是 API 的请求体去掉公共字段：`acc`、`operator`、`password` 由服务注入，`body` 里出现 `acc`、`operator`、`password`、`password_enc` 时工具报错；`year`、`date` 可以在 `body` 里覆盖（`date` 缺省本机今天）。

结果：成功时是一段紧凑 JSON 文本（API 的响应体原样；`u8_write` 用了幂等键时另加 `idempotency_key`），同时放在 `structuredContent` 里。失败时工具结果带 `isError: true`，内容是：

```json
{"status": 409, "error": {"code": "u8_rejected", "message": "…", "retryable": false}, "retry_after": 5, "idempotency_key": "…"}
```

| 键 | 说明 |
| --- | --- |
| `status` | API 的 HTTP 状态；服务在本机就失败（没有发出请求，或发出后没收到响应）时为 `0` |
| `error` | API 的错误体（`code`、`message`、`retryable`，可能有 `field`、`hint`，见 `api-reference.md`），或本机生成的同形错误 |
| `retry_after` | 响应头 `Retry-After` 的秒数，有才带 |
| `error.accounts`、`error.accs_missing` | 托管方式的经营管理查询失败时：本次请求的账套号；没有绑定时缺绑定的账套号（只有账套号） |
| `idempotency_key` | `u8_write` 用了幂等键时带上，重试用同一个 |

`status` 为 `0` 时的 `code`：

| code | 含义 |
| --- | --- |
| `bad_arguments` | 工具参数不对（`body` 里有账套、操作员、口令，`dry_run` 不是布尔值，预演带了幂等键，`u8_describe` 的选择参数组合不对等），`field` 可能指出参数 |
| `config_error` | 配置文件或密钥文件不可用（见 `--check`） |
| `token_failed` | 拿不到令牌（令牌端点不可达、拒绝） |
| `read_only` | 配置了 `read_only`，却调了写路由 |
| `mgmt_forbidden` | 调了 `u8_mgmt_*`，但配置里没有启用 `mgmt`，或令牌没有经营管理声明 |
| `unavailable` | 连不上 API，请求没有发出，`retryable: true` |
| `outcome_unknown` | 写请求可能已送达却没收到响应；或者写请求在 API 前面的网关（反向代理）上得到不是 API 错误格式的 502、503、504。按结果未知处理：先核对，不要直接重试 |
| `internal_error` | 服务自身出错，细节在标准错误的日志里 |

托管方式的经营管理查询另有这些来自身份绑定服务的 `code`（`status` 为它的 HTTP 状态）：`binding_missing`（409，调用者在该账套没有绑定 U8 操作员）、`mgmt_forbidden`（403，没有该账套的经营管理权限；未知的 403 也归为它）、`no_permission`（403，绑定的操作员缺这项查询的功能权限）、`proxy_unauthorized`（401，身份绑定服务不认本服务的令牌，检查配置）、`incomplete`（API 部分账套失败，整体不返回，`retryable: true`）；连不上时 `status` 为 0、`code` 为 `unavailable`。

读请求在网关上得到非 API 格式的错误时是 `bad_response`（429、502、503 为 `retryable: true`）。

另外提供一个资源 `u8co://guide`（`resources/list`、`resources/read`）：同 `u8_guide` 的指南。没有提示模板（`prompts/list` 为空）。

## 5. 推荐的调用顺序

让代理按这个顺序做写入（指南里也是这么写的，完整示例见 [快速开始](getting-started.md)）：

1. **解析。** 用户说的是名称（「甲公司」「示例存货 X1」），用 `u8_resolve` 换成编码；`ambiguous`、`partial` 要让用户确认，不要自己挑。
2. **查字段。** `u8_describe type=<类型>`（修改加 `op=update`，生单加 `op=generate source=<来源>`）看能写哪些字段、中文名、哪些必填、枚举值、字段对应哪个档案。要找已有单据用 `u8_read vouchers/search`，一次读几张用 `vouchers/load_many` / `archives/get_many`。
3. **预演。** `u8_write dry_run=true`，把 `docs` 里的客户、存货、数量、金额给用户看。`validate_only` 表示 U8 自己的保存检查没有跑。
4. **写入。** 用户确认后同样的 `body` 去掉 `dry_run` 再调一次；记下结果里的 `idempotency_key`（没给就自动生成），重试时带上同一个。
5. **核对。** 用 `u8_read vouchers/load` 等读回结果。
6. **出错。** `retryable: true` 的按 `Retry-After` 稍后重试；504 `outcome_unknown` 不要直接重试，先 `u8_idempotency_get`（带过键的）或 `u8_read` 核对；400 看 `field` 改正再发。

## 6. 安全

- 代理能做的就是这个令牌和这个 U8 操作员能做的全部。给最小权限；只读场景用只读令牌 + `read_only: true`。
- 托管方式不保存共用的 U8 登录：经营管理查询一律经身份绑定服务以调用者本人的 U8 操作员执行，不能让多人借同一个操作员看到超出自己 U8 权限的数据。
- 客户端的「每次调用前确认」开关建议对 `u8_write` 保持打开，至少在预演之后、真写之前由人确认。
- 口令、客户端密钥、令牌只在本机密钥文件或环境变量里；服务的日志写到标准错误，不含这些内容，错误信息也会去掉它们。配置文件、密钥文件都不要放进任何仓库或项目目录。
- stdio 方式只在本机经标准输入输出通信，不监听端口。HTTP 方式（上文「托管」）只接受带 Bearer 令牌的请求，缺省只绑 `127.0.0.1`，令牌只在本次请求内使用，不缓存、不写日志。
- 调 API 和令牌端点时不使用系统代理（忽略 `HTTPS_PROXY` 等环境变量），也不跟随重定向，令牌和口令只发给配置里的地址。
- API 的限流按调用方计：代理循环调用时会先碰到 429 `rate_limited`，按 `Retry-After` 等待。

## 7. 测试

单元测试只用标准库，不连任何服务（HTTP 层用假的传输替换）：

```bash
python3 -m unittest discover -s mcp/tests -t mcp
```

在仓库根目录运行。API 侧另有测试核对 `mcp/u8co_mcp/routes.json` 与 API 的路由表一致（`api/tests`）。
