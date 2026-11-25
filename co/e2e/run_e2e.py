"""测试账套上的 u8co 端到端测试。默认走已安装服务，结束时用快照还原。站点值见 co/e2e/e2e.example.json。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from co.e2e import daytime
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.select import merge_changed, parse_names, reject_unknown
from co.e2e.engine import run
from co.e2e.plan import clamp_minutes
from co.e2e.settings import ConfigError, conf

# 并行是试验性的：实测 4 次全量并行里有 2 次多个 STA 工人同时卡在 U8 组件里（互相占着锁），桥看门狗重启服务。
# 缺省串行；--jobs N 显式打开。
DEFAULT_JOBS = 1
MAX_JOBS = 8
_ROOT = Path(__file__).resolve().parents[2]


class ChangedError(RuntimeError):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="在测试账套上跑 u8co，结束时用数据库快照还原。")
    parser.add_argument("--dry-run", action="store_true", help="只做只读预检和指纹")
    parser.add_argument(
        "--watchdog-min",
        type=int,
        default=None,
        help="还原看门狗分钟数，限制在 10 到 120，缺省取配置 watchdog.run_minutes（25）",
    )
    parser.add_argument("--cases", default="", help="逗号分隔的用例名，默认全部")
    parser.add_argument(
        "--jobs",
        type=int,
        default=DEFAULT_JOBS,
        help=f"同时跑几条资源不冲突的用例，1 到 {MAX_JOBS}，缺省 {DEFAULT_JOBS}（串行）；试验性，见 docs/testing.md",
    )
    parser.add_argument("--serial", action="store_true", help="一条接一条跑（排查、二分时用）")
    parser.add_argument(
        "--changed-since",
        default="",
        metavar="GIT_REF",
        help="只跑与 GIT_REF...HEAD 及未提交改动相关的用例（总带 health、login）。开发自测用，发版前必须跑全量",
    )
    parser.add_argument(
        "--force-deploy",
        action="store_true",
        help="服务器上的桥与本机源码哈希一致时也重新编译部署（缺省一致就跳过）",
    )
    parser.add_argument(daytime.FLAG, action="store_true", help="配置了 business_hours 时仍在业务时间内运行")
    parser.add_argument(
        "--in-session",
        action="store_true",
        help="在本通道已打开的 session 会话里跑：不预检、不快照、不还原，还原由 session close/reset 负责。开发自测用，不是完整回归结论",
    )
    parser.add_argument(
        "--estimate-min",
        type=int,
        default=None,
        help="--in-session 的预计用时（分钟），看门狗须至少还剩这么多再加 5 分钟；缺省取配置 watchdog.run_minutes",
    )
    return parser


def pick_jobs(jobs: int, serial: bool) -> tuple[int, str]:
    if serial:
        return 1, ""
    if jobs < 1 or jobs > MAX_JOBS:
        return 0, f"--jobs 必须在 1 到 {MAX_JOBS} 之间"
    return jobs, ""


def _git(args: list[str]) -> str:
    try:
        done = subprocess.run(
            ["git", "-C", str(_ROOT), *args],
            check=False,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ChangedError("git 调用失败：" + type(exc).__name__) from None
    if done.returncode != 0:
        raise ChangedError("git 失败：" + done.stderr.decode("utf-8", errors="replace").strip()[:200])
    return done.stdout.decode("utf-8", errors="replace")


def parse_porcelain(text: str) -> list[str]:
    """`git status --porcelain -z` 的输出 → 路径。改名、复制两边都算。"""
    paths: list[str] = []
    items = text.split("\0")
    index = 0
    while index < len(items):
        item = items[index]
        index += 1
        if len(item) < 4:
            continue
        paths.append(item[3:])
        if item[0] in "RC" and index < len(items) and items[index]:
            paths.append(items[index])
            index += 1
    return paths


def changed_paths(ref: str) -> list[str]:
    """REF...HEAD 的改动加上工作区未提交（含未跟踪）的改动，路径相对仓库根。只读 git。

    diff 关掉改名检测（--no-renames），改名的新旧两边都算改动；status 的改名本来就报两边。
    """
    if ref == "" or ref.startswith("-") or any(ch.isspace() for ch in ref):
        raise ChangedError("--changed-since 的 git 引用不合法")
    committed = [item for item in _git(["diff", "--name-only", "--no-renames", "-z", ref + "...HEAD", "--"]).split("\0") if item]
    local = parse_porcelain(_git(["status", "--porcelain", "-z", "--untracked-files=all"]))
    return sorted(set(committed) | set(local))


def cases_for_changes(ref: str, cases_text: str) -> str:
    """改动选出的用例（coverage.select_changed，总带 health、login）与 --cases 取交集（select.merge_changed）。

    每条改动命中哪条规则打印到 stderr。全部选中且没给 --cases 时返回空串，报告里照旧写「全部」。
    """
    try:
        from co.e2e.casespec.coverage import explain, select_changed
    except (ImportError, SyntaxError) as exc:
        raise ChangedError("co/e2e/casespec/coverage.py 不可用，不能按改动选用例：" + type(exc).__name__) from None
    catalog = known_names()
    paths = changed_paths(ref)
    for path, rule, picked in explain(paths):
        print(f"{path}  规则 {rule or '（无）'}  →  {picked}", file=sys.stderr)
    chosen = merge_changed(parse_names(cases_text), select_changed(paths, list(catalog)))
    print("按改动选出的用例：" + ",".join(chosen), file=sys.stderr)
    if cases_text.strip() == "" and len(chosen) == len(catalog):
        return ""
    return ",".join(chosen)


def _resolve_cases(args: argparse.Namespace) -> tuple[str, str]:
    problem = reject_unknown(args.cases, known_names())
    if problem or not args.changed_since:
        return args.cases, problem
    try:
        return cases_for_changes(args.changed_since, args.cases), ""
    except ChangedError as exc:
        return "", str(exc)


def in_session_problem(args: argparse.Namespace) -> str:
    """--in-session 与哪些选项不能同用。--estimate-min 只配 --in-session。"""
    if not args.in_session:
        return "--estimate-min 只能和 --in-session 一起用" if args.estimate_min is not None else ""
    if args.dry_run:
        return "--in-session 不能和 --dry-run 同用：会话内运行不做预检和指纹"
    if args.watchdog_min is not None:
        return "--in-session 不用自己的看门狗，不能给 --watchdog-min；预计用时用 --estimate-min"
    if args.estimate_min is not None and args.estimate_min < 1:
        return "--estimate-min 必须是正整数"
    return ""


def estimate_minutes(args: argparse.Namespace) -> int:
    return conf().run_minutes if args.estimate_min is None else args.estimate_min


def _refusal(args: argparse.Namespace) -> str:
    try:
        cfg = conf()
    except ConfigError as exc:
        return str(exc)
    if args.in_session:
        # 会话内运行占用 U8 许可的时长按预计用时算。
        return daytime.refusal(cfg.business_hours, args.allow_daytime, False, minutes=estimate_minutes(args)) or ""
    # 看门狗分钟数与 engine.run 的算法一致：这段时间内都会占用 U8 许可。
    minutes = clamp_minutes(cfg.run_minutes if args.watchdog_min is None else args.watchdog_min)
    return daytime.refusal(cfg.business_hours, args.allow_daytime, args.dry_run, minutes=minutes) or ""


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    jobs, problem = pick_jobs(args.jobs, args.serial)
    if not problem:
        problem = in_session_problem(args)
    if not problem:
        cases, problem = _resolve_cases(args)
    if not problem:
        problem = _refusal(args)
    if problem:
        print(problem)
        return 2
    if args.in_session:
        from co.e2e.insession import run_in_session

        opts = {"cases": cases, "jobs": jobs, "force_deploy": args.force_deploy}
        return run_in_session(opts, estimate_minutes(args))
    return run(args.dry_run, args.watchdog_min, cases, jobs, args.force_deploy)


if __name__ == "__main__":
    raise SystemExit(main())
