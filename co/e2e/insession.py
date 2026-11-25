"""run_e2e --in-session：在本通道已由 session open 打开的会话里跑用例。开发自测用，不是完整回归结论。

不做准备、预检、指纹、快照、看门狗和还原：会话已经持有两个快照和看门狗，还原由 session close（或 reset）负责。
桥源码有改动时照常编译部署（与回归同一套跳过判定），然后跑用例，写普通报告（mode 为 in-session，
rollback_verified 为 null）。multi 和 multi_report 不收这种报告。

锁：整个运行持本通道 session.lock 的独占锁（lanelock.session_lock(True)）。close、reset 也要独占，
运行期间它们拿不到锁、直接拒绝，不会在用例写到一半时还原；deploy 和别的 call、sql、comprobe 同样被挡住，
免得运行中途换桥、或别的写入混进用例的数据。锁不排队：拿不到就拒绝本次运行。
"""

from __future__ import annotations

import json
import signal
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from co.e2e.bridge_job import deploy_service
from co.e2e.context import Run
from co.e2e.flow import local_day, local_stamp, make_remote, utc_stamp
from co.e2e.guard import fetch_status, start_if_idle
from co.e2e.lanelock import session_lock
from co.e2e.markers import marker_owner, read_linux_marker
from co.e2e.remote import Remote, RemoteError, release_all
from co.e2e.report import exit_code, judge, render_markdown
from co.e2e.safety import minutes_left, text_list, unique_report_dir, window_block_reason
from co.e2e.settings import conf
from co.e2e.timing import phase

MODE = "in-session"
ROLLBACK_NOTE = "会话内运行，还原由 session close 负责"
REMINDER = "会话内运行结束；需要干净基准时用 session reset，收工用 session close"
BUSY = "不能在会话内运行：这条通道还有别的 session 命令（open、deploy、close、reset、call、sql、audit）或探针在跑，等它结束再执行"
# 看门狗至少要比预计用时多留的分钟数。
MARGIN_MIN = 5


def session_refusal(local: dict | None, status: dict, estimate_min: int, now: datetime) -> str:
    """能否在会话里跑：要拒绝就返回中文说明，否则返回空串。纯判断，不连服务器。"""
    if local is None:
        return "没有打开的会话：先执行 python -m co.e2e.session open"
    owner = marker_owner(local, status)
    if owner != "session":
        return "本通道的会话不是 session open 打开的（所有者：" + (owner or "无") + "）"
    if local.get("keep"):
        return "会话正在 close --keep"
    if not status.get("marker") or str(status.get("owner") or "session") != "session":
        return "服务器上没有 session open 留下的会话标记"
    blocked = window_block_reason(status, now)
    if blocked:
        return blocked
    # 与 session reset 同一个做法：同一条窗口规则往后推预计用时，即看门狗至少还剩 预计用时 + 5 分钟。
    if window_block_reason(status, now + timedelta(minutes=estimate_min)):
        left = minutes_left(str(status.get("next_run") or ""), now)
        need = estimate_min + MARGIN_MIN
        return f"看门狗剩余 {left} 分钟，不足 {need} 分钟（预计用时 {estimate_min} + {MARGIN_MIN}）；先延长看门狗（session extend）或用 --estimate-min 调小预计用时"
    return ""


def after_run_problem(status: dict) -> str:
    """运行结束后再看一次：看门狗中途还原过，或快照不在了，用例结果就不可信。"""
    if status.get("done"):
        return "运行期间看门狗已还原测试账套，用例结果不可信"
    found = set(text_list(status.get("snapshots")))
    if any(name not in found for name in conf().snapshots):
        return "运行结束时快照不在，用例结果不可信"
    return ""


def in_session_fields(report: dict) -> dict:
    """把普通报告改成会话内运行的报告：不声称已还原，判定只看用例与失败项。"""
    failed = bool(report.get("reasons"))
    verdict = judge(False, failed, True, list(report.get("tests") or []))
    return {
        "mode": MODE,
        "overall": verdict,
        "rollback_verified": None,
        "rollback_note": ROLLBACK_NOTE,
        "fingerprint_diff": [],
        "manual": "",
        "gate_evidence": False,
    }


