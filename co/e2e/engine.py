"""编排一次回归：预检、快照、已安装服务上的测试、必定还原。"""

from __future__ import annotations

import json
import signal

from co.e2e.bridge_job import deploy_service
from co.e2e.context import Run
from co.e2e.flow import (
    _must,
    arm_snapshot,
    cleanup,
    close_report,
    do_fingerprint,
    do_preflight,
    local_day,
    local_stamp,
    open_run,
    prepare_tree,
    utc_stamp,
)
from co.e2e.guard import capture_msgcenter
from co.e2e.plan import clamp_minutes
from co.e2e.remote import release_all
from co.e2e.settings import conf
from co.e2e.suite import run_suite
from co.e2e.timing import phase


def _on_term(signum, frame) -> None:
    raise KeyboardInterrupt


def _cred_ready() -> None:
    from pathlib import Path

    path = Path(conf().operator_file)
    if not path.is_file():
        raise RuntimeError("找不到测试凭证")
    if path.stat().st_mode & 0o077:
        raise RuntimeError("测试凭证权限过宽")


def do_pick(ctx: Run) -> None:
    result = ctx.remote.run_script("pick-docs.ps1", None, 180)
    _must(ctx, "pick-docs", result)
    local = ctx.report_dir / "pick-docs.json"
    ctx.remote.download(conf().remote("out", "pick-docs.json"), local)
    data = json.loads(local.read_text(encoding="utf-8"))
    ctx.sale_id = data.get("sale_id") or None
    ctx.dispatch_id = data.get("dispatch_id") or None
    ctx.sale_mode = data.get("sale_mode") or "verify-first"
    ctx.dispatch_mode = data.get("dispatch_mode") or "verify-first"
    ctx.dispatch_downstream_id = data.get("dispatch_downstream_id") or None


def _prep(ctx: Run) -> None:
    prepare_tree(ctx)
    do_preflight(ctx)
    do_fingerprint(ctx, "before")
    if ctx.dry_run:
        return
    _cred_ready()
    capture_msgcenter(ctx, "open")
    from co.e2e.session_ops import write_both_markers

    write_both_markers(ctx, "run")
    arm_snapshot(ctx)
    do_fingerprint(ctx, "snapshot")
    do_pick(ctx)


def sequence(ctx: Run) -> None:
    # 各阶段的起止时刻和耗时写进报告的 phases（timing.phase）。
    with phase(ctx, "prep"):
        _prep(ctx)
    if ctx.dry_run:
        return
    with phase(ctx, "deploy"):
        deploy_service(ctx)
    with phase(ctx, "cases"):
        run_suite(ctx, local_day(), local_stamp(), utc_stamp())


def _fail_soft(ctx: Run, prefix: str, exc: Exception) -> None:
    ctx.fail(prefix + type(exc).__name__ + ": " + str(exc)[:200])


def _wind_down(ctx: Run) -> None:
    # 停服务、还原、再启动服务之前关上闸门：万一还有没退出的用例线程，它也发不出桥调用和 SQL，
    # 不会在还原之后往测试账套里写（casespec/gate.py）。收尾 DELETE 会绕过 stop，本次运行若做过快照，
    # 先置 cleanup_closed，restore_row 才不会在 SINGLE_USER/RESTORE 期间往 UFDATA 写。
    # 正常跑完时没有这样的线程，置位无副作用。会话内运行不走这里。
    ctx.stop.set()
    if ctx.snapshots_made:
        ctx.cleanup_closed.set()
    try:
        cleanup(ctx)
    except Exception as exc:
        _fail_soft(ctx, "清理中断: ", exc)
    if ctx.before is not None and not ctx.dry_run and ctx.snapshots_made:
        try:
            do_fingerprint(ctx, "post")
        except Exception:
            ctx.fail("测试后指纹失败")


_HELD = (signal.SIGINT, signal.SIGTERM)
HELD_NOTE = "还原期间收到中断信号，已忽略：还原和报告完成后才退出"


def hold_signals(ctx: Run) -> dict:
    """还原开始到报告写完之前，Ctrl-C / SIGTERM 只记一条警告，不打断：半截还原会把快照和标记留在服务器上。"""

    def handler(signum, frame) -> None:
        if HELD_NOTE not in ctx.warnings:
            ctx.warnings.append(HELD_NOTE)
        try:
            print(HELD_NOTE, flush=True)
        except (OSError, ValueError):
            pass

    return {sig: signal.signal(sig, handler) for sig in _HELD}


def release_signals(previous: dict) -> None:
    for sig, old in previous.items():
        signal.signal(sig, old)


def run(dry_run: bool, watchdog_min: int | None = None, cases: str = "", jobs: int = 1, force_deploy: bool = False) -> int:
    signal.signal(signal.SIGTERM, _on_term)
    try:
        ctx = open_run(dry_run)
        ctx.watchdog_min = clamp_minutes(conf().run_minutes if watchdog_min is None else watchdog_min)
        ctx.case_names = cases
        # 试运行不跑用例，报告里不写并行度。
        ctx.jobs = 1 if dry_run else jobs
        ctx.force_deploy = force_deploy
        try:
            sequence(ctx)
        except KeyboardInterrupt:
            ctx.fail("收到中断，开始回滚")
        except Exception as exc:
            ctx.fail(type(exc).__name__ + ": " + str(exc)[:300])
        held = hold_signals(ctx)
        try:
            with phase(ctx, "revert"):
                _wind_down(ctx)
            if ctx.owner == "run":
                from co.e2e.session_ops import finish_run_marker

                finish_run_marker(ctx)
            return close_report(ctx)
        finally:
            release_signals(held)
    finally:
        release_all()
