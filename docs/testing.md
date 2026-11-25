# 测试

本项目的测试分四层：

| 层 | 在哪里运行 | 是否连接 U8 |
| --- | --- | --- |
| 离线单元测试（各组件） | 开发机、CI | 否 |
| 桥的自检：`--selftest`、`--check-signatures`、`--check-config` | U8 应用服务器（`--selftest` 也可在任何 Windows 上） | 否（只读注册表和本机配置） |
| 在你自己的 U8 测试账套上手工验证（第 3 节） | 你的测试环境 | 是，只用测试账套 |
| 自动回归 `co/e2e`（第 4 节） | 开发机经 SSH 驱动你的 U8 测试服务器 | 是，只用专用测试账套，结束时快照还原 |

贡献者在自己的测试账套上，按第 3 节的公开步骤或第 4 节的自动回归验证改动（`CONTRIBUTING.md`）；账套、操作员和数据由贡献者自行准备。自动回归需要的服务器条件较多（见 4.1），不具备时用第 3 节。

## 1. 离线单元测试

在仓库根目录运行，均不连接任何服务器：

| 组件 | 命令 |
| --- | --- |
| API 服务 | `cd api && uv run pytest -n auto` |
| Python 客户端 | `uv run --no-project --python 3.12 --with cryptography python -m unittest discover -s co/client/tests -t .` |
| MCP 服务 | `python3 -m unittest discover -s mcp/tests -t mcp`（只用标准库，Python 3.10 及以上） |
| 事件服务 | `cd events && uv run pytest`（假桥、假 Redis） |
| 桥 | Windows 上 `pwsh scripts/ci/build-cs.ps1`：编译并运行 `--selftest` |
| 仓库脚本（公开前扫描） | `python3 -m unittest discover -s scripts/tests` |

- 客户端测试单进程较慢，可改用 pytest 并行：`uv run --no-project --python 3.12 --with cryptography --with pytest --with pytest-xdist python -m pytest -q -n auto co/client/tests`。
- API 测试中有一条核对 `mcp/u8co_mcp/routes.json` 与 API 路由表一致。
- 协议测试向量在 API、客户端和桥的 `--selftest` 中各有一份，修改时三处须同步（`architecture.md`「桥协议」）。

CI（`.github/workflows/ci.yml`）在每次推送和拉取请求上运行代码质量门禁、上述各组件的单元测试，以及桥在 Windows 上的编译和自检。

## 2. 桥的自检

在 U8 应用服务器上以管理员身份运行（路径按实际安装目录替换）：

```powershell
$exe = "$env:ProgramData\U8Co\u8co\bin\u8co-bridge.exe"
& $exe --selftest
& $exe --check-config
& $exe --check-signatures
& $exe --check-signatures --strict   # 有不通过项时退出码 2
```

| 命令 | 检查内容 | 退出码 |
| --- | --- | --- |
| `--selftest` | 密钥派生、口令解密、签名的测试向量，以及桥内部各模块的自检。不连 U8、不读 `secret.hex` | 0 通过，1 失败 |
| `--check-config` | 加载 `config.json` 和密钥，做与服务启动相同的全部检查（运行目录与文件权限、白名单、各开关），并打印生效的取值 | 0 通过，1 失败 |
| `--check-signatures` | 对桥用到的每个 ProgID 读取已注册的类型库，与桥内置的期望表对比成员、参数个数、可选尾参数和按引用位置。只读：不创建对象、不登录 U8、不写注册表，不需要停服务 | 0；带 `--strict` 且有不通过项时 2 |

`--check-signatures` 输出一行 JSON：`summary` 为 `ok` 或 `mismatch:<n>`，`features` 按功能列出状态和受影响的路由。每行状态为 `ok`、`mismatch`、`missing`（ProgID 未注册或组件文件不存在）或 `unknown`（读不到类型库，通常是 .NET 组件）。U8 升级或打补丁之后应先运行一次。服务启动后也会在后台运行一次，结果见健康检查的 `signatures` 字段和 `meta` 的 `features.signatures`。

## 3. 在你自己的 U8 测试账套上验证

写操作只在专用测试账套上试，不要在正式账套上试。

### 准备

