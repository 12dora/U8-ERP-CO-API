# 变更日志

本文件的格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.0] - 2026-10-04

首个公开版本。

### 新增

- **桥**（`co/bridge`）：U8 应用服务器上的 32 位 Windows 服务。每个请求以调用方提供的 U8 操作员登录，调用 U8 业务组件或执行只读查询，提交后在新连接上回读确认。HMAC 签名请求、口令逐次 AES 加密、来源 IP 白名单、账套白名单；写线程池与只读 SQL 线程池、按单据加锁、写闸门、登录缓存与登录复用、看门狗；审计日志；签名自检（`--check-signatures`）；许可点数采样与保护。
- **API 服务**（`api/u8co_api`）：FastAPI 发布 `/v1/co/*` 和 OpenAPI 3.1。OIDC 令牌离线校验，读、写、经营管理、权限评估四类权限，账套白名单与令牌账套声明，限流，按账套分流到多个桥，审计。
- **单据**：销售、采购、库存、生产、质量和应收应付单据的读取、新增、修改、删除、审核、关闭、锁定和参照生单；质量单据审批流；采购结算（含手工结算）。
- **总账与应收应付**：总账凭证读取、列表、新增、修改、作废、审核、出纳签字、删除、记账、红字冲销；应收应付核销、取消核销、自动核销、制单、取消制单；票据登记与处理。
- **基础档案、列表与报表**：基础档案读取、列表、新增、修改、删除；单据与票据列表（筛选、只取主键、按 rowversion 增量同步）；现存量；科目余额、辅助核算余额、往来余额与账龄、库存台账、收发存汇总、期初余额、固定资产等只读报表；账套体检 `reports/account_readiness`。
- **经营管理与公司间**：1 到 3 个账套的利润表、销售毛利、往来账期、资金与存货、关键指标（可合并）；公司间对账、多账套汇总、合并试算和买方生单。需要单独的经营管理权限。
- **按人员的权限**：读取可绑定到 U8 操作员，继承其功能权限、记录权限和字段权限；被遮蔽的字段返回 `null` 并列在 `masked_fields`；`perm/snapshot`、`perm/evaluate` 查询有效权限。
- **只读账套**：桥 `readOnlyAccounts` 与 API `U8CO_READONLY_ACCOUNTS` 中的账套只开放读取，写路由 403 `account_read_only`。
- **写入策略**：桥 `writePolicyFile` 与 API `U8CO_WRITE_POLICY_FILE`，按账套 × 单据类型 × 操作放行；冻结、时段、操作员、行数和金额上限、写入限额、许可点数保护；热加载，策略失效即拒写。
- **复现写入（第二级写入）**：月末结账、存货核算记账与期末处理、期初记账与期初单据、库存期初结存单、坏账、汇兑损益、应付票据、总账取消记账、期间损益结转与自定义转账。按 U8 界面执行的 SQL 写入，已在测试账套上实测核对。**缺省关闭**：桥 `enableReplicatedWrites`（缺省 `false`）未开启时 403 `feature_disabled`；开启后仍只对 `testAccounts` 中的账套开放，其他账套 403 `test_account_only`。健康检查报告 `replicated_writes`。
- **面向程序与代理**：全部写路由支持预演（`dry_run`）和幂等键（`Idempotency-Key`、`idempotency/get`）；结构化错误（`retryable`、`field`、`hint`）；名称解析 `archives/resolve`；字段元数据 `meta` 与字段标签 `meta/fields`；单据查询 `vouchers/search`；批量读取 `vouchers/load_many`、`archives/get_many`；响应裁剪 `fields`、`compact`。
- **客户端与命令行**（`co/client`）：直接调用桥的 Python 库和 CLI。
- **MCP 服务**（`mcp/`）：本地 stdio MCP 服务 `u8co-mcp`（仅用 Python 标准库），可接入 Claude Code、Claude Desktop、Cursor；只读模式；经营管理查询工具；多人共用的 HTTP 方式。
- **单据事件**（`events/`）：轮询单据、票据、应收应付处理、总账凭证和基础档案的变化，写入 Redis Streams；断电不丢、至少一次投递、按 `event_id` 去重。
