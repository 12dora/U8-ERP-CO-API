# u8co-events 镜像。构建上下文是仓库根目录（要带上 co/client 做签名和口令加密）：
#   docker build -f events/Dockerfile -t u8co-events .
# 需要代理时用 --build-arg HTTP_PROXY=... 临时传入（Docker 预定义参数，不会留在镜像里）。
# 排除规则见 events/Dockerfile.dockerignore（BuildKit 优先用它，而不是根目录的 .dockerignore）。
# 基础镜像按摘要固定（多架构索引摘要）。升级时用 docker buildx imagetools inspect <镜像> 查新摘要。
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.12.9@sha256:8b940d3a9d65bed080436972241af2e21c84b5e8c9193f7014ed71479ee795ff /uv /uvx /bin/

WORKDIR /app

COPY events/pyproject.toml events/uv.lock ./
COPY events/u8co_events ./u8co_events

RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH" \
    U8CO_EVENTS_CONFIG=/etc/u8co-events/config.json

WORKDIR /app

# 状态库目录归运行用户、0700。命名卷第一次挂载时会沿用这里的属主和权限。
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin u8co \
    && mkdir -p /var/lib/u8co-events \
    && chown 10001:10001 /var/lib/u8co-events \
    && chmod 0700 /var/lib/u8co-events

COPY --from=build /app/.venv /app/.venv
# 运行时 import co.client.u8co_client（签名、口令加密、vouchers/list）。
COPY co/__init__.py /app/co/__init__.py
COPY co/client /app/co/client

# SQLite 状态库（水位、快照、出箱）。必须是持久卷：丢了它就会重新回填、丢掉未发出的事件。
VOLUME ["/var/lib/u8co-events"]

USER 10001

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["u8co-events", "healthcheck"]

# 停机：SIGTERM 后等在途的桥调用结束（最长 bridge.timeout_seconds + 10 秒）。
CMD ["u8co-events", "run"]
