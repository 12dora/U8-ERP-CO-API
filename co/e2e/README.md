# co/e2e：测试账套端到端回归

在一个专用的测试账套上跑桥的全部路由，结束时用 SQL Server 数据库快照把测试账套的两个库还原到快照时点，并用逐表指纹证明已经还原。本文是参考手册：模块、用例清单、调试会话、安全护栏、已知的还原差异、加用例的规则、多通道分片回归。前提、配置键和日常运行见用户指南 [`docs/testing.md`](../../docs/testing.md) 第 4 节，这里不重复。

```bash
RUN="uv run --no-project --python 3.12 --with cryptography python -m"
$RUN co.e2e.run_e2e --dry-run                      # 只读预检和指纹
$RUN co.e2e.run_e2e --cases health,login           # 只跑指定用例
$RUN co.e2e.run_e2e --jobs 4                       # 并行（试验性）
$RUN co.e2e.run_e2e --serial                       # 串行（缺省）
$RUN co.e2e.run_e2e --changed-since origin/main    # 只跑与改动相关的用例（开发自测）
$RUN co.e2e.session open                           # 调试会话
$RUN co.e2e.session extend --minutes 60
$RUN co.e2e.run_e2e --in-session --cases health,login
python -m unittest discover -s co/e2e/tests -t .   # 离线单元测试
```

缺省串行（`--jobs 1`）；`--jobs N` 并行跑资源不冲突的用例，是试验性的（偶发 U8 组件互相卡住，见「运行」），只用于开发自测。`--changed-since` 只跑与改动相关的用例，是开发自测的过滤器，不能代替完整回归。`--in-session` 在已 `session open` 的会话里跑用例，不预检、不快照、不还原（还原由 `session reset` / `close` 负责），报告 `mode: in-session`、`rollback_verified: null`，只用于开发自测，`multi` 不收，见「会话内跑用例」。完整回归的结论以全量串行或多通道分片回归（`python -m co.e2e.multi`）合并结论 PASS 为准。

## 模块

| 文件 | 作用 |
| --- | --- |
| `run_e2e.py`、`flow.py`、`suite.py`、`engine.py` | 回归编排：预检、快照、看门狗、部署、跑用例、还原、报告 |
| `insession.py` | `run_e2e --in-session`：在打开的会话里跑用例，持通道独占锁，跳过准备、快照和还原 |
| `session.py`、`session_*.py` | 调试会话：`open`、`status`、`deploy`、`call`、`sql`、`audit`、`reset`、`extend`、`close` |
| `settings.py`、`settings_parse.py`、`e2e.example.json` | 读取并校验本机配置 `~/.config/u8co/e2e.json`（可用 `U8CO_E2E_CONFIG` 换一份；不认识的键直接报错），以及样例 |
| `lane.py`、`lanelock.py`、`lane.example.json`、`remote/grant-lane.sql.example` | 多条调试通道：通道指纹、防撞名的启发式检查、通道独占锁、通道配置样例和 SQL 授权模板 |
| `multi.py`、`multi_report.py`、`shard_assign.py`、`shard_recompute.py`、`shards.example.json` | 多通道分片回归：分片表、子进程编排、合并报告、按报告重算分片表 |
| `runfiles.py` | 每次调用唯一的临时文件名，同一会话里并行的 `sql`、`call`、`audit`、`status` 互不覆盖 |
| `testdata.py`、`testdata.example.json` | 测试数据文件（科目、部门、档案候选编码等）和样例 |
| `plan.py`、`fingerprint.py`、`fingerprint_bg.py`、`cases.py`、`markers.py`、`report.py`、`safety.py`、`guard.py`、`daytime.py`、`timing.py` | 纯判断：步骤、指纹比对和 U8 后台表容忍名单、会话标记、报告、安全检查、业务时间。不连服务器 |
| `sqlguard.py`、`sqlchan.py`、`sqlssh.py` | SQL 文本守卫和经 SSH 的 SQL 通道；并行跑用例时每个线程一条（`SqlPool`），共用一条 SSH 主连接 |
| `remote.py`、`mux.py`、`kitpack.py`、`bridge_job.py`、`deployskip.py`、`remote_idle.py` | SSH / scp（共用一条主连接）、建远程工作目录并锁权限、上传前的空闲检查、打包上传桥的源码、源码未变时跳过部署的判定 |
| `taskcfg.py`、`calllog_io.py`、`callshape.py`、`context.py` | `U8TaskConfig` 差异判定、读审计、调用正文、运行上下文 |
| `wpolicy.py` | 写入策略用例：替换本通道桥的 `writePolicyFile`、等桥重载、用完写回。写回重试后仍失败时把通道标记为阻塞（`lane_blocked`），本次运行之后的用例记 SKIP 并带上原因。阻塞标记只在本进程内存里，不落盘、不跨进程：`multi` 的其他通道进程、下一次 `run_e2e` 或 `session` 命令都看不到它。看到「没有还原」的报错后，先按报错手工恢复桥的 `writePolicyFile`（`/u8co/v1/health` 的 `write_policy.state` 回到 `ok`）再跑这条通道 |
| `perm_oracle.py` | 权限核对的纯计算：按账套库数据独立算出应有权限 |
| `perf_reads*.py`、`concurrency_probe.py` | 只读路由性能和并发登录探测（见「只读路由性能」） |
| `seed/` | 空账套造数：`python -m co.e2e.seed plan` / `run`，含 `config.py`（可选 `seed` 配置段）和 `preflight.py`（造数前的只读预检） |
| `casespec/` | 用例登记表（`catalog.py` 汇总）、挑单 SQL、正文构造、断言、资源标签（`tags.py`）、`--changed-since` 的映射（`coverage.py`） |
| `casespec/sched.py`、`casespec/runner.py`、`casespec/gate.py` | 跑用例：`--serial` 一条接一条；`--jobs N`（N>1）时登录及其之前串行，其余资源标签不相交的同时跑，每条用例一份 Env，报告按登记顺序；中断后闸门关上，不再发桥调用和 SQL |
| `remote/*.ps1` | 服务器端脚本：预检、快照、还原、看门狗、服务启停、换程序、SQL 工人 |
| `tests/` | 离线单元测试 |

## 用例

用例名用于 `--cases`。全部用例名以 `casespec/catalog.py` 为准，下面按主题列出主要用例和需要说明的细节。用例从测试账套里按条件挑单据；挑不到就 SKIP（标了可选的记 N/A）。会新建或生单的用例在同一条里删掉新单据，并回读确认来源累计数回到原值。

### 基础和审核

| 名字 | 做什么 |
| --- | --- |
| `health` | 健康检查 |
| `bad-signature` | 错误签名得 401 |
| `account-foreign` | 名单外账套得 403，审计里没有操作员和年度 |
| `login` | 登录检查 |
| `sale-verify` / `dispatch-verify` | 未审核的销售订单、蓝字发货单审核再弃审 |
| `missing-404` | 不存在的单据 404 |
| `bad-action` | 非法 `action` 400 |
| `repeat-unverify` | 已是目标状态 409 `state_mismatch` |
| `downstream-unverify` | 有下游的发货单弃审被拒绝 |

### 审批流和单据读写

| 名字 | 做什么 |
| --- | --- |
| `wf-qm04-cycle` | 产品检验单审批闭环 |
| `wf-qm04-withdraw` | 提交后撤销 |
| `wf-qm06-state` / `wf-qm03-state` | 读取审批状态 |
| `wf-other-in-type` | 其他入库不走审批流，400 |
| `stock-09-verify` / `stock-09-reverse` | 其他出库审核与弃审、弃审与重新审核（可选） |
| `other-in-cycle` / `other-out-cycle` | 其他入库、其他出库新建、审核、弃审、删除 |
| `po-verify` / `arrival-verify` | 采购订单、到货单审核再弃审 |
| `sale-create` | 销售订单新建、审核、删除 |
| `load-<类型>` | 读取各类型最新或符合条件的一张单据；两种报检单另核 `source.type`（到货单 / 生产订单）、有明细、不带 `wf`；其他报检单（`load-qm_other_inspect`）核 `source` 为 null、有明细、不带 `wf`，其他检验单（`load-qm_other_check`）不带 `wf`，两条都可选（没有数据 N/A） |
| `opinion-too-long` | 审批意见超长 400 |
| `create-protected` | 新建时带制单人等禁写字段，400 |
| `account-foreign-load` | 名单外账套读取 403 |

### 修改、关闭和生单

| 名字 | 做什么 |
| --- | --- |
| `sale-update` | 新建销售订单，改备注和数量，加一行，再删除 |
| `sale-close` | 关闭并打开已审核订单，再关闭一行；未审核订单关闭应被拒绝 |
| `po-cycle` | 采购订单从新建、修改、审核、关闭到删除 |
| `po-close-lines` | 已审核采购订单只关闭一行再打开 |
| `dispatch-generate` | 销售订单生发货单，再整张生成销售出库，然后删掉 |
| `invoice-generate` | 生成销售发票 → 删除（累计开票数回退）→ 再生成 → 复核 → 弃复 → 删除。发货行有已复核开票数量残留时删除 409，发票留给快照还原；这是「用例删掉自己新建的单据」的唯一例外 |
| `purchase-in-generate` | 采购订单生采购入库，审核后再删除 |
| `transfer-cycle` | 调拨单新建、修改、审核（带出其他入出库）后删除 |
| `other-in-update` | 其他入库改数量并加行，核对金额 |
| `sale-invoice-unverify-reject` | 应收已审的销售发票弃复被拒绝 |
| `update-verified` | 修改已审核销售订单被拒绝 |
| `close-other-in` | 其他入库不能关闭 |

### 财务、生产、列表和并发

| 名字 | 做什么 |
| --- | --- |
| `ar-receipt-cycle` / `ap-payment-cycle` / `ar-bill-cycle` / `ap-bill-cycle` | 新建 → 读取核对单号和金额 → 审核（有往来明细）→ 已审删除 409 → 弃审（明细删光）→ 删除 → 读取 404 |
| `ar-receipt-voucher-refuse` | 已制凭证的收款单弃审、删除都 409 |
| `material-out-generate` | 已审生产订单的子件生成材料出库，已领量 +1，删除后回到原值 |
| `material-out-no-rd` | 同一挑单生成材料出库但表头不带 `crdcode`：400 `bad_request`、`field` 为 `head.crdcode`，子件已领量不变。挑不到记 N/A。可选 |
| `product-in-generate` | 已审产品检验单生成产成品入库，合格入库量和检验单累计入库量增加，删除后回到原值 |
| `purchase-invoice-generate` | 已审采购入库行生成采购发票：错来源类型和缺发票号先 400；累计开票数 +1；同号再生成 409；删除后回到原值 |
| `purchase-in-from-qm` | 已审来料检验单生成采购入库，三处累计数 +1，删除后回到原值 |
| `arrival-generate` | 采购订单生成到货单：超剩余 409；到货 1 个后累计到货 +1；删除后回到原值 |
| `mo-unverify-verify` | 没有下游的已审生产订单弃审再审核 |
| `mo-verify-audited` | 已审生产订单再审核 409 |
| `list-sale-order` | 销售订单列表按客户和日期筛选，行数与 SQL 计数一致；`keys_only` 与完整列表 id 相同 |
| `list-ar-receipt` | 收款单列表筛选并翻页 |
| `list-changed-since` | 取水位，新建一张其他入库，按 `changed_since` 只返回之后改过的行，再删除 |
| `stock-current-filter` | 现存量按仓库和存货筛选，行数与 SQL 计数一致 |
| `bridge-concurrency` | 同时发 12 个请求（9 种读取 + 3 张其他入库新建），单号和 id 不重复，再并发删除 |
| `gl-cycle` | 转账凭证新建、修改、审核、已审修改 409、取消审核、无现金行签字 409、作废、取消作废、作废、删除 |
| `gl-bank-cashflow` | 带现金流量的付款凭证，出纳签字、取消签字后删除 |
| `gl-module-readonly` | 其他模块生成的凭证不能在总账作废 |
| `gl-list` | 列表能查到新建的凭证 |
| `archive-<档案>-cycle` | 九类档案各一条：新建、读取、修改、删除；存货另写 `Inventory_Sub` 的非缺省值 `finvoutuplimit`、`binbyprocheck`（0）、`bbondedinv`（1）、`iwarrantyperiod`（5）、`fbuyexcess`，读取核对，修改规格后这几列保持不变（U8 没写会被桥的落库核对报 502 `u8_fields_ignored`） |
| `archive-inventory-sub-ignored` | 存货新增带 `bbatchproperty1`=1（模板存货没有批次档案标志时 U8 不写）→ 502 `u8_fields_ignored`、`detail.fields.bbatchproperty1`（sent 1、stored 0）→ 删除新存货 → 读取 404；U8 真写进去则 N/A |
| `archive-user-define-alias-dup` | 自定义项档案：同一自定义项下建两个档案值，把第二个的 `alias` 改成第一个的 → 409 `state_mismatch`，档案值不变，两个都删除 |
| `archive-delete-used` | 已被单据引用的客户删除 409 `u8_rejected` |
| `archive-list` | 客户档案按编码前缀分页 |
| `voucher-so-defines` | 销售订单表头自定义项写入（`vouchers/so_defines`，`so_defines*`）：数据驱动挑已审核订单（`cdefine1` / `cdefine2` 为空的未关闭无发货、已关闭、已发货各一张，另一张 `cdefine1` 已有值），fields 为 `cdefine1` + `cdefine2`、`empty_values` 为「无」：预演不改库；expect 不符 409，`detail.current` 的键恰好是请求的列；第一张另走否定路径（不认识的键、`cdefine17`、值为「无」、空白、`type` 不对、超长、非文本列 400，后两者 `field=fields.cdefineN`；订单不存在 404）、别人锁定 409 `reason=locked`；真写只改请求的列和修改人类列，下游不动；幂等重放、已是目标 `changed=false`、大小写不同算真改、只写 `cdefine3` 不动另两列；再走 `cascade=true` 一轮（只来自本订单的下游全部 updated、再写 unchanged；有外来明细的 `skipped` / `mixed_sources`）。路由只写不清空，结束用 SQL 把写过的列按起始快照写回 |

### 只读报表和档案、退货、请购单、幂等键

| 名字 | 做什么 |
| --- | --- |
| `meta-fields` | 字段元数据：签名调用、不带账套和口令；含 `revision`、`features`、各单据类型和全部只读档案 |
| `report-close-status` / `report-gl-balance` / `report-gl-aux-balance` / `report-arap-balance` / `report-arap-aging` / `report-bom` | 六张报表各调一次，核对响应和行的键：科目余额表 1 月到当月、一级、非零；客户辅助核算 5 行；应收余额；应付账龄按到期日（每行账龄列数等于 `buckets`）；挑一个有效母件展开两层 |
| `report-arap-detail` / `report-gl-detail` | 明细账（`report_detail`）：用应收余额挑一个客户，从当年 1 月 1 日查到登录日期，期末等于往来余额、期初 + 借 − 贷 = 期末；用科目余额表（1 月到当月、一级、非零）挑一个有发生额的科目，期初、借贷合计、期末与余额表一致；有下一页时再读一页，核对滚动余额连续。挑不到记 N/A |
| `report-stock-summary` / `report-stock-ledger` / `report-position-stock` / `report-batch-stock` / `report-customer-credit` / `report-price-list` | 库存与销售支持报表（`report_stock`）：用现存量挑一个有数的（仓库、存货），从当年 1 月 1 日查到登录日期——收发存汇总表的期末等于现存量合计、期初 + 入 − 出 = 期末；库存台账的期初等于汇总表的期初，逐行结存连续，有下一页时第二页的 `carry` 等于第一页最后一行的结存；货位存量每行有货位、数量不为 0；批次存量与同一批号的现存量合计一致；客户信用的 `used` 等于额度检查公式里打开的各项之和；价格表三种各列一次（都没有数据记 N/A）。挑不到记 N/A |
| `archive-read-<档案>` | 只读档案各列 5 行再读取一个：`account`、`unit`、`unit_group`、`settle_style`、`voucher_sign`、`currency`、`bank`、`project`、`position`、`rd_style`、`purchase_type`、`sale_type`、`district_class`、`trade_class`、`aa_bank`、`customer_address`、`user_define`、`customer_inventory`、`pay_agreement`、`customer_invoice_company`（用例名里下划线换成连字符）；币种按 SQL 挑的名称读，项目编码是 `<大类>:<编码>`，两列主键的档案读列表第一行的 `<第一段>:<第二段>`；账套里没有该档案记 N/A |
| `list-sale-return` / `list-purchase-return` / `list-purchase-requisition` / `list-dispatch` / `list-arrival` / `list-stock-opening` | 单据类型的列表：id 升序、水位是数字串（可以为空列表）；发货单、到货单只含蓝字，退货单只含红字（`red` 与类型一致） |
| `purchase-invoice-review` | 采购入库生成采购发票 → 复核（`cVerifier` 是操作员，读取 `state.reviewed`）→ 取消复核（清空）→ 删除，累计开票数回到原值 |
| `purchase-return-cycle` | 蓝字到货行退 1 个：原行 `fRetQuantity`、订单行 `fPoRetQuantity` +1，`iArrQTY` −1；审核、弃审、删除后三处回到原值 |
| `sale-return-cycle` | 蓝字发货行按未开票退 2 个：原行 `iRetQuantity`、`fretqtywkp` +2，订单行 `fretquantity` +2、`iFHQuantity` −2，新行 `iCorID` 指回原行；审核、弃审、删除后回到原值 |
| `purchase-requisition-cycle` | 请购单新建、读取、改备注、未审核关闭 409、审核、关闭、打开、弃审、删除。需要测试数据 `purchase_requisition.*` |
| `product-in-from-reject` | 已审产品不良品处理单（处理流程 2、未入库）生成产成品入库：部分数量 409「须一次入库全部」，全部数量成功，入库行挂处理单行、处理单行标已入库；删除 200 或 409 都算通过（U8 可能自动关闭订单行，留给快照还原）。可选 |
| `purchase-in-nosrc-havepo` | 采购选项「普通业务必有订单」打开时无来源采购入库 409；选项关闭记 N/A，回归不改账套选项。可选 |
| `idempotency-gl-create` | 总账凭证带幂等键：借贷不平 400 不占键 → 同键合法正文新建 → 重放返回同一张、库里按摘要只有一张 → 同键换内容 409 `idempotency_mismatch` → 作废删除 |

### 生单单据的修改、按行生单、生产订单行关闭

修改类用例都是先生单 2 个、改成 1 个（数量只能减少），核对来源累计数少 1、新行金额已重算，再删除并确认来源回到原值；生单和修改后的回读先记进 bag，删除之后再判。

