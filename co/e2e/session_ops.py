"""打开、查看、部署和关闭调试会话。还原走 flow，和回归同一条路径。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from co.e2e.bridge_job import deploy_service
from co.e2e.context import Run
from co.e2e.flow import (
    _log,
    _must,
    arm_snapshot,
    close_report,
    do_fingerprint,
    keep_fingerprint,
    do_preflight,
    make_remote,
    open_run,
    prepare_tree,
    run_close_remote,
    stop_and_keep,
    stop_and_revert,
    utc_stamp,
)
from co.e2e.guard import assert_window, capture_msgcenter, fetch_status, poll_until_idle, start_if_idle
from co.e2e.lane import fingerprint_lines
from co.e2e.markers import (
    close_plan,
    keep_refusal,
    keep_resumable,
    linux_marker_path,
    marker_payload,
    marker_owner,
    markers_cleared,
    read_linux_marker,
    refusal_reason,
    remove_linux_marker,
    write_linux_marker,
)
from co.e2e.plan import clamp_minutes
from co.e2e.remote import Remote, RemoteError
from co.e2e.safety import minutes_left, text_list, unique_report_dir, window_block_reason
from co.e2e.session_reset import reset_note
from co.e2e.settings import conf


def write_both_markers(ctx: Run, owner: str) -> None:
    ctx.owner = owner
    payload = marker_payload(ctx.report_dir, ctx.watchdog_min, utc_stamp(), owner)
    write_linux_marker(payload)
    body = ctx.report_dir / "session-body.json"
    body.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ctx.remote.upload(body, conf().remote("out", "session-body.json"))
    _must(ctx, "session-mark", ctx.remote.run_script("session-mark.ps1", None, 60))


def _open_body(ctx: Run) -> None:
    prepare_tree(ctx)
    do_preflight(ctx)
    do_fingerprint(ctx, "before")
    capture_msgcenter(ctx, "open")
    arm_snapshot(ctx)
    do_fingerprint(ctx, "snapshot")
    write_both_markers(ctx, "session")


def fail_open(ctx: Run) -> None:
    # 快照尝试之后失败：还原，不把会话标记写成已打开。
    if not ctx.snapshots_made:
        return
    stop_and_revert(ctx)


def open_session(watchdog_min: int, remote: Remote | None = None) -> int:
    reason = refusal_reason(linux_marker_path().is_file(), False)
    if reason:
        print(reason)
        return 1
    ctx = open_run(False, remote or make_remote())
    ctx.watchdog_min = clamp_minutes(watchdog_min)
    try:
        _open_body(ctx)
    except (RuntimeError, RemoteError, OSError, KeyboardInterrupt) as exc:
        ctx.fail(str(exc)[:300])
        fail_open(ctx)
        close_report(ctx)
        return 1
    print(ctx.report_dir)
    print("watchdog-min " + str(ctx.watchdog_min))
    return 0


def _stamp() -> str:
    return datetime.now(ZoneInfo(conf().timezone)).strftime("%Y%m%d-%H%M%S")


def _status_folder(local: dict | None) -> Path:
    raw = str((local or {}).get("report_dir") or "")
    if raw and Path(raw).is_dir():
        return Path(raw)
    return unique_report_dir(Path(conf().report_dir), _stamp() + "-status")


def read_server_status(remote: Remote, dest: Path | None = None) -> dict:
    # status / close 不再上传脚本。脚本只在 open 和回归开始时上传。
    return fetch_status(remote, dest or _status_folder(read_linux_marker()), ensure=False)


def _report_dir(local: dict | None, status: dict) -> Path:
    raw = str(local.get("report_dir") or "") if local else ""
    if raw == "":
        raw = str(status.get("report_dir") or "")
    if raw and Path(raw).is_dir():
        return Path(raw)
    return unique_report_dir(Path(conf().report_dir), _stamp() + "-close")


def _saved_minutes(local: dict | None, status: dict) -> int:
    if local and local.get("watchdog_min"):
        return int(local["watchdog_min"])
    if status.get("watchdog_min"):
        return int(status["watchdog_min"])
    return 0


def _saved_owner(local: dict | None, status: dict) -> str:
    if local and local.get("owner"):
        return str(local["owner"])
    return str(status.get("owner") or "")


def _load_named(ctx: Run, attr: str, name: str) -> None:
    path = ctx.report_dir / name
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(data, dict):
        setattr(ctx, attr, data)


def bind_close_run(remote: Remote, local: dict | None, status: dict) -> Run:
    ctx = Run(False, _report_dir(local, status), remote)
    minutes = _saved_minutes(local, status)
    if minutes:
        ctx.watchdog_min = minutes
    ctx.owner = _saved_owner(local, status)
    _load_named(ctx, "before", "fingerprint-before.json")
    _load_named(ctx, "reference", "fingerprint-snapshot.json")
    note = reset_note(ctx.report_dir)
    if note:
        ctx.warnings.append(note)
    return ctx


def clear_both_markers(ctx: Run) -> None:
    try:
        result = ctx.remote.run_script("session-clear.ps1", None, 60)
    except RemoteError:
        ctx.fail("服务器会话标记没删掉")
        return
    _log(ctx, "session-clear", result)
    if result.code != 0:
        ctx.fail("服务器会话标记没删掉")
        return
    remove_linux_marker()


def _post_fingerprint(ctx: Run) -> None:
    try:
        do_fingerprint(ctx, "post")
    except (RuntimeError, RemoteError, OSError):
        ctx.fail("测试后指纹失败")


def _finish_close(ctx: Run, kind: str) -> int:
    try:
        capture_msgcenter(ctx, "close")
        run_close_remote(ctx, kind)
        _post_fingerprint(ctx)
        if markers_cleared(kind, ctx.reverted, ctx.disarmed, ctx.after is not None):
            clear_both_markers(ctx)
    except KeyboardInterrupt:
        ctx.fail("收到中断")
    code = close_report(ctx)
    if ctx.after is None:
        return 1
    return code


def _finish_keep(ctx: Run, resume: bool) -> int:
    doc = None
    try:
        if resume:
            # 上次已删快照、取消看门狗，只补新指纹。
            ctx.snapshots_made = True
            ctx.disarmed = True
            doc = keep_fingerprint(ctx)
        else:
            capture_msgcenter(ctx, "close")
            doc = stop_and_keep(ctx)
        if markers_cleared("keep", doc is not None, ctx.disarmed, doc is not None):
            clear_both_markers(ctx)
    except KeyboardInterrupt:
        ctx.fail("收到中断")
    return close_report(ctx, {"fingerprint": doc})


def _keep_refused(local: dict | None, status: dict, kind: str) -> str:
    reason = keep_refusal(kind, marker_owner(local, status), conf().seedable)
    if reason:
        return reason
    # 与 sql/call 同一条窗口规则：两个快照都在、没有 done、看门狗在且至少还有 5 分钟。
    blocked = window_block_reason(status, datetime.now(ZoneInfo(conf().timezone)))
    return "不能 close --keep：" + blocked if blocked else ""


def _close_keep(remote: Remote, local: dict | None, status: dict, kind: str) -> int:
    ctx = bind_close_run(remote, local, status)
    if keep_resumable(local, status, kind, (ctx.report_dir / "fingerprint-keep.json").is_file()):
        return _finish_keep(ctx, True)
    reason = _keep_refused(local, status, kind)
    if reason:
        print(reason)
        return 1
    return _finish_keep(ctx, False)


def close_session(remote: Remote | None = None, keep: bool = False) -> int:
    # --keep 先看本机配置，没开 seedable 就不碰远端。
    if keep and not conf().seedable:
        print(keep_refusal("", "", False))
        return 1
    remote = remote or make_remote()
    local = read_linux_marker()
    dest = _report_dir(local, {})
    try:
        status = poll_until_idle(remote, dest)
    except (RuntimeError, RemoteError, OSError, json.JSONDecodeError) as exc:
        print(str(exc)[:300])
        return 1
    kind = close_plan(local, status, "session")
    if keep:
        return _close_keep(remote, local, status, kind)
    if kind == "foreign":
        print("这是另一个运行的快照，session close 不会动它。请等该运行结束，或按报告里的人工步骤处理。")
        return 1
    if kind == "already-closed":
        print("already-closed")
        return 0
    return _finish_close(bind_close_run(remote, local, status), kind)


def _print_status(local: dict | None, status: dict) -> None:
    snaps = text_list(status.get("snapshots"))
    report = ""
    if local:
        report = str(local.get("report_dir") or "")
    elif status.get("report_dir"):
        report = str(status.get("report_dir"))
    print("linux_marker " + ("yes" if local else "no"))
    print("server_marker " + ("yes" if status.get("marker") else "no"))
    print("snapshots " + (",".join(str(item) for item in snaps) or "none"))
    print("done " + ("yes" if status.get("done") else "no"))
    print("watchdog " + str(status.get("watchdog_state") or "none"))
    print("next_run " + str(status.get("next_run") or ""))
    print("minutes_left " + minutes_left(str(status.get("next_run") or "")))
    print("service " + str(status.get("service") or "missing"))
    print("owner " + str((local or {}).get("owner") or status.get("owner") or ""))
    if report:
        print("report_dir " + report)


def status_session(remote: Remote | None = None) -> int:
    # 先打印通道指纹：同一台服务器上的每条通道这些值都必须互不相同（docs/testing.md 4.5）。
    for line in fingerprint_lines(conf()):
        print(line)
    remote = remote or make_remote()
    try:
        status = read_server_status(remote)
    except (RuntimeError, RemoteError, OSError, json.JSONDecodeError) as exc:
        print(str(exc)[:300])
        return 1
    _print_status(read_linux_marker(), status)
    return 0


def deploy_session(remote: Remote | None = None, force: bool = False) -> int:
    local = read_linux_marker()
    if local is None:
        print("没有打开的会话，不能部署")
        return 1
    report = Path(str(local.get("report_dir") or ""))
    if not report.is_dir():
        print("会话目录不存在")
        return 1
    ctx = Run(False, report, remote or make_remote())
    ctx.force_deploy = force
    try:
        assert_window(ctx.remote, report, ensure=True)
        skipped = deploy_service(ctx)
    except (RuntimeError, RemoteError, OSError) as exc:
        start_if_idle(ctx)
        print(ctx.scrub(str(exc)[:300]))
        return 1
    print("deploy ok（桥未改动，跳过编译部署）" if skipped else "deploy ok")
    return 0


def _clear_if_idle(ctx: Run) -> None:
    try:
        status = fetch_status(ctx.remote, ctx.report_dir)
    except (RuntimeError, RemoteError, OSError, json.JSONDecodeError):
        return
    if text_list(status.get("snapshots")):
        return
    state = str(status.get("watchdog_state") or "none")
    if state not in ("", "none"):
        return
    clear_both_markers(ctx)


def finish_run_marker(ctx: Run) -> None:
    if ctx.owner != "run":
        return
    if markers_cleared("revert", ctx.reverted, ctx.disarmed, ctx.after is not None):
        clear_both_markers(ctx)
        return
    _clear_if_idle(ctx)