def write_report(ctx: Run, session_dir: str) -> int:
    # 先按普通报告组装（会把 rollback_verified 判成 false），再改成会话内的字段，避免 report.py 多一条分支。
    from co.e2e.flow import _report_state
    from co.e2e.report import assemble

    report = assemble(_report_state(ctx))
    report.update(in_session_fields(report))
    report["session_report_dir"] = session_dir
    (ctx.report_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # render_markdown 把 None 写成 none，这里按 JSON 写成 null。
    text = render_markdown(report).replace("rollback_verified：none", "rollback_verified：null")
    (ctx.report_dir / "report.md").write_text(text, encoding="utf-8")
    print(ctx.report_dir)
    print(report["overall"])
    return exit_code(report["overall"])


def _now() -> datetime:
    return datetime.now(ZoneInfo(conf().timezone))


def check_session(remote: Remote, local: dict | None, estimate_min: int) -> str:
    """开跑前的全部核对，要拒绝就返回中文说明。本机标记不对时不连服务器。"""
    if local is None:
        return session_refusal(None, {}, estimate_min, _now())
    folder = Path(str(local.get("report_dir") or ""))
    if not folder.is_dir():
        return "会话目录不存在"
    # 持独占锁，可以像 session deploy 一样重锁远程目录（ensure=True）。
    status = fetch_status(remote, folder, ensure=True)
    reason = session_refusal(local, status, estimate_min, _now())
    if reason:
        return reason
    # 与 session call / sql 相同：服务器端再核对一次窗口（session-gate.ps1）。
    result = remote.run_script("session-gate.ps1", None, 60)
    if result.code != 0:
        return (result.out or "会话窗口已关闭")[:300]
    return ""


def _check_after(ctx: Run) -> None:
    try:
        problem = after_run_problem(fetch_status(ctx.remote, ctx.report_dir, ensure=False))
    except (RuntimeError, RemoteError, OSError, json.JSONDecodeError) as exc:
        problem = "运行结束后读不到会话状态：" + str(exc)[:120]
    if problem:
        ctx.fail(problem)


def sequence(ctx: Run) -> None:
    from co.e2e.engine import _cred_ready, do_pick
    from co.e2e.suite import run_suite

    _cred_ready()
    with phase(ctx, "deploy"):
        deploy_service(ctx)
    with phase(ctx, "cases"):
        do_pick(ctx)
        run_suite(ctx, local_day(), local_stamp(), utc_stamp())
    _check_after(ctx)


def _new_run(remote: Remote, local: dict) -> Run:
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    ctx = Run(False, unique_report_dir(Path(conf().report_dir), stamp + "-insession"), remote)
    ctx.owner = "session"
    if local.get("watchdog_min"):
        ctx.watchdog_min = int(local["watchdog_min"])
    return ctx


def _on_term(signum, frame) -> None:
    raise KeyboardInterrupt


def _body(ctx: Run, local: dict) -> int:
    try:
        sequence(ctx)
    except KeyboardInterrupt:
        ctx.stop.set()
        ctx.fail("收到中断，已停止跑用例；不还原，会话照旧打开")
    except Exception as exc:
        ctx.stop.set()
        ctx.fail(type(exc).__name__ + ": " + str(exc)[:300])
        # 与 session deploy 相同：部署中途失败时服务可能停着，库空闲就拉起来。
        start_if_idle(ctx)
    return write_report(ctx, str(local.get("report_dir") or ""))


def _locked(remote: Remote | None, opts: dict, estimate_min: int) -> int:
    local = read_linux_marker()
    remote = remote or make_remote()
    reason = check_session(remote, local, estimate_min)
    if reason or local is None:
        print("不能在会话内运行：" + reason)
        return 2
    ctx = _new_run(remote, local)
    ctx.case_names = str(opts.get("cases") or "")
    ctx.jobs = int(opts.get("jobs") or 1)
    ctx.force_deploy = bool(opts.get("force_deploy"))
    return _body(ctx, local)


def run_in_session(opts: dict, estimate_min: int, remote: Remote | None = None) -> int:
    """opts：cases（逗号分隔，空串为全部）、jobs、force_deploy。返回退出码：0 通过、1 失败、2 拒绝。"""
    signal.signal(signal.SIGTERM, _on_term)
    entered = False
    try:
        # 独占锁（见模块说明）：close、reset、deploy、call、sql 在运行期间都拿不到锁。
        with session_lock(True):
            entered = True
            code = _locked(remote, opts, estimate_min)
        if code != 2:
            print(REMINDER)
        return code
    except KeyboardInterrupt:
        print("收到中断")
        return 1
    except (RuntimeError, OSError, RemoteError, json.JSONDecodeError) as exc:
        if not entered and isinstance(exc, RuntimeError):
            print(BUSY)
            return 2
        print("不能在会话内运行：" + str(exc)[:300])
        return 1
    finally:
        release_all()