| 名字 | 做什么 |
| --- | --- |
| `dispatch-update` | 发货单修改：数量 2 改 1（改大 400），订单行累计发货随之回退，金额重算，删除后还原 |
| `sale-return-update` | 退货单（红字发货单）修改：退货数量 2 改 1，原发货行和订单行回写随之回退，删除后还原 |
| `sale-invoice-update` | 销售发票修改：数量 2 改 1，发货行累计开票随之回退，金额重算，删除后还原 |
| `arrival-update` | 到货单修改：数量 2 改 1，订单行累计到货随之回退，金额重算，删除后还原 |
| `purchase-return-update` | 采购退货单修改：退货数量 2 改 1，到货行、订单行回写随之回退，删除后还原 |
| `purchase-invoice-update` | 采购发票修改：数量 2 改 1，入库行累计开票随之回退，金额重算，删除后还原 |
| `purchase-invoice-update-red` | 红字采购发票修改：退货 2 个 → 红字入库 2 个 → 红字发票 2 个，数量 2 改 1 并改备注，入库行累计开票移向 0（2 → 1），金额重算，删除后回 0。可选 |
| `sale-out-update` | 销售出库单修改：数量减 1，发货单累计出库随之减 1，删除后回退 |
| `purchase-in-po-update` | 参照采购订单的采购入库单修改：数量减 1，订单行累计入库随之减 1，删除后回退 |
| `purchase-in-qm-update` | 参照来料检验单的采购入库单修改：检验单、到货单、订单累计数随之减 1，删除后回退 |
| `product-in-update` | 参照产品检验单的产成品入库单修改：合格入库量、检验单累计入库随之减 1，删除后回退 |
| `material-out-update` | 材料出库单修改：数量减 1，子件已领量随之减 1，删除后回退；加数量、新增行 400 |
| `sale-out-partial` | 发货单按行部分生成销售出库：只出请求行 1 个，其余行不动，删除后回退 |
| `binned-other-in-delete` | 有货位记录的其他入库删除：先清货位再删，`InvPosition` 与货位存量回退 |
| `ar-receipt-edit` / `ap-payment-edit` / `ar-bill-edit` / `ap-bill-edit` | 新建（金额 10）→ 改金额为 7、改摘要 → 回读表头合计、未核销、表体合计与行数、摘要、修改人，往来明细仍为 0 → 删除 → 读取 404 |
| `purchase-return-po-cycle` | 采购订单生成采购退货单：订单行累计退货 +1、累计到货 −1，新行挂订单行；审核、弃审、删除后回到原值 |
| `production-order-close-cycle` | 生产订单按行关闭（关闭人有值）→ 再关 409 → 打开，行状态回读，其他行前后不变。可选 |
| `mo-create-delete` | 生产订单新增（不带单号，U8 自动编号；存货、部门、订单类别从账套挑）→ 未审核、子件已按标准 BOM 展开 → 审核 → 删除 409 → 弃审 → 删除 → 读取 404，库里不留行。可选 |
| `mo-create-bad` | 生产订单新增拒绝：存货不存在、完工日期早于开工日期，都是 400 `bad_request`，不调用 U8。可选 |
| `mo-update-allocates` | 生产订单修改（`mo_update`）：挑单同 `mo-create-delete`，新建（数量 1，子件按标准 BOM 展开）→ 改表头备注和完工日期（子件行数、数量合计、关联列校验和（`OpComponentId`、条码、需求跟踪、用量、日期、替代料行数）不变，`UpperMoQty` 等于行数量）→ 数量改成 2（行数量翻倍、子件行数和校验和不变、每个子件数量等于基本用量 × 行数量按账套小数位舍入）→ 删除（表头、明细、子件都没了）。可选 |
| `mo-update-verified-nodown` | 已审核生产订单修改（`mo_edit`）：新建 → 审核 → 数量改成 2（响应仍已审核；行仍 `Status=3`、有审核人，数量翻倍，子件行数、校验和不变，子件按基本用量重算，最小 `AllocateId` 大于修改前最大值）→ 弃审 → 删除。可选 |
| `mo-update-verified-issued` | 已领料生产订单修改：新建 → 审核 → 挑一个可领料子件（变动用量、现存量够）领满生成材料出库单（出库行指向该子件，已领量大于 0）→ 数量改成 2（出库行全部指向本单同一存货的新子件、已领量合计不变、行仍已审核）→ 删出库单（已领量回到 0）→ 弃审 → 删除。可选，数据 `stock.material_out_rd` |
| `mo-update-issued-below-409` | 新建 → 审核 → 数量改成 2 → 领满一个子件 → 数量改回 1（需求少于已领）409 `state_mismatch`，出库行仍指原子件 → 删出库单 → 弃审 → 删除。可选 |
| `mo-update-closed-409` | 新建 → 审核 → 按行关闭 → 修改 409 `state_mismatch`（请先打开）→ 打开 → 弃审 → 删除。可选 |
| `mo-update-substitutes` | 生产订单修改保留替代料（`mo_update_subs`）：挑标准 BOM 子件带替代料（`bom_opcomponentsub`）的自制件，新建（数量 1）→ 记下 U8 新增时按 BOM 带出的替代料行数（`mos_created`，只记录）→ 一行都没有时夹具照 BOM 给一个子件插一行替代料（`mos_seed`，只动这张新订单），保证修改路径总被走到 → 每行替代料（`mom_moallocatesub`）都是 BOM 上该子件的替代料 → 改表头备注和完工日期 → 替代料行数、内容校验和不变，全部挂在新子件上（`AllocateId` 大于修改前最大值）→ 数量改成 2（子件按基本用量重算，替代料同样不变）→ 删除。挑不到记 N/A。可选 |
| `mo-update-substitutes-replace` | 生产订单修改由调用方替换 / 清空替代料（`mo_subs_replace`，第二级写入）：照上一条挑单新建生产订单，另挑一个允许做 BOM 子件、未停用、不是母件和它子件的存货 → 取第一个子件和原有替代料 → 预演替换（替代料不变）→ 只换替代料：200、`changed=1`、响应 `allocations` 即给的、`AllocateId` 不变（不调用 U8），读取等于给的 → 清空，读取为空 → 反例不改单据：子件对不上 409、替代料是子件本身 / 母件 / 不允许做 BOM 子件的存货 400 → 改数量同时替换（调用 U8，`AllocateId` 换新），读取等于给的 → 换回原有的 → 删除。挑不到存货、桥没开第二级写入或账套不在 `testAccounts` 里记 N/A。可选 |
| `bom-lifecycle` | 物料清单新增版本（母件和三个子件从它现有的最新标准 BOM 挑，另挑一个做过子件的存货做替代料，远期生效日期；行 10 带一条替代料）→ 读取（行 10 一条替代料）、未审核 → 修改（行 10 改用量、删行 20、加第三个子件，不送替代料：回读仍是原替代料）→ 替换行 10 的替代料（子件 2，优先级 2、系数 2）→ 清空替代料（回读 0 条）→ 审核 → 删除 409 → 弃审 → 删除 → 读取 404，版本、母件、子件和选项行都不留。可选；中途失败时尽力弃审并删除 |
| `bom-create-bad` | 物料清单新增拒绝：子件不存在 400 `bad_request`、版本号重复 409 `state_mismatch`，都在调用 U8 之前。可选 |
| `archive-position-write` / `archive-unit-write` / `archive-user-define-write` / `archive-customer-inventory-write` | 档案写入（新增、删除走 EAI；自定义项档案、客户存货对照的修改走桥的受控 SQL，只改 `alias`、`barcode` / 客户存货名称这类非主键列）：依赖（启用货位的仓库、计量单位组、需要建档的自定义项、没有对照的客户和存货）从账套挑，新编码带时间（`ZZ` + 月日时分秒；货位用空闲的一级编码 `Z0`…）→ 新增 → 读取 → 修改 → 读取 → 删除 → 读取 404。挑不到记 N/A；中途失败时尽力删掉新建的档案 |
| `archive-rw7-delete-guard` | 有下级的货位、已被存货使用的计量单位删除都是 409 `state_mismatch`（桥在调用 U8 之前拒绝），之后仍能读取 |
| `archive-unit-group-write` / `archive-settle-style-write` / `archive-rd-style-write` / `archive-purchase-type-write` / `archive-sale-type-write` / `archive-district-class-write` / `archive-aa-bank-write` | 七类档案写入（EAI）：编码挑第一个空闲的（`ZZ` 打头；结算方式、收发类别、地区分类按编码方案建成一级，一级只有 1 位时用 `Z`、`Y`、`X`）；计量单位组建固定换算（`type=1`，无换算组 U8 只许一个），采购类型、销售类型带 SQL 挑的末级入库 / 出库类别 `rstype_code`（档案设置可能要求必输）；新增 → 读取 → 修改名称 → 读取 → 删除 → 读取 404。币种没有这条通用用例，见 `archive-currency-write` |
| `archive-rw8-delete-guard` | 有下级的收发类别、已有计量单位的计量单位组删除都是 409 `state_mismatch`（桥在调用 U8 之前拒绝），之后仍能读取 |
| `archive-pay-agreement-write` / `archive-pay-agreement-delete-guard` / `archive-pay-agreement-delete-guard-doc` | 收付款协议（受控 SQL）：编码取第一个空闲的 `ZZ00`…`ZZ09` → 新增（`cName` 和条款列 `iLZYJ`、`iPLNum`、`dblZQNum`）→ 读取 → 修改（`cName`、`dblZQNum`）→ 读取 → 删除 → 读取 404；guard 挑一个被客户档案销售协议栏（`cCusSAProtocol`）引用的协议，guard-doc 挑一个只被销售订单的 `cgatheringplan` 引用、客户和供应商档案都没用的协议，删除都是 409 `state_mismatch`，之后仍能读取。挑不到记 N/A |
| `archive-project-delete` | 项目删除（受控 SQL）：SQL 挑一个被辅助账 `GL_accass` 引用、没有额外栏目的项目大类，按列表取该大类一个项目的末级分类，新建 `<大类>:ZZ<时间>` → 读取 → 删除（`deleted`）→ 读取 404；再删被引用的项目 → 409 `state_mismatch`「项目已被使用…」，之后仍能读取。挑不到记 N/A |
| `archive-voucher-sign-write` | 凭证类别（新增走 `IDsign`，改名、删除走受控 SQL）：挑空闲类别字 `Z?`，新增（`type_name`）→ 读取 `ctext` → 改名 → 读取 → 删除 → 读取 404；再删预置的「转」→ 409 `state_mismatch`，之后仍能读取。挑不到记 N/A |
| `archive-currency-write` | 币种（新增走 `ICurrency`，修改、删除走受控 SQL）：挑空闲名称 `ZZE2E?` 和符号，新增（`code`、`caltype`、`precision`）→ 读取 `idec` → 改 `precision` → 读取 → 删除 → 读取 404；再删本位币 → 409，之后仍能读取 |
| `sale-red-invoice` | 红字销售发票（`sale_red_invoice`）：蓝字发货行按已开票退 1 个（`bneedbill=1`）→ 退货单未审核时生红票 409 → 审核 → 参照退货单生成红字发票（`bReturnFlag=1`、`iDisp=1`、数量 −1），退货行 `iSettleQuantity` −1、订单行 `iKPQuantity` 净减 1 → 删除后两处回到原值 → 再生成一张、复核、已复核删除 409、弃复后删除成功（红字弃复后可删，与蓝字不同），退货行回写归零，再弃审并删除退货单。可选 |
| `shape-change-cycle` | 形态转换单（`stock_other_docs`）：挑一个不管货位的仓库里两种有现存量的简单存货和一个末级部门，新建（转换前 / 转换后各 1，同组）→ 改表头和转换后行的备注 → 审核，响应 `generated` 为其他出库、其他入库，`cSource=形态转换` 的生成单 2 张 → 已审核删除 409 → 弃审，生成单 0 张 → 删除。审核走 12 参 `Verify`（`StockCo.MiscMake`）。可选 |
| `transfer-request-cycle` | 调拨申请单：两个不同的仓库和一种简单存货，新建 → 改数量并加一行（2 行、合计 3）→ 审核（不生单）→ 已审核删除 409 → 弃审 → 删除。可选 |
| `transfer-from-request` | 调拨单参照调拨申请单（`transfer_gen`）：挑单同 `transfer-request-cycle`（另要求账套没开「允许超调拨申请调拨」），新建申请单（申请 3、核准 2）→ 审核 → 生单 3 个 409 → 生单 1 个（调拨单行带 `iTRIds`，申请单累计调拨 1）→ 申请单弃审 409 → 删除调拨单（累计调拨回 0）→ 弃审、删除申请单。可选 |
| `stock-check-create-delete` | 盘点单：挑没有未审核盘点单的仓库和其中一种有现存量的简单存货，新建（只给实盘 1，账面由桥按现存量填，回读核对）→ 同一仓库再建 409 → 删除。审核（生成盘盈 / 盘亏单）不测。可选 |
| `archive-customer-bank-write` / `archive-vendor-bank-write` | 客户、供应商银行账户（`ArcPartnerBank`，受控 SQL）：挑一个还没有账户的客户（供应商，优先档案上银行账号也为空的），账号现编（`E2E` + 时间）。第一个账户自动成为默认 → 新增非默认的第二个 → 第二个设为默认后第一个变成 0 → 删除默认账户、取消默认都是 409 `state_mismatch` → 换回默认 → 两个都删掉 → 读取 404。桥回读比较该客户全部账户的全部列。挑不到记 N/A；中途失败时先删非默认、后删默认 |
| `archive-customer-contact-write` | 客户联系人（EAI `customerlinker`，U8 自动编号）：新增 `code` 写成 `<客户编码>:`（姓名、性别、手机）→ 从响应取 U8 编的 `code` → 读取 `cContactName` → 改备注 → 读取 `cMemo`、手机不变 → 删除 → 读取 404 |
| `archive-vendor-contact-read` | 供应商联系人读取：列表 200；有数据时读一条（不写） |
| `archive-vendor-contact-write` | 供应商联系人（新增走 EAI 分发器 `vendorcontact`，U8 自动编号；修改、删除是桥的受控 SQL）：挑一个没停用的供应商，新增 `code` 写成 `<供应商编码>:`（姓名、手机、非主要联系人）→ 从响应取 U8 编的 `code` → 读取 `cContactName` → 改备注 → 读取 `cMemo`、手机不变 → 删除 → 读取 404。挑不到记 N/A |
| `archive-exchange-rate-write` | 汇率（`arc_exch`；新增走 EAI 分发器 `currencyrate`，修改、删除是桥的受控 SQL）：经 API 挑一个非本位币的币种，在登录年度里找该币种没有任何汇率的未来期间（12 期往回到登录月份之后）→ 新增记账汇率 → 再新增 409 `state_mismatch` → 读取 → 修改 → 读取 → 删除 → 读取 404。没有外币或空期间记 N/A；中途失败时删掉该期间的汇率（该期间原本为空） |
| `archive-reason-write` | 原因码（`arc_reason`；新增、修改、删除都走 EAI `reason`）：T 开头的时间戳编码（9 个字符）先读确认 404 → 新增（`Reasontype` 1）→ 读取 `name`、`Reasontype` → 改名称和说明 → 读取 → 删除 → 读取 404。中途失败时删掉本用例建的原因码 |
| `dispatch-nosrc` / `sale-invoice-advance` / `arrival-nosrc` / `material-out-nosrc` | 无来源新增（`srcless`）：挑单 SQL 同时读账套选项（`SA.bMustSO_ptxs`、`PU.bPTHavePO`、`ST.ballowAddnewVouch`）。选项禁止时断言 409 `state_mismatch`、消息带选项名；放开时自动新建 → 读取 → 删除 → 读取 404（先开票另核 U8 生成的发货单指回发票、删除后不在）。回归不改账套选项；挑不到主数据记 N/A。可选 |
| `sale-return-create-free` | 无来源退货单（红字发货单，`vouchers/create` `type=sale_return`）：新建 → 读取 → 删除 → 读取 404，不受 `SA.bMustSO_ptxs` 限制；挑单同 `dispatch-nosrc`。可选 |
| `sale-return-from-so` | 退货单参照销售订单（`source_type=sale_order`，`sale_return_so`）：挑累计发货数量（`iFHQuantity`）>= 1 的已审核订单行退 1 个，核对新行红字、挂订单行、`iCorID=0`，订单行 `fretquantity` +1、`iFHQuantity` −1，删除后回到原值。挑不到记 N/A。可选 |
| `purchase-return-create-free` | 无来源采购退货单（红字到货单，`vouchers/create` `type=purchase_return`）：新建 → 读取 → 删除 → 读取 404，不受 `PU.bPTHavePO` 限制；挑单同 `arrival-nosrc`。可选 |
| `product-in-from-mo` | 产成品入库参照生产订单：挑不需检验、剩余 >= 2 的已审核订单行入 1 个，`QualifiedInQty` +1，删除后回到原值。订单行几乎都要检验的账套挑不到，记 N/A。可选 |
| `product-in-mo-inspect` | 产成品入库参照需要检验（`QcFlag=1` 且 `ST.bQuality`）的生产订单行：409「需要检验」。可选 |
| `qm-incoming-reject-cycle` / `qm-product-reject-cycle` / `qm-reject-bad-request` | 不良品处理单（`qm_reject`，QM05 / QM06）：挑造数 `qmr_*` 建的已审核检验单（合格 1、不良 1，`BREJFLAG=0`）→ 预演生单不留单 → 生单 1 行（来料拒收 `Sys01`；产品降级 `Sys03`，`cdiminvcode` 取检验单存货）→ 回读单号前缀 `LLBL` / `CPBL`、`CHECKID`、`IVTID` 355 / 356，检验单 `BREJFLAG=1` → 再生单 409 → 审核、弃审（受审批流控制时直接审核 409，跳过）→ 删除，`BREJFLAG` 回到 0；降级不给 `cdiminvcode` 预演 400。没有造数检验单、没有质量管理类不良原因（`Reason` `iReasontype=1`；造数步骤 `qm_reason` 经 `archives/create` 建 01）时 N/A |
| `purchase-in-from-qm-reject` | 采购入库参照来料不良品处理单（`purchase_in_qm_reject`，可选）：挑造数 `qmr_arrival` 建的已审核来料检验单 → 生成处理流程 2 的 QM05（`cdiminvcode` 取检验单存货）→ 审核（受审批流控制记 N/A）→ 部分数量 409「须一次入库全部」、超量 409 → 整数量生成采购入库 200：入库行 1 行、数量 = 处理后数量、`cSource=来料不良品处理单`、`iRejectIds` 挂回处理单行、处理单行 `BFLAG=1` → 再生单 409「已入库完毕」→ 删除入库单后 `BFLAG` 回到 0 → 弃审删除处理单，检验单 `BREJFLAG` 回到 0。挑不到造数检验单、不良原因、流程 2 的处理方式或仓库时记 N/A；与 `qm-incoming-reject-cycle` 同挑一张检验单，互斥 |

### 期初、结账、存货核算、核销与合并制单

期初、结账、存货核算的用例只在测试账套（桥 `testAccounts`）上真做，非测试账套记 N/A；改整个账套期间状态的用例独占运行，结束时把标志原样恢复。

