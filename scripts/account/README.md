# 账套准备脚本

把一个刚建好的空 U8 账套补齐到能跑自动回归（`co/e2e`，见 [`docs/testing.md`](../../docs/testing.md) 第 4 节）的状态。完整流程和每一步怎么核对见 [`docs/getting-started.md`](../../docs/getting-started.md) 第 7 节。脚本只是没有 U8 界面可用时的替代：**U8 自己能做的（建立年度账、系统启用、工作日历、审批流设计、单据编号设置）优先在 U8 里做。**

每做完一步，用账套体检 `reports/account_readiness` 复查。体检每一项的 `docs_anchor` 指向指南里对应的小节。准备好之后，基础档案和单据用 `python -m co.e2e.seed` 造（`docs/testing.md` 第 4.6 节）。

## 脚本和顺序

| 顺序 | 脚本 | 体检项 | 写哪里 | 放行开关 |
| --- | --- | --- | --- | --- |
| 1 | `replay-patches.ps1` | `patches` | 测试账套的 `UFDATA_<账套>_<年度>` 和 `UFMeta_<账套>` | `-Force`（有单据时） |
| 2 | `copy-config.sql` | `yearly_config`（起始年度的科目、编码方案） | 测试账套的 UFDATA | 无：只用于空账套 |
| 3 | `create-years.sql` | `years` | 测试账套的 UFDATA，以及 **UFSystem 里这个账套自己的行**（`UA_Period`、`UA_Account_sub`、`UA_HoldAuth`，条件都限定 `cAcc_Id`） | `Force` |
| 4 | `extend-calendar.sql` | `calendar` | 测试账套的 UFDATA | `Force` |
| 5 | `copy-year-config.sql` | `yearly_config` | 测试账套的 UFDATA | `Force`（跨年补新年度：只插进空的（年度，表），不删） |
| 6 | `copy-qm-workflow.sql` | `workflow`（审批流） | 测试账套的 UFDATA | 无：只用于空账套 |
| 7 | `ensure-extradefine.sql` | `vendor_extradefine` | 测试账套的 UFDATA | `Force` |
| 8 | `align-numbering.sql` | `defaults`（编号规则） | 测试账套的 UFDATA | `Force` |
| 9 | `copy-settle-accounts.sql` | `yearly_config`（结算方式科目） | 测试账套的 UFDATA，只插不删 | `Force`（造数之后有单据，要设 1） |

**`copy-config.sql` 必须在建年度之前。** 建年度（`create-years.sql` 或 U8 的建立年度账）从起始年度复制科目、科目编码方案和年度选项，`copy-config.sql` 只改 `TargetYear` 这一年。已经先建了年度的，对每个后续年度再跑一次 `copy-config.sql`，`TargetYear` 设成那一年。`copy-year-config.sql` 在建好全部年度之后跑，造数之前又建了新年度就再跑一次；`Force` = 0 时它和其他两个复制脚本一样，造数之后拒绝执行。

**跨年。** 已造数并保留的账套到了新的一年，体检 `yearly_config` 对今年 `fail`：先 `create-years.sql`（`Force` = 1）建今年，再 `copy-year-config.sql`（`FirstYear` = `LastYear` = 今年，`Force` = 1）。`Force` = 1 时不查单据，只往这一年在这张表里一行都没有的（年度，表）里插，已有行的不动，不删任何行；最后用 `copy-settle-accounts.sql`（`Force` = 1）补今年的结算方式科目。

**结算方式科目放在最后。** 结算方式要经 API 造数才有，所以 `copy-settle-accounts.sql` 在造数并 `close --keep` 之后跑；那时目标已有单据，要把 `Force` 设为 1。它只补源账套里不带银行账号的通用行，目标已有的行一律不动，不删任何行。

脚本里的 `RefDb` 是源账套：一个配置完整的账套（例如 U8 自带的演示账套 `998`），只读；`TargetDb` 是要准备的空测试账套（例子用 `801`）。两者在同一个 SQL Server 实例上，打过同样的 U8 补丁。源账套的科目、编码方案和选项会被复制到测试账套，请选和你要测的业务口径一致的账套。

## 共同约定

