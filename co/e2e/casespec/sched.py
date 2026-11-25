"""并行跑用例的调度。不连接服务器，跑什么、冲突怎么判都由调用方传进来，便于单测。

- 第 0 波串行：登记表里登录及其之前的用例（健康检查、错误签名、名单外账套、登录）。登录写进 bag 的结果
  后面的用例都要用，登录也让桥的授权缓存先热起来，后面的只读路由才走读池。
- 其余用例按登记顺序贪心：空位不到 jobs、与正在跑的用例都不冲突就开跑；冲突的先跳过，有用例跑完再从头找。
- 带 wave:2 标签（报表等只读断言）的用例等本波所有不带 wave:2 的用例跑完才开始，免得读到别人写了一半的数。
- 结果按登记顺序返回，报告与串行跑时同序。
- 某条用例抛异常（不是 FAIL，是代码出错）：不再开新用例，等其他正在跑的跑完，每条异常都带出去。
- 收到中断（Ctrl-C、SIGTERM）：置位 Rules.stop（用例在下一步之前停、桥客户端和 SQL 不再发新请求），
  不再开新用例，最多等 DRAIN_SECONDS 让正在跑的那一步结束；再按一次中断只把等待缩到 AGAIN_SECONDS，
  不会丢下线程不管。等不完的线程名单带出去，它们已经发不出桥调用。
"""

from __future__ import annotations

import importlib
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any

from co.e2e.casespec.model import CaseSpec

MAX_JOBS = 8
BARRIER = "login"
LATE = "wave:2"
# 中断后等正在跑的那一步：SQL 最长 180 秒，桥调用 90 秒。
DRAIN_SECONDS = 240.0
AGAIN_SECONDS = 30.0

Conflicts = Callable[[frozenset[str], frozenset[str], bool, bool], bool]
Bind = Callable[[CaseSpec, Mapping[str, str]], frozenset[str]]
RunOne = Callable[[CaseSpec], Any]
Exclusive = Callable[[CaseSpec], bool]
Crash = tuple[str, BaseException]


class Interrupted(KeyboardInterrupt):
    """中断后已跑完的结果（登记顺序）、期间的用例异常，以及等不完的用例名。"""

    def __init__(self, results: dict[str, Any], crashes: list[Crash] | None = None, stuck: list[str] | None = None) -> None:
        super().__init__("收到中断")
        self.results = results
        self.crashes = list(crashes or [])
        self.stuck = list(stuck or [])


class CaseCrashed(RuntimeError):
    """用例抛了异常（不是 FAIL）。串行跑时这会中止整轮，并行时等其他用例跑完再中止。crashes 是全部异常。"""

    def __init__(self, crashes: list[Crash], results: dict[str, Any]) -> None:
        name, cause = crashes[0]
        super().__init__(name + " " + type(cause).__name__ + ": " + str(cause)[:200])
        self.results = results
        self.cause = cause
        self.crashes = list(crashes)


def crash_text(name: str, error: BaseException) -> str:
    return "用例 " + name + " 异常 " + type(error).__name__ + ": " + str(error)[:200]


def all_conflict(_a: frozenset[str], _b: frozenset[str], _a_excl: bool, _b_excl: bool) -> bool:
    # 没有标签规则时一律冲突，等于串行。
    return True


def no_tags(spec: CaseSpec, _bag: Mapping[str, str]) -> frozenset[str]:
    return frozenset(spec.tags)


def own_flag(spec: CaseSpec) -> bool:
    return spec.exclusive


def default_rules() -> tuple[Conflicts, Bind, Exclusive]:
    # casespec/tags.py 由标签那一步提供；没有这个模块时退回「全部冲突」，独占只看 spec.exclusive。
    try:
        tags = importlib.import_module("co.e2e.casespec.tags")
    except ImportError:
        return all_conflict, no_tags, own_flag
    conflicts = getattr(tags, "conflicts", None)
    bind = getattr(tags, "bind_tags", None)
    if conflicts is None or bind is None:
        return all_conflict, no_tags, own_flag
    return conflicts, bind, getattr(tags, "is_exclusive", own_flag)


def split_wave0(specs: list[CaseSpec]) -> int:
    """第 0 波的条数：登录及其之前；没选登录时取开头连续几条不要求登录的用例。"""
    for index, spec in enumerate(specs):
        if spec.name == BARRIER:
            return index + 1
    count = 0
    for spec in specs:
        if spec.requires_login:
            break
        count += 1
    return count


def ordered(specs: Iterable[CaseSpec], results: Mapping[str, Any]) -> dict[str, Any]:
    return {spec.name: results[spec.name] for spec in specs if spec.name in results}


def _run_serial(specs: list[CaseSpec], run_one: RunOne, results: dict[str, Any]) -> None:
    for spec in specs:
        try:
            results[spec.name] = run_one(spec)
        except Exception as exc:
            raise CaseCrashed([(spec.name, exc)], {}) from exc


@dataclass(frozen=True)
class Rules:
    """冲突判定、标签绑定和独占判定。bag 在第 0 波之后才读（此时已有登录结果和挑单）。stop 是中断标志。"""

    conflicts: Conflicts = all_conflict
    bind: Bind = no_tags
    bag: Mapping[str, str] = field(default_factory=dict)
    exclusive: Exclusive = own_flag
    stop: threading.Event = field(default_factory=threading.Event)


