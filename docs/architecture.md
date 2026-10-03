# 架构

本文说明各组件的职责、一次请求的处理过程、桥协议（签名、口令加密、来源白名单）、桥的线程与事务模型，以及审计。配置键的完整说明见 `configuration.md`，路由与错误码见 `api-reference.md`，写入分级与风险见 `limitations.md`。

## 1. 组件

```
 调用方程序                     API 服务（Linux，容器）              U8 应用服务器（Windows）
+-------------+  HTTPS       +----------------------+  HTTP        +------------------------------+
| 你的系统     | -----------> | u8co-api (FastAPI)   | -----------> | u8co 桥（32 位 Windows 服务）  |
| OIDC 令牌    |  /v1/co/*    |  OIDC 验签、读写分级  |  /u8co/v1/*  |  HMAC 验签、来源 IP 白名单     |
| + U8 操作员  |              |  账套白名单、限流     |  签名 +      |  账套白名单                    |
|   编码和口令 |              |  口令加密后转发       |  加密口令    |  写线程池 / 读线程池（STA）    |
+-------------+              |  审计（每请求一行）   |              |  U8Login -> 业务 COM / SQL     |
                             +----------------------+              |  审计（每请求一行）            |
                                                                   +---------------+--------------+
 运维                                                                              |
+-------------+  直接签名调用桥（受信任的内网主机）                                  v
| co/client   | ------------------------------------------------------------>  U8 数据库（SQL Server）
| Python CLI  |                                                                UFDATA_<账套>_<年度> 等
+-------------+
```

| 目录 | 组件 | 运行在 |
| --- | --- | --- |
| `co/bridge/` | 桥：C# 5 编写的 32 位 Windows 服务，进程内调用 U8 的 VB6 业务 COM 组件，也直接读 U8 数据库 | U8 应用服务器 |
| `api/u8co_api/` | API 服务：FastAPI，发布 `/v1/co/*` 与 OpenAPI，负责 OIDC 认证和读写分级，签名后转发给桥 | Linux 或任何能运行容器的主机 |
| `co/client/` | Python 客户端库和命令行，直接签名调用桥 | 受信任的运维主机 |
| `mcp/` | MCP 服务：把 API 包装成供 AI 代理使用的工具，经 HTTPS 调用 API，不直接连桥（`mcp.md`） | 使用者的电脑，或托管主机 |
| `events/` | 单据事件服务：轮询桥的只读路由，把变化写入 Redis Streams（`events.md`） | Linux 或容器 |

分两层的原因：U8 业务组件是 32 位 VB6 进程内 COM，只能在装有同版本 U8 的 Windows 进程里调用，所以桥必须部署在 U8 服务器上，并且尽量小。面向其他程序的职责（OIDC、令牌权限、限流、OpenAPI）放在 Linux 上的 API 服务里；桥只认一把共享密钥和一份来源白名单。

## 2. 一次请求

1. 调用方携带 Bearer 令牌、账套、操作员编码和口令，POST 到 API 服务。
2. API 服务离线校验令牌（缺省 RS256，JWKS 缓存），按令牌的读写权限检查路由，检查账套白名单和只读账套，占用全局和每调用方的在途名额，记录审计字段。
3. API 服务用 AES-256-CBC 把口令加密成 `password_enc`，签名后 POST 到桥（第 3 节）。
4. 桥检查来源 IP、时间窗、随机数和签名，解析并校验字段，依次过账套闸门（下表），算出锁键，把任务放入写队列或读队列。
5. 工作线程取到任务：用操作员编码和口令登录 U8，读路由先过操作员权限闸门，然后调用业务组件或执行 SQL，提交事务，在新连接上回读确认，最后 `ShutDown` 登录对象、释放 COM。
6. 桥写一行审计并返回 JSON。API 服务把桥的错误码映射为自己的错误体，写一行审计，返回调用方。

### 写入经过的闸门

一笔写入依次通过下列各层，任一层拒绝即不写。各键的写法见 `configuration.md`，分级与风险见 `limitations.md`。

| 层 | 配置 | 拒绝时 |
| --- | --- | --- |
| API 账套白名单 | `U8CO_ACCOUNTS`、令牌的账套声明 | 403 `account_not_allowed` |
| API 读写权限 | 令牌的写权限声明或 scope | 403，不访问桥 |
| API 只读账套 | `U8CO_READONLY_ACCOUNTS` | 403 `account_read_only` |
| API 写入策略 | `U8CO_WRITE_POLICY_FILE` | 按策略返回 |
| 桥账套白名单 | `allowedAccounts` | 403 `account_not_allowed` |
| 桥只读账套 | `readOnlyAccounts` | 403 `account_read_only`（解密口令、登录 U8 之前；出队后登录前再查一次） |
| 第二级写入 | `enableReplicatedWrites`（缺省 `false`）、`testAccounts` | 开关关闭 403 `feature_disabled`；账套不在 `testAccounts` 403 `test_account_only` |
| 桥写入策略 | `writePolicyFile` | 按策略返回；另有写入限额和许可点数保护 |
| U8 授权 | U8 系统管理 | U8 按操作员的功能权限和数据权限判断 |

只读账套独立于写入策略和写入分级：策略放行全部、账套在 `testAccounts` 里都不能解除。第二级写入与写入策略是「与」的关系。预演（`dry_run`）和带幂等键的重发同样经过以上各层。

