"""按各线最近一次全量报告重算分片表（python -m co.e2e.shard_assign recompute）。不连接服务器，只读写分片表和报告文件。

- 每份报告对应分片表里的一个标签：--report 标签=report.json，可重复。分片表里必到的线（不在
  lane_classes 里的）都要有报告；类里的线没给报告就不分给它独占用例。没有原表时，default_lane
  取第一份 --report 的标签，各报告标签都当作必到线。
- 第 0 波每条线都跑。其余用例只在报告 PASS 的线里挑归属：只在一条线 PASS 的归那条线；几条线都 PASS 的
  按报告耗时从长到短贪心分配，让这几条线里最长的那条（含各自准备、部署、还原的固定开销）尽量短；
  哪条都没 PASS 的归 default_lane。everywhere 用例在本类每条线都跑，归属只在类外挑。
- 分片表 pinned、tail 里的用例原样留在所固定的线，不看报告；tail 写在本线名单最后、顺序不变。只重分其余用例。
- known_na（已知只能 N/A 的用例及原因）原样保留，只去掉登记表已没有的和现属第 0 波的。
- 报告须是全量轮次。全量里 PASS 的用例可能依赖前面用例留下的数据，重分后要跑一轮分片回归确认。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from co.e2e.shard_assign import (
    CLASS_VERSION,
    DEFAULT_FILE,
    VERSION,
    Assign,
    classes_dict,
    from_dict,
    load,
    plan,
    wave0_of,
)


@dataclass(frozen=True)
class Run:
    """一份全量报告里重算要用的：各用例状态、耗时（毫秒），以及固定开销（准备 + 部署 + 还原）。"""

    statuses: dict[str, str]
    durations: dict[str, int]
    fixed_ms: int = 0


def _ms(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _fixed_ms(phases: object) -> int:
    if not isinstance(phases, dict):
        return 0
    return sum(_ms(item.get("ms")) for key, item in phases.items() if key != "cases" and isinstance(item, dict))


def read_report(path: str | Path) -> Run:
    """读报告。没有用例结果（如只做了还原的轮次）拒绝；耗时、阶段缺了按 0 算。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("读不了报告 " + str(path) + "：" + str(exc)) from exc
    tests = data.get("tests") if isinstance(data, dict) else None
    if not isinstance(tests, list) or not tests:
        raise ValueError("报告里没有用例结果：" + str(path))
    rows = [item for item in tests if isinstance(item, dict) and item.get("case")]
    statuses = {str(item["case"]): str(item.get("status", "")) for item in rows}
    durations = {str(item["case"]): _ms(item.get("duration_ms")) for item in rows}
    return Run(statuses, durations, _fixed_ms(data.get("phases")))


def report_statuses(path: str | Path) -> dict[str, str]:
    """报告 tests 里 {用例名: 状态}。"""
    return read_report(path).statuses


def uncovered(statuses: Mapping[str, str], catalog: list[str]) -> list[str]:
    """登记表里（第 0 波除外）报告没有结果的用例。"""
    head = set(wave0_of(catalog))
    return [name for name in catalog if name not in head and name not in statuses]


def _check_cover(runs: Mapping[str, Run], catalog: list[str], allow_partial: bool) -> None:
    if allow_partial:
        return
    for label, run in runs.items():
        missing = uncovered(run.statuses, catalog)
        if missing:
            raise ValueError(
                label + " 报告没覆盖登记表，缺 " + str(len(missing)) + " 条：" + ",".join(missing[:20])
                + ("…" if len(missing) > 20 else "") + "。请用全量轮次的报告；确要按部分结果重算加 --allow-partial"
            )


def _check_labels(base: Assign, runs: Mapping[str, Run]) -> None:
    extra = sorted(set(runs) - base.labels())
    if extra:
        raise ValueError("分片表里没有标签 " + ",".join(extra) + "；新的线先在分片表 lane_classes 里登记")
    absent = sorted(base.labels() - base.optional() - set(runs))
    if absent:
        raise ValueError("缺标签 " + ",".join(absent) + " 的报告")


def cost(runs: Mapping[str, Run], label: str, name: str) -> int:
    """用例在这条线上的预计耗时：本线报告有就用本线的，否则取别的线里最长的。"""
    own = runs[label].durations.get(name, 0) if label in runs else 0
    return own or max((run.durations.get(name, 0) for run in runs.values()), default=0)


def class_lanes(classes: Mapping[str, object]) -> dict[str, set[str]]:
    """用例 -> 要在哪些类线上都跑。classes 是分片文件里 lane_classes 的字典形式。"""
    out: dict[str, set[str]] = {}
    for item in classes.values():
        if isinstance(item, dict):
            for name in item.get("everywhere") or ():
                out.setdefault(name, set()).update(item.get("lanes") or ())
    return out