| 名字 | 做什么 |
| --- | --- |
| `openings-post-dry` / `openings-post-refuse-posted` / `openings-post-test-only` / `openings-bad-module` | 采购期初记账：预演不落库、已记账 409、非测试账套 403 `test_account_only`（桥总开关未打开时 `feature_disabled`，同样通过；测试账套 N/A）、坏 `module` 400 |
| `periods-close-dry` / `periods-close-refuse-order` / `periods-bad-module` | 月末结账：预演逐月结账不改标志、跳期 409、坏 `module` 400 |
| `openings-arap-test-only` / `openings-arap-closed` / `openings-arap-cycle` | 应收应付期初单据：非测试账套 403、启用月已结账 409、期初应收单新增到删除一整轮 |
| `openings-arap-live-cycle` / `openings-arap-live-cycle-ap` | 启用月已结账时临时打开，期初应收单 / 应付单新增、审核、弃审、删除，结账标志原样恢复 |
| `stock-opening-test-only` / `stock-opening-closed` / `stock-opening-update-refused` | 库存期初结存单：非测试账套 403、库存已月结时 U8 拒绝（409 `u8_rejected`）、修改一律 400 |
| `stock-opening-live-cycle` | 库存已月结时临时清标志：新增 2 行（2 张）、审核、已审核拒删、弃审、删除，标志原样恢复 |
| `openings-ia-post-live` | 存货核算期初记账：预演计数与回滚；取消必然成功的账套真记账、核对期初汇总后取消 |
| `ia-post-dry` / `ia-period-end-dry` / `ia-bad-action` | 存货核算记账、期末处理的预演（回读不变）和坏 `action` 400 |
| `ia-post-period-end-live` / `periods-unclose-reclose` | 真实写入（排在最后）：正式记账、期末处理再取消；各模块取消结账再结账，库存快照、存货结转、结账标志复原 |
| `ia-voucher-post-cycle` | 存货核算制单真实写入（排在最后）：本月记账后入库单据预演、逐张制单，回读凭证和存货明细账、`gl/vouchers/load`、再制单 409，取消制单、恢复记账后回到用例之前 |
| `ar-writeoff-prepay` / `ap-writeoff-prepay` | 预收 / 预付行对发票行核销 1.00，余额各少 1、冲减行 `bPrePay=1`；取消后复原 |
| `ar-writeoff-fx` / `ap-writeoff-fx` | 外币收付款单对同币种、同汇率的应收单 / 应付单整单核销原币 1.00，本币按汇率、没有汇兑损益；取消后复原。账套没有外币往来科目时 N/A |
| `ar-voucher-merge` | 应收合并制单：两张收款单 `ids` 合成一张凭证，各行带各自单号、回写同一个外部业务号；单张再制单 409；一次取消制单两张都清干净 |
| `binned-sale-out-delete` / `sale-out-bin-split` / `sale-out-batch-override` | 有货位记录的销售出库删除（先清货位）；销售出库生单按货位拆行、改批号，删除后回退 |
| `product-in-merged-check` | 参照合并检验的产品检验单（`BMERGECHECKFLAG=1`）按来源生成产成品入库（`qm_merged_in`）：`vouchers/load` 带 `merge_sources` → 别的检验单的来源 400 / 409 → 只对一个来源入库 1 个，表体 `imergecheckautoid`、`iMPoIds`、`cmocode`、`iCheckIdBaks`，表头无 `cMPoCode`，来源 / 检验单 / 订单行累计数各 +1 → 审核、弃审、删除后全部回退。账套没有合并检验数据时 N/A |
| `qm04-merge-create-delete` | 产品检验单合并检验（`qm_merge`）：挑两条同存货、同辅计量单位的生产订单行，各生成一张产品报检单（1 个，未自动审核 N/A）→ 只给一条来源 400 → 两条报检行合并检验（`source_ids`）→ `BMERGECHECKFLAG=1`、合并明细 2 行指向这两条报检行、明细合计 = 表头，报检行累计检验各 1、`BFLAG=1` → `vouchers/load` 带 2 条 `merge_sources` → 再合并 409 → 删除检验单后表头、检验项目、合并明细都不在，报检行回到 0 → 删除两张报检单 |
| `qm-other-read` | 其他报检单、其他检验单只读（`qm_other_read`）：两种各取一页列表，行不带 `wf` / `wf_state` / `current_auditor`、`check_type=OTH`，其他报检单来源三列为空、其他检验单有 `inspect_id` → `keys_only` 主键同完整列表 → 翻页 → 读取首行（其他检验单的 `INSPECTID` 能读出其他报检单）→ `vouchers/search` 按单号找回。账套没有这两类单据时 N/A |
| `qm-other-cycle` | 其他报检单新增、其他检验单生单的一整轮（`qm_other_write`）：照最近一张真实的其他检验单挑存货、检验员、检验部门、检验方案，模板（VT 361 / 365）必输的自定义项照抄最近一张同类单据 → 预演新增（最大 ID 不变）→ 新增 1 行，桥按选项补审核（没做时响应带 `auto_verify_error`）→ 其他报检单弃审、审核（没审核的直接审核）→ 预演生单 → 生单（一张检验单挂在报检单行上、行 `BFLAG=1`、`IsWfControlled=0`、未审核）→ 再生单 409（万一 200，第二张也清理）→ 已有检验单的报检单弃审 409 → 审核 → 弃审 → 删除检验单（`BFLAG` 回 NULL / 0）→ 删除报检单（先弃审，`unverified=true`）→ 按两个 ID 计数归零。失败时尽力弃审、删除新单。账套没有真实的其他检验单时 N/A |
| `qm03-update-split` / `qm04-update-split` | 检验单修改（`qm_update`）：照 `qm01-create-delete` / `qm02-create-delete` 建到检验单（全部合格），预演修改（结论、数量、`ufts` 不变）→ 改成不良 1（来料）/ 让步 1（产品，另带让步接收核准人 `cYielderCode`，挑在职人员）、带原因 → 回读结论和三个数量、修改人是本操作员、报检单行累计不变 → 倒序删除。挑不到质量管理类原因码时 N/A |
| `qm03-update-verified-409` | 来料检验单提交审批后修改 409 `workflow_enabled`、单据不变，撤回后倒序删除（补偿先撤回再删） |
| `qm02-unverify` | 产品报检单单独弃审（`qm_update`）：照 `qm02-create-delete` 建产品报检单（1 个，U8 自动审核）→ 单独审核 400 → 弃审（`vouchers/verify action=unverify`，`state.verified=false`）→ 再弃审 409 → 删除，生产订单 `DeclaredQty` 复原。产品报检单不开放修改（U8 的 `VoucherOperate update` 不可用） |
| `qm11-update-nodown` / `qm11-update-verified-409` | 其他报检单修改（`qm_other_update`）：弃审 → 预演 → 改 `cDefine10` → 回读、`ufts` 变 → 审核 → 删除；已审核时修改 409、单据不变。数据同 `qm-other-cycle`，没有时 N/A |
| `qm15-update-cycle` / `qm15-update-verified-409` | 其他检验单修改：生单后预演 → 改成不良 1（不带原因：原因是让步接收原因）、结论、`cDefine10`、第一个检验项目的单项判定 → 回读、`ufts` 变、修改人 → 审核、弃审 → 删除；已审核时修改 409、单据不变，弃审后删除 |
| `purchase-settle-read` | 采购结算单只读（`pu_settle_read`）：挑最新一张有发票行的结算单 → `vouchers/load` 的 `code`、`state`（未审核、行数、已记账行数）、挑中结算行的 `in_id` / `invoice_id` 与 SQL 一致 → 按结算号列表正好一行（供应商、`line_count`、`accounted_lines`）→ `keys_only` + `changed_since` 水位减一能看到、等于水位看不到（`psufts` / `psdufts`）→ `vouchers/search` 按结算号、供应商、存货找回 → `vouchers/verify` 400。没有结算单的账套 N/A |
| `purchase-settle-cycle` | 采购结算单写入（`pu_settle_write`）：挑最新一张未结算、已采购复核、未应付审核、1 到 5 行且每行参照已审核采购入库行的发票，结算日期取 `GL_mend` 第一个采购未结账期间的最后一天 → 预演生单 200、回读没有结算行 → `vouchers/generate`（`head.settle_date`）200，回读正好一张结算单、每行（`iRdsID`、`iBsID`、`iSVQuantity`）与发票行一致、发票表头表体 `dSDate` 是结算日期、应付审核人仍空 → 再生单 409 → `vouchers/delete`（`date` 同结算日期）200，回读结算单不在、发票 `dSDate` 清空。清理在生单之前登记（结束时还有结算单就删）。没有可结算发票的账套 N/A |
| `ia-adjust-read` / `list-inv-price-adjust` / `report-fa-changes` / `report-fa-depreciation` | 只读（`ia_sa_fa`）：出入库调整单列表、`keys_only`、读取（`state.posted` 与列表的 `verified` 一致）、按单号搜索；存货调价单列表（空表也要能跑通）；固定资产变动单按卡片、单号过滤；折旧行的期间都在 `posted_periods` 里，首张卡片在 `fa_card` 的 `depr_period` 上的累计折旧与 `fa_card` 一致。两张固定资产报表各跑两次：缺省（登录年度）和 `fiscal_year` = 登录年度 − 1（`fiscal_year` 是固定资产的业务年度，不是账套库年度）；变动单另核对 `change_date` 在该年度内，折旧只在 `fa_card.fiscal_year` 等于所查年度时与 `fa_card` 比对。两个年度都没有数据才记 N/A（存货调价单空表记 PASS） |
| `binned-qm-purchase-in-delete` | 参照来料检验单生成采购入库进货位仓、行带末级货位：表体 `cPosition`、检验单累计入库数 +1，保存 / 审核后的 `InvPosition` 与货位存量，弃审删除后全部回退。挑不到检验单或货位仓记 N/A |
| `position-adjust-cycle` | 货位调整单（`position_adjust`）：货位管理仓库里挑一个简单存货（结存 >= 1 的末级调出货位、同仓库另一个末级调入货位）→ 调出、调入相同 400 `lines.0.caposcode`（不保存）→ 新建调整 1 → 保存后货位结存不变、没有货位台账 → 修改（改表头备注和行备注；同一请求删旧行、加新行（新行有行号、单号等于表头单号）；调出货位结存 ≥ 2 时已有行数量改 2 再改回 1；改成调出调入相同 400 `lines.0.caposcode`）后行数、备注与请求一致、结存不变、没有台账 → 审核（响应 `ledger_rows=2`、两项 `bin_moves`，`vouchers/load` 的 `positions` 2 行）→ 台账一出一入、调出 −1、调入 +1 → 已审核的单据修改 409 `state_mismatch` → 弃审 → 台账没了、结存回到原值 → 删除。写入是第一级，403 `test_account_only` 记 FAIL；挑不到数据记 N/A |
| `purchase-in-gen-position-bad` | 采购入库生单的货位校验（参照来料检验单、采购订单）：货位仓不带货位、货位不存在、普通仓带货位 400 `lines.0.cposition`，仓库不存在 400 `head.cwhcode`，都不调用 U8；意外保存就删掉并记 FAIL |
| `purchase-in-shelf-made` | 保质期存货的无来源采购入库（`made_inv`）：按挑到的外购简单存货复制一个新存货，SQL 打开批次、保质期管理（30 天），再走 `purchase-in-shelf` 同一套入库和回读，记下 U8 补的有效期至、`cExpirationdate` → 删单 → 删掉该存货结存为 0 的现存量记录 → 删存货（删不掉只记说明，留给快照还原） |
| `mo-allocate-fixed-scrap` / `mo-allocate-aux` | 生产订单子件（`mo_alloc`）：复制新母件、新建并审核物料清单（固定用量 + 损耗率；或自建固定换算组和挂它的子件、换算率 6 位小数），建生产订单后改数量 1 → 3：子件静态列不变、数量和件数按 U8 算法（`MoUpdateAlloc`）重算；辅计量子件按桥的 `CheckAux`：主用量等于辅用量 × 换算率（或其按数量小数位舍入的值，U8 展开时这样存）且主、辅两条算法的新数量相同时按算法重算，否则改数量应 409（「辅计量用量与主用量不一致」或「按主计量和辅计量算出的新数量不同」）且子件不变；新建时的展开结果只记录（SQL 输出留在报告目录）→ 删订单、弃审删物料清单、删自建档案。这三条是观察型用例（`CaseSpec.observe`），PASS 时报告说明带上实测值 |
| `mo-generate-ato` / `mo-generate-bad` | ATO 生产订单参照销售订单生成（`mo_gen_ato`、`mo_gen_bad`，用造数步骤 `ato` 的固定数据，测试数据没有 `ato.*` 时 N/A）。ato：两行（不选配 2 个、选配 1 个）预演 200 `validate`、最大 MoId 不变 → 生成：只新增一张订单，响应 `lines` 2、`details` 按请求顺序（`source_line_id`、`cust_bom` false / true）、`allocates` 等于子件行数；订单行存货是普通 ATO 件 / ATO 模型，`OrdFlag` 1、`OrderType` 1、`OrderDId` 是销售订单行、`CustBomId` 0 / `ato.cbom_id`，需求跟踪键按 `Usp_MO_GenMoFromSo`（选配行是这一行销售订单，不选配行照需求跟踪方式）；子件按 U8 的展开：不选配行按普通 ATO 件的标准物料清单（第一层和虚拟件下都只取不选配的子件、虚拟件本身不进子件表），只有必选子件；选配行是选配结果里的末级子件；两行已下达数量（`iMoQuantity`）各加 2、1 → 第 1 行再生成「剩余 + 1」个：账套不许超计划下达（`mom_parameter.RlsOverPlanFlag` = 0）时 409「超过可生单数量」，允许时（U8 和桥都放行）只发预演、应 200 `validate`，回归不改这个参数；两种都核对没有新订单行、已下达数量不变 → 删除订单，记下 U8 是否退回已下达数量（观察型）。bad：缺 `mo_type`、未知字段、来源行重复、来源行不在订单上 400，别的订单里非 ATO 模型的行、未审核订单 409，最大 MoId 不变 |
| `sale-out-trim-multi` / `sale-out-trim-compensate` | 销售出库按行部分生单的两步（`sale_out_trim`）：multi 自建 3 行订单、两个仓库的发货单（A 2、B 1 / C 1）只出 A 1，第 2 步改数量、删行、整张删另一仓库的出库单，只返回保留的一张，累计出库 1 / 0 / 0，删除后回 0；compensate 在货位仓建发货单后用 SQL 给自建发货行写货位，按行出 1 时生成单有货位记录、第 2 步失败，桥撤回 → 409 `u8_rejected`「已撤回生成的销售出库单：…」，没有出库单、累计出库和货位存量回到生成前（504 记 FAIL；第 2 步没失败或第 1 步就被拒记 N/A）。挑不到数据记 N/A |
| `gl-controlled-ar-option` / `gl-controlled-ap-option` | 总账选项 `bUseYSKM` / `bUseYFKM`（`gl_controlled`）：用只受应收（应付）控制的末级科目新建转账凭证，选项关时桥 400「…受控科目，总账凭证不能使用」，开时 U8 接受 200（回读该科目行 `bvalueedit`、`coutsysname`）；恢复时作废删除新建的凭证（结果不明时按摘要 + 当天 + 该科目找回），选项按原值逐字写回（NULL 仍为 NULL）并回读核对。选项行不是一行或挑不到科目记 N/A |
| `purchase-in-red-nosrc` | 无来源红字采购入库（退库）：「普通业务必有订单」打开或关闭都新建（该选项只拦蓝字，同 U8 客户端），核对数量为负、可删除；结果里记选项状态 |

### 新单据类型与写入、总账冲销与取消记账、经营管理报表、写入策略

写入用例只在桥 `testAccounts` 里的账套真做，其他账套登录前 403、记 N/A；对应的 `*-test-only` 用例反过来核对非测试账套上的 403，账套是测试账套时记 N/A。