class _Wave:
    """第 0 波之后的贪心并行。只在调用线程里做决定，工作线程只跑 run_one。"""

    def __init__(self, run_one: RunOne, jobs: int, rules: Rules, results: dict[str, Any]) -> None:
        self.run_one = run_one
        self.jobs = jobs
        self.rules = rules
        self.results = results
        # 本波还没跑完的、不带 wave:2 的用例数；降到 0 之前 wave:2 的用例不开。
        self.early = 0
        self.pending: list[tuple[CaseSpec, frozenset[str]]] = []
        self.running: dict[Future, tuple[CaseSpec, frozenset[str]]] = {}
        self.crashes: list[Crash] = []
        self.halt = False

    def fits(self, spec: CaseSpec, tags: frozenset[str]) -> bool:
        if len(self.running) >= self.jobs:
            return False
        if LATE in tags and self.early > 0:
            return False
        mine = self.rules.exclusive(spec)
        for other, other_tags in self.running.values():
            if self.rules.conflicts(tags, other_tags, mine, self.rules.exclusive(other)):
                return False
        return True

    def start_ready(self, pool: ThreadPoolExecutor) -> None:
        waiting = []
        for entry in self.pending:
            if self.halt or not self.fits(*entry):
                waiting.append(entry)
                continue
            future = None
            try:
                future = pool.submit(self.run_one, entry[0])
            finally:
                # 中断落在 submit 与登记之间时也要登记上，否则收尾不会等它。
                if future is not None:
                    self.running[future] = entry
                else:
                    waiting.append(entry)
        self.pending = waiting

    def collect(self, done: Iterable[Future]) -> None:
        for future in done:
            spec, tags = self.running.pop(future)
            if LATE not in tags:
                self.early -= 1
            error = future.exception()
            if error is None:
                self.results[spec.name] = future.result()
            else:
                self.crashes.append((spec.name, error))
                self.halt = True

    def drain(self) -> None:
        """中断后等正在跑的用例（有上限）。再来的中断只缩短上限，不丢下线程。"""
        deadline = time.monotonic() + DRAIN_SECONDS
        while self.running:
            left = deadline - time.monotonic()
            if left <= 0:
                return
            try:
                done, _ = wait(list(self.running), timeout=left, return_when=FIRST_COMPLETED)
            except KeyboardInterrupt:
                self.rules.stop.set()
                deadline = min(deadline, time.monotonic() + AGAIN_SECONDS)
                continue
            self.collect(done)

    def interrupted(self) -> bool:
        return any(isinstance(error, KeyboardInterrupt) for _name, error in self.crashes)

    def loop(self, pool: ThreadPoolExecutor) -> None:
        while self.pending or self.running:
            if self.interrupted():
                raise KeyboardInterrupt
            if not self.halt:
                self.start_ready(pool)
            if not self.running:
                return
            done, _ = wait(list(self.running), return_when=FIRST_COMPLETED)
            self.collect(done)


def _load(wave: _Wave, specs: list[CaseSpec], tags_of: Callable[[CaseSpec], frozenset[str]]) -> None:
    try:
        wave.pending = [(spec, tags_of(spec)) for spec in specs]
    except Exception as exc:
        raise CaseCrashed([("资源标签", exc)], {}) from exc
    wave.early = sum(1 for _spec, tags in wave.pending if LATE not in tags)


def _run_wave(wave: _Wave) -> None:
    pool = ThreadPoolExecutor(max_workers=wave.jobs, thread_name_prefix="e2e-case")
    try:
        try:
            wave.loop(pool)
        except KeyboardInterrupt:
            wave.rules.stop.set()
            wave.halt = True
            wave.drain()
            raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    if wave.interrupted() or (wave.pending and not wave.crashes):
        raise KeyboardInterrupt
    if wave.crashes:
        first = wave.crashes[0][1]
        raise CaseCrashed(wave.crashes, {}) from first


def run_waves(specs: Iterable[CaseSpec], run_one: RunOne, jobs: int, rules: Rules) -> dict[str, Any]:
    """按登记顺序返回 {用例名: run_one 的结果}。中断抛 Interrupted，用例异常抛 CaseCrashed，都带已有结果。

    jobs 为 1 时整轮串行（仍按第 0 波、其余的顺序，与登记顺序一致）。
    """
    chosen = list(specs)
    jobs = max(1, min(int(jobs), MAX_JOBS))
    head = split_wave0(chosen)
    results: dict[str, Any] = {}
    wave = _Wave(run_one, jobs, rules, results)
    try:
        _run_serial(chosen[:head], run_one, results)
        if jobs == 1:
            _run_serial(chosen[head:], run_one, results)
        else:
            _load(wave, chosen[head:], lambda spec: rules.bind(spec, rules.bag))
            _run_wave(wave)
    except KeyboardInterrupt:
        rules.stop.set()
        stuck = [spec.name for spec, _tags in wave.running.values()]
        crashes = [item for item in wave.crashes if not isinstance(item[1], KeyboardInterrupt)]
        raise Interrupted(ordered(chosen, results), crashes, stuck) from None
    except CaseCrashed as exc:
        exc.results = ordered(chosen, results)
        raise
    return ordered(chosen, results)


def run_parallel(specs: Iterable[CaseSpec], run_one: RunOne, *, jobs: int, conflicts: Conflicts, bind: Bind) -> dict[str, Any]:
    """run_waves 的简写：bind 拿到的是空 bag（单测和不依赖挑单的标签用）。回归里用 run_waves 传入真实 bag。"""
    return run_waves(specs, run_one, jobs, Rules(conflicts, bind))
