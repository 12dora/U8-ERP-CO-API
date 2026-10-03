# 贡献指南

欢迎提交 issue 和拉取请求。本项目直接修改 U8 中的业务数据，因此对「如何证明改动正确」要求较严。动手之前请读完本页。

安全问题不要开公开 issue，见 `SECURITY.md`。

## 1. 基本规则

1. **不要提交任何真实业务信息。** 公司、客户、供应商、人员的名称和编码，真实的单据号、金额、账套号、服务器地址、口令、密钥，都不能进入代码、测试、文档或提交信息。示例使用文档保留地址（`192.0.2.x`、`198.51.100.x`、`203.0.113.x`、`example.com`）和占位值：账套 `801`、`802`、`803`（另有 `998`、`999`：`999` 是 U8 自带的演示账套号，`998` 是桥协议测试向量里固定的账套号，这两个照写，不要改），客户 `C900001`，供应商 `S900001`，操作员 `op001`（张三）、`op002`（李四），部门 `D901`。示例操作员只用 `op001`、`op002`；U8 自带的内置操作员（`SYSTEM`、`UFSOFT`、`demo`）只在说明 U8 本身的行为时提及，不得用作示例操作员。
2. **先实测，后编码。** 文档中没有记载的 U8 调用，先在测试账套上跑通，核对 U8 回写了哪些表和累计数，把观察到的行为写进 `docs/u8-notes.md`，再写进桥。不要依据非官方资料、社区帖子或推测编写代码；调用形状以测试账套上实际跑通的为准。
3. **只在测试账套上试。** 写操作只在专用测试账套上进行（`docs/testing.md`）。不要在正式账套上试，也不要在代码中把任何账套写进白名单。
4. **不引入第三方 U8 封装包。** 不部署、不拷贝它们的程序或代码。
5. 代码注释和文档使用中文，平实陈述事实。

## 2. 代码质量门禁

入口是 `scripts/check-quality.sh`，检查三件事：文件过长、函数过长、圈复杂度过高。风格问题（缩进、引号、import 顺序）不在此检查。Ruff、体积、PowerShell、C# 任一段失败，脚本即以非 0 退出。

```bash
bash scripts/check-quality.sh            # 整个仓库
bash scripts/check-quality.sh --changed  # 相对 HEAD 的改动，含未跟踪文件
git config core.hooksPath .githooks      # 可选：提交时自动运行完整检查
```

### 上限

这些数字是审查时仍能一次读完的上限，超过即失败，促使代码拆分。

| 范围 | 规则 | 上限 |
| --- | --- | --- |
| Python | McCabe `C901` | 10 |
| Python | 分支数 `PLR0912` | 12 |
| Python | 参数个数 `PLR0913` | 5 |
| Python | 语句数 `PLR0915` | 50 |
| Python 文件 | 非空非注释行 | 400 |
| `test_*.py` / `*_test.py` | 非空非注释行 | 800 |
| Python 函数 / 方法 | 非空非注释行 | 60 |
| shell 文件 | 非空非注释行 | 300 |
| shell 函数 | 非空非注释行 | 60 |
| SQL 文件 | 非空非注释行 | 400 |
| PowerShell 文件 | 非空非注释行 | 400 |
| PowerShell 函数 | 非空非注释行 | 60 |
| PowerShell 函数 | 圈复杂度估计 | 10 |
| PowerShell 文件 | 解析错误 | 0 |
| C# 文件（`co/**/*.cs`） | 非空非注释行 | 500 |
| C# 函数 / 方法 | 非空非注释行（lizard NLOC） | 60 |
| C# 函数 / 方法 | 圈复杂度（lizard CCN） | 10 |
| C# 函数 / 方法 | 参数个数 | 6 |

- Python 参数上限为 5：新代码用上下文对象收拢参数，不要再加形参。测试文件只放宽**文件**上限到 800，函数长度、圈复杂度和参数个数与源文件相同。
- shell 比 Python 更严（300），因为部署脚本没有测试兜底。SQL 文件再长就应按主题拆分。Markdown 不参与计数。
- C# 文件上限为 500：花括号单独成行，同样的逻辑行数更多。参数上限为 6：COM 包装需要传一个上下文对象和若干 COM 句柄，再多须收拢。
- Ruff 检查 `api/**/*.py` 和 `co/**/*.py`。`api/pyproject.toml` 中另有一套只查风格的 ruff 配置；门禁始终以 `--config` 指向仓库根目录的 `ruff.toml`。