def _balance(open_: dict[str, list[str]], runs: Mapping[str, Run], totals: dict[str, int], choice: dict[str, str], default: str) -> None:
    """几条线都 PASS 的用例按耗时从长到短贪心分配。"""
    order = sorted(open_, key=lambda name: -max(runs[label].durations.get(name, 0) for label in open_[name]))
    for name in order:
        cands = open_[name]

        def score(label: str, name: str = name, cands: list[str] = cands) -> tuple:
            mine = totals[label] + cost(runs, label, name)
            others = max((totals[item] for item in cands if item != label), default=0)
            # 先比候选里最长的那条，再比本线；并列时不放 default_lane，再按报告顺序。
            # 现用分片表的 default_lane 不是「只在部分账套有数」的那条线，与旧的按线名优先结果相同。
            return (max(mine, others), mine, label == default, cands.index(label))

        best = min(cands, key=score)
        choice[name] = best
        totals[best] += cost(runs, best, name)


def _base_totals(runs: Mapping[str, Run], wave0: list[str], rest: list[str], spread: dict[str, set[str]]) -> dict[str, int]:
    totals = {label: run.fixed_ms + sum(cost(runs, label, name) for name in wave0) for label, run in runs.items()}
    for name in rest:
        for label in spread.get(name, ()):
            if label in totals:
                totals[label] += cost(runs, label, name)
    return totals


def _pins_out(base: Assign, rest: list[str]) -> dict[str, dict[str, list[str]]]:
    """新分片表的 pinned、tail：去掉登记表已没有的和现属第 0 波的，空线不写。"""
    keep = set(rest)
    out: dict[str, dict[str, list[str]]] = {}
    for key, group in (("pinned", base.pinned), ("tail", base.tail)):
        lanes = {label: [name for name in names if name in keep] for label, names in group.items()}
        lanes = {label: names for label, names in lanes.items() if names}
        if lanes:
            out[key] = lanes
    return out


def _known_na_out(base: Assign, rest: list[str]) -> dict[str, dict[str, str]]:
    keep = set(rest)
    out = {label: {name: why for name, why in items.items() if name in keep} for label, items in base.known_na.items()}
    return {label: items for label, items in out.items() if items}


def _lane_lists(labels: list[str], rest: list[str], choice: Mapping[str, str], tail: Mapping[str, list[str]]) -> dict[str, list[str]]:
    """各线名单按登记表顺序，tail 用例按所列顺序放在最后。"""
    out = {}
    for label in labels:
        last = tail.get(label, [])
        out[label] = [name for name in rest if choice[name] == label and name not in last] + list(last)
    return out


def _base_assign(old: Assign | None, runs: Mapping[str, Run]) -> Assign:
    """有原表用原表；否则 default_lane 和必到线都来自报告标签（第一份为缺省）。"""
    if old is not None:
        return old
    labels = list(runs)
    if not labels:
        raise ValueError("至少要给一份报告")
    default = labels[0]
    return Assign((), {label: () for label in labels}, default)


def recompute(runs: Mapping[str, Run], catalog: list[str], old: Assign | None = None, allow_partial: bool = False) -> dict:
    """按各线报告重算，返回新的分片文件内容。old 提供 default_lane、lane_classes、pinned 和 tail。"""
    base = _base_assign(old, runs)
    _check_labels(base, runs)
    _check_cover(runs, catalog, allow_partial)
    default = base.default_lane
    wave0 = wave0_of(catalog)
    rest = [name for name in catalog if name not in set(wave0)]
    classes = classes_dict(base)
    spread = class_lanes(classes)
    pins = _pins_out(base, rest)
    fixed = {name: label for group in pins.values() for label, names in group.items() for name in names}
    totals = _base_totals(runs, wave0, rest, spread)
    choice: dict[str, str] = {}
    open_: dict[str, list[str]] = {}
    for name in rest:
        cands = [label for label in runs if runs[label].statuses.get(name) == "PASS" and label not in spread.get(name, ())]
        if name not in fixed and len(cands) > 1:
            open_[name] = cands
            continue
        choice[name] = fixed.get(name) or (cands[0] if cands else default)
        totals[choice[name]] += cost(runs, choice[name], name)
    _balance(open_, runs, totals, choice, default)
    labels = [default, *(label for label in runs if label != default)]
    out = {"version": VERSION, "wave0": wave0, "lanes": _lane_lists(labels, rest, choice, pins.get("tail", {}))}
    out["default_lane"] = default
    if classes:
        out.update(version=CLASS_VERSION, lane_classes=classes)
    if pins:
        out.update(version=CLASS_VERSION, **pins)
    known_na = _known_na_out(base, rest)
    if known_na:
        out.update(version=CLASS_VERSION, known_na=known_na)
    return out


def predict(new: dict, runs: Mapping[str, Run]) -> dict[str, int]:
    """按报告耗时预计每条有报告的线的总时长（毫秒）：固定开销 + 第 0 波 + 归属用例 + 本类 everywhere 用例。"""
    spread = class_lanes(new.get("lane_classes") or {})
    out = {}
    for label, run in runs.items():
        names = dict.fromkeys([*new["wave0"], *new["lanes"].get(label, ())])
        names.update(dict.fromkeys(name for name, where in spread.items() if label in where))
        out[label] = run.fixed_ms + sum(cost(runs, label, name) for name in names)
    return out


def _minutes(ms: int) -> str:
    return f"{ms / 60000:.1f} 分钟"