| 名字 | 做什么 |
| --- | --- |
| `ap-refund-cycle` / `ar-refund-cycle` | 供应商退款 AP48、客户退款 AR49（`arap_refund`）：给 `ap_master` / `ar_master` 新建 100.00 的退款单 → 回读模板 `VT_ID`、条码 `||ap48|` / `||ar49|` + 单号、表头表体金额为正、`iType=0`、`bPrePay=0`；`vouchers/load` 可读，同一 id 按付款单 / 收款单读 404 → 改摘要 → 审核（往来明细行金额为负）→ 已审核删除 409 → 弃审 → 删除 → 读取 404。账套没有应收 / 应付受控科目记 N/A |
| `refund-voucher-cycle` / `ar-refund-voucher-cycle` | 退款单制单（`refund_voucher`）：给 `ap_master` / `ar_master` 新建 100.00 的退款单并审核 → `arap/voucher` 制单 → 响应和总账都是 2 行一张凭证：往来科目红字 −100.00、结算科目 +100.00，供应商退款都在借方（类别「收」）、客户退款都在贷方（「付」），每行带本单来源（`AP`/`48` 或 `AR`/`49`、单号），往来行辅助项是本单往来单位；表头 `cPzID` 和原始往来明细全部回写 → 再制单 409 → `arap/voucher/delete` 后总账、往来明细、表头都不再引用外部业务号 → 弃审 → 删除。账套没有应收 / 应付受控科目记 N/A |
| `fa-card-cycle` / `fa-card-period-refuse` / `fa-dispose-refused` / `archive-read-equipment` / `equipment-create` | 固定资产与设备台账（`fa_eq`、`fa_eq_more`，可选）。账套没启用固定资产或设备、或当前期间已结账时记 N/A。固定资产用例的登录日期取固定资产当前期间的可写日期（`AccInformation` FA `dWritableDate`）；新卡片照抄最新一张在役单部门卡片的类别、增加方式、使用状况、折旧方法、部门（都是末级），开始使用日期取上个月 1 日，币种不传（本位币）。card-cycle：预演（最大 `sCardID` 不变）→ `archives/create fa_card`（`code` 是资产编号，响应 `code` 是卡片编号）→ 读取（资产编号一致、录入日期在当前期间）→ `archives/delete`（撤销本期新增）→ 读取 404；中途失败按卡片编号撤销。period-refuse：登录日期取本年度更早的已结账期间 → 409，卡片表不变。dispose-refused：变动单、资产减少不在 API 里（在 U8 客户端做），`vouchers/create` 的 `fa_change` / `fa_dispose`（未知单据类型）、`vouchers/load fa_change`、`archives/update fa_card` 都 400（不限错误码，带 `dry_run`、不存在的卡片编号）。archive-read-equipment：列表、读取、`keys_only`、`code_prefix`，没有设备 N/A。equipment-create：预演、新增 `ZZ` 开头的设备、读取，修改 / 删除 400；新设备留给快照还原（API 没有设备删除） |
| `list-ap-refund` / `list-ar-refund` | 退款单只读：列表行数与 SQL 一致，`vouch_type`、所属系统对，按单号过滤、读取；同一 id 按付款单 / 收款单读 404。没有这类单据记 N/A |
| `dispatch-update-add-line` / `dispatch-update-add-over-remain` / `dispatch-update-add-other-so` | 发货单修改新增行（`dispatch_add`）：挑不管货位的仓库里可用量 >= 4 的简单存货，自建 2 行销售订单（各 2 个）并审核，参照第 1 行生成发货单 2 个（不审核）。add-line：`op=add` 新增订单第 2 行 1 个 → 200、`lines=2`，`vouchers/load` 2 行，新行数量、仓库、金额对上，订单行累计发货第 1 行不变、第 2 行 +1 → 删除发货单后两行回到原值；over-remain：新增 3 个（只剩 2）→ 409 / 400，发货单仍 1 行、累计发货不变；other-so：另建一张同客户的订单，加它的行 → 400，发货单和两张订单的累计发货不变。自建自删，挑不到数据记 N/A |
| `sale-out-nosrc-update` | 来源库存的销售出库单修改（`sale_out_nosrc_update`）：账套启用了销售管理（`SA.dSaleStartDate` 非空）或 `ST.bSAcreat` 打开时记 N/A；否则在不管货位的仓库里无来源新建 1 行 2 个 → 一次修改改表头备注、数量 2 改 1、加 1 行 1 个（响应 `lines` 2，两行都不挂发货单行，来源库存、普通销售、VT 87）→ 删掉新加的行（`lines` 1）→ 换仓库 409「不能修改销售出库单的仓库」（只有一个可用仓库时略过）→ 读取 → 删除 → 读取 404。挑不到数据记 N/A |
| `sale-out-nosrc` / `dry-sale-out-nosrc` | 无来源销售出库新增（`sale_out_nosrc`，可选）：账套启用了销售管理（`SA.dSaleStartDate` 非空）或 `ST.bSAcreat` 打开时核对 409 `state_mismatch` 和表头最大主键不变；否则新建 1 行 1 个（`cSource=库存`、`cBusType=普通销售`、`VT_ID` 87、没有 `iDLsID`）→ 审核 → 弃审 → 删除 → 读取 404。预演版同一正文带 `dry_run`：允许时 200 rollback、`docs` 有新单、最大主键不变，禁止时 409。回归从不改账套选项 |
| `list-sale-return-apply` / `load-sale-return-apply` / `sale-return-apply-write-refused` | 退货申请单只读（`return_apply`，可选）：列表行带 `line_count`、数量 ≤ 0，`keys_only` 一致，按单号搜到；读取第一张，行数与 `line_count` 一致，`state.verified` / `closed` 与列表一致，单据追溯向上一跳、向下一跳（行带 `iDLsID` 时有发货单 → 退货申请单的边）；关闭不存在的申请单、把它当生单目标都在登录前 400。没有申请单记 N/A |
| `sale-return-apply-cycle` | 退货申请单写入（`return_apply_cycle`）：挑可申请、可退数量都 ≥ 2 的已审核蓝字发货行 → 新建申请 2（行数量 −2，挂发货行）→ 改成 1 → 审核 → 参照申请生成退货单 1（`irtnappid` / `crtnappcode` 指回申请，申请行 `fretqty` −1）→ 改这一行的数量、删这一行 409 → 退货单还在时弃审申请 409 → 删退货单（`fretqty` 回 0）→ 弃审 → 删申请。发货行、订单行的累计数全部回到原值。没有数据记 N/A |
| `sale-red-from-blue` | 红冲蓝字销售发票（`sale_blue_red`）：在不管货位、可用量 ≥ 4 的仓库自建订单 3（审核）→ 发货单 3（审核）→ 蓝字发票 3。蓝票未复核时红冲 409 → 复核蓝票 → 红冲 4 个 409 → 红冲 1 个：`bReturnFlag=1`、同发票类型、合计 −1、1 行且 `iSBVID` 指回蓝字行 → 再红冲 3 个（只剩 2）409 → 红票日期早于蓝票 400 → 已被红冲的蓝票弃复、删除 409 → 红票复核、弃复、删除 → 蓝票弃复、删除，再删发货单、订单。蓝票复核又弃复后删除 409 时（同 `invoice-generate`）蓝票、发货单、订单留给快照还原，仍记 PASS 并注明。桥对发货 / 订单累计数的回写只记审计，用例不断言 |
| `sale-invoice-multi-dispatch` | 一张销售发票参照多张发货单（`invoice_multi`，正文 `sources`）：在不管货位、可用量 ≥ 4 的仓库自建订单 4（审核）→ 参照生成发货单 A、B 各 2（审核）→ 拒绝：同一张发货单列两次 400、B 行开 3 个（只剩 2）409、A 的来源下列 B 的行 400，两行累计开票不变 → 预演 A 行 2 个 + B 行 1 个：`dry_run=true`，累计开票和指向两行的发票行数不变 → 正式同一正文：`source_id`=A、`source_ids`=[A, B]、`lines`=2，发票两行数量 2 / 1（B 部分开票）、`cbdlcode` 各是自己的发货单号；账套有 `SaleBillVouchs_extradefine.cbdefine1` 时带一个数值（2.15 / 0.95），库里和 `vouchers/load` 都读回同值（没有该列时不带并注明）→ A 行累计开票 +2、B 行 +1 → 删除发票、回读已删、累计开票回到原值 → 弃审删除发货单、订单。发票不复核。第一次失败先看审计「发票行核对」 |
| `dispatch-gen-line-bdefine` | 发货单参照销售订单生单的表体扩展自定义项（`dispatch_line_bdefine`）：按列目录查 `DispatchLists_extradefine` 的 `cbdefine5` / `6` / `7`（账套没有的列跳过并在说明里写明，三列都没有记 N/A），按列类型给两行各编不同的值（文本截到列宽、数值 3 / 4、日期今天 / 昨天）→ 在不管货位、可用量 ≥ 4 的仓库自建 2 行订单（各 2 个，审核）→ 拒绝：账套没有的一列 `cbdefineN`（编号最大的）400「账套没有扩展自定义项」，挂两条订单行的发货单行仍为 0 → 预演：`dry_run=true`，发货单行仍为 0，`docs` 的两条发货单行（按 `iSOsID`）各读回自己的值 → 正式（带幂等键）：`lines`=2，库里（按 `iDLsID`）和 `vouchers/load` 各行读回自己的值、没有串行 → 同一键同一正文重放：同一张发货单，发货单行仍为 2 → 删除发货单、弃审删除订单。清理前按用例自己的订单行对账（响应 504、没给 id、预演误落库的发货单也删），清理后按订单行和记下的 `iDLsID` 核对发货单行、扩展表行都没有。请求明确给出各列，不依赖 U8 从订单行抄 |
| `purchase-in-from-arrival` | 采购入库参照到货单（`pu_arrival_in`，可选）：订单 2 → 审核 → 到货 2 → 审核 → 入库 3 个 409 → 带 `lines` 入库 1：来源 `采购到货单`、`ipurarriveid` 指回到货单、1 行、到货行 `fValidInQuan` +1 → 审核 → 弃审 → 删除（回 0）→ 整单生成（数量 2、`fValidInQuan` 2）→ 删除回 0，倒序清理。「普通业务必有订单」开关两种状态都能跑 |
| `arrival-close` | 到货单关闭、打开（`arrival_close`，可选）：订单 2 → 审核 → 到货 2 → 未审核关闭 409 → 审核 → 关闭预演（`mode=rollback`，状态不变）→ 整单关闭：响应 `closed`、`closed_by` 等于表头 `ccloser`，表头 `dclosedate`、各行 `cbcloser` / `dlineclosedate` 都有值 → 再关 409 → 整单打开：全部清空，倒序清理（中途失败先打开） |
| `arrival-close-lines` | 到货单按行关闭、打开（同上，可选）：两行订单 → 审核 → 参照两行生成到货单 → 审核 → 只关第一行（`line_ids`）：响应和回读都是第一行关闭、第二行和表头未关 → 该行再关 409 → 只打开该行：全部未关闭。关闭请求失败但回读已关闭（如 504）也先整单打开再清理 |
| `purchase-settle-manual-partial` / `purchase-settle-rd-offset` / `purchase-settle-inv-offset` | 采购手工结算（`pu_settle_manual`，测试账套，可选）。partial：蓝字入库 2 → 审核 → 发票 2 → 采购复核 → 预演结算 1（200，没有结算行）→ 配对结算 1：入库行、发票行各已结算 1，没有结算日期，金额是发票的一半，入库行 `iPrice` / `iUnitCost` 不变 → 结算 2 个 409 → 删除后全部回 0。rd-offset：蓝字 +1、红字 −1 入库对冲，各已结算 ±1、没有发票行。inv-offset：蓝字、红字发票对冲（红票经 `PuInvRed`），各已结算 ±1，行和表头写结算日期，入库行已结算数不变，删除后清掉日期。入库来源随「普通业务必有订单」：开时红字链路走订单 → 到货 → 蓝字入库 → 退货单 → 红字入库。挑不到数据或结算月以前有采购未结账的月份记 N/A |
| `purchase-settle-manual-tier1` | 手工结算是第一级写入：非测试账套上用不存在的行预演手工结算，过了分级到行校验 400 / 409 为 PASS，403 `test_account_only`（第二级闸门）FAIL |
| `purchase-settle-manual-replay` / `arap-exchange-gain-replay` | 重做比对（`settle_replay`、`exgain_replay`，准备步骤 `replay_prep`；可选，账套须在桥的 `testAccounts` 里；不补偿，准备和重做的改动都留给快照还原）。准备只用本服务的接口：`periods/close` 按 U8 倒序取消结账（同期先取消依赖它的模块，总账最先），已记账的凭证先取消总账结账再逐次 `gl/vouchers/unpost`，`unsign`、`unverify` 后 `arap/voucher/delete`，已应付审核的采购发票 `vouchers/verify` `arap_unverify`；任一步被拒记 N/A。settle：配对、红蓝入库对冲、红蓝发票对冲、多张发票四种形态各挑一张 U8 做的结算单（未经存货核算、发票本位币且没有核销等处理、此前各月采购已结；要准备得少的优先，每种最多试 20 张：发票带的凭证在总账里读不到、对应多张或已作废的只读预查后跳过，准备或删除被拒的也换下一张，都不能用时按原因列张数），准备后快照，`vouchers/delete`，按同一结算日期和行重做，表头、结算行、入库行已结算数量与单价、发票结算日期都与原单一致；四种都挑不到记 N/A。exgain：本年度最近一次应收汇兑损益（单一外币、单据类型都是桥做的），之后还有核销等处理的客户、该币种在登记日期之后还有往来的客户（桥按当前余额算）跳过；准备（取消应收与总账结账、取消原凭证）后快照，按处理号取消，用同一币种、客户、登记日期和缺省调整汇率重做，9M 行和单据余额一致；再以同一损益科目、凭证类别、制单日期制单，分录按科目、客户合计一致。只做应收。汇兑损益取消仍只限测试账套，账套不在 `testAccounts` 里时取消 403 记 N/A |
| `gl-voucher-reverse-cycle` / `gl-voucher-reverse-bad-body` | 总账红字冲销（`gl_reverse`）：同 `gl-post` 新建、审核、记账一张转账凭证 → 预演冲销（validate）→ 冲销：响应带 `reversal_of` 和非空 `out_no`，红字凭证日期同原凭证、未审核未记账、行数和科目相同、借贷取负、摘要以 `[冲销yyyy.MM.dd 类别-NNNN号凭证]` 开头、`blue_out_no` 等于原凭证的外部号 → 再冲销原凭证 409 `state_mismatch`，冲销红字凭证 409（未记账）→ 红字凭证作废、删除、读取 404。已记账的原凭证留给快照还原。N/A 条件同 `gl-post`。bad-body：不带 `sign` / `no` 登录前 400 |
| `gl-post-unpost-cycle` | 总账取消记账（`gl_unpost`，测试账套）：先不带核对字段试一次取消记账，403 `test_account_only` / `feature_disabled` 记 N/A、什么都不建；否则在第一个未结账期间新建转账凭证 → 审核 → 对科目余额表、辅助余额表、多辅助余额表（本期到 12 期）取校验和 → 记账 → 预演取消记账（仍是已记账）→ 取消记账（带 `period` 和 `vouchers` 核对）200 `count=1` → 凭证行 `ibook=0`、`cbook` 为空，三张余额表与记账前相同，本年度 `GL_mpostcond` 已清 → 再取消一次 409 `state_mismatch` → 弃审、作废、删除。记账后、取消前失败时凭证留给快照还原 |
| `report-mgmt-pnl` / `report-mgmt-meta` / `report-mgmt-cash-stock` | 经营管理报表（`mgmt_gl`，只读，可选）。pnl：1 月到登录月，`prefix4` 含与不含未记账分别与对照 SQL 一致；`leaf` + `dims` [dept, item] 按科目前 4 位汇总等于 `prefix4`；`gl_closed` 与 `close_status` 一致；本年没有损益发生记 N/A。meta：两次调用的模块、期间、水位完全相同，凭证张数和最大日期与 SQL 一致，总账已启用，结账标志一致。cash-stock：最后一个总账已结账期间（或登录月）的货币资金、存货结存、产成品入库、采购（发票或入库，看 `source`）与 SQL 一致，明细 + `others` 等于合计 |
| `report-mgmt-sales` / `report-mgmt-arap-terms` | 经营管理报表（`mgmt_sa`，只读，可选）。sales：收入、成本合计与 SQL 一致，明细不超过 `top`、按收入降序，明细 + `others` 等于合计，按期间分组与不分组合计相同；未启用销售管理时空明细、合计为 0 记 PASS。arap-terms：应收、应付两侧余额和未结票据与 SQL 一致；每个往来单位余额 = 各账龄段之和 − 预收（付），逾期 = 第 1 段起之和，信用期分布的张数之和等于 `invoice_count`，明细 + `others` 等于合计；两侧都未启用记 N/A |
| `report-doc-trace-qm-other` | 单据追溯（`report_trace`，只读，可选）：挑最新一张 `INSPECTAUTOID` 指向其他报检单（QM11）的其他检验单（QM15），从检验单向上一跳找到报检单、从报检单向下一跳找到检验单，边的方向是报检 → 检验；不存在的检验单 id 404 `not_found`。没有这样的一对记 N/A |
| `wp-health` / `wp-deny-unlisted-type` / `wp-freeze-account` / `wp-window` / `wp-operator-deny` / `wp-max-lines` / `wp-invalid-keeps-last` / `wp-tier2-still-test-only` | 写入策略（`write_policy`，独占）：前提是本通道桥的 `config.json` 配了 `writePolicyFile`（见「多条调试通道」）；健康检查没有 `write_policy` 或 `state` 为 `off` 记 N/A，`state` 不是 `ok` 直接 FAIL（先修好本通道的策略文件）。每条经 `wpolicy.py` 换上自己的策略（`reloadSeconds: 1`、`unlisted: deny`，本账套放行全部再加本条的限制），只发预演或登录前就被拒的写入，账套不变，用完写回原文件。health：`state=ok`、`version=1`、有 `loaded_at`，`freeze` 有 `global`、`accounts`，`window_open` 是布尔。deny-unlisted-type：只放行 `sale_order` / `create`，总账凭证新建预演 403 `write_not_allowed`、`detail` 的类型和操作是 `gl` / `create`，销售订单预演不被策略拒绝。freeze-account：冻结本账套（原因「回归冻结」），健康检查 `freeze.accounts` 含本账套，写入预演 503 `write_frozen` 且消息带原因，`vouchers/list` 照常 200。window：`denyDates` 写今天，`window_open=false`，写入预演 503 `write_window`。operator-deny：`operators.deny` 写本通道操作员，403 `operator_not_allowed`。max-lines：`maxLines: 1`，两行的预演 400 `write_limit`、`field=lines`。invalid-keeps-last：先冻结，再写带未知键的文件，等到 `state=invalid`，`version` 仍是 1、写入预演仍 503 `write_frozen`。tier2-still-test-only：策略放行全部，`periods/close` 预演仍 403 `test_account_only`（被策略的错误码拒绝记 FAIL；本账套是测试账套时记 N/A） |
| `wp-quota-minute` | 每分钟写入限额（同上）：只在可造数通道（`account.seedable`）且有 `sale_master` 时跑，否则 N/A。`writesPerMinute: 1`：先预演（遇 429 按 `retry_after_seconds` + 1 秒等，最多 65 秒再试）→ 真建一张销售订单 → 再预演 429 `write_quota`、`detail.retry_after_seconds > 0` → 写回策略后删掉这张订单（限额满时删除也会 429） |

### 权限核对

`perm-oracle-all-ops`、`perm-supervisor-all`、`perm-empty-ops`、`perm-wh-control`、`perm-snapshot-self`、`perm-eval-denied`、`perm-read-subset`：桥 `perm/evaluate`、`perm/snapshot` 的权限与账套库 SQL 独立算出的数据权限一致（`perm_oracle.py`、`casespec/perm_check*.py`），全部只读。需要评估操作员的用例在没配 `account.perm_evaluator_file` 的通道记 N/A。口径、配置和局限见「多条调试通道」的「权限核对」。

### 写预演、名称解析、幂等结果查询、结构化错误

预演用例（`casespec/dry_*.py`，步骤 `dry_op` 在 `dry_ops.py`）每步都核对 200、`dry_run`、`mode`（rollback / validate / plan）、`warnings`、`docs` 里的单据映像，再核对「什么都没变」：预演前后读取同一张单据完全相同，rollback 给出的新单据读取 404，新建 / 生单时该类型表头的行数和最大主键不变。为了预演修改、审核、关闭、删除，用例先像原用例那样真建一张，最后真删掉并回读来源累计数；中途失败时 `DRY_SUITE_CLEANUP`（`dry_cleanup.py`）先由 `dry_leaks.py` 撤回预演万一真写了漏出去的东西（预演给出的新单据、预演前最大主键之后多出来的同类单据、锁定、核销号），再尽力弃审、删除、取消核销、取消制单。预演前读不到要比较的单据时直接失败，不当成「前后相同」。

| 名字 | 做什么 |
| --- | --- |
| `dry-sale-order` / `dry-po-cycle` / `dry-purchase-requisition` | 新建、修改、审核、（销售订单）锁定与解锁、关闭、弃审、删除逐一预演，中间夹着真建、真锁定 / 解锁、真审核、真弃审、真删除 |
| `dry-dispatch-generate` / `dry-sale-invoice` / `dry-sale-return` | 销售订单生发货单、发货单审核与生销售出库、生销售发票（复核、删除）、生退货单的预演，来源累计数不变 |
| `dry-arrival` / `dry-purchase-return` / `dry-purchase-in` / `dry-purchase-invoice` | 采购链路生单及审核、弃审、复核、删除的预演 |
| `dry-other-in` / `dry-other-out` / `dry-transfer` / `dry-shape-change` / `dry-transfer-request` / `dry-stock-check` | 库存单据新建、修改、审核、弃审、删除的预演（后三条可选） |
| `dry-material-out` / `dry-product-in` / `dry-mo-close` / `dry-qm-product` | 材料出库、产成品入库生单，生产订单按行关闭（可选），产品报检单生单（rollback）与检验单生单、报检单删除（validate，可选） |
| `dry-ar-receipt` / `dry-ap-payment` | 收款单新建、修改、审核、弃审、删除，应收单、付款单、应付单新建的预演 |
| `dry-mo-open` / `dry-qm-incoming` | 生产订单行关闭、打开预演（中间真关闭、真打开，可选）；到货单生来料报检单预演（rollback）、报检单单独弃审带 `dry_run` 仍 400、删除已审核报检单只校验（validate，可选） |
| `dry-sale-out-cycle` / `dry-material-out-cycle` / `dry-product-in-cycle` | 真生单后审核、删除预演，再真删除并回读来源累计数（不真审核） |
| `dry-other-out-cycle` / `dry-sale-return-cycle` / `dry-purchase-return-cycle` | 真建 / 真生单后审核、弃审、删除预演，中间真审核、真弃审，最后真删除并回读回写 |
| `dry-gl-create` / `dry-gl-ops` | 总账凭证新建只校验（按摘要查不到凭证）；作废、审核、取消作废、删除预演，凭证前后读取相同 |
| `dry-archive-create` / `dry-wf-submit` / `dry-mo-create` / `dry-bom-create` | 存货档案新建、提交审批、生产订单新建、物料清单新建只校验（validate，`validate_only`），什么都不建 |
| `dry-ar-writeoff` / `dry-ar-writeoff-auto` / `dry-ar-voucher` | 核销、取消核销预演（余额和核销行不变，中间真核销、真取消）；自动核销 `dry_run` 的 `mode` 是 `plan`；应收单修改、审核、制单（validate，`detail.voucher`，只算计划）、弃审、删除预演，之后照常弃审、删除 |
| `dry-refuse` | 旧路由 `sale-orders/verify`、`dispatches/verify` 带 `dry_run` 400，预演带幂等键 400，读路由带 `dry_run` 400（`field=dry_run`） |
| `errors-structured` | 新建单据带未知字段、缺 `lines` 的 400 带 `field` |
| `resolve-archives` | 名称解析：编码 exact、查不到 none（同一请求两项按顺序）、名称和包含、歧义（测试数据 `resolve.ambiguous_q`，没配时从客户列表里找共有片段）、停用客户（`include_disabled`）、不支持的档案 400 `field=items.0.archive` |
| `idempotency-get` | 总账凭证带幂等键新建后按键查到同一张（`state=ok`），未用过的键 `found=false`，不支持幂等的路由 400 `field=route`；再作废删除 |
| `meta-fields-voucher` / `meta-fields-archive` / `meta-fields-gl` | 字段标签：销售订单新建、客户档案、总账凭证的字段名与 meta 可写字段一致（不多不少），每行结构、必填、枚举对，至少有标签（总账凭证全部有）；只核对结构，不写死标签文字；未知类型、档案和不支持的 `op` 400 带 `field` |
| `vouchers-search` | 单据搜索（销售订单）：客户、日期、审核条件与 `vouchers/list` 同条件第一页相同，单号包含、存货（对照 SQL）各自收窄正确，`limit 1` + `after` 翻页，没有该列的条件 400 `field=partner` / `warehouse` |
| `batch-load-sql` / `batch-load-com` / `archive-get-many` | 批量读取：逐项与单张读取相同，不存在的 id / 编码单项 `error.code=not_found`，超过上限 400（SQL 类型 21 张、COM 类型 6 张、档案 21 个） |
| `idempotency-sale-order` / `idempotency-gl-void` / `idempotency-wf-submit` / `idempotency-auto-plan` | 全部写路由的幂等键：销售订单修改、审核、删除，总账凭证作废，产品检验单提交审批（可选；账套没有这类单据或没启用审批流记 N/A）带键重放拿到第一次的结果，换内容 409 `idempotency_mismatch`，不带键重复 409；按审核、作废的键 `idempotency/get` 查到；删除重放仍 200；自动核销试算带键 400 `field=idempotency_key` |
| `ap-writeoff-auto` / `ap-voucher` | 应付一侧：应付自动核销（试算、上限 1.00 执行、取消复原，可选）；应付单修改、审核、制单、取消制单、删除的预演，夹着真制单、重复制单 409、真取消制单、弃审、删除 |

加用例的规则见「加用例」。

## 回归手册

### 一次回归做什么

编排器 `co/e2e/run_e2e.py` 对已经安装的桥服务跑全部用例（或 `--cases` 选的），顺序是：

