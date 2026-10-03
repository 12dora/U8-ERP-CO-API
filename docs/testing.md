# 测试

本项目的测试分三层：

| 层 | 在哪里运行 | 是否连接 U8 |
| --- | --- | --- |
| 离线单元测试（各组件） | 开发机、CI | 否 |
| 桥的自检：`--selftest`、`--check-signatures`、`--check-config` | U8 应用服务器（`--selftest` 也可在任何 Windows 上） | 否（只读注册表和本机配置） |
| 在你自己的 U8 测试账套上验证 | 你的测试环境 | 是，只用测试账套 |

维护者用于发版的完整端到端回归工具（数据库快照、自动还原等）依赖特定的测试环境，不包含在本仓库中。提交改动时请按本页在你自己的测试账套上验证（`CONTRIBUTING.md`）。

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

### 步骤

```bash
CLI="uv run --no-project --python 3.12 --with cryptography python -m co.client.u8co_client"
$CLI health
$CLI login-check --acc 801 --year 2026 --operator op001 --date 2026-01-31
$CLI meta
$CLI load --acc 801 --year 2026 --operator op001 --date 2026-01-31 --type sale_order --id 1000000003
```

口令只从终端读取。之后按下列顺序验证要使用的每一种写操作：

1. **健康检查。** `health` 的 `signatures` 应为 `ok`；不是时先处理 `--check-signatures` 报告的问题。
2. **读取。** 先读出要操作的单据，记下状态和来源单据的累计数（已领量、入库量、开票数、到货数等）。
3. **预演。** 对写命令加 `--dry-run`（或请求体 `dry_run: true`），核对返回的 `docs`，再读一次，确认单据和累计数都没有变化。
4. **写入。** 去掉预演执行，带幂等键（`--idempotency-key`）；在 U8 客户端中核对结果（审核人、审核时间、累计数量、凭证）。
5. **反向操作。** 弃审、删除新建或生成的单据，确认来源累计数回到原值。
6. **拒绝路径。** 对已是目标状态、被下游引用或不满足门槛的单据重复操作，应返回 409 `state_mismatch` 或相应错误码，且 U8 中没有变化。

测试结束后用备份恢复测试账套，或在 U8 中清理测试数据。不要把测试账套的真实数据、截图或日志提交到仓库。
