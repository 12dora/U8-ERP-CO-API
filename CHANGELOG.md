# 变更日志

本文件的格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.1.0] - 2026-11-15

升级顺序：先桥，后 API。新增的只读例外项、`taxInvoiceNoFields` 等配置键只有新版桥认得，两端升级完成后再写入；回滚到 1.0.0 之前先删除这些配置。

### 新增

- **销售订单表头自定义项写入** `vouchers/so_defines`：写任意销售订单（含已审核、已有下游单据的订单）表头的 `cdefine1`–`cdefine16` 文本列，由调用方给出列、值和判定「空」的附加标记。`fields` 为 1 到 16 项；`expect` 必填，键与 `fields` 完全一致，给出调用方看到的当前值，NULL、空白和 `empty_values`（0 到 8 项）中的值都算空，其余去首尾空白后逐字比较，不符 409 `state_mismatch`（`detail.current`）；订单被他人锁定同码（`detail.reason=locked`）。目标值已全部落库时 `changed:false`。列须是订单表上的文本列，值不超过自定义项档案长度和列宽，否则 400 `bad_request`；行版本乐观锁，写后回读。`cascade: true` 在同一事务里把这些列写到下游发货单 / 退货单、销售出库单和销售发票表头：每张单据的全部来源订单在该列上都是目标值才改，否则跳过（`mixed_sources`、`locked`、`unchanged`）；下游列缺失、不是文本或太窄时 400、什么都不写；超过 200 张 409 `too_many_downstream`。响应带 `cascade` 清单，支持 `dry_run`。写入分类 `(sale_order, so_defines)`，写入策略的操作词表新增 `so_defines`（放行 `vouchers/update` 不连带放行）。客户端 `voucher_so_defines`，命令行 `voucher-so-defines`，MCP 路由目录同步。
- **一张销售发票参照多张发货单**：`vouchers/generate` 的 `sale_invoice`（参照发货单）可用 `sources: [{id, lines}, ...]` 代替 `id` + `lines`（1 到 200 张、合计 1 到 200 行，与 `id`、`lines` 互斥）。各张须蓝字、非期初、已审核、未关闭，且同客户、业务类型、币种、汇率、表头税率；行数量不超过剩余可开票数量。表头取第一张发货单，每行记自己的发货单号，每张来源都加单据锁；响应另有 `source_ids`。客户端 `generate_invoice_from_dispatches`。
- **销售订单、发货单、销售发票的可写标注列**：以下列在保存前按 `INFORMATION_SCHEMA` 列目录核对存在、类型和长度（列须在账套里存在，否则 400 `bad_request`），保存后逐项回读，与请求不同回滚并返回 409 `u8_rejected`，只回读本次请求给出的列。
  - 销售订单表头：`cgatheringplan`、`caddcode`、`cdefine1`–`cdefine16`；表体：`cunitid`、`inum`、`cwhcode`、`cbdefine1`–`99`（`SO_SODetails_extradefine`）。
  - 发货单（参照订单生成）表头：`caddcode`、`cdefine1`–`cdefine16`、表头扩展自定义项 `chdefine1`–`99`（`DispatchList_extradefine`）。
  - 销售发票（蓝字，参照发货单生成）表体：表体扩展自定义项 `cbdefine1`–`99`（`SaleBillVouchs_extradefine`）；红字发票不收表体标注。
  - 纯标注列和扩展自定义项不经 U8 组件，由桥在 `Save` 之后、提交之前的同一事务里写入；档案类字段（收付款协议、收货地址、仓库、计量单位）按档案校验；数量、件数、换算率保存后核对原样落库且满足 数量 = 件数 × 换算率。`vouchers/load` 与预演 `docs` 另带扩展自定义项列；`float` / `real` 类型的扩展自定义项按 6 位小数的十进制文本给出。`meta/fields` 的可写名单随之增加。
- **只读例外**：
  - `type=vouchers` 的项新增 `voucher`（单据类型）：可开放的 (`op`, `voucher`) 由 API 与桥同一张登记表决定，包括销售订单的 `create`、`update`、`close`、`verify`，发货单的 `verify`、`generate`（来源订单），销售发票的 `generate`（来源发货单，单张或 `sources`）、`verify`（只放行复核、弃复）；各项互不连带。
  - 单据项可用 `callers`（API 信任项 `name` 数组）代替 `operators`：验过签的令牌所属信任项命中时放行，不必随人员变动改配置。API 在写路由上以请求体字段 `caller_trust` 把它发给桥（含在 HMAC 签名内），桥不带该字段不命中。信任项 `name` 在信任文件和环境变量项之间必须唯一。审计增加 `read_only_match`，健康检查摘要列出 `callers`。
  - `type=vouchers` 的 `op` 增加 `head_fix`、`so_defines`，`type=archives` 的 `op` 增加 `customer_fields`、`customer_address`、`customer_address_add`、`customer_address_delete`、`create`、`delete`；`create` 可列 `district_class`、`customer`，`delete` 只能列 `district_class`。