1. **只读预检。** SSH 可达；桥服务已注册且在运行，配置和密钥在，端口在监听，读得到当天审计；测试账套的两个库（`UFDATA_<账套>_<年度>` 和 `UFMeta_<账套>`）在线且是 MULTI_USER；没有残留的快照或会话标记；测试用的 Windows 账户是这两个库的所有者、有 CREATE ANY DATABASE、不是 sysadmin、对其他 U8 库没有访问权；快照所在盘至少还有 2 GiB；桥的 `allowedAccounts` 正好只有测试账套；本机的密钥文件存在且权限是 `0600`。另外报告测试账户能不能执行 `master.dbo.u8co_release_db`（见「SQL Server」），不能时只在报告里警告，不拦。
2. **指纹。** 每个用户表的行数和 `CHECKSUM_AGG(BINARY_CHECKSUM(*))`。空表先用 `EXISTS` 判出来，不再扫表算校验，记 0 行、校验为空。
3. **S4U 检查。** 用 S4U 计划任务（不保存密码）以测试账户对测试库执行 `SELECT 1`。失败则到此为止，不写库。
4. **快照。** 为两个库的每个数据文件建稀疏快照，只给 SQL Server 服务账号快照目录的修改权限。
5. **看门狗。** 登记一次性计划任务，到点若还没有「已还原」标记，就停桥、从快照还原、再启动桥。分钟数限制在 10 到 120，缺省 25。
6. **快照时点指纹。** 作为还原是否一致的基准。
7. **部署被测的桥。** 把 `co/bridge/src/*.cs`、`build.ps1`、`u8co-bridge.exe.config` 和 `co/SafePath.ps1` 打成 zip 上传，在服务器上编译、`--selftest`、`--check-config`（读已安装的配置），然后停服务，等旧进程退出（服务报 Stopped 之后进程仍可能卡在 COM 里：`service-stop.ps1` 再等 15 秒，`service-redeploy.ps1` 再兜底 60 秒，不退就只结束本通道的桥进程——路径正好是 `<bridge.root>\bin\u8co-bridge.exe`，或读不到路径时停之前记下的服务进程号且创建时间更早——并确认已退出；结束不掉就失败，不换 exe、不启动服务，不会出现新旧两个桥进程同时在跑），把当前的 exe 和 exe.config 备份为 `.prev`，换上新文件，启动并做健康检查；失败就换回 `.prev`。不改 `config.json`、密钥和防火墙。健康检查通过后在 exe 旁边写 `<bridge.root>\bin\src.sha256`：本机算的源码哈希（上面这批文件按包内路径排序，对原样字节算 SHA-256，换行不同也算改动），以及当时 exe 和 exe.config 的 SHA-256。
   **跳过规则：** 上传之前先核对服务器（`remote/bridge-hash.ps1`，不停服务、不写文件）；`src.sha256` 里的源码哈希与本机一致、exe 和 exe.config 仍是写哈希时那一份、服务 binPath 核对通过、已安装的 exe 对已安装的配置执行 `--check-config --root <bridge.root>` 通过、健康检查应答，才跳过上传、解压、编译、自检和换程序，报告步骤里记「桥未改动，跳过编译部署」；之后照样载入服务密钥并做健康检查。哈希文件不在、读不了、格式不对或任何一项没通过都照常编译部署；换程序之前先删旧的哈希文件，部署中途失败不会留下过期的哈希。`--force-deploy`（`run_e2e` 和 `session deploy` 都有）不核对、总是编译部署。跳过时不重启桥，进程是上一次启动的那个（通常是上一次还原之后）；要重新编译并重启桥就加 `--force-deploy`。核对时的健康检查会让桥在审计日志里追加一行。
8. **跑用例。** 从本机对桥做健康检查（还要求 COM 签名自检 `signatures` 为 `ok`：桥刚启动、自检还在跑（`pending`）时每 3 秒再查，最多等 90 秒；`mismatch:<n>` 或缺这个字段都判 FAIL，细节看 `meta` 的 `features.signatures`）、错误签名得 401、名单外账套得 403（审计里没有操作员和年度）、登录，然后按用例登记表逐条执行。挑单和回读的 SQL 只打到测试库。
9. **还原。** 无论成功、失败还是中断：先等看门狗（如果它正在跑），然后停服务，对两个库 `SINGLE_USER`、`RESTORE DATABASE … FROM DATABASE_SNAPSHOT`（这一步会回到多用户，所以立刻再 `SINGLE_USER`），在同一会话里导出 `U8TaskConfig` 并取指纹，`MULTI_USER`，删除快照，写「已还原」标记并取消看门狗，确认远端没有还原还在进行，再启动服务。
10. **比对。** 还原会话里的指纹必须与快照时点的指纹一致。再取一份放开多用户之后的指纹，只作参考。

退出码 0 只在两种情况出现：试运行的预检和指纹都成功；或者完整测试的每一项都是 PASS（或 N/A），并且还原后的指纹与快照一致。缺少单据而跳过（SKIP）的用例不算通过。

报告目录里有 `report.md`、`report.json` 和每一步的日志。口令和共享密钥不写进这些文件。

**耗时。** `report.json` 的每条用例带 `duration_ms`（毫秒，并行时含在桥上排队的时间）和 `case`（用例名），`phases` 记各编排阶段的起止时刻（UTC）和毫秒数：`prep`（建目录、预检、指纹、S4U、快照、看门狗、挑单）、`deploy`（编译部署或跳过）、`cases`（跑用例和取审计）、`revert`（停服务、还原、比对、放开多用户之后的指纹）；`steps` 每一步带结束时刻 `at`。`report.md` 的测试表多一列「耗时」，另有「耗时」一节：各阶段相加的总计、各阶段、用例合计、最慢的 15 条，以及按用例族（用例名第一个 `-` 之前，如 `sale`、`po`、`ar`、`gl`、`report`、`dry`、`archive`）的条数和耗时合计。

### 测试环境要求

本机的设置都在 `~/.config/u8co/e2e.json` 和测试数据文件里（键见 [`docs/testing.md`](../../docs/testing.md) 4.2）；环境变量 `U8CO_E2E_CONFIG` 可以换成另一份配置，多条调试通道就靠它。配置里写了不认识的键（包括拼错的键）直接报错，不会悄悄用缺省值；`bridge.url` 必须形如 `http://主机:<bridge.port>/u8co`，与桥的 `listenPrefix` 规则一致。本仓库不带服务器准备脚本，下面列出要求，并给出最小的示例；执行前按你的环境替换尖括号里的占位值，并由服务器管理员审核。清单式的前提见 `docs/testing.md` 4.1，这里补充细节。

#### 测试账套

- 一个专用的测试账套：用 U8 系统管理把一份账套的备份引入为新的账套号，只用于测试。**不要在正式账套上跑回归。**
- 桥的 `allowedAccounts` 只放这个测试账套（预检会检查）。API 的账套白名单同理。
- 一个测试操作员，在测试账套里有各被测模块的功能权限（销售、采购、库存、应收应付、总账、基础档案、质量管理、生产订单），关联一个人员（审批测试要用）。凭据放在本机一个权限为 `0600` 的 JSON 文件里（`acc`、`year`、`operator`、`password`）。
- 账套里要有各种状态的单据供用例挑选：未审核的销售订单和发货单、已审核的订单、库存单据、采购订单和入库单、检验单、生产订单、应收应付单据等。用例按条件用 SQL 动态挑单，不写死单据号；挑不到就记 SKIP。空账套用 `seed` 造数（见「空账套造数」）。
- 总账用例需要当前期间未结账、有凭证类别；部分用例需要的科目、部门等固定数据放在测试数据文件里（`co/e2e/testdata.example.json`），缺了就 SKIP。材料出库、产成品入库生单的用例要 `stock.material_out_rd`、`stock.product_in_rd`（桥对这两类单据没有缺省收发类别）。
- 总账凭证用例的凭证类别字取测试数据的可选键 `gl.sign_transfer`（转账类凭证，缺省「转」）和 `gl.sign_bank`（带现金流量的银行 / 现金凭证，缺省「付」）。只设一种类别的账套（如只有「记」）两个键都写这个字；值只收 1～2 个汉字，写了不合法的值时相关用例 SKIP。凭证类别删除闸门只对 收 / 付 / 转 / 记 试，`gl.sign_transfer` 是别的字时跳过这一步。
- 退货、退货申请、不良品处理等用例另要：已出库的蓝字发货行（销售退货）、有合格数量的蓝字到货行（采购退货）、已审产品不良品处理单（可选）、有效的物料清单；请购单用例的部门和存货放在测试数据 `purchase_requisition.*`。「普通业务必有订单」用例只在该采购选项打开时跑，关闭记 N/A；回归不改账套选项。

#### SQL Server

- 支持数据库快照的版本：Enterprise、Developer，或 2016 SP1 以上的 Standard。
- 快照放在服务器本机盘上，所在盘至少 2 GiB 空闲（稀疏文件，实际占用随写入增长）。
- 服务器上装 `sqlcmd`（ODBC 驱动自带的那份）。
- 测试账户（`server.windows_principal`，Windows 身份）：
  - 是两个测试库的所有者，
  - 有 `CREATE ANY DATABASE`（建快照要用），
  - **不是** sysadmin，
  - 对其他 U8 库（`UFDATA_*`、`UFMeta_*`、`UFSystem`、`UFModel`）没有访问权。预检会逐个检查 `HAS_DBACCESS`。
- 推荐：每台 SQL 实例由 sysadmin 跑一次 `scripts/lane/install-release-proc.sql`（`LaneLogins` 写全部通道的测试账户，逗号分隔；加了通道就写全再跑一次）。它在 `master` 里装签名过程 `dbo.u8co_release_db`：测试账户可以用它结束占住**自己拥有的** `UFDATA_` / `UFMeta_` 库的会话，此外什么权限都没有。原因见「已知的还原差异」的「单用户被别人占住」；做法和撤销见 [`scripts/lane/README.md`](../../scripts/lane/README.md)。没装也能跑，只是还原时可能要等。

示例（sqlcmd，以 sysadmin 身份执行一次）：

```sql
:setvar PRINCIPAL "<服务器名>\u8co-ops"
:setvar DATA_DB   "UFDATA_<账套>_<年度>"
:setvar META_DB   "UFMeta_<账套>"

USE [master];
IF SUSER_ID(N'$(PRINCIPAL)') IS NULL
    CREATE LOGIN [$(PRINCIPAL)] FROM WINDOWS;
GRANT CREATE ANY DATABASE TO [$(PRINCIPAL)];
ALTER AUTHORIZATION ON DATABASE::[$(DATA_DB)] TO [$(PRINCIPAL)];
ALTER AUTHORIZATION ON DATABASE::[$(META_DB)] TO [$(PRINCIPAL)];
```

改库的所有者之前，先记下原来的所有者，测试结束后如需恢复再改回去。不要把这个登录加进任何服务器角色，也不要在其他 U8 库里为它建用户。

#### Windows

- OpenSSH Server，测试账户（`server.ssh_user`）只允许密钥登录。本机的私钥和 `known_hosts` 放在 `ssh.key`、`ssh.known_hosts` 指定的位置。
- PowerShell 7（`server.pwsh`）。
- 测试账户要能停启桥服务、登记最高权限的计划任务，并有「作为批处理作业登录」的权限（S4U 任务要用）。
- 桥已经安装、在运行，版本不限（回归会换上当前源码编译的版本）。回归只替换 `<bridge.root>\bin\` 下的四个文件（exe、exe.config 和它们的 `.prev`），拒绝重解析点。
- 远程工作目录（`server.remote_root`）由回归自己建并锁权限：断开继承，只给 SYSTEM、Administrators（完全控制）和计划任务账户（`server.windows_principal`，没写时是 SSH 账户；修改），所有者设为 Administrators。计划任务账户能改这里的脚本，而 SSH 账户会以管理员身份运行它们，所以它必须是你信任的账户。
  - 它的每一级已有上级目录都不能是联接点，所有者须是 SYSTEM、Administrators、TrustedInstaller 或 SSH 账户，也不能给别的账户删除、改名、改权限或取得所有权的权限（在盘符根目录下或 `C:\ProgramData` 下新建目录即可）。缺的上级目录由回归新建并只给 SYSTEM 和 Administrators。
  - 已有的远程工作目录所有者须是 SYSTEM、Administrators 或 SSH 账户；锁之前和锁好后都逐项检查里面没有联接点、没有别人的文件（`snap\` 里的快照文件除外），不满足就停下，请人工检查后删掉该目录再跑。锁的时候删掉名单外账户的显式授权（例如改过 `server.windows_principal` 后旧账户的修改权），锁完再核对只剩 SYSTEM、Administrators 和计划任务账户。
  - 服务器脚本读 `e2e.json` 之前先确认目录的所有者是 SYSTEM 或 Administrators、已断开继承，读完再确认除这两者外只授权给了计划任务账户，否则拒绝。
  - 服务器上留下的 `e2e.json` 缺少 `ProbeTask` 或 `SnapSuffix` 时，上传前的检查会停下并提示人工检查：确认没有会话、看门狗和快照后，删掉远程工作目录里的 `e2e.json` 再跑。
- 远程脚本经 SSH 以 `pwsh -EncodedCommand` 启动。长脚本（例如建目录、锁权限那段）的正文从 stdin 送过去，命令行只有固定的一小段引导，不超过 `cmd.exe` 的 8191 字符上限，所以 OpenSSH 的 `DefaultShell` 保持缺省（`cmd.exe`）或改成 pwsh 都可以。

OpenSSH 示例（管理员 PowerShell 7；只允许一个用户、只允许密钥、只允许回归主机的地址）：

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service sshd -StartupType Automatic
# 把回归主机的公钥写进 C:\Users\<ssh 用户>\.ssh\authorized_keys；
# 该用户是管理员时写 C:\ProgramData\ssh\administrators_authorized_keys，并只给 SYSTEM 和 Administrators 权限。
# 在 C:\ProgramData\ssh\sshd_config 里设置：
#   PasswordAuthentication no
#   PubkeyAuthentication yes
#   AllowUsers <ssh 用户>
Restart-Service sshd
New-NetFirewallRule -Name u8co-e2e-ssh -DisplayName u8co-e2e-ssh -Direction Inbound `
  -Action Allow -Protocol TCP -LocalPort 22 -RemoteAddress <回归主机 IP>
