# U8-ERP-CO-API

用友 U8+ ERP 的 REST API（OpenAPI 3.1），附 Python SDK / CLI、MCP 服务和单据事件流。
*An unofficial REST API for Yonyou U8+ ERP, with a Python SDK/CLI, an MCP server for AI agents and a document event stream.*

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml) [![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE) [![OpenAPI 3.1](https://img.shields.io/badge/OpenAPI-3.1-6BA539)](docs/api-reference.md) [![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB)](docs/getting-started.md)

**[📖 交互式接口文档 / Interactive API docs](https://12dora.github.io/U8-ERP-CO-API/)** · [快速开始](docs/getting-started.md) · [接口参考](docs/api-reference.md) · [English](#english)

## 它做什么

- 把 U8 的业务组件（COM）发布为 HTTP 接口 `/v1/co/*`，供业务系统、集成平台和 AI 代理调用。
- 写入经 U8 自身组件完成并由 U8 校验；读写都按调用方 U8 操作员的权限执行。
- 提供预演（`dry_run`）、幂等键、只读账套和写入策略，便于安全接入。

> ⚠️ 本项目会修改 U8 中的业务数据。先在测试账套验证，再按 [配置参考](docs/configuration.md) 逐步开放正式账套。

## 架构

```
业务系统 / AI 代理 ─HTTPS+OIDC→ API 服务（容器） ─HTTP+HMAC→ 桥（U8 应用服务器） ─COM / SQL→ U8
```

| 组件 | 目录 | 说明 |
| --- | --- | --- |
| 桥 | `co/bridge` | 32 位 Windows 服务，以调用方的 U8 操作员登录并调用业务组件 |
| API 服务 | `api/` | FastAPI；OIDC 鉴权、读写分级、账套白名单、写入策略、限流 |
| 客户端 | `co/client` | Python SDK 与命令行 |
| MCP 服务 | `mcp/` | 让 Claude Code、Cursor 等 AI 客户端读写 U8 |
| 单据事件 | `events/` | 单据与档案的变化写入 Redis Streams |

## 功能

| 领域 | 能力 |
| --- | --- |
| 单据 | 销售、采购、库存、生产、质量、应收应付：读取、新增、修改、删除、审核、关闭、参照生单 |
| 总账 | 凭证读写、审核、出纳签字、记账、红字冲销 |
| 应收应付 | 核销、取消核销、制单、票据登记与处理 |
| 基础档案 | 客户、供应商、存货、部门、人员、仓库等的读写 |
| 列表与报表 | 增量同步、现存量、余额与账龄、收发存、折旧等只读报表 |
| 经营管理 | 1–3 个账套的利润、毛利、账期、资金与存货，公司间对账与合并 |
| 审批流 | 质量单据的提交、审批、退回、弃审 |
| 面向 AI | 名称解析、字段说明、条件查询、批量读取、响应裁剪 |

结账、存货核算、期初等「第二级写入」缺省关闭，只对测试账套开放。详见 [已知限制](docs/limitations.md)。

## 快速开始

需要：已授权的 U8+（在 V18.0 上测试）及测试账套；装有 .NET Framework 4.8 和 PowerShell 7.4+ 的 Windows Server；Python 3.12 或 Docker；支持 client credentials 的 OIDC 身份提供方。

按 [快速开始](docs/getting-started.md) 安装桥和 API 服务，完成第一次调用。

## 文档

| 文档 | 内容 |
| --- | --- |
| [快速开始](docs/getting-started.md) | 安装、客户端、端到端示例、升级 |
| [接口参考](docs/api-reference.md) | 全部路由、字段、错误码 |
| [配置参考](docs/configuration.md) | 各组件配置、只读账套、写入策略 |
| [架构](docs/architecture.md) | 请求流程、事务、审计、桥协议 |
| [已知限制](docs/limitations.md) | 功能范围、写入分级、覆盖对照 |
| [U8 行为说明](docs/u8-notes.md) | 业务组件的调用方式与事务行为 |
| [MCP 服务](docs/mcp.md) · [单据事件](docs/events.md) · [测试](docs/testing.md) · [常见问题](docs/faq.md) | |

## English

U8-ERP-CO-API exposes Yonyou U8+ business components as an HTTP API (`/v1/co/*`, OpenAPI 3.1) for business systems, integration platforms and AI agents. Writes go through U8's own components and are checked by U8; every call runs with the caller's U8 operator permissions. It also ships a Python SDK/CLI, an MCP server and a Redis Streams event service.

**It changes business data in U8** — validate on a test account set first. Replicated writes (period close, inventory accounting, openings, etc.) are disabled by default.

Start with the [interactive API docs](https://12dora.github.io/U8-ERP-CO-API/) and [getting started](docs/getting-started.md). The docs are in Chinese; [`llms.txt`](llms.txt) indexes them.

## 声明与许可

本项目不是用友官方项目，与用友网络科技股份有限公司无关联，也未获其认可。「用友」「U8」「U8+」是其商标，此处仅用于说明兼容对象。本项目不包含、不分发任何用友软件；使用者须自备已授权的 U8，并自行确认许可条款允许此类集成。

*Not affiliated with or endorsed by Yonyou Network Technology Co., Ltd. "Yonyou", "用友", "U8" and "U8+" are its trademarks, used only to describe compatibility. No Yonyou software is included; you need your own licensed U8 and must confirm your license permits this integration.*

[Apache License 2.0](LICENSE) · [NOTICE](NOTICE) · [贡献指南](CONTRIBUTING.md) · [安全](SECURITY.md) · [变更日志](CHANGELOG.md)
