# 单据事件

`events/`（`u8co-events`，Python 3.12 以上）是一个独立的小服务：定时轮询桥的只读列表，和上一轮的状态对比，把变化写成事件，发到 Redis Streams。下游程序订阅 stream 就能知道 U8 里哪些单据变了，不用自己轮询，也不用在 U8 里装插件。

能订阅的数据源（`accounts[].sources`，缺省只开 `vouchers`）：

| 数据源 | 事件 `type` | 读的桥路由 | 说明 |
| --- | --- | --- | --- |
| `vouchers` | 全部单据类型（`accounts[].types`，缺省为 `co/client/u8co_kinds.py` 的 `KIND_NAMES`） | `vouchers/list` | 新增、修改、审核、关闭、审批流、删除 |
| `notes` | `ar_note`、`ap_note` | `vouchers/list`、`notes/get` | 应收 / 应付票据，规则同单据，见 §2「票据」 |
| `arap_process` | `ar_process`、`ap_process` | `arap/process/list` | 应收应付处理批次（核销、转账、并账、红票对冲、汇兑损益、票据处理等）的处理、取消、制单 |
| `gl` | `gl_voucher` | `gl/vouchers/digest` | 总账凭证的新增、修改、审核、出纳签字、记账、作废、删除 |
| `archives` | `archive:<档案>` | `archives/list` | 基础档案的新增、修改、停用、启用、删除 |

所有数据源进同一个 stream（`<stream_prefix><账套>`），按 `type` 区分；投递语义（§1）、出箱、健康检查都相同。

```
桥的只读路由 --HTTP，签名--> u8co-events --XADD--> Redis Streams（u8co:events:<账套>）--XREADGROUP--> 你的程序
                                   |
                                   +-- SQLite 状态库（水位、快照、出箱）
```

它只读 U8：平时只调上表的列表 / 摘要路由（都走桥的只读 SQL 线程池，不登录 U8 业务组件），只有删除扫描（或水位倒退后的整轮读取）为空时才逐条确认（单据调 `vouchers/load`，要登录 U8；票据调 `notes/get`、档案调 `archives/get`、总账凭证调 `gl/vouchers/load`，都只跑 SQL；见 §2「删除」），不改任何数据。

## 1. 投递语义

- **至少一次。** 同一个事件可能收到不止一次（服务在发出后、记账前断电或重启时会重发）。消费方**必须按 `event_id` 去重**。
- **断电不丢。** 事件先和水位、快照一起在一个 SQLite 事务里写进出箱（WAL、`synchronous=FULL`，提交即落盘），之后才发 Redis；Redis 确认落盘后才从出箱删掉。任何时刻断电，重启后没发完的会重发，还没轮询到的会从已提交的水位接着轮询。
- **`event_id` 是确定的。** `sha256("账套|type|id|kind|ufts")` 的十六进制（`workflow` 也一样，`kind` 就是 `workflow`）；删除事件是 `sha256("账套|type|id|deleted|最后一次看到的 ufts")`。同一次变化无论重发几次、服务重启几次，`event_id` 都一样。
- **顺序。** 只保证同一张单据（同一账套、`type`、`id`）的事件按发生顺序进同一个 stream。不同单据之间、不同 `type` 之间没有全局顺序。
- **延迟。** 轮询间隔（缺省 30 秒）加一次列表调用。删除靠定期全量扫描发现（缺省 30 分钟一次），所以 `deleted` 最多晚一个扫描周期。

### Redis 必须配置成断电不丢

服务启动时用 `CONFIG GET` 核对 Redis，不合格就拒绝启动：

| 配置 | 要求 |
| --- | --- |
| `appendonly` | `yes` |
| `appendfsync` | `always`；Redis 7.2 以上也接受 `everysec`（靠 `WAITAOF` 确认落盘）。`no` 一律拒绝 |
| `no-appendfsync-on-rewrite` | Redis 7.2 以下必须是 `no`（否则 AOF 重写期间不 fsync） |
| `maxmemory-policy` | 设了 `maxmemory` 时必须是 `noeviction`（否则内存满时 stream 会被淘汰） |

托管 Redis 常禁用 `CONFIG`，这时也拒绝启动。确认能接受丢数据时才设 `redis.allow_nondurable_redis=true`（只打警告，照常启动）。启动时连不上 Redis 是另一回事：退出码 3，稍后重启即可。

**落盘证明和写入在同一条连接上。** Redis 7.2 以上，一批 `XADD` 和末尾的 `WAITAOF 1 0 10000` 放在同一个 pipeline 里发出，`WAITAOF` 回复本机已 fsync 才算这批成功；`appendfsync always` 时它立即返回，运行中有人把 `appendonly` 关掉时它会报错，事件留在出箱里。Redis 客户端关闭了自动重试：重连后在新连接上单独补发的 `WAITAOF` 前面没有写入，会立即返回，证明不了这批已经落盘。失败一律由出箱整批重发（可能产生重复，按 `event_id` 去重）。

`compose.example.yml` 里自带一个 `redis:7-alpine`，参数是 `--appendonly yes --appendfsync always --no-appendfsync-on-rewrite no --maxmemory-policy noeviction`，数据放在命名卷里。

## 2. 事件

### 种类

| `kind` | 什么时候 |
| --- | --- |
| `created` | 出现了以前没见过的 `id` |
| `modified` | `ufts` 变了，且审核、关闭状态都没变 |
| `verified` / `unverified` | 审核状态 0→1 / 1→0 |
| `closed` / `opened` | 关闭状态 0→1 / 1→0 |
| `workflow` | 只有质量单据（`qm_incoming_check`、`qm_product_check`、`qm_incoming_reject`、`qm_product_reject`）：审批状态 `wf_state` 或当前审核人 `current_auditor` 变了 |
| `deleted` | 全量扫描里不见了（见下文「删除」） |

一次轮询里同一张单据可能同时出现审核、关闭、审批流变化，这时各发一个事件（比如终审通过同时发 `verified` 和 `workflow`，弃审发 `unverified` 和 `workflow`）；有这些变化时不再另发 `modified`。

### 审批流