U8 授权由 U8 自身判断：桥每次都用调用方给出的操作员登录，桥和 API 服务都不保存操作员口令，也没有「超级用户」。读路由另经桥的操作员权限闸门（第 4 节「操作员权限」）。

## 3. 桥协议

直接调用桥（`/u8co/v1/*`）的程序须按本节签名请求、加密口令。API 服务和 Python 客户端已实现该协议（`api/u8co_api/co_crypto.py`、`co/client/u8co_client.py`），桥一侧在 `co/bridge/src/Crypto.cs`、`Auth.cs`。

下列常量属于协议，不是部署配置，不可修改：URL 路径前缀 `/u8co`、派生标签 `u8co/v1/mac` 和 `u8co/v1/enc`、请求头 `X-U8co-*`。

### 共享密钥

32 字节随机数，写成 64 位**小写**十六进制。安装桥时生成在桥运行目录的 `secret.hex` 中，同一份配给 API 服务、客户端和事件服务。大写十六进制会被拒绝，避免同一份密钥派生出两套结果。

由共享密钥派生两把 32 字节密钥：

```
k_mac = HMAC-SHA256(secret, "u8co/v1/mac")
k_enc = HMAC-SHA256(secret, "u8co/v1/enc")
```

`secret` 是十六进制解码后的 32 字节原文，标签为 ASCII，结果为原始字节。

### 请求签名

除 `GET /u8co/v1/health` 外，每个请求带三个头：

| 头 | 值 |
| --- | --- |
| `X-U8co-Ts` | Unix 秒（十进制） |
| `X-U8co-Nonce` | 32 位小写十六进制随机数，10 分钟内不得重复 |
| `X-U8co-Sig` | 小写十六进制 `HMAC-SHA256(k_mac, 签名串)` |

签名串是下列五项以 `\n` 连接（末尾无换行），按 ASCII 编码：

```
METHOD
PATH
TS
NONCE
hex(SHA256(原始请求体))
```

- `METHOD` 大写，例如 `POST`。
- `PATH` 是不含主机和查询串的绝对路径，例如 `/u8co/v1/sale-orders/verify`。
- 请求体按发送的原始字节计算哈希。桥不重新序列化 JSON，签名之后不得改动正文（不能重排键、增减空白）。

桥依次检查：

- **来源 IP 白名单。** 来源 IP 必须逐字等于 `allowedClients` 中的某一项（不支持网段），不看 `X-Forwarded-For`；不符时不读请求体，直接拒绝。安装脚本按同一份名单建立 Windows 防火墙规则，名单为空时不开放端口。
- `|服务器时间 − TS| ≤ 120` 秒。
- 随机数 10 分钟内未使用过（进程内缓存）。
- 签名以常量时间比较。

任一项失败都返回 `401 {"ok":false,"code":"unauthorized"}`，不说明是哪一项。

### 口令加密

请求体中不出现明文口令，改为 `password_enc`：

```
password_enc = Base64( IV ‖ AES-256-CBC-PKCS7(k_enc, IV, UTF-8(口令)) )
```

IV 为每次随机生成的 16 字节，置于密文之前。完整性由请求签名保证，不另做 MAC。桥解密后只在本次登录中使用口令，不写日志和审计。

### 响应与健康检查

响应不签名。调用方不能仅凭 JSON 证明其来自桥，须依靠来源白名单、防火墙和网络隔离（`SECURITY.md`）。

`GET /u8co/v1/health` 不签名，桥也不检查来源 IP，只由防火墙规则限制来源。响应只含线程、队列和开关状态，不含业务数据；字段见 `api-reference.md`。

### 测试向量

仅用于核对实现，不得用作真实密钥。

| 项 | 值 |
| --- | --- |
| 共享密钥 | `00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff` |
| `k_mac` | `a85c2c38ccf25d1c837ec12b0a3707ee6aa584ed7834f8eac3302f81098b6407` |
| `k_enc` | `3d9103ebd3f1448ce0dab668a4ce27c51a2ff0ac0b10c90cc5c300520e3411bc` |
| IV | `0f0e0d0c0b0a09080706050403020100` |
| 口令 | `测试Pass#01` |
| `password_enc` | `Dw4NDAsKCQgHBgUEAwIBAK86wvyLgq4AmzNMoENmeZU=` |
| 方法、路径 | `POST`、`/u8co/v1/sale-orders/verify` |
| 请求体 | `{"acc":"998","year":"2026","operator":"op001","password_enc":"Dw4NDAsKCQgHBgUEAwIBAK86wvyLgq4AmzNMoENmeZU=","date":"2026-09-26","id":1000000003,"action":"verify"}` |
| 请求体 SHA-256 | `d1e19ce73e0391436e1e3eb76870e4237a113da406669a9f7e835abb920c2c58` |
| `X-U8co-Ts` | `1790434800` |
| `X-U8co-Nonce` | `a1b2c3d4e5f60718293a4b5c6d7e8f90` |
| `X-U8co-Sig` | `7ca396c444e8bd5f5ad0163ad12bb365688ce638d7bb4bb46da0b278e666061f` |

同一组向量出现在 `co/client/tests/test_u8co_client.py`、`api/tests/test_co_crypto.py` 和桥的 `--selftest`（`co/bridge/src/SelfTest.cs`），修改时三处须同步。

