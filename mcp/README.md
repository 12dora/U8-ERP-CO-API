# u8co-mcp

用友 U8+ CO 接口（`/v1/co`）的本地 stdio MCP 服务，给 Claude Code、Claude Desktop、Cursor 等 MCP 客户端用。只用 Python 标准库（3.10+），没有第三方运行依赖。

- 工具：`u8_guide`、`u8_describe`、`u8_read`、`u8_write`、`u8_resolve`、`u8_idempotency_get`。
- 配置：`$U8CO_MCP_CONFIG` 或 `~/.config/u8co/mcp.json`，格式见 `mcp.example.json`。密钥和口令放在权限 600 的文件里（或环境变量 `U8CO_MCP_PASSWORD`），不要写进配置文件和仓库。
- `read_only: true` 时不提供写工具。
- 经营管理查询 `u8_mgmt_*`（概览、利润表、销售、往来、资金与存货）：配置 `mgmt` 段且令牌带经营管理声明时才出现。stdio 方式用配置的各账套登录；HTTP 方式不能配共用登录（配了不启动），每次调用经 `mgmt.person_proxy`（你自己运行的身份绑定服务）以调用者本人绑定的 U8 操作员执行。
- HTTP 方式（多人共用，给 AI 平台按用户接入）：`u8co-mcp --http --host 0.0.0.0 --port 8095`，令牌 `{"type": "incoming"}`、`read_only: true`（否则不启动）——每个请求的 `Authorization: Bearer` 原样放在 `X-U8co-Caller-Token` 头里转给身份绑定服务（由它验签并转给 API），只提供 `u8_guide` 和（令牌有权限时的）`u8_mgmt_*`，没有令牌回 401。样例 `mcp.http.example.json`，容器见 `Dockerfile`、`compose.example.yml`。

安装和各客户端的接入方法见 [docs/mcp.md](../docs/mcp.md)。

```bash
pip install ./mcp          # 或 uv tool install ./mcp
u8co-mcp --check           # 检查配置、令牌和连通性
```

单元测试（在仓库根目录）：

```bash
python3 -m unittest discover -s mcp/tests -t mcp
```
