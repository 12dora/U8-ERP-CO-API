# co

U8 服务器上的桥，以及直接调用它的 Python 客户端。

| 目录 | 作用 | 文档 |
| --- | --- | --- |
| `bridge/` | 32 位 Windows 服务，进程内调用 U8 业务组件 | [`bridge/README.md`](bridge/README.md)、[`docs/getting-started.md`](../docs/getting-started.md) |
| `client/` | Python 客户端库和命令行，直接签名调用桥 | [`docs/getting-started.md`](../docs/getting-started.md)、[`docs/configuration.md`](../docs/configuration.md) |
| `SafePath.ps1` | 安装、卸载脚本共用的路径断言 | |

## client

| 文件 | 作用 |
| --- | --- |
| `u8co_client.py` | 客户端和命令行入口：`python3 -m co.client.u8co_client` |
| `u8co_cli*.py` | 命令行子命令 |
| `u8co_kinds.py` | 单据类型名单：读取、新建、删除、修改、关闭、生单 |
| 其余 `u8co_*.py` | 按业务域划分的库方法（总账凭证、基础档案、单据列表、现存量、应收应付、票据、期初、结账、存货核算、报表、元数据、批量读取等），混入 `U8CoClient` |
| `u8co_idem.py` | 幂等键参数 `--idempotency-key` |
| `u8co_errors.py` | 错误类，以及按 `code` 解析桥的 JSON |
| `u8co_settings.py` | 桥地址和密钥的来源：命令行、环境变量、`~/.config/u8co/client.json` |
| `tests/` | 协议测试向量、正文和命令行解析，不访问 U8 |

口令只从终端读取，不接受命令行参数。客户端直接调用桥，绕过 API 服务的令牌、限流和审计，只应在受信任的运维主机上使用，且该主机的 IP 须在桥的 `allowedClients` 中。签名和口令加密协议见 [`docs/architecture.md`](../docs/architecture.md)「桥协议」。

单元测试（仓库根目录）：

```bash
uv run --no-project --python 3.12 --with cryptography python -m unittest discover -s co/client/tests -t .
```