### 用其他语言实现客户端

1. 读取共享密钥，派生 `k_mac`、`k_enc`。
2. 加密口令得到 `password_enc`，与其他字段一起序列化为紧凑 JSON（UTF-8）。
3. 生成 `TS`、`NONCE`，按上文计算签名，带三个头发送 POST，`Content-Type: application/json`。
4. 读桥的超时须长于 75 秒（Python 客户端缺省 90 秒）。请求已送出而未读到响应时按「结果未知」处理，不要自动重试写操作。
5. 先用上面的向量核对通过，再连接真实的桥。

## 4. 桥的线程和队列

服务监听 `listenPrefix`（必填；安装脚本缺省写 `http://+:18089/u8co/`）。HTTP 线程只做鉴权、校验和排队，不调用 U8。

VB6 组件是套间线程模型，登录对象不能跨线程复用。因此 U8 调用只在桥自建的 STA 线程上执行，COM 对象在创建它的线程上用完并释放，不跨线程传递。

| 线程池 | 线程数 | 处理 | 登录 |
| --- | --- | --- | --- |
| 写线程池 | `staWorkers`（缺省 4，1 到 8） | 全部写路由，以及需要登录对象的读路由 | 每个任务登录一次，`finally` 中 `ShutDown` 并 `FinalReleaseComObject`（开启登录复用时见下文） |
| 读线程池 | `readWorkers`（缺省 4，1 到 16） | `RouteClass.cs` 中登记的纯 SQL 读路由 | 不登录，使用登录缓存中的连接串；未命中时转入写线程池 |

读线程池处理的路由包括：采购发票、生产订单、质量单据的 `vouchers/load`，`workflow/state`、`workflow/history`，`gl/vouchers/load`、`gl/vouchers/list`、`gl/vouchers/digest`，`archives/get`、`archives/list`，`vouchers/list`，`stock/current` 等。这些处理函数只能使用连接和操作员姓名，不能访问登录对象。需要登录对象、业务组件或应收应付组件的读取（例如收付款单读取）留在写线程池。

两条队列各自最多 `queueCap` 个任务（缺省 32，1 到 256），满时返回 429 `busy`。

### 单据锁

入队时算出锁键，线程只取锁键全部空闲的任务，执行完释放。取不到锁键的任务留在队列中等待，不占线程，也不持锁等待。

| 路由 | 锁键 |
| --- | --- |
| 带 `id` 的单据路由 | `<类型>:<id>`（两条专用审核路由为 `sale_order:<id>`、`dispatch:<id>`） |
| 新增 | `new:<类型>`，同类单据的编号串行 |
| 参照生单 | 来源 `<来源类型>:<id>` 和 `new:<目标类型>` |
| 总账凭证 | `gl:<year>:<期间>:<类别>:<凭证号>`，新增为 `new:gl:<类别>` |
| 档案 | `arc:<档案>:<编码大写>`，档案写入另持 `arc:write` |
| `login-check`、不带 `id` 的待办、列表、现存量 | 不加锁 |

新增写路由必须在 `DocLocks.KeysOf` 中有锁键，否则会与其他写线程并发修改同一张单据或同一段编号。

### 写闸门

配置键 `serializeWrites` 控制经 U8 组件的写入是否在单据锁之外再持闸门键（`WriteGate.cs`，由 `DocLocks.KeysOf` 追加）：

| 取值 | 闸门键 | 效果 |
| --- | --- | --- |
| `true`（缺省） | `u8:write` | 整个桥同一时刻只执行一笔写 |
| `"account"` | `u8:write:<账套>` | 同一账套一次一笔，不同账套并行 |
| `false` | 无 | 只按单据锁排程（档案写入仍持 `arc:write`） |

- 受闸门约束的写路由以 `WriteGate.cs` 中的名单为准，包括单据的新增、修改、删除、审核、关闭、生单、锁定，专用审核路由，审批流动作，总账凭证写入，档案写入，应收应付的核销、制单及其取消。幂等重放同样按写入排程。新增写路由须登记到该名单。
- 闸门只用于排程：取不到闸门键的写入留在队列中，线程跳过它去取后面锁键空闲的任务（`StaPool.Pick` 按队列顺序找第一个锁键全部空闲的任务），读路由不会被排在前面的写入阻塞。读线程池的纯 SQL 路由不经过闸门，写线程池上的读路由也不持闸门键。
- 排队中的写入同样受 75 秒排队期限和 `queueCap` 约束，没有单独的「等待闸门」错误。写入密集时调用方应控制并发。
- **迟到的写入不开始。** 75 秒同时是 HTTP 线程等待结果的时限；写入排到很晚才开始时，执行未完成 HTTP 已返回 504 `outcome_unknown`，U8 却照样写入。所以写路由出队时剩余时间不足 30 秒（`WorkItem.MinWriteStartMs`）就不再开始，按排队超时返回 503 `busy_timeout`（未执行，可重试），即写入最多排队 45 秒。该规则与 `serializeWrites` 无关。自检（`WriteGateSelfTest`）逐条核对路由表：每条路由要么是显式列出的读路由，要么登记在 `WriteGate` 中。
- 缺省全局串行的原因：多个写线程同时在 U8 组件中执行时，曾出现互相等待的应用层阻塞（不是 SQL Server 死锁，不会触发 1205），持续数分钟后才一起释放。档案的 EAI 导入必须与任何写入互斥，更细粒度的并行收益有限、风险难以穷举。`"account"` 只串行同一账套的写入，`UFSystem` 中各账套共用的登记（审批流任务、许可任务）不随之串行，启用前应在测试账套上实测。

