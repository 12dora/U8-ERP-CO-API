"""命令行入口：u8co-events run | check | status | healthcheck。

run：一个轮询线程（写出箱）+ 一个发布线程（读出箱发 Redis）+ 可选的健康检查 HTTP。
每个线程各开一个 Store（SQLite 连接不跨线程）。任一线程异常退出就整体停下、非 0 退出，交给容器重启；
出箱和水位都在 SQLite 里，重启后接着发、接着轮询，不丢事件。

状态库是空的、Redis 里却已经有这些账套的 stream，多半是状态卷丢了：这时照常启动会悄悄重新回填，
丢掉上次水位之后的变化和没发出的事件。所以拒绝启动，除非 run --init 或配置 allow_reseed=true。
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sqlite3
import sys
import threading
from collections.abc import Callable, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

from u8co_events import bridge, config, health
from u8co_events.config import Config, ConfigError
from u8co_events.outbox import OutboxLoop
from u8co_events.poller import Poller
from u8co_events.publisher import Publisher, RedisStreamsPublisher, RedisUnavailable, build_publisher, describe
from u8co_events.readonly import ReadOnlyState
from u8co_events.source import SOURCE_MODULES, default_factory
from u8co_events.state import Store

log = logging.getLogger("u8co_events")

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2
EXIT_REDIS_DOWN = 3
EXIT_RESEED = 4


class ReseedRefused(RuntimeError):
    """状态库是空的，但 Redis 里已有 stream。"""


class Runner:
    """run 子命令的线程编排。"""

    def __init__(self, cfg: Config, lister: Any, publisher: Publisher) -> None:
        self.cfg = cfg
        self.lister = lister
        self.publisher = publisher
        self.stop = threading.Event()
        self.loop: OutboxLoop | None = None
        self.failed: list[str] = []
        self.threads: dict[str, threading.Thread] = {}

    def _guard(self, name: str, body: Callable[[], None]) -> None:
        try:
            body()
        except BaseException:
            log.exception("线程 %s 异常退出，停止服务", name)
            self.failed.append(name)
            self.stop.set()

    def _poll(self) -> None:
        with closing(Store(self.cfg.state_path)) as store:
            Poller(self.cfg, store, self.lister).run_forever(self.stop)

    def _publish(self) -> None:
        pub = self.cfg.publisher
        with closing(Store(self.cfg.state_path)) as store:
            self.loop = OutboxLoop(store, self.publisher, pub.batch, pub.idle_seconds)
            self.loop.run_forever(self.stop)

    def outbox_problems(self) -> list[str]:
        loop = self.loop
        return [] if loop is None else loop.problems()

    def monitor(self) -> health.Monitor:
        return health.Monitor(self.cfg, self.threads, checks=[self.outbox_problems], notes=[self.publisher.warnings])

    def start(self) -> None:
        for name, body in (("outbox", self._publish), ("poller", self._poll)):
            th = threading.Thread(target=self._guard, args=(name, body), name=name, daemon=True)
            self.threads[name] = th
            th.start()

    def wait(self) -> int:
        while not self.stop.wait(1.0):
            if any(not th.is_alive() for th in self.threads.values()):
                self.stop.set()
        for th in self.threads.values():
            # 轮询线程可能正卡在一次桥调用里，最多等桥超时再多 10 秒。
            th.join(self.cfg.bridge.timeout_seconds + 10)
        self.publisher.close()
        return EXIT_FAIL if self.failed else EXIT_OK


def guard_reseed(cfg: Config, publisher: Publisher, init: bool) -> None:
    """状态库为空且目标 stream 已存在时拒绝启动（见模块说明）。"""
    with closing(Store(cfg.state_path)) as store:
        fresh = store.is_fresh()
    if not fresh or not isinstance(publisher, RedisStreamsPublisher):
        return
    existing = publisher.existing_streams([acc.acc for acc in cfg.accounts])
    if not existing:
        return
    if init or cfg.allow_reseed:
        log.warning("状态库是空的，Redis 里已有 %s；按 --init / allow_reseed 重新回填", ", ".join(existing))
        return
    raise ReseedRefused(
        f"状态库 {cfg.state_path} 是空的，但 Redis 里已有 {', '.join(existing)}：多半是状态卷丢了。"
        "照常启动会重新回填，上次水位之后的变化和没发出的事件都会丢。"
        "确认要从现在起重新开始时，用 u8co-events run --init（或配置 allow_reseed=true）"
    )


def _prepare(cfg: Config, init: bool) -> tuple[Any, Publisher]:
    """启动前检查：状态库可写、数据源模块、密钥和操作员文件、Redis 持久化、状态卷是否丢失。"""
    with closing(Store(cfg.state_path)):
        pass
    # 配置里开了、但还没有实现模块的数据源：启动前就报配置错误。
    for name in sorted({name for account in cfg.accounts for name in account.sources if name in SOURCE_MODULES}):
        default_factory(name)
    lister = bridge.build_lister(cfg)
    publisher = build_publisher(cfg)
    try:
        guard_reseed(cfg, publisher, init)
    except BaseException:
        publisher.close()
        raise
    return lister, publisher


def cmd_run(cfg: Config, args: argparse.Namespace) -> int:
    lister, publisher = _prepare(cfg, args.init)
    runner = Runner(cfg, lister, publisher)

    def _on_signal(signum: int, _frame: object) -> None:
        log.info("收到信号 %d，停止", signum)
        runner.stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    runner.start()
    server = None
    if cfg.health.listen:
        server = health.start_server(cfg.health.listen, runner.monitor(), cfg.state_path)
    log.info("u8co-events 已启动：%d 个账套，发布到 %s", len(cfg.accounts), cfg.publisher.kind)
    try:
        return runner.wait()
    finally:
        if server is not None:
            health.stop_server(server)


def cmd_check(cfg: Config, _args: argparse.Namespace) -> int:
    _lister, publisher = _prepare(cfg, False)
    try:
        if isinstance(publisher, RedisStreamsPublisher):
            print("redis: " + describe(publisher.durability))
    finally:
        publisher.close()
    print("配置检查通过")
    return EXIT_OK


def cmd_status(cfg: Config, _args: argparse.Namespace) -> int:
    try:
        with closing(ReadOnlyState(cfg.state_path)) as state:
            report = health.Monitor(cfg).report(state)
    except sqlite3.Error as exc:
        print(f"读不了状态库 {cfg.state_path}: {exc}", file=sys.stderr)
        return EXIT_FAIL
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_healthcheck(cfg: Config, _args: argparse.Namespace) -> int:
    return health.probe(cfg.health.listen)


COMMANDS: dict[str, Callable[[Config, argparse.Namespace], int]] = {
    "run": cmd_run,
    "check": cmd_check,
    "status": cmd_status,
    "healthcheck": cmd_healthcheck,
}


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="u8co-events", description="U8 单据事件服务")
    parser.add_argument("--config", help="配置文件（缺省取 U8CO_EVENTS_CONFIG 或 /etc/u8co-events/config.json）")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="启动轮询和发布")
    run.add_argument(
        "--init",
        action="store_true",
        help="状态库为空而 Redis 里已有 stream 时仍然启动（重新回填，之前没发出的事件会丢）",
    )
    sub.add_parser("check", help="检查配置、密钥文件、状态库、Redis 持久化和状态卷是否丢失，不轮询")
    sub.add_parser("status", help="打印每种单据的水位、延迟、删除扫描和出箱积压（JSON，只读）")
    sub.add_parser("healthcheck", help="请求本机 /healthz，健康时退出码 0（给容器 HEALTHCHECK 用）")
    return parser.parse_args(argv)


def _dispatch(args: argparse.Namespace) -> int:
    try:
        cfg = config.load(Path(args.config) if args.config else None)
        return COMMANDS[args.command](cfg, args)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except RedisUnavailable as exc:
        print(f"Redis 不可用（稍后重试或检查 redis.url）: {exc}", file=sys.stderr)
        return EXIT_REDIS_DOWN
    except ReseedRefused as exc:
        print(f"拒绝启动: {exc}", file=sys.stderr)
        return EXIT_RESEED


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    return _dispatch(args)


if __name__ == "__main__":
    sys.exit(main())