```

改 `sshd_config` 之前先备份，改完在另一个会话里确认还能用密钥登录，再关掉当前会话。

### 运行

先做试运行。它只做只读预检和指纹，不建快照、不登录 U8：

```bash
uv run --no-project --python 3.12 --with cryptography python -m co.e2e.run_e2e --dry-run
```

预检没问题后跑完整测试：

```bash
uv run --no-project --python 3.12 --with cryptography python -m co.e2e.run_e2e
uv run --no-project --python 3.12 --with cryptography python -m co.e2e.run_e2e --watchdog-min 25
uv run --no-project --python 3.12 --with cryptography python -m co.e2e.run_e2e --cases health,login
uv run --no-project --python 3.12 --with cryptography python -m co.e2e.run_e2e --serial
uv run --no-project --python 3.12 --with cryptography python -m co.e2e.run_e2e --changed-since origin/main
```

`--cases` 是逗号分隔的用例名，缺省全部。名字不在登记表里就直接退出，不建快照。用例清单见上文「用例」。

试运行会在服务器上建远程工作目录并上传脚本，以便做预检和指纹；它不建快照，也不写测试账套的业务数据。

#### 并行和串行

快照、部署、还原和指纹始终串行，只有跑用例这一段并行：

- 第 0 波串行：`health`、`bad-signature`、`account-foreign`、`login`（登记表里登录及其之前的用例）。登录写进的结果后面都要用，也让桥的授权缓存先热起来，只读路由才走读池。
- 其余用例按登记顺序贪心开跑：最多 `--jobs` 条同时跑，资源标签（`co/e2e/casespec/tags.py`，单据、编号、库存、审批流等）与正在跑的用例相交就先等；独占用例（如 `bridge-concurrency`）跑的时候别的都不跑。还没有标签规则时一律当冲突，等于串行。
- 每条用例用自己的一份挑单结果和中间状态，SQL 各走各的通道（同一条 SSH 主连接上的独立 SQL 工人），报告按登记顺序写，与串行跑时同序。
- 报告目录里的 SQL 文件名是「本次运行序号-用例名-本用例第几条-随机数」（例如 `0007-sale-verify-q2-1a2b3c4d.sql`，挑单时用例名是 `pick`），按名字排序就是执行顺序；串行也用这个名字。SQL 工人退出、退回 `session-sql.ps1` 时，服务器 out 下的同名文件用完即删，每条查询多一次 SSH 往返。
- 某条用例 FAIL 不影响别的；某条用例抛异常（代码出错）时不再开新用例，等其余正在跑的跑完再中止，每条异常各记一条失败。
- Ctrl-C 或 SIGTERM 后置位中断标志：不再开新用例，正在跑的用例在下一步之前停（这条记 SKIP「收到中断」），桥客户端和 SQL 不再发新请求；最多等 4 分钟让已发出的那一次调用结束（桥上已开始的调用取消不了），再按一次中断把等待缩到 30 秒，不会丢下线程不管。之后照常停服务、还原。清理开始前这个标志一定置位，所以即使有线程没在时限内退出，它也发不出桥调用，不会在还原之后写进测试账套；没退出的用例名记在失败原因里。

`--jobs N` 缺省 1（串行），范围 1 到 8，建议不超过桥的写工人数（`staWorkers` 缺省 4）。**并行是试验性的**：全量并行时偶发几个写工人同时卡在 U8 组件调用里（产成品入库审核、采购入库与到货单生单、人员档案等互相占着数据库锁，SQL 报「查询超时已过期」），桥的看门狗停掉服务后其余用例全部失败；还原仍然正确。并发的 EAI 档案写入已由桥对全部档案写入加全局串行键解决，单据与档案组件之间的情形原因待查。完整回归不用 `--jobs`；并行只用于开发时快速试跑，出现 503 stopping / 「请求已在执行」时改用串行复跑。桥 `/health` 报的 `workers` 更小时自动降到它。桥配置改大了 `staWorkers` 才值得调高，超过写工人数的请求只会在桥上排队、等到 75 秒超时。并行时同一时刻最多有 `--jobs` 个 U8 登录，许可点数也按这个数占用，业务时间护栏照旧。桥缺省开启全局写闸门（`serializeWrites`，见 [architecture.md](../../docs/architecture.md)「写闸门」）：所有写入在桥上一次一笔，`--jobs` 只让读取、列表、报表和用例里的 SQL 核对重叠，写入多的用例并行也快不了多少；`DEFAULT_JOBS` 仍为 1。

`--serial`（或 `--jobs 1`）一条接一条跑，共用一份挑单结果和一条 SQL 通道。怀疑用例互相干扰、要二分定位时用它；并行跑出的偶发失败先用 `--serial` 复跑同一批 `--cases` 确认。

`--changed-since REF` 只跑与改动相关的用例，见下文「按改动挑用例」。它只是开发自测的过滤器，**不能代替完整回归**。

`--in-session` 在本通道已用 `session open` 打开的会话里跑用例，省掉准备和还原，见「会话内跑用例」。只用于开发自测。

#### 业务时间护栏

回归要反复登录 U8，许可按工作站和模块计点，白天跑会和同事争用许可点数（点数满了，同事登录时会看到「加密点数已饱和」）。在 e2e 配置里写上业务时间，回归（全量或 `--cases` 部分）和 `session open` 在这个时间段内拒绝启动：

```json
"business_hours": {"days": "1-6", "start": "08:00", "end": "17:30"}
```

`days` 是 ISO 星期（1 星期一到 7 星期日），写成 `1-5`、`1,3,5` 或 `1-3,6`；`start`、`end` 是 `HH:MM`，含开始、不含结束，不支持跨午夜；`tz` 缺省取 `server.timezone`。不认识的键、格式不对都算配置错误。这个键必须写：缺了算配置错误，写 `null` 才表示不设护栏；样例里是星期一到星期六 08:00–17:30。回归或会话的看门狗时长会进入业务时间时也拒绝（可以缩短 `--watchdog-min`）。确实要白天跑就加 `--allow-daytime`（`run_e2e` 和 `session open` 都认）。`--dry-run` 不登录 U8，从不拦；已经打开的会话里的 `call`、`sql`、`close` 等也不拦。

#### 按改动挑用例

`--changed-since REF` 按 `git diff --name-only REF...HEAD` 的改动文件挑用例，映射表在 `co/e2e/casespec/coverage.py` 的 `PATH_RULES`（按顺序匹配，第一条命中的算数；目标是用例名前缀）：

- 总是跑 `health`、`login`。
- 桥源码按文件名前缀对到用例族：`Sale*`/`So*` → 销售，`Pu*`/`Purchase*` → 采购，`Stock*` → 库存，`Arap*` → 应收应付，`Gl*` → 总账，`Arc*` → 档案，`Mo*`/`Mfg*`/`Bom*` → 生产，`Qm*`、`Wf*` → 质检和审批流，`Reports*`、`List*`、`Meta*`、`Idem*` 各对自己的用例。
- 共享基础设施（`Dispatch.cs`、`HttpServer.cs`、`Sta*.cs`、`CoTrans.cs`、`DomRows*.cs`、`Kinds.cs`、`Config*.cs`、`Auth*.cs`、`Perm*.cs`、`VoucherRead.cs` 等）改了就全跑。
- 失败即全跑：`co/bridge/`、`co/client/`、`api/u8co_api/`、`co/e2e/`（回归框架本身）、`co/SafePath.ps1` 下表里没有具体规则的文件，一律全跑，漏登记只会多跑、不会漏跑。
- 只改了文档、`README*`、`CHANGELOG`、`events/`、`scripts/docs/`、单元测试，只跑 `health`、`login`。
- 同时给 `--cases` 时取交集，`health`、`login` 照跑。

新加用例后 `co/e2e/tests/test_coverage.py` 会检查它能被某条具体规则选中，新加桥源码文件要在 `coverage.py` 里登记到对应的族。

### 调试会话

会话做和回归一样的预检、指纹、S4U、快照和看门狗，然后停在那里，方便手工调用、试 SQL 或跑探针。打开时缺省看门狗 120 分钟（10 到 120），之后可用 `extend` 延长，会话从打开起最长 240 分钟。

```bash
E2E="uv run --no-project --python 3.12 --with cryptography python -m co.e2e.session"
$E2E open
$E2E status
$E2E extend --minutes 60
$E2E deploy
$E2E call health
$E2E call v1/login-check
$E2E call v1/vouchers/load --json '{"type":"sale_order","id":1000000003}'
$E2E sql --query "SELECT TOP 1 ID FROM SO_SOMain"
$E2E audit
$E2E reset
$E2E close
```

- `open` 在本机和服务器各写一个会话标记，所有者为 `session`；回归在建快照前写同样的标记，所有者为 `run`。任一标记还在，或还有快照、看门狗，`open` 和回归都会拒绝。标记里没有口令。
- `deploy` 按回归第 7 步换上当前源码编译的桥；服务器上的桥已是这份源码时同样跳过（打印「deploy ok（桥未改动，跳过编译部署）」），这时不重新编译、也不重启桥；要强制重新编译、换程序并重启桥用 `deploy --force-deploy`。换之前确认服务 binPath 的程序正是 `<bridge.root>\bin\u8co-bridge.exe`、并带 `--root "<bridge.root>"`（旧版本登记的服务没有，换上新版会找不到配置），否则拒绝，先按 [`docs/getting-started.md`](../../docs/getting-started.md) 第 2 节重跑 `install.ps1`。预检、停服务、启动服务（包括看门狗）都做同样的核对：服务名配错时不会去停、启别的通道或正式环境的桥。换完的健康检查还要看到本通道运行目录的审计日志多出一行，否则说明端口上应答的是别的桥。
- `call` 用测试凭据补上缺的 `acc`、`year`、`operator`、`date`（今天）。口令只进 `password_enc`。请求和响应擦掉密钥后写进会话目录。
- `sql` 只能打到测试账套的两个库。快照窗口里允许写，用来造测试数据。
- `sql`、`call`、`deploy` 执行前都在服务器上确认：两个快照都在、没有「已还原」标记、看门狗至少还有 5 分钟。看门狗已经触发就拒绝，避免把还原之后的写入留在测试账套里。
- 同一个会话里可以并行跑多条 `call`、`sql`、`audit`、`status`（例如按功能拆开的几个测试脚本同时跑）：每条命令用自己的 SSH 主连接，本机和服务器上的查询、结果、状态和审计副本都带本次调用的标记（进程号加随机数），互不覆盖，服务器上的副本用完即删。`open`、`deploy`、`close`、`reset`、`extend`、`comprobe build` 要独占：本机在 `<report.dir>/session.lock` 上加锁，它们和 `call`、`sql`、`audit`、`comprobe run` 互斥，谁后到谁直接报错退出（`status` 不加锁）。这把锁只管同一台机器上的命令。
- `comprobe run` 的脚本含写步骤时，按看门狗剩余时间算一个写入预算（到点前 3 分钟为止，最多 900 秒），`comprobe.exe` 在每个写步骤前核对，超出就停下不再写。改过探针源码或升级后先 `comprobe build`。探针的用法见 [`co/probe/README.md`](../probe/README.md)。
- 升级本机代码之前，最好先 close 所有打开的会话：服务器脚本只在 open 和回归开始时上传。仍用旧脚本打开的会话，`status`、`call`、`audit`、`close` 会退回不带标记的旧文件名继续工作并打印警告，这时并行命令可能互相覆盖文件；close 之后再 open 就换成新脚本。
- `extend [--minutes N]` 把看门狗的到点时刻往后推 N 分钟（缺省 60，每次 10 到 120），会话不关，一段连续的调试不必 close 再 open。只处理所有者为 `session`、两个快照都在、没有「已还原」标记、看门狗在且没触发、至少还剩 1 分钟的会话，否则拒绝，退出码 1。新的到点时刻封顶在打开后 240 分钟，封顶后能延长的不足 5 分钟就拒绝（「会话已到 240 分钟上限，请 session close」）。服务器上的 `watchdog-extend.ps1` 原地改看门狗任务的触发时刻（不删不重建，任何时刻都有看门狗），任务不在、正在还原、已触发或有 `done` 时拒绝，并按服务器会话标记里的打开时间再核对一次 240 分钟上限。成功打印 `extend ok, 看门狗到点 HH:mm（会话已开 X 分钟，最多 240）`；两边会话标记记下新的到点时刻（`deadline`）和延长次数，`status`、`sql`、`call`、`deploy` 的窗口检查读的就是看门狗任务的时间，随之生效。每次的步骤记在会话目录的 `extend-<n>/`。看门狗仍是兜底：没人延长，到点照样还原。升级前打开的会话服务器上没有这个脚本，先 close 再 open。
- `run_e2e --in-session` 在会话里跑用例，见「会话内跑用例」。它和 `close`、`reset`、`deploy` 一样独占通道锁。
- `close` 只处理所有者为 `session` 的会话：停服务、从快照还原、同会话取指纹、放开多用户、比对，然后删快照、解除看门狗、启动服务。两边都没有标记、快照和看门狗时打印 `already-closed`，退出码 0。
- `reset` 把测试账套的两个库还原到本次会话的快照，会话保持打开，约 1 分钟（close 再 open 要 4 到 7 分钟）。两轮探针之间要回到干净基线时用它；调试结束用 `close`。前提：所有者为 `session`，两个快照都在、没有 `done`，看门狗至少还有 15 分钟（不足时直接 close）；和 `close` 一样独占通道锁。它停服务、`snapshot-revert.ps1 -KeepSnapshot` 还原（不删快照）、用还原会话内的指纹与 `fingerprint-snapshot.json` 比对、启动服务，打印 `reset ok <秒数>`；有差异或任何一步失败退出码 1，会话仍然打开；服务没停下就不还原；还原期间看门狗到点（快照不在或有 `done`）时提示改用 `close`。看门狗不取消，到点时间不变；不读审计、登录数和 MsgCenter。步骤日志在会话目录的 `reset-<n>/`，每次结果追加到 `resets.json`，close 的报告会提到。`close` 照常做完整的还原、删快照和核对。服务器脚本是旧版（不认 `-KeepSnapshot`）时拒绝，先 close 再 open。

#### 会话内跑用例

开发时反复跑同一批用例，不必每次都快照、还原：先 `session open`，再在会话里跑。

```bash
RUN="uv run --no-project --python 3.12 --with cryptography python -m co.e2e.run_e2e"
$E2E open --watchdog-min 120
$RUN --in-session --cases health,login,sale-create
$RUN --in-session --changed-since origin/main --estimate-min 15
$E2E reset    # 需要干净基准时
$E2E close    # 收工
```

- 不做准备（建目录、上传脚本）、预检、指纹、MsgCenter 核对、快照、看门狗和还原：会话已经持有两个快照和看门狗，还原由 `session close`（或 `reset`）负责。服务器脚本也不重新上传，用的是 open 时那一份。
- 桥源码有改动时照常编译部署，判定与回归第 7 步相同（`--force-deploy` 照样可用）；然后挑单、跑用例，读审计和登录数。`--cases`、`--changed-since`、`--jobs`、`--serial` 与普通回归相同。
- 开跑前核对，不过就退出码 2、不写报告：本机和服务器都有所有者为 `session` 的会话标记（回归自己的 `run` 标记不行），会话不在 `close --keep` 中途，两个快照都在、没有 `done`，看门狗在且剩余时间至少是「预计用时 + 5 分钟」，服务器端 `session-gate.ps1` 通过。预计用时用 `--estimate-min N` 给（缺省取 `watchdog.run_minutes`）；不够时先延长看门狗，或缩小用例范围、调小预计用时。业务时间护栏按预计用时算。
- 不能和 `--dry-run`、`--watchdog-min` 同用；`--estimate-min` 只配 `--in-session`。
- **通道锁：** 整个运行持 `<report.dir>/session.lock` 的独占锁。`close`、`reset` 也要独占，运行期间它们直接报错退出，不会在用例写到一半时还原；`deploy`、`call`、`sql`、`audit`、`comprobe` 同样拿不到锁，免得中途换桥、或别的写入混进用例的数据。反过来，这些命令在跑时 `--in-session` 也直接拒绝（退出码 2）。和别的 session 命令一样，只管同一台机器。
- 跑完再读一次会话状态：运行期间看门狗已还原（有 `done`）或快照不在，判 FAIL，用例结果不可信。部署中途失败时服务若停着、库空闲就拉起来；不做任何还原。Ctrl-C 停在下一步之前，照样写报告，会话保持打开。
- 报告在 `<report.dir>/<时间>-insession-<序号>-<随机>/`，格式与回归相同，另有：`mode` 为 `in-session`，`rollback_verified` 为 `null`，`rollback_note` 为「会话内运行，还原由 session close 负责」，`gate_evidence` 为 `false`，`session_report_dir` 指向会话目录。用例全过时结论可以是 PASS，但不证明已还原。
- 结束时打印「会话内运行结束；需要干净基准时用 session reset，收工用 session close」。
- **不能作为完整回归的结论。** `multi` 不接受 `--in-session`，子进程也不会带它；合并报告遇到 `mode` 为 `in-session` 的通道报告判 FAIL。会话内连续跑几轮，后一轮看到的是前一轮留下的数据，用例结果可能依赖顺序；要和回归一致的起点，先 `session reset`。

#### 忘了 close

看门狗到点会自己停服务、还原、再启动服务。之后：

- 标记是 `session`：执行 `session close`。快照已经不在时，它只核对指纹并清标记。
- 标记是 `run`：不要用 `session close`。等回归进程自己收尾；进程已经没了，就按报告里的人工步骤处理，确认快照和看门狗任务都没有之后，再删本机和服务器上的会话标记。

在这之前不要手工删快照。

#### 空账套造数

造数之前账套本身要先准备好（补丁、年度、日历、配置、系统启用、审批流），步骤和脚本见 [`docs/getting-started.md`](../../docs/getting-started.md) 第 7 节和 [`scripts/account/README.md`](../../scripts/account/README.md)，每一步用账套体检 `reports/account_readiness` 核对。

新建的空测试账套（只有账套参数、科目、人员类别等系统管理里复制过来的设置）可以用 `python -m co.e2e.seed` 经 API 补上回归要用的基础档案，并写出这条通道的测试数据文件：

```bash
SEED="uv run --no-project --python 3.12 --with cryptography python -m co.e2e.seed"
$E2E open
$SEED plan                  # 只读：每一步已存在、待建的对象
$SEED run                   # 按顺序执行；已存在的跳过，可以重跑
$SEED run --only customers,vendors
$SEED run --skip qc_approve,qc_in   # 跳过某几步（在 --only 之后再去掉）
$E2E close --keep           # 核对无误后把结果留作这条通道的基线
```

- 前提：这条通道的配置打开了 `account.seedable`（要求写了顶层 `lane`），并且有一个所有者为 `session` 的打开会话。否则拒绝，不连服务器。是否造数只看 `account.seedable`，不按账套号判断。和 `call`、`sql` 一样共享通道锁、执行前核对快照和看门狗。
- **可选的 `seed` 配置段。** 缺省编码是中性的测试值，不一定符合账套的编码方案和科目表。需要时在 e2e 配置顶层写 `seed` 对象逐项覆盖（`co/e2e/seed/config.py`；不认识的键拒绝，不写则全用缺省）：`seed.date`（造数日期，`YYYY-MM-DD`，须落在账套已建的年度内）、`seed.codes`（部门、客户 / 供应商 / 存货分类、收发类别、结算方式、地区分类、货位的编码）、`seed.cycle_codes`（「新增-修改-删除」用例使用的客户、供应商、部门、仓库、分类、人员、存货编码，账套里必须不存在）、`seed.gl`（`expense_account`、`payable_account`、`bank_account`、`cash_flow_item`）。样例键见 `docs/testing.md` 4.6。
- **预检。** 写入之前，`seed` 先只读核对（`co/e2e/seed/preflight.py`）：每个编码的位数是否符合账套的编码方案（`GradeDef_Base`）、`seed.gl` 的科目在造数年度的科目表里存在且是末级、现金流量项目存在且未关闭。有不合的一次列出全部，并写明该改 `seed` 的哪个键；不通过时不写入任何数据。
- 建什么（编码和名称都在 `co/e2e/seed/data.py`，全是中性的测试值）：计量单位组和单位、收发类别（一级再末级）、结算方式、采购和销售类型、地区分类、银行档案和本单位开户银行、外币美元、不良原因（原因码 `01`，所属类型 1，步骤 `qm_reason`）、客户 / 供应商 / 存货分类、部门、两个普通仓和一个货位仓（`bWhPos` 用 SQL 打开）及其货位、业务人员、客户（含一个带税号的、一个留着不用的）、供应商、存货。
- 审批人：在服务器上用 XQuery 把启用的审批流（`Table_WorkFlowRelease.Status=0`）的 XML 拆成每个审批人一行，取出其中的人员编码（`$<StartUser>` 这类占位忽略），缺的建成人员；e2e 操作员在 `UserHrPersonContro` 里对到专用人员（编码见 `seed/data.py` 的 `OPERATOR_PERSON`，不是任何审批流的审批人，回归 `wf-qm04-cycle` 要先看到提交后操作员不是当前审批人；已对到别的人员时用 SQL 改过来）。造数自己走审批时逐节点把待办改派给这个人员再同意。
- 存货：新增必须带模板，空账套的第一条模板存货用 `co/e2e/seed/sql/inventory_template.sql` 插入（列按现场账套调整），其余按模板复制，再用 SQL 按用途改标志（BOM 母件、免检 BOM 母件、子件、来料检验、批次、普通件）。
- 档案之后写 `testdata.file`：造数的键合并进已有文件，别的键保留；总账的银行科目 `gl.bank_account` 在造数时按 SQL 取本通道账套科目表里 `1002` 下的第一个末级科目（本位币优先），取不到时保留文件里已有的值，再没有才用中性占位值（配了 `seed.gl.bank_account` 就固定用配置的）；已有文件先留一份 `.bak`，重跑不覆盖。缺省路径 `~/.config/u8co/testdata.json`（不带 `lane` 的配置共用的文件）和路径里没有通道名或账套号的文件拒绝写。新增-修改-删除用例的编码写之前逐类核对账套里还没有。跑完会提醒：不带 `--keep` 就 close 时，要把 `.bak` 恢复回去（原来没有文件就删掉）。
- 每一步的请求、响应、SQL 和输出写进会话目录的 `seed-calls/`，进度在 `seed-state.json`。失败时停在那一步，打印桥的错误码、原文、字段、提示和调用记录的路径。
- 单据（`co/e2e/seed/steps_docs_*.py`，排在测试数据文件之后）：两个母件（一个打开质检、一个免检）的标准 BOM（子件行带固定的普通仓，新增并审核）；其他入库（每个存货至少 10 个，批次件带批号）、其他出库、仓库间调拨；固定测试客户的两张销售订单（一张不审核，一张审核后参照发 2 个，再生成销售出库和 1 个的专用发票）；采购单据之前先做采购期初记账（`pu_opening`：按 SQL 看采购管理启用年度 `GL_mend` 第 0 期的 `bflag_PU`，未记账时经 API 调 `openings/post` `{"module":"pu","action":"post"}`，已记账或桥回 409「采购期初已记账」都算已存在，记完再回读；采购管理没启用时这一步失败），否则 U8 拒绝保存采购发票；固定测试供应商的三张采购订单（一张不审核；`po_arr` 审核后只到货 3 个；`po_in` 审核后只直接入库 2 个，再开 1 个的采购发票，不复核。U8 不许同一张订单既到货又直接入库，回归的入库挑单排除有到货的订单，到货挑单排除有直接入库的订单）；来料检验件的订单 → 到货 → 报检 → 检验 → QM03 审批（逐节点把待办改派给操作员对应的人员再同意）→ 参照检验单入库；BOM 母件的生产订单（审核），参照它领料 2 个生成材料出库（材料出库收发类别），两张不提交的产品检验单（QM04，留给 `wf-qm04-*`），再做第三张产品报检单和检验单、走完 QM04 审批后参照它入库 2 个（产成品入库收发类别）——检验单、订单、子件都留有余量给 `mo_alloc`、`qm_pin`、`qmp` 等挑单；免检母件另有一张审核后不动的生产订单（`QcFlag=0`），给 `mo_free` 和 `product-in-from-mo` 挑；客户收款单（审核并制单）和供应商付款单（审核）；另有两张给不良品处理单用例（`qm-*-reject-*`）的检验单（`qmr_*`：来料、产品各一条订单 → 报检 → 检验合格 1、不良 1 → 走完 QM03 / QM04 审批，不入库，用例按 `seed:qmr_arrival`、`seed:qmr_mo` 找回，跳过时用 `--skip` 那 9 步）。自己新建的单据在表头备注或摘要里写 `seed:<步骤名>`，生单按来源找，重跑时已有的跳过、已审核的不再审核；各源单都留有余量给回归挑单。来料检验链和 QM04 依赖账套里的检验方案、生产订单类别、报检自动审核选项，不满足时用 `--skip` 跳过那几步（产品质检链是 `mo_inspect,qm04,mo_inspect3,qm04_pass,qm04_approve,mo_pin`）。收发类别里另建了末级的材料出库、产成品入库两类（编码见 `co/e2e/seed/data.py`），写进测试数据 `stock.material_out_rd`、`stock.product_in_rd`。
- ATO 固定数据（步骤 `ato`，`co/e2e/seed/steps_docs_ato.py`，排在全部单据之后、`close_prior_months` 之前；已有的跳过，可单独 `--only ato`）：存货两个选项子件、选项类（`bCheckItem`，不自制、不采购、不委外）、ATO 模型（`bATOModel`、`bInvModel`、自制、可销售、`bProductBill`）、必选子件和普通 ATO 件（`bATOModel`、不是模型），按模板复制再用 SQL 改标志；物料清单经 API 新建并审核（选项类下两个选项 `optional`、`mutex_rule` 1；模型下必选子件和虚拟的选项类；普通 ATO 件下只有必选子件），选项类审核前核对选配标志（没有就停下、不审核，重跑时删掉重建）；固定测试客户的销售订单（表头备注 `seed:ato`）第 1 行普通 ATO 件 5 个、第 2 行 ATO 模型 3 个，先只保存；第 2 行的选配结果（选项 A）用会话 SQL 写 U8 生成生产订单时读的最少几行（`bom_cbom`、`bom_cbomcomponent`、行上 `iCusBomID`），主键经 `comprobe` 探针登录 U8、在 U8 自己的连接上执行 `sp_GetID`（表名 `bom_cbom`、`bom_cbomcomponent`，计数在 `UFSystem..UA_Identity`，会话 SQL 碰不到）取号，再用会话 SQL 写行；写之前清掉上次半途留下的选配指向（见 `steps_docs_ato_sql.py`）。要先 `python -m co.probe.comprobe build`；有了选配再审核订单——U8 不许审核没有选配的 ATO 模型件行（「ATO+模型件…未选配,不能审核」），普通 ATO 件不用选配；订单已保存未选配、已选配未审核、已审核都能重跑接上；最后把 `ato.*`（`so_id`、`line_plain`、`line_cbom`、`cbom_id`、`model`、`std`、`option`、`base`、`mo_type`、`dept`）合并进通道的测试数据文件。
- 最后一步 `close_prior_months`（`co/e2e/seed/steps_period_close.py`，排在全部单据之后）：按 SQL 看总账（`GL_mend.bflag`）到造数日期上个月为止还有没有未结账的期间，有就经 API 调 `periods/close` `{"action":"close","through":true,"fiscal_year":…,"period":<上月>}` 把各已启用模块逐月结到上月，再回读；造的单据都在本月，本月保持未结账，回归的总账记账用例才能记本月的凭证。通道账套不在桥的 `testAccounts` 里（403 `test_account_only`）、桥没开第二级写入总开关（403 `feature_disabled`），或桥拒绝结账（任何 409，例如重用的通道里之前造的单据、未记账凭证落在了已过去的月份）时打印原因、跳过，不算失败；也可 `--skip close_prior_months`。
- 不做：结算方式科目（`Ap_SStyleCode`：`close --keep` 之后跑 `scripts/account/copy-settle-accounts.sql`，`Force=1`，见 `scripts/account/README.md`）、汇率、期初余额（采购期初记账只打记账标志，见上）。
- 不 `close --keep` 就 `close`，造的数据随快照还原一起消失。

### 安全护栏

回归和会话会改测试账套，所以护栏比较多。改动编排器时不要削弱它们。

- **只碰测试账套。** 允许的数据库只有测试账套的两个库和本次的两个快照；`ALTER`、`RESTORE`、`DROP` 之前核对库名。`account.denied` 必须显式写出，测试账套出现在里面算配置错误。
- **配置一致。** 本机配置上传成服务器上的 `e2e.json`，服务器脚本核对它的远程目录就是自己所在的目录；会话标记还在，或者服务器上已有的 `e2e.json` 记下的看门狗、S4U 探测任务或快照还在时，不允许上传新的脚本和配置（改了任务名前缀或账套也拦得住）。例外是本通道自己的 S4U 探测任务（`<前缀>-s4u-probe`，只在 s4u-arm 运行期间存在）：上次运行被中断留下、且不在运行或排队时，自动注销并在日志里记「清掉上次中断留下的探测任务 …（状态 …，上次运行 …）」，再继续；服务器上记下的账套、库名、快照后缀或任务名与本机配置不同时，`status`、`close`、`sql`、`call`、`deploy` 都拒绝。
- **SQL 文本守卫。** 本机先检查每条 SQL（`co/e2e/sqlguard.py`）：拒绝别的库名、三段或四段名称、`EXEC`、`sp_executesql`、`OPENROWSET` / `OPENQUERY` / `OPENDATASOURCE`、`BACKUP`、`RESTORE`、`USE`、`ALTER` / `DROP` / `CREATE DATABASE`、`DBCC`、`xp_`、`SHUTDOWN`、`KILL`、`GRANT` / `REVOKE` / `DENY`、登录和用户的增删改、`sqlcmd` 的 `:` 和 `!!` 命令等。这是防手误的文本检查，不是 SQL 沙箱。
- **时间窗检查。** 每条写 SQL 执行前，服务器上的 SQL 工人确认快照都在、没有「已还原」标记、看门狗至少还有 5 分钟（最多沿用 30 秒内的检查结果）。
- **路径。** 远程脚本只在解析后的路径位于远程工作目录之下时才删除或覆盖文件，不用通配符，不递归删除。换程序时只写桥 `bin\` 里的 exe、exe.config 和它们的 `.prev`。不写 U8 安装目录；唯一的例外是只读地对消息中心的本地库取哈希，开始和结束各一次，变了就在报告里警告。
- **不动桥的配置。** 回归和会话不覆盖桥的 `config.json`、`secret.hex`，也不删审计日志。
- **所有者标记。** 回归不会还原所有者为 `session` 的会话，`session close` 也不会还原回归的快照。
- **结束会话只限自己的库。** `master.dbo.u8co_release_db` 只接受调用者自己拥有的 `UFDATA_` / `UFMeta_` 库；带 `@restore_snapshot` 时快照必须是该库自己的（`source_database_id`）。证书只补 `KILL` 和看会话的权限，`ALTER DATABASE`、`RESTORE` 仍用通道登录作为库所有者的权限。
- **还原失败时保留现场。** 还原失败会留下快照和看门狗，只有确认远端不忙才尝试启动服务，报告写明人工步骤。

### 已知的还原差异

- 指纹是逐表扫描，不是单一时间点。对不上时 `rollback_verified` 为 false，按报告的人工步骤处理，不要先删快照。
- `RESTORE` 回到多用户的那一瞬间，U8 的任务服务可能改写 `U8TaskConfig` 里调度任务的上次/下次执行时间等几列。只有行集合和其余列不变、新时间不倒退且晚于还原开始时，报告才把这几列单独说明；其他变化仍算还原失败。
- **U8 后台表（已容忍）。** 还原会话内的指纹在 `SINGLE_USER` 里取，但 `RESTORE` 到重新占回唯一连接之间，U8 应用服务器（任务服务、报表服务、登录记账，可能是另一台主机上的池连接）可能抢先写入（实测过的有：`U8LoginProcess`、`U8TaskLog`、`tc_ufrefresh`、`MetricProcessEvent`、`MetricValues`、`UAP_Report_StaticReport`、`UA_ToNextYear_base`、`UFTmpTable_<n>_<n>`，元数据库的 `UAP_ReportStaticRpt`）。名单固定在 `co/e2e/fingerprint_bg.py`，只在差异能归到还原之后的 U8 活动时容忍：同一张表在放开多用户之后的指纹里仍在变，或者是还原之后才出现的 `UFTmpTable_<n>_<n>`（`session reset` 不取放开多用户之后的指纹，只容忍后者）。`U8TaskConfig` 仍按上一条判别。业务表（`SO_`、`PO_`、`Rd`、`AP_`、`GL_` 等）不在名单里，差异一律失败；只要有一行不能容忍，整份差异原样列在「指纹差异」里。容忍的行带上依据，列在 `report.md` 的「U8 后台表（已容忍）」一节（`report.json` 的 `background_tolerated`），不隐藏；`session reset` 每次容忍的行和调度记账记进 `resets.json`，close 报告的 reset 说明里汇总行数；多通道合并报告按通道列出「已容忍 U8 后台表 N 行」。
- `SO_SOMain` / `SO_SODetails` 行数不变、校验变了：没有找到会改销售订单的 U8 后台任务，按还原失败处理，不容忍。出现时按报告的人工步骤保留快照排查。
- `UFSystem` 不在快照内：登录记录、锁行、主键计数和凭证导入改动的开放接口令牌行不会被还原，只留下主键断号。报告里的登录次数来自桥的审计。
- 某张表 `BINARY_CHECKSUM(*)` 报错时，前后两次相同的错误视为一致，错误文本变了则算失败。
- 停服务要等在途的请求结束（最多约 150 秒），否则 `SINGLE_USER` 可能等不到。桥这一侧有上限：`OnStop` 起 90 秒内进程一定结束（[architecture.md](../../docs/architecture.md)「超时和看门狗」）。`service-stop.ps1` 在服务报 Stopped 之后还确认本通道的桥进程已退出（15 秒不退按路径和进程号结束），残留的桥进程不会带着 U8 连接进入还原；服务本来就是 Stopped 时也会清掉上一次留下的残留进程（先给 5 秒自行退出）。
- **单用户被别人占住。** U8 应用服务器可能以自己的登录连着测试账套库（.Net SqlClient 连接池，可能在另一台主机上）。`SET SINGLE_USER` 之后，池里的连接可能抢先连上，占住唯一的连接：`RESTORE` 失败，`SET MULTI_USER` 报 5064 或 1205，库停在 `SINGLE_USER`。e2e 登录是库的所有者但不是 sysadmin，自己不能 `KILL`。装了 `master.dbo.u8co_release_db`（见「SQL Server」）时，`snapshot-revert.ps1`（看门狗也用它）在每个 `SET SINGLE_USER`、`RESTORE` 之前，以及库还不是 MULTI_USER 时的 `SET MULTI_USER` 之前，在同一批、同一连接里调用它结束占用该库的其他会话（含跨库持有该库库级锁的会话，例如 U8 应用服务器的任务服务从 `UFSystem` 周期性重连轮询各账套库），间隔最短（库已是 MULTI_USER 时不调用，免得杀掉 U8 和桥刚连上的池连接）；过程不在、没权限或库不归本登录所有时跳过，照旧靠重试。每次调用在还原日志里记一行「u8co_release_db <库> 结束会话 N 个：会话号 主机 程序 登录 …」（服务器上是旧版过程时没有冒号后的说明；重跑 `install-release-proc.sql` 即升级）。`RESTORE` 之后同一个 TRY 里紧接着再 `SET SINGLE_USER`；`USE` 之前不再调用它：进不去（924，别人抢先占了唯一的连接、可能已经写入）就报「未能进入 <库>（最后错误 924）」，连 `RESTORE` 一起整批重来，而不是结束占用者再进。遇到 924、1205、3101、3102、5061、5064、5070 时整批重来（`SINGLE_USER` + `RESTORE` + 同会话指纹），每 2–5 秒一次，最多约 3 分钟；放开多用户也同样重试，并且在还连着库时就放开。仍然失败时报「被另一个连接以单用户模式占住」并附上最近结束过的占用会话，快照完好：请 DBA `KILL` 占用该库的会话，再 `ALTER DATABASE … SET MULTI_USER`，然后重新还原。
  U8 应用服务器频繁轮询测试账套库时，池连接可能在 1 秒内就重连，上面的客户端重试抢不过：两步之间每次都隔着一次 `sqlcmd` 往返。新版过程（有 `@restore_snapshot` 参数；重跑 `install-release-proc.sql` 即升级）把重试搬到服务器端：每轮先结束占用会话，紧接着在**同一个动态语句**里 `SET SINGLE_USER WITH ROLLBACK IMMEDIATE` + `RESTORE … FROM DATABASE_SNAPSHOT`，`RESTORE` 之后紧接着再 `SET SINGLE_USER WITH ROLLBACK IMMEDIATE`（`RESTORE` 会回到快照当时的 MULTI_USER，中间不留空档），任何一步失败都连 `RESTORE` 一起整句重来，失败只停 0.1 秒，最多 60 轮、约 90 秒；回到批里马上 `USE` 该库占住唯一的连接（被抢先说明别人进过库、可能已经写入，就让过程连 `RESTORE` 一起重做，最多 20 轮），之后照旧导出 `U8TaskConfig`、算同会话指纹、在库里放开多用户。放开多用户（含看门狗「已无快照」时）也走过程的同样循环。过程用尽重试时先放开多用户（不把库留在被别人占着的 `SINGLE_USER`），报「u8co_release_db <库>：重试 N 次仍被占用，最后错误 <号>」，编排器把它当占用、整批重来，最终报错附上最后一次的原文。日志里每次调用一行「u8co_release_db <库> 结束会话 N 个，尝试 T 次：…」，同一主机、程序、登录只记第一个会话。还原脚本先查过程有没有 `@restore_snapshot` 参数，旧版过程或没装时仍按上面的旧批执行。

### 加用例

用例登记在 `co/e2e/casespec/`，`catalog.py` 汇总全部用例名。一条用例是一个 `CaseSpec`：

| 字段 | 说明 |
| --- | --- |
| `name` | 用例名，`--cases` 用它 |
| `title` | 报告里的说明 |
| `needs` | 依赖的挑单结果（挑单 SQL 的名字） |
| `steps` | 步骤：`call`（调桥并断言状态、错误码和字段）、`read` / `sql`（回读或执行登记过的 SQL）、`apply`（按构造器生成正文再调用） |
| `requires_login` | 需要登录 U8 |
| `optional` | 账套里可能根本没有这类单据；缺单据记 N/A，不算失败。非可选用例缺单据记 SKIP，只有通道打开 `account.sparse_samples` 时记 N/A（见「多条调试通道」） |

规则：

1. 挑单用 SQL 按条件动态选（放在 `casespec/sql_*.py` 或各用例自己的 `*_sql.py`），不写死单据号、编码或金额。账套特有的固定数据放测试数据文件，缺了就 SKIP。
2. 用例在自己的步骤里删掉新建或生成的单据，并回读确认来源的累计数（已领量、入库量、开票数、到货数等）回到原值。快照还原是兜底，不是清理手段。例外写在用例说明里：`invoice-generate`、`ar-invoice-audit`（蓝字销售发票复核再弃复后可能删不掉，留给快照还原；红字发票弃复后能删）、`gl-post`（不做取消记账，新建的凭证已记账、科目总账和辅助账的发生额留给快照还原）、`gl-voucher-reverse-cycle`（已记账的原凭证留给快照还原，红字凭证自己删）、`sale-red-from-blue`（蓝字发票复核又弃复后删不掉时同 `invoice-generate`）、`gl-post-unpost-cycle`（只在记账之后、取消记账之前失败时把凭证留给快照还原）。
3. 新的写路由至少要有一条正向用例和一条拒绝用例（门槛、已是目标状态、下游引用）；能预演的（`meta` 的 `kinds[].dry_run`、`dry_run_routes`）再加一条预演用例，断言预演前后单据和来源累计数都没有变化。
4. 先在调试会话里用探针或 `session call` 把调用跑通，再写成用例。
5. 改完跑 `co/e2e/tests` 的离线测试，确认登记表能列出新名字，再在会话里单跑这条用例。

### 多条调试通道

一条通道 = 一个测试账套 + 一个开发用桥服务 + 一份本机配置。几条通道可以在同一台 U8 服务器上同时开会话、跑回归，互不干扰。每条命令用哪条通道，由环境变量 `U8CO_E2E_CONFIG` 指定配置文件；`run_e2e`、`session`、`comprobe` 都只经 `co/e2e/settings.py` 的 `conf()` 读这一份（`comprobe` 查会话状态时起的子进程继承这个环境变量）。通道名由你起（2–12 位小写字母或数字），下文用 `main`、`lane2`、`lane3`。

#### 准备（每条通道一次）

1. **复制账套。** 由 U8 管理员在系统管理里把一份账套的备份引入为新的账套号（例如 `802`），得到新的 `UFDATA_<账套>_<年度>` 和 `UFMeta_<账套>`。不要在正式账套上开通道。
2. **Windows 登录。** 每条通道一个本机管理员账户（`server.ssh_user`，例如 `u8co-ops-802`），按「Windows」配好密钥登录，并加进 `sshd_config` 的 `AllowUsers`。不能几条通道共用一个登录：预检要求运行 e2e 的登录只能访问自己的两个库，看得到别的通道的库就拒绝。`server.windows_principal` 写了的话，也要每条通道各用一个。注意：这些账户都是本机管理员，公钥都写在共用的 `administrators_authorized_keys` 里，任何一条通道的密钥都能登录成别的通道的账户。所以通道之间的隔离是防配错、防误操作，不是安全边界。
3. **SQL 授权。** 按 `co/e2e/remote/grant-lane.sql.example` 替换占位值后，由 sysadmin 执行一次：建 Windows 登录、`CREATE ANY DATABASE`、把本通道两个库的所有者改成这个登录，最后列出它能访问的 U8 库，核对只有这两个。这一步用 `EXECUTE AS LOGIN` 模拟，看不到经 Windows 组成员身份得到的访问权；以预检（用 SSH 登录的真实令牌逐个查 `HAS_DBACCESS`）为准。
   然后由 sysadmin 重跑 `scripts/lane/install-release-proc.sql`，`LaneLogins` 写上这台实例全部通道的登录（含新通道），还原时才能结束占住本通道库的 U8 连接（见「已知的还原差异」）。预检报告里的 `u8co_release_db` 警告消失即可。
4. **开发用桥。** 每条通道装一个自己的桥服务，与正式桥和别的通道的桥分开：

   ```powershell
   .\install.ps1 -Root C:\ProgramData\U8Co\u8co-802 -ServiceName u8co-802 -Port 18090 `
     -U8Server <U8 应用服务器> -AllowedClients <回归主机 IP> -AllowedAccounts 802
   ```

   `-Root`、`-ServiceName`、`-Port` 各通道互不相同；`-AllowedAccounts` 只放本通道的账套（预检会检查）。防火墙规则与服务同名（分组 `U8Co`），只放行 `-AllowedClients` 到本通道的端口。把这个桥的 `secret.hex` 复制到本机的 `bridge.secret_file`（`0600`）。详见 [`docs/getting-started.md`](../../docs/getting-started.md) 第 2 节。