- **客户档案受控写入**（均走受控 SQL，事务内加锁、`expect` 核对、写后回读，响应带 `before` / `after`）：
  - `archives/customer_fields`：`name`、`abbrname`、`tax_no`、`district`（须为 `DistrictClass` 末级）、`end_date`、`sa_protocol`（须为 `AA_Agreement` 中存在且未停用的编码）；简称撞唯一索引 409 `abbrname_taken`。
  - `archives/customer_person`：专管业务员，按规则同步分管部门和 `Customer_Auth`。
  - `archives/customer_address`、`customer_address_add`、`customer_address_delete`：收货地址的修改、新增、删除，编码被引用 409 `address_in_use`。
  - 409 `state_mismatch` 的 `detail.current` 给出结构化当前值（Python 客户端 `U8CoStateMismatch.current`）。客户端 `arc_customer_fields`、`arc_customer_person`、`arc_customer_address*`，命令行 `arc-customer-*`；`arc_update` 接受 `idempotency_key`。
- **已审核发货单表头修正** `vouchers/head_fix`：只改发货单表头的 `caddcode`、`cPersonCode`，并按规则同步只由这张发货单供货的销售出库单、销售发票；响应的 `undo` 记录实际写入的行，带 `undo` 反向调用可恰好还原。支持 `dry_run`；写入分类 `(dispatch, head_fix)`；客户端 `voucher_head_fix`，命令行 `voucher-head-fix`。
- **发票号检索**：配置 `taxInvoiceNoFields`（账套 → `SaleBillVouch` 表头文本列）后，销售发票 `vouchers/search` 的 `code_like` 同时匹配该列；销售发票、应收应付单可按 `settled` 筛选，列表行另有 `tax_invoice_no`、`amount`、`open_amount`、`settled`。账龄分析与经营管理往来账期每项另有 `max_overdue_days`、`oldest_overdue_date`。
- **排序**：`vouchers/search`、`reports/order_execution` 新增 `order`（`asc` 缺省，`desc` 最新在前，keyset 游标）；旧桥回「未知字段」时 API 去掉 `order` 重试一次并以 `order_applied: false` 提示。
- **报表**：
  - 质量：`reports/qm_indicators`、`reports/qm_pass_rate`。
  - 生产：`reports/production_daily`、`reports/mo_progress`、`reports/mo_consumption`。
  - 库存：`reports/stock_age`；`reports/stock_summary` 新增 `include_amount`。
  - 采购：`reports/purchase_analysis`、`reports/arrival_otif`。
  - 销售：`reports/open_orders`、`reports/sales_price_history`、`reports/customer_churn`。
  - 收付款与现金流：`reports/receipts_payments`、`reports/notes_due`、`reports/cash_flow`。
  - 账务核对：`reports/close_check`、`reports/ia_anomalies`、`reports/cost_unit`。
  - 字段权限（`AA_ColumnAuth`）同样作用于这些报表和相应的单据读取：被拒绝的金额字段连同同行其他金额置 `null`，响应带 `masked_fields`；会遮蔽金额时不再按金额排序、截断。
- **经营管理与库存**：
  - 经营管理（`mgmt/*`）、公司间对账与合并试算一次最多 8 个账套。
  - `mgmt/sales` 新增 `group_by: inventory_class`（`class_grade`、`inv_class`）和关联方标注（`ic_customers`、`ic_account`、`ic_totals`）。
  - `mgmt/cash_stock` 新增 `sections`、`class_grade`、`inv_class`；产量按存货分类。
  - `stock/current` 每行新增 `inv_unit`，并接受 `nonzero`。

### 变更

- 桥按操作员编码把需要登录的请求固定到一个写线程，同一操作员的请求逐笔执行，不再因同时登录回 422「登录状态不是 0」。代价是同一操作员并发请求排队；读路由和 `login-check` 排队超过 75 秒、写路由超过 45 秒回 503 `busy_timeout`（可重试）。健康检查新增 `pinning`、`worker_queued`。
- 桥「登录状态不是 0」自愈：同一操作员连续 `wedgedLoginRecycle`（缺省 2，0 关闭）次卡住后换掉其固定写线程；换线程后仍卡住时可请求重启进程（`wedgedLoginRestart`，只在以服务运行且失败动作允许时生效）。桥自己结束进程一律记入 `<root>\bridge-failfast.log`。新增审计事件 `login_wedged`、`worker_recycled`、`login_probe`、`restart_requested`，健康检查新增 `wedged_logins`。
- `mgmt/sales`、`mgmt/arap`、`mgmt/overview` 不再依赖总账水位：`mgmt/meta` 因功能权限不足读不到的账套照样出报表，某一部分无权限时只缺这一部分（`missing`、`missing_reasons`、`missing_sides`）。
- 经营管理和公司间汇总在全部账套都因功能权限不足失败时回 403 `no_permission`（原为 503 `unavailable`）。
- 只读例外的操作员比较统一为只忽略 ASCII 字母大小写；API 的 `vouchers/generate` 对显式 `"source_type": null` 回 400 `bad_request`，与桥一致。
- 有字段权限对象的单据，`vouchers/load` 的 `state.settled` 与金额组一起遮蔽。

