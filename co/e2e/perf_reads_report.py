"""读路由性能测量的输出：perf.json（全部调用记录）和 perf.md（分组汇总、逐次调用、失败清单）。"""

from __future__ import annotations

import json
from pathlib import Path

from co.e2e.perf_reads_probe import CallRecord, group_totals


def build_doc(meta: dict, records: list[CallRecord], walls: dict[str, float], notes: dict) -> dict:
    totals = group_totals(records)
    for name, wall in walls.items():
        totals.setdefault(name, {"calls": 0, "failed": 0, "ms_total": 0.0, "ms_max": 0.0, "items": 0, "bytes": 0})
        totals[name]["wall_ms"] = wall
    failures = [item for item in records if not item.ok]
    return {
        "meta": meta,
        "groups": totals,
        "notes": notes,
        "failed": len(failures),
        "calls": [vars(item) for item in records],
    }


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _table(header: tuple[str, ...], rows: list[tuple]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return lines


def _meta_lines(doc: dict) -> list[str]:
    meta = doc["meta"]
    lines = ["# 读路由性能", ""]
    for key in ("lane", "acc", "year", "date", "bridge", "groups", "repeat", "started", "finished"):
        if key in meta:
            lines.append(f"- {key}: {_cell(meta[key])}")
    logins = meta.get("logins") or {}
    lines.append(f"- 登录计数差（opened / reused / closed）: {logins.get('opened')} / {logins.get('reused')} / {logins.get('closed')}")
    lines.append(f"- 失败调用: {doc['failed']}")
    return lines + [""]


def _group_lines(doc: dict) -> list[str]:
    header = ("组", "调用", "失败", "合计 ms", "最长 ms", "墙钟 ms", "条目", "字节")
    rows = [
        (name, row["calls"], row["failed"], row["ms_total"], row["ms_max"], row.get("wall_ms"), row["items"], row["bytes"])
        for name, row in doc["groups"].items()
    ]
    return ["## 分组汇总", ""] + _table(header, rows) + [""]


def _note_lines(doc: dict) -> list[str]:
    if not doc["notes"]:
        return []
    lines = ["## 附注", ""]
    for name, value in doc["notes"].items():
        lines.append(f"- {name}: `{json.dumps(value, ensure_ascii=False)}`")
    return lines + [""]


def _call_lines(doc: dict) -> list[str]:
    header = ("组", "路由", "参数", "状态", "错误码", "ms", "条目", "字节")
    rows = [
        (c["group"], c["route"], c["params"], c["status"], c["code"], c["ms"], c["items"], c["bytes"]) for c in doc["calls"]
    ]
    return ["## 逐次调用", ""] + _table(header, rows) + [""]


def _failure_lines(doc: dict) -> list[str]:
    failed = [c for c in doc["calls"] if not c["ok"]]
    if not failed:
        return []
    rows = [(c["group"], c["route"], c["params"], c["status"], c["code"], c["message"]) for c in failed]
    return ["## 失败", ""] + _table(("组", "路由", "参数", "状态", "错误码", "消息"), rows) + [""]


def render_markdown(doc: dict) -> str:
    lines = _meta_lines(doc) + _group_lines(doc) + _note_lines(doc) + _failure_lines(doc) + _call_lines(doc)
    return "\n".join(lines)


def write_outputs(out: Path, doc: dict) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    data = out / "perf.json"
    text = out / "perf.md"
    data.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text.write_text(render_markdown(doc), encoding="utf-8")
    return data, text