### 计数规则

体积由 `scripts/quality/sizes.py` 计算，只数非空、非注释行。

- 一行中既有代码又有行尾注释：计一行。只含注释：不计。字符串内容计为代码，Python 文档字符串也计。
- Python 用标准库 `tokenize` 区分注释和代码；语法错误时报 `py-parse`，行数退回「`#` 到行尾」的粗略计数。函数和方法用 `ast`（含 `async def`）；嵌套函数各自计长度，外层函数的跨度包含内层源码；装饰器行计入函数；lambda 不计。
- shell：`#` 只有在词首才是注释（`${#arr}`、`foo#bar` 中的不是）。`<<'PY'` 等 heredoc 正文计为代码，`<<<` 不当作 heredoc。函数识别 `name() {`、`function name {`、`function name() {`，从定义行到**同一缩进**的结束 `}`；单行函数不单独报告；找不到结束括号时在标准错误告警。扩展名为 `.sh` / `.bash`，或 shebang 指向 `bash` / `sh` / `dash` 的无扩展名文件（如 `.githooks/pre-commit`）都按 shell 计。
- SQL：`--` 和 `/* */`；字符串、双引号标识符、方括号标识符中的 `--` 不当作注释。
- PowerShell 的行数和函数行数由 `scripts/quality/ps-metrics.ps1` 用 `[System.Management.Automation.Language.Parser]::ParseFile` 计算，注释 token 和空行不计。圈复杂度从 1 起，每遇到下列一项加 1：`if`、`elseif`、`switch` 的每个子句（含 `default`）、`for`、`foreach`、`while`、`do`、`catch`、`trap`、`-and`、`-or`；`else`、`try`、`finally` 不加。嵌套函数的决策点只计在内层函数。解析错误每条报一行 `ps-parse`。
- C# 文件行数由 `scripts/quality/cs_metrics.py` 计算：去掉 `//` 和 `/* */` 注释后的空行不计，字符串（`"..."`、`@"..."`）和字符字面量中的注释记号不当作注释。函数的 NLOC、圈复杂度和参数个数用固定版本的 lizard：`uvx --from lizard==1.17.13 lizard -l csharp --csv`。超限报告格式为 `path:line CS-CCN|CS-PARAMS|CS-NLOC metric=N limit=M name=Class::Method`；文件超限为 `file-lines`。

### 没有基线，没有豁免

门禁不保留基线文件，也没有「旧债可留、只许变好」的棘轮：已有文件超限与新增超限一样失败。没有豁免名单，也没有按目录放宽的上限。超限就拆分，或修改上表的数字并说明理由。

不要写 `# noqa`、`ruff: noqa`，除非同一行或紧邻的注释写明为何必须豁免、由谁同意。禁止使用 `ruff check --add-noqa`。

### 工具

脚本先确认 `python3` 和 `uvx` 可用，失败时用中文说明原因。Ruff 固定为 `uvx ruff@0.16.9`，C# 度量固定为 `uvx --from lizard==1.17.13 lizard`，均不装进本仓库，缓存沿用本机 uv 的缓存目录。lizard 不可用时 C# 段说明原因并以非 0 退出。

只有仓库中有 `*.ps1`（或 `--changed` 涉及 ps1）时才需要 `pwsh`。`scripts/quality/pwsh.sh` 依次使用 PATH 中的 `pwsh`、`~/.cache/u8co/pwsh-7.4.6/` 中已校验的副本，或下载官方 `powershell-7.4.6-linux-x64.tar.gz` 并用脚本中固定的 sha256 核对（下载使用环境中的 `https_proxy` / `HTTPS_PROXY`，核对失败即删除压缩包）。

`--changed` 下没有 Python 改动时跳过 Ruff，没有 ps1 改动时跳过 PowerShell，没有 `co/**/*.cs` 改动时跳过 C#；体积与 C# 段只测量本次改动的文件。提交钩子 `.githooks/pre-commit` 运行完整检查；停用：`git config --unset core.hooksPath`。

## 3. 测试

