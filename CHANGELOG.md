# 变更日志

本文件的格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增

- **端到端回归套件公开**：`co/e2e`（回归编排、调试会话、多通道分片、空账套造数）、`co/probe`（COM 探针）、`scripts/lane`（快照还原用的签名存储过程）、`scripts/account`（空账套准备脚本）随仓库发布，适用于任意自备测试账套：单通道直接用 `run_e2e`；多通道用自定标签，缺省按 `co/e2e/shards.example.json` 全部跑在一条通道上，可用 `python -m co.e2e.shard_assign recompute` 按自己的报告生成分片表；造数的编码方案、科目和日期可在 e2e 配置的 `seed` 中覆盖，写入前预检账套；挑不到合适单据的用例记 SKIP 或 N/A。前提与步骤见 `docs/testing.md` 第 4 节。
- **客户开票单位新增、删除、设默认**：`archives/customer_invoice_company_add`（`code` 为 `<客户编码>:<开票单位编码>`，可选 `default`）、`archives/customer_invoice_company_delete`、`archives/customer_invoice_company`（设默认，二者必带 `expect.bdefault`），均可带 `expect.customer_ufts`。受控 SQL 写 `sa_invoicecustomers`，不走客户 EAI（客户 EAI 的修改会按单据重写整张开票单位表、删掉额外行）。开票单位必须是已有客户（已停用的允许），同一对重复新增按重放 `changed:false`，默认行不能删除（409 `invoice_company_is_default`），写后该客户恰有一个默认；默认变化时同事务更新客户的 `cInvoiceCompany`、修改人、修改日期（按 `pubufts` 乐观锁），不改单据。功能权限 `AS011` 加客户、开票单位两个编码的记录权限，预演为 `validate`，写入分类 `(archives, <路由名>)`，只读例外和写入策略的 `op` 同步增加这三项，客户端 `arc_customer_invoice_company*`、命令行 `arc-customer-invoice-company*`、MCP 路由表同步。
- **无来源采购退货单、红字采购发票修改**：`vouchers/create` 开放 `purchase_return`（红字到货单，不挂到货单和订单）：表头必填 `cvencode`，可填 `cdepcode`、`cpersoncode`、`cptcode`、`ddate`、`cmemo`，行填 `cinvcode`、正数 `iquantity`、可选 `cwhcode`、`ioricost` / `ioritaxcost`、`itaxrate`，名单同无来源到货单；桥用红字 `Init` 保存，表头 `ibilltype=1`、`bnegative=1`，数量和金额写负数，不写 `icorid` / `iposid`，保存后核对落库的红蓝字标志、行无来源和数量符号；不受 `PU.bPTHavePO` 限制，审批流已发布时 409。`vouchers/update` 的 `purchase_invoice` 可修改红字发票：数量仍填正数，桥按符号 −1 写负数并重算金额，红字入库行 `iSumBillQuantity` 随数量减少朝 0 移动，红蓝字行混合的发票 409，闸门（已复核、应付审核、结算、现付、期初、期间已结账）同蓝字。两者待在 U8 上实测。客户端、命令行同步。
- **退货单参照销售订单、无来源退货单**：`vouchers/generate` 的 `sale_return` 新增 `source_type=sale_order`（`id` 是 `SO_SOMain.ID`，`source_line_id` 是订单行 `iSOsID`，`quantity` 填正数），只挂订单行、不挂发货行，退的是已发货的货（不指明发货单），可退数量为订单行累计发货数量（`iFHQuantity`，已扣过以前的退货；超过 409「超过可退货数量（订单行累计发货数量）」，U8 自己不检查上限）；桥用红字发货单模板（VT 10、卡片 03）按订单参照视图组装，保存后在同一事务里核对订单行 `fretquantity` 加了本次数量、`iFHQuantity` 减了本次数量，删除、修改红字数量时反向核对。`vouchers/create` 开放 `sale_return`（无来源红字发货单，字段同无来源发货单，数量填正数），不受 `SA.bMustSO_ptxs` 限制，保存后同一事务里核对新单红字、每行负数量与合计、不挂来源行。无来源退货单待在 U8 上实测。客户端、命令行同步。
- **不良品处理单修改**：`vouchers/update` 开放 `qm_incoming_reject`、`qm_product_reject`。表头可改 `dDate`、`cDefine1`–`16`、`chDefine11`–`16`；`lines` 按 `op` 增删改处理行（`line_id` 是表体 `AUTOID`，可带 `quantity`、`cScrapDisCode`、`cReasonCode`、`cDimInvCode`、`fDimQuantity`、`cbWhCode`），不收检验单、存货、来源字段。改完的整单按生单规则核对（档案、降级行须有处理后存货且处理后数量大于 0、不良原因 + 处理方式不重复、至少一行、数量合计等于本处理单的待处理不良品数量 `FSUMQUANTITY`；`chDefine11`–`16` 须是账套已有的扩展自定义项列，新的处理后存货、仓库按数据权限核对）。只改未审核、未提交审批、没有下游且未入库未返工的单据（409）。经 U8 质量管理组件 `GetTheVoucher` + `UpdateVoucher`，由 U8 自行提交，`dry_run` 只做校验；保存后在新连接上核对 `ufts`、表头字段、表体、不良品处理标记 `BREJFLAG`（合并检验的处理单读合并明细）与下游，`ufts` 未变 409 `u8_rejected`，其他不符 504。功能权限同新增（`QM02010303` / `QM02020303`）。客户端 `update_voucher`、CLI `update --type` 同步。生单同步：合并检验的检验单不能参照生单（409，请在 U8 中按合并明细生成）；`chDefine11`–`16` 同样须是账套已有的扩展自定义项列（400）。
- **采购入库参照来料不良品处理单**：`vouchers/generate` 的 `purchase_in` 新增 `source_type=qm_incoming_reject`（`id` 是 QM05 处理单 `ID`，`source_line_id` 是处理单表体 `AUTOID`，同一张处理单的 1 到 200 行生成一张入库单）。处理单须已审核（受审批流控制的须审批完成），只接处理流程为「降级 / 让步接收 / 升级」（`IDISPOSEFLOW=2`）、未入库（`BFLAG=0`）的行，入库存货取处理后存货，`quantity` 必须等于该行剩余的处理后数量（U8 不接受部分入库，否则 409）；调用 U8 `VoucherCO.Insert("01")`，保存后在同一事务里核对每行已入库数和 `BFLAG`，不符回滚并 409 `u8_rejected`。这样生成的入库单可删除，删除时核对 `BFLAG` 回退；不能修改。产成品入库参照产品不良品处理单与它共用来源规则，行为不变。
- **存货核算制单 `ia/voucher`、取消制单 `ia/voucher/delete`（第二级写入，只对测试账套开放）**：把已记账、未制单的出入库单据（采购入库 01、其他入库 08、其他出库 09、产成品入库 10、材料出库 11、出入库调整 20 / 21、红字 / 蓝字回冲 24 / 30、销售出库 32 与发出商品 3201）生成总账凭证；调拨、形态转换、组装、拆卸的其他入库 08 / 其他出库 09 按业务单号成对制单（只选一边时另一边一起做，只有存货科目，缺省摘要为业务单据名，凭证行 `coutid` 为业务单号）。按单据（`ids` 的 `{type, id}`）或月份（`month`、`types`）选，`merge` 为 `per_bill`（一张单据一张凭证，最多 10 张；按月份时做前 10 张，其余在 `pending`）或 `all`（合成一张，最多 100 张）；`sign` 缺省取最近一张存货核算凭证的类别，`digest` 缺省按 U8 取单据备注或单据类型名，`voucher_date` 缺省登录日期。存货科目、对方科目按 U8 制单的取法（「存货科目」按存货、分类前缀、仓库；「对方科目」按收发类别、存货、分类、部门、项目取最后一行；暂估记账和红字回冲取暂估科目，发出商品取发出商品科目），同方向同科目同辅助项合并、借方在前，经凭证导入保存，来源列与外部业务号（年度 + IA + 13 位，`Ap_CancelNo` 取号）照 U8 补齐，回写存货明细账、发出商品明细、单据表头；回写失败删除刚生成的凭证并 409；逐张制单第 2 张起失败时 `error.detail` 带已完成的 `done` 和要重发的 `pending`。取消制单按外部业务号删凭证并照 U8 清除，恢复记账后留下的凭证也能删。成对业务两边金额不等、非采购来源的回冲单、差异、外币科目、要求凭证自定义项的科目 409。功能权限 `IASCPZ` / `IACXPZ`。桥、API、客户端（`ia-voucher`、`ia-voucher-delete`）、MCP 路由目录、文档、测试、e2e 用例同步；凭证外部业务号取号和新凭证核对抽成 `arap/voucher` 与本路由共用。
- **产品检验单合并检验**：`vouchers/generate` 的 `qm_product_check` 可用 `source_ids`（2 到 20 条已审核产品报检单表体 `AUTOID`，可来自不同报检单）代替 `id`，生成一张合并检验的产品检验单（`BMERGECHECKFLAG=1`），调用 U8 `clsProCheckCO.AddVoucherWithMergeDetail`，U8 写合并明细 `QMMergeCheckDetail` 并回写每条来源报检行。`source_ids` 是报检单表体行 id；`lines` 可省略（整行检验、全部合格），给出时与 `source_ids` 一一对应；`merge_rules` 是 U8 合并规则编码（缺省 `["0"]` 自由项，`3` 需求跟踪不支持）。来源须同存货、辅计量单位及换算率、批号、检验方式、检验类型，满足所选合并规则，未检验过、不是分拣行，否则 409。保存后按单号核对合并明细和来源回写，不符 504。合并检验的检验单（含 U8 客户端生成的）删除改走 VO 接口 `DelVoucher`（带合并明细，U8 的 `VoucherOperate` 删除对这类单据报错），U8 回退每条来源，桥删除后另核对。API、客户端（`generate_merged_check`，命令行 `generate` 文件写 `source_ids`）、文档、测试同步。
- **成本核算期间数据 `reports/ca_inputs`**：只读，查成本管理（CA）某个会计期间的成本核算数据——工时统计、工时日报、人工费用、制造费用、折旧费用、在产品固定 / 变动约当、自定义分配项目、完工产品产量（`tables` 指定，缺省全部，每张表 `limit` 截断并给全部行数 `count`），每行解析成本中心、成本核算对象（存货编码、生产订单号）、成本项目，`values` 给 U8 表上的列原名 → 数值；另给成本计算期间 `status.calculated_through` 和结账状态 `status.closed`。要有所读各表录入窗体的功能权限，数据权限按成本核算对象的存货。桥、API、MCP 工具目录、文档、测试、e2e 只读用例同步。
- **生产订单参照销售订单生成（ATO）**：`vouchers/generate` 新增 `production_order` ← `sale_order`，调用 U8「生产订单生成」组件，写来自订单标记、需求跟踪、已下达量和 ATO 净需求；选配过的行按选配结果展开子件。表头 `mo_type` 必填，可带 `mo_code`、`remark`；行带 `source_line_id`、`quantity`、`dept_code`。部分成功 504 `outcome_unknown`。ATO 存货不能用 `vouchers/create`（U8 拒绝），见 §11。
- **发货单生单表体扩展自定义项**：`vouchers/generate` 的 `dispatch`（参照销售订单）表体可带 `cbdefine1`–`99`（`DispatchLists_extradefine`），规则同蓝字发票表体：保存前按 `INFORMATION_SCHEMA` 核对列存在、类型和长度（没有该列 400「账套没有扩展自定义项 …」），不进 U8 组件 DOM；`Save` 之后、提交之前按来源订单行 `iSOsID` 找到本次的发货单行（行数、数量不符 409 `u8_rejected`），写入（有行改、没有行插入）并逐项回读，不符回滚 409 `u8_rejected`。预演同样；`meta/fields` 的发货单表体可写名单随之增加。请求没有表体扩展自定义项时行为不变。
- **落库核对字段数值列的范围**：订单、发货单、发票的纯标注数值列（含扩展自定义项）在保存前按列类型核对范围、精度和小数位（整数类型、`decimal(p,s)`、`money`），列表示不了的值 400 并带字段路径，不再在保存后写入时报 500。发票、发货单行数量的保存后核对改为先转 `decimal(38,6)` 再比较（`float` 列不再因 6 位有效数字截断误判 409）。API 的落库核对字段形状错误带 `field`，超大 JSON 整数不再 500。
- **桥写数值的写法**：桥补写的数值列（含扩展自定义项）保存前按十进制写法解析并按列类型改写成纯文本（整数列 `3.000`、`1E+01` → `3`、`10`；其余去指数、去尾零），不再在 `Save` 之后因 SQL 隐式转换失败 500；千分位、括号负数、货币符号 400。
- **删除清理扩展自定义项**：`vouchers/delete` 删除发货单、蓝字销售发票时，同一事务里删除其 `DispatchList(s)_extradefine` / `SaleBillVouch(s)_extradefine` 行（U8 是否连带删除未经验证；已删过时无操作，预演随事务回滚）。销售订单、红字发票、退货单的删除暂未接入。
- **只读例外开放删除**：只读例外 `type=vouchers` 登记表新增 `delete`（`sale_order`、`dispatch`，API `VOUCHER_OPS` 与桥 `ReadOnlyVoucherOps.Rows` 同步），配置后只读账套上可删除未审核的销售订单、发货单（含预演；桥照常检查未审核、无下游、不在审批流），可按 `operators` 或 `callers` 开放。销售发票等其它单据的删除仍 403。写入策略另需放行 `(sale_order|dispatch, delete)`。
- **物料清单选配子件** `vouchers/create` / `vouchers/update` 的 `type=bom` 明细可写 `optional`（JSON 布尔）和 `mutex_rule`（1 单选、2 全选、3 任选、4 至少选一）。母件须是模型或选项类，否则 409。新增时 `mutex_rule` 须与 `optional: true` 同送；修改时已是选配的行可只改 `mutex_rule`。写后回读核对两字段。

