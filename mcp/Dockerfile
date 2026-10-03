# u8co-mcp 镜像（HTTP 方式，多人共用）。构建：docker build -t u8co-mcp .（在 mcp/ 目录）
# 只用 Python 标准库，没有第三方依赖，构建时不需要联网。
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    U8CO_MCP_CONFIG=/config/mcp.json

WORKDIR /app

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin u8co

COPY u8co_mcp ./u8co_mcp

USER 10001

EXPOSE 8095

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8095/healthz', timeout=3)"]

# 令牌必须是 incoming（每个请求的 Bearer 转给 API），否则不启动。改端口时同时改 HEALTHCHECK 和 EXPOSE。
CMD ["python", "-m", "u8co_mcp", "--http", "--host", "0.0.0.0", "--port", "8095"]