质量单据走 U8 审批流时，审批可能在 U8 客户端、U8 移动端，或经本项目的 `workflow/*` 接口完成。上层应用（例如消息平台的待办）订阅 `workflow` 事件，就能及时知道哪张单据进入审批、换了审核人、审批结束，再按 `type` + `id` 调 `workflow/state` 取待办人（`pending`）去建或撤自己的待办（见 `api-reference.md` 的审批流一节）。

| 字段 | 内容 |
| --- | --- |
| `wf_state` | 表头 `iVerifyStateNew`：`0` 未提交、`1` 审批中、`2` 通过、`-1` 不通过；U8 里为 NULL 时是 `null` |
| `current_auditor` | 表头 `cCurrentAuditor`，当前审核人姓名（U8 的原值），没有时是 `null` |

这两个字段只出现在质量单据事件的 `prev` / `curr` 里（所有种类都带，不只 `workflow`）；其他类型的事件没有这两个键。常见的变化：提交 `0→1` 且出现审核人；中间节点同意后审核人换人、`wf_state` 仍是 `1`；终审通过 `1→2`（同时 `verified`）；不同意结束 `1→-1`；撤销提交或退回提交人回到 `0`。同一轮之间来回变只能看到最终状态。

示例（提交后）：

```json
{
  "kind": "workflow",
  "type": "qm_product_check",
  "id": 1000123,
  "code": "QC-0001",
  "prev": {"verified": false, "closed": false, "red": false, "verifier": null, "closer": null, "wf_state": 0, "current_auditor": null},
  "curr": {"verified": false, "closed": false, "red": false, "verifier": null, "closer": null, "wf_state": 1, "current_auditor": "审批人乙"}
}
```

（省略了 `event_id`、`account`、`ufts`、`detected_at`，与其他种类相同。）

桥返回的列表行不带这两个键时（桥的版本早于事件服务）不对比、不发 `workflow`；请先升级桥，再升级事件服务。

### 字段

stream：`<stream_prefix><账套>`，缺省 `u8co:events:999`。每条消息的字段：

| 字段 | 内容 |
| --- | --- |
| `event_id` | 去重键，64 位十六进制 |
| `account`、`type`、`kind`、`id` | 账套、单据类型、种类、单据主表 ID（十进制字符串） |
| `payload` | 完整事件 JSON（UTF-8，键排序、无空格） |

`payload` 的内容：

```json
{
  "event_id": "3f9a…",
  "account": "999",
  "type": "sale_order",
  "id": 1000123,
  "code": "SO-0001",
  "kind": "verified",
  "ufts": "123456789",
  "detected_at": "2026-09-28T01:02:03Z",
  "prev": {"verified": false, "closed": false, "red": false, "verifier": null, "closer": null},
  "curr": {"verified": true, "closed": false, "red": false, "verifier": "张三", "closer": null}
}
```

- `ufts`：单据的 rowversion（表头和表体取大），十进制字符串。
- `detected_at`：服务发现变化的时间（UTC），不是 U8 里操作的时间。
- `created` 的 `prev` 是 `null`；`deleted` 的 `curr` 是 `null`，`code`、`ufts` 取最后一次看到的值。
- 事件只带状态摘要，要单据内容就按 `type` + `id` 调 `vouchers/load`（或 API 的对应路由）。

### 删除

rowversion 看不到删除（行已经没了）。服务每隔 `delete_scan_minutes` 用 `keys_only` 把每种单据的主键整轮扫一遍，快照里有、扫描里没有的就发 `deleted`。扫描中途任何一页出错，这一轮就作废，不发删除事件，下一轮再扫。

**空扫描要逐张确认。** 一张都没扫到而快照里有单据时，可能真的都删了（比如某种单据本来就只有几张，被全部删除），也可能是账套或权限出了问题。服务按快照里的 `id` 逐张调 `vouchers/load` 确认（最多 `empty_scan_confirm_max` 张，缺省 50）：

- 每一张都是桥明确回答的「单据不存在」（HTTP 404，错误体 JSON 里显式带 `"code": "not_found"`，且不是「未知路径」）：当作都已删除，照常发 `deleted`、删快照，日志（INFO）记一条「主键扫描为空，逐张确认都已不存在」；
- 读到第一张还在的，或读取返回别的错误（403、503、连不上、没带 `code` 的 404——比如反向代理挡掉了 `vouchers/load`、桥的「未知路径」404……）：立即停下，当作列表或权限出了问题，这一轮作废，`scan_error` 写明「至少 1 张（id=…）仍能读取」或读取出的错，不发删除事件；
- 快照多于 `empty_scan_confirm_max` 张，或它设为 0：不确认，这一轮作废（0 就是旧行为）；
- 确认途中服务收到停止信号：立即停下，这一轮作废，不记错误。

`vouchers/load` 按单据类型登录 U8 对应子系统（与 API 的读取相同），比列表重，每一张都占一次登录和加密点数（点数紧张时见 u8-notes 的「许可点数」一节），也占着轮询线程（每张最长 `bridge.timeout_seconds`）。所以只在空扫描时才调，读到一张还在的就不再往下读。确认删除后快照清空，之后不再重复；确认失败（列表或权限问题没解决）时，每次扫描最多读到第一张还在的那张为止。

票据（`ar_note`、`ap_note`）规则相同，只是逐张确认改调 `notes/get`（只跑 SQL，不登录 U8、不占点数）。

### 票据

`sources` 含 `notes` 时，应收票据 `ar_note`、应付票据 `ap_note`（`AP_Note`，按 `cFlag` 分）跟单据走同一套轮询：按表头 `Ufts` 增量、`keys_only` 主键扫描找删除，字段、`event_id`、首次运行的规则都同上。`id` 是 `Auto_ID`，`code` 是票据号 `cVouchID`。

| `kind` | 什么时候 |
| --- | --- |
| `created` / `deleted` | 登记新票据 / 票据被删除 |
| `closed` / `opened` | 余额 `iRAmount` 变为 0（结算、贴现、背书、退回完）/ 由 0 恢复（取消处理） |
| `modified` | 表头 `Ufts` 变了而余额是否为 0 没变（改票据、部分处理回写余额等） |