1. 用 U8 系统管理把一份账套备份引入为新的账套号（示例用 `801`），只用于测试。改动前请 DBA 做完整备份，以便随时恢复。
2. 在测试账套中建一个测试操作员（示例 `op001`），授予被测模块的功能权限和数据权限。
3. 桥的 `allowedAccounts` 只放测试账套；需要测试第二级写入时，再设置 `enableReplicatedWrites: true` 并把测试账套放入 `testAccounts`（`configuration.md`）。正式账套永远不要放入 `testAccounts`。
4. 本机按 `configuration.md` 配好客户端（桥地址和共享密钥），本机 IP 须在桥的 `allowedClients` 中。

涉及字段权限、记录权限的改动，另备一个非账套主管的测试操作员（示例 `op002`）并在 U8 中配置相应的权限对象，用它和 `op001` 分别执行同一读取，核对 `masked_fields` 与返回值。

### 步骤

```bash
CLI="uv run --no-project --python 3.12 --with cryptography python -m co.client.u8co_client"
$CLI health
$CLI login-check --acc 801 --year 2026 --operator op001 --date 2026-01-31
$CLI meta
$CLI load --acc 801 --year 2026 --operator op001 --date 2026-01-31 --type sale_order --id 9000000003
```

口令只从终端读取。之后按下列顺序验证要使用的每一种写操作：

1. **健康检查。** `health` 的 `signatures` 应为 `ok`；不是时先处理 `--check-signatures` 报告的问题。
2. **读取。** 先读出要操作的单据，记下状态和来源单据的累计数（已领量、入库量、开票数、到货数等）。
3. **预演。** 对写命令加 `--dry-run`（或请求体 `dry_run: true`），核对返回的 `docs`，再读一次，确认单据和累计数都没有变化。
4. **写入。** 去掉预演执行，带幂等键（`--idempotency-key`）；在 U8 客户端中核对结果（审核人、审核时间、累计数量、凭证）。
5. **反向操作。** 弃审、删除新建或生成的单据，确认来源累计数回到原值。
6. **拒绝路径。** 对已是目标状态、被下游引用或不满足门槛的单据重复操作，应返回 409 `state_mismatch` 或相应错误码，且 U8 中没有变化。

测试结束后用备份恢复测试账套，或在 U8 中清理测试数据。不要把测试账套的真实数据、截图或日志提交到仓库。

## 4. 自动回归（`co/e2e`）

`co/e2e` 把第 3 节的手工验证自动化：从开发机经 SSH 驱动你的 U8 测试服务器，对测试用的桥服务按用例登记表跑全部路由（读取、预演、写入、反向操作、拒绝路径），结束时把测试账套还原到开始时的样子，并用逐表指纹证明。用例清单、用例的写法和调试会话的细节见 [`co/e2e/README.md`](../co/e2e/README.md)。

一轮回归依次做：

1. 只读预检：SSH、桥服务、两个库的状态和权限、快照所在盘的空闲空间。
2. 取两个库每个用户表的行数和校验和，作为基线指纹。
3. 为 `UFDATA_801_2024` 和 `UFMeta_801` 建数据库快照，并登记看门狗计划任务：到点还没还原，就自动停桥、从快照还原、再启桥。
4. 把本机的桥源码（`co/bridge/src`）上传到服务器编译、自检，替换服务的 exe；服务器上已是同一份源码时跳过。
5. 对桥跑用例。
6. 无论成败：停服务，从快照还原，在还原会话里取指纹与基线比对，删快照，启服务。

退出码 0 仅在每条用例都是 PASS 或 N/A、且还原后的指纹与基线一致时出现。账套里没有用例需要的单据时：必需挑单挑不到记 SKIP（不算通过，整轮不是 0）；本来就可选的用例记 N/A；通道配置了 `account.sparse_samples: true`（见 4.5）时，必需挑单挑不到也记 N/A。挑单都是 SQL 现查，不写死单据号；固定的编码从测试数据文件取，缺了同样 SKIP。

### 4.1 前提

下面各项都是必需的；缺任何一项，回归在预检阶段就会停下（`--dry-run` 只做预检，不写账套）。没有这些条件（没有 SQL Server 快照、不能 SSH 到 Windows、不能替换桥）时，用第 3 节的手工步骤。