各组件的离线单元测试、桥的自检命令，以及在自己的测试账套上验证的步骤，见 `docs/testing.md`。CI（`.github/workflows/ci.yml`）在每次推送和拉取请求上运行代码质量门禁、各组件的单元测试，以及桥在 Windows 上的编译和 `--selftest`。

文本文件统一使用 LF，`.bat` / `.cmd` 保持 CRLF（`.gitattributes`）。

## 4. C# 约束

桥用 Windows 自带的 C# 5 编译器编译，不安装 SDK：

- 不用 `?.`、`$""`、`nameof`、表达式体成员、`out var`、元组、模式匹配、`using static`。
- 不引用互操作程序集。COM、ADO、MSXML 一律晚绑定，只经 `ComUtil.*`。
- 平台 x86，源文件 UTF-8。
- 错误一律使用 `BridgeException(status, code, message)`，`message` 用中文。500 对调用方只写「内部错误」，细节进审计。

修改 `co/bridge` 或复审此类改动时，逐条核对 `co/bridge/CHECKLIST.md`。

## 5. 新增单据类型或操作

以「为某类单据新增一个操作」为例，按以下顺序进行：

1. **实测。** 在测试账套上跑通调用序列：使用哪个组件、`Init` 参数、方法签名、哪些参数按引用、返回什么算成功、U8 回写了哪些表和累计数、事务能否回滚、删除后能否回退。把结论写进 `docs/u8-notes.md`。新增的 COM 成员要加入桥的签名期望表（`co/bridge/src/SigTable.cs`），并用 `--check-signatures` 核对。
2. **桥。**
   - 类型信息在 `co/bridge/src/Kinds.cs` 的 `VoucherKind`：表、列、登录子系统、生单来源、能否审核等。
   - 请求解析和字段校验在 `Requests*.cs`，可写字段走该类型的白名单，并与 U8 行集 schema 一致。
   - 写路由必须在 `DocLocks.KeysOf` 中有锁键、在 `WriteGate.cs` 中登记；纯 SQL 的读路由须在 `RouteClass.cs` 登记才走读线程池，并在 `PermRegistry` 中登记权限规则（未登记的读路由一律 403）。
   - 请求连接上的 U8 写放入 `CoTrans`，提交后在新连接上回读确认；自行提交的组件不包事务，调用前完成门槛检查，调用后回读，失败返回 504 `outcome_unknown`（`docs/architecture.md`「事务」）。
   - 调用前检查门槛（状态、下游引用、审批流），拒绝用 409 `state_mismatch` 并写明原因。
   - 在预演模式表 `DryRunModes` 中登记该操作的预演模式，自行提交的组件之前调用 `DryRun.Stop`。
3. **API。** 在 `api/u8co_api/co_models*.py` 中添加请求和响应模型，在路由模块中添加路由，在 `co_access.py` 的 `ACCESS` 中登记读或写（未登记按写处理）。桥返回的额外响应字段不要在 API 层丢弃。
4. **客户端。** `co/client/u8co_kinds.py` 的类型名单、库方法和命令行子命令；如有需要，同步 MCP 的 `mcp/u8co_mcp/routes.json`。
5. **测试。**
   - API、客户端的离线测试：正文形状、字段校验、拒绝路径。
   - 在测试账套上按 `docs/testing.md` 验证：至少一条正向、一条拒绝；能预演的再验证预演前后单据和来源累计数都没有变化；删除新建的单据并确认来源累计数回到原值。
6. **文档。** `docs/api-reference.md`（路由、字段、错误码）、`docs/u8-notes.md`（观察到的 U8 行为）、必要时 `docs/limitations.md`，以及 `CHANGELOG.md`：在最新版本之上的 `## [Unreleased]` 一节（没有时新建）里按「新增」「变更」「修复」分类记录。

## 6. 拉取请求

- 一个拉取请求只做一件事，说明改了什么、为什么，以及如何验证。
- 修改了桥的 U8 调用时，写明在哪个 U8 版本的测试账套上验证过哪些操作（正向、拒绝、预演、反向操作）。只运行过离线测试的，写明「未在 U8 上验证」。
- 门禁和离线测试须通过。
- 不要在同一个拉取请求中顺带修改无关的格式。

## 7. 许可

提交即表示你同意按 Apache-2.0（`LICENSE`）授权你的贡献。