- 票据没有审核，`verified` 恒为 `false`，不会有 `verified` / `unverified`；`prev` / `curr` 与单据相同（`verified`、`closed`、`red`、`verifier`、`closer`）。
- 票据处理（托收 9A、退回 9C、贴现 9D、背书 9E）同时还会作为 `ar_process` / `ap_process` 批次事件出现（开了 `arap_process` 时）：票据事件看票据本身，处理事件看那一批处理，两者都发，按需订阅。
- 要票据内容和处理记录，按 `type` + `id` 调 `notes/get`（`vouchers/load` 不收票据类型）。
- 操作员的数据权限按往来单位、部门、业务员过滤，看不到的票据不会有事件；权限收窄后原来看得到的票据会在主键扫描里当作 `deleted`。

### 基础档案

`sources` 含 `archives` 时，`accounts[].archives` 里的每种档案各是一个类型 `archive:<档案>`（如 `archive:customer`），读桥的 `archives/list`（只跑 SQL，不登录 U8、不占点数）。`id` 恒为 `0`，`code` 是档案编码（两段主键的档案写成 `<第一段>:<第二段>`，与 `archives/get` 相同），`ufts` 是指纹，当不透明值用。

| `kind` | 什么时候 |
| --- | --- |
| `created` | 出现了以前没见过的编码 |
| `modified` | 有时间戳的档案：`ufts` 变了；没有时间戳的：名称、分类编码变了。停用状态没变 |
| `disabled` / `enabled` | 停用状态 否→是 / 是→否 |
| `deleted` | 编码扫描（或整表比对）里不见了 |

停用状态变了只发 `disabled` / `enabled`，不再另发 `modified`。`prev` / `curr` 是 `{name, class_code, disabled, end_date}`：

- `end_date`：客户、供应商的停用日期，仓库、部门的停用日期，人员的失效日期，存货的停用日期（`yyyy-mm-dd`），固定资产卡片的处置日期；其他档案为 `null`。填了日期就算停用，与当天日期无关（填了以后的日期也立即发 `disabled`）。
- `disabled`：上面的日期已填；操作员的停用状态；项目已关闭；固定资产卡片已处置（卡片连已处置的一起列，处置发 `disabled`，不当作删除）。

两类档案读法不同：

- **有时间戳**（`accounts[].archives` 缺省的 29 种）：每轮按水位（`MIN_ACTIVE_ROWVERSION()-1`）取 `changed_since` 之后的整行，指纹 = `ufts`；按 `delete_scan_minutes` 用 `keys_only` 整轮扫编码找删除。水位倒退时整轮重新对比，规则同单据（§6 的「水位倒退」一条）。人员按 `hr_hi_person`、`Person` 两张表较大的 rowversion。
- **没有时间戳**（`voucher_sign`、`project`、`customer_address`、`customer_bank`、`vendor_bank`、`fa_card`、`operator`、`role`，要写明才开）：没有水位，按 `delete_scan_minutes` 整表读一遍，与快照逐条比内容（同时找出删除），所以变化最多晚一个扫描周期；`delete_scan_minutes=0` 时这些档案不产生事件。指纹 = `sha256(上次指纹|编码|名称|分类|停用日期|停用)` 前 16 位，改回原值也是新指纹，`event_id` 不与先前的事件重复；新出现的编码（含删掉后又建的同一编码）没有上次指纹，用发现时刻代替，「建→删→再建→再删」四个事件的 `event_id` 各不相同。只比上面这几项，其他字段（如项目的自定义项、卡片的原值）变了没有事件。

其他规则：

- 首次运行：有时间戳的档案按水位、没有时间戳的按第一次整表比对，只记快照，不发 `created`（`backfill_events=true` 时发，只限状态库全新时就开着的档案，见「首次运行」）。
- 编码扫描或整表比对一条都没读到、快照里却有档案时，按编码逐条调 `archives/get` 确认，规则同上文「删除」（同一个 `empty_scan_confirm_max` 上限，只认「档案不存在」的 404）；`archives/get` 只跑 SQL，不占点数。
- 有时间戳的档案之间改名、改分类等任何列变了都会改 `ufts`，事件只带上面四项，要全部字段就按 `code` 调 `archives/get`。只改了 `ufts` 以外看不出差别的列也发 `modified`。若夜间任务或其他程序重写档案行，时间戳会变而事件字段不变，产生 `prev` 与 `curr` 相同的 `modified`；消费方应比较 `prev` / `curr`，勿把无字段变化的 `modified` 当作业务变更，或像档案缓存那样把同一批事件合成一次增量同步。
- `exchange_rate` 只看操作员凭证年度的汇率（`archives/list` 缺省年度）；换年度要同时换凭证文件里的 `year`。
- 数据权限（按客户、供应商、部门、存货等）过滤列表：看不到的档案不会有事件；权限收窄后原来看得到的会在编码扫描里当作 `deleted`。`operator`、`role` 只有账套主管能读，`fa_card` 数据量大，开之前先确认。

### 总账凭证

`sources` 含 `gl` 时多一个类型 `gl_voucher`，读桥的 `gl/vouchers/digest`（只跑 SQL，不登录 U8、不占点数）。`GL_accvouch` 没有 rowversion，所以**每一轮**（`poll_interval_seconds`）都把扫描范围内的凭证摘要整轮读完，与快照逐张比对；每轮都是完整扫描，删除不必等 `delete_scan_minutes`。

- 扫描范围：操作员凭证文件里那个年度的未结账期间（`GL_mend`），再加最近 `gl_closed_periods` 个已结账期间（缺省 1，看得到结账前一刻的改动）。期间 0（期初）不看。
- `id` 恒为 `0`；`code` 是 `<凭证类别字>-<凭证号>`（如 `记-12`）；年度、期间在 `prev` / `curr` 里。`ufts` 是链式指纹（上一次的指纹加这一次的状态取 sha256），当不透明值用：审核后弃审再审核，第二次 `audited` 的 `event_id` 也与第一次不同。

