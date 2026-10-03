# u8co-api 镜像。构建：docker build -t u8co-api .
# 需要代理时用 --build-arg HTTP_PROXY=... 临时传入（Docker 预定义参数，不会留在镜像里）。
FROM python:3.12-slim AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY u8co_api ./u8co_api

RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin u8co

COPY --from=build /app/.venv /app/.venv

USER 10001

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "u8co_api.healthcheck"]

# 监听地址和端口见 U8CO_API_HOST / U8CO_API_PORT；停机宽限 100 秒，给在途的桥调用留时间。
CMD ["u8co-api"]
