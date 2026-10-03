# events

单据事件服务（`u8co-events`）：定时轮询桥的只读列表，与上一轮对比，把单据、票据、应收应付处理、总账凭证和基础档案的变化写成事件发到 Redis Streams。只读 U8，不改任何数据。

- 投递语义、事件格式、部署、配置和运维：[`docs/events.md`](../docs/events.md)。
- 样例：`config.example.json`、`compose.example.yml`、`Dockerfile`。

单元测试（假桥、假 Redis）：

```bash
cd events && uv run pytest
```
