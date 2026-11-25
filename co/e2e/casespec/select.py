"""`--cases a,b` 选择。空名单表示全部。未知名字拒绝，不静默漏跑。"""

from __future__ import annotations

from co.e2e.casespec.model import CaseSpec


def parse_names(text: str) -> tuple[str, ...] | None:
    raw = (text or "").strip()
    if raw == "":
        return None
    parts = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not parts:
        raise ValueError("用例名单为空")
    if len(parts) != len(set(parts)):
        raise ValueError("用例名重复")
    return parts


def reject_unknown(text: str, names: tuple[str, ...]) -> str:
    try:
        chosen = parse_names(text)
    except ValueError as exc:
        return str(exc)
    if chosen is None:
        return ""
    missing = [name for name in chosen if name not in set(names)]
    if missing:
        return "未知用例 " + ",".join(missing)
    return ""


def filter_cases(specs: list[CaseSpec], names: tuple[str, ...] | None) -> list[CaseSpec]:
    if names is None:
        return list(specs)
    known = {spec.name for spec in specs}
    missing = [name for name in names if name not in known]
    if missing:
        raise ValueError("未知用例 " + ",".join(missing))
    wanted = set(names)
    return [spec for spec in specs if spec.name in wanted]


def merge_changed(names: tuple[str, ...] | None, changed: list[str]) -> tuple[str, ...]:
    """`--changed-since` 挑出的名单（coverage.select_changed，已按用例名单排序）与 `--cases` 取交集。

    没给 `--cases` 就是挑出的全部；交集之外 health、login 照跑，交集为空时只剩这两个。
    """
    # 用到时才导入：coverage.py 坏了只影响 --changed-since，不拖垮普通回归。
    from co.e2e.casespec.coverage import ALWAYS

    if names is None:
        return tuple(changed)
    wanted = set(names) | set(ALWAYS)
    return tuple(name for name in changed if name in wanted)
