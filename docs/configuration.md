# 配置参考

所有站点相关的值都来自配置，代码里没有内置的服务器地址、账套或来源 IP。缺省值偏安全：白名单为空即拒绝全部，没有缺省的桥地址，密钥和口令只从文件或环境变量读取，不接受命令行参数。

本文是配置项的唯一权威说明：桥的 `config.json`（第 1 节）、API 服务的环境变量与站点文件（第 2 节）、写入控制与正式账套开放（第 3 节）、Python 客户端（第 4 节）、本地 MCP 服务（第 5 节）。各写入的风险分级见 [已知限制](limitations.md)「写入分级」。

## 1. 桥（Windows）

### 运行目录

桥的程序、配置、密钥和日志都在一个运行目录下（下文写作 `<root>`）：

| 文件 | 内容 |
| --- | --- |
| `<root>\bin\u8co-bridge.exe`、`u8co-bridge.exe.config` | 程序和 CLR 配置 |
| `<root>\config.json` | 桥的配置（下节） |
| `<root>\secret.hex` | 共享密钥，一行 64 位小写十六进制 |
| `<root>\sql.json` | 可选，专用 SQL 登录 |
| `<root>\logs\u8co-yyyyMMdd.log` | 审计日志（缺省位置） |
| `<root>\logs\write-quota.json` | 写入策略的日限额计数（配置了写入策略和审计目录时） |
| `<root>\unhandled.log` | 未处理异常和程序集解析诊断 |
| `<root>\startup-error.txt` | 启动失败的原因 |

`<root>` 缺省为 `%ProgramData%\U8Co\u8co`，由服务的命令行参数 `--root` 指定（安装脚本的 `-Root`）。要求：

- 带盘符的绝对路径，至少在盘符下两级，不含引号。
- `<root>` 的 ACL 只给 SYSTEM 和 Administrators。
- `<root>` 的每一级上级都不能是重解析点，所有者必须是 SYSTEM、Administrators 或 TrustedInstaller，且除这三者和 CREATOR OWNER 外没有账户能删除、改名、改权限或改所有者（允许新建）。不满足时安装脚本停止、服务拒绝启动（处理方法见 [快速开始](getting-started.md)）。
- `secret.hex`、`sql.json` 被授予其他 SID 读取时，服务拒绝启动。

### config.json

只在服务启动时读取，修改后须重启服务（`writePolicyFile` 指向的策略文件除外，见第 3 节）。未知键、类型不对或超出范围的值都会导致启动失败。完整示例在 `co/bridge/config.example.json`。

**站点与白名单**

| 键 | 必填 | 缺省 | 说明 |
| --- | --- | --- | --- |
| `listenPrefix` | 是 | 安装脚本写 `http://+:18089/u8co/` | HttpListener 前缀，形如 `http://<主机>:<端口>/u8co/`。只支持 `http`；主机可以是 `+`、`*`、主机名、IPv4 或 `[IPv6]`；端口必须写明（1 到 65535）；路径固定为 `/u8co/` |
| `u8Server` | 是 | 无 | 传给 U8 登录的应用服务器：U8 中登记的服务器计算机名或 IP。1 到 255 个字符，不含空白、控制字符、引号和 `;<>&\|` |
| `allowedAccounts` | 否 | `[]` | 允许登录的账套号（三位数字）。为空时拒绝所有账套，请求在登录 U8 之前返回 403 `account_not_allowed` |
| `allowedClients` | 否 | `[]` | 允许调用的来源 IP，逐字比较，只写单个地址的规范写法，不支持网段和主机名。为空时拒绝所有来源 |
| `auditLog` | 否 | `<root>\logs` | 审计目录，必须位于 `<root>` 之下。安装脚本不写这个键 |
| `u8Home` | 否 | `C:\U8SOFT` | U8 安装目录（缺省是 U8 安装程序的缺省目录，按实际安装目录填写）。桥只从这里读取 .NET 程序集和 EAI 字段对照表（`EAI\XML\RsXml`），从不写入。必须是带盘符的绝对路径，不能是盘符根，不能与 `<root>` 相同或互相包含 |

**写入分级、只读与权限评估**

| 键 | 必填 | 缺省 | 说明 |
| --- | --- | --- | --- |
| `enableReplicatedWrites` | 否 | `false` | 第二级写入（复现 U8 界面 SQL 的写入，清单见 [已知限制](limitations.md)「写入分级」）的总开关，只能写 `true` / `false`。为 `false` 时第二级写入（含预演）一律返回 403 `feature_disabled`，即使账套在 `testAccounts` 里；一般在登录 U8 之前返回，`arap/voucher/delete` 取消处理凭证时在登录后、写入前返回（见第 3 节「第二级写入」）。为 `true` 时仍只对 `testAccounts` 里的账套开放。健康检查的 `replicated_writes`、`meta` 的 `features.replicated_writes` 报告该值；`--check-config` 打印该值 |
| `testAccounts` | 否 | `[]` | 测试账套（三位数字）。`enableReplicatedWrites` 为 `true` 时，第二级写入只对这些账套开放，其他账套返回 403 `test_account_only`（判定时机同上）。只放测试账套，**不要放正式账套**。名单中的账套还须在 `allowedAccounts` 里，否则 `--check-config` 和启动时给出警告；名单不为空而 `enableReplicatedWrites` 不是 `true` 时同样警告（第二级写入仍全部关闭）。`--check-config` 打印该键 |
| `readOnlyAccounts` | 否 | `[]` | 只读账套（三位数字）。名单中的账套只开放读取，每条写路由（含预演和带幂等键的重发）一律在解密口令、登录 U8 之前返回 403 `account_read_only`「该账套只开放读取」，出队后登录前再检查一次。写入策略放行、账套在 `testAccounts` 里都不能解除。检查位于 `allowedAccounts` 之后（名单外的账套仍是 `account_not_allowed`）；审计行 `policy` 记 `account_read_only`；读路由不受影响。可以先于 `allowedAccounts` 配置。健康检查和 `meta` 列出 `read_only_accounts`；`--check-config` 打印该键。详见第 3 节「只读账套」 |
| `permEvaluateOperators` | 否 | `[]` | 允许调用 `perm/evaluate`（查询其他操作员的有效权限）的 U8 操作员编码，每项 1 到 20 个字符，不含空白、单引号、分号，不区分大小写。为空时一个都不放行，账套主管也不例外；名单外的调用操作员在登录 U8 之前返回 403 `no_permission`。`perm/snapshot`（查询本人权限）不受影响。建议只配置专门的系统后台身份，正式账套留空。`--check-config` 打印该键。见 [接口参考](api-reference.md)「操作员权限」 |
| `writePolicyFile` | 否 | 无 | 写入策略文件的路径，相对路径按 `<root>` 解析，必须位于 `<root>` 之下。不配置时不启用写入策略；配置后必须指向一份有效策略，文件不存在或无效时所有写入（含预演）返回 503 `write_policy_unavailable`，读路由不受影响。策略文件改动不必重启（按内容哈希每 `reloadSeconds` 秒重读），增加或修改这个键须重启。格式见第 3 节「写入策略」。健康检查多出 `write_policy`；`--check-config` 打印路径和状态，并在 `license.maxConcurrentLogins` 小于 2、或开启 `loginReuse` 而该值不大于 `staWorkers` 时警告 |

**线程、队列与超时**

| 键 | 必填 | 缺省 | 说明 |
| --- | --- | --- | --- |
| `staWorkers` | 否 | 4 | 写线程数，1 到 8 |
| `readWorkers` | 否 | 4 | 读线程数，1 到 16 |
| `queueCap` | 否 | 32 | 写队列、读队列各自的上限，1 到 256 |
| `authCacheSeconds` | 否 | 600 | 登录缓存有效秒数，0 到 3600，0 关闭 |
| `iaCommandSeconds` | 否 | 900 | 长时操作的请求级时间预算（秒），300 到 7200，从任务开始执行时起算。适用于 `ia/post`、`ia/period_end`，以及 `periods/close` 中有数据月份的存货核算结账、取消结账。说明见下文「长时操作」 |
| `serializeWrites` | 否 | `true` | 写闸门。经 U8 组件的写入（单据新增、修改、删除、审核、关闭、生单、锁定，审批流操作，总账凭证写入，档案写入）在单据锁之外再持闸门键：`true` 为全局键 `u8:write`，同一时刻只执行一笔写入；`"account"` 为按账套的键 `u8:write:<账套>`，同一账套一次一笔、不同账套并行（限制见第 3 节「按账套串行写入」）；`false` 只按单据锁排程。读路由不受影响。开启后写入在桥上排队，超过 75 秒排队期限按原样返回。只能写 `true`、`false` 或 `"account"`；`--check-config` 打印该值。见 [架构](architecture.md)「写闸门」 |
| `templateCache` | 否 | `true` | 空白模板缓存开关，只能写 `true` / `false`。见下文「空白模板缓存」 |
| `mobilePush` | 否 | `false` | 经本服务审批时是否推送 U8 移动审批（友空间），只能写 `true` / `false`。见下文「移动审批推送」 |

**登录复用**

| 键 | 必填 | 缺省 | 说明 |
| --- | --- | --- | --- |
| `loginReuse` | 否 | `false` | 登录复用，只能写 `true` / `false`。见下文「登录复用」 |
| `loginReuseOffHours` | 否 | 无 | 登录复用的停用时段，例如 `{"days": "1-5", "start": "09:00", "end": "18:00"}`。见下文「登录复用」 |

**许可点数与审批流孤儿任务**

| 键 | 必填 | 缺省 | 说明 |
| --- | --- | --- | --- |
| `licenseRetries` | 否 | 2 | U8 回「加密点数已饱和」或登录状态不是 0 时的登录重试次数，0 到 5，0 不重试。等待表固定为 3 秒、8 秒，写 3 到 5 也只重试 2 次。连同各次登录本身总共不超过 20 秒（下一次等待加上次登录用时会超出时不再重试）。任何一次见过饱和、最后一次仍是这两种暂时性失败时，返回 503 `u8_license_full` |
| `licenseSampleMinutes` | 否 | 5 | 许可采样间隔（分钟），0 到 60，0 关闭采样。采样不为此登录 U8 |
| `licenseLimits` | 否 | 无 | 读不到 U8 许可总数时的备用总数。对象，键是两位大写字母的子系统号，值 1 到 9999，如 `{"SA":99,"PU":99}` |
| `licenseWarnFree` | 否 | 1 | 某子系统登记的工作站数 ≥ 总数减此数时视为「接近用满」（`near`），0 到 50 |
| `licenseServer` | 否 | 空 | 加密服务器（许可服务器）的计算机名或 IP，只含字母、数字和 `.` `-` `:`，不超过 80 个字符。留空时读取 `<u8Home>\AppServer\UFSoft.U8.Framework.Login.BO.config` 中 `U8.AA.AppServerConfig.RightServerName` 的值（即 U8 自身使用的地址），一般不必配置 |
| `licenseLeases` | 否 | 编译含读取实现时 `true`，否则 `false` | 是否经 U8 自带的本机许可客户端库读取加密服务器的点数使用情况。`false` 时桥从不调用该库，许可采样只用 `UA_TaskLog` 统计（`license_source` 为 `tasklog`）；该库出现卡住、访问冲突等异常时用它关闭。只能写 `true` / `false`。点数租约读取的实现不在本源码树中；没有实现时只用 `UA_TaskLog`：写 `true` 也一样，`--check-config` 显示「此版本不含点数租约读取」，许可总数只取 `licenseLimits` |
| `cleanOrphanTasks` | 否 | `false` | 审批流操作后清理 U8 质量管理终审在本机留下的孤儿任务行，只能写 `true` / `false`。见下文「审批流孤儿任务」 |