- **读取 `pay_agreement`、`customer_invoice_company`**：`archives/get`、`list`、`get_many` 读收付款协议（`AA_Agreement`，`fields` 是表列，`dEDate` 不空为已停用，`archives/resolve` 也支持，缺省不返回已停用）和客户开票单位（`sa_invoicecustomers`，编码 `<客户编码>:<开票单位编码>`，`fields.bdefault` 是默认开票单位，没有 rowversion）。开票单位做成客户的子档案（同收货地址、银行账户），不并进客户档案的 `get`。功能权限：协议 `AS60201`（协议档案菜单）或 `AS60202`（协议档案的新增、修改、删除按钮），开票单位 `AS011Q` / `AS011` 并按客户记录权限（两段都判断）；开票单位写入 400「该档案只读」，收付款协议的写入见「收付款协议 `pay_agreement` 可新增、修改、删除」。事件服务的档案缺省清单随之增加 `pay_agreement`，`customer_invoice_company` 属于无时间戳档案，要写明才开；客户端 `arc-get`、`arc-list` 的 `--archive` 同步。
- **期初结存单 `stock_opening` 开放列表和搜索**：`vouchers/list`、`vouchers/search` 支持 `stock_opening`（列同其他出入库单，搜索支持存货和表头自定义项），事件服务缺省的类型清单里的 `stock_opening` 不再 400。
- **货位调整单修改**：`vouchers/update` 开放 `position_adjust`，只改未审核的单据，表头可改仓库、日期、部门、业务员、备注和自定义项，行可 `add`、`update`、`delete`（`line_id` 是行的 `autoID`，不能改存货）。桥经 U8 库存组件 `Load` + `Update`，调用前按改完的整张单据做与新增相同的核对（货位管理仓库、末级货位、调出调入不同、存货管理方式与批号、调出货位结存），保存后、提交前回读整张单据，与改完的不一致、出现货位台账或结存变化时回滚 409 `u8_rejected`；功能权限同录入（`ST010806`），支持预演。API `UpdateType`、客户端 `UPDATABLE_KINDS`（命令行 `update --type position_adjust`）同步。
- **存货 `Inventory_Sub` 扩展属性可写**：`archives/create`、`update` 的 `inventory` 新增 64 个 `Inventory_Sub` 列的标签（批次、质量、定价自由项、`binbyprocheck` 是否依据产品检验结果入库、`finvoutuplimit` 发货允超上限、`idrawtype` 等；出自 U8 随带的存货导入样式表，`fbuyexcess`、`isurenesstype` 等 14 列 RsXml 表头本来就有）。登录前按列类型校验（bit、smallint、整数、小数，不收空串；浮点最多 15 位有效数字），只发调用方给出的列，不从模板、不从当前值带；`meta` 的 `writable` 列出全部标签。`get` 的 `fields` 返回 113 个 Sub 列（字符串），其中 35 个 U8 不写的列（`fMaterialCost`、`dInvCreateDatetime`、八个标准成本列、`iPlanCheckDay` 等，样式表里有但实测不写的也在内）只读。新增、修改成功后桥在新连接上核对每个调用方给的 Sub 列都已落库，没写进去的（例如存货未启用批次管理时 `bBatchCreate` 和批次属性、未启用保质期管理时 `iExpiratDateCalcu` 不写）返回 502 `u8_fields_ignored`，`error.detail.fields` 列出 `sent`、`stored`，存货已保存。
- **收付款协议 `pay_agreement` 可新增、修改、删除**：U8 没有这个档案的 EAI 对象，桥用受控 SQL 在一个事务里写 `AA_Agreement`（锁编码、事务内核对、提交后新连接回读，不符 504），预演为 `rollback`。`fields` 的键是表列名（`cName` 必填、`cMemo`（不存 NULL）、`dEDate`、条款列 `iLZYJ`、`iPLNum`、`dblZQNum` 等），制单日期、制单人由桥写；协议编码最长 10。删除先查引用：客户、供应商档案的协议栏，以及单据、往来明细、合同上的收付款协议栏 `cGatheringPlan`、`cGatheringPlanCode`，被引用 409；要探几十张表，删除比其他档案写入慢。功能权限 `AS60202`。
- **自定义项档案、客户存货对照可修改**：U8 的 EAI 不提供修改，`archives/update` 改由桥直接改表，只改非主键列（自定义项的 `alias`、`barcode`；对照的客户存货编码、名称和检验设置），编码两段不能改；同一（客户、存货）有多条对照时 409。自定义项的代码、条码在同一自定义项下不能重复（U8 的 `CheckcAliasUnique`），重复 409。功能权限 `AS025`、`AS1204E`，预演为 `rollback`。`meta` 的 `updatable` 为 true，API、客户端 `arc-update` 不再拒绝这两类。
- **客户字段 `archives/customer_fields` 扩到整张白名单**：除原有的简称、名称、税号、地区分类、停用日期、销售默认收付款协议，新增联系方式（`contact`、`phone`、`mobile`、`fax`、`bp`、`email`、`address`、`postcode`、`legal_man`、`memo`、`devliver_site`）、银行（`bank_open`、`bank_acc_number`、`ccusbankcode`）、结算与发运（`pay_condition`、`ccussscode`、`deliver_mode`、`deli_warehouse`、`ccusexch_name`）、四个协议键、客户分类 `sort_code`、所属行业 `ccustradeccode`（同时写行业名称）、总公司 `head_corp_code`、发展日期 `seed_date`、自定义项 `self_define1`…`16`（按账套的客户自定义项设置校验）、对应供应商 `crelvendor`（同事务维护供应商的反向关联）、四个 0/1 标志位和四个比率。一张声明表驱动解析、校验、SQL 和响应；信用、统计、助记码、开票单位、部门与专管业务员、可修订项、GSP / 许可证列不进这条路由。账套开启客户名称唯一（`RptCusName=1`）时改名称重名 409 `name_taken`；协议键按 U8 的协议用途 `iLZYJ` 过滤。键 → 列见接口参考「客户字段」。**升级顺序：先桥，后 API。**