5. **U8 登录许可。** 每个在跑的桥登录 U8 都占并发站点许可；几条通道同时跑时许可数要够，其他用户也在用同一批许可。
6. **写入策略文件。** 写入策略用例（`wp-*`）要本通道桥的 `config.json` 配 `writePolicyFile`（路径在桥的运行目录之下，例如 `"writePolicyFile": "write-policy.json"`，改了重启桥），测试通道的内容用放行全部的 `{"version": 1, "unlisted": "allow"}`（格式见 [`docs/configuration.md`](../../docs/configuration.md) 第 3 节「写入策略」）。没配时 `wp-*` 记 N/A，其余用例不受影响；配了而文件不在时写回会写上这份放行的内容。`co/e2e/wpolicy.py` 经 SSH 把用例的策略写到临时文件再原子替换，等健康检查的 `write_policy.loaded_at` 变化（最多 10 秒；`loaded_at` 只精确到秒，用例的策略都写 `reloadSeconds: 1`），用完写回原文件（最多试 4 次、每次等 30 秒；文件已是原内容且桥仍是 `ok` 时不再写——内容不变桥不会重载）。写回仍失败时用例 FAIL，并把本通道标记为阻塞：本进程之后的用例全部记 SKIP 并带原因。阻塞标记只在本进程内存里，`multi` 的其他通道、之后的 `run_e2e` 和 `session` 看不到；要手工把策略文件改回去，直到健康检查的 `write_policy.state` 是 `ok`，再跑。

#### 本机配置

以 `co/e2e/lane.example.json` 为样本写一份，例如 `~/.config/u8co/lanes/802.json`。下面这些值每条通道都必须不同：

| 项 | 为什么 |
| --- | --- |
| `account.code`（及 `data_db`、`meta_db`） | 各自的库和快照 |
| `server.ssh_user` | 见上面第 2 步 |
| `server.remote_root` | 脚本、`e2e.json`、会话标记、快照文件、桥源码套件都在这里 |
| `snapshot.suffix` | 快照库名 |
| `snapshot.task_prefix` | 看门狗和 S4U 探测计划任务的名字 |
| `bridge.root`、`bridge.service_name`、`bridge.port`（及 `bridge.url`） | 部署、启停、审计日志都只认本通道的桥 |
| `report.dir` | 本机会话标记 `session.json` 放在这里 |

账套较小、部分单据类型根本没有的通道，可在 `account` 里写 `"sparse_samples": true`（要求写了顶层 `lane`）：非可选用例因必需挑单没挑到单据而跳过时记 N/A（「本通道账套没有这类单据（sparse_samples）」），这条通道的全量回归才可能判 PASS。只放过「挑单查询正常但没有结果」；挑单 SQL 出错（报告原因「pick SQL 失败」）、缺测试数据键、登录失败、前序依赖失败照旧 SKIP / FAIL。多通道编排时各通道按自己的配置判定。

另外建议：`account.denied` 列上别的通道的账套号（SQL 守卫会拒绝点名它们的语句）；`account.operator_file`、`bridge.secret_file`、`testdata.file` 用本通道自己的文件（测试数据的编码随账套不同）。

**加载时的检查（启发式）。** 加载配置时看不到别的通道，所以只查一条规则：写了顶层 `"lane"`（2–12 位小写字母或数字）时按通道检查——`server.remote_root` 和 `bridge.root` 的最后一段、`snapshot.suffix`、`snapshot.task_prefix`、`bridge.service_name` 不能是缺省值，且要含账套号或通道名；`bridge.port` 不能是 18089；`report.dir` 不能是缺省目录，最后一段要含账套号或通道名；`server.ssh_user` 不能是缺省的 `u8co-ops`。不满足就报错并逐项列出。没写 `lane` 的配置就是缺省的那一条通道，不查。检查拦得住「复制了样例只改了账套号」，拦不住两份自定义配置恰好撞名，另外预检会核对桥 `config.json` 的 `listenPrefix` 端口就是 `bridge.port`，服务 binPath 正是 `bridge.root` 下的桥。开会话前仍用 `status` 对一遍：

```bash
U8CO_E2E_CONFIG=~/.config/u8co/lanes/802.json python -m co.e2e.session status
```

`status` 先打印本通道的指纹（`lane_tag`、`lane_account`、`lane_ssh_user`、`lane_remote_root`、`lane_snapshot_suffix`、`lane_task_prefix`、`lane_bridge_root`、`lane_service_name`、`lane_bridge_port`、`lane_report_dir`），再打印服务器状态。同一台服务器上的各条通道，这些值必须两两不同。

#### 权限核对

`perm-*` 七条用例（`co/e2e/casespec/perm_check*.py`，纯计算在 `co/e2e/perm_oracle.py`）核对桥 `perm/evaluate`、`perm/snapshot` 给出的权限，与账套库 SQL 独立算出的「应有权限」一致。全部只读。