最小示例（地址用文档保留段；`999` 是 U8 的演示账套号，换成你的测试账套）：

```json
{
  "listenPrefix": "http://+:18089/u8co/",
  "u8Server": "198.51.100.10",
  "allowedAccounts": ["999"],
  "allowedClients": ["192.0.2.20"]
}
```

白名单为空或 `u8Home` 不存在时服务仍能启动，但 `--check-config` 和启动时给出警告。

**长时操作。** `iaCommandSeconds` 控制存货核算脚本：每个脚本的命令超时取「本值」与「预算剩余减 120 秒回滚预留」中的较小者，剩余不足 30 秒时不再开始新的脚本（409，已回滚）；超时返回 503 `ia_timeout`（已回滚，可重试）。这几类请求（`periods/close` 为 `module` 是 `ia` 或 `through` 为 `true` 时）桥的 HTTP 等待为本值加 60 秒（缺省 960 秒），排队容忍度仍是 45 秒；卡死检测平时为 3 分钟，脚本执行期间放宽到脚本超时的两倍再加 120 秒，脚本结束后（提交、回读或回滚）按已执行脚本总时长加 120 秒（至少 3 分钟）。回滚超过 HTTP 等待时调用方收到 504 `outcome_unknown`，实际没有写入。调用方对这几条路由的超时必须大于该等待：API 用 `U8CO_BRIDGE_LONG_TIMEOUT`（缺省 1000 秒），Python 客户端用 `U8CoClient` 的 `long_timeout`（缺省 1000 秒），MCP 用 `long_timeout_s`；调大本值时一并调整。

**空白模板缓存（`templateCache`）。** 新建采购订单、调拨单、采购入库、材料出库、产成品入库时，桥把 `where 1=2` 取得的纯 schema 空白 DOM 缓存在内存中，每次使用前核对表结构指纹和 ADO/MSXML 组件指纹，最长 30 分钟，重启即清空。U8 打补丁、改视图后不必手动清缓存；需要完全排除缓存影响时写 `"templateCache": false` 并重启服务。见 [架构](architecture.md)「空白模板缓存」。

**移动审批推送（`mobilePush`）。** 缺省 `false`：桥不加载审批引擎的 `YonYou.U8.MA.*` 程序集，经本服务审批的单据不会出现在 U8 移动审批中。写 `true` 并重启服务后，桥从 `<u8Home>\U8AuditWebSite\bin` 和 `<u8Home>\U8AuditWebSite\bin\Query` 加载这些程序集，审批引擎照常推送；此时 `u8Home` 下没有 `U8AuditWebSite\bin` 会在 `--check-config` 和启动时给出警告。`meta` 的 `features.mobile_push` 报告实际生效值。见 [架构](architecture.md)「程序集解析和崩溃防护」。

**登录复用（`loginReuse`、`loginReuseOffHours`）。**

- 开启后，每个写线程（读线程不保留）把用过的 U8 登录对象留在本线程，同一账套、年度、操作员、口令、子系统、登录日期的下一笔请求直接复用，不再新建 `clsLogin`、不再登录。
- 每个写线程最多保留 16 个；闲置 60 秒作废（线程空闲时也按时关闭）；从真正登录起最长使用 600 秒，到期重新登录（口令修改、操作员停用最迟此时生效）。复用前确认登录状态仍为 0、操作员姓名可读，否则关闭重登。
- 请求出现任何错误、请求连接上仍有未结束的事务时，本次登录不保留；登录子系统为质量管理（QM）、应收（AR）、应付（AP）的请求一律不复用，用完即关。
- 许可影响：保留的登录在作废前一直占用本机该子系统的 1 个许可点数，`UA_TaskLog` 中的登记行也一直存在；许可紧张时人工用户可能因此无法登录。
- 同一操作员不能在同一台机器上同时保持两个登录：某个写线程保留着操作员 A 的登录时，别的线程再以 A 登录，U8 回「登录状态不是 0」，按许可重试表重试仍失败（实测：事件服务与集成方的档案同步都用同一操作员时持续失败）。开启复用时，让并发的调用方使用不同的操作员，或保持关闭。
- 用途是减少登录次数、缩短每笔请求的登录耗时。先在测试账套上对比核对，再在生产环境开启。
- 健康检查多出 `login_reuse`（配置的开关）、`login_reuse_active`（此刻是否真的在复用）、`logins_opened`、`logins_reused`、`logins_closed`。
- `loginReuseOffHours` 设置停用时段：`loginReuse` 开启时，时段内不复用登录，每笔请求照旧新建登录、用完即关；时段外照常复用。`days` 是 ISO 星期（1 为星期一，7 为星期日），写成 `1-5`、`1,3,5` 或 `1-3,6`；`start`、`end` 为 `HH:MM`，含 `start`、不含 `end`，`end` 必须晚于 `start`（不支持跨午夜）；按桥所在机器的本地时间计算。进入时段时，写线程已保留的登录在该线程下一次取用或空闲清理（约 0.5 秒）时全部关闭，最迟到原有效期。每次取用、放回都按当时时间判断，不必重启。缺少或写 `null` 表示不设时段；`loginReuse` 为 `false` 时该键不起作用。写错（不是对象、未知字段、星期或时刻不合法、`end` 不晚于 `start`）时服务不启动。`--check-config` 打印如「开（工作时间 周一至周五 09:00–18:00 关闭）」的取值。
- 见 [架构](architecture.md)「登录复用」。

**许可点数（`license*`）。**

- U8 许可按「工作站 × 子系统」占点：桥所在机器每个子系统只占 1 点，但点数被其他工作站用满时登录会失败。桥按 `licenseRetries` 重试，仍失败返回 503 `u8_license_full`（不占幂等键，可稍后重发），并在审计日志写 `license_full` 事件；该子系统之后第一次登录成功时写 `license_ok`。
- `licenseSampleMinutes` 不为 0 时，后台每隔该分钟数经 U8 自带的本机许可客户端库向加密服务器（`licenseServer`）读取实时点数使用情况，按产品包计算已用和总数（许可总数和产品包每小时读一次），与 U8「许可管理」一致。读不到或超时则本轮回落 `UA_TaskLog`。`licenseLeases` 为 `false` 时不读租约。
- 读不到租约（库不在、服务器不通、回复不对、产品包为空、超时）时回落为按子系统统计 `UFSYSTEM..UA_TaskLog` 中登记的工作站数，许可总数每 6 小时经 U8 客户端库读取一次（在单独的 AppDomain 中读、最多等 20 秒，读完卸载；读不到时保留上一次的总数，从未读到时用 `licenseLimits`）。回落后尚无人登录时，第一次登录后约 15 秒即采样。`UA_TaskLog` 统计会少算，见 [已知限制](limitations.md)。
- 健康检查 `GET /u8co/v1/health` 多出：`license`（`ok` / `near` / `full` / `unknown`；`full` 指 15 分钟内见过饱和，`unknown` 指从未采样也未见过饱和，或从租约回落后尚未采到 `UA_TaskLog`；只看桥用到的子系统 SA、AS、PU、ST、QM、AR、AP、GL、MO、BO 和 `licenseLimits` 的键）、`license_detail`（按子系统的 `used`、`limit`、`full_24h`，只有数字）、`license_source`（`leases` / `tasklog`）、`license_packs`（全部产品包的数字）。HTTP 状态仍为 200。租约数字超过 3 个采样间隔未更新时不再采用，按 `tasklog` 显示。
- 审计事件：总体状态变化写 `license_near` 或 `license_state`，数字来源变化写 `license_source`（带回落原因，不含地址和身份信息）。
- 配置了 `sql.json` 时，专用登录须能读 `UFSYSTEM..UA_TaskLog`，否则采样失败（只影响采样，写 `license_sample_error`）。
- `--check-config` 打印这些键（`licenseServer` 为空时打印从应用服务器配置读到的地址）。

**审批流孤儿任务（`cleanOrphanTasks`）。**

- 开启后，每次调到 U8 的审批流操作（提交、撤销提交、同意、不同意、退回、弃审、重新提交，含 U8 拒绝）之后，删除 U8 质量管理终审在本机留下的 QM 孤儿任务行（`UA_TaskLog` / `ua_Task_Common`）。
- 只删除同时满足以下条件的行：本机 `cStation`、`cSub_Id` 为 `QM`、调用前快照中没有、登录时间在调用前与 U8 返回后两次数据库时间之间；`ua_Task_Common` 有该任务的行时，操作员和账套还必须属于本次请求（否则不删并审计 `orphan_tasks_skipped`，原因 `foreign_owner`）；同一任务还有其他子系统的行时不删。
- 开启后桥的审批流操作串行执行：后到的在调用 U8 之前排队，最多等 60 秒且不超过请求期限减 30 秒，等不到返回 503 `busy_timeout`（未调用 U8，可重发）。
- 服务器控制台上同一时间窗口内、同一操作员和账套的 QM 登录无法区分，见 [U8 行为说明](u8-notes.md)。
- 这是对 `UFSystem` 的写操作：配置了 `sql.json` 时专用登录须能读写 `UFSYSTEM..UA_TaskLog` 和 `UFSYSTEM..ua_Task_Common`，否则每次审批流操作都写 `orphan_tasks_failed`。`--check-config` 打印该值。

### secret.hex

32 字节随机数，64 位小写十六进制，一行。安装脚本在文件不存在时生成（先写入 `<root>` 中的临时文件，再改名）；已有文件不改内容。API 服务和 Python 客户端使用同一份密钥（协议见 [架构](architecture.md)）。更换密钥须两端同时更换，并重启桥。

### sql.json（可选）

缺省情况下，桥使用 U8 登录对象给出的数据库连接串（`UfDbName`），与 U8 客户端完全相同；U8 自己的连接通常是数据库的特权登录。需要让桥的直接 SQL 使用专用登录时，写 `sql.json`：

```json
{"user": "u8co_sql", "password": "口令里不能有分号"}
```

- `user` 不能是 `sa`，用户名和口令都不能含分号。未知键导致启动失败。
- 桥沿用登录对象连接串中的服务器和库名，只把凭据换成这里的。
- 业务组件审核时会跨库访问 `UFSystem`，权限过低的登录可能使 U8 内部失败。先在测试账套上验证。
- 安装脚本不创建该文件；文件已存在时，安装脚本把权限收紧为 SYSTEM 和 Administrators。

### u8co-bridge.exe.config