### 变更

- **客户字段的响应只含请求的键**：`archives/customer_fields` 的 `before`、`after` 和 409 `detail.current` 只含请求 `fields` 里的键（此前固定带全部六个键），键按声明表顺序，空值仍是空串。
- **合并检验规则只收一个**：`merge_rules` 只能给一个合并规则编码，多于一个 400（多个编码以逗号写入表头时，U8 保存后回读报「获取单据失败」）。来源可只检部分数量，剩余数量按普通检验单检验。
- **生产订单修改可替换替代料**：`vouchers/update` 的 `production_order` 行上可带 `allocations`（每项 `sort_seq`、`inv_code` 定位子件，`substitutes` 整组替换该子件的替代料，空数组清空；替代料每项同物料清单，另收 `defines`）；只带 `allocations` 的行也算修改。只改替代料时不调用 U8，桥在一个事务里锁子件、删旧插新、核对后提交，子件 `AllocateId` 不变；与行字段一起改时照常调用 U8，写回替代料时用请求的代替快照。属第二级写入，只对测试账套开放。响应另给 `allocations`（替代料改了的子件和回读的替代料）。替代料的 `eff_begin`、`eff_end` 和日期型自定义项不能早于 1753-01-01（`datetime` 列的下限），否则 400；物料清单的替代料起止日期、表头 `eff_date` 和生产订单的 `start_date`、`due_date` 共用同一校验，同样 400。
- **权限快照缓存秒数可配置**：桥新增 `config.json` 可选键 `permCacheSeconds`（缺省 60，0 到 3600，重启生效），此前固定 60 秒。0 为不缓存，每个读请求都重新读取权限，用于测试中改了 U8 权限、须立即生效的场合；健康检查新增 `perm_cache_s`，`perm/snapshot` 的 `ttl_s` 返回生效值，`--check-config` 打印该项。旧版桥拒绝未知键：先升级桥再写入本键，回滚前先删除。
- **发货单、到货单只认各自的单据**：`vouchers/list`、`vouchers/search`、附件和 `vouchers/load` 里，`dispatch` 只收蓝字发货单（`bReturnFlag` 为 0 或空），`arrival` 只收蓝字到货单（`iBillType` 为 0 或空），红字发货单、采购退货单只在 `sale_return`、`purchase_return` 下；此前 `dispatch` 的列表含红字发货单、`arrival` 的列表含采购退货单，`dispatch`、`arrival` 的读取也不核对。`vouchers/load` 读到同表另一个类型的单据（四个类型两两对应，`sale_return` 读蓝字发货单、`purchase_return` 读到货单同样）400 `bad_request`，`detail.reason` 为 `type_mismatch`、`detail.type` 给出实际类型；没有数据权限的操作员仍先得到 403。事件服务据此区分「改属别的类型」和「删除」：快照里记在某个类型下、扫描里不见了的单据，逐张 `vouchers/load` 确认，回答 `type_mismatch` 的只清快照、不发 `deleted`（任何一对同表类型都一样，水位倒退后的整轮重读也同样处理），所以升级后红字发货单、采购退货单不会在 `dispatch`、`arrival` 下发 `deleted`；其他回答照旧发 `deleted`。删除扫描里不见了的单据因此多一次 `vouchers/load`（最多 `empty_scan_confirm_max` 张）。
- **销售行写报价含税 `bsaleprice`**：新增销售订单、无来源发货单 / 发票、修改时新增的行，以及参照生成的发货单、销售发票、退货单、退货申请、红字发票、红冲蓝字发票，每一行在 `BodyCheck` 之前写 `bsaleprice`，同 U8 界面：新增行取账套选项「销售报价是否含税」（SA `bSalePrice`），参照生单、退货、退货申请、红字和红冲蓝字照抄来源行（来源为空时取选项）；选项为是时金额按含税单价算，不再按无税单价差一分，旧行全额冲销仍冲平。
- **生产订单替代料**：`vouchers/load`（含 `load_many`）的 `production_order` 每个子件另带 `substitutes`（`mom_moallocatesub`：`sequence`、`inv_code`、`factor`、`replace_flag`、`eff_begin`、`eff_end`，自定义项有值时 `defines`）。字段权限：子件存货无权查看时 `inv_code` 为 null，`define22`–`37` 随子件同号自定义项遮蔽。`vouchers/update` 不再拒绝带替代料的订单：`MOrderUpdate` 重插子件时丢掉替代料，桥在写回子件关联列的同一事务里按调用前快照原样插回新子件并核对，不符回滚并 504 `outcome_unknown`；快照随审计事件 `mo_update_snapshot` 记录。子件由替代料生成（`MoallocateSubId` 非空）、用替代料出库的订单仍 409。
- **物料清单替代料、定位符**：`vouchers/load` 的 `bom` 每行另带 `substitutes`（`bom_opcomponentsub`：`sequence`、`inv_code`、`factor`、`replace_flag`、`eff_begin`、`eff_end`，自定义项有值时 `defines`）和 `locators`（`sort_seq`、`loc`），行上的 `extras` 换成 `client_only`（只标分段损耗和子件、替代料的自由项）。`vouchers/create` / `vouchers/update` 的明细行可带 `substitutes`、`locators` 数组（各最多 50 项）：修改时没带的行保留现有的（桥原样重送，U8 差异更新会删掉没送的），带了整组替换，空数组清空；新增行和带了 `substitutes` 的行上的替代存货按子件规则查，不能是该行子件或母件本身，同一行不重复；母件是选项类 409。写后回读核对。带替代料、定位符的清单不再 409，带分段损耗或自由项的仍 409。字段权限按 U8 对象 `BO26` 遮（子件存货、替代料的 `inv_code` 和自定义项）。桥、API、客户端各用一张按单据类型登记的表放行数组字段（检验单表头 `items`、物料清单行的这两个数组），其他类型、其他字段仍不能嵌套。
- 物料清单母件闸门改为与 U8 一致：存货只需「允许做物料清单母件」（`bBomMain`），模型、选项类不必是自制件；否则 400「不允许做物料清单母件」。