### 登录缓存

写线程每次登录成功后，记下登录对象给出的数据库连接串和操作员姓名。键是进程随机密钥上的 HMAC-SHA256（账套、年度、操作员、口令、子系统、登录日期），不保存口令。

- 有效期 `authCacheSeconds` 秒（缺省 600，0 关闭；关闭后读路由都走写线程池）。
- 最多 256 条，满时丢弃最早的。
- 同一键登录抛出任何异常即删除；登录失败不缓存。
- 读线程执行前再次确认条目未过期、未被删除，否则该任务在读线程上按写线程的流程登录一次。
- 登录日期在键中，命中缓存不会绕过 U8 的日期检查。
- 连接串不进日志、审计和响应。

### 登录复用

`loginReuse`（缺省 `false`）开启后，写线程把用过的 U8 登录对象（`clsLogin`）留给下一笔请求（`LoginCache.cs`）。关闭时每笔请求新建登录、用完 `ShutDown`。

- 只有写线程（`u8co-sta-N`）保留登录，每个写线程各有一份，只在本线程上取用和关闭；读线程在登录缓存未命中时登录，用完即关。只缓存登录对象本身，CO 组件、业务对象和数据库连接每笔请求照常新建。
- 键与登录缓存相同，任一项不同即重新登录；跨过零点登录日期改变，自然不命中。
- 闲置 60 秒作废，每次放回重新计时；写线程空闲时也在本线程上关闭过期登录（`LoginCache.Sweep`）。自真正登录起最长使用 600 秒，到期重新登录：U8 中修改口令、停用操作员，最迟此时生效。复用不刷新登录缓存的有效期。
- 每个写线程最多 16 个，满时关闭最久未用的。复用前确认 `LogState` 仍为 `0`、`cUserName` 可读，否则关闭后重新登录。
- 当场关闭、不放回的情况：请求抛出任何异常（含 4xx 的 `BridgeException`）；处理函数调用了 `ctx.DropLogin()`；处理完成时请求连接上 `@@TRANCOUNT` 不为 0。
- 不复用的请求：登录子系统为质量管理（QM）、应收（AR）、应付（AP）的请求一律不放回（`StaExec.KeepAfter` 按 `WorkRun.SubOf` 判断）。其他子系统中凡是把登录对象放进 by-ref 参数交给组件的路径（CO 的 `Init`、凭证导入和 EAI 的 `Transact` 等），调用前调用 `ctx.DropLogin()`，或经 `LoginBack` 核对组件交回的是否为原登录对象，不是则不放回。
- `loginReuseOffHours` 可设置停用时段，时段内每笔请求照常新建登录、用完即关。
- 写线程退出前在本线程上逐个 `ShutDown`。看门狗 `FailFast` 时来不及注销，缓存中的登录在 `UA_TaskLog` 中的登记行会残留，需在 U8 系统管理中清除异常任务，或重启 U8 应用服务。
- 许可：U8 按「工作站 × 子系统」占用许可点数。缓存中的登录在作废之前一直占用本机该子系统的 1 个点数（闲置最多 60 秒，最长 600 秒），`UA_TaskLog` 中的登记行也一直存在；点数紧张时人工用户可能因此无法登录。
- 管理员在系统管理中清除桥的任务后，缓存中的登录可能已失效而健康检查无法发现，下一笔请求可能因此失败一次，随后该登录被关闭并重新登录。
- 健康检查增加 `login_reuse`、`login_reuse_active`、`logins_opened`、`logins_reused`、`logins_closed`（进程启动后累计）；`logins_opened` 减 `logins_closed` 约等于当前未关闭的登录数。

### 操作员权限

读路由在登录（写线程）或登录缓存命中（读线程）之后、进入处理函数之前经过 `PermGate`（只使用 `ctx.Conn`）：

- 按 `PermRegistry` 找到该路由（单据类型、档案、报表）的规则，读出权限快照 `PermContext`（功能 id 集合、是否账套主管、已开启的数据权限开关、数据权限管理员、各受控对象可查询的编码），功能权限不足返回 403。读路由没有登记规则时一律拒绝。
- 处理函数用 `PermSql` 向列表 SQL 追加参数化条件（记录级权限），用 `PermCheck` 核对单张单据。
- 读路由成功后由 `PermMask` 统一做字段权限遮蔽：把操作员在 U8 中无权查看的字段置为 `null`，响应增加 `masked_fields`（被置空的字段名，排序去重）。只遮不删，行和单据照常返回。账套主管和没有字段权限设置的操作员不受影响；审计只记被遮字段的个数。
- 快照在 `PermCache` 中按（账套、请求年度、登录年度、操作员）缓存 60 秒，最多 256 条；登录失败时清除该操作员的条目。
- 写路由不经过 `PermGate`，由 U8 组件按操作员权限判断；总账写入的 `GlState.Permit` 使用同一套判断，但每次现读、不缓存。
- `perm/snapshot` 返回调用操作员自己的权限快照；`perm/evaluate` 查询其他操作员的权限，只对 `permEvaluateOperators` 中列出的调用操作员开放。语义见 `api-reference.md`。