CLR 在进程启动时读取 exe 旁的这份文件（不是 `config.json`）：`supportedRuntime` 为 `v4.0`，sku `.NETFramework,Version=v4.8`；`runtime` 中 `legacyUnhandledExceptionPolicy enabled="1"`。不要删改这一项，原因见 [架构](architecture.md)「程序集解析和崩溃防护」。

### 命令行

| 参数 | 作用 |
| --- | --- |
| （无） | 作为 Windows 服务运行 |
| `--console` | 在控制台前台运行，Ctrl+C 退出。仍需 urlacl、配置和密钥 |
| `--check-config` | 加载配置和密钥并执行全部检查，0 成功，1 失败 |
| `--check-signatures [--strict]` | 核对本机 U8 业务组件的运行时类型库签名（方法名、参数个数、按引用方式），不需要运行目录和配置。加 `--strict` 时有不匹配即以退出码 2 结束 |
| `--selftest` | 核对密钥派生、口令解密和签名的测试向量，并运行桥内部各模块的自检；不连 U8、不读 `secret.hex` |
| `--root <目录>` | 运行目录 |
| `--service-name <名字>` | 服务名，缺省 `u8co`。只含字母、数字、`.`、`_`、`-`，以字母或数字开头，最长 64 |

用法：`u8co-bridge.exe [--console|--selftest|--check-config|--check-signatures [--strict]] [--root <目录>] [--service-name <名字>]`。

### 安装脚本参数

`co/bridge/install.ps1`（64 位 PowerShell 7，管理员）：

| 参数 | 缺省 | 说明 |
| --- | --- | --- |
| `-Root` | `%ProgramData%\U8Co\u8co` | 运行目录 |
| `-ServiceName` | `u8co` | 服务名。防火墙规则同名（分组 `U8Co`），显示名「U8 CO Bridge (服务名)」。同名服务已存在时，其可执行文件必须是 `<root>\bin\u8co-bridge.exe`，否则脚本停止 |
| `-U8Server` | 无 | 首次安装必填，写入 `u8Server` |
| `-AllowedClients` | 无 | 首次安装必填，写入 `allowedClients`，同时作为防火墙规则的远程地址。逗号或空白分隔 |
| `-AllowedAccounts` | 空 | 写入 `allowedAccounts`。不给时为空名单，服务能启动但拒绝所有账套（脚本会警告） |
| `-TestAccounts` | 空 | 写入 `testAccounts`，逗号或空白分隔，每项三位数字。不给时为空名单。第二级写入另需 `-EnableReplicatedWrites`（或在 `config.json` 中设置 `enableReplicatedWrites`） |
| `-Port` | 18089 | 与 `-ListenHost` 组成 `listenPrefix` |
| `-ListenHost` | `+` | 同上 |
| `-MobilePush` | 不给 | 开关参数。给出时在新 `config.json` 中写 `"mobilePush": true`，否则写 `false` |
| `-EnableReplicatedWrites` | 不给 | 开关参数。给出时在新 `config.json` 中写 `"enableReplicatedWrites": true`（第二级写入总开关，仍只对 `testAccounts` 开放）；不给时不写这个键，等同 `false` |
| `-U8Home` | `C:\U8SOFT` | 写入 `u8Home`，按实际安装目录填写。脚本拒绝在该目录（以及它所在的非系统盘）写文件。已有 `config.json` 时以其中的 `u8Home` 为准，给出不一致的值时脚本停止 |

- 除 `-Root`、`-ServiceName`、`-U8Home` 外，这些参数只在 `config.json` 尚不存在时生效。已有 `config.json` 时脚本不覆盖，并逐个提示未生效的参数；修改配置请直接编辑 `config.json` 后重跑脚本。
- `allowedClients` 为空时脚本不建防火墙规则，端口不开放。
- 脚本（经 `co/SafePath.ps1`）拒绝写 `u8Home` 之下的路径；U8 装在非系统盘上时，整块盘都拒绝写入。
- `uninstall.ps1` 的 `-Root`、`-ServiceName` 须与安装时相同；`-Port` 只在运行目录里没有 `config.json` 时用来拼出要删除的 urlacl；`-U8Home` 同样以 `config.json` 的 `u8Home` 为准。卸载只删除可执行文件为 `<root>\bin\u8co-bridge.exe` 的同名服务和分组为 `U8Co` 的同名防火墙规则。

## 2. API 服务

全部配置从环境变量读取，样例在 `api/.env.example`。共享密钥只从文件读取。格式不对的变量使进程启动失败；例外是桥地址、密钥缺失或不合法，以及 `U8CO_BRIDGE_TIMEOUT`、`U8CO_BRIDGE_LONG_TIMEOUT`、`U8CO_CONCURRENCY`、`U8CO_CALLER_CONCURRENCY`、`U8CO_RPM` 不是整数（按 0 处理）或超出范围：这些情况进程照常启动，`/v1/co/*` 返回 503 `unavailable`，`/healthz` 报告 `"configured": false`。

### 环境变量

**桥与超时**

| 变量 | 缺省 | 说明 |
| --- | --- | --- |
| `U8CO_BRIDGE_URL` | 无，必须设置 | 桥的前缀，例如 `http://192.0.2.10:18089/u8co`。只收 `http` / `https`，路径必须是 `/u8co` |
| `U8CO_BRIDGE_SECRET_FILE` | 无，必须设置 | 共享密钥文件，内容为 64 位小写十六进制。普通文件对属组或其他用户有任何权限（不是 0400 / 0600 这类）时拒绝使用，记一条警告并按未配置处理；`/run/secrets/` 下（docker secrets）不查权限 |
| `U8CO_BRIDGE_TIMEOUT` | 90 | 读桥的超时（秒），至少 80（桥自身最多等 75 秒） |
| `U8CO_BRIDGE_LONG_TIMEOUT` | 1000 | 长时操作读桥的超时（秒）：`ia/post`、`ia/period_end`，以及 `module` 为 `ia` 或 `through` 为 `true` 的 `periods/close`。桥对它们等 `iaCommandSeconds` + 60 秒（缺省 960），本值须比之更大。不写时取 1000 与 `U8CO_BRIDGE_TIMEOUT` 中的较大者；写了就不能小于 `U8CO_BRIDGE_TIMEOUT`，否则按未配置处理。API 前的反向代理对这几条路径的读超时也须放长 |
| `U8CO_BRIDGE_ROUTES_FILE` | 无 | 可选，按账套分流的桥配置文件（JSON）。文件有任何问题时进程启动失败。见下文「按账套分流的桥」 |
| `U8CO_SHUTDOWN_GRACE` | 100 | 停机时等待在途请求的秒数（uvicorn 的 `timeout_graceful_shutdown`；只在用 `u8co-api` 命令启动时生效）。要让在途的长时操作完成，设为不小于 `U8CO_BRIDGE_LONG_TIMEOUT`，并同步调整容器的停止宽限（compose 的 `stop_grace_period`）。不是正整数时按 100 并记警告 |

**账套与写入控制**

| 变量 | 缺省 | 说明 |
| --- | --- | --- |
| `U8CO_ACCOUNTS` | 空 | 允许的账套，逗号分隔的三位数字。为空时所有业务调用返回 403 `account_not_allowed` |
| `U8CO_READONLY_ACCOUNTS` | 空 | 可选，只开放读取的账套，写法同 `U8CO_ACCOUNTS`，每个都必须同时在 `U8CO_ACCOUNTS` 中（否则启动失败）。这些账套的写路由（含 `dry_run` 预演、带 `Idempotency-Key` 的重试、`intercompany/generate_buyer` 的买方）一律返回 403 `account_read_only`「该账套只开放读取」，在调桥和写入策略之前判定，不重放幂等结果，不带 `Retry-After`；读路由、经营管理查询不受影响。与桥的 `readOnlyAccounts` 相互独立，建议两侧同时配置。`/v1/co/health` 的 `api_read_only_accounts` 列出本值（未配置时省略），`read_only_accounts` 是桥报告的列表 |
| `U8CO_WRITE_POLICY_FILE` | 无 | 可选，写入策略文件，与桥的 `writePolicyFile` 使用同一份 JSON（第 3 节）。API 在调桥之前按它判定冻结、时段、放行规则、操作员、行数和金额上限（限额和许可保护只在桥上）。设置后文件不存在或无效时所有写入返回 503 `write_policy_unavailable`；无效的新内容不替换上一份有效内容。文件不超过 1 MiB，判定或健康检查时距上次读取超过 `reloadSeconds` 即按内容哈希重读，不必重启。时段按 `U8CO_TIMEZONE` |
| `U8CO_ENABLED` | `1` | 设为 `0` 时 `/v1/co/*` 返回 404，用作总开关 |

**限流**

| 变量 | 缺省 | 说明 |
| --- | --- | --- |
| `U8CO_RPM` | 30 | 每个调用方每分钟的大约次数。调用方按「信任项 `name` + 客户端」区分，客户端依次取令牌的 `azp`、`client_id`、`sub`。健康检查不计入 |
| `U8CO_CONCURRENCY` | 8 | 全体同时在途请求的上限 |
| `U8CO_CALLER_CONCURRENCY` | 4 | 每个调用方（同上）同时在途请求的上限 |

**认证与审计**

| 变量 | 缺省 | 说明 |
| --- | --- | --- |
| `U8CO_TRUST_FILE` | `/config/u8co-trust.json` | 信任配置文件，见下文「OIDC 信任配置」。使用缺省路径而文件不存在时跳过；显式设置但文件不存在时启动失败 |
| `U8CO_OIDC_ISSUER`、`U8CO_OIDC_AUDIENCE`、`U8CO_OIDC_JWKS_URL`、`U8CO_OIDC_NAME`（缺省 `default`）、`U8CO_OIDC_ALGORITHMS`、`U8CO_OIDC_WRITE_CLAIM`、`U8CO_OIDC_READ_CLAIM`、`U8CO_OIDC_WRITE_SCOPE`、`U8CO_OIDC_READ_SCOPE`、`U8CO_OIDC_ACCOUNTS_CLAIM`、`U8CO_OIDC_MGMT_CLAIM`、`U8CO_OIDC_MGMT_SCOPE`、`U8CO_OIDC_ALLOW_INSECURE_HTTP` | 无 | 单发行者的简写：设置了 `ISSUER` 和 `AUDIENCE` 即相当于多一条信任项，与信任文件中的项合并。其余变量对应信任项的同名键（`ALLOW_INSECURE_HTTP` 取 `1` / `true` / `yes` / `on` 为真） |
| `U8CO_JWT_LEEWAY` | 60 | 校验 `exp` / `nbf` 的时钟偏差（秒），0 到 300 |
| `U8CO_USER_HEADER` | `X-U8co-User` | 可选的终端用户标识头（UUID），只记审计，不参与授权。只对信任项设置了 `on_behalf_header: true` 的调用方生效；其余令牌有 `sub` 时审计的终端用户就是 `sub`，该头被忽略 |
| `U8CO_AUDIT_LOG` | `stdout` | 审计输出：`stdout`、`stderr`、`off`，或一个绝对路径 |

**其他**