| `kind` | 什么时候 |
| --- | --- |
| `created` / `deleted` | 扫描范围内出现新凭证 / 凭证不见了 |
| `audited` / `unaudited` | 审核人空→有 / 有→空 |
| `signed` / `unsigned` | 出纳签字人空→有 / 有→空 |
| `posted` | 记账标志 0→1 |
| `voided` | 作废标志 0→1 |
| `modified` | 金额、分录数、制单人、日期等变了，且上面的状态都没变；取消记账、取消作废也发 `modified` |

`prev` / `curr`：`year`、`period`、`sign`、`no`、`date`、`maker`、`checker`、`cashier`、`bookkeeper`（记账人）、`posted`、`void`、`debit_total`（借方合计）、`lines`（分录数）、`digest`（桥算的内容指纹）；人名为空时是 `null`。审核和签字同一轮发生时各发一条，有状态类事件时不再发 `modified`。

- 水位记成 `<ident>|<年度>|<期间,...>`（发现过还原后再加 `|<还原次数>`）：`ident` 是 `IDENT_CURRENT('GL_accvouch')`，只在库被还原时倒退，倒退时照常整轮比对并记水位倒退告警（`MAX(i_id)` 在删掉最新一张凭证时也会变小，不用它判断）。还原后自增号回退，重做的凭证 `i_id`、内容都可能与还原前一样；每发现一次还原，还原次数加一并混进之后新凭证的指纹，重做凭证的 `created` 不与还原前的 `event_id` 重复。
- 期间滑出扫描范围（结账后又有更新的月份结账）或换了年度：这些快照静默删掉，不发 `deleted`。期间重新进入范围（反结账、改 `gl_closed_periods`、换年度）：只记快照，不发 `created`。
- 整轮一张都没读到而范围内有快照时，按凭证逐张调 `gl/vouchers/load` 确认（规则同上文「删除」）。
- 看不到：同一轮之间来回改只见最终状态；原地改写分录的摘要、科目等而借贷合计、分录数、最大 `i_id`、制单 / 审核 / 出纳 / 记账人和标志都没变的修改（指纹只含这些列）；数据权限外的凭证（有一行分录可见就算整张可见）。

### 应收应付处理

`sources` 含 `arap_process` 时多两个类型 `ar_process`、`ap_process`，读桥的 `arap/process/list`（只跑 SQL，不登录 U8、不占点数）。一个事件对应**一批处理**：同一侧往来明细（`Ar_Detail` / `Ap_Detail`）里处理方式 `cProcStyle` + 处理号 `cCancelNo` 相同的那些行，不含单据自身的审核行。应收冲应付、应付冲应收、并账、票据背书等两侧都记账的处理，两个类型各发一条。`id` 是这批的最小 `Auto_ID`，`code` 是处理号，`ufts` 是指纹（当不透明值用）。

| `kind` | 什么时候 |
| --- | --- |
| `processed` | 出现新批次（核销、转账、并账、红票对冲、汇兑损益、票据处理等）；`prev` 为 `null` |
| `cancelled` | 批次被取消（行被删）；`curr` 为 `null`，`prev` 是最后一次看到的样子 |
| `vouchered` / `unvouchered` | 制单（凭证号 `cPZid` 空→有，或换了凭证）/ 取消制单（有→空） |
| `modified` | 金额合计或行数变了而凭证号没变（正常不会出现） |

`prev` / `curr`：`flag`（`AR` / `AP`）、`style`、`style_name`（9P 核销、9I 应收冲应付、9J 应付冲应收、BZ 并账、9N 红票对冲、9M 汇兑损益、9A 票据托收、9C 票据退回、9D 票据贴现、9E 票据背书、9F / 9G / 9H 坏账、9K 应收冲应收、9L 应付冲应付、XJ 现结；不认识的照写代码）、`partners`（往来单位编码）、`docs`（`[{type, id, line_id, debit_f, credit_f}]`，单据类型、单据号、行 ID、原币借贷，最多 200 行）、`voucher_id`（`cPZid`，未制单 `null`）、`gl_sign`、`gl_no`、`rows`（总行数）、`min_id`、`max_id`、`debit_f`、`credit_f`（原币合计，两位小数字符串）、`year`、`period`。制单类事件不再另发 `modified`；同一处理号取消后又做了一批（最小 `Auto_ID` 变了）发 `cancelled` + `processed`。

往来明细没有 rowversion，读法分两步，**每一轮**都做：

- **新批次按自增号增量读。** 水位是 `Auto_ID`。在途事务可能先占较小的号、后提交，所以每轮从上次水位起重读一个窗口，水位只推进到 `max(0, watermark − lag)`（不后退），`lag = max(auto_id_lag, ident − watermark)`，`ident` 是 `IDENT_CURRENT`；窗口里已知的批次不重发。回滚留下的空号不会再用，滑出窗口后就是永久空号，不发事件。
- **制单、取消制单、取消处理靠期间摘要。** 这些是原地改 `cPZid` 或删行，自增号看不到；每轮按期间（该侧未结账期间，加最近一个已结账期间）取批次摘要（最小 / 最大 `Auto_ID`、凭证号、借贷合计、行数、往来单位）与快照比对。有变化的批次按 `Auto_ID` 区间回读明细，补全 `docs`、`gl_sign`、`gl_no`。更早的已结账期间不会再变，那里的批次滑出回看窗口后从快照里静默去掉。指纹：新批次是最小 `Auto_ID`，之后每变一次是 `最小Auto_ID|凭证号|第几次变化`，取消制单后又制成同一凭证号时 `event_id` 也不同；发现过账套还原后，新批次写成 `最小Auto_ID#还原次数`，变化再加 `|还原次数`（见下文）。

其他规则：

