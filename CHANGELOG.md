# 变更日志

本文件的格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增

- 销售发票 `vouchers/search` 的 `code_like` 同时匹配账套配置的数电/金税发票号列；销售发票、应收应付单可按 `settled` 筛核销状态，列表/搜索行另有 `tax_invoice_no`（仅发票；无权看该列时为 null）、`amount`、`open_amount`、`settled`。销售发票的 `amount`、`open_amount`、`settled` 只含操作员有存货 / 仓库记录权限的明细行，无权看任何明细行或金额字段时为 null；无权看金额字段时按 `settled` 筛选 403「无权按核销状态筛选：缺少金额字段权限」。应收应付单的 `open_amount`、`settled` 只受记录权限约束（U8 无字段权限对象，不会遮蔽，按 `settled` 筛选也不会 403）。按被字段权限拒绝的表头自定义项筛选时 403「无权按表头自定义项 defineN 搜索」，返回的 `defines.defineN` 一并遮蔽。账龄分析与经营管理往来账期每项另有 `max_overdue_days`、`oldest_overdue_date`（无单据号；`oldest_overdue_date` 在 `basis=due` 时为到期日、`basis=document` 时为单据日期；应收应付都只看核销后净余额大于 0 的原单，按往来单位、原单类型、原单号分开合计；others/totals 取成员最大值及该成员的日期）。**升级顺序：先升级桥二进制，再在 `config.json` 写下 `taxInvoiceNoFields`；回滚到旧桥前先删除该键（旧桥遇未知键会拒绝启动，值为 `{}` 也不行）。先桥，后 API**。
- 客户专管业务员 `archives/customer_person`：只改 `Customer.cCusPPerson`（当前专管业务员对应部门与客户分管部门一致、或分管部门为空时一并改 `cCusDepart`），不走客户 EAI。新部门须在 `Department` 中、末级且未停用，否则不改分管部门，响应 `dept_note`；分管部门实际改动时同步 `Customer_Auth`（`Privilege_Type=0` 的部门维度，`autoId` 是 IDENTITY，插入不写该列）。请求 `code`、`person`，可选 `expect`（空串表示当前为空）。响应 `before` / `after`、`dept_before` / `dept_after`、`dept_note`。预演为 `validate`。写入分类 `(archives, customer_person)`，放行 `archives/update` 不连带放行。只读例外改为 `{accounts, operators, type, op}`，`op` 只收 `customer_person`；`archive` / `fields` 已取消（未知键进程起不来）。`archives/update` 在只读账套上仍一律 403。客户端 `arc_customer_person`，命令行 `arc-customer-person`。审计行另记 `read_only_exception`，健康检查列出例外。**升级顺序：先桥，后 API**。
- 客户端 `arc_update` 接受 `idempotency_key`（与 `arc_create` 一致）。
- `vouchers/search`、`reports/order_execution` 新增 `order`（`asc` 缺省，与以前完全一致；`desc` 最新在前）：按单据（订单）日期降序、主键降序，用 keyset 游标续读（`next` 原样放回 `after`，游标不能跨 `order` 使用），一次调用就能取到最新的单据。`vouchers/search` 的 `after` 在 `desc` 时是游标文本，`asc` 时仍是整数主键。响应在 `desc` 时另有 `order`，API 在请求给了 `order` 时另加 `order_applied`。**升级顺序：先桥，后 API**（API 对旧桥仍可用：旧桥回「含未知字段」/「不能设置字段」时去掉 `order` 重试一次，`order_applied` 为 `false`，列表仍是最旧在前）。桥的 `--selftest` 增加 `order`、游标编解码和降序 SQL 的核对。
- `stock/current` 每行新增 `inv_unit`（存货主计量单位名，`Inventory.cComUnitCode` → `ComputationUnit.cComUnitName`），不同存货的数量可以先看单位再比较。**升级顺序：先桥，后 API**（旧桥的行里没有该键）。
- `mgmt/sales` 按存货分类汇总：`group_by` 新增 `inventory_class`，`class_grade`（1 到 9）往上取该级次的祖先分类，`inv_class` 按存货分类编码前缀过滤收入行和成本行；`inventory` 维度的行另有 `inventory_unit`、`inventory_class_code`。
- `mgmt/sales` 关联方标注：按客户分组的行有 `ic_account`，每个账套有 `ic_totals`（所有关联方客户的合计，不受 `top` 影响）。桥新增 `ic_customers` 参数，多跑一遍只含这些客户的汇总；API 只在按客户分组时发送它。**升级顺序：先桥，后 API**（API 对不认识该字段的旧桥会去掉它重试一次，`ic_totals` 为 `null` 并提醒）。
- `mgmt/cash_stock` 的产量按存货分类：`production.items` 每项有 `class_code`、`class_name`，新增 `class_grade`、`inv_class`（只过滤产量）。
- `stock/current` 接受 `nonzero`（布尔），转给桥。
- `mgmt/cash_stock` 新增 `sections`（`cash`、`notes`、`inventory`、`production`、`purchases` 的不重复子集，缺省全部）：桥只算要的段，API 合并也只合要的段。只要 `production` 时功能权限改为产成品入库单查询（`ASM0301`，规则 key `report:mgmt/cash_stock#production`），不要总账权限，登录子系统 `ST`，不读 `mgmt/meta`、不出 `closed`；有其他段仍走原来的总账规则。产量段的数据权限对象与 `voucher:product_in` 相同（部门、仓库、存货必有，客户、供应商、业务员、收发类别可空）。桥的 `--selftest` 增加参数、规则选择、登录子系统、权限登记和产量 SQL 数据权限条件的核对。**部署顺序：先升级桥，再升级 API**（旧桥不认识 `sections`，API 会去掉它重试一次并在 `warnings` 提醒）。
- 质量统计报表 `reports/qm_indicators`（检验指标统计）、`reports/qm_pass_rate`（检验合格率）：`kind` 选 `incoming`（来料检验单）或 `product`（产品检验单），只含已审核（审核人非空）的检验单，`include_unverified` 可放开。`qm_indicators` 按（存货、检验指标）给检验行数、可转为数字的行数、均值、最小、最大、样本标准差（少于 2 个数值时为 `null`）、不良行数、按月走势和最近的不良批次，统计在桥里按小数解析后计算。`qm_pass_rate` 以一张检验单为一个批次，结论含「不合格」记不合格，含「让步」按合格计，按月份、供应商（仅来料）或存货分组，另给检验数量、合格数量、让步数量、不合格数量的合计；不返回比率，比率由调用方用返回的数量和批次数计算。其他检验单（QM15）不计。功能权限：来料指标 `QM040125_01`、产品指标 `QM040329_01`、来料合格率 `QM040101_01`、产品合格率 `QM040301_01`（规则 key 带 `:incoming` / `:product` 后缀，由请求里的 `kind` 选）；数据权限：存货必有，供应商可空（仅来料）。登录子系统同检验单列表。**升级顺序：先桥，后 API**（旧桥没有这两个路径）。
- 生产报表 `reports/production_daily`（产成品入库日报）、`reports/mo_progress`（生产订单完工状况）、`reports/mo_consumption`（生产订单用料分析）：日报按日或月、按产品或存货分类级次汇总已审核（`cHandler` 非空）的产成品入库数量和单据数，另给有入库的天数；完工状况每个订单行一行，给订单数量、合格入库数量、关闭状态和状态，`open_only` 只看未完成；用料分析按（母件、子件）汇总订单个数、订单数量、合格入库数量、子件的物料清单用量和已领用数量。完工率、单耗由调用方用返回的数量计算（分母用合格入库数量）。功能权限：日报 `ASM0301`（登录子系统库存 `ST`）、完工状况 `MO04005_01`、用料分析 `MO04005_01` 或 `MO03005Q`（登录子系统同生产订单读取）；数据权限对象分别同产成品入库单、生产订单。**升级顺序：先桥，后 API**。
- 库存：`reports/stock_age`（库龄分析）按（存货、仓库）列现存量，给最后一次已审核入库日期、最后一次已审核出库日期、库龄（距最后入库的天数）、`idle_days` 内没有出库的呆滞标记、按 `buckets` 分档的件数和金额，金额取最近已结账存货核算月份的结存单价乘数量；`sort` 可按金额或库龄，`by_wh=false` 合并仓库。`reports/stock_summary` 新增 `include_amount` 和每行 `unit`：请求区间正好是整月（月初到月末）时，给期初、收入、发出、结存金额（取存货核算期间汇总，`amount_basis: "month"`），区间不是整月时只有数量（`"partial"`），按仓库分组而存货核算不按仓库记账时也只有数量（`"no_wh"`）。库龄和收发存的金额需要存货核算查询权限 `IA3011_01`（规则 key `mask:amount`）；没有该权限的操作员照给数量，金额字段为 `null`，响应带 `masked_fields: ["amount"]`，不返回 403。库龄功能权限 `ST020205_01` 或 `ST020204_01`，数据权限：仓库、存货必有。**升级顺序：先桥，后 API**（旧桥不认 `include_amount`）。
- 采购报表 `reports/purchase_analysis`（采购综合统计）、`reports/arrival_otif`（采购时效）：综合统计的 `source` 取 `invoice`（采购发票，缺省，不含期初发票）或 `receipt`（已审核采购入库），按物料、供应商、月份任意组合（1 到 3 项）汇总数量、金额、价税合计、单据数，另给合计；发票口径金额取本币列。时效按订单行的计划到货日期窗口统计（已审核、未关闭的采购订单行，截至登录日期）：到货行数、准时（首次到货日期不晚于计划日期）、晚到、已逾期未到货、未到期，并列出最久的 `overdue_limit` 行逾期明细，到货日期取到货单。功能权限：采购综合统计 `PU050307_01`（供应商可空）、采购时效 `PU050314_01`（供应商必有），存货必有；登录子系统 `PU`。**升级顺序：先桥，后 API**。
- 销售报表 `reports/open_orders`（销售订单执行）、`reports/sales_price_history`（单据历史价格）、`reports/customer_churn`（客户新增、流失、下滑）：执行统计按 `basis`（`ship` 发货、`out` 出库、`invoice` 开票，缺省 `ship`）算每个订单行的未执行数量，只含未关闭且未执行数量大于 0 的行，按客户、存货或订单汇总，给未执行数量、未执行金额（未执行数量乘订单行本币含税单价）、已逾期行数和最早到期日；历史价格列出已审核销售发票的明细，新的在前，给本币无税单价和含税单价（金额除以数量，6 位小数），`customer` 和 `inv` 至少给一个；客户变化以已审核销售发票的无税收入为准，把 `date_from` 起的当前窗口与之前 `lookback_months` 个月的基线比较，分新增（基线前无发票）、流失（基线有收入、当前无）、下滑（月均低于基线月均的 `100 - decline_pct`%）三个清单，各清单按收入大小排序。功能权限：订单执行 `SA030105_01`（数据权限同销售订单）、历史价格 `SA031209` 或 `SA040401_01`、销售统计 `SA040401_01`；客户必有，业务员可空；登录子系统 `SA`。**升级顺序：先桥，后 API**。
- 收付款与现金流报表 `reports/receipts_payments`（收款单、付款单汇总）、`reports/notes_due`（票据到期分析）、`reports/cash_flow`（现金流量统计）：收付款按 `side` 取应收（收款单）或应付（付款单），已审核（审核人非空，期初除外），按日、月、往来单位、业务员、部门或结算方式最多两层分组，给单据数、收付款金额、退款金额和净额；票据到期按 `expire_from`、`expire_to`（缺省登录日期起 30 天）列票据，`open_only`（缺省 true）只含余额不为 0 的，给到期天数和状态（未结清、已结算、贴现、背书、退回）；现金流量统计按期间取 `GL_CashTable` 的现金流量项目金额，另给已记账但没有指定现金流量项目的现金科目凭证（`unassigned`）。功能权限：收款单查询 `AR0601031`、付款单查询 `AP0601031`、应收票据到期 `AR212013` 或 `AR2231`、应付票据 `AP212013` 或 `AP2231`、现金流量统计 `GL1302` 或 `GL1301`（规则 key 带 `:ar` / `:ap` 后缀，由请求里的 `side` 选）；数据权限：往来单位按收付款为必有、票据为可空，部门、业务员可空；现金流量统计无数据权限对象。登录子系统：收付款、票据按 `side` 为 `AR` / `AP`，现金流量 `GL`。**升级顺序：先桥，后 API**。
- 账务核对报表 `reports/close_check`（月末结账检查）、`reports/ia_anomalies`（存货核算检查）、`reports/cost_unit`（完工产品单位成本）：月末检查给各模块结账标志，以及期间内未审核的发货、销售发票、采购发票、各类出入库单，未记账凭证，存货核算未记账，应收应付未制单，损益未结转的数量和最多 5 个样例单号，只给该期间还开着的模块相关的项目；存货核算检查列出截至期末已审核采购入库中未开票的行（剩余数量乘单价估算金额、天数）和期间内没有存货核算明细的已审核出入库行；单位成本取成本核算模块的完工产品单位成本，按成本要素给每主计量单位的成本，缺省取最近已结账的期间。功能权限：月末检查同 `close_status`（`GL1512`、`GL1520`、`GL030301`、`GL0305`）、存货核算检查 `IA3017_01` 或 `IA3011_01`、完工产品成本 `CA0504_01` 或 `CA0512`；数据权限：存货核算检查按存货、供应商（可空）、仓库（可空）过滤，成本按存货过滤。登录子系统：月末检查、成本 `GL`，存货核算检查 `IA`。**升级顺序：先桥，后 API**。
- 字段权限（`AA_ColumnAuth`）同样作用于新增的分族报表：采购综合统计按 `source` 取采购入库单（`24`）或采购发票（`25`）的拒绝字段，订单执行取销售订单（`17`、`VCH_17`、`solist`），历史价格与客户新增 / 流失取销售发票（`07`、`VCH_07`、`VCH_13`），收付款汇总取收款单 / 付款单（`VCH_AR48` 等，按 `side`），存货核算检查的暂估金额取采购入库单，收发存汇总与库龄的金额取各自报表对象并从严并上出入库单列表。被拒绝的金额字段连同同行其他金额（含 `amount_tax`、`unit_price`、`unit_price_tax`、`est_amount`、`open_amount`、`refund_amount`、`net_amount`、`*_amt`）一起置 `null`，响应带 `masked_fields`；数量、单号、日期照给。销售订单、销售发票、采购发票的单据读取（`vouchers/*`）改按各自的字段权限对象遮蔽。质量统计、生产、采购时效、到期票据、现金流量、月末检查没有金额，完工产品单位成本在 U8 里没有字段权限对象，均不遮。
- 字段权限遮金额时，报表不再按被遮的金额排序、截断：`stock_age`（同没有 `IA3011_01` 的路径）、`purchase_analysis`、`receipts_payments`、`open_orders`、`customer_churn`、`ia_anomalies` 在查询前判断（`PermMask.WillMaskAmounts`），会遮时改按键、单据数、编码或日期，并回显实际用的排序（`sort` / `order`）。报表自己的字段权限对象上的任何拒绝按金额组处理（栏目标题对不上响应的键）；收款单、付款单及退款的单据读取按 `VCH_AR48` 等对象遮蔽。

