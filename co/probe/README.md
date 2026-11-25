# comprobe：JSON 驱动的 COM 探针

`comprobe` 是在 U8 服务器上运行的 32 位小程序（`comprobe.exe`），按一份 JSON 脚本依次登录 U8、创建组件对象、调用方法、读写属性、读写 DOM 和执行 SQL，每步输出一行 JSON。用来在**测试账套**上研究 U8 组件的调用序列和返回值，例如桥要对接的新组件、某个方法的参数形状、U8 报错的原文。它和自动回归共用 `co/e2e` 的配置和调试会话，前提和配置见 [`docs/testing.md`](../../docs/testing.md) 第 4 节。

| 文件 | 作用 |
| --- | --- |
| `comprobe.py`、`comprobe_remote.py` | 本机的命令行：校验脚本、上传源码、经 SSH 在服务器上编译和运行 |
| `comprobe/*.cs`、`comprobe/build.ps1`、`comprobe/comprobe.exe.config` | `comprobe.exe` 的 C# 源码、编译脚本和配置 |
| `u8co-probe.ps1`、`u8co-probe-login.ps1`、`run-u8co-probe.bat` | 安装前探针（另一个工具，见下节「安装前探针」） |

## 安装前探针

`u8co-probe.ps1` 在装桥之前，在 U8 服务器上只读地探一遍环境：.NET 版本、U8 注册表和安装目录、`U8Login.clsLogin` 等 COM 组件是否注册及文件版本、本机 SQL Server 的账套和库清单；可选地用测试账套登录一次。不写注册表，不用 `sa`，写出的文件只有本目录 `results\u8co-probe-*.txt`。目录查询只读；可选的登录会在 `UFSystem` 留下登录记录，不能说成完全不改库。

在 U8 服务器上用 `run-u8co-probe.bat` 运行（它起 32 位 PowerShell 7，需要已安装 `C:\Program Files (x86)\PowerShell\7\pwsh.exe`），参数原样传给 `u8co-probe.ps1`：

| 参数 | 作用 |
| --- | --- |
| `-U8Home` | U8 安装目录，缺省 `D:\U8SOFT`。读该目录下的文件必须显式带 `-ReadU8Files`，否则只报 `skipped-need-ReadU8Files` |
| `-ReadU8Files` | 允许读 `-U8Home` 下的文件（取版本号） |
| `-Server` | SQL Server 主机名或 IPv4，缺省 `127.0.0.1`，不能是连接串 |
| `-TestAccount`、`-Operator`、`-Year`、`-Date` | 要登录一次时给：账套号（3 位数字）、操作员、会计年度（必须显式给，4 位数字）、日期（`yyyy-MM-dd`，缺省今天）；口令在控制台提示输入，不回显，不写入结果文件。账套和操作员都不给时不登录 |
| `-TestAccountAllowlist` | 允许登录的账套号清单（逗号分隔，pwsh 里要加引号，例如 `'801,802'`）；`-TestAccount` 必须在其中，没给清单就不登录 |
| `-DenyAccounts` | 始终拒绝登录的账套号（如正式账套），写法同上 |

例：只读探测，再用测试账套登录一次。

```powershell
.\run-u8co-probe.bat -ReadU8Files -TestAccount 801 -TestAccountAllowlist '801' -Operator op001 -Year 2024
```

## 编译和运行

```bash
RUN="uv run --no-project --python 3.12 --with cryptography python -m"
$RUN co.e2e.session open                              # 含写步骤的脚本要先打开调试会话
$RUN co.probe.comprobe build                          # 上传源码并在服务器上编译（见下）
$RUN co.probe.comprobe run --script probe.json
$RUN co.e2e.session close
```