| 变量 | 缺省 | 说明 |
| --- | --- | --- |
| `U8CO_TIMEZONE` | `+08:00` | 固定时区偏移（U8 服务器所在时区），格式 `+HH:MM` / `-HH:MM`。用于计算缺省登录日期 `date`；API 侧写入策略的时段和禁写日期也按它判断，不看容器的 `TZ` |
| `U8CO_IC_MAP_FILE` | 无 | 可选，公司间对照文件，见下文「公司间对照」。不设置时公司间接口和多账套合并返回 404 `ic_not_configured`；文件有任何问题时进程启动失败 |
| `U8CO_MGMT_LINES_FILE` | 无 | 可选，经营管理利润表的行定义，见下文「利润表行定义」。不设置时使用内置的通用定义；文件有任何问题时进程启动失败 |
| `U8CO_API_HOST`、`U8CO_API_PORT` | `0.0.0.0`、`8080` | `u8co-api` 启动命令的监听地址 |

以下属于桥协议，不是配置：桥的路径 `/u8co`，密钥派生标签 `u8co/v1/mac`、`u8co/v1/enc`，请求头 `X-U8co-Ts`、`X-U8co-Nonce`、`X-U8co-Sig`（见 [架构](architecture.md)）。

### 按账套分流的桥

同一台 U8 服务器上可以安装多个桥实例（各自的服务名、端口、`config.json` 和 `secret.hex`），例如测试账套与正式账套分开。`U8CO_BRIDGE_ROUTES_FILE` 指定哪些账套走哪个桥；未列出的账套和不带账套的调用（`/v1/co/health`、`/v1/co/meta`）走缺省桥 `U8CO_BRIDGE_URL`。

```json
{
  "routes": [
    {
      "accounts": ["998"],
      "url": "http://203.0.113.10:18100/u8co",
      "secret_file": "/run/secrets/u8co_bridge_secret_998",
      "timeout": 90
    },
    {
      "accounts": ["802", "803"],
      "url": "http://203.0.113.10:18101/u8co",
      "secret_file": "/etc/u8co/bridge-802.secret"
    }
  ]
}
```

| 键 | 必填 | 说明 |
| --- | --- | --- |
| `routes` | 是 | 顶层只有这一个键，值为数组 |
| `accounts` | 是 | 该桥服务的账套号，三位数字的字符串，非空。一个账套只能出现在一条路由中 |
| `url` | 是 | 桥的前缀，规则同 `U8CO_BRIDGE_URL`：`http(s)://主机[:端口]/u8co` |
| `secret_file` | 是 | 该桥的共享密钥文件，格式和权限要求同 `U8CO_BRIDGE_SECRET_FILE` |
| `timeout` | 否 | 读桥的超时（秒），不小于 80；不写时沿用 `U8CO_BRIDGE_TIMEOUT`。长时操作的超时取 `U8CO_BRIDGE_LONG_TIMEOUT` 与本值中的较大者 |

- 校验是严格的：有不认识的键、缺必填键、账套号格式不对或重复、地址不合法、密钥文件读不到、内容不对或权限过宽、`timeout` 不合法时，进程启动失败，错误信息指出路由序号（`routes[序号]`，从 0 起），不含密钥内容。不会悄悄退回缺省桥。文件不超过 64 KiB。
- 分流不放宽授权：路由中的每个账套必须同时在 `U8CO_ACCOUNTS` 中（否则启动失败）；令牌配置了账套声明时声明中也须有该账套；分流桥自己的 `allowedAccounts` 同样须包含它。
- 缺省桥未配置好（`/healthz` 的 `configured` 为 `false`）时整个 `/v1/co/*` 返回 503，分流桥也不使用。
- 每个桥使用独立的共享密钥，不要多个桥共用一个密钥文件。密钥文件属主设为运行用户、权限 0600 或 0400，或挂载为 docker secrets。分流文件本身不含密钥，但也不应让其他用户可写。
- 幂等记录按桥隔离：`Idempotency-Key` 重放和 `idempotency/get` 都落到服务该账套的桥。把账套从一个桥迁到另一个桥后，旧记录查不到，带同一个键重试会被当作新请求再执行一次；迁移前先确认没有待重试的写请求。不同桥上的两个账套使用同一个键不会被识别为重复，调用方不要跨账套复用幂等键。
- `/v1/co/meta` 不带账套，总是读缺省桥，反映缺省桥那个版本的能力；分流桥版本不同时，以 `/v1/co/health` 的 `routes[].version` 为准。
- 审计行的 `bridge` 字段记录服务该次调用的桥：`default` 或 `routes[序号]`；未访问桥的请求为 `null`。
- `/v1/co/health` 顶层只描述缺省桥；配置了分流时多一个 `routes` 数组，每个分流桥一项：`route`、`accounts`、`ok`，可用时带 `version`、`write_policy`、`replicated_writes`，不可用时带错误码 `error`。分流桥与缺省桥并行探测，每个读取超时 8 秒（建连 5 秒），合计最多 10 秒，超时记 `ok: false`、`error: "unavailable"`；某个分流桥不可用不影响整体状态码。无需令牌的 `/healthz` 只多一个 `bridge_routes`（分流桥个数），不列账套号。

### 公司间对照

`U8CO_IC_MAP_FILE` 指向一份 JSON，说明哪些账套属于同一组公司、各公司在对方账套中的客户 / 供应商编码、哪些存货和科目是「同一个」，以及合并时的抵销规则。公司间对账、多账套汇总和合并、经营管理查询的合并都依赖它（见 [接口参考](api-reference.md)「公司间接口」「经营管理查询」）。文件含站点的往来单位编码和科目，应放在仓库之外，不要提交。示例（账套和编码均为占位）：

```json
{
  "groups": [
    {
      "id": "grp1",
      "accounts": ["801", "802", "803"],
      "names": { "801": "甲公司", "802": "乙公司", "803": "丙公司" },
      "as_customer": { "801": { "802": "C900001", "803": "C900002" }, "802": { "801": "C900003" } },
      "as_vendor": { "802": { "801": "S900001" }, "803": { "801": "S900002" } },
      "inventory": [
        { "id": "inv1", "codes": { "801": "A901", "802": "A901" } },
        { "id": "inv2", "codes": { "801": "INV0021", "803": "INV0031" }, "match": "qty_date" }
      ],
      "gl": [
        { "logical": "ic_ar", "codes": { "801": "112201" } },
        { "logical": "ic_ap", "codes": { "802": "220201" } }
      ],
      "elim": [
        { "rule": "ar_ap", "pairs": [ { "ar": ["801", "ic_ar", "C900001"], "ap": ["802", "ic_ap", "S900001"] } ] },
        { "rule": "rev_cogs", "seller": "801", "buyer": "802", "revenue_source": "sales_to_customer", "cost_source": "ia_to_customer" }
      ]
    }
  ]
}
```

| 键 | 说明 |
| --- | --- |
| `groups` | 顶层只有这一个键，非空数组。组 `id` 不能重复，一个账套只能属于一个组。请求中的账套必须都在同一组（否则 400 `ic_group_mismatch`） |
| `id` | 组名，`[A-Za-z0-9_-]`，1 到 40 个字符 |
| `accounts` | 本组的账套，至少 2 个，三位数字，不重复 |
| `names` | 可选，`{账套: 公司名}`（1 到 60 个字符），用于响应的 `accounts[].name`、汇总的 `totals[].name`；未写时用账套号 |
| `as_customer` / `as_vendor` | 可选，`{所在账套 x: {公司账套 y: 编码}}`：账套 x 中代表公司 y 的客户（供应商）编码。x、y 都在本组且不相同，同一个 x 下编码不能重复 |
| `inventory` | 可选，`[{id, codes, match}]`：`codes` 是各账套中「同一存货」的编码；`match` 为 `code`（缺省，对账时同存货、同数量按日期窗口配对）或 `qty_date`（只认数量和同一日期）。`id` 不能重复，同一账套的同一编码只能出现一次 |
| `gl` | 可选，`[{logical, codes}]`：逻辑科目（`[a-z0-9_]`，1 到 40 个字符）在各账套的科目编码（1 到 20 位数字）。按编码精确匹配，不按前缀 |
| `elim` | 可选，合并抵销规则。`{"rule": "ar_ap", "pairs": [{ar, ap}]}`：`ar`、`ap` 各为 `[账套, 逻辑科目, 往来单位编码]`，逻辑科目须在 `gl` 中有该账套的编码，往来单位编码须与 `as_customer` / `as_vendor` 一致，用于 `reports/consolidation` 的往来抵销。`{"rule": "rev_cogs", seller, buyer, revenue_source, cost_source}`（全部必填）：经营管理利润表合并时从营业收入、营业成本中减去 seller 卖给 buyer 的部分；`revenue_source` 为 `sales_to_customer`（seller 按 buyer 这个客户的销售统计）或 `gl_revenue_all`（seller 的全部营业收入），`cost_source` 为 `ia_to_customer`（按客户的销售成本）或 `gl_cogs_all`（全部营业成本），按客户取数时 `as_customer[seller][buyer]` 必须存在；同一对 seller → buyer 只能有一条 |

- 校验是严格的：任何一层有不认识的键、缺必填键、编码格式不对或重复，进程都启动失败（`U8CO_IC_MAP_FILE: <原因>`）。文件不超过 256 KiB，UTF-8。不与 `U8CO_ACCOUNTS` 交叉校验：对照中有、白名单中没有的账套照样不能调用。
- 对照只说明「谁是谁」，不放宽授权：公司间和经营管理接口要求请求中的每个账套都通过账套白名单和令牌的账套声明（有一个不通过即整体 403）。
- 修改后须重启 API。

### 利润表行定义

经营管理利润表（`/v1/co/mgmt/pnl`）把损益科目的发生额归入利润表各行。不设置 `U8CO_MGMT_LINES_FILE` 时使用内置的通用定义，按 2007 年企业会计准则的一级科目前缀：

| `id` | 行 | 科目前缀 | 方向 |
| --- | --- | --- | --- |
| `revenue` | 营业收入 | 6001、6051 | 收入（贷减借） |
| `cogs` | 营业成本 | 6401、6402 | 费用（借减贷） |
| `tax` | 税金及附加 | 6403 | 费用 |
| `selling` | 销售费用 | 6601 | 费用 |
| `admin` | 管理费用 | 6602 | 费用 |
| `finance` | 财务费用 | 6603 | 费用 |
| `impairment` | 资产减值损失 | 6701、6702 | 费用 |
| `fair_value` | 公允价值变动收益 | 6101 | 收入 |
| `invest` | 投资收益 | 6111 | 收入 |
| `other_gain` | 其他收益 | 6117、6115 | 收入 |
| `nonop_in` | 营业外收入 | 6301 | 收入 |
| `nonop_out` | 营业外支出 | 6711 | 费用 |
| `income_tax` | 所得税费用 | 6801 | 费用 |

派生行：`gross_profit` 毛利 = 营业收入 − 营业成本；`operating_profit` 营业利润 = 毛利 + 公允价值变动收益 + 投资收益 + 其他收益 − 税金及附加 − 销售费用 − 管理费用 − 财务费用 − 资产减值损失；`total_profit` 利润总额 = 营业利润 + 营业外收入 − 营业外支出；`net_profit` 净利润 = 利润总额 − 所得税费用。

