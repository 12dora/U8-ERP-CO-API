# api

U8+ CO 接口的 HTTP 服务（`u8co-api`，FastAPI）：对外发布 `/v1/co/*` 和 OpenAPI 文档，校验 OIDC 令牌、读写分级、账套白名单和限流，再签名调用 U8 服务器上的桥。

- 部署与运行：[`docs/getting-started.md`](../docs/getting-started.md) 第 3 节；环境变量和信任配置：[`docs/configuration.md`](../docs/configuration.md)「API 服务」。
- 样例：`.env.example`（环境变量）、`trust.example.json`（OIDC 信任配置）、`compose.example.yml`、`Dockerfile`。
- 路由与字段：[`docs/api-reference.md`](../docs/api-reference.md)。

单元测试（不连接桥或 U8）：

```bash
cd api && uv run pytest -n auto
```
