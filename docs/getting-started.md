# 快速开始

本文从零完成一套部署：在 U8 应用服务器上安装桥，在 Linux 主机上启动 API 服务，用客户端核对连通，然后在测试账套上走完一次「预演 → 正式新增 → 读回」。最后一节说明如何把一个新账套准备到可用状态，账套体检（`reports/account_readiness`）的每个检查项都链接到那里。

示例中的地址、账套和档案都是占位：U8 应用服务器 `198.51.100.10`，桥 `192.0.2.10:18089`，API 服务主机 `192.0.2.20`，测试账套 `801`（年度 2026），操作员 `op001`。换成你自己的值。**不要在正式账套上执行本文的写入步骤。**

## 1. 环境要求

| 项 | 要求 |
| --- | --- |
| U8 | 已授权的 U8+ 安装。本项目在 U8+ V18.0 上开发和测试，其他版本未经验证。使用前核对你的 U8 许可是否允许此类集成（[常见问题](faq.md)） |
| 测试账套 | 一个专用测试账套，与正式账套隔离 |
| 桥所在主机 | 装有 U8 应用服务器组件的 Windows Server（或装有同版本 U8、能创建 U8 业务 COM 组件的 Windows 主机） |
| .NET Framework | 4.8（注册表 `Release` ≥ 528040）。编译使用系统自带的 `C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe`，无需安装 SDK |
| 位数 | 桥为 32 位（`/platform:x86`），因为 U8 业务组件是 32 位进程内 COM |
| PowerShell | 7.4 以上。安装、卸载使用 64 位 `C:\Program Files\PowerShell\7\pwsh.exe` |
| 服务账号 | LocalSystem。服务没有桌面，U8 若弹出交互对话框，工作线程会被卡住，由看门狗处理（[架构](architecture.md)「超时和看门狗」） |
| 网络 | 桥监听一个 TCP 端口（缺省 18089），只对 API 服务主机开放。桥使用 HTTP，必须放在受信任的网段（[安全](../SECURITY.md)） |
| 数据库 | 缺省使用 U8 登录给出的连接串，无需另建登录。使用专用 SQL 登录见 [配置参考](configuration.md)「sql.json」，不能是 `sa` |
| U8 服务 | U8 自身的服务须在运行。生产订单审核另需生产制造服务 `U8MPool` |
| API 服务主机 | Python 3.12 以上（使用 [uv](https://docs.astral.sh/uv/)）或 Docker；能访问桥的端口和身份提供方的 JWKS |
| 身份提供方 | 支持 client credentials 的 OIDC 提供方 |

不要把安装包放在 U8 安装目录或 U8 所在的非系统盘上，安装脚本会拒绝在那里写文件。

## 2. 安装桥

下文 `<root>` 是桥的运行目录，缺省 `%ProgramData%\U8Co\u8co`。全部配置项见 [配置参考](configuration.md)「桥（Windows）」。

### 2.1 编译

在服务器上，本仓库的 `co/bridge/` 目录中：

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
.\out\u8co-bridge.exe --selftest
.\out\u8co-bridge.exe --check-signatures
```

- `build.ps1` 打印实际执行的 `csc.exe` 命令：平台 x86，源文件 UTF-8（`/codepage:65001`），有编译错误即失败，警告不算错误。成功后把同目录的 `u8co-bridge.exe.config` 复制到 `out\`。缺少这份配置的程序不要用于 U8 调用。
- `--selftest` 核对协议测试向量并运行桥内部各模块的自检，退出码 0 为通过、1 为失败；不连 U8，不读密钥（[测试](testing.md)「桥的自检」）。U8 不在 `C:\U8SOFT` 时加 `--u8-home <U8 安装目录>`，自检会顺带核对该目录下的 EAI 字段对照表；没有 U8 的机器上这几项跳过。
- `--check-signatures` 核对本机 U8 组件的注册和方法签名（见 2.5），不需要配置文件。安装前先跑一次，确认 U8 组件已注册且与桥的调用方式一致。

### 2.2 安装

以管理员运行：

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 `
  -U8Server 198.51.100.10 -AllowedClients 192.0.2.20 -AllowedAccounts 801
```

参数见 [配置参考](configuration.md)「安装脚本参数」。脚本可重复执行，依次完成：

1. 核对 `<root>` 的每一级上级目录（从盘符根到直接上级）：不能是重解析点，所有者必须是 SYSTEM、Administrators 或 TrustedInstaller；除这三者和 CREATOR OWNER 外，任何账户都不能删除、改名（删除子项）、改权限或改所有者；允许其他账户在上级中新建（盘符根和 `%ProgramData%` 缺省即如此）。不满足时脚本停止，服务启动和 `--check-config` 同样拒绝。然后创建 `<root>` 并立即收紧 ACL：去掉继承，只给 SYSTEM 和 Administrators 完全控制，所有者设为 Administrators。本次新建的上级目录（例如首次安装时的 `%ProgramData%\U8Co`）同样处理；它们的所有者只能是上述三者或当前管理员账号，否则视为被他人抢先创建，脚本停止。完成后再核对一遍上级目录和 `<root>` 中已有对象的所有者。
2. 同名服务已存在时，其可执行文件必须正是 `<root>\bin\u8co-bridge.exe`，否则脚本停止，不停、不改、不删其他服务。服务在运行时先停止，再把 `out\u8co-bridge.exe` 和 `out\u8co-bridge.exe.config` 复制到 `<root>\bin\`。
3. 没有 `config.json` 时按参数写入；已有文件不覆盖。
4. 没有 `secret.hex` 时生成 32 字节随机密钥；已有文件不改内容。
5. 执行 `u8co-bridge.exe --check-config`，失败则不启动服务。
6. `netsh http add urlacl`，SDDL `D:(A;;GX;;;SY)`，只允许 SYSTEM 监听该前缀。
7. 创建防火墙入站规则（规则名与服务名相同，分组 `U8Co`）：TCP，端口取自 `listenPrefix`，远程地址取自 `allowedClients`。`allowedClients` 为空时不开端口。有防火墙配置文件处于关闭状态时给出警告。
8. 登记服务（LocalSystem，自动启动），失败后 60 秒重启、一天重置计数；然后启动并做健康检查。
9. 执行 `u8co-bridge.exe --check-signatures`，打印摘要和不通过的功能。不通过不算安装失败。

使用自定义 `-Root`（例如 `D:\Apps\U8Co\u8co`）时，事先建好上级目录并收紧权限。在系统盘以外新建的目录通常从盘符根继承了 Authenticated Users 的「修改」权限，会被第 1 步拒绝：去掉继承，只保留 SYSTEM、Administrators 完全控制和 Users 读取。

防火墙：同名规则的分组不是 `U8Co` 时脚本停止，不修改它；无分组的同名规则只在同名服务确认是本桥时删除重建；同名服务不存在（例如被手工删除）时脚本停止，此时文件和配置已写好，确认该规则无用后执行 `Remove-NetFirewallRule -Name <服务名>` 再重跑。

日志写在脚本目录的 `results\u8co-install-*.log`，屏幕上先打印回滚步骤。脚本不打印密钥。账套白名单为空时给出警告：服务能启动，但所有账套都被拒绝。

脚本拒绝写入 `u8Home` 之下的路径；U8 装在非系统盘时，整块盘都拒绝写入。已有 `config.json` 时先读取它（读取前同样核对上级目录和 `<root>` 中对象的所有者），按其中的 `u8Home` 保护，不必再给 `-U8Home`；给出的值与之不一致时脚本停止。配置样例见 `co/bridge/config.example.json`。

### 2.3 分发密钥

把 `<root>\secret.hex` 中的那一行交给 API 服务（存为密钥文件，由 `U8CO_BRIDGE_SECRET_FILE` 指向，见第 3 节）或运维主机上的客户端（见第 4 节）。不要写进命令行、仓库、镜像或日志。

### 2.4 验证

在 API 服务主机上（其 IP 须在 `allowedClients` 中；需要本仓库的检出，在仓库根目录执行，见 4.1）：

```bash
CLI="uv run --no-project --python 3.12 --with cryptography python -m co.client.u8co_client"
export U8CO_SECRET_FILE=~/.config/u8co/bridge-secret.hex   # 0600 或 0400
$CLI health --base-url http://192.0.2.10:18089/u8co
$CLI login-check --base-url http://192.0.2.10:18089/u8co --acc 801 --year 2026 --operator op001 --date 2026-01-15
```

口令在终端提示时输入。`login-check` 会真正登录 U8，`UFSystem` 中会留下登录记录。

排障时可以不经服务，在服务器上前台运行：

```powershell
& "$env:ProgramData\U8Co\u8co\bin\u8co-bridge.exe" --console
```

前台运行同样需要 urlacl、配置和密钥，并须先停止服务（同一端口只能有一个进程监听）。前台运行使用当前管理员身份，服务使用 LocalSystem。首次安装后两种方式各做一次 `login-check`：只有服务方式失败时，多半是 LocalSystem 无法创建某个 U8 组件，503 `com_unavailable` 的 `message` 和 `<root>\unhandled.log` 会给出原因。

### 2.5 签名自检

桥以晚绑定调用 U8 的 COM 组件。更换 U8 版本或打补丁后，方法可能改名，参数个数或按引用方式可能变化，这类问题要到调用时才报错。签名自检提前把它们找出来：

```powershell
& "$env:ProgramData\U8Co\u8co\bin\u8co-bridge.exe" --check-signatures
& "$env:ProgramData\U8Co\u8co\bin\u8co-bridge.exe" --check-signatures --strict   # 有不通过时退出码 2
```

对桥用到的每个 ProgID，从注册表查 CLSID、`TypeLib` 和 `Version`，找到 `HKCR\TypeLib\{guid}\{ver}\0\win32` 登记的类型库（没有时用 `InprocServer32` 登记的组件文件本身），以 `LoadTypeLibEx(REGKIND_NONE)` 读出该类所有非事件接口的成员，与桥内置的期望表（`co/bridge/src/SigTable.cs`）比较实参个数、可选尾参数和按引用下标。只读：不创建对象、不登录 U8、不写注册表；不需要 `config.json`，也不需要停止服务。

输出一行 JSON（非 ASCII 字符写成 `\uXXXX`）：`summary` 为 `ok` 或 `mismatch:<n>`（n 为不通过的功能个数）；`counts` 为各状态的行数；`features` 按功能（`login`、`sa`、`pu`、`st`、`arap`、`gl-write`、`arc-write`、`wf`、`mo` 等）给出 `status`、受影响的 `routes`，以及不通过或带提示的行（`progid`、`member`、`status`、`detail`、`note`）。

| 状态 | 含义 |
| --- | --- |
| `ok` | 成员存在，参数个数和按引用方式与桥的调用一致 |
| `mismatch` | 成员不存在、调用方式不对、实参个数不在类型库允许的范围内，或桥按引用传递的槽位在类型库中是按值（桥读不到写回值） |
| `missing` | ProgID 未注册、没有登记服务器，或组件文件不存在 |
| `unknown` | 读不到类型库（.NET 组件通常没有类型库，只确认注册），或读取过程中出错 |

类型库为按引用、桥按值传递的槽位不算问题（VB6 会自行转换，只是桥不读写回值）。运行时行为与类型库声明不一致、以实测为准的几处（采购 `Delete`、采购 `GetDefaultVTID`、调拨 `Verify` 第 9 个参数、销售 `VerifyVouch`、销售 `BodyCheck`）只在 `note` 中提示。

服务启动后也会在后台线程跑一次，不阻塞启动：结果写入审计日志（事件行 `"event":"signature_check"`，含摘要、计数和至多 30 条不通过的行）；`GET health` 带 `"signatures"`（`pending`、`ok`、`mismatch:<n>`，自检本身失败时为 `unknown`，不含细节）；完整报告在签名调用的 `meta` 的 `features.signatures` 中。

生产订单审核加载 U8 API 框架的 .NET 程序集时，桥先查找 `u8Home\UFMOM\U8APIFramework`，再查找 `Interop`、`EAI` 等其余目录；每个实际加载的程序集按完整路径在审计日志中记一次 `"event":"assembly_loaded"`（`name`、`path`、`version`、`file_version`）。打补丁后据此确认使用的是哪一份程序集。

## 3. 安装 API 服务

API 服务（`api/u8co_api/`）对外发布 `/v1/co/*` 和 OpenAPI，校验 OIDC 令牌，再签名转发给桥。它不连 U8 数据库，只需访问桥的端口和身份提供方的 JWKS。全部环境变量见 [配置参考](configuration.md)「API 服务」。

### 3.1 部署位置

```
调用方 --HTTPS--> 反向代理（TLS） --HTTP--> u8co-api:8080 --HTTP，签名--> 桥 :18089
                                              |
                                              +--HTTPS--> 身份提供方（JWKS / discovery）
```

- API 服务本身使用 HTTP。对外须放在做 TLS 的反向代理之后，或只在内网使用。
- API 服务主机的 IP 须写进桥的 `allowedClients`；经过 NAT 时写桥看到的源地址。
- 一个容器只运行一个 uvicorn worker：限流计数在进程内，多个 worker 会各自计数。

### 3.2 准备

1. 桥已安装并通过健康检查（第 2 节）。
2. 取得桥的共享密钥（`<root>\secret.hex` 中的那一行），存成文件。API 服务只从文件读取密钥。
3. 在身份提供方为每个调用方建一个机器客户端（client credentials），令牌中带写权限或只读权限（3.4）。每个客户端各有一份限流名额。
4. 确定允许的账套，先只放测试账套。

仓库中的样例：

| 文件 | 内容 |
| --- | --- |
| `api/.env.example` | 环境变量样例 |
| `api/trust.example.json` | 信任配置样例 |
| `api/compose.example.yml` | Docker Compose 样例 |
| `api/Dockerfile`、`api/.dockerignore` | 镜像，构建上下文为 `api/` |

### 3.3 运行

**直接运行：**

```bash
cd api
uv sync --frozen
export U8CO_BRIDGE_URL=http://192.0.2.10:18089/u8co
export U8CO_BRIDGE_SECRET_FILE=/etc/u8co/bridge.secret   # 0400 或 0600，仅运行用户可读
export U8CO_ACCOUNTS=801
export U8CO_TRUST_FILE=/etc/u8co/u8co-trust.json
uv run u8co-api
```

`u8co-api` 按 `U8CO_API_HOST`、`U8CO_API_PORT`（缺省 `0.0.0.0:8080`）启动 uvicorn。也可以直接执行 `uvicorn --factory u8co_api.main:create_app --host 0.0.0.0 --port 8080 --timeout-graceful-shutdown 100`。

**Docker Compose：**

```bash
cd api
cp compose.example.yml compose.yml
cp .env.example .env                       # 按实际环境修改
mkdir -p secrets config
printf '%s' '<64 位小写十六进制>' > secrets/bridge.secret
sudo chown 10001 secrets/bridge.secret && sudo chmod 0400 secrets/bridge.secret
cp trust.example.json config/u8co-trust.json   # 按实际环境修改
docker compose up -d --build
```

- 镜像以 uid 10001 运行，密钥文件须让 uid 10001 可读（`chown 10001`、`chmod 0400`）。普通文件的权限宽于 0600（属组或其他用户有任何权限，包括 `0440`、`0640`、`0644`）时，服务拒绝使用该文件，按未配置处理并记一条警告，`/v1/co/*` 全部 503。挂在 `/run/secrets/` 下的 docker secrets（或同一路径下的 Kubernetes Secret 卷）不检查权限。启动后看 `/healthz` 的 `configured` 是否为 `true`。
- 样例 compose 只把端口绑定在本机，根文件系统只读，去掉全部 capability。
- 镜像带健康检查（`python -m u8co_api.healthcheck`，请求 `/healthz`）。
- 构建需要代理时，使用 Docker 的代理设置或临时的 `--build-arg HTTP_PROXY=…`，不要把代理写进 Dockerfile 或镜像。

不要把密钥写进 compose 文件、`.env`、镜像或仓库。

**按账套分流到多个桥（可选）：** 为个别账套另装一个桥实例时，用 `U8CO_BRIDGE_ROUTES_FILE` 让这些账套走该实例，其余账套仍走 `U8CO_BRIDGE_URL`。不设该变量时行为不变。

```bash
mkdir -p secrets config
printf '%s' '<分流桥的 64 位小写十六进制密钥>' > secrets/bridge-802.secret
sudo chown 10001 secrets/bridge-802.secret && sudo chmod 0400 secrets/bridge-802.secret
cat > config/u8co-bridge-routes.json <<'EOF'
{"routes": [{"accounts": ["802"], "url": "http://192.0.2.11:18100/u8co", "secret_file": "/run/secrets/u8co_bridge_secret_802"}]}
EOF
```

然后在 compose 中挂载分流文件和该密钥（`compose.example.yml` 中有注释掉的样例），`.env` 的 `U8CO_ACCOUNTS` 加上 `802`。要点：

- 每个分流桥使用自己的共享密钥，与该桥实例的 `secret.hex` 相同；密钥文件 0400 / 0600、属主为运行用户，或挂成 docker secrets。
- 路由中的账套必须同时在 `U8CO_ACCOUNTS` 中；令牌配置了账套声明时，声明中也要有它；分流桥的 `allowedAccounts` 也要包含它。
- 分流文件有任何问题（未知键、账套重复或不在白名单、地址不合法、密钥文件读不到或权限过宽），进程都启动失败，不会悄悄退回缺省桥。格式见 [配置参考](configuration.md)「按账套分流的桥」。
- 启动后用 `/v1/co/health` 查看 `routes` 中每个分流桥的 `ok`；审计行的 `bridge` 字段记录每次调用使用的桥。

**停止：** 进程停止时会等待正在执行的请求：uvicorn 使用 `--timeout-graceful-shutdown 100`，compose 的 `stop_grace_period` 也是 100 秒；桥对普通请求最多等 75 秒，足够一次请求完成。强行终止进程时，已送出的写请求结果未知，调用方看到连接断开。

长时操作例外（`ia/post`、`ia/period_end`，以及 `module` 为 `ia` 或 `through` 为 `true` 的 `periods/close`）：桥对它们等待 `iaCommandSeconds` + 60 秒（缺省 960 秒），API 读桥使用 `U8CO_BRIDGE_LONG_TIMEOUT`（缺省 1000 秒）。要让停机等这类请求完成，把 `U8CO_SHUTDOWN_GRACE`（缺省 100 秒）和 compose 的 `stop_grace_period` 一并调到不小于 `U8CO_BRIDGE_LONG_TIMEOUT`；否则停服务前先确认没有这类请求在执行。API 前面有反向代理或负载均衡时，这几条路径的读超时也要大于 `U8CO_BRIDGE_LONG_TIMEOUT`。

**启动检查：**

- 环境变量格式错误（例如账套不是三位数字）、显式指定的信任文件不存在、信任文件中有未知键或重复的（`issuer`、`audience`）：进程启动失败。
- 设置了 `U8CO_BRIDGE_ROUTES_FILE` 而文件不可读、不合法，或其中的地址、密钥文件、账套有问题：进程启动失败。
- `U8CO_READONLY_ACCOUNTS` 中的账套不在 `U8CO_ACCOUNTS` 中：进程启动失败。
- 桥地址或密钥文件未配置、读不到、权限过宽或不合法：进程照常启动，`/healthz` 返回 `"configured": false`，`/v1/co/*` 返回 503 `unavailable`。
- `U8CO_BRIDGE_TIMEOUT`、`U8CO_BRIDGE_LONG_TIMEOUT`、`U8CO_CONCURRENCY`、`U8CO_CALLER_CONCURRENCY`、`U8CO_RPM` 不是整数时按 0 处理，同样视为未配置：进程照常启动，`/v1/co/*` 返回 503 `unavailable`，启动日志列出这些变量名。

### 3.4 OIDC

API 服务离线校验 Bearer JWT：

- 令牌的 `iss` 和 `aud` 必须恰好命中信任配置中的一项（[配置参考](configuration.md)「OIDC 信任配置」）。
- 头部必须有 `kid`。算法必须在该项的 `algorithms` 中（缺省只有 RS256；支持 RS、PS、ES 系列，永远不接受 HS），密钥类型须与算法一致（JWK 带 `alg` 时也须一致）。
- 公钥取自该项的 `jwks_url`；未配置时读取 `{issuer}/.well-known/openid-configuration`，要求其中的 `issuer` 与配置一致，再使用其 `jwks_uri`。这两个地址都必须是 https（本机回环地址，或该项显式写了 `allow_insecure_http: true` 时才允许 http），重定向只跟随 https 到 https。
- 检查 `exp` / `nbf`，时钟偏差为 `U8CO_JWT_LEEWAY`（缺省 60 秒）；`exp`、`iss`、`aud` 必须存在。
- 调用方在审计中的标识依次取 `azp`、`client_id`、`sub`；限流键为信任项的 `name` 加该标识，同一发行者下的多个客户端分别计数。

权限（每个信任项可分别设置）：

| 方式 | 写权限 | 只读权限 |
| --- | --- | --- |
| 布尔声明（缺省） | `u8co_write: true` | `u8co_read: true` |
| scope | `scope` / `scp` 含 `write_scope` | `scope` / `scp` 含 `read_scope` |

声明值必须是 JSON 布尔 `true`，字符串 `"true"` 不算。只读权限只能调用读路由。经营管理查询和公司间接口另需经营管理权限（`mgmt_claim` 或 `mgmt_scope`），`perm/evaluate` 另需信任项写明 `perm_evaluate: true`（[接口参考](api-reference.md)「API 的读写分级」）。

可选的 `accounts_claim`：设置后，令牌只能使用该声明中列出的账套（JSON 数组，或以逗号、空格分隔的字符串；缺少该声明表示一个都不能用），且账套仍须在 `U8CO_ACCOUNTS` 中。不设置时令牌本身不限制账套。

在身份提供方一侧：

1. 为调用方建一个 confidential client，开启 client credentials。
2. 受众（`aud`）设为信任项的 `audience`。
3. 用该提供方的协议映射或 scope 功能，把读写权限写进访问令牌：布尔声明或 scope。
4. 把发行者、受众（以及 JWKS 地址，提供方不支持 discovery 时）写进信任配置，重启 API 服务。

### 3.5 验证

```bash
curl -s http://127.0.0.1:8080/healthz          # 不需要令牌，不访问桥：{"ok":true,"configured":true}

TOKEN=$(curl -s -d grant_type=client_credentials -u "$CLIENT_ID:$CLIENT_SECRET" \
  https://idp.example.com/token | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/v1/co/health
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/v1/openapi.json | head -c 300
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"acc":"801","operator":"op001","password":"…","year":"2026"}' \
  http://127.0.0.1:8080/v1/co/login-check
```

实际使用时口令不要出现在命令行或 shell 历史中，此处只演示请求形状。`/docs`、`/redoc`、`/openapi.json` 均为 404；OpenAPI 文档在 `GET /v1/openapi.json`。

`/v1/co/health` 中，`replicated_writes` 显示桥的复现写入开关（第 6 节），`read_only_accounts`、`api_read_only_accounts` 分别列出桥和 API 的只读账套。

| 结果 | 原因 |
| --- | --- |
| 401 `unauthorized` | 令牌的 `iss` / `aud` 不在信任配置中，或签名、`kid`、算法、有效期不对 |
| 403 `forbidden` | 令牌中没有权限，或只有只读权限却调用了写路由 |
| 403 `account_not_allowed` | 账套不在 `U8CO_ACCOUNTS` 或令牌的 `accounts_claim` 中；由桥返回时，账套不在桥的 `allowedAccounts` 中 |
| 403 `account_read_only` | 账套在只读账套名单中，却调用了写路由 |
| 503 `unavailable` | 桥地址或密钥未配置、连不上桥，或两端密钥不一致 |
| 422 `login_failed` | U8 拒绝了登录，`message` 为 U8 原文 |

## 4. 客户端与命令行

`co/client/` 是直接调用桥的 Python 库和命令行。它自行签名、加密口令，不经过 API 服务，所以运行它的主机 IP 必须在桥的 `allowedClients` 中。适合运维、排障和集成脚本；面向业务系统时使用 API 服务。

### 4.1 安装与凭据

需要 Python 3.11 以上和 `cryptography`。在仓库根目录：

```bash
uv run --no-project --python 3.12 --with cryptography python -m co.client.u8co_client --help
```

或在已安装 `cryptography` 的环境中直接执行 `python3 -m co.client.u8co_client`。

- 桥地址没有缺省值，依次取 `--base-url`、环境变量 `U8CO_BASE_URL`、客户端配置文件（缺省 `~/.config/u8co/client.json`，可用 `U8CO_CLIENT_CONFIG` 更改）的 `base_url`。
- 共享密钥（64 位小写十六进制，与桥的 `secret.hex` 相同）依次取环境变量 `U8CO_SECRET`、`U8CO_SECRET_FILE` 指向的文件、客户端配置文件的 `secret_file`。密钥文件不能给属组和其他用户任何权限（`0600` 或 `0400`）。不接受命令行参数。见 [配置参考](configuration.md)「Python 客户端」。
- 口令只在终端提示时输入，没有 `--password`。
- 客户端经 `http.client` 调用桥，不读取 `http_proxy` 等代理变量。

### 4.2 命令

除 `health` 外，所有命令都需要 `--acc`、`--year`、`--operator`、`--date`。

| 命令 | 额外参数 |
| --- | --- |
| `health` | 无 |
| `login-check` | 无 |
| `sale-order` / `dispatch` | `--id` `--action verify\|unverify`（专用审核路由，保留；推荐用 `verify`） |
| `load` | `--type` `--id` |
| `verify` | `--type` `--id` `--action verify\|unverify`；采购发票、销售发票另可用 `--action arap_verify\|arap_unverify`（应收审核、应付审核，见 [接口参考](api-reference.md)「应收审核、应付审核（`arap_verify`、`arap_unverify`）」） |
| `create` | `--type`，以及 `--head-json` 和 `--lines-json`，或 `--file`。可新增的类型见 [接口参考](api-reference.md)「单据类型」。`stock_opening`（期初结存单）每行建一张单据，响应 `docs` 逐张列出，属复现写入（第 6 节） |
| `update` | `--type` `--id` `--file`。可修改的类型和限制见 [接口参考](api-reference.md)「修改 `vouchers/update`」 |
| `delete` | `--type` `--id` |
| `close` | `--type` `--id` `--action close\|open`，可选 `--line-ids`（逗号分隔的行主键）。类型为 `sale_order`、`purchase_order`、`purchase_requisition`（只能整单）、`production_order`、`arrival` |
| `generate` | `--type` `--id` `--file`，可选 `--source-type`。`--type` 是目标单据，`--id` 是来源单据 |
| `lock` | `--type` `--id` `--action lock\|unlock`。类型只能是 `sale_order` |
| `openings-post` | `--module pu\|ia`，可选 `--unpost`。期初记账或取消（`openings/post`）；存货核算的登录日期须在其启用年度。复现写入 |
| `openings-arap` | `--side ar\|ap --action create\|delete\|verify\|unverify`。`create` 加 `--partner`、`--amount`（负数为反方向余额）、`--account`，可选 `--department`、`--person`、`--digest`、`--currency`、`--exch-rate`；其余动作加 `--id`。应收应付期初单据（`openings/arap`）。复现写入 |
| `periods-close` | `--fiscal-year Y --period P`，加 `--module pu\|sa\|st\|ia\|ar\|ap\|gl`（可选 `--reopen` 取消结账）或 `--through`（各已启用模块逐月结到该期）。月末结账（`periods/close`）。复现写入 |
| `ia-post` | `--fiscal-year Y --period P`，可选 `--unpost`（恢复记账）、`--on-uncosted refuse\|skip`（只用于记账）。存货核算正常单据记账。复现写入 |
| `ia-period-end` | `--fiscal-year Y --period P`，可选 `--cancel`。存货核算期末处理或取消。复现写入 |
| `wf-state` / `wf-history` / `wf-submit` / `wf-withdraw` / `wf-resubmit` | `--type` `--id`。类型为质量单据 |
| `wf-tasks` | 可选 `--type` |
| `wf-approve` / `wf-abandon` | `--type` `--id`，可选 `--opinion` |
| `wf-disagree` / `wf-return` | `--type` `--id` `--opinion` |
| `gl-load` / `gl-void` / `gl-unvoid` / `gl-verify` / `gl-unverify` / `gl-sign` / `gl-unsign` / `gl-delete` | `--period` `--sign` `--no`。`gl-sign` 为出纳签字 |
| `gl-create` | `--file`：`head`（必须有 `sign`）和 2 到 200 条 `lines` |
| `gl-update` | `--period` `--sign` `--no` `--file`，整张替换 |
| `gl-list` | `--period-from` `--period-to`，可选 `--sign` `--date-from` `--date-to` `--maker` `--state all\|unaudited\|audited\|posted\|void` `--after` `--limit`（最多 200） |
| `arc-get` / `arc-delete` | `--archive` `--code`。档案种类、可写性和复合编码的写法见下文 |
| `arc-create` / `arc-update` | `--archive` `--code` `--file`。文件为 `{"fields":{EAI 标签: 值}}`，新增可再带 `template`。`arc-update` 不接受 `user_define`、`customer_inventory`（U8 不提供修改，须删除后重建） |
| `arc-list` | `--archive`，可选 `--code-prefix` `--name-like` `--changed-since` `--after` `--limit`（最多 500）；`project` 另可 `--project-class`；`exchange_rate` 另可 `--currency`（币种名称）、`--fiscal-year`（省略时取登录日期的年份）；`fa_card` 另可 `--type-code` `--dept-code` `--include-disposed` |
| `list` | `--type`，可选 `--filter KEY=VALUE`（可重复；`verified`、`closed`、`red` 取 true/false）`--keys-only` `--changed-since` `--after` `--limit`（最多 500） |
| `stock` | 可选 `--wh` `--inv` `--batch` `--after` `--limit`（最多 500） |
| `meta-fields` | `--type <类型>`（可选 `--op create\|update\|generate`，`generate` 需 `--source <来源类型>`）、`--archive <档案>`、`--gl` 三选一 |
| `search` | `--type`，可选 `--code-like` `--partner` `--dept` `--person` `--warehouse` `--maker` `--inventory` `--date-from` `--date-to` `--verified true\|false` `--closed true\|false` `--after` `--limit`（最多 200）`--define defineN=值` `--define-like defineN=值` `--define-prefix defineN=值`（表头文本自定义项，可重复，最多 4 个键） |
| `load-many` | `--type` `--ids 101,102`（1 到 20 个；经 U8 组件读取的类型最多 5 个） |
| `arc-get-many` | `--archive` `--codes C900001,C900002`（1 到 20 个） |
| `resolve` | `--item ARCHIVE=Q`（可重复，1 到 20 项，如 `--item customer=甲公司 --item inventory=示例存货X1`），可选 `--limit`（1 到 20）`--include-disabled` |
| `idem-get` | `--route`（任一写路由的桥路径，如 `/v1/vouchers/create`）`--key`，可选 `--caller`（缺省按 `direct` 查询） |
| `report-account-readiness` | 可选 `--as-of yyyy-MM-dd`。账套体检（第 7 节） |

其余命令（只读报表、附件列表等）以 `--help` 为准。

档案（`--archive`）：

- 只读：`account`、`trade_class`、`customer_address`、`fa_card`、`operator`、`role`。
- 可写（`template` 不适用）：`currency`、`voucher_sign`、`exchange_rate`（`fields` 接受 `rate`、`adjust_rate`）、`reason`（编码最长 10，`fields` 接受 `name`、`Reasontype`、`ReasonMemo`）、`customer_bank`、`vendor_bank`、`customer_contact`、`vendor_contact`。
- 其余档案（客户、供应商、存货、部门、人员、仓库及分类，`position`、`unit`、`unit_group`、`settle_style`、`rd_style`、`purchase_type`、`sale_type`、`district_class`、`aa_bank`、`project`、`user_define`、`customer_inventory` 等）可新增、修改、删除；`project` 被单据、凭证引用时桥拒绝删除。
- 复合编码：`project` 写 `<项目大类>:<项目编码>`；`customer_address`、`user_define`、`customer_inventory` 写 `<第一段>:<第二段>`；`exchange_rate` 写 `<币种>:<年度>:<期间>[:<日>]`（如 `美元:2026:1`），`arc-get` 另接受 `<币种>:<yyyy-mm-dd>`，返回该日单据会使用的汇率；银行账户、联系人写 `<客户或供应商编码>:<银行账号或联系人编码>`，新增联系人写 `<客户或供应商编码>:`，由 U8 自动编号。

完整的档案清单和字段见 [接口参考](api-reference.md)「基础档案 `archives/*`」。

`--file` 为 UTF-8 JSON：

- 新增必须同时有 `head` 和 `lines`；修改至少有其中一项，`lines` 每行带 `op`。
- 生单的 `lines` 是 `{source_line_id, quantity}`；销售出库的 `lines` 可省略（即整张发货单生成）；`head` 可省略。
- `--source-type`：`purchase_in` 可取 `purchase_order`（缺省）或 `qm_incoming_check` 等，其他目标见 [接口参考](api-reference.md)「参照生单 `vouchers/generate`」。

写命令（`create`、`update`、`delete`、`verify`、`close`、`generate`、`lock`、审批动作、凭证写操作、档案写操作、`openings-*`、`periods-close`、`ia-*`）可加 `--dry-run` 做预演：能回滚的操作真实执行后回滚，不写入，响应带 `mode`、`docs`、`warnings`。专用审核命令 `sale-order`、`dispatch` 不支持预演，改用 `verify`。

全部写命令（含两条专用审核命令）可加 `--idempotency-key <键>`（1 到 128 个可见 ASCII 字符）：超时或断线后用同一个键重发，U8 中只执行一次。`--dry-run` 与 `--idempotency-key` 不能同时使用。

`--changed-since` 填上一轮返回的 `watermark`；`--after` 填上一页的 `next`：`list` 和 `stock` 只接受整数，凭证和档案的游标是字符串，原样传入。`fields` 中值为 null 的标签不发给 U8。

成功时把响应 JSON 打印到标准输出；失败时在标准错误打印错误码和说明（桥给出出错字段和提示时一并打印，如「（字段 lines.0.cinvcode）」「提示：…」），并以非 0 退出。

### 4.3 库

```python
from co.client.u8co_client import U8CoClient, U8Call

client = U8CoClient("http://192.0.2.10:18089/u8co", secret_hex, timeout=90)
call = U8Call("801", "2026", "op001", password, "2026-01-15", 9000000001, "")
doc = client.load_voucher(call, "sale_order")
```

账套、年度、操作员、口令、日期和单据主键放在 `U8Call` 中（来源单据的 id 也放在 `U8Call.doc_id`）。

| 方法 | 作用 |
| --- | --- |
| `health`、`login_check` | 健康检查、登录检查 |
| `verify_sale_order`、`verify_dispatch` | 两条专用审核路由（保留，推荐 `vouchers/verify`） |
| `load_voucher`、`verify_voucher`、`delete_voucher` | 读取、审核、删除 |
| `create_voucher(call, VoucherDraft)` | 新增。期初结存单每行建一张单据，返回的 `docs` 逐张给出 `id`、`code`、`line`、`wh`、`inv`、`qty` |
| `update_voucher(call, VoucherEdit)` | 修改 |
| `close_voucher(call, kind, action, line_ids)` | 关闭、打开 |
| `lock_voucher(call, kind, action)` | 销售订单锁定、解锁 |
| `generate_voucher(call, VoucherGen)` | 参照生单 |
| `post_openings(call, module="pu", action="post")` | 期初记账（`action="unpost"` 取消） |
| `openings_arap(call, side, action, doc_id=None, **create)` | 应收（`side="ar"`）/ 应付（`"ap"`）期初单据：`action="create"` 时以关键字给出 `partner`、`amount`、`account`（可选 `department`、`person`、`digest`、`currency`、`exch_rate`）；`delete`、`verify`、`unverify` 只给 `doc_id`。字段组合不对时本地抛出 `ValueError` |
| `close_period(call, PeriodClose(module, fiscal_year, period, action="close", through=False))` | 月末结账（`action="reopen"` 取消；`through=True` 时 `module` 写空串）。`PeriodClose` 在 `co.client.u8co_periods` |
| `ia_post(call, IaMonth(fiscal_year, period, action, on_uncosted=""))`、`ia_period_end(call, IaMonth(fiscal_year, period, action))` | 存货核算记账（`post` / `unpost`，`on_uncosted` 为 `refuse` / `skip` 或空串）、期末处理（`run` / `cancel`）。`IaMonth` 在 `co.client.u8co_ia` |
| `workflow_state`、`workflow_history`、`workflow_tasks`、`workflow_submit`、`workflow_withdraw`、`workflow_approve`、`workflow_disagree`、`workflow_return`、`workflow_abandon`、`workflow_resubmit` | 审批流 |
| `gl_load`、`gl_list(GlQuery)`、`gl_create(GlDraft)`、`gl_update(GlKey, GlDraft)`、`gl_op` | 总账凭证。`gl_op` 的动作为 `void`、`unvoid`、`verify`、`unverify`、`sign`、`unsign`、`delete` |
| `gl_digest(GlDigestQuery)` | 总账凭证摘要：按期间返回每张凭证的指纹 |
| `arc_get`、`arc_list(ArcQuery)`、`arc_create` / `arc_update(ArcRecord)`、`arc_delete` | 基础档案 |
| `list_vouchers(VoucherQuery)`、`stock_current(StockQuery)` | 列表、现存量 |
| `meta_fields(call, FieldsTarget(kind=… \| archive=… \| gl=True, op=…, source=…))` | 字段标签 |
| `search(call, SearchQuery(kind, …))` | 单据查询；`items` 另有 `partner_name`，有下一页时把 `next` 传给 `after` |
| `load_many(call, kind, ids)`、`get_many(call, archive, codes)` | 批量读取，1 到 20 个（经 U8 组件读取的单据类型最多 5 个）；失败项为 `{id 或 code, error}` |
| `resolve(call, items, limit=None, include_disabled=False)` | 名称 → 编码，`items` 为 `[{"archive": …, "q": …}]`，1 到 20 项 |
| `idempotency_get(call, route, key, caller=None)` | 按幂等键查询首次请求的结果；`route` 写 `/v1/…` 或 `/u8co/v1/…` 均可，可以是任一写路由（`WRITE_ROUTES`） |
| `keyed(key)` | 返回带幂等键的副本：其全部写调用都带 `idempotency_key`，读调用原样发出。一个键只用于一次写操作，重试时用同一个键再建一个副本 |
| `dry()` | 返回预演用的副本：其写调用都带 `dry_run: true`，读调用原样发出，原客户端不受影响。专用审核路由和带幂等键的调用在本地拒绝（`ValueError`）。例：`client.dry().create_voucher(call, draft)` |
| `call(route, fields)` | 发送任意签名 POST。`fields` 中的 `password` 换成 `password_enc`，键顺序保持调用方给出的顺序 |

请求正文的键顺序固定：先是公共字段 `acc`、`year`、`operator`、`password_enc`、`date`，再是 `type`、`id`（或凭证键 `period`、`sign`、`no`，或档案的 `archive`、`code`），最后是 `head`、`lines`、`action`、`line_ids`、`fields`、`template`。可选字段为空时不发送。成功响应最多 8 MiB，错误响应最多 64 KiB，超出时为 `bad_response`。

### 4.4 错误

所有错误都是 `U8CoError`（`co/client/u8co_errors.py`），带 `status`、`code`、`message`、`field`（出错字段路径）、`hint`（桥给出的提示；桥未给出时为空串）和 `detail`（桥在 4xx 上给出的结构化补充，如存货核算记账拒绝时的 `uncosted`、`uncosted_total`；没有时为 `None`）；`describe()` 把它们拼成一段可读文字。常见错误码有对应子类，如 `U8CoStateMismatch`、`U8CoRejected`、`U8CoLoginFailed`、`U8CoBusy`；审批流的 `not_submitted`、`already_submitted`、`not_current_approver`、`workflow_disabled` 和 `stock_shortage` 各有子类；存货核算脚本超时（503 `ia_timeout`，已回滚）为 `U8CoIaTimeout`。未识别的错误码仍是 `U8CoError`，保留原 code 和 message。

读取超时缺省 90 秒，比桥的 75 秒长，以便先收到桥给出的结果。存货核算记账、期末处理和经过存货核算的月末结账（`module=ia` 或 `through`）改用 `long_timeout`（缺省 1000 秒，不小于 `timeout`）。

客户端自身产生的错误码：

| code | 含义 | 能否重试 |
| --- | --- | --- |
| `connect_failed` | 连不上桥，请求未送出 | 可以 |
| `outcome_unknown` | 请求已送出，但没有读到响应 | 写操作不要直接重试，先核对 |
| `bad_response` | 响应不是可解析的 JSON 或超过上限 | 视情况；写操作先核对 |

其他语言的客户端按 [架构](architecture.md) 中的桥协议实现，并用其中的测试向量对拍。

## 5. 端到端示例：新增一张销售订单

场景：给客户「甲公司」下一张销售订单，存货「示例存货」（规格 X1）一百个，含税单价 1.13。在测试账套 `801` 上依次完成名称解析、字段查询、预演、正式新增和读回。

约定：

- 请求为 `POST https://u8co.example.com/v1/co/<路由>`，带 `Authorization: Bearer <令牌>` 和 `Content-Type: application/json`。
- 每个请求体都有公共字段 `acc`、`operator`、`password`（可选 `year`、`date`），下文只在第一次写出，之后省略。经 [MCP 服务](mcp.md) 调用时由服务注入。
- 主键、单号、金额均为示意值，以你的账套为准。

### 5.1 名称解析

`archives/resolve`（读权限）把名称换成编码：

```json
{"acc": "801", "operator": "op001", "password": "…", "date": "2026-01-15",
 "items": [{"archive": "customer", "q": "甲公司"},
           {"archive": "inventory", "q": "示例存货X1"},
           {"archive": "department", "q": "示例车间"}]}
```

```json
{"ok": true, "results": [
  {"archive": "customer", "q": "甲公司", "status": "exact",
   "match": {"code": "C900001", "name": "甲公司", "match": "abbr"},
   "candidates": [{"code": "C900001", "name": "甲公司", "match": "abbr", "abbr": "甲公司"}], "more": false},
  {"archive": "inventory", "q": "示例存货X1", "status": "partial",
   "match": {"code": "A01", "name": "示例存货", "match": "contains"},
   "candidates": [{"code": "A01", "name": "示例存货", "match": "contains", "spec": "X1", "unit": "01"}], "more": false},
  {"archive": "department", "q": "示例车间", "status": "exact",
   "match": {"code": "D901", "name": "示例车间", "match": "name"},
   "candidates": [{"code": "D901", "name": "示例车间", "match": "name"}], "more": false}
]}
```

- `exact` 可直接使用。
- `partial`（这里是名称「示例存货」+ 规格「X1」的包含匹配）须先由用户确认。
- `ambiguous` 时把 `candidates` 交给用户选择，不要自行决定；`more: true` 表示候选未列全。

### 5.2 查询字段

`GET /v1/co/meta` 给出每种单据可写的字段、行数上限、字段到档案的对照（`field_refs`）和各操作的预演模式。要给人看中文名、必填项和枚举值，调用 `meta/fields`（按本账套的单据模板）：

```json
{"type": "sale_order", "op": "create"}
```

```json
{"ok": true, "type": "sale_order", "op": "create", "vt_id": 95, "card": "17", "vt_source": "card_default",
 "head": [{"name": "ccuscode", "label": "客户编码", "type": "string", "required": false, "max_length": 20},
          {"name": "cbustype", "label": "业务类型", "type": "enum", "required": true,
           "enum": [{"code": "普通销售", "name": "普通销售"}]}],
 "lines": [{"name": "iquantity", "label": "数量", "type": "decimal", "required": true}],
 "fields_revision": "…"}
```

模板要求必输的字段 `required` 为 `true`，写入时必须带上。需要避免重复下单时，先用 `vouchers/search` 按客户、存货和日期查询当天是否已有同样的订单。

### 5.3 预演

`vouchers/create` 带 `dry_run: true`。销售订单是 `rollback` 模式：桥真实调用 U8 保存，读出事务中的新单据，然后回滚。

```json
{"type": "sale_order", "dry_run": true,
 "head": {"ccuscode": "C900001", "cstcode": "01", "cdepcode": "D901", "ddate": "2026-01-15"},
 "lines": [{"cinvcode": "A01", "iquantity": 100, "itaxunitprice": 1.13}]}
```

```json
{"ok": true, "dry_run": true, "mode": "rollback", "route": "vouchers/create", "type": "sale_order", "action": "create",
 "docs": [{"type": "sale_order", "id": 9000000001, "code": "0000000001", "state": "exists",
           "head": {"id": 9000000001, "csocode": "0000000001", "ddate": "2026-01-15", "ccuscode": "C900001",
                    "cstcode": "01", "cdepcode": "D901", "cmaker": "张三", "itaxrate": 13.0},
           "lines": [{"isosid": 9000000101, "cinvcode": "A01", "iquantity": 100.0, "iunitprice": 1.0,
                      "itaxunitprice": 1.13, "imoney": 100.0, "itax": 13.0, "isum": 113.0}],
           "lines_total": 1}],
 "warnings": ["number_may_skip", "locks_held"],
 "message": "预演完成，已回滚，没有写入"}
```

把 `docs` 中的客户、存货、数量、单价和价税合计交给用户确认，再正式执行。

- 预演响应的 `head`、`lines` 是表列（列名小写、值为 JSON 数字），与 `vouchers/load` 的写法不同。程序只按 `ok`、`dry_run`、`mode`、`docs`、`warnings` 判断，`message` 和 `detail` 供人阅读。
- `number_may_skip`：预演中 U8 取过的单号、主键不退回，正式执行时拿到的号会不同。
- 预演失败即正式执行时会遇到的错误，例如 409 `u8_rejected`（`message` 为 U8 原文），或 400 `bad_request` 带 `field`（如 `lines.0.isosid` 不允许设置）。
- 预演不能带 `Idempotency-Key`。

### 5.4 正式新增

去掉 `dry_run`，加请求头 `Idempotency-Key`（每次写入一个新的随机值，重试时保持不变）：

```http
POST /v1/co/vouchers/create
Idempotency-Key: 6f1c2a0e-3b7d-4c52-9a55-0d8f3e1b2a47
```

```json
{"type": "sale_order",
 "head": {"ccuscode": "C900001", "cstcode": "01", "cdepcode": "D901", "ddate": "2026-01-15"},
 "lines": [{"cinvcode": "A01", "iquantity": 100, "itaxunitprice": 1.13}]}
```

```json
{"ok": true, "type": "sale_order", "id": 9000000002, "code": "0000000002",
 "state": {"verified": false, "verifier": "", "verified_at": ""}}
```

审核（`vouchers/verify`）、参照生单（`vouchers/generate`）等后续写入按同样的方式先预演、再带新的幂等键执行。

### 5.5 读回

用 `fields` 只取需要的列：

```http
POST /v1/co/vouchers/load?fields=lines.iSOsID,lines.cInvCode,lines.iQuantity&compact=true
```

```json
{"type": "sale_order", "id": 9000000002}
```

```json
{"ok": true, "type": "sale_order", "id": 9000000002, "code": "0000000002",
 "head": {"cSOCode": "0000000002", "cCusCode": "C900001", "dDate": "2026-01-15", "...": "..."},
 "lines": [{"iSOsID": "9000000102", "cInvCode": "A01", "iQuantity": "100"}],
 "state": {"verified": false, "verifier": "", "verified_at": ""}}
```

`vouchers/load` 的键是 U8 的列名、值是 U8 存储的字符串，空值省略；`fields` 匹配不区分大小写，只写了 `lines.` 前缀时 `head` 原样返回。最后在 U8 客户端中核对这张订单。

### 5.6 出错时

| 情况 | 处理 |
| --- | --- |
| `retryable: true`（如 429 `busy`、`rate_limited`，503 `busy_timeout`、`u8_license_full`） | 请求未执行。按 `Retry-After` 等待后原样重发，写请求沿用同一个幂等键；设置重试上限 |
| 504 `outcome_unknown`、网络中断 | 写入可能已生效，不要直接重发。用 `idempotency/get`（`{"path": "/v1/co/vouchers/create", "key": "<原键>"}`）查询首次结果：`state: "ok"` 即已成功；`in_flight` 稍后再查；`found: false` 先按业务内容核对，确认未生效后用同一个键重发 |
| 400 | 按 `field` 和 `message` 修正请求后重发（4xx 不占用幂等键） |
| 409 | 业务状态不符（`state_mismatch`、`u8_rejected`、`workflow_enabled`、`stock_shortage` 等）。先读取现状，把 `message` 交给用户，不要原样重发 |
| 同一个键、请求内容不同 | 409 `idempotency_mismatch`：恢复原内容，或确认首次未生效后换新键 |

`idempotency/get` 须使用与首次请求相同的调用方和 U8 操作员。完整的错误码与重试规则见 [接口参考](api-reference.md)「错误码」「幂等键」。

### 5.7 接入 AI 代理

API 服务运行后，可以在本机安装 MCP 服务，让 AI 客户端用同样的流程（解析 → 查字段 → 预演 → 确认 → 写入 → 核对）操作 U8：

```bash
uv tool install ./mcp                                  # 或 pipx install ./mcp
mkdir -p ~/.config/u8co
cp mcp/mcp.example.json ~/.config/u8co/mcp.json        # 填写 API 地址、令牌来源、账套和操作员
chmod 600 ~/.config/u8co/mcp-client-secret ~/.config/u8co/mcp-u8-password
u8co-mcp --check
claude mcp add u8co --scope user -- u8co-mcp
```

先使用只读令牌和 `"read_only": true`。配置、各客户端的接入方法和工具说明见 [MCP 服务](mcp.md)。

## 6. 开放更多账套

- **新增账套：** 在桥 `config.json` 的 `allowedAccounts` 和 API 的 `U8CO_ACCOUNTS` 中都加上，然后重启两端。
- **只读开放：** 只读取的账套先放进桥的 `readOnlyAccounts` 和 API 的 `U8CO_READONLY_ACCOUNTS`，再加入白名单。这些账套的写路由（含预演和带幂等键的重发）一律 403 `account_read_only`。
- **写入策略：** 开放写入的正式账套配置写入策略文件（桥 `writePolicyFile`、API `U8CO_WRITE_POLICY_FILE`），按账套、单据类型和操作逐项放行。
- **复现写入：** 结账、存货核算、期初、坏账、汇兑损益、应付票据、总账取消记账、损益结转与自定义转账等第二级写入缺省关闭：桥未设 `enableReplicatedWrites: true` 时一律 403 `feature_disabled`；开启后仍只对 `testAccounts` 中的账套开放，其他账套 403 `test_account_only`。它们按 U8 界面执行的 SQL 写入，已在测试账套上实测核对，用于测试账套；正式账套上请在 U8 客户端中完成这些操作。正式账套不要放进 `testAccounts`。

配置项见 [配置参考](configuration.md)，分级和风险见 [已知限制](limitations.md)。

## 7. 账套准备

新建或引入的账套可能缺补丁、年度、日历或基础配置，写入会因此失败。账套体检 `reports/account_readiness`（读路由，十项检查全是 `SELECT`，正式账套上也可以执行）逐项给出状态和修复提示，每项的 `docs_anchor` 指向本节对应的小节。

原则：**U8 能做的在 U8 中做。** 建立年度账、系统启用、工作日历、审批流设计、单据编号设置都有界面。修改数据库前，请 DBA 对相关库做 `COPY_ONLY` 完整备份；每做完一步再执行一次体检，确认对应项变为 `ok`。

### 7.1 执行体检

体检需要登录 U8，登录日期须落在账套已建的年度内。新账套只有启用年度时，用启用年度的最后一天登录：

```bash
$CLI report-account-readiness --acc 801 --year 2026 --operator op001 --date 2026-12-31
```

或经 API `POST /v1/co/reports/account_readiness`，请求体只有公共字段，可选 `as_of`（`yyyy-MM-dd`，检查年度和日历时使用的日期，缺省为登录日期）。功能权限：账套主管，或有总账「结账」`GL1512`、「凭证整理」`GL0202` 的操作员。响应：

```json
{"ok": true, "overall": "fail", "as_of": "2026-12-31", "server_today": "2026-06-15", "acc": "801",
 "checks": [{"id": "calendar", "title": "工作日历", "status": "fail",
             "detail": "…", "fix_hint": "…", "docs_anchor": "getting-started.md#calendar", "safe": true}]}
```

`status` 为 `ok`、`warn`、`fail`、`unknown` 之一；`overall` 取最差的一项（`fail` > `unknown` > `warn` > `ok`）。`unknown` 多半是桥使用的 SQL 登录读不了 `UFSystem`：授予其 `UFSystem` 的 `SELECT` 权限。字段说明见 [接口参考](api-reference.md)「账套体检」。

| 检查项 | 小节 |
| --- | --- |
| `patches` | [补丁](#patches) |
| `years` | [年度](#years) |
| `prior_gl_close` | [之前年度的总账结账](#prior_gl_close) |
| `calendar` | [工作日历](#calendar) |
| `yearly_config` | [年度配置](#yearly_config) |
| `modules` | [系统启用](#modules) |
| `workflow` | [审批流和人员](#workflow) |
| `pu_opening` | [采购期初](#pu_opening) |
| `vendor_extradefine` | [供应商扩展自定义项表](#vendor_extradefine) |
| `defaults` | [缺省档案和编号规则](#defaults) |

<a id="patches"></a>
### 补丁（`patches`）

在 U8 打过补丁之后才新建或引入的账套，没有经过补丁的数据库升级：缺 `UA_PatchList`，也缺补丁增加的列（如 `WFAudit.signatureid`），业务组件会报「列名无效」。

**处理：** 在数据库服务器上以 32 位运行 `<U8 安装目录>\Admin\UFDBTMP\DBEnginSys.exe -all`，在「获取账套」中先全部取消，只勾选该账套的数据库。它同时会重跑 UFSystem 和工作流库的补丁脚本（可重复执行）。

**判定：** 账套库没有 `UA_PatchList`，或系统库有记录而账套库一条都没有时 `fail`；缺 `signatureid` 列只 `warn`（旁证）；账套库条数少于系统库也只 `warn`（系统库还记录了只改系统库的补丁）。

<a id="years"></a>
### 年度（`years`）

U8 登录按日期查找 `UFSystem..UA_Period`。账套只有启用年度时，用今天的日期登录会报「不存在的年度」（422 `login_failed`）。体检要求 `as_of` 和数据库服务器的今天都落在已建年度内。

**处理：** 先对齐年度配置（[年度配置](#yearly_config)），再在系统管理 → 年度账 → 建立中，从启用年度的下一年起逐年建到今年。建立年度会从上一年度复制科目、科目编码方案和年度选项。

完成后用今天的日期 `login-check` 应当成功，之后体检可以用今天的日期执行。

<a id="prior_gl_close"></a>
### 之前年度的总账结账（`prior_gl_close`）

建立新年度后，之前年度的总账月份如果没有结账，今年的凭证不能记账（`gl/vouchers/post` 拒绝）。只有启用年度且各月都未结时为 `ok`；有后续年度而之前年度仍有未结月份时为 `fail`。

**处理：** 在 U8 中按顺序（采购、销售 → 库存 → 存货核算 → 应收、应付 → 总账）把之前的月份逐月结账。测试账套在开启复现写入后也可以用 `periods/close`（`{"action":"close","through":true,"fiscal_year":<本年>,"period":<上月>}`）一次结到上月；条件和限制见 [接口参考](api-reference.md)「月末结账 `periods/close`」。

<a id="calendar"></a>
### 工作日历（`calendar`）

新账套的系统工作日历只到建账时预置的日期为止。日历用完后，按工作日排期的单据（如生产订单）出错。日历末日早于 `as_of` 或今天时 `fail`，不足 30 天时 `warn`。

**处理：** 基础档案 → 工作日历，延长 SYSTEM 日历。

<a id="yearly_config"></a>
### 年度配置（`yearly_config`）

按年度保存的配置（现金流量取数、应收应付的基本 / 对方 / 受控 / 结算方式科目、科目和科目编码级次、年度选项）在后续年度缺失时，写入会使用错误的缺省值或失败。启用年度有、后续某年度没有时：该年度不晚于 `as_of` 和服务器当年的 `fail`，更晚的年度只 `warn`。

**处理：** 在 U8 中逐项设置：基础设置中的科目和编码方案，应收款管理、应付款管理的科目设置，总账的现金流量取数设置。先在启用年度设好，再建立后续年度（建立时会复制）；已经建好的年度逐年补齐。

<a id="modules"></a>
### 系统启用（`modules`）

需要启用销售、采购、库存、应收、应付、总账、质量、生产订单、物料清单（`SA`、`PU`、`ST`、`AR`、`AP`、`GL`、`QM`、`MO`、`BO`）；存货核算（`IA`）可选，未启用时 `warn`。

**处理：** 以账套主管登录企业应用平台 → 基础设置 → 基本信息 → 系统启用，逐个勾选并填写启用日期（通常为启用年度的 1 月 1 日）。建账向导的最后一步也可以直接启用。API 没有对应的路由。

只使用部分模块时，未启用模块对应的单据和操作不可用，可忽略本项。

<a id="workflow"></a>
### 审批流和人员（`workflow`）

质量单据的审批要求来料检验单（QM03）和产品检验单（QM04）的审批流已发布并启用，且调用所用的操作员已关联到一个人员。

**处理：** 在 U8 审批流设计器中为两种检验单设计并发布审批流；在 U8 中把操作员关联到人员档案。

<a id="pu_opening"></a>
### 采购期初（`pu_opening`）

启用采购管理后，U8 要求先完成采购期初记账才能保存采购发票。体检查看采购启用年度 `GL_mend` 第 0 期的 `bflag_PU`。

**处理：** 在 U8 采购管理中执行期初记账。测试账套在开启复现写入后也可以调用 `openings/post`（`{"module":"pu","action":"post"}`，或命令行 `openings-post --module pu`）。

<a id="vendor_extradefine"></a>
### 供应商扩展自定义项表（`vendor_extradefine`）

部分建账模板建出的账套没有 `Vendor_extradefine` 表，新增供应商时失败。

**处理：** 在 U8 基础档案 → 自定义项中为供应商增加一个扩展自定义项，U8 会建出这张表。

<a id="defaults"></a>
### 缺省档案和编号规则（`defaults`）

要求：有本位币（缺失时 `fail`）；有缺省采购类型、末级的入库和出库收发类别、单据编号规则（缺失时 `warn`）。

**处理：**

- 币种、采购类型、收发类别：在 U8 基础档案中设置，或用档案接口 `archives/create`（`currency`、`purchase_type`、`rd_style`）。
- 编号规则：在 U8 单据编号设置中设置。

全部处理完后，体检 `overall` 应为 `ok`（`calendar` 临近到期时可以是 `warn`）。体检只读、不扫描单据表，几秒内返回，适合放进日常巡检。

## 8. 升级与卸载

### 8.1 升级桥

1. 在服务器上取得新版本，执行 `build.ps1`、`--selftest`。U8 升级或打补丁后，先执行一次 `--check-signatures`（2.5）。
2. 备份 `<root>\bin\u8co-bridge.exe` 和 `u8co-bridge.exe.config`（例如复制为同目录的 `.prev`）。
3. 重新执行 `install.ps1`（`-Root` 和 `-ServiceName` 与原安装相同）。它会停止服务、替换程序、检查配置、启动并做健康检查，同时改写服务的 binPath。`config.json`、`secret.hex`、`sql.json` 和日志保持不变。
4. 查看 [变更日志](../CHANGELOG.md) 中有无新的配置键或行为变化。
5. 核对防火墙入站规则：脚本只管理规则名等于服务名、分组为 `U8Co` 的规则；名称不同的其他规则不会被接管或删除，仍按原端口和远程地址放行。用 `Get-NetFirewallRule -Direction Inbound | Where-Object { $_.Group -ne 'U8Co' }` 查找，确认后 `Remove-NetFirewallRule -Name '<规则名>'`。

健康检查失败时回滚：停止服务，把两份 `.prev` 复制回去，启动服务。

停止服务最多约 90 秒：桥在 `OnStop` 中等待工作线程 65 秒，仍有 COM 调用未返回时在 `unhandled.log` 记一行并自行结束进程，90 秒兜底同样如此（[架构](architecture.md)「超时和看门狗」）。服务已显示「已停止」而替换程序时仍提示文件被占用，说明旧进程尚未退出：用 `Get-CimInstance Win32_Process` 找到 `ExecutablePath` 正好是 `<root>\bin\u8co-bridge.exe` 的进程，按进程号结束，确认退出后再替换文件、启动服务。不要按进程名结束（同一台机器上可能有其他运行目录的桥）。

### 8.2 升级 API 服务

拉取新版本，重新构建镜像或执行 `uv sync --frozen`，重启服务。启动后检查 `/healthz` 的 `configured` 为 `true`，`/v1/co/health` 中各桥 `ok`。

### 8.3 卸载桥

```powershell
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1
```

`-Root`、`-ServiceName` 须与安装时相同。脚本停止并删除服务、`bin\` 中的程序和配置、urlacl、防火墙规则（只删除分组为 `U8Co` 的同名规则），日志写在 `results\u8co-uninstall-*.log`。`config.json`、`secret.hex`、`sql.json` 和审计日志保留，以免重新安装后调用方的密钥对不上。

安全检查与安装相同：读取 `config.json` 前核对 `<root>` 的上级目录和其中对象的所有者，不满足时停止（按不可信的 `listenPrefix` 删除 urlacl 可能删掉其他程序的保留）；`<root>` 中有对象的所有者不是 SYSTEM 或 Administrators 时停止，错误信息给出核对后把所有者改回 Administrators 的 `icacls` 命令；同名服务的可执行文件不是 `<root>\bin\u8co-bridge.exe` 时停止，不删除。

确认不再使用、审计日志已经转存之后，可以手工删除整个运行目录。只删除解析后仍是该目录的路径：

```powershell
$expected = Join-Path $env:ProgramData 'U8Co\u8co'
$root = [System.IO.Path]::GetFullPath($expected)
if ($root -ne $expected) { throw '拒绝删除' }
Remove-Item -LiteralPath $root -Recurse -Force
```

卸载不会冲销业务数据。已在 U8 中审核、新增或删除的单据，须在 U8 客户端中处理。
