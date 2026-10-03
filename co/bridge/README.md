# co/bridge：U8 CO 桥

运行在 U8 应用服务器上的 32 位 Windows 服务。它监听一个 HTTP 端口，校验签名和来源 IP，用调用方给出的 U8 操作员登录，在自建的 STA 线程上调用 U8 业务 COM 组件或直接读库，提交后在新连接上回读确认，然后写审计、返回 JSON。

编码和复审先逐条核对 [`CHECKLIST.md`](CHECKLIST.md)。

## 文档

| 主题 | 文档 |
| --- | --- |
| 进程、线程池、锁、登录缓存、事务、审计；签名、口令加密和来源白名单 | [`docs/architecture.md`](../../docs/architecture.md) |
| 前提、编译、安装、升级、卸载 | [`docs/getting-started.md`](../../docs/getting-started.md) |
| `config.json`、`sql.json`、`secret.hex`、命令行、安装参数 | [`docs/configuration.md`](../../docs/configuration.md) |
| 路由、字段、错误码 | [`docs/api-reference.md`](../../docs/api-reference.md) |
| 各业务组件的调用形状和 U8 行为 | [`docs/u8-notes.md`](../../docs/u8-notes.md) |
| 自检和测试 | [`docs/testing.md`](../../docs/testing.md) |

## 目录

| 文件 | 作用 |
| --- | --- |
| `build.ps1` | 用系统自带的 C# 5 编译器（`csc.exe /platform:x86`）编译到 `out\` |
| `install.ps1` / `uninstall.ps1` | 安装、更新、卸载服务（64 位 PowerShell 7，管理员） |
| `config.example.json` | `config.json` 样例 |
| `u8co-bridge.exe.config` | CLR 配置，必须放在 exe 旁边（`legacyUnhandledExceptionPolicy`） |
| `../SafePath.ps1` | 安装、卸载脚本共用的路径断言 |

`src/` 中的源文件按前缀分组：

| 前缀 | 内容 |
| --- | --- |
| `Program`、`AppHost`、`HttpServer`、`Auth`、`Crypto`、`Config*`、`Paths`、`FileAcl`、`AuditLog`、`SelfTest` | 进程、HTTP、鉴权、配置、审计、自检 |
| `StaPool`、`StaWorker`、`StaExec`、`WorkItem`、`WorkRun`、`WorkContext`、`DocLocks`、`RouteClass`、`WriteGate`、`AuthCache`、`LoginCache`、`Watchdog` | 线程池、队列、锁、写闸门、登录缓存与复用、看门狗 |
| `ReadOnlyGate`、`TestAccountGate`、`WritePolicy*`、`WriteClass` | 只读账套、第二级写入开关、写入策略 |
| `Perm*` | 操作员的功能、记录和字段权限 |
| `Idem*`、`DryRun*` | 幂等键、写预演 |
| `Sig*` | COM 签名自检（`--check-signatures`） |
| `U8Session`、`U8Resolve`、`ComUtil`、`CoTrans`、`AdoCreds`、`AdoXml`、`DomRows*`、`Rows*`、`CoRows`、`SqlRead`、`BillNo` | 登录、程序集解析、晚绑定调用、事务、DOM 和 SQL 工具、单号 |
| `Requests*`、`Kinds`、`Json`、`ApiResult`、`EditMsg`、`Meta*` | 请求解析、单据类型表、JSON、响应、元数据 |
| `Sale*`、`So*`、`Sa*`、`Dispatch` | 销售：订单、发货、发票、审核、生单 |
| `Purchase*`、`Pu*` | 采购：订单、到货、发票、结算 |
| `Stock*` | 库存：其他出入库、调拨、采购入库、销售出库 |
| `Mfg*`、`Mo*` | 生产：材料出库、产成品入库、生产订单 |
| `Qm*` | 质量管理 |
| `Arap*` | 应收应付 |
| `Gl*` | 总账凭证 |
| `Arc*` | 基础档案 |
| `List*`、`Reports*` | 单据列表、现存量、报表 |
| `Wf*`、`Workflow*`、`VoucherRead` | 审批流和读取 |

## 编译和自检

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
.\out\u8co-bridge.exe --selftest
```

也可以在仓库根目录运行 `pwsh scripts/ci/build-cs.ps1`（CI 使用同一脚本）。

`--selftest` 核对协议测试向量和桥内部各模块，不连接 U8。编译使用 Windows 自带的 .NET Framework 4 编译器，语法限于 C# 5，不引用互操作程序集。没有 U8 的环境（例如 CI）中只能编译和自检；安装到 U8 服务器后再用 `--check-config` 和 `--check-signatures` 核对配置与 COM 签名（[`docs/testing.md`](../../docs/testing.md)）。
