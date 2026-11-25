"""多通道分片回归：按分片表把用例分到几条通道，每条通道一个 run_e2e 子进程同时跑，最后合并报告。

合并结论为 PASS 即可作完整回归结论，用法和判定见 co/e2e/README.md「多通道分片回归」。合并与判定在 co/e2e/multi_report.py。
"""

# --lane 把分片表里的通道标签（用户自选）对到一份 e2e 配置（子进程的通道配置环境变量，见 settings.lane_env）。
# 每个子进程自己做预检、快照、部署和还原，输出直接写进自己的日志文件，不依赖本进程活着。
# 某条通道提前中止（许可已满、桥 stopping）时，别的通道照常跑到自己的还原，不会被杀掉；
# Ctrl-C 或 SIGTERM 只转发一次 SIGINT 给各子进程（run_e2e 还原阶段自己屏蔽中断），然后等它们各自还原完。

from __future__ import annotations

import argparse
import json
import re
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from co.e2e import daytime, shard_assign
from co.e2e import lane as lanes
from co.e2e.casespec.catalog import known_names
from co.e2e.multi_report import LaneResult, exit_code, merge, warn_owners, write_merged
from co.e2e.plan import clamp_minutes
from co.e2e.safety import unique_report_dir
from co.e2e.settings import ConfigError, E2EConfig, lane_env, load_lane
from co.e2e.timing import fmt_ms

_ROOT = Path(__file__).resolve().parents[2]
PROGRESS_SECONDS = 60
_LABEL = re.compile(r"^[a-z0-9]{2,12}\Z")
_TAIL = 40
# 主机名写法不同时（别名、FQDN、解析失败只能比字符串）也不许这两组同时相同：很可能是同一台上的同一条通道。
_CROSS_HOST = (("account", "snapshot_suffix"), ("service_name", "bridge_port"))


class UsageError(ValueError):
    pass


@dataclass(frozen=True)
class Lane:
    label: str
    config: Path
    cfg: E2EConfig

    @property
    def report_root(self) -> Path:
        # 子进程在仓库根目录运行：相对的 report.dir 按仓库根解析。
        return (_ROOT / Path(self.cfg.report_dir).expanduser()).resolve()


@dataclass(frozen=True)
class Options:
    dry_run: bool = False
    allow_daytime: bool = False
    watchdog_min: int | None = None
    force_deploy: bool = False


@dataclass
class Child:
    lane: Lane
    cases: list[str]
    log: Path
    proc: subprocess.Popen | None = None
    started: float = 0.0
    started_at: float = 0.0
    wall_ms: int | None = None
    exit_code: int | None = None
    lines: int = 0
    tail: list[str] = field(default_factory=list)
    offset: int = 0
    partial: bytes = b""
    note: str = ""


@dataclass
class MultiRun:
    children: list[Child]
    catalog: tuple[str, ...]
    opts: Options
    out: Path
    default_lane: str = ""
    owners: dict[str, str] = field(default_factory=dict)
    known_na: dict[str, dict[str, str]] = field(default_factory=dict)


def _say(text: str) -> None:
    # 终端断开后打印会失败；本进程还要等子进程还原、写合并报告，不能因此退出。
    try:
        print(text, flush=True)
    except (OSError, ValueError):
        pass


# ---- 参数、配置和分片 ----


def parse_lane(text: str) -> tuple[str, Path]:
    label, sep, path = text.partition("=")
    if not sep or _LABEL.fullmatch(label) is None or not path.strip():
        raise UsageError(f"--lane 要写成 <通道名>=<配置文件>，通道名为 2–12 位小写字母或数字：{text!r}")
    # 子进程的工作目录是仓库根，配置路径先按本进程的当前目录解析成绝对路径。
    return label, Path(path.strip()).expanduser().resolve()


def _addresses(host: str) -> set[str]:
    try:
        found = {str(info[4][0]) for info in socket.getaddrinfo(host, None)}
    except (OSError, UnicodeError):
        found = set()
    return found or {host.lower()}


def same_server(left: str, right: str) -> bool:
    if left.lower() == right.lower():
        return True
    return bool(_addresses(left) & _addresses(right))