- `build` 按 e2e 配置生成 `comprobe.json`（账套、年度、桥的运行目录、U8 安装目录），连同 `comprobe/` 里的源码上传到服务器上 `server.remote_root` 下的 `comprobe` 目录，由那里的 `build.ps1` 用 32 位 `csc /platform:x86` 编译。改过探针源码或升级后要重新 `build`。
- `run --script x.json` 把脚本送给 `comprobe.exe` 执行。脚本是 `{"steps": [...]}`，最大 1 MiB；每步一个 `op`，结果变量写在 `var`，引用已有变量写成 `$名字`。出错就停（带 `tolerate` 的 `call` 除外，全部跑完退出码为 2）。输出里的 `message` 是 U8 或 COM 的原文。
- 配置同 `co/e2e`：缺省读 `~/.config/u8co/e2e.json`，`U8CO_E2E_CONFIG` 可换一份。

## 步骤

| `op` | 作用 |
| --- | --- |
| `login` | `sub`（子系统，必填），可选 `year`、`operator`、`date`、`server`；`year`、`operator` 省略时取操作员凭据文件里的值，`server` 省略时读桥 `config.json` 的 `u8Server` |
| `hold` | `sub`、`count`（1–20）：连续登录并保持 `count` 次，记下成功次数，最后全部登出；用来测许可点数 |
| `create` | `progid`（ProgID 或 `{CLSID}`）和 `var`，得到 COM 对象 |
| `call` | `target`、`method`，可选 `args`、`byref`、`dispatch_null`、`var`、`tolerate`。参数可以是字面量、`$var`、`{"short":n}`、`{"int":n}`、`{"bool":b}`、`{"date":"yyyy-MM-dd"}`（按引用传的 VB 日期参数要用它）和 `$__secret`（操作员口令，只能当 `call` 的参数，不进变量表、不回显） |
| `get` / `set` | 读、写 `target` 的 `prop`（`set` 另有 `value`） |
| `dom.new` / `dom.blank` / `dom.load` | 空的 MSXML DOM；按 `sql` 存成没有行的 DOM；按 `sql` 和整数 `id`（绑定 `?`）装载，没有行就失败 |
| `dom.addrow` / `dom.set` / `dom.single` | 在 DOM 上追加 `z:row`；设置或（`value` 为 null 时）删除某行的属性；克隆 DOM 只留一行 |
| `dom.rows` / `dom.xml` | 打印行（`cols` 为 `*` 或逗号分隔的列名）；打印 XML（`max` 为截断长度） |
| `sql` | `query` 和 `params`（`?` 绑定），走本次登录的连接，打印行；可选 `max`、`timeout_s` |
| `trans.begin` / `trans.commit` / `trans.rollback` | 登录连接上的事务；提交时把「没有事务」当成已经提交 |
| `lastsql` | 另开一条连接，读本进程每个会话最近执行的 SQL |

例：读一张销售订单的表头，打印单号和审核人。

```json
{"steps":[
  {"op":"login","sub":"SA"},
  {"op":"dom.load","var":"head","sql":"select '' as editprop, saleorderq.* from SaleOrderQ saleorderq where saleorderq.id=?","id":1000000003},
  {"op":"dom.rows","dom":"$head","cols":"csocode,cverifier"}
]}
```

## 安全

- **只用测试账套。** 账套固定为配置里的测试账套，且必须在已安装的桥的 `allowedAccounts` 里；脚本里的 `acc` / `account` 只能是它或省略。
- **会改 U8 的步骤**（`call`、`trans.*`、`dom.set`）只有在调试会话开着、两个快照都在、看门狗至少还有 5 分钟时才执行；脚本含这类步骤时，`comprobe.exe` 还按看门狗剩余时间算一个写入预算（到点前 3 分钟为止，最多 900 秒），每个写步骤前核对，超出就停下。会话结束时测试账套照常还原，探针写进去的数据不会留下。
- **口令只走 stdin。** 口令从测试操作员凭据文件（`account.operator_file`，权限必须是 `0600`）读出，放在 stdin 的第一行，后面才是脚本；没有命令行口令参数，脚本里不能写口令，输出里的口令换成 `***`。
- **不输出连接串。** 脚本和输出里都不要打印登录所用的数据库连接串（含账号口令）；`sql` 步骤只打印查询结果。
- 探针不写 U8 安装目录，也不在远程工作目录之外删文件。
- `build`、`session open`、`close` 等要独占通道锁，与 `run` 互斥；谁后到谁直接报错退出。
