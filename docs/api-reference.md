# 接口参考

本文列出全部路由、请求字段、响应字段和错误码。字段和校验以代码为准：API 服务的 Pydantic 模型在 `api/u8co_api/co_models*.py`，桥的校验在 `co/bridge/src/Requests*.cs`。API 服务运行时另提供 OpenAPI 3.1 文档（`GET /v1/openapi.json`，§17）。

## 目录

- [1. 两层接口](#1-两层接口)
- [2. 公共字段](#2-公共字段)
- [3. 路由总表](#3-路由总表)
- [4. 单据类型](#4-单据类型)
- [5. 读取 `vouchers/load`](#5-读取-vouchersload)
- [6. 审核 `vouchers/verify`](#6-审核-vouchersverify)
- [7. 新增 `vouchers/create`](#7-新增-voucherscreate)
- [8. 修改 `vouchers/update`](#8-修改-vouchersupdate)
- [9. 删除 `vouchers/delete`](#9-删除-vouchersdelete)
- [10. 关闭和打开 `vouchers/close`](#10-关闭和打开-vouchersclose)
- [11. 参照生单 `vouchers/generate`](#11-参照生单-vouchersgenerate)
- [12. 收付款单和应收应付单](#12-收付款单和应收应付单)
- [13. 审批流 `workflow/*`](#13-审批流-workflow)
- [14. 总账凭证 `gl/vouchers/*`](#14-总账凭证-glvouchers)
- [15. 基础档案 `archives/*`](#15-基础档案-archives)
- [16. 列表和现存量](#16-列表和现存量)
- [17. 健康检查、登录检查和 OpenAPI](#17-健康检查登录检查和-openapi)
- [18. 错误码](#18-错误码)
- [19. 只读报表 `reports/*`](#19-只读报表-reports)
- [20. 幂等键](#20-幂等键)
- [21. 字段元数据 `meta`](#21-字段元数据-meta)
- [22. 操作员权限](#22-操作员权限)
- [23. 预演 `dry_run`](#23-预演-dry_run)
- [24. 名称解析 `archives/resolve`](#24-名称解析-archivesresolve)
- [25. 裁剪响应 `fields`、`compact`](#25-裁剪响应-fieldscompact)
- [26. 字段标签 `meta/fields`](#26-字段标签-metafields)
- [27. 单据查询 `vouchers/search`](#27-单据查询-voucherssearch)
- [28. 批量读取 `vouchers/load_many`、`archives/get_many`](#28-批量读取-vouchersload_manyarchivesget_many)
- [29. 期初记账与期初单据 `openings/post`、`openings/arap`](#29-期初记账与期初单据-openingspostopeningsarap)
- [30. 账套体检 `reports/account_readiness`](#30-账套体检-reportsaccount_readiness)
- [31. 月末结账 `periods/close`](#31-月末结账-periodsclose)
- [32. 存货核算记账与期末处理 `ia/post`、`ia/period_end`](#32-存货核算记账与期末处理-iapostiaperiod_end)
- [33. 公司间接口（仅 API）](#33-公司间接口仅-api)
- [34. 经营管理查询](#34-经营管理查询)
- [35. 其他单据和写入](#35-其他单据和写入)

## 1. 两层接口

同一套业务路由有两个入口：

| 入口 | 路径 | 认证 | 错误体 |
| --- | --- | --- | --- |
| API 服务（推荐） | `/v1/co/<路由>` | OIDC Bearer JWT，读写分级 | `{"error":{"code","message","retryable","field","hint","detail"}}` |
| 桥（仅限内网、受信任的调用方） | `/u8co/v1/<路由>` | HMAC 签名 + 来源 IP 白名单，见 `architecture.md` | `{"ok":false,"code","message","field","hint","detail"}` |

`field`、`hint`、`detail` 可能没有（§18）。例如 `POST /v1/co/vouchers/load` 转发到桥的 `POST /u8co/v1/vouchers/load`。两层的请求字段相同，区别只有：

- API 收明文 `password`，加密成 `password_enc` 后转给桥；直接调桥时由调用方加密。
- API 的 `date`、`year` 可省略（§2）；桥要求必填。
- API 先用自己的模型校验，不合法是 400 `bad_request`「请求参数无效：<字段>」（`error.field` 是出错字段的路径），不访问桥；桥上同样的错误带更具体的中文 `message`。
- API 的每个 POST 路由另收查询参数 `fields`、`compact` 裁剪响应（§25）。
- API 另有令牌权限（§17）、账套白名单、只读账套、频率和并发限制。

下文路径省略前缀，写成 `vouchers/load`。成功响应在桥上都带 `"ok": true`，API 原样返回。请求和响应都是 UTF-8 JSON；桥的请求体上限 64 KiB，成功响应最多 8 MiB，错误响应最多 64 KiB。

## 2. 公共字段

除 `health`、`meta` 外，每个请求都带：

| 字段 | 说明 |
| --- | --- |
| `acc` | 三位账套号。必须在 API 的账套白名单（`U8CO_ACCOUNTS`）和桥的 `allowedAccounts` 里，否则 403 `account_not_allowed`，不登录 U8。只读账套（API `U8CO_READONLY_ACCOUNTS`、桥 `readOnlyAccounts`）的写路由 403 `account_read_only`（[configuration.md](configuration.md)） |
| `year` | 四位年度，即账套库年度（数据库名 `UFDATA_<账套>_<年度>` 里的年度），不一定等于当前会计年度。API 缺省取 `date` 的年份；账套库年度不同时要显式传 |
| `operator` | U8 操作员编码，1 到 20 个字符，不含空白、引号或分号 |
| `password` | 仅 API。U8 操作员口令，1 到 128 个字符，只在本次请求里使用，不保存、不写审计 |
| `password_enc` | 仅桥。加密后的口令，见 `architecture.md` |
| `date` | 登录日期 `yyyy-MM-dd`。API 缺省为 `U8CO_TIMEZONE`（缺省 `+08:00`）的今天。总账的会计年度取这个日期的年份 |

顶层出现未知字段是 400（桥上 `message` 为「含未知字段」）。单据主键 `id` 是 1 到 2147483647 的整数。

每次请求都用该操作员登录 U8，按它在 U8 里的功能权限、数据权限（记录级）和字段权限判断能不能做：越权 403，读取时字段权限隐去的字段置为 `null` 并列在 `masked_fields`（§22）。桥和 API 不保存操作员和口令。

## 3. 路由总表

「权限」一列是 API 的读写分级（§17），直接调桥没有这层分级。「第二级」见表后说明。

| 方法 | 路由 | 权限 | 作用 |
| --- | --- | --- | --- |
| GET | `health` | 读 | 桥的健康检查 |
| GET（桥上 POST） | `meta` | 读 | 字段元数据：单据类型、可写字段、档案标签、路由（§21） |
| POST | `login-check` | 读 | 校验操作员能否登录 |
| POST | `meta/fields` | 读 | 字段标签：本账套单据模板里的中文名、类型、必填、枚举（§26） |
| POST | `sale-orders/verify` | 写 | 销售订单审核、弃审（专用路由） |
| POST | `dispatches/verify` | 写 | 蓝字发货单审核、弃审（专用路由） |
| POST | `vouchers/load` | 读 | 读取单据 |
| POST | `vouchers/list` | 读 | 单据列表 |
| POST | `vouchers/search` | 读 | 按编号、往来单位、部门、存货、日期等条件查单据（§27） |
| POST | `vouchers/load_many` | 读 | 一次读同一类型的 1 到 20 张单据（§28） |
| POST | `vouchers/attachments/list` | 读 | 单据附件列表（§5） |
| POST | `vouchers/verify` | 写 | 按类型审核、弃审 |
| POST | `vouchers/create` | 写 | 新增 |
| POST | `vouchers/update` | 写 | 修改 |
| POST | `vouchers/delete` | 写 | 删除 |
| POST | `vouchers/close` | 写 | 关闭、打开 |
| POST | `vouchers/lock` | 写 | 销售订单锁定、解锁（§10；采购订单不支持） |
| POST | `vouchers/generate` | 写 | 参照生单 |
| POST | `notes/get` | 读 | 读一张应收 / 应付票据及其处理记录（§16） |
| POST | `notes/create` | 写 | 票据登记，同时生成收款单；应付票据（生成付款单）是第二级（§16） |
| POST | `notes/delete` | 写 | 删除还没处理的票据及其收款单；应付票据的删除是第二级（§16） |
| POST | `notes/process` | 写 | 应收票据结算、贴现、背书冲应付、退回；应付票据结算、退回是第二级。返回处理号（§16） |
| POST | `arap/writeoff` | 写 | 核销：收付款单对发票、应收应付单（§12） |
| POST | `arap/writeoff/cancel` | 写 | 按核销号整批取消核销（§12） |
| POST | `arap/writeoff/auto` | 写 | 自动核销：对一个客户（供应商）配对，一次核多行，可 `dry_run`（§12） |
| POST | `arap/voucher` | 写 | 应收 / 应付制单：一张单据或同类型 2 到 20 张合并生成一张总账凭证（§12） |
| POST | `arap/voucher/delete` | 写 | 取消制单：按外部业务号删凭证、清单据上的凭证号；删汇兑损益、坏账、应付票据处理生成的凭证是第二级（§12） |
| POST | `arap/transfer`、`arap/merge`、`arap/red_offset` | 写 | 应收冲应付 / 应付冲应收、并账、红票对冲，返回处理号（§12） |
| POST | `arap/process/cancel` | 写 | 按处理号取消应收应付处理；取消坏账、应付票据处理是第二级（§12、§16） |
| POST | `arap/process/voucher` | 写 | 按处理号制单；汇兑损益、坏账、应付票据处理的制单是第二级（§12、§16） |
| POST | `arap/exchange_gain`、`arap/exchange_gain/cancel` | 写（第二级） | 应收 / 应付汇兑损益及取消（§12） |
| POST | `arap/bad_debt` | 写（第二级） | 坏账发生、坏账收回、计提坏账准备，返回处理号 `HZAR`…（§12） |
| POST | `arap/process/list` | 读 | 应收 / 应付处理记录与期间批次摘要，供事件服务增量比对（§16） |
| POST | `openings/post` | 写（第二级） | 期初记账、取消记账：采购管理（`pu`）、存货核算（`ia`）（§29） |
| POST | `openings/arap` | 写（第二级） | 应收 / 应付期初单据的新增、删除、审核、弃审（§29） |
| POST | `periods/close` | 写（第二级） | 月末结账、取消结账、逐月结账（§31） |
| POST | `ia/post` | 写（第二级） | 存货核算正常单据记账、恢复记账（§32） |
| POST | `ia/period_end` | 写（第二级） | 存货核算期末处理、取消期末处理（§32） |
| POST | `stock/current` | 读 | 现存量 |
| POST | `workflow/state`、`workflow/history`、`workflow/tasks` | 读 | 审批状态、审批历史、待办 |
| POST | `workflow/submit`、`withdraw`、`approve`、`disagree`、`return`、`abandon`、`resubmit` | 写 | 审批动作 |
| POST | `gl/vouchers/load`、`gl/vouchers/list` | 读 | 总账凭证读取、列表 |
| POST | `gl/vouchers/attachments/list` | 读 | 总账凭证附件列表（§14） |
| POST | `gl/vouchers/digest` | 读 | 总账凭证摘要（指纹），供事件服务比对（§14） |
| POST | `gl/vouchers/create`、`update`、`void`、`unvoid`、`verify`、`unverify`、`sign`、`unsign`、`delete` | 写 | 总账凭证写操作 |
| POST | `gl/vouchers/post` | 写 | 总账记账（§14） |
| POST | `gl/vouchers/reverse` | 写 | 红字冲销：整张复制一张已记账的凭证、金额取负（§14） |
| POST | `gl/vouchers/unpost` | 写（第二级） | 取消记账，恢复到最近一次记账之前（§14） |
| POST | `gl/transfer/pnl`、`gl/transfer/custom` | 写（第二级） | 期间损益结转、自定义转账：按 U8 的转账定义生成结转凭证（§14） |
| POST | `archives/get`、`archives/list` | 读 | 基础档案读取、列表 |
| POST | `archives/create`、`update`、`delete` | 写 | 基础档案写操作 |
| POST | `archives/get_many` | 读 | 一次按编码读同一档案的 1 到 20 条（§28） |
| POST | `archives/resolve` | 读 | 按名称、简称、助记码查编码，一次最多 20 项（§24） |
| POST | `idempotency/get` | 读 | 按幂等键查第一次请求的结果（§20） |
| POST | `reports/close_status`、`gl_balance`、`gl_aux_balance`、`gl_detail`、`arap_balance`、`arap_aging`、`arap_detail`、`arap_writeoffs`、`bom` | 读 | 只读报表：月结状态、科目余额表、辅助核算余额表、科目明细账、往来余额、账龄分析、往来明细账、核销记录、物料清单（§19） |
| POST | `reports/stock_ledger`、`stock_summary`、`position_stock`、`batch_stock`、`customer_credit`、`price_list` | 读 | 只读报表：库存台账、收发存汇总表、货位存量、批次存量、客户信用、价格表（§19） |
| POST | `reports/order_execution`、`doc_trace` | 读 | 只读报表：订单执行、单据追溯（§19） |
| POST | `reports/opening_balance` | 读 | 只读报表：期初余额（库存、应收应付、总账，§19） |
| POST | `reports/fa_changes`、`fa_depreciation` | 读 | 只读报表：固定资产变动单、按卡片和期间的折旧（§19） |
| POST | `reports/account_readiness` | 读 | 账套体检：空账套能不能跑回归，逐项给出状态和修复提示（§30） |
| POST | `reports/mgmt/pnl`、`mgmt/meta`、`mgmt/sales`、`mgmt/arap_terms`、`mgmt/cash_stock` | 仅桥 | 单账套的经营管理报表（§34）。API 不直接开放，经下一行的 `/v1/co/mgmt/*` 按账套调用 |
| POST | `mgmt/meta`、`mgmt/pnl`、`mgmt/sales`、`mgmt/arap`、`mgmt/cash_stock`、`mgmt/overview` | 经营管理 | 仅 API：1 到 3 个账套的经营管理查询，可合并（§34） |
| POST | `reports/intercompany_match`、`reports/aggregate`、`reports/consolidation` | 经营管理 | 仅 API：公司间对账、多账套汇总、合并试算，按账套、往来单位返回数量和金额（§33） |
| POST | `intercompany/generate_buyer` | 写 | 仅 API：按卖方单据在买方账套参照公司间采购订单生成入库单或到货单（§33） |
| POST | `perm/snapshot` | 读 | 登录操作员自己的有效权限（§22） |
| POST | `perm/evaluate` | 权限评估 | 指定操作员的有效权限，只给信任项写了 `perm_evaluate: true` 的调用方（§22） |
| GET | `/v1/openapi.json` | 任意已认证调用方 | 仅 API：OpenAPI 文档 |

**预演。** 标「写」的路由都可以在请求体里带 `dry_run: true`：走同样的检查和锁，但不写入（§23）。例外：`sale-orders/verify`、`dispatches/verify` 不支持预演（经 API 是 400「请求参数无效：dry_run」）；`arap/writeoff/auto` 的 `dry_run` 表示「只出计划」。

**分级标记。** OpenAPI 里每个 `/v1/co` 操作带扩展字段 `x-u8co-access`（`read`、`write`、`mgmt`、`perm_evaluate`），与「权限」一列相同。配置了写入策略（[configuration.md](configuration.md)）时，写路由还要过策略的放行规则、冻结和时段。

**第二级写入。** 标「第二级」的写入（以及 §4 里期初结存单的写入）没有可调用的 U8 组件，或改的是跨模块的期初 / 结账状态，由桥执行与 U8 界面相同的 SQL（已在测试账套上与 U8 界面执行的 SQL 实测核对）。桥在登录 U8 之前（含预演）依次检查（例外：`arap/voucher/delete` 要读出凭证才知道它是否属于第二级，在登录之后、事务里任何写入之前检查，见 [limitations.md](limitations.md)「第二级：复现写入」）：

1. 桥配置 `enableReplicatedWrites` 为 `true`（缺省 `false`），否则 403 `feature_disabled`，`message` 为「第二级写入未开启：」加下面各路由的原文；
2. 账套在桥的 `testAccounts` 里，否则 403 `test_account_only`。

`health` 的 `replicated_writes` 显示开关状态。这类写入只用于测试账套；正式账套请在 U8 客户端操作（风险见 [limitations.md](limitations.md)）。

## 4. 单据类型

`type` 不在下表里是 400「单据类型无效」。「行主键」是修改时的 `line_id`，也是生单时来源行的 `source_line_id`。

| `type` | 单据 | 读取 | 审核 | 新增 | 修改 | 删除 | 关闭 | 生单来源 | 行主键 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sale_order` | 销售订单 | 是 | 是 | 是 | 是 | 是 | 是 | | `iSOsID` |
| `dispatch` | 发货单（蓝字） | 是 | 是 | 无来源 | 是 | 是 | | `sale_order` | `iDLsID` |
| `sale_invoice` | 销售发票 | 是 | 复核 | 先开票 | 是 | 是 | | `dispatch`（缺省）、`sale_return`（红字发票）、`sale_invoice`（红冲蓝字发票） | `AutoID` |
| `sale_out` | 销售出库单 | 是 | 是 | 无来源（账套未启用销售管理时） | 是 | 是 | | `dispatch`（整张或按行） | `AutoID` |
| `sale_return` | 退货单（红字发货单） | 是 | 是 | | 是 | 是 | | `dispatch`、`sale_return_apply` | `iDLsID` |
| `sale_return_apply` | 退货申请单（卡片 SA31） | SQL | 是 | 参照发货单 | 是 | 是 | | | `AutoID` |
| `purchase_order` | 采购订单 | 是 | 是 | 是 | 是 | 是 | 是 | | `PO_Podetails.ID` |
| `arrival` | 到货单 | 是 | 是 | 无来源 | 是 | 是 | 是 | `purchase_order` | `Autoid` |
| `purchase_return` | 采购退货单（红字到货单） | 是 | 是 | | 是 | 是 | | `arrival`（缺省）、`purchase_order` | `Autoid` |
| `purchase_requisition` | 请购单 | 是 | 是 | 是 | 是 | 是 | 整单 | | `AutoID` |
| `purchase_invoice` | 采购发票 | SQL | 复核 | | 是 | 是 | | `purchase_in` | `ID` |
| `purchase_settle` | 采购结算单（卡片 99） | SQL | 没有审核 | 手工结算 | | 是 | | `purchase_invoice`（整张发票自动结算） | `ID` |
| `inventory_price_adjust` | 存货调价单（销售，卡片 SA18） | SQL | | | | | | | `autoid` |
| `purchase_in` | 采购入库单 | 是 | 是 | 无来源 | 是 | 是 | | `purchase_order`（缺省）、`qm_incoming_check`、`purchase_return`（生成红字入库）、`arrival`（蓝字到货单） | `AutoID` |
| `other_in` | 其他入库单 | 是 | 是 | 是 | 是 | 是 | | | `AutoID` |
| `other_out` | 其他出库单 | 是 | 是 | 是 | 是 | 是 | | | `AutoID` |
| `transfer` | 调拨单 | 是 | 是 | 是 | 是 | 是 | | `transfer_request` | `autoID` |
| `shape_change` | 形态转换单 | 是 | 是 | 是 | 是 | 是 | | | `autoID` |
| `transfer_request` | 调拨申请单 | 是 | 是 | 是 | 是 | 是 | | | `autoID` |
| `stock_check` | 盘点单 | 是 | 不支持 | 是 | | 是 | | | `autoID` |
| `position_adjust` | 货位调整单（单据类型 19） | SQL | 是 | 是 | | 是 | | | `autoID` |
| `ia_adjust` | 出入库调整单（存货核算，单据类型 20 / 21 等） | SQL | 记账见 `ia/post` | | | | | | `AutoID` |
| `stock_opening` | 期初结存单（库存期初，单据类型 34） | 是 | 第二级（没有记账） | 第二级，每行一张 | | 第二级 | | | `AutoID` |
| `product_in` | 产成品入库单 | 是 | 是 | | 是 | 是 | | `qm_product_check`（缺省）、`qm_product_reject`、`production_order` | `AutoID` |
| `material_out` | 材料出库单 | 是 | 是 | 无来源 | 是 | 是 | | `production_order` | `AutoID` |
| `production_order` | 生产订单 | SQL | 是（U8 API） | 是（U8 API） | 是（U8 API，§8） | 是（U8 API） | 是 | | `MoDId` |
| `bom` | 物料清单（标准 BOM） | SQL | 是（U8 API） | 是（U8 API） | 是（U8 API） | 是（U8 API） | | | `sort_seq`（§8） |
| `qm_incoming_inspect` | 来料报检单（QM01） | SQL | 保存时自动 | 生单 | | 是 | | `arrival` | `AUTOID` |
| `qm_product_inspect` | 产品报检单（QM02） | SQL | 保存时自动；可单独弃审 | 生单 | | 是 | | `production_order` | `AUTOID` |
| `qm_incoming_check` | 来料检验单（QM03） | SQL | 审批流 | 生单 | 只改表头 | 是 | | `qm_incoming_inspect` | `AUTOID`（报检单表体） |
| `qm_product_check` | 产品检验单（QM04） | SQL | 审批流 | 生单 | 只改表头 | 是 | | `qm_product_inspect` | `AUTOID`（报检单表体） |
| `qm_incoming_reject` | 来料不良品处理单（QM05） | SQL | 是（受审批流控制的走审批流） | 生单 | | 是 | | `qm_incoming_check` | `ID`（检验单） |
| `qm_product_reject` | 产品不良品处理单（QM06） | SQL | 是（受审批流控制的走审批流） | 生单 | | 是 | | `qm_product_check` | `ID`（检验单） |
| `qm_other_inspect` | 其他报检单（QM11） | SQL | 是（新增后桥按选项补审核） | 无来源 | 只改表头 | 是 | | | `AUTOID` |
| `qm_other_check` | 其他检验单（QM15） | SQL | 是（没有审批流） | 生单 | 只改表头 | 是 | | `qm_other_inspect` | `AUTOID`（其他报检单表体） |
| `ar_receipt` | 收款单 | 是（UFAPBO） | 是 | 是 | 是 | 是 | | | `ID` |
| `ap_payment` | 付款单 | 是（UFAPBO） | 是 | 是 | 是 | 是 | | | `ID` |
| `ar_refund` | 客户退款（应收的付款单，`AR` / `49`） | 是（UFAPBO） | 是 | 是 | 是 | 是 | | | `ID` |
| `ap_refund` | 供应商退款（应付的收款单，`AP` / `48`） | 是（UFAPBO） | 是 | 是 | 是 | 是 | | | `ID` |
| `ar_bill` | 应收单 | 是（UFAPBO） | 是 | 是 | 是 | 是 | | | `Auto_ID` |
| `ap_bill` | 应付单 | 是（UFAPBO） | 是 | 是 | 是 | 是 | | | `Auto_ID` |

共 41 种可读取类型。「SQL」表示读取直接查表，不走业务组件。「生单」表示只能经 `vouchers/generate` 参照来源新增（§11），`vouchers/create` 是 400。`ia_adjust`、`inventory_price_adjust` 只读，写路由一律 400。退货申请单、退款单、无来源销售出库、手工结算、红冲蓝字发票的细则见 §35。

质量单据：

- 报检单（QM01、QM02）没有审批流，不能修改、关闭，也不开放单独审核：U8 在保存时按选项自动审核，删除时桥先弃审（§6、§9）；产品报检单可单独弃审（§6）。
- 检验单（QM03、QM04）的审核走 `workflow/*`，可修改表头（§8「质量单据」），不能关闭。
- 不良品处理单（QM05、QM06）可参照检验单生单、删除；不受审批流控制的可直接审核、弃审，受控的走 `workflow/*`；不能修改。
- 其他报检单（QM11）、其他检验单（QM15）没有来源单据、没有审批流（`IsWfControlled` 恒为 0，`workflow/*` 400）。其他报检单可无来源新增（§7）、审核、弃审、删除；其他检验单可参照其他报检单生单（§11）、审核、弃审、删除；两者都可修改表头。

各类型使用的 U8 登录子系统：销售和质量单据 `SA`，库存 `ST`，采购 `PU`，收款单、客户退款和应收单 `AR`，付款单、供应商退款和应付单 `AP`；生产订单读取和关闭 `SA`，审核、新增、修改和删除 `MO`；物料清单读取和列表 `SA`，审核、新增、修改和删除 `BO`。不带类型的路由：总账 `GL`，档案 `AS`，列表、现存量、`login-check` 和不带 `type` 的待办 `SA`。个别写入另用 `QM`、`AR`、`AP`，在各节注明。

## 5. 读取 `vouchers/load`

请求：`type`、`id`。

响应：

| 字段 | 说明 |
| --- | --- |
| `type`、`id`、`code` | 类型、主键、单据号 |
| `head` | 表头。值是 U8 存成的字符串，空值省略。二进制列（含 `ufts`）用十六进制 |
| `lines` | 表体，每行同样是字符串字典。超过 500 行时只返回前 500 行，并带 `lines_truncated: true` |
| `state` | `verified`、`verifier`、`verified_at`。生产订单另有 `closed`。采购发票的 `verified` 是采购复核，另有同义的 `reviewed`、`reviewer`、`reviewed_at`，以及应付审核 `ap_verified`、`ap_verifier`（`cPBVVerifier`）；采购发票、销售发票另有 `arap_verified`、`arap_verifier`、`arap_verified_at`、`gl_voucher`（§6） |
| `wf` | 仅检验单和不良品处理单（QM03 到 QM06）：审批状态，结构同 `workflow/state` 的 `wf` |
| `source` | 仅报检单：来源单据 `{"type","id","code"}`。来料报检单是到货单（`id` 是 `PU_ArrivalVouch.ID`），产品报检单是生产订单（`id` 是 `MoId`）；表头没有来源主键时为 null，其他报检单恒为 null |
| `allocations` | 仅生产订单：子件分配，另有 `allocations_truncated` |
| `merge_sources` | 仅合并检验（`BMERGECHECKFLAG=1`）的产品检验单：每个合并来源（`QMMergeCheckDetail`）一项：`source_line_id`（来源 `AUTOID`，参照生成产成品入库时用）、`mo_code`、`mo_seq`、`mo_detail_id`（`MoDId`）、`inspect_code`（来源报检单号）、`qualified`（合格）、`concession`（让步接收）、`stocked`（累计入库）、`remaining`（合格 + 让步 − 累计入库，不小于 0）、`done`（U8 已标为入库完毕，`BPROINFLAG=1`）。非合并检验没有这个键 |
| `version` | 仅物料清单：版本号 |

销售订单和发货单的读取由 U8 按操作员的数据权限过滤。收付款单和应收应付单的主键对应的行若是别的 `cFlag` / `cVouchType`（例如供应商退款），按不存在处理，404。

**报检单**（QM01、QM02、QM11）只取固定的一组列（列名同 U8 单据模板）：

- 表头：单号 `CINSPECTCODE`、报检日期 `DDATE`、报检人 `CMAKER`、报检部门 `CINSPECTDEPCODE`（`CINSPECTDEPNAME`）、业务部门 `CDEPCODE`、供应商或客户（带名称）、来源 `CSOURCE` / `CSOURCECODE` / `CSOURCEID`、到货日期、检验类型、审核人和审核日期；自定义项 `CDEFINE1`–`CDEFINE16`。
- 表体：`AUTOID`、来源行 `SOURCEAUTOID`（到货单行 `Autoid` 或生产订单行 `MoDId`）、存货（带名称、规格、主计量单位）、仓库、批号、报检数量 `FQUANTITY` / 件数 `FNUM`、累计检验数量 `FSUMCHECKQTY` / 件数 `FSUMCHECKNUM`、生产订单号和行；自由项 `CFREE1`–`CFREE10`、自定义项 `CDEFINE22`–`CDEFINE37`。
- 下游检验单（QM03 / QM04 / QM15）表头的 `INSPECTID` / `INSPECTAUTOID` 指回报检单的 `ID` / `AUTOID`。
- 其他报检单（QM11）来源各列为空，一般只有一行。U8 不回写它的 `FSUMCHECKQTY`；是否已检验看表体 `BFLAG`：`1` 为已检验，NULL 或 `0` 都是未检验（不要只判 `= 0`）。

其他检验单（QM15）的读取同来料、产品检验单（表头整行、检验项目表体），只是没有 `wf`。

**物料清单**（`bom`，`id` 是 `bom_bom.BomId`）只读标准 BOM（`BomType=1`），替代 BOM 400「只支持标准物料清单」。`code` 是母件存货编码（表头没有单号）。键是固定的小写下划线名：

- 表头：`bom_id`、`inv_code`、`inv_name`、`inv_std`、`part_id`（母件 `bas_part.PartId`）、`version`、`version_desc`、`eff_date`、`end_date`、`status`（1 未审核、3 已审核、4 停用）、`verifier`、`verified_at`、`parent_scrap`、`wf`、`created_by`、`created_at`、`modified_by`、`modified_at`、`ufts`（十进制）。
- 每行：`line_id`（`OpComponentId`）、`sort_seq`、`op_seq`、`inv_code`、`inv_name`、`inv_std`、`base_qty_n`、`base_qty_d`、`comp_scrap`、`wip_type`、`wh_code`、`dept_code`、`eff_beg`、`eff_end`、`remark`、`fv_flag`、`product_type`、`byproduct_flag`、`offset_days`、`plan_rate`、`accu_cost_flag`、`optional_flag`、`mutex_rule`、`cost_wip_rel`、`aux_unit`、`extras`（`"1"` 表示该行带替代料、定位符、分段损耗或自由项，接口不能修改这张清单；辅计量看 `aux_unit`，§8）。
- `state.verified` 是 `status` 为 3，`closed` 是 `status` 为 4。数据权限按母件存货。

**采购结算单**（`purchase_settle`，`id` 是 `PurSettleVouch.PSVID`，`code` 是结算号 `cSVCode`）读取只查表。写入有参照采购发票自动结算（`vouchers/generate`，§11）、手工结算（`vouchers/create`，§35）和删除（§9）；修改、审核 400（U8 里也没有）。

- `head` 是 `PurSettleVouch` 的原列，另加供应商名称 `ven_name`、`ven_abbname`。
- `lines` 是 `PurSettleVouchs` 的原列（`iRdsID` 入库单行、`iBsID` 发票行、`iSVQuantity` 结算数量、`iSVPrice` 结算金额、`iSVAPrice` 暂估金额、`bAccount` 存货核算已处理结算成本等），每行另加入库与发票的对照：`in_id`（采购入库单 `RdRecord01.ID`）、`in_code`、`in_date`、`in_qty`（入库行数量）、`in_aprice`（入库行暂估金额）、`in_price`（入库行金额，同月结算时 U8 改成结算金额）、`invoice_id`（采购发票 `PBVID`）、`invoice_code`、`invoice_date`、`invoice_qty`、`invoice_money`（发票行本币无税金额）。`iBsID` 为 0 的行是红蓝入库对冲，发票对照为空；上游类型 `cUpSoType` 不是 `01`（入库单）的行入库对照为空。
- `state.verified` 恒为 false（结算单只有制单人），另有 `line_count`、`accounted_lines`（`bAccount=1` 的行数）、`accounted`（每行都已处理结算成本）、`invoice_count`、`receipt_count`（涉及的发票、入库单张数）。
- 表头、表体的 rowversion 是 `psufts` / `psdufts`。数据权限按供应商、存货（表体），部门、业务员、采购类型、表体仓库为可空列；功能权限认「结算单列表查询」`PU040305`（U8 没有单独的结算单查询功能）。

**出入库调整单**（`ia_adjust`，表 `JustInVouch` / `JustInVouchs`，`id` 是表头自增列 `id`，`code` 是单号 `cJVCode`）只读。入库调整（`cVouchType=20`，卡片 0401）、出库调整（`21`，卡片 0402）和发出商品等其他调整类型同在一张表，不按类型拆分。

- `head` 是表头原列，另加 `wh_name`、`dep_name`、`rd_name`；`lines` 是表体原列（按单号 `cJVCode` 关联；`iJVPrice` 调整金额、`cbAccounter` 记账人、`CorID` / `cCorCode` 被调整单据等），另加 `inv_name`、`inv_std`。
- 调整单不改现存量和货位，只记存货核算明细账（`IA_Subsidiary`）。
- 没有审核：`state.verified`（同 `state.posted`）表示表体每行都已记账；`verifier` 取表头记账人 `cAccounter`，为空时取表体的记账人；`verified_at` 为空串。`state` 另有 `line_count`、`posted_lines`、`vouch_type`、`vouch_name`（`20` 入库调整单、`21` 出库调整单，其他类型为空串）、`auto`（`cAuto=TRUE`，期末处理自动生成的出库调整单）。
- 记账、期末处理（会生成、删除出库调整单）走 `ia/post`、`ia/period_end`（§32）。数据权限按存货（表体），仓库、部门、业务员为可空列。

**存货调价单**（`inventory_price_adjust`，表 `SA_InvPriceJustMain` / `SA_InvPriceJustDetail`，`id` 是表头 `id`，`code` 是 `ccode`）只读：`head`、`lines` 是原列，另加 `dep_name`、`inv_name`、`inv_std`；`state.verified` 按审核人 `cverifier` 非空，`verified_at` 是审核日期 `dverifydate`（未审核为空串）。审核后 U8 把新价写进存货价格表（`reports/price_list` 的 `kind=inventory`）。列名按 U8 数据字典，尚未用实际数据核对。

### 单据附件 `vouchers/attachments/list`

请求同 `vouchers/load`（`type`、`id`），权限同读取该单据（功能权限和记录级数据权限，越权 403）。只查数据库，不调用 U8 组件。单据不存在 404 `not_found`。

U8 单据卡片上的「附件」登记在通用附件表 `VoucherAccessories`：`VoucherTypeID` 是卡片号（`vouchers.CardNumber`，如销售订单 `17`），`VoucherID` 是该卡片主键列（`vouchers.VchTblPrimarykeyNames`，应收应付单是 `cLink`）的值。桥列出同一张表头表的全部卡片（例如发货单和退货单共用 `DispatchList`）下挂在这张单据上的附件。

响应 `{"ok":true,"type","id","items","truncated"}`。每项 `card`（卡片号）、`file_id`、`name`（原文件名）、`memo`、`size`（内容存在 U8 库里时的字节数，否则 null）、`stored`（`database`：内容在 `FileContent` 列；`file_server`：只登记了 U8 文件服务器上的文件标识）。按文件名排序，最多 500 个，超过时 `truncated` 为 true。只列清单，不提供下载（`limitations.md`）。各键按 U8 附件表结构写出，尚未用实际附件核对，使用前请先核对 `card`、`file_id`、`stored`。

客户端命令：`attachments --type <类型> --id <主键>`。

## 6. 审核 `vouchers/verify`

请求：`type`、`id`、`action`（`verify` 审核，`unverify` 弃审；采购发票、销售发票另有 `arap_verify`、`arap_unverify`）。

成功响应：`type`、`id`、`action`，以及（有则返回）`verified_by`、`verified_at`、`acc`（收付款单、应收应付单和生产订单可能不返回）、`state`、`generated`、`ar_verifier`（销售发票的应收审核人）。

通用规则：

- 调用前单据已经处于目标状态：409 `state_mismatch`。
- 成功的判定：U8 返回成功，桥在本连接提交后 `@@TRANCOUNT` 为 0，并且在一条新连接上（不加 `NOLOCK`）读到目标状态：审核人和审核日期都非空、审核人等于登录操作员的姓名；弃审则两者都空。回读对不上是 409 `state_mismatch`，这时事务已经提交，不要再发同一次审核。

### 销售

- 销售发票的审核是复核、弃复，状态列 `cChecker`、`dverifydate`。应收已审核的发票弃复由 U8 拒绝，409 `u8_rejected`（U8 原文）；先用 `arap_unverify` 弃审。
- 红字销售发票：只接受参照退货单生成的红字发票（表头 `iDisp=1`、没有发货单的 `SBVID` 指回本发票、每一行都指向退货单行），销售组件用红字的 VT（26 → 1，27 → 3）；先开票等其他红字发票 400「仅支持蓝字销售发票和参照退货单生成的红字销售发票」。
- 退货单（`sale_return`）：同发货单的审核、弃审，组件换成红字发货单（VT 10）。`id` 必须是红字发货单（`bReturnFlag=1`、`cVouchType=05`），否则 400「该单据不是退货单（红字发货单）」；审批流检查同发货单。

### 应收审核、应付审核（`arap_verify`、`arap_unverify`）

只收采购发票（应付款管理的审核，登录子系统 `AP`）和销售发票（应收款管理的审核，登录子系统 `AR`）；其他类型 400 `bad_request`「只有采购发票、销售发票支持应收应付审核」（经 API 时请求校验就失败）。只收专用、普通发票（采购 `01` / `02`，销售 `26` / `27`），其他发票类型 400「只支持专用发票和普通发票」（先于状态检查）。

- 调用：UFAPBO 的 `clsPub_AP`，先 `PUVouchCanSign` / `SAVouchCanSign`，再 `Sign_PurBill` / `Sign_SaleBill`（弃审 `CancelSign_PurBill` / `CancelSign_SaleBill`），都在桥的事务里。
- 审核要求：已复核（采购发票 `cVerifier`、销售发票 `cChecker` 非空，否则 409「发票未复核，请先复核」）；未审核；`IsWfControlled` / `iswfcontrolled` 为 0（否则 409 `workflow_enabled`）；采购发票没有网络锁（`iNetLock`）；表体没有 `cClue`；登录日期所在年月应付（应收）未结账（`GL_mend.bflag_AP` / `bflag_AR`，否则 409「应付已结账」/「应收已结账」）。
- 弃审要求：已审核；明细没有凭证号（409「已生成凭证，请先删除凭证」）；没有核销或其他处理（`cProcStyle` 不等于单据类型的行，或本单作为对方单据的行：409「已核销或有其他处理，请先取消」）；审核登记行所在期间未结账。审核人是否本人由 U8 判断。
- 除上面的 400 和 `workflow_enabled` 外，拒绝都是 409 `state_mismatch`；`CanSign` 或 `Sign` 返回 false 时 409 `u8_rejected`（U8 原文）。
- 写入：`cPBVVerifier`（销售发票 `cVerifier`、`dArverifydate`）为登录操作员姓名、日期为登录日期，在 `Ap_Detail`（`Ar_Detail`）登记原始行；不生成凭证。登记行的往来科目 `cCode` 由桥在同一事务里调 U8 的 `clsPub_AP.CusVenToCtrlKMForSAPU`（往来单位、币种、销售 / 采购类型、存货、`AR` / `AP`）补上（与 U8 客户端审核的结果一致，制单按这一列取往来科目）；取不到时整笔回滚，409 `state_mismatch`「取不到往来单位 … 的往来控制科目，请先在 U8 应收应付的科目设置里设好」。
- 桥在提交前核对审核人与登记行，提交后在新连接上回读：不符或回读失败都是 504 `outcome_unknown`（已提交，先核对，不要直接重投）。
- 响应的 `verified_by`、`verified_at` 是应付（应收）审核人和日期，`state` 为 `arap_verified`、`arap_verifier`、`arap_verified_at`、`gl_voucher`（任一明细行已有凭证号）。
- 功能权限：采购发票 `AP050104` 审核、`AP050105` 弃审，销售发票 `AR050104`、`AR050105`；数据权限按表头供应商（客户）和部门。

### 采购

- 采购发票：`verify` / `unverify` 是采购管理的复核、取消复核（`VoucherCO_PU` 的 `ConfirmBill` / `CancelconfirmBill`），状态列 `cVerifier`、`cAuditDate`。只收普通采购的专用、普通发票（`01` / `02`）；`IsWfControlled=1` 或采购发票已发布审批流时 409 `workflow_enabled`。红字发票（`bNegative=1`）同样可复核、取消复核（`Init` 的 `bPositive` 传 false）。复核要求未复核；取消复核要求已复核，且未在应付款管理审核（409「已在应付款管理审核，请先弃审」）、未结算、未现付、没有应付明细，否则 409 `state_mismatch`。U8 拒绝时 409 `u8_rejected`（U8 原文）。桥在提交前核对复核人确实写入或清除，提交后在新连接上回读；回读失败 504 `outcome_unknown`。
- 采购退货单（`purchase_return`）：与到货单同一组方法（`ConfirmArr` / `CancelconfirmArr`），用红字的 `Init` 参数（§11）。`id` 不是退货单（`iBillType` 不为 1）时 400「不是采购退货单」；弃审时已报检或已入库 409 `state_mismatch`。到货单（`arrival`）只收蓝字。
- 请购单（`purchase_requisition`）：`VoucherCO_PU` 的 `ConfirmApp` / `CancelconfirmApp`，状态列 `cVerifier`、`cAuditDate`。只收普通采购；`IsWfControlled=1` 或请购单已发布审批流时 409 `workflow_enabled`；表头或任一行已关闭 409。弃审时已被采购订单参照（`PO_Podetails.iAppIds`）或已有累计订货、合同、询价数量 409 `state_mismatch`。

### 库存

- 调拨单：审核成功时 U8 生成其他出库单和其他入库单（都未审核），响应多一个 `generated: [{"type":"other_out","id":…},{"type":"other_in","id":…}]`。弃审删除这两张生成单；生成单已审核时 U8 拒绝。
- 形态转换单（`shape_change`）：同调拨单（12 参 `Verify`），生成转换出库（其他出库，`cSource=形态转换`、`cBusCode` 为形态转换单号）和转换入库（其他入库）各一张，响应带 `generated`。弃审时生成单由 U8 删除；生成单已审核时 409「生成的其他出入库单已审核，请先在 U8 里弃审」。形态转换单或盘点单生成的其他出入库单不能单独弃审（409「该单据由形态转换单生成，不能弃审」「……由盘点单生成……」），也不能单独修改、删除（来源不是库存）。
- 调拨申请单（`transfer_request`）：只写审核人（9 参 `Verify`），不生成调拨单。弃审前表头或任一行已关闭、已有调拨单参照（`TransVouchs.iTRIds`）或累计调拨数量不为 0 时 409 `state_mismatch`。
- 盘点单（`stock_check`）：不支持审核、弃审（U8 组件生成盘盈 / 盘亏其他出入库单时报「类型不匹配生单时出错」）。桥在任何 COM 调用之前 400「盘点单审核暂不支持（U8 生成盘盈盘亏单时报类型不匹配），请到 U8 客户端审核」；经 API 时类型不在可审核名单里，同样 400。在 U8 客户端审核的盘点单，审核人列是 `cAccounter`、审核日期 `dveridate`。
- 货位调整单（`position_adjust`）：9 参 `Verify` / `UnVerify`，不生单。U8 审核时写货位台账 `InvPosition`（`cvouchtype=19`，每行一出、一入两条）并改货位结存 `InvPositionSum`，弃审退回。桥在同一事务里：调用前按本单的净变动查结存（审核看调出，弃审看退回，按货位、存货、批号、自由项合计，不够 409 `stock_shortage`）；调用后核对审核人（审核后是登录操作员且有审核日期，弃审后都为空）、本单的货位台账行数（审核后是行数的 2 倍，弃审后 0）和每个结存的变动等于调整单，不符回滚 409 `u8_rejected`。响应另有 `ledger_rows`（台账行数）和 `bin_moves`（每个结存的 `wh`、`pos`、`inv`、`batch`、`before`、`after`）。审核人列 `chandler`、审核日期 `dVeriDate`。
- 期初结存单（`stock_opening`）：只有审核、弃审（U8 的库存期初没有记账），第二级写入（§3、§7）。走 `USERPCO.VoucherCO`，审核日期由桥在同一事务里改成单据日期（与 U8 录入的期初单一致）；存货核算已期初记账后弃审 409 `state_mismatch`。

### 质量

- 报检单不开放单独审核。来料报检单（`qm_incoming_inspect`）审核、弃审都 400 `bad_request`「来料报检单不支持单独审核、弃审（保存时由 U8 自动审核；删除时桥会先弃审）」（经 API 时请求校验就失败）。产品报检单（`qm_product_inspect`）只收 `action=unverify`，`verify` 400「产品报检单只支持弃审（action=unverify）：审核由 U8 在保存时按选项自动完成」（经 API 时请求校验就失败，直接调桥在登录前 400）。
- 产品报检单弃审走 `VoucherOperate(…, "unconfirm", …)`（同删除前的弃审，U8 自己提交，预演只做校验），要求已审核（409「单据未审核」）且还没有检验单（行累计检验数量大于 0 或有检验单关联，409「已有检验单，不能弃审」）；回读同删除。弃审后接口不能再审核，也不能修改（§8「质量单据」），只能删除或在 U8 客户端处理。功能权限 `QM02020107`，数据权限按每行存货、仓库和表头部门。
- 报检单只在保存时按质量选项（来料 `bArrInspectAutoVerify`、产品 `bProInspectAutoVerify`）自动审核，U8 组件的审核对未审核的报检单无效（`u8-notes.md`「质量单据新增」）。选项关闭的账套里经本服务生成的报检单是未审核的，不能用来生成检验单。
- 检验单（QM03、QM04）不能直接审核，请用 `workflow/*`。经 API 时请求校验就失败（400「请求参数无效」）；直接调桥 400「该单据类型不支持直接审核」。
- 不良品处理单（`qm_incoming_reject`、`qm_product_reject`）：U8 质量管理组件的 `AuditVoucher` / `UnAuditVoucher`（先 `GetTheVoucher` 载入），登录子系统 `QM`，由 U8 自己提交（不在桥的事务里）。受审批流控制（`IsWfControlled=1`）或已提交审批（`iVerifyStateNew` 非 0）的 409 `workflow_enabled`，请用 `workflow/*`。审核要求未审核，弃审要求已审核且没有下游（同删除的下游判断），否则 409 `state_mismatch`。U8 拒绝时 409 `u8_rejected`（`ErrBag` 原文）；U8 报单据不存在或没有数据权限时 404。调用后在新连接上回读审核人（`CVERIFIER`），回读失败或调用异常且状态未到 504 `outcome_unknown`。响应另有顶层 `verified_by`、`verified_at`（弃审后为空串）。功能权限：来料 `QM02010305` 审核、`QM02010307` 弃审，产品 `QM02020305`、`QM02020307`；数据权限按表头供应商、存货、仓库。
- 其他检验单（`qm_other_check`）：组件 `UFQMCo.clsOtherCheckVoucherCO`，调用、回读、错误码和响应同不良品处理单（预演只做校验）。没有审批流（万一受控或已提交审批 409 `workflow_enabled`）。弃审要求没有下游：累计入库数大于 0、被入库单 `rdrecords01/08/09/10.iCheckIdBaks` 引用、已有不良品处理单都是 409 `state_mismatch`。功能权限：审核 `QM02060205`、弃审 `QM02060207`；数据权限按表头部门、存货、仓库。
- 其他报检单（`qm_other_inspect`）：组件 `UFQMCo.clsOtherInspectVoucherCO`，调用、回读、错误码和响应同不良品处理单。用于补审新增后没有自动审核的单据（`auto_verify_error`，§7），也可弃审后再审核。弃审要求还没有其他检验单（`INSPECTID` 指向本单或行 `BFLAG=1`，否则 409「已有其他检验单，不能弃审」）。功能权限：审核 `QM02060105`、弃审 `QM02060107`；数据权限按报检部门、每行存货、仓库。

### 应收应付单据、生产订单、物料清单

- 收付款单、应收应付单走 UFAPBO 的 `Sign` / `CancelSign`，响应带 `state`：`verified`、`verifier`、`verified_at`、`gl_voucher`、`settled`、`amount`、`unsettled_amount`。
- 生产订单（`id` 是 `MoId`）走 U8 API 框架，登录子系统 `MO`。`state.verified` 表示全部明细行都已审核，另有 `closed`、`verifier`、`verified_at`。U8 生产制造服务（`U8MPool`）没在运行时 503 `u8_unavailable`；受审批流控制的订单 409 `workflow_enabled`。
- 物料清单（`bom`，`id` 是 `BomId`）走 U8 API 的 `BomAuditing` / `BomUnauditing`（参数是母件 `PartId`、1、版本号字符串），登录子系统 `BO`。审核只收未审核（`Status=1`），弃审只收已审核（`Status=3`），停用的 409「物料清单已停用」；`IsWFControlled=1` 409 `workflow_enabled`。功能权限要有物料清单卡片的「审核」（`BO01001A`）或「弃审」（`BO01001UA`），母件存货要在数据权限内（403）。U8 审核时会顺延同一母件其他版本的失效日期；弃审时 U8 不查生产订单是否在用这个版本，桥也不拦。桥在新连接上回读 `Status`，没到目标时：调用异常 504、IPC 错误 503、U8 返回成功却不符 409。响应带 `code`（母件）、`version` 和 `state`（同读取）。

### 专用审核路由

`sale-orders/verify`（`id` 为 `SO_SOMain.ID`）和 `dispatches/verify`（`id` 为 `DispatchList.DLID`）：请求 `id`、`action`，成功 `{"ok":true,"acc","id","action","verified_by","verified_at"}`。`verified_by` 取 `cVerifier`，`verified_at` 优先 `dverifysystime`，否则 `dverifydate`。

发货单只接受蓝字：红字（`bReturnFlag`）、`cVouchType` 不是 `05`、期初 `bFirst = 1` 都直接拒绝。已有退货单行（`iCorID`）指向其明细的发货单不能弃审，409「发货单已有退货单，请先删除退货单」（`vouchers/verify` 的 `dispatch` 同样）。

这两条路由检查 U8 审批流：`AuditBizObjects` 里没有该表的行，或审批流表不可用，409 `workflow_unknown`；有行并且 `Table_WorkFlowRelease` 里该业务对象有 `Status = 0`、事件为 `<对象ID>.Submit` 的发布，或单据自己的 `iswfcontrolled` 为真，409 `workflow_enabled`。查不到不当成「没有启用」。

## 7. 新增 `vouchers/create`

请求：`type`、`head`（一层对象，值只能是字符串、数字或布尔）、`lines`（1 到 200 行，同样是一层对象）。

允许的类型：

- 通用名单（本节「可写字段」）：`sale_order`、`purchase_order`、`other_in`、`other_out`、`transfer`、`purchase_in`（无来源）、`purchase_requisition`、`shape_change`、`transfer_request`、`stock_check`、`position_adjust`、`stock_opening`；
- 无来源新增：`dispatch`、`sale_invoice`（先开票）、`arrival`、`material_out`、`qm_other_inspect`；
- 应收应付：`ar_receipt`、`ap_payment`、`ar_bill`、`ap_bill`、`ar_refund`、`ap_refund`（§12、§35）；
- 自有字段：`production_order`（1 到 50 行）、`bom`；
- 见 §35：`sale_out`（无来源）、`sale_return_apply`、`purchase_settle`（手工结算）。

其他类型 400「该单据类型不支持新增」。固定资产变动单不在 API 里（§15「固定资产卡片与设备台账的写入」）。

成功：`{"ok":true,"type","id","code","state"}`。采购订单、请购单、生产订单和应收应付四种另有 `lines`（保存后的表体行数）；生产订单另有 `allocates`、`details`、`warnings`，物料清单另有 `version`、`components`。收付款单和应收应付单可能不带 `state`。新单据保持未审核（其他报检单例外，见下）。

字段值不能填主键和来源行 id（`id`、`autoid`、`isosid`、`idlsid`、`iposid`、`poid`、`dlid`、`cbsysbarcode`）、单号、制单人、审核人、`ufts`、`editprop`，也不能填销售订单行的关闭人和累计数量（`cscloser`、`ifhquantity`、`ikpquantity`、`foutquantity`）。行号 `irowno` 可以填。不在该类型允许名单里的字段是 400「不能设置字段 x」，在名单里但不在 U8 行集 schema 里的是 400「未知字段 x」。自定义项和自由项不接受前导零（`cdefine01`、`cfree07`）。

### 可写字段

表头自定义项 `cdefine1` 到 `cdefine16`，表体自定义项 `cdefine22` 到 `cdefine37`，自由项 `cfree1` 到 `cfree10`。除此之外：

| 类型 | 表头 | 表体 |
| --- | --- | --- |
| `sale_order` | `ccuscode`、`cstcode`、`cdepcode`、`cpersoncode`、`cbustype`、`cexch_name`、`iexchrate`、`itaxrate`、`ddate`、`cmemo`、`ccusoaddress`、`cshipaddress`、`cscode`、`cpaycode`、`dpredatebt`、`dpremodatebt` | `cinvcode`、`iquantity`、`inum`、`cunitid`、`cgroupcode`、`igrouptype`、`ccomunitcode`、`iinvexchrate`、`iquotedprice`、`iunitprice`、`itaxunitprice`、`imoney`、`itax`、`isum`、`inatunitprice`、`inatmoney`、`inattax`、`inatsum`、`inatdiscount`、`idiscount`、`kl`、`kl2`、`itaxrate`、`dpredate`、`dpremodate`、`cmemo` |
| `purchase_order` | `cvencode`、`cdepcode`、`cpersoncode`、`cptcode`、`cbustype`、`cexch_name`、`nflat`、`itaxrate`、`dpodate`、`cmemo`、`darrivedate`（只作行计划到货日的缺省） | `cinvcode`、`iquantity`、`iunitprice`、`itaxprice`、`ipertaxrate`、`darrivedate`、`cbmemo`、`cunitid`、`inum` |
| `other_in` / `other_out` | `cwhcode`、`crdcode`、`cdepcode`、`cpersoncode`、`ddate`、`cmemo`、`cvencode`、`ccuscode`、`citemcode` | `cinvcode`、`iquantity`、`inum`、`iunitcost`、`iprice`、`cbatch`、`cposition`、`cbmemo`、`dmadedate`、`dvdate`、`imassdate`、`cmassunit`、`cassunit`、`iinvexchrate` |
| `stock_opening`（不能修改） | `cwhcode`、`crdcode`、`cdepcode`、`cpersoncode`、`ddate`（不用）、`cmemo`、`cvencode` | `cwhcode`（覆盖表头）、`cinvcode`、`iquantity`、`inum`、`iunitcost`、`iprice`、`cbatch`、`cposition`、`dmadedate`、`dvdate`、`imassdate`、`cmassunit`、`cassunit`、`iinvexchrate`、`citem_class`、`citemcode` |
| `transfer` | `cowhcode`、`ciwhcode`、`cordcode`、`cirdcode`、`codepcode`、`cidepcode`、`dtvdate`、`cmemo`、`cpersoncode` | `cinvcode`、`itvquantity`、`cassunit`、`cbatch`、`cbmemo` |
| `shape_change` | `davdate`、`cdepcode`、`cpersoncode`、`cirdcode`、`cordcode`、`cavmemo` | `cinvcode`、`cwhcode`、`bavtype`、`igroupno`、`iavquantity`、`cassunit`、`cavbatch`、`cbmemo` |
| `transfer_request` | `dtvdate`、`cowhcode`、`ciwhcode`、`codepcode`、`cidepcode`、`cordcode`、`cirdcode`、`cpersoncode`、`ctvmemo` | `cinvcode`、`itvquantity`、`itvchkquantity`（核准数量）、`cassunit`、`ctvbatch`、`cbmemo` |
| `position_adjust`（不能修改） | `cwhcode`、`ddate`、`cdepcode`、`cpersoncode`、`cmemo` | `cinvcode`、`cbposcode`（调出货位）、`caposcode`（调入货位）、`iquantity`、`cassunit`、`cbatch`、`cbmemo` |
| `stock_check`（不能修改） | `cwhcode`、`dcvdate`、`dacdate`、`cdepcode`、`cpersoncode`、`cirdcode`、`cordcode`、`ccvmemo` | `cinvcode`、`icvquantity`（账面）、`icvcquantity`（实盘）、`cassunit`、`ccvbatch`、`ccvreason`、`cbmemo` |
| `purchase_requisition` | `ddate`、`cdepcode`、`cpersoncode`、`cbustype`（只能是「普通采购」）、`cmemo` | `cinvcode`、`fquantity`、`drequirdate`、`darrivedate`、`cvencode`（建议供应商）、`ioricost`、`ioritaxcost`、`ipertaxrate`、`cbmemo` |
| `purchase_in`（无来源） | `cwhcode`、`crdcode`、`cdepcode`、`cpersoncode`、`ddate`、`cmemo`、`cvencode`、`cptcode` | `cinvcode`、`iquantity`、`inum`、`iunitcost`、`iprice`、`ioritaxcost`、`itaxrate`、`cbatch`、`cposition`、`cbmemo`、`dmadedate`、`dvdate`、`imassdate`、`cmassunit`、`cassunit`、`iinvexchrate` |

修改用同一份名单。金额、价税、辅数量由桥计算（U8 的业务组件保存时不重算），算法见 `u8-notes.md`「金额」。有计量单位组的存货还要辅计量，缺了 U8 会拒绝。

必填项与缺省：

- 采购订单：供应商（400「必须填写供应商」）、部门（400「必须填写部门」）；每行存货（400「必须填写存货编码」）、大于 0 的数量（400「数量必须大于 0」）、无税单价或含税单价之一（400「必须填写单价或含税单价」）。表头缺省：采购类型 `cptcode` 取默认采购类型（`PurchaseType.bDefault = 1`），没有时不写、由 U8 判断（U8 要求时 409 `u8_rejected` 带原文）；业务类型 `cbustype`「普通采购」；币种 `cexch_name` 取本位币（`foreigncurrency.iotherused = -1`），汇率 `nflat` 1；表头税率 `itaxrate` 13（固定缺省，请按账套需要自己给）；日期 `dpodate` 取登录日期。行税率 `ipertaxrate` 不给时：请求表头给了 `itaxrate` 就跟表头，否则取存货档案的 `iTaxRate`，档案没填仍跟表头（修改时新增的行同理）。
- 销售订单：制单人写登录操作员姓名。币种同无来源发货单：不带币种或填本位币时写本位币、汇率 1，另给的汇率必须是 1（400 `head.iexchrate`）；外币必须给大于 0 的汇率（U8 组件不补，汇率空时报「汇率不可以小于等于0」）。表体预完工日期缺省依次取行上的预发货、表头预完工、表头预发货、单据日期；预发货日期（U8 模板必输）缺省取表头预发货日期（早于本行预完工时取预完工，U8 要求预完工不晚于预发货），没有时取本行预完工日期，再取单据日期。多计量单位存货由桥按存货档案补计量单位组、单位、换算率和件数。
- 调拨单：转出仓（400「必须指定转出仓库」）、转入仓（400「必须指定转入仓库」）。收发类别 `cordcode`、`cirdcode` 只用调用方给的，桥不补缺省；U8 要求时 409 `u8_rejected` 带原文。
- 采购入库单（无来源）：仓库（400「必须指定仓库」）、供应商（400「必须指定供应商 cvencode」）；每行存货、大于 0 的数量。
- 请购单：每行存货（400「必须填写存货编码」）、大于 0 的数量 `fquantity`（400「数量必须大于 0」）。表头没有必填项。

### 形态转换单、调拨申请单、盘点单

三类都走 `USERPCO.VoucherCO`（登录子系统 `ST`），与调拨单同一组方法：`Insert` 13 参、`Update` 12 参、`Delete` / `UnVerify` 9 参，都在请求连接的事务里；单号按 U8 编号规则取（`sVouchType` 15、62、18，卡片 0305、0324、0307）；空白模板取视图 `AssemM` / `AssemD`、`transrequestm` / `transrequestd`、`checkm` / `checkd`。备注、批号用各表自己的列名（`cavmemo`、`ctvmemo`、`ccvmemo`，`cavbatch`、`ctvbatch`、`ccvbatch`），不做 `cmemo` 别名。

- 形态转换单（`shape_change`，表 `AssemVouch` / `AssemVouchs`，只认 `cVouchType='15'`，同表的组装单 13、拆卸单 14 当作不存在）：表头没有仓库，每行要有仓库 `cwhcode`（400「形态转换单每行都要有仓库 cwhcode」）、存货、大于 0 的 `iavquantity`、`bavtype`（「转换前」或「转换后」）；`igroupno` 缺省 1，每组至少一行转换前、一行转换后（400「形态转换每组（igroupno）都要有转换前和转换后的行」），修改后按整单再核一次。日期 `davdate` 缺省取登录日期；收发类别缺省按名称取末级类别「转换出库」（`cordcode`）、「转换入库」（`cirdcode`），账套里没有就留空。
- 调拨申请单（`transfer_request`，表 `ST_AppTransVouch` / `ST_AppTransVouchs`）：转出仓、转入仓必填（同调拨单的 400）；每行存货和大于 0 的 `itvquantity`。核准数量 `itvchkquantity`：0 或以上，最多 6 位小数，不能大于申请数量（400「核准数量不能大于申请数量」；修改时只调小申请数量也按整行复核）；新增时（含修改时新增的行）不给取申请数量，修改已有行时只在送了才改；核准件数按换算率补。U8 审核不写核准数量；参照生成调拨单按核准数量算，核准为 0 的行不能参照。不做关闭 / 打开。
- 盘点单（`stock_check`，表 `CheckVouch` / `CheckVouchs`）：盘点仓库 `cwhcode` 必填（400「必须指定盘点仓库」）；每行存货和实盘数量 `icvcquantity`（可以是 0）。账面数量 `icvquantity` 不给时按现存量填（`CurrentStock` 该仓库、该存货、同批号、同自由项 1–10 的合计，没给的批号和自由项按空比；不分货位）。盘盈数量 `iAdInQuantity` = 实盘 − 账面（大于 0 时，否则 0）、盘亏数量 `iAdOutQuantity` = 账面 − 实盘（大于 0 时，否则 0），件数 `iAdInNum` / `iAdOutNum` 同理，都由桥算（同 U8 界面），不收调用方的值。同一仓库已有未审核的盘点单 409「该仓库已有未审核的盘点单」；存货、批号、自由项都相同的行重复 400。日期 `dcvdate`、盘点日期 `dacdate` 缺省取登录日期；收发类别缺省按名称取末级类别「盘盈入库」（`cirdcode`）、「盘亏出库」（`cordcode`），账套里没有就留空（可自己给）。不能修改，不做复盘，不能审核（§6）。

### 货位调整单

货位调整单（`position_adjust`，表 `AdjustPVouch` / `AdjustPVouchs`，U8 单据类型 19，卡片 0313）在同一仓库内把存货从一个货位挪到另一个货位，不动仓库现存量。新增、审核、弃审、删除是普通写入，按写入策略放行。不能修改：`vouchers/update` 400「货位调整单不能修改，请删除后重新录入」。

- 新增走 `USERPCO.VoucherCO.Insert("19")`，空白模板取视图 `AdjustPM` / `AdjustPD`，`VT_ID` 113，单号按卡片 0313 的编号规则取。表头仓库 `cwhcode` 必填（400「必须指定仓库」），日期 `ddate` 缺省取登录日期。每行存货、调出货位 `cbposcode`、调入货位 `caposcode`、大于 0 的 `iquantity` 必填，调出和调入相同 400「调出货位和调入货位不能相同」；件数按换算率补，不收 `inum`。
- 调用 U8 之前桥核对：仓库存在、是货位管理（`Warehouse.bWhPos=1`，否则 400「仓库 x 不是货位管理仓库」）、不是代管仓（`bProxyWh=1` 400）；两个货位都是本仓库的末级货位（`Position.bPosEnd=1`，否则 400，`field` 指到行）；存货存在；保质期管理（`bInvQuality=1`）的存货 400；调出货位上有 VMI 供应商（`cvmivencode`）结存的 400；批次管理的存货必须给 `cbatch`，非批次管理不能给（400 `lines.N.cbatch`）。结存按货位、存货、批号、自由项 1–10 合计（`InvPositionSum`），按本单的净变动核对：同一张单据里调入某货位的行先抵掉从它调出的行（A→B、B→C 两行时 B 净变动为两者之差），净减少的不能超过当前结存，否则 409 `stock_shortage`。U8 在中间状态拒绝时原文 409 `u8_rejected`、整笔回滚。
- 保存后、提交前桥在同一事务里回读：新主键能读到，表头仓库、表体行数和逐行的存货、调出货位、调入货位、批号、数量与请求一致，还没有货位台账，涉及的货位结存没有变；不符回滚 409 `u8_rejected`。
- 保存时 U8 不写货位台账，审核时才写（§6）。删除只删未审核的（已审核 409「单据已审核」）；万一有货位台账，同其他库存单据先 `ClearPosition` 再以 `bList=true` 删除（`u8-notes.md`「库存」）。删除在提交前确认表头、表体都已不在，否则回滚 409。
- 读取（`vouchers/load`）是 SQL：表头、表体照表，另有 `positions`：本单的货位台账（`id`、`line_id`、`inbound`（1 入、0 出）、`pos`、`inv`、`batch`、`qty`、`date`、`handler`），未审核时为空；超过 1000 行只给前 1000 行并带 `positions_truncated: true`。列表的增量只按表头 `ufts`（表体没有 rowversion）；附加列 `memo`、`source`、`created_at`、`modified_at`。
- 功能权限：查询 `ST010807`；新增、删除要「录入」`ST010806`（删除没有单独的功能 id），审核 `ST010802`、弃审 `ST010803`，写入时在任何 COM 调用之前现查，没有 403 `no_permission`。数据权限：仓库（表头）、存货（行）、部门和业务员（可空）。

### 其他报检单

其他报检单（`qm_other_inspect`，QM11，VT 361）没有来源单据，用 `vouchers/create` 新增。走 U8 质量管理组件 `UFQMCo.clsOtherInspectVoucherCO`（基于 VO 的接口，同不良品处理单），登录子系统 `QM`。

```json
{"type": "qm_other_inspect",
 "head": {"dDate": "2026-01-15", "cInspectDepCode": "D901", "cDefine10": "LOT-001"},
 "lines": [{"cInvCode": "A01", "quantity": 100}]}
```

- 表头只收 `dDate`（缺省登录日期）、`cInspectDepCode`（报检部门，省略时取本操作员最近一张其他报检单的报检部门）、`cDefine1`…`16`、`chDefine11`…`16`。表体 1 到 200 行，每行 `cInvCode`、`quantity`（必填），可带 `iTestStyle`（检验方式，0 到 3 的整数，省略时取存货档案的 `iTestStyle`，档案为空取 3）和 `cWhCode`。不收来源字段（`source_line_id`、`CSOURCE*` 都是 400）。存货、仓库、部门不存在 400。
- 存货有换算（`iGroupType` 不为 0）时桥按存货的库存计量单位带辅计量和件数（`CUNITID`、`FCHANGRATE`、`FNUM` = 数量 / 换算率，按账套件数小数位四舍五入）。检验类型写 `OTH`，制单人（报检人）是操作员姓名，单号按 U8 编号规则取（卡片 QM11），取了不退。
- U8 单据模板（VT 361）的必输项没有填是 400「缺少必输字段 …（U8 单据模板设置为必输）」；模板可能把表头自定义项、扩展自定义项设为必输。
- 保存由 U8 自己提交，不在桥的事务里；预演在必输项核对之后、取号之前停。
- 自动审核：VO 接口的 `AddVoucher` 不按质量选项 `bOtherInspectAutoVerify` 审核。选项为 True 时桥在保存后接着 `GetTheVoucher` + `AuditVoucher`（同 U8 客户端保存即审核），只在操作员有审核权限（`QM02060105`）时做，`state.verified` 从回读取。补审核没做（「无审核权限，报检单保存为未审核」）或没成功（组件、载入、U8 拒绝、回读等任何错误）时，新增仍返回 200 和新单的 `id`、`code`，另带 `auto_verify_error`，单据是未审核的：用 `vouchers/verify` 补审（§6），不要重发新增。
- 保存后在新连接上按单号（U8 换了号时按调用前最大主键之后、本操作员建的唯一一张）找新单，核对制单人和表体（存货、数量）；对不上或读不到 504 `outcome_unknown`。
- 删除见 §9。功能权限：新增 `QM02060103`、删除 `QM02060104`（删除已审核的另要弃审 `QM02060107`）、保存后补审核要 `QM02060105`；数据权限按报检部门、每行存货、仓库。

### 期初结存单

期初结存单（`stock_opening`，表 `rdrecord34` / `rdrecords34`，U8 单据类型 34）是库存管理的期初数据。读取、列表照常开放；新增、删除、审核、弃审是第二级写入（§3），在登录 U8 之前检查：开关关闭 403 `feature_disabled`，账套不在 `testAccounts` 里 403 `test_account_only`「期初结存单只对配置为测试账套的账套开放」。

- 新增走 U8 的 EAI 导入（`U8Distribute.iDistribute.ProcessEx`，报文根 `storeqc`），不走 `USERPCO.VoucherCO.Insert`（无界面调用时 U8 报「在对应所需名称或序数的集合中，未找到项目」）。U8 的期初结存单一张一行，所以**请求的每一行建成一张单据**，单号由 U8 编。成功：`{"ok":true,"type","id","code","docs":[{"id","code","line","wh","inv","qty"}],"count","date"}`：`docs` 按请求行次序，`line` 是请求 `lines` 的下标（从 0 开始）；顶层 `id`、`code` 取第一张；`count` 是张数；`date` 是单据日期；传入的日期未采用时另有 `warnings`。
- 字段名单见「可写字段」，行上的 `cwhcode` 覆盖表头的仓库；其他字段 400「期初结存单不能设置字段 x」。每行要有仓库（400「缺少仓库 cwhcode」）、存货（400「缺少存货编码 cinvcode」）和大于 0 的数量；只给单价或金额时桥补另一项。仓库、存货不存在，批次管理的存货没填批号（或未启用批次却填了），货位管理仓库的货位不对，数量小数位超过账套的存货数量小数位，都是 400 并带 `field`，在调用 U8 之前查完，避免只导入一部分行。
- 单据日期固定为库存启用日前一天（`AccInformation` 的 `dSTStartDate` 减一天），传入的 `ddate` 不用，响应 `warnings` 带提示；库存管理未启用 409 `state_mismatch`。
- 登录日期：新增、删除、审核、弃审都以库存启用日（第一个库存期间的第一天）登录 U8，不用请求的 `date`（期初日可能落在没有会计期间的年度）。请求的 `date` 与启用日不同时，桥为本请求另登录一次、用完即关，不进登录缓存；请求连接和事务不变。
- 预演：新增是 `validate` 模式，只做调用 U8 之前的检查（字段、仓库、存货、分级检查），不导入，响应 `detail.entries` 是将要建成的张数；删除、审核、弃审是 `rollback` 模式。
- EAI 导入由 U8 自己提交、不在请求的事务里。部分行被 U8 拒绝时桥删掉本次已导入的单据再 409 `u8_rejected`（多行时按「第 n 行：原文」列出）；导入结果与请求对不上、回读失败或补偿删除失败时 504 `outcome_unknown`，先按列表核对，不要直接重发。
- 不能修改：`vouchers/update` 400「期初结存单不能修改，请删除后重新录入」（API 层就拦下）。
- U8 的拒绝原样透出：库存启用的第一个月已月结时，新增、删除 409 `u8_rejected`「库存启用的第一个月已月结,不可以增加删除期初!」。存货核算已做期初记账后，新增、删除、弃审 409 `state_mismatch`「存货核算已期初记账，不能修改库存期初结存」；审核不受影响。
- 审核后桥在同一事务里把审核日期写成单据日期（U8 写的是登录日期）。

### 无来源采购入库单

`purchase_in` 的新增只做来源为库存的单据：表头 `cSource` 写「库存」，业务类型「普通采购」，`VT_ID` 27，本币、汇率 1。蓝字为缺省，表头 `red: true` 为红字退库（见本节末条）。参照采购订单或来料检验单入库用 `vouchers/generate`。

- 采购选项「普通业务必有订单」（`AccInformation` 里 `PU` / `bPTHavePO` 为 True）打开时，蓝字新增、修改里带新增行都是 409「账套设置了普通业务必有订单，不能无来源录入采购入库单」；红字退库不受此限制。
- 仓库不存在 400。仓库启用货位管理（`Warehouse.bWhPos`）时每行必须填末级货位 `cposition`（400「仓库有货位管理，必须指定货位 cposition」），货位要属于该仓库（`Position.cWhCode`）且是末级；未启用货位管理的仓库不能填货位。桥只写表体 `cPosition`，货位台账由 U8 写。修改时货位管理仓库的单据不能换仓库（409）。
- 采购类型 `cptcode` 不填时取默认采购类型（`PurchaseType.bDefault = 1`），收发类别 `crdcode` 不填时取该采购类型的收发类别；都没有是 400。
- 供应商不存在 400；供应商币种不是本位币（`foreigncurrency.iotherused = -1`）409「外币供应商的采购入库单本期不支持」。
- 存货不存在 400；需来料检验（`bPropertyCheck`）409「该存货需来料检验，不能直接入库」；不是外购属性 409。批次管理或保质期管理的存货必须填 `cbatch`，保质期管理的还要填 `dmadedate` 和 `dvdate`（`imassdate`、`cmassunit` 不填取存货档案）；未启用批次管理的存货不能填 `cbatch`。
- 单价三选一：含税单价 `ioritaxcost`、无税单价 `iunitcost`、无税金额 `iprice`（按金额反算单价）。含税单价不能和另两个同时给。都不给时不写金额。税率 `itaxrate` 不填取存货档案，给了单价又没有税率是 400。桥按采购公式补原币、本币的单价、金额、税额、价税合计（`u8-notes.md`「金额」），`bTaxCost` 按所给单价写 1 或 0。
- 采购入库单已启用审批流时 409 `workflow_enabled`。
- 红字退库：表头 `red` 为 `true`（布尔，别的值 400「red 必须是布尔」；`false` 或省略是蓝字）。请求照蓝字写，数量、单价填正数，上面各条校验照旧（「普通业务必有订单」除外，与 U8 客户端一致）；桥写表头 `bredvouch=1`，表体数量、件数和原币 / 本币的无税金额、税额、价税合计取负（单价不变，同 U8 里手工录的红字退库），`Insert` 的 `bIsRedVouch` 传 true。没有来源，不回写。红字单可以删除、审核、弃审，不能修改（409「红字采购入库单不支持修改」）。红字是出库方向，保存前另查（同销售出库的批号 / 货位预检）：批次存货必须填 `cbatch`（400），保质期管理的存货 409；表头仓库里该存货（批号、自由项）的可用量、填了货位的行在该货位上的结存不够 409 `stock_shortage`（多行合计）。保存后在新连接上确认 `bredvouch=1` 且每行数量为负，否则 504 `outcome_unknown`（带新主键）。`meta` 的采购入库新增表头列出 `red`。

### 无来源发货单、先开票销售发票、无来源到货单、无来源材料出库单

不挂来源单据的新增。账套选项不允许时一律 409 `state_mismatch`，消息带 U8 选项名：

| `type` | 做法 | 禁止它的账套选项（`AccInformation`） | 409 消息 |
| --- | --- | --- | --- |
| `dispatch` | `VoucherCO_Sa` VT 9、卡片 01 的 `GetDefaultVoucherDom` 模板，表体不写 `isosid`，`Save(…, 0)` | `SA` / `bMustSO_ptxs`（普通销售必有订单）为真 | 「账套设置了普通销售必有订单（SA.bMustSO_ptxs），不能无来源录入发货单」 |
| `sale_invoice` | 先开票：专票（`cvouchtype` 26，缺省）VT 0 / 卡片 07，普票（27）VT 2 / 卡片 13；表头 `idisp=0`，表体不写 `idlsid`。U8 按发票生成发货单（`DispatchList.SBVID` 指回发票），响应另给 `dispatch_id`（读不到时省略） | 同上 | 「…不能无来源录入销售发票」 |
| `arrival` | `VoucherCO_PU` vt 2、`sBillType="0"`，表头不写 `cpocode`，表体不写 `iposid` / `cordercode`，`VoucherSave2(…, 2)` | `PU` / `bPTHavePO`（普通业务必有订单）为真 | 「账套设置了普通业务必有订单（PU.bPTHavePO），不能无来源录入到货单」 |
| `material_out` | `USERPCO.Insert("11")`，`csource=库存`、业务类型「领料」、`vt_id` 65，表体不写 `iMPoIds` | `ST` / `ballowAddnewVouch`（领料必有订单）为真 | 「账套设置了领料必有订单（ST.ballowAddnewVouch），不能无来源录入材料出库单」 |

可写字段（另加表头 `cdefine1`–`cdefine16`；销售表体另加 `cfree1`–`cfree10`、`cdefine22`–`cdefine37`）：

| `type` | 表头 | 表体 | 必填 |
| --- | --- | --- | --- |
| `dispatch` / `sale_invoice` | `ccuscode`、`cstcode`、`cdepcode`、`cpersoncode`、`cexch_name`、`iexchrate`、`itaxrate`、`ddate`、`cmemo`、`cshipaddress`、`cscode`、`cpaycode`；发票另有 `cvouchtype`（26 / 27） | `cwhcode`、`cinvcode`、`iquantity`、`inum`、`cunitid`、`iquotedprice`、`iunitprice`、`itaxunitprice`、`itaxrate`、`kl`、`kl2`、`cbatch`、`cmemo` | 表头 `ccuscode`、`cstcode`；行 `cwhcode`、`cinvcode`、`iquantity` |
| `arrival` | `cvencode`、`cdepcode`、`cpersoncode`、`cptcode`、`ddate`、`cmemo` | `cwhcode`、`cinvcode`、`iquantity`、`ioricost`（无税单价）、`ioritaxcost`（含税单价）、`itaxrate` | 表头 `cvencode`；行 `cinvcode`、`iquantity` |
| `material_out` | `cwhcode`、`crdcode`、`cdepcode`、`cpersoncode`、`ddate`、`cmemo` | `cinvcode`、`iquantity`、`cbatch`、`cposition`、`cbmemo` | 表头 `cwhcode`、`crdcode`（出库类末级收发类别，桥不补缺省；缺了 400「必须指定收发类别」，`field` 为 `head.crdcode`）；行 `cinvcode`、`iquantity` |

- 业务类型固定「普通销售」/「普通采购」/「领料」，蓝字，制单人写登录操作员姓名。销售的价税按单行 `BodyCheck` 算（同销售订单）；到货单按采购公式算（`ioricost` 与 `ioritaxcost` 只能给一个，都不给按 0；税率缺省取存货档案；采购类型 `cptcode` 缺省取默认采购类型，没有就不写）。
- 币种、汇率：发货单、发票的 `cexch_name` 不填或填本位币时写本位币、汇率 1（另给的 `iexchrate` 必须是 1，否则 400）；填外币时必须给大于 0 的 `iexchrate`（400「外币 x 必须填写汇率 iexchrate」）。到货单固定本位币、汇率 1，只收本位币供应商（外币 409「外币供应商的到货单本期不支持」）。材料出库不涉及币种。
- 部门：发货单、发票不填 `cdepcode` 时取客户档案的分管部门，到货单取供应商档案的分管部门；都没有时在调用 U8 之前 400「无来源发货单（销售发票、到货单）需要部门 cDepCode」，`field` 为 `head.cdepcode`。材料出库的部门可选。
- 字段不在名单里 400「不能设置字段 x」，不在 U8 模板里 400「未知字段 x」；缺必填 400「必须填写 x」；数量要大于 0。
- 到货单：存货不是外购属性 409，浮动换算率存货 409。材料出库：批次管理的存货必须填 `cbatch`，保质期管理的存货 409；货位规则同无来源采购入库（缺货位 400，`field` 为 `lines.<i>.cposition`；桥只写表体 `cPosition`，货位台账 `InvPosition` 由 U8 写）。
- 删除走各自原有的删除（材料出库另放行来源「库存」）。先开票发票删除时 U8 连带删除它生成的发货单；桥在同一事务里核对，还在就回滚 409「先开票生成的发货单未随发票删除，请到 U8 客户端处理」。
- 锁键：先开票发票另锁 `new:dispatch`，到货单另锁 `new:purchase_return`（同生单的互锁）。

### 请购单

`purchase_requisition`（`PU_AppVouch` / `PU_AppVouchs`，卡片 27）走 `VoucherCO_PU`，登录子系统 `PU`，新增 `VoucherSave2` 状态 2、修改状态 1，单号由 `GetVoucherNO(头, "27", …)` 取。

- 表头 `ddate` 不填取登录日期，`cbustype` 固定「普通采购」，制单人写登录操作员姓名。日期写 `yyyy-MM-dd`，否则 400。
- 表体数量是 `fquantity`（不是 `iquantity`）。需求日期 `drequirdate` 不填取单据日期；建议订货日期 `darrivedate` 可省。多计量单位存货由桥按存货档案补采购单位、换算率和件数。
- 单价可省；给了就二选一：原币无税单价 `ioricost` 或原币含税单价 `ioritaxcost`（同给 400「单价和含税单价只能填一个」）。税率 `ipertaxrate` 不填取存货档案，再没有用 13。币种固定本位币（`foreigncurrency.iotherused = -1`）、汇率 1，桥补原币和本币的单价、金额、税额、价税合计（`fmoney` 是本币价税合计），`bTaxCost` 按所给单价写 1 或 0。
- 修改：`line_id` 是 `PU_AppVouchs.AutoID`；只改未审核、未关闭、没有下游的普通采购请购单。改了数量、单价或税率的行重算金额。要改的行不是本位币、汇率 1（在 U8 里录的外币行）时 409 `state_mismatch`。行操作里空的 `cinvcode` 当作没传。修改、删除、弃审在事务里调用 U8 之前再查一遍下游（锁住请购行），查到就回滚并 409。

### 生产订单

`production_order` 走 U8 API 框架的 `MOrderAdd`（与审核同一个 `U8ApiComBroker`，登录子系统 `MO`），不走通用字段名单，字段名是下面这些（不分大小写），其余 400「不能设置字段 x」：

| 位置 | 字段 | 说明 |
| --- | --- | --- |
| 表头 | `mo_code` | 生产订单号，最长 30。省略则 U8 按单据编号规则（MO21）自动编号。已存在 409 `state_mismatch`「生产订单号已存在：x」。`code` 在全局禁写名单里，所以单号叫 `mo_code` |
| 表头 | `remark` | 最长 255。`mom_order` 没有备注列，作各行备注 `DRemark` 的缺省 |
| 表体 | `inv_code` | 必填。存货要存在（400）、是自制件 `bSelf`（400）、在开工日期未停用（400） |
| 表体 | `qty` | 必填。大于 0、不超过 1000000000000、最多 6 位小数的 JSON 数；另按账套的存货数量小数位（`AccInformation` 里 `AA` 的 `iStrsQuanDecDgt`）检查，多出的小数 400「第 n 行：qty 最多 d 位小数（U8 存货数量小数位）」 |
| 表体 | `start_date`、`due_date` | 必填，`yyyy-MM-dd`。完工早于开工 400「due_date 不能早于 start_date」 |
| 表体 | `mo_type` | 必填。生产订单类别编码（`mom_motype.MotypeCode`），不存在 400 |
| 表体 | `dept_code` | 必填。生产部门，要存在且是末级部门（400） |
| 表体 | `wh_code` | 可选。预入仓库，要存在且未停用（400） |
| 表体 | `remark` | 可选，最长 255。覆盖表头 `remark` |

- 行号按顺序从 1 编，订单类别固定「标准」（`DMoClass=1`）。不收销售订单关联（`DOrderType` / `DOrderCode` / `DOrderSeq`）。
- 不送子件，U8 按存货在开工日期有效的标准 BOM 自动展开用料（`Usp_MO_GenAllocate`），数量为 `BaseQtyN / BaseQtyD × qty`。存货没有有效的标准 BOM 时 U8 照样保存、不展开，该行 `allocates` 为 0，响应带 `warnings`。
- 新订单行 `Status=2`（未审核），制单人是操作员编码。审核、弃审走 §6，修改走 §8「生产订单」（U8 的 `MOrderUpdate` 会清空并重写全部子件，桥把 U8 加载出的子件全部重送并回读核对），删除走 §9，关闭走 §10。
- 功能权限要有生产订单输入卡片的「增加」（`MO02001N`），否则 403；每行的存货、部门、仓库还要在操作员的数据权限内（403）。
- 成功另给：`allocates`（全部行的子件行数）、`details`（每行 `line_id`、`sort_seq`、`inv_code`、`qty`、`status`、`allocates`）、`warnings`（有行没展开子件时）。
- 编码类字段（单号、存货、类别、部门、仓库）和备注去掉首尾空白后不能含控制字符（API 层拒绝 Unicode 类别 C 的字符，如制表符、零宽字符；全角空格照收）。日期只收 `yyyy-MM-dd`。
- U8 API 自己提交，不在桥的事务里。U8 拒绝（`InvokeApi` 返回 false）是 409 `u8_rejected`，原文取第一行（去掉 .NET 异常类型名和后面的堆栈）；生产制造服务（`U8MPool`）没起是 503 `u8_unavailable`。
- 结果确认：新 `MoId` 取自 U8 返回的实体，桥在新连接上确认这张订单存在且制单人是本操作员。拿不到 `MoId`（调用中途异常、IPC 错误，或 U8 返回成功却没有 `MoId`）时，桥在新连接上取「调用前的最大 `MoId` 之后、本操作员建的」订单逐张比对：只有恰好一张有相同存货，且行数、每行行号、存货、数量（按存货数量小数位）、部门、类别、仓库、备注、开工和完工日期都相同、创建时间不早于调用前的数据库时间（带了 `mo_code` 再比单号），才按成功返回；有多张、没有或对不上都是 504 `outcome_unknown`（U8 返回了成功时消息是「U8 已返回成功但回读不到新生产订单，结果未知」），这时不要重投，先按列表核对。可以带 `Idempotency-Key`（§20）。

### 物料清单

`bom` 走 U8 API 的 `BomAdd`（登录子系统 `BO`），为一个母件新建一个标准 BOM 版本（`BomType=1`），不收替代 BOM。字段名是下面这些（不分大小写），其余 400「不能设置字段 x」：

| 位置 | 字段 | 说明 |
| --- | --- | --- |
| 表头 | `inv_code` | 必填。母件存货：要存在、自制（`bSelf`）且允许做 BOM 母件（`bBomMain`），在登录日期未停用，有不带自由项的物料（`bas_part`），否则 400 |
| 表头 | `version` | 可选，正整数。省略时取该母件现有标准 BOM 的最大版本加版本增量（`mom_parameter.VersionIncrement`，读不到按 10）；给了且已存在 409 `state_mismatch`「物料清单版本已存在」 |
| 表头 | `version_desc` | 可选，最长 255，缺省空串 |
| 表头 | `eff_date` | 可选，`yyyy-MM-dd`，缺省登录日期。与该母件其他版本的生效日期相同 409 `state_mismatch` |
| 表头 | `parent_scrap` | 可选，母件损耗率（%），0 到小于 100，最多 3 位小数，缺省 0 |
| 表体 | `inv_code` | 必填。子件：要存在、允许做 BOM 子件（`bBomSub`）、未停用、有不带自由项的物料，不能是母件本身（400） |
| 表体 | `base_qty_n` | 必填。基本用量分子，大于 0、不超过 1000000000000、最多 6 位小数 |
| 表体 | `base_qty_d` | 可选，基本用量分母，同上，缺省 1 |
| 表体 | `comp_scrap` | 可选，子件损耗率（%），同 `parent_scrap`，缺省 0 |
| 表体 | `wip_type` | 可选，供应类型 1 入库倒冲、2 工序倒冲、3 领用、4 虚拟件、5 直接供应，缺省 3 |
| 表体 | `wh_code` | 可选，最长 10。要存在且未停用（400）；省略时 U8 取存货档案的缺省仓库 |
| 表体 | `remark` | 可选，最长 255 |
| 表体 | `op_seq` | 可选，工序行号，最长 4，缺省 `0000` |
| 表体 | `sort_seq` | 可选，子件行号 1 到 99999，同一请求里不能重复；省略的行接在已用的最大行号后面，每次加 10 |

- 明细 1 到 200 行。子件的生效日期取版本生效日期，失效日期 `2099-12-31`。每行的其他标志按 U8 缺省送（变动用量、偏置期 0、计划比例 100、非产出品、计成本、非选配、互斥全部、产出类型 1、不带分段损耗、不关联成本在制），缺了 U8 保存时报 `CheckStructureIntegrity`。
- 新版本的状态按 U8 选项「BOM 缺省状态」（`mom_parameter.BomDefaultStatus`，一般是 1 未审核），审批流启用时 U8 自己标 `IsWFControlled`。审核、弃审走 §6，修改走 §8，删除走 §9。
- 功能权限要有物料清单卡片的「增加」（`BO01001N`），否则 403；母件存货要在操作员的数据权限内（403）。
- 成功另给：`version`、`lines`（子件行数）、`components`（每行 `line_id`、`sort_seq`、`inv_code`、`base_qty_n`、`base_qty_d`）；`code` 是母件存货编码。
- U8 API 自己提交，不在桥的事务里。U8 拒绝是 409 `u8_rejected`（原文第一行，去掉堆栈）；生产制造服务没起是 503 `u8_unavailable`。
- 结果确认：U8 不把新 `BomId` 写回实体，桥在新连接上按（母件、版本、主 BOM）找：U8 返回成功时认创建时间不早于调用前数据库时间的那一行；调用中途异常或 IPC 错误时还要制单人是本操作员、行号、子件和用量分子逐行相同。找到按成功返回；有这个版本却认不了是 504 `outcome_unknown`，一行都没有时 IPC 错误 503、其他 504。504 时不要重投，先按列表核对。可以带 `Idempotency-Key`（§20）。

应收应付单据的字段见 §12。
## 8. 修改 `vouchers/update`

请求：`type`、`id`，`head` 与 `lines` 至少一项非空，两项都空 400「没有要修改的内容」；不在下表的类型 400「该单据类型不支持修改」。物料清单（`bom`）按 `sort_seq` 定位行，生产订单（`production_order`）只能 `update` 已有行，规则见下文对应小节。

| 组 | 类型 | 行操作 |
| --- | --- | --- |
| 自由修改 | `sale_order`、`purchase_order`、`other_in`、`other_out`、`transfer`、`purchase_in`（来源为库存）、`sale_out`（来源为库存）、`purchase_requisition`、`shape_change`、`transfer_request` | `add`、`update`、`delete` |
| 生单来的单据 | `dispatch`、`sale_return`、`sale_invoice`、`sale_out`（来源为发货单）、`product_in`、`material_out`、`purchase_in`（来源为采购订单或来料检验单）、`arrival`、`purchase_return`、`purchase_invoice` | `update`、`delete`；不能 `add`（发货单例外，见 §35「发货单修改新增行」） |
| 应收应付手工单 | `ar_receipt`、`ap_payment`、`ar_bill`、`ap_bill`、`ar_refund`、`ap_refund` | `add`、`update`、`delete` |
| 退货申请单 | `sale_return_apply` | 只有 `update`（§35） |
| 质量单据 | `qm_incoming_check`、`qm_product_check`、`qm_other_check`、`qm_other_inspect` | 不收 `lines`，只改表头 |

`lines` 为 0 到 200 行，每行有 `op`：

| `op` | 要求 |
| --- | --- |
| `add` | 不能带 `line_id`，其余键是字段值，名单同新增 |
| `update` | `line_id` 为正整数，至少再改一个字段 |
| `delete` | 只能有 `op` 和 `line_id` |

其他 `op` 400「op 只能是 add、update 或 delete」；同一请求 `line_id` 重复 400「明细行重复」；`line_id` 不属于本单 400「明细行不存在」；删光现有行又不新增 400「不能删除全部明细」。库存单据（其他入库、其他出库、调拨、形态转换、调拨申请）换存货 400「不能修改存货编码，请删除该行后新增」。

只改未审核、未关闭、没有下游的单据，否则 409 `state_mismatch`；在审批中 409 `workflow_enabled`。各类型的拒绝条件与其删除相同（§9）。其他入/出库还要求来源是库存，来源是调拨时 409「调拨生成的单据不能修改」。

成功：`{"ok":true,"type","id","code","state","lines"}`，`lines` 是保存后的表体行数。U8 的销售、应收应付业务组件保存时不写修改人，桥补写修改人、修改日期和修改时间（行集里有这些列时）。提交后回读失败 504 `outcome_unknown`：修改已保存，不要重投。

### 自由修改的类型

- 采购入库单（来源为库存）只改蓝字单：红字、期初、已记账、已被采购发票或采购结算引用 409；外币或汇率不是 1 的单 409「外币采购入库单不支持修改」。新增行按新增规则核对；改了数量、单价或税率的行由桥重算税价，只改备注、自定义项的行不动金额。不能清空批号；改已有行的批号、生产日期、失效日期按该存货的批次 / 保质期规则核对（400）。
- 货位：已有行改 `cposition` 400「暂不支持修改已有行的货位，请在 U8 客户端修改」（值与现值相同时放行），新增行可带货位。单据有货位记录时不能换仓库；有货位的行（`InvPosition` 有该行或行上 `cPosition` 非空）不能删，只能改 `cbmemo` 和表体自定义项，其余 409「该行有货位记录，桥暂只能改备注和自定义项，请在 U8 客户端修改」。

### 生单来的单据

这些单据挂着来源行，修改只做缩减：

- 不能新增行，400「该单据类型修改不能新增行」（API 层即拒绝）；发货单可新增参照来源销售订单的行（§35「发货单修改新增行」）。可以删行，至少留一行。
- 数量只能减少：大于原数量 400「修改只能减少数量」，不大于 0 400「数量必须大于 0」。红字单据（退货单、采购退货单）按绝对值比较，请求写正数，桥写负数，同生单。
- 可写字段只有下表所列，加表头 `cdefine1`–`cdefine16`、表体 `cdefine22`–`cdefine37`；名单外 400「不能修改字段 x」，在名单里但不在 U8 行集 schema 里 400「未知字段 x」。存货、来源行关联、仓库（发货单、退货单除外）、单价、退货单的 `bneedbill` / `invoiced` 不能改。
- 文本字段送空串 `""` 为清空；任何字段送 JSON `null` 被拒绝（API 层 422，直接调桥 400）。
- 改了数量的行由桥按该类型生单时的算法重算金额（销售按比例缩放后单行 `BodyCheck`，采购按 `btaxcost`，库存按单价 × 数量），单价不变。
- 桥在事务里先锁来源行读累计数，保存后按每行数量差核对来源累计数（未改的行应变 0），并核对本单行数和数量；不符回滚并 409 `u8_rejected`，审计 `detail` 记基准值和实际值。U8 在保存中已自行提交时核对失败为 504 `outcome_unknown`，需人工核对。

| `type` | 表头 | 表体 | 回写核对 |
| --- | --- | --- | --- |
| `dispatch` | `cmemo`、`ddate`、`cdepcode`、`cpersoncode`、`cshipaddress` | `iquantity`、`cwhcode`、`cbatch`、`cmemo`、`cfree1`–`cfree10`（存货须启用该自由项，否则 400） | 订单行 `iFHQuantity` |
| `sale_return` | `cmemo`、`ddate`、`cdepcode`、`cpersoncode` | `iquantity`（正数）、`cwhcode`、`cmemo` | 原发货行 `iRetQuantity` 与 `fretqtywkp` / `fretqtyykp`（按表头 `bneedbill`），订单行 `fretquantity`、`iFHQuantity` |
| `sale_invoice` | `cmemo`、`ddate` | `iquantity`、`cmemo` | 发货行 `iSettleQuantity`；订单行 `iKPQuantity` 只记审计 |
| `sale_out` | `cmemo` | `iquantity`、`cbmemo` | 发货行 `fOutQuantity`、`fOutNum`（来源值为空时只记审计） |
| `purchase_in`（来源采购订单、来料检验单） | `cmemo` | `iquantity`、`cbmemo` | 来源采购订单：订单行 `iReceivedQTY`；来源来料检验单：检验单 `FsumQuantity`、到货行 `fValidInQuan`、订单行 `freceivedqty` |
| `product_in` | `cmemo` | `iquantity`、`cbmemo`（来源为产品不良品处理单的只能改备注和自定义项，改数量或删行 400「参照不良品处理单的入库单不能改数量」） | 生产订单行 `QualifiedInQty`、检验单累计入库 |
| `material_out` | `cmemo` | `iquantity`、`cbmemo` | 子件 `IssQty` |
| `arrival` | `cmemo`、`ddate`、`cdepcode` | `iquantity`、`cbmemo` | 订单行 `iArrQTY` |
| `purchase_return` | `cmemo`、`ddate`、`cdepcode` | `iquantity`（正数）、`cbmemo` | 原到货行 `fRetQuantity`，订单行 `fPoRetQuantity`、`iArrQTY` |
| `purchase_invoice` | `cpbvmemo`、`dpbvdate` | `ipbvquantity`、`cbmemo` | 入库行 `iSumBillQuantity`；红字发票 409「红字采购发票暂不支持修改」 |

各类型另外的拒绝条件：

- 发货单：非蓝字、期初同审核一样 400（「仅支持蓝字发货单」「仅支持发货单类型 05」「不支持期初发货单」）；已关闭 409。退货单：`id` 须是红字发货单（`bReturnFlag=1`、`cVouchType=05`），否则 400「该单据不是退货单（红字发货单）」。两者已被销售出库或销售发票引用 409「单据已有下游单据」。
- 销售发票：只改蓝字（红字 400「仅支持蓝字销售发票」）、未复核、应收未审核的；复核后又弃复、已复核开票数量没有退回时 409（同删除）。有行没有发货单来源（`iDLsID` 为空），或有发货单的 `SBVID` 指回本发票（先开票），409「先开票或无发货单来源的发票暂不支持修改」。表头 `iDisp` 为 0 的发票 U8 视为先开票、原样修改会被拒（「先开票不可以参照发货单」），因此其余 `iDisp` 为 0 的发票修改时桥写 `iDisp=1` 并补表体 `cbdlcode`（桥生单时已写这两项，见 `u8-notes.md`）。
- 库存单据：已记账、红字、期初 409；有货位记录的只能改备注和自定义项（同上）。销售出库只改来源为发货单或库存的；材料出库、产成品入库对应的生产订单行须仍为审核状态。参照合并检验的产品检验单生成的产成品入库（任一行 `imergecheckautoid` 大于 0）409 `state_mismatch`「参照合并检验单生成的产成品入库单不能修改，请删除后重新生成」。
- 到货单、采购退货单：已报检或已入库 409。采购发票：已复核、已应付审核、已结算、已现付、期初、有应付明细、采购期间已结账 409。

### 物料清单

`bom`（`id` 是 `BomId`）走 U8 API `BomUpdate`（登录子系统 `BO`）。只改未审核（`Status=1`）、未进审批流的标准 BOM：已审核 409 `state_mismatch`「已审核的物料清单不能修改」，停用 409，`IsWFControlled=1` 409 `workflow_enabled`（U8 API 本身不查状态，闸门由桥做）。

桥读出现有全部行和每行的标志，叠上本次修改，以差异更新（`UpdateByDiff=true`）送全部行：U8 按行号更新，未送的行号删除（不带差异更新时 U8 整表替换）。差异更新对行上附属内容的处理决定了哪些清单能改：

| 行上带的内容 | 差异更新时 U8 的处理 | 桥 |
| --- | --- | --- |
| 替代料（`bom_opcomponentsub`） | 未送的替代料被删除 | 拒绝（409） |
| 定位符（`bom_opcomponentloc`） | 未送的定位符被删除 | 拒绝（409） |
| 分段损耗（`bom_opcomponentscrap`） | 桥无法送分段损耗标志，行为未验证 | 拒绝（409） |
| 子件自由项 | 按送来的 `DInvCode` 和自由项重取物料；桥不送自由项，子件会变成不带自由项的物料 | 拒绝（409） |
| 表体自定义项（`Define22`…`Define37`） | 只写送了的，未送保留原值 | 可以改，自定义项不变 |
| 领料部门（`DrawDeptCode`） | 只在送了 `DDeptCode` 时写 | 可以改，部门不变 |
| 辅计量（`AuxUnitCode`） | 只在送了辅计量单位时写，未送保留单位、辅用量和换算率；改主用量时辅用量不重算 | 可以改，但该行不能改 `base_qty_n` / `base_qty_d`（409「第 n 行带辅计量，用量请在 U8 客户端修改」） |

任何一行带前四类（读取里 `extras` 为 `"1"`）时 409 `state_mismatch`「第 n 行带替代料、定位符、分段损耗或自由项，请在 U8 客户端修改」，不论本次是否改那一行。

- 表头只收 `version_desc`、`eff_date`、`parent_scrap`（规则同新增）；改了 `eff_date` 且与该母件其他版本相同 409。子件生效日期等于旧版本日期或早于新日期的，跟到新日期。
- 行按 `sort_seq` 定位，不用 `line_id`。`update` 须带 `sort_seq` 并至少再改一个字段，可改 `base_qty_n`、`base_qty_d`、`comp_scrap`、`wip_type`、`wh_code`、`remark`、`op_seq`；带 `inv_code` 400「不能修改存货编码，请删除该行后新增」；`wh_code`、`remark` 不能改成空（400）。`delete` 只能有 `op` 和 `sort_seq`。`add` 的字段同新增的行，`sort_seq` 不能与现有行重复（400「sort_seq 已存在」），省略时取最大行号加 10。`update` / `delete` 的 `sort_seq` 重复 400「明细行重复」，不存在 400「明细行不存在」，删光 400「不能删除全部明细」。新增或改过的行按新增规则查存货和仓库。
- 功能权限：物料清单卡片「修改」（`BO01001M`）；母件存货须在数据权限内（403）。
- 调用后在新连接上回读，与目标状态逐项比对：表头版本说明、生效日期、母件损耗率，行数，每行行号、工序行号、子件、用量分子分母、子件损耗率、供应类型、仓库、备注、生效和失效日期，以及桥送的全部标志（变动用量、偏置期、计划比例、产出品、计成本、选配、互斥、产出类型、成本在制关联）。全部相同才算成功。不一致时：调用异常 504 `outcome_unknown`；IPC 错误（生产制造服务未启动）503 `u8_unavailable`，回读已是目标状态时按成功返回；U8 返回成功却不一致 409。
- 成功响应同新增（`version`、`lines`、`components`），API 模型为 `CoUpdateOut`。

### 生产订单

`production_order`（`id` 是 `MoId`）走 U8 API：`MOrderLoad` 取出整张订单（表头、行、全部子件），在加载出的实体上修改，再以新的 `U8EnvContext` / `U8ApiComBroker` 调用 `MOrderUpdate` 交回（登录子系统 `MO`，U8 自行提交，不在桥的事务里）。`MOrderUpdate` 按行号（`DSortSeq`）对行，并**删除每一行的全部子件、按送去的子件重插**（`AllocateId` 全部换新）：不送子件即清空用料，所以桥总是重送加载出的全部子件；扩展实体带不回的子件列由桥在 U8 提交后写回（见下）。

**闸门**

- 任何一行已关闭（`Status=4`）409「生产订单已关闭，请先打开再修改」（`vouchers/close` 的 `action=open`）；其他非 1 / 2 / 3 状态 409；集合生产订单（`CollectiveFlag` 非 0）409；`IsWFControlled=1` 409 `workflow_enabled`；已报检（`DeclaredQty` 大于 0）、已被产成品入库引用 409 `state_mismatch`。
- 已审核（`Status=3`）的订单按 U8 客户端「变更」直接修改，不弃审，改后仍为已审核；响应 `state.verified`、`verifier`、`verified_at` 按回读填。
- 已领料（子件 `IssQty` 大于 0、有材料出库行按 `iMPoIds` 指向子件）：改了数量的行，子件改后需求少于已领数量 409「第 n 行子件 X 已领 a，改后需求 b 少于已领数量，不能修改」（同 U8 客户端）；出库行存货与子件不同（替代料出库）409。
- 子件主键会换新，所以有任何行按 `AllocateId` 指向本单子件时 409「生产订单子件已被其他单据或计划引用，不能修改（表名）」：替代料 `mom_moallocatesub`、分段损耗、缺料明细、子件历史、领料申请、调拨申请、配比出库、材料出库历史表、报废、MRP / 计划（`mps_*`）、下级订单（`PAllocateId`）等（账套里没有的表跳过；材料出库单 `rdrecords11` 不在此列，由桥改写，见下）。同一行上行号、存货都相同的两个子件也 409（写回时无法对准）。
- 子件有 U8 修改接口重建时不保留、桥也不写回的非缺省设置时 409「子件有 U8 修改接口不能保留的设置（列名），不能修改」：母件损耗率、偏置期、质检、成本在制关联、需求跟踪（`SoType` / `SoCode` / `SoSeq` / `DemandCode`）、工厂、各种累计量和标志（调拨、领料申请、拣货、补料、报检、原始数量）、成本项、`MoallocateSubId`，以及结束需求日期不等于开始需求日期。
- 功能权限：生产订单输入卡片「修改」（`MO02001M`）；每行的存货、部门、仓库须在数据权限内（403）。

**请求**

- 表头只收 `remark`（最长 255）：`MOrderUpdate` 不读表头其他字段，表头 `remark` 写到本次没有单独给 `remark` 的每一行。
- `lines` 0 到 50 行，`op` 只能是 `update`（`add`、`delete` 400「生产订单修改暂不能新增行 / 删除行」），按 `line_id`（`MoDId`）定位，不是本单的行 400「明细行不存在」。可改：`qty`（大于 0，小数位不超过账套存货数量小数位）、`due_date`（`yyyy-MM-dd`，不早于开工日期；有产出品子件的行 409，因 U8 重建子件时不重算产出品需求日期）、`remark`（最长 255）、文本型表体自定义项 `define22`–`define25`（最长 60）和 `define28`–`define33`（最长 120）。`start_date` 一律 400「暂不支持修改开工日期（U8 重建子件时不重算需求日期）」。`inv_code` 只能送当前值，换存货 400（U8 同样拒绝）。`remark` 和自定义项不能送空串（U8 不写空值，400）；只带 `null` 的行、只有 `null` 的表头按无改动 400。
- 请求值与现值完全相同时不调用 U8，直接返回（`changed` 为 0）。

**子件数量重算**（改了数量的行，按 U8 的子件用量算法）

- 系数 `1 + 子件损耗率/100`；变动用量（`FVFlag=1`）且非返工订单（`MoClass=2`）时再除以 `1 − 母件损耗率/100`。变动用量 = 新行数量 × 基本用量分子 / 分母 × 系数；固定用量不随行数量变。按存货数量小数位 `iStrsQuanDecDgt` 四舍五入。手工调过数量的子件同样重算；没改数量的行子件原样重送。
- 有换算率的子件用 `AuxBaseQtyN` 计算，件数 = 未舍入数量 / 换算率（按件数小数位 `iNumDecDgt`）。`BaseQtyN` 须等于 `AuxBaseQtyN × 换算率`，或等于其按存货数量小数位四舍五入的值（U8 展开子件时即如此存储，例如辅用量 1、换算率 2.345678，主用量存 2.35）。只等于舍入值时，U8 按存货的「BOM 展开单位」走主计量或辅计量算法，桥读不到该设置，两条都算，新数量和件数都相同才修改，否则 409「按主计量和辅计量算出的新数量不同（取决于存货的 BOM 展开单位）」（上例行数量改为 3 时主计量得 7.05、辅计量得 7.04）。两者都不等 409「辅计量用量与主用量不一致」；有换算率却没有 `AuxBaseQtyN`、母件损耗率不小于 100 的子件也 409。行上有辅计量的按换算率重算行件数。

**调用与核对**

- 调用前桥在请求连接上记下全部行和子件，把每个子件要写回的快照值和旧 `AllocateId` 写进审计事件 `mo_update_snapshot`。U8 加载出的行数、行号、子件数、子件（行号、存货、用量、仓库、自由项）与库里不符时不调用 `MOrderUpdate`，409「…，未修改」。用量、数量和换算率按快照原值写回实体（Load 时按精度舍入过）。
- 写回子件列：U8 重插子件时丢失 `OpComponentId`、`VirOpComponentIds`、`cSubSysBarCode`、`SoDId`、`UpperMoQty`（变为 0 / NULL），桥在自己的事务里写回快照原值（`UpperMoQty` 是展开时的母件数量，U8 客户端改数量时也不动它）：`UPDLOCK` 重读本单子件，确认子件确已重插（现有 `AllocateId` 都不在快照里），按（行、行号、存货）为每个快照子件对上恰好一行，要写的列须仍是 U8 重建后的缺省或已等于快照值（否则可能有人在 U8 客户端改过，不覆盖）；任何失败回滚并 504（U8 的修改已提交，按审计事件手工恢复）。
- 改写材料出库关联：`MOrderUpdate` 不改出库行 `rdrecords11.iMPoIds`（仍指向旧 `AllocateId`，已领量随重插带到同一子件的新行），桥按 U8 客户端变更后的结果补齐。调用前把全部引用行写进审计事件 `mo_update_refs`（表、`AutoID`、出库单 `ID`、旧 `AllocateId`、子件的行 / 行号 / 存货、数量）；U8 提交后在自己的事务里 `UPDLOCK` 读现有子件，按（行、行号、存货）把旧 `AllocateId` 对到唯一一行新子件，按 `AutoID` 改写 `iMPoIds`，再核对每一行都指向同一存货的新子件、旧 id 不再被引用、新子件已领量等于旧值且不超过需求，全部通过才提交，旧→新对照写进审计事件 `mo_update_remap`。对不上或核对失败回滚并 504 `outcome_unknown`（U8 的修改已提交），消息带修复办法：按两条审计事件修正出库行的子件关联，或到 U8 客户端核对。
- 完整核对（新连接）：行数不变，每行存货不变、数量 / 件数 / 日期 / 备注 / 自定义项等于请求，每行子件数不变，每个子件数量、件数、`UpperMoQty` 等于目标，其余受保护的列和替代料行数不变。全部相符才算成功，否则 504 `outcome_unknown`，消息列出前几项不符（审计 `detail` 记全部），请到 U8 客户端核对用料，不要重投。
- `MOrderUpdate` 报错或调用异常时同样回读：与修改前完全相同时，IPC 错误 503 `u8_unavailable`、其余 409 `u8_rejected`（U8 原文第一行）；已是请求状态按成功；其余 504。
- 成功响应同新增：`lines`、`allocates`（全部行的子件行数）、`details`（每行 `line_id`、`sort_seq`、`inv_code`、`qty`、`status`、`allocates`），另有 `changed`（本次改动的行数）；API 模型为 `CoUpdateOut`。子件 `AllocateId` 会变。
- 已在测试账套实测：固定用量（`FVFlag=2`）、带子件损耗率的子件改数量后，U8 写入的数量等于上述算法；已审核订单改后仍为已审核；领料后修改时已领量带到新子件。尚未验证：Load 的数量精度是否就是 `iStrsQuanDecDgt`；主用量与辅用量 × 换算率严格相等时辅计量子件改数量的写入；小数位多于件数精度的子件换算率写回。

### 质量单据

可修改来料检验单（QM03）、产品检验单（QM04）、其他检验单（QM15）、其他报检单（QM11）。只收 `head`，送了非空 `lines` 400「质量单据修改只收表头 head（检验项目放在 head.items），不收 lines」；字段名不分大小写，名单外 400「不能设置字段 x」。

| 类型 | 可写字段 |
| --- | --- |
| 检验单（QM03、QM04、QM15） | `ddate`、`ccheckpersoncode`（检验员，桥同时写姓名）、`cchkconclusion`（最长 60）、`creasoncode`、`fregquantity` / `fconquantiy` / `fdisquantity`、`cyieldercode`（让步接收核准人的人员编码，桥按人员档案写姓名 `CYIELDERNAME`，不存在 400）、`dyielddate`（让步接收核准日期 `yyyy-MM-dd`；送了核准人而未送时取请求的 `ddate`，再无则取登录日期）、`cdefine1`–`cdefine16`、`chdefine11`–`chdefine16`、`items` |
| 其他报检单（QM11） | `ddate`、`cinspectdepcode`、`cdefine1`–`cdefine16`、`chdefine11`–`chdefine16` |

- 检验数量 `fquantity`、存货、仓库、检验方案、抽检量、报检单关联不能改（400）；其他报检单的表体数量、仓库不开放修改。
- 数量：送了三个数量中任一个，就按单据已有检验数量 `FQUANTITY` 核对：未送的让步、不良取 0，未送的合格 = 检验数量 − 让步 − 不良，三者之和须等于检验数量（400）。改了数量又未送结论时，结论按不良数量取「合格」/「不合格」。有辅计量单位的单据按单据换算率同时改件数。
- 让步接收：`creasoncode` 是「让步接收原因」。改了数量且让步数量大于 0 时须有原因（请求带或单据已有，400）；只有不良数量不要求原因。其他检验单修改后没有让步数量时不能填原因，400（U8 要到审核时才报「没有'让步接收数量'，'让步接收原因'不能填写！」）。来料 / 产品检验单修改后让步数量大于 0 时须有核准人（请求带 `cyieldercode` 或单据已有），否则 400（U8 模板必输）；让步数量改回 0 时桥不清核准人。单据已有的 `chdefine11`–`chdefine16` 原样写回（`chdefine15` 是处置说明，不是核准人）。
- `items`：1 到 50 项 `{cchkitemcode, cchkguidecode, ccheckvalue?, ctargetqjug?}`，按检验项目和指标对到单据已有的检验项目行，覆盖检验值和单项判定，不增删行；对不上 400「单据中没有检验项目 x 指标 y」。单项判定变化时该行抽检量挪到指标合格数或不合格数（同生单）。
- 送了的 `ccheckpersoncode`、`cchkconclusion`、`creasoncode`、`cinspectdepcode` 不能是空串（400）；检验员、报检部门不存在 400。
- 状态：只改未提交审批、未审核、没有下游的单据。检验单受审批流控制且已提交或已审核（或 `iVerifyStateNew` 不为 0）409 `workflow_enabled`，先在 `workflow/*` 撤回或弃审；已审核 409 `state_mismatch`（其他检验单、其他报检单先用 `vouchers/verify` 弃审，桥不自动弃审）；已入库、已生成不良品处理单 409；其他报检单已有其他检验单 409。U8 本身不拦已审核的其他报检单，这些闸门由桥做。
- 功能权限（修改按钮）：来料检验单 `QM02010203`、产品检验单 `QM02020203`、其他检验单 `QM02060203`、其他报检单 `QM02060103`（与新增同一 id）；数据权限同删除。
- 检验单经 `VoucherOperate(…, "update", …)`，其他检验单、其他报检单经 VO 的 `UpdateVoucher`，均由 U8 自行提交；`dry_run` 只做校验（模式 `validate`）。调用后在新连接上回读：`ufts` 已变、请求的每个字段都读到、检验单对应的报检单行累计检验数量和检验标记未变，才算成功。`ufts` 未变时 409 `u8_rejected`（U8 原文）；`ufts` 已变但有不符，或 U8 报错，504 `outcome_unknown`（已修改，不要重投，先用 `idempotency/get` 或回读核对）。检验单、其他检验单由 U8 写修改人，其他报检单不写。
- 产品报检单（`qm_product_inspect`）、来料报检单不能修改（400「该单据类型不支持修改」）：U8 组件对这两类的修改接口在测试账套上无法写入（见 `u8-notes.md`）。
- 成功响应同其他修改，`lines` 是检验项目（检验单）或表体（其他报检单）行数。

### 应收应付

收款单、付款单、应收单、应付单只改未审核的手工单据，拒绝条件同删除（§12：已制单、已核销、有往来明细、票据或网银生成、期初、单据月份已结账）。

- 可写字段是新增字段表（§12）去掉往来单位 `cdwcode`、币种 `cexch_name`、汇率 `iexchrate`。可新增、修改、删除行，金额可增可减；桥重算表头合计和未核销金额（等于金额），税额拆分同新增。只改备注、部门等时不动该行税额拆分，送了金额或 `itaxrate` 的行才重算。
- `cdigest`、`cmemo`、`cdefine*` 送空串 `""` 为清空，送 JSON `null` 被拒绝（API 层 422，直接调桥 400）；金额、日期、编码的空值忽略。
- 收款单、付款单改到别的月份时，桥同时改表头会计期间 `iPeriod` 并在提交前核对。
- UFAPBO 以 `SaveVouch(…, IsAdd=false)` 保存，在事务里；提交前核对仍没有往来明细、表头金额等于表体合计。
- 成功响应同新增（`state` 带 `gl_voucher`、`settled`、`amount`、`unsettled_amount`）。

## 9. 删除 `vouchers/delete`

请求：`type`、`id`。成功 `{"ok":true,"type","id","deleted"}`，`deleted` 为 true 表示表头已不存在。报检单、检验单、物料清单另带 `code`（见下）。

允许：`sale_order`、`purchase_order`、`purchase_requisition`、`other_in`、`other_out`、`transfer`、`dispatch`、`sale_return`、`arrival`、`sale_out`、`purchase_in`、`sale_invoice`、`purchase_invoice`、`material_out`、`product_in`、`ar_receipt`、`ap_payment`、`ar_bill`、`ap_bill`、`purchase_return`、`production_order`、`bom`、`qm_incoming_inspect`、`qm_product_inspect`、`qm_incoming_check`、`qm_product_check`、`qm_incoming_reject`、`qm_product_reject`、`qm_other_inspect`、`qm_other_check`、`shape_change`、`transfer_request`、`stock_check`、`stock_opening`、`purchase_settle`，以及 §35 所列类型。其他类型 400「该单据类型不支持删除」。

只删未审核的单据，并挡住下游（409 `state_mismatch`）。

**货位**：库存单据有货位记录（`InvPosition` 有该单据的行）时，桥在删除事务里先让 U8 清掉该单据的货位记录（`ClearPosition`）再删除，U8 回退货位存量（`InvPositionSum`）；U8 拒绝清货位 409 `u8_rejected`（原文带回）；删除后货位记录或表头仍在则回滚并 409 `u8_rejected`。采购入库、其他入库、其他出库、销售出库、材料出库、产成品入库均适用。

**销售**

- 发货单：已被销售出库或销售发票引用的不删；非蓝字、期初同审核一样拒绝。
- 退货单（`sale_return`）：只删红字发货单，未审核、不在审批中、未被销售出库或销售发票引用的；删除前每行标 `editprop=D`。桥在同一事务里核对 U8 退回了回写：原发货行（按 `iCorID`）`iRetQuantity` 与 `fretqtywkp` / `fretqtyykp`（按表头 `bneedbill`）减、订单行 `fretquantity` 减、`iFHQuantity` 加，不符回滚并 409 `u8_rejected`。
- 销售出库：只删来源为发货单或库存、且没有发票行指向它的。
- 销售发票：只删未复核且应收未审核的。复核后又弃复、发货行已复核开票数量没有退回时拒绝，请到 U8 客户端处理（见 `limitations.md`）。
- 红字销售发票：只删参照退货单生成的（判断同 §6，其余红票 400）。复核后又弃复、退货行已复核开票数量（`fVeriBillQty`，负数，按绝对值比）没有退回时 409。删除前每行标 `editprop=D`，桥在同一事务里核对退货行 `iSettleQuantity` 与订单行 `iKPQuantity` 各加回发票数量，不符回滚并 409 `u8_rejected`。
- 供应商退款、客户退款、退货申请单和红冲蓝字发票的删除见 §35。

**采购**

- 请购单：只删未审核、未关闭、普通采购、未被采购订单参照、没有累计订货或询价数量的。
- 到货单：只删尚未报检或入库的。
- 采购退货单：只删未审核、尚未报检或入库的；U8 删除时回退来源行累计退货数和订单行累计退货、累计到货，桥在同一事务里核对，未回退则回滚并 409 `u8_rejected`。
- 采购入库：只删来源为采购订单、来料检验单、采购到货单（参照到货单生成的蓝字、参照采购退货单生成的红字）或库存，且未被采购发票或采购结算引用的；其他来源 409「只能删除来源为采购订单、来料检验单、采购到货单或库存的单据」。任何来源已记账（表体 `cbaccounter`）409「单据已记账，不能删除」；期初（`bpufirst`、`bIsSTQc`、`biafirst` 任一为真）409「期初采购入库单不能删除」；发票、结算引用在事务里再查一次。来源为来料检验单的，桥在同一事务里核对检验单累计入库数已退回；来源为采购到货单的，核对到货 / 退货行 `fValidInQuan` 已退回（红字加回、蓝字减去本单数量），否则回滚并 409 `u8_rejected`。
- 采购发票：只删未审核、未应付审核、未结算、未现付、非期初、没有应付明细、采购期间未结账的。红字发票（`bNegative=1`）同样可删，U8 退回入库行的负累计开票数，桥核对。
- 采购结算单（`purchase_settle`，`id` 是 `PSVID`）：自动结算和手工结算生成的结算单都适用。`VoucherCO_PU.Init(5)` 载入后 `Delete`，在桥的事务里；先查功能权限，再查状态。只删普通采购、未制凭证（`bMakePz`）、未被锁定（`iNetLock`）、每行存货核算都未处理结算成本（`bAccount=1` 409「存货核算已记账，先取消记账」）、关联发票都未应付审核（409「发票已应付审核，请先取消应付审核」）、结算日期所在期间采购未结账的，最多 400 行。U8 删除后清空关联发票表头、表体的结算日期 `dSDate`；桥在同一事务里核对结算单表头、表体已不在、发票 `dSDate` 已清空，提交前再查一次关联发票仍未应付审核（期间被他人审核也 409），否则回滚并 409。登录日期所在月以前的月份采购未结账时 U8 拒绝（409 原文），可带 `date` 指定登录日期。功能权限「删除结算单」`PU040315`，数据权限同生单。

**库存**

- 其他入/出库：只删来源为库存的。
- 形态转换单、盘点单：只删未审核的（形态转换审核后生成的其他出入库单随弃审删除；在 U8 客户端审核的盘点单须先在客户端弃审）。调拨申请单另须未关闭、未被调拨单参照、累计调拨数量为 0。
- 材料出库、产成品入库：只删来源为生产订单、产品检验单或产品不良品处理单，且对应订单行仍为审核状态的。U8 回退已领量、合格入库量和检验单累计入库量；来源为不良品处理单的，桥在同一事务里核对合格入库量和已入库标记已回退；参照合并检验的检验单生成的，核对合并来源累计入库数、检验单累计入库数、订单行合格入库量都减回本单数量；不符回滚并 409 `u8_rejected`。产成品入库使合格入库量达到订单数量时 U8 自动关闭订单行，此时删除 409「生产订单行已关闭（入库完成时 U8 自动关闭），请先在 U8 打开生产订单行再删除」。

**应收应付**：收付款单、应收应付单见 §12。

**质量单据**

- 来料报检单、产品报检单：U8 质量管理组件 `VoucherOperate` 的 `delete`，传入从视图读出的整张单据（空白 DOM 会被 U8 拒绝）。任一行已检验（`FSUMCHECKQTY > 0`）或有检验单引用 409「已有检验单，不能删除」。已审核的（报检单保存时通常自动审核）先弃审再删，须有弃审权限，否则 409；响应另带 `unverified: true`。弃审由 U8 自行提交，此后删除只要未成功（U8 拒绝、回读不符、任何异常）都返回 504 `outcome_unknown`，不是 409：单据已由已审核变为未审核。桥不会重新审核（U8 不能经接口审核报检单），消息末尾带「（报检单已弃审且未删除；接口无法重新审核，请在 U8 客户端处理或再次调用删除）」。U8 回退来源累计报检数：到货行 `fInspectQuantity` 回 0、`bInspect` 回 0，生产订单行 `DeclaredQty` 减本次数量。
- 来料检验单、产品检验单：只删未审核（`CVERIFIER` 空）、未提交审批（`iVerifyStateNew = 0`）、没有下游的：已被采购入库（`rdrecords01.iCheckIdBaks`）或产成品入库（`rdrecords10.iCheckIdBaks`）引用、已有不良品处理单、累计入库数大于 0，都是 409。U8 回退报检单行 `FSUMCHECKQTY` 和 `BFLAG`。
- 来料不良品处理单、产品不良品处理单：`GetTheVoucher` 载入后 `DelVoucher`。只删未审核、未提交审批（`iVerifyStateNew = 0`）、没有下游的：表体行已被出入库单（`rdrecords01/08/09/10/11/32/34.iRejectIds`）、报废单（`ScrapVouchs.iRejectIds`）、到货单（`PU_ArrivalVouchs.irejectautoid`）、报检单行（`QMINSPECTVOUCHERS.REJECTAUTOID`）引用，有报检单（`QMINSPECTVOUCHER.REJECTID`）指向它，领料申请单行（`MaterialAppVouchs.crejectcode`）写着它的单号，409「已被下游单据引用，不能删除」；表体已处理（`BFLAG=1`）或已有返工数量，409「不良品已处理（已入库或已返工），不能删除」。U8 删除后回退检验单 `BREJFLAG`，此后可重新参照该检验单生单。桥在新连接上核对表头、表体已不在且 `BREJFLAG` 已回 0；表头已删但表体或标记未回退 504 `outcome_unknown`（需在 U8 中核对）。功能权限：来料 `QM02010304`、产品 `QM02020304`。
- 其他报检单、其他检验单：`GetTheVoucher` 载入后 `DelVoucher`（组件见 §7「其他报检单」、§11「其他检验单生单」）。其他检验单只删未审核、未提交审批、没有下游的（弃审判断同 §6），U8 删除后回退其他报检单行 `BFLAG`；桥在新连接上核对表头、检验项目已不在且 `BFLAG` 已回退，否则 504 `outcome_unknown`。其他报检单已有其他检验单（`INSPECTID` 指向本单或行 `BFLAG=1`）409「已有其他检验单，不能删除」；已审核的（保存时自动审核）先 `UnAuditVoucher` 再删，须有弃审权限（`QM02060107`，否则 409），响应 `unverified` 为 true；弃审后删除未成功一律 504 并注明已弃审（同来料报检单）。功能权限：其他报检单 `QM02060104`、其他检验单 `QM02060204`。
- 报检单、检验单、不良品处理单的删除及删除前的弃审由 U8 自行提交（不在桥的事务里）：桥先在单据锁下查完条件，调用后在新连接上确认表头已不在；调用异常且回读不清 504 `outcome_unknown`，先 `vouchers/load` 核对再决定是否重发。报检单、检验单的删除响应另带 `code`（被删单号）。

**生产制造**

- 生产订单（`production_order`，`id` 是 `MoId`）：U8 API `MOrderDelete`（登录子系统 `MO`，U8 自行提交）。集合生产订单（任一行 `CollectiveFlag` 非 0）409 `state_mismatch`「集合生产订单请在 U8 客户端删除」；任一行不是未审核（`Status` 1 或 2）409「已审核或关闭的生产订单不能删除」；审批流控制且已提交（`IsWFControlled=1`、`iVerifyState=1`）409 `workflow_enabled`；已被材料出库单（`rdrecords11.iMPoIds` 指向子件）或产成品入库单（`rdrecords10.iMPoIds` 指向订单行）引用 409。功能权限「删除」（卡片 `MO02001D` 或生产订单列表 `MO03001D`），每行在数据权限内。U8 删除表头、明细、日程和子件；桥在新连接上确认表头已不在。U8 拒绝 409 `u8_rejected`（原文第一行，去掉堆栈），生产制造服务未启动 503，调用异常且表头仍在 504 `outcome_unknown`。
- 物料清单（`bom`，`id` 是 `BomId`）：U8 API `BomDelete`（参数同审核，登录子系统 `BO`）。已审核或停用 409「已审核的物料清单不能删除」；`IsWFControlled=1` 409 `workflow_enabled`；被单据引用时 409「X已引用该物料清单，不能删除」，X 依次查：生产订单（`mom_orderdetail.BomId`）、委外订单（`OM_MODetails.BomId`）、组装 / 拆卸 / 形态转换单（`AssemVouch.BomId`）、配比出库单（`MatchVouch.BomId` 或表体 `MatchVouchs.Bomid`）、调拨单（`TransVouch.BomId`）。U8 删除本身不查这些引用，由桥挡住。功能权限「删除」（`BO01001D`），母件存货在数据权限内。U8 删除版本、母件和子件行；是该母件最后一个标准 BOM 且还有替代 BOM 或共享 BOM 时 U8 拒绝（409 `u8_rejected`）。桥在新连接上确认已不在；未删掉时调用异常 504、IPC 错误 503、U8 返回成功却仍在 409。响应另带 `code`（母件）和 `version`。

## 10. 关闭和打开 `vouchers/close`

请求：`type`、`id`、`action`（`close` 或 `open`，否则 400「action 只能是 close 或 open」），可选 `line_ids`。允许 `sale_order`、`purchase_order`、`purchase_requisition`、`production_order`、`arrival`，其他类型 400「该单据类型不支持关闭」。

`line_ids` 省略表示整张单据；带上则为 1 到 200 个不重复的正整数（§4 的行主键）。写成 JSON `null` 是 400，不视为整单。

关闭要求单据已审核，未审核 409「单据未审核」（U8 组件本身会关闭未审核的销售订单，桥在调用前拒绝）。整单已关闭再关 409「单据已关闭」，未关闭就打开 409「单据未关闭」，按行同理。采购订单没有可处理的行 409「没有可处理的行」。

成功：`{"ok":true,"type","id","action","closed","closed_by","closed_at","lines":[{"line_id","closed"}]}`，`lines` 是全部表体行。采购订单只关部分行时表头关闭人仍为空，全部行关闭后 U8 才写表头。

**请购单**（`purchase_requisition`）：只做整单（`VoucherCO_PU` 的 `CloseApp` / `OpenApp`），带 `line_ids` 400「请购单只支持整单关闭或打开」。关闭要求已审核，表头已关闭再关 409；打开要求表头或任一行已关闭，否则 409「单据未关闭」，打开后在新连接上核对表头和各行关闭人都已清空。响应 `lines` 为空数组。

**到货单**（`arrival`）：整单或按行，`id` 是到货单 `ID`，`line_ids` 是 `Autoid`。走 `VoucherCO_PU`（Init vt 2）的 `CloseArrItems` / `OpenArrItems`，与采购订单一样在请求连接的事务里，可预演（`dry_run`，回滚模式）。

- 只收蓝字到货单（`iBillType=0`），采购退货单 400「仅支持蓝字到货单」；`type` 为 `purchase_return` 400「该单据类型不支持关闭」。
- 关闭要求已审核（`cverifier` 非空），否则 409「单据未审核」。表头 `ccloser` 非空再关 409「单据已关闭」；表头和各行（`cbcloser`）都没有关闭人时打开 409「单据未关闭」。按行规则同采购订单。
- 提交前在同一事务里核对：所处理的行（整单为全部行）关闭后都有行关闭人、打开后都没有，否则回滚 409「U8 没有处理任何行」；按行时未选的行关闭状态须与调用前相同（行数也不能变），否则回滚 409「U8 同时改动了未选的明细行，已撤销」。U8 拒绝 409 `u8_rejected`，原文带回。
- 响应同采购订单：`closed`、`closed_by` 取表头 `ccloser`（操作员姓名），`closed_at` 取表头 `dclosedate`（只有日期），`lines` 是全部表体行（`line_id` 为 `Autoid`）。提交后回读失败 504 `outcome_unknown`，不要重发。
- 关闭只表示不再参照生单（采购入库、来料报检参照时拒绝已关闭的行），不要求已全部入库。

**生产订单**（`production_order`）：按行关闭和打开，`id` 是 `MoId`，`line_ids` 是 `MoDId`。U8 没有关闭 / 打开的 API，桥按 U8 界面的做法在请求连接的事务里调用 `Usp_MO_Close` / `Usp_MO_UnClose`（见 `u8-notes.md`）。

- 不带 `line_ids`：关闭取全部已审核未关闭的行（`Status=3`），打开取全部已关闭的行（`Status=4`）。一行都没有时，关闭 409「单据已关闭」（全部已关闭）或「单据未审核」，打开 409「单据未关闭」。
- 带 `line_ids`：行不属于本单 400「明细行不存在」，重复 400「明细行重复」；关闭时该行已关闭 409「单据已关闭」、未审核 409「单据未审核」，打开时该行未关闭 409「单据未关闭」。
- 选中的行 `IsWFControlled=1`：409 `workflow_enabled`「单据已启用审批流，本期不支持」。
- 登录日期所在月已做成本计算：409 `u8_rejected`「本月成本已计算，不能关闭」（打开为「不能打开」）。
- 关闭时该行还有在制品（车间管理工序结存不为 0）：409 `u8_rejected`「生产订单还有在制，不能关闭」。U8 界面缺省允许关闭在制订单，桥传 `@v_closeflag=0`，不关闭在制订单。
- 功能权限：关闭 `MO03001CLS`、打开 `MO03001UNC`（生产订单列表的「关闭」「还原」按钮，输入卡片上没有），缺少时 403「没有生产订单关闭权限」「没有生产订单打开权限」。选中各行的存货、生产部门、仓库须在数据权限内（与读取同一组对象），否则 403「没有该单据的数据权限」。关闭时存储过程报无权限（`Errno=0`）也是 403；打开时 `Errno=0` 也可能是恢复预留后预留量超过现存量，报 409 `u8_rejected`「预留量超过现存量或没有权限，不能打开」。
- 任一行处理失败整笔回滚。提交前在同一事务里核对选中的行到达目标状态、其他行不变。
- 响应在上述字段外另有 `code`（生产订单号）、`changed`（本次处理行数）、`state`（`verified`、`closed`、`verifier`、`verified_at`，同读取）。`closed` 表示全部行已关闭；`closed_by`、`closed_at`（日期）取第一条已关闭的行，是 U8 记在行上的操作员编码，没有已关闭的行时为空。打开后 `CloseUser` 清空。

### 锁定和解锁 `vouchers/lock`

请求：`type`、`id`、`action`（`lock` 或 `unlock`，否则 400「action 只能是 lock 或 unlock」）。只允许 `sale_order`（`id` 是 `SO_SOMain.ID`），整单锁定，不按行。`purchase_order` 400 `bad_request`「采购订单锁定暂不支持（U8 采购组件 DoLock 实测一律拒绝）」（API 参数校验即拒绝，不调用 U8；U8 采购组件的 `DoLock` 一律回「本张已经被修改,不能锁定.」，见 `limitations.md`）；其他类型 400「该单据类型不支持锁定」。

锁定人写在表头 `cLocker`，值是操作员姓名（不是编码）；读取的表头里为 `clocker`，列表的附加列为 `locker`（§16）。

闸门（调用 U8 之前，按顺序）：

- 单据不存在：404。
- 功能权限：销售订单锁定 `SA03010108`、解锁 `SA03010109`（销售订单卡片「锁定」「解锁」按钮），缺少时 403「没有销售订单锁定权限」等。表头客户、部门、业务员、销售类型须在数据权限内，否则 403「没有该单据的数据权限」。
- 已审核的销售订单不能锁定：409 `state_mismatch`「已审核的销售订单不能锁定」（U8 原文「销售订单已经审核!」）。
- 本人已锁定再锁定：不调用 U8，200，响应另带 `already: true`（锁定 504 后可直接重试）。他人已锁定：409 `state_mismatch`「单据已被 X 锁定」。未锁定就解锁：409「单据未锁定」。
- 只有锁定人本人能解锁，他人解锁 409「单据由 X 锁定」。姓名按 U8 写入 `cLocker` 的方式比较：两边去空格，操作员姓名截到列宽 20 个字符。

走 `VoucherCO_Sa.LockVouch`，在桥的事务里；提交前在同一连接上核对 `cLocker` 已到目标状态，否则 409 `u8_rejected`「U8 没有锁定单据」「U8 没有解锁单据」；U8 拒绝 409 `u8_rejected`，原文带回。提交后在新连接上回读，失败或状态不符 504 `outcome_unknown`（先 `vouchers/load` 核对再决定是否重试）。

成功：`{"ok":true,"type","id","action","locked","locker"}`（本人已锁定的重试另带 `"already":true`），`locked` 是回读的 `cLocker` 是否非空，`locker` 是锁定人姓名，解锁后为空串。

销售订单、采购订单被其他操作员锁定时（采购订单只能在 U8 客户端加锁；姓名比较同上），该单据的修改（`vouchers/update`）、删除（`vouchers/delete`）、审核和弃审（`vouchers/verify`、`sale-orders/verify`）返回 409 `state_mismatch`「单据已被 X 锁定」，与 U8 界面一致；锁定人本人不受影响。关闭、打开和参照生单不查锁定。

## 11. 参照生单 `vouchers/generate`

请求：`type` 是要生成的单据，`id` 是来源单据主键，可选 `source_type`、`head`、`lines`。来源须已审核、未关闭。新单据为未审核（销售发票为未复核）。

`source_type` 省略取该目标的缺省来源；不在名单里的来源 400「该单据类型不能参照此来源类型生单」；类型不能生单 400「该单据类型不支持生单」。

| 目标 | 来源 | `lines` | 剩余数量 | 表头可写 | 行可写 |
| --- | --- | --- | --- | --- | --- |
| `dispatch` | 销售订单 | 1 到 200 行，必填 | `iQuantity - isnull(iFHQuantity,0)` | `ddate`、`cmemo`、`cwhcode`、`cdepcode`、`cpersoncode`、`cshipaddress`、`cdefine1`…`16` | `cwhcode`、`cbatch`、`cmemo`、`cdefine22`…`37`、`cfree1`…`10` |
| `sale_invoice` | 发货单 | 同上，`source_line_id` 是 `iDLsID` | `iQuantity - isnull(iSettleQuantity,0) - isnull(fretqtywkp,0)`（扣除未开票退货，同 U8 视图 `sale_DispToSaleVouchJS_B`） | `cvouchtype`（`26` 专用或 `27` 普通，缺省 26，其他值 400「该发票类型不支持」）、`ddate`、`cmemo`、`cdefine1`…`16` | `cmemo`、`cdefine22`…`37` |
| `sale_invoice`（`source_type: "sale_return"`） | 退货单（红字发货单，`id` 是 `DLID`），生成红字发票 | 可省略（全部有剩余的退货行按剩余数量）；带了是 1 到 200 行，`source_line_id` 是退货行 `iDLsID`，`quantity` 填正数 | `abs(iQuantity) - abs(isnull(iSettleQuantity,0))` | 同蓝字发票；`cvouchtype` 省略时取原蓝字发票（退货行 `iCorID` 上的发票）的类型，没有则 26 | `cmemo`、`cdefine22`…`37` |
| `sale_invoice`（`source_type: "sale_invoice"`） | 已复核的蓝字销售发票（`id` 是 `SBVID`），红冲 | 可省略（全部有剩余的蓝字行按剩余数量）；带了是 1 到 200 行，`source_line_id` 是蓝字发票行 `AutoID`，`quantity` 填正数 | 蓝字行数量 − 已红冲数量（指向该行的红字行合计） | `cvouchtype`（只能同蓝字）、`ddate`（不早于蓝字发票）、`cmemo`、`cdefine1`…`16` | `cmemo`、`cdefine22`…`37`。见 §35「红冲蓝字销售发票」 |
| `sale_out` | 发货单 | 可省略（整张剩余）；带了是 1 到 200 行，`source_line_id` 是 `iDLsID`，只收 `source_line_id`、`quantity`、`cbatch`、`cposition`（同一发货行可按批号 / 货位拆行） | 整张：`sum(iQuantity - isnull(fOutQuantity,0)) > 0`；按行：`iQuantity - isnull(fOutQuantity,0)` | 不能带表头字段 | 两步、非原子，见下文 |
| `purchase_in` | 采购订单 | 1 到 200 行，`source_line_id` 是 `PO_Podetails.ID` | `iQuantity - isnull(iReceivedQTY,0)` | `cwhcode` 必填（400「必须指定仓库」），另可写 `crdcode`（缺省见下文「收发类别」）、`ddate`、`cmemo`、`cdepcode`、`cpersoncode`、`cdefine1`…`16` | `cbatch`、`cbmemo`、`cposition`。一张入库单一个仓库 |
| `purchase_in`（`source_type: "qm_incoming_check"`） | 来料检验单 | 恰好 1 行，`source_line_id` 等于检验单 ID（即 `id`） | 合格 + 让步接收 − 累计入库 | 同上，`cwhcode` 必填 | `cbatch`（缺省检验单批号）、`cbmemo`、`cposition` |
| `purchase_in`（`source_type: "purchase_return"`） | 采购退货单（红字到货单，`id` 是退货单 `ID`），生成红字入库 | 1 到 200 行，`source_line_id` 是退货行 `Autoid`，`quantity` 填正数 | `-(iQuantity - isnull(fValidInQuan,0) - isnull(fInValidInQuan,0))`（退货行数量为负，同 U8 视图 `pu_v_preparestockbyarrforst`） | 同参照采购订单，`cwhcode` 必填 | `cbatch`（缺省退货行批号）、`cbmemo`、`cposition` |
| `purchase_in`（`source_type: "arrival"`） | 蓝字到货单（`iBillType=0`，`id` 是到货单 `ID`） | 可省略（全部未关闭、有剩余的到货行按剩余数量）；带了是 1 到 200 行，`source_line_id` 是到货行 `Autoid` | `iQuantity - isnull(fRefuseQuantity,0) - isnull(fValidInQuan,0) - isnull(fInValidInQuan,0)`（同 U8 视图 `pu_arrbody` 的 `fininquantity`） | 同参照采购订单；表头无 `cwhcode` 时取各行一致的 `cwhcode`（都没有 400「必须指定仓库」，不一致 400「一张采购入库单只能有一个仓库」） | `cwhcode`、`cbatch`（缺省到货行批号）、`cbmemo`、`cposition`；到货行的生产日期、失效日期、保质期、自由项照抄 |
| `material_out` | 生产订单（`id` 是 `MoId`） | 1 到 200 行，`source_line_id` 是子件 `AllocateId`，须属于同一订单行 | 不限（允许超领） | `cwhcode`、`crdcode` 必填，另可写 `ddate`、`cmemo`、`cdepcode`（缺省订单部门）、`cpersoncode`、`cdefine1`…`16` | `cbatch`、`cbmemo`、`cposition` |
| `product_in`（`source_type: "production_order"`） | 生产订单（`id` 是 `MoId`） | 恰好 1 行，`source_line_id` 是 `MoDId` | `Qty - isnull(QualifiedInQty,0)`；库存选项 `ST.bOverMPIn`（允许超生产订单入库）为真时不按剩余拦，交给 U8 | 同材料出库 | `cbatch`、`cbmemo`、`cposition` |
| `product_in` | 产品检验单 | 非合并检验恰好 1 行，`source_line_id` 等于检验单 ID；合并检验 1 到 20 行，每个来源一行，`source_line_id` 是合并来源 `AUTOID`（`vouchers/load` 的 `merge_sources`） | 合格 + 让步接收 − 累计入库；合并检验按来源（`QMMergeCheckDetail` 同名列） | 同材料出库 | `cbatch`、`cbmemo`、`cposition` |
| `product_in`（`source_type: "qm_product_reject"`） | 产品不良品处理单（QM06，`id` 是处理单 `ID`） | 恰好 1 行，`source_line_id` 是处理单表体 `AUTOID` | 处理后数量（`FDIMQUANTITY`，为空时取不良品数量）− 已入库数（`rdrecords10.iRejectIds` 汇总）；U8 不接受部分入库，`quantity` 须等于剩余数，否则 409「参照不良品处理单须一次入库全部处理后数量，本行应为 …」 | 同材料出库 | `cbatch`（缺省处理后批号）、`cbmemo`、`cposition` |
| `arrival` | 采购订单 | 1 到 200 行，`source_line_id` 是 `PO_Podetails.ID`，只收 `source_line_id`、`quantity` | `iQuantity - isnull(iArrQTY,0)`，不允许超订单到货 | 只收 `cWhCode`（缺省存货默认仓库）、`dDate`（缺省登录日期）、`cMemo`、`cDepCode`（缺省订单部门），键名不分大小写 | |
| `purchase_return` | 蓝字到货单（缺省，`id` 是到货单 `ID`） | 1 到 200 行，`source_line_id` 是原到货行 `Autoid`，只收 `source_line_id`、`quantity` | `iQuantity - isnull(fRetQuantity,0)` | 同到货单（`cWhCode` 缺省原到货行仓库，`cDepCode` 缺省原到货单部门） | |
| `purchase_return`（`source_type: "purchase_order"`） | 采购订单（`id` 是 `POID`） | 同上，`source_line_id` 是 `PO_Podetails.ID` | `isnull(iArrQTY,0)`（U8 退货时已从累计到货中扣减） | 同到货单 | |
| `sale_return` | 蓝字发货单 | 1 到 200 行，`source_line_id` 是原发货行 `iDLsID` | 未开票退货 `iQuantity - iSettleQuantity - fretqtywkp`，已开票退货 `(iSettleQuantity - (iQuantity - fretqtywkp) > 0 ? iQuantity - fretqtywkp : iSettleQuantity) - fretqtyykp`，都不超过原发货行 `iQuantity - iRetQuantity` | `ddate`、`cmemo`、`cdepcode`、`cpersoncode`、`cdefine1`…`16`，布尔 `invoiced` | `cwhcode`（缺省原发货行仓库）、`cmemo`、`cdefine22`…`37` |
| `sale_return`（`source_type: "sale_return_apply"`） | 已审核的退货申请单（`id` 是申请单 `ID`） | 1 到 200 行，`source_line_id` 是申请行 `AutoID`，所选行须指向同一张蓝字发货单 | 申请行 `iQuantity` 绝对值 − 已退数量 `fretqty` 绝对值 | 同参照发货单；`invoiced` 须与申请行的开票标志一致 | 同参照发货单，`cwhcode` 缺省取申请行仓库。见 §35「退货申请单」 |
| `purchase_invoice` | 采购入库单（蓝字生成蓝字发票，红字生成红字发票） | 1 到 200 行，`source_line_id` 是入库行 `AutoID`，`quantity` 填正数 | `iQuantity - isnull(iSumBillQuantity,0)`；红字入库行取两者绝对值之差 | 只收字符串：`cPBVCode`（发票号，必填，≤ 30）、`cPBVBillType`（`01` 专用 / `02` 普通，缺省 01）、`dPBVDate`（缺省登录日期）、`cPBVMemo`（≤ 255） | 只有 `source_line_id`、`quantity` |
| `qm_incoming_inspect` | 蓝字到货单（`id` 是到货单 `ID`） | 1 到 200 行，`source_line_id` 是到货行 `Autoid`，行须要检验（`bGsp=1`）、未关闭、未报检（`bInspect=0`） | `iQuantity - isnull(fInspectQuantity,0)` | `dDate`（缺省登录日期）、`cDepCode`（缺省到货单部门）、`cInspectDepCode`（报检部门，缺省同 `cDepCode`）、`cDefine1`…`16` | `cWhCode` |
| `qm_product_inspect` | 生产订单（`id` 是 `MoId`） | 1 到 200 行，`source_line_id` 是 `MoDId`，行须已审核（`Status=3`）、未关闭、要质检（`QcFlag=1`） | `Qty - isnull(DeclaredQty,0)` | 同来料报检单（`cDepCode` 缺省订单行生产部门） | `cWhCode` |
| `qm_incoming_check` | 来料报检单（`id` 是报检单 `ID`） | 恰好 1 行，`source_line_id` 是报检单表体 `AUTOID`，`quantity` 是本次检验数量 | `FQUANTITY - isnull(FSUMCHECKQTY,0)` | `cCheckPersonCode`（检验员，必填）、`cDepCode`（检验部门，缺省报检部门）、`project_code`（检验方案）、`cChkConclusion`、`fDtQuantity`（抽检量，缺省 1）、`dDate`、`cDefine1`…`16`、`chDefine11`…`16`、`items`、`cYielderCode`（让步接收核准人，有让步数量时来料 / 产品检验单必填，400）、`dYieldDate`（缺省检验日期） | `fRegQuantity`（合格）、`fConQuantiy`（让步）、`fDisQuantity`（不良） |
| `qm_product_check` | 产品报检单（`id` 是报检单 `ID`） | 同来料检验单 | 同来料检验单 | 同来料检验单 | 同来料检验单 |
| `qm_incoming_reject` | 来料检验单（`id` 是检验单 `ID`） | 1 到 200 行，每行一种处置；`source_line_id` 都等于检验单 ID（可重复），各行 `quantity` 之和须等于检验单不良品数量 | 检验单 `FDISQUANTITYS`（不良数量，同 U8 参照视图口径）；一张检验单只能生成一张处理单 | `dDate`、`cDefine1`…`16`、`chDefine11`…`16` | `cScrapDisCode`（处理方式，必填）、`cReasonCode`（不良原因，必填）、`cDimInvCode`（处理后存货，降级类必填）、`cbWhCode` |
| `qm_product_reject` | 产品检验单（`id` 是检验单 `ID`） | 同来料不良品处理单 | 同来料不良品处理单 | 同来料不良品处理单 | 同来料不良品处理单 |
| `qm_other_check` | 其他报检单（`id` 是报检单 `ID`） | 恰好 1 行，`source_line_id` 是报检单表体 `AUTOID` | 行 `FQUANTITY`；一行只能生成一张（已有检验单或 `BFLAG=1` 409） | 同来料检验单；`cDepCode`（检验部门）省略时取检验员所属部门 | 同来料检验单 |
| `purchase_settle` | 采购发票（`id` 是 `PBVID`） | 不能带（整张发票自动结算，见下文「采购结算单生单」） | 发票未结算（表头、表体 `dSDate` 为空，没有结算行） | 只收 `settle_date`（`yyyy-MM-dd`，可省，即本次 U8 登录日期） | |
| `transfer` | 调拨申请单（`id` 是申请单 `ID`） | 1 到 200 行，`source_line_id` 是申请单行 `autoID`，只收 `source_line_id`、`quantity`、`cbmemo` | `iTvChkQuantity - isnull(iTvSumQuantity,0)`（核准数量减累计调拨，比 U8 按存货超额比例 `fInExcess` 放宽的口径严，件数不单独查）；账套选项 `ST.bOverTransRequestTransfer` 为真时不设上限 | `dtvdate`、`cmemo`、`codepcode`、`cidepcode`、`cpersoncode`、`cordcode`、`cirdcode`、`cdefine1`…`16`；仓库取自申请单，不能填 | 见 `lines` |

每行必填 `source_line_id`（正整数）和 `quantity`（大于 0，不超过 1000000000000）。来源行重复 400「来源明细重复」，例外：销售出库同一发货行可按批号 / 货位列多次，（行、批号、货位）都相同才算重复，400「明细行重复」，且在桥读取发货单之后才判（发货单不存在、未审核先回 404 / 409）；不良品处理单的来源行就是检验单本身，每种处置一行，`source_line_id` 都等于检验单 ID。数量超过剩余 409「超过可生单数量」。

成功：`{"ok":true,"type","id","code","source_type","source_id","state","lines"}`，`source_type` 是实际来源，`lines` 是新单据的表体行数。检验单、不良品处理单另有 `wf`（其他检验单没有审批流，不带）。

采购发票和到货单保存后，桥在同一事务里核对 U8 已把本次数量加到来源累计数上，否则回滚并 409 `u8_rejected`。若 U8 在保存中已自行提交（本连接 `@@TRANCOUNT` 为 0），核对不过时无法回滚，返回 500 `internal`「U8 已自行提交，无法核对回写」，单据已落库，需人工核对。

### 通用：收发类别、采购类型、货位

- 收发类别：采购入库（参照采购订单、来料检验单、采购退货单）不给 `crdcode` 时同 U8，取来源单据采购类型（`cPTCode`，参照检验单、退货单时取到货单 / 退货单上的）的 `PurchaseType.cRdCode`；采购类型没有设收发类别时取默认采购类型（`PurchaseType.bDefault = 1`）的；仍没有则 400「必须指定收发类别」（`field` 为 `head.crdcode`，`hint`「采购类型没有设置收发类别，请在请求里给 crdcode」）。材料出库、产成品入库（三种来源）U8 没有缺省，须给 `crdcode`（出库用出库类、入库用入库类的末级收发类别），缺少 400「必须指定收发类别」（`field` 为 `head.crdcode`），不存在、非末级或方向不对 400「收发类别无效」。桥不写死任何收发类别编码。
- 采购类型、币种：到货单、采购退货单、采购发票生单时来源单据上没有采购类型的，取默认采购类型（`PurchaseType.bDefault = 1`），也没有则不写、由 U8 判断；来源上没有币种时取本位币（`foreigncurrency.iotherused = -1`）。
- 货位（材料出库、产成品入库三种来源、采购入库参照采购订单 / 来料检验单 / 采购退货单）：行上可带 `cposition`，规则同无来源采购入库。仓库启用货位管理（`Warehouse.bWhPos`）时每行须填该仓库（`Position.cWhCode`）的末级货位，否则 400「仓库有货位管理，必须指定货位 cposition」；未启用的仓库填了 400「仓库未启用货位管理，不能填货位 cposition」；`field` 为 `lines.<i>.cposition`。采购入库表头仓库不存在 400「仓库不存在：<编码>」，`field` 为 `head.cwhcode`。U8 本身不拦（货位仓的入库行不带货位也能保存，`cPosition` 为空、不写 `InvPosition`），所以桥在调用 U8 之前一律核对。桥只写表体 `cPosition`，货位台账 `InvPosition` 由 U8 保存时写；删除时先清货位再删（§9）。红字入库（参照采购退货单）从货位出库，同样须调用方指定货位，桥不按原蓝字入库行带出。

### 销售

- 发货单：仓库必填（行上的 `cwhcode`，否则表头，否则来源行），都没有 400「必须指定仓库」。
- 销售发票：同一张发票的行须来自同一客户；来源发货单已关闭或是期初时拒绝。
- 红字销售发票（`source_type: "sale_return"`）：退货单须是红字发货单（否则 400「该单据不是退货单（红字发货单）」）、非期初（400「不支持期初退货单」）、已审核、未关闭，且是已开票退货（`bneedbill=1`，否则 409「退货单是未开票退货（bneedbill=0），不开红字发票」：未开票退货冲减的是原发货行的未开票数量，不需要红票）；行未关闭，没有可开票数量 409「退货单没有可开票数量」。销售组件用红字 VT 1（26）/ 3（27），卡片同蓝字（07 / 13）。桥先试 `GetNegaVouchData`，U8 生成的表体不含全部请求行时改按参照视图（`Sales_FHD_T` / `Sales_FHD_W`）组装；表头 `bReturnFlag=1`、`iDisp=1`、`sbvid=""`，行 `iDLsID` 是退货行、`cbdlcode` 是退货单号，数量写负的本次数量，金额按比例后由 U8 按数量重算。可开票数量在事务里加 `UPDLOCK`、`HOLDLOCK` 重读；保存后在同一事务里核对：新发票行数、全为负数量且合计等于请求，退货行 `iSettleQuantity` 减了本次数量（更负），挂订单的订单行 `iKPQuantity` 净减本次数量；不符回滚并 409 `u8_rejected`。红字发票不能修改，可复核、弃复、删除（§6、§9）。
- 退货单（红字发货单）：来源须是蓝字发货单（`bReturnFlag=0`、`cVouchType=05`，否则 400「仅支持参照蓝字发货单」）、非期初（400「不支持期初发货单」）、已审核、未关闭，行未关闭。`quantity` 填正数。一张退货单要么是未开票退货（表头 `bneedbill=0`），要么是已开票退货（`bneedbill=1`）：`head.invoiced` 为 false 时每行不超过未开票可退、为 true 时不超过已开票可退（否则 409「超过未开票可退数量」/「超过已开票可退数量」）；省略时全部行都在未开票可退内则按未开票，否则全部在已开票可退内则按已开票，都不行 409 `state_mismatch`，请按 `invoiced` 拆成两张。桥让 U8 按原发货单生成整张红字单（`GetNegaVouchData`），只留请求的行，改为新增，数量改为负的本次退货数，金额按比例缩放后由 U8 按数量和单价重算；`iCorID` / `cCorCode` 指回原发货行和原单号，原行有订单关联时挂上 `iSOsID`、`csocode`、`cordercode`、`iorderrowno`。保存后在同一事务里核对：新单行数、每行负数量与合计；原发货行 `iRetQuantity` 与 `fretqtywkp`（未开票）或 `fretqtyykp`（已开票）加了本次数量；挂订单的销售订单行 `fretquantity` 加、`iFHQuantity` 减了本次数量（同一订单行按合计）。不符回滚并 409 `u8_rejected`，审计 `detail` 记基准值和实际值。

**销售出库**

- 省略 `lines` 时一次生成该发货单剩余的全部行，没有剩余 409「没有可出库数量」。带 `lines` 时只出列出的发货行和数量：行不属于该发货单 400「明细行不存在」，行上有其他字段 400「不能设置字段 x」。已关闭的发货行（`cSCloser` 非空）整单生成时不出，带 `lines` 列出它 409 `state_mismatch`「明细行已关闭」。
- 两步、非原子：桥先让 U8 整单生成并提交，再按发货行比较生成数量与应出数量（带 `lines` 为请求数量，不带为该行剩余数量），多出的在另一个事务里改回：新单的行改为应出数量（辅数量按换算率、有单位成本时金额按数量重算），不要的行删掉，一行都不要的整张删掉，提交前核对发货行累计出库数量等于生成前加应出数量。不带 `lines` 时这一步通常无动作；此前部分出库过的发货单 U8 会按原剩余数量整份再出，桥把它改回真实剩余。
- 第二步失败（含带 `lines` 时生成不足请求数量、要改数量的出库单有货位记录）时，桥删除第一步生成的全部出库单，核对累计出库数量回到生成前，返回 409 `u8_rejected`「已撤回生成的销售出库单：<原因>」；撤回也失败时 504 `outcome_unknown`，消息列出生成的出库单 id，先到 U8 核对，不要直接重发。
- 跨仓库时一张发货单生成多张销售出库，响应另有 `ids`（保留下来的全部新主键，升序，`id` 是第一张）。
- 第一步总是出该发货单剩余的全部行，所以带 `lines` 时每一个未出完的行都须能按剩余数量整行出库（库存够、货位仓有货位、批次存货有批号）；任何一行出不了，整个请求 409（例如 `stock_shortage`，消息里可能是未列出的另一行的存货）。此时先在 U8 处理那一行（补库存、关闭该行等），或在 U8 客户端出库。
- 批号、货位：每行另可带字符串 `cbatch`（最长 60）、`cposition`（最长 20）；同一 `source_line_id` 可列多次（拆行），批号或货位不同，各次数量之和是该发货行的应出数量。仓库不能改（跟发货行；要换仓库请在发货单生单时写行上的 `cwhcode`）。调用 U8 之前桥先查：存货未启用批次管理却填批号 400「存货 X 未启用批次管理，不能填批号 cbatch」，保质期管理的存货填批号 409（暂不支持）；仓库未启用货位管理却填货位 400「仓库 W 未启用货位管理，不能填货位 cposition」；货位不存在、不属于该仓库、非末级 400；拆行时批次存货每次都要有批号、货位仓每次都要有货位，两样都不管理的存货不能拆（400）；批号在该仓库的可用量（`iQuantity − fStopQuantity`，冻结行为 0，按自由项匹配）、货位上该存货的结存（`InvPositionSum`，批号取请求的或发货行的）不足 409 `stock_shortage`（多行合计）。
- 按上述两步生成、改数量后再多一步：出库行的批号、货位与请求一致（U8 已按发货行带出且数量一致）则不改；否则在事务外读出单据、在新事务里修改（只带数量的请求行不动，U8 生成几行就是几行）：该发货行的第 1 行改为请求的第 1 次（数量、批号、货位；换批号时清掉批次属性和生产 / 失效日期等随批号的列，再按批次属性档案 `AA_BatchProperty` 填新批号属性；应发数量、应发件数随之改），多出的次数复制该行追加（同一 `iDLsID`），多余的行删掉；货位记录（`InvPosition`）由 U8 保存时写。提交前核对发货行累计出库数量、每个发货行的出库行与请求一一对上、带货位的行都有货位记录、指定批号的行批次属性与档案一致，不符回滚。这一步失败同样按上文补偿（409 `u8_rejected`「已撤回生成的销售出库单：…」）。U8 生成时已带出货位记录的出库单又要改批号或货位时拒绝（按失败补偿）。

### 采购

- 到货单：订单须已审核、未关闭、普通采购，行未关闭、有单价。
- 采购退货单：`quantity` 填正数，桥写负的数量、件数和金额（单价取原到货行或订单行），表头 `iBillType=1`、`bNegative=1`、`iVTid=8169`。参照到货单时原到货单须是蓝字（否则 409「来源须为蓝字到货单」）、已审核、未关闭、普通采购，新行 `iCorId` 是原行 `Autoid`，有订单行时带 `iPOsID`、`cordercode`；参照采购订单时带 `iPOsID`、`cordercode`。超过可退数量 409「超过可退货数量」；浮动换算率或带自由项的来源行 409；有订单行时，同一订单行上本单退货合计不能超过订单行 `iArrQTY`（409「超过采购订单行可退货数量」）；可退数量在事务里加 `UPDLOCK`、`HOLDLOCK` 重读。保存后在同一事务里核对回写：参照到货单时原到货行 `fRetQuantity` 加、订单行 `fPoRetQuantity` 加、`iArrQTY` 减本次数量；参照采购订单时订单行 `fPoRetQuantity` 加、`iArrQTY` 减（审核、弃审、删除后均复原）。不符回滚并 409 `u8_rejected`。采购退货单与到货单共用编号，两者的生单互相串行。
- 采购入库（参照采购订单）：需要来料检验的存货 409「该存货需来料检验，不能直接入库」。外币订单：表头币种 `cExch_Name`、汇率 `iExchRate` 取订单的 `cexch_name`、`nflat`，原币按采购公式计算（`u8-notes.md`），本币单价、金额、税额、价税合计 = 原币 × 汇率（单价 6 位、金额 2 位，即 `iSum = round(ioriSum × 汇率, 2)`）。
- 采购入库（参照来料检验单）：检验单须已审核、来源是到货单、不是合并检验（409「合并检验的检验单暂不支持生单」），到货行未关闭。
- 采购入库（参照到货单，`source_type: "arrival"`）：到货单须是蓝字（`iBillType=0`，否则 409「来源须为蓝字到货单」）、已审核、未关闭、普通采购，行未关闭。到货行要来料检验（`bGsp=1`）或存货 `bPropertyCheck=1` 时 409「到货单的存货需要来料检验，请先报检、检验后参照检验单入库」。表头 `csource=采购到货单`、`ipurarriveid` / `carvcode` / `darvdate` 指向到货单，`cordercode` 只在各行属于同一张订单时写；行 `iarrsid` 是到货行 `Autoid`，`iposid` / `cpoid` 取到货行的订单行，单价、税率取到货行（字段与 U8 界面参照到货单生成的入库一致）。可入库数量在事务里加 `UPDLOCK`、`HOLDLOCK` 重读；保存后在同一事务里核对到货行 `fValidInQuan` 加了本次数量（订单行 `iReceivedQTY` 只记审计），不符回滚并 409 `u8_rejected`。账套开启「普通业务必有订单」且订单已有到货单时，参照订单生成入库 U8 报「该操作会造成订单到货和入库同时存在」，应改为参照到货单。这样生成的入库可以生成采购发票（行上带订单行）。
- 红字采购入库（`source_type: "purchase_return"`）：采购退货单须是 `iBillType=1`（否则 409「来源须为采购退货单」）、已审核、未关闭、普通采购、不受审批流控制，行未关闭。表头 `bredvouch=1`、`csource=采购到货单`（U8 库存来源枚举里没有「采购退货单」）、`carvcode` / `ipurarriveid` 指向退货单，行 `iarrsid` 是退货行 `Autoid`，数量、金额为负。可入库数量在事务里加 `UPDLOCK`、`HOLDLOCK` 重读；保存后在同一事务里核对退货行 `fValidInQuan` 减了本次数量（订单行 `iReceivedQTY` 只记审计），不符回滚并 409 `u8_rejected`。不按 `bPropertyCheck` 拒绝（退的是已到货的货）。
- 采购发票：入库单须已审核、来源为采购订单或来料检验单、普通采购、蓝字，行有订单行和单价、未结算；发票日期所在期间采购未结账。同一发票号加类型已存在 409「发票号已存在」。
- 红字采购发票：来源入库单是红字（`bredvouch=1`）时生成红字发票：入库单须已审核、普通采购、来源为库存 / 采购订单 / 来料检验单 / 采购到货单，行数量为负、有单价、未结算，不要求订单行；`quantity` 填正数，桥写负的数量和金额（单价为正），表头 `bNegative=1`，`VoucherCO_PU.Init` 的 `bPositive` 传 false，其余同蓝字。保存后在同一事务里核对入库行 `iSumBillQuantity` 减了本次数量，不符回滚并 409 `u8_rejected`。红字发票可删除、复核、取消复核（闸门同蓝字，`Init` 同样用 `bPositive=false`），不能修改。

### 库存与生产

- 调拨单参照调拨申请单：申请单须已审核、未关闭、不受审批流控制；行未关闭（`cBCloser`）、核准数量大于 0，否则 409「明细行已关闭」「调拨申请行没有核准数量」；超过核准减累计 409「超过调拨申请行的可调拨数量」（事务里加锁再查一次）。走 `USERPCO` 的 `Insert("12")`（同调拨单新增，`vt_id=89`、`csource=1`）；表头仓库、部门、业务员、收发类别取自申请单（部门等可由调用方改；申请单无收发类别且调用方未给时桥不补缺省，U8 要求时由 U8 拒绝），`ctranrequestcode` 写申请单号；表体 `itrids` = 申请单行 `autoID`，存货、批号、自由项、保质期、辅计量照申请单行，件数按换算率重算。提交前在同一事务里核对新调拨单各行都带 `iTRIds`、申请单行 `iTvSumQuantity` 加了本次数量，不符回滚 409 `u8_rejected`。这样生成的调拨单不能修改（409，删除后重新生成）；删除时在同一事务里核对 `iTvSumQuantity` 已退回。申请单已有调拨单参照时不能弃审、修改、删除（§6、§9）。响应另有 `source_type`、`source_id`。
- 材料出库：订单行须已审核、未关闭，子件须是领料方式，否则 409「生产订单未审核」「生产订单已关闭」「该子件不是领料方式，不能生成材料出库」。
- 产成品入库（参照产品检验单）：检验单须已审核、来源是生产订单、未入库完毕（表头 `BPROINFLAG=1` 409「检验单已入库完毕」）。非合并检验只能 1 行（多行 400「非合并检验的检验单只能有 1 行」）。合并检验的检验单（`BMERGECHECKFLAG=1`）每个来源一行，生成一张入库单：`source_line_id` 不是该检验单的合并来源 400「明细行不存在」（`field` 为 `lines.<i>.source_line_id`）；来源已入库完毕（`BPROINFLAG=1`）409「该合并来源已入库完毕」；来源的生产订单行须已审核未关闭、存货与检验单相同；数量不超过该来源的合格 + 让步 − 累计入库。表体每行 `iMPoIds`、`cmocode`、`imoseq` 取该来源的订单行，`iNQuantity` 是该来源合格数，`imergecheckautoid` 是来源 `AUTOID`，`iCheckIdBaks` 仍是检验单 ID；表头不写 `cMPoCode`（同 U8 客户端）。提交前在同一事务里核对来源累计入库数、检验单累计入库数、各订单行合格入库量都加了本次数量，不符回滚并 409 `u8_rejected`。
- 产成品入库（参照生产订单）：订单行须已审核未关闭；库存选项 `ST.bQuality` 为真且订单行 `QcFlag=1`（需要检验）时 409「生产订单行需要检验（QcFlag=1，库存选项 ST.bQuality），请参照产品检验单入库」。`csource` 写「生产订单」，表体 `iMPoIds` 是 `MoDId`，不写检验单。提交前在同一事务里核对 `QualifiedInQty` 加了本次数量，删除时核对减回；不符 409 `u8_rejected` 并回滚。这种入库单不能修改（409「只能修改来源为产品检验单的单据」）。
- 产成品入库（参照产品不良品处理单）：处理单须已审核（受审批流控制的须审批完成），来源是生产订单，订单行已审核未关闭；只接处理流程为「降级 / 让步接收 / 升级」（`IDISPOSEFLOW=2`）的行，否则 409「该不良品处理方式不能入库」；已入库完毕（`BFLAG=1`）409「不良品处理单已入库完毕」。入库存货是处理后存货（`CDIMINVCODE`）。保存后在同一事务里核对本行已入库数、生产订单行合格入库量（`QualifiedInQty`）都加了本次数量、入库满额时 `BFLAG` 为 1，不符回滚并 409 `u8_rejected`。

### 质量单据

- 通用：U8 单据模板（VT 351 到 356，其他报检单 361、其他检验单 365）设为必输而请求未给、桥也推不出的字段，一次列出全部，400 `bad_request`「缺少必输字段 x（U8 单据模板设置为必输）」。检验单的扩展自定义项（`chDefine11` 等）常被设为必输，须由调用方填。U8 拒绝时 409 `u8_rejected`，`message` 是 U8 原文（错误列表的描述，或 `ErrBag` 的 `description`）。
- 来料报检单、产品报检单：来源到货单须已审核、是蓝字（采购退货单 400）；到货行可分次报检，不超过剩余数量（`iQuantity` − `fInspectQuantity`）；已全部报检（`bInspect=1`）的行 409，与 U8 的报检参照（只列 `binspect=0` 的行）一致。生产订单行须已审核、未关闭、要质检（`QcFlag=1`，否则 409）。报检人（`CMAKER`）是登录操作员姓名；单号按账套单据编号设置取（响应 `code` 取保存后的表）。U8 在保存中回写来源累计报检数（到货行 `fInspectQuantity`，满额时 `bInspect=1`；生产订单行 `DeclaredQty`），并按质量管理选项自动审核，响应 `state` 是回读结果。保存在桥的事务里：U8 先回写再校验，校验失败时已加上的来源累计数随事务回滚（见 `u8-notes.md`）。
- 来料检验单、产品检验单：报检单须已审核（经接口只能依靠 U8 保存时的自动审核，见 §6）。一张检验单一个存货，只能 1 行。`fRegQuantity`、`fConQuantiy`、`fDisQuantity` 都不小于 0，三者之和须等于 `quantity`（400）；省略 `fRegQuantity` 时取 `quantity` 减另外两项。`cCheckPersonCode` 须是存在的人员编码，否则 400。`project_code` 是检验方案编码（`QMCHECKPROJECT.CPROJECTCODE`，未停用）；省略时取同一存货最近一张同类检验单的方案，一张都没有 400「请指定检验方案 project_code」。`cChkConclusion` 缺省：不良数为 0 时「合格」，否则「不合格」。检验项目缺省为方案里的全部项目，检验值取标准值、判定「合格」；带 `items` 时整组替换：1 到 50 项，每项 `cChkItemCode`、`cChkGuideCode` 必填，可带 `cCheckValue`（检验值）、`cTargetQJug`（只能是「合格」或「不合格」），字符串最长 60，同一项目加指标重复 400。单号由桥按单据编号规则分配（U8 照收调用方送的号，不自行编号）。新检验单未审核、未提交审批，响应另有 `wf`（`controlled` 是否受审批流控制、`verify_state_new` 为 0），之后照常走 `workflow/submit`。U8 回写报检单行 `FSUMCHECKQTY`，检完时 `BFLAG=1`。保存由 U8 自行提交：桥先查完条件，调用后在新连接上回读新单；调用异常且回读不到 504 `outcome_unknown`，先按报检单核对再决定是否重发。

### 不良品处理单生单

`qm_incoming_reject`（QM05）参照来料检验单，`qm_product_reject`（QM06）参照产品检验单，`source_type` 省略取对应的检验单类型。走 U8 质量管理组件 `clsArrRejectCO` / `clsProRejectCO` 的 `AddVoucher`，登录子系统 `QM`。

```json
{"type": "qm_product_reject", "source_type": "qm_product_check", "id": 5,
 "head": {"dDate": "2026-01-10", "cDefine1": "批次复核"},
 "lines": [
   {"source_line_id": 5, "quantity": 1, "cScrapDisCode": "Sys01", "cReasonCode": "01"},
   {"source_line_id": 5, "quantity": 2, "cScrapDisCode": "Sys03", "cReasonCode": "01", "cDimInvCode": "A02", "cbWhCode": "01"}
 ]}
```

- 检验单须已审核（受审批流控制的须审批完成）、来源行未关闭（到货行 `cbcloser` / 生产订单行 `CloseUser`）、尚无不良品处理单、不良品数量大于 0，否则 409 `state_mismatch`：已有 `CHECKID` 指向它的处理单 409「检验单已生成不良品处理单」；`BREJFLAG=1` 却找不到处理单 409「检验单已标记生成不良品处理单，但找不到对应单据，需在 U8 中核对」。`source_line_id` 不等于 `id` 400「source_line_id 必须等于检验单 ID」。
- 表头另收扩展自定义项 `chDefine11`…`16`（按 U8 模板字段名的原样大小写；产品不良品处理单模板常把 `chdefine15` 设为必输，缺少时 400「缺少必输字段 chDefine15（处理类型）（U8 单据模板设置为必输）」，括号内是 U8 界面标题，`field` 为 `head.chDefine15`）。表体的部门、件数、处理后单位与件数由桥按检验单、报检单和存货补齐（见 `u8-notes.md`）。
- 各行数量之和不等于检验单不良品数量 400（`field` 为 `lines`）。处理方式（`QMSCRAPDISPOSE`）、不良原因（`Reason`，只收质量管理类 `iReasontype=1`）、处理后存货、仓库不存在 400。处理流程为 2（降级、让步接收、升级）的处理方式须给 `cDimInvCode`，桥写处理后数量 `FDIMQUANTITY` = 本行数量；其他处理方式给了 `cDimInvCode` 也 400。表头、表体都没有备注列，`cMemo`、`cbMemo` 400「不能设置字段」。
- 新单 `IsWfControlled` 按 QM05 / QM06 是否有已启用的审批流程写 1 或 0；受控的新单之后走 `workflow/submit`。单号按 U8 编号规则取（来料 `QMRJ`、产品 `QMPJ` + 单据日期 + 4 位流水），取号不退。U8 单据模板（VT 355 / 356）必输项未填满 400「缺少必输字段 …」。
- 保存由 U8 自行提交，不在桥的事务里；预演（`dry_run`）在 U8 参照带入、必输项核对之后、取号之前停止。保存后在新连接上按检验单查找新单，核对单号、制单人、表体行数和检验单 `BREJFLAG=1`（U8 保存后置 1）；找不到且 U8 报错 409 `u8_rejected`，回读失败或不符 504 `outcome_unknown`（先 `vouchers/list` 按检验单核对，不要直接重投）。
- 功能权限：来料 `QM02010303`、产品 `QM02020303`；数据权限按检验单的供应商、部门、存货、仓库。

### 其他检验单生单

`qm_other_check`（QM15，VT 365）参照已审核的其他报检单（`qm_other_inspect`），一个报检单行生成一张检验单。走 U8 质量管理组件 `UFQMCo.clsOtherCheckVoucherCO`（基于 VO 的接口，同不良品处理单）的 `AddVoucher`，登录子系统 `QM`。

```json
{"type": "qm_other_check", "source_type": "qm_other_inspect", "id": 1001,
 "head": {"cCheckPersonCode": "op001", "cDepCode": "D901", "project_code": "P01"},
 "lines": [{"source_line_id": 1101, "quantity": 100}]}
```

- 请求规则同来料检验单（`cCheckPersonCode` 必填，表体恰好 1 行，合格、让步、不良数量之和等于 `quantity`，可带 `items`、`fDtQuantity`、`cChkConclusion`、`project_code`、`cDefine1`…`16`、`chDefine11`…`16`）。检验部门 `cDepCode` 省略时取检验员（`Person`）的所属部门，都没有 400（`field` 为 `head.cdepcode`）；不以报检部门代替。检验方案缺省取同存货最近一张其他检验单的方案。
- 报检单须存在（404）、已审核（409）；`source_line_id` 不是本单的行 400；该行已有检验单 409「已生成其他检验单」，`BFLAG=1` 却找不到检验单 409。U8 不写报检单行累计检验数量（`FSUMCHECKQTY`），是否已检看 `BFLAG`。`quantity` 不能超过行数量。
- 没有来源单据：不写 `CSOURCE`、`SOURCEID`、`SOURCEAUTOID`、`SOURCECODE`；检验类型取报检单的（`OTH`）；`ISWFCONTROLLED`、`IVERIFYSTATE`、`IVERIFYSTATENEW` 都写 0，新单未审核，可用 §6 直接审核。表头、检验项目的字段集同来料检验单，属性名按 U8 模板字段名大写。单号按 U8 编号规则取（卡片 QM15），取号不退。U8 单据模板（VT 365）必输项未填 400（有的账套把 `chDefine11`…`13` 设为必输）。
- 保存由 U8 自行提交；预演（`dry_run`）在必输项核对之后、取号之前停止。保存后在新连接上按报检单行（`INSPECTAUTOID`）查找新单，核对制单人、检验项目行数等于方案行数、报检单行 `BFLAG=1`；不符 504 `outcome_unknown`。成功响应另有 `items`（检验项目行数），没有 `wf`。
- 功能权限：`QM02060203`；数据权限按检验部门、存货。

### 采购结算单生单

`purchase_settle`（卡片 99）参照一张采购发票自动结算：每个发票行按其 `RdsId` 与采购入库行配对，结算数量等于发票数量，一张发票生成一张结算单。走 U8 采购组件 `VoucherCO_PU`（载入发票后 `CheckSettle`、`bRdBVAutoSettle`），在桥的事务里，可预演（`dry_run` 回滚模式）。

```json
{"type": "purchase_settle", "source_type": "purchase_invoice", "id": 9000000007,
 "head": {"settle_date": "2026-01-31"}}
```

- 不能带 `lines`（不支持按行结算，400）。表头只收 `settle_date`（`yyyy-MM-dd`，可省），给了即为本笔的 U8 登录日期（覆盖 `date`），U8 写进结算单 `dSVDate` 和发票 `dSDate`。`settle_date` 不能晚于今天，否则 400；它不与请求的 `year`（账套年度库）比较。
- 结算日期：不早于发票日期和各入库单日期（409）；所在期间（按 U8 会计期间 `UA_Period`，不在任何期间内 409）采购未结账（`GL_mend.bflag_PU`，409「结算日期所在期间采购已结账」）；所在月以前的各月采购须已结账，否则 U8 拒绝，409 `u8_rejected` 带 U8 原文（「当前登录日期所在的会计月以前的月份未结账，不能结算」）。上月尚未结账时，把 `settle_date` 设为上月最后一天。
- 发票须存在（404）、普通采购、非期初、蓝字、非现付、已采购复核（`cVerifier`）、未结算（表头、表体 `dSDate` 为空且没有结算行，否则 409「发票已结算」），1 到 400 行；每行都参照采购入库单（`UpSoType` 为 `rd`、`RdsId` 指向存在的入库行），入库单已审核、供应商与发票相同（否则 409「采购入库单的供应商与发票不一致」）。不检查应付审核，结算也不做应付审核（`cPBVVerifier` 不变；应付审核走 `vouchers/verify` 的 `arap_verify`）。
- 桥在同一事务里核对：恰好生成一张结算单，行数等于发票行数，每行（`iRdsID`、`iBsID`、`iSVQuantity`）等于发票行（`RdsId`、`ID`、`iPBVQuantity`），结算单 `dSVDate` 和发票表头、表体 `dSDate` 都等于结算日期，应付审核人未变，`@@TRANCOUNT` 未变；不符回滚并 409 `u8_rejected`。U8 同时回写入库行的结算数量和成本（同月结算时入库行金额改为结算金额，跨月由存货核算的暂估处理接手）。
- 成功响应同生单：`id` 是新结算单 `PSVID`，`code` 是结算号，`lines` 是结算行数，`state.verified` 为 false，另有 `settle_date`。提交后在新连接上回读不到 504 `outcome_unknown`。
- 功能权限「自动结算」`PU040301`（在状态检查之前查）；数据权限（状态检查之后）按发票的供应商（必有）、部门、业务员、采购类型和每行的存货（必有）、入库仓库（可空列）。
## 12. 收付款单和应收应付单

`ar_receipt`（收款单）、`ap_payment`（付款单）在 `Ap_CloseBill`，`ar_bill`（应收单）、`ap_bill`（应付单）在 `Ap_Vouch`。新增、修改、删除只处理手工录入的蓝字单据。核销、取消核销、自动核销、制单、取消制单、坏账、转账（应收冲应付、并账、红票对冲）、汇兑损益见本节各小节。

新增字段名不分大小写，用 U8 列名。名单外 400「不能设置字段 x」，同名重复 400「字段重复 x」。自定义项表头 `cdefine1`…`16`，表体 `cdefine22`…`37`。

| 类型 | 表头 | 表体 |
| --- | --- | --- |
| `ar_receipt` / `ap_payment` | 必填 `cDwCode`（客户或供应商）、`cCode`（结算科目，存在且末级）、`cSSCode`（结算方式）。可写 `dVouchDate`、`cDeptCode`、`cPerson`、`cexch_name`、`iExchRate`、`cDigest`、`cItem_Class`、`cItemCode`、`cOrderNo`、`cBank`、`cBankAccount` | 必填 `cKm`（本系统受控科目）和金额（本币 `iAmt`，外币 `iAmt_f`）。可写 `iType`（0 应收付款，1 预收付款）、`iAmt_f`、`cDepCode`、`cPersonCode`、`cXmClass`、`cXm`、`cMemo` |
| `ar_bill` / `ap_bill` | 必填 `cDwCode`、`cCode`（本系统受控科目）。可写 `dVouchDate`、`cDeptCode`、`cPerson`、`cexch_name`、`iExchRate`、`cDigest`、`cItem_Class`、`cItemCode`、`cPayCode`、`cOrderNo` | 必填 `cCode`（对方科目，不能是应收/应付受控科目）和金额（本币 `iAmount`，外币 `iAmount_f`；也收 `iAmt` / `iAmt_f`，同义的两个只能填一个）。可写 `iAmount_f`（或 `iAmt_f`）、`iTaxRate`（0 到 100）、`cDeptCode`、`cPerson`、`cItem_Class`、`cItemCode`、`cDigest` |

规则：

- 日期缺省登录日期，币种缺省账套本位币（`foreigncurrency.iotherused = -1`）。本位币汇率缺省 1 且必须是 1，原币金额必须等于本币（消息里写本位币名称，如「人民币单据的汇率必须是 1」）。与本位币有关的检查在登录后做，登录前只查字段和金额格式。
- 外币必须给 `iExchRate`（400「外币必须给汇率」），每行必须给原币金额；本币不给按 `round(原币 × 汇率, 2)` 算，给了要与之相差不超过 0.01。
- 外币应收单 / 应付单的行币种按行科目（单据日期所在年度的科目表）定，与 U8 客户端录入一致：科目核算币种（`code.cexch_name`）等于表头币种的行用表头币种、汇率和原币金额；其余科目（收入、费用、税金等）的行记本位币，汇率 1，`iAmount_f` = `iAmount` = 本币金额（`round(原币 × 汇率, 2)` 或调用方给的本币）。`bexchange` 不参与判断。行上仍按表头币种送原币 `iAmount_f`，行的 `cexch_name` / `iExchRate` 不能送。行科目核算的外币既不是表头币种也不是本位币时 400「科目 x 按…核算，与单据币种…不一致」（field `lines.N.ccode`）。
- 外币应收单 / 应付单的表头本币必须等于各行本币之和，且等于 `round(表头原币 × 汇率, 2)`（U8 保存时两条都查），表头原币是各行原币之和。两者不等时桥把差额并到一行记本位币、本币未由调用方给出的行（优先本次送了原币的行；修改时也可以是不动的行，该行随之按新金额重拆税额）。没有可调的行、差额超过 0.01 × 行数、或调整后该行不大于 0：400「表头本币 … 与原币合计 … × 汇率 … = … 不一致，请调整某一记本位币行的本币金额」（field `lines`），不调用 U8；全是外币科目行的多行单据尾差一分时同样 400。修改时表头原币只按删除、新增、改金额、改币种的行增减（记本位币的已有行按 `round(本币 / 汇率, 2)` 折回原币），只改表头或备注、部门等时表头合计不动。
- 金额大于 0、不超过 1000000000000、最多两位小数，只能建蓝字单据。
- 单据日期所在月份应收或应付已结账时 409「应收已结账」/「应付已结账」。
- 币种、结算方式、部门（末级）、业务员、项目大类和项目先查存在，不存在 400。
- 表头金额、未核销金额、应收应付单的不含税金额和税额由桥计算（见 `u8-notes.md`）。部门、业务员、摘要行上没给就取表头。

期初单据（`bStartFlag=1`）不经这几条路由，用 `openings/arap`（§29）。弃审和删除都拒绝已生成凭证、非手工录入（含期初）、来自票据或网银、已核销的单据；弃审还拒绝除本单审核行以外还有往来明细、审核期间已结账；删除要求未审核且没有任何往来明细。都是 409 `state_mismatch`。审核成功要求新连接上审核人等于登录操作员姓名。

例外：`notes/create` 登记生成的收款单（`cSrcFlag='C'`、`cCoVouchType='50'`）同 U8 收付款单审核界面可以弃审，条件是票据号与来源号一致、来源票据的 `iCloseID` 指向本单（分包票据的收款单票据号是「票据号-起-止」，起止须与票据的子票区间一致）、票据不是期初、没有处理记录（`AP_Note_Sub`）、余额等于票面、没换票，其余弃审条件照旧（不满足 409 `state_mismatch`，如「票据 X 已有处理记录（结算、贴现、背书等），不能弃审其收款单」）。这类收款单不能修改、删除，删除走 `notes/delete`。

### 核销 `arap/writeoff`

收款单（付款单）的一行，对若干张销售发票、应收单（采购发票、应付单）核销，相当于 U8 应收（应付）款管理的手工核销。调用 U8 核销组件 `U8ApCancel.cLsCancel`（`Init` 后 `Save` 核销报文），登录子系统随收付款单（收款单 `AR`、付款单 `AP`），在桥的事务里执行。

请求（不带 `type`、`id`）：

```json
{"receipt": {"type": "ar_receipt", "id": 9000000005, "line_id": 9000000103},
 "items": [{"type": "sale_invoice", "id": 9000000006, "line_id": 9000000104, "amount": 6.00}]}
```

| 字段 | 说明 |
| --- | --- |
| `receipt.type` | `ar_receipt` 或 `ap_payment` |
| `receipt.id` | `Ap_CloseBill.iID` |
| `receipt.line_id` | 收付款单表体行 `Ap_CloseBills.ID`。只有一行有未核销余额时可省略，否则 400 |
| `items` | 1 到 50 项。收款单只能核销 `sale_invoice`、`ar_bill`，付款单只能核销 `purchase_invoice`、`ap_bill`，否则 400；同一单据行不能重复 |
| `items[].id` | 销售发票 `SBVID`、采购发票 `PBVID`、应收单 / 应付单 `Ap_Vouch.Auto_ID` |
| `items[].line_id` | 发票表体行（`SaleBillVouchs.AutoID` / `PurBillVouchs.ID`，即往来明细的 `iBVid`）。发票只有一行有未核销余额时可省略。应收单、应付单按整单核销（`iBVid` 为 0），带了 400 |
| `items[].amount` | 本次核销金额（原币），大于 0、不超过 1000000000000、最多两位小数 |
| `date` | 公共字段，登录日期即核销日期（缺省今天） |

币种：收付款单的币种和汇率就是这次核销的币种和汇率（表头为空按本位币；本位币汇率必须是 1，外币汇率必须大于 0）。每张单据必须与它同币种，否则 409 `state_mismatch`「…的币种与收付款单不一致」。单据自己的汇率可以不同：同 U8，核销一律按收付款单的汇率记，汇兑差额留给期末汇兑损益（`9M`，见 `arap/exchange_gain`），本接口不做。报文 `close` 和每个 `vouch` 都写收付款单的 `cexchname`、`iexchrate`；本币 = 原币 × 收付款单汇率四舍五入到分，`close` 按合计折算，各 `vouch` 逐个折算，尾差并到第一个 `vouch`。余额核对一律按原币。

预收 / 预付行（`Ap_CloseBills.bPrePay=1`，即新增时 `iType=1` 的行）同样可以核销，报文带 `bprepay="1"`。U8 在这种行上核销时可能另插 `Ap_CloseBills` 行，桥核对余额时按行主键重读该行，再加上 `Save` 新插行（本收付款单上行主键大于调用前最大值的行）的余额。

以 `AR` / `AP` 登录时 U8 的 `Save` 不查日期、期间和锁，这些由桥在事务里带锁读单据后检查：

- 收付款单存在且类型对得上（否则 404）、已审核（`cCheckMan`）、该行未核销余额 `iRAmt_f` 大于 0；
- 每张单据存在（否则 404）、已应收（应付）审核（有原始往来明细行）、往来单位（原始往来明细的 `cDwCode`）与收付款单相同；
- 收付款单和每张单据不受审批流控制（否则 409 `workflow_enabled`），采购发票没有网络锁（`iNetLock`）；
- 每行的未核销余额不小于 `amount`，口径与 U8 相同：`Ar_Detail` / `Ap_Detail` 上 `cCoVouchType`、`cCoVouchID` 是该单据，`cDwCode` 是往来单位，`iBVid` 是该行，`iFlag<3`（`NULL` 不算），应收取借方减贷方，应付取贷方减借方；
- `amount` 合计不超过收付款单该行的未核销余额；
- 核销日期不早于收付款单和每张单据的日期，也不早于应收（应付）启用日期（`AccInformation` 的 `dARStartDate` / `dAPStartDate`）；
- 会计期间按 `UFSYSTEM..UA_Period`（`dBegin` ≤ 日期 ≤ `dEnd`）定，不一定是自然月；找不到期间 409，该期应收（应付）已结账（`GL_mend.bflag_AR` / `bflag_AP`）409「应收已结账」/「应付已结账」。

400 `bad_request`：字段、类型、数量、金额不合法；`line_id` 不属于该单据「明细行不存在」；没给 `line_id` 而收付款单或发票有多行未核销余额（「请指定 line_id」）。其余拒绝（含币种不一致）都是 409 `state_mismatch`。U8 的 `Save` 抛错或返回 false 时 409 `u8_rejected`，`message` 为 U8 原文。桥不查 `Locked_by_Other`（`LockVouch` 表），见 `docs/limitations.md`。

U8 写入 `Ar_Detail` / `Ap_Detail` 的 `9P` 行（同一个新核销号 `cCancelNo`，`HXAR…` / `HXAP…`；每个单据行一条，另有收付款单的冲减行），并更新收付款单行余额、发票累计核销、应收应付单余额。提交前核对：收付款单该行余额减少了合计、每个单据行余额减少了各自金额、本收付款单上新写的核销行只有一个核销号，否则回滚、409 `u8_rejected`；事务已被数据库回滚（如死锁牺牲品）503 `u8_unavailable`（未写入，可以重试）。提交后在新连接上回读本核销号的核销行，每个单据行的金额和收付款单冲减合计对得上才算成功；不符或回读失败 504 `outcome_unknown`（已提交，先用 `reports/arap_detail` 或 `vouchers/load` 核对，不要直接重投）。

响应：

```json
{"ok": true, "acc": "801", "cancel_no": "HXAR0000000000001", "date": "2026-09-28",
 "receipt": {"type": "ar_receipt", "id": 9000000005, "line_id": 9000000103, "code": "SK0000000001", "remaining": 844.00},
 "items": [{"type": "sale_invoice", "id": 9000000006, "line_id": 9000000104, "code": "SOZP0000000001",
            "amount": 6.00, "remaining": 0.00}]}
```

`remaining` 是核销后的未核销余额；应收单、应付单没有 `line_id`。

- 锁键：收付款单、每张单据，另加 `arap:writeoff:AR` / `arap:writeoff:AP`（同一侧的核销串行）。
- 功能权限：应收「手工核销」`AR050201` 或「选择收款」`AR0503`，应付 `AP050201` 或「选择付款」`AP0503`（U8 的选择收付款在一个界面里同时收付款并核销，所以也放行）。数据权限按收付款单和每张单据表头的往来单位（必控）、部门、业务员，任一张不在权限内 403。
- 可以带 `Idempotency-Key`（§20）：收到 504 先用 `idempotency/get` 查；没带键时先核对再决定是否重试。
- 撤销用 `arap/writeoff/cancel`。

### 取消核销 `arap/writeoff/cancel`

按核销号整批撤销一次核销，相当于 U8 应收（应付）款管理「其他处理 → 取消操作」里取消一条核销（`9P`）。U8 没有可调用的取消组件，桥执行与 U8 界面相同的 SQL（`9P` 分支，实测核对），在请求连接上的一个事务里完成，提交前核对每个余额精确回到核销前。登录子系统就是 `flag`。

请求（不带 `type`、`id`）：

```json
{"flag": "AR", "cancel_no": "HXAR0000000000001"}
```

| 字段 | 说明 |
| --- | --- |
| `flag` | `AR`（应收，`Ar_Detail`）或 `AP`（应付，`Ap_Detail`） |
| `cancel_no` | `arap/writeoff` 返回的核销号，或 U8 里核销的 `cCancelNo`。`HXAR` / `HXAP` 后接 1 到 20 位数字，前缀要与 `flag` 一致，否则 400 |

只收与 `arap/writeoff` 同形的批次。桥在事务里带锁读出该核销号的往来明细（条件 `cProcStyle=N'9P'`、`cCancelNo`、`cFlag`，走 U8 索引 `INDEX_Ar_Detail_HXZD` / `INDEX_Ap_Detail_HXZD`，只锁这一批）后检查：

- 一行都没有 404「核销号不存在」；超过 500 行 409；
- 每行都是本侧（`cFlag` = `flag`）的 `9P` 行，挂在同一张收款单（`48`）/ 付款单（`49`）上，对方是这张收付款单自己（冲减行，`iCoClosesID` 是它的行）、销售发票 `26` / `27` 或应收单 `R0`（应付：采购发票 `01` / `02` 或应付单 `P0`）；否则 409（请在 U8 客户端取消）；
- 没有制单（`cPZid` 为空），否则 409「核销已制单，请先删除凭证」；没有合同（`cContractID`）；同一会计期间；整批同一币种、同一汇率（否则 409，请在 U8 客户端取消）。外币核销同样能取消：原币、本币分开按核销行加回，与单据自己的汇率无关；
- 核销行的期间（`iPeriod`，年度按核销登记日期在 `UA_Period` 上所在的年度）应收（应付）未结账，否则 409「应收已结账，不能取消该期间的核销」；
- 收付款单和每张单据在本批之后（`Auto_ID` 更大、核销号不同）没有审核登记（Sign）以外的处理，否则 409「单据在本次核销之后还有其他处理，请先取消后面的处理」。Sign 行是 `cProcStyle` 为 `26`、`27`、`R0`、`48`、`49`、`01`、`02`、`P0` 或等于该行单据类型（`cVouchType`，如期初 `50`）的行；其余都算后续处理，包括核销 `9P`、红票对冲 `9N`、汇兑损益 `9M`、票据背书 `9E`、应收冲应付 `9I`、应付冲应收 `9J`、票据托收 / 退回 / 贴现 `9A` / `9C` / `9D`、并账 `BZ` 等（与 U8 的判断一致）；
- 应收一侧：账套里有未审核的收款单（不含现结，全账套检查，同 U8 存储过程 `AR_ExistUnAuditCloseBill`）时 409，U8 同样不让取消；
- 收付款单、每张单据都在（按单号带锁读），采购发票没有网络锁（`iNetLock`）。

写入内容和顺序同 U8：按冲减行加回 `Ap_CloseBills.iRAmt_f` / `iRAmt` / `iRAmt_s`（表头由触发器 `TR_Ap_CloseBills` 汇总）；加回应收单 / 应付单 `Ap_Vouch.iRAmount_f` / `iRAmount`，`iRAmount_s` 按原币余额比例重算；销售发票经临时表 `#ap_SaleBillVouchHXdata` 交 U8 回写组件 `Ussaupdispatch.clsWrite2Bill.UpdateBillForAR`（同一事务）加回 `iExchSum` / `iMoneySum`，并连带销售订单、发货单的累计核销和信用额度；采购发票经 `#ap_PurBillVouchHXdata` 加回 `PurBillVouchs.iOriTotal` / `iTotal`；收付款单各行都回到未核销时清空 `cCancelMan`、`bPrepay` 置 0；最后删掉该核销号的 `9P` 行。不做 U8 的「保留线索」（选项 `bAR2Cancel` / `bAP2Cancel`）。

提交前在同一事务里核对：核销行已删光；收付款单每行余额 = 原值 + 冲减合计；每个单据行余额（同核销的口径）= 原值 + 本批金额；应收单 / 应付单 `iRAmount_f` = 原值 + 本批金额；发票累计核销 = 原值 + 临时表合计。不符回滚、409 `u8_rejected`；回写组件抛错 409 `u8_rejected`（原文），改变了事务 504；事务被数据库回滚 503 `u8_unavailable`。提交后在新连接上确认该核销号的行已不在：读不出或仍在 504 `outcome_unknown`（已提交，先核对，不要直接重投）。

响应：

```json
{"ok": true, "acc": "801", "cancel_no": "HXAR0000000000001", "flag": "AR",
 "receipt": {"type": "ar_receipt", "id": 9000000005, "line_id": 9000000103, "code": "SK0000000001", "remaining": 850.00,
             "lines": [{"line_id": 9000000103, "amount": 6.00, "remaining": 850.00}]},
 "items": [{"type": "sale_invoice", "id": 9000000006, "line_id": 9000000104, "code": "SOZP0000000001",
            "amount": 6.00, "remaining": 6.00}]}
```

`amount` 是加回的金额，`remaining` 是取消后的未核销余额；批次涉及收付款单多行时 `receipt.line_id`、`receipt.remaining` 为 `null`，看 `lines`。

- 锁键只有 `arap:writeoff:AR` / `arap:writeoff:AP`（涉及的单据入队前不可知，所以与同一侧的全部核销、取消核销串行）。
- 功能权限：「其他处理 → 取消操作」应收 `AR0807`、应付 `AP0807`；数据权限同核销。
- 可以带 `Idempotency-Key`（§20）；收到 504 先用 `idempotency/get` 查，或用 `reports/arap_detail`（带 `include_writeoff`）查该核销号是否还在。

### 自动核销 `arap/writeoff/auto`

对一个客户（供应商）按 U8 的自动核销规则配对，一次核掉多行，相当于 U8 应收（应付）款管理的「自动核销」。U8 的 `cLsCancel.AutoCancel` 无界面调用时不写入（见 `u8-notes.md`），所以由桥配对，每行收付款单一批，交给与 `arap/writeoff` 相同的核心（同一道闸门、同一个 `Save` 报文：一个 `close` 下挂这一批的全部 `vouch`，同样在事务里核对）。登录子系统就是 `flag`，核销日期是登录日期。

请求（不带 `type`、`id`）：

```json
{"flag": "AR", "partner": "C900001", "date_to": "2026-09-28",
 "receipt": {"type": "ar_receipt", "id": 9000000005, "line_id": 9000000103},
 "targets": [{"type": "sale_invoice", "id": 9000000006}],
 "max_amount": 100.00, "dry_run": true}
```

| 字段 | 说明 |
| --- | --- |
| `flag` | `AR` 或 `AP`，也是登录子系统 |
| `partner` | 必填，客户（`AR`）或供应商（`AP`）编码（`cDwCode`），1 到 20 个字符。不做整个账套的自动核销 |
| `date_to` | 可选，只取单据日期不晚于它的收付款单和单据；缺省登录日期，晚于登录日期 400 |
| `receipt` | 可选，`{type, id[, line_id]}`：只用这张收付款单（这一行）。`type` 须与 `flag` 一致（`ar_receipt` / `ap_payment`），否则 400 |
| `targets` | 可选，1 到 200 项 `{type, id}`：只核这些单据（不分行）。类型须与 `flag` 一致，不能重复，否则 400 |
| `max_amount` | 可选，本次核销合计上限（原币累计，不分币种），大于 0、最多两位小数 |
| `dry_run` | 可选布尔，缺省 `false`。`true` 只返回计划，不写 |
| `include_prepay` | 可选布尔，缺省 `false`。`true` 时预收 / 预付（`bPrePay=1`）的收付款单行也参加配对（报文带 `bprepay="1"`，同 `arap/writeoff`） |

配对规则（U8 核销规则 `iHxRule=0` 时的规则，只按往来单位，不按订单、合同、存货）：

- 收付款单一侧：该往来单位下已审核、币种汇率有效（本位币汇率 1，外币汇率大于 0）、不受审批流控制、单据日期不晚于 `date_to`、行余额 `iRAmt_f` 大于 0 的收款单（付款单）行，按单据日期、`iID`、行 `ID` 排序。预收 / 预付行只在 `include_prepay: true` 时参加。
- 单据一侧：该往来单位下有未核销余额的行（余额口径同 `arap/writeoff`），类型为销售发票 `26` / `27`、应收单 `R0`（应付：采购发票 `01` / `02`、应付单 `P0`），已审核、币种汇率有效、不受审批流控制、采购发票没有网络锁、单据日期不晚于 `date_to`；按单据日期、单号、行排序。
- 先进先出：第一行收付款单冲第一行单据，每次取两边剩余余额（和 `max_amount` 剩余）的较小者，一边用完换下一行，直到任一边用完或到上限。只在同币种（空按本位币）的行之间配对；单据汇率不比，按收付款单汇率核销，汇兑差额留给期末汇兑损益。
- 一次最多 20 行收付款单、200 行单据，超出 409，请用 `receipt`、`targets` 或 `date_to` 缩小范围。

账套设置了按规则核销（`iHxRule` 不为 0，或 `bAPAutoCancelWithHxRule` 为真）时 409「账套设置了按规则核销（订单 / 合同 / 存货等），自动核销暂不支持」，`dry_run` 同样。

执行：全部批在一个事务里，逐批先过 `arap/writeoff` 的闸门（带锁重读，余额含前面各批 `Save` 的结果；同样的 404 / 409 / 403），再 `Save`、核对余额和新核销号；任一批失败整体回滚，返回 U8 原文（409 `u8_rejected`）或闸门的错误。提交后在新连接上逐批回读核销行，不符或回读失败 504 `outcome_unknown`（已提交，`message` 列出核销号，先核对，不要直接重投）。没有可配对的不是错误：200，`batches` 为空、`total` 为 0。

`dry_run`：候选查询和配对照做，每一批也过闸门和数据权限（只读，不开事务、不建组件），返回计划。

响应（执行）：

```json
{"ok": true, "acc": "801", "flag": "AR", "partner": "C900001", "date": "2026-09-28", "date_to": "2026-09-28",
 "batches": [{"cancel_no": "HXAR0000000000002",
              "receipt": {"type": "ar_receipt", "id": 9000000005, "line_id": 9000000103, "code": "SK0000000001",
                          "remaining": 750.00},
              "items": [{"type": "sale_invoice", "id": 9000000006, "line_id": 9000000104, "code": "SOZP0000000001",
                         "amount": 100.00, "remaining": 0.00}],
              "amount": 100.00}],
 "total": 100.00}
```

响应（`dry_run`）：`dry_run: true`、`mode: "plan"`（区别于 §23 的预演响应），`plan` 每项 `{receipt: {type, id, line_id, code, date, remaining}, targets: [{type, id, line_id, code, date, balance, amount}], amount}`（`remaining`、`balance` 是分配前的余额），另有 `total`；没有 `batches`。

- 每一批的核销号可以用 `arap/writeoff/cancel` 逐个取消（`reports/arap_writeoffs` 可查）。
- 锁键只有 `arap:writeoff:AR` / `arap:writeoff:AP`（与同一侧的全部核销、取消核销串行），受全局写闸门约束（`dry_run` 也是）。
- 功能权限：「核销处理 → 自动核销」应收 `AR050202`、应付 `AP050202`，与手工核销分开（只有手工核销权限不能自动核销），没有时 403 `no_permission`；数据权限同 `arap/writeoff`。
- 执行时可以带 `Idempotency-Key`（§20），`dry_run: true` 不能带（400「预演不能带 Idempotency-Key」）；收到 504 先用 `idempotency/get` 查，或用 `reports/arap_writeoffs` 按往来单位核对。

### 制单 `arap/voucher`

一张已审核的单据生成一张总账凭证，相当于 U8 应收（应付）款管理「制单处理」里对一张单据制单；用 `ids` 可把同类型的 2 到 20 张单据合成一张凭证（「合并制单」，见下文）。U8 没有可无界面调用的制单组件，桥按 U8 的制单规则拼分录，经 U8 凭证导入组件 `U8PzInsert.clsPZInsert.Transact` 保存（同 §14，组件自行提交），再做与 U8 相同的回写。登录子系统就是 `flag`。

请求（带 `type`、`id`）：

```json
{"flag": "AR", "type": "sale_invoice", "id": 9000000006, "sign": "转", "voucher_date": "2026-09-24", "digest": "销售"}
```

| 字段 | 说明 |
| --- | --- |
| `flag` | `AR` 或 `AP`，也是登录子系统 |
| `type` | 应收 `sale_invoice`（26 / 27）、`ar_receipt`（48）、`ar_bill`（R0）、`ar_refund`（客户退款 49）；应付 `purchase_invoice`（01 / 02）、`ap_payment`（49）、`ap_bill`（P0）、`ap_refund`（供应商退款 48）。须与 `flag` 同侧，否则 400 |
| `id` | 单据主键：发票 `SBVID` / `PBVID`，收付款单和退款单 `Ap_CloseBill.iID`，应收应付单 `Ap_Vouch.Auto_ID`。与 `ids` 只能给一个 |
| `ids` | 可选，合并制单：`[{"type": …, "id": …}, …]`，2 到 20 项，`type` 都相同、`id` 不重复，否则 400。经 API 时顶层 `type` 可省略（取 `ids` 的）；直接调桥时顶层 `type` 必填且与各项一致 |
| `sign` | 可选，凭证类别（`dsign.csign`，不能是调整期类别）。省略时：凭证里有现金或银行科目（`code.bcash` / `bbank`）时，第一行这种科目在借方用「收」、在贷方用「付」，否则「转」；该类别不存在 409，请指定 |
| `voucher_date` | 可选，制单日期 `yyyy-MM-dd`，缺省取单据日期（合并制单取最晚的单据日期）。请求的 `date` 是登录日期，不是制单日期 |
| `digest` | 可选，摘要，最多 120 字；缺省取往来明细上的摘要（往来行优先），都没有就是「销售 / 购 / 收 / 付」加往来单位名称。给了就每行相同；合并制单缺省时各单据的分录用各自的摘要 |
| `cash_items` | 可选，指定现金流量项目：`{"<科目编码>": "<现金流量项目编码>"}`，最多 20 个科目，科目编码不超过 40 位、项目编码不超过 20 位、都不含空白，科目不分大小写不能重复，否则 400。要挂项目的分录（见下文「现金流量」）科目在表里就用给的项目；项目须存在（`fitemss98`）且未关闭，否则 400（`field` 为 `cash_items.<科目>`）；科目在本次凭证里没有要挂项目的分录 400「科目 X 在本次凭证里没有现金流量行」（`field` 为 `cash_items`） |

支持的单据和分录（与 U8 客户端制单结果一致；科目都按 `code` 中会计年度 = 制单日期年份的科目查）：

| 单据 | 借方 | 贷方 |
| --- | --- | --- |
| 销售发票 | 往来科目：往来明细（`Ar_Detail`，审核时登记，`cProcStyle = cVouchType`）的 `cCode`，金额 `iDAmount` | 收入科目按表体 `iNatMoney`、销项税科目按 `iNatTax` |
| 采购发票 | 采购科目按表体 `iMoney`、进项税科目按 `iTaxPrice` | 往来科目：`Ap_Detail.cCode`，金额 `iCAmount` |
| 收款单 | 结算科目：往来明细的结算行（`iFlag=6`，现金、银行、票据科目），带结算方式、票据号、单据日期 | 往来科目：往来行（`iFlag=0`）；票据登记行（`iFlag=3`）不出分录 |
| 付款单 | 往来科目（往来行） | 结算科目（结算行） |
| 供应商退款 | 往来科目（往来行，**负数**），再结算科目（结算行，正数） | — |
| 客户退款 | — | 往来科目（往来行，**负数**），再结算科目（结算行，正数） |
| 应收单 | 往来科目（往来明细） | 对方科目：表体 `Ap_Vouchs.cCode`，不含税 `iNoTaxAmount`；税额 `iNatTax` 走销项税科目 |
| 应付单 | 对方科目 + 进项税科目 | 往来科目 |

- 收入 / 采购 / 税金科目：先查「对方科目设置」`AP_OppCodeSet`（`cFlag`、会计年度）：行上填了的键（存货、存货分类、往来单位、往来单位分类、销售 / 采购类型、地区、币种、税率）都要与单据一致，分类和地区按编码前缀；取最具体的一行（第一个填了的键越靠前越具体，同一个键比编码长度），同样具体的几行给出不同科目 409。没有匹配行再用「基本科目设置」`Ap_InputCode`：销售收入 `xssrkm`、应交增值税 `xssjkm`、采购 `cgkm`、采购税金 `cgsjkm`。都没有 409，采购发票是「采购发票制单暂不支持：账套未设置采购科目」。**从不猜科目。**
- 科目要存在、末级、未封存、不核算外币，否则 409。分录上的档案值同总账新增（§14）逐项再查：部门存在且末级，人员、客户、供应商、结算方式存在，现金流量项目存在且未关闭，否则 409「第 N 行分录的部门 … 不存在或不是末级部门」之类。辅助核算只按科目要求（`bcus` / `bsup` / `bdept` / `bperson` / `bitem`）填，值取自单据：客户 / 供应商取往来单位（`AR` 只能是客户、`AP` 只能是供应商），部门、个人取明细行或表体行，个人没有部门时取人员档案的部门；项目大类是「存货核算」（`fitem.ctable = inventory`）时项目取存货编码，其余大类要单据行上带了同一大类的项目。缺值 409。有客户或供应商辅助项的行另写业务员姓名（`cname`；单据没有业务员写 `-`）。
- 合并：应收应付选项 `bYPzKMHB`（发票、应收应付单）/ `bSPzKMHB`（收付款单）为真（缺省）时，同科目、同方向、同辅助项的行合并。借方在前，同方向保持上面的先后。借贷必须平、2 到 200 行，否则 409。
- 现金流量：总账选项 `bXJLL` 开着、凭证里既有现金流量科目（`code.bCashItem`）又有别的科目时，别的科目每行按「现金流量项目数据来源」`GL_CashItemDataSource` 挂一个项目（按科目编码前缀，`bDir` 1 对借方行、0 对贷方行，取最长的前缀；有起止日期的按制单日期），金额方向同该行；定不下来（没有匹配的前缀，或同样长的前缀给出不同项目）409「科目 X 的现金流量项目无法按数据来源唯一确定，请用 cash_items 指定，或在 U8 客户端制单」。
- 附单据数 = 单据张数。只做本币（本位币、汇率 1）、蓝字（金额不为负）单据，外币、红字 409。
- 退款单：U8 审核时把退款登记成负数往来明细，凭证与 U8 客户端生成的退款凭证一致：往来行在前、红字（金额为负，留在明细的那一方），结算行翻到同一方、记正数，借贷合计相等（都可以是 0）。供应商退款借 应付 −金额、借 银行 +金额，凭证类别缺省「收」；客户退款贷 应收 −金额、贷 银行 +金额，缺省「付」。摘要缺省同收付款单，都没有时「收<供应商>退款」/「付<客户>退款」。`coutsign` 两种都是 `RP`。现金流量行同分录的列和符号（往来红字行的现金流量也是负数），数据来源的方向按分录所在的列取。往来明细之外的费用行不拼：往来明细有 `iFlag` 0 / 3 / 6 以外的行或借贷不平 409，请在 U8 客户端制单。

闸门（在事务里带锁，409 `state_mismatch`，文案尽量用 U8 的）：

- 单据不存在 404；类型不在上表 400；
- 未审核（发票：未应收 / 应付审核）或没有审核登记的往来明细；已制单「凭证已生成，不能重复制单。」；
- 现结 / 现付发票（往来明细有 `cProcStyle='XJ'` 行）409「现结发票请在 U8 客户端制单」；
- 已用于坏账收回的收款单（往来明细有 `cVouchType='48'`、`cVouchID` = 该单号的 `9H` 行）409「收款单 X 已用于坏账收回（处理号 HZAR…），请对坏账收回制单」，用 `arap/process/voucher` 对该 `HZAR` 处理号制单；
- 总账里已有本单的凭证（`coutbillsign` = 单据类型、`coutid` = 单号、制单系统 `flag` 或 `GL`，不论是否记账）而它的外部业务号没有本单任何往来明细引用（上次制单 504 留下的）409「单据已有一张没有回写的凭证 …（外部业务号 …）…」，核对后用取消制单或在 U8 删除该凭证再制单（存货核算结转成本的凭证制单系统是 `IA`，不算）；
- 单据登记期间应收（应付）已结账；制单日期早于单据日期、不在登录年度内、总账该期间已结账、应收（应付）该月已结账；制单序时控制（`bMakShtSort`）；
- 科目、辅助项、现金流量、凭证类别如上。

功能权限：应收 `AR0508`、应付 `AP0508`（U8 授权目录「生成凭证」）；数据权限按单据表头的往来单位（必控）、部门、业务员（同核销）。

执行分三步：

1. 事务：带锁读单据、过闸门、拼分录，取外部业务号：`Ap_CancelNo` 里 `cType='PZ'`、`cFlag=AR|AP` 的号加一（`AR` / `AP` 加 13 位补零数字，如 `AR0000000000001`；已被占用就往后跳），提交。
2. `Transact` 保存凭证（`renewproofno="y"`，U8 取凭证号）。报文带外部来源：`voucher_making_system` = `AR` / `AP`、`reserve1`（`coutsign`：付款单 `RP`、应付单 `AR`，其余同 `flag`）、`reserve2` = 外部业务号、每条分录 `bill_type` / `bill_id` / `bill_date`（单据类型、单号、制单日期）。按 `AR` / `AP` 被拒且确认未写入时改用 `GL` 再导一次（响应 `making_system` 说明实际值），仍被拒 409 `u8_rejected`，带 U8 原文（外部业务号已消耗，同 U8 不回退）。然后（不在事务里）核对 U8 返回的凭证号上正是这张凭证（行数、借贷合计、未记账），否则 504，不碰它。
3. 事务：补齐凭证的来源列（`coutsysname`、`coutsign`、`coutno_id`、`coutbillsign`、`coutid` 等）和 `GL_CashTable.csign`，做与 U8 相同的回写：往来明细 `cPZid`、`dPZDate`、`cGLSign`、`iGLno_id`、`ino_id`（该明细行所在分录的分录号）；发票表体 `cClue` = 外部业务号、`cPZNum`（如 `转-0001`）、`dSignDate`；收付款单、应收应付单表头 `cPzID`、`cPZNum`、`doutbilldate`。核对回写完整、外部业务号没有撞号（U8 客户端取号不等桥的锁：总账里只有本凭证用这个号，往来明细里用这个号的正好是本单的原始行），都对才提交。

第 3 步失败（包括单据在第 1、3 步之间被 U8 客户端制了单、外部业务号撞号）：在新事务里删掉刚生成的凭证（同取消制单；撞号时只按凭证键删本凭证，不清别人的回写）并清掉回写，409 `state_mismatch`「制单回写失败（…），已删除刚生成的凭证 …」，单据仍未制单；删不掉 504 `outcome_unknown`，消息带凭证号和外部业务号，请在 U8 里删除该凭证。第 2 步调用异常、返回无法解析或没有凭证号 504 `outcome_unknown`（凭证可能已保存）。提交后在新连接上回读凭证行和往来明细，对不上或读不出 504。

响应：

```json
{"ok": true, "acc": "801", "flag": "AR", "type": "sale_invoice", "id": 9000000006, "code": "SOZP0000000001",
 "pz_id": "AR0000000000001", "making_system": "AR",
 "voucher": {"year": 2026, "period": 9, "sign": "转", "no": 1, "num": "转-0001", "date": "2026-09-24"},
 "lines": [{"entry": 1, "account": "1122", "digest": "销售…", "debit": 113.00, "credit": 0, "customer": "C900001",
            "dept": null, "person": null, "supplier": null, "item_class": null, "item": null, "settle": null},
           {"entry": 2, "account": "6001", "digest": "销售…", "debit": 0, "credit": 100.00, "item_class": "ch",
            "item": "INV001", "...": "..."},
           {"entry": 3, "account": "2221", "digest": "销售…", "debit": 0, "credit": 13.00, "...": "..."}]}
```

`lines` 是提交后回读的凭证行，每行另有 `bill_code`（凭证行的 `coutid`，即来源单据号）。`bills` 是本凭证覆盖的单据 `[{"type","id","code","vouch_type"}]`（按请求顺序，单张制单也有一项）；`type`、`id`、`code` 是第一张。

合并制单（`ids`）：与 U8 合并制单的凭证一致（例如多张收款单一张凭证，每行 `coutid` 是该行来源单据号）。逐张带锁读单据、过上面全部闸门（任一张不合格整笔拒绝，消息前加「ids 第 i 张（id …）」，`field` 是 `ids.<i>`）；每张单据各自拼分录、只在本单内合并，再整张排序（借方在前，同方向按单据顺序）；制单日期不早于任何一张单据日期；一个外部业务号、一张凭证，各单据逐张回写。锁键是每张单据的单据键加下面的凭证键。预演的 `detail.voucher.sources` 列出全部单据、每行分录带 `bill_code`。取消用 `arap/voucher/delete` 一次完成。不做混合类型（如发票和收款单一张凭证）、核销制单。

- 锁键：单据键（`sale_invoice:<id>` 等）、`new:gl:<类别>`（与总账新增共用，没给 `sign` 时收、付、转都锁）、`arap:voucher:AR|AP`，受全局写闸门约束。
- 可以带 `Idempotency-Key`（§20），收到 504 先用 `idempotency/get` 查。没带键时注意：504 时往来明细的 `cPZid` 往往还是空的（凭证已导入、回写没做），不能只看它；先在总账按单据号（`coutid` = 单号、`coutbillsign` = 单据类型）或消息里的外部业务号（`coutno_id`）核对（`gl/vouchers/list` 看当期新凭证）。直接重投时若总账已有本单没有回写的凭证，本路由 409 并点名那张凭证，不会生成第二张。
- 凭证之后可在总账审核、记账；要改先取消制单再重做（总账的修改、作废、删除只收总账自己做的凭证）。

### 取消制单 `arap/voucher/delete`

按外部业务号删除应收（应付）生成的凭证并清掉单据上的凭证号，相当于 U8 应收（应付）款管理「凭证查询」里删除凭证。桥在请求连接的一个事务里执行与 U8 相同的删除和清除。登录子系统就是 `flag`。

请求（不带 `type`、`id`）：

```json
{"flag": "AR", "pz_id": "AR0000000000001"}
```

`pz_id` 是 `arap/voucher` 返回的外部业务号，或 U8 里制单的单据往来明细上的 `cPZid`（= 凭证的 `coutno_id`）；`AR` / `AP` 后接 1 到 20 位数字，前缀要与 `flag` 一致，否则 400。

闸门（带锁）：

- 没有这张凭证 404；一个外部业务号对应多张凭证 409；
- 来源不是发票、收付款单、应收应付单（`coutbillsign` 不是 `26`、`27`、`01`、`02`、`48`、`49`、`R0`、`P0`）409；
- 应收、应付往来明细里引用该外部业务号的行，只要有一行不是本方（`cFlag` = `flag`）上述 8 类单据的原始行（`cProcStyle = cVouchType`）就 409「该凭证不是发票 / 收付款单 / 应收应付单制单或处理制单（应收冲应付、应付冲应收、并账、汇兑损益、红票对冲、票据处理、坏账处理整批）生成的，请在 U8 客户端删除」。核销（`9P`）、转账等处理生成的凭证也带原单的 `coutbillsign`，删除时 U8 各有回写，桥不做。没有往来明细引用的凭证（例如制单 504 留下的）可以取消。处理制单的凭证（整批引用）放行，只清凭证号，其中坏账收回（`9H`）的凭证连同收款单审核行和表头的凭证号一起清；坏账处理的凭证属于第二级写入，条件同 `arap/bad_debt`；
- 「此凭证已记账，不能删除」「此凭证已审核，不能删除」「此凭证已经出纳签字，不能删除」「此凭证所在期间已结账，不能删除」（U8 原文）；已做银行对账或往来两清；被红字冲销；正被别人编辑（`GL_mvcontrol`）；不是本系统生成的（`coutsysname` ≠ `flag`）；应收（应付）在凭证月份或单据登记期间已结账。

功能权限同制单（`AR0508` / `AP0508`）；数据权限按往来明细的往来单位、部门、业务员。

合并制单的凭证同样一次取消：清除语句按外部业务号执行，引用它的全部单据在同一个事务里清掉凭证号。

写入：删未记账凭证（`GL_accvouch`、`GL_CashTable`、`GL_CodeRemark`）；同 U8 清除发票表体、`Ar_BadPara`、`AR_RZDetail`、`Ap_Vouch`、`Ap_CloseBill`、`Ap_Note_Sub`、`CM_Balance` 上的凭证号，往来明细的 `cPZid`、`cGLSign`、`iGLno_id`（`dPZDate`、`ino_id` 不清）。提交前核对凭证行、往来明细、发票线索号、表头凭证号都不再引用该外部业务号，否则回滚 409 `u8_rejected`；提交后在新连接上再核对：读不出或仍有引用 504 `outcome_unknown`（已提交，先核对，不要直接重投）。`Ap_CancelNo` 的号不回退（同 U8）。

响应：`{"ok": true, "acc": "801", "flag": "AR", "pz_id": "AR0000000000001", "deleted": true, "voucher": {"year": 2026, "period": 9, "sign": "转", "no": 1}}`。锁键只有 `arap:voucher:AR|AP`，受全局写闸门约束。

### 坏账 `arap/bad_debt`

U8 应收款管理「坏账处理」的三种操作，只做应收（登录子系统 `AR`）。属于**第二级写入**（复刻 U8 界面执行的 SQL，已在测试账套实测核对）：缺省关闭，桥 `config.json` 未开 `enableReplicatedWrites` 时 403 `feature_disabled`；打开后只对 `testAccounts` 里的账套开放，其他账套 403 `test_account_only`。两道检查在登录前做，含预演。正式账套请在 U8 客户端操作（见 `docs/configuration.md`、`docs/limitations.md`）。写入在请求的事务里执行，提交前核对。

| `action` | 处理 | 处理方式 | 处理号 | 字段 |
|---|---|---|---|---|
| `occur` | 坏账发生 | 9G | `HZAR`… | `customer`（必填）、`lines`（必填，1 到 50 项）、`currency`、`digest`（缺省「坏账发生」）、`dept`、`person` |
| `recover` | 坏账收回 | 9H | `HZAR`… | `customer`、`receipt`、`amount`（都必填）、`currency`、`digest`（缺省「坏账收回」） |
| `provision` | 计提坏账准备 | 9F | `HZAR`… | 无（只有登录字段和 `dry_run`） |

某个 `action` 不收的字段给了就 400。三种处理共用 U8 的处理号 `HZAR` + 13 位数字（`Ap_CancelNo` 的 `cType=HZ`、`cFlag=AR`，在桥的事务里取号）；`currency` 省略为本位币。

公共条件（409 `state_mismatch`，除注明外）：

- 处理日期就是登录日期 `date`；会计年度、期间按 `UA_Period` 定，不在任何会计期间里 409「日期 … 不在 U8 的会计期间里」，早于应收启用日期 409；该月应收已结账（`GL_mend.bflag_AR`）409「应收 YYYY 年 M 月已结账」。`digest` 为空串或全空白同省略。
- 坏账准备参数（`Ar_BadPara`）：同 U8，发生、收回都更新 `autoid` 最大的那一行；该行的 `iYear` 与 `date` 所在会计年度不同时 409，不碰别的年度；没有该年度参数行 409「未设置 YYYY 年坏账准备参数（应收款管理 › 设置 › 坏账准备）」；计提方法 `iJtStyle` 不是 1、2、3 时 409「不支持的坏账计提方法」（不支持直接销售法）。

`occur`（坏账发生）：`lines` 每项 `{type, id, line_id?, amount}`。`type` 是 `26` / `27` / `28` / `29`（销售发票）或 `R0` 到 `R9`（应收单，不含 `RZ`；不收收付款单，同 U8），`id` 是单据号；`line_id` 是发票表体行，省略时按行主键从小到大分摊，应收单按整单、不能带；`amount` 是原币，大于 0、最多两位小数、不超过该单据（行）的正余额。同一单据行不能重复，同一单据不能既整单又按行。单据须已审核、属于 `customer`、币种与 `currency` 相同；外币各单据的汇率须相同（否则 400），坏账行用单据的汇率折本币。`dept`（最多 12 位）须是存在的末级部门、`person` 须是存在的业务员，登记日期当天或之前已停用的也不行（都是 400，同应收单据新增）。写入：一个处理号，每个单据行一行 9G 贷方往来明细（`iCAmount` / `iCAmount_f`，`cVouchType = cCoVouchType` 为原单据，`cCode` 是原单据的应收科目），冲减单据余额（应收单 `Ap_Vouch.iRAmount*`；发票经 `#ap_SaleBillVouchHXdata` 和 U8 回写组件 `UpdateBillForAR`），坏账准备余额 `iRemainAmount` 减去本币合计。

`recover`（坏账收回）：`receipt` 是该客户、该币种的收款单（`48`）单号（须唯一），须未审核（`cCheckMan` 为空）、未核销（`cCancelNo` 为空）、**只有一行**应收款（`iType=0`，多行 409）、不是票据或网银生成、不受审批流锁定；已有往来明细（做过审核、核销或其他处理）时在审核前拒绝（409）。`amount` 必须等于收款单全部余额，否则 409「坏账收回金额须等于收款单金额 X」（U8 整张收款单一起消耗，取消时整张复原）。写入（一个事务）：先用 U8 收款单审核组件审核收款单（同 `vouchers/verify` 的 `ar_receipt`，写审核行（贷方）和审核人；审核日期是登录日期），再写一行 9H 借方往来明细挂在收款单上（`cVouchType = cCoVouchType = 48`，登记日期为 `date`，科目取收款单行的应收科目 `Ap_CloseBills.cKm`），收款单余额 `iRAmt*` 清零，坏账准备余额加上本币金额。9H 借方与审核行贷方相抵，客户应收余额不变；提交前核对这张收款单上的往来明细（`iFlag<3`）借贷合计本币、原币都为 0，否则回滚 409 `u8_rejected`，消息带合计。不另建应收单。制单时一并回写收款单审核行和表头凭证号（见下文「制单」），U8 的制单列表不再列出这张收款单。

`provision`（计提坏账准备）：按参数行的计提方法算应计坏账准备 `target`：

| `iJtStyle` | 方法 | 基数 `base` | `target` |
|---|---|---|---|
| 1 | 应收余额百分比 | 应收往来明细借贷差（`iFlag<3`，不含合同等业务类型） | `base × nJtRate` |
| 2 | 账龄分析 | 按 `Ar_BadAge` 各账龄区间的应收余额（账套选项按收款条件的信用天数推算时同 U8） | Σ 区间余额 × 区间比率 |
| 3 | 销售收入百分比 | `date` 所在年度 1 月 1 日到 `date` 已审核、非期初、未作废的销售发票本币价税合计（`SaleBillVouchs.iNatMoney`） | `base × nJtRate` |

方法 3 的取数区间是本服务的约定，与 U8 界面结果不一致时以 U8 为准。本次计提 = `round(target − 当前余额, 2)`，可以为负（冲回）；为 0 时 409「本次计提金额为 0」。写入：只更新该年度的参数行（`dJtDate`、`iRemainAmount` 与 `iJtAmount` 各加本次计提、`cProcStyle=9F`、`cCancelNo` 为本次处理号），不写往来明细；没有该年度参数行时 409（U8 会新增一行，桥不新增）。

响应：`{"ok": true, "acc": "801", "action": "occur", "cancel_no": "HZAR0000000000001", "style": "9G", "amount": 120.5, "remain_before": 500, "remain_after": 379.5, "rows": [{"type": "26", "id": "0000000012", "line_id": 1001, "amount": 100.5, "remaining": 0}, …]}`。`amount` 在发生、收回是原币合计，在计提是本次计提（本币）；`remain_before` / `remain_after` 是坏账准备余额；`style_name` 是处理方式名称（坏账发生、坏账收回、计提坏账）；计提另有 `base`、`rate`、`target`、`method_name`（应收余额百分比法、账龄分析法、销售收入百分比法）。`dry_run` 是 rollback 模式。

取消（`arap/process/cancel`，`flag=AR`，`cancel_no` 为 `HZAR`…，开放条件同上）：桥按处理号判断种类（往来明细里的 9G / 9H 行，或参数行 `cCancelNo` 对应 9F）。处理所在会计年度、期间按处理日期在 `UA_Period` 里查。

- 条件：未制单（9F 看参数行的 `cPZID`）、处理所在月份应收未结账；9G / 9H 涉及的单据在本次之后没有审核登记以外的处理。
- 9G：加回单据余额和坏账准备余额。
- 9H：删处理行、收款单余额复原、坏账准备余额减回，再用 U8 收款单弃审组件撤销审核（删审核行和审核人；审核所在期间已结账 409），核对收款单未审核、余额已复原、没有往来明细，不符回滚 409 `u8_rejected`。收款单没有审核行的 9H（如在 U8 客户端做的坏账收回）409「该坏账收回不是本接口生成的（收款单没有审核行），请在 U8 客户端取消」；收款单号不唯一 409。
- 9F：参数行 `iRemainAmount` 减去 `iJtAmount`，`iJtAmount`、`dJtDate` 清空（同 U8）；该年度有多行参数时 409。U8 一个年度只在参数行上记最后一次计提的处理号，`iJtAmount` 是累计数，所以只能取消该年度最后一次计提，且会把同一年度的全部计提一起冲掉；前一次计提不能单独取消（409）。

制单（`arap/process/voucher`，`flag=AR`，`cancel_nos` 为 `HZAR`…，一次只能是同一种坏账处理，否则 409；开放条件同上）：凭证来源 `coutsign` 为 `JT`，`coutsysname` 为 `AR`。

- 分录：计提借参数里的对方科目 `cDyCode`、贷坏账准备科目 `cHzCode`；发生每行借 `cHzCode`、贷该行的应收科目（带客户辅助项）；收回借收款单表头的结算科目（要求现金流量项目时同 `arap/voucher` 收款单的取法）、贷 `cHzCode`。科目须是登录年度的末级科目，否则 409「坏账准备参数里的科目 X 不存在或不是末级」。收回的借方只取收款单表头的结算科目，没有则 409（收款单行的应收科目不能代替）。
- 回写：9G / 9H 往来明细的 `cPZid`、`cGLSign`、`iGLno_id`、`dPZDate`；9H 另回写收款单自己的审核行（`cProcStyle = cVouchType = 48`，`ino_id` 取借方银行分录）和表头 `Ap_CloseBill.cPzID`、`cPZNum`、`doutbilldate`，收款单已在别处制单则回滚 409；9F 回写参数行 `cPZID`。
- 取消制单用 `arap/voucher/delete`，清掉同样这些凭证号。外币坏账发生不能制单（处理制单只收本币明细，409），请在 U8 客户端制单。

读取：9G / 9H 是往来明细里的处理行，出现在 `arap/process/list`；9F 没有往来明细，只出现在 `arap/process/list` 的摘要项里（每个年度参数行一项，取最近一次计提），不在按 `Auto_ID` 增量的处理记录列表里。

功能权限（按 U8 窗体的权限号检查）：发生 `AR050602`、收回 `AR050603`、计提 `AR050601`（上级「坏账处理」`AR0506` 也放行）；取消同 `arap/process/cancel`（`AR0807`），制单同 `arap/process/voucher`（`AR0508`）。数据权限按客户、部门、业务员。

### 应收冲应付、并账、红票对冲 `arap/transfer`、`arap/merge`、`arap/red_offset`

U8 应收（应付）款管理「转账」里的三种处理。都写往来明细的处理行，发票累计核销经 U8 的回写组件，在桥的事务里执行，提交前核对余额，不符回滚 409 `u8_rejected`。处理日期就是登录日期 `date`（缺省今天），须在本系统（转账为应收、应付两边）未结账的期间内，且不早于单据日期和系统启用日期。三者都返回处理号 `cancel_no`：取消用 `arap/process/cancel`，制单用 `arap/process/voucher`（处理号表见 §16「`notes/process`」末尾）。`dry_run` 是 `rollback` 模式。

单据项 `{type, id, line_id?, amount}`：`type` 是 U8 单据类型代码，应收一侧 `26` / `27`（销售发票）、`R0`（应收单），应付一侧 `01` / `02`（采购发票）、`P0`（应付单）；收付款单（`48` / `49`）不收，请用核销。`id` 是单据号；`line_id` 是发票表体行，省略时按行主键从小到大依次分摊，应收单、应付单按整单、不能带；`amount` 是原币，大于 0、不超过 1000000000000、最多两位小数。同一单据行不能重复，同一单据不能既整单又按行。每侧 1 到 50 项。`digest` 最多 120 个字符。

| 路由 | 字段 | 处理方式 / 处理号 |
| --- | --- | --- |
| `arap/transfer` | `flag`（`AR` 应收冲应付，`AP` 应付冲应收，也是登录子系统）、`customer`、`vendor`（都必填）、`ar_lines`、`ap_lines`（都必填，两侧 `amount` 合计须相等）、`currency`（省略为本位币，各单据须同币种）、`digest`（省略用 U8 的缺省摘要） | 9I `YCFAP`… / 9J `FCYAR`… |
| `arap/merge` | `flag`（`AR` / `AP`）、`from`（并出）、`to`（并入，不能与 `from` 相同）、`lines`（必填，类型随 `flag`；`amount` 可省略，表示并入该单据或行在 `from` 名下的全部余额）、`digest`（省略为「并账」） | BZ `BZAR`… / `BZAP`… |
| `arap/red_offset` | `flag`（`AR` / `AP`）、`partner`（客户或供应商，必填）、`red`、`blue`（都必填，类型随 `flag`，两侧合计须相等，同一单据不能同时出现在两侧）、`currency`、`digest`（U8 组件不收时忽略） | 9N `HRAR`… / `HPAP`… |

请求示例：

```json
{"flag": "AR", "customer": "C900001", "vendor": "S900001",
 "ar_lines": [{"type": "26", "id": "0000000001", "amount": 100.00}],
 "ap_lines": [{"type": "P0", "id": "0000000002", "amount": 100.00}]}
```

并账每张单据（行）写一对 ± 处理行，单据本身不改；红票对冲调用 U8 的对冲组件 `U8ApCancel.cLsCancel.AP_JZ_Red`。

响应：

- `arap/transfer`：`acc`、`flag`、`style`、`cancel_no`、`date`、`customer`、`vendor`、`currency`、`amount`、`digest`；`ar_rows` / `ap_rows` 每项 `type`、`id`、`doc_id`、`line_id`（整单时省略）、`amount`（原币）、`amount_native`（本币）、`remaining`（处理后的余额）。
- `arap/merge`：`acc`、`flag`、`cancel_no`、`date`、`from`、`to`、`currency`、`digest`、`amount`（合计）；`rows` 每项同上，另有 `from_remaining`、`to_remaining`。
- `arap/red_offset`：`acc`、`flag`、`cancel_no`、`amount`；`red_rows` / `blue_rows` 每项同上。

错误：404 `not_found` 单据不存在；409 `workflow_enabled` 单据受审批流控制；409 `state_mismatch` 单据未审核、往来单位或币种不符、余额不足、日期早于单据日期或系统启用日期、期间已结账（转账另有外币两侧折合本币不等，并账另有单据不属于 `from`、一次超过 500 行，红票对冲另有红蓝方向不对）；U8 拒绝时 409 `u8_rejected`（带回 U8 原文）。

功能权限：应收冲应付 `AR050502`、应付冲应收 `AP050502`，红票对冲 `AR050503` / `AP050503`，并账 `AR050504` / `AP050504`；上级「转账」`AR0505` / `AP0505` 同样放行。数据权限按各单据的往来单位、部门、业务员（并账另按 `from`、`to`）。

### 汇兑损益 `arap/exchange_gain`、`arap/exchange_gain/cancel`

U8 应收（应付）款管理的汇兑损益（处理方式 9M）：按外币余额和调整汇率计算本币差额，每个（往来单位、单据）一个处理号 `SYRAR`… / `SYPAP`…，写往来明细，发票累计核销经 U8 的回写组件。属于**第二级写入**（§3），开放条件同 `arap/bad_debt`。登录子系统就是 `flag`；登记日期就是登录日期 `date`，须在本系统未结账的期间内。

`arap/exchange_gain` 请求：

| 字段 | 说明 |
| --- | --- |
| `flag` | 必填，`AR` 或 `AP` |
| `currency` | 必填，外币名称，如「美元」；本位币 409 |
| `rate` | 调整汇率，大于 0、不超过 1000000、最多 10 位小数；省略取该期外币设置里的调整汇率，没有则 409「本期没有调整汇率」 |
| `partners` | 1 到 200 个客户（供应商）编码，不能重复；省略为全部 |
| `settle_cleared` | 缺省 `true`：原币已结清只剩本币尾差的单据一并结清 |

```json
{"flag": "AR", "currency": "美元", "rate": 7.0, "partners": ["C900001"]}
```

响应：`acc`、`flag`、`date`、`fiscal_year`、`period`、`currency`、`rate`（使用的调整汇率）、`rows`（写入的明细行数）、`total`（本币差额合计）、`batches`（每个处理号的 `cancel_no`、`partner`、`type`、`id`、`lines`、`diff`，`diff` 是本币差额，借正贷负）。

`arap/exchange_gain/cancel` 请求：`flag`，可选 `cancel_nos`（1 到 200 个处理号，`SYRAR`… 用 `AR`、`SYPAP`… 用 `AP`，不能重复）；省略时取消登记日期为登录日期 `date` 的全部未制单汇兑损益。桥按 U8 界面执行的取消 SQL 写，在一个事务里加回余额、删掉明细行并核对。响应：`acc`、`flag`、`by_date`（按日期取消时的日期，按处理号取消时省略）、`rows`（删掉的行数）、`batches`、`total`。

错误：登记时 409 `state_mismatch` 为币种不存在或是本位币、日期不在会计期间或早于启用日期、期间已结账、批次过多；取消时 404 `not_found` 为处理号不存在或该日没有汇兑损益，409 `state_mismatch` 为已制单（先用 `arap/voucher/delete` 删凭证）、期间已结账、之后还有其他处理。核对不符回滚 409 `u8_rejected`。`dry_run` 是 `rollback` 模式。

制单用 `arap/process/voucher`，须给 `pl_code`（汇兑损益科目编码，U8 在制单界面选，桥不猜），只做本币批次。功能权限：汇兑损益 `AR0507` / `AP0507`，取消同「取消操作」`AR0807` / `AP0807`；数据权限按往来单位。

## 13. 审批流 `workflow/*`

只接入了四种质量单据：`qm_incoming_check`（QM03）、`qm_product_check`（QM04）、`qm_incoming_reject`（QM05）、`qm_product_reject`（QM06）。其他类型 400「该单据类型未接入审批流」。审批流本身（节点、审批人、条件分支）在 U8 里配置，桥只按 U8 的规则推进。

| 路由 | 请求 | 作用 |
| --- | --- | --- |
| `workflow/state` | `type`、`id` | 审批状态 |
| `workflow/history` | `type`、`id` | 审批历史 |
| `workflow/tasks` | 可选 `type` | 当前操作员的待办。带了 `type` 时必须是已接入的类型，否则登录前 400 |
| `workflow/submit` | `type`、`id` | 提交。不能带 `opinion` |
| `workflow/withdraw` | `type`、`id` | 撤销提交。不能带 `opinion` |
| `workflow/approve` | `type`、`id`，可选 `opinion` | 同意 |
| `workflow/disagree` | `type`、`id`、`opinion` 必填 | 不同意并继续（末节点时流程以「不通过」结束） |
| `workflow/return` | `type`、`id`、`opinion` 必填 | 退回提交人 |
| `workflow/abandon` | `type`、`id`，可选 `opinion` | 弃审：撤回本人上一次同意，终审后也可 |
| `workflow/resubmit` | `type`、`id` | 退回后重新提交 |

`opinion` 最长 500 字，不写审计。

`wf` 对象（`state` 的响应，也出现在 `vouchers/load` 和各审批动作的响应里）：

| 字段 | 说明 |
| --- | --- |
| `controlled` | 是否受审批流控制 |
| `status` | `not_submitted` 未提交、`in_approval` 审批中、`approved` 已通过、`not_approved` 不通过、`returned` 已退回、`not_controlled` 未启用审批流 |
| `verify_state`、`verify_state_new` | 表头 `IVERIFYSTATE`、`iVerifyStateNew`（0 未提交，1 审批中，2 已通过，-1 不通过） |
| `return_count` | 退回次数 |
| `current_auditor`、`verifier`、`verified_at` | 当前审核人姓名、终审人、终审日期 |
| `instance` | 流程实例 `{piid, running, started_by, started_at}`，未提交或已撤销时为空 |
| `pending` | 待办 `[{task_id, activity_id, person, operator, task_type}]`，`task_type` 1 审批、4 弃审后重审、5 退回后重提 |

`history` 响应：`{"ok":true,"type","id","code","history":[…]}`，每项 `action`（U8 动作编号）、`action_name`（`submit`、`agree`、`disagree`、`reject`、`withdraw`、`return`、`abandon`、`resubmit`，其他为 `other`）、`task`、`opinion`、`person`、`operator`、`name`、`at`。

`tasks` 响应：`{"ok":true,"operator","person","tasks":[…],"other_count"}`，每项 `task_id`、`type`、`biz`（U8 业务对象，如 `QM04`）、`id`、`code`、`task_type`、`activity_id`、`from`（上一处理人）、`created_at`（待办到达时间）、`title`（待办标题，即 U8 消息中心显示的那一句）、`piid`（同 `wf.instance.piid`）。`other_count` 是业务对象无法映射到已知类型的待办条数。操作员没有关联人员时返回空列表。节点名称只存在 U8 的流程定义里，这里不返回；同一节点用 `activity_id` 区分。

给即时通讯或待办系统建待办：订阅事件服务的 `workflow` 事件（`docs/events.md`）得知哪张单据的审批状态或当前审核人变了，再用 `workflow/state` 的 `pending`（每个待办人的 `person`、`operator`、`task_id`）决定给谁建、撤哪条；某个操作员自己的待办清单用 `workflow/tasks`。

审批动作响应：`{"ok":true,"type","id","code","action","u8_message","wf"}`，`wf` 是动作之后重新读到的状态。桥清理了本次审批留下的 U8 孤儿任务行时多一个 `orphan_tasks_cleaned`（行数，只在大于 0 时出现）。

审批相关的错误码（都是 409）：`workflow_disabled` 单据未启用审批流，`already_submitted` 已经提交，`not_submitted` 未提交或没有在途实例，`not_current_approver` 当前操作员不是待办人（操作员未关联人员时 `message` 为「操作员未关联人员」）。U8 的审批服务自行提交：返回成功（或调用异常）后在新连接上回读审批状态，既不是目标状态也不是原状态时 504 `outcome_unknown`（先用 `workflow/state` 核对，不要直接重试）；回读仍是原状态且调用异常时按 U8 拒绝 409。

审批引擎会尝试向 U8 移动端推送消息：缺省拦下；桥 `config.json` 设 `"mobilePush": true` 后照常推送（见 `docs/configuration.md`）。

## 14. 总账凭证 `gl/vouchers/*`

登录子系统 `GL`。会计年度取登录日期 `date` 的年份；请求的 `year` 是账套库年度，不作会计年度用。

凭证键：`period`（1 到 12）、`sign`（1 到 2 个字的凭证类别字，必须在 U8 的凭证类别里）、`no`（1 到 32767）。

功能权限查 U8 的操作员权限表（本人或所属角色，或 `admin`），没有时 403 `no_permission`「没有…权限」：

| 操作 | 功能 id |
| --- | --- |
| 新增、修改、作废、取消作废、红字冲销、期间损益结转、自定义转账 | `GL0201`（填制凭证） |
| 删除 | `GL0202`（凭证整理） |
| 出纳签字、取消签字 | `GL0203` |
| 审核、取消审核 | `GL0204` |
| 记账、取消记账 | `GL0208` |

第二级写入（取消记账、期间损益结转、自定义转账）复现 U8 界面执行的 SQL：桥的 `enableReplicatedWrites` 缺省关闭，关闭时 403 `feature_disabled`；打开后只对 `testAccounts` 里的账套开放（含预演），其他账套登录前 403 `test_account_only`。正式账套请在 U8 客户端操作。开关说明见 configuration.md，风险见 limitations.md。

### create / update

`head` 只有 `sign`、`date`（缺省登录日期，年份必须等于登录年度；修改时必须在原期间内）、`attachments`（附单据数，0 到 32767）。修改时 `head.sign` 必须等于 `sign`。

`lines` 2 到 200 行，每行：

| 字段 | 说明 |
| --- | --- |
| `account` | 科目编码，≤ 40，必须末级、未封存 |
| `digest` | 摘要，≤ 120 |
| `debit` / `credit` | 必须且只能填一个大于 0 的金额 |
| `dept`、`person`、`customer`、`supplier`、`item_class`、`item` | 辅助核算，须与科目设置一致 |
| `settle`、`doc_no`、`doc_date` | 结算方式、票号、票据日期 |
| `currency`、`rate` | 外币和汇率，要么都填要么都不填 |
| `qty` | 数量 |
| `cash_flow` | 现金流量，最多 50 项 `{item, debit 或 credit}`。现金流量科目必须带 |

借贷合计必须相等且大于 0。凭证号由 U8 编，新增成功 `{"ok":true,"period","sign","no"}`。

`update` 整张替换，只改总账自己的、未审核、未签字、未作废、未被红字冲销、未做银行对账或往来两清的凭证；红字冲销凭证和带自定义项的凭证不能修改。调用方不能填的列由桥从原凭证带过来，带这些值的分录在新报文里同一分录号必须仍是同一科目（409「修改不能改动分录的科目顺序」）。

新增和修改由 U8 的凭证导入组件自己提交，不在桥的事务里。调用抛错、应答无法解析、回读失败都是 504 `outcome_unknown`：先 `load` 或 `list` 核对，再决定是否重试。

### 其他操作

`void`（作废）、`unvoid`（取消作废）、`verify`（审核）、`unverify`（取消审核）、`sign`（出纳签字）、`unsign`（取消签字）、`delete`（删除）只带凭证键。成功带 `state`：`verified`、`checker`、`audit_date`、`signed`、`cashier`、`posted`、`void`、`error`；删除另带 `deleted: true`。

门槛（都是 409 `state_mismatch`）：

- 未记账、期间未结账、U8 界面没有锁着这张凭证（「凭证正被其他人编辑」）、各行状态一致。
- 修改、作废、取消作废、删除只收总账自己的凭证；其他系统生成的凭证 409「凭证由 X 系统生成，请在来源模块处理」。审核和出纳签字也处理其他系统生成的凭证。
- 作废要求未审核、未签字、未做银行对账或往来两清。删除要求已作废、未记账，不重排凭证号。
- 出纳签字只对有现金或银行科目行的凭证；取消签字只能取消本人的。
- 审核：登录日期不早于制单日期；总账选项「允许制单人审核」「允许取消他人审核」「出纳凭证必须先签字」「修改他人凭证」「制单序时」按 U8 的规则执行。

### post（记账）

请求：`period`（1 到 12）、`vouchers`（1 到 200 项 `{sign, no}`，不能重复）、可选 `fiscal_year`（缺省登录日期的年份）。例：

```json
{"acc": "801", "operator": "op001", "password": "…", "date": "2026-09-29", "period": 9,
 "vouchers": [{"sign": "转", "no": 3}, {"sign": "收", "no": 12}]}
```

成功 `{"ok":true,"period","fiscal_year","posted":[{"sign","no","state"}],"local_txn":true}`，顺序同请求；`state` 同上，另带 `poster`（记账人姓名），`posted` 为 `true`。`local_txn` 恒为 `true`：桥在提交前核对过事务没有升级为 MSDTC 分布式事务。

桥在写线程上直接调用 U8 总账的 .NET 记账组件（与 U8 界面「记账」相同的组件，见 docs/u8-notes.md），不经 COM。全部凭证在一个事务里一次记账，任何一张不合格都不记。门槛（409 `state_mismatch`，凭证不存在 404）：

- `period` 必须是该年度第一个未结账的总账期间：早于它「该期间总账已结账」，晚于它「上一会计期间没有结账，不能记账」；记 1 月时上年度必须已结账。
- 每张凭证：各行状态一致、未记账、不是错误凭证、已审核；总账选项「出纳凭证必须经出纳签字」开着时，有出纳科目的凭证要已出纳签字（出纳科目指现金、银行标志恰有一个的科目，两个都勾的不算）；「凭证须主管签字」开着时要有主管签字；U8 界面没有锁着这张凭证。
- 作废凭证同 U8 一样也记账：只打记账标志，不计入科目总账、辅助账；不查审核和签字，但带审核人的作废凭证 409。
- 年度首张凭证：总账选项「期初余额对账不平允许年度首张凭证记账」未开启时先做期初对账（同 U8 界面，会重写对账结果），总账与明细账、总账与辅助账、辅助账与明细账等不平 409「期初余额对账不平」；再做期初试算，不平 409「期初试算不平衡」。期初试算一般取第 1 期的年初余额；年中建账的建账年度取第 1 期的期末余额。
- 操作员姓名含单引号 409。

与 U8 客户端并发：事务第一条语句给 U8 的记账范围表加表级更新锁（持有到提交），U8 客户端的汇总要等桥提交。本年度已汇总未记账的范围同 U8 自己的汇总一样被覆盖；桥记完后在同一事务里清掉本年度的范围。因此若 U8 用户在桥加锁之前已在「记账」向导里汇总，之后再点「记账」不会记任何凭证（向导可能仍报成功），并且 U8「恢复记账前状态」再也撤销不了桥这次的记账；这时让对方重新汇总再记账。

记账过程（一个事务，ReadCommitted，超时 5 分钟）：加锁 → U8 按请求的凭证汇总记账范围 → 桥核对范围与请求完全一致（U8 会静默跳过不合格的凭证，对不上 409 并给原因） → U8 记科目总账、辅助账并回写记账标志 → 桥修正误写的凭证（见下） → 核对 → 清范围 → 核对事务仍是本地事务 → 提交。

U8 的已知缺陷：记账组件回写记账标志时按（期间、类别序号、凭证号）关联范围表，不带年度。多年度的账套库里，别的年度键相同的凭证会被改成本操作员记账（U8 客户端记账同样如此）。桥在同一事务里调用前快照这些行的记账标志和记账人，调用后改回，再核对本次凭证每行已记账、记账人是本操作员、其余行与快照一致，不符整体回滚。

错误（提交之前的错误都回滚、不留痕迹）：

| 情况 | 结果 |
| --- | --- |
| 死锁、锁请求超时、执行超时、事务被中止或超时 | 503 `u8_unavailable`「已回滚，未写入，可以稍后重试」 |
| 事务升级为分布式事务 | 503 `u8_unavailable`「记账事务升级为分布式事务，已回滚」 |
| U8 组件不在或签名不符 | 503 `u8_unavailable`「U8 总账记账组件不可用」 |
| U8 组件里的其他 SQL 错误、桥的 SQL 出错、记账后核对不符 | 500 `internal`（经 API 是 502） |
| U8 组件抛的其他异常 | 409 `u8_rejected`（原文第一行） |
| 提交时事务被中止 | 503 `u8_unavailable` |
| 提交结果不确定；提交后在新连接上回读失败或不是本操作员记账 | 504 `outcome_unknown`（消息写明已提交） |

收到 504 先 `load` 看 `posted`、`poster` 再决定是否重试。取消记账见 `unpost`（第二级写入）；正式账套上记错了用 `reverse` 或在 U8 客户端处理。

### reverse（红字冲销）

把一张已记账的凭证整张复制为红字凭证（金额取负），同 U8 凭证界面的「冲销凭证」。请求：凭证键 `period`、`sign`、`no` 定位原凭证；可选 `fiscal_year`（原凭证的会计年度，缺省登录日期的年份，不能晚于登录年度，可以是上一年度，即跨年冲销）、`voucher_date`（红字凭证日期，缺省登录日期，必须在登录年度内、不早于原凭证日期）。例：

```json
{"acc": "801", "operator": "op001", "password": "…", "date": "2026-02-10",
 "fiscal_year": 2026, "period": 1, "sign": "转", "no": 9}
```

成功 `{"ok":true,"period","sign","no","fiscal_year","voucher_date","lines","out_no","reversal_of":{"fiscal_year","period","sign","no","out_no","out_no_assigned"}}`：顶层是红字凭证（期间取 `voucher_date` 的月份，类别同原凭证，号由 U8 编），`reversal_of` 是原凭证。红字凭证未审核、未记账，之后照常审核、记账，也可以作废后删除，不能修改。

- 复制：科目，部门、人员、客户、供应商、项目辅助核算，结算方式、票号、票据日期、币种、汇率，原始单据类型和单号、业务员，表头备注、外部业务类型，附单据数。取负：本币借贷、原币、数量（单价仍为正）、现金流量金额（项目不变）。原币只在汇率大于 0 时写，取原凭证原币的负数，不按汇率重算。
- 摘要：`[冲销yyyy.MM.dd 类别-NNNN号凭证]` 加原摘要（日期是原凭证的制单日期，凭证号补到至少 4 位），最长 120 字，超出截断。例如 `[冲销2026.01.31 转-0009号凭证]`。
- 关联：红字凭证的 `cblueoutno_id` 写原凭证的外部业务号 `coutno_id`（`load` 的 `blue_out_no` 与 `out_no`）。原凭证没有外部业务号时（例如经凭证导入生成的）先按 U8 的编号规则补一个，`out_no_assigned: true`；原凭证所在期间已结账时照样补写（只是关联标识，不改金额）。
- 门槛（409 `state_mismatch`，原凭证不存在 404）：原凭证已记账、未作废、各行状态一致、是总账自己的凭证、至少 2 行；没有被冲销过（本接口只做整张、一次冲销）；本身不是红字冲销凭证；不带自定义项；每行数量与金额同方向；外币行汇率不为 0（有原币而汇率为 0 时请在 U8 客户端冲销）；科目和辅助核算在红字凭证的年度仍合格；红字凭证所在期间未结账；「制单序时」打开时日期不早于本期同类别最后一张凭证。U8 导入拒绝时 409 `u8_rejected`，库里没有改动。
- 执行：读原凭证（加锁）、检查、拼分录 → 凭证导入组件保存（U8 自己提交）→ 一个事务里取号、写关联、补现金流量的类别 → 新连接上逐行回读核对。保存之后任何一步失败都是 504 `outcome_unknown`（消息写明红字凭证可能已保存及其期间、类别）：先 `load` 或 `list` 核对，不要直接重发，因为没写上关联的红字凭证桥识别不出来。
- 预演是 `validate` 模式：`detail.voucher` 给出红字凭证的分录和预取的外部业务号（随回滚撤销，实际冲销会重新取号）。与同类别 `create` 互斥、与记账互斥（`gl:post`）。写入策略里归 (`gl`, `create`)。

### unpost（取消记账，第二级写入）

相当于 U8「恢复记账前状态 → 最近一次记账」：把本年度最近一次记账（桥记的和 U8 客户端记的都算）整批恢复为未记账，不能挑单张凭证。

请求全部可选：`fiscal_year`（缺省登录日期的年份）、`period`、`vouchers`（`[{sign, no}]`，不重复，最多 200 张，给了就必须给 `period`）。`period`、`vouchers` 只用来核对：给了就必须与最近一次记账的期间、凭证集合完全一致，否则 409 并写明不一致之处，什么都不改。成功 `{"ok":true,"fiscal_year","period","count","vouchers":[{"sign","no"}]}`。

- 门槛（409，消息带首张命中的凭证）：本年度有可恢复的记账（本年还没记过账或最近一次已恢复时 409）；范围不跨期间、不超过 500 张；该期间和后续期间总账都未结账；范围里的凭证都还在、都已记账；没有做银行对账、往来两清或往来对账；没有被红字冲销；U8 界面没有锁着。
- 执行（一个事务，锁同记账）：按 U8 记账的公式把这批凭证的发生额从科目总账（本期和后续期间的余额）、辅助总账、多辅助总账里冲回 → 凭证改回未记账 → 自检：科目总账发生额与已记账凭证按科目重算一致、本期起余额首尾相接、辅助总账一致，不符 409 并回滚 → 清掉本年度的恢复范围 → 提交 → 新连接上回读，不符 504 `outcome_unknown`。
- 记账时新增的总账、辅助账行（新科目、币种或辅助组合）只改回零、不删除，余额不受影响。依赖这批凭证的期间损益结转凭证不检查。
- 功能权限按记账 `GL0208` 查（U8 的恢复记账没有单独的功能 id）。预演是 `rollback` 模式，`detail.unpost` 给出期间、凭证和张数。写入策略里归 (`gl`, `other`)：放行 `post` 不连带放行取消记账。

### 期间损益结转、自定义转账（`gl/transfer/pnl`、`gl/transfer/custom`，第二级写入）

相当于 U8 总账「期末 → 转账生成」的期间损益结转和自定义转账。桥按 U8 的转账定义和已记账余额算出分录，经与 `create` 相同的凭证导入保存，再按 U8 生成的结转凭证补标记。

请求：`fiscal_year`（必填，必须是登录日期 `date` 的年份）、`period`（1 到 12）；可选 `voucher_date`（缺省该期间最后一天，必须在该期间内）、`exclude_existing`（只能和 `dry_run` 一起用，见下）；自定义转账另有可选 `tran_id`（U8 的转账序号，如 `T001`，缺省生成全部定义）。例：

```json
{"acc": "801", "operator": "op001", "password": "…", "date": "2026-09-30", "fiscal_year": 2026, "period": 9}
```

成功 `{"ok":true,"kind","fiscal_year","period","voucher_date","out_sign","count","vouchers":[{"sign","period","no","lines","out_no","digest","debit","credit","pack"|"tran_id"}],"skipped":[{"tran_id","account","reason"}]}`。

- 期间损益：每个转账序号两行定义（第 1 行损益科目、第 2 行本年利润科目）。损益类末级科目（带辅助核算的按辅助项分行）已记账期末余额非零的，反向结平，差额转入本年利润；贷方性质的科目（收入，`pack: income`）一张凭证、本年利润在最后一行贷方，借方性质的（费用，`pack: expense`）一张、本年利润在第一行借方；余额在反方向的写成同一边的负数；正负相抵为 0 时不写本年利润行。摘要「期间损益结转」，类别取定义上的类别。定义不完整、科目不存在或不是末级、本年利润科目带辅助核算的跳过（`skipped`）。
- 自定义转账：每个转账序号一张凭证，行的科目、方向取定义，金额按公式算，摘要取定义（没有时取转账说明）。公式支持 `QM(科目, 月|年, [借|贷], [辅助项])`、`CE()`、数字、`+ - * /` 和括号：`QM` 取该科目（含下级，可以不是本行科目）已记账的期末余额，不给方向时按科目余额方向取净额，给「借」「贷」只取该方向的余额；给辅助项时该科目必须是只核算一种辅助项的末级科目；`CE()` 是本凭证其余各行的借贷差额。`QM` 的期间参数只支持 `月`：用到 `年` 的定义在给了 `tran_id` 时 400 `bad_request`「转账定义 <序号> 用到按年取数（QM(…,年,…)），暂不支持，请在 U8 客户端生成」，不给时跳过并在 `skipped` 写明。其他函数（`FS`、`JE`、`QC`、`LFS` 等）400 `bad_request`，消息写明函数名。行的辅助项取定义上固定的辅助项，没有时取本行公式里唯一带辅助项的 `QM` 的辅助项（类型须与本行科目相同），给不全的跳过。金额为 0 的行不写；全为 0、按年取数、科目不合格、本期已生成的定义跳过。
- 门槛（409 `state_mismatch`）：
  - 该期间总账未结账；本期没有未作废的未记账凭证（结转只取已记账余额，同 U8）。
  - 期间损益本期已有未作废的结转凭证时 409「该月已经做过期间损益结转」。例外：已有凭证能按损益科目性质认出是收入还是费用，且已记账后重新算出的只有缺的那一类，则照常生成，响应和预演另给 `existing: [{"sign","no","pack"}]`。
  - 不给 `tran_id` 而后面定义 `QM` 取数的科目（含下级）与前面定义本次生成的分录科目重叠时 409（U8 按序号逐个结转、后一个在前一个记账之后取数；请用 `tran_id` 逐个生成、审核、记账；核对模式不查）。
  - 自定义转账同期已有外部业务类型「自定义转账」、摘要相同的未作废凭证时跳过该定义（给了 `tran_id` 时 409）；全部跳过时 409「没有生成凭证：…」并列出原因。
  - 每张凭证借贷平衡、不超过 500 行，科目和辅助核算按 `create` 的规则查；制单序时。
- 标记（同 U8 生成的结转凭证）：`coutsign` 为「期间损益」或「自定义转账」，`coutno_id` 按 U8 的编号规则取，附单据数 `idoc=-1`，`coutsysname` 为空，`ioutyear` 为空、`ioutperiod` 为期间、`doutbilldate` 为凭证日期。凭证未审核、未记账，之后照常审核、记账；要重做时作废、删除后再生成。
- 执行：逐张保存（U8 自己提交）→ 每张在一个事务里取号、补标记、补现金流量类别 → 全部保存后新连接上逐行回读。第一张被 U8 拒绝时 409 `u8_rejected`，库里没有改动；之后任何失败（含第二张被拒）都是 504 `outcome_unknown`，消息列出已保存的凭证和补救办法，先 `list` 核对。期间损益只存了一张时，把它审核、记账后再调用 `gl/transfer/pnl`，桥只补生成缺的那张；已保存的凭证没补上标记时作废、删除后整体重做。
- 预演是 `validate` 模式：停在凭证导入之前，`detail.transfer` 给出 `vouchers`（每张的类别、摘要、借贷合计、`lines`）和 `skipped`。`exclude_existing: true`（核对模式）：余额里去掉本期已生成的同类结转凭证及同类别、凭证号在它之后的结转凭证，不查结账、未记账、重复三道门槛，用来与 U8 已生成的凭证逐行核对，去掉的凭证在 `detail.transfer.excluded`。`exclude_existing` 只能预演：登录前按 `dry_run` 查，入队后按实际的预演状态再查，保存前再查一次。
- 数据权限：口径同总账查询。科目（总账选项「明细账查询权限控制到科目」打开时）、部门、人员、客户、供应商、项目的数据权限开着时，定义里取数和入账的每个科目、`QM` 的辅助项参数、每条取数余额和每行分录都要有查询权限，否则 403 `no_permission`（消息不带金额）。定义里的科目在取数之前查，其余在算出之后查，预演和核对模式相同。账套主管、该对象的数据权限管理员不受限。
- 锁：同年同期的两种结转互斥（`gl:transfer:<年>-<期>`），与记账和取消记账（`gl:post`）、总账结账（`period:gl`）、常见类别的新凭证编号（`new:gl:转` 等）互斥。写入策略里归 (`gl`, `voucher`)：放行 `create` 不连带放行自动转账。
- 不支持：对应结转、销售成本结转、汇兑损益结转、修改转账定义、自动审核记账。

### load / list

`load` 返回 `voucher`（`period`、`sign`、`no`、`date`、`attachments`、`maker`、`checker`、`audit_date`、`cashier`、`poster`、`posted`、`void`、`error`、`source_system`、`source_sign`、`source_no`、`out_no`、`blue_out_no`（红字冲销凭证指向的原凭证外部业务号））和 `lines`（`entry`、`account`、`account_name`、`digest`、`debit`、`credit`、`debit_fc`、`credit_fc`、`qty_debit`、`qty_credit`、`currency`、`rate`、`dept`、`person`、`customer`、`supplier`、`item_class`、`item`、`settle`、`doc_no`、`doc_date`、`cash_flow`）。

`list` 请求：`period_from`、`period_to` 必填，可选 `sign`、`date_from`、`date_to`、`maker`、`state`（`all`、`unaudited`、`audited`、`posted`、`void`）、`after`（上一页的 `next`，不透明字符串）、`limit`（1 到 200，缺省 50）。按（期间、类别、凭证号）翻页。每项 `period`、`sign`、`no`、`date`、`maker`、`checker`、`cashier`、`posted`、`void`、`debit_total`、`lines`（分录数）。

数据权限：总账选项「明细账查询权限控制到科目」打开、科目开了数据权限控制、操作员不是账套主管也不是科目的数据权限管理员时，`load`、`list`、`digest`、`attachments/list` 按整张凭证过滤：全部分录科目都有查询权限才可见（取较严的口径）。`list`、`digest` 里越权的凭证不出现（`debit_total`、`watermark` 只算可见凭证），`load`、`attachments/list` 403 `no_permission`「没有该单据的数据权限」，凭证不存在仍是 404。见 §22。

### digest（凭证摘要）

供事件服务使用。凭证表没有 rowversion，事件服务按期间逐张比对凭证指纹，发现新增、删除、审核、出纳签字、记账、作废和修改。走读线程池（只跑 SQL），权限同凭证查询（含按科目整张过滤：事件服务的操作员看不到的凭证不产生事件）。

请求全部可选：`fiscal_year`（缺省登录日期的年份）、`periods`（1 到 12 个不重复的期间）、`closed_periods`（0 到 12，缺省 1，不能与 `periods` 同时给）、`after`、`limit`（1 到 500，缺省 200）、`keys_only`。`periods` 省略时取该年度全部未结账期间（不含期初 0 期），再加最近 `closed_periods` 个已结账期间；年度没有期间记录时 `periods`、`items` 为空。缺省期间只在这一年度里取：上一年度未结账的期间和跨年度的最近已结账期间要另发请求，显式给 `fiscal_year` 和 `periods`。

响应 `{"ok":true,"fiscal_year","periods","items","next","watermark","ident"}`：`periods` 是实际扫描的期间（升序），翻页时原样放进下一页的 `periods`，避免翻页途中结账改变范围；`watermark` 是所扫期间的 `MAX(i_id)`，`ident` 是 `IDENT_CURRENT('GL_accvouch')`（删凭证不回退），都在读这一页之前取，十进制字符串。每项 `period`、`sign`、`no`、`fingerprint`；不带 `keys_only` 时另有 `date`、`maker`、`checker`、`cashier`、`bookkeeper`（记账人）、`posted`、`void`、`debit_total`、`lines`。`fingerprint` 是 `SUM(md)`、`SUM(mc)`（4 位小数）、行数、`MAX(i_id)`、制单人、审核人、出纳、记账人、`ibook`、`iflag` 以 `|` 连接后的 SHA-256（小写十六进制）。按（期间、类别序号、凭证号）翻页，`after` 同 `list`。

### attachments/list

请求同 `load`（`period`、`sign`、`no`，年度取登录日期的年份），权限同凭证读取。凭证不存在 404 `not_found`。

凭证的 `attachments` 只是附单据数；电子附件（U8 填制凭证界面的「附件」）登记在 `GL_AccAttachs`，文件在 U8 文件服务器上。响应 `{"ok":true,"voucher","items","truncated"}`：`voucher` 是 `period`、`sign`、`no`、`attachments`；每项 `id`、`name`（原文件名）、`file_id`（文件服务器上的标识）、`submitted_at`（`yyyy-MM-dd HH:mm:ss`）、`source`（来源模块）。按上传顺序，最多 500 个。只列清单，不提供下载；没有附件时为空数组。

客户端命令：`gl-attachments --period 9 --sign 转 --no 3`。

## 15. 基础档案 `archives/*`

登录子系统 `AS`。`archive` 为 `customer`、`vendor`、`inventory`、`department`、`person`、`warehouse`、`customer_class`、`vendor_class`、`inventory_class`（下称「九类可写档案」），以及下文各节的档案，其他 400「未知档案类型 x」。`code` 1 到 30 个字符（其他档案按下表的长度），不含控制字符，前后没有空格（中间可以有空格）。`code_prefix` 最长与该档案的编码相同，`name_like` 1 到 60 个字符，都不含控制字符。

| 路由 | 请求 | 响应 |
| --- | --- | --- |
| `archives/get` | `archive`、`code` | `{"ok":true,"archive","code","fields"}` |
| `archives/list` | `archive`，可选 `code_prefix`、`name_like`、`changed_since`、`after`、`limit`、`project_class`（只给 `project`）、`currency` / `fiscal_year`（只给 `exchange_rate`）、`type_code` / `dept_code` / `include_disposed`（只给 `fa_card`）、`keys_only` | `{"ok":true,"archive","items":[{"code","name","class_code","ufts"}],"next","watermark"}` |
| `archives/create` | `archive`、`code`、`fields`，可选 `template` | `{"ok":true,"archive","code"}` |
| `archives/update` | `archive`、`code`、`fields` | 同上 |
| `archives/delete` | `archive`、`code` | 另带 `deleted: true` |

九类可写档案的 `fields` 键是 U8 EAI 的标签名（不分大小写，发送时换成 U8 对照表的写法），值是字符串、数字或布尔，`null` 表示不发送。未知标签 400「未知字段 x」；`code`、建档/变更人和日期、统计类标签（例如客户的应收余额、最后交易日）400「不能设置字段 x」。

- `get` 返回对照表里有对应列的全部标签的当前值，包括银行账号、联系方式。按需给调用方授只读权限。
- `list` 按编码翻页，`after` 是上一页最后的编码，`limit` 1 到 500（缺省 100），`changed_since` 见 §16「翻页和增量」。
- `keys_only: true`（只收布尔，任何档案都可用）时每项只有 `code`、`ufts`，整轮的编码集合用来发现被删的档案。
- `customer`、`vendor`、`warehouse`、`department`、`person`、`inventory` 的整行另有 `end_date`（停用日期 `yyyy-mm-dd`）和 `disabled`（已填停用日期即为 true，不与当天比较）；未填时桥给 `null`，API 省略。
- 选择器标志（整行才有，`keys_only` 不给；列值为空按 `false`）：`warehouse` 的 `bin_managed`（启用货位管理）；`position` 的 `leaf`（末级货位，`class_code` 是所属仓库）；`inventory` 的 `batch_managed`（批次管理）、`shelf_life_managed`（保质期管理）。这些键不影响事件服务的档案指纹（只看名称、分类、停用状态和停用日期）。
- 新增必须有 `name`；存货新增必须带 `template`（已有存货编码，桥按它铺满其余标签），其他档案可选。编码已存在 409「档案编码已存在」。客户、供应商要给税号类必填项，缺了由 U8 报错。
- 修改至少一个字段，不能改编码。桥发整条记录（当前值打底，再用 `fields` 覆盖），因为 U8 修改时按档案设置检查全部必输项。
- 删除：已被单据使用的档案由 U8 拒绝，409 `u8_rejected`。

经 EAI 的写操作由 U8 自己提交。U8 拒绝时 409 `u8_rejected`，`message` 是 U8 原文；原文含「不可为空」时后面加「（U8 档案设置为必输）」，调用方要在 `fields` 里给出该字段。U8 说成功但回读对不上，或应答无法识别，504 `outcome_unknown`。EAI 对照文件读不到或格式不对 503 `u8_unavailable`。

其他档案的写入能力：

| 档案 | 新增 | 修改 | 删除 | 见 |
| --- | --- | --- | --- | --- |
| `bank`、`project` | 是 | 是 | 是 | 开户银行与项目的写入 |
| `position`、`unit`、`unit_group`、`settle_style`、`rd_style`、`purchase_type`、`sale_type`、`district_class`、`aa_bank` | 是 | 是 | 是 | 货位、计量单位等 EAI 档案的写入 |
| `user_define`、`customer_inventory` | 是 | 否 | 是 | 同上 |
| `currency`、`voucher_sign` | 是 | 是 | 是 | 币种、凭证类别的写入 |
| `exchange_rate` | 是 | 是 | 是 | 汇率的写入 |
| `reason` | 是 | 是 | 是 | 原因码的写入 |
| `customer_bank`、`vendor_bank`、`customer_contact`、`vendor_contact` | 是 | 是 | 是 | 客户、供应商的银行账户和联系人 |
| `fa_card` | 是 | 否 | 撤销本期新增 | 固定资产卡片与设备台账的写入 |
| `equipment` | 是 | 否 | 否 | 同上 |
| `account`、`trade_class`、`customer_address`、`operator`、`role` | 否 | 否 | 否 | 只读，写路由 400「该档案只读」（API 在转发前拒绝） |

### 只读档案

以下档案的读取不用 EAI 对照表：`get` 的 `fields` 键是 U8 表的列名，返回全部非空列，去掉口令类列（列名含 `password` 或 `pwd`）和原始 rowversion；有 rowversion 的表另带十进制 `ufts`。`exchange_rate`、`fa_card`、`operator`、`role` 例外，见下文。

| `archive` | 表 | `code` | `list` 的 `class_code` | `changed_since` |
| --- | --- | --- | --- | --- |
| `account` 科目 | `code` | 科目编码 `ccode`，1 到 40 个字符 | 无 | 支持 |
| `unit` 计量单位 | `ComputationUnit` | `cComunitCode`，1 到 35 个字符 | 计量单位组 `cGroupCode` | 支持 |
| `unit_group` 计量单位组 | `ComputationGroup` | `cGroupCode`，1 到 35 个字符 | 无 | 支持 |
| `settle_style` 结算方式 | `SettleStyle` | `cSSCode`，1 到 3 个字符 | 无 | 支持 |
| `voucher_sign` 凭证类别 | `dsign` | 凭证类别字 `csign` | 无 | 不支持（400） |
| `currency` 币种 | `foreigncurrency` | 币种名称 `cexch_name`，1 到 8 个字符（单据、凭证引用的是名称；编码 `cexch_code` 在 `fields` 里） | 无 | 支持 |
| `bank` 本单位开户银行 | `Bank` | `cBCode`，1 到 3 个字符 | 无 | 支持 |
| `project` 项目 | `fitemss<大类>` | `<项目大类>:<项目编码>`，例如 `98:01` | 项目分类 `citemccode` | 不支持（400） |
| `position` 货位 | `Position` | `cPosCode`，1 到 20 个字符 | 仓库 `cWhCode` | 支持 |
| `rd_style` 收发类别 | `Rd_Style` | `cRdCode`，1 到 5 个字符 | 无 | 支持 |
| `purchase_type` 采购类型 | `PurchaseType` | `cPTCode`，1 到 2 个字符 | 无 | 支持 |
| `sale_type` 销售类型 | `SaleType` | `cSTCode`，1 到 2 个字符 | 无 | 支持 |
| `district_class` 地区分类 | `DistrictClass` | `cDCCode`，1 到 12 个字符 | 无 | 支持 |
| `trade_class` 行业分类 | `TradeClass` | `cTradeCCode`，1 到 12 个字符 | 无 | 支持（列名 `ufts`） |
| `aa_bank` 银行档案（所属银行） | `AA_Bank` | `cBankCode`，1 到 5 个字符 | 无 | 支持 |
| `customer_address` 客户收货地址 | `CusDeliverAdd` | `<客户编码>:<地址编码>`（`cCusCode` 20、`cAddCode` 30），例如 `C900001:01` | 客户 `cCusCode` | 不支持（400） |
| `user_define` 自定义项档案 | `UserDefine` | `<自定义项号>:<档案值>`（`cID` 10、`cValue` 400），例如 `1002:快递` | 自定义项号 `cID` | 支持 |
| `customer_inventory` 客户存货对照 | `CusInvContrapose` | `<客户编码>:<存货编码>`（`cCusCode` 20、`cInvCode` 60） | 客户 `cCusCode` | 支持 |
| `exchange_rate` 汇率 | `exch` | `<币种>:<年度>:<期间>[:<日>]`，例如 `美元:2026:9`；`get` 另收 `<币种>:<yyyy-mm-dd>` | 无 | 支持（取并入各行的最大 `pubufts`） |
| `fa_card` 固定资产卡片 | `fa_Cards` 等 | 卡片编号 `sCardNum`，1 到 20 个字符 | 无（项里有 `type_code`） | 不支持（400） |
| `equipment` 设备台账 | `EQ_EQData` | 设备编码 `cEQCode`，1 到 30 个字符 | 无 | 支持（列名 `ufts`） |
| `operator` U8 操作员 | `UFSYSTEM..UA_User` 等 | 操作员编码 `cUser_Id`，1 到 20 个字符 | 无 | 不支持（400） |
| `role` 角色 | `UFSYSTEM..UA_Group` 等 | 角色编码 `cGroup_Id`，1 到 20 个字符 | 无 | 不支持（400） |

- 没有 rowversion 的档案（`voucher_sign`、`project`、`customer_address`、`fa_card`、`operator`、`role`）列表的 `ufts`、`watermark` 为 `null`，带 `changed_since` 400；需要增量时整轮重读。
- `account`：科目按年度存，读登录日期那一年的科目，响应带 `fiscal_year`。
- `bank`：返回银行账号等付款要用的列，按需授只读权限。
- `project`：大类取自 `fitem`，只认 `ctable` 恰好是 `fitemss<大类>` 且表存在的大类；借用其他档案的大类（如存货核算）不在其中，用对应的档案读。大类 1 到 2 位字母数字，项目编码 1 到 60 个字符（`code`、`code_prefix` 整串最长 63）。`get` 另带 `project_class`；大类不存在 404「项目大类不存在」。`list` 可选 `project_class` 只列一个大类，省略时列全部大类（超过 100 个大类时 400，请按大类分别列），按整串编码排序翻页，`code_prefix` 也按整串匹配（例如 `98:`）。每项另带 `project_class` 和 `closed`（项目已关闭）。
- 两段编码的档案（`customer_address`、`user_define`、`customer_inventory`）：`code` 写成 `<第一段>:<第二段>`，按第一个冒号拆开（第一段不含冒号，第二段可以含），两段长度按上表、前后没有空格，否则 400。`list` 按（第一段，第二段）排序，`next` / `after` 是整串编码；`code_prefix` 按整串匹配，例如 `C900001:` 只列这个客户的收货地址；`class_code` 是第一段。已知限制：某一段本身带前导或尾随空格的记录仍出现在 `list` 里（编码原样带空格），但 `get` 取不到（400）；若它是一页的最后一行，下一页请求会 400，这时用 `code_prefix` 或 `name_like` 缩小范围跳过它。
- `user_define`：`name` 就是档案值 `cValue`，别名、上级值等在 `fields` 里。
- `customer_inventory`：U8 的唯一索引是（客户、存货、客户存货编码），同一客户同一存货可以有几条客户存货编码不同的对照，而 API 的编码只有两段：有重复时 `get` 只返回其中一条，`list` 都列出但编码相同，恰好落在页边界时下一页会跳过同编码的另一条。见「货位、计量单位等 EAI 档案的写入」的已知限制。
- `position`：`class_code` 是所属仓库。货位本身的数据权限不展开，也不按仓库的数据权限过滤。
- `exchange_rate`：U8 的 `exch` 一行一个汇率，`itype` 2 是记账汇率（固定汇率，按月），3 是调整汇率（按月，期末调汇用），1 是浮动汇率（按日，`cdate` 是日期，存成 `yyyy-mm-dd`）。桥把同一（币种、年度、期间、日）的行并成一项：`currency`、`year`、`period`（月份）、`day`（浮动汇率的 `cdate` 原文，固定汇率为 `null`）、`rate`（记账汇率或当日浮动汇率）、`adjust_rate`（调整汇率，未录入为 `null`）、`mode`（`fixed` / `floating`），`name` 是币种名称；`get` 的 `fields` 也是这几项加 `code`。`list` 可选 `currency`（币种名称，1 到 8 个字符）和 `fiscal_year`（四位年度，缺省登录日期的年份），按（币种、期间、日）排序，`after` 必须是本年度列表的 `next`；`name_like` 按币种名称匹配，`code_prefix` 按整串编码。响应另带 `fiscal_year` 和 `rate_mode`（账套的汇率方式：`fixed` 或 `floating`）。
- `exchange_rate` 按日期读取（`<币种>:<yyyy-mm-dd>`）返回 U8 给该日期单据带出的汇率：固定汇率取日期所在年月的记账汇率（期间按自然月换算；会计期间不按自然月划分的账套，月初月末可能落到相邻期间，请以 U8 单据带出的汇率为准），浮动汇率取当日汇率（当日没有 404「该日期没有汇率」，不向前找）；响应另带 `rate_mode`，`fields.code` 是命中项的编码。单据上的汇率可能从来源单据带出或被手工修改，以单据为准。外币付款先用它取汇率，再把 `rate` 作为单据的汇率。
- `fa_card`：`get` 的 `fields` 与 `list` 的每一项相同，不是表列名：`code`（卡片编号）、`name`（资产名称）、`asset_num`（资产编号）、`spec`（规格型号）、`type_code` / `type_name`（资产类别）、`status_code` / `status`（使用状况）、`origin_code` / `origin`（增加方式）、`depreciation_method_code` / `depreciation_method`（折旧方法）、`start_date`（开始使用日期）、`entry_date`（录入日期）、`useful_life_months`（使用年限，月）、`used_months`（已使用月份）、`original_value`（原值）、`accumulated_depreciation`（累计折旧）、`impairment`（减值准备）、`net_value`（净值 = 原值 − 累计折旧 − 减值准备）、`net_salvage`（净残值）、`disposed` / `disposed_date` / `disposal_code` / `disposal_way`（已减少、减少日期、减少方式）、`keeper`（保管人）、`location`（存放地点）、`depts`（使用部门 `[{"code","name","ratio"}]`，单部门卡片 `ratio` 是 1）。金额保留两位小数。
- `fa_card` 的口径：U8 每次变动、减少都新增一个卡片版本。桥取截至日（登录日期所在月的月末）当时的版本，即录入、变动、减少日期都不晚于截至日的最后一个版本，同 U8 卡片查询；截至日还没录入的卡片 404。累计折旧取登录年度「已计提期间」期末的累计值（已计提期间是该年度不晚于登录月份的最大计提期间）；本年还没计提时取年初数（本年录入的卡片取录入时的累计折旧）；已减少的卡片取减少时的累计折旧；登录年度固定资产还没建账时为 `null`。响应另带 `fiscal_year`、`as_of`（截至日）和 `depr_period`（所取的已计提期间，本年还没计提为 `null`）。会计期间不按自然月划分的账套、多部门卡片 `ratio` 的口径未覆盖。
- `fa_card` 的 `list` 按卡片编号排序，缺省只列在役卡片，`include_disposed: true` 连已减少的一起列。`type_code`（1 到 20 个字符）按类别编码前缀匹配，含下级类别；`dept_code`（1 到 12 个字符）是使用部门之一；`name_like` 按资产名称匹配。`get` 对已减少的卡片照常返回（`disposed` 为 true）。
- `operator`、`role`：数据在 U8 系统库，桥用三段名 `UFSYSTEM..UA_User`、`UA_Group`、`UA_Role`、`UA_HoldAuth` 读取，比较编码时按账套库的排序规则。只列本账套有授权的：操作员本人或所属角色在本账套、请求年度或建账年度（年度窗口同 §22）有任一授权；角色本身有这样的授权。系统管理员（`admin`）不列。不在范围内的编码 `get` 404。`get` 的 `fields` 与 `list` 的每一项相同。操作员：`code`、`name`、`dept`（操作员档案上的所属部门，文本原样）、`state`（`nState` 原值，0 正常、1 停用）、`disabled`（`nState` 不是 0）、`roles`（所属角色编码，只含本账套有授权的角色）、`person_code` / `person_name`（关联的人员，与登录对象的 `cEmployeeId`、审批任务的人员是同一编码；没有关联为 `null`）。角色：`code`、`name`、`members`（成员操作员编码，系统管理员除外）。口令、口令日期、邮箱、手机、系统用户名等列不读。`name_like` 按姓名或角色名称匹配。

读取权限与数据权限（§22）：

| 档案 | 功能权限（读取） | 记录级数据权限 |
| --- | --- | --- |
| `position` | `AS030Q`（货位档案查询）或 `AS030` | 无 |
| `bank` | `AS013Q` 或 `AS013` | 无 |
| `rd_style` | `AS016Q` 或 `AS016` | 收发类别 |
| `trade_class` | `AS050Q` 或 `AS050` | 无 |
| `customer_inventory` | `AS1204Q` | 客户和存货（`get` 分别判断两段） |
| `purchase_type`、`sale_type` | — | 采购类型、销售类型 |
| `customer_address` | — | 客户 |
| `currency`、`exchange_rate` | `AS028M` | 无 |
| `fa_card` | `FA1501`（卡片管理「打开」）或 `FA1505`（「修改」） | 部门开了数据权限控制时按使用部门：全部版本的全部使用部门都在授权内才给（同 §19 固定资产报表），`list` 去掉越权卡片，`get`、`get_many` 403；不按类别过滤 |
| `operator`、`role`、`aa_bank` | 只有请求年度的账套主管（按账套主管单独判定，不看 `admin` 授权行），否则 403 `no_permission` | 无 |

地区分类、行业、银行档案、自定义项不受记录级控制。客户、供应商银行账户和开户银行（`bank`）的写入要填所属银行编码（`bank_code`，取自银行档案），非账套主管读不到银行档案，需向账套主管要编码。

### 开户银行与项目的写入

| `archive` | 写入方式 |
| --- | --- |
| `bank` 本单位开户银行 | EAI（`BankXmlRs.xml`，根标签 `bank`），同九类可写档案 |
| `project` 项目 | 桥的受控 SQL（U8 没有单个项目的写入组件，EAI 的项目导入只收大类） |

- `bank`：`fields` 用 EAI 标签（`name`、`account`、`flag`、`cbankcode`、`ccurrencyname`、`caccname`、`copenaccaddr` 等）。新增必须有 `name`、`account`（银行账号）、`cbankcode`（所属银行，须在银行档案里）、`ccurrencyname`（币种名称，须存在），`flag`（暂封）缺省 0。所属银行设了企业账号定长时账号长度必须等于设定值，否则 400「银行账号要求定长（N位）」。这些在调用 U8 前按合并后的整条记录（模板、当前值、`fields`、缺省值）检查，缺项或不存在 400；修改时币种只在改了 `ccurrencyname`、所属银行和账号定长只在改了 `account` 或 `cbankcode` 时检查。可选 `template`（已有开户银行编码），不复制账号、账户名称、客户编号、开户日期、签约标志。修改发整条记录（`proc=diffedit`）。已被单据使用的删除由 U8 拒绝，409 `u8_rejected`。功能权限：写入 `AS013`，没有 403 `no_permission`。
- `project` 新增、修改：`code` 写成 `<项目大类>:<项目编码>`，编码不能改。`fields` 只收 `name`（1 到 255 个字符，不能全是空白）、`bclose`（是否结算，布尔或 0 / 1，新增缺省 0）、`citemccode`（所属分类，必须是该大类的末级分类），其他字段 400；不收 `template`。新增必须有 `name`、`citemccode`。桥在一个事务里锁住大类、所属分类和编码，再插入或更新，提交后在新连接上回读核对；回读失败或不一致 504 `outcome_unknown`（「已提交」，先 `get` 核对，不要直接重发）。大类不存在或不可用（含存货核算）404；编码已存在（按库的排序规则，不分大小写）409「档案编码已存在」。该大类的项目结构只能有标准栏目（`I_id`、`citemcode`、`citemname`、`bclose`、`citemccode`、`iotherused`）且没有子表，项目表也不能有其他非空无缺省值的列，否则 409「该项目大类有额外栏目或子表，请在 U8 客户端维护（栏目名）」，例如现金流量项目、项目管理。编码规则只约束项目分类，桥不检查项目编码。功能权限「项目目录」`AS029` 或「项目编辑」`AS029M`，没有 403；不检查项目的记录级数据权限。
- `project` 删除：只删一个项目，不删大类和分类。桥在一个事务里锁住大类（同样拒绝有额外栏目或子表的大类），排他锁确认该编码恰好一行（没有 404），再查引用：账套库里同时有项目编码列（`citemcode`、`citem_id`、`citemid`）和项目大类列（`citem_class`、`citemclass`）的全部表（从数据库目录现取，覆盖总账凭证、辅助账、现金流量、应收应付、购销存、存货核算、质量、出口、服务、预算、项目对照及其历史、备份表；U8 临时表 `UFTmpTable*`、`TMPUF_*` 除外），以及项目子表。任一表有引用 409 `state_mismatch`「项目已被使用（表名），不能删除」；这样的表多于 2000 张时不删，500。删除后在同一事务里复查，提交后新连接上回读，还读得到或回读失败 504 `outcome_unknown`。成功返回 `deleted: true`。功能权限同新增。剩余窗口：排他锁只挡住读写项目这一行的连接，查完引用到提交之间其他连接新写入的引用看不到；用其他列名引用项目的表（例如对方项目列）不查。

### 货位、计量单位等 EAI 档案的写入

以下档案经 EAI 写入（`U8SrvTrans.IClsCommon.Transact`，同九类可写档案），`get`、`list` 仍按表列名返回。`fields` 用 EAI 标签（见 `meta` 的 `archives[].tags` / `writable`）。修改发整条记录（`proc=diffedit`）。下面的校验都在调用 U8 之前做：请求不对 400，库里的状态不允许 409 `state_mismatch`；U8 自己拒绝 409 `u8_rejected` 带原文。

| `archive` | 根标签（RsXml） | 可写标签 | 新增必须 | 编码 |
| --- | --- | --- | --- | --- |
| `position` 货位 | `position`（`PositionXmlRs.xml`） | `name`、`warehouse_code`、`maxcubage`、`maxweight`、`remark`、`barcode`；`grade`、`end_flag` 不能写 | `name`、`warehouse_code` | `cPosCode`，按编码方案分级 |
| `unit` 计量单位 | `unit`（`UnitXmlRs.xml`） | `name`、`group_code`、`main_flag`、`changerate`、`portion`、`SerialNum`、`barcode`、`censingular`、`cenplural`、`cunitrefinvcode` | `name`、`group_code` | `cComunitCode` |
| `user_define` 自定义项档案 | `define`（`DefineXmlRs.xml`） | `alias`、`barcode` | 无（名称就是档案值） | `<自定义项号>:<档案值>`，发送时拆成 `id`、`value` |
| `customer_inventory` 客户存货对照 | `cusinvcontrapose`（`CusInvContraposeXmlRs.xml`） | `ccusinvcode`、`ccusinvname`、检验相关标签 | `ccusinvname` | `<客户编码>:<存货编码>`，发送时拆成 `ccuscode`、`cinvcode` |
| `unit_group` 计量单位组 | `unitgroup`（`UnitGroupXmlRs.xml`） | `name`、`type`、`cgrprelinvcode` | `name`、`type`（0 无换算、1 固定换算、2 浮动换算） | `cGroupCode`，最长 35 |
| `settle_style` 结算方式 | `balancetype`（`BalanceTypeXmlRs.xml`） | `name`、`flag`（票据管理，缺省 0）、`issbilltype`（缺省 0）；`code_rank`、`end_rank_flag` 不能写 | `name` | `cSSCode`，最长 3，按编码方案分级 |
| `rd_style` 收发类别 | `receivesendtype`（`ReceiveSendTypeXmlRs.xml`） | `name`、`rsflag`（1 收、0 发）、`oppsubject_code`；`sort`、`end_flag` 不能写 | `name`；一级还要 `rsflag` | `cRdCode`，最长 5，按编码方案分级 |
| `purchase_type` 采购类型 | `purchasetype`（`PurchaseTypeXmlRs.xml`） | `name`、`rstype_code`（入库类别）、`bdefau`、`bpfdefault`（缺省 0） | `name` | `cPTCode`，最长 2 |
| `sale_type` 销售类型 | `saletype`（`SaleTypeXmlRs.xml`） | `name`、`rstype_code`（出库类别）、`bdefau`（缺省 0） | `name` | `cSTCode`，最长 2 |
| `district_class` 地区分类 | `districtclass`（`DistrictClassXmlRs.xml`） | `name`；`sort`、`endflag` 不能写 | `name` | `cDCCode`，最长 12，按编码方案分级 |
| `aa_bank` 银行档案（所属银行） | `aa_bank`（`AA_BankXmlRs.xml`） | `name`、账号定长类标签（`bindfixlen`、`iindaccnolen`、`bcomdfixlen`、`icomaccnolen` 等）；`i_id` 不能写 | `name` | `cBankCode`，最长 5 |

- 分级档案（`position`、`settle_style`、`rd_style`、`district_class`）：编码按 U8 编码方案（例如 `2222`、`122`）分级，长度不落在某一级末尾 400；第 2 级起上级必须存在（400），上级是末级且已被使用时 409。桥按编码算级次、末级标志，新增下级后上级的末级标志由 U8 维护。有下级时不能删除（409）。
- `position`：`warehouse_code` 必须是启用了货位管理的仓库（400）；上级货位必须在同一仓库（400）；上级已有存货记录（货位存量、货位出入库记录、存货的默认货位、存货货位对照）时不能建下级（409）。修改不能换仓库（400）。有存货记录时不能删除（409）。可选 `template`（已有货位编码），不复制条码和备注。
- `unit`：`group_code` 必须存在。无换算组的单位都是主计量单位，不能给 `changerate`、`main_flag=0`（400），桥补 `main_flag=1`。固定 / 浮动换算组：组里还没有主计量单位时新单位就是主计量单位（`main_flag=1`、`changerate=1`，给了别的值 400）；已有主计量单位时新单位只能是辅计量单位，`changerate` 必填且大于 0，`main_flag=1` 400。`SerialNum` 缺省取组里最大序号 + 1。修改不能改 `group_code`、`main_flag`（400）；只有换算组的辅计量单位能改 `changerate`，已被存货使用时 409。删除：被存货的任一计量单位列（主计量、辅计量、采购、销售、库存、成本、生产、零售）引用时 409；换算组的主计量单位在组里还有其他单位时 409。可选 `template`，不复制主计量单位标志、序号、条码、对应存货。
- `user_define`、`customer_inventory` 不能修改（U8 EAI 不提供）：API 的 `archives/update` 不收这两类（400 `bad_request`，不转给桥）；直接调桥 400「U8 不提供修改，请删除后重新新增」。`meta` 的 `updatable` 为 false。
- `user_define`：自定义项号必须在自定义项设置里、设为「需要建档」、不取自其他档案，档案值不超过使用长度（按字符计；定长时必须等长），否则 400。`fields` 里给 `id`、`value` 400。不收 `template`。
- `customer_inventory`：客户、存货必须存在（400）；同一（客户、存货）已有任一条对照 409「档案编码已存在」。不收 `template`。已知限制：U8 客户端允许同一客户同一存货有几条客户存货编码不同的对照，API 每一对只能管一条；已有多条的对，删除哪一条由 EAI 决定，这类数据请在 U8 客户端维护。
- `unit_group`：`type` 只能是 0、1、2（400）；组里已有计量单位时不能改 `type`（409）。U8 只允许一个无换算组：新增或改成 `type=0` 而已有别的无换算组时 409 `state_mismatch`「计量单位分组最多只能有一个无换算单位组！」。
- `rd_style`：`rsflag` 下级没给时取上级的，给了必须与上级相同（400），一级必须给（400）。
- `purchase_type`、`sale_type`：`rstype_code` 新增时给了或修改时改了，必须是存在的末级收发类别（400）。档案设置把入库 / 出库类别设为必输时，不给 `rstype_code` 由 U8 拒绝（如「入库类别不可为空！」），409 `u8_rejected` 加「（U8 档案设置为必输）」。`bdefau` 置 1 时 U8 把其他类型的默认标志清成 0；之后删掉这个类型，原默认不会恢复，需要把原类型改回 `bdefau=1`。
- 删除时桥查的引用（409 `state_mismatch`「档案 X 已被…使用，不能删除」）：计量单位组——计量单位、存货档案；结算方式——收付款单、销售订单、发货单、销售发票、往来明细；收发类别——采购入库、其他入库、其他出库、产成品入库、材料出库、销售出库单，发货单、销售发票、领料申请单，采购类型、销售类型；采购类型——采购订单、到货单、采购发票、采购入库单、请购单、委外订单；销售类型——销售订单、发货单、销售发票、销售出库单、报价单；地区分类——客户、供应商档案；银行档案——本单位开户银行、客户和供应商档案、客户和供应商银行账户、人员档案。U8 自己还会拒绝更多引用（409 `u8_rejected`）。
- 功能权限（桥在写之前查，没有 403 `no_permission`）：

| 档案 | 新增 | 修改 | 删除 |
| --- | --- | --- | --- |
| `position` | `AS030` | `AS030` | `AS030` |
| `unit`、`unit_group` | `AS032M` | `AS032M` | `AS032M` |
| `settle_style` | `AS018` | `AS018` | `AS018` |
| `rd_style` | `AS016` | `AS016` | `AS016` |
| `user_define` | `AS025A` | — | `AS025D` |
| `customer_inventory` | `AS1204A` | — | `AS1204D` |
| `purchase_type`、`sale_type`、`district_class`、`aa_bank` | 不查，由 U8 判断 | 同左 | 同左 |

  客户存货对照另按客户、存货的数据权限判断编码的两段；收发类别写入只查功能权限。

### 币种、凭证类别的写入

`currency`、`voucher_sign` 可以 `create`、`update`、`delete`。`get`、`list` 照旧按表列名返回；写入的 `fields` 用 RsXml 的 EAI 标签。两类都不收 `template`（400）。

- 新增走 EAI（币种、凭证类别各自的 EAI 导入组件），按总账（`GL`）登录。组件自己提交，桥在调用前查完门槛，调用后在新连接上回读（币种另核对符号、折算方式、小数位数、最大误差）；回读查不到或不一致 504 `outcome_unknown`。组件缺 `Transact` 成员时 503 `com_unavailable`（重试无效）。
- 修改、删除走受控 SQL（EAI 组件只提供新增），按 `AS` 登录：一个事务里锁住要改的行、重查门槛、写，提交后在新连接上回读，不一致 504 `outcome_unknown`。编码按库的排序规则比较（不分大小写、全角半角，`Ｚ1` 与 `Z1` 是同一个类别字）；撞上唯一键时 409 `state_mismatch`。

| `archive` | 编码 | 可写标签 | 新增必填 |
| --- | --- | --- | --- |
| `currency` 币种 | 币种名称 `cexch_name`（最长 8），EAI 报文发成 `<name>` | `code`（币种符号 `cexch_code`，最长 4，新增必填、不能修改）、`caltype`（折算方式 0 / 1，缺省 1）、`precision`（小数位数 0 到 10，缺省 5）、`error`（最大误差，缺省 0.00001）；`id`、`otherused`、`name` 不能写 | `code` |
| `voucher_sign` 凭证类别 | 类别字 `csign`（最长 2），EAI 报文发成 `<type>` | `type_name`（类别名称 `ctext`，最长 30，唯一）；新增另收 `order_code`（排序号 1 到 255，缺省最大加一，不能与其他类别重复）；修改只收 `type_name` | `type_name` |

- 功能权限（没有 403）：币种 `AS028M`（外币设置）；凭证类别 `AS026`。
- `currency`：本位币在 U8 账套参数里维护，修改、删除一律 409；其他系统已使用的 409。币种符号已被别的币种使用时新增 409。修改（`caltype`、`precision`、`error`，只改给出的列）只在币种没有任何引用时允许；被客户、供应商、汇率、科目、本单位开户银行、凭证、销售订单、采购订单、发货单、销售发票、采购发票、收付款单、应收应付单、往来明细、到货单、报价单、委外订单、采购入库单引用时 409 `state_mismatch`。删除时汇率不算引用：没有其他引用的币种连同它的汇率在同一事务里一起删。
- `voucher_sign`：新增时排序号在桥里串行计算，新增后核对排序号不重复（重复只记审计）。删除拒绝 U8 预置的 `收`、`付`、`转`、`记`，账套里最后一个类别，以及被凭证（任一年度）、凭证草稿、常用凭证、自动转账定义、出纳、应收应付明细、固定资产凭证、限制科目引用的类别（409）。新类别的限制类型一律「无限制」，不收限制科目。EAI 组件不可用时新增也可走受控 SQL（桥的 `ArcSign.SqlOnly` / `SqlWhenNoTransact` 开关）。
- `account` 只读：科目按年度存，EAI 报文没有年度，写入落到哪一年无法确定。

### 汇率的写入

`exchange_rate` 可以 `create`、`update`、`delete`。编码同读取：`<币种>:<年度>:<期间>` 是固定汇率的一个期间，`fields` 收 `rate`（记账汇率）和 `adjust_rate`（调整汇率）；`<币种>:<年度>:<期间>:<日>` 是一天的浮动汇率，只收 `rate`（给 `adjust_rate` 400）。`<币种>:<yyyy-mm-dd>` 不能用于写入（400）。汇率必须大于 0、不超过 1000000000（400）；不收 `template`，其他标签 400「未知字段 x」。

- 新增经 U8 EAI 分发器（`U8Distribute.iDistribute.ProcessEx`，根标签 `currencyrate`），按 `GL` 登录。U8 把汇率写进登录年度，所以编码的年度必须是登录日期的年份（400）。`rate`、`adjust_rate` 必须恰好给一个（400）。浮动汇率的日写成 `yyyy-mm-dd`，必须在编码的年度、期间（自然月）里（400）。同一（币种、年度、期间、类型[、日]）已有汇率时 409 `state_mismatch`，请用 `update`（U8 对重复导入回成功但不改汇率）。导入自行提交，预演停在调用之前（`validate`）；调用后在新连接上回读：恰好一行且汇率一致才成功（浮动汇率按实际的日期改写响应的 `code`）；有行但不唯一或汇率不符 504 `outcome_unknown`；没有行时按 U8 应答（拒绝 409 `u8_rejected`，说成功 504）。
- 修改、删除走受控 SQL（EAI 不提供），按 `AS` 登录：一个事务里锁住该编码下的汇率行、过门槛、逐行写、事务内复查，提交后在新连接上回读，不一致 504 `outcome_unknown`。修改只改给出的那种汇率，该编码下没有这种汇率 404（请用 `create`），多于一行 409；删除删掉该编码下的全部汇率（固定汇率是该期间的记账和调整汇率），一行都没有 404。预演回滚（`rollback`）。已有单据、凭证存的是各自的汇率，不受影响。
- 门槛（新增、修改、删除都查）：币种必须存在（400）；本位币没有汇率（409）；总账该年度该期间已结账 409 `state_mismatch`。
- 功能权限 `AS028M`，没有 403。

### 原因码的写入

`reason`（原因码档案，表 `Reason`）可以 `get`、`list`、`create`、`update`、`delete`，都走 EAI（`U8SrvTrans.IClsCommon.Transact`，根标签 `reason`），同九类可写档案：按 `AS` 登录，组件自己提交，桥在调用前查完、调用后在新连接上回读，预演停在调用之前（`validate`）。`get` 的 `fields` 是 EAI 标签（`code`、`name`、`Reasontype`、`ReasonMemo`）。读取走 SQL。

| 标签 | 列 | 说明 |
| --- | --- | --- |
| `code`（顶层 `code`） | `cReasonCode` | 1 到 10 个字符，不能改，不能放进 `fields` |
| `name` | `cReasonName` | 1 到 30 个字符，新增必填 |
| `Reasontype` | `iReasontype` | 所属类型，0 到 255 的整数，新增必填。1 不良品原因、2 让步放行原因、3 采购退货原因、4 销售退货原因、5 变更原因、6 拖欠原因；其他取值按 U8 原因码分类（如预置的 `Refund` 是 15 退款退货）。不良品处理单（QM05 / QM06）表体的 `creasoncode` 要 1 |
| `ReasonMemo` | `cReasonMemo` | 说明，最长 240，给空串清空 |

- 修改发整条记录（`proc=edit`，当前值打底再叠 `fields`）；删除只带 `code`。
- 删除前查引用，被引用时 409 `state_mismatch`「档案 X 已被…使用，不能删除」：不良品处理单（表头退货原因、表体不良原因）、检验单（不良原因、退货原因）、报检单（退货原因）、发货单、销售发票、退货申请单、销售结算。生产、车间单据的原因列不查；U8 自己拒绝时 409 `u8_rejected`。
- 功能权限：U8 没有为原因码档案设功能 id；读取登录即可、不按记录过滤，写入由 U8 判断。
- `meta` 的 `field_refs` 把 `creasoncode` 指向 `reason`；`archives/resolve` 可按名称解析原因码。

### 客户、供应商的银行账户和联系人

客户银行账户、供应商银行账户、客户联系人、供应商联系人可以 `get`、`list`、`create`、`update`、`delete`，一次只动一行，同一客户（供应商）的其他行不动。编码是两段（规则同 `customer_address`），`get` 的 `fields` 是表列名；写入的 `fields` 是下表的固定标签（不分大小写），其他标签 400「未知字段 x」，不收 `template`。

| `archive` | 表 | `code` | 列表 `name` | `changed_since` | 可写标签 | 新增必填 |
| --- | --- | --- | --- | --- | --- | --- |
| `customer_bank` 客户银行账户 | `CustomerBank` | `<客户编码>:<银行账号>`（20、50） | 开户银行 `cBranch` | 不支持（400） | `branch` 开户银行（最长 100）、`bank_code` 所属银行编码（最长 5）、`account_name` 账户名称（60）、`default` 默认账户（布尔）、`province`、`city`（20）、`cbb_dep_id`、`branch_id`（60）、`branch_id_sec`（5） | `branch` |
| `vendor_bank` 供应商银行账户 | `VendorBank` | `<供应商编码>:<银行账号>`（20、50） | 同上 | 不支持（400） | 同上 | `branch` |
| `customer_contact` 客户联系人 | `Crm_Contact` | `<客户编码>:<联系人编码>`（20、30）；新增写成 `<客户编码>:`（冒号后留空） | `cContactName` | 支持 | `name`、`title`、`sex`（男 / 女 / 不详）、`birthday`（YYYY-MM-DD）、`native`、`position`、`direct_leader`、`mobile`、`office_phone`、`family_phone`、`bp`、`email`、`web`、`work_address`、`postcode`、`marriage`（已婚 / 未婚 / 离异 / 不详）、`family_member`、`family_address`、`favorite`、`be_main_linker`（布尔）、`charge_person`、`memo`、`self_define1`–`self_define10`；长度按列宽（`name` 50，`title`、`bp`、`postcode`、`charge_person`、`self_define1`–`3` 20，`native`、`direct_leader` 30，`web` 50，`memo` 240，`self_define4`–`6` 60、`7`–`10` 120，其余 100 到 255），超长 400；`name` 前后不能有空格 | `name` |
| `vendor_contact` 供应商联系人 | `Ven_Contact` | `<供应商编码>:<联系人编码>`（20、30）；新增写成 `<供应商编码>:` | `cContactName` | 支持 | 同客户联系人，但没有 `position`、`favorite`；列宽相同 | `name` |

- 上级客户（供应商）必须存在（400）。数据权限按编码的第一段，`get` 403，`list` 去掉不在授权内的行。
- 功能权限（没有 403）：

| 档案 | 读取 | 新增 | 修改 | 删除 |
| --- | --- | --- | --- | --- |
| `customer_bank` | `AS011Q` 或 `AS011` | `AS011` | `AS011` | `AS011` |
| `vendor_bank` | `AS005Q` 或 `AS005` | `AS005` | `AS005` | `AS005` |
| `customer_contact` | `CS020202`（联系人查询）、`AS011Q` 或 `AS011` | `CS020204` | `CS020201` | `CS020205` |
| `vendor_contact` | `AS020302`、`AS005Q` 或 `AS005` | `AS020305` | `AS020301` | `AS020306` |

- 银行账户的默认账户同 U8：每个客户（供应商）至多一个默认账户。第一个账户没给 `default` 时自动设为默认，给 `default=false` 409；设某个账户为默认时其余清成非默认；默认账户不能取消默认（409），还有其他账户时不能删除（409，先把另一个设为默认），只剩它一个时可以删除。账号已存在 409「档案编码已存在」，不存在 404。单据里的账号是文字副本，删除账户不查单据。
- 银行账户的写入是桥的受控 SQL：一个事务里锁住上级行和它的全部账户，只改目标行，目标设为默认时清其他默认，并把上级档案的开户银行、银行账号、所属银行编码改成它的值（同 U8）；删掉最后一个账户时，若上级档案上的账号就是它则清空这三列。提交后在新连接上回读该上级的全部账户，与预期有任何不同 504 `outcome_unknown`。
- 客户联系人的写入走 EAI（`customerlinker`，`proc` 为 `add`、`edit`、`Delete`）。新增时 U8 自己编联系人编码，所以 `code` 写成 `<客户编码>:`（第二段不空 400）；桥调用后在新连接上找这个客户、这个名称、调用前没有的那一行：恰好一行且字段一致才成功；有新行但不唯一或字段不符时 504 `outcome_unknown`，消息带新行编码（不要重发，以免重复）；一行都没有时按 U8 应答（拒绝 409，说成功 504）。响应的 `code` 是 `<客户编码>:<U8 编的号>`，之后修改、删除用它（第二段为空 400）。性别、婚姻状况没给按「不详」。修改发整条当前记录再叠 `fields`；当前的性别、婚姻状况在 U8 基础编码里找不到又没在 `fields` 里给时 409。修改后回读比较整行，调用方没改的列（变更人、变更日期、rowversion 除外）有变化 504。删除前查引用（409）：客户档案和客户对照上的主要联系人、收货地址、销售订单、发货单、销售发票、销售报价单、销售支出单、合同联系人。
- 供应商联系人新增经 U8 EAI 分发器（`U8Distribute.iDistribute.ProcessEx`，根标签 `vendorcontact`），U8 编号为供应商编码加 8 位流水；`code` 写成 `<供应商编码>:`，回读规则同客户联系人（另要求与应答的 `u8key` 一致）。性别、婚姻状况没给就不发。预演停在调用之前（`validate`）。修改、删除走受控 SQL（EAI 不支持，预演回滚）：一个事务里锁住该行，修改只写给出的标签（性别、婚姻状况换成 U8 基础编码，换不出 409；空串写成空值；变更人、变更日期按本操作员、当前时间补上），事务内和提交后各回读一次，提交后不一致 504。主要联系人：每个供应商至多一个，已有其他主要联系人时新增或改成主要 409（先把原来的改掉）；供应商档案上登记的主要联系人不能取消主要（409），桥不改供应商档案。删除前查引用（409）：供应商档案和供应商对照上的主要联系人、采购订单、采购发票、委外订单、进项发票登记、供应商资格审批、合同联系人。

### 固定资产卡片与设备台账的写入

固定资产卡片（`fa_card`）可以新增、撤销本期新增，设备台账（`equipment`）可以新增，都经 U8 EAI 分发器（`U8Distribute.iDistribute.ProcessEx`；卡片根标签 `capitalasserts`，设备 `eqdata`），由 U8 自己提交。U8 拒绝时 409 `u8_rejected`；回读对不上或应答无法识别 504 `outcome_unknown`。

固定资产卡片 `fa_card`：

- `archives/create`：`code` 是**资产编号**（EAI 的 `assetno`，1 到 20 个字符，已存在 409），不是卡片编号；卡片编号由 U8 按编号规则编。响应 `{"ok":true,"archive":"fa_card","code","asset_num","card_id"}`：`code` 是新卡片的卡片编号（之后 `get`、`delete` 用它），`asset_num` 是资产编号，`card_id` 是 `sCardID`。
- `fields`（数字可以是 JSON 数字或数字字符串）：必填 `name`（最长 50）、`type_code`（资产类别，末级）、`original_value`（原值，大于 0，不超过 1e12）、`start_date`（开始使用日期 yyyy-mm-dd，必须早于固定资产当前期间的第一天：EAI 导入的是原始卡片）、`origin_code`（增加方式）、`status_code`（使用状况）、`depreciation_method_code`（折旧方法）、`dept_code`（使用部门，末级；只支持单部门，比例 1）。可选 `useful_life_months`（0 到 11988 的整数）、`used_months`（不小于 0 的整数）、`accumulated_depreciation`、`net_salvage`（不小于 0、不超过原值）、`net_salvage_rate`（0 ≤ x < 1）、`spec`、`location`（各最长 50）、`keeper`（最长 20）、`impairment`（不小于 0）、`currency`（只收本位币名称，缺省本位币）。不收 `template`（400）。编码不存在或不是末级 400，非本位币 400。
- 期间检查（新增、撤销相同，在调用 EAI 之前）：账套启用了固定资产，登录日期在固定资产当前期间，且该期间固定资产未结账，否则 409 `state_mismatch`。桥不做固定资产结账和计提折旧（在 U8 客户端做）。
- `archives/delete`（撤销本期新增，EAI `proc=delete`）：`code` 是卡片编号。只能撤销当前期间录入、只有一个版本（没有变动单、没有减少、没有制单）的卡片，否则 409 `state_mismatch`；不存在 404。响应另带 `deleted: true`。这不是资产减少。
- `archives/update` 400：卡片不能直接修改，变化要做变动单。变动单和资产减少都不在 API 里（U8 EAI 不提供），在 U8 客户端做。
- 功能权限：新增 `FA1502`、撤销 `FA1506`，另按使用部门查数据权限（403）。预演是 `validate` 模式。写入策略里归 (`fa_card`, `create` / `delete`)，不归 `archives`。

设备台账 `equipment`：

- `get`、`list` 按表列名返回，`list` 的 `name` 是 `cEQName`，支持 `code_prefix`、`name_like`、`after`、`changed_since`、`keys_only`。读取权限 `archive:equipment`。
- `archives/create`：`fields` 必填 `name`，另收 EAI 模板的标签：`ceqtypecode`、`cabccode`、`csupeqcode`、`cstacode`、`cpcode`、`cdepcode`、`cseq`、`cpiccode`、`cvencode`、`dtccdate`、`dtgmdate`、`dtazdate`、`dtsydate`、`intsynx`、`dtbxdate`、`dbldjgl`、`intdjnum`、`cdjdw`、`dblzdsj`、`cassetnum`、`cmemo`、`cdefine1` 到 `cdefine16`。制单人（登录操作员姓名）和日期（登录日期）由桥填。编码已存在 409。响应 `{"ok":true,"archive":"equipment","code"}`。写入策略里归 `archives`。
- 修改 400「设备台账的 U8 官方导入（EAI eqdata）只支持新增，修改请在 U8 客户端处理」；删除 400「档案 equipment 不支持删除，请在 U8 客户端处理」。

## 16. 列表和现存量

### `vouchers/list`

`type` 是 §4 表里的可读取类型之一，另收只读的票据类型 `ar_note` / `ap_note`（见下文「票据」）。可选：

| 字段 | 说明 |
| --- | --- |
| `filter` | `code`、`code_from`、`code_to`、`date_from`、`date_to`、`cus_code`、`ven_code`、`wh_code`、`dep_code`、`person_code`、`maker`（字符串，1 到 60 字），以及 `verified`、`closed`、`red`（布尔）。该类型没有对应列的键 400「该单据类型不支持筛选字段 x」，未知键 400「未知筛选字段 x」 |
| `keys_only` | true 时每项只有 `id`、`code`、`ufts` |
| `changed_since` | 上一轮的 `watermark` |
| `after` | 上一页的 `next`（整数） |
| `limit` | 1 到 500，缺省 100 |

响应 `{"ok":true,"type","items","next","watermark"}`。完整行的键固定，没有值时为 null：`id`、`code`、`doc_date`、`cus_code`、`ven_code`、`wh_code`、`dep_code`、`person_code`、`maker`、`verifier`、`verified_at`、`closer`、`verified`、`closed`、`red`、`ufts`，再加各类型的附加列。收付款单列表不含银行账号列。表体有 rowversion 的类型，增量按表头、表体较大的算；表体没有 rowversion 的只按表头。

各类型的特别说明：

| 类型 | 说明 |
| --- | --- |
| `arrival`、`purchase_return` | `arrival` 包括退货单，可用 `filter.red=false` 排除；`purchase_return` 只列 `iBillType=1` 的到货单，`red` 取 `bNegative`，附加列同到货单 |
| `dispatch`、`sale_return` | `dispatch` 含红字行（`red` 为 1，可用 `filter.red=false` 排除）；`sale_return` 只列红字发货单（`cVouchType=05` 且 `bReturnFlag=1`） |
| `sale_order`、`purchase_order` | 附加列 `locker`：锁定人姓名，未锁定时为 null 或空串（见 §10「锁定和解锁」） |
| `purchase_invoice` | 附加列 `reviewer`、`reviewed_at`（采购复核，与 `verifier`、`verified_at` 相同）和 `ap_verifier`（应付款管理的审核人） |
| 检验单、不良品处理单（QM03 到 QM06） | 附加列 `wf`（布尔）、`wf_state`（字符串 `"0"` 未提交、`"1"` 审批中、`"2"` 通过、`"-1"` 不通过）、`current_auditor`（当前审核人姓名，原样返回，没有时为 null），事件服务靠它们发 `workflow` 事件；其他类型没有这三个键 |
| `qm_incoming_inspect`、`qm_product_inspect` | 只列 `QM01` / `QM02`。没有审批流，不带 `wf`、`wf_state`、`current_auditor`；`dep_code` 是业务部门，`wh_code`、`person_code` 为 null（仓库、存货在表体）；附加列 `source`、`source_code`、`source_id`（到货单 ID 或生产订单 `MoId`）、`inspect_dep_code`（报检部门）、`check_type`、`created_at`、`modified_at`，来料报检单另有 `arrival_date` |
| `qm_other_inspect` | 只列 `QM11`，同产品报检单，`source`、`source_code`、`source_id` 恒为 null |
| `qm_other_check` | 只列 `QM15`，没有审批流；`dep_code` 是检验部门；附加列 `source`（恒为 null）、`inspect_code`、`inspect_id`（对应的其他报检单）、`inspect_dep_code`、`check_type`（`OTH`）、`created_at`、`modified_at` |
| `bom` | 只列标准 BOM：`code` 是母件存货编码（`filter.code` 按母件筛），`doc_date` 是版本生效日期，`maker` 是制单人编码，`verified` 是已审核状态，`closed` 是停用状态，`closer` 是停用人；附加列 `version`、`version_desc`、`end_date`、`status`、`wf`、`created_at`、`modified_at`；数据权限按母件存货 |
| `shape_change` | 只列 `cVouchType=15`，表头没有仓库（`wh_code` 为 null）；附加列 `in_rd_code`、`out_rd_code`、`source`、`created_at`、`modified_at`；增量只按表头 |
| `transfer_request` | `wh_code`、`dep_code` 是调出方，附加列同调拨单，`closer` 是表头关闭人 |
| `stock_check` | `verifier`、`verified_at` 是 `cAccounter`、`dveridate`；附加列另有 `check_date`（盘点日期） |
| `purchase_settle` | `code` 是结算号，`doc_date` 是结算日期，`ven_code` 是供应商，`maker` 是制单人；没有审核，`verifier`、`verified_at` 为 null，`verified` 恒为 false；没有 `cus_code`、`wh_code`、`closed`、`red`（对应筛选 400）。附加列 `settle_type`（常为 `01`）、`bus_type`、`pt_code`、`opening`（期初）、`memo`，以及按表体汇总的 `line_count`、`accounted_lines`（存货核算已处理结算成本的行数）、`invoice_lines`（有发票的行数）、`receipt_count`（涉及的入库单号个数）、`first_invoice_code`、`first_in_code`（单号最小的发票号、入库单号）、`quantity`（结算数量合计）、`amount`（结算金额合计） |
| `position_adjust` | `code` 是 `cVouchCode`，`doc_date` 是 `dDate`，`wh_code` 是表头仓库，`verifier` / `verified_at` 是 `chandler` / `dVeriDate`；没有 `cus_code`、`ven_code`、`closed`、`red`；附加列 `memo`、`source`、`created_at`、`modified_at`；增量只按表头 |
| `ia_adjust` | 不按单据类型过滤，`doc_date` 是 `dJVDate`；`verified` 表示表体每行都已记账（`filter.verified` 同此），`verifier` 是表头记账人、为空时取第一个有记账人的表体行，`verified_at` 为 null；没有 `cus_code`、`ven_code`、`closed`、`red`（对应筛选 400）。附加列 `vouch_type`（`20` 入库调整、`21` 出库调整等）、`rd_flag`、`rd_code`、`auto`（`TRUE` 为期末处理自动生成）、`bus_type`、`unit_code`（客户或供应商编码）、`vendor_code`、`handler`（经手人）、`memo`，按表体汇总的 `line_count`、`posted_lines`、`amount`，以及 `created_at`、`modified_at`。增量只按表头：只改表体记账人的记账不出现在增量里 |
| `inventory_price_adjust` | `doc_date` 是 `ddate`，`verifier` 是 `cverifier`，`verified_at` 是 `dverifydate`；没有往来单位、仓库、`closed`、`red`；附加列 `memo`；增量按表头 |

`vouchers/search` 对上面几类的支持：`purchase_settle` 按供应商（`partner`）、部门、业务员、存货查找，不支持表头自定义项；`ia_adjust` 按部门、业务员、仓库、存货和表头自定义项；`inventory_price_adjust` 按部门、业务员、存货，不支持表头自定义项。

票据：`ar_note` / `ap_note` 列应收 / 应付票据，只读，不是单据类型（`vouchers/load` 和写路由 400，单张用 `notes/get`）。`id` 是 `Auto_ID`，`code` 是票据号，`doc_date` 是签发日期，往来单位在 `cus_code`（应收）或 `ven_code`（应付），常为空，单位名称在附加列 `dw_name`；`maker` 取经办人，为空时取登记人；票据没有审核，`verified` 恒为 false；`closed` 表示余额为 0；没有 `red`、`wh_code`。附加列 `settle_code`、`amount`、`remainder`、`close_id`（登记时生成的收款单）、`opening`（期初票据，布尔）、`expire_date`、`dw_name`、`currency`。增量只按表头（结算、贴现、背书、退回回写余额时表头随之变化）。登录子系统 `AR` / `AP`；功能权限 `AR2231` / `AP2231`（票据列表查询），数据权限按往来单位、部门、业务员、项目。

### `notes/get`

只读（读线程池，只查数据库，不登录 U8）。`type`（`ar_note` / `ap_note`）加 `id`（`Auto_ID`）或 `code`（票据号，1 到 60 字）二选一。响应 `{"ok":true,"type","head","subs","subs_truncated"}`：

- `head`：`id`、`code`、`flag`、`doc_date`、`receipt_date`、`expire_date`、`partner_code`、`dw_name`、`dep_code`、`person_code`、`maker`、`settle_code`、`currency`、`rate`、`amount`、`amount_local`、`remainder`、`remainder_local`、`interest_rate`、`km_code`（票据科目）、`bank`（出票银行）、`digest`、`item_class`、`item_code`、`endorser`、`close_id`、`opening`、`sub_package`、`sub_start`、`sub_end`、`change_type`、`created_at`、`modified_at`、`ufts`，不含银行账号列。
- `subs`：处理记录（按主键排序，最多 500 条）：`id`、`style`（9A 结算、9C 退回、9D 贴现、9E 背书）、`style_name`、`date`、`amount`、`amount_local`、`interest`、`expense`、`discount_rate`、`bank`（结算银行科目，背书时是被背书单位）、`km_code`、`operator`、`cancel_no`（处理号）、`source_type`、`source_code`、`gl_ref`（凭证号）、`sub_start`、`sub_end`。

权限同票据列表：越权 403 `no_permission`，票据不存在 404 `not_found`（事件服务据此确认票据已删除）。

### `notes/create`、`notes/delete`

登记和删除应收票据（`flag` `AR`）与应付票据（`flag` `AP`，见本节末）。U8 的票据组件不能无界面调用，桥按 U8 登记票据的写入结果在请求连接的一个事务里完成：检查 → 加锁复查票据号 → 写票据（票据科目取基本科目，交票客户、出票人、票面、余额、登记人等同 U8 登记的当期票据）→ 经收款单新增的同一条路径生成收款单（48），标成票据来源（来源票据号即票据号，分包票据是「票据号-起-止」）→ 回写票据的关联收款单 → 核对 → 提交；任何一步失败整笔回滚。分包票据另写分包标志、子票区间和一行整段可用区间（金额 = 票面），同 U8 登记的分包票据（见 docs/u8-notes.md）。

`notes/create` 字段：

- 必填：`note_no`（票据号，不能已存在）、`settle_code`（结算方式即票据类型，如 301 银行承兑、302 商业承兑，末级）、`amount`（本位币票面金额）、`sign_date`、`receipt_date`（= 收款单日期）、`expire_date`、`customer`（交票客户）、`dept`（末级）、`receiver`（收款人）。
- 可选：`drawer`（出票人，缺省客户名称）、`drawer_bank`（出票人开户银行，也写到收款单）、`person`、`receive_bank`、`receive_account`、`km`（收款单表体科目，缺省基本科目，须是应收受控科目）、`digest`（收款单摘要，缺省「票据登记」）、`note_km`（票据科目，末级，缺省基本科目；与基本科目不同时不能是应收、应付受控科目，否则 400）、`sub_start` / `sub_end`（分包票据的子票区间：成对给出，1 到 15 位正整数，`sub_end` 不小于 `sub_start`，每个号 0.01 元，`(sub_end − sub_start + 1)` 必须等于 `amount × 100`，不符 400 `bad_request`，`field` 为 `sub_end`，如「子票区间与金额不符：区间 N 张 = X.XX 元，票面 Y.YY（每张子票 0.01 元）」；都不给是非分包票据）。
- 409 `state_mismatch`：票据号已存在；已有收款单使用该票据号（含分包后缀，补零也算）；以该票据号加子票区间为票据号的收付款单超过 200 张、无法核对是否重号；票据已有残留的可用区间行；期间已结账、没有设置票据科目等。
- 日期：签发、收票日期不晚于登录日期，到期日、收票日期不早于签发日期，收票日期在会计期间内、不早于应收启用日期、所在期间应收未结账。
- 响应 `note_id`、`note_no`、`receipt_id`、`receipt_code`、`amount`、`customer`、`settle_code`、`sub_start`、`sub_end`（非分包为 null）、`receipt_verified`（总是 false：收款单未审核，`notes/process` 之前用 `vouchers/verify` 审核它）。

`notes/delete` 字段：`note_no` 或 `id`（`Auto_ID`）二选一。只删登记后还没处理的票据：不是期初、没有处理记录、没换过票、余额等于票面，分包票据的可用区间仍是登记时的整段（非分包票据不能有可用区间行），U8 客户端登记的分包票据同样可删；它的收款单未审核、未制单、未核销、没有往来明细、不在审批流中（`vouchers/delete` 拒绝删除来自票据的收款单，这里是唯一的入口）。在一个事务里锁住票据并确认未被修改，删收款单、可用区间、保证金、付款申请明细和票据，核对后提交，任何一步失败整笔回滚。响应 `note_id`、`note_no`、`receipt_id`、`receipt_code`、`sub_start`、`sub_end`（非分包为 null）、`deleted`。

两者 `dry_run` 是 `rollback` 模式：真实写入、核对后回滚，`detail` 带检查后的输入。锁键 `note:AR`，登记另加 `new:ar_receipt`，删除另加 `arap:writeoff:AR`。功能权限：登记「票据录入」`AR0504`，删除「票据删除」`AR2403`；数据权限按客户、部门、业务员。

应付票据（`flag` `AP`，第二级写入，`feature_disabled` / `test_account_only` 见 §18）：

- 往来单位字段是 `vendor`（收票供应商；`customer` 给了 400），应收票据反过来不收 `vendor`。
- 登记生成付款单（49，表体一行应付款，科目 `km` 缺省取应付基本科目，须是应付受控科目），标记、回写同收款单。票据科目取 `note_km`，省略取应付的票据科目设置，两者都没有 409「应付款管理没有设置票据科目…」。
- 收票日期按应付的启用日期、结账检查。响应把 `customer` 换成 `vendor`，`receipt_id` / `receipt_code` 是付款单。删除同应收票据，删的是付款单。
- 锁键 `note:AP`、`new:ap_payment`（删除 `arap:writeoff:AP`）。功能权限：登记 `AP0504`、删除 `AP2403`；数据权限按供应商、部门、业务员。

### `notes/process`

票据的结算、贴现、背书和退回。U8 票据处理窗体没有可调用的组件，桥按 U8 处理票据的写入结果，在一个事务里写票据处理行、票据余额、往来明细、分包票据的可用区间，提交前后核对；不制单（制单走 `arap/process/voucher`）。

| `op` | 处理 | 处理方式 | 处理号前缀 | 专用字段 |
|---|---|---|---|---|
| `settle` | 托收 / 结算 | 9A | `PJJAR` | `bank_code`（必填，末级银行科目）、`bank_name`（缺省科目上级名称） |
| `discount` | 贴现 | 9D | `PJTAR` | 同上，另有 `expense`（贴现息、手续费）、`interest`（票据利息）、`rate`（贴现率 %，最多六位小数） |
| `endorse` | 背书冲应付 | 9E | `PJBAR` | `vendor`（被背书的供应商）、`ap_lines`（1 到 50 项 `{type, id, line_id?, amount}`，`01` / `02` / `P0`，不收付款单 `49`） |
| `return` | 退回 | 9C | `CLAR` | 无 |

公共字段：`note`（票据号字符串，或 `Auto_ID` 整数）、可选 `amount`（本位币；省略时背书取 `ap_lines` 合计，其余取票据余额，分包票据取第一段可用区间；可以部分处理，退回除外）、`sub_start` / `sub_end`（分包票据的子票区间，成对，每个号 0.01 元，同时给 `amount` 时须一致）、`digest`。某个 op 不用的专用字段给了就 400。贴现净额 = 金额 + `interest` − `expense`，须大于 0；背书的 `ap_lines` 合计须等于背书金额。处理日期就是登录日期，须在应收（背书另查应付）未结账的期间内，不早于签发、收票日期；登记生成的收款单须已审核。

退回：把票据（分包票据须是剩下的全部可用区间）退还客户，并生成一张应收单（`R0`）重新挂上应收款（见 docs/u8-notes.md）。应收单经 U8 应收单组件保存（同 `vouchers/create` 的 `ar_bill`，主键、单号由 U8 分配）：往来单位是票据的客户，部门、业务员取票据，科目是登记收款单应收款行的应收控制科目（没有时取基本科目），表头摘要「转出票据{票据号}」；保存后补写处理标记、余额 0、审核人（操作员）和审核日期（退回日期），同 U8 退回生成的应收单。这张应收单不能经 `vouchers/verify` 审核、弃审（409「单据由票据退回等处理生成，不能审核或弃审」），只能取消退回。往来明细写应收单借方一行和票据贷方一行，缺省摘要「退回{客户名称}电子承兑」。登记时的收款单不动。409：退回日期早于票据已有的处理；分包票据没有退回全部可用区间（「分包票据退回须退回全部可用区间…」，可用区间不止一段时请在 U8 客户端退回）；取不到应收控制科目或应收单模板；U8 拒绝保存应收单 409 `u8_rejected`。

响应 `cancel_no`、`style`、`op`、`date`、`note`（`id`、`code`、`partner`、`partner_name`）、`amount`、`remaining`（处理后的票据余额）、`sub_start`、`sub_end`、`digest`；结算、贴现另有 `bank_code`、`bank_name`，贴现另有 `net`、`expense`、`interest`、`rate`，背书另有 `vendor`、`ap_rows`（每张应付单据行的 `amount`、`remaining`），退回另有 `r0_id`、`r0_code`。404 `not_found`：票据、科目、供应商不存在；409 `state_mismatch`：外币票据、已换票、已退回、余额不足、子票区间不可用、收款单未审核、科目不是末级银行科目、期间已结账、票据在 U8 客户端被占用等；提交后回读不符 504 `outcome_unknown`。`dry_run` 是 `rollback` 模式（退回时 U8 取过的应收单号、主键计数不退，真做时跳号）。

锁键 `arap:writeoff:AR`，背书另加 `arap:writeoff:AP`，退回另加 `new:ar_bill`。功能权限（都另收「票据录入」`AR0504`）：结算「票据结算」`AR240203` 或「票据收款」`AR240201`，贴现 `AR240205`，背书 `AR240206`，退回「票据退票」`AR240207` 或「票据转出」`AR240202`；数据权限按票据的客户、部门、业务员，背书另按每张应付单据。

应付票据（`flag` `AP`，第二级写入）只收 `settle`、`return`，贴现、背书 400「应付票据只支持结算（settle）、退回（return）…」：

- `settle`（9A，处理号 `PJJAP…`）：往来明细的票据行记借方；缺省摘要「付{供应商名称}票据到期结算」。
- `return`（9C，`CLAP…`）：把票据退还供应商，生成一张应付单（`P0`，同应收经 U8 应付单组件保存，科目取登记付款单应付款行的应付控制科目，没有时取应付基本科目），往来明细写应付单贷方一行和票据借方一行；响应另有 `p0_id`、`p0_code`（不是 `r0_*`）。
- 处理日期按应付的启用日期、结账检查；登记生成的付款单须已审核。锁键 `arap:writeoff:AP`，退回另加 `new:ap_bill`。功能权限：结算「票据结算」`AP240203` 或「票据付款」`AP240201`，退回「票据转出」`AP240202`（都另收 `AP0504`）；数据权限按供应商。

处理号用于 `arap/process/cancel`（取消）和 `arap/process/voucher`（制单）。两者收的处理号前缀：

| 前缀 | 处理 | 处理方式 | `flag` | 取消 | 制单 |
|---|---|---|---|---|---|
| `YCFAP` / `FCYAR` | 应收冲应付 / 应付冲应收 | 9I / 9J | AR / AP | 是 | 是（来源 ZZ） |
| `BZAR` / `BZAP` | 并账 | BZ | AR / AP | 是 | 是（BZ） |
| `HRAR` / `HPAP` | 红票对冲 | 9N | AR / AP | 是 | 是 |
| `SYRAR` / `SYPAP` | 汇兑损益 | 9M | AR / AP | 用 `arap/exchange_gain/cancel` | 是（SY，须 `pl_code`，第二级写入） |
| `PJJAR` | 票据结算 | 9A | AR | 是 | 是（PJ） |
| `PJTAR` | 票据贴现 | 9D | AR | 是 | 是（PJ，可给 `expense_code`） |
| `PJBAR` | 票据背书 | 9E | AR | 是（另加回被背书供应商的应付单据余额） | 是（PJ） |
| `CLAR` | 票据退回（U8 客户端或 `notes/process` 做的） | 9C | AR | 是（另删它生成的应收单） | 是（PJ） |
| `PJJAP` | 应付票据结算（第二级写入） | 9A | AP | 是 | 是（PJ，借应付票据、贷银行，缺省类别「付」） |
| `CLAP` | 应付票据退回（第二级写入） | 9C | AP | 是（另删它生成的应付单） | 是（PJ，借应付票据、贷应付账款） |
| `HZAR` | 坏账发生 / 收回 / 计提（第二级写入） | 9G / 9H / 9F | AR | 是（计提只取消该年度最后一次） | 是（JT，同一种坏账处理） |

- 取消票据处理加回票据余额和分包票据的可用区间、删票据处理行；已制单要先 `arap/voucher/delete`。取消退回另删它生成的应收（应付）单，该单已被核销、转账等引用时 409，先取消那些处理。
- `arap/process/voucher` 的 `expense_code`（贴现费用科目，不超过 40 位、不含空白）只用于 `PJTAR` 批次，省略取基本科目设置；其他批次给了 400。
- `arap/process/voucher` 的 `cash_items`（形状和规则同 `arap/voucher` 的 `cash_items`）：票据处理和坏账收回（9H）的凭证按「现金流量项目数据来源」给对方分录挂现金流量项目，推不出时 409「…请用 cash_items 指定，或在 U8 客户端制单」；其他处理（应收冲应付、并账、红票对冲、汇兑损益、坏账发生 / 计提）的凭证不挂项目，给了 400「科目 X 在本次凭证里没有现金流量行」。
- 应付票据的贴现、背书批次 `PJTAP`、`PJBAP` 不收（400）。第二级写入的批次（`PJJAP`、`CLAP`、`HZAR`、`SYRAR` / `SYPAP`）的取消、制单和 `arap/voucher/delete` 删其凭证，同样受 `feature_disabled` / `test_account_only` 限制。

### `stock/current`

可选 `wh`、`inv`、`batch`、`nonzero`（只要现存量不为 0 的行）、`changed_since`、`after`、`limit`。响应 `{"ok":true,"items","next","watermark"}`。每项有仓库、存货、批次、自由项和各项数量，以及三种可用量：

| 字段 | 口径 |
| --- | --- |
| `qty_available_raw` | `CurrentStock.fAvaQuantity` 的原值。U8 通常不维护这一列 |
| `qty_available` | 按 U8 的可用量公式：整行冻结为 0，否则现存量 − 冻结量；待入、调拨待入、待出、调拨待出只在库存选项打开时计入 |
| `qty_forecast_available` | 现存 − 冻结 + 预计入库合计 − 预计出库合计。本项目的口径，U8 没有同名数值 |

### `arap/process/list`

只读（读线程池，只查数据库）。列出 `flag`（`AR` / `AP`）一侧往来明细里的处理行：核销、应收冲应付、应付冲应收、并账、红票对冲、汇兑损益、票据等，不含单据本身的审核行和没有处理号的行。登录子系统是 `flag`。功能权限「取消操作」`AR0807`、「应收核销明细表」`AR060107` 或「选择收款」`AR0503`（应付 `AP0807` / `AP060107` / `AP0503`）；数据权限按往来单位、部门、业务员过滤行，过滤后照常 200。用作事件源时事件服务的操作员必须有全部往来数据权限：这条路由没有 403 / 404 的存在性证明，被过滤掉的批次和被取消的批次看起来一样。

明细（缺省）：可选 `changed_since`（上一轮的 `watermark`，十进制字符串或整数，0 即全量）、`after`（本轮上一页的 `next`，整数）、`limit`（1 到 500，缺省 100）、`keys_only`（每行只有 `id`、`flag`、`style`、`code`）、`open_only`（只要登记期间该侧未结账的行；`changed_since` 大于 0 时缺省 true）。按 `Auto_ID` 升序，响应 `{"ok":true,"flag","items","next","watermark","ident","open_periods","last_closed"}`。完整行：`id`（`Auto_ID`）、`flag`、`style`（处理方式）、`code`（处理号）、`vouch_type`、`vouch_id`、`co_vouch_type`、`co_vouch_id`、`partner`、`dept`、`person`、`line_id`（发票行，0 为 null）、`debit_f`、`credit_f`（原币，字符串）、`pz_id`、`gl_sign`、`gl_no`、`reg_date`、`period`、`fiscal_year`、`row_flag`。`fiscal_year` 是处理所在的会计年度：`reg_date` 可能是单据日期（如红票对冲取所对冲单据的最大日期），早于处理所在期间；`period` 小于登记月份时（次年 1 月处理上年 12 月的单据）为登记年份加 1。归期间请用 `fiscal_year` 和 `period`，`open_only` 也按它判断。

往来明细没有 rowversion，增量按 `Auto_ID`。`watermark` 是查询前已提交可见的最大 `Auto_ID`，`ident` 是 `IDENT_CURRENT`（之后取，不小于 `watermark`），都是十进制字符串。在途事务可能先拿到较小的号、后提交，所以下一轮的 `changed_since` 要从 `watermark` 往回退 `max(滞后量, ident − watermark)`，按批次的最小 `Auto_ID` 去重。制单（`pz_id` 原地改写）和取消（删行）在明细里看不出来，用摘要比对。

摘要（`digest: true`）：可选 `fiscal_year`（2000 到 2099，缺省登录年度）、`periods`（1 到 12 个期间）、`after`（上一页的 `next`，字符串）、`limit`（批数，1 到 500，缺省 500）；不能带 `changed_since`、`keys_only`、`open_only`，明细也不能带 `fiscal_year`、`periods`。`periods` 省略时取该侧全部未结账期间（给了 `fiscal_year` 只取该年度）。按（处理方式、处理号）排序，每批：`flag`、`style`、`code`、`min_id`、`max_id`、`pz`（未制单为 null）、`sum_d_f`、`sum_c_f`（原币合计，字符串）、`rows`、`fiscal_year`（批内最小的会计年度）、`partners`（往来单位编码，升序）。响应另有 `digest: true` 和 `periods`（实际汇总的期间 `[{year, period}]`）。

两种用法都返回 `open_periods`（该侧未结账的期间，年度不晚于登录年度）和 `last_closed`（最近一个已结账期间，没有为 null）。

### 翻页和增量

都按主键翻页：`after` 填上一页的 `next`，没有下一页时 `next` 为 null。

`watermark` 在查询前取（数据库的 `MIN_ACTIVE_ROWVERSION() - 1`，十进制字符串）。增量同步：一轮从 `after` 缺省开始，按 `next` 读到没有下一页；把第一页的 `watermark` 存下来，作为下一轮的 `changed_since`。表体有 rowversion 的类型按表头、表体两者较大的算，下游只回写了表体也能看到。`keys_only` 的整轮主键集合可以用来发现被删的单据。档案的 `changed_since` 用法相同（人员按两张人员表中较大的 `ufts`）。

## 17. 健康检查、登录检查和 OpenAPI

`GET health`：不签名、不需要账套。有任务卡住超过 3 分钟或工作线程退出时 503 `{"ok":false,"code":"unhealthy"}`。桥返回：

| 字段 | 说明 |
| --- | --- |
| `version`、`workers`、`read_workers`、`queued`、`read_queued`、`running` | 版本，写、读线程数，写、读队列里的任务数，正在执行的任务数 |
| `signatures` | COM 签名自检摘要：`pending`、`ok`、`mismatch:<n>`、`unknown`（细节见 `meta` 的 `features.signatures` 和 getting-started.md） |
| `license` | U8 许可点数：`ok`、`near`（将满）、`full`（已满）、`unknown`（读不到或还没采到） |
| `license_detail` | 按子系统两位码给出点数，如 `{"SA":{"used":3,"limit":10,"full_24h":0}}`：`used` 已用、`limit` 总数、`full_24h` 近 24 小时出现「已满」的次数 |
| `license_source` | `leases`：加密服务器上实时的点数租约，按产品包计数，与 U8「许可管理」一致，`license_detail` 里某子系统的数字是它所在产品包的数（「加密点数已饱和」就是这个包满了），独立模块给自己的数。`tasklog`：读不到租约时的回落，`used` 是登记的工作站数（偏少，只是下限），`limit` 是 U8 许可总数或 `licenseLimits` |
| `license_packs` | 只在 `leases` 时有内容（`tasklog` 时为 `{}`），形如 `{"XX":{"used":3,"limit":10,"modules":["PU","SA","ST"]},"YY":{"used":1,"modules":["GL"]}}`：包码、占点的租约数、总数（桥不知道时省略）和包内模块，最多 32 个包、每包 16 个模块 |
| `write_policy` | 写入策略状态（见 configuration.md）。没配 `writePolicyFile` 时 `{"state":"off"}`；配了时 `{"state","version","loaded_at","freeze":{"global","accounts"},"window_open"}`，`state` 为 `ok`、`invalid`（新内容无效，仍按上一份有效策略判定）、`missing`（没有可用策略，写入全拒），`version`、`loaded_at`（UTC）是当前有效策略的，没有时为 null，`window_open` 是此刻能否写入（已计入 `denyDates`）。不影响健康检查的状态码 |
| `read_only_accounts` | 只读账套（`readOnlyAccounts`），这些账套的写路由一律 403 `account_read_only` |
| `replicated_writes` | 第二级写入总开关 `enableReplicatedWrites`（缺省 `false`） |

`license`、`license_detail` 只统计桥自己会登录的子系统（桥用不到的包满了不影响 `license`），`license_packs` 列出全部产品包。`leases` 时 `near`、`full` 按包的数字算，已用 ≥ 总数也算 `full`。只有数字和子系统码，不含授权方信息。

API 的 `/v1/co/health` 返回 `ok`、`version`，以及桥报了的 `license`、`license_detail`、`license_source`、`license_packs`、`write_policy`、`read_only_accounts`、`replicated_writes`（桥未报告时省略），另有本服务的 `api_write_policy`（配了 `U8CO_WRITE_POLICY_FILE` 时，形状同 `write_policy`）和 `api_read_only_accounts`（配了 `U8CO_READONLY_ACCOUNTS` 时）。API 校验桥给的值：`license` 只收上面四个值；`license_detail` 只收两位大写字母的子系统码和三个非负整数；`license_source` 只收 `leases`、`tasklog`；`license_packs` 的包码和模块码只收两位大写字母或数字，`used`、`limit` 须为非负整数、`modules` 须为列表，最多 32 个包、每包 16 个子系统；`write_policy` 形状不对整项丢掉；其余丢掉。桥不可达一律 503 `unavailable`，不计入调用方的每分钟次数。配了按账套分流的桥（`U8CO_BRIDGE_ROUTES_FILE`，见 configuration.md）时多一个 `routes` 数组，每个分流桥一项：`route`（`routes[序号]`）、`accounts`、`ok`，可用时带 `version`、`write_policy`、`replicated_writes`，不可用时带错误码 `error`；顶层字段只描述缺省桥，某个分流桥不通不影响整体状态码。分流桥并行探测，每个读取超时 8 秒，合计最多等 10 秒，超时记 `ok: false`、`error: "unavailable"`。

`login-check`：`{"ok":true,"operator","operator_name"}`，`operator_name` 是 U8 的操作员姓名。

`GET /v1/openapi.json`（仅 API）：任意已认证调用方都能读，不要求读写权限。OpenAPI 3.1，只含 `/v1/co/*`，每条路径带 Bearer 安全要求，说明末尾写明所需权限，`password` 标为 writeOnly。

### API 的读写分级

令牌的权限来自它命中的信任项（见 configuration.md）：布尔声明（缺省 `u8co_write`、`u8co_read`），或 `scope` / `scp` 里配置的 scope。声明值必须是 JSON 布尔 `true`，字符串 `"true"` 不算。

| 权限 | 能调用 | 没有时 |
| --- | --- | --- |
| 写权限 | 全部路由（另有要求的除外，见下） | — |
| 只读权限 | §3 表里标「读」的路由 | 写路由 403 `forbidden`「只读权限不能调用写操作」，不访问桥 |
| 两个都没有 | 无 | 403 `forbidden`「无权使用 CO 接口」 |
| 经营管理权限（信任项的 `mgmt_claim` 声明为 `true`，或 `scope` 含 `mgmt_scope`） | 经营管理查询 `/v1/co/mgmt/*`（§34），公司间对账、多账套汇总、合并试算 `reports/intercompany_match`、`aggregate`、`consolidation`（§33）。写权限不代替它 | 403 `mgmt_forbidden`「无权查询经营管理数据」 |
| 信任项写了 `perm_evaluate: true`，且令牌有读或写权限 | `perm/evaluate`（查别的操作员的权限）。桥上另按 `permEvaluateOperators` 校验 | 403 `forbidden`「无权查询其他操作员的权限」，不访问桥 |

读写登记表只有一张：`api/u8co_api/co_access.py` 的 `ACCESS`，没登记的路由按写处理。

账套：请求的 `acc` 必须在 `U8CO_ACCOUNTS` 里，否则 403 `account_not_allowed`「账套不在允许列表」。信任项配置了 `accounts_claim` 时，`acc` 还必须在令牌的这个声明里，否则 403 `account_not_allowed`「令牌无权使用该账套」。`acc` 在 `U8CO_READONLY_ACCOUNTS` 里时写路由 403 `account_read_only`「该账套只开放读取」。这几种情况都不访问桥。

调用方与审计：

- 调用方按「信任项:客户端:sub」区分（令牌没有 `sub` 或 `sub` 与客户端相同时是「信任项:客户端」）：幂等记录（`caller` 字段）、`idempotency/get`、每个调用方的并发与频率名额都按它算，同一个 `azp` 下的两个用户互相查不到对方的幂等结果。
- 审计的终端用户（`user`）：令牌有 `sub` 时取 `sub`，`U8CO_USER_HEADER` 头被忽略；只有信任项写了 `on_behalf_header: true` 的机器调用方（代人调用）才取头里的 UUID，令牌自己的 `sub` 另记在 `sub` 字段。
- 经营管理缓存的键含调用方和各账套「账套、操作员、口令、年度、日期」的 HMAC（进程内随机密钥），每次请求先由桥按本次口令登录读 `mgmt/meta`；口令不对的请求拿到桥的登录错误，读不到缓存。
- 桥的错误消息转给调用方前截到 300 字符；403、409 消息里一串 5 个以上的编码折成「首项 等 N 项」，403 的 `detail` 只保留标量字段。

### 不需要令牌的路由

`GET /healthz` 返回 `{"ok":true,"configured":…}`，`configured` 表示 CO 已启用且桥地址和密钥都已配置。配了按账套分流的桥时多一个 `bridge_routes`（分流桥个数）。它不访问桥，只用于容器和负载均衡的存活检查。`/docs`、`/redoc`、`/openapi.json` 都是 404。

### 离线接口参考页

不启动服务也能导出同一份 OpenAPI：`cd api && uv run python -m u8co_api.openapi_export --out openapi.json`。它在内存里建应用（空信任项、空桥地址，不取 JWKS、不连桥），输出与 `/v1/openapi.json` 相同，只有两处不同：说明里的账套一行换成「以部署配置为准」（`--accounts 801,802` 可写入具体账套），`servers` 是占位地址 `https://u8co.example.com`；另补顶层 `tags` 的中文分组说明。输出按键排序，重复导出逐字节相同。

`scripts/docs/build-api-site.sh [目录]` 生成静态文档站点，缺省 `dist/api-site/`：`openapi.json`、`index.html`（模板在 `docs/site/`）、`scalar.standalone.js`，以及由 `scripts/docs/build_docs_pages.py` 把 README 和 `docs/*.md` 渲染成的 `docs/<名称>.html`（同目录附 Markdown 原文）、`404.html`、`robots.txt`、`llms.txt`。环境变量 `SITE_URL`（站点根地址）给出时另生成 `sitemap.xml` 并写入 canonical 等绝对地址，`REPO_URL`（仓库地址）给出时指向仓库文件的链接指向仓库；都不给时省略这些内容。Scalar 按脚本里固定的版本从 npm 仓库下载，sha512 与脚本里的值不符就中止。页面不访问任何 CDN、字体或统计服务，CSP 只允许同源，「试一试」只能发给与页面同源的地址。

`.github/workflows/api-docs.yml` 在 main 分支上 `api/`、`docs/`、`README.md`、`llms.txt`、`scripts/docs/` 有改动时（或手动触发）构建，两个地址都在构建时从 GitHub 上下文取得，上传工作流制品 `api-reference`（保留 30 天）。本工作流构建静态页；GitHub Pages 可选：仓库可见性为公开、且设置里 Pages 的来源选 GitHub Actions 时另行部署。

## 18. 错误码

桥的错误体 `{"ok":false,"code","message"}`，API 的错误体 `{"error":{"code","message","retryable"}}`，两者都可能另带 `field`、`hint`、`detail`。`message` 为中文；U8 拒绝时带回 U8 的文本（API 转出前去掉控制字符、截到 300 字符，403、409 里 5 个及以上的编码清单折成「首项 等 N 项」）；500 对调用方只写「内部错误」，细节只进桥的审计日志。

### 结构化错误

```json
{"error": {"code": "bad_request", "message": "请求参数无效：lines.0.cinvcode", "retryable": false,
           "field": "lines.0.cinvcode", "hint": "…"}}
```

| 键 | 在哪 | 说明 |
| --- | --- | --- |
| `retryable` | 仅 API，总是有 | 布尔。`busy`、`busy_timeout`、`stopping`、`u8_license_full`、`ia_timeout`、`rate_limited`、`unavailable`、`store_unavailable`、`write_policy_unavailable`、`write_frozen`、`write_window`、`write_quota`、`u8_license_hold` 为 `true`（请求没有执行，按 `Retry-After` 稍后重发）；其余一律 `false`，`outcome_unknown` 也是 `false`（见下文「重试」） |
| `field` | 知道是哪个字段时。桥在 401 以外的 4xx 上都可能带；API 只在 400 上转出 | 出错字段在请求体里的路径：点号分隔，键名写法同调用方发来的（桥自己生成的路径用小写），数组下标从 0 开始，例如 `lines`、`head.ccuscode`、`lines.2.iquantity`（不知道下标时 `lines.iquantity`）、`fields.ccusname`（档案）、`items.0.archive`。只含字母、数字、`_`、`.`、`-`，最长 200 |
| `hint` | 桥和 API，可能没有 | 简短的中文处理建议。桥给了就用桥的（最长 300），否则 API 按码补缺省的，见下表。401 和 500 不带 |
| `detail` | 桥和 API，只在 4xx（401 除外），可能没有 | 结构化补充（JSON 对象），各路由自己定义键，例如存货核算记账 409 时的 `uncosted`（`[{wh, inv, batch}]`，最多 20 项）和 `uncosted_total`（§32）。API 转出桥给的对象（403 只保留标量值，字符串同 `message` 截断）；不是对象、为空或序列化后超过 32 KiB 时丢掉 |

API 自己的请求校验失败时，`field` 取校验器报的第一个错误位置（去掉开头的 `body`；查询参数 `fields`、`compact` 出错时就是 `fields` / `compact`），`message` 为「请求参数无效：<field>」；取不到位置时为「请求参数无效」。仍是 400 `bad_request`。

API 的缺省 `hint`（表在 `api/u8co_api/errors.py`；表里没有的码没有缺省提示）：

| code | hint |
| --- | --- |
| `busy`、`busy_timeout`、`stopping` | 稍后重试（见 Retry-After） |
| `u8_license_full` | U8 许可点数已满，60 秒后重试 |
| `ia_timeout` | 已回滚，没有写入：稍后重试，或让管理员调大桥的 iaCommandSeconds |
| `rate_limited` | 降低调用频率，按 Retry-After 重试 |
| `outcome_unknown` | 写入可能已生效：先用 load/list（或 idempotency/get）核对，不要直接重试 |
| `idempotency_mismatch` | 同一个 Idempotency-Key 只能用于同样的请求内容 |
| `login_failed` | 检查账套、年度、操作员和口令 |
| `account_not_allowed` | 这个账套不在允许范围内，换一个账套 |
| `test_account_only` | 结账、核算、期初、坏账、票据、汇兑等第二级写入只对 CO 桥 testAccounts 里的测试账套开放 |
| `feature_disabled` | 第二级写入默认关闭：桥设 enableReplicatedWrites 并配置 testAccounts |
| `no_permission`、`forbidden` | 当前操作员（令牌）没有这项权限 |
| `state_mismatch` | 先 load 看单据当前状态 |
| `unavailable`、`store_unavailable` | 稍后重试 |
| `not_found` | 核对类型、id 或编码（只给桥返回的 `not_found`；API 自己的 404，如路由关闭，不带提示） |
| `write_policy_unavailable` | 写入策略文件缺失或无效，所有写入暂停：请管理员检查策略文件 |
| `write_frozen` | 写入已被管理员冻结，解冻后再试 |
| `write_window` | 不在允许写入的时段：到允许的时段再试 |
| `write_not_allowed` | 写入策略没有放行这个账套的这类写入（见 detail 的 type、op），请管理员调整策略 |
| `operator_not_allowed` | 写入策略不允许这个操作员在此账套写入，换一个操作员或请管理员调整策略 |
| `write_limit` | 行数或金额超过写入策略的上限（见 detail 的 max、actual），拆成几笔再写 |
| `write_quota` | 账套写入限额已满，按 Retry-After 重试 |
| `u8_license_hold` | 接口占用的 U8 登录已达上限，稍后重试 |

调用方不要按 `hint` 的文字做判断，只按 `code`、`retryable`、`field`。

### 桥

| HTTP | code | 含义 |
| --- | --- | --- |
| 400 | `bad_request` | 字段不合法 |
| 400 | `write_limit` | 写入策略的行数（`field=lines`）或金额上限，`detail` 带 `max`、`actual` |
| 401 | `unauthorized` | 签名、时间、随机数或来源 IP 不对，不说明是哪一项 |
| 403 | `account_not_allowed` | 账套不在 `allowedAccounts` |
| 403 | `account_read_only` | 账套在 `readOnlyAccounts` 里，写路由（含预演和幂等重放）一律拒绝，登录 U8 之前判定 |
| 403 | `feature_disabled` | 第二级写入（复现 U8 界面 SQL 的写入）而桥的 `enableReplicatedWrites` 未打开（缺省），账套在 `testAccounts` 里也一样；登录 U8 之前拒绝 |
| 403 | `test_account_only` | 第二级写入的账套不在 `testAccounts` 里（含预演），登录 U8 之前拒绝。第二级写入包括：月末结账（`periods/close`，§31）、存货核算记账和期末处理（`ia/post`、`ia/period_end`，§32）、期初记账和期初单据（`openings/post`、`openings/arap`，§29）、库存期初结存单、坏账、汇兑损益、应付票据、总账取消记账和自动转账（§14）。完整清单见 limitations.md；采购手工结算是第一级写入 |
| 403 | `write_not_allowed` | 写入策略没有放行这个账套的这类写入，`detail` 带 `type`、`op`；登录 U8 之前拒绝（见 configuration.md） |
| 403 | `operator_not_allowed` | 写入策略不允许这个操作员在此账套写入 |
| 403 | `no_permission` | 写操作：操作员没有对应的 U8 功能权限（总账、档案写入、生产订单关闭和打开等），或记录不在数据权限内。读路由：没有该功能权限，或单张单据、档案不在数据权限内（§22） |
| 404 | `not_found` | 单据不存在 |
| 409 | `state_mismatch` | 调用前已是目标状态，不满足门槛，或提交后回读对不上 |
| 409 | `u8_rejected` | U8 返回了拒绝文本，`message` 是原文 |
| 409 | `workflow_enabled` | 单据走审批流，不能直接审核 |
| 409 | `workflow_unknown` | 查不到审批流配置，按失败处理 |
| 409 | `workflow_disabled`、`already_submitted`、`not_submitted`、`not_current_approver` | 审批流状态不符（§13） |
| 409 | `stock_shortage` | 库存不足 |
| 409 | `idempotency_mismatch` | 同一个幂等键已用于内容不同的请求（§20） |
| 422 | `login_failed` | U8 登录失败，`message` 是 U8 的原文 |
| 429 | `busy` | 写队列或读队列已满；入队本身失败时同一个码以 503 返回。都没有执行，可以重试 |
| 429 | `write_quota` | 账套写入限额已满，`detail.retry_after_seconds` 是建议等待的秒数 |
| 500 | `internal` | 桥内部错误 |
| 503 | `com_unavailable` | COM 组件没有注册、创建失败，或所需程序集加载失败 |
| 503 | `u8_unavailable` | U8 自己的服务或文件不可用（例如生产制造服务没在运行、EAI 对照文件读不到） |
| 503 | `busy_timeout` | 排队超过 75 秒，任务已放弃，可以重试；写入（新增、修改、删除、审核、关闭、生单、锁定、审批流操作、总账和档案写入、应收应付的核销和取消核销）排队超过 45 秒、剩余时间不足 30 秒时也不再开始，同样返回它（没有执行）；开了 `cleanOrphanTasks` 时也可能是审批流操作等孤儿清理锁超时（没有调用 U8），同样可以重发 |
| 503 | `u8_license_full` | U8 许可点数已满（「加密点数已饱和」），桥已自己有限重试过，`message` 写明子系统。请求没有执行；写入策略 `holdWritesWhen` 暂停写入时也是这个码（「…接口暂停写入，请稍后重试」） |
| 503 | `ia_timeout` | 存货核算脚本超过 `iaCommandSeconds`（§32）：在提交之前超时，事务已回滚，没有写入；可以稍后重发，或调大 `iaCommandSeconds` |
| 503 | `write_policy_unavailable`、`write_frozen`、`write_window` | 写入策略：没有可用的策略（失效即拒写）、写入已冻结（消息带原因）、不在可写时段。写入没有执行 |
| 503 | `u8_license_hold` | 写入策略的 `license.maxConcurrentLogins`：本进程的 U8 登录已达上限，没有登录、没有执行 |
| 503 | `stopping` | 服务正在停止 |
| 503 | `store_unavailable` | 幂等记录读写失败，请求没有执行，可以重试（§20） |
| 503 | `unhealthy` | 健康检查失败 |
| 504 | `outcome_unknown` | 75 秒时任务已经在执行，或 U8 自己提交后回读失败。结果未知 |

### API

API 把桥的错误码原样带上，HTTP 状态按下表：

| HTTP | code | 含义 |
| --- | --- | --- |
| 400 | `bad_request` | 请求字段不合法（模型校验失败时 `message` 为「请求参数无效」） |
| 400 | `ic_group_mismatch` | 请求里的账套不在同一公司组 |
| 400 | `idempotency_required` | `intercompany/generate_buyer` 正式生成没带 `Idempotency-Key`（`field=dry_run`） |
| 401 | `unauthorized` | 缺少或无效的访问令牌，带 `WWW-Authenticate: Bearer` |
| 403 | `forbidden` | 令牌没有所需权限 |
| 403 | `account_not_allowed` | 账套不在 API 的白名单，不访问桥 |
| 403 | `account_read_only` | 账套在 `U8CO_READONLY_ACCOUNTS` 里，写路由不访问桥 |
| 403 | `mgmt_forbidden` | 令牌没有经营管理权限（§17） |
| 403、503、400 | 写入策略各码 | 配置了 `U8CO_WRITE_POLICY_FILE` 时 API 在调桥之前按同样的规则拒绝，码和消息与桥相同（不含 `write_quota`、`u8_license_hold`，那两项只在桥上） |
| 404 | `ic_not_configured` | 没有配置公司间对照（`U8CO_IC_MAP_FILE`），公司间接口和多账套合并不可用 |
| 404 | `not_found` | `U8CO_ENABLED=0` 关闭了 CO 路由，或单据不存在 |
| 409 | `ic_party_unmapped`、`ic_inventory_unmapped`、`ic_no_open_po` | 公司间接口：对照里缺往来单位编码、缺买方存货编码（`detail.codes`），或没有能覆盖的公司间采购订单（§33） |
| 422 | `ic_too_many_rows` | 公司间接口：某个账套的数据超过翻页上限，缩小日期或条件 |
| 429 | `rate_limited` | 本服务的频率或在途上限。与桥的 `busy` 不是一回事 |
| 502 | `internal` | 桥返回 500 |
| 502 | `bad_response` | 桥的错误响应不是可解析的 JSON、超过 64 KiB，或发生了重定向 |
| 503 | `unavailable` | 桥没有配置、连不上、健康检查失败，或桥拒绝了 API 的签名（两边密钥不一致） |
| 503 | `u8_license_full` | 原样带上桥的码和 `message`，响应头 `Retry-After: 60` |
| 503 | `ia_timeout` | 原样带上桥的码和 `message`，响应头 `Retry-After: 60`，`retryable` 为 `true` |
| 504 | `outcome_unknown` | 请求已经送出但没有收到结果，或桥返回了无法解析的成功响应（含超过 8 MiB） |

其余码（`state_mismatch`、`u8_rejected`、`login_failed`、`feature_disabled`、`test_account_only`、`busy`、`busy_timeout`、`stopping`、`com_unavailable`、`u8_unavailable`、审批相关码等）状态与桥相同。

`Retry-After`（秒）：`u8_license_full`、`ia_timeout` 为 60；`busy`、`busy_timeout`、`stopping` 为 5；`write_policy_unavailable`、`u8_license_hold` 为 30，`write_frozen`、`write_window` 为 300，`write_quota` 取桥的 `detail.retry_after_seconds`（1 到 86400 的整数，否则 60）；本服务的 `rate_limited` 是频率窗口空出的秒数（至少 1），并发超限为 5；其余错误不带。桥本身不发这个头。

### 重试

| 情况 | 能否重试 |
| --- | --- |
| 连不上桥（API 503 `unavailable`、客户端 `connect_failed`） | 可以，请求没有送出 |
| 429 `busy` / `rate_limited`，503 `busy_timeout` / `stopping` | 可以，稍后再试；`rate_limited` 按 `Retry-After` |
| 503 `u8_license_full` | 可以，按 `Retry-After` 稍后再试；桥已自己重试过，不要立刻连发。请求没有执行，带幂等键的可以用同一个键重发 |
| 503 `write_policy_unavailable` / `write_frozen` / `write_window` / `u8_license_hold`，429 `write_quota` | 可以，按 `Retry-After` 稍后再试；写入没有执行，带幂等键的可以用同一个键重发 |
| 403 `write_not_allowed` / `operator_not_allowed` / `account_read_only` / `feature_disabled` / `test_account_only`，400 `write_limit` | 不要原样重发（要改请求或配置），不占幂等键 |
| 503 `ia_timeout` | 可以，按 `Retry-After` 稍后再试（同样的数据多半还会超时，先调大桥的 `iaCommandSeconds`）。事务已回滚，带幂等键的可以用同一个键重发 |
| 504 `outcome_unknown` | 不要盲目重试。写操作可能已经生效：带了幂等键的先用 `idempotency/get`（§20）查第一次的结果，再用 `load`、`list`、`gl/vouchers/load`、`archives/get` 核对，再决定 |
| 409 `state_mismatch`（审核回读对不上） | 不要重发，事务已经提交 |
| 502 `internal`（桥 500） | 不要重发，写入可能已落库（例如 U8 自行提交后回写核对不过），要人工核对 |
| 带幂等键的写请求（§20，全部写路由） | 可以用同一个键重发：成功的原样重放，不会重复执行；结果未知的原样重放 504，仍要先核对；4xx 不占用键，修正后可用同一个键 |

桥在 75 秒时给出结果。API 读桥的超时要大于这个值（缺省 90 秒），这样调用方先看到桥的结果，而不是自己先断开。
## 19. 只读报表 `reports/*`

报表路由只查数据库（读线程池），不调用 U8 组件，不写数据。参数在登录前全部校验，不合法返回 400 `bad_request`：整数必须是 JSON 整数，布尔必须是 `true`/`false`，未知字段一律拒绝。审计动作是 `report_<名称>`。

桥登录的子系统：

| 报表 | 子系统 |
| --- | --- |
| `close_status`、`bom`、`account_readiness`（§30）、`fa_changes`、`fa_depreciation`、`customer_credit` | SA |
| `gl_balance`、`gl_aux_balance`、`gl_detail` | GL |
| `arap_balance`、`arap_aging`、`arap_detail` | 按 `side`：AR 或 AP |
| `arap_writeoffs` | 按 `flag`：AR 或 AP |
| `order_execution` | 销售订单 SA，采购订单 PU |
| `doc_trace` | 同起点单据的 `vouchers/load` |
| `stock_ledger`、`stock_summary`、`position_stock`、`batch_stock` | ST |
| `price_list` | `kind=vendor` 为 PU，其余 SA |
| `opening_balance` | `stock` 为 ST，`arap` 按 `side` 为 AR 或 AP，`gl` 为 GL |

公共约定：

| 项 | 说明 |
| --- | --- |
| 会计年度 | 可选 `fiscal_year`（2000 到 2099），缺省取登录日期 `date` 的年份，与总账凭证相同。`year` 是账套库年度，不参与判断。响应原样带回 `fiscal_year`。库存与销售支持报表不带 `fiscal_year` |
| 金额 | JSON 数字，本币，两位小数，与 `gl/vouchers/load` 的 `debit`/`credit` 一致。数量、物料清单用量六位小数 |
| 翻页 | `limit` 加 `after`。`after` 填上一页的 `next`，是不透明字符串（1 到 512 个可见 ASCII 字符），不要解析；最后一页 `next` 为 null。`bom` 不分页 |
| 余额方向 | 余额按方向拆成借方、贷方两列（其中一列为 0），方向字段取 `借`、`贷`、`平` |
| 开销 | 带期初、合计的报表（`gl_detail`、`stock_summary`、`stock_ledger`、`arap_detail`）每一页都重新汇总（不缓存），翻页越多总开销越大；数据量大时缩小范围或加大 `limit` |

功能权限（任一即可，账套主管不受限）：

| 报表 | 功能 id |
| --- | --- |
| `close_status` | 总账「结账」`GL1512`、「反结账」`GL1520`、余额表 `GL030301`、明细账 `GL0305` |
| `gl_balance` | `GL030301`、`GL0305`、`GL030101` |
| `gl_aux_balance` | `GL030301`、`GL0305` |
| `gl_detail` | `GL0305` |
| `arap_balance` | 应收总账表 `AR060201_01` 或应收审核 `AR050104`（应付 `AP060201_01` / `AP050104`） |
| `arap_aging` | 应收账龄分析 `AR060301` 或 UAP 报表视图「查询:应收账龄分析」`AR[__]bdfd5d21-763b-4d7b-a6ae-eb3abb026f91_001_01`（应付 `AP060301` / `AP[__]5d20e375-da98-4d59-a5a5-345d9123f2d0_002_01`） |
| `arap_detail` | 应收明细账 `AR060202_01`、对账单 `AR060203_01`、总账表 `AR060201_01`（应付同理） |
| `arap_writeoffs` | 应收核销明细表 `AR060107`、手工核销 `AR050201`、选择收款 `AR0503`、取消操作 `AR0807`（应付 `AP060107`、`AP050201`、`AP0503`、`AP0807`） |
| `opening_balance` | 见「期初余额」 |
| `bom` | `BO01001Q` |
| `order_execution` | 销售订单 `SA03010104` / `SA03010201` 或采购订单 `PU0310`，再按 `type` 要求对应类型 |
| `doc_trace` | 登录即可，各节点按单据类型的读取规则 |
| `stock_ledger` | 打印 / 输出 `ST020102_03`、`ST020102_04` |
| `stock_summary` | `ST020301_01` |
| `position_stock` | 货位存量 `ST020107_01`、货位汇总表 `ST010812_01` |
| `batch_stock` | `ST020305_01`、`ST020107_01` |
| `customer_credit` | 信用余额表 `SA040410_01` |
| `price_list` | 路由接受任一：客户价格 `SA0312030101`、存货价格 `SA0312020101`、供应商存货价格 `PU060105` / `PU060106`；处理时再按 `kind` 要求对应的一组，缺了 403（如「没有客户价格表查询权限」） |
| `account_readiness` | 账套主管，或 `GL1512`、`GL0202` |
| `fa_changes` | 变动单查看 `FA1603` |
| `fa_depreciation` | 折旧清单查看 `FA2403`、折旧清单表 `FA18105` |

数据权限见 §22，各报表的过滤对象在各小节说明。

### `close_status` 月结状态

可选 `fiscal_year`。响应 `{"ok":true,"fiscal_year","modules","periods"}`。`modules` 固定为 `SA` 销售、`PU` 采购、`ST` 库存、`IA` 存货核算、`GL` 总账、`AR` 应收、`AP` 应付、`CA` 成本、`FA` 固定资产。`periods` 是该年度已建立的期间（1 到 12，升序），每项 `{"period","closed":{模块:布尔}}`；年度没有建立时为空数组，不返回 404。

### `gl_balance` 科目余额表

| 字段 | 说明 |
| --- | --- |
| `period_from`、`period_to` | 必填，1 到 12，`period_from` 不大于 `period_to` |
| `grade_from`、`grade_to` | 科目级次，1 到 9，缺省 1 和 9 |
| `code_prefix` | 科目编码前缀，1 到 40 位数字、字母、点或短横 |
| `leaf_only` | 只要末级科目，缺省 false |
| `include_unposted` | 含未记账凭证（不含作废），缺省 false。未记账凭证汇总到上级科目：`period_from` 之前的计入期初，区间内的计入本期，累计和期末都含。只有未记账凭证、还没有科目总账记录的科目也列出 |
| `nonzero` | 去掉期初、本期、累计、期末全为 0 的行，缺省 false |
| `after`、`limit` | 翻页，`limit` 1 到 1000，缺省 200 |

每项：`code`、`name`、`grade`、`leaf`、`class`（科目类型）、`natural_dir`（科目性质 `借`/`贷`）、`open_dir`、`open_debit`、`open_credit`、`period_debit`、`period_credit`、`ytd_debit`、`ytd_credit`、`close_dir`、`close_debit`、`close_credit`。按科目编码排序。期初是 `period_from` 月初，期末是 `period_to` 月末（期末 = 期初 + 本期借 − 本期贷），累计是 1 月到 `period_to`。上级科目已包含下级，不要跨级相加。

数据权限：按科目，只在总账选项「明细账查询权限控制到科目」打开时生效。

### `gl_aux_balance` 辅助核算余额表

必填 `dim`（`customer`、`vendor`、`dept`、`person`、`project`）、`period_from`、`period_to`；可选 `fiscal_year`、`code_prefix`、`dim_code`（维度编码等于，1 到 60 个字符）、`project_class`（项目大类，1 到 20 位字母或数字，只能和 `dim=project` 一起用）、`nonzero`、`after`、`limit`。只含已记账凭证，没有 `include_unposted`。

每项：`code`、`name`（科目）、`dim_code`、`dim_name`、`project_class`，以及与 `gl_balance` 相同的期初、本期、累计、期末列。按科目、项目大类、维度编码排序。项目的 `dim_name` 为 null（项目名称分散在各大类的表里）；编码已不存在时也为 null。

数据权限：按 `GL_accass` 原始行的科目与各辅助项过滤。

### `arap_balance` 往来余额

| 字段 | 说明 |
| --- | --- |
| `side` | 必填。`ar` 应收（按客户），`ap` 应付（按供应商） |
| `as_of` | 截止日期 `yyyy-MM-dd`，按登记日期含当天。缺省为 `date` |
| `accounts` | 控制科目编码前缀，1 到 20 个。缺省应收 `["1122"]`，应付 `["2202"]` |
| `exclude_accounts` | 要排除的前缀，最多 20 个（例如暂估科目） |
| `partner` | 客户或供应商编码等于 |
| `nonzero` | 去掉余额为 0 的往来单位，缺省 **true** |
| `after`、`limit` | 翻页，`limit` 1 到 1000，缺省 200 |

每项 `partner`、`name`、`debit`、`credit`（截至 `as_of` 的累计）、`balance`。应收 `balance` = 借 − 贷（正数是客户欠我方），应付 `balance` = 贷 − 借（正数是我方欠供应商）。不含应收票据和现金类记录。按往来单位编码排序。数据权限按客户（应付为供应商）。

### `arap_aging` 账龄分析

字段同 `arap_balance`，另有：

| 字段 | 说明 |
| --- | --- |
| `basis` | `document` 按单据日期（缺省）；`due` 按到期日，见下文 |
| `buckets` | 账龄区间上限天数，1 到 10 个严格递增的整数，每个 1 到 3650。缺省 `[30,60,90,180,365]`。逾期催收常用 `basis=due` 加 `[30,60,90]` |
| `group_by` | `partner` 按往来单位（缺省）；`person` 按业务员；`partner_person` 按往来单位 + 业务员 |
| `person` | 业务员编码，1 到 20 个（每个 1 到 20 个字符），按解析出的业务员过滤，任何分组都可用 |
| `overdue_only` | 只留 `overdue` 大于 0 **且** `balance` 大于 0 的行（净额仍欠），缺省 false。只能和 `basis=due` 一起用，否则 400 |
| `default_credit_days` | 缺省信用天数，0 到 3650，缺省 0。只能和 `basis=due` 一起用，否则 400（`field` 为 `default_credit_days`）。给了时响应原样带回 |

到期日（`basis=due`）：取明细行（`Ar_Detail` / `Ap_Detail`）的收款（付款）日期 `dGatheringDate`；没有时，信用期 `iCreditPeriod` 不为 0 的取信用起算日 `dCreditStart`（没有取单据日期）+ 信用期，不加 `default_credit_days`；信用期为 0 或空的取起算日 + `default_credit_days`。`default_credit_days` 为 0 时结果与不给相同。客户、供应商信用期为 0 且未填收款日期时，到期日落在单据日期、`overdue` 等于全部余额；这时用 `default_credit_days`（例如 30）按统一账期计算逾期。

业务员：取原单据明细上的业务员 `cPerson`（只看原单自己的行，核销行上的不用）；原单没有时取客户（供应商）档案的专管业务员 `cCusPPerson` / `cVenPPerson`；都没有时归到业务员为空的一组。未核销的收付款单本身就是原单，按自己的业务员归组。

响应另有 `basis`、`group_by`、`buckets`（`[{"key","from","to"}]`：第一项 `not_due`（账龄 ≤ 0），然后每个区间，最后一项没有上限，共 `buckets` 个数加 2 项）。每项先是分组键：`partner` 分组为 `partner`、`name`；`person` 分组为 `person_code`、`person_name`、`person_source`（`document` 全部取自单据，`customer` / `vendor` 全部取自档案，`mixed` 两者都有；业务员为空的一组三项都是 null）；`partner_person` 两组都有。之后是 `balance`、`aging`（与 `buckets` 一一对应的金额）、`prepaid`（未核销的预收或预付，正数），`basis=due` 时另有 `overdue`（`aging` 除 `not_due` 外的合计）。`balance` = `aging` 合计 − `prepaid`。

- 排序按分组键，业务员为空的一组在最前。`after` 只能用同一 `group_by` 的上一页 `next`，混用 400。
- 账龄天数 = `as_of` − 起算日期。应收先把核销分摊回原单据再算账龄，应付按明细行算。坏账收回（`cProcStyle=9H`）等处理行与其他行一样计入余额和账龄。
- **逾期是毛额**：`overdue` 只合计单据逾期的部分，未核销的收付款不冲抵，单列在 `prepaid`；一个单位可能 `overdue` 大于 0 而 `balance` 小于等于 0。按业务员分组时，单据有业务员而收付款单没有的单位会拆成两行（业务员一行正的逾期，空业务员一行负的余额）。
- 同一条件下（不带 `overdue_only`）各分组方式的 `balance`、`prepaid` 合计相同；`nonzero` 去掉的是余额和预收付都为 0 的行（区间内可能正负相抵），所以各区间和 `overdue` 的合计在不同分组下可能略有出入。
- 数据权限：客户（供应商）按明细的往来单位过滤，不看应收 / 应付选项的「启用客户（供应商）权限」开关，只要该对象受控就过滤（从严）。业务员只在应收 / 应付选项「是否启用业务员权限」（`AccInformation.PersonAuthCtrl`）打开且人员受控时过滤，按解析后的业务员判断，没有业务员的单据放行；打开后按往来单位分组的金额也只含有权限的业务员的单据。选项关闭时 U8 账龄表可能比本报表多出行，不会少。
- 未与 U8 客户端账龄表逐项核对的情形：坏账收回行、上述权限选项关闭时。

### `bom` 物料清单

| 字段 | 说明 |
| --- | --- |
| `parent` | 必填，母件存货编码 |
| `as_of` | 生效日期，缺省为 `date`。只取主 BOM（`BomType=1`）当天有效的已审核版本；同一存货有多个物料（自由项不同）时先取不带自由项的，再取最高版本；子件取当天有效的 |
| `levels` | 展开层数 1 到 10，缺省 1 |
| `limit` | 最多返回行数 1 到 5000，缺省 1000。超出时 `truncated` 为 true，不翻页 |

响应 `{"ok":true,"parent","as_of","levels","bom_id","version","items","truncated"}`，`version` 是整数版本号。每项 `level`、`parent`（这一层的母件）、`component`、`name`、`spec`、`sort`、`qty_n`、`qty_d`（基本用量的分子、分母）、`qty`（每 1 个根母件累计需要的数量，全精度相乘后保留六位小数，不含损耗率；分母为 0 时为 null）、`path`。按层级、母件、序号、子件排序。已在路径上的子件不再展开（防环）；每个母件只查一次。该日期没有已审核的物料清单时 404 `not_found`。数据权限：母件存货不在授权内 403，不在授权内的子件不列出、也不展开。

### `arap_detail` 往来明细账

数据源同 `arap_balance`（应收 / 应付明细，不含应收票据和现金类记录）。

| 字段 | 说明 |
| --- | --- |
| `side` | 必填。`ar` 应收（按客户），`ap` 应付（按供应商） |
| `partner` | 必填。往来单位编码，或 1 到 20 个编码的数组 |
| `date_from` | 必填，起始日期（含）。期初是这一天之前的累计 |
| `date_to` | 截止日期（含），缺省为 `date`，不能早于 `date_from` |
| `basis` | 日期口径，只收 `register` 登记日期（缺省，与 `arap_balance` 的 `as_of` 相同）；其他值 400 |
| `accounts`、`exclude_accounts` | 同 `arap_balance` |
| `dept`、`person` | 明细行上的部门、业务员编码等于（1 到 20 个字符），期初也只算这些行 |
| `include_writeoff` | 在 `items` 里列出核销行（`cProcStyle=9P`），缺省 false。只决定输出哪些行：期初、借贷合计、期末和滚动余额一律含核销行。不列时相邻两行的余额差可能不等于后一行的借贷；游标到本页最后一行之间被隐去的核销行多于 20000 行时 400，请缩小范围或列出核销行 |
| `after`、`limit` | 翻页，`limit` 1 到 1000，缺省 200 |

响应 `{"ok":true,"side","basis","date_from","date_to","partners","items","next"}`。

- `partners` 按编码排序，每个往来单位一项：`partner`、`name`、`opening`（`date_from` 之前的余额）、`debit`、`credit`（区间合计）、`closing`；区间及之前都没有记录的单位不列，每页都给全部单位。`closing` 等于同一 `as_of`、同一科目条件下 `arap_balance` 的 `balance`。
- `items` 按往来单位、口径日期、登记顺序排序，一行是同一天、同一单据、同一处理方式、同一对方单据的明细合计（发票多行合成一行）：`partner`、`date`（口径日期）、`reg_date`、`doc_date`（组内最早）、`doc_type`（`cVouchType`）、`doc_type_name`（应收 `26`/`27` 销售专用 / 普通发票、`R0` 应收单，应付 `01`/`02` 采购专用 / 普通发票、`P0` 应付单，两边 `48` 收款单、`49` 付款单；其他为 null）、`doc_code`、`proc_style`、`co_doc_type`、`co_doc_code`、`digest`、`account`（多个科目时为 null）、`dept`、`person`、`debit`、`credit`、`balance`。
- 滚动余额方向同 `arap_balance`，每个往来单位从 `opening` 起滚动；翻页后桥按游标重算前面的发生额继续滚动。

数据权限按客户（供应商），期初、合计和明细一致；没有权限的单位不出现。

### `gl_detail` 科目明细账

| 字段 | 说明 |
| --- | --- |
| `code` | 必填，科目编码。该年度没有这个科目 404 `not_found` |
| `include_sub` | 缺省 true：含下级科目（编码以 `code` 开头的末级科目）；false 只查 `code` 本身（只对末级科目有意义） |
| `period_from`、`period_to` | 期间 1 到 12，与日期二选一。年度同 `gl_balance` |
| `date_from`、`date_to` | 日期（含），与期间二选一，两个都要给，必须在同一年度；带 `fiscal_year` 时须与日期的年份一致 |
| `include_unposted` | 含未记账凭证（不含作废），缺省 false。起始期间之前的计入期初，区间内的列为明细（`posted` 为 false） |
| `customer`、`vendor`、`dept`、`person`、`project`、`project_class` | 辅助核算条件（等于）；`project_class` 只能和 `project` 一起用 |
| `after`、`limit` | 翻页，`limit` 1 到 1000，缺省 200 |

响应顶层：`fiscal_year`、`code`、`name`、`leaf`、`include_sub`、`include_unposted`、`period_from`、`period_to`（按日期时是日期的月份）、`date_from`、`date_to`（按期间时为 null），整个区间的 `open_dir`、`open_debit`、`open_credit`、`total_debit`、`total_credit`、`close_dir`、`close_debit`、`close_credit`（每页相同），以及 `items`、`next`。`items` 按期间、日期、凭证类别、凭证号、分录号排序，列名同 `gl/vouchers/load` 的分录：`period`、`date`、`sign`、`no`、`entry`、`digest`、`account`、`account_name`、`debit`、`credit`、`posted`、`dept`、`person`、`customer`、`supplier`、`item_class`、`item`，另有滚动余额 `dir` 和 `balance`（绝对值），翻页后继续滚动。

期初规则同 `gl_balance`：起始期间的年初余额按末级科目合计，不带辅助核算的末级科目取科目总账，带辅助核算的取辅助总账，所以辅助项条件对期初同样生效。按日期查询时，起始期间里 `date_from` 之前的凭证也计入期初。不带辅助项条件、按期间查询时，期初、合计、期末与同期 `gl_balance` 的该科目一致。

数据权限：凭证分录和辅助总账按科目与各辅助项过滤；科目总账只按科目过滤。科目只在「明细账查询权限控制到科目」打开时受控。

客户端命令：`report-close-status`、`report-gl-balance`、`report-gl-aux`、`report-gl-detail`（`--period-from`/`--period-to` 或 `--date-from`/`--date-to`，`--exact` 不含下级科目，`--include-unposted`，`--customer`、`--vendor`、`--dept`、`--person`、`--project`、`--project-class`）、`report-arap-balance`、`report-arap-aging`（`--buckets 30,60,90`、`--group-by person`、`--person` 可重复、`--overdue-only`、`--default-credit-days 30`）、`report-arap-detail`（`--partner` 可重复、`--include-writeoff`）、`report-bom`。往来报表的 `--account`、`--exclude-account` 可重复，`--include-zero` 保留余额为 0 的单位。

### `order_execution` 订单执行

逐行列出订单的数量、金额和执行进度。执行数取 U8 订单行上的累计列（由 U8 回写），桥不另外汇总下游单据。

| 字段 | 说明 |
| --- | --- |
| `type` | 必填。`sale_order` 销售订单，`purchase_order` 采购订单 |
| `ids` | 订单 id（表头主键），1 到 100 个正整数。不能和 `code`、`date_from`、`date_to`、`partner` 一起用 |
| `code` | 订单号等于，1 到 30 个字符 |
| `date_from`、`date_to` | 订单日期范围（含两端），`date_from` 不能晚于 `date_to` |
| `partner` | 客户（销售）或供应商（采购）编码等于，1 到 20 个字符 |
| `only_open` | 只要未执行完且未关闭的行，缺省 false |
| `after`、`limit` | 翻页，`limit` 1 到 1000，缺省 200 |

响应 `{"ok":true,"type","only_open","items","next"}`。`items` 按订单 id、行 id 排序，每行一项：`id`、`code`、`date`、`partner`、`partner_name`、`currency`、`verified`、`closed`（订单或该行已关闭）、`open`、`line_id`、`row_no`、`inv_code`、`inv_name`、`inv_std`、`due_date`（销售为预发货日期，采购为计划到货日期）、`qty`、`amount`（原币价税合计）、`nat_amount`（本币价税合计），以及执行数：

| 销售订单 | U8 列 | 采购订单 | U8 列 |
| --- | --- | --- | --- |
| `shipped_qty`、`shipped_amount` 累计发货 | `iFHQuantity`、`iFHMoney` | `arrived_qty`、`arrived_amount` 累计到货 | `iArrQTY`、`iArrMoney` |
| `out_qty` 累计出库 | `foutquantity` | `in_qty` 累计入库 | `iReceivedQTY` + `freceivedqty` |
| `invoiced_qty`、`invoiced_amount` 累计开票 | `iKPQuantity`、`iKPMoney` | `invoiced_qty`、`invoiced_amount` 累计开票 | `iInvQTY`、`iInvMoney` |
| `returned_qty` 累计退货 | `fretquantity` | `returned_qty` 累计退货 | `fPoRetQuantity` |
| `received_amount`、`received_nat_amount` 累计收款（原币、本币） | `iexchsum`、`imoneysum` | `paid_amount`、`paid_nat_amount` 累计付款（原币、本币） | `iOriTotal`、`iTotal` |

- U8 没有值时为 0。发货、出库、开票是扣掉退货（红字）后的净数。
- 采购入库：直接参照订单入库记在 `iReceivedQTY`，经到货单（及来料检验）入库记在 `freceivedqty`，`in_qty` 是两者之和。
- 收付款列随发票核销回写，可能超过订单金额（例如开票金额大于订单金额）；预收款按订单核销时同样回写。
- `only_open`：订单和该行都未关闭（采购订单表头 `cState=2` 也算关闭），且销售的累计发货、出库、开票（采购的累计入库、开票）至少一项的绝对值小于订单数量。未审核的订单也算。

数据权限同该订单类型的 `vouchers/list`：记录级条件（客户、供应商、部门、业务员、销售 / 采购类型、表体存货）加在订单表头上，整张订单要么全列要么不列。

### `doc_trace` 单据追溯

从一张单据出发列出上游（来源）和下游（去向）单据。

| 字段 | 说明 |
| --- | --- |
| `type` | 必填，起点单据类型（节点类型之一） |
| `id` | 必填，起点单据 id（表头主键） |
| `depth` | 每个方向最多几跳，1 到 3，缺省 3 |
| `direction` | `both`（缺省）、`up` 只找上游、`down` 只找下游 |
| `max_nodes` | 最多返回的节点数（含起点），1 到 200，缺省 200 |

关联（上游 → 下游，括号里是关联列）：

| 链路 | 关联 |
| --- | --- |
| 销售 | 销售订单 → 发货单（发货行 `iSOsID`）→ 销售出库单（`iDLsID`）、销售发票（`iDLsID`）→ 收款单；发货单 → 退货单（退货行 `iCorID` = 原发货行）→ 销售出库单（红字）、销售发票（红字）；没有原发货行的退货单、没有发货行的发票直接挂订单行 |
| 退货申请 | 发货单（蓝字）→ 退货申请单（申请行 `iDLsID` = 发货行）→ 退货单（退货行 `irtnappid` = 申请行 `AutoID` 且 `crtnappcode` = 申请单号） |
| 采购 | 请购单 → 采购订单（`iAppIds`）→ 到货单（`iPOsID`）→ 来料报检单（`SOURCEAUTOID`，只认 `CSOURCE=到货单`）→ 来料检验单（`INSPECTAUTOID`）→ 采购入库单（`iCheckIdBaks`）→ 采购发票（`RdsId`）→ 付款单；来料检验单 → 来料不良品处理单（`CHECKID`）；到货单 → 采购退货单（`iCorId`）→ 采购入库单（红字）。没有检验单的入库挂到货单（`iArrsId`），没有到货单的入库、没有入库行的发票挂订单行 |
| 生产 | 物料清单 → 生产订单（`BomId`）→ 材料出库单（`iMPoIds` = 子件 `AllocateId`）、产成品入库单（`iMPoIds` = 订单行）、产品报检单（`SOURCEAUTOID`，只认 `CSOURCE=生产订单`）→ 产品检验单 → 产品不良品处理单（`CHECKID`）→ 产成品入库单（`iRejectIds`）；产品检验单 → 产成品入库单（`iCheckIdBaks`，不含参照不良品处理单的行） |
| 其他质检 | 其他报检单 → 其他检验单（检验单 `INSPECTAUTOID` = 报检单表体 `AUTOID`）。两者都没有出入库来源 |
| 收付款 | 销售发票 → 收款单、采购发票 → 付款单：按核销明细（`Ar_Detail` / `Ap_Detail` 中收款单 48 / 付款单 49 核销发票的行，`cProcStyle=9P`）关联。核销应收单 / 应付单、预收预付、红票对冲不在图里 |

节点类型（28 种）：`sale_order`、`dispatch`、`sale_return`、`sale_return_apply`、`sale_out`、`sale_invoice`、`ar_receipt`、`ar_refund`、`purchase_requisition`、`purchase_order`、`arrival`、`purchase_return`、`purchase_in`、`purchase_invoice`、`ap_payment`、`ap_refund`、`qm_incoming_inspect`、`qm_product_inspect`、`qm_incoming_check`、`qm_product_check`、`qm_incoming_reject`、`qm_product_reject`、`qm_other_inspect`、`qm_other_check`、`production_order`、`material_out`、`product_in`、`bom`。`ar_refund`（客户退款）、`ap_refund`（供应商退款）只能作为起点（没有边）。

响应 `{"ok":true,"type","id","depth","direction","nodes","edges","omitted","truncated"}`。`nodes` 第一项是起点，每项 `type`、`id`、`code`（物料清单是母件存货编码）、`date`、`state`（`unverified`、`verified`、`closed`；生产订单全部行关闭为 `closed`、全部行已下达为 `verified`）、`level`（起点 0，下游第 n 跳为 n，上游为 −n）。`edges` 每项 `from_type`、`from_id`、`to_type`、`to_id`（总是上游指向下游）、`lines`（关联的明细行数，收付款为核销记录数）；只列两端都在 `nodes` 里的边。

- 上游找到的单据只继续往上游找，下游同理，不会从下游单据绕回别的订单。
- 节点数到 `max_nodes` 时两个方向都停止并标 `truncated`；某条边一次查出的关联行超过 20000 时只停这个方向（也标 `truncated`）。
- 数据权限同 `vouchers/load`：起点单据不存在 404 `not_found`，越权 403 `no_permission`。其余单据按各自类型的读取规则判断，不可见的不列出、也不从它往下找，只计入 `omitted`（去重后的张数）。

客户端命令：`report-order-exec`（`--type`，`--id` 可重复，`--code`、`--date-from`、`--date-to`、`--partner`，`--only-open`）、`report-doc-trace`（`--type`、`--id`，`--depth`、`--direction`、`--max-nodes`）。

### 库存与销售支持报表

收发记录的来源（`stock_ledger`、`stock_summary` 共用）：采购入库 01、其他入库 08、其他出库 09、产成品入库 10、材料出库 11、销售出库 32、库存期初 34 七类单据的表头 + 表体（`RdRecord*` / `rdrecords*`）。调拨、盘点、形态转换在 U8 里生成其他入库 / 其他出库，不另算。缺省只算已审核（表头 `cHandler` 非空）的单据，此时按（仓库、存货）合计与现存量 `CurrentStock.iQuantity` 一致；`include_unverified` 为 true 时连未审核的一起算（期初也含）。数量按单据原数，红字入库是负的入库、红字出库是负的出库。

数据权限：四张库存报表按仓库、存货过滤（同 `stock/current`），`stock_ledger` 的存货不在授权内 403；`customer_credit` 按客户；`price_list` 按客户、供应商（可空）和存货。

### `stock_ledger` 库存台账

| 字段 | 说明 |
| --- | --- |
| `inv` | 必填，存货编码。不存在 404 `not_found` |
| `date_from` | 必填，`yyyy-MM-dd`（含）。期初是这一天之前的累计 |
| `date_to` | 截止日期（含），缺省为 `date`；早于 `date_from` 时 400 |
| `wh`、`batch` | 仓库、批号等于。不给仓库时全部仓库合在一起滚动结存 |
| `include_unverified` | 含未审核单据，缺省 false |
| `after`、`limit` | 翻页，`limit` 1 到 1000，缺省 200 |

响应 `{"ok":true,"inv","inv_name","inv_std","wh","batch","date_from","date_to","include_unverified","opening","carry","closing","items","next"}`。`opening` 是 `date_from` 之前的结存；`carry` 是本页第一行之前的结存（第一页等于 `opening`，之后由桥按游标重算）；`closing` 只在最后一页给出，其余页为 null。每项 `date`、`type`（`purchase_in`、`other_in`、`other_out`、`product_in`、`material_out`、`sale_out`、`stock_opening`）、`id`、`line_id`（表体 `AutoID`）、`code`、`wh_code`、`wh_name`、`batch`、`rd_code`（收发类别）、`source`（来源，如 `调拨`、`生产订单`）、`verified`、`in_qty`、`out_qty`、`balance`（本行之后的结存）。按日期、表体 `AutoID` 排序（七类表体共用一个号段，`AutoID` 不重复）。

### `stock_summary` 收发存汇总表

必填 `date_from`；可选 `date_to`、`wh`、`inv`、`inv_class`（存货分类编码前缀，含下级）、`by_wh`（缺省 true 按存货 + 仓库分行，false 每个存货一行）、`include_unverified`、`nonzero`（缺省 true，去掉期初、入库、出库都为 0 的行）、`after`、`limit`（1 到 1000，缺省 200）。

每项 `inv_code`、`inv_name`、`inv_std`、`inv_class`、`wh_code`、`wh_name`（`by_wh=false` 时为 null）、`opening`、`in_qty`、`out_qty`、`closing`（= 期初 + 入 − 出）。按存货、仓库编码排序。只有数量，不给金额（出库成本在存货核算记账后才有）。`date_to` 为今天时 `closing` 等于现存量。

### `position_stock` 货位存量

可选 `wh`、`inv`、`batch`、`position`（货位编码前缀，含下级）、`nonzero`（缺省 true）、`after`、`limit`（1 到 1000，缺省 200）。来源 `InvPositionSum`，按其主键翻页。每项 `id`、`wh_code`、`wh_name`、`position`、`position_name`、`inv_code`、`inv_name`、`inv_std`、`batch`、`free1` 到 `free10`、`qty`、`qty_aux`、`made_date`、`valid_until`、`expires`。只含已指定货位的数量：入库后尚未指定货位的部分只在现存量里，所以货位合计可能小于现存量。

### `batch_stock` 批次存量

可选 `wh`、`inv`、`batch`、`expiring_before`（只要失效日期不晚于这一天的批次，用于保质期预警；没有失效日期的批次不列）、`nonzero`（缺省 true）、`after`、`limit`。来源 `CurrentStock`，只取有批号的行，按（仓库、存货、批号）合并自由项不同的行。每项 `wh_code`、`wh_name`、`inv_code`、`inv_name`、`inv_std`、`batch`、`qty`、`qty_aux`、`qty_frozen`、`made_date`、`valid_until`、`expires`（多行时取最早）、`rows`（合并的现存量行数）。按仓库、存货、批号排序。可用量等逐行口径用 `stock/current`（可按 `batch` 过滤）。

### `customer_credit` 客户信用

可选 `customer`（1 到 20 个客户编码）、`controlled_only`（只列档案上勾了信用额度控制 `bCredit` 的客户）、`after`、`limit`（1 到 200，缺省 100）。按客户编码翻页。

各项占用与 U8 信用余额表（`Sa_saleCreReport` 及 `CreditSoForReport`、`CreditDLForReport`、`CreditBillForReport`、视图 `Ap_CreditDetail`）口径一致，本币价税合计：

| 键 | 内容 |
| --- | --- |
| `order` | 未执行完的销售订单：未关闭行按未发货数量（直运销售按未开票）折算，数量为 0 的行按金额 |
| `dispatch` | 未开票的发货单：需开票、未结算完的行扣掉已开票和退货 |
| `invoice` | 销售已开、应收未审核的销售发票（扣现结） |
| `ar` | 应收账款余额（`Ap_CreditDetail`：应收明细 `iFlag<2`，检查点为保存时另加未审核的应收单、收款单） |
| `expense` | 代垫费用单 |

信用检查点（销售选项 `bCrCheckWhen`）为「保存」时各项都含未审核单据，为「审核」时只含已审核的，订单和发货单改用 U8 的已审核累计列（`fVeriDispQty`、`fVeriBillQty` 等）。订单一项按「信用余额控制用余额表」（`bUseBanlaceTable`）打开时的口径；关闭时的 U8 算法未覆盖。

响应另有 `credit_control`（销售选项「是否有客户信用额度控制」）、`check_point`（`save` / `verify`）、`balance_table`、`ar_enabled`（应收款管理已启用）、`formula`（额度检查公式 `cCrCheckFunction` 各项是否打开：`order`、`dispatch`、`invoice`、`ar`、`expense`、`contract`、`export_order`、`export_consignment`、`export_invoice`）、`unsupported`（公式里打开了、本报表不计算的合同结算单和出口单据）。每项 `code`、`name`、`controlled`、`credit_line`（`iCusCreLine`）、`credit_days`（`iCusCreDate`）、`credit_days_controlled`（`bCreditDate`）、`credit_grade`（`cCusCreGrade`）、`credit_company`（信用单位）、上表五项、`used`（公式里打开的项之和，`ar` 另要求应收已启用）、`available`（受控客户为 `credit_line − used`，否则 null）。

- U8 的信用检查按信用单位合并同一 `credit_company` 下全部客户的占用；本报表按客户逐个列出，需要时按 `credit_company` 自行相加。
- 本报表是实时口径。U8 保存销售订单时的信用检查（经 `vouchers/create` 保存时同样生效，超额 409「信用检查不通过」）依赖信用余额表 `SA_CreditSum`：余额表未重算时 U8 只拿本单金额与额度比较，不计已有的未开票发货单，此时两者结果不一致。
- 未与 U8 客户端「信用余额表」逐项核对。

### `price_list` 价格表

| 字段 | 说明 |
| --- | --- |
| `kind` | 必填。`customer` 客户价格表（`SA_CusUPrice`）、`inventory` 存货价格表（`SA_InvUPrice`）、`vendor` 供应商存货价格表（`Ven_Inv_Price`） |
| `customer` | 客户编码，只能和 `kind=customer` 一起用；连同该客户所属客户分类的价格行 |
| `vendor` | 供应商编码，只能和 `kind=vendor` 一起用 |
| `inv` | 存货编码等于 |
| `as_of` | 生效日期，缺省 `date`：只列这一天有效（生效日期不晚于、失效日期为空或不早于这一天）、未标失效的行 |
| `all_dates` | true 时不按日期过滤，不能和 `as_of` 一起用 |
| `after`、`limit` | 翻页，`limit` 1 到 1000，缺省 200 |

响应 `{"ok":true,"kind","as_of","items","next"}`（`all_dates` 时 `as_of` 为 null）。每项 `id`、`inv_code`、`inv_name`、`inv_std`、`currency`、`start_date`、`end_date`、`min_qty`（数量下限）、`tax_included`、`promotion`、`memo`；`customer` 另有 `customer`、`customer_class`、`invalid`、`quote`（`iInvSCost`）、`discount_rate`（`iCusDisRate`）、`price`（`iInvNowCost`）、`min_price`（`fcusminprice`）；`inventory` 另有 `invalid`、`levels`（`[{"level","price","tax_price"}]`，`iUPrice1`–`10` 无税、`ISalePrice1`–`10` 含税，两者都空的级别不列）；`vendor` 另有 `vendor`、`max_qty`、`price`（无税）、`tax_price`、`tax_rate`、`supply_type`。按表主键排序。

客户端命令：`report-stock-ledger`（`--inv`、`--date-from`，`--wh`、`--batch`、`--include-unverified`）、`report-stock-summary`（`--date-from`，`--inv-class`、`--no-wh`、`--include-zero`）、`report-position-stock`（`--position`）、`report-batch-stock`（`--expiring-before`）、`report-customer-credit`（`--customer` 可重复、`--controlled-only`）、`report-price-list`（`--kind`，`--customer`、`--vendor`、`--inv`、`--as-of`、`--all-dates`）。

### `opening_balance` 期初余额

只读。期初的录入、修改不经本接口；采购、存货核算的期初记账、取消记账用 `openings/post`（§29），其他模块的期初记账不支持（见 `docs/limitations.md`）。

| 字段 | 说明 |
| --- | --- |
| `module` | 必填。`stock` 库存期初，`arap` 应收或应付期初，`gl` 总账期初余额 |
| `side` | `module=arap` 时必填：`ar` 应收（按客户），`ap` 应付（按供应商） |
| `fiscal_year` | 只用于 `gl`：会计年度，缺省为 `date` 的年份，不能早于总账启用年度（400） |
| `wh`、`inv`、`batch` | 只用于 `stock`：仓库、存货、批号等于 |
| `partner` | 只用于 `arap`：客户或供应商编码等于 |
| `code_prefix` | 只用于 `arap`、`gl`：科目编码前缀 |
| `leaf_only` | 只用于 `gl`：只要末级科目 |
| `dim` | 只用于 `gl`：按辅助项列期初（`customer`、`vendor`、`dept`、`person`、`project`），缺省按科目 |
| `nonzero` | 缺省 true：去掉期初为 0 的行 |
| `after`、`limit` | 翻页，`limit` 1 到 1000，缺省 200 |

带了别的模块的字段 400（例如 `module=stock` 带 `code_prefix`）。

响应公共字段：`module`、`side`（`arap` 以外为 null）、`start_date`（模块启用日期，`AccInformation` 的 `dSTStartDate` / `dARStartDate` / `dAPStartDate` / `dGLStartDate`，未启用为 null）、`opening_year`（库存、往来是启用年度，总账是 `fiscal_year`）、`posted`（期初是否已记账：`GL_mend` 在该年度第 0 期的 `bflag_ST` / `bflag_AR` / `bflag_AP` / `bflag`；已记账的期初在 U8 里不能再改），以及 `items`、`next`。

- `stock`：库存期初单（单据类型 34，`rdrecord34` / `rdrecords34`，卡片 `0319`），按仓库、存货、批号汇总：`wh_code`、`wh_name`、`inv_code`、`inv_name`、`inv_std`、`batch`（空为 null）、`qty`、`qty_aux`（件数）、`amount`（表体 `iPrice` 合计）、`lines`（表体行数）、`unverified_lines`（表头未审核的行数）。数据权限按仓库、存货。
- `arap`：`Ap_Vouch` 上 `bStartFlag=1` 的应收单 / 应付单（只有表头，金额为表头本币 `iAmount`，`bd_c` 为借贷方向），按往来单位、科目汇总：`partner_code`、`partner_name`、`account`、`account_name`、`debit`、`credit`、`balance_dir`、`balance_debit`、`balance_credit`、`docs`、`unverified_docs`。期初发票、期初收付款单、期初票据不在范围内。数据权限按客户或供应商。
- `gl`：期初是期初期间那一行的 `mb`（按 `cbegind_c` 定借贷；`GL_accsum` / `GL_accass` 没有第 0 期）。期初期间：启用年度是启用月份，其余年度是 1 月，响应 `period` 给出。按科目时每项 `code`、`name`、`grade`、`leaf`、`natural_dir`、`open_dir`、`open_debit`、`open_credit`、`pre_debit`、`pre_credit`（期初期间之前各期的借贷发生，即启用年度的「累计借方 / 贷方」），第一页另有 `trial`（末级科目期初借方合计 `debit`、贷方合计 `credit`、`difference`、`balanced`，即期初试算平衡）；按辅助项时每项 `code`、`name`、`dim_code`、`dim_name`、`project_class`、`open_dir`、`open_debit`、`open_credit`，没有 `trial`。数据权限同 `gl_balance` / `gl_aux_balance`。

功能权限：路由接受下列任一 id，桥再按 `module`（和 `side`、`dim`）要求对应的一组：库存 `ST000101`（期初结存）或 `ST020107_01`，应收 `AR0306` 或 `AR060201_01`，应付 `AP0306` 或 `AP060201_01`，总账 `GL010303`、`GL010304` 或 `GL030301`。

客户端命令：`report-opening-balance --module stock|arap|gl`（`--side`、`--fiscal-year`、`--wh`、`--inv`、`--batch`、`--partner`、`--code-prefix`、`--leaf-only`、`--dim`、`--include-zero`）。

### `arap_writeoffs` 核销记录

按核销号（`Ar_Detail` / `Ap_Detail` 的 `cCancelNo`，`cProcStyle=9P`，`HXAR…` / `HXAP…`）列出应收或应付的核销批次，包括本服务 `arap/writeoff` 和 U8 客户端做的核销。用于查找核销号、核对一次核销冲了哪些单据行，以及预先判断能否用 `arap/writeoff/cancel` 取消。

| 字段 | 说明 |
| --- | --- |
| `flag` | 必填。`AR` 应收（`Ar_Detail`），`AP` 应付（`Ap_Detail`） |
| `partner` | 客户或供应商编码等于（`cDwCode`） |
| `receipt` | `{"type","id"}`：只看这张收付款单的核销。`flag=AR` 时 `type` 只能是 `ar_receipt`，`AP` 时只能是 `ap_payment`；`id` 是 `Ap_CloseBill.iID`。与 `receipt_code` 二选一 |
| `receipt_code` | 收付款单号等于（`cVouchID`）。与 `receipt` 二选一 |
| `target` | `{"type","id"}`：只看核销了这张单据的批次。`flag=AR` 时 `sale_invoice` / `ar_bill`，`AP` 时 `purchase_invoice` / `ap_bill`；`id` 是 `SBVID`、`PBVID` 或 `Ap_Vouch.Auto_ID`。与 `target_code` 二选一 |
| `target_code` | 被核销单据的单号等于（`cCoVouchID`，不含收付款单自身的冲减行）。与 `target` 二选一 |
| `date_from`、`date_to` | 登记日期（`dRegDate`）区间，含两端 |
| `cancel_no` | 核销号等于，`HXAR` / `HXAP` 后接数字，前缀须与 `flag` 一致 |
| `after`、`limit` | 翻页，`limit` 1 到 200，缺省 50 |

找不到的收付款单或单据不报 404，结果为空。按登记日期降序、核销号降序。

响应 `{"ok":true,"flag","items","next"}`，每批一项：

| 字段 | 说明 |
| --- | --- |
| `cancel_no` | 核销号 |
| `date` | 登记日期（核销日期） |
| `year`、`period` | 期间是往来明细的 `iPeriod`；年度是登记日期在 `UFSYSTEM..UA_Period` 上所在的年度（找不到取日期的年份），与取消核销判断结账用的一致 |
| `partner`、`currency`、`operator` | 往来单位编码、币种名称、核销人（`cOperator`，U8 里是姓名） |
| `receipt` | 核销行所在的收付款单：`type`（`ar_receipt` / `ap_payment`；应收一侧的付款单为 `ar_refund`，应付一侧的收款单为 `ap_refund`）、`vouch_type`（`48` / `49`）、`id`（`Ap_CloseBill.iID`，找不到为 null）、`code`、`line_ids`（涉及的行 `Ap_CloseBills.ID`，取自自身冲减行的 `iCoClosesID`）、`line_id`（只涉及一行时即该行，否则 null） |
| `targets` | 被核销单据，按（类型、单号、行）合计：`type`（`sale_invoice`、`ar_bill`、`purchase_invoice`、`ap_bill`；对方是另一张收付款单时 `ar_receipt` / `ap_payment` / `ar_refund` / `ap_refund`；其余为 null）、`vouch_type`（`cCoVouchType` 原值）、`id`（找不到为 null）、`line_id`（发票行 `iBVid`，应收应付单整单核销为 null）、`code`、`amount` |
| `amount` | `targets` 的 `amount` 合计 |
| `gl_voucher`、`voucher` | 是否已制单（有行带 `cPZid`）；已制单时 `voucher` 为 `{"id","sign","no","date"}`（`cPZid`、`cGLSign`、`iGLno_id`、`dPZDate`），否则 null |
| `cancellable`、`reason` | 能否用 `arap/writeoff/cancel` 取消；不能时 `reason` 是取消核销会返回的原因，否则 null |

金额是原币、两位小数：应收取被核销单据一侧的贷方减借方（`iCAmount_f`），应付取借方减贷方（`iDAmount_f`），与取消核销加回的金额相同。

`cancellable` 与取消核销用同一套判断，顺序和消息相同：超过 500 行、不是一张收付款单对单据的核销、已制单、涉及合同、外币、跨期间、收付款单或单据找不到、采购发票被网络锁定、期间已结账、之后还有其他处理（同取消核销，见 §12）、应收一侧账套里有未审核的收款单。只读判断不加锁，结果是查询时刻的快照，取消时桥在事务里带锁重查。

数据权限按客户或供应商（`cDwCode`），越权的批次不列。

### `fa_changes` 固定资产变动单

列出固定资产变动单（`fa_Vouchers`：原值增加 / 减少、计提减值准备、部门转移、使用状况调整等），一张一行，按变动单号排序。U8 每做一次变动，卡片新增一个版本（`fa_Cards` 一行），变动单记录变动前后的值和两个版本号；卡片在某一天的状态用 `archives/get` 的 `fa_card`（§15）。

| 字段 | 说明 |
| --- | --- |
| `fiscal_year` | 固定资产的业务年度：按变动日期 `dTransdate` 的年份，缺省登录年度。不是账套库年度（请求的 `year`）：一个账套库里常有多个业务年度的固定资产数据 |
| `period` | 变动期间 `iTransPeriod`，1 到 12，缺省全年 |
| `card` | 卡片编号（`fa_card` 的 `code`） |
| `code` | 变动单号 |
| `change_type` | 变动单类型 `iVoucherType`（1 到 99，如 `1` 原值增加、`11` 计提减值准备），名称见响应的 `change_name` |
| `after`、`limit` | 翻页，`limit` 1 到 1000，缺省 200 |

响应 `{"ok":true,"fiscal_year","items","next"}`。每项 `code`、`card_code`、`asset_name`（变动后版本的名称）、`opt_id`（变动后的卡片版本 `lOptID`）、`pre_opt_id`（变动前版本）、`change_type`、`change_name`、`before_value`、`after_value`（变动前后内容，文本）、`reason`（最多 1000 字）、`change_date`、`period`、`operator`（经手人）、`currency`、`exchange_rate`、`site_after`、`keeper_after`、`effective`（当期生效 `bAct`）、`gl_sign`、`gl_num`（凭证类别字、凭证号，未制单为 null），以及 `depts`（`fa_Vouchers_Detail`：`dept_code`、`dept_name`、`before_value`、`after_value`）。没有固定资产的账套返回空列表。会计期间不按自然月划分的账套未实测。

### `fa_depreciation` 固定资产折旧

按卡片和期间列出已计提的折旧。U8 折旧表 `fa_DeprTransactions` 一张卡片一年一行、十二个月各一列，这里按该年度已计提的期间（`fa_DeprList`）展开，未计提的期间不出现；本年录入的卡片从录入期间起列，年中减少的卡片减少之后的期间不列。

| 字段 | 说明 |
| --- | --- |
| `fiscal_year` | 固定资产的业务年度（折旧表的 `iyear`），缺省登录年度；不是账套库年度 |
| `period` | 只要这一期，1 到 12，缺省全部已计提期间 |
| `card` | 卡片编号 |
| `nonzero` | true 时去掉当月折旧为 0 的行，缺省 false |
| `after`、`limit` | 翻页，`limit` 1 到 1000，缺省 200 |

响应 `{"ok":true,"fiscal_year","posted_periods","items","next"}`。`posted_periods` 是该年度已计提的期间（升序）。每项按卡片编号、期间排序：`card_code`、`asset_num`（`sDeprAssetNum`）、`asset_name`（卡片最新版本的名称）、`period`、`depr_date`、`amount`（当月折旧 `dblDepr<期间>`）、`accumulated`（当月末累计折旧 `dblDeprT<期间>`，与 `fa_card` 同期的 `accumulated_depreciation` 同源）、`rate`（月折旧率，六位小数）、`month_value`（月初原值）、`depr_months`（月末已计提月份）、`used_months`（月末已使用月份）。

`fa_changes`、`fa_depreciation` 的数据权限：部门受控、操作员不是账套主管也不是部门的数据权限管理员时，按卡片使用部门过滤——卡片全部版本的全部使用部门（`fa_DeptScale`）都在授权内、且至少有一个使用部门，卡片的行才出现（部门转移过的卡片要转出、转入两边都有权限）。变动单另要求部门明细上的非空部门都在授权内。越权的行不出现，照常 200。卡片档案 `fa_card` 的 `archives/list` 同样过滤，`archives/get`、`get_many` 对越权卡片 403「没有该档案的数据权限」（卡片不存在仍 404）。

## 20. 幂等键

写路由可以带幂等键：网络中断、超时之后用同一个键重发，U8 里只执行一次。**全部写路由**都支持（§3 表里标「写」的，含专用审核路由 `sale-orders/verify`、`dispatches/verify`）；读路由带幂等键 400「该接口不支持 Idempotency-Key」。写路由名单每层只有一份：桥是写闸门的写路由表，API 是权限表里的写动作，MCP 是 `routes.json` 里 `access` 为 `write` 的路由，客户端是 `WRITE_ROUTES`。

| 层 | 写法 |
| --- | --- |
| API | 请求头 `Idempotency-Key`，只能出现一次。API 把它转成桥请求体的 `idempotency_key`，并加上 `caller` = `<信任项名>:<客户端>`（超过 200 字符时换成 `sha256:<摘要>`） |
| 桥 | 请求体字段 `idempotency_key`，可选 `caller`（1 到 200 个字符，不含控制字符），都在签名的请求体里 |
| 客户端 | `client.keyed(键)` 返回带键的副本；`create_voucher`、`generate_voucher`、`gl_create`、`arc_create` 另有 `idempotency_key` 参数；命令行全部写命令都有 `--idempotency-key`（见 `docs/getting-started.md`） |

键是 1 到 128 个可见 ASCII 字符（`!` 到 `~`，不含空格）。不带键时没有幂等记录。

**记录的定位**是 `caller` + 账套 + 路由 + 键：不同调用方、账套、路由用同一个键互不影响。直接调桥不带 `caller` 时一律记为 `direct`，这些调用方共用一个命名空间；多个直接调用方并存时各自带不同的 `caller`，或给键加前缀。

**内容摘要**：除 `password_enc`、`date`、`idempotency_key`、`caller` 以外的整个请求体（含 `year`、`operator`），按键排序的规范 JSON 的 SHA-256。口令每次加密都不同，登录日期只决定登录上下文（API 缺省填当天，过零点重发也算同一请求），所以不参与比较；单据自己的日期在 `head` 里，照常参与。

| 同一个键再次到达时 | 桥的响应 |
| --- | --- |
| 内容摘要不同 | 409 `idempotency_mismatch`，不执行 |
| 第一次已成功 | 校验本次登录后原样返回第一次的 HTTP 状态和响应体，不再调用业务组件 |
| 第一次结果未知（500、504 `outcome_unknown`、除下一行以外的 503，或服务在执行中中断） | 同样校验登录后原样返回，不再调用 U8。先用 `load`、`list`、`gl/vouchers/load`、`archives/get` 核对 |
| 第一次以 4xx 结束（字段不合法、单据不存在、`state_mismatch`、`workflow_enabled`、`u8_rejected`、登录失败、无权限、`busy` 等），或 503 `busy_timeout`、`stopping`、`u8_license_full`、`u8_license_hold`、`ia_timeout`，以及写入策略拒绝的码（503 `write_policy_unavailable`、`write_frozen`、`write_window`，429 `write_quota`，§18） | 不占用键，记录删除；修正后可用同一个键重发，照常执行 |
| 第一次还在执行（同一进程） | 等第一次的结果，最多 75 秒：成功或结果未知则校验登录后同样返回；第一次没占用键则本次接着执行；仍在执行则 504 `outcome_unknown`。不会同时发起第二次 U8 调用 |

- 4xx 都发生在 U8 提交之前（提交之后的错误一律是 504 `outcome_unknown` 或 500），所以不保存。5xx 只有保证未执行的码（`busy`、`busy_timeout`、`stopping`、`u8_license_full`、`u8_license_hold`、`ia_timeout`、`write_policy_unavailable`、`write_frozen`、`write_window`）删除记录，其余 5xx（含 `com_unavailable`）按结果未知保存。
- **重放不绕过登录**：返回存下的响应之前，桥用本次的账套、年度、操作员、口令和登录日期确认能登录 U8（登录缓存命中即通过，否则在写线程池上做一次只登录的任务，同 `login-check`）。口令错误照常 422 `login_failed`。
- 非新建类写入重放时同样原样返回第一次的响应：例如审核成功后用同一个键重发，得到第一次的 200，而不是 409「已审核」。
- 桥直接返回的重放响应带头 `Idempotent-Replayed: true`；API 不转发这个头，响应体与第一次相同。第一次超过 75 秒仍在执行时先回 504，任务结束后桥记下真实结果，之后的重发拿到真实结果。
- 保存期从第一次请求算起：成功 24 小时，结果未知 72 小时；过期后同一个键视为新请求。自动重试的窗口要在 24 小时以内。
- 只读账套（`readOnlyAccounts` / `U8CO_READONLY_ACCOUNTS`）上的写请求连同重放一律 403 `account_read_only`。
- 幂等键不代替单据锁：不同的键新建同一类单据时照常按 `new:<类型>` 排队。
- 审计：`detail` 带「幂等键 <键>」；重放请求的 `outcome` 是 `idempotent_replay`；重放前的登录校验是一条 `action` 为 `idempotent_login` 的审计行。
- 预演（`dry_run: true`，§23）和 `arap/writeoff/auto` 的计划模式不能带幂等键，400「预演不能带 Idempotency-Key」（API 和桥都查）；预演不读写幂等记录。

### 查询结果 `idempotency/get`

读路由。收到 504 `outcome_unknown` 或连接中断后，先按幂等键查第一次的结果，再决定是否重发。登录照常校验（登录子系统跟原路由走：`/u8co/v1/gl/` 下的路由按 `GL`，其余按 `AS`），不调用业务组件、不进写闸门、不加单据锁。

记录绑定操作员：只有第一次请求的 U8 操作员查得到（去空格、不分大小写），其他操作员（即使同一令牌、同一账套）得到 `found: false`。

API 请求（公共字段之外）：

| 字段 | 说明 |
| --- | --- |
| `path` | 第一次请求的 API 路径，须是写路由，如 `/v1/co/vouchers/create`、`/v1/co/gl/vouchers/void`、`/v1/co/arap/writeoff`；读路由 400（`field` 为 `path`） |
| `key` | 第一次请求头 `Idempotency-Key` 的值 |

API 按与第一次请求相同的规则算出 `caller`，转给桥 `route`（桥路径）、`idempotency_key`、`caller`，所以只有同一信任项、同一客户端查得到自己的记录。直接调桥时请求体是 `route`（写路由的桥路径，否则 400、`field` 为 `route`）、`idempotency_key`、可选 `caller`（缺省 `direct`）。

响应：

```json
{"ok": true, "found": true, "state": "ok", "status": 200, "created_utc": "2026-09-29T08:00:00Z",
 "response": {"ok": true, "type": "sale_order", "id": 9000000004, "code": "0000000004"}}
```

| 字段 | 说明 |
| --- | --- |
| `found` | 记录在不在。没有或已过期是 `{"ok":true,"found":false}`，HTTP 200 |
| `state` | `ok`（第一次成功）、`outcome_unknown`（结果未知，要按业务内容核对）、`in_flight`（第一次还在执行） |
| `status` | 第一次请求的 HTTP 状态。经 API 时是 API 会返回的状态（桥的 500 对应 502 等） |
| `created_utc` | 第一次请求到达的时间（UTC） |
| `response` | 第一次的响应体，`in_flight` 时没有。经 API 时非 2xx 的响应换成 API 错误体 `{"error":{…}}`，码和状态的对应同 §18 |

`found: false` 不等于「没有写入」：第一次请求没带幂等键、或以不占用键的结果结束时也查不到。`state` 为 `outcome_unknown` 时仍要按业务内容核对。客户端命令：`idem-get`。

## 21. 字段元数据 `meta`

API：`GET /v1/co/meta`（读权限）。桥：`POST /u8co/v1/meta`，照常 HMAC 签名，请求体为空或 `{}`，其他字段 400「含未知字段」；**不带** `acc`、`year`、`operator`、`password_enc`、`date`。桥不登录 U8、不进工作队列，在 HTTP 线程上直接返回，只读进程内的表和 U8 安装目录下的 RsXml，不调用 COM。客户端命令：`meta`（不读口令）。

内容由桥按自己的校验表生成（可写字段直接取自各领域的白名单判断），API 不另存；API 请求模型里的 `Literal` 只是便利，以 meta 为准。

| 字段 | 说明 |
| --- | --- |
| `version` | 元数据格式版本，现为 `"1"` |
| `revision` | 除 `features` 外全部内容的规范 JSON（键按序数排序）的 SHA-256，十六进制小写。内容不变则不变，可用来判断是否需要重新生成调用方的字段表 |
| `complete` | 档案标签是否全部读到。某个档案的 RsXml 读不到时为 false，该档案 `tags`、`writable` 为 null，另带 `tags_error` |
| `kinds` | 单据类型，见下 |
| `archives` | 档案类型：`name`、`root`（EAI 根标签）、`rs_file`、`table`、`key`、`name_col`、`need_template`、`read_only`（只能 get、list，`writable` 为空数组）、`deletable`（`project` 为 false）、`block`（不能写的标签，另有公共的 `code`、`CreatePerson`、`ModifyPerson`、`ModifyDate`）、`private`（get 照常返回但不从模板复制）、`tags`（RsXml 全部标签）、`writable`（`tags` 去掉不能写的）。没有 RsXml 的只读档案 `tags` 为 null、不带 `tags_error`；`project` 的 `tags`、`writable` 固定为 `name`、`bclose`、`citemccode` |
| `gl` | 总账凭证：`head`、`line`、`cash_flow` 字段名，`required_head`、`required_line`，`lines_min` 2、`lines_max` 200、`cash_flow_max` 50 |
| `list_kinds` | `vouchers/list` 支持的类型 |
| `routes` | 桥的全部路由（取自分派表，另加 `login-check` 和 `meta`），按路径排序：`path`、`keys`（请求体允许的顶层字段，含公共字段；为 null 表示分派表和字段表不一致，应当报告）、`optional`（`keys` 之外的可选字段：写路由的 `idempotency_key`、`caller`（§20）和 `dry_run`（§23，两条专用审核路由除外；`arap/writeoff/auto` 的 `dry_run` 在 `keys` 里）） |
| `field_refs` | 写字段名（小写）到档案类型的映射，例如 `ccuscode` → `customer`、`cinvcode` → `inventory`、`cwhcode` / `cowhcode` / `ciwhcode` → `warehouse`。用于把名称解析成编码时选档案（§24） |
| `gl_field_refs` | 总账分录键到档案类型：`account` → `account`、`dept` → `department`、`person` → `person`、`customer` → `customer`、`supplier` → `vendor`、`item` → `project`、`settle` → `settle_style`、`currency` → `currency`、`sign` → `voucher_sign` |
| `dry_run_routes` | 单据路由以外的写路由的预演模式（§23），例如 `{"gl/vouchers/create": "validate", "archives/create": {"project": "rollback", "*": "validate"}, "arap/writeoff": "rollback"}`；值是模式名，或按档案类型分的对象（`*` 为其余类型）。不在表里的路由不支持预演 |
| `features` | 运行时自检结果（例如 COM 签名自检 `signatures`），可能随时变化，不计入 `revision` |

`kinds` 每项：`name`、`title`、`family`、`sub_id`、`verify_sub`、`tables`（`head`、`id`、`code`、`body`、`body_fk`、`line_id`、`verifier`、`verify_date`）、`ops`（布尔值：`create`、`update`、`delete`、`verify`、`close`、`workflow`、`generate`、`lock`（只有销售订单为 true）、`arap_verify` / `arap_unverify`（能做应收应付审核、弃审的类型）、`writeoff`、`writeoff_cancel`、`writeoff_auto`（参与核销的收付款单、发票、应收应付单）、`arap_voucher`（能制单的六种类型，§12））、`sources`（生单来源，第一个是缺省）、`blocked`（任何写操作都不能填的字段，小写：全局名单加该类型的主键、单号、审核人、审核日期列）、`dry_run`、`writable`：

- `writable.create`、`writable.update`：不支持时为 null。`writable.generate`：按来源类型分的对象，没有来源时为 `{}`。
- 每个操作 `{"head","lines","required","lines_min","lines_max","line_control"}`。`head` / `lines` 为 null 表示该操作不收表头 / 表体（如 `sale_out` 生单）；否则是 `{"exact":[…],"spans":[{"prefix","from","to"}]}`：`exact` 逐个列出的字段（小写），`spans` 是 `cdefine`、`cfree` 的编号区间，例如 `{"prefix":"cdefine","from":22,"to":37}`。字段名不分大小写。
- `required`：`{"head":[…],"lines":[…]}`，登录前检查的必填字段。U8 自己要求的字段（例如销售订单的客户）不在这里，缺了由 U8 拒绝。
- `line_control`：表体控制字段。修改是 `op`、`line_id`（物料清单是 `op`、`sort_seq`，`sort_seq` 也是新增行可写的字段）；生单是 `source_line_id`、`quantity`（必带）。
- 发货单、销售发票生单的表头字段还须出现在 U8 单据模板的行集里，meta 给不出，运行时不在就 400。
- `dry_run`：该类型各写操作的预演模式，如 `{"create": "rollback", "verify": "rollback", "generate": {"sale_order": "rollback"}}`（`generate` 按来源类型分）；不支持预演的组合不出现（§23）。

缓存：桥在所有档案标签都读到后把结果缓存到进程结束（RsXml 按文件缓存，U8 安装变更后要重启桥）；API 在进程内缓存成功结果 60 秒，失败不缓存；缓存过期时同时到达的请求只调一次桥，其余等待（最多 100 秒，超时 503 `unavailable`）。桥返回 504 `outcome_unknown` 时 API 改报 503 `unavailable`（meta 只读，可直接重试）。

## 22. 操作员权限

读路由按调用的 U8 操作员检查权限，效果与 U8 客户端一致：先查功能权限，再按数据权限（记录级）过滤，最后按字段权限把不可见的列置空。`meta`、健康检查、`login-check` 不查权限。写路由的权限见各写路由小节（总账写 §14 查 `GL0201`–`GL0204`，生产订单关闭和打开、销售订单和采购订单锁定解锁 §10，应收 / 应付核销、制单 §12，物料清单写入 §6–§9，档案写入 §15）。

**功能权限。** 读 `UFSYSTEM..UA_HoldAuth`：操作员本人（`iIsUser=1`）或所属角色（`UA_Role`，不做角色继承）持有该功能 id。年度窗口：请求的 `year`（账套库年度）或账套的建账年度（`UFSYSTEM..UA_Account.iYear`）——U8 的业务授权挂在建账年度下，所以登录新年度的库也按建账年度的授权判断；登录日期不参与。账套主管（持有 `admin` / `Admin`）只看本账套不晚于请求年度、最近一个有 `admin` 行的年度（某年收回的主管不会因早年的行仍算主管）。账套主管的功能与数据权限都不受限。

每个读路由（单据类型、档案、报表）可接受的功能 id 登记在桥的 `PermRegistry*.cs`，任一即可；读取和列表用同一组 id。例如销售订单 `SA03010104`（查询）或 `SA03010201`（列表），现存量 `ST020107_01`，总账凭证 `GL0202` 或 `GL0201`，档案 `AS011Q` 或 `AS011`（客户），会计科目任一总账功能，物料清单 `BO01001Q`，其他报检单 `QM02060101` 或 `QM030601`，其他检验单 `QM02060201` 或 `QM030603`，采购结算单 `PU040305`，出入库调整单（`ia_adjust`）入库调整单 `IA1001` / `IA02040201` 或出库调整单 `IA1004` / `IA02040301`，存货调价单 `SA03120202` 或 `SA0312020301`。没有时 403 `no_permission`「没有<功能名>权限」（如「没有销售订单查询权限」）。不带 `type` 的 `workflow/tasks` 只列操作员自己的待办，登录成功即可。登记的 id 已与 U8 授权目录（`UA_Auth`、`UA_Menu`）逐个核对含义，未在 U8 客户端逐个授权点验。

**数据权限（记录级）。** 只对账套里打开了「数据权限控制」的业务对象生效（`AA_BusObject_base.bAuthControl=1`）。开关打开时，账套主管和该对象的数据权限管理员（`AA_holdBusobject.iAdmin=1`）不受限；其他人只能看到 `AA_HoldAuth` 里授了查询（`cFuncId` 含 `R`）的编码，一个都没授就什么都看不到。受控对象：客户、供应商、部门、人员（授权行在 `person` 或 `hr_hi_person`）、仓库、存货、会计科目、项目（大类 + 编码）、销售类型、采购类型、收发类别、凭证类别。`user` 对象（制单人）在 U8 里管删改他人单据，读取不按它过滤。

会计科目（`code`）只在总账选项「明细账查询权限控制到科目」（`bQryCtlSubj`）打开时生效，管科目余额表、辅助余额表、明细账、经营管理损益，以及总账凭证的 `gl/vouchers/load`、`list`、`digest`、`attachments/list`：整张凭证的全部分录科目都在授权内才可见（U8 客户端是否逐张按科目控制未核对，取较严口径）。总账凭证不按「查询他人凭证控制到操作员」（`bFindVouchCtrl`）过滤；科目档案、应收应付单上的科目不按科目权限过滤。

| 情形 | 列表（`vouchers/list`、`archives/list`、`stock/current`、`gl/vouchers/list`、报表） | 读取单张（`vouchers/load`、`archives/get`、`gl/vouchers/load`、`workflow/state`、`history`） |
| --- | --- | --- |
| 表头对象不在授权内 | 这一行不出现，200 | 403 `no_permission`「没有该单据的数据权限」（档案是「没有该档案的数据权限」） |
| 受控列为空 | 这一行不出现（本来可为空的列除外：如采购入库单上的客户，采购、库存单据的业务员，收付款单与应收应付单的部门和业务员） | 403 |
| 存货、表体仓库：表体一行都不在授权内 | 单据不出现 | 403 |
| 存货、表体仓库：部分行在授权内 | 单据出现 | 整张返回全部行（同 U8 卡片） |
| 调拨单 | 调出、调入两个仓库都要在授权内 | 同左，否则 403 |
| 单据不存在 | — | 404 |

- 两列主键的只读档案按两段过滤：客户收货地址（`customer_address`）按客户段，客户存货对照（`customer_inventory`）按客户段和存货段。自定义项（`user_define`）不按档案值过滤。
- 列表因数据权限变少或变空时照常 200；游标和 `watermark` 的语义不变。
- 单据读取先按列表同一套条件在表头表上探一次：越权直接 403，不走 COM 读取；按类型条件探不到时照常读取，读到后再核对。
- `workflow/tasks` 去掉操作员没有查询功能权限的单据类型的待办，不按档案对象过滤。
- 各报表的过滤对象见 §19 各小节。经营管理报表：`reports/mgmt/pnl` 的 `dims` 含 `dept` 时，部门受控则聚合前去掉部门不在授权内的分录（没有部门的分录照常计入）；含 `item` 时同样按项目（大类 + 编码）过滤；不按部门时只按科目过滤。`reports/mgmt/meta` 只查结账类功能权限、不按记录过滤，数据水位给 `gl_content_checksum`（不给金额），并带权限指纹 `perm_fingerprint`（同 `perm/snapshot` 的 `fingerprint`），权限一变按 meta 定的缓存键随之失效。

**字段权限。** 读 `AA_ColumnAuth`：只认字段权限开关已打开的对象（`AA_BusObject_base` 里 `iAuthType=1`、`bAuthControl=1`；记录级是 `iAuthType=0`），`cFuncID` 含 `N` 的行是「不能查看」。U8 是拒绝清单：没有行即可见。本人的拒绝与所属角色的拒绝取并集；本人在同一对象、同一字段上有不含 `N` 的行时角色的拒绝不算；本人已拒绝时角色放不开。账套主管不受限；对象的数据权限管理员（`iAdmin`，只管记录级）不免遮。

读路由成功（200）之后桥统一遮一次：拒绝的字段置为 `null`（行、单据照给，不 403），响应体加 `masked_fields`（本次响应里出现并被置空的字段名，排序去重；没有则不带）。字段名不分大小写比对，去掉 `B;`、`T;` 这类前缀，报表对象的中文列名（如「原币无税金额」）换成物理列名。

| 范围 | U8 字段权限对象（`cKey`） |
| --- | --- |
| `purchase_in`、`purchase_settle` | `24`（采购入库单列表）及采购入库明细报表对象 |
| `arrival`、`purchase_return` | `26`（到货单列表） |
| `purchase_order`、`purchase_requisition`；`reports/order_execution` 的 `type=purchase_order` | `88`（采购订单列表） |
| `other_in`、`other_out`、`sale_out`、`product_in`、`material_out` | `0301`、`0302`、`0303`、`0411`、`0412` |
| `transfer`、`transfer_request`；`shape_change` | `0304`；`0305` |
| `dispatch`、`sale_return`、`sale_return_apply` | `01`、`VCH_01`、`dispatchpriceref`、`SARefDispB` |
| `stock_opening`、`stock_check`、`ia_adjust`、`inventory_price_adjust`；`reports/opening_balance` 的 `module=stock` | 出入库单列表的并集（`24`、`0301`、`0302`、`0303`、`0411`、`0412`） |
| `reports/mgmt/sales` | `0303`：遮 `cogs`、`gross`、`gross_pct`，收入、数量照给 |
| `reports/mgmt/cash_stock` | 只遮 `purchases` 段（`24`）和 `inventory` 段（出入库单并集） |
| 应收应付、票据、总账、档案、`perm/*`、写路由 | 不遮（U8 没有对应的字段权限对象） |
| 上表没有的单据类型（如 `sale_order`、`sale_invoice`、`purchase_invoice`、生产、质量单据） | 从严：操作员在任一对象上拒绝的字段名，出现在响应里就置空（不展开金额组） |

- **金额组（从严）**：某对象上拒绝了任一金额列（单价、成本、金额、税额、价税合计等，如 `iPrice`、`iUnitCost`、`iSum`、`iOrderAmt`），该范围内响应里的全部金额列一起置空，连同桥起的金额别名（`amount`、`amt`、`nat_amount`、`*_amount`、`cogs`、`gross` 等），以免由同行其他金额反推；数量只在本身被拒绝时置空。因此可能比 U8 界面多遮。
- `vouchers/load` 的 `head`、`lines`、`positions` 的值可以是 `null`，顶层带 `masked_fields`；`load_many` 每个条目带自己的 `masked_fields`，信封上是并集；`list`、`search` 和报表的条目同样带 `null`。`fields` 投影时 `items[].masked_fields` 总是保留；`compact=true` 去掉值为 `null` 的键，被遮的字段名仍在 `masked_fields` 里。
- 经营管理合并时被遮的金额不当 0：`mgmt/sales` 任一账套 `cogs` 为 `null` 时合并的 `cogs`、`gross`、`gross_pct` 都是 `null`；`mgmt/cash_stock` 任一账套的存货金额或采购金额为 `null` 时合并的 `inventory.amount`、`purchases.external_amount` 为 `null`；`consolidated.masked_fields` 是各账套 `masked_fields` 与合并时置空字段的并集，`by_account` 各账套照带。`mgmt/overview` 取自被遮部分的指标（如 `inventory_value`）为 `null`，指标名列在 `masked_fields`。
- 审计 `detail` 只记置空的字段个数（`masked=<n>`），不记字段名。

**缓存。** 读路由按（账套、请求年度、登录年度、操作员）缓存权限快照（含字段权限）60 秒，最多 256 条；该操作员登录失败时清掉。U8 里改了授权或开关，读路由最多 60 秒后生效；写路由的权限检查（总账写、生产订单关闭和打开、销售订单和采购订单锁定解锁、档案写入）每次现读。某个对象的授权超过 100 个编码时，列表改用 `AA_HoldAuth` 的实时条件，单张读取、档案读取和物料清单也实时查。

**校验先于权限。** 请求字段不合法（缺类型、档案类型写错等）返回 400，不会变成 403。

### 权限快照 `perm/snapshot` 与权限评估 `perm/evaluate`

两条只读路由给出读路由过滤用的同一份权限快照（同一个 60 秒缓存），供下游按人员过滤（如审批应用的档案选择）。只跑 SQL，走读线程池；响应不含口令和档案名称。

- 桥 `POST /u8co/v1/perm/snapshot`：只有公共字段（`acc`、`year`、`operator`、`password_enc`、`date`）。返回登录操作员本人的权限，登录成功即可。
- 桥 `POST /u8co/v1/perm/evaluate`：公共字段加 `subject`（被查询的操作员编码，1 到 20 个字符，不含空白、单引号、分号；不合法 400）。只允许桥 `config.json` 的 `permEvaluateOperators` 里的调用操作员（不分大小写），缺省为空即一律 403 `no_permission`「该操作员不能查询其他操作员的权限」；账套主管不因主管身份放行。名单在登录前和处理时各查一次。`subject` 不登录 U8，年度窗口同调用方，不进权限缓存；在 `UFSYSTEM..UA_User` 里不存在或已停用（`nState` 不为 0）时 404 `not_found`「操作员不存在或已停用」。正式账套建议名单留空。
- API：`POST /v1/co/perm/snapshot`（读权限，请求体只有登录字段）和 `POST /v1/co/perm/evaluate`（另带 `subject`）原样转给桥，使用调用方自己提交的操作员和口令。`perm/evaluate` 的 `x-u8co-access` 是 `perm_evaluate`：只有信任项设了 `perm_evaluate: true`、令牌又有读或写权限的调用方能调，否则 403 `forbidden`，不访问桥；它不是写路由，不受写入策略和只读账套限制，不带 `caller`。`perm/snapshot` 在 MCP 路由目录里，`perm/evaluate` 不在。

响应（`evaluate` 另带 `subject`，`operator` 是被查询的操作员）：

```json
{
  "ok": true, "acc": "998", "year": 2026, "acct_year": 2026, "operator": "op001",
  "supervisor": false, "gl_subj_ctl": false,
  "roles": ["R01"], "functions": ["AS011Q", "SA03010104"],
  "objects_on": ["code", "customer", "user"], "data_admin": [],
  "data": {
    "customer": {"codes": ["C900001", "C900002"]},
    "fitem": {"all": true},
    "warehouse": {"all": true}
  },
  "columns": {"0303": ["iPrice", "iUnitCost"]},
  "ttl_s": 60,
  "fingerprint": "<64 位小写十六进制>"
}
```

- `functions`、`roles`、`objects_on`（数据权限开关已打开的对象，含读取不用的 `user`、`gzauth`）、`data_admin` 都按序数排序。账套主管 `supervisor=true`，此时数据权限全部不受限。
- `data` 每个读取会用到的对象一项（客户、供应商、部门、人员、仓库、存货、会计科目 `code`、项目 `fitem`、销售类型、采购类型、收发类别、凭证类别 `dsign`；`user`、`gzauth`、货位不给）：不受控（开关没开、账套主管、该对象的数据权限管理员；科目另要 `bQryCtlSubj` 打开）是 `{"all": true}`；受控是 `{"codes": [...]}`，项目是 `{"pairs": [{"class": "00", "code": "P1"}]}`。受控而数组为空表示一条都不能看。编码超过 100 个时照样全部给出，另带 `"live": true`（桥的列表此时改用实时条件）。
- `columns` 是字段权限：{对象 id: [拒绝查看的字段]}，字段名照 `AA_ColumnAuth.cFld` 原样（去空格），只列至少有一个字段的对象，对象和字段都按序数排序；账套主管、没有字段权限时为 `{}`。
- `fingerprint` 是 `{columns, data, functions, supervisor}` 规范 JSON（键按序数排序、数组已排序）的 SHA-256，不含账套、操作员、角色和口令；权限相同指纹相同。下游按（账套、操作员、指纹）缓存，`ttl_s` 秒内有效。
- 审计行 `detail` 只记 `subject`、功能 id 个数、受控对象个数、编码个数、字段权限的对象-字段对数（`columns=`）和指纹，不记编码和字段清单。

## 23. 预演 `dry_run`

写路由的请求体可以带 `dry_run: true`：桥照常登录、检查、加锁，走到写库那一步为止，不写入，返回「如果真做会是什么结果」。不带或 `false` 是正常写入（API 只在 `true` 时才把这个键转给桥）。用于在真正写入前核对字段、编码和金额，尤其适合 AI 代理。

### 两种模式

| 模式 | 做法 | 能说明什么 |
| --- | --- | --- |
| `rollback` | 桥在自己的事务里真的调用 U8 业务组件或执行 SQL，在同一连接上读出事务里的新单据（或改后的单据），然后回滚 | 高：U8 自己的校验、编号、金额计算都跑过了，返回的 `docs` 就是 U8 会存下的样子 |
| `validate` | U8 组件自己提交、桥的事务管不住（凭证导入、档案导入、U8 API 框架、审批服务等）。桥把调用组件之前的全部检查照做，停在调用之前 | 中：桥的检查都过了，但 U8 组件保存时自己的检查（必输项、结构完整性等）没有跑，真做时仍可能被拒绝 |

`arap/writeoff/auto` 的 `dry_run` 是第三种 `plan`：只出配对计划（§12），响应带 `mode: "plan"`。

### 各路由的模式

| 路由 | `rollback` | `validate` |
| --- | --- | --- |
| `vouchers/create`、`update`、`delete`、`verify`、`close`、`lock`、`generate` | 除右列以外的全部单据类型和操作，包括：生产订单的关闭、打开；报检单（`qm_*_inspect`）的生单；期初结存单（`stock_opening`）的删除、审核、弃审；供应商退款、客户退款、无来源销售出库、采购手工结算、红冲蓝字发票、参照退货申请单生成退货单 | 期初结存单的新增（EAI 导入）；生产订单的新增、修改、删除、审核、弃审；物料清单的全部写操作；检验单（`qm_*_check`）的生单、删除；不良品处理单（`qm_*_reject`）的生单、审核、弃审、删除；报检单的删除（含删除前的弃审）；产品报检单（`qm_product_inspect`）的弃审；其他报检单（`qm_other_inspect`）的新增、审核、弃审、删除；其他检验单（`qm_other_check`）的生单、审核、弃审、删除；退货申请单（`sale_return_apply`）的新增、修改、删除、审核、弃审 |
| `gl/vouchers/*` | `void`、`unvoid`、`verify`、`unverify`、`sign`、`unsign`、`delete`、`unpost` | `create`、`update`、`post`、`reverse` |
| `gl/transfer/pnl`、`gl/transfer/custom` | — | 全部：停在凭证导入之前，分录在 `detail.transfer` |
| `archives/create`、`update`、`delete` | 项目（`project`）、客户和供应商银行账户（`customer_bank`、`vendor_bank`）的新增、修改、删除；币种、凭证类别、汇率、供应商联系人（`currency`、`voucher_sign`、`exchange_rate`、`vendor_contact`）的修改、删除 | 其余走 EAI 的档案（含原因码 `reason`）；币种、凭证类别、汇率、供应商联系人的新增；客户联系人（`customer_contact`） |
| `workflow/*` 写操作 | — | `submit`、`withdraw`、`approve`、`disagree`、`return`、`abandon`、`resubmit` |
| `arap/*` | `writeoff`、`writeoff/cancel`、`voucher/delete` | `voucher`：桥算完分录、停在凭证导入之前，计划的凭证在 `detail` 里 |
| `openings/post` | `post`、`unpost` | — |
| `openings/arap` | `create`、`delete`、`verify`、`unverify` | — |
| `periods/close` | `close`、`reopen`（含 `through`） | — |
| `ia/post`、`ia/period_end` | `post`、`unpost`；`run`、`cancel` | — |
| `arap/writeoff/auto` | — | —（`plan`，见上） |

`intercompany/generate_buyer`（仅 API）缺省就是预演，模式同买方的 `vouchers/generate`。

权威的表在桥里（`DryRunModes`），经 `meta` 公开：单据看 `kinds[].dry_run`，其余看 `dry_run_routes`（§21）。表里没有的组合在登录之前拒绝，400「该操作不支持预演」。`sale-orders/verify`、`dispatches/verify` 两条专用审核路由不支持预演：经 API 时是 400「请求参数无效：dry_run」（`field` 为 `dry_run`），不到桥；直接调桥是 400「旧路由不支持预演，请用 vouchers/verify」。

### 响应

预演成功是 HTTP 200，桥和 API 的形状相同：

```json
{
  "ok": true,
  "dry_run": true,
  "mode": "rollback",
  "route": "vouchers/create",
  "type": "sale_order",
  "action": "create",
  "docs": [
    {"type": "sale_order", "id": 9000000004, "code": "0000000004", "state": "exists",
     "head": {"ccuscode": "C900001", "ddate": "2026-09-29", "cstcode": "01"},
     "lines": [{"autoid": 9000000101, "cinvcode": "A01", "iquantity": 5.0, "itaxunitprice": 11.3, "isum": 56.5}],
     "lines_total": 1}
  ],
  "warnings": ["number_may_skip", "locks_held"],
  "message": "预演完成，已回滚，没有写入"
}
```

| 字段 | 说明 |
| --- | --- |
| `dry_run`、`mode` | 恒为 `true`；`rollback` 或 `validate` |
| `route`、`type`、`action` | 预演的路由（不带前缀）、单据类型、操作 |
| `docs` | 受影响单据在事务里的样子，最多 10 张：先是请求的 `type` / `id` 那张（生单时是**来源单据**），之后是新单据和其他受影响单据。`state` 是 `exists`（新建、修改、审核后的样子）或 `deleted`（事务里已删，没有 `head`、`lines`）。`head`、`lines` 是表头表、表体表的整行（`SELECT *`），列名小写，去掉空值和二进制列（`ufts` 等），数字是 JSON 数字，日期时间为 0 点时写成 `yyyy-MM-dd`、否则 `yyyy-MM-ddTHH:mm:ss`，字符串去掉右侧空格。每张最多 200 行，`lines_total` 是实际行数。注意这是表列，与 `vouchers/load`（U8 行集、值为字符串）写法不同。全部映像超过约 4 MiB 时去掉各单的 `lines`（保留 `lines_total`），`detail.truncated` 为 `true` |
| `detail` | 各路由的补充和提醒，见下表；没有内容时不出现 |
| `warnings` | `number_may_skip`（`rollback` 的新增、生单：U8 在预演中取过的单号可能不退回，真做时号码可能跳一个）、`validate_only`（`validate` 模式：U8 保存时自己的检查没有跑）、`locks_held`（`rollback` 总有：预演期间持有与真写相同的锁） |
| `message` | 中文说明：`rollback`「预演完成，已回滚，没有写入」；停在组件之前的 `validate`「预演完成：只做了提交前的校验，没有调用 U8 组件，没有写入」；在桥事务里算完再停的 `validate`（如 `arap/voucher`）「预演完成（校验模式），已回滚，没有写入」；没有改动「预演完成：没有需要写入的改动」。程序不要按文字判断 |

`validate` 模式不产生新单据，新增、生单的 `docs` 是空数组；对已有单据的操作 `docs` 可能带上它当前已提交的样子（`state: "exists"`）。`rollback` 模式下没有需要写入的改动（例如再锁定一张已由本人锁定的销售订单）时，`detail.no_change` 为 `true`，`docs` 是单据当前的样子。

`detail` 的键：

| 键 | 出现在 | 含义 |
| --- | --- | --- |
| `stopped_before` | `validate` | 停在了哪个组件调用之前，如 `U8PzInsert.Transact`、`GlPostTx.VouchPostAll`、`U8API MOrderAdd`、`UFQMCo.VoucherOperate(add)`、`AuditServiceProxy` |
| `gl` | 总账作废、审核、签字、删除等；`arap/voucher/delete` | 操作后（事务里）的凭证：`op`、`iyear`、`iperiod`、`csign`、`ino_id`、`lines`、`state` |
| `voucher` | 总账 `create` / `update`；`arap/voucher` | 将要导入的凭证：`op`、`iyear`、`iperiod`、`csign`、`ino_id`（修改时）、`date`、`maker`、`attachments`、`lines`、`lines_total` |
| `post` | 总账 `post` | `iyear`、`iperiod`、`poster`、`vouchers`（`[{csign, ino_id}]`）、`first_posting_checks: "not_run"`（年度首张凭证的期初对账只有真做才执行） |
| `transfer` | `gl/transfer/*` | 将要生成的结转分录 |
| `archive` | 档案写入 | `archive`、`code`、`op`；`rollback` 另有 `after`（事务里写后的记录）或删除时的 `deleted: true` |
| `writeoff`、`writeoff_cancel` | `arap/writeoff`、`arap/writeoff/cancel` | 与真做成功的响应同形（去掉 `ok`） |
| `opening` | `openings/post` | 与真做成功的响应同形（去掉 `ok`）：`module`、`action`、`posted`（操作后的状态）、`opening_year`、`start_date`；存货核算另有 `counts` |
| `periods` | `periods/close` | 会结账（取消结账）的期间 `[{module, fiscal_year, period}]`，按执行顺序；`through` 时可能是空数组；库存那几项带 `stock_rows` |
| `stock_rows` | `periods/close`（单个库存） | 库存快照会写入（取消结账：会删掉）的行数 `{account, accounts, v, vs, check}`（§31） |
| `counts` | `ia/post`、`ia/period_end` | 存货核算脚本在事务里算出的诊断计数，同真做成功的 `counts`（§32） |
| `ia_counts` | `periods/close`（存货核算有数据的月份） | 存货核算月末结账（取消结账）脚本的诊断计数（§31） |
| `workflow` | 审批流写操作 | `action`、`code`（单号）、`state_before`（调用前的审批状态） |
| `input` | 生产订单、物料清单、检验单生单的 `validate` | 桥规范化后的输入 |
| `unverify_first` | 质量单据删除 | `true`：已审核的报检单，真做时先弃审再删（两步都由 U8 提交） |
| `not_previewed` | 销售出库按行生单（带 `lines`） | 预演只到整单 `MakeOutVouch`：`docs` 是整张发货单剩余数量生成的出库单，按行改回数量、批号和货位没有预演 |
| `no_change` | 任意 | 没有需要写入的改动 |
| `truncated` | 任意 | 映像太大，去掉了 `lines` |
| `new_id_unknown` | 库存单据新增 | 新单据的主键没取到，`docs` 里没有这张新单 |
| `generated_unknown` | 库存单据审核、销售出库生单 | 连带生成的单据没读出来，不在 `docs` 里 |
| `preview_error` | 档案写入 | 事务里写后的档案记录没读出来，`archive` 里没有 `after` |
| `docs_unavailable` | `validate`、`no_change` | 已有单据的当前映像读不出来，`docs` 为空 |

最后几项提醒键出现时预演仍然成功（没有写入），只是 `docs` 不完整，应一并告知确认的人。预演中途失败是正常的错误响应，与真做时相同（例如 409 `u8_rejected` 带 U8 原文、400 带 `field`，§18），事务照常回滚。

### 规则

- **从不提交。** `rollback` 模式在提交点检查本连接上的事务还在（`@@TRANCOUNT` ≥ 1）；如果 U8 组件已自行提交，返回 504 `outcome_unknown`「预演时 U8 组件已自行提交，写入可能已生效，请核对」，审计记 `dry_run_self_commit`。处理函数开了事务却既没走到提交点、也没停在组件之前就返回时，504 `outcome_unknown`「预演没有走到提交点，无法确认没有写入，请核对」，审计记 `dry_run_no_commit`。两者都按真写的 504 先核对。
- **主映像读不出。** 提交点读 `docs` 失败时回滚，返回 500 `internal`（经 API 是 502「内部错误」），审计记 `dry_run_preview_failed`；没有写入，可以重发。附加信息读不出不算失败（见 `generated_unknown`、`preview_error`）。
- **锁、写闸门、登录与真写相同。** 预演按单据加锁、进全局写闸门（`serializeWrites`），同样可能 429 `busy` / 503 `busy_timeout`，期间别的写入要等它；用调用方的操作员登录 U8、占许可点数，功能权限、数据权限照常检查。
- **写入分级同样适用。** 第二级写入（复现 U8 界面 SQL 的写入，清单见 `docs/limitations.md`）的预演同样要求 `enableReplicatedWrites` 打开（否则 403 `feature_disabled`）且账套在 `testAccounts` 里（否则 403 `test_account_only`）；只读账套上的预演同样 403 `account_read_only`。
- **不能带幂等键**（§20）。
- **审计。** 桥的审计行 `outcome` 为 `dry_run`（失败时是错误码），另有 `dry_run` 字段（`rollback` / `validate`）。API 的审计行有布尔字段 `dry_run`（预演，包括失败的预演，为 `true`）；`action`、`status` 与真写相同。
- **不产生事件。** 事件服务只看已提交的数据。
- 读路由收到 `dry_run` 是 400（未知字段）。

预演与真做之间单据可能被他人修改，预演通过不保证真做成功；`validate` 模式只说明桥的检查通过了。

## 24. 名称解析 `archives/resolve`

读路由。把名称（「甲公司」「示例存货 X1」）解析成写单据要用的编码：一次最多 20 项，按档案查出候选编码。权限同该档案的 `archives/list`：功能权限按每一项的档案类型查，记录级数据权限照样过滤（§22），越权的记录不会出现在候选里；任何一项的档案没有功能权限，整个请求 403 `no_permission`。

请求（公共字段之外）：

```json
{"items": [{"archive": "customer", "q": "甲公司"}, {"archive": "inventory", "q": "示例存货X1"}],
 "limit": 5, "include_disabled": false}
```

| 字段 | 说明 |
| --- | --- |
| `items` | 1 到 20 项 `{archive, q}`。`archive` 是能 `list` 的档案类型，但不收两列主键的类型（`customer_address`、`customer_inventory`、`customer_bank`、`vendor_bank`、`customer_contact`、`vendor_contact`、`user_define`）和 `exchange_rate`、`fa_card`；`q` 去掉前后空白后 1 到 100 个字符 |
| `limit` | 每项最多返回几个候选，1 到 20，缺省 5 |
| `include_disabled` | 缺省 `false`：停用的记录不参加匹配。`true` 时参加，候选上标 `disabled: true` |

匹配分五档，按顺序找，**第一档有命中就停**，同档内按编码排序：

| 档 | `match` | 规则 |
| --- | --- | --- |
| 1 | `code` | 编码完全相等（项目写 `<大类>:<编码>` 或只写编码） |
| 2 | `name` | 名称完全相等 |
| 3 | `abbr` | 简称完全相等：客户 `cCusAbbName`、供应商 `cVenAbbName` |
| 4 | `mnemonic`、`add_code` | 助记码完全相等（不分大小写）：客户 `cCusMnemCode`、供应商 `cVenMnemCode`、存货 `cInvMnemCode`；存货代码 `cInvAddCode`（`add_code`）同一档 |
| 5 | `contains` | 名称包含 `q`；客户、供应商的简称包含 `q`；存货的「名称 + 规格」包含 `q`（直接相连或中间隔一个空格都算，所以「示例存货X1」能找到名称「示例存货」、规格「X1」的存货） |

响应：

```json
{"ok": true, "results": [
  {"archive": "customer", "q": "甲公司", "status": "exact",
   "match": {"code": "C900001", "name": "甲公司商贸有限公司", "match": "abbr"},
   "candidates": [{"code": "C900001", "name": "甲公司商贸有限公司", "match": "abbr", "abbr": "甲公司"}],
   "more": false},
  {"archive": "inventory", "q": "示例存货X1", "status": "partial",
   "match": {"code": "A01", "name": "示例存货", "match": "contains"},
   "candidates": [{"code": "A01", "name": "示例存货", "match": "contains", "spec": "X1", "unit": "01"}],
   "more": false}
]}
```

| 字段 | 说明 |
| --- | --- |
| `status` | `exact`：第 1 到 4 档恰好一个命中，可以直接用；`ambiguous`：命中的那一档不止一个，需要挑选；`partial`：只有第 5 档命中且恰好一个，建议确认后再用；`none`：没有命中 |
| `match` | 只在 `exact`、`partial` 时有：选中的 `code`、`name` 和命中的档 |
| `candidates` | 命中那一档的前 `limit` 个；`none` 时为空数组。每个候选 `code`、`name`、`match`，有值时另带 `abbr`、`spec`（存货规格 `cInvStd`）、`unit`（存货主计量单位 `cComUnitCode`）、`class_code`（分类）、`disabled` |
| `more` | 命中那一档超过 `limit` 个 |

停用的判断：客户、供应商 `dEndDate`、存货 `dEDate`、部门 `dDepEndDate`、仓库 `dWhEndDate` 不晚于登录日期；会计科目 `bclose = 1`（科目另外只取末级 `bend = 1`）；项目 `bclose = 1`；操作员已停用。没有这类列的档案不排除。会计科目按登录日期的年份（会计年度）查，同 `archives/list`。

写单据时字段该查哪个档案，看 meta 的 `field_refs` 和 `gl_field_refs`（§21）。客户端命令：`resolve`。

## 25. 裁剪响应 `fields`、`compact`

仅 API。每个 `/v1/co/*` 的 POST 路由都收两个查询参数，用来减小响应体：

| 参数 | 说明 |
| --- | --- |
| `fields` | 逗号分隔的键名，最多 100 个，每个是 `[A-Za-z0-9_]+`，可带前缀 `head.`、`lines.`、`items.`、`fields.`、`docs.head.`、`docs.lines.`。不分大小写 |
| `compact` | 布尔，缺省 `false`。`true` 时去掉值为 `null`、空串（去掉空白后）、`[]`、`{}` 的键；`0` 和 `false` 保留 |

两者只作用在自由形态的容器上：`head`（对象）、`lines`（对象数组）、`items`（对象数组）、`fields`（`archives/get` 的对象）、预演的 `docs[].head` / `docs[].lines`。信封上的键（`ok`、`type`、`id`、`code`、`next`、`watermark` 等）永远不删。

- 不带前缀的名字作用于所有容器，带前缀的只作用于对应容器（`head.` 只管顶层 `head`，`docs.head.` 只管预演的 `docs[].head`）。例如 `fields=head.ccuscode,lines.cinvcode,lines.iquantity`。
- 容器没有适用的名字时原样保留：`fields=lines.cinvcode` 只裁 `lines`，`head` 不动。
- 有适用的名字但一个都不在容器里时：对象容器变成 `{}`；数组容器每行变成 `{}`，行数不变。容器不会被删掉。
- `fields` 语法不对（超过 100 项、名字含其他字符、前缀不认识）：400 `bad_request`，`field` 为 `fields`。
- 先裁剪，再按响应模型校验，必填的信封字段不受影响。
- `vouchers/close`、`arap/writeoff`、`arap/writeoff/cancel`、`arap/voucher` 的响应是固定结构：带 `fields` 400「该接口不支持 fields」（`field` 为 `fields`），带 `compact=true` 400「该接口不支持 compact」（`field` 为 `compact`）。这些路由的预演响应可以裁剪。
- `vouchers/load_many`、`archives/get_many` 只裁每一项的顶层键（§28）。

例：`POST /v1/co/vouchers/list?fields=id,code,cus_code,doc_date&compact=true`。

## 26. 字段标签 `meta/fields`

读路由，要登录；除能登录外不查功能权限，不进写闸门。`meta`（§21）只给字段名；中文名、类型、必填、枚举值取决于账套自己的单据模板（各账套会改自定义项名称、加必输项），所以由本路由按账套现查。标签全部现查，只有总账凭证的标签是固定的。

请求（公共字段之外），`type`、`archive`、`gl` 三选一：

| 字段 | 说明 |
| --- | --- |
| `type` | 单据类型（同 `vouchers/load`） |
| `op` | 只配合 `type`：`create`（缺省）、`update`、`generate` |
| `source` | `op` 为 `generate` 时必填：来源单据类型；其他情况不能给 |
| `archive` | 档案类型 |
| `gl` | `true`：总账凭证 |

单据的响应：

```json
{"ok": true, "type": "sale_order", "op": "create", "vt_id": 95, "card": "17", "vt_source": "card_default",
 "head": [{"name": "ccuscode", "label": "客户编码", "type": "string", "required": false, "max_length": 20},
          {"name": "cbustype", "label": "业务类型", "type": "enum", "required": true,
           "enum": [{"code": "普通销售", "name": "普通销售"}]},
          {"name": "cdefine1", "label": "表头自定义项1", "type": "string", "required": false, "max_length": 20}],
 "lines": [{"name": "iquantity", "label": "数量", "type": "decimal", "required": true}],
 "fields_revision": "…"}
```

（标签、模板号为示意，实际取自账套。）

- **字段名**恰好是桥对该类型、该操作接受的可写字段，与 `meta` 的 `kinds[].writable` 同源（自定义项、自由项区间已展开成 `cdefine1`…`cdefine16` 等）。
- **模板（VT）**：取桥写这类单据时用的模板，按顺序取第一张在本账套有模板行的：`fixed`（桥固定的模板号，如到货单、采购退货单、专用采购发票、无来源采购入库、质量单据）→ `user_default`（经 U8 组件取缺省显示模板的类型：销售类、采购订单、请购单，先看操作员自选的模板 `Voucher_UserDefaultTemplate`）→ `card_default`（该卡片号的缺省模板 `vouchers.DEF_ID`）。`vt_source` 说明用的是哪种；没有卡片号或都取不到时为 `none`，此时 `vt_id` 为 null、标签全为 null。`card` 是 U8 的卡片号。
- **标签**来自模板行（`voucheritems` + `voucheritems_lang`，简体中文，按字段名和表头 / 表体匹配，不分大小写）；模板里没有的字段 `label`、`type` 为 null，字段照样列出。
- **类型**：`bool`、`string`、`int`、`decimal`、`date`；模板标为枚举的字符串字段是 `enum`。模板标为枚举、`AA_Enum` 里有取值的字段都带 `enum`（`code`、`name`，按顺序），非字符串的枚举字段 `type` 保留原类型。不认识的类型按 `string`。`max_length` 有才给。
- **必填** `required`：模板设为必输，或桥自己要求（meta 的 `required`）。
- `line_control`：修改、生单时表体的控制字段（`op`、`line_id`、`sort_seq`、`source_line_id`、`quantity`），与 `lines` 分开列出，结构相同、标签固定；新增时为空数组。
- `fields_revision`：返回的字段列表的 SHA-256，模板改了就变。

档案（`archive`）的响应是 `{"ok", "archive", "fields": [...], "fields_revision"}`：字段名是 `meta` 的 `archives[].writable` 标签；`label` 按标签 → 表列 → U8 列字典（`AA_ColumnDic_base.cCaption`）现查，取不到为 null；`type` 为 null，没有枚举；`required` 是桥新增时要求的标签（名称，以及开户银行的账号、所属银行、币种，计量单位组的类型，货位的仓库，计量单位的计量单位组，客户和供应商银行账户的开户行，项目的项目大类）。只读档案 `fields` 为空数组。

总账凭证（`gl: true`）：`{"ok", "gl": true, "head", "lines", "cash_flow", "fields_revision"}`，字段名同 `meta` 的 `gl`（分录的 `cash_flow` 类型为 `array`），标签固定（凭证类别、日期、附件数；科目、摘要、借方、贷方、部门、人员、客户、供应商、项目大类、项目、结算方式、票据号、票据日期、币种、汇率、数量、现金流量；现金流量里的流量项目、借、贷），必填同 `gl.required_head` / `required_line`。

错误（400 `bad_request`，带 `field`）：`type` / `archive` / `gl` 都没给或给了不止一个；单据类型、档案类型不认识（`hint` 指向 `/v1/co/meta`）；该类型不支持这个操作（`op`）；`generate` 缺来源、来源不对、非 `generate` 带了来源（`source`）。经 API 时这些组合先由请求模型检查，`message` 为「请求参数无效：…」。

客户端命令 `meta-fields`；MCP 的 `u8_describe type=…` / `archive=…` / `gl=true` 一并返回。

## 27. 单据查询 `vouchers/search`

读路由。按条件找某类单据，与 `vouchers/list`（§16）同一套取数：相同的条目、相同的记录级数据权限（§22）、按主键续读。增量同步用 `vouchers/list` 的 `changed_since`，本路由不给 `watermark`。

请求（公共字段之外）：

| 字段 | 说明 |
| --- | --- |
| `type` | 必填，同 `vouchers/list` 的类型 |
| `code_like` | 单据编号包含这段文字，1 到 40 个字符（`%`、`_` 按字面匹配） |
| `partner` | 客户或供应商编码；收付款单、应收应付单是往来单位（`cDwCode`） |
| `dept`、`person`、`maker` | 部门、业务员、制单人 |
| `warehouse` | 表头仓库 |
| `inventory` | 存货编码：明细里有这个存货的单据（表头有存货列的类型按表头） |
| `date_from`、`date_to` | 单据日期，`yyyy-MM-dd`，含两端 |
| `verified`、`closed` | 布尔：只要已审核 / 未审核、已关闭 / 未关闭 |
| `after` | 上一页的 `next` |
| `limit` | 1 到 200，缺省 50 |
| `defines` | 表头自定义项条件，1 到 4 个键，见下 |

除 `type` 外都可省略。文字条件都是 1 到 60 个字符。该类型没有对应的列时 400「该单据类型不支持按 <字段> 搜索」，`field` 为该字段；不支持的类型 400，`field` 为 `type`。

**`defines`**：按表头**文本**自定义项找单据（例如录在销售订单表头自定义项 1 里的纸质合同号）。键是 `define1`–`define3`、`define8`–`define14`；`define4`、`define6` 是日期，`define5`、`define15` 是整数，`define7`、`define16` 是小数，不能按文本搜索。值的写法：

| 值 | 含义 |
| --- | --- |
| `"HT202609039"` 或 `{"eq": "HT202609039"}` | 规整后相等 |
| `{"like": "框架"}` | 包含（`%`、`_`、`[` 按字面匹配） |
| `{"prefix": "HT2026"}` | 开头是 |

规整：全角空格（U+3000）和不换行空格（U+00A0）换成半角空格，再去掉两端的半角空格；请求值和列值都这样处理后再比较（中间的全角空格同样当半角空格比）。其他空白字符原样保留。规整后的值 1 到 120 个字符，不含控制字符。多个键之间、与其他条件之间都是 AND。

- 键不认识（`define17`、`define01` 等，须恰好是 `define` 加编号）或是日期、数字列：400「未知的表头自定义项 …」/「… 是日期或数字列，不能按文本搜索」，`field` 为 `defines.<键>`，`hint` 列出可用的键（由桥判断，API 只核对值的写法）。
- 值不合法（空、太长、对象不是恰好一个 `eq` / `like` / `prefix`）：400，`field` 为 `defines.<键>`；`defines` 不是对象、是空对象或多于 4 个键：400，`field` 为 `defines`。
- 表头没有 `cDefine1`–`16` 的类型：400「该单据类型没有表头自定义项 <键>」，`field` 为 `defines`。支持的类型：销售（`sale_order`、`dispatch`、`sale_return`、`sale_invoice`）、采购（`purchase_order`、`arrival`、`purchase_return`、`purchase_invoice`、`purchase_requisition`）、库存（`purchase_in`、`other_in`、`other_out`、`product_in`、`material_out`、`sale_out`、`transfer`、`transfer_request`、`shape_change`、`stock_check`、`position_adjust`）、质检（`qm_*` 八种）、收付款单和应收应付单（`ar_receipt`、`ap_payment`、`ar_bill`、`ap_bill`）、出入库调整单（`ia_adjust`）。不支持：生产订单（列名为 `Define1`–`16`）、物料清单、采购结算单（`purchase_settle`）、存货调价单（`inventory_price_adjust`）。

响应 `{"ok": true, "type", "items", "next"}`：`items` 按主键排序，每项同 `vouchers/list` 的完整条目，有往来单位列的类型另有 `partner_name`；请求带了 `defines` 时每项另有 `defines`（只含请求里的键，值是表头上规整后的值，空为 `null`）。还有下一页时 `next` 是本页最后的主键。`fields`、`compact`（§25）作用在 `items` 上。

按合同号找销售订单：

```json
{"acc": "801", "operator": "op001", "password": "...", "type": "sale_order",
 "defines": {"define1": "HT202609039"}}
```

```json
{"ok": true, "type": "sale_order",
 "items": [{"id": 9000000002, "code": "0000000002", "doc_date": "2026-09-29", "cus_code": "C900001",
            "partner_name": "甲公司商贸有限公司", "verified": true, "closed": false, "...": "...",
            "defines": {"define1": "HT202609039"}}],
 "next": null}
```

客户端命令 `search`：`--define define1=HT202609039`（相等）、`--define-like define10=框架`、`--define-prefix define1=HT2026`，都可重复，同一个键只能给一次。

## 28. 批量读取 `vouchers/load_many`、`archives/get_many`

读路由。一次读同一类型的几张单据或几条档案，整个请求只登录一次。

**`vouchers/load_many`**：请求 `type`、`ids`（1 到 20 个主键，不能重复）。经 U8 组件读取的类型（销售、采购、库存、应收应付的单据）在写线程池上逐张读、逐张持单据锁，一次最多 5 张，多了 400「该类型逐张用 COM 读取，一次最多 5 张」（`field` 为 `ids`）；按 SQL 读取的类型（采购发票、生产订单、物料清单、报检单、检验单、不良品处理单）在读线程池上，最多 20 张。每张的读取和权限检查与 `vouchers/load` 相同。

**`archives/get_many`**：请求 `archive`、`codes`（1 到 20 个编码，不能重复，写法同 `archives/get` 的 `code`），只读数据库。

响应按请求顺序，每项是单张读取的响应体或失败项：

```json
{"ok": true, "type": "sale_order",
 "items": [{"ok": true, "type": "sale_order", "id": 9000000002, "code": "0000000002", "head": {"...": "..."}, "lines": ["..."]},
           {"id": 9000000003, "error": {"code": "not_found", "message": "单据不存在"}}]}
```

`archives/get_many` 同形：`{"ok", "archive", "items"}`，失败项是 `{"code": "<编码>", "error": {"code", "message"}}`。

- 单项的 4xx（`not_found`、`no_permission`、U8 拒绝等）只记在该项的 `error` 里，整个响应仍是 200。
- 请求级错误照常返回：请求体不合法（400，`field` 如 `ids`、`codes.2`）、登录失败、许可满、繁忙等。
- 任何一项遇到 5xx（服务不可用、结果未知、内部错误），整个请求按该错误失败；改用单张读取定位。
- `fields`、`compact`（§25）只作用在每一项的**顶层键**上，`items[].head`、`items[].lines`、`items[].fields` 不裁剪；每一项总会保留 `id`、`code`、`ok`、`error`。

客户端命令 `load-many`、`arc-get-many`。

## 29. 期初记账与期初单据 `openings/post`、`openings/arap`

写路由。模块启用后须先做「期初记账」才能开展该模块的日常业务（例如采购未期初记账时 U8 拒绝保存采购发票）。`openings/post` 做采购管理期初记账（`module=pu`，U8 菜单 `PU0206`）和存货核算期初余额记账（`module=ia`）及其取消；`openings/arap` 录入应收应付期初单据。其他模块见 `limitations.md`。

**第二级写入。** 本节、§31、§32 的路由复现 U8 界面执行的 SQL（实测核对），属于第二级写入（`limitations.md`「写入分级」，配置见 `configuration.md`）。两道条件依次检查，含预演，都在登录 U8 之前：

- 桥 `config.json` 的 `enableReplicatedWrites` 为 false（缺省）：403 `feature_disabled`，消息为「第二级写入未开启：」加下面的原文。
- 开关打开、但账套不在 `testAccounts` 里：403 `test_account_only`，消息原文：期初记账「期初记账只对配置为测试账套的账套开放」，期初单据「期初单据只对配置为测试账套的账套开放」，月末结账「月末结账只对配置为测试账套的账套开放」，存货核算「存货核算记账和期末处理只对配置为测试账套的账套开放」。

字段校验（400）在这两道条件之前，任何账套的坏请求都是 400。健康检查的 `replicated_writes` 反映开关状态。这类写入用于测试账套；正式账套请在 U8 客户端操作。

请求（不带 `type`、`id`；公共字段之外）：

```json
{"module": "pu", "action": "post"}
```

| 字段 | 说明 |
| --- | --- |
| `module` | 必填。`pu`（采购管理）或 `ia`（存货核算），其他值 400（`field` 为 `module`，`hint` 列出支持的模块） |
| `action` | 必填。`post` 期初记账，`unpost` 取消记账；其他值 400（`field` 为 `action`） |
| `dry_run` | 可选，`rollback` 模式（§23）：检查、改标志、再读都在事务里做，然后回滚，结果在 `detail.opening` |

另收幂等键（§20）。

### 采购管理期初记账（`module=pu`）

登录子系统 PU；功能权限 `PU0206`（记账与取消记账同一个 id，账套主管放行），没有时 403 `no_permission`。

采购期初记账只是一个标志：记账把 `GL_mend` 启用年度中启用月份之前各期（含第 0 期）的 `bflag_PU` 置 1，取消记账改回 0，与 U8 界面执行的 SQL 一致。启用日期取 `AccInformation` 的 `dPUStartDate`，启用年度、启用月份由它算出。

闸门（在请求连接的一个事务里以 `UPDLOCK, HOLDLOCK` 读 `GL_mend` 该年度各期，均为 409 `state_mismatch`）：

- 没有启用日期：「采购管理未启用」；`GL_mend` 没有启用年度的第 0 期：请在 U8 客户端处理。
- `post`：第 0 期已是 1「采购期初已记账」；启用月份起（不早于第 1 期）有 `bflag_PU=1`「采购已有月份结账，不能期初记账」（启用月份之前各期的标志在启用模块时就会置上，不算结账）。
- `unpost`：第 0 期是 0「采购期初未记账」；启用月份起有已结账的月份「采购已有月份结账，不能取消期初记账」；存货核算第 0 期已记账（`bflag_IA=1`）「存货核算已期初记账，不能取消采购期初记账」；`PurBillVouch` 有任何发票（含期初发票）「已有采购发票，不能取消期初记账」。

改完在同一事务里再读第 0 期，不是目标状态则回滚，409 `u8_rejected`；提交后在新连接上回读，读不出或不对是 504 `outcome_unknown`（已提交，先用 `reports/opening_balance` 核对，不要直接重投）。

响应：

```json
{"ok": true, "module": "pu", "action": "post", "posted": true, "opening_year": 2026, "start_date": "2026-01-01"}
```

`posted` 是操作后第 0 期的标志；`opening_year` 是启用年度，`start_date` 是采购管理启用日期。锁键 `opening:pu`，受全局写闸门约束。预演的 `detail.opening` 是上面这几个字段（`posted` 为操作后会是的状态）。

客户端命令：`openings-post --module pu`（取消记账加 `--unpost`；`--dry-run`、`--idempotency-key` 同其他写命令）。

### 存货核算期初记账（`module=ia`）

对应 U8 存货核算「期初余额」的「记账」（从库存期初结存单取数、汇总、置标志）和「恢复」，脚本 `co/bridge/sql/ia/qc_keep.sql`、`qc_recover.sql` 在请求事务里整批执行。

- 登录子系统 IA；**登录日期须在存货核算启用年度内**（建议用启用日期 `dIAStartDate`），否则 400（`field` 为 `date`，`hint` 给出启用日期）。功能权限 `ASM3102`（期初余额的记账 / 恢复，同一个 id），账套主管放行，没有时 403 `no_permission`。
- `post`：取已审核（`cHandler` 非空）的期初结存单行（排除应税劳务存货和直运业务），写 `IA_Subsidiary` 第 0 期（`iMonth=0`、`cVouType='34'`；金额取 `iPrice`，为空取 `round(数量 × iUnitCost, 2)`，都没有记 0），按核算方式（按仓库 / 按存货 / 按部门，含自由项、批次）汇总写 `IA_Summary` 第 0 期；然后置 `bQCInput=True` 和启用月份之前各期 `bflag_IA=1`，写年度开账标志和历史设置表。启用年度已有结账月份照样可以记账；没有已审核的期初结存单也能记账（期初为 0）。
- `unpost`：只删启用年度第 0 期的期初明细和汇总，清第 0 期标志和上述收尾数据。**不是** U8 的「取消开账」（删除整个年度数据并清启用日期），桥不做。
- 409 `state_mismatch`：存货核算未启用；库存管理未启用，或库存与存货核算启用日期不同（「不能期初取数」）；总账期间表没有启用年度第 0 期；记账时「存货核算期初已记账」；取消时「存货核算期初未记账」「存货核算已有月份结账，不能取消期初记账」「存货核算本年已有日常数据（汇总或明细），不能取消期初记账」；有先进先出 / 后进先出计价的期初行时「接口暂不支持」（桥不写计价辅助表 `IA_ValuationAss`，请在 U8 里记账）。脚本执行后第 0 期标志不是目标状态时回滚，409 `u8_rejected`。
- 提交后在新连接上回读第 0 期标志和期初汇总行数，与脚本计数不符或读不出是 504 `outcome_unknown`（已提交，先核对，不要直接重投）。脚本超时 503 `ia_timeout`（已回滚），等待时间同存货核算长任务（`iaCommandSeconds` + 60 秒，客户端按 `long_timeout` 读）。

响应另有 `counts`（脚本的诊断计数）：

```json
{"ok": true, "module": "ia", "action": "post", "posted": true, "opening_year": 2026, "start_date": "2026-01-01",
 "counts": {"st34_verified": 2, "subsidiary_m0_34": 2, "summary_m0": 2, "fifo_lines_qcass_skipped": 0, "gl_mend_p0": 1, "gl_mend_lt_start": 1}}
```

记账的计数：`st34_verified` 取数的期初结存单行数，`subsidiary_m0_34` 写入的第 0 期明细行数，`summary_m0` 第 0 期汇总行数，`fifo_lines_qcass_skipped` 先进先出 / 后进先出的期初行数（大于 0 时已 409），`gl_mend_p0` 第 0 期标志，`gl_mend_lt_start` 启用月份之前已置标志的期间数。取消的计数：`summary_m0_deleted`、`subsidiary_m0_deleted`、`summary_item_m0_deleted`、`summary_m0_left`（应为 0）、`gl_mend_p0`（应为 0）、`bQCInput_true`。锁键 `opening:ia`，受全局写闸门约束。预演的 `detail.opening` 同样带 `counts`。

客户端命令：`openings-post --module ia`（登录日期用 `--date` 给存货核算启用日期）。

### 应收应付期初单据 `openings/arap`

写路由，第二级写入（见本节开头）。对应 U8 应收款管理、应付款管理「期初余额」中期初单据的录入（菜单 `AR0306` / `AP0306`）：新增、删除、审核、弃审。期初单据是 `Ap_Vouch` 中 `bStartFlag=1` 的应收单 `R0` / 应付单 `P0`，只有表头（`Ap_Vouchs` 无行），单据日期为模块启用日期的前一天。新增、审核、弃审、删除走与 `ar_bill` / `ap_bill` 相同的 U8 组件（`UFAPBO`）；普通的 `vouchers/*` 路由照旧拒绝期初单据。

请求（不带 `type`；公共字段之外）：

```json
{"side": "ar", "action": "create", "partner": "C900001", "amount": 1200.00, "account": "112201"}
{"side": "ar", "action": "verify", "id": 41}
```

| 字段 | 说明 |
| --- | --- |
| `side` | 必填。`ar` 应收、`ap` 应付，也决定登录子系统（AR / AP） |
| `action` | 必填。`create` 新增，`delete` 删除，`verify` 审核，`unverify` 弃审 |
| `id` | `delete`、`verify`、`unverify` 必填：期初单据主键（`Ap_Vouch.Auto_ID`）。`create` 带了 400 |
| `partner` | `create` 必填：客户编码（`ar`）或供应商编码（`ap`），最长 20；启用日期前不能已停用 |
| `amount` | `create` 必填：原币金额，非 0、绝对值不超过 1000000000000、最多两位小数。负数表示反方向余额（应收为贷方，如预收；应付为借方，如预付）：同 U8 存为正数，借贷方向 `bd_c` 取反 |
| `account` | `create` 必填：科目编码，须为启用年度、末级、本系统受控（应收 / 应付）的科目 |
| `department`、`person` | 可选：部门编码（末级）、业务员编码 |
| `digest` | 可选：摘要，最长 120；省略为「期初应收」/「期初应付」 |
| `currency`、`exch_rate` | 可选：币种名称，省略为本位币。本位币的汇率只能省略或为 1；外币必须给汇率，本币金额为 `amount` 的绝对值乘汇率、四舍五入到两位 |
| `dry_run` | 可选，`rollback` 模式（§23） |

`delete`、`verify`、`unverify` 只收 `id`，带了 `create` 的字段 400。没有日期字段：单据日期固定为启用日期前一天。另收幂等键（§20）。功能权限 `AR0306` / `AP0306`（账套主管放行），没有时 403 `no_permission`。锁键与 `ar_bill` / `ap_bill` 相同（新增 `new:<类型>`，其余 `<类型>:<id>`），受全局写闸门约束。

期初形态：U8 的期初单据在往来明细中是第 0 期，登记和审核日期为启用日前一天。桥在同一事务里审核成功后把本单的审核行改成这一形态，并把 `Ap_Vouch.dVerifyDate` 改为启用日前一天；弃审时先把本单审核行改回登录月，再由 U8 组件照常弃审。新增保存后在同一事务里核对 `bStartFlag` 已写入，否则回滚 409。

闸门（均为 409 `state_mismatch`，状态检查在请求连接的事务里）：

- 模块启用月份已结账（`GL_mend` 启用年度、启用月份的 `bflag_AR` / `bflag_AP` 为 1）：「应收款管理启用的第一个月已经结账，不能修改期初单据」/「应付款管理…」，四种 `action` 都拒绝。
- 没有启用日期：「应收款管理未启用」/「应付款管理未启用」。
- `delete`、`verify`、`unverify`：主键不存在 404 `not_found`「单据不存在」；不是期初单据「不是期初单据」。
- `verify`：「单据已审核」。`unverify`：「单据未审核」。`delete`：已审核「单据已审核」。
- `unverify`、`delete`：「单据已生成凭证，不能弃审 / 不能删除」；「只能处理手工录入的期初单据，…」；「单据已核销，…」；存在本单审核行以外的往来明细（删除：任何往来明细）。

档案检查（400）：币种、部门（末级）、业务员、往来单位、科目不存在或不合要求。

响应同 `ar_bill` / `ap_bill` 的新增、审核、删除（§7、§6、§9），另带 `side` 和 `opening: true`；新增还带 `start_date`（启用日期）和 `date`（单据日期）：

```json
{"ok": true, "type": "ar_bill", "id": 41, "code": "0000000041", "state": {"verified": false, "verifier": "", "verified_at": ""}, "side": "ar", "opening": true, "start_date": "2026-01-01", "date": "2025-12-31"}
```

提交后回读不出是 504 `outcome_unknown`（已提交，先核对再重投）。预演返回 §23 的预演响应（`action` 为 `opening_arap_<action>`）。期初余额用 `reports/opening_balance`（`module=arap`，§19）核对。

客户端命令：`openings-arap --side ar --action create --partner C900001 --amount 1200 --account 112201`；`--action delete|verify|unverify --id N`（`--dry-run`、`--idempotency-key` 同其他写命令）。

## 30. 账套体检 `reports/account_readiness`

读路由。检查新建或引入的账套能否用于写操作、缺什么、怎么补。十项检查只读目录、配置表和小表，不扫单据表，不写数据，正式账套上也可运行。补齐流程见 `getting-started.md`。

请求（不带 `type`、`id`；公共字段之外）：

| 字段 | 说明 |
| --- | --- |
| `as_of` | 可选，`yyyy-MM-dd`。年度账、工作日历按这一天和服务器当天两个日期检查；缺省为登录日期 `date` |

其他字段（含 `fiscal_year`、`after`、`limit`）一律 400。

登录子系统 SA（同 `close_status`）。`date` 不在任何已建年度内时 U8 登录失败（422 `login_failed`「不存在的年度」），到不了体检；此时用已有年度中的一天登录（例如起始年度的最后一天），体检仍按服务器当天（`GETDATE()`）检查缺的年度。

功能权限：账套主管，或有总账「结账」`GL1512` 或「凭证整理」`GL0202` 的操作员，没有时 403 `no_permission`。不按记录过滤。审计动作 `report_account_readiness`。

登录和权限通过即返回 200，问题都在 `checks` 里，不报 409：

```json
{"ok": true, "overall": "fail", "as_of": "2026-06-15", "server_today": "2026-06-15", "acc": "801",
 "checks": [
  {"id": "patches", "title": "账套补丁", "status": "warn", "detail": "账套库补丁记录 10 条，系统库 12 条",
   "fix_hint": "用 DBEnginSys.exe -all 只勾本账套重放补丁…", "docs_anchor": "getting-started.md#patches", "safe": true},
  {"id": "years", "title": "年度账", "status": "ok", "detail": "已建年度 2026–2026",
   "fix_hint": null, "docs_anchor": "getting-started.md#years", "safe": true},
  {"id": "calendar", "title": "工作日历", "status": "fail", "detail": "SYSTEM 日历没有日子",
   "fix_hint": "在基础档案中延长工作日历…", "docs_anchor": "getting-started.md#calendar", "safe": true}
 ]}
```

（示例只列三项；实际 `checks` 总是十项，顺序同下表。）

| 字段 | 说明 |
| --- | --- |
| `overall` | 各项中最差的一个：`fail` > `unknown` > `warn` > `ok` |
| `as_of` | 实际检查的日期（请求的 `as_of`，缺省为登录日期） |
| `server_today` | 数据库服务器的当天（`GETDATE()`） |
| `acc` | 账套号 |
| `checks[].id` | 检查项，见下表；也是 `getting-started.md` 中对应小节的锚点 |
| `checks[].status` | `ok`；`warn`（能用，但有缺省值或余量不足）；`fail`（会让写操作失败）；`unknown`（查不了，例如桥的 SQL 登录读不到 `UFSYSTEM`） |
| `checks[].detail` | 只有计数、日期、缺的编号，不含业务数据 |
| `checks[].fix_hint` | 修复建议（中文一句）；`ok` 时为 null |
| `checks[].docs_anchor` | `getting-started.md#<id>` |
| `checks[].safe` | 恒为 true：检查只读 |

| `id` | 查什么 | 不通过时 |
| --- | --- | --- |
| `patches` | 账套库有 `UA_PatchList`，`WFAudit` 有补丁加的 `signatureid` 列；`UA_PatchList` 条数与 `UFSYSTEM..UA_PatchList` 比 | `fail`：没有补丁表，或账套库 0 条而系统库有。`warn`：缺 `signatureid` 列，或账套库条数少于系统库（可能没重放完；系统库也记录只改系统库的补丁）。修：用 U8 的 `DBEnginSys.exe -all` 只勾本账套重放补丁 |
| `years` | `UFSYSTEM..UA_Period` 本账套未删除的年度覆盖 `as_of` 和服务器当天；`detail` 给出已建年度范围 | `fail`：任一日期不在已建年度内。修：系统管理建立年度账 |
| `calendar` | `bas_calendardetail` 中 SYSTEM 日历（`CalendarId=1`）的最后一天 | `fail`：表不存在、没有日子，或早于 `as_of` / 当天；`warn`：离当天不到 30 天。修：基础档案延长工作日历 |
| `yearly_config` | 按年度的配置表（`GL_CashItemDataSource`、`Ap_InputCode`、`AP_OppCodeSet`、`AP_CtrlCodeSet`、`Ap_SStyleCode`、`code`、`GradeDef_Base` 的科目编码级次、`AccInformation_Year`）在每个已建年度的行数；年度名单取 `UA_Period`，读不到时取 `GL_mend` | `fail`：起始年度有行、之后某个不晚于 `as_of` 和服务器当年的年度为 0（`detail` 列出「表 年度」）；为 0 的只有更晚的年度时 `warn`；只有一个年度时 `ok`。修：建立年度账会拷 `code` / `GradeDef` / `AccInformation_Year`；应收应付科目、现金流量取数、结算方式科目在 U8 对应设置中补齐 |
| `pu_opening` | 采购管理启用日期（`AccInformation` 的 `dPUStartDate`）与启用年度 `GL_mend` 第 0 期的 `bflag_PU`，同 `reports/opening_balance` | `fail`：没有启用日期，或期初未记账。修：`openings/post` `{"module":"pu","action":"post"}`（§29） |
| `vendor_extradefine` | 账套库有 `Vendor_extradefine` 表 | `fail`：没有这张表时供应商档案写入失败。修：建空表 `Vendor_extradefine`（主键 `cVenCode`，先备份） |
| `modules` | `UFSYSTEM..UA_Account_sub`（`iYear=9999`）中 SA、PU、ST、AR、AP、GL、QM、MO、BO 都有启用日期；IA 可选 | `fail`：必需的子系统没启用；`warn`：只缺 IA。修：系统管理 / 企业应用平台的系统启用（没有 API） |
| `prior_gl_close` | 同记账闸门（§14 `post`）：取 `as_of` 与服务器当天中较晚的那天的年、月，查 `GL_mend` 该年 1 到 12 期的第一个未结账期间，以及上年度和更早年度的未结账月份 | `fail`：第一个未结账期间早于上月（`detail` 如「本年 1–9 期总账未结账，本月凭证不能记账」）、本月已结账，或本月是 1 月而上年度有未结账月份；`warn`：只差上月没结账（月初的正常时间差），或本月能记账、但更早年度还有未结账月份，或 `GL_mend` 没有该年度。修：按顺序在 U8 里结账，或在测试账套上用 `periods/close` 的 `through` 逐月结到上月（`hint` 给出具体的 `fiscal_year`、`period`；§31） |
| `workflow` | 来料检验单（QM03）、产品检验单（QM04）各有启用的审批流（`Table_WorkFlowRelease` `Status=0`，判断同 §13）；登录操作员在 `UserHrPersonContro` 中对应了人员 | `fail`：缺任一项。修：在 U8 审批流设计器发布；在 U8 中为操作员关联人员 |
| `defaults` | 本位币（`foreigncurrency.iotherused=-1`）、缺省采购类型（`PurchaseType.bDefault=1`）、末级收发类别收、发各至少一个（`Rd_Style`）、单据编号规则（`VoucherNumber`） | `fail`：没有本位币；`warn`：缺其余任一项。修：档案接口 `currency`、`purchase_type`、`rd_style`（§15），编号规则用 U8 单据编号设置 |

读 `UFSYSTEM` 的三项（`patches` 的系统库条数、`years`、`modules`）用请求连接的三段式名称，与权限快照（`UFSYSTEM..UA_HoldAuth`）同一个登录；读不到时该项 `unknown`，`fix_hint` 提示给桥的 SQL 登录授予 `UFSYSTEM` 的 `SELECT`。其他项查询出错同样只让该项 `unknown`。整个体检约十几条小查询，与其他报表共用 75 秒的期限。

`ok` 只说明这十项条件具备，不保证所有写操作都能成功。

## 31. 月末结账 `periods/close`

写路由，第二级写入（§29 开头）。

总账记账只能记本年第一个未结账期间（§14 `post`），新建账套从起始年度起各月未结账时本月凭证无法记账（体检项 `prior_gl_close`，§30）。采购、销售、应收、应付、总账的月末结账改的是 `GL_mend` 该模块该期的结账标志（`bflag`、`bflag_PU`、`bflag_SA`、`bflag_ST`、`bflag_IA`、`bflag_AR`、`bflag_AP`）；桥做完下列检查后改标志，库存和存货核算另写月结数据。

请求（不带 `type`、`id`；公共字段之外）：

```json
{"module": "gl", "fiscal_year": 2026, "period": 9, "action": "close"}
```

```json
{"action": "close", "through": true, "fiscal_year": 2026, "period": 9}
```

| 字段 | 说明 |
| --- | --- |
| `module` | `pu` 采购、`sa` 销售、`st` 库存、`ia` 存货核算、`ar` 应收、`ap` 应付、`gl` 总账。其他值 400（`field` 为 `module`，`hint` 列出可用值）。`through` 为 true 时可省略（给了也须合法，不使用） |
| `fiscal_year` | 必填，会计年度（4 位整数，`GL_mend.iyear`）。与公共字段 `year`（账套库年度）无关 |
| `period` | 必填，1 到 12。第 0 期（期初）不收，期初记账见 §29 |
| `action` | 必填，`close` 结账，`reopen` 取消结账 |
| `through` | 可选布尔，只能和 `close` 一起用。按 U8 的结账顺序（采购、销售 → 库存 → 存货核算 → 应收、应付 → 总账），把各**已启用**模块从 `GL_mend` 最早年度起到 `fiscal_year` 年 `period` 期为止的每个未结账期间逐个结账 |
| `dry_run` | 可选，`rollback` 模式（§23）：检查、改标志、再读都在事务里做，然后回滚；会结账的期间在 `detail.periods` |

另收幂等键（§20）。已启用模块取 `UFSYSTEM..UA_Account_sub`（`iYear=9999` 有启用日期）；读不到 `UFSYSTEM` 时 409「读不到 UFSYSTEM..UA_Account_sub…」。请求的模块（`through` 时为总账）未启用 409「<模块>未启用」。

登录子系统为模块的子系统号，`through` 用 GL。功能权限（账套主管放行），没有时 403 `no_permission`：采购 `PU0207`、销售 `SA020901`、库存 `ST0304`、存货核算 `IA2007`、应收 `AR0509`、应付 `AP0509`、总账结账 `GL1512`、总账取消结账 `GL1520`；其余模块的取消结账使用结账的 id。`through` 需要所涉每个模块的结账权限。

**闸门。** 一个请求一个事务（含 `through`）：加锁读 `GL_mend` 全部年度各期标志，逐步检查和修改（后面的步骤按改后状态判断），完成后在同一事务里复读，不是目标状态则回滚，409 `u8_rejected`。拒绝都是 409 `state_mismatch`，`message` 以「<模块> <年> 年 <期> 期：」开头；`through` 时指卡住的那一步，整批不做：

- `GL_mend` 没有该年该期：请在 U8 客户端处理。
- 结账：已结账「该期间已结账」；不是该年第一个未结账期间，或是 1 月而上年 12 期（`GL_mend` 有上年时）未结账：「上一期间还没结账（…）」。
- 取消结账：未结账「该期间还没结账」；之后（同年更晚期间或以后年度）还有已结账期间：「只能取消最后一个已结账的期间（…）」。
- 同期其他模块（只看已启用的）。结账前必须已结账：库存要采购、销售；存货核算要采购、销售、库存；应收要销售；应付要采购；总账要其余六个（总账最后结账）。取消结账反之：依赖它的模块同期已结账时拒绝，例如采购取消结账时库存、存货核算、应付或总账已结账（「…本期已结账，不能取消采购管理结账」）。
- 采购结账：启用年度（`AccInformation` 的 `dPUStartDate`）`GL_mend` 第 0 期 `bflag_PU` 不是 1「采购未做期初记账，请先 openings/post」（§29；结账后 U8 不再允许期初记账）；有日期在本月及以前、未复核的采购发票。
- 存货核算结账：启用年度第 0 期 `bflag_IA` 不是 1 时照样结账，响应（预演在 `detail.warnings`）带 `warnings`：`ia_opening_not_posted：…`。可先用 `openings/post` `{"module":"ia","action":"post"}`（§29）期初记账；有月份结账之后仍能记账，但不能再取消。销售没有期初记账闸门。
- 销售结账：本月有未审核的发货单 / 退货单，或未复核的销售发票。
- 应收 / 应付结账：本月有未审核的应收单、应付单、收款单、付款单，或未应收 / 应付审核的销售 / 采购发票；选项「月末结账前全部制单」（`bZDMonth`）打开时，本月审核登记的单据还有未制单的。
- 总账结账：已启用的固定资产、薪资管理、成本管理本期未结账「<模块>本期还没结账，总账最后结」（桥不结这几个模块，`through` 也停在这里，请先在 U8 里结）；本期有未记账凭证（不含作废）「本期还有未记账的凭证，请先记账」；损益类末级科目本期借贷不平、又没有 `coutsign='期间损益'` 的凭证「请先做期间损益结转」。
- 总账取消结账：总账选项「反结账须输入账套主管口令」（`bConselClos`）打开时不做。取消 12 期时，下一年度期间已结账由「只能取消最后一个已结账的期间」挡住；下一年度都未结账、但下一年度第 0 期 `bflag=1` 且已有凭证（多半做过年度结转）时也不做：「下一年度已有凭证（可能已做年度结转），不能取消 12 期结账；请在 U8 里处理」。

**库存结账**（有单据、有结存的月份都做）：同 U8 库存月末结账，写该年该月的五张月结快照，再改 `bflag_ST`。

- 写入：本月的采购入库、其他入库、其他出库、产成品入库、材料出库（不含「假退料」）、销售出库、期初单（`rdrecord01/08/09/10/11/32/34`，排除采购 / 委外 / 存货期初转来的单据）收 +1、发 −1，加上上月快照，按仓库、存货、自由项 1–10、代管供应商、批号汇总成月末结存，写入 `ST_MonthAccount` / `ST_MonthAccounts`（按单据日期）、`ST_MonthAccountV` / `ST_MonthAccountVs`（按审核日期，只算已审核）、`ST_MonthAccountCheck`（再按单据类型分），口径与 U8 自己生成的快照一致（实测核对）。数量、件数按 `AccInformation` 的 `iStrsQuanDecDgt` / `iNumDecDgt`（缺省 2 / 4）逐行舍入；结存为 0 的行不写，除非库存选项 `bKeepBlankMonthData` 打开。先删本月五张表的行再写入（重结同 U8）；写入行数与算出的不符时回滚，500 `internal`（细节进审计）。
- 前提（另见顺序闸门）：截至月末没有未审核的盘点单、调拨单、组装 / 拆卸 / 形态转换单、报废单、货位调整单、库存期初单；本月没有未审核的其他入库、其他出库、销售出库、产成品入库、采购入库（采购 / 委外 / 存货期初转来的除外）、材料出库（假退料和委外期初除外）单。否则 409「…还有未审核的单据：盘点单、本月其他入库单…，请先审核」，一次列全。
- 接口暂不支持（409「…接口暂不支持；请在 U8 里做库存月末结账」）：库存选项月结按收发类别、业务类型或部门汇总；账套有保质期管理的存货；已审核的库存期初单据审核日期与单据日期不在同一个月；首次库存结账时期初单据日期早于 `GL_mend` 最早年度的 1 月 1 日。
- 不检查（U8 结账窗体会查）：货位结存与单据数量不符、其他出库单预算超额、质检人设置、库存期初记账。
- 取消结账：只能取消最后一个已结账月份；同期存货核算（已启用）已结账时拒绝。删除该年该月五张快照表的行再清 `bflag_ST`，删后复查，有剩余则回滚（500 `internal`）。只做单月取消，不做 U8 的年度 / 启用日期回退。

**存货核算结账**：该月有存货核算数据时，同 U8「月末结账」执行 `IA_Close`（结存结转到下月）再置 `bflag_IA`，支持范围同 §32（否则 409「…接口暂不支持」）；没有数据的月份只改标志。

- 前提（409 `state_mismatch`）：该月已做期末处理（§32 `ia/period_end`，汇总表各键都已期末处理）；本月没有已审核、未记账的出入库单据行（先 `ia/post`）；选项「月末结账前检查单据审核」打开时本月也没有未审核的出入库单据行。U8 存储过程自己的拒绝是 409 `u8_rejected`「U8 拒绝：…」。
- 响应（`through` 时在存货核算那几项里）另带 `ia_counts`（脚本诊断计数）。脚本超过 `iaCommandSeconds` 时 503 `ia_timeout`（已回滚，同 §32）；`module=ia` 或 `through` 的请求桥等待 `iaCommandSeconds` + 60 秒，API 读桥用 `U8CO_BRIDGE_LONG_TIMEOUT`。
- 取消结账：只能取消最后一个已结账月份（下月存货核算已结账时拒绝）。有数据的月份执行 `IA_UnClose` 删除结转到下月的数据，再清 `bflag_IA`；没有数据的月份只清标志。
- `through` 经过存货核算有数据的月份时同样执行结账脚本，全部在一个事务里，任何一步被拒整批回滚。存货核算的记账、期末处理不在 `through` 里，须先用 §32 逐月做完。

期间按自然月算（本月 1 日到下月 1 日）。提交后在新连接上回读全部标志，读不出或不对是 504 `outcome_unknown`（已提交，先用 `reports/close_status` 核对，不要直接重投）。

响应：

```json
{"ok": true, "module": "gl", "action": "close", "fiscal_year": 2026, "period": 9, "closed": true}
```

```json
{"ok": true, "action": "close", "through": [{"module": "pu", "fiscal_year": 2026, "period": 9}, {"module": "gl", "fiscal_year": 2026, "period": 9}], "count": 2}
```

```json
{"ok": true, "module": "st", "action": "close", "fiscal_year": 2026, "period": 8, "closed": true, "stock_rows": {"account": 120, "accounts": 120, "v": 120, "vs": 120, "check": 560}}
```

`closed` 是操作后的状态（`reopen` 为 false）。库存另有 `stock_rows`：结账时写入（取消时删除）`ST_MonthAccount`、`ST_MonthAccounts`、`ST_MonthAccountV`、`ST_MonthAccountVs`、`ST_MonthAccountCheck` 的行数；`through` 时在库存各项里各带一份。有提示时另有 `warnings`（字符串数组，以提示码开头，目前只有 `ia_opening_not_posted`），没有则不出现。`through` 按执行顺序列出结了账的期间，没有要结的期间时 `count` 为 0、HTTP 仍为 200。锁键 `period:<模块>`（`through` 锁全部七个），受全局写闸门约束。预演的 `detail.periods` 是同样的 `{module, fiscal_year, period}` 列表（库存项带 `stock_rows`），单个库存请求另有 `detail.stock_rows`；库存快照照样写入、随事务回滚。审计动作 `period_close` / `period_reopen`。

不做：不改 `UFSYSTEM..UA_Account_sub` 的 `iModiPeri`（请求连接从不写 `UFSYSTEM`）；库存的 `ST_MonthAccountsbus` / `ST_MonthAccountVsbus`、`ST_TotalVenSum`；年度结转。见 `limitations.md`。

客户端命令：`periods-close --fiscal-year 2026 --period 9 --module gl`（取消结账加 `--reopen`；逐月结账用 `--through` 代替 `--module`；`--dry-run`、`--idempotency-key` 同其他写命令）。

## 32. 存货核算记账与期末处理 `ia/post`、`ia/period_end`

写路由，第二级写入（§29 开头）。

存货核算月末流程为「正常单据记账 → 期末处理 → 月末结账」，取消时反向。本节两条路由做前两步（月末结账见 §31 `module=ia`）。每一步按 U8 界面的调用顺序和参数（存货核算存储过程加界面 SQL）写成 T-SQL 脚本（`co/bridge/sql/ia/`），在请求事务里整批执行。

**支持范围。** 执行脚本之前检查四项账套选项，任一项不符即 409「…接口暂不支持」，不做任何修改：存货核算方式为按仓库核算（`AccInformation` 的 `cValueStyle`）；每个仓库的计价方式都是全月平均法（`Warehouse.cWhValueStyle`）；暂估方式为单到回冲（`cEstimate`）；销售成本核算方式为发出商品或销售出库单（`bSaleType`）。记账时本月有直接供应的材料出库，脚本执行中 409「…接口暂不支持」，整个事务回滚。标准成本、委外加工等其他设置未实测且不检查，这类账套不要使用本接口。结果已与 U8 界面生成的明细账、汇总表逐键核对。

请求（公共字段之外，不带 `type`、`id`）：

```json
{"fiscal_year": 2026, "period": 9, "action": "post", "on_uncosted": "refuse"}
```

```json
{"fiscal_year": 2026, "period": 9, "action": "run"}
```

| 字段 | 说明 |
| --- | --- |
| `fiscal_year` | 必填，会计年度（4 位整数）。与公共字段 `year`（账套库年度）无关 |
| `period` | 必填，1 到 12 |
| `action` | 必填。`ia/post`：`post` 正常单据记账，`unpost` 恢复记账；`ia/period_end`：`run` 期末处理，`cancel` 取消期末处理。其他值 400（`field` 为 `action`） |
| `on_uncosted` | 仅 `ia/post` 且 `action=post`，可选：`refuse`（缺省）或 `skip`，见下 |
| `dry_run` | 可选，`rollback` 模式（§23）：整段脚本在事务里执行，读出诊断计数后回滚；计数在 `detail.counts` |

另收幂等键（§20）。登录子系统 IA，记账人为登录操作员的 U8 用户名，记账日期为该月最后一天。功能权限（账套主管放行），没有时 403 `no_permission`：正常单据记账 `IA2004`、恢复记账 `IA2005`、期末处理和取消期末处理 `IA2006`（月末结账、取消结账是 §31 的 `IA2007`）。锁键 `ia:<年>-<两位期>`（如 `ia:2026-09`）和 `period:ia`（与存货核算月末结账串行），另持全局写闸门。执行时间随该月单据量增长，大账套可达数分钟，期间同一桥上的其他写请求排不上队，会 503 `busy_timeout`（未执行，可稍后重试）。审计动作 `ia_post`、`ia_unpost`、`ia_period_end`、`ia_period_end_cancel`。

### 正常单据记账 `post`

把该年该月已审核、未记账的出入库单据（采购入库、其他入库、其他出库、产成品入库、材料出库、销售出库，审核日期在本月；未审核的不记）记入存货明细账（`IA_Subsidiary`）并更新汇总表（`IA_Summary`），在单据行上写记账人。销售成本按发出商品时，销售出库按发出商品记，已复核的销售发票同批记账。有单价的入库按单据金额记；有 U8 无法确定成本的存货（U8 界面会要求手工输入单价）时，按 `on_uncosted`：

- `refuse`（缺省）：整笔不记，409 `state_mismatch`「有存货 U8 无法确定成本…」；错误体的 `detail.uncosted` 列出这些存货（最多 20 项，`{wh, inv, batch}`），`detail.uncosted_total` 是总数（§18）。可在 U8 界面记账时手工输入单价，或改用 `skip`。
- `skip`：这些存货（按仓库、存货、自由项、批号整键）不记账，同 U8 界面取消勾选；其余照常记账。未记的单据保持未记账，下次记账再处理。

拒绝（409 `state_mismatch`）：存货核算未启用；该月早于存货核算启用月份；该月存货核算已结账；本月有直接供应的材料出库（「接口暂不支持」，事务回滚）；上面的 `refuse`。

没有可记账的单据时照常 200，`counts.area` 为 0，响应带中文 `message`。

### 恢复记账 `unpost`

整月恢复，同 U8「恢复记账」：删除该月明细账中各类出入库单据（含发出商品、销售发票）的记账行，回退汇总表，清除单据行上的记账人。该月没有已记账单据时照常 200，`counts.restore_rows` 为 0，带中文 `message`。拒绝（409 `state_mismatch`）：该月已做期末处理（先取消期末处理）；发出商品的销售出库还有不在本次恢复范围内的销售发票（例如下月的发票），须先恢复这些发票的记账。

### 期末处理 `run`

同 U8 存货核算「期末处理」：按全月平均法计算各结存键的出库单价，给本月出库单据定成本，按 U8 的规则生成出库调整，把该月汇总表标记为已期末处理（`IA_Summary.iPeriod=1`）。拒绝（409 `state_mismatch`）：该月存货核算已结账；该月还没有记账数据（先 `ia/post`）。

### 取消期末处理 `cancel`

删除期末处理生成的出库调整、清除期末处理定下的出库成本，汇总表改回未期末处理。拒绝（409 `state_mismatch`）：该月存货核算已结账（先 `periods/close` 取消结账）。

### 错误和响应

U8 存储过程自己的拒绝是 409 `u8_rejected`「U8 拒绝：<U8 原文>」。脚本超过 `iaCommandSeconds`（桥 `config.json`，缺省 900 秒；一次请求中的几段脚本共用这个期限）是 503 `ia_timeout`「存货核算处理超时，已回滚；可稍后重试，或调大 iaCommandSeconds」：事务已回滚，未写入，带 `Retry-After`（60 秒）。脚本中其他 SQL 错误是 500 `internal`（细节只进审计），事务回滚。提交后在新连接上回读该月明细账行数和结账标志，读不出或对不上是 504 `outcome_unknown`「…请先在 U8 里核对该月的记账和期末处理」：已提交，先核对，不要直接重投。

```json
{"ok": true, "action": "post", "fiscal_year": 2026, "period": 9, "counts": {"subsidiary_month": 1280, "summary_month": 312, "summary_iPeriod1": 0}}
```

`counts` 是脚本最后一步的诊断计数（名称 → 行数），例如 `area`（本次记账的单据行数）、`restore_rows`（本次恢复的行数）、`subsidiary_month`（该月明细账行数）、`summary_month`（该月汇总表行数）、`summary_iPeriod1`（已期末处理的汇总行数）；各操作的名称不同，只作核对参考，不要依赖固定的键集合。预演的 `detail.counts` 是同样的计数（事务内、回滚之前）。

客户端命令：`ia-post --fiscal-year 2026 --period 9`（恢复记账加 `--unpost`；`--on-uncosted refuse|skip`）、`ia-period-end --fiscal-year 2026 --period 9`（取消加 `--cancel`）；`--dry-run`、`--idempotency-key` 同其他写命令。

## 33. 公司间接口（仅 API）

用于同组公司之间买卖的多个账套。须先配置公司间对照 `U8CO_IC_MAP_FILE`（`configuration.md`「公司间对照」），未配置时 404 `ic_not_configured`「未配置公司间对照」；请求中的账套必须同属一组，否则 400 `ic_group_mismatch`「账套不在同一公司组」。

共同规则：

- 请求带 `logins`：每个账套一份登录 `{acc, operator, password, year?, date?}`（同公共字段，§2），账套不能重复。每个账套都须通过 `U8CO_ACCOUNTS` 和令牌的账套声明，有一个不通过即整个 403 `account_not_allowed`，不访问任何桥（全有或全无）。
- 三条报表路由（对账、汇总、合并试算）按账套返回金额，要令牌有经营管理权限（§17），否则 403 `mgmt_forbidden`。
- 每个账套一路调桥（错误映射同单账套调用），一个请求最多 3 路并行；额外的并行占用全局和本调用方的在途名额，占不到就少开，不报 429。
- 审计每个请求一行：`accs` 列出全部账套，`action` 末尾带各账套的结果（`acc=ok` 或错误码）。
- 这几条路由跨多个账套，不在 MCP 的通用工具目录里（`u8_read` / `u8_write` 调不到）。
- 某账套的数据超过翻页上限时 422 `ic_too_many_rows`「账套 X 的数据超过 N 行」，缩小日期或条件再试。

### `reports/intercompany_match` 公司间对账

卖方的销售单据与买方的采购单据逐笔配对，找出单边存在的记录。

| 字段 | 说明 |
| --- | --- |
| `logins` | 2 到 3 个，须包含卖方和买方账套 |
| `seller` | `{acc, type}`，`type` 为 `sale_out`、`dispatch`、`sale_invoice` |
| `buyer` | `{acc, type}`，`type` 为 `purchase_in`、`arrival`、`purchase_invoice`；不能与卖方同一账套 |
| `date_from`、`date_to` | 必填，最多 93 天 |
| `window_days` | 0 到 7，缺省 1：日期相差几天内仍算同一笔 |
| `invoice_mode` | `line`（逐行）或 `month`（按月比金额），只在两边都是发票时可给，缺省 `month`。发票只能与发票对 |

- 卖方取客户为买方公司（对照 `as_customer`）的单据，买方取供应商为卖方公司（`as_vendor`）的单据；对照缺这两个编码时 409 `ic_party_unmapped`。单据头用 `vouchers/search`，出入库明细用 `reports/stock_ledger`（只查对照里的存货），其余用 `vouchers/load_many`（最多 300 张）。任一方读取失败即整个请求失败，用该账套的状态码和错误码报告（消息带账套号）。
- 截断：`clip_date` 取两边最后一笔日期中较早的那天，之后的行不参与（计入 `clipped`），避免一边未录完时误报。
- 逐行：存货按对照的 `inventory` 换成同一 id，先按（存货、日期、数量）完全相同一对一配对，再对 `match: code` 的存货在 `window_days` 天内找日期最近的；`qty_date` 的存货只认同一天。没有对照的存货不配对，列为未配对（`reason: unmapped`），有对照但未配上的是 `no_counterpart`。
- 按月：两边每月价税合计相差不超过 0.05 算配上，否则 `amount_differs`。
- 响应：`group`、`mode`、`seller`、`buyer`（带往来单位编码）、`clip_date`、`window_days`、`matched`、`unmatched_seller`、`unmatched_buyer`、`rates`（两边各自配上的比例，四位小数）、`clipped`、`warnings`（如 `no_inventory_map:<账套>`、`mapped_inventory_only:<账套>`、`load_failed:<账套>:<id>`）。

### `reports/aggregate` 多账套汇总

同一张只读报表在 1 到 3 个账套上各取一遍，再按对照合计。

- `report`：`stock_current`（现存量）、`arap_balance`（往来余额）、`arap_aging`（账龄）、`gl_balance`（科目余额表）；`params`：传给该报表的参数（同单账套报表，§16、§19），不能含 `acc`、`operator`、`password`、`year`、`date`、`after`、`limit`。每个账套最多取 2 万行。
- 合计只按对照：现存量按 `inventory` 的 id，往来按往来单位代表的公司（`as_customer` / `as_vendor`，由 `params.side` 决定），科目余额按 `gl` 的逻辑科目（精确匹配）。未进对照的列在 `unmapped`（每类最多 50 个编码）。
- 响应：`by_account`（每个账套的原样结果，或 `{ok: false, status, error}`；某个账套在等待时间内没有返回时 `status` 为 504、错误码 `timeout`）、`totals`、`unmapped`、`warnings`。部分账套失败时 200，失败的不计入合计、`warnings` 说明；全部失败 503 `unavailable`。

### `reports/consolidation` 合并试算

`logins`（2 到 3 个）、`fiscal_year`、`period_from`、`period_to`。各账套取科目余额表，按对照的逻辑科目归并，再按对照中 `rule: ar_ap` 的抵销对冲掉内部往来：应收方按客户、应付方按供应商取辅助核算余额，同号时抵销绝对值较小的一方（借应付、贷应收），`difference` 为应收减应付；方向相反的不抵销（`skipped: "opposite_sign"`，并提醒）。

响应：`group`、`complete`、`by_account`、`trial`（每个账套是否平衡、末级借贷合计）、`eliminations`、`consolidated`（按逻辑科目的合并数）、`unmapped`、`warnings`、`notes`（口径说明，如未抵销内部交易中未实现的利润）。有账套失败时 `complete: false`、`consolidated: null`，全部失败 503。只做往来抵销，不抵销内部收入成本和未实现利润（经营管理利润表的合并见 §34）。

### `intercompany/generate_buyer` 买方生单（写）

按卖方的出货，在买方账套参照一张公司间采购订单生成采购入库单或到货单（经买方账套的 `vouchers/generate`）。

| 字段 | 说明 |
| --- | --- |
| `login` | 买方账套的登录 |
| `seller_acc` | 卖方账套，不能等于买方 |
| `type` | `purchase_in` 或 `arrival`（`other_in` 400：公司间采购须参照订单） |
| `po_id` | 可选，指定买方的采购订单 |
| `lines` | 1 到 200 行 `{inv_code, quantity, seller_id?}`：`inv_code` 是**卖方**的存货编码 |
| `date`、`head` | 可选，同 `vouchers/generate`；表头没有 `dDate` 时 `date` 写入表头 |
| `dry_run` | 缺省 **`true`**：只出计划和预演，正式生成须显式写 `false` |

- 供应商编码取对照的 `as_vendor[买方][卖方]`，缺失时 409 `ic_party_unmapped`；卖方存货换成买方编码，缺对照时 409 `ic_inventory_unmapped`（`detail.codes`）。
- 选订单：买方账套中该供应商、未关闭、剩余数量（入库看已入库，到货看已到货）足够的公司间采购订单；给了 `po_id` 只看这一张，否则取能覆盖全部存货和数量的 id 最小的一张，数量按订单行顺序分配。选不到时 409 `ic_no_open_po`（「没有能覆盖这些存货和数量的公司间采购订单」，或「采购订单 N 不是卖方公司的未执行完订单，或剩余数量不够」）。
- 预演和正式生成各自选订单：不给 `po_id` 时两次之间订单余量变化可能选到不同订单。要锁定，把预演响应的 `plan.po_id` 作为正式请求的 `po_id`。
- 正式生成必须带 `Idempotency-Key`，否则 400 `idempotency_required`。同一个键重发时先查买方桥上 `vouchers/generate` 的幂等记录：已成功的原样重放（响应没有 `plan`）；单据类型、来源或给定的 `po_id` 与第一次不一致 409 `idempotency_mismatch`；第一次结果未知 504 `outcome_unknown`。`idempotency/get` 查这条路由时按买方桥上的 `vouchers/generate` 记录查。
- 写入策略按买方账套的 `vouchers/generate` 判定（预演也判定），类型为 `type`、操作为 `generate`。
- 响应：正式为 `{ok, type, id, code, state, lines, plan}`，预演为 §23 的预演响应另加 `plan`；`plan` 含 `buyer_acc`、`seller_acc`、`vendor`、`po_id`、`po_code`、`lines`（`buyer_inv_code`、`seller_inv_codes`、`quantity`、`source_line_id`）。

## 34. 经营管理查询

面向经营者的汇总：利润表、销售与毛利、往来账期、资金与存货，1 到 3 个账套，可合并。分两层：

- 桥的 `reports/mgmt/*`：单账套聚合报表，只查数据库（读线程池），不翻页。API 不直接开放，直接调用桥的客户端可以使用。
- API 的 `/v1/co/mgmt/*`：按账套调桥、合并、算比率、缓存。要令牌有经营管理权限（§17，否则 403 `mgmt_forbidden`），再按账套白名单和令牌的账套声明逐个检查（全有或全无）。OpenAPI 中 `x-u8co-access` 为 `mgmt`。本地 MCP 服务有对应的 `u8_mgmt_*` 工具（`mcp.md`）。

### 桥的报表 `reports/mgmt/*`

公共：`fiscal_year` 2000 到 2099，缺省登录年度。`pnl`、`meta`、`cash_stock` 登录 `GL`，`sales` 登录 `SA`，`arap_terms` 按 `side` 登录 `AR` / `AP`。科目前缀参数每项只含字母、数字、`.`、`-`，最多 10 项。

| 报表 | 参数 | 内容 |
| --- | --- | --- |
| `mgmt/pnl` | `period_from`、`period_to`（必填）、`include_unposted`（缺省 `false`）、`detail`（`prefix4` 按科目前 4 位，缺省；`leaf` 按末级科目）、`dims`（`dept`、`item` 的子集）、`pl_accounts`（损益科目前缀，缺省 `["6"]`）、`profit_account`（本年利润，缺省 `4103`） | 损益科目按期间（× 科目 × 部门 / 项目）的借、贷发生额，`net_income` = 贷 − 借，`normal` 是科目的余额方向，`posted` 表示该组全部已记账。另给每期的总账结账状态、未记账凭证张数、是否已做期间损益结转。超过 2 万行 400。按部门且部门数据权限打开时，聚合前去掉无权部门的分录（§22） |
| `mgmt/meta` | 只有 `fiscal_year` | 各模块（GL、SA、PU、ST、IA、AR、AP、FA）是否启用和启用日期；1 到 12 期各模块的结账状态、未记账凭证张数、是否已结转损益；数据水位 `watermarks`（凭证行数、已记账和作废行数、校验和与分录内容校验和 `gl_content_checksum`（不给借贷合计，金额变动同样使水位变化）、最大主键和日期，发票、入库单、票据、科目和结账表的时间戳，存货核算和往来明细的行数和最大主键）；登录操作员的权限指纹 `perm_fingerprint`（同 `perm/snapshot` 的 `fingerprint`） |
| `mgmt/sales` | `period_from`、`period_to`（必填）、`group_by`（`period`、`customer`、`inventory`、`person`、`department` 的子集，缺省 `["customer"]`，`[]` 只出合计）、`top`（1 到 500，缺省 200）、`include_unverified` | 收入、成本、毛利、毛利率按分组；按收入降序取前 `top` 组，其余并成 `others`，另有 `totals`。未启用销售管理时明细为空、`sa_enabled: false`。超过 2 万组 400 |
| `mgmt/arap_terms` | `side`（`ar` / `ap`）、`as_of`（缺省登录日期）、`accounts`（缺省应收 `1122`、应付 `2202`）、`buckets`（缺省 `[30,60,90,180]`）、`default_credit_days`、`top`（缺省 200） | 每个往来单位：余额、预收（付）、逾期、按到期日的账龄各段、未结票据，近 12 个月的单据张数和金额、已付和未付、信用期分布，回款（付款）天数的金额加权平均和中位数。按余额绝对值降序取前 `top`，其余并成 `others`。余额、预收付、逾期都为 0 且近 12 个月没有单据的单位不列 |
| `mgmt/cash_stock` | `period`（必填）、`cash_accounts`（缺省 `1001`、`1002`、`1012`）、`notes_accounts`（缺省 `1121`）、`top`（缺省 200）、`purchase_source`（`auto` 缺省：有采购发票用发票，否则用采购入库；`invoice`；`receipt`）、`include_unverified` | 货币资金和应收票据科目的期末余额（末级科目，已记账）；未结应收票据（查询时点）；存货结存（存货核算汇总）；产成品入库按存货排行；采购按供应商排行 |

口径：

- 利润表：发生额取凭证（`GL_accvouch`），排除整张期间损益结转凭证（`coutsign` 为「期间损益」，或凭证里有本年利润科目、其余分录全是 `code.cclass`「损益」类科目）；「结转销售成本」「结转制造费用」「汇兑损益」等不排除。作废凭证不计；缺省只计已记账，`include_unposted` 时含未记账。`prefix4` 假定一级科目为 4 位编码。
- 销售：收入取销售发票（非期初，发票日期在期间内），缺省只计已复核（`cChecker`；`cVerifier` 是应收审核人），本币不含税 `iNatMoney`、含税 `iNatSum`，红字发票为负数直接相加。成本取存货核算明细中 `bSale=1`、出库方向的行，金额 `iAOutPrice`（入库方向的成本调整不计入）；明细上没有业务员、部门时取销售出库单表头的。收入、成本按分组键全外连接，只有成本的组也列出。
- 期间：销售和资金存货按 U8 会计期间（`UFSYSTEM..UA_Period`）定起止日期，读不到系统库或期间不连续时按自然月；`period_source` 为 `u8` 或 `calendar`，读不到时另有提醒。
- 往来：账龄按到期日（单据日期 + 信用天数，没有时用 `default_credit_days`）；首次回款日期按核销（`9P`）登记日期，只看原始单据行、金额为正的单据。未结票据：`AP_Note` 余额 `iRAmount` 不为 0（结算、贴现、背书、退回会减到 0）、签发日期不晚于 `as_of`，按当前状态；往来单位取 `cDwCode`，为空时取 `cEndorser`。
- 资金存货：未结票据同上，但按本币余额 `iRAmount_Local` 不为 0、查询时点的数，不是期末数。产成品、采购缺省只计已审核（发票口径不论是否复核）。
- 权限：`pnl` 要总账余额表或明细账的查询权限（`GL030301`、`GL0305`、`GL030101` 之一），明细账按科目控制数据权限时按科目过滤，没有部门权限时部门名称为空、金额照常；`meta` 要结账或余额表的查询权限（`GL1512`、`GL1520`、`GL030301`、`GL0305` 之一）；`sales` 要销售统计类报表的权限，按客户、存货（业务员、部门可空）过滤；`arap_terms` 同账龄分析（`AR060201_01`、`AR060301` 或账龄视图，应付同理），按往来单位过滤；`cash_stock` 各段分别按科目、客户、存货和仓库、供应商过滤，没有授权的段为 0，不报错。

### API 的 `/v1/co/mgmt/*`

| 路由 | 参数（另有公共的 `logins`、`fiscal_year`） |
| --- | --- |
| `mgmt/meta` | 无。不缓存、不合并 |
| `mgmt/pnl` | `period_from`、`period_to`、`consolidate`、`include_unposted`、`dims`（最多 1 项：`dept` 或 `item`）。取数粒度由利润表行定义决定（`configuration.md`「利润表行定义」），不收 `detail` |
| `mgmt/sales` | `period_from`、`period_to`、`consolidate`、`group_by`、`top`、`include_unverified` |
| `mgmt/arap` | `period_from`、`period_to`、`consolidate`、`side`（`ar`、`ap`、`both`，缺省 `both`）、`as_of`（缺省 `period_to` 的月末，晚于今天时取今天，按 `U8CO_TIMEZONE`）、`buckets`（1 到 8 项、严格递增）、`default_credit_days`、`top`。另给净额：未核销的收款（付款）按先进先出冲抵该单位最早的账龄段，得到 `aging_net`、`overdue_net`（`aging`、`overdue` 不变）；未核销金额超过账龄合计 10% 时附警告。余额小于等于 0 时 DSO / DPO 为 null |
| `mgmt/cash_stock` | `period_from`、`period_to`、`consolidate`、`top`、`purchase_source`、`include_unverified`。只按 `period_to` 一个期间出具（`period_from` 小于它时 `notes` 说明） |
| `mgmt/overview` | `period_from`、`period_to`、`consolidate`、`as_of`。一次给出关键指标：营业收入、营业成本、毛利和毛利率、净利润和净利率、货币资金、存货、应收票据、应收余额和逾期、应付余额和逾期、DSO、DPO（由利润表、资金存货、往来三部分组成；逾期用冲抵未核销收付款后的净额） |

- `logins` 1 到 3 个、不重复；`fiscal_year` 2000 到 2099；`period_from` ≤ `period_to`，同一年度内（最多 12 个期间）。`consolidate`：只有 1 个账套时不合并；2 个及以上时缺省合并，写 `false` 不合并。合并要求配置了公司间对照且账套同组（否则 404 `ic_not_configured` / 400 `ic_group_mismatch`）；不合并时有对照则借用其中的公司名，没有也不报错。
- 响应：`report`、`fiscal_year`、`period_from`、`period_to`、`complete`、`accounts`（`[{acc, name, close}]`，`close` 是所选期间各模块的结账状态）、`by_account`（每个账套的结果或 `{ok: false, status, error}`，账套超时时 `status` 为 504、错误码 `timeout`）、`consolidated`、`eliminations`、`warnings`、`notes`、`cache`（`{hit, age_s}`）。
- 每次请求先读各账套的 `mgmt/meta`，只对读到的账套取报表。部分账套失败时 200、`complete: false`、`consolidated: null`，`warnings` 说明；全部失败 503 `unavailable`。`overview` 三部分都失败才 503，部分失败的账套带 `missing`。
- 利润表：按行定义把损益科目归行（前缀匹配），派生行和比率（毛利率、净利率，两位小数，收入为 0 时 null）按合计重算；一行都没命中的损益科目列在 `unmapped`。每行 `{id, name, kind, sign, total, periods}`，拆维度时另有 `by_dim`。
- 合并：
  - 利润表：各账套相加，再按对照中 `rule: rev_cogs` 的规则从营业收入、营业成本中减去卖方卖给买方的部分（按客户逐期间读卖方的销售统计）。卖方销售统计读不到，或某期客户超过 500 个、内部客户可能并在 `others` 里时，不出合并数（`consolidated: null`、`complete: false`）。没有适用规则时 `notes` 说明合并数含内部交易。未实现的内部利润（买方存货中的内部毛利）不抵销，`notes` 固定说明。
  - 销售：合计减去客户为本次所选其他公司的销售（`eliminations` 的 `rule: ic_sales`）。内部客户一律从按客户补读的结果中找（每个账套 500 组）；仅当主查询本身只按客户分组且没有 `others` 时直接用主查询。内部客户不在列出的组里而 `others` 不为 0 时不出合并数。合并明细：按存货分组时只按对照的存货 id 相加，没有对照的进 `unmapped`；不按存货时各账套去掉内部客户后并列（每行带 `acc`）。编码不跨账套合并。
  - 往来：按对照的往来单位编码去掉内部往来（`rule: ic_arap`），DSO、DPO 按抵销后的余额重算；内部单位可能并在 `others` 里时只提醒。合并后的回款天数不重算。不使用 `ar_ap` 抵销对（那是 `reports/consolidation` 用的）。
  - 资金存货：资金、票据、存货金额相加，不抵销存货中的内部利润；存货数量直接相加（单位可能不同，仅供参考）；产量按对照的存货相加；采购去掉供应商为所选其他公司的部分（`rule: ic_purchase`）。
- 比率：毛利率、净利率；DSO / DPO = 余额 × 天数 ÷ 近 12 个月单据金额（天数按往来报表的统计窗口，缺省 365，合并时取最后一个账套的窗口）。不提供存货周转。
- 缓存：进程内，最多 256 项。键为路由、各账套和操作员（不含口令）、全部参数和各账套 `mgmt/meta` 的数据水位（meta 每次都读，开销很小）；所选期间在每个账套都已总账结账时保留 6 小时，否则 60 秒。结果不完整、有账套 meta 读取失败时不缓存。缓存不区分调用方：在 U8 中收回某操作员的权限后，缓存过期前同一组参数仍可能返回旧结果。
- 审计：每个请求一行，`mgmt` 字段记 `report`、`fiscal_year`、`periods`、`consolidate`、`cache_hit`。

## 35. 其他单据和写入

### 供应商退款、客户退款 `ap_refund`、`ar_refund`

U8「付款单据录入」中的供应商退款（应付的收款单，`Ap_CloseBill` 上 `cFlag=AP`、`cVouchType=48`，卡片 `AP48`）和「收款单据录入」中的客户退款（应收的付款单，`AR` / `49`，卡片 `AR49`）。读取、列表、查询、事件、新增、修改、删除、审核、弃审都同收付款单（UFAPBO `clsCloseBill`，§12），字段、行数、`iType`（0 应收付款、1 预收付款）也相同；往来单位应付为供应商、应收为客户。

- 金额填正数，U8 审核时在往来明细写负数。条码前缀 `||ap48|`、`||ar49|`。
- 同一个 `iID` 按别的类型读 404：供应商退款不能当付款单读，客户退款不能当收款单读。
- 不能核销（`arap/writeoff` 400「receipt.type 只能是 ar_receipt 或 ap_payment」），不能用于票据和坏账，请在 U8 客户端处理。可经 `arap/voucher` 制单（§12「制单」，往来红字、结算正数同在一方），`arap/voucher/delete` 取消。
- 功能权限同孪生类型（在同一个录入界面）：供应商退款同付款单，客户退款同收款单。预演都是 `rollback` 模式。写入策略的金额上限对它们生效。

### 退货申请单 `sale_return_apply`

销售管理的退货申请单（卡片 `SA31`，`SA_ReturnsApplyMain` / `SA_ReturnsApplyDetail`），表体数量、金额为负。

- 读取（SQL）、列表、查询、单据追溯、事件（按表头 `ufts`）。列表另有 `sale_type`、`bus_type`、`wf`、`verify_state`、`currency`、`memo`、`line_count`、`quantity`、`amount`（负数）等列。`state.verified` 看审核人，`state.closed` 看关闭人。读取功能 id：查询 `SA03250104`，列表 `SA03250201`；数据权限按客户、部门、业务员、销售类型、存货（仓库可空）。
- 新增（`vouchers/create`）：只能参照已审核、未关闭、非期初的蓝字发货单行，每行 `source_line_id` 为发货行 `iDLsID`，`quantity` 填正数（桥写负数）；不支持无来源新增。表头只收 `ddate`、`cmemo`、`cdepcode`、`cpersoncode`、`cdefine1`…`16`；行只收 `cwhcode`（行上没有仓库时必填）、`cmemo`、`creasoncode`（退货原因码，须存在）、`cdefine22`…`37`。1 到 200 行，各行同一客户、同一币种。可申请数量 = 发货行数量 − 已退数量 − 其他未关闭申请行尚未退完的部分，超过时 409「超过可退货数量」。
- 修改：只能改已有行（`op: update`），行可改 `iquantity`（正数）、`cmemo`、`creasoncode`、`cdefine22`…`37`，表头 `ddate`、`cmemo`、`cdefine1`…`16`。修改、删除要求未审核、未关闭、不受审批流控制（409 `workflow_enabled`「退货申请单受审批流控制，请到 U8 客户端处理」）、没有下游退货单（409「退货申请单已有退货单，请先删除退货单」）。
- 审核、弃审（`vouchers/verify`）：弃审同样要求未关闭、没有下游。关闭、打开不支持（400）。
- 组件：销售 CO 的单据类型 34。该卡片的保存、删除、审核由 U8 的 .NET 服务自行提交，桥无法包在事务里：检查全部在调用前完成、不跨调用持锁；调用异常或之后回读不符一律 504 `outcome_unknown`（消息写明已保存 / 已删除），U8 返回拒绝文本时 409 `u8_rejected`（已由 U8 回滚）。预演是 `validate` 模式，停在调用 U8 之前，不占单号。
- 功能 id：录入 `SA03250101`、审核 `SA03250102`、弃审 `SA03250103`、删除 `SA03250110`；写入时每次现查，不限测试账套。
- 退货单参照退货申请单（`vouchers/generate`，`type: sale_return`、`source_type: sale_return_apply`，功能 id `SA03020218`）：申请单已审核、未关闭，所选申请行未关闭、都挂着同一张蓝字发货单，数量不超过申请行剩余；开票标志（`bneedbill`）取申请行的，几行不一致 400，表头给了 `invoiced` 须与之一致。其余同参照发货单生成退货单（§11）。退货行写 `irtnappid` = 申请行 `AutoID`、`crtnappcode` = 申请单号，U8 回写申请行的已退数量 `fretqty`；桥在提交前核对关联和回写，不符 409 并回滚。删除该退货单时核对 `fretqty` 已退回。预演是 `rollback` 模式。
- 参照申请单生成的退货行不能改数量或删行：409「参照退货申请单生成的退货行暂不支持修改数量或删除，请删除退货单后重新生成」。

### 无来源销售出库 `sale_out`

账套未启用销售管理（只用库存管理）时，销售出库单可以无来源新增（`cSource=库存`、普通销售），以及修改、删除这类单据。

- 新增（`vouchers/create`，`USERPCO.VoucherCO`，同其他出库单）：表头必填 `cwhcode`、`ccuscode`、`cdepcode`，可选 `crdcode`（出库类末级收发类别）、`cpersoncode`、`ddate`、`cmemo`、`cstcode`、`cdefine1`…`16`；行必填 `cinvcode`、`iquantity`，其余同其他出库单（批号、货位、成本、自由项等），不要求成本。1 到 200 行。
- 账套闸门（调用 U8 之前，409 `state_mismatch`）：已启用销售管理（`AccInformation` 的 `SA` / `dSaleStartDate` 非空）时「账套已启用销售管理，销售出库单须参照发货单生成」；库存选项「销售出库单由销售系统生成」（`ST.bSAcreat`）打开时「库存选项设置了销售出库单由销售系统生成」。
- 存货：批次管理的必须给批号、非批次的不能给；保质期管理的存货 409「存货 X 启用保质期管理，本期不支持无来源销售出库」；货位仓规则同其他出库单。
- 修改：来源为库存的可自由修改（加行、改行、删行），来源为发货单的按生单单据处理（§8），其他来源 409。不能换仓库（409「不能修改销售出库单的仓库」），不能清空客户、部门；改了客户、部门、销售类型、收发类别时重新检查档案。
- 删除：来源为库存的照常删除（没有发货单回写），已开票 409。预演都是 `rollback` 模式。

### 发货单修改新增行

`vouchers/update` 的发货单可以带 `op: add` 的行，新增参照来源销售订单的行（退货单、销售发票仍不能新增行，400「该单据类型修改不能新增行」）。

- 新增行必填 `source_line_id`（订单行 `iSOsID`）、`iquantity`（大于 0）、`cwhcode`，可选 `cbatch`、`cmemo`、`cfree1`…`10`（存货须启用该自由项）、`cdefine22`…`37`；不能带 `line_id`，同一订单行不能加两次。
- 订单行必须属于本发货单已有行的来源订单（400「只能新增本发货单来源销售订单的行」），客户相同；订单已审核、订单和行未关闭（409）。本次修改后该订单行的数量差不能超过 `iQuantity − iFHQuantity`（409「超过可生单数量」，没有超发容差），事务里加锁再核一次。
- 发货单仍要求未审核、没有下游；已有行仍只能改小数量。保存后按订单行和数量匹配新行、核对订单行累计发货数，不符 409 并回滚。预演是 `rollback` 模式。新增行的保存尚未在测试账套实测。

### 采购手工结算 `purchase_settle`

`vouchers/create` 的 `purchase_settle`：按调用方给的配对结算，同 U8「手工结算」。写入与 U8 手工结算界面执行的 SQL 一致（实测核对：配对、红蓝入库对冲、红蓝发票对冲，与 U8 生成的结算单比对一致）。第一级写入：所有 `allowedAccounts` 账套按写入策略（`purchase_settle` 的 `create`）放行。委外、外币、费用分摊、同一发票行既对冲又配对等 U8 界面能做而桥不做的结算会被拒绝（400 / 409，见下面的门槛），请在 U8 客户端处理。

- `head`：只收 `settle_date`（缺省为本次的登录日期，给了就替换登录日期，不能晚于今天）。
- `lines`：1 到 400 行，每行 `in_line_id`（采购入库行 `AutoID`，0 或省略表示没有）、`invoice_line_id`（采购发票行 `ID`，同上）、`quantity`（带符号，红字为负，不能为 0）、可选 `amount`（结算无税金额，正负同 `quantity`；缺省按发票行无税金额 × 数量比例，结清的那一笔取余额）。三种行：两者都给是入库与发票配对（可部分结算）；只给入库行是红蓝入库对冲；只给发票行是红蓝发票对冲（对冲行同一存货的数量合计须为 0；只给入库行时不能带 `amount`）。
- 门槛（409，消息带「第 n 行：」）：只做普通采购、本位币、专用或普通发票；入库单已审核；发票已采购复核、未应付审核、非期初、非现付、参照入库单开具；配对的两行同一存货；只有一个供应商；数量正负与原行一致、不超过各自的未结算数量；结算日期不早于入库和发票日期、所在期间采购未结账（U8 的 `CheckSettle` 拒绝时 409 原文）；同一发票行不能在一张结算单里既对冲又配对（税额无法分摊，400），与已有结算混用同样 409。
- 写入（一个事务）：加锁重读并重算 → 按 U8 的取号过程分配结算单主键（号被占用 409「结算单号冲突，请重试」）→ 写结算单表头和行 → 每个入库行执行 U8 的回写过程 `PU_SettleWriteBKRDS`（只在本次把入库行结清时才按结算价改写入库行的单价、金额，部分结算保持原值）→ 回写发票行和表头的结算日期、存货的最新结算单价。提交前核对行数、数量、入库行已结算数、发票结算日期，不符 409 并回滚；提交后回读不到 504。
- 响应同新增，另带 `settle_date`。删除用 `vouchers/delete`（U8 组件，对手工结算的结算单同样回退）。功能 id `PU040302`（手工结算），数据权限按供应商、存货（部门、业务员、采购类型、仓库可空）。预演是 `rollback` 模式。

### 红冲蓝字销售发票

`vouchers/generate` 的 `sale_invoice`，`source_type: sale_invoice`，`id` 是已复核的蓝字发票 `SBVID`（专票 26 / 普票 27，红字发票同类型）。

- `lines` 可省（全部有剩余的蓝字行按剩余数量），带了则每行 `source_line_id`（蓝字行 `AutoID`）、`quantity`（正数），另可 `cmemo`、`cdefine22`…`37`；表头 `cvouchtype`（只能同蓝字）、`ddate`（缺省登录日期，不早于蓝字发票日期）、`cmemo`、`cdefine1`…`16`。
- 门槛：蓝字发票存在、不是红字、非期初（400）；未作废、已复核、不是现结（409「现结发票不能红冲，请在 U8 客户端处理」）；客户存在且未停用、销售管理该月未结账（409）；未全部红冲、各行不超过剩余（409「蓝字发票已全部红冲」「超过可生单数量」）。
- 红蓝关联由桥定义：U8 数据中没有可用的红蓝发票关联列，桥在每条红字行的 `SaleBillVouchs.iSBVID` 写对应蓝字行的 `AutoID`；剩余数量 = 蓝字行数量 − 指向它的红字行（`bReturnFlag=1`）数量合计。保存后关联未留住或行、数量不符时 409 并回滚。
- 组件：销售 CO 按蓝字发票主键生成红字发票，再按请求保留行、取号保存（同一事务内）。发货行、订单行的累计开票数变化只记审计，不核对。生成的红票未复核，用 `vouchers/verify` 复核、弃复；可删除（未复核、未应收审核），不能修改。预演是 `rollback` 模式。
- 蓝字发票被红冲后，弃复、删除、修改都 409「蓝字发票已被红冲，请先删除红字发票」。

### 相关说明

- `purchase_in` 参照到货单（`source_type: arrival`）见 §11。
- 写入策略（`writePolicyFile` / `U8CO_WRITE_POLICY_FILE`）可能在登录前拒绝任何写路由：错误码见 §18，规则见 `configuration.md`「写入策略」。
- 取消汇兑损益（`arap/exchange_gain/cancel`）的采购发票累计回写使用采购侧组件 `Pu_Productinf.cls_ForAPsrv.UpdateBillForAP`（`u8-notes.md`）。