- **以 sysadmin / DBA 身份执行。** 这些脚本写测试账套的库，`create-years.sql` 还写 `UFSystem` 里这个账套自己的行，要由 sysadmin 或同等权限的 DBA 来跑；不是回归用的受限登录（后者对 `UFSystem` 没有访问权）。
- **先备份。** 每个脚本执行前请 DBA 对要写的库做一份 `COPY_ONLY` 完整备份。脚本头部写了怎么还原；多数只能靠恢复备份。
- **不要对正式账套跑。** 业务单据指总账凭证、生产订单、检验单、采购订单、销售订单、采购入库单、销售出库单、应收应付单、收付款单（每张表只查 `EXISTS`）。写配置的复制脚本 `copy-config.sql`、`copy-qm-workflow.sql` 发现目标有业务单据就拒绝，没有放行开关；`copy-year-config.sql` 只有跨年用的 `Force`（只补空的（年度，表），不删）；其余脚本要显式打开 `Force` 才放行。
- **源和目标不能是同一个账套。** 两个库名的账套号相同时拒绝。库名必须是 `UFDATA_<三位账套>_<四位年度>`，`UFSystem` 等共享库不能当目标。
- **只写点名的库。** 除了 `create-years.sql` 会写 UFSystem 里目标账套自己的行，脚本只写目标 UFDATA（`replay-patches.ps1` 另写目标的 UFMeta），只读源账套。表都用三段名引用；`copy-config.sql` 的复制批先 `USE` 目标库，让临时表的 `COLLATE DATABASE_DEFAULT` 取目标库的排序规则（否则是登录缺省库的，排序规则不同时报 468）。
- **参数。** SQL 脚本顶部是 `:setvar`，值是尖括号占位，不改就在首个批次的检查里失败。`:setvar` 的优先级高于 `sqlcmd -v`，所以要改文件里的值（或复制一份改），不要只在命令行传。SSMS 里执行要打开「SQLCMD 模式」。
- **执行。** `sqlcmd -S <SQL 实例> -E -b -f 65001 -i <脚本>`。脚本是 UTF-8（中文注释和报错），`-f 65001` 让 sqlcmd 按 UTF-8 读；不加时按系统代码页读，中文报错会变乱码。脚本开头是 `:on error exit`，任何一批出错就停；写入在一个事务里（`SET XACT_ABORT ON`），失败整体回滚。末尾的 `SELECT` 是核对结果，按脚本头部的说明看。
- **可以重跑。** 已存在的跳过或按键覆盖。
- **补丁重放。** `replay-patches.ps1` 先用 `-WhatIf` 看清单；正式执行要 `-BackupConfirmed`，连接串只从环境变量 `U8_PATCH_CS` 取。每个补丁文件一个事务，出错停在那个文件，之前已提交的文件不回滚。执行前它扫一遍全部脚本，列出写三段名（`库.架构.对象`、`库..对象`）的批次（含 `CREATE` 和 `SELECT … INTO`）：对 `UFSystem` 的 `INSERT` / `UPDATE` / `MERGE` 是补丁里已知的可重复执行的定义 upsert，标「提示」照常执行（DBEnginSys 也会执行）；对 `UFSystem` 的其他写法（`DELETE`、`DROP`、`TRUNCATE`、`CREATE`、`SELECT … INTO`、`ALTER`、`EXEC`）标「拒绝」，加 `-Force` 才标「放行」照常执行；写目标两个库、`tempdb`、`UFSystem` 之外其他库的一律「拒绝」，`-Force` 也不放行。有「拒绝」时正式执行一个文件都不跑就停，`-WhatIf` 只列出。识别只看写语句后紧跟的对象名，是清单不是安全边界。

## 不在这里的

- 系统启用（`modules`）：U8 企业应用平台 → 基础设置 → 基本信息 → 系统启用。
- 采购期初记账（`pu_opening`）：API `openings/post`，造数的 `pu_opening` 一步会做。
- 人员、操作员与人员对应、基础档案、币种、采购类型、收发类别：造数 `python -m co.e2e.seed`（`docs/testing.md` 第 4.6 节）。
- 月末结账（`prior_gl_close`）：在 U8 里做；测试账套（桥 `testAccounts` 里的）也可以用 API `periods/close` 的 `through`，造数的最后一步 `close_prior_months` 会做。
