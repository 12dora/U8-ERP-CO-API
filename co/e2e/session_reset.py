"""session reset：把测试账套还原到本次会话的快照，会话保持打开。

和 close 同一条还原路径（停服务、snapshot-revert.ps1、同会话指纹、启动服务），区别只有：快照不删（-KeepSnapshot）、
看门狗不取消、标记不清，不读审计、登录数和 MsgCenter，也不另取测试后指纹。看门狗的到点时间不变。
每次 reset 的步骤日志写在会话目录的 reset-<n>/ 下，结果追加到 resets.json，session close 的报告会提到。
SQL 工人（sql-worker.ps1）每条查询起一次 sqlcmd，不常驻数据库连接；reset 独占通道锁，期间没有 sql 在跑，
之后的 session sql 照常起新工人、新连接。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from co.e2e.context import Run
from co.e2e.flow import (
    _bad_revert,
    _diff_of,
    _fetch_inrevert,
    _load_revert_side,
    _log,
    make_remote,
    stop_service,
    utc_stamp,
)
from co.e2e.guard import clear_local_inrevert, fetch_status, start_if_idle, wait_until_idle
from co.e2e.markers import close_plan, marker_owner, read_linux_marker
from co.e2e.remote import Remote, RemoteError, RemoteResult, script_call
from co.e2e.safety import revert_failure_text, text_list, window_block_reason
from co.e2e.settings import conf

RESETS_FILE = "resets.json"
_KEEP_SWITCH = "[switch]$KeepSnapshot"
_KEEP_DONE = "snapshot-revert ok keep-snapshot"
_SHOWN_DIFF = 20
# reset 本身约 1 分钟，再留出跑下一轮探针的时间；不足时直接 close。
_RESET_MARGIN_MIN = 15
_WATCHDOG_FIRED = "看门狗已经还原（快照不在或已有 done），请执行 session close"


def _session_report() -> tuple[dict, Path]:
    local = read_linux_marker()
    if local is None:
        raise RuntimeError("没有打开的会话，不能 reset")
    report = Path(str(local.get("report_dir") or ""))
    if not report.is_dir():
        raise RuntimeError("会话目录不存在")
    return local, report


def reset_refusal(local: dict | None, status: dict, now: datetime) -> str:
    # 与 close 同样拒绝别的运行的快照；窗口规则与 sql、call 相同（两个快照都在、没有 done、看门狗至少还有 5 分钟）。
    if close_plan(local, status, "session") == "foreign":
        return "这是另一个运行的快照，session reset 不会动它。"
    if marker_owner(local, status) != "session":
        return "只有 session open 打开的会话才能 reset。"
    blocked = window_block_reason(status, now)
    if blocked:
        return "不能 reset：" + blocked
    # 同一条规则往后推 10 分钟：剩余不足 5 + 10 分钟就拒绝。
    if window_block_reason(status, now + timedelta(minutes=_RESET_MARGIN_MIN - 5)):
        return "看门狗剩余不足 " + str(_RESET_MARGIN_MIN) + " 分钟，请直接 session close"
    return ""


def next_reset_dir(report: Path) -> Path:
    number = 1
    while (report / ("reset-" + str(number))).exists():
        number += 1
    folder = report / ("reset-" + str(number))
    folder.mkdir()
    return folder


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _bind(remote: Remote, report: Path) -> Run:
    ctx = Run(False, next_reset_dir(report), remote)
    ctx.owner = "session"
    for attr, name in (("reference", "fingerprint-snapshot.json"), ("before", "fingerprint-before.json")):
        data = _read_json(report / name)
        if isinstance(data, dict):
            setattr(ctx, attr, data)
    return ctx


def _remote_step(ctx: Run, name: str, label: str, timeout: int) -> RemoteResult:
    try:
        result = ctx.remote.run_script(name, None, timeout)
    except RemoteError as exc:
        result = RemoteResult(1, exc.output)
    _log(ctx, label, result)
    return result


def _assert_keep_script(ctx: Run) -> None:
    # 旧版 snapshot-revert.ps1 没有 CmdletBinding，不认识的 -KeepSnapshot 会被吞掉，照常删快照。先确认服务器上是新版。
    local = ctx.report_dir / "snapshot-revert.ps1"
    try:
        ctx.remote.download(conf().remote("snapshot-revert.ps1"), local)
        text = local.read_text(encoding="utf-8-sig", errors="replace")
    except (OSError, RemoteError):
        ctx.add_step("script-check", False)
        raise RuntimeError("读不到服务器上的 snapshot-revert.ps1，不还原") from None
    finally:
        local.unlink(missing_ok=True)
    ok = _KEEP_SWITCH in text
    ctx.add_step("script-check", ok)
    if not ok:
        raise RuntimeError("服务器上的 snapshot-revert.ps1 是旧版，不能保留快照。请先 session close，再重新 open")


def _preflight(ctx: Run) -> None:
    if ctx.reference is None and ctx.before is None:
        raise RuntimeError("会话目录里没有快照指纹，无法核对还原结果")
    gate = _remote_step(ctx, "session-gate.ps1", "session-gate", 60)
    if gate.code != 0:
        raise RuntimeError(("服务器上的会话窗口核对没通过 " + _first_line(gate.out)).strip())
    _assert_keep_script(ctx)


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:300]
    return ""


def _fail_revert(ctx: Run, text: str) -> bool:
    ctx.fail(revert_failure_text(text) or "还原失败。快照和看门狗都保留。")
    return False


def _revert_keep(ctx: Run) -> bool:
    body = script_call("snapshot-revert.ps1") + " -KeepSnapshot"
    try:
        result = ctx.remote.run_body(body, 1800)
    except RemoteError as exc:
        _log(ctx, "revert", RemoteResult(1, exc.output))
        return _fail_revert(ctx, exc.output)
    _log(ctx, "revert", result)
    if result.code != 0 or _bad_revert(result.out):
        return _fail_revert(ctx, result.out)
    if _KEEP_DONE not in result.out:
        ctx.fail("还原脚本没有确认保留快照。请先 session status 查看快照，再 session close")
        return False
    return True


def _restore(ctx: Run) -> None:
    wait_until_idle(ctx)
    count = len(ctx.reasons)
    stop_service(ctx)
    if len(ctx.reasons) > count:
        raise RuntimeError("服务没停下，不还原。快照和看门狗都保留")
    clear_local_inrevert(ctx.report_dir)
    ctx.reverted = _revert_keep(ctx)
    if not ctx.reverted:
        ctx.fail("两个库可能只还原了一个，请再 reset 或 close")
        return
    _load_revert_side(ctx)
    _fetch_inrevert(ctx)


def _reset_remote(ctx: Run) -> None:
    try:
        _preflight(ctx)
    except (RuntimeError, RemoteError, OSError) as exc:
        ctx.fail(str(exc)[:300])
        return
    try:
        _restore(ctx)
    except (RuntimeError, RemoteError, OSError) as exc:
        ctx.fail(str(exc)[:300])
    finally:
        start_if_idle(ctx)
    _check_window_after(ctx)


def _check_window_after(ctx: Run) -> None:
    # 还原期间看门狗可能到点：快照没了或有 done，这次的结果不算数，只能 close。
    if not ctx.reverted:
        return
    try:
        status = fetch_status(ctx.remote, ctx.report_dir)
    except (RuntimeError, RemoteError, OSError, json.JSONDecodeError) as exc:
        ctx.fail("还原后读不到会话状态 " + str(exc)[:200])
        return
    found = set(text_list(status.get("snapshots")))
    if not set(conf().snapshots) <= found or status.get("done"):
        ctx.fail(_WATCHDOG_FIRED)


def _verdict(ctx: Run) -> list[str]:
    if ctx.after is None:
        if not ctx.failed:
            ctx.fail("没有还原会话内的指纹，不能证明已经还原")
        return []
    diff = _diff_of(ctx)
    if diff:
        ctx.fail("还原后的指纹与快照不一致")
    if ctx.failed:
        return diff
    # 服务器 out 下的 fp-inrevert-*.json 表示「还原过」；不清掉，之后的 close --keep 核对会拒绝。
    if _remote_step(ctx, "clear-inrevert.ps1", "clear-inrevert", 60).code != 0:
        ctx.fail("清理服务器上的还原指纹失败，close --keep 会拒绝；不带 --keep 的 close 不受影响")
    return diff


def _append_reset(report: Path, row: dict) -> None:
    path = report / RESETS_FILE
    rows = _read_json(path)
    if not isinstance(rows, list):
        rows = []
    rows.append(row)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def reset_note(report: Path) -> str:
    rows = _read_json(report / RESETS_FILE)
    if not isinstance(rows, list) or not rows:
        return ""
    failed = sum(1 for row in rows if not (isinstance(row, dict) and row.get("ok") is True))
    tail = "，其中 " + str(failed) + " 次失败" if failed else ""
    tail += _noted(rows, "background_tolerated", "已容忍 U8 后台表")
    tail += _noted(rows, "scheduler_notes", "U8 调度记账")
    return "本次会话中途 reset 过 " + str(len(rows)) + " 次" + tail + "，见 " + RESETS_FILE


def _noted(rows: list, key: str, label: str) -> str:
    total = 0
    for row in rows:
        items = row.get(key) if isinstance(row, dict) else None
        if isinstance(items, list):
            total += len(items)
    return "，" + label + " " + str(total) + " 行" if total else ""


def _print_outcome(ctx: Run, diff: list[str], seconds: float) -> int:
    if not ctx.failed:
        print("reset ok " + str(round(seconds)) + "s")
        for line in ctx.background_notes:
            print("U8 后台表（已容忍）：" + line)
        return 0
    print("reset 失败（" + str(round(seconds)) + "s）。会话仍然打开，可以再 reset 或 session close")
    for reason in ctx.reasons:
        print(reason)
    for line in diff[:_SHOWN_DIFF]:
        print(line)
    if len(diff) > _SHOWN_DIFF:
        print("共 " + str(len(diff)) + " 处差异")
    print(ctx.report_dir)
    return 1


def reset_session(remote: Remote | None = None) -> int:
    started = time.monotonic()
    try:
        local, report = _session_report()
    except RuntimeError as exc:
        print(str(exc))
        return 1
    remote = remote or make_remote()
    try:
        status = fetch_status(remote, report)
    except (RuntimeError, RemoteError, OSError, json.JSONDecodeError) as exc:
        print(str(exc)[:300])
        return 1
    reason = reset_refusal(local, status, datetime.now(ZoneInfo(conf().timezone)))
    if reason:
        print(reason)
        return 1
    ctx = _bind(remote, report)
    _reset_remote(ctx)
    diff = _verdict(ctx)
    seconds = time.monotonic() - started
    row = {"at": utc_stamp(), "seconds": round(seconds, 1), "ok": not ctx.failed, "diff_count": len(diff)}
    row["dir"] = ctx.report_dir.name
    # 已容忍的 U8 后台表和调度记账逐次记下，close 的报告经 reset_note 汇总，不因中途 reset 丢掉。
    row["background_tolerated"] = list(ctx.background_notes)
    row["scheduler_notes"] = list(ctx.scheduler_notes)
    _append_reset(report, row)
    return _print_outcome(ctx, diff, seconds)