科目表与此不同的账套（例如自设 6699 研发费用、6698 财务费用，研发费用设在 6602 下级，或使用小企业会计准则的 5 字头科目）须在站点文件中写自己的定义：

```json
{
  "version": 1,
  "lines": [
    { "id": "revenue", "name": "营业收入", "codes": { "*": ["6001", "6051"] }, "sign": "income" },
    { "id": "cogs", "name": "营业成本", "codes": { "*": ["6401", "6402"] }, "sign": "expense" },
    { "id": "rd", "name": "研发费用", "codes": { "*": ["6699"], "803": ["66029901"] }, "sign": "expense" },
    { "id": "finance", "name": "财务费用", "codes": { "*": ["6698"] }, "sign": "expense" }
  ],
  "derived": [
    { "id": "gross_profit", "name": "毛利", "plus": ["revenue"], "minus": ["cogs"] },
    { "id": "net_profit", "name": "净利润", "plus": ["gross_profit"], "minus": ["rd", "finance"] }
  ]
}
```

- 顶层恰好是 `version`（`1`）、`lines`、`derived` 三个键，都必填。文件不超过 64 KiB，UTF-8；有任何问题时进程启动失败（`U8CO_MGMT_LINES_FILE: <原因>`）。
- `lines`：1 到 60 项，每项恰好 `{id, name, codes, sign}`。`id` 为 `[a-z][a-z0-9_]`（不超过 40 个字符）、不重复，必须有 `revenue` 和 `cogs`；`name` 1 到 40 个字符；`sign` 为 `income`（贷减借）或 `expense`（借减贷）；`codes` 的键为 `"*"`（各账套缺省）或三位账套号，值为科目前缀（1 到 20 位数字）列表，某账套写了自己的列表即整体替换 `"*"` 的列表。
- 科目按前缀匹配（`6001` 匹配 `600101`）。一个科目可以命中多行，每行都计入，因此一行可以是另一行的「其中」项。未命中任何行的损益科目列在响应的 `unmapped` 中并给出提醒。
- `derived`：0 到 60 项，`{id, name, plus, minus}`，`plus` 非空，只能引用前面已定义的行或派生行，按顺序计算。毛利率、净利率依赖 `gross_profit`、`net_profit` 这两个 `id`，缺少时这两个比率为 null。
- 桥按定义中最长的科目前缀决定取数粒度：都是 4 位时按一级科目（`prefix4`）取数，有更长前缀时按末级科目（`leaf`）取数。`prefix4` 假定一级科目为 4 位编码。
- 修改后须重启 API。

### OIDC 信任配置

信任文件是 JSON 数组，每项对应一个可信的（发行者、受众）。样例 `api/trust.example.json`：

```json
[
  {
    "name": "app-a",
    "issuer": "https://idp.example.com",
    "audience": "u8co-api",
    "jwks_url": "https://idp.example.com/oauth/jwks",
    "algorithms": ["RS256"],
    "write_claim": "u8co_write",
    "read_claim": "u8co_read"
  },
  {
    "name": "app-b",
    "issuer": "https://login.example.com/tenant-b/v2.0",
    "audience": "api://u8co-api",
    "write_scope": "u8co.write",
    "read_scope": "u8co.read",
    "accounts_claim": "u8co_accs",
    "accounts": ["801", "802"]
  }
]
```

| 键 | 缺省 | 说明 |
| --- | --- | --- |
| `name` | 必填 | 调用方名称，与令牌的客户端标识共同组成限流键，并记入审计的 `trust` 字段（也接受键名 `caller`） |
| `issuer` | 必填 | 令牌的 `iss`，必须是 https |
| `audience` | 必填 | 令牌的 `aud` 必须包含它 |
| `jwks_url` | 无 | 签名公钥地址，必须是 https。不写时读取 `{issuer}/.well-known/openid-configuration`，要求其中的 `issuer` 一致，再使用其 `jwks_uri`（同样必须是 https） |
| `allow_insecure_http` | `false` | 设为 JSON `true` 才允许 `jwks_url` / `jwks_uri` 使用 http。本机回环地址（`127.0.0.1`、`::1`、`localhost`）不必设置。http 上的中间人替换公钥即可伪造令牌，只在可信网络中使用 |
| `algorithms` | `["RS256"]` | 允许的签名算法，可选 RS、PS、ES 系列，不接受 HS |
| `write_claim` / `read_claim` | `u8co_write` / `u8co_read` | 写权限、只读权限的布尔声明名，值必须是 JSON `true` |
| `write_scope` / `read_scope` | 无 | 也可用 scope 授权：令牌的 `scope` 或 `scp` 含该值 |
| `mgmt_claim` / `mgmt_scope` | 无 | 经营管理查询（`/v1/co/mgmt/*`）的权限：令牌中该布尔声明为 JSON `true`，或 `scope` / `scp` 含该值。两者都不设置时，该发行者的令牌一律没有经营管理权限，写权限也不能代替（403 `mgmt_forbidden`）。`mgmt_claim` 不能与 `write_claim`、`read_claim`、`accounts_claim` 同名，`mgmt_scope` 不能与 `write_scope`、`read_scope` 相同，否则启动失败。经营管理查询能看到各账套的利润、往来、资金，只授予需要的调用方 |
| `accounts_claim` | 无 | 设置后令牌只能使用该声明中列出的账套（数组，或逗号、空格分隔的字符串；没有该声明时一个都不能用），再与 `U8CO_ACCOUNTS` 取交集。不设置时令牌不限制账套 |
| `accounts` | 无 | 可选，该信任项的静态账套上限：非空、不重复的三位数字字符串数组。实际可用账套 = `U8CO_ACCOUNTS` ∩ 令牌账套声明（配置了 `accounts_claim` 时）∩ `accounts`，令牌声明不能放宽它；单账套路由、经营管理查询和公司间多账套路由都按该结果判定（403 `account_not_allowed`）。适合为测试或机器客户端固定可用账套，防止身份提供方的映射误加正式账套 |
| `perm_evaluate` | `false` | 可选，设为 JSON `true` 才能调用 `/v1/co/perm/evaluate`（查询其他操作员的权限），令牌还须有读或写权限；令牌中的声明不能打开它。只授予专门的系统后台调用方；桥一侧另须把调用操作员列入 `permEvaluateOperators` |
| `on_behalf_header` | `false` | 可选，设为 JSON `true` 表示这是代人调用的机器调用方：审计的终端用户取 `U8CO_USER_HEADER` 头中的 UUID，令牌自身的 `sub` 另记在审计的 `sub` 字段。不设置时令牌有 `sub` 即以 `sub` 为终端用户，该头被忽略 |

- 令牌的 `iss` 和 `aud` 必须恰好命中一项。重复的（`issuer`、`audience`）和未知键都使启动失败。没有任何信任项时，所有 `/v1` 请求返回 401。修改信任配置后须重启服务。
- JWKS 按地址缓存：遇到未知 `kid` 才刷新，同一地址两次成功刷新至少间隔 300 秒，失败记忆 30 秒，响应上限 256 KiB。拉取发现文档和公钥时只跟随 https 到 https 的跳转，跳到 http 或其他协议、以及 http 来源的任何跳转都按拉取失败处理。
- 身份提供方已在令牌中使用其他声明名时，在信任项中修改 `write_claim` / `read_claim` 即可，不必改动身份提供方。

## 3. 写入控制与正式账套开放

本服务直接修改 U8 的业务数据。开发和验证都在测试账套上进行；要让调用方写正式账套，先在与正式账套同版本、同选项的测试账套上按正式配置预演，再逐个账套开放。本节说明涉及的各道闸门、写入策略文件，以及预演和开放的检查清单。

### 闸门

一笔写入依次通过下列各层，任一层拒绝即不写入：

| 层 | 配置位置 | 作用 | 修改后 |
| --- | --- | --- | --- |
| API 账套白名单 | `U8CO_ACCOUNTS`、信任项的 `accounts`、令牌的账套声明 | 哪些账套能被调用 | 重启 API |
| API 只读账套 | `U8CO_READONLY_ACCOUNTS` | 名单中的账套一律不能写（含预演） | 重启 API |
| API 读写权限 | 令牌的写权限声明或 scope | 哪个调用方能写 | 更换令牌 |
| API 写入策略 | `U8CO_WRITE_POLICY_FILE` | 按账套、单据类型、操作放行；冻结、时段、操作员、行数和金额上限 | 自动重载 |
| 桥账套白名单 | `allowedAccounts` | 该桥登录哪些账套 | 重启桥 |
| 桥只读账套 | `readOnlyAccounts` | 名单中的账套一律不能写（含预演） | 重启桥 |
| 写入分级 | `enableReplicatedWrites`、`testAccounts` | 第二级写入默认关闭；开启后也只对测试账套开放 | 重启桥 |
| 桥写入策略 | `writePolicyFile` 指向的文件 | 同 API，另加写入限额和许可点数保护 | 自动重载 |
| U8 权限 | U8 系统管理 | 操作员的功能权限、数据权限、字段权限 | 立即生效（读路由的权限快照缓存 60 秒） |

API 和桥配置同一份策略。API 一侧在调桥之前即拒绝，省一次往返、不占桥的队列；桥一侧是最后一道，直接调桥的客户端（`co/client`）也无法绕过。只配置 API 一侧时，限额和许可保护不生效。

### 只读账套

只需读取的账套（例如只开放查询和经营管理报表的正式账套）在两侧都列为只读：桥 `readOnlyAccounts`，API `U8CO_READONLY_ACCOUNTS`。

- 写闸门登记的每条写路由（单据新增、修改、删除、审核、关闭、生单、锁定，审批流操作，总账凭证、档案、应收应付、票据、期初、结账、存货核算的写入，专用审核路由）返回 403 `account_read_only`。
- 预演（`rollback`、`validate`、`plan` 各模式）和带幂等键的重发同样被拒绝；需要查询以前的结果时用读路由 `idempotency/get`。
- 独立于写入策略和写入分级：策略放行全部、账套在 `testAccounts` 里都不能解除只读。
- 桥的检查位于 `allowedAccounts` 之后、解密口令和登录 U8 之前，出队后登录前再检查一次；审计行 `policy` 记 `account_read_only`；健康检查、`meta` 和 `--check-config` 列出名单。
- 开放只读正式账套的顺序：先把账套加入 `readOnlyAccounts`（和 `U8CO_READONLY_ACCOUNTS`），再加入 `allowedAccounts`（和 `U8CO_ACCOUNTS`）。

### 第二级写入

第二级写入复现 U8 界面执行的 SQL（已在测试账套上与 U8 界面执行的 SQL 实测核对），U8 本身没有可调用的组件来完成这些操作。路由清单和风险说明见 [已知限制](limitations.md)「写入分级」。开放条件（含预演；一般在登录 U8 之前判定，任务出队后再判一次。`arap/voucher/delete` 要读出凭证才知道是不是汇兑损益、坏账、应付票据的处理凭证，这一步在登录 U8 之后、事务里任何写入之前判定）：

