"""把出箱里的事件发出去。发布成功（对方已落盘）才算数，失败就抛异常，出箱行留着下次重发。

落盘证明必须和写入在同一条连接上：一批 XADD 和 WAITAOF 放在同一个 pipeline 里发出，
客户端关闭自动重试（重连后单独发的 WAITAOF 在新连接上没有写入，会立即返回，证明不了什么）。
重试只由出箱循环整批重发。
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TextIO

from redis import exceptions as redis_errors

from u8co_events.config import Config, ConfigError, RedisConf, read_secret
from u8co_events.state import OutboxRow
from u8co_events.trim import Trimmer

log = logging.getLogger("u8co_events")

# WAITAOF 从 Redis 7.2 起才有。
_WAITAOF_MIN = (7, 2)
# 等本机 AOF 落盘的上限（毫秒）。appendfsync always 时应立即返回。
_WAITAOF_TIMEOUT_MS = 10000
_SOCKET_TIMEOUT = 30.0
_FSYNC_OK = ("always", "everysec")
# 连不上、超时：这类错误启动时报「Redis 不可用」，不当成配置错误。
_UNAVAILABLE = (redis_errors.ConnectionError, redis_errors.TimeoutError, OSError)


class PublishError(RuntimeError):
    """发布失败。出箱行不删，下一轮重发。"""


class DurabilityError(ConfigError):
    """Redis 没有配置成断电不丢，拒绝启动。"""


class RedisUnavailable(RuntimeError):
    """启动时连不上 Redis。不是配置错误，稍后重启即可。"""


class Publisher(Protocol):
    def publish(self, rows: Sequence[OutboxRow]) -> None:
        """按顺序发布整批。返回即表示全部已被对方持久化；任何一条失败都要抛异常。"""
        ...

    def warnings(self) -> list[str]:
        """不影响发布、但需要人看的情况（比如 stream 超长）。"""
        ...

    def close(self) -> None: ...


class StdoutPublisher:
    """开发和测试用：每个事件一行 JSON（即出箱里的 payload 原文）。"""

    def __init__(self, out: TextIO | None = None) -> None:
        self._out = out if out is not None else sys.stdout

    def publish(self, rows: Sequence[OutboxRow]) -> None:
        for row in rows:
            self._out.write(row.payload + "\n")
        self._out.flush()

    def warnings(self) -> list[str]:
        return []

    def close(self) -> None:
        return None


@dataclass(frozen=True)
class Durability:
    """启动时从 Redis 读到的持久化配置。"""

    appendonly: bool
    appendfsync: str
    version: tuple[int, int]
    maxmemory: int
    policy: str
    rewrite_nofsync: bool = False

    @property
    def waitaof(self) -> bool:
        return self.appendonly and self.version >= _WAITAOF_MIN


def stream_name(prefix: str, account: str) -> str:
    return prefix + account


def stream_fields(row: OutboxRow) -> dict[str, str]:
    """一条 Redis 消息的字段。消费方按 event_id 去重，payload 是完整事件 JSON。"""
    return {
        "event_id": row.event_id,
        "account": row.account,
        "type": row.type,
        "kind": row.kind,
        "id": str(row.doc_id),
        "payload": row.payload,
    }


class RedisStreamsPublisher:
    """每个账套一个 stream。一批用一个非事务 pipeline 发出，保持出箱顺序。

    Redis 7.2 以上，同一个 pipeline 末尾带 WAITAOF 1 0，确认本机 AOF 已 fsync 才算成功。
    XADD 不带 MAXLEN；超长时由 Trimmer 按最慢的消费组裁剪，不删未读事件。
    """

    def __init__(self, client: Any, conf: RedisConf, durability: Durability, trimmer: Trimmer | None = None) -> None:
        self._client = client
        self._prefix = conf.stream_prefix
        self.durability = durability
        self._waitaof = durability.waitaof
        self._trimmer = trimmer if trimmer is not None else Trimmer(client, conf.maxlen)

    def publish(self, rows: Sequence[OutboxRow]) -> None:
        if not rows:
            return
        streams = list(dict.fromkeys(stream_name(self._prefix, row.account) for row in rows))
        try:
            pipe = self._client.pipeline(transaction=False)
            for row in rows:
                pipe.xadd(stream_name(self._prefix, row.account), stream_fields(row))
            if self._waitaof:
                pipe.execute_command("WAITAOF", 1, 0, _WAITAOF_TIMEOUT_MS)
            replies = pipe.execute(raise_on_error=True)
        except Exception as exc:  # redis 的各种连接 / 响应错误
            raise PublishError(f"写入 Redis 失败: {type(exc).__name__}: {exc}") from exc
        if self._waitaof:
            _check_waitaof(replies[-1] if replies else None)
        self._trimmer.maybe_trim(streams)

    def warnings(self) -> list[str]:
        return self._trimmer.warnings()

    def existing_streams(self, accounts: Sequence[str]) -> list[str]:
        """这些账套的 stream 里已经存在的。"""
        names = [stream_name(self._prefix, acc) for acc in accounts]
        try:
            return [name for name in names if int(self._client.exists(name))]
        except _UNAVAILABLE as exc:
            raise RedisUnavailable(f"连不上 Redis: {type(exc).__name__}: {exc}") from exc

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # 关闭失败不影响退出
            log.debug("关闭 Redis 连接失败", exc_info=True)


def _check_waitaof(reply: object) -> None:
    try:
        local = int(reply[0])  # type: ignore[index]
    except (TypeError, ValueError, IndexError):
        raise PublishError(f"WAITAOF 的回复不对: {reply!r}") from None
    if local < 1:
        raise PublishError("Redis 在限定时间内没有确认 AOF 落盘（WAITAOF）")


def _config_value(client: Any, name: str) -> str:
    got = client.config_get(name)
    value = got.get(name) if isinstance(got, dict) else None
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return "" if value is None else str(value)


def _version(client: Any) -> tuple[int, int]:
    info = client.info("server")
    raw = str(info.get("redis_version", "0.0"))
    parts = (raw.split(".") + ["0", "0"])[:2]
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return 0, 0


def read_durability(client: Any) -> Durability:
    """读 Redis 的持久化相关配置。

    连不上抛 RedisUnavailable；CONFIG 被禁用（托管 Redis 常见）等其他错误抛 DurabilityError。
    """
    try:
        return Durability(
            appendonly=_config_value(client, "appendonly").lower() == "yes",
            appendfsync=_config_value(client, "appendfsync").lower(),
            version=_version(client),
            maxmemory=int(_config_value(client, "maxmemory") or "0"),
            policy=_config_value(client, "maxmemory-policy").lower(),
            rewrite_nofsync=_config_value(client, "no-appendfsync-on-rewrite").lower() == "yes",
        )
    except _UNAVAILABLE as exc:
        raise RedisUnavailable(f"连不上 Redis: {type(exc).__name__}: {exc}") from exc
    except Exception as exc:
        raise DurabilityError(f"读不到 Redis 的持久化配置（CONFIG GET / INFO）: {type(exc).__name__}: {exc}") from exc


def _fsync_problems(d: Durability) -> list[str]:
    if d.appendfsync not in _FSYNC_OK:
        return [f"appendfsync={d.appendfsync or '?'}：只接受 always 或 everysec（配合 WAITAOF）"]
    problems: list[str] = []
    if d.appendfsync == "everysec" and not d.waitaof:
        problems.append(
            "appendfsync=everysec 且 Redis 低于 7.2（没有 WAITAOF）：断电会丢最近约 1 秒的事件；请设 always"
        )
    if d.rewrite_nofsync and not d.waitaof:
        problems.append("no-appendfsync-on-rewrite=yes：AOF 重写期间不 fsync，断电会丢事件；请设 no")
    return problems


def durability_problems(d: Durability) -> list[str]:
    """断电可能丢事件的配置项，逐条说明。空列表表示合格。"""
    if not d.appendonly:
        problems = ["appendonly 不是 yes：Redis 重启或断电会丢掉已发布的事件"]
    else:
        problems = _fsync_problems(d)
    if d.maxmemory > 0 and d.policy != "noeviction":
        problems.append(f"maxmemory-policy={d.policy}：内存满时会淘汰 stream；请设 noeviction")
    return problems


def check_durability(client: Any, allow_nondurable: bool) -> Durability:
    """启动检查。不合格时抛 DurabilityError，除非配置了 allow_nondurable_redis。连不上抛 RedisUnavailable。"""
    try:
        d = read_durability(client)
    except DurabilityError:
        if not allow_nondurable:
            raise
        log.warning("读不到 Redis 持久化配置，按 allow_nondurable_redis=true 继续")
        return Durability(False, "", (0, 0), 0, "")
    problems = durability_problems(d)
    if problems and not allow_nondurable:
        raise DurabilityError("Redis 没有配置成断电不丢事件: " + "；".join(problems))
    for text in problems:
        log.warning("allow_nondurable_redis=true，忽略: %s", text)
    return d


def redis_client(conf: RedisConf) -> Any:
    import redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    password = read_secret(conf.password_file) if conf.password_file else None
    return redis.Redis.from_url(
        conf.url,
        password=password,
        decode_responses=True,
        socket_timeout=_SOCKET_TIMEOUT,
        socket_connect_timeout=_SOCKET_TIMEOUT,
        health_check_interval=30,
        # 不自动重试：重连后的命令不在原连接上，WAITAOF 的落盘证明会失效。重试交给出箱循环。
        retry=Retry(NoBackoff(), 0),
        retry_on_timeout=False,
    )


def build_publisher(cfg: Config, client: Any | None = None) -> Publisher:
    """按配置建发布器。kind=redis 时先做持久化检查；client 只给测试注入假 Redis。"""
    if cfg.publisher.kind == "stdout":
        return StdoutPublisher()
    if client is None:
        client = redis_client(cfg.redis)
    durability = check_durability(client, cfg.redis.allow_nondurable_redis)
    return RedisStreamsPublisher(client, cfg.redis, durability)


def describe(d: Durability) -> str:
    return json.dumps(
        {
            "appendonly": d.appendonly,
            "appendfsync": d.appendfsync,
            "version": ".".join(str(x) for x in d.version),
            "maxmemory": d.maxmemory,
            "maxmemory_policy": d.policy,
            "no_appendfsync_on_rewrite": d.rewrite_nofsync,
            "waitaof": d.waitaof,
        },
        ensure_ascii=False,
    )