- [ ] **U8 服务器（Windows）**：U8 和桥已按 `getting-started.md` 第 2 节装好，桥是 Windows 服务。回归会**替换这个服务的 exe**（只写 `<bridge.root>\bin\` 下的 exe、exe.config 和它们的 `.prev`），所以必须是专用的测试桥，不能指向正式桥。
- [ ] **OpenSSH 与权限**：OpenSSH Server 只允许密钥登录。SSH 用户（`server.ssh_user`）必须是 Windows 服务器上的本地管理员（Administrators 组成员），要能停启桥服务、登记最高权限的计划任务，并有「作为批处理作业登录」权限（S4U 看门狗任务要用）。服务器上有 PowerShell 7 和 `sqlcmd`。
- [ ] **SQL Server 权限**：版本支持数据库快照（Enterprise、Developer，或 2016 SP1 以上的 Standard）。运行回归的 Windows 身份（`server.windows_principal`，不写时是 SSH 用户）须是两个测试库的所有者、有 `CREATE ANY DATABASE`、**不是** sysadmin，且对其他 U8 库（`UFDATA_*`、`UFMeta_*`、`UFSystem`、`UFModel`）没有访问权；预检逐项核对。授权模板见 `co/e2e/remote/grant-lane.sql.example`，由 sysadmin 执行一次。快照所在盘至少 2 GiB 空闲。
- [ ] **签名还原过程（按需）**：U8 应用服务器以 `sa` 连接测试库时，池里的连接会在 `SINGLE_USER` 之后抢占唯一的连接，还原要反复重试甚至失败。这类服务器由 sysadmin 装一次 `scripts/lane/install-release-proc.sql`，见 [`scripts/lane/README.md`](../scripts/lane/README.md)；没装也能跑，预检只给警告。
- [ ] **专用测试账套**：例如 `801`，由 U8 系统管理引入，只用于测试。桥的 `allowedAccounts` 正好只有它（预检核对），`testAccounts` 里有它并设 `enableReplicatedWrites: true`（`configuration.md`）。账套要先通过体检 `reports/account_readiness`；是空账套时见 4.6。
- [ ] **测试操作员**：`op001`（账套主管或有各被测模块的功能权限和数据权限，并关联一个人员，审批用例要用）。字段权限和记录权限用例另需非主管操作员 `op002`；没配的用例记 N/A。
- [ ] **本机**：Python 3.12 与 `uv`，到服务器的 SSH 私钥和 `known_hosts`，桥的共享密钥文件（`bridge.secret_file`）；凭据文件权限必须是 `0600`。
- [ ] **时间**：回归反复登录 U8，白天会和同事争用许可点数；缺省的业务时间护栏会拒绝在业务时间内启动（见 4.7）。

### 4.2 配置

三个本机文件，路径都可在配置里改；环境变量 `U8CO_E2E_CONFIG` 可换一份配置，多条通道就靠它。配置里有不认识的键（包括拼错的）直接报错，不会悄悄用缺省值。

| 文件 | 来源 | 内容 |
| --- | --- | --- |
| `~/.config/u8co/e2e.json` | 复制 `co/e2e/e2e.example.json` | 服务器、SSH、SQL、账套、快照、桥、报告目录、看门狗、业务时间 |
| 操作员凭据（`account.operator_file`） | 自己写 | `{"acc": "801", "year": "2024", "operator": "op001", "password": "…"}`，`acc` 必须等于配置的测试账套；权限 `0600` |
| 测试数据（`testdata.file`） | 复制 `co/e2e/testdata.example.json` | 账套里固定的编码（科目、部门、收发类别、档案模板等）；缺哪个键，用到它的用例 SKIP |

配置键（缺省值见样例；带 * 的必填）：

| 键 | 含义 |
| --- | --- |
| `server.host`*、`ssh_user`、`windows_principal` | 服务器地址；SSH 用户；跑 SQL 和计划任务的 Windows 身份（空则用 SSH 用户） |
| `server.remote_root`、`u8_home`、`pwsh`、`timezone` | 服务器上的回归工作目录（脚本、快照、标记；由回归自己建并锁权限）；U8 安装目录（不会被碰；与回归目录不在同一个盘时，整盘都不碰）；PowerShell 7 路径；业务时间用的时区 |
| `ssh.key`、`known_hosts`、`control_dir` | 私钥、已知主机文件、SSH 复用连接目录（空则不复用） |
| `sql.server`、`port`、`service`、`sqlcmd` | SQL Server 实例、端口、Windows 服务名、服务器上的 `sqlcmd` 路径 |
| `account.code`*、`year`* | 测试账套号（3 位数字）和启用年度；库名默认 `UFDATA_<账套>_<年度>`、`UFMeta_<账套>`，可用 `data_db`、`meta_db` 覆盖 |
| `account.denied`* | 绝不能碰的账套号数组；没有就写 `[]`，测试账套本身出现在里面算配置错误 |
| `account.operator_file`、`perm_operator_file`、`perm_evaluator_file` | 测试操作员；字段权限用例用的非主管操作员；权限核对用的评估操作员（后两个空则相关用例 N/A） |
| `account.foreign_probe` | 「账套不在允许列表」用例用的账套号；空则取 `denied` 的第一个 |
| `account.seedable`、`sparse_samples` | 允许 `close --keep` 和造数；挑不到必需单据时记 N/A。两者都要求写了顶层 `lane` |
| `snapshot.suffix`、`task_prefix` | 快照库名后缀；看门狗和 S4U 探测计划任务的名字前缀 |
| `bridge.url`、`port`、`root`、`service_name`、`secret_file` | 桥地址（形如 `http://主机:端口/u8co`，端口等于 `port`）、端口、运行目录、服务名、共享密钥文件 |
| `report.dir` | 报告和会话标记的本机目录 |
| `watchdog.run_minutes`、`session_minutes` | 回归和调试会话的看门狗时长（10–120 分钟） |
| `business_hours`* | 业务时间护栏，见 4.7；写 `null` 才是不设护栏 |
| `lane`、`seed` | 通道名（4.5）；造数覆盖值（4.6） |