### 修复
- **事件服务 `accounts[].restored_by_tests`**：标记会被测试还原（数据库快照或备份）的测试账套，水位倒退只记警告、不报不健康；告警的类型问题不再重复报水位倒退（由健康检查判断）。

- **存货核算记账、恢复记账处理出入库调整单**：`ia/post` 此前只装出入库单据，手工录入的入库调整（20）、出库调整（21）不记账（`IA_AccountLoad` 的调整单条件为空）；现在按 U8 界面的做法同批装入单据日期在本月的未记账调整单，加锁、盖记账人和记账日期，结果在明细账和汇总表里。`ia/unpost` 同步恢复 20 / 21：回退汇总表、删明细账行、清调整单记账人；记账时系统生成的调整单整张删除，避免重复生成；成本管理生成的「在产成本调整」不恢复，留在记账状态。诊断计数新增 `just_posted`、`just_still_posted`。
- **取消期末处理只删期末处理生成的出库调整**：`ia/period_end` 的 `cancel` 此前删除该月全部出库调整（21）的明细账行，现在只删期末处理生成的（明细账 `cBusType` 为「期末处理」），并连同调整单行、没有行的表头一起删，不再留下已记账的孤立调整单；手工录入的、记账时生成的 21 不动。
- **恢复记账拒绝已制单的记账行**：本月有记账行已制单（`IA_Subsidiary.cPZID` 非空）时 `ia/unpost` 返回 409「本月有已制单的记账行，请先取消制单（ia/voucher/delete）再恢复记账」，同 U8 恢复记账清单不列已制单行；此前这类行会被恢复，总账凭证的来源行丢失。
- **存货核算支持范围增加出库成本绝对值算法**：存货核算选项「出库成本按绝对值算法」（`bAbsOutCost`）打开时，`ia/post`、`ia/unpost`、`ia/period_end`、`periods/close` 的 `module=ia` 返回 409「…接口暂不支持」。
- **存货核算期末处理、月末结账拒绝未记账的出入库调整单**：本月（单据日期在本月）还有未记账的手工出入库调整单（20 / 21；系统生成的不算）时，`ia/period_end` 的 `run` 和 `periods/close` 的 `module=ia` 返回 409 `state_mismatch`「本月还有 N 行未记账的出入库调整单，请先正常单据记账（ia/post）」，同 U8 界面的检查（期末处理限存货、仓库在处理范围内）。

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
