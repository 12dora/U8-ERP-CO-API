"""耗时：用例、编排阶段和步骤的计时。时钟走本模块的 monotonic / wall，单测可以换成假时钟。"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

# 编排阶段：键写进 report.json 的 phases，标签用在 report.md。
PHASE_LABELS = {
    "prep": "准备（预检、指纹、快照、看门狗）",
    "deploy": "编译部署",
    "cases": "用例",
    "revert": "还原与复核指纹",
}


def monotonic() -> float:
    return time.monotonic()


def wall() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def elapsed_ms(started: float) -> int:
    return max(0, int(round((monotonic() - started) * 1000)))


@contextmanager
def phase(ctx: object, name: str) -> Iterator[None]:
    """记一个编排阶段的起止时刻和毫秒数，出异常也记（finally）。ctx 没有 phases 时什么也不做。"""
    phases = getattr(ctx, "phases", None)
    start_wall = wall()
    started = monotonic()
    try:
        yield
    finally:
        if isinstance(phases, dict):
            phases[name] = {"start": start_wall, "end": wall(), "ms": elapsed_ms(started)}


def family(case: str) -> str:
    """用例族：用例名第一个 - 之前的部分（sale-out-partial → sale）。"""
    text = str(case or "").strip()
    if not text:
        return "（未知）"
    return text.split("-", 1)[0]


def fmt_ms(value: object) -> str:
    if not isinstance(value, int) or isinstance(value, bool):
        return ""
    return f"{value / 1000:.1f} 秒"


def _ms_of(item: dict) -> int | None:
    value = item.get("duration_ms")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def slowest(tests: list[dict], limit: int = 15) -> list[dict]:
    timed = [item for item in tests if _ms_of(item) is not None]
    timed.sort(key=lambda item: -(_ms_of(item) or 0))
    return timed[:limit]


def by_family(tests: list[dict]) -> list[tuple[str, int, int]]:
    """[(族, 条数, 毫秒合计)]，按合计从大到小。没有计时的用例不计。"""
    totals: dict[str, list[int]] = {}
    for item in tests:
        ms = _ms_of(item)
        if ms is None:
            continue
        slot = totals.setdefault(family(str(item.get("case") or "")), [0, 0])
        slot[0] += 1
        slot[1] += ms
    rows = [(name, count, ms) for name, (count, ms) in totals.items()]
    rows.sort(key=lambda row: (-row[2], row[0]))
    return rows


def phase_ms(phases: object) -> list[tuple[str, int]]:
    if not isinstance(phases, dict):
        return []
    rows = []
    for name, item in phases.items():
        ms = item.get("ms") if isinstance(item, dict) else None
        if isinstance(ms, int) and not isinstance(ms, bool):
            rows.append((str(name), ms))
    return rows


def _cell(value: object) -> str:
    return str(value or "").replace("|", "/").replace("\n", " ")


def _phase_lines(phases: object) -> list[str]:
    rows = phase_ms(phases)
    if not rows:
        return []
    total = sum(ms for _, ms in rows)
    parts = [f"{PHASE_LABELS.get(name, name)} {fmt_ms(ms)}" for name, ms in rows]
    return [f"- 总计（各阶段相加）：{fmt_ms(total)}", "- 阶段：" + "；".join(parts)]


def _slow_lines(tests: list[dict]) -> list[str]:
    top = slowest(tests)
    if not top:
        return []
    lines = ["", f"### 最慢的 {len(top)} 条用例", "| 用例 | 用例名 | 耗时 |", "| --- | --- | --- |"]
    for item in top:
        lines.append(f"| {_cell(item.get('name'))} | {_cell(item.get('case'))} | {fmt_ms(_ms_of(item))} |")
    return lines


def _family_lines(tests: list[dict]) -> list[str]:
    rows = by_family(tests)
    if not rows:
        return []
    lines = ["", "### 按用例族（用例名第一个 - 之前）", "| 用例族 | 条数 | 耗时合计 |", "| --- | --- | --- |"]
    lines.extend(f"| {_cell(name)} | {count} | {fmt_ms(ms)} |" for name, count, ms in rows)
    return lines


def _note_lines(steps: object) -> list[str]:
    if not isinstance(steps, list):
        return []
    return [f"- {_cell(item.get('note'))}" for item in steps if isinstance(item, dict) and item.get("note")]


def timing_section(report: dict) -> list[str]:
    """report.md 的「耗时」一节：总计、各阶段、用例合计、最慢 15 条、按用例族合计。什么都没计到时不出这一节。"""
    tests = [item for item in report.get("tests") or [] if isinstance(item, dict)]
    timed = [ms for ms in (_ms_of(item) for item in tests) if ms is not None]
    lines = _phase_lines(report.get("phases"))
    if timed:
        lines.append(f"- 用例合计：{len(timed)} 条，{fmt_ms(sum(timed))}")
    lines.extend(_note_lines(report.get("steps")))
    lines.extend(_slow_lines(tests))
    lines.extend(_family_lines(tests))
    if not lines:
        return []
    return ["", "## 耗时", *lines]
