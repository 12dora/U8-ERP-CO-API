"""读路由性能测量：对一条测试通道的桥顺序调用各读路由，记录每次的耗时、状态码、条目数和响应字节数。

    python -m co.e2e.perf_reads [--out <dir>] [--only lists,poll] [--repeat 3]

按 run_e2e 的方式用环境变量选通道，配置经 settings.conf() 读（co/e2e/lane.example.json）：桥地址和密钥取
bridge.url、bridge.secret_file，操作员取 account.operator_file。口令和密钥不打印、不写进输出。只调读路由，不开调试会话、不碰数据库快照。
输出 perf.json 和 perf.md，缺省写到通道 report.dir 下的 perf-<时间>/。
退出码：0 跑完（个别调用失败也是 0，失败列在输出里）；2 配置、凭证或参数有误。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from co.client.u8co_client import U8Call
from co.e2e import settings
from co.e2e.bridge_job import read_service_secret
from co.e2e.cases import load_operator
from co.e2e.perf_reads_groups import PerfCtx, run_aging, run_archives, run_gl, run_lists, run_process, run_search
from co.e2e.perf_reads_poll import run_deletescan, run_health, run_poll
from co.e2e.perf_reads_probe import MeteredClient, Probe
from co.e2e.perf_reads_report import build_doc, write_outputs
from co.e2e.settings_parse import ConfigError
from co.e2e.suite import login_delta, read_login_counters

RUNNERS = {
    "health": run_health,
    "lists": run_lists,
    "archives": run_archives,
    "search": run_search,
    "aging": run_aging,
    "gl": run_gl,
    "process": run_process,
    "poll": run_poll,
    "deletescan": run_deletescan,
}
GROUPS = tuple(RUNNERS)
TIMEOUT = 180.0


class Setup:
    def __init__(self, cfg: settings.E2EConfig, call: U8Call, secret: str) -> None:
        self.cfg = cfg
        self.call = call
        self.secret = secret


def parse_only(text: str | None) -> tuple[str, ...]:
    """--only 的组名，逗号分隔；按 GROUPS 的固定次序执行。省略即全部。"""
    if not text:
        return GROUPS
    wanted = {part.strip() for part in text.split(",") if part.strip()}
    unknown = sorted(wanted - set(GROUPS))
    if unknown or not wanted:
        raise ConfigError("--only 只收这些组：" + ",".join(GROUPS))
    return tuple(name for name in GROUPS if name in wanted)


def today_in(zone: str) -> str:
    return datetime.now(ZoneInfo(zone)).date().isoformat()


def load_setup() -> Setup:
    cfg = settings.conf()
    operator = load_operator(Path(cfg.operator_file))
    secret = read_service_secret(Path(cfg.secret_file))
    call = U8Call(cfg.account, operator.year, operator.code, operator.password, today_in(cfg.timezone))
    return Setup(cfg, call, secret)


def run_groups(ctx: PerfCtx, groups: tuple[str, ...]) -> dict[str, float]:
    walls: dict[str, float] = {}
    for name in groups:
        ctx.probe.group = name
        start = ctx.probe.clock()
        RUNNERS[name](ctx)
        walls[name] = round((ctx.probe.clock() - start) * 1000.0, 1)
    return walls


def measure(ctx: PerfCtx, groups: tuple[str, ...], meta: dict) -> dict:
    # 整轮前后各读一次 /health 的登录计数；只走 SQL 的读路由在 AuthCache 命中后不应新开登录。
    before = read_login_counters(ctx.client)
    meta["started"] = _utc()
    walls = run_groups(ctx, groups)
    meta["finished"] = _utc()
    after = read_login_counters(ctx.client)
    meta["logins"] = login_delta(before, after)
    meta["logins_before"] = before
    meta["logins_after"] = after
    return build_doc(meta, ctx.probe.records, walls, ctx.notes)


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="对测试通道的桥顺序调用读路由，测单个调用方的延迟。")
    parser.add_argument("--out", default="", help="输出目录，缺省为通道 report.dir/perf-<时间>")
    parser.add_argument("--only", default="", help="只跑这些组，逗号分隔：" + ",".join(GROUPS))
    parser.add_argument("--repeat", type=int, default=1, help="poll 组的轮数（缺省 1）")
    parser.add_argument("--timeout", type=float, default=TIMEOUT, help="单次调用超时秒数（缺省 180）")
    return parser


def _prepare(args: argparse.Namespace) -> tuple[tuple[str, ...], Setup, MeteredClient]:
    if args.repeat < 1 or args.repeat > 100:
        raise ConfigError("--repeat 必须是 1 到 100")
    if args.timeout <= 0:
        raise ConfigError("--timeout 必须大于 0")
    groups = parse_only(args.only)
    setup = load_setup()
    client = MeteredClient(setup.cfg.bridge_url, setup.secret, timeout=args.timeout)
    return groups, setup, client


def _print_summary(doc: dict, paths: tuple[Path, Path]) -> None:
    for name, row in doc["groups"].items():
        print(f"{name}: 调用 {row['calls']}，失败 {row['failed']}，墙钟 {row.get('wall_ms')} ms")
    print(f"失败调用 {doc['failed']}；输出 {paths[0]}、{paths[1]}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        groups, setup, client = _prepare(args)
    except (ConfigError, RuntimeError, OSError, ValueError) as exc:
        print("配置错误：" + str(exc)[:300], file=sys.stderr)
        return 2
    probe = Probe(client, needles=[setup.call.password, setup.secret])
    ctx = PerfCtx(probe, setup.call, setup.call.date, repeat=args.repeat)
    cfg = setup.cfg
    meta = {
        "lane": cfg.lane or "-", "acc": cfg.account, "year": setup.call.year, "date": setup.call.date,
        "bridge": cfg.bridge_url, "groups": ",".join(groups), "repeat": args.repeat,
    }
    doc = measure(ctx, groups, meta)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = Path(args.out).expanduser() if args.out else Path(cfg.report_dir) / ("perf-" + stamp)
    _print_summary(doc, write_outputs(out, doc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