1. 桥 `config.json` 的 `enableReplicatedWrites` 为 `true`，否则 403 `feature_disabled`（消息以「第二级写入未开启：」开头，后接路由的原文）。
2. 账套在桥的 `testAccounts` 中，否则 403 `test_account_only`。

这两项只在桥上配置，API 不另设开关。健康检查和 `meta` 的 `replicated_writes` 报告开关状态，`/v1/co/health` 透传缺省桥和各分流桥的值。

第二级写入面向测试账套。正式账套不要加入 `testAccounts`；正式账套上的这类操作请在 U8 客户端完成。写入策略与写入分级是「与」的关系：策略放行全部（`"type": "*", "ops": ["*"]`）也不能打开第二级写入。

### 写入策略

**启用。**

- 桥：`config.json` 增加 `writePolicyFile`，值为策略文件路径（相对路径按运行目录解析，必须位于运行目录之下）。增加该键须重启一次桥，之后修改策略文件不必重启。
- API：环境变量 `U8CO_WRITE_POLICY_FILE` 指向同一份 JSON。
- 两侧都不配置时没有策略，写入只受其他闸门约束。

配置后必须有一份有效的文件：启动时文件不存在、内容无效，或运行中文件被删除，所有写入（含预演）一律返回 503 `write_policy_unavailable`「写入策略不可用」；读路由不受影响。这是有意的「失效即拒写」。

**文件格式。** JSON，UTF-8（可带 BOM）。示例（账套号为占位）：

```json
{
  "version": 1,
  "reloadSeconds": 2,
  "comment": "正式写入策略。改动前先在测试账套上预演",
  "freeze": { "global": false, "accounts": [], "reason": "月末结账" },
  "windows": [ { "days": "1-5", "start": "09:00", "end": "18:00" } ],
  "denyDates": ["2026-12-31"],
  "license": { "maxConcurrentLogins": 4, "holdWritesWhen": ["full"] },
  "defaults": { "writesPerMinute": 10, "writesPerDay": 200, "maxLines": 200, "maxAmount": 0 },
  "unlisted": "deny",
  "accounts": {
    "801": {
      "comment": "甲公司：销售订单、采购订单、付款单、质检审批",
      "operators": { "allow": [], "deny": ["op002"] },
      "quotas": { "writesPerDay": 100, "maxAmount": 500000 },
      "allow": [
        { "type": "sale_order", "ops": ["create", "update", "verify"] },
        { "type": "purchase_order", "ops": ["create", "verify"] },
        { "type": "ap_payment", "ops": ["create", "verify"] },
        { "type": "qm_incoming_check", "ops": ["workflow"] }
      ]
    },
    "802": { "comment": "乙公司：业务范围确认前不放行任何写入", "allow": [] }
  }
}
```

通用规则：

- 任何一层出现不认识的键，整份文件无效。每一层对象都可以写字符串 `"comment"` 作注释。
- 键名、`type`、`ops` 区分大小写；操作员编码不区分。字符串去掉首尾空白，可选字符串去掉空白后为空按未写处理。
- API 一侧文件不超过 1 MiB。

| 键 | 缺省 | 说明 |
| --- | --- | --- |
| `version` | 必填 | 整数 `1`（写 `1.0`、`"1"` 都无效） |
| `reloadSeconds` | 2 | 重读文件的间隔（秒），1 到 3600 |
| `freeze.global` | `false` | 冻结全部账套的写入 |
| `freeze.accounts` | `[]` | 冻结这些账套（三位数字）的写入 |
| `freeze.reason` | 无 | 冻结原因，附在错误消息后：「写入已冻结：月末结账」 |
| `windows` | 无（不限时段） | 只在这些时段内可写。每项 `{days, start, end}` 都必填：`days` 为 ISO 星期（1 为星期一，7 为星期日），写成 `1-5`、`1,3,5`、`1-3,6`，不带空格，范围不能反写；`start`、`end` 为 `HH:MM`（00:00 到 23:59），含 `start`、不含 `end`，`end` 必须晚于 `start`（不支持跨午夜）；多个时段任一命中即可。不限时段时删除该键，写成空数组无效 |
| `denyDates` | `[]` | `yyyy-MM-dd`，这些日期全天不可写（不论是否有 `windows`） |
| `license.maxConcurrentLogins` | 0（不限） | 桥进程中同时存在的 U8 登录上限，0 到 64。只在桥上生效 |
| `license.holdWritesWhen` | `[]` | 许可状态为其中之一时暂停写入，取值 `near`、`full`、`unknown`。只在桥上生效 |
| `defaults` | 全 0 | 各账套的缺省上限：`writesPerMinute`（0 到 100000）、`writesPerDay`（0 到 10000000）、`maxLines`（0 到 100000）为整数，`maxAmount` 可带小数（0 到 1 万亿）。0 表示不限 |
| `unlisted` | `"deny"` | `accounts` 中未列出的账套：`"deny"` 一律不放行，`"allow"` 放行全部写入（只用于测试环境） |
| `accounts.<账套>.operators` | 无 | `{allow: [], deny: []}`：`deny` 优先；`allow` 为空表示任何操作员都可以，不为空时只有名单中的操作员可写 |
| `accounts.<账套>.quotas` | 同 `defaults` | 按键覆盖 `defaults`，未写的键沿用 `defaults` |
| `accounts.<账套>.allow` | `[]` | 放行规则 `{type, ops}`。`type` 为单据类型或路由族（见下表），或 `"*"`；`ops` 为非空数组，取值为下文的操作词或 `"*"`。一条规则的 `type` 相同（或为 `"*"`）且 `ops` 含本次操作（或含 `"*"`）即放行。列出了账套却没写 `allow`，等于不放行任何写入 |

测试环境放行全部写入的写法：`{"version": 1, "unlisted": "allow"}`。

**操作词与分类。** 每个写请求先归为（`type`，`op`）再按规则匹配。`op` 只有：`create`、`update`、`delete`、`verify`、`unverify`、`close`、`open`、`generate`、`lock`、`unlock`、`workflow`、`writeoff`、`voucher`、`post`、`process`、`other`。

| 路由 | `type` | `op` |
| --- | --- | --- |
| `vouchers/create`、`update`、`delete`、`generate` | 请求体的 `type` | 同路由名 |
| `vouchers/verify` | 请求体的 `type` | `action`：`verify`、`arap_verify` → `verify`；`unverify`、`arap_unverify` → `unverify` |
| `vouchers/close` | 请求体的 `type` | `close` / `open` |
| `vouchers/lock` | 请求体的 `type` | `lock` / `unlock` |
| `workflow/*`（提交、撤回、同意、不同意、退回、弃审、重新提交） | 请求体的 `type` | `workflow` |
| `sale-orders/verify`、`dispatches/verify`（专用审核路由） | `sale_order`、`dispatch` | `verify` / `unverify` |
| `gl/vouchers/create`、`update`、`delete`、`verify`、`unverify`、`post` | `gl` | 同路由名 |
| `gl/vouchers/reverse` | `gl` | `create` |
| `gl/transfer/pnl`、`gl/transfer/custom` | `gl` | `voucher` |
| `gl/vouchers/void`、`unvoid`、`sign`、`unsign`、`unpost` | `gl` | `other` |
| `archives/create`、`update`、`delete` | `archives` | 同路由名 |
| `arap/writeoff`、`arap/writeoff/auto` | `arap` | `writeoff` |
| `arap/voucher`、`arap/process/voucher` | `arap` | `voucher` |
| `arap/merge`、`transfer`、`red_offset`、`exchange_gain`、`bad_debt` | `arap` | `process` |
| `arap/writeoff/cancel`、`arap/voucher/delete`、`arap/process/cancel`、`arap/exchange_gain/cancel` | `arap` | `other` |
| `notes/create`、`notes/delete`、`notes/process` | `notes` | `create` / `delete` / `process` |
| `openings/post` | `openings` | `post`；`unpost` → `other` |
| `openings/arap` | `openings` | `create`、`delete`、`verify`、`unverify` |
| `periods/close` | `periods` | `close`；`reopen` → `open` |
| `ia/post` | `ia` | `post`；`unpost` → `other` |
| `ia/period_end` | `ia` | `run` → `close`；`cancel` → `open` |
| `intercompany/generate_buyer`（只在 API） | 买方单据类型 | `generate`（按买方账套的 `vouchers/generate` 判定） |

- 各种取消（取消核销、取消制单、取消处理、取消记账等）一律为 `other`：放行 `writeoff`、`voucher`、`process`、`post` 不连带放行对应的取消。
- `action` 写错或缺少时也记为 `other`，因此这类请求可能在 `action` 校验之前先被策略 403（规则未放行 `other` 时）。
- `type` 按请求体中的原样比较。
- 规则中含通配（`"type": "*"` 或 `"ops": ["*"]`）的账套，会放行该账套全部第一级写入（包括采购手工结算、货位调整单、到货单关闭打开等），开通前逐项确认。

**判定顺序与错误码。** API 在调桥之前、桥在解密口令之前（登录前）各判定一次；桥在任务出队后按当时的策略再判定一次，排队期间生效的冻结、时段变化在此拦截。以最先不通过的一步为准：

| 步 | 条件 | HTTP | 错误码 | 消息 |
| --- | --- | --- | --- | --- |
| 1 | 没有可用策略（文件不存在、首次加载即无效） | 503 | `write_policy_unavailable` | 写入策略不可用 |
| 2 | `freeze.global`，或账套在 `freeze.accounts` 中 | 503 | `write_frozen` | 写入已冻结（有原因时「写入已冻结：<原因>」） |
| 3 | 今天在 `denyDates` 中，或此刻不在任何 `windows` 时段内 | 503 | `write_window` | 当前时段不允许写入 |
| 4 | 账套未列出且 `unlisted` 不是 `allow`，或列出的账套没有规则匹配 | 403 | `write_not_allowed` | 该账套不允许此写入（`detail` 带 `type`、`op`） |
| 5 | 列出的账套的操作员名单不通过 | 403 | `operator_not_allowed` | 该操作员不能在此账套写入 |
| 6 | `lines` 行数超过 `maxLines` | 400 | `write_limit` | 行数超过上限（`field=lines`，`detail` 带 `max`、`actual`） |
| 6 | 金额或汇率无法识别 | 400 | `write_limit` | 金额或汇率无法识别，按超过上限处理 |
| 6 | 金额超过 `maxAmount` | 400 | `write_limit` | 金额超过上限（`detail` 带 `max`、`actual`） |
| 7 | 许可状态在 `holdWritesWhen` 中（只在桥上） | 503 | `u8_license_full` | U8 许可点数已满（full），接口暂停写入，请稍后重试（`near`、`unknown` 时为「紧张」） |
| 8 | 账套写入限额已满（只在桥上） | 429 | `write_quota` | 已超过账套写入限额（`detail.retry_after_seconds`） |
| 9 | 新的 U8 登录达到 `maxConcurrentLogins`（只在桥上，读写都计） | 503 | `u8_license_hold` | 接口登录已达上限，请稍后重试 |