def _pair_clash(left: Lane, right: Lane) -> list[str]:
    # 同一台服务器上通道指纹每项都要两两不同（lane.py）；不同服务器至少本机报告目录要不同。
    one = lanes.fingerprint(left.cfg)
    two = lanes.fingerprint(right.cfg)
    same = {key for key in one if key not in ("tag", "report_dir") and one[key].lower() == two[key].lower()}
    head = f"通道 {left.label} 和 {right.label} 的 "
    out = [head + f"report.dir 相同：{left.report_root}"] if left.report_root == right.report_root else []
    if same_server(left.cfg.host, right.cfg.host):
        return out + [head + f"{key} 相同：{one[key]}" for key in one if key in same]
    return out + [head + "、".join(pair) + " 都相同" for pair in _CROSS_HOST if set(pair) <= same]


def lane_clashes(items: list[Lane]) -> list[str]:
    problems: list[str] = []
    for index, left in enumerate(items):
        for right in items[index + 1 :]:
            problems.extend(_pair_clash(left, right))
    return problems


def load_lanes(items: list[str]) -> list[Lane]:
    if not items:
        raise UsageError("至少要给一条 --lane")
    out: list[Lane] = []
    for item in items:
        label, path = parse_lane(item)
        if any(lane.label == label for lane in out):
            raise UsageError(f"通道 {label} 重复")
        try:
            cfg = load_lane(path)
        except ConfigError as exc:
            raise UsageError(f"通道 {label} 的配置不可用：{exc}") from None
        out.append(Lane(label, path, cfg))
    clashes = lane_clashes(out)
    if clashes:
        raise UsageError("通道之间撞名，不能同时跑：" + "；".join(clashes))
    return out


def plan_cases(
    assign_path: Path, labels: list[str], catalog: tuple[str, ...]
) -> tuple[dict[str, list[str]], str, dict[str, str], dict[str, dict[str, str]]]:
    # 抽象通道名对到自己：plan 返回的键就是 --lane 上的通道名。另返回缺省通道名、每条用例的归属通道
    # 和分片表 known_na（合并报告的 N/A 提醒要用）。
    try:
        assign = shard_assign.load(assign_path)
        planned = shard_assign.plan_detail(assign, list(catalog), {label: label for label in labels})
    except (OSError, ValueError) as exc:
        raise UsageError(f"分片表 {assign_path} 不可用：{exc}") from None
    out: dict[str, list[str]] = {}
    for label in labels:
        # 空名单交给 run_e2e 等于「全部用例」，必须拒绝。
        cases = list(dict.fromkeys(planned.cases.get(label) or []))
        if not cases:
            raise UsageError(f"通道 {label} 没有分到用例")
        out[label] = cases
    return out, assign.default_lane, planned.owner, assign.known_na


def daytime_refusal(items: list[Lane], opts: Options) -> str:
    # 与 run_e2e 的算法一致；在起任何子进程之前拒绝，免得一条通道跑起来、另一条被拒。
    for lane in items:
        minutes = clamp_minutes(lane.cfg.run_minutes if opts.watchdog_min is None else opts.watchdog_min)
        text = daytime.refusal(lane.cfg.business_hours, opts.allow_daytime, opts.dry_run, minutes=minutes)
        if text:
            return f"通道 {lane.label}：{text}"
    return ""


IN_SESSION_REFUSED = "multi 不支持 --in-session：分片回归每条通道都要自己快照、还原并核对指纹。会话内运行请直接用 run_e2e --in-session"


def child_command(cases: list[str], opts: Options) -> list[str]:
    cmd = [sys.executable, "-m", "co.e2e.run_e2e", "--cases", ",".join(cases)]
    if opts.dry_run:
        cmd.append("--dry-run")
    if opts.watchdog_min is not None:
        cmd.extend(["--watchdog-min", str(opts.watchdog_min)])
    if opts.force_deploy:
        cmd.append("--force-deploy")
    if opts.allow_daytime:
        cmd.append(daytime.FLAG)
    return cmd


def child_env(lane: Lane, base: dict[str, str] | None = None) -> dict[str, str]:
    env = lane_env(lane.config, base)
    env["PYTHONUNBUFFERED"] = "1"
    return env


# ---- 子进程 ----