### 空白模板缓存

部分写路由新建单据时先用 `select … where 1=2` 取得只有 schema 的空白 DOM（`DomRows.Blank`：采购订单、调拨单、采购入库、材料出库、产成品入库）。该 DOM 缓存在进程内存中（`TplCache`），可用 `templateCache` 关闭（缺省开启）。安全优先于命中率：

- 只缓存 ADO「where 1=2」持久化的纯 schema。`GetDefaultVoucherDom`、`GetDefaultVTID`、档案模板行（`ArcTpl`）、填过表头的 DOM，以及 CO 组件返回的空白（采购发票、到货单的 `GetVoucherDataById(…, 0, …)`，应收应付的 `GetVouchData`）都不缓存。
- 缓存的是 XML 文本，每次命中都重新解析一份 DOM，并核对字段数、没有 `z:row`。
- 键：服务器名、库名（含账套号和年度）、登录名、缺省架构、模板 SQL 原文。
- 每次使用都在同一连接上核对数据库对象指纹：对 `sys.dm_exec_describe_first_result_set(模板 SQL, NULL, 1)` 返回的整张结果集元数据（列序、列名、类型、长度、精度、排序规则、可空、键列、隐藏列、来源表和来源列）计算 SHA-256。视图改定义、底表增减列、类型变化都会反映出来。描述出错、连接上有未提交事务时本次不使用缓存。
- 组件指纹：`ADODB.Recordset`、`MSXML2.DOMDocument` 进程内服务器 DLL 的 FileVersion、修改时间和大小，最多每 60 秒重查一次。
- 未命中时先算指纹、取模板、再算一次指纹，两次一致才写入缓存。
- 硬 TTL 30 分钟；最多 64 条，满时整体清空。计算指纹出现任何异常都现取、不写入，并暂停缓存 10 分钟。重启服务即清空，不落盘。

### 幂等键

全部写路由都支持 `idempotency_key`（`IdemReq.Supports(path)` 即 `WriteGate.IsWrite(path)`，新增写路由自动支持）。带键的请求走 `IdemFlow`，查记录和等待都在 HTTP 线程上、入队之前完成：

