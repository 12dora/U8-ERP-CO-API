# U8-ERP-CO-API：用友 U8+ ERP 的 REST API、Python SDK 与 MCP 服务

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml) [![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE) [![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB)](docs/getting-started.md) [![OpenAPI 3.1](https://img.shields.io/badge/OpenAPI-3.1-6BA539)](docs/api-reference.md) [![.NET Framework 4 x86](https://img.shields.io/badge/.NET%20Framework-4%20x86-512BD4)](docs/architecture.md)

> 本项目不是用友的官方项目，与用友网络科技股份有限公司没有关联，也未获其认可或赞助。「用友」「U8」「U8+」是用友网络科技股份有限公司的商标，本项目提及它们仅为说明兼容对象。本项目不包含、不分发任何用友软件，使用者须自备已授权的 U8，并自行确认其许可条款允许此类集成。
>
> This project is not an official Yonyou project and is not affiliated with, endorsed by, or sponsored by Yonyou Network Technology Co., Ltd. "Yonyou", "用友", "U8" and "U8+" are trademarks of Yonyou Network Technology Co., Ltd. and are used here only to describe compatibility. The project contains and redistributes no Yonyou software; you need your own licensed U8 installation and must confirm that your license permits this kind of integration.

[English](#english) · [快速开始](docs/getting-started.md) · [接口参考](docs/api-reference.md) · [变更日志](CHANGELOG.md)

U8-ERP-CO-API 把用友 U8+ 的业务组件（COM 组件）发布为 REST API（`/v1/co/*`，OpenAPI 3.1），供业务系统、集成平台和 AI 代理调用，可用于 U8 二次开发和 ERP 集成：单据的读取、新增、修改、删除、审核、关闭和参照生单，质量单据审批流，总账凭证，应收应付核销与制单，基础档案，列表、现存量和只读报表，以及跨账套的经营管理与公司间查询。

*An unofficial REST API (OpenAPI 3.1) for Yonyou U8+ ERP: read and write documents, GL vouchers, master data and reports over HTTP, with a Python SDK and CLI, an MCP server for AI agents and a document event stream.*

- **REST API**：`/v1/co/*`，OpenAPI 3.1 描述，OIDC 鉴权，按 U8 操作员的权限执行
- **业务覆盖**：单据（销售、采购、库存、生产、质量、应收应付）、总账凭证、基础档案、列表与报表
- **调用 U8 自身的业务组件**：第一级写入经 U8 的 COM 组件完成，由 U8 校验
- **Python 客户端与命令行**（SDK / CLI），**MCP 服务**供 Claude Code、Cursor 等 AI 代理读写 U8，**单据事件**推送到 Redis Streams
- **稳妥写入**：预演（`dry_run`）、幂等键、只读账套、写入策略

**本项目会直接修改 U8 中的业务数据。** 所有写入先在专用测试账套上验证，正式账套按 [配置参考](docs/configuration.md) 的只读账套和写入策略逐步开放。

## 组成

```
业务系统 / AI 代理 --HTTPS + OIDC--> API 服务（Linux / 容器） --HTTP + HMAC--> 桥（U8 应用服务器） --COM--> U8 业务组件
                                                                                             \--SQL--> U8 数据库
```

| 组件 | 目录 | 作用 |
| --- | --- | --- |
| 桥 | `co/bridge` | 32 位 Windows 服务，部署在 U8 应用服务器上。每个请求以调用方提供的 U8 操作员登录，调用业务组件或只读查询，提交后在新连接上回读确认。只接受共享密钥签名的请求和白名单内的来源 IP |
| API 服务 | `api/u8co_api` | FastAPI 应用，发布 `/v1/co/*` 和 OpenAPI。校验 OIDC 令牌，区分读、写、经营管理权限，检查账套白名单、只读账套、写入策略和限流，再签名转发给桥 |
| 客户端与命令行 | `co/client` | 直接调用桥的 Python 库和 CLI，供运维、排障和脚本使用 |
| MCP 服务 | `mcp/` | 本地 MCP 服务 `u8co-mcp`（仅用 Python 标准库），让 Claude Code、Claude Desktop、Cursor 等 AI 客户端经 API 服务读写 U8 |
| 单据事件 | `events/` | 轮询单据、票据、应收应付处理、总账凭证和基础档案的变化，写入 Redis Streams；断电不丢、至少一次投递、按 `event_id` 去重 |

权限由 U8 判断：每次请求都带 U8 操作员编码和口令，U8 按该操作员的功能权限、数据权限决定能否执行。口令只用于当次登录，不保存、不写日志。

## 功能

### 单据

| 领域 | 单据类型（`type`） | 支持的操作 |
| --- | --- | --- |
| 销售 | 销售订单、发货单、退货单、退货申请单、销售发票、销售出库单 | 读取、新增（订单；发货单、出库单、发票可无来源或先开票）、修改、删除、审核 / 复核、关闭、锁定（销售订单）、参照生单（订单 → 发货单 → 出库单 / 发票，退货与红冲） |
| 采购 | 请购单、采购订单、到货单、采购退货单、采购入库单、采购发票、采购结算单 | 读取、新增、修改、删除、审核 / 复核、关闭、参照生单（订单 → 到货单 → 入库单 → 发票 → 结算），采购手工结算 |
| 库存 | 其他入库 / 出库单、调拨单、调拨申请单、形态转换单、盘点单、货位调整单、材料出库单、产成品入库单、期初结存单 | 读取、新增、修改、删除、审核；材料出库、产成品入库参照生产订单或检验单生单 |
| 生产 | 生产订单、物料清单 | 读取、新增、修改、删除、审核、关闭（经 U8 API 框架） |
| 质量 | 来料 / 产品 / 其他报检单，来料 / 产品 / 其他检验单，来料 / 产品不良品处理单 | 读取、参照生单、删除、表头修改；检验单走 U8 审批流 |
| 应收应付 | 收款单、付款单、客户退款、供应商退款、应收单、应付单、应收 / 应付票据 | 读取、新增、修改、删除、审核；核销、取消核销、自动核销、制单、取消制单，票据登记与处理 |
| 只读 | 存货调价单、出入库调整单 | 读取、列表 |

完整类型表（行主键、生单来源、每种操作的限制）见 [接口参考](docs/api-reference.md)「单据类型」。

### 其他能力

| 能力 | 说明 |
| --- | --- |
| 审批流 | 质量单据的状态、历史、待办、提交、撤销、同意、不同意、退回、弃审、重新提交 |
| 总账 | 凭证读取、列表、新增、整张修改、作废、审核、出纳签字、删除、记账、红字冲销，按 U8 功能权限和科目权限检查 |
| 基础档案 | 客户、供应商、存货、部门、人员、仓库及分类，币种、汇率、凭证类别、结算方式、收发类别、计量单位、货位、项目、原因码、银行账户、联系人等：读取、列表、新增、修改、删除 |
| 列表与报表 | 单据和票据列表（筛选、只取主键、按 rowversion 增量同步），现存量，科目余额、辅助核算余额、往来余额与账龄、库存台账、收发存汇总、期初余额、固定资产折旧等只读报表 |
| 经营管理与公司间 | 1 到 3 个账套的利润表、销售毛利、往来账期、资金与存货、关键指标，可合并；同组公司间的对账、汇总、合并试算和买方生单。需单独的经营管理权限 |
| 按人员的权限 | 读取绑定到 U8 操作员，继承其功能权限、记录权限和字段权限；被遮蔽的字段返回 `null` 并列在 `masked_fields`。`perm/snapshot`、`perm/evaluate` 查询有效权限 |
| 只读账套 | 桥 `readOnlyAccounts`、API `U8CO_READONLY_ACCOUNTS` 中的账套只开放读取，写路由一律 403 `account_read_only` |
| 写入策略 | 按账套 × 单据类型 × 操作放行；冻结、时段、操作员、行数和金额上限、写入限额、许可点数保护；热加载，策略失效即拒写 |
| 写入分级 | 经 U8 组件完成的写入为第一级。结账、存货核算记账与期末处理、期初记账与期初单据、坏账、汇兑损益、应付票据、总账取消记账、损益结转与自定义转账等按 U8 界面执行的 SQL 写入为第二级（复现写入）：缺省关闭（桥 `enableReplicatedWrites`），开启后也只对 `testAccounts` 中的账套开放 |
| 预演 | 写路由带 `dry_run: true`：可回滚的在桥的事务中真实执行、读出结果后回滚；由 U8 自行提交的只做完前置检查。不写入 |
| 幂等与错误 | 全部写路由接受 `Idempotency-Key`，`idempotency/get` 按键查询首次结果；错误体带 `retryable`、`field`、`hint` |
| 面向代理 | `archives/resolve` 名称解析，`meta` / `meta/fields` 字段说明，`vouchers/search` 条件查询，`load_many` / `get_many` 批量读取，`fields` / `compact` 裁剪响应 |
| 并发与安全 | 写线程池与只读 SQL 线程池、按单据加锁、写闸门、登录缓存；OIDC、读写分级、两层账套白名单、请求签名、口令逐次加密、来源白名单、双端审计 |

各项的限制见 [已知限制](docs/limitations.md)。

## 环境要求

- 已授权的 U8+（在 V18.0 上开发和测试）和一个专用测试账套。
- U8 应用服务器：Windows Server、.NET Framework 4.8、PowerShell 7.4 以上。
- API 服务主机：Python 3.12 或 Docker。
- 支持 client credentials 的 OIDC 身份提供方。

安装、首次调用和账套准备见 [快速开始](docs/getting-started.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [快速开始](docs/getting-started.md) | 安装桥和 API 服务、客户端与命令行、端到端示例、账套准备、升级与卸载 |
| [架构](docs/architecture.md) | 组件、请求流程、线程与队列、锁、事务、预演、审计、桥协议 |
| [配置参考](docs/configuration.md) | 桥、API 服务、客户端、MCP 的全部配置；只读账套、写入策略、复现写入开关 |
| [接口参考](docs/api-reference.md) | 全部路由、字段、错误码、幂等键、预演、名称解析、报表 |
| [已知限制](docs/limitations.md) | 功能范围、写入分级与风险、一致性、安全，与 U8 官方接口的覆盖对照 |
| [U8 行为说明](docs/u8-notes.md) | 业务组件的调用方式、事务行为和已知差异 |
| [MCP 服务](docs/mcp.md) | 安装、配置、接入 AI 客户端、工具说明 |
| [单据事件](docs/events.md) | 事件语义、去重、消费示例、部署 |
| [测试](docs/testing.md) | 单元测试和在测试账套上的验证方法 |
| [常见问题](docs/faq.md) | 许可、版本、常见错误 |
| [贡献指南](CONTRIBUTING.md) · [安全](SECURITY.md) · [变更日志](CHANGELOG.md) · [`llms.txt`](llms.txt) | |

这些文档另以网页发布在本项目的 GitHub Pages 站点上，站点首页是交互式接口文档（由 OpenAPI 3.1 生成）。

## 许可

[Apache License 2.0](LICENSE)。见 [NOTICE](NOTICE)。

---

## English

**U8-ERP-CO-API** publishes the business components of Yonyou U8+ as an HTTP API (`/v1/co/*`, OpenAPI 3.1) for business systems, integration platforms and AI agents.

**It changes business data in U8.** Validate every write on a dedicated test account set first; open production account sets gradually using read-only accounts and the write policy.

### Components

- **Bridge** (`co/bridge`): a 32-bit Windows service on the U8 application server. It logs in to U8 as the operator supplied by the caller, calls the business component or runs a read-only query, commits and reads the result back on a fresh connection. It accepts only HMAC-signed requests from allow-listed source IPs.
- **API service** (`api/u8co_api`): a FastAPI app that validates OIDC tokens, enforces read / write / management permissions, account allow-lists, read-only accounts, the write policy and rate limits, then signs and forwards each call to the bridge.
- **Client and CLI** (`co/client`), **MCP server** (`mcp/`, Python standard library only) and **event service** (`events/`, document changes to Redis Streams, at-least-once, deduplicated by `event_id`).

Authorization is done by U8: every request carries a U8 operator and password, and U8 applies that operator's function and data permissions. Passwords are never stored or logged.

### Features

- Sales, purchasing, inventory, manufacturing, quality and AR/AP documents: load, create, update, delete, approve, close, and generate from source documents; U8 approval workflow for quality documents.
- General-ledger vouchers, AR/AP write-off and voucher generation, master data, document lists with incremental sync, on-hand stock and read-only reports.
- Management reporting and inter-company queries across 1 to 3 account sets (separate permission).
- Per-operator permissions: reads inherit the bound U8 operator's function, record and field permissions; masked fields are returned as `null` and listed in `masked_fields`.
- Read-only accounts, a hot-reloaded write policy, dry runs on every write route, idempotency keys and machine-readable errors.
- Replicated writes (period close, inventory accounting, openings, bad debt, exchange gain, AP notes, GL unpost and transfers) replay the SQL the U8 client executes. They ship **disabled** (`enableReplicatedWrites`) and, when enabled, are limited to `testAccounts`.

### Getting started

Requirements: a licensed U8+ (developed and tested on V18.0) with a test account set; Windows Server with .NET Framework 4.8 and PowerShell 7.4+; a host with Python 3.12 or Docker; an OIDC provider with client credentials. Follow [docs/getting-started.md](docs/getting-started.md). The documentation in `docs/` is written in Chinese; [`llms.txt`](llms.txt) indexes it.

Security reports: see [SECURITY.md](SECURITY.md). Contributions: see [CONTRIBUTING.md](CONTRIBUTING.md).

### License

[Apache License 2.0](LICENSE). See [NOTICE](NOTICE).