- 首次运行（以及账套被还原时）：只记回看窗口和摘要期间里的批次快照，不发 `processed`（首次运行且 `backfill_events=true` 时发）；快照里有、摘要里没有的批次发 `cancelled`。
- 还原的判断：水位记成 `<mark>|<ident>|<还原次数>|<历史界>`，`ident` 是上一轮的 `IDENT_CURRENT`，这一轮比它小就是还原（回退几个号也认得出，不受回看窗口影响），记水位倒退告警，还原次数加一。还原后自增号回退、处理号重发，重做的批次与还原前的最小 `Auto_ID` 相同；还原次数混进之后的指纹，重做批次的 `processed`、`vouchered` 等不与还原前的 `event_id` 重复。还原与重做都发生在两轮轮询之间、`ident` 也回到原值时看不出来：库里的样子与还原前相同，不发事件。
- 摘要期间里出现快照没有、又早于水位的批次：最小 `Auto_ID` 大于历史界（首次运行或最近一次还原时的水位）的，是在途超过回看窗口才提交的晚到批次，补发 `processed`；不大于历史界的老批次（如期间反结账后重现）只记快照。指纹按最小 `Auto_ID` 定，同一批再次出现时 `event_id` 不变，消费方按 `event_id` 去重。
- 增量读完、取摘要之前刚好制单（如核销时立即制单）：这一轮的 `processed` 里 `voucher_id` 为 `null`，下一轮补发 `vouchered`。
- 摘要整轮一批都没有、快照里却有要取消的批次时，逐批按最小 `Auto_ID` 用同一路由回读（最多 `empty_scan_confirm_max` 批），都读不到才发 `cancelled`，否则这一轮作废并报错。这个回读与摘要同一路由、同样的数据权限，挡得住账套或期间出错，挡不住操作员数据权限被收窄。
- 数据权限按往来单位（必控）、部门、业务员过滤行：事件服务的操作员要有该侧**全部**往来单位的数据权限，否则一批只看到部分行，权限收窄还会被当作 `cancelled`。
- 年度：每行的会计年度取桥给的 `fiscal_year`（期间小于登记月份时算下一年，例如年初对上年末单据做的红票对冲），桥未提供该字段时按登记日期的年份。
- 跨年：桥只看操作员凭证文件那个年度及以前的期间（`GL_mend`）。日历进入新年度而凭证文件的 `year` 没换时，新年度的批次仍按 `Auto_ID` 发 `processed`，但不在摘要期间里，`vouchered`、`unvouchered`、`cancelled` 都看不到，滑出回看窗口后被静默去掉。换年度时同时换凭证文件里的 `year`（与总账凭证相同）。
- 看不到：同一轮之间来回变只见最终状态（如制单后又取消制单）；只改了凭证的总账字段（`cGLSign`、`iGLno_id`）而 `cPZid` 没变；未结账期间以外（更早期间）的原地修改。

### 首次运行

`backfill_events=false`（缺省）时，第一次运行只记下现有单据的快照和水位，不为已有单据发 `created`。之后的变化才发事件。设为 `true` 会把现有单据全部当作 `created` 发一遍。

对附加数据源（`arap_process`、`archives`、`gl`），`backfill_events` 只对状态库全新时就开着的类型生效：状态库已经有数据之后再开的数据源或档案，即使 `backfill_events=true`，首轮也只记快照，不会把已有的客户、存货、凭证、处理批次整批当作新事件发出。单据类型和票据（`vouchers`、`notes`）仍按各类型自己的首轮判断。

### 看不到的变化

- 锁定 / 解锁：列表里没有锁定人字段。
- 只落在审批流自己的表里、单据表头没变的变化：待办表（`Table_Task`）新增或作废而 `iVerifyStateNew`、`cCurrentAuditor` 都没变（例如同一节点多人审批时有人先办了、代理人变化），不会有 `workflow`。需要逐条待办时用 `workflow/state` 的 `pending` 对账。会签或多审批人时 `cCurrentAuditor` 的取值因节点而异，消费方不要假设只有一个审批人。
- 被拦下没有保存的操作（什么都没写进库）。
- 同一轮轮询之间来回改（比如审核后又弃审）只能看到最终状态；中间状态不会补发。
- 总账凭证、应收应付处理不在单据类型里，要开对应数据源才看得到（`gl`、`arap_process`，见开头的数据源表）；它们没有 rowversion，靠按期间比对和自增号增量发现变化，各自的局限见对应小节。
- 票据只改了处理记录（`AP_Note_Sub`）而表头 `Ufts` 没变：看不到，直到表头再被改动（处理回写余额时表头会变，能看到）。
- 只动了表体、而表头 rowversion 没变的修改：表体有 rowversion 的类型按表头、表体较大的算，能看到；表体没有 rowversion 的类型看不到，直到表头再被改动。有的状态是由表体推出来的（例如生产订单的审核状态取自明细行），只改表体行时同理可能漏掉，上线前请在测试账套上对要订阅的类型各试一次。
- 删除只靠定期主键扫描发现，最长延迟一个 `delete_scan_minutes`；扫描失败期间不会有 `deleted`。
- 数据权限：事件服务的操作员必须有应收应付处理（`arap_process`）、基础档案（`archives`）、票据（`notes`）、总账凭证（`gl`）所涉及的**全部**数据权限（往来单位、部门、业务员、存货、科目等）。这些读取都按操作员的数据权限过滤，看不到的行和不存在的行在列表里没有区别：权限被收窄后，原来看得到的会被当作 `cancelled` / `deleted` 发出，之后放开权限又会当作新出现的发出。不要给事件服务的操作员配收窄过的数据权限，调整权限前先停服务。
- 若用备份或快照把账套库还原到较早状态，rowversion 水位可能不回退：被还原成旧值的单据 rowversion 低于水位，不会产生 `modified`；还原时消失的单据仍会由下一轮主键扫描发出 `deleted`。还原后应删掉状态库并用 `run --init` 重新建立。

## 3. 消费示例

用消费组读，处理完再 `XACK`；按 `event_id` 去重（示例用 Redis 集合，保留 7 天）。消费方崩溃后，未 `XACK` 的消息还在该消费者的待处理列表里，重启后先读 `0` 把它们处理完。

