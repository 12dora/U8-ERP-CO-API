"""状态告警：给健康检查和启动检查用的纯判断，不连桥、不连 Redis。

- type_problems：某个（账套, 类型）的删除扫描失败、扫描过期、水位倒退；
- reseed_problem：状态库是新的但 Redis 里已经有事件流（多半是状态卷丢了），拒绝悄悄重新回填。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from u8co_events.config import Config
from u8co_events.state import Store, TypeStatus

# 扫描超过间隔的这么多倍还没成功，算过期。
SCAN_STALE_FACTOR = 3
# 水位倒退后告警保留多久（之后只在 status 里能看到记录）。
RESET_ALERT = timedelta(hours=24)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        got = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return got if got.tzinfo else got.replace(tzinfo=UTC)


def _scan_stale(st: TypeStatus, now: datetime, cfg: Config) -> bool:
    if cfg.delete_scan_minutes <= 0 or st.watermark is None:
        return False
    last = _parse(st.last_scan_at)
    if last is None:
        # 首次扫描紧跟着首轮回填；没扫成时 scan_error 会有值，这里不重复报。
        return False
    limit = timedelta(minutes=cfg.delete_scan_minutes * SCAN_STALE_FACTOR, seconds=cfg.poll_interval_seconds)
    return now - last > limit


def type_problems(st: TypeStatus, now: datetime, cfg: Config) -> list[str]:
    """一个（账套, 类型）需要人看的问题。增量轮询的延迟由 health 自己判断，这里不重复。"""
    name = f"{st.account}/{st.type}"
    problems: list[str] = []
    if st.scan_error:
        problems.append(f"{name} 删除扫描失败：{st.scan_error}")
    if _scan_stale(st, now, cfg):
        problems.append(f"{name} 删除扫描超过 {cfg.delete_scan_minutes * SCAN_STALE_FACTOR:.0f} 分钟没有成功")
    reset_at = _parse(st.watermark_reset_at)
    if reset_at is not None and now - reset_at < RESET_ALERT:
        problems.append(
            f"{name} 水位倒退（{st.watermark_reset}），已整轮重新对比；请确认 U8 库是否被还原或凭证指向了别的库"
        )
    return problems


def reseed_problem(cfg: Config, store: Store, stream_exists: Callable[[str], bool]) -> str | None:
    """启动前调用。返回 None 表示可以启动，否则返回拒绝启动的原因。

    stream_exists(name) 由发布端提供（Redis 用 EXISTS）；stdout 发布端传 lambda _: False。
    """
    if cfg.allow_reseed or not store.is_fresh():
        return None
    names = [cfg.redis.stream_prefix + account.acc for account in cfg.accounts]
    existing = [name for name in names if stream_exists(name)]
    if not existing:
        return None
    return (
        f"状态库是新的，但 Redis 里已经有事件流 {', '.join(existing)}。多半是状态卷丢了：直接启动会从现在重新回填，"
        "丢掉上次水位之后的变化和发件箱里没发出的事件。确认接受后在配置里设 allow_reseed=true 再启动"
    )