- 预演（`dry_run`）同样经过第 1–7 步；第 8 步对预演只检查不计数（已满时同样 429）。
- 第 4、5 步：未列出、按 `unlisted: "allow"` 放行的账套不查操作员名单。
- 第 6 步的行数只对 `create`、`update`、`generate` 计，按请求体 `lines` 数组的元素个数：不带 `lines` 的整单生单不受限，`gl/vouchers/reverse` 也不带行。
- 第 6 步的金额只对 `ar_receipt`、`ap_payment`、`ar_refund`、`ap_refund` 的 `create`、`update` 计：本次请求各行合计（每行取本币 `iamt`，没有时取原币 `iamt_f` × 表头 `iexchrate`，未给汇率按 1），取绝对值比较；没有一行带金额（例如只改表头）时不检查。数字按写入时相同的规则解析（允许指数写法，如 `6e6`）。配置了 `maxAmount` 时以下情况一律按超限 400：金额或汇率无法解析、类型不对；同一字段有只差大小写的两个键；超出数值范围；修改时某行只给 `iamt_f` 而未给 `iamt`（外币修改须连同本币一起给）。
- 金额上限不覆盖：`notes/create`（生成收款单）、坏账收回（`arap/bad_debt` 生成收款单）、期初单据 `openings/arap`、全部参照生单。上限只按本次请求中的行计，不含单据上未改动的行。
- 503 和 429 可以重试（API 的 `Retry-After`：`write_policy_unavailable`、`u8_license_hold` 30 秒，`write_frozen`、`write_window` 300 秒，`write_quota` 取桥给出的秒数，没有时 60 秒；桥本身不发 `Retry-After` 头，只在 `detail.retry_after_seconds` 中给出）；403、400 不要原样重试。这些错误都保证没有写入，不占幂等键，同一个 `Idempotency-Key` 可以再次使用。
- 带幂等键的重放同样经过第 1–6 步；被拒绝时不登记该键。

**重载。** 桥每隔 `reloadSeconds`（尚无有效策略时每 2 秒）读取一次文件，按内容的 SHA-256 判断是否变化（不看修改时间和长度）：

- 内容有效：整份换为新策略，审计事件 `write_policy_loaded`，健康检查 `state` 为 `ok`。
- 内容无效：保留上一份有效策略继续判定，审计事件 `write_policy_invalid`（带原因），`state` 为 `invalid`。启动时即无效则没有可用策略，写入全部拒绝。
- 文件被删除：不再有可用策略，写入全部拒绝，审计事件 `write_policy_missing`，`state` 为 `missing`。
- 读取出错（权限、占用）：状态不变，下一轮再读。启动时文件存在但无法读取，`state` 显示 `missing`。

API 没有后台线程：判定或健康检查时距上次读取已过 `reloadSeconds` 即重读，规则相同，日志写入 `u8co.api`。修改文件时先写临时文件再改名替换，不要在原文件上就地编辑。

**时区。** 桥按 Windows 本机时间判断 `windows`、`denyDates`；API 按 `U8CO_TIMEZONE`。两侧须指同一时区。

**限额（只在桥上）。** 按账套计已提交的写入：非预演，且结果为 2xx 或 504 `outcome_unknown`（可能已写入 U8，从严计入）。出队时先占一个名额，结束时成功的转为已提交、其余退回；在途写入也计入上限，同一账套并行写入不会超额。

- 每分钟：滑动 60 秒窗口，只在内存中，重启清零。
- 每天：按桥的本地日历日，保存在审计目录下的 `write-quota.json`（`{"date": "yyyy-MM-dd", "counts": {"801": 12}}`，临时文件加改名写入），重启后当天继续累计；未配置审计目录时不落盘。文件损坏时从零开始并记 `write_quota_load_failed`。
- 429 的 `retry_after_seconds`：日限额到本地午夜；分钟限额到窗口中最早一笔满 60 秒（至少 1 秒）；名额全被在途写入占用时 5 秒。
- 幂等重放不入队，不计数。

**许可保护（只在桥上）。**

- `holdWritesWhen`：桥的许可状态（`ok` / `near` / `full` / `unknown`，同健康检查的 `license`）在列表中时暂停写入，503 `u8_license_full`。出队时对所有写入判定一次（含复用缓存登录的），新登录时对写路由再判定一次；读路由不受影响。写了 `unknown` 时，桥尚未采到许可数据即会暂停全部写入。
- `maxConcurrentLogins`：本进程同时存在的 U8 登录（含 `loginReuse` 保留的闲置登录）达到上限时，新登录（读写都计）返回 503 `u8_license_hold`。到达上限时桥先关闭本线程闲置的缓存登录再判定一次；仍不够时让所有线程在下一次空闲清理时关闭此前放回的闲置登录，本次请求仍返回 503。复用缓存中的登录不算新登录；同一请求内的嵌套登录（库存期初按启用日期另登录一次）不另计。U8 许可按「工作站 × 子系统」计点，同一台机器多次登录不多占点；该上限保护的是本机进程和 `UA_TaskLog` 登记，不是许可总数。
- `--check-config` 在 `maxConcurrentLogins` 小于 2，或开启 `loginReuse` 而上限不大于 `staWorkers` 时给出警告：前者在读写并发时会频繁 503，后者各写线程缓存的闲置登录会占满名额。

### 按账套串行写入

`serializeWrites`：

- `true`（缺省）：全局写闸门 `u8:write`，整个桥一次一笔写入。
- `"account"`：闸门按账套 `u8:write:<账套>`，同一账套一次一笔，不同账套并行。多个账套同时日常读写时可用它缩短排队。
- `false`：只按单据锁排程。

`"account"` 只串行同一账套的写入。`UFSystem` 中由所有账套共用的部分在不同账套之间不串行：审批流终审后的孤儿任务清理（`UA_Task` / `UA_TaskLog`）、审批流和许可的任务登记。同一账套上并行写入曾观察到 U8 组件中的写线程一起卡住，因此缺省仍为全局串行。跨账套并行须先在测试账套上实测（含两个账套同时做审批流终审），再在正式环境开启。

### 审计和健康检查

- 桥的审计行包含 `caller`（API 送来的调用方标识 `<信任项名>:<客户端>`，过长或含控制字符时换成哈希）、`op`（分类后的操作，读路由为空）、`policy`（策略第 1–6 步的结果：`allow` 或拒绝的错误码；只读账套为 `account_read_only`；未配置策略为空）。限额、许可的拒绝记在 `outcome` 和消息中，`policy` 仍为 `allow`。
- API 的审计行包含 `type`（单据类型或路由族）、`op`、`outcome`（桥成功为 `ok`，否则为桥或 API 策略的错误码）。
- 健康检查的 `write_policy`：未配置时 `{"state": "off"}`；配置后 `{state, version, loaded_at, freeze: {global, accounts}, window_open}`，`state` 为 `ok` / `invalid` / `missing`，`loaded_at` 为 UTC，`window_open` 已计入 `denyDates`。API 的 `/v1/co/health` 透传缺省桥（顶层）和各分流桥（`routes[].write_policy`）的值，另给出 API 自身的 `api_write_policy`。
- `--check-config` 打印策略文件的路径和状态（未配置 / 文件不存在 / 有效：版本、冻结、此刻是否可写 / 无效：原因）、`serializeWrites`、`enableReplicatedWrites`、`testAccounts`、`readOnlyAccounts` 的取值。

监控建议：健康检查 `write_policy.state` 不是 `ok`、`window_open` 在工作时间为 `false`、`freeze` 非预期、`replicated_writes` 在生产桥上为 `true`，或审计中出现 `write_policy_invalid`、`write_policy_missing`、`outcome_unknown`、`write_quota`，都应告警。

### 预演检查清单

在与正式环境同版本 U8、同样账套选项的测试账套上，按正式配置完整执行一遍。多账套并行写入时，在同等数量的测试账套上预演。

- [ ] 预演使用独立的桥实例：`allowedAccounts` 只有预演用的测试账套，`enableReplicatedWrites` 为 `false`、`testAccounts` 为空（第二级写入必须返回 403）。
- [ ] `writePolicyFile` 指向生产策略草案：`unlisted: "deny"`；每个账套的 `allow` 只列本公司实际需要的写入（按调用方的业务清单整理，未确认的账套写 `"allow": []`）；`windows`、`denyDates`、`defaults`、`quotas`、`maxAmount` 按生产拟定值。
- [ ] API 的 `U8CO_WRITE_POLICY_FILE` 指向同一份内容，`U8CO_TIMEZONE` 与桥所在机器一致；`U8CO_ACCOUNTS` 只有预演用的测试账套，不含正式账套。
- [ ] `loginReuse`、`serializeWrites`、`license.maxConcurrentLogins`、`holdWritesWhen` 按生产拟定值；`--check-config` 没有警告。
- [ ] 调用方的实际业务流程用预演（`dry_run`）走一遍，确认每一步都被放行、未列出的写入返回 403；第二级写入返回 403 `feature_disabled`。
- [ ] 冻结演练：写 `freeze.accounts`，确认写入返回 503 `write_frozen`、读路由正常、在途写入照常完成；解冻后恢复。
- [ ] 无效文件演练：写入带未知键的文件，确认 `state` 变为 `invalid`、旧策略照常生效、审计有 `write_policy_invalid`；改回后 `state` 回到 `ok`。
- [ ] 限额演练：把 `writesPerMinute` 调小，确认 429 带 `Retry-After`，同一幂等键等待后重发能成功。
- [ ] 审计：写入行有 `caller`、`operator`、`acc`、`type`、`op`、`policy`、`outcome`；监控能收到上一节的告警。
- [ ] 若开启 `serializeWrites: "account"`，在多个测试账套上并行写入（含审批流终审），确认没有卡住、没有孤儿任务残留。
- [ ] 预演结束后，测试账套的策略恢复为测试用配置，或按需保留。

### 开放检查清单

每次只开放一个正式账套，确认稳定后再开放下一个。

- [ ] 预演检查清单全部通过，使用的是同一份策略草案。
- [ ] 策略中该账套的 `allow` 已由业务负责人确认；首批只放行最常用、最容易核对的写入（例如订单新增、审核），不使用 `"*"`。
- [ ] 调用方使用的 U8 操作员只有所需的功能权限、数据权限和字段权限，不使用账套主管；需要时用 `operators.allow` 限定操作员。
- [ ] 正式账套不在任何桥的 `testAccounts` 中，生产桥的 `enableReplicatedWrites` 为 `false`。
- [ ] 只读的正式账套已列入 `readOnlyAccounts` 和 `U8CO_READONLY_ACCOUNTS`。
- [ ] 桥：`allowedAccounts` 加入该账套（重启桥），`writePolicyFile` 已配置且健康检查 `write_policy.state` 为 `ok`；API：`U8CO_ACCOUNTS`、令牌的账套声明、信任项的 `accounts`、按账套分流的桥配置（如有）加入该账套，API 的策略文件同步。
- [ ] 先把该账套写入 `freeze.accounts`，确认读路由和健康检查正常，再在约定时间解冻。
- [ ] 指定冻结的负责人和操作步骤（修改策略文件的 `freeze`，不必重启）；月末结账、盘点等时段提前写入 `denyDates` 或 `freeze`。
- [ ] 监控和告警指向生产桥和 API。
- [ ] 开放后设观察窗口，到期冻结写入，逐行核对审计：每个 `outcome_unknown` 都要有结论，检查 `write_not_allowed` 中是否有应放行而未放行的写入。
- [ ] 稳定运行后再按业务需要逐步扩大 `allow`，每次扩大同样先在测试账套上预演。