### 4.3 运行

```bash
RUN="uv run --no-project --python 3.12 --with cryptography python -m"
$RUN co.e2e.run_e2e --dry-run                      # 只读预检和指纹，不建快照、不登录 U8
$RUN co.e2e.run_e2e --cases health,login           # 先验证桥可达、签名正确、能登录
$RUN co.e2e.run_e2e                                # 完整回归
$RUN co.e2e.run_e2e --changed-since origin/main    # 只跑与改动相关的用例（开发自测）
```

先 `--dry-run`，再跑 `--cases health,login`，两者都过再跑完整回归。`--cases` 是逗号分隔的用例名，名字不在登记表里就直接退出；`--watchdog-min N` 改看门狗时长（缺省 25 分钟）；`--serial` 一条接一条跑，`--jobs N`（1–8）并行跑互不冲突的用例，是试验性的，只用于开发自测；`--force-deploy` 不管源码是否变化都重新编译、换程序、重启桥。`--changed-since` 只是过滤器，不能代替完整回归。

报告在 `report.dir` 下按时间命名的目录里：`report.md`、`report.json` 和每一步的日志，含每条用例的耗时、指纹比对结论（`rollback_verified`）和失败原因；口令和共享密钥不会写进去。

**调试会话。** 做和回归相同的预检、指纹、快照和看门狗，然后停下，让你手工调用、查 SQL，开发时反复试同一批用例：

```bash
$RUN co.e2e.session open [--watchdog-min 120]
$RUN co.e2e.session call v1/login-check              # 补上测试凭据；call health 同理
$RUN co.e2e.session sql --query "SELECT TOP 1 ID FROM SO_SOMain"
$RUN co.e2e.session extend --minutes 60              # 延长看门狗，会话从打开起最长 240 分钟
$RUN co.e2e.session reset                            # 回到快照基线，会话保持打开
$RUN co.e2e.run_e2e --in-session --cases sale-order-cycle   # 在会话里跑用例，不快照、不还原
$RUN co.e2e.session close                            # 还原、核对指纹、删快照
```

`session` 另有 `status`、`deploy`、`audit`。`sql` 只能打到测试账套的两个库；`sql`、`call`、`deploy` 执行前都会确认快照在、看门狗还剩至少 5 分钟。没有 `close` 时看门狗到点照样还原。`--in-session` 的结果不证明已还原，只用于开发自测。

### 4.4 还原核对

