"""多通道分片回归的合并报告：判定、完整性、覆盖提醒和 Markdown。只读各通道的 report.json，不起进程。"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

from co.e2e import shard_assign
from co.e2e.timing import fmt_ms

STATUSES = ("PASS", "SKIP", "N/A", "FAIL")
_LIST_CAP = 30


@dataclass(frozen=True)
class LaneResult:
    label: str
    account: str
    exit_code: int | None
    report: dict | None
    wall_ms: int | None = None
    report_name: str = ""
    planned: int = 0
    note: str = ""


def tests_of(result: LaneResult) -> list[dict]:
    tests = (result.report or {}).get("tests") or []
    return [item for item in tests if isinstance(item, dict)]


def counts(tests: list[dict]) -> dict[str, int]:
    out = dict.fromkeys(STATUSES, 0)
    for item in tests:
        status = item.get("status")
        out[status if status in ("PASS", "SKIP", "N/A") else "FAIL"] += 1
    return out


def clip(names: list[str]) -> str:
    more = f" 等 {len(names)} 条" if len(names) > _LIST_CAP else ""
    return ", ".join(names[:_LIST_CAP]) + more


def _names_with(result: LaneResult, status: str) -> list[str]:
    return [str(item.get("case") or item.get("name")) for item in tests_of(result) if item.get("status") == status]


def lane_reasons(result: LaneResult, dry_run: bool) -> list[str]:
    tag = f"通道 {result.label}"
    if result.report is None:
        note = f"：{result.note}" if result.note else ""
        return [f"{tag} 没有报告（退出码 {result.exit_code}）{note}"]
    reasons = []
    if result.report.get("mode") == "in-session":
        # 会话内运行（run_e2e --in-session）没有自己还原、没有核对指纹，不能当完整回归的证据。
        reasons.append(f"{tag} 是会话内运行的报告（mode in-session），不能作为完整回归的证据")
    if result.exit_code != 0:
        reasons.append(f"{tag} 退出码 {result.exit_code}")
    overall = result.report.get("overall")
    if overall != ("DRY-RUN" if dry_run else "PASS"):
        reasons.append(f"{tag} 判定 {overall}")
    if dry_run:
        return reasons
    skipped = _names_with(result, "SKIP")
    if skipped:
        reasons.append(f"{tag} 有 SKIP：" + clip(skipped))
    # 判定之外再逐条看：哪条用例失败要写进原因（everywhere 用例在哪个账套失败）。
    failed = [str(item.get("case") or item.get("name")) for item in tests_of(result) if item.get("status") not in ("PASS", "SKIP", "N/A")]
    if failed:
        reasons.append(f"{tag} 有失败：" + clip(failed))
    if result.report.get("rollback_verified") is not True:
        reasons.append(f"{tag} rollback_verified 不是 true")
    return reasons


def warn_owners(owners: Mapping[str, str], known_na: Mapping[str, Collection[str]]) -> dict[str, str]:
    """N/A 提醒用的归属表：去掉分片表 known_na 里登记在归属通道上的用例（已知只能 N/A，原因见分片表），这些不再提醒覆盖缺口。"""
    return {name: label for name, label in owners.items() if name not in known_na.get(label, ())}


def lane_warnings(results: list[LaneResult], default_lane: str, owners: Mapping[str, str] | None = None) -> list[str]:
    # 非缺省通道只分到在那里实测 PASS 的用例；变成 N/A 说明那边数据变了，这条可能该回到缺省通道。
    # 只提醒、不判 FAIL：N/A 本身是合法结果，该用 shard_assign recompute 重算分片表。
    # 给了归属表时只看归属这条通道的用例：第 0 波和 everywhere 在各通道多跑的那几遍出现 N/A 不提醒（小账套本来就可能没有）。
    # 归属表经 warn_owners 去掉 known_na 后，已知只能 N/A 的用例也不提醒。
    out = []
    for result in results:
        if result.label == default_lane:
            continue
        names = [name for name in _names_with(result, "N/A") if owners is None or owners.get(name) == result.label]
        if names:
            out.append(f"通道 {result.label} 有 N/A（覆盖可能缺口，考虑重算分片表）：" + clip(names))
    return out


def executed(results: list[LaneResult]) -> dict[str, list[str]]:
    return {res.label: [str(item["case"]) for item in tests_of(res) if item.get("case")] for res in results}


def lane_row(result: LaneResult) -> dict:
    report = result.report or {}
    row = {"lane": result.label, "account": result.account, "exit_code": result.exit_code, "overall": report.get("overall") or ""}
    row.update(rollback_verified=report.get("rollback_verified") is True, counts=counts(tests_of(result)), planned=result.planned)
    row.update(wall_ms=result.wall_ms, report=result.report_name, tolerated=tolerated_count(report))
    return row


def tolerated_count(report: dict) -> int:
    """通道报告里已容忍的 U8 后台表差异行数（report.json 的 background_tolerated，见 fingerprint_bg）。"""
    items = report.get("background_tolerated")
    return len(items) if isinstance(items, list) else 0


def _tolerated_lines(rows: list[dict]) -> list[str]:
    items = [f"{row['lane']}：已容忍 U8 后台表 {row['tolerated']} 行（见该通道 report.md）" for row in rows if row.get("tolerated")]
    return _bullets("## U8 后台表（已容忍）", items)


def _head(catalog: tuple[str, ...]) -> set[str]:
    try:
        return set(shard_assign.wave0_of(list(catalog)))
    except ValueError:
        return set()


def spread(tests: list[dict], skip: set[str]) -> dict[str, dict[str, str]]:
    """第 0 波（skip）以外在两条及以上通道跑过的用例：{用例: {通道: 结果}}，按首次出现的顺序。"""
    seen: dict[str, dict[str, str]] = {}
    for item in tests:
        if item.get("case") and item["case"] not in skip:
            seen.setdefault(str(item["case"]), {})[str(item.get("lane"))] = str(item.get("status"))
    return {name: lanes for name, lanes in seen.items() if len(lanes) > 1}


def merge(
    results: list[LaneResult], catalog: tuple[str, ...], dry_run: bool, default_lane: str = "", owners: Mapping[str, str] | None = None
) -> dict:
    # 第 0 波和分片表 everywhere 的用例在几条通道各跑一遍，重复不算错，每条通道的结果都参与判定；
    # 完整性看各通道执行过的用例并集是否覆盖登记表。
    reasons = [text for res in results for text in lane_reasons(res, dry_run)]
    missing: list[str] = []
    if not dry_run:
        missing = list(shard_assign.completeness(executed(results), list(catalog)))
    if missing:
        reasons.append("缺用例结果：" + clip(missing))
    overall = "FAIL" if reasons else ("DRY-RUN" if dry_run else "PASS")
    tests = [dict(item, lane=res.label) for res in results for item in tests_of(res)]
    head = {"kind": "multi", "dry_run": dry_run, "overall": overall, "catalog_size": len(catalog), "missing": missing}
    body = dict(head, lanes=[lane_row(res) for res in results], tests=tests, reasons=reasons)
    body["warnings"] = [] if dry_run else lane_warnings(results, default_lane, owners)
    body["spread"] = spread(tests, _head(catalog))
    return body


def exit_code(overall: str) -> int:
    return 0 if overall in ("PASS", "DRY-RUN") else 1


def _cell(value: object) -> str:
    return str(value if value is not None else "").replace("|", "/").replace("\n", " ")


def _lane_table(rows: list[dict]) -> list[str]:
    lines = [
        "| 通道 | 账套 | 退出码 | 判定 | PASS | SKIP | N/A | FAIL | rollback_verified | 用时 | 报告目录 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        num = row["counts"]
        tail = [str(row["rollback_verified"]).lower(), fmt_ms(row["wall_ms"]), row["report"]]
        cells = [row["lane"], row["account"], row["exit_code"], row["overall"] or "（无报告）", *[num[key] for key in STATUSES], *tail]
        lines.append("| " + " | ".join(_cell(item) for item in cells) + " |")
    return lines


def _case_table(tests: list[dict]) -> list[str]:
    if not tests:
        return ["- （没有执行 HTTP 测试）"]
    lines = ["| 通道 | 用例 | 名称 | 结果 | 说明 | 耗时 |", "| --- | --- | --- | --- | --- | --- |"]
    for item in tests:
        cells = [item.get("lane"), item.get("case"), item.get("name"), item.get("status"), item.get("detail")]
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + f" | {fmt_ms(item.get('duration_ms'))} |")
    return lines


def completeness_line(report: dict) -> str:
    if report.get("dry_run"):
        return "完整性：试运行不检查"
    missing = report.get("missing") or []
    if missing:
        return f"完整性：登记表 {report.get('catalog_size')} 条，缺 {len(missing)} 条：" + clip(missing)
    return f"完整性：登记表 {report.get('catalog_size')} 条用例全部有结果"


def _spread_lines(report: dict) -> list[str]:
    # 第 0 波以外在几条通道都跑的用例（everywhere），各通道结果并排，便于看出哪个账套的选项不同。
    rows = report.get("spread") or {}
    if not rows:
        return []
    lines = ["", "## 多通道用例（第 0 波除外）", "", "| 用例 | 各通道结果 |", "| --- | --- |"]
    for name, lanes in rows.items():
        lines.append("| " + _cell(name) + " | " + _cell("；".join(f"{lane} {status}" for lane, status in lanes.items())) + " |")
    return lines


def _bullets(title: str, items: list[str]) -> list[str]:
    return ["", title, *[f"- {item}" for item in items]] if items else []


def render_markdown(report: dict) -> str:
    lines = ["# u8co 多通道分片回归", "", f"结论：{report.get('overall')}", completeness_line(report)]
    lines.append("说明：合并结论为 PASS 即可作完整回归结论；重算分片表后，建议另在一条通道上串行跑一次全量。")
    lines.extend(["", "## 通道"])
    lines.extend(_lane_table(list(report.get("lanes") or [])))
    lines.extend(_bullets("## 警告", list(report.get("warnings") or [])))
    lines.extend(_bullets("## 失败原因", list(report.get("reasons") or [])))
    lines.extend(_tolerated_lines(list(report.get("lanes") or [])))
    lines.extend(_spread_lines(report))
    lines.extend(["", "## 用例（各通道并集，第 0 波和 everywhere 用例每条通道各一行）"])
    lines.extend(_case_table(list(report.get("tests") or [])))
    lines.append("")
    return "\n".join(lines)


def write_merged(report: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_markdown(report), encoding="utf-8")