```python
import json
import redis

r = redis.Redis(host="redis", port=6379, decode_responses=True)
stream, group, me = "u8co:events:999", "erp-sync", "worker-1"
try:
    r.xgroup_create(stream, group, id="0", mkstream=True)
except redis.ResponseError as exc:
    if "BUSYGROUP" not in str(exc):
        raise

def handle(event: dict) -> None:
    print(event["kind"], event["type"], event["id"], event["code"])

start = "0"  # 先处理自己名下没确认的，再读新的
while True:
    got = r.xreadgroup(group, me, {stream: start}, count=100, block=5000)
    if not got or not got[0][1]:
        start = ">"
        continue
    for msg_id, fields in got[0][1]:
        # 去重标记必须在业务处理成功之后写；处理本身最好也是幂等的
        if not r.exists("seen:" + fields["event_id"]):
            handle(json.loads(fields["payload"]))
            r.set("seen:" + fields["event_id"], 1, ex=7 * 86400)
        r.xack(stream, group, msg_id)
```

同样的逻辑用 `redis-cli` 看：

```bash
redis-cli XRANGE u8co:events:999 - + COUNT 5
redis-cli XINFO GROUPS u8co:events:999
```

**stream 的裁剪只删所有消费组都已确认的事件。** `XADD` 不带 `MAXLEN`。每个 stream 最多每分钟检查一次，长度超过 `redis.maxlen`（缺省 100 万条）时：

- 有消费组：取每个消费组还需要的最小 ID（有待确认的取其中最小的，否则取 `last-delivered-id`），再取所有消费组里最小的，`XTRIM MINID ~` 只删比它小的。未读、未确认的一条不删。裁完仍超长，说明有消费组落后，`/status` 的 `warnings` 里会写出来，日志也有警告。
- 没有任何消费组：无从知道谁读过，按 `MAXLEN ~ maxlen` 裁，并记警告日志。**要可靠地消费，请用消费组**（`XREADGROUP` + `XACK`），不要只用 `XREAD`。

不再使用的消费组要删掉（`XGROUP DESTROY`），否则它会让 stream 一直增长。Redis 内存要按「最慢的消费者可能积压多少」来留。

## 4. 部署

### Docker Compose

```bash
cd events
cp compose.example.yml compose.yml
mkdir -p secrets config
printf '%s' '<64 位小写十六进制>' > secrets/bridge.secret
cat > secrets/operator-999.json <<'JSON'
{"acc": "999", "year": "2026", "operator": "<只读操作员>", "password": "<口令>"}
JSON
sudo chown 10001 secrets/* && sudo chmod 0600 secrets/*
cp config.example.json config/config.json     # 按实际环境改
docker compose up -d --build
docker compose exec u8co-events u8co-events status
```

- 镜像以 uid 10001、只读根文件系统运行，状态库在命名卷 `events-state`（`/var/lib/u8co-events`）。**这个卷必须持久**：丢了它，出箱里没发出的事件和上次水位之后的变化都没了；服务发现状态库是空的而 Redis 里已有 stream 时会拒绝启动（退出码 4），见 §6 的「状态库丢失」一条。
- 基础镜像按摘要固定；升级 Python 时用 `docker buildx imagetools inspect python:3.12-slim` 查新摘要改 `events/Dockerfile`。
- 构建上下文是仓库根目录（要带上 `co/client`），排除规则在 `events/Dockerfile.dockerignore`。
- 本机 IP 要写进桥的 `allowedClients`。操作员用一个只有查询权限的 U8 账号即可。
- 用自己的 Redis 时，按 §1 配好持久化。

### 直接运行

```bash
cd events
uv sync --frozen
export PYTHONPATH=..                                   # 要能 import co.client
export U8CO_EVENTS_CONFIG=/etc/u8co-events/config.json
uv run u8co-events check                               # 检查配置、密钥文件权限、状态库、Redis 持久化
uv run u8co-events run
```

### 命令

| 命令 | 作用 |
| --- | --- |
| `u8co-events run` | 启动轮询和发布，`SIGTERM` / `SIGINT` 停止 |
| `u8co-events run --init` | 状态库为空而 Redis 里已有 stream 时仍然启动（重新回填），效果同配置 `allow_reseed=true` |
| `u8co-events check` | 只做启动检查（含状态卷是否丢失），不轮询 |
| `u8co-events status` | 用只读连接打印每种单据的水位、快照数、延迟、删除扫描、最后错误，以及出箱积压（JSON） |
| `u8co-events healthcheck` | 请求本机 `/healthz`，健康时退出码 0（镜像的 `HEALTHCHECK` 用它） |

退出码：0 正常；1 运行中某个线程异常退出（交给容器重启），或 `status` 读不了状态库；2 配置错误（含 Redis 持久化不合格）；3 启动时连不上 Redis；4 状态库为空而 Redis 里已有 stream，拒绝启动。

### 健康检查

`health.listen`（缺省 `127.0.0.1:8090`，留空关闭）上有两个只读路径，正文相同：

- `GET /healthz`：健康 200，否则 503。
- `GET /status`：总是 200。

健康检查用只读连接（`mode=ro`）读状态库，不建库、不写库；状态库不存在时返回 503。只看配置里的（账套, 类型），配置里去掉的类型留下的旧记录不算。

`types[]` 每个（账套, 类型）一行，附加数据源也一样：开了哪些数据源，就多出哪些行——票据 `ar_note`、`ap_note`，应收应付处理 `ar_process`、`ap_process`，总账凭证 `gl_voucher`，档案每种一行 `archive:<档案>`（如 `archive:customer`）。下面「某种单据」的各项检查对这些行同样适用。

不健康（`problems`）的情况：

- 轮询或发布线程退出；发布线程超过 5 分钟没有成功一轮（比如 Redis 连不上）；
- 出箱首条事件连续发布失败 5 次以上（报出它的 `event_id`，见 §6 的「发不出去的事件」一条）；
- 某种单据（或数据源类型）超过 max(10 分钟, 10 个轮询周期) 没有成功轮询；
- 删除扫描失败（`scan_error`），或超过 max(10 分钟, 3 × `delete_scan_minutes`) 没有完成一次扫描；
- 24 小时内发现过水位倒退。

启动后的前 max(10 分钟, 10 个轮询周期) 不检查「多久没成功」。`warnings` 只提示、不影响 200/503，目前是 stream 超过 `maxlen` 但有消费组没读完。`types[].lag_seconds` 是距上次成功轮询的秒数，`scan_age_seconds` 是距上次完成删除扫描的秒数，`outbox_size` 是还没发出的事件数。