- 记录存放在运行目录的 `idem\` 子目录（继承运行目录权限），一个键一个文件，文件名为 `caller` + 账套 + 路由 + 键的 SHA-256。首次写 `in_flight` 用 `CreateNew` + `WriteThrough` 直接建正式文件，成功才入队；之后的改写先 `WriteThrough` 写临时文件，再 `MoveFileEx(REPLACE_EXISTING | WRITE_THROUGH)` 原子替换（`IdemFile`）。只有临时文件、没有正式文件的键按结果未知处理。
- 终态只有 `ok`（2xx）和 `outcome_unknown`（其余 5xx）。所有 4xx 都发生在 U8 提交之前（提交之后的失败一律 504），连同 `busy`、`busy_timeout`、`stopping` 等保证未执行的错误码，删除记录、不占用键。5xx 只按错误码放行，不按 HTTP 状态。终态写入失败重试 3 次，仍失败记审计事件 `idem_persist_failed`，此后按结果未知重放。
- 本进程中正在执行的键另有一张内存表。同一键的第二个请求在表中等待第一个的结果，不入队；第一个未占用键时，等待者接着自行执行。进程重启后磁盘上残留的 `in_flight` 改记为 `outcome_unknown`，不会自动重做。
- 预演不使用幂等记录：`dry_run: true` 带键返回 400，`arap/writeoff/auto` 的计划模式同样。
- `idempotency/get` 按同样算法定位记录，只读；记录中保存了操作员，其他操作员查询得到 `found: false`；登录子系统按原路由（`/u8co/v1/gl/` 下为 `GL`，其余 `AS`）。
- 重放前先校验本次登录（`IdemLogin`）：登录缓存命中即通过，否则在写线程池上执行一个只登录的任务。
- 首次请求 75 秒时仍在执行：HTTP 线程返回 504，另起后台线程等待任务结束，再把真实结果写入记录；无法起线程时直接记为结果未知。
- 过期：`ok` 24 小时，`outcome_unknown` 72 小时；损坏的记录按文件修改时间计算。服务启动时及之后每 30 分钟（有幂等请求时）清理一次。记录读写失败返回 503 `store_unavailable`，请求不执行。

### 超时和看门狗

| 时限 | 行为 |
| --- | --- |
| 75 秒 | HTTP 线程等待任务的上限。仍在排队：任务标为放弃，503 `busy_timeout`（可重试），工作线程之后跳过它并记审计 `expired`。已在执行：504 `outcome_unknown`，任务不会被取消 |
| 90 秒 | API 服务和 Python 客户端读桥的缺省超时，长于 75 秒，以便先看到桥的结果 |
| 3 分钟 | 任一工作线程上的单个任务超过该时间，或任一工作线程（`u8co-http`、`u8co-sta-N`、`u8co-read-N`）已退出：健康检查变为 503，两个池不再接受新任务（排队中的返回 503 `stopping`），等其他线程手上的任务完成（最多 75 秒）后进程 `FailFast` |

服务的恢复动作是失败后 60 秒重启、一天内重置计数，因此 `FailFast` 之后服务会被重新拉起。停止服务时，两条队列中仍在排队的请求返回 503 `stopping`，所有线程在同一个总时限（65 秒）内结束；`OnStop` 会向服务控制管理器再申请 120 秒。停止有上限：

- `OnStop` 一开始即启动后台线程 `u8co-stop-watch`，90 秒后无论进行到哪一步，都在 `unhandled.log` 记一行并 `FailFast`。`OnStop` 返回后它继续计时，用于处理许可查询超时后遗留在 COM 调用中的 STA 线程导致进程无法退出的情况。
- 等满 65 秒仍有工作线程存活（COM 调用未返回）时，记一行后立即 `FailFast`。
- 以上两种情况都在结束进程前先向服务控制管理器报告 `SERVICE_STOPPED`，按正常停止处理，不触发失败重启。看门狗的 `FailFast` 走同一入口（`StopGuard.Kill`）：恰逢停止请求时同样先报告已停止；平时不报告，由恢复动作拉起。控制台模式（`--console`，Ctrl+C）同样按 65 秒结束。
- 手工停止服务后若 exe 仍被锁定无法替换，只结束 `<root>\bin\u8co-bridge.exe` 对应的那个进程，不要按进程名结束。

启动时把 .NET 线程池最小线程数提高到 `2 × queueCap + staWorkers + readWorkers + 8`（不低于原值）。

## 5. 事务

请求连接上的每一次 U8 写都放在桥的 `CoTrans` 中：

1. `BeginTrans`，记下 `@@TRANCOUNT`。
2. 调用业务组件。返回成功才 `CommitTrans`，否则 `RollbackTrans`，返回 409 `u8_rejected` 并附 U8 原文。异常同样回滚。
3. 提交后要求本连接 `@@TRANCOUNT` 为 0。「没有活动事务」只认 HRESULT `0x8004D00E`，不看错误文本。
4. 在新连接上（不加 `NOLOCK`，`SET LOCK_TIMEOUT 10000`）回读目标状态。回读失败或不一致时写入已经提交：审核返回 409 `state_mismatch`，保存类操作返回 504 `outcome_unknown` 并附已知的新主键或单号。

提交前的回写核对（累计开票数、累计到货数等）在同一事务内进行，核对不通过即回滚。部分 U8 组件会在保存中自行提交，此时核对不通过也无法回滚，桥返回 500「U8 已自行提交，无法核对回写」，细节记在审计 `detail` 中。

以下几类 U8 组件自行开启事务或自行提交，不放入 `CoTrans`：

| 组件 | 用途 | 桥的做法 |
| --- | --- | --- |
| 凭证导入 | 总账凭证新增、修改 | 调用前完成全部可做的检查，调用后在新连接上回读。调用抛错或回读失败一律 504 `outcome_unknown` |
| 档案导入 | 基础档案新增、修改、删除 | 同上 |
| U8 API 框架 | 生产订单审核、弃审 | 同上 |
| 审批代理和事务服务 | 审批动作 | 由 U8 事务服务开启事务，成功才提交 |

总账的作废、审核、签字、删除执行与 U8 客户端界面相同的 SQL（实测核对）：同一事务内先以 `UPDLOCK, HOLDLOCK` 读取状态、检查门槛、写入、再读一遍确认，未达到目标状态即回滚。第二级写入（`limitations.md`「写入分级」）同样执行与 U8 界面相同的 SQL，只在开启 `enableReplicatedWrites` 且账套位于 `testAccounts` 时可用。

SQL 只使用参数 `?`，调用方输入不拼入 SQL 文本；表名和列名只来自代码中的类型表。

### 预演（`dry_run`）

写路由带 `dry_run: true` 时，桥复用正常写入的整条路径，只把「提交」这一点换成预演。预演执行的检查、锁和 U8 调用与真实写入是同一份代码。

1. **解析。** `Requests.ParseNew` 在检查未知字段之前从请求体中取出 `dry_run`，记入 `WorkItem.DryRun`，只对写路由（`WriteGate.IsWrite`）如此；对读路由它仍是未知字段。`arap/writeoff/auto` 不走这里，其 `dry_run` 保持「只出计划」。同时带幂等键返回 400。随后查模式表 `DryRunModes`（路由、类型、操作 → `rollback` / `validate` / 拒绝），表中没有的组合在登录之前返回 400「该操作不支持预演」。
2. **上下文。** 写线程执行任务时以 `DryRun.Begin` / `DryRun.End` 包住处理函数，预演上下文为线程静态，只在该 STA 线程、该任务期间有效。处理函数在得知新单据主键时调用 `DryRun.Created`，涉及请求以外的已有单据（如生单的来源单）时调用 `DryRun.Touched`，有补充信息时调用 `DryRun.Set`；非预演时这些调用不做任何事。
3. **提交钩子（`rollback`）。** `CoTrans.Commit` / `CommitSeen` 在预演时不提交，而是：先查本连接的 `@@TRANCOUNT`，为 0 说明 U8 组件已自行提交，返回 504 `outcome_unknown` 并在审计中记 `dry_run_self_commit`；否则在**同一连接、同一事务内**读出登记过的单据（`DryRunPreview`：表头表、表体表 `SELECT *`，每张最多 200 行，最多 10 张，序列化超过约 4 MiB 时去掉表体并标 `detail.truncated`；读取失败则回滚并返回 500，审计 `dry_run_preview_failed`），再 `RollbackTrans`，最后抛出 `DryRunDone` 携带结果。提交后的新连接回读因此不会执行。
4. **停止点（`validate`）。** 自行提交的组件（凭证导入 `U8PzInsert`、EAI 档案导入、U8 API 框架的生产订单和物料清单、审批服务、检验单的保存和删除、总账记账的 `TransactionScope`、`arap/voucher` 的凭证导入）调用之前，处理函数调用 `DryRun.Stop(ctx, "<组件>")`。预演时它抛出 `DryRunDone`（`detail.stopped_before` 记录停止位置），非预演时不做任何事。组件之前的全部检查都照常执行。
5. **收尾。** 外层（`DryRunRun.Run`）把 `DryRunDone` 转为 200 响应，审计 `outcome` 记 `dry_run`，审计行另有 `dry_run` 字段（模式）。处理函数正常返回却既未到达提交点也未 `Stop` 时：`rollback` 模式下从未开启事务，说明没有需要写入的改动，返回 200 预演并带 `detail.no_change`；开启过事务，或为 `validate` 模式，都无法确认没有写入，返回 504 `outcome_unknown`，审计记 `dry_run_no_commit`。预演完成后本线程上再调用 `CoTrans.Begin` / `Commit` / `CommitSeen` 一律抛出异常。

| 模式 | 适用 | 是否调用 U8 组件 | 事务 | 返回 |
| --- | --- | --- | --- | --- |
| `rollback` | 组件在桥的 `CoTrans` 中、写在请求连接上（单据 CO、UFAPBO、核销组件），或桥自身的 SQL（总账作废、审核、签字、删除，生产订单关闭，项目、银行账户、币种和凭证类别的修改删除，取消核销，取消制单） | 是 | 提交点改为：`@@TRANCOUNT` 检查 → 同连接读预览 → 回滚 | `docs`（事务内的样子）、`detail`、`number_may_skip`、`locks_held` |
| `validate` | 组件自行开连接或自行提交（上表各类，以及检验单保存、总账记账） | 否，停在调用之前 | 调用前的检查照做，桥自身不写 | 已有单据当前的样子（如有）、`detail`、`validate_only` |
| `plan` | 仅 `arap/writeoff/auto` | 否 | 不开事务 | 配对计划 |

预演占用的资源与真实写入相同：单据锁、写闸门、写线程、一次 U8 登录；排队和超时规则也相同（75 秒，45 秒开始余量）。

## 6. 审计

### 桥

目录为 `auditLog`（必须位于桥的运行目录之下），按本地日期写 `u8co-yyyyMMdd.log`，一行一个 JSON：

| 字段 | 内容 |
| --- | --- |
| `ts` | UTC 时间 |
| `ip` | 来源 IP |
| `path`、`route` | 请求路径 |
| `acc`、`year`、`operator` | 账套、年度、操作员编码 |
| `type`、`id`、`action` | 单据类型（生单时为目标类型）、主键（新增或生单成功后为新主键）、动作 |
| `caller` | 请求体中的调用方标识 |
| `op` | 写入分类（写入策略使用的操作词） |
| `policy` | 写入策略或只读账套的判定：`allow` 或拒绝的错误码；未配置策略时为空 |
| `outcome` | 结果：成功、错误码、`expired`（放弃的排队任务）或 `dry_run`（预演完成，未写入） |
| `dry_run` | 仅预演行：`rollback` 或 `validate`（失败的预演也有，`outcome` 为错误码）；带 `dry_run: true` 但在确定模式之前即被拒绝时为 `requested` |
| `message` | U8 消息或错误说明，最多 300 字 |
| `detail` | 补充信息，最多 300 字。总账记凭证键，档案记档案和编码 |
| `duration_ms` | 耗时 |
| `trancount_before`、`trancount_after` | 审核前后的 `@@TRANCOUNT` |

另有事件行（带 `event` 字段），例如 `signature_check`、`assembly_loaded`、`license_full`、`idem_persist_failed`、`write_policy_invalid`。

不记录口令、签名、密钥、登录令牌、连接串，也不记录 `head`、`lines`、`fields`、分录和审批意见。异常文本中出现的连接串口令替换为 `***`。401、403 等在登录前即被拒绝的请求，审计中没有操作员和年度。

### API 服务

每个请求一行 JSON，缺省写到标准输出（`U8CO_AUDIT_LOG` 可改为标准错误、文件或关闭）。uvicorn 的访问日志也在标准输出，审计行以 `{` 开头，可据此区分。

| 字段 | 内容 |
| --- | --- |
| `ts` | UTC 时间 |
| `caller` | 令牌的 `azp`，没有则为 `client_id` 或 `sub` |
| `trust` | 命中的信任项名 |
| `endpoint` | 请求路径 |
| `action` | 路由和动作，附单据主键（`#id`）、凭证键（期间-类别-凭证号）或档案编码 |
| `accs` | 账套 |
| `operators` | `账套=操作员编码` |
| `user` | 终端用户标识（`X-U8co-User` 头中的 UUID，头名可配置），没有则为空 |
| `status`、`took_ms` | HTTP 状态、耗时 |
| `dry_run` | 布尔：写预演（包括失败的预演）为 `true`，其余为 `false` |
| `bridge` | 服务本次调用的桥：`default`（`U8CO_BRIDGE_URL`）或 `routes[序号]`（`U8CO_BRIDGE_ROUTES_FILE` 中的分流桥）；未访问桥的请求为 `null` |

不记录令牌、口令、正文、审批意见和档案字段。

## 7. API 服务的分层

| 层 | 职责 |
| --- | --- |
| 认证 | 解析 Bearer，按令牌的 `iss` 和 `aud` 找到唯一一条信任配置，按其允许的算法（缺省 RS256）和 JWKS 验签，检查 `exp` / `nbf`（缺省 60 秒偏差） |
| 读写分级 | 令牌的写/读声明或 scope；`co_access.ACCESS` 把每条路由登记为读或写，未登记的按写处理 |
| 账套白名单 | 请求的 `acc` 不在 `U8CO_ACCOUNTS`（以及令牌的账套声明，若已配置）中返回 403 `account_not_allowed`，不访问桥 |
| 只读账套、写入策略 | `U8CO_READONLY_ACCOUNTS`、`U8CO_WRITE_POLICY_FILE`，在调桥之前拒绝 |
| 限流 | 全局在途上限、每调用方在途上限和每分钟次数，均在进程内计数 |
| 转发 | 补缺省 `date`、`year`，加密口令，签名，调桥；把桥的错误码映射为 HTTP 状态 |
| 审计 | 每个请求一行 |

限流在进程内计数，一个容器只运行一个 uvicorn worker；多进程时各自计数。进程停止时等待正在执行的 CO 请求，缺省 100 秒（uvicorn `--timeout-graceful-shutdown`，compose 的 `stop_grace_period` 取同值）。

JWKS 按地址缓存（信任项未写 `jwks_url` 时先经 OIDC discovery 取地址）。遇到未知 `kid` 才刷新，同一地址两次成功刷新至少间隔 300 秒；拉取失败记忆 30 秒。响应超过 256 KiB 或不是 JSON 按失败处理，`use` 为 `enc` 的密钥忽略。

`GET /healthz` 不需要令牌、不访问桥，返回 `{"ok":true,"configured":…}`，供容器健康检查使用。

## 8. 程序集解析和崩溃防护

桥不是 U8 客户端，有两类程序集问题会导致进程退出，桥在 `Main` 开始、任何 U8 COM 调用之前处理：

1. **移动端推送。** U8 审批引擎会在 .NET 线程池中排队向 U8 移动审批推送消息，加载 `YonYou.U8.MA.*` 程序集。该程序集在桥进程中无法解析，缺省会在线程池线程上抛出未处理异常并结束进程。桥通过 `u8co-bridge.exe.config` 开启 `legacyUnhandledExceptionPolicy`，未处理异常只写入 `unhandled.log`，不结束进程。是否推送由 `mobilePush` 决定：
   - `false`（缺省）：在 `AssemblyResolve` 中对移动审批程序集（短名前缀 `YonYou.U8.MA.`、`Yonyouup.U8.MA.`、`UFIDA.U8.MA.`，不区分大小写）写一行 `blocked` 诊断后返回 null，不加载。经本服务的审批不会推送到 U8 移动审批。
   - `true`：移动审批程序集按短名在 `<u8Home>\U8AuditWebSite\bin`、`<u8Home>\U8AuditWebSite\bin\Query` 中查找 `<短名>.dll` 加载；其依赖先在这两个目录中查找，再到第 2 条的目录中查找。只认短名、不允许路径字符、文件必须已存在；找不到时在 `unhandled.log` 记一行（每个短名一次）。每个加载的程序集在审计日志中记一行 `assembly_loaded`（路径、版本）。推送过程中的异常只写 `unhandled.log`，不影响审批结果。`meta` 的 `features.mobile_push` 报告实际生效值。
2. **U8 自己的 .NET 程序集。** U8 的 exe 依靠 probing 配置找到 `Interop`、`U8APIFramework` 等目录下的程序集，桥进程找不到。桥在 U8 安装目录下固定的几个子目录中按短名查找 `<短名>.dll` 并加载（只读）。加载失败返回 503 `com_unavailable`。

未处理异常的文本（已擦除口令）追加到运行目录的 `unhandled.log`。U8 线程池上的异常不触发 `FailFast`。`u8co-bridge.exe.config` 缺失或策略未开启时，审批路由返回 503 `com_unavailable` 并说明原因，单据读写不受影响。

## 9. 设计取舍

- **32 位、系统自带编译器。** 使用 Windows 自带的 `C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe`，`/platform:x86`，语法限于 C# 5。U8 服务器上无需另装运行时或 SDK。
- **晚绑定。** 不引用互操作程序集，COM、ADO、MSXML 一律晚绑定（`ComUtil.*`）。U8 打补丁后互操作程序集常与组件版本不一致，晚绑定少一个出错点；组件签名的变化由 `--check-signatures` 在运行时对照类型库发现。
- **每次登录。** 调用方每次携带操作员和口令，由 U8 自行授权。桥只在内存中按上文规则缓存读路由使用的连接串，以及（开启时）复用登录对象。
- **白名单默认拒绝。** 账套白名单和来源白名单为空时一律拒绝；放行须修改配置并重启，不能依靠修改代码。第二级写入缺省关闭。
- **先实测、后编码。** 每条 COM 调用序列先在测试账套上跑通并核对 U8 的回写，再写入桥（`CONTRIBUTING.md`）。
- **不依赖第三方封装。** 本项目不包含、也不部署任何第三方 U8 接口封装包。
