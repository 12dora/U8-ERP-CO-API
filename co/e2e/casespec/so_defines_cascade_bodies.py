"""销售订单表头自定义项写入连带下游（voucher-so-defines 的 cascade 一轮）的 cascade 列表断言和快照比对。不连接服务器。

快照格式同 so_defines_bodies（标签见 so_defines_cascade_sql.cascade_snapshot_sql），NULL 记 ``<nil>``，行版本列不算变化。
写入后允许变的只有：订单表头请求的列和桥盖的修改人类列；结论为 updated 的下游表头的请求列。
下游表头的修改人、审核人等任何其它列都不许变。下游比较按桥的口径：去首尾空白后逐字相等。
"""

from __future__ import annotations

from co.e2e.casespec.head_fix_bodies import Snap
from co.e2e.casespec.so_defines_bodies import hits_of, is_stamp
from co.e2e.casespec.arc_cus_person_sql import NIL
from co.e2e.casespec.so_defines_cascade_sql import DOWN_TYPES

Down = tuple[str, str]


def downs_of(snap: Snap) -> list[Down]:
    """快照里的下游单据 (标签, 主键)。"""
    return sorted((tag, key) for tag in DOWN_TYPES for key in snap.get(tag, {}))


def _exact(one: object, two: object) -> bool:
    return ("" if one is None else str(one)).strip() == ("" if two is None else str(two)).strip()


def _raw(snap: Snap, tag: str, key: str, col: str) -> str | None:
    value = snap.get(tag, {}).get(key, {}).get(col, NIL)
    return None if value == NIL else value


def cascade_problem(payload: dict, before: Snap, want: dict[Down, tuple[str, str | None]], keys: tuple[str, ...]) -> str:
    """响应 cascade 列表：恰好是 want 里的单据（type、id），result / reason 与期望一致，before 的键恰好是 keys、值是快照里的原值。"""
    entries = payload.get("cascade")
    if not isinstance(entries, list):
        return "响应缺 cascade 列表"
    names = {kind: tag for tag, kind in DOWN_TYPES.items()}
    seen: dict[Down, dict] = {}
    for item in entries:
        tag = names.get(str(item.get("type"))) if isinstance(item, dict) else None
        if tag is None:
            return f"cascade 里有不认识的项 {item!r}"[:200]
        seen[(tag, str(item.get("id")))] = item
    if set(seen) != set(want):
        return f"cascade 单据 {sorted(seen)} 期望 {sorted(want)}"[:200]
    for down, item in sorted(seen.items()):
        bad = _entry_problem(item, before, down, want[down], keys)
        if bad:
            return bad
    return ""


def _entry_problem(item: dict, before: Snap, down: Down, want: tuple[str, str | None], keys: tuple[str, ...]) -> str:
    if (item.get("result"), item.get("reason")) != want:
        return f"cascade {down} result/reason={item.get('result')!r}/{item.get('reason')!r} 期望 {want!r}"[:200]
    old = item.get("before")
    if not isinstance(old, dict) or set(old) != set(keys):
        return f"cascade {down} 的 before 键应恰好是 {list(keys)}：{old!r}"[:200]
    tag, key = down
    for col in keys:
        if not _exact(old.get(col), _raw(before, tag, key, col)):
            return f"cascade {down} before.{col}={old.get(col)!r} 与快照不符"[:200]
    return ""


def cascade_write_problem(before: Snap, after: Snap, so_id: str, targets: dict[str, str], updated: set[Down]) -> tuple[str, list[str]]:
    """写后：只有订单表头请求的列和修改人类列、updated 下游表头请求的列变了，且新值是目标值；返回 (问题, 变了的修改人类列名)。"""
    stamps: list[str] = []
    for (tag, key, col), (old, new) in sorted(hits_of(before, after).items()):
        allowed = col in targets and ((tag == "SO" and key == so_id) or (tag, key) in updated)
        if allowed and not _exact(new, targets[col]):
            return f"{(tag, key, col)} 写后 {new!r} 期望 {targets[col]!r}"[:200], stamps
        if not allowed and tag == "SO" and key == so_id and is_stamp(col):
            stamps.append(col)
        elif not allowed:
            return f"不该变的格子变了 {(tag, key, col)} {old!r}→{new!r}"[:200], stamps
    for down in sorted(updated):
        now = {col: _raw(after, *down, col) for col in targets}
        if not all(_exact(now[col], value) for col, value in targets.items()):
            return f"下游 {down} 写后是 {now!r}，期望 {targets!r}"[:200], stamps
    return "", stamps