## 4. Python 客户端

没有缺省的桥地址。各项设置按下列顺序取第一个有值的（模块 `co/client/u8co_settings.py`）：

| 设置 | 来源（按优先级） |
| --- | --- |
| 桥地址 | `--base-url`，然后环境变量 `U8CO_BASE_URL`，然后客户端配置文件的 `base_url` |
| 共享密钥 | 环境变量 `U8CO_SECRET`，然后 `U8CO_SECRET_FILE` 指向的文件，然后客户端配置文件的 `secret_file`。不接受命令行参数 |
| 口令 | 只在终端提示时输入，没有 `--password` |

客户端配置文件缺省为 `~/.config/u8co/client.json`，可用环境变量 `U8CO_CLIENT_CONFIG` 更换位置：

```json
{"base_url": "http://192.0.2.10:18089/u8co", "secret_file": "~/.config/u8co/bridge-secret.hex"}
```

密钥文件不能给属组和其他用户任何权限（`0600` 或 `0400`），内容为一行 64 位小写十六进制。

## 5. 本地 MCP 服务 `mcp.json`

`mcp/` 中的 `u8co-mcp` 运行在使用者自己的电脑上，由 AI 客户端（Claude Code、Claude Desktop、Cursor 等）以子进程启动，经 HTTPS 调用 API 服务（`/v1/co/*`）。安装和各客户端的接入见 [MCP](mcp.md)。

配置文件缺省为 `~/.config/u8co/mcp.json`，可用环境变量 `U8CO_MCP_CONFIG` 更换位置，样例在 `mcp/mcp.example.json`。配置文件中不放任何密钥，只写密钥文件的路径；不要把真实配置提交到仓库。

```json
{"base_url": "https://u8co.example.com",
 "token": {"type": "client_credentials", "token_url": "https://auth.example.com/oauth/token",
           "client_id": "mcp-client", "client_secret_file": "~/.config/u8co/mcp-client-secret", "scope": "openid"},
 "u8": {"acc": "801", "year": "2026", "operator": "op001", "password_file": "~/.config/u8co/mcp-u8-password"},
 "read_only": false, "timeout_s": 90, "long_timeout_s": 1000, "ca_file": null}
```

| 键 | 说明 |
| --- | --- |
| `base_url` | 必填。API 服务的地址，不带 `/v1/co`。必须是 `https://`，只有本机回环地址（`localhost`、`127.0.0.1`）可以用 `http://`；不能带用户名、口令、查询串 |
| `token` | 必填。Bearer 令牌的来源，`type` 见下表 |
| `u8.acc`、`u8.operator` | 必填（`token.type` 为 `incoming` 时整个 `u8` 段可以省略）。三位账套号和 U8 操作员编码，注入到每个请求体中 |
| `u8.year` | 可选，四位账套库年度；省略时由 API 按登录日期取 |
| `u8.password_file` | U8 操作员口令文件。也可改用环境变量 `U8CO_MCP_PASSWORD`（优先） |
| `read_only` | 缺省 `false`。`true` 时不提供写工具 `u8_write`，调用也被拒绝。只读场景建议同时使用只读令牌 |
| `timeout_s` | 调用 API 的超时秒数，1 到 600，缺省 90（须大于桥的 75 秒） |
| `long_timeout_s` | 长时操作调用 API 的超时秒数（`ia/post`、`ia/period_end`，以及 `module` 为 `ia` 或 `through` 为 `true` 的 `periods/close`）。不小于 `timeout_s`、不大于 7300；不写时取 1000 与 `timeout_s` 中的较大者。桥对这几条路由等 `iaCommandSeconds` + 60 秒（缺省 960，最多 7260），API 的 `U8CO_BRIDGE_LONG_TIMEOUT` 也须相应放长 |
| `ca_file` | 可选，自签证书时的 CA 文件（PEM）。TLS 证书校验始终开启 |
| `allow_insecure_http` | 缺省 `false`。`true` 时 `base_url` 可以使用非回环地址的 `http://`（例如 HTTP 方式与 API 同在一个容器网络：`http://u8co-api:8080`）。只能与 `token.type: incoming` 一起使用，其他令牌类型设置后启动失败；只放宽 `base_url`，`token_url` 仍须 `https://` |
| `http` | 可选，HTTP 方式（见下文）。`enabled`（缺省 `false`，`true` 时不加 `--http` 也以 HTTP 方式运行）、`host`（缺省 `127.0.0.1`）、`port`（缺省 `8095`）、`path`（缺省 `/mcp`）、`user_header`（缺省 `X-U8co-User`，与 API 的 `U8CO_USER_HEADER` 一致）、`allowed_origins`（缺省空：带 `Origin` 头的请求一律 403；浏览器直连时列出允许的来源，如 `https://chat.example.com`）。命令行 `--host`、`--port` 覆盖这里的值 |
| `mgmt` | 可选，经营管理查询（`u8_mgmt_*` 工具）。`accounts`（必填，1 到 12 项）：各账套的只读登录 `{acc, operator, password_file}`，账套不能重复，每个账套的口令放在各自的口令文件中；`claim`：令牌中表示经营管理权限的声明名，缺省 `u8co_mgmt`，须与 API 信任项的 `mgmt_claim` 一致（API 一侧没有缺省值，须显式配置同一名称）；`scope`：可选，表示经营管理权限的 scope 值，须与 API 信任项的 `mgmt_scope` 一致（字母或下划线开头，只含字母、数字和 `_.:/-`）；`accounts_claim`：令牌中列出可用账套的声明名，缺省 `u8co_accs`，须与 API 信任项的 `accounts_claim` 一致，工具未给 `accounts` 时只取配置中声明里有的账套（令牌没有该声明时取全部，交由 API 判断；都不在声明中时工具报 `bad_arguments`），写空串 `""` 表示不看；`enabled`：缺省 `true`。只有 `enabled` 为 `true`，且当前令牌（JWT）载荷中该声明为 `true` 或配置了 `scope` 而令牌的 `scope` / `scp` 含该值时，才列出这些工具；本服务只解码载荷、不验签，API 仍按信任配置校验。`person_proxy`：`token.type` 为 `incoming` 时必填、其余方式不能配置，见下文；`incoming` 方式下 `accounts` 各项只写 `{acc}`，写了 `operator` 或 `password_file` 即启动失败；`u8.password_file` 和环境变量 `U8CO_MCP_PASSWORD` 在 `incoming` 方式下同样不允许 |

| `token.type` | 键 | 行为 |
| --- | --- | --- |
| `client_credentials` | `token_url`、`client_id`、`client_secret_file`（必填），`scope`（可选） | 向身份提供方的令牌端点用 client credentials 换取令牌，缓存到过期前 60 秒；API 回 401 时丢弃缓存重取一次。`token_url` 的地址规则同 `base_url` |
| `file` | `path` | 每次调用重新读取该文件（由外部工具负责刷新令牌） |
| `env` | `name` | 每次调用读取该环境变量 |
| `incoming` | 无 | 只用于 HTTP 方式：每个 HTTP 请求的 `Authorization: Bearer` 令牌原样转给 API。本服务不持有客户端密钥，不缓存、不刷新令牌 |

- 密钥文件（客户端密钥、令牌文件、U8 口令文件）必须是普通文件，不给属组和其他用户任何权限（`chmod 600`），内容去掉首尾空白后不能为空，否则启动或调用时报错（错误中只有路径，没有内容）。路径中的 `~` 会展开。
- 登录日期 `date` 缺省取本机今天。工具的 `body` 中不能出现 `acc`、`operator`、`password`、`password_enc`（工具报错），账套和操作员只能来自配置；`year`、`date` 可以覆盖。
- 在身份提供方为 MCP 单独登记一个客户端（例如 `mcp-client`），只授予需要的读写权限和账套（见第 2 节「OIDC 信任配置」），并在 U8 中给它使用的操作员最小的功能和数据权限。

### HTTP 方式（多人共用）

`u8co-mcp --http`（或配置 `http.enabled: true`）以 MCP Streamable HTTP 方式监听 `http.path`，供 AI 平台按用户接入。样例 `mcp/mcp.http.example.json`，容器镜像和 Compose 示例见 `mcp/Dockerfile`、`mcp/compose.example.yml`。

- `token.type` 必须是 `incoming`，否则不启动（退出码 2），以免共享的机器令牌暴露在网络上；反过来 `incoming` 也只能用于 HTTP 方式。
- 必须配置 `read_only: true`，否则不启动（退出码 2）。
- 只提供 `u8_guide` 和 `u8_mgmt_*`，不提供 `u8_read`、`u8_write` 等通用工具，`u8` 段可以省略。
- 连接：非 2xx 响应一律关闭连接；`Transfer-Encoding` 请求回 411；请求体上限 256 KiB；同时最多 32 个连接（超出回 503）；套接字超时 30 秒；收到 `SIGTERM` 时停止接受新连接，在途请求最多等 25 秒。
- 每个 `POST` 必须带 `Authorization: Bearer <令牌>`，否则 401（带 `WWW-Authenticate: Bearer`）；令牌是 JWT 且 `exp` 已过期时也回 401（`error="invalid_token"`），让客户端刷新。签名和权限由 API 按信任配置校验，本服务只解码载荷。
- 工具列表、经营管理门控（`mgmt.claim` / `mgmt.scope`）和缺省账套（`mgmt.accounts_claim`）按本次请求的令牌计算。
- 令牌 `sub` 是 UUID 时，调用 API 时另带 `http.user_header`（缺省 `X-U8co-User`）头，API 只记审计，不参与授权。
- 经营管理查询按调用者本人的 U8 权限执行：本服务不保存任何共用的 U8 登录，每次调用经 `mgmt.person_proxy`（部署方自行运行的身份绑定服务）以调用者本人绑定的 U8 操作员调用 API，结果继承该操作员的功能权限和数据权限。协议见 [MCP](mcp.md)「身份绑定服务」。

| `mgmt.person_proxy` 的键 | 说明 |
| --- | --- |
| `url` | 必填。身份绑定服务的地址，原样使用。规则同 `base_url`（`allow_insecure_http` 也放宽它，例如同一容器网络中的 `http://binding:8000/v1/bindings/as-person`） |
| `token_file` | 必填。本服务调用身份绑定服务的服务令牌文件（权限要求同其他密钥文件），每次调用重新读取，可在线轮换 |
| `timeout_s` | 可选，1 到 600 秒，缺省同顶层 `timeout_s` |

在 API 的信任配置中为这类用户令牌单独设一项（`mgmt_claim`、`accounts_claim`），不要给机器客户端加 `mgmt_claim`。