| 用例 | 做什么 |
| --- | --- |
| `perm-oracle-all-ops` | 按操作员档案（`archives/list operator`，本账套有授权、年度窗口同桥）枚举全部启用操作员，评估操作员逐个 `evaluate`：各数据权限对象「全部 / 编码」、`objects_on`、`data_admin` 与账套库一致 |
| `perm-supervisor-all` | `evaluate` 为账套主管的操作员各对象都是全部、`functions` 非空；测试操作员能否读操作员档案（只有主管能读）与它的 `supervisor` 一致。没有主管记 N/A |
| `perm-empty-ops` | 账套库里出现过（数据权限行、人员对照）而没有本账套授权的操作员：`evaluate` 得 404，或 `supervisor` false、`functions` 为空、受控对象按账套库（没有编码就是一个都看不到）；不存在的操作员 404 |
| `perm-wh-control` | 仓库数据权限打开的账套逐个操作员核对仓库编码；开关关着记 N/A |
| `perm-snapshot-self` | 测试操作员（和评估操作员）自己的 `snapshot` 与账套库一致，并与评估操作员对它的 `evaluate` 完全相同（含 `fingerprint`） |
| `perm-eval-denied` | 不在 `permEvaluateOperators` 里的测试操作员（即使是账套主管）调 `evaluate` 得 403。评估操作员就是测试操作员时记 N/A |
| `perm-read-subset` | 测试操作员（和评估操作员）每个受控对象的档案列表翻页读完，每一行都在自己 `snapshot` 的编码里；列表 403 也算不越权 |

**配置。** 在通道配置的 `account` 里写 `"perm_evaluator_file": "~/.config/u8co/lanes/802/perm-evaluator.json"`（格式和 `0600` 权限同 `operator_file`），并把这个操作员写进本通道桥 `config.json` 的 `permEvaluateOperators`。没写这个键时，需要评估操作员的用例记 N/A（`perm-snapshot-self`、`perm-read-subset` 仍只用测试操作员跑）。评估操作员最好不是测试操作员，`perm-eval-denied` 才有名单外的调用方。正式桥的 `permEvaluateOperators` 留空。

**口径与局限。** 数据权限按 U8 的规则独立再算：`AA_BusObject_base` 打开（`bAuthControl=1`、`iAuthType=0`）、读取会用到、不是账套主管、不是该对象的数据权限管理员（`AA_holdBusobject iAdmin=1`）的对象受控，科目另要总账 `bQryCtlSubj` 打开；编码取 `AA_HoldAuth` 里 `cFuncId` 含 R 的本人和所属角色的行，人员没有行时取 `hr_hi_person`，项目按「大类 + 编码」。所属角色取响应的 `roles`，没有时取操作员档案的角色（只含本账套有授权的角色），两者都有时核对档案里的角色都在响应里。功能权限（`UA_HoldAuth`）和账套主管在 `UFSystem`，回归账户按设计读不到（SQL 守卫拒绝、预检要求没有访问权），所以只核不变式：有授权的操作员 `functions` 非空、主管各对象全部、`snapshot` 与 `evaluate` 相同。

**不落编码。** 对照 SQL 不输出编码：受控对象只输出「去空格、转大写、去重」后的个数和每个编码 SHA-256 前 7 字节之和，Python 端对响应按同一规则算，两边比个数和摘要和。报告里只有操作员编码、对象名和个数，SQL 输出文件里也没有编码清单。

**字段权限用例（`col-auth-*`）。** 改权限表后不等桥的权限缓存过期，只在桥权限缓存关闭的通道上跑：该通道桥的 `config.json` 须写 `permCacheSeconds: 0`（健康检查 `perm_cache_s` 为 0；见 [`docs/configuration.md`](../../docs/configuration.md)），仅限测试桥，不要写进正式桥。用例先读 `perm_cache_s`（第 0 波健康检查已读到的复用，否则现取一次），缺字段或不是 0 记 N/A「桥权限缓存未关闭（permCacheSeconds≠0）」，不写任何东西。

#### 并行跑

每个终端（或每个测试脚本）固定一条通道：

```bash
E2E="uv run --no-project --python 3.12 --with cryptography python -m co.e2e.session"
# 终端 1：缺省配置 ~/.config/u8co/e2e.json
$E2E open
# 终端 2：通道 lane2
export U8CO_E2E_CONFIG=~/.config/u8co/lanes/802.json
$E2E open
$E2E call v1/login-check
uv run --no-project --python 3.12 --with cryptography python -m co.probe.comprobe run --script probe.json
```

两条通道之间分开的东西：测试库和快照（库名、快照后缀）；服务器上的远程工作目录（脚本、`e2e.json`、会话标记、`done` 标记、快照文件、桥源码套件、`out\` 里的文件、`comprobe` 目录）；看门狗和 S4U 计划任务；桥服务（服务名、运行目录、端口、防火墙规则、urlacl、审计日志；换程序前只等本服务的进程退出，不理会别的通道的同名 exe）；SQL 工人（每个工人自己的查询文件）；本机的会话标记、报告目录和命令锁；SSH 主连接（每个进程、每个 SSH 用户和主机一条）。

分不开的东西（U8 和服务器本身共用）：

- `UFSystem`：登录记录、锁行、主键计数由所有账套共用，也不在快照里。
- U8 并发登录许可（见上面第 5 步）。
- U8 安装目录里的消息中心本地库 `MsgCenter.db`：别的通道在跑时它可能变化，会话结束时的「MsgCenter.db 发生变化」警告可能来自别的通道。
- SQL Server 实例和快照所在的盘：每条通道的快照都占同一块盘，预检各自只查 2 GiB 空闲，几条通道同时大量写入时要留足余量。
- 同一台服务器的 CPU、内存和 SQL 锁：大批量写会拖慢别的通道。

#### 多通道分片回归

把全量用例按分片表分到几条通道同时跑，缩短整轮回归：

```bash
$RUN co.e2e.multi --lane main=~/.config/u8co/e2e.json
$RUN co.e2e.multi --lane main=~/.config/u8co/e2e.json --lane lane2=~/.config/u8co/lanes/802.json
```

- `--lane 通道名=配置文件`：通道名由你起，必须出现在分片表里；配置文件就是该通道的 `U8CO_E2E_CONFIG`。`--assign` 换分片表；`--allow-daytime`、`--watchdog-min`、`--force-deploy`、`--dry-run` 原样传给每条通道。
- 没有 `co/e2e/shards.json` 时用 `co/e2e/shards.example.json`：只有一条通道 `main`，也是缺省通道，所有用例都分给它，所以单通道可以直接 `--lane main=…`。要用别的通道名或几条通道，需要自己的分片表，先让每条通道各自跑一轮完整回归，再用各自的报告生成（见下面「维护分片表」）。
- 第 0 波（`health`、`bad-signature`、`account-foreign`、`login`）每条通道都跑；其余用例每条有一条归属通道，分片表没列的落到 `default_lane`。
- **分片表（`shards.json` 第 2 版）。** 在第 1 版（`wave0`、`lanes`、`default_lane`）之外可写 `lane_classes`：把账套选项不同的一组通道归成一类，例如 `{"full": {"lanes": ["lane3"], "everywhere": [用例名…]}}`。类里的通道是可选的：命令行没给时，归属它们的用例回到 `default_lane`；给了时，`everywhere` 里的用例除在归属通道跑一次外，在这一类到场的每条通道各再跑一次。`everywhere` 放结果取决于账套选项的关键业务（应收应付、总账凭证、票据、质检模板、带货位和批次的库存），各账套的选项不同，每个都要实测。缺省通道不能列进类里；一条通道只能属于一个类；`everywhere` 不能含第 0 波。第 1 版文件写 `lane_classes` 拒绝。起子进程之前先查：各通道配置能加载、同一台服务器上的通道指纹两两不同（主机名按解析出的地址比，别名和 IP 视为同一台；解析不了时，账套号加快照后缀、或服务名加端口相同也拒绝）、每条通道都分到用例、不在业务时间内（或带了 `--allow-daytime`）；不过就退出码 2。
- **固定用例（`pinned`、`tail`，第 2 版）。** `"pinned": {"lane2": [用例名…]}` 列固定在某条通道的用例：依赖本通道造数、要临时改账套选项或只认测试账套（`testAccounts`）的放在可造数通道，依赖某条通道的历史数据的放在那条通道；反过来核对「非测试账套登录前 403 `test_account_only`（或总开关未打开时 `feature_disabled`）」的 `*-test-only` 用例和写入策略用例（`wp-*`）放在对应的通道。`"tail": {"lane2": ["ia-post-period-end-live", "periods-unclose-reclose", "ia-voucher-post-cycle"]}` 是本通道最后跑的独占用例（存货记账月结、反结账再结账、存货核算制单），须是该通道 `lanes` 名单的最后几条、顺序一致，在登记表里也要注册在最后（实际按登记表顺序跑，不是最后几条时分线打印警告）。两者都只能写必到的通道（不能写类里的通道），用例须已列在该通道的 `lanes` 里，同一用例不能固定两处，`tail` 里的不必再列进 `pinned`；违反任一条加载即拒绝。重算分片表不动固定用例，只重分其余的。新用例要固定时把名字同时加进 `lanes` 和 `pinned`，并在用例的单元测试里断言它在 `pinned` 里（参照 `test_casespec_flip.py`）。
- **已知必然 N/A（`known_na`）。** `{"lane2": {"openings-arap-cycle": "原因…"}}` 登记已知在归属通道上必然 N/A 的用例，这些不再触发合并报告的提醒；按通道列用例名和原因，原因必填；第 1 版文件写 `known_na`、或写了分片表里没有的通道都拒绝加载；重算分片表时原样保留（登记表已没有、或现属第 0 波的用例去掉）。
- 每条通道一个 `run_e2e` 子进程（工作目录是仓库根，配置里的相对路径按仓库根解析），各自预检、快照、部署、还原。子进程的输出直接写进各通道报告目录的 `multi-<时间>-<序号>-<随机>.log`，不经编排进程，编排进程断开（终端掉线）也不影响它们跑完、还原；子进程的 pid 和日志位置在合并报告目录的 `children.json`。终端只打印启动、结束和每分钟一行进度。某条通道提前中止（许可已满、桥 stopping），另一条照常跑到自己的还原。Ctrl-C 只转发一次给各子进程，还没启动的通道不再启动；`run_e2e` 进入还原阶段后屏蔽中断，直到报告写完，所以不会打断还原。不要强行结束。
- 合并报告写在第一条 `--lane` 的报告目录下 `multi-<时间>-<序号>-<随机>/report.json` 和 `report.md`（与日志同名）：各通道一行汇总（账套、PASS/SKIP/N/A/FAIL、rollback_verified、用时），警告和失败原因，再列全部用例并注明通道，完整性在开头。各通道的报告按子进程最后打印的报告目录找；没打印时只在开跑之后该通道报告目录里恰好新出现一份报告时采用，多于一份就判这条通道没有报告。任一子进程退出码非 0 或判定不是 PASS、有 SKIP 或失败的用例、任一 `rollback_verified` 不是 true、各通道结果的并集缺登记表里的用例，结论就是 FAIL（退出码 1）。同一条用例在几条通道跑（第 0 波、`everywhere`），每条通道的结果都参与判定，任一通道失败即 FAIL；完整性只要求每条用例至少在一条通道有结果。第 0 波以外在几条通道跑过的用例另列「多通道用例」表（JSON 的 `spread`），各通道结果并排。非缺省通道上归属这条通道的用例出现 N/A 只记警告（JSON 的 `warnings`）：那条用例当初是在这条通道 PASS 才分过去的，N/A 说明数据变了，考虑重算分片表；`everywhere` 和第 0 波在别的通道多跑的那几遍出现 N/A 不提醒（`sparse_samples` 通道本来就可能没有这类单据）。
- **多通道的同机注意事项。** 同一台服务器上同时跑几条通道时，两个桥的 `cleanOrphanTasks` 都必须保持缺省的 `false`：它们是同一个工作站名，打开后会互删对方的 QM 孤儿任务行。快照所在的盘要按每条通道各 2 GiB 以上留余量（预检各自只查 2 GiB）。一条通道的还原和另一条的用例可能重叠，SQL 负载会叠加；U8 并发登录许可要够用。
- `multi` 不接受 `--in-session`（退出码 2），合并报告也不收 `mode` 为 `in-session` 的通道报告：会话内运行不能作为完整回归的结论。
- **维护分片表。** 加了用例不必改分片表（自动落到 `default_lane`）；要重新按实测结果分配时，用各通道最近一次全量报告重算：`python -m co.e2e.shard_assign recompute --report main=<report.json> --report lane2=<report.json> [--report lane3=<report.json> …] --write`。不加 `--write` 只打印差异。第一份 `--report` 的通道成为缺省通道；必到的通道都要给报告；类里的通道没给报告就不分给它归属用例。每条用例只在报告 PASS 的通道里挑归属：只在一条通道 PASS 的归那条；几条都 PASS 的按报告里的用例耗时从长到短分配，让这几条通道里预计总时长（含各自准备、部署、还原，以及类通道上的 `everywhere` 用例）最长的那条尽量短；哪条都没 PASS 的归 `default_lane`。`everywhere` 用例的归属只在类外挑。`lane_classes` 原样保留；`pinned`、`tail` 里的用例留在原通道（`tail` 写在名单最后），打印每条通道固定了几条、其中本通道报告不是 PASS 的，登记表已没有或现属第 0 波的固定用例从名单里去掉并提示。先看打印的差异、各通道预计时长和「everywhere 用例在某通道不是 PASS」的提示，再加 `--write` 写回。每份报告都必须是全量轮次（缺用例时拒绝，确要按部分结果重算加 `--allow-partial`）。全量里 PASS 的用例可能依赖前面用例留下的数据，分到别的通道后不一定还能过，重分之后先跑一轮分片回归确认，再信这份分片表。分片表里有登记表已没有的用例名（改名、删除）或第 0 波与登记表不一致时只打印警告，照常分配。分片表里只有用例名和抽象通道名，不写账套号和路径。

#### 登录复用对比

桥的 `loginReuse`（[configuration.md](../../docs/configuration.md)）先在一条测试通道上做 A/B，再考虑在正式环境打开。报告里的 `u8_logins` 按请求计数，打开复用后不会变少，不能用来对比；看桥健康检查里的 `logins_opened`（真正登录的次数）、`logins_reused`（复用的次数）和 `logins_closed`（关掉的登录数），都是进程启动后的累计值，也包括同一个桥上别的调用方的请求。

回归报告 `report.json` 的 `bridge_logins`（`report.md` 的「U8 登录」行）是用例阶段前后两次读数之差，同一个桥在这段时间里所有调用方的登录都算在内，对比期间这个桥上不要有别的调用方；前后 `login_reuse` 不一致或计数变小时判为中途重启，差值记为空并标 `restarted: true`。

1. 本通道桥的 `config.json` 保持 `serializeWrites: true`，A 轮写 `"loginReuse": false`，重启桥服务；`$E2E call health` 确认 `login_reuse` 为 `false`，记下三个计数。
2. 串行（`--jobs 1`）跑一轮全量回归，跑完再读一次健康检查：A 轮 `logins_reused` 应为 0，`logins_opened` 的增量就是这一轮的登录次数，`logins_opened` 与 `logins_closed` 相等。
3. B 轮改成 `"loginReuse": true`，重启桥，同一份造数基线、同样的参数再跑一轮；`logins_opened` 的增量应明显变少，`logins_reused` 增加。两轮的用例结果必须一致。跑完闲置 1 分钟以上再读健康检查，`logins_opened` 与 `logins_closed` 应相等（空闲的登录已按时关掉）。
4. B 轮在同一个桥进程里再跑一遍，核对复用的登录在多轮之间不串状态。
5. 任一轮出现 503 `stopping`、`u8_license_full` 或看门狗重启就中止，关掉 `loginReuse` 并保留报告和审计日志。跑的过程中健康检查 `license_packs` 各产品包的 `used` 每个工作站仍只占 1 点。每轮结束、停桥之后，以及任何一次看门狗或强制结束之后，查 `UFSYSTEM..UA_TaskLog` 里本机的登记行：应当没有；有残留时在系统管理里清除，并记进报告。

#### 保留造数结果

新建的空测试账套要先造一批基础数据。做法是正常 `open` 一个会话，用 `sql`、`call` 造完数，然后 `close --keep`：不还原，把造数后的状态留作本通道的新基线。

```bash
$E2E close --keep
```

- **只在可造数通道上用。** 本通道配置要写 `"account": {"seedable": true}`（要求写了顶层 `lane`）。没写 `lane` 的配置加载就报错；上传到服务器的 `e2e.json` 带 `Seedable`，`snapshot-drop.ps1` 不是 `true` 就拒绝。
- **任何远端改动之前的核对。** 本机：开了 `seedable`；会话所有者是 `session`（回归的快照永远还原）；服务器状态和 `sql`、`call` 同一条窗口规则——两个快照都在、没有 `done`、看门狗在、没触发、至少还剩 5 分钟。停服务之后、取消看门狗之前，服务器上再跑 `session-gate.ps1` 和 `snapshot-drop.ps1 -Mode check`：快照名字和源库对、没有 `out\fp-inrevert-*.json`、两个库都是 `MULTI_USER`（否则看门狗可能已经还原过一部分）。任何一项不过都拒绝，退出码 1，快照和看门狗原样保留。旧脚本打开的会话没有 `snapshot-drop.ps1`，也在这一步失败：先不带 `--keep` close，再重新 open。不加 `--keep` 的 `close` 照旧还原；回归（`run_e2e`）总是还原。
- **顺序。** 核对通过后在本机会话标记里记下 keep，取消看门狗，再删掉两个快照（`snapshot-drop.ps1` 再核对一遍，并要求 `done` 已写、看门狗任务已不在），启动服务，最后对两个库取一份新指纹（会话目录里的 `fingerprint-keep.json`），清掉两边的会话标记。不和快照指纹比对。报告结论写「已保留（未还原）」并列出新指纹，`rollback_verified` 为 `false`。
- **取消看门狗之后失败。** 普通的 `close` 不能再还原（`done` 已写），`close --keep` 也不能重试删快照；按报告里的人工步骤处理。只差新指纹时（快照已删、看门狗已取消、本机标记记着 keep），再执行一次 `close --keep`，它只补指纹并清标记。
- **忘了 close 仍会还原。** 看门狗不变：会话没有 close 就到点，造的数照样被还原，要重新造。
- **保留之后请 DBA 做一次 `COPY_ONLY` 完整备份。** 快照已经删了，这份新基线没有别的副本。

### 只读路由性能

`python -m co.e2e.perf_reads` 按 `run_e2e` 的方式用环境变量选通道，顺序调用这条通道的桥：列表（首页 500 条、`keys_only` 整轮、`changed_since`）、档案、单据查询、应收应付账龄（常见的逾期提醒查询：按到期日、往来单位 + 业务员分组、只要逾期）、总账摘要、应收应付处理、模拟一轮事件轮询（每类单据和档案各一次 `changed_since`，加处理增量和摘要）、模拟一轮删除扫描。只读，不需要打开会话；每次调用记录耗时、条数、字节，前后各读一次桥健康检查的登录计数。结果写在通道 `report.dir/perf-<时间>/`（`perf.json`、`perf.md`），`--out` 可改。

```bash
U8CO_E2E_CONFIG=~/.config/u8co/lanes/802.json python -m co.e2e.perf_reads --repeat 3
U8CO_E2E_CONFIG=… python -m co.e2e.perf_reads --only health,search,aging   # 冒烟
```

`python -m co.e2e.concurrency_probe --n 6 --rounds 3 [--mode burst|hold]` 验证同一操作员并发登录：只在 `session open` 之后跑（与 `session call` 同一把共享锁、同一条签名调用），只调 `login-check`、小的 `reports/mgmt/cash_stock`、小页 `archives/list`；`burst` 每轮并发 N 个，`hold` 在整份 `cash_stock` 运行期间顺序 3 次 `login-check`。打印表格和 JSON 汇总（另存会话目录 `calls/`），出现 422「登录状态不是 0」退出 1，其他非 200 且非 503 `busy`/`busy_timeout` 退出 2（含 429 `busy`，即队列已满；缺省 `--n` 不会出现）。

## 探针

COM 探针（`co/probe/`）的用法、步骤表和安全约束见 [`co/probe/README.md`](../probe/README.md)。它和回归共用 `~/.config/u8co/e2e.json` 与调试会话；会改 U8 的步骤只在调试会话开着、两个快照都在、看门狗至少还有 5 分钟时才执行（见「调试会话」）。