还原成功与否看报告里的 `rollback_verified`。指纹对不上时，保留快照，按报告里的人工步骤处理，不要先删快照。`UFSystem` 不在快照内：登录记录、主键计数等不会被还原，只留下主键断号。

### 4.5 多条通道

一条通道 = 一个测试账套 + 一个测试桥服务 + 一份配置文件。几条通道可以在同一台服务器上同时开会话、跑回归，用 `U8CO_E2E_CONFIG=<配置文件>` 选通道。

每条通道需要自己的：账套（`802`）、Windows 登录（SSH 用户，如 `u8co-ops-802`，且只能访问自己的两个库）、桥服务（运行目录、服务名、端口）、`remote_root`、`snapshot.suffix`、`snapshot.task_prefix`、`report.dir`。以 `co/e2e/lane.example.json` 为样本，顶层写 `"lane": "l802"`（通道标记，2–12 位小写字母或数字）。写了 `lane` 时，加载配置会检查这些名字不能停在缺省值上、且要含账套号或通道名，端口不能是 18089，`ssh_user` 不能是缺省值，否则逐项报错。这只是防配错的启发式检查；开会话前用 `session status` 对一遍各通道打印的指纹（`lane_*`），同一台服务器上它们必须两两不同。

**分片回归 `co.e2e.multi`。** 每条通道一个 `run_e2e` 子进程同时跑，把全量用例按分片表分到各条通道，最后合并报告：

```bash
$RUN co.e2e.multi --lane main=~/.config/u8co/e2e.json
$RUN co.e2e.multi --lane main=~/.config/u8co/e2e.json --lane lane2=~/.config/u8co/lanes/802.json
```

- `--lane 通道名=配置文件` 里的通道名（如 `main`、`lane2`）是分片表里的分片标签，由你起，必须出现在分片表里；它和配置文件里顶层 `lane` 的通道标记（如 `l802`）是两回事，互不要求相同。没有 `co/e2e/shards.json` 时用 `co/e2e/shards.example.json`：只有一条通道 `main`，也是缺省通道，所有用例都分给它；所以单通道可以直接 `--lane main=…`。
- 要用别的通道名或几条通道，需要自己的分片表。先让每条通道各自跑一轮完整回归，再用各自的报告生成：

  ```bash
  python -m co.e2e.shard_assign recompute --report main=<main 的 report.json> --report lane2=<lane2 的 report.json> --write
  ```

  不加 `--write` 只打印差异。第一份 `--report` 的通道成为缺省通道；每条用例分给报告里 PASS 的通道，几条都 PASS 的按耗时均衡。分到别的通道后不一定还能过，生成后先跑一轮分片回归确认。分片表里只有用例名和通道名，不含账套号和路径；`--assign <文件>` 可指定别的分片表。
- 开始之前先检查：各配置能加载、同一台服务器上的通道指纹互不相同、每条通道都分到用例、不在业务时间内。`--dry-run`、`--watchdog-min`、`--force-deploy`、`--allow-daytime` 原样传给每条通道。
- 合并报告写在第一条 `--lane` 的报告目录下；只有每条通道都 PASS（或 N/A）、每个 `rollback_verified` 为 true、且登记表里每条用例至少在一条通道有结果时，结论才是 PASS。`multi` 不接受 `--in-session`。
- 同一台服务器上同时跑几条通道时，两个桥的 `cleanOrphanTasks` 都保持缺省的 `false`；各通道的快照共用一块盘，按每条通道各 2 GiB 以上留余量；U8 并发登录许可要够用。

`account.sparse_samples: true`（需要 `lane`）用于账套较小、本来就缺某些单据的通道：必需挑单挑不到时记 N/A 而不是 SKIP。只放过「挑单查询正常但没有结果」；挑单出错、缺测试数据键、登录失败照旧 SKIP 或 FAIL。

### 4.6 空账套造数

账套本身要先准备好（补丁、年度、日历、配置、系统启用、审批流），步骤见 `getting-started.md` 第 7 节和 [`scripts/account/README.md`](../scripts/account/README.md)，每一步用体检核对。之后可以用 `python -m co.e2e.seed` 经 API 补上回归用的基础档案（计量单位、收发类别、结算方式、分类、部门、仓库、人员、客户、供应商、存货等）和一批供用例挑选的单据（订单、出入库、检验单、生产订单、收付款等），并写出这条通道的测试数据文件。它只在满足下面全部条件时运行，否则拒绝，不连服务器：