## 5. 配置

JSON 文件，未知键直接报错。路径取 `--config`，其次环境变量 `U8CO_EVENTS_CONFIG`，缺省 `/etc/u8co-events/config.json`。样例见 `events/config.example.json`。

| 键 | 缺省 | 说明 |
| --- | --- | --- |
| `state_path` | `/var/lib/u8co-events/state.sqlite3` | SQLite 状态库；目录 0700、文件 0600 |
| `bridge.base_url` | 必填 | 桥地址，形如 `http://192.0.2.10:18089/u8co` |
| `bridge.secret_file` | 必填 | 桥的共享密钥文件，必须 0600 或 0400 |
| `bridge.timeout_seconds` | 60 | 单次桥调用超时，5–600 |
| `accounts[].acc` | 必填 | 账套号，3 位数字 |
| `accounts[].operator_file` | 必填 | 操作员凭证 JSON（`acc`、`year`、`operator`、`password`），必须 0600 |
| `accounts[].types` | 全部单据类型 | 要轮询的单据类型（票据不在这里，用 `sources` 的 `notes`） |
| `accounts[].sources` | `["vouchers"]` | 开哪些数据源：`vouchers`（单据，类型见 `types`）、`arap_process`（应收应付处理，类型 `ar_process`、`ap_process`）、`archives`（基础档案，类型 `archive:<档案>`）、`gl`（总账凭证，类型 `gl_voucher`）、`notes`（应收应付票据，类型 `ar_note`、`ap_note`）。不含 `vouchers` 时不能写 `types`。每种类型在 `status` 里各占一行，延迟、扫描、水位倒退照常检查 |
| `accounts[].archives` | 有时间戳的 29 种档案 | 开了 `archives` 时轮询哪些档案（`archives/list` 的档案名）。缺省是有时间戳、能按水位增量读的那些；没有时间戳的（`voucher_sign`、`project`、`customer_address`、`customer_bank`、`vendor_bank`、`fa_card`、`operator`、`role`）按删除扫描的节奏整表比对，要写明才开，其中 `fa_card`、`operator`、`role` 数据量大或只有账套主管能读 |
| `poll_interval_seconds` | 30 | 轮询间隔，5–3600 |
| `page_limit` | 500 | 每页条数，1–500 |
| `delete_scan_minutes` | 30 | 删除扫描间隔，0 表示不扫（不会有 `deleted` 事件） |
| `empty_scan_confirm_max` | 50 | 删除扫描（或水位倒退后的整轮读取）为空而快照里有单据时，逐张 `vouchers/load` 确认的最多张数，0–500；0 表示不确认，空扫描一律作废（见 §2「删除」） |
| `auto_id_lag` | 500 | 应收应付处理按自增号取增量时每轮回看的最少条数，50–1000000；防止晚提交的事务占用的较小号被漏掉 |
| `gl_closed_periods` | 1 | 总账凭证除未结账月份外再比对最近几个已结账月份，0–12 |
| `backfill_events` | `false` | 首次运行是否为已有单据发 `created`；附加数据源只对状态库全新时就开着的类型生效（见 §2「首次运行」） |
| `outbox_high_water` | 100000 | 出箱积压达到这个数就暂停轮询（水位不前移，不丢变化），等发布追上 |
| `redis.url` | `redis://redis:6379/0` | `redis://`、`rediss://` 或 `unix://`；不能带口令（`user:口令@` 或 `?password=` 直接报错） |
| `redis.password_file` | 空 | Redis 口令文件（0600），口令只能放这里 |
| `redis.stream_prefix` | `u8co:events:` | stream 名 = 前缀 + 账套 |
| `redis.maxlen` | 1000000 | stream 超过这个长度才裁剪，且只裁所有消费组都已确认的（见 §3） |
| `redis.allow_nondurable_redis` | `false` | 见 §1 |
| `publisher.kind` | `redis` | `stdout` 把事件逐行打到标准输出，只用于开发调试 |
| `publisher.batch` | 100 | 每批发布条数 |
| `publisher.idle_seconds` | 1.0 | 出箱空闲时的检查间隔 |
| `health.listen` | `127.0.0.1:8090` | 健康检查地址，空串关闭 |
| `allow_reseed` | `false` | 状态库是新的、但 Redis 里该账套的流已存在时，是否允许重新回填（见 §6 的「状态库丢失」一条） |

只有路径可以用环境变量覆盖：`U8CO_EVENTS_STATE`（`state_path`）、`U8CO_EVENTS_SECRET_FILE`（`bridge.secret_file`）、`U8CO_EVENTS_REDIS_PASSWORD_FILE`（`redis.password_file`）。

## 6. 运维

