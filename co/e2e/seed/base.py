"""造数步骤的公共部分：步骤定义、按 SQL 查已有编码、只新建缺的档案、把结果记进状态。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from co.e2e.flow import utc_stamp
from co.e2e.seed.context import SeedContext, SeedError
from co.e2e.seed.data import KIND_TABLE, SAFE_CODE, Arc

_EXISTS = "档案编码已存在"


@dataclass(frozen=True)
class Probe:
    existing: tuple[str, ...]
    missing: tuple[str, ...]


@dataclass(frozen=True)
class Step:
    name: str
    title: str
    probe: Callable[[SeedContext], Probe]
    # 返回（新建数，已存在数）。
    apply: Callable[[SeedContext], tuple[int, int]]


def quoted(codes: tuple[str, ...] | list[str]) -> str:
    for code in codes:
        if SAFE_CODE.fullmatch(code) is None:
            raise SeedError("编码不合法：" + code)
    return ", ".join("N'" + code + "'" for code in codes)


def existing_codes(ctx: SeedContext, kind: str, codes: tuple[str, ...] | list[str]) -> set[str]:
    # 按库的排序规则（不分大小写）查，返回请求里的写法。
    if not codes:
        return set()
    table, column = KIND_TABLE[kind]
    rows = ctx.rows(f"SELECT {column} AS code FROM {table} WHERE {column} IN ({quoted(codes)})")
    found = {row.get("code", "").strip().upper() for row in rows}
    return {code for code in codes if code.upper() in found}


def _have(ctx: SeedContext, items: tuple[Arc, ...]) -> set[tuple[str, str]]:
    kinds: dict[str, list[str]] = {}
    for arc in items:
        kinds.setdefault(arc.kind, []).append(arc.code)
    have = set()
    for kind, codes in kinds.items():
        have.update((kind, code) for code in existing_codes(ctx, kind, codes))
    return have


def probe_archives(ctx: SeedContext, items: tuple[Arc, ...]) -> Probe:
    have = _have(ctx, items)
    existing = tuple(arc.kind + ":" + arc.code for arc in items if (arc.kind, arc.code) in have)
    missing = tuple(arc.kind + ":" + arc.code for arc in items if (arc.kind, arc.code) not in have)
    return Probe(existing, missing)


def fail_text(response: dict) -> str:
    parts = [str(response.get(key)) for key in ("status", "code", "message") if response.get(key) not in (None, "")]
    for key in ("field", "hint"):
        if response.get(key):
            parts.append(key + "=" + str(response[key]))
    return " ".join(parts) or "桥没有返回 ok"


def create_archive(ctx: SeedContext, arc: Arc) -> bool:
    """新建一条档案。返回 True 表示新建，False 表示桥说编码已存在（并行或上次半途）。"""
    body: dict = {"archive": arc.kind, "code": arc.code, "fields": dict(arc.fields)}
    if arc.template:
        body["template"] = arc.template
    response = ctx.call("v1/archives/create", body)
    if response.get("ok") is True:
        return True
    if response.get("status") == 409 and _EXISTS in str(response.get("message") or ""):
        return False
    raise SeedError(arc.kind + " " + arc.code + " 新建失败：" + fail_text(response), response, ctx.last_call)


def ensure_archives(ctx: SeedContext, items: tuple[Arc, ...]) -> tuple[int, int]:
    have = _have(ctx, items)
    new = 0
    for arc in items:
        if (arc.kind, arc.code) in have:
            continue
        if create_archive(ctx, arc):
            new += 1
            remember(ctx, arc.kind, arc.code)
    return new, len(items) - new


def remember(ctx: SeedContext, kind: str, code: str) -> None:
    created = ctx.state.setdefault("created", {}).setdefault(kind, [])
    if code not in created:
        created.append(code)


def record_step(ctx: SeedContext, name: str, new: int, existed: int) -> None:
    ctx.state.setdefault("steps", {})[name] = {"new": new, "existed": existed, "at": utc_stamp()}
    ctx.save_state()


def archive_step(name: str, title: str, items: tuple[Arc, ...]) -> Step:
    return Step(name, title, lambda ctx: probe_archives(ctx, items), lambda ctx: ensure_archives(ctx, items))
