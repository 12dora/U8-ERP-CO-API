"""分片跑 e2e 时用例分到哪条线。不连接服务器，只读分片表。

- 分片表里的线是用户自选的抽象标签，不写账套号和路径；命令行再把标签对到具体的线（--lane 标签=配置）。
- 第 0 波（健康检查、错误签名、名单外账套、登录）每条线都跑；其余每条用例恰好有一条归属线，
  文件里没列的归 default_lane。
- 第 2 版可写 lane_classes：一类线（账套选项不同的一组通道）的标签和 everywhere 名单。everywhere
  里的用例除归属线外，在这一类到场的每条线都再跑一遍。类里的线是可选的：命令行没给时，
  归属这些线的用例回到 default_lane，everywhere 只在到场的线上跑。
- 同一用例列在两条线、标签对不上一律拒绝。文件里有登记表已没有的用例（改名、删除）只警告并忽略，
  第 0 波与登记表不一致时警告并按登记表补齐，免得一次改名挡住所有分片回归。
- 第 2 版还可写 pinned（{线: [用例…]}）和 tail（{线: [用例…]}）：固定在某条线的用例，重算分片表时不动。
  pinned 放依赖本线造数、要临时改账套选项或只认测试账套的用例；tail 是本线最后跑的独占用例（如反结账），
  须是这条线名单的最后几条、按所列顺序，也是登记表里本线最后的几条。两者都只能写必到的线，且须已列在 lanes 里。
- 第 2 版还可写 known_na（{线: {用例: 原因}}）：已知在这条线上只能 N/A 的固定用例（如 U8 本身不支持、要删真实单据才能造数），
  多通道合并报告不再为它们提示「覆盖可能缺口」。原因必填，便于日后复查；重算分片表时原样保留。
- 按各线报告重算分片表见 shard_recompute.py（命令行仍是 python -m co.e2e.shard_assign recompute）。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

VERSION = 1
CLASS_VERSION = 2
_DIR = Path(__file__).resolve().parent
DEFAULT_FILE = _DIR / "shards.json"
EXAMPLE_FILE = _DIR / "shards.example.json"


def default_table(path: str | Path | None = None) -> Path:
    """读分片表：显式路径优先；否则有 shards.json 用它，没有则用样例。"""
    if path is not None and str(path):
        return Path(path).expanduser()
    return DEFAULT_FILE if DEFAULT_FILE.is_file() else EXAMPLE_FILE


@dataclass(frozen=True)
class LaneClass:
    lanes: tuple[str, ...]
    everywhere: tuple[str, ...]


@dataclass(frozen=True)
class Assign:
    wave0: tuple[str, ...]
    lanes: dict[str, tuple[str, ...]]
    default_lane: str
    classes: dict[str, LaneClass] = field(default_factory=dict)
    pinned: dict[str, tuple[str, ...]] = field(default_factory=dict)
    tail: dict[str, tuple[str, ...]] = field(default_factory=dict)
    known_na: dict[str, dict[str, str]] = field(default_factory=dict)

    def fixed(self) -> dict[str, str]:
        """固定用例 -> 线：pinned 加 tail。"""
        return {name: label for group in (self.pinned, self.tail) for label, names in group.items() for name in names}

    def optional(self) -> set[str]:
        """lane_classes 里的线：命令行可以不给。"""
        return {label for item in self.classes.values() for label in item.lanes}

    def labels(self) -> set[str]:
        return set(self.lanes) | {self.default_lane} | self.optional()


@dataclass(frozen=True)
class Planned:
    cases: dict[str, list[str]]
    owner: dict[str, str]


def _names(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(where + " 必须是用例名列表")
    if len(value) != len(set(value)):
        raise ValueError(where + " 里用例名重复")
    return tuple(value)


def _lane_class(name: str, value: object) -> LaneClass:
    where = "lane_classes." + name
    if not isinstance(value, dict):
        raise ValueError(where + " 必须是对象")
    lanes = _names(value.get("lanes"), where + ".lanes")
    if not lanes:
        raise ValueError(where + ".lanes 不能为空")
    return LaneClass(lanes, _names(value.get("everywhere", []), where + ".everywhere"))


def _classes(data: dict, default: str) -> dict[str, LaneClass]:
    raw = data.get("lane_classes", {})
    if data.get("version") == VERSION and "lane_classes" in data:
        raise ValueError("第 1 版分片文件不能写 lane_classes，请把 version 改成 2")
    if not isinstance(raw, dict):
        raise ValueError("lane_classes 必须是对象")
    out = {str(name): _lane_class(str(name), value) for name, value in raw.items()}
    seen: dict[str, str] = {}
    for name, item in out.items():
        for label in item.lanes:
            if label == default:
                raise ValueError("缺省线 " + default + " 不能列进 lane_classes." + name + "（类里的线是可选的）")
            if label in seen:
                raise ValueError("线 " + label + " 同时列在 lane_classes." + seen[label] + " 和 " + name)
            seen[label] = name
    return out


def _pin_group(data: dict, key: str) -> dict[str, tuple[str, ...]]:
    raw = data.get(key, {})
    if data.get("version") == VERSION and key in data:
        raise ValueError("第 1 版分片文件不能写 " + key + "，请把 version 改成 2")
    if not isinstance(raw, dict):
        raise ValueError(key + " 必须是对象")
    return {str(label): _names(names, key + "." + str(label)) for label, names in raw.items()}


def _known_na(data: dict) -> dict[str, dict[str, str]]:
    """known_na：{线: {用例: 原因}}，原因不能为空。"""
    raw = data.get("known_na", {})
    if data.get("version") == VERSION and "known_na" in data:
        raise ValueError("第 1 版分片文件不能写 known_na，请把 version 改成 2")
    if not isinstance(raw, dict) or not all(isinstance(item, dict) for item in raw.values()):
        raise ValueError("known_na 必须是 {线: {用例: 原因}} 对象")
    out: dict[str, dict[str, str]] = {}
    for label, items in raw.items():
        for name, reason in items.items():
            if not name or not isinstance(reason, str) or not reason.strip():
                raise ValueError("known_na." + str(label) + " 里的 " + str(name) + " 必须写明原因")
        out[str(label)] = {str(name): reason for name, reason in items.items()}
    return out


def _check_pins(assign: Assign) -> None:
    """pinned、tail 只写必到的线，用例须已列在这条线；同一用例不能固定两处；tail 是本线名单最后几条。"""
    seen: dict[str, str] = {}
    for key, group in (("pinned", assign.pinned), ("tail", assign.tail)):
        for label, names in group.items():
            where = key + "." + label
            if label not in assign.lanes or label in assign.optional():
                raise ValueError(where + "：只能固定在 lanes 里列出的必到线上")
            listed = set(assign.lanes[label])
            for name in names:
                if name not in listed:
                    raise ValueError(where + " 里的 " + name + " 没列在 lanes." + label)
                if name in seen:
                    raise ValueError("用例 " + name + " 同时固定在 " + seen[name] + " 和 " + where)
                seen[name] = where
    for label, names in assign.tail.items():
        if names and assign.lanes[label][-len(names):] != names:
            raise ValueError("tail." + label + " 必须是 lanes." + label + " 最后几条且顺序一致：" + ",".join(names))


def from_dict(data: object) -> Assign:
    if not isinstance(data, dict):
        raise ValueError("分片文件必须是 JSON 对象")
    if data.get("version") not in (VERSION, CLASS_VERSION):
        raise ValueError("分片文件版本不支持：" + str(data.get("version")))
    lanes = data.get("lanes")
    if not isinstance(lanes, dict):
        raise ValueError("分片文件缺少 lanes")
    default = data.get("default_lane")
    if not isinstance(default, str) or not default:
        raise ValueError("分片文件缺少 default_lane")
    wave0 = _names(data.get("wave0"), "wave0")
    parsed = {str(label): _names(names, "lanes." + str(label)) for label, names in lanes.items()}
    out = Assign(wave0, parsed, default, _classes(data, default), _pin_group(data, "pinned"), _pin_group(data, "tail"), _known_na(data))
    _check_pins(out)
    stray = sorted(set(out.known_na) - out.labels())
    if stray:
        raise ValueError("known_na 里有分片表没有的线：" + ",".join(stray))
    return out


def load(path: str | Path | None = None) -> Assign:
    target = default_table(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("读不了分片文件 " + str(target) + "：" + str(exc)) from exc
    return from_dict(data)


def classes_dict(assign: Assign) -> dict[str, dict[str, list[str]]]:
    return {name: {"lanes": list(item.lanes), "everywhere": list(item.everywhere)} for name, item in assign.classes.items()}


def _tail_notes(assign: Assign, result: Mapping[str, list[str]], lanes: Mapping[str, str], notes: list[str]) -> None:
    """实际执行按登记表顺序：tail 用例在登记表里不是本线最后几条时记警告。"""
    for label, names in assign.tail.items():
        cases = result.get(lanes.get(label, ""), [])
        want = [name for name in names if name in cases]
        if want and cases[-len(want):] != want:
            notes.append(label + " 线的收尾用例在登记表里不是本线最后几条，按登记表顺序跑会不在最后：" + ",".join(want))


def _effective_wave0(assign: Assign, catalog: list[str], notes: list[str]) -> list[str]:
    """实际的第 0 波：文件里仍在登记表的，加上登记表登录及其之前的。两者不一致时记警告。"""
    known = set(catalog)
    try:
        expected = wave0_of(catalog)
    except ValueError:
        expected = []
    listed = [name for name in assign.wave0 if name in known]
    merged = set(listed) | set(expected)
    if expected and set(expected) != set(assign.wave0):
        notes.append("分片文件的第 0 波与登记表不一致，按登记表补齐：" + ",".join(assign.wave0) + " -> " + ",".join(expected))
    return [name for name in catalog if name in merged]


def _owner_map(assign: Assign, known: set[str], wave0: set[str], notes: list[str]) -> dict[str, str]:
    """用例 -> 标签。登记表里已没有的名字记警告后丢弃；两条线重复、第 0 波又列进某条线拒绝。"""
    unknown = [name for name in assign.wave0 if name not in known]
    owner: dict[str, str] = {}
    for label, names in assign.lanes.items():
        for name in names:
            if name not in known:
                unknown.append(name)
            elif name in assign.wave0:
                raise ValueError("用例 " + name + " 属于第 0 波，不能再列进 " + label)
            elif name in owner:
                raise ValueError("用例 " + name + " 同时列在 " + owner[name] + " 和 " + label)
            elif name in wave0:
                notes.append("用例 " + name + " 现属第 0 波，每条线都跑，不再只分给 " + label)
            else:
                owner[name] = label
    if unknown:
        notes.append("分片文件里有登记表已没有的用例，已忽略（改名或删除后请重算）：" + ",".join(unknown))
    return owner


def _everywhere_map(assign: Assign, known: set[str], wave0: set[str], notes: list[str]) -> dict[str, list[str]]:
    """用例 -> 还要在哪些类线上跑。规则与 _owner_map 相同：已删的用例警告，第 0 波的拒绝。"""
    out: dict[str, list[str]] = {}
    unknown: list[str] = []
    for cname, item in assign.classes.items():
        for name in item.everywhere:
            if name not in known:
                unknown.append(name)
            elif name in assign.wave0:
                raise ValueError("用例 " + name + " 属于第 0 波，不能再列进 lane_classes." + cname + ".everywhere")
            elif name in wave0:
                notes.append("用例 " + name + " 现属第 0 波，每条线都跑，不必再列进 lane_classes." + cname)
            else:
                out.setdefault(name, []).extend(item.lanes)
    if unknown:
        notes.append("lane_classes 里有登记表已没有的用例，已忽略：" + ",".join(unknown))
    return out


def _check_labels(assign: Assign, lanes: Mapping[str, str]) -> None:
    extra = sorted(set(lanes) - assign.labels())
    if extra:
        raise ValueError("分片文件里没有标签 " + ",".join(extra))
    absent = sorted(assign.labels() - assign.optional() - set(lanes))
    if absent:
        raise ValueError("标签 " + ",".join(absent) + " 没有对到具体的线")
    targets = list(lanes.values())
    if any(not target for target in targets) or len(targets) != len(set(targets)):
        raise ValueError("每个标签要对到不同的线，且线名不能为空")


def plan_detail(
    assign: Assign, catalog_names: Iterable[str], lanes: Mapping[str, str], notes: list[str] | None = None
) -> Planned:
    """标签对到具体线名后，返回各线的用例名单（登记表顺序）和每条非第 0 波用例的归属线名。

    归属线没到场（可选的类线）时归 default_lane；everywhere 用例另加到到场的类线上。
    """
    catalog = list(catalog_names)
    _check_labels(assign, lanes)
    found: list[str] = []
    wave0 = set(_effective_wave0(assign, catalog, found))
    owner = _owner_map(assign, set(catalog), wave0, found)
    extra = _everywhere_map(assign, set(catalog), wave0, found)
    result: dict[str, list[str]] = {lanes[label]: [] for label in sorted(assign.labels()) if label in lanes}
    owners: dict[str, str] = {}
    for name in catalog:
        if name in wave0:
            targets = list(result)
        else:
            label = owner.get(name, assign.default_lane)
            owners[name] = lanes[label if label in lanes else assign.default_lane]
            targets = [owners[name], *(lanes[item] for item in extra.get(name, ()) if item in lanes)]
        for target in dict.fromkeys(targets):
            result[target].append(name)
    _tail_notes(assign, result, lanes, found)
    _emit(found, notes)
    return Planned(result, owners)


def plan(
    assign: Assign, catalog_names: Iterable[str], lanes: Mapping[str, str], notes: list[str] | None = None
) -> dict[str, list[str]]:
    """{线名: 用例名单}，按登记表顺序。第 0 波每条线都有。

    登记表里已没有的用例、第 0 波与登记表不一致只是警告：传了 notes 就追加进去，否则打印到 stderr。
    新加的用例没列在文件里，落到 default_lane。
    """
    return plan_detail(assign, catalog_names, lanes, notes).cases


def _emit(found: list[str], notes: list[str] | None) -> None:
    if notes is not None:
        notes.extend(found)
        return
    for line in found:
        print("警告：" + line, file=sys.stderr)


def completeness(executed_by_lane: Mapping[str, Iterable[str]], catalog_names: Iterable[str]) -> list[str]:
    """各线实际跑过的用例并起来，登记表里还缺哪些（登记表顺序）。"""
    done: set[str] = set()
    for names in executed_by_lane.values():
        done.update(names)
    return [name for name in catalog_names if name not in done]


def wave0_of(catalog: list[str]) -> list[str]:
    """登记表里登录及其之前的用例，与调度第 0 波一致。"""
    from co.e2e.casespec.sched import BARRIER

    if BARRIER not in catalog:
        raise ValueError("登记表里没有 " + BARRIER + " 用例")
    return catalog[: catalog.index(BARRIER) + 1]


def main(argv: list[str] | None = None) -> int:
    from co.e2e import shard_recompute

    return shard_recompute.main(argv)


if __name__ == "__main__":
    sys.exit(main())
