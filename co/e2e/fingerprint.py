"""指纹的归一化和比较。输入是已经取回的 JSON。"""

from __future__ import annotations

from co.e2e.settings import conf


def _signed(value: int) -> int:
    masked = value & 0xFFFFFFFF
    if masked >= 0x80000000:
        return masked - 0x100000000
    return masked


def totals_of(tables: list[dict]) -> dict:
    rows = 0
    folded = 0
    errors = 0
    for table in tables:
        if table.get("error"):
            errors += 1
            continue
        count = table.get("rows")
        if isinstance(count, int) and not isinstance(count, bool):
            rows += count
        checksum = table.get("checksum")
        if isinstance(checksum, int) and not isinstance(checksum, bool):
            folded = _signed(folded ^ checksum)
    return {"tables": len(tables), "rows": rows, "checksum_xor": folded, "errors": errors}


def _clean_table(row: dict) -> dict:
    checksum = row.get("checksum")
    count = row.get("rows")
    if isinstance(checksum, bool):
        checksum = None
    if isinstance(count, bool):
        count = None
    return {
        "schema": str(row["schema"]),
        "name": str(row["name"]),
        "rows": count,
        "checksum": checksum,
        "error": str(row.get("error") or ""),
    }


def normalize(doc: dict) -> dict:
    raw = doc.get("databases")
    if not isinstance(raw, dict):
        raise ValueError("指纹缺少 databases")
    names = conf().databases
    if set(raw) != set(names):
        raise ValueError("指纹含名单外的库或缺少测试账套的库")
    databases = {}
    for name in names:
        item = raw[name]
        tables = [_clean_table(row) for row in item.get("tables") or []]
        tables.sort(key=lambda row: (row["schema"], row["name"]))
        databases[name] = {"tables": tables, "totals": totals_of(tables)}
    return {"databases": databases}


def merge_parts(parts: list[dict]) -> dict:
    databases: dict = {}
    names = conf().databases
    for part in parts:
        name = part.get("database")
        if name not in names or name in databases:
            raise ValueError("指纹库名不对")
        tables = part.get("tables")
        if not isinstance(tables, list):
            raise ValueError("指纹缺少表清单")
        databases[name] = {"tables": tables}
    if set(databases) != set(names):
        raise ValueError("指纹未覆盖测试账套的两个库")
    return {"databases": databases}


def _cell(table: dict) -> tuple:
    return (table["rows"], table["checksum"], table["error"])


def _diff_db(name: str, left: dict, right: dict) -> list[str]:
    messages = []
    before = {(row["schema"], row["name"]): row for row in left["tables"]}
    after = {(row["schema"], row["name"]): row for row in right["tables"]}
    for key in sorted(set(before) | set(after)):
        label = f"{name} {key[0]}.{key[1]}"
        if key not in before:
            messages.append(f"{label} 仅出现在测试后")
        elif key not in after:
            messages.append(f"{label} 仅出现在测试前")
        elif _cell(before[key]) != _cell(after[key]):
            old = before[key]
            new = after[key]
            messages.append(f"{label} 行数 {old['rows']} -> {new['rows']} 校验 {old['checksum']} -> {new['checksum']}")
    if left["totals"] != right["totals"]:
        messages.append(f"{name} 合计不一致")
    return messages


def diff_fingerprints(before: dict, after: dict) -> list[str]:
    try:
        left = normalize(before)
        right = normalize(after)
    except (ValueError, KeyError, TypeError) as exc:
        return [f"指纹无法比较: {exc}"]
    messages: list[str] = []
    for name in conf().databases:
        messages.extend(_diff_db(name, left["databases"][name], right["databases"][name]))
    return messages