### 修复

- 销售订单修改：数量重算不再冲掉本次请求给出的价格，只改数量时含税单价不再逐次漂移。
- `customer_fields` 改简称撞唯一索引时由 500 改为 409 `abbrname_taken`；API 错误体的 `field` 在 409 上同样转出。
- 只读例外的放行判断要求规则的 `op` 是该 `type` 可开放的 op，绕过配置加载直接构造的规则不放行 `archives/update`。
- 桥 `reports/mgmt/sales` 在 `group_by` 含 `period` 且起止期间相同时不再生成只有 `ELSE` 的 `CASE`。
- 桥停止服务时自己结束进程的记账规则：只有 `OnStop` 返回前向 SCM 报已停止成功才不记，其余一律记。

## [1.0.0] - 2026-10-04

首个公开版本。

### 新增

- **桥**（`co/bridge`）：U8 应用服务器上的 32 位 Windows 服务。每个请求以调用方提供的 U8 操作员登录，调用 U8 业务组件或执行只读查询，提交后在新连接上回读确认。HMAC 签名请求、口令逐次 AES 加密、来源 IP 白名单、账套白名单；写线程池与只读 SQL 线程池、按单据加锁、写闸门、登录缓存与登录复用、看门狗；审计日志；签名自检（`--check-signatures`）；许可点数采样与保护。
- **API 服务**（`api/u8co_api`）：FastAPI 发布 `/v1/co/*` 和 OpenAPI 3.1。OIDC 令牌离线校验，读、写、经营管理、权限评估四类权限，账套白名单与令牌账套声明，限流，按账套分流到多个桥，审计。
- **单据**：销售、采购、库存、生产、质量和应收应付单据的读取、新增、修改、删除、审核、关闭、锁定和参照生单；质量单据审批流；采购结算（含手工结算）。
- **总账与应收应付**：总账凭证读取、列表、新增、修改、作废、审核、出纳签字、删除、记账、红字冲销；应收应付核销、取消核销、自动核销、制单、取消制单；票据登记与处理。
- **基础档案、列表与报表**：基础档案读取、列表、新增、修改、删除；单据与票据列表（筛选、只取主键、按 rowversion 增量同步）；现存量；科目余额、辅助核算余额、往来余额与账龄、库存台账、收发存汇总、期初余额、固定资产等只读报表；账套体检 `reports/account_readiness`。
- **经营管理与公司间**：多个账套的利润表、销售毛利、往来账期、资金与存货、关键指标（可合并）；公司间对账、多账套汇总、合并试算和买方生单。需要单独的经营管理权限。
- **按人员的权限**：读取可绑定到 U8 操作员，继承其功能权限、记录权限和字段权限；被遮蔽的字段返回 `null` 并列在 `masked_fields`；`perm/snapshot`、`perm/evaluate` 查询有效权限。
- **只读账套**：桥 `readOnlyAccounts` 与 API `U8CO_READONLY_ACCOUNTS` 中的账套只开放读取，写路由 403 `account_read_only`。
- **写入策略**：桥 `writePolicyFile` 与 API `U8CO_WRITE_POLICY_FILE`，按账套 × 单据类型 × 操作放行；冻结、时段、操作员、行数和金额上限、写入限额、许可点数保护；热加载，策略失效即拒写。
- **复现写入（第二级写入）**：月末结账、存货核算记账与期末处理、期初记账与期初单据、库存期初结存单、坏账、汇兑损益、应付票据、总账取消记账、期间损益结转与自定义转账。按 U8 界面执行的 SQL 写入，已在测试账套上实测核对。**缺省关闭**：桥 `enableReplicatedWrites`（缺省 `false`）未开启时 403 `feature_disabled`；开启后仍只对 `testAccounts` 中的账套开放，其他账套 403 `test_account_only`。健康检查报告 `replicated_writes`。
- **面向程序与代理**：全部写路由支持预演（`dry_run`）和幂等键（`Idempotency-Key`、`idempotency/get`）；结构化错误（`retryable`、`field`、`hint`）；名称解析 `archives/resolve`；字段元数据 `meta` 与字段标签 `meta/fields`；单据查询 `vouchers/search`；批量读取 `vouchers/load_many`、`archives/get_many`；响应裁剪 `fields`、`compact`。
- **客户端与命令行**（`co/client`）：直接调用桥的 Python 库和 CLI。
- **MCP 服务**（`mcp/`）：本地 stdio MCP 服务 `u8co-mcp`（仅用 Python 标准库），可接入 Claude Code、Claude Desktop、Cursor；只读模式；经营管理查询工具；多人共用的 HTTP 方式。
- **单据事件**（`events/`）：轮询单据、票据、应收应付处理、总账凭证和基础档案的变化，写入 Redis Streams；断电不丢、至少一次投递、按 `event_id` 去重。