def _moves(old: Assign | None, new: dict, label: str) -> str:
    before = set(old.lanes.get(label, ())) if old else set()
    after = new["lanes"].get(label, [])
    added = [name for name in after if name not in before]
    dropped = sorted(before - set(after))
    return label + " 线新进 " + str(len(added)) + " 条：" + (",".join(added) or "无") + "；移出 " + str(len(dropped)) + " 条：" + (",".join(dropped) or "无")


def _class_weak(new: dict, runs: Mapping[str, Run]) -> list[str]:
    lines = []
    for label in runs:
        weak = [name for name, where in class_lanes(new.get("lane_classes") or {}).items() if label in where and runs[label].statuses.get(name) != "PASS"]
        if weak:
            lines.append("everywhere 用例在 " + label + " 报告里不是 PASS 的：" + ",".join(weak))
    return lines


def _pin_lines(old: Assign | None, new: dict, runs: Mapping[str, Run]) -> list[str]:
    """固定用例：每条线几条、本线报告不是 PASS 的；原分片表里固定、新表里已去掉的。"""
    lines = []
    kept: set[str] = set()
    for key in ("pinned", "tail"):
        for label, names in (new.get(key) or {}).items():
            kept.update(names)
            weak = [name for name in names if runs[label].statuses.get(name) != "PASS"]
            lines.append(key + "." + label + " 固定 " + str(len(names)) + " 条（不重分）；本线报告不是 PASS 的：" + (",".join(weak) or "无"))
    dropped = sorted(set(old.fixed()) - kept) if old else []
    if dropped:
        lines.append("固定名单里登记表已没有或现属第 0 波的用例，已去掉：" + ",".join(dropped))
    return lines


def summary(old: Assign | None, new: dict, runs: Mapping[str, Run], catalog: list[str]) -> list[str]:
    default = new["default_lane"]
    guess = predict(new, runs)
    lines = ["登记表 " + str(len(catalog)) + " 条；第 0 波 " + str(len(new["wave0"])) + " 条（每条线都跑）"]
    for label in new["lanes"]:
        lines.append(label + " 线归属 " + str(len(new["lanes"][label])) + " 条；预计 " + _minutes(guess[label]) + "（固定 " + _minutes(runs[label].fixed_ms) + "）")
    weak = [name for name in new["lanes"][default] if runs[default].statuses.get(name) != "PASS"]
    lines.append(default + " 线里 " + default + " 报告不是 PASS 的：" + (",".join(weak) or "无"))
    lines.extend(_pin_lines(old, new, runs))
    lines.extend(_moves(old, new, label) for label in new["lanes"] if label != default)
    lines.extend(_class_weak(new, runs))
    stray = sorted({name for run in runs.values() for name in run.statuses} - set(catalog))
    if stray:
        lines.append("报告里有登记表外的用例（已忽略）：" + ",".join(stray))
    if old and list(old.wave0) != new["wave0"]:
        lines.append("第 0 波变了：" + ",".join(old.wave0) + " -> " + ",".join(new["wave0"]))
    return lines


def _old(path: Path) -> Assign | None:
    if not path.exists():
        return None
    try:
        return load(path)
    except ValueError as exc:
        print("原分片文件无效，按空文件比较：" + str(exc), file=sys.stderr)
        return None


def report_paths(args: argparse.Namespace) -> dict[str, str]:
    out: dict[str, str] = {}
    for text in args.report:
        label, sep, path = text.partition("=")
        if not sep or not label or not path:
            raise ValueError("--report 要写成 <标签>=<report.json>：" + text)
        if label in out:
            raise ValueError("标签 " + label + " 给了两份报告")
        out[label] = path
    if not out:
        raise ValueError("至少要给一份 --report")
    return out


def _recompute_cmd(args: argparse.Namespace) -> int:
    from co.e2e.casespec import catalog as catalog_mod

    catalog = list(catalog_mod.known_names())
    runs = {label: read_report(path) for label, path in report_paths(args).items()}
    path = Path(args.assign)
    old = _old(path)
    new = recompute(runs, catalog, old, args.allow_partial)
    for line in summary(old, new, runs, catalog):
        print(line)
    if not args.write:
        print("未写入；加 --write 写入 " + str(path))
        return 0
    plan(from_dict(new), catalog, {label: label for label in runs})
    path.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("已写入 " + str(path))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m co.e2e.shard_assign", description="e2e 分片：用例分到哪条线")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("recompute", help="按各线报告重算分片文件")
    rec.add_argument("--report", action="append", default=[], metavar="标签=report.json", help="某条线最近一次全量的报告，可重复")
    rec.add_argument("--assign", default=str(DEFAULT_FILE), help="分片文件（默认 co/e2e/shards.json，没有则新建）")
    rec.add_argument("--write", action="store_true", help="写回分片文件；不加只打印差异")
    rec.add_argument("--allow-partial", action="store_true", help="报告不是全量时也重算，报告里缺的用例不算这条线 PASS")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _recompute_cmd(args)
    except ValueError as exc:
        print("错误：" + str(exc), file=sys.stderr)
        return 2