- 配置里 `account.seedable` 为 `true`（要求写了顶层 `lane`）。
- 有一个由 `session open` 打开的会话。
- `testdata.file` 是这条通道自己的文件：路径里含通道名或账套号，不能是缺省路径。

```bash
$RUN co.e2e.session open
$RUN co.probe.comprobe build                   # 造数的 ato 步骤要用 comprobe.exe，先在服务器上编译好
$RUN co.e2e.seed plan                          # 只读：每一步已存在和待建的对象
$RUN co.e2e.seed run [--only customers,vendors] [--skip 步骤名,…]   # 步骤名由 plan 列出；已存在的跳过，可重跑
$RUN co.e2e.session close --keep               # 核对无误后保留造数结果作为新基线
```

**编码和科目。** 缺省编码是中性的测试值，不一定符合你账套的编码方案和科目表。需要时在 e2e 配置的顶层写可选的 `seed` 对象，逐项覆盖（不认识的键拒绝；不写则全用缺省）：

| 键 | 覆盖什么 |
| --- | --- |
| `seed.date` | 造数日期，`YYYY-MM-DD`，须落在账套已建的年度内 |
| `seed.codes` | 部门（5 个）、客户分类、供应商分类、存货分类（上下两级共 2 个）、收发类别（`rd_style`）、结算方式、地区分类、货位的编码 |
| `seed.cycle_codes` | 「新增-修改-删除」用例使用的客户、供应商、部门、仓库、分类、人员、存货编码（各 3 个，账套里必须不存在） |
| `seed.gl` | 费用科目 `expense_account`、应付科目 `payable_account`、银行科目 `bank_account`、现金流量项目 `cash_flow_item` |

**预检。** 写入之前，`seed` 先只读核对：每个编码的位数是否符合账套的编码方案（`GradeDef_Base`）、`seed.gl` 的科目在造数年度的科目表里存在且是末级、现金流量项目存在且未关闭。有不合的一次列出全部，并写明该改 `seed` 的哪个键；不通过时不写入任何数据。

不 `close --keep` 就 `close`，造的数据随快照还原一起消失；`close --keep` 之后快照已经删除，请 DBA 立刻做一次 `COPY_ONLY` 完整备份。

### 4.7 业务时间和安全护栏

**业务时间。** `business_hours` 必须写（写 `null` 才是不设护栏）：

```json
"business_hours": {"days": "1-5", "start": "08:00", "end": "17:30"}
```

`days` 是 ISO 星期（1 星期一到 7 星期日，写成 `1-5`、`1,3,5`）；`start`、`end` 是 `HH:MM`，含开始不含结束，不跨午夜；`tz` 缺省取 `server.timezone`。回归和 `session open` 在这段时间内、或看门狗时长会进入这段时间时拒绝启动；确实要白天跑就加 `--allow-daytime`。`--dry-run` 和已打开会话里的 `call`、`sql`、`close` 不受限制。

**安全护栏。**

- 只碰测试账套：允许的库只有测试账套的两个库和本次的快照；本机先检查每条 SQL，拒绝别的库名、`EXEC`、`BACKUP`、`RESTORE`、`DROP`、`xp_` 等（防手误的文本检查，不是沙箱）；`account.denied` 必须显式写出。
- 桥侧只放测试账套：预检要求桥的 `allowedAccounts` 正好只有它；非 `testAccounts` 的账套拿不到第二级写入。
- 快照还原是兜底：无论成功、失败还是中断都会还原；还原失败会保留快照和看门狗，并在报告里写明人工步骤。
- 看门狗：到点还没还原，服务器上的计划任务自己停桥、还原、启桥。会话忘了 `close` 也一样。
- 路径和权限：只在 `server.remote_root` 之下写文件，不碰 U8 安装目录及其所在盘；不改桥的 `config.json`、`secret.hex`，不删审计日志；SSH 用户不应是你不信任的账户（它能改回归脚本）。
- 同一时刻只有一个所有者（`run` 或 `session`）持有一条通道；标记还在、或还有快照、看门狗时，回归和 `session open` 都会拒绝。