def start(child: Child, opts: Options) -> None:
    child.started = time.monotonic()
    child.started_at = time.time()
    try:
        child.log.parent.mkdir(parents=True, exist_ok=True)
        # 输出直接进日志文件：本进程死了子进程也照常写完、还原。子进程单独成组，终端的 Ctrl-C 只到本进程。
        with child.log.open("ab") as out:
            child.proc = subprocess.Popen(
                child_command(child.cases, opts),
                cwd=_ROOT,
                env=child_env(child.lane),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except OSError as exc:
        child.exit_code = 127
        child.wall_ms = 0
        child.note = "启动失败：" + type(exc).__name__
        return
    lane = child.lane
    _say(f"[{lane.label}] 已启动 pid {child.proc.pid}（账套 {lane.cfg.account}，{len(child.cases)} 条用例），日志 {child.log}")


def drain(child: Child) -> None:
    # 读日志新增的部分，只为进度行和报告路径；读不了不影响子进程。
    try:
        with child.log.open("rb") as src:
            src.seek(child.offset)
            data = src.read()
    except OSError:
        return
    child.offset += len(data)
    chunks = (child.partial + data).split(b"\n")
    child.partial = chunks.pop()
    for raw in chunks:
        child.lines += 1
        child.tail.append(raw.decode("utf-8", errors="replace").rstrip("\r"))
    del child.tail[:-_TAIL]


class Forwarder:
    """SIGINT / SIGTERM 处理：第一次转发 SIGINT 给还在跑的子进程，之后只提示。"""

    def __init__(self, children: list[Child]) -> None:
        self.children = children
        self.hits = 0

    def __call__(self, signum, frame) -> None:
        self.hits += 1
        if self.hits > 1:
            _say("中断已转发过，仍在等各通道还原。不要强行结束，否则快照可能留在服务器上。")
            return
        _say("收到中断：已转发给各通道，还没启动的通道不再启动，等已启动的各自还原后再退出。")
        for child in self.children:
            if child.proc is not None and child.proc.poll() is None:
                try:
                    child.proc.send_signal(signal.SIGINT)
                except OSError:
                    pass


def _elapsed(child: Child) -> str:
    if child.wall_ms is not None:
        return fmt_ms(child.wall_ms)
    return fmt_ms(int((time.monotonic() - child.started) * 1000))


def progress_line(children: list[Child]) -> str:
    parts = []
    for child in children:
        state = "运行中" if child.exit_code is None else f"退出码 {child.exit_code}"
        last = (child.tail[-1] if child.tail else "").strip()[:60]
        parts.append(f"{child.lane.label} {state} {_elapsed(child)} 输出 {child.lines} 行 {last}")
    return "进度：" + "；".join(parts)


def _finish(child: Child, code: int) -> None:
    child.exit_code = code
    child.wall_ms = int((time.monotonic() - child.started) * 1000)
    drain(child)
    if child.partial:
        child.tail.append(child.partial.decode("utf-8", errors="replace").rstrip("\r"))
        child.partial = b""
    _say(f"[{child.lane.label}] 结束，退出码 {code}，用时 {fmt_ms(child.wall_ms)}")


def wait_all(children: list[Child], interval: int = PROGRESS_SECONDS) -> None:
    pending = [child for child in children if child.proc is not None]
    next_tick = time.monotonic() + interval
    while pending:
        for child in list(pending):
            code = child.proc.poll() if child.proc is not None else 127
            if code is not None:
                _finish(child, code)
                pending.remove(child)
            else:
                drain(child)
        if pending and time.monotonic() >= next_tick:
            _say(progress_line(children))
            next_tick = time.monotonic() + interval
        if pending:
            time.sleep(1)


def record_children(run: MultiRun) -> None:
    # 本进程中途死掉时，凭这份清单找子进程和日志。
    rows = [
        {"lane": child.lane.label, "account": child.lane.cfg.account, "pid": child.proc.pid if child.proc else None, "log": str(child.log)}
        for child in run.children
    ]
    try:
        (run.out / "children.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        _say("写不了子进程清单 children.json")


# ---- 找各通道的报告 ----


def _printed_report(line: str, root: Path) -> Path | None:
    text = line.strip()
    if not text:
        return None
    try:
        # 相对路径按子进程的工作目录（仓库根）解析。
        path = (_ROOT / Path(text).expanduser()).resolve()
        found = path / "report.json"
        return found if path.parent == root.resolve() and found.is_file() else None
    except (OSError, RuntimeError, ValueError):
        return None


def _report_since(root: Path, since: float) -> tuple[Path | None, str]:
    try:
        found = [entry / "report.json" for entry in root.iterdir() if not entry.name.startswith("multi-")]
        found = [path for path in found if path.is_file() and path.stat().st_mtime >= since]
    except OSError:
        return None, "读不了报告目录"
    if len(found) == 1:
        return found[0], ""
    if not found:
        return None, "没有找到报告"
    return None, f"开跑之后报告目录里有 {len(found)} 份报告，不能确定哪份是本次的"


def find_report(tail: list[str], root: Path, since: float) -> tuple[Path | None, str]:
    # run_e2e 收尾时打印报告目录和结论（flow.close_report）；没打印时只在开跑之后恰好新出现一份报告时采用。
    for line in reversed(tail):
        found = _printed_report(line, root)
        if found is not None:
            return found, ""
    return _report_since(root, since)


def read_report(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def collect(child: Child) -> LaneResult:
    lane = child.lane
    if child.proc is None:
        return LaneResult(lane.label, lane.cfg.account, child.exit_code, None, child.wall_ms, "", len(child.cases), child.note)
    path, note = find_report(child.tail, lane.report_root, child.started_at - 1)
    name = path.parent.name if path else ""
    return LaneResult(lane.label, lane.cfg.account, child.exit_code, read_report(path), child.wall_ms, name, len(child.cases), note)


# ---- 入口 ----


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按分片表把用例分到几条通道同时跑，合并报告；结论为 PASS 即可作完整回归结论。")
    parser.add_argument("--lane", action="append", default=[], metavar="通道名=配置文件", help="可重复；名须在分片表里，类里的线可不给")
    parser.add_argument("--assign", default=None, metavar="文件", help="分片表（缺省仓库 shards.json，没有则用样例）")
    parser.add_argument(daytime.FLAG, action="store_true", help="配置了 business_hours 时仍在业务时间内运行")
    parser.add_argument("--watchdog-min", type=int, default=None, help="原样传给每条通道的 run_e2e")
    parser.add_argument("--force-deploy", action="store_true", help="原样传给每条通道的 run_e2e")
    parser.add_argument("--dry-run", action="store_true", help="每条通道只做只读预检和指纹")
    # 只为给出明确的拒绝说明：分片回归每条通道都要自己快照、还原并核对指纹，不能借用调试会话。
    parser.add_argument("--in-session", action="store_true", help=argparse.SUPPRESS)
    return parser


def prepare(args: argparse.Namespace, opts: Options, stamp: str) -> MultiRun:
    items = load_lanes(list(args.lane))
    catalog = known_names()
    assign_file = shard_assign.default_table(args.assign)
    planned, default_lane, owners, known_na = plan_cases(assign_file, [lane.label for lane in items], catalog)
    problem = daytime_refusal(items, opts)
    if problem:
        raise UsageError(problem)
    try:
        # 合并报告目录名带随机后缀，同一秒起两次也不会共用目录和日志。
        out = unique_report_dir(items[0].report_root, "multi-" + stamp)
    except (OSError, RuntimeError) as exc:
        raise UsageError("建不了合并报告目录：" + type(exc).__name__) from None
    children = [Child(lane, planned[lane.label], lane.report_root / (out.name + ".log")) for lane in items]
    return MultiRun(children, catalog, opts, out, default_lane, owners, known_na)


def _install(forward: Forwarder) -> dict:
    wanted = {signal.SIGINT: forward, signal.SIGTERM: forward}
    if hasattr(signal, "SIGHUP"):
        # 终端断开不结束本进程：还要等子进程还原、写合并报告。
        wanted[signal.SIGHUP] = signal.SIG_IGN
    return {sig: signal.signal(sig, handler) for sig, handler in wanted.items()}


def execute(run: MultiRun) -> int:
    forward = Forwarder(run.children)
    previous = _install(forward)
    try:
        for child in run.children:
            if forward.hits:
                child.note = "收到中断，未启动"
                continue
            start(child, run.opts)
        record_children(run)
        wait_all(run.children)
    finally:
        for sig, old in previous.items():
            signal.signal(sig, old)
    report = merge([collect(child) for child in run.children], run.catalog, run.opts.dry_run, run.default_lane, warn_owners(run.owners, run.known_na))
    write_merged(report, run.out)
    for text in report["warnings"]:
        _say("警告：" + text)
    for text in report["reasons"]:
        _say("- " + text)
    _say(str(run.out))
    _say(report["overall"])
    return exit_code(report["overall"])


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.in_session:
        print(IN_SESSION_REFUSED)
        return 2
    opts = Options(args.dry_run, args.allow_daytime, args.watchdog_min, args.force_deploy)
    try:
        run = prepare(args, opts, time.strftime("%Y%m%d-%H%M%S"))
    except UsageError as exc:
        print(exc)
        return 2
    return execute(run)


if __name__ == "__main__":
    raise SystemExit(main())