### 变更

- 有字段权限对象的单据，`vouchers/load` 响应里的 `state.settled` 改与金额组一起遮蔽（无权看金额时为 null）。
- 桥按操作员固定写线程：需要登录的请求（写、`login-check`、需要登录对象的读、未命中登录缓存的纯 SQL 读）按操作员编码（去首尾空白、不分大小写）固定到一个写线程，同一操作员的这些请求在该线程上逐笔执行，桥自己的线程之间不再因同一操作员同时登录而回 422 `login_failed`「登录状态不是 0」。代价是时延：同一操作员的并发请求排队依次执行，哈希到同一写线程的不同操作员共用该线程；读路由和 `login-check` 最多排队 75 秒、写路由 45 秒内开始不了即放弃，都返回 503 `busy_timeout`（可重试）。命中登录缓存的纯 SQL 读路由仍在读线程池上并行；读线程不再登录，缓存失效时把任务转回该操作员固定的写线程。登录复用时，写线程为同一操作员新建登录前先关掉本线程缓存的该操作员的其他闲置登录（计入 `logins_closed`），同一操作员在几个账套之间交替时复用效果有限。健康检查新增 `pinning` 和按写线程的 `worker_queued`，原有字段不变。桥的 `--selftest` 增加固定规则、取任务和同一操作员腾退的核对。
- `mgmt/sales`、`mgmt/arap` 不再依赖总账：`mgmt/meta` 因功能权限不足（`no_permission`）读不到的账套照样出报表（没有水位和结账状态，`accounts[].close` 为 `null`，不缓存）。其他原因的 meta 失败行为不变。
- `mgmt/overview` 不再依赖总账水位，某一部分无权限时只缺这一部分：账套结果的 `missing` 列出读不到的部分，新增 `missing_reasons`（部分名 → 原因码，`no_permission` 或读取故障）；三部分都无权限才是 403。`mgmt/arap` 的 `side: "both"` 一边因权限读不到时，账套仍给另一边并带 `missing_sides`，`complete` 为 `false`。
- 经营管理（`/v1/co/mgmt/*`）和公司间汇总、合并试算：全部账套都因功能权限不足（`no_permission`）失败时回 403 `no_permission`，不再是 503 `unavailable`；有账套因其他原因失败时仍是 503，部分账套失败仍留在 `by_account`（`ok: false`、`error.code: no_permission`）。

### 修复

- 桥 `reports/mgmt/sales` 在 `group_by` 含 `period` 且 `period_from` 等于 `period_to` 时拼出只有 `ELSE` 的 `CASE`，SQL 语法错误；单个期间现在直接用期间号。

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