- **积压。** `status` 里 `outbox_size` 持续增长说明 Redis 发不出去，看日志里的「发布失败」。达到 `outbox_high_water` 后轮询暂停，恢复后自动追上。
- **发不出去的事件。** 事件按出箱顺序发，一条发不出去会挡住后面所有事件。出箱首条连续失败 3 次后改为一次只发这一条，日志（ERROR）写出它的 `event_id`、账套、类型和单据 ID；连续失败 5 次后健康检查报不健康。服务不会自动跳过或丢弃任何事件：查明原因（Redis 报错内容在日志和 `problems` 里）修好后自动继续。
- **某种单据报错。** 看 `status` 的 `last_error`；该类型的水位不前移，其他类型不受影响。删除扫描的失败单独记在 `scan_error`，增量轮询成功不会清掉它，下一次扫描成功才清；扫描超过 3 倍 `delete_scan_minutes` 没成功，健康检查也会报。`scan_error` 是「主键扫描没有返回任何单据」时看后半句：「仍能读取」或读取出错，查操作员的查询权限和账套；「超过逐张确认上限」，确认该类型单据确实都删了之后，临时调大 `empty_scan_confirm_max`（最大 500）让下一次扫描确认。
- **水位倒退。** 桥返回的水位比已存的小，说明 U8 库被还原到较早的备份，或者凭证指向了别的库。服务不再信任增量：整轮读取全部单据，与快照重新对比（含删除），按对比结果发事件，再用新水位继续。整轮读取一张都没有而快照里有单据时（例如还原到这类单据还一张没有的时候），按上面「删除」一节同样逐张确认（同一个 `empty_scan_confirm_max` 上限、同样的规则）：都不存在才提交删除和新水位；否则这一轮作废、水位不动，`last_error` 写明「水位倒退后的整轮读取没有返回任何单据」和原因。这种确认按删除扫描的节奏做（每个账套、类型每 `delete_scan_minutes` 分钟最多一次，它为 0 时每 30 分钟一次；时刻只记在内存里，服务重启后先确认一次）：两次确认之间的每轮轮询照旧整轮读取、报同一个原因（后面注明上次确认的时间），但不调 `vouchers/load`，免得列表或权限问题持续期间每轮都登录 U8、占加密点数。`status` 里记下 `watermark_reset_at` 和「旧水位->新水位」，24 小时内健康检查报警。还原后单据的 `ufts` 可能与还原前用过的值重复，按 `event_id` 去重的消费方可能把个别事件当成重复，请人工核对该时段。应收应付处理、总账凭证没有 rowversion，按自增计数（`ident`）判断还原，判断出来后给之后的事件换指纹（见 §2 对应小节）；没有时间戳的档案按内容比对，不受还原影响。测试账套恢复备份后，先等一轮轮询（让服务看到还原）再继续测试，否则还原与重做落在同一个轮询间隔里，可能看不到变化。
- **状态库丢失。** 状态库是新的（没有水位、快照、待发事件），而 Redis 里已经有该账套的流，多半是状态卷丢了：服务拒绝启动（`run` 和 `check` 都是退出码 4）。直接回填会丢掉上次水位之后的变化和发件箱里没发出的事件。确认接受后用 `u8co-events run --init`（或设 `allow_reseed=true`）启动一次，回填完成后去掉。
- **重建。** 想从头来（比如换了账套）：停服务，删状态库文件（连同 `-wal`、`-shm`），再启动。会按 `backfill_events` 重新回填；此前未发出的事件会丢，先确认 `outbox_size` 为 0。
- **操作员权限。** 事件服务的操作员须对要轮询的每种类型有查询功能权限，否则该类型每轮 403（`status` 的 `last_error`），水位不前移，其他类型不受影响。不需要的类型在 `accounts[].types` 中不写即可。部分类型要求的功能 id（满足其一即可；账套主管不受限）：

  | 类型 / 数据源 | 功能 id |
  | --- | --- |
  | `ar_bill` / `ap_bill` | `AR21101` 或 `AR0601021` / `AP21101` 或 `AP0601021` |
  | `ar_refund` / `ap_refund` | 同收款单 `AR0601031`、`AR22101` / 同付款单 `AP0601031`、`AP22101` |
  | `qm_incoming_inspect`、`qm_product_inspect` | `QM02010101`、`QM02020101` |
  | `qm_other_inspect` | `QM02060101` 或列表 `QM030601` |
  | `qm_other_check` | `QM02060201` 或列表 `QM030603` |
  | `purchase_settle` | 结算单列表查询 `PU040305` |
  | `position_adjust` | `ST010807` |
  | `ia_adjust` | 入库调整单 `IA1001` / 列表 `IA02040201`，或出库调整单 `IA1004` / 列表 `IA02040301` |
  | `inventory_price_adjust` | `SA03120202` 或列表 `SA0312020301` |
  | `sale_return_apply` | `SA03250104` 或列表 `SA03250201` |
  | `archives` 中的货位、收发类别、本单位开户银行、行业分类 | `AS030Q`、`AS016Q`、`AS013Q`、`AS050Q` |
  | `arap_process`（`arap/process/list`） | `AR0807`、`AR060107`、`AR0503` 之一（应付同理）；读不到时看不到的批次可能被当作已取消 |

- **部分类型的增量规则。** 来料、产品、其他报检单和其他检验单没有审批流，不发 `workflow`。采购结算单（`purchase_settle`）没有审核，只发新增、修改、删除；增量按表头 `psufts`、表体 `psdufts` 较大者，存货核算处理结算成本（表体 `bAccount` 回写）也算修改。货位调整单（`position_adjust`，表 `AdjustPVouch`）、出入库调整单（`ia_adjust`，表 `JustInVouch`）、存货调价单（`inventory_price_adjust`，表 `SA_InvPriceJustMain`）、退货申请单（`sale_return_apply`，表 `SA_ReturnsApplyMain`）只按表头 `ufts` 增量。出入库调整单的 `verified` 表示表体每行都已记账；记账只改表体、不改表头时不会触发 `verified`（期末处理自动生成的调整单是新增，照常发 `created`）。退款单（`ar_refund`、`ap_refund`）的事件同收付款单。
- **版本搭配。** 先升级桥，再升级事件服务：版本较低的桥不认识的类型每轮报错，其他类型不受影响。状态库版本与程序不符时拒绝启动（报「状态库版本 N 与程序（M）不符」）；回退程序前先停服务、备份状态库。
- **负载。** 空闲时每轮每种单据一次列表调用；删除扫描每种单据按 `page_limit` 翻页，读的都是桥的只读线程池。附加数据源每轮（`poll_interval_seconds`）还有：
  - 应收应付处理：每侧一次增量（取水位、`IDENT_CURRENT`、读 `GL_mend`、列明细），再每个摘要期间一次摘要（未结账期间数 + 1）。每次摘要按期间扫一遍往来明细（`Ar_Detail` / `Ap_Detail`，排除单据审核行的条件用不上索引）、再读一次往来单位和 `GL_mend`；4 个未结账期间时每轮约 20 次明细表扫描，表随年度增长。
  - 总账凭证：每轮把扫描期间的凭证整体重新汇总一遍，翻页时每页都重算剩下的范围。未结账期间少时很轻；全年不结账的账套每轮要汇总全年的凭证和分录。
  - 基础档案：有时间戳的档案每种每轮一次 `archives/list`（缺省 29 种即 29 次）；没有时间戳的按删除扫描的节奏整表读。
  大账套开 `arap_process`、`gl` 前先在测试账套上看一轮的耗时，必要时调大 `poll_interval_seconds`。
