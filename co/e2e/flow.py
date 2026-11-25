"""预检、快照和还原。run_e2e 与 session 共用，避免两套还原。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from co.e2e.bridge_job import load_service_secret, upload_scripts
from co.e2e.context import Run
from co.e2e.fingerprint import diff_fingerprints, merge_parts, normalize
from co.e2e.fingerprint_bg import only_totals, split_background
from co.e2e.guard import (
    capture_msgcenter,
    clear_local_inrevert,
    load_remote_json,
    start_if_idle,
    wait_until_idle,
)
from co.e2e.markers import linux_marker_path, note_keep, read_linux_marker, refusal_reason
from co.e2e.plan import build_plan, clamp_minutes, preflight_problems, preflight_warnings
from co.e2e.remote import Remote, RemoteError, RemoteResult
from co.e2e.report import assemble, exit_code, render_markdown
from co.e2e.safety import revert_failure_text, unique_report_dir
from co.e2e.settings import conf
from co.e2e.taskcfg import classify, only_taskcfg
from co.e2e.timing import elapsed_ms, monotonic


def _now() -> datetime:
    return datetime.now(ZoneInfo(conf().timezone))


def local_stamp() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%S")


def local_day() -> str:
    return _now().date().isoformat()


def utc_stamp() -> str:
    return _now().astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_remote() -> Remote:
    cfg = conf()
    return Remote(cfg.ssh_key, cfg.known_hosts)


def open_run(dry_run: bool, remote: Remote | None = None) -> Run:
    report_dir = unique_report_dir(Path(conf().report_dir), _now().strftime("%Y%m%d-%H%M%S"))
    ctx = Run(dry_run, report_dir, remote or make_remote())
    plan = build_plan(dry_run)
    (report_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return ctx


def _log(ctx: Run, name: str, result) -> None:
    text = ctx.scrub(result.out)
    (ctx.report_dir / f"{len(ctx.steps):02d}-{name}.log").write_text(text, encoding="utf-8")
    ctx.add_step(name, result.code == 0)


def _must(ctx: Run, name: str, result) -> None:
    _log(ctx, name, result)
    if result.code != 0:
        raise RuntimeError(name + " 失败")


def prepare_tree(ctx: Run) -> None:
    if linux_marker_path().is_file():
        raise RuntimeError(refusal_reason(True, False))
    _must(ctx, "ensure-dirs", ctx.remote.ensure_dirs())
    _must(ctx, "assert-idle", ctx.remote.assert_idle())
    upload_scripts(ctx)


def do_preflight(ctx: Run) -> None:
    if linux_marker_path().is_file():
        raise RuntimeError(refusal_reason(True, False))
    result = ctx.remote.run_script("preflight.ps1", None, 180)
    _must(ctx, "preflight", result)
    local = ctx.report_dir / "preflight.json"
    ctx.remote.download(conf().remote("out", "preflight.json"), local)
    info = json.loads(local.read_text(encoding="utf-8"))
    problems = preflight_problems(info)
    ctx.warnings.extend(preflight_warnings(info))
    try:
        load_service_secret(ctx)
    except RuntimeError as exc:
        problems.append(str(exc))
    if problems:
        raise RuntimeError("；".join(problems))


def _fingerprint_one(ctx: Run, database: str, source: str) -> tuple[RemoteResult, int]:
    started = monotonic()
    remote = conf().remote("out", "fp-" + database + ".json")
    result = ctx.remote.run_script(
        "fingerprint.ps1",
        {"Database": database, "OutFile": remote, "Source": source},
        7200,
    )
    return result, elapsed_ms(started)


def _fingerprint_parts(ctx: Run, label: str) -> list[dict]:
    # 各库（UFDATA、UFMeta）的指纹同时跑，等全部结束后再按库的顺序记步骤、遇到第一个失败就停，与逐库串行时一致。
    # 步骤的 ms 是各库自己的用时：并行时前后两步的 at 差值不是耗时。
    source = "snapshot" if label == "snapshot" else "live"
    databases = list(conf().databases)
    with ThreadPoolExecutor(max_workers=max(1, len(databases))) as pool:
        futures = [pool.submit(_fingerprint_one, ctx, database, source) for database in databases]
        wait(futures)
    parts = []
    for database, future in zip(databases, futures):
        result, ms = future.result()
        name = "fingerprint-" + label + "-" + database
        text = ctx.scrub(result.out)
        (ctx.report_dir / f"{len(ctx.steps):02d}-{name}.log").write_text(text, encoding="utf-8")
        ctx.add_step(name, result.code == 0, ms=ms)
        if result.code != 0:
            raise RuntimeError(name + " 失败")
        local = ctx.report_dir / f"fp-{label}-{database}.json"
        ctx.remote.download(conf().remote("out", "fp-" + database + ".json"), local)
        parts.append(json.loads(local.read_text(encoding="utf-8")))
    return parts


def do_fingerprint(ctx: Run, label: str) -> dict:
    parts = _fingerprint_parts(ctx, label)
    doc = normalize(merge_parts(parts))
    text = json.dumps(doc, ensure_ascii=False, indent=2)
    (ctx.report_dir / f"fingerprint-{label}.json").write_text(text, encoding="utf-8")
    if label == "before":
        ctx.before = doc
    elif label == "snapshot":
        ctx.reference = doc
    elif label == "post":
        ctx.post = doc
    return doc


def arm_snapshot(ctx: Run) -> None:
    _must(ctx, "s4u-arm", ctx.remote.run_script("s4u-arm.ps1", None, 180))
    clear_local_inrevert(ctx.report_dir)
    _must(ctx, "clear-inrevert", ctx.remote.run_script("clear-inrevert.ps1", None, 60))
    ctx.snapshots_made = True
    _must(ctx, "snapshot-create", ctx.remote.run_script("snapshot-create.ps1", None, 600))
    ctx.watchdog = True
    minutes = str(clamp_minutes(ctx.watchdog_min))
    named = {"Minutes": minutes}
    _must(ctx, "watchdog-arm", ctx.remote.run_script("watchdog-arm.ps1", named, 60))


def _bad_revert(text: str) -> bool:
    return "已无快照" in text or "之后的写入未还原" in text


def _fail_revert(ctx: Run, text: str) -> bool:
    ctx.fail(revert_failure_text(text) or "还原失败。快照和看门狗都保留。")
    return False


def _revert(ctx: Run) -> bool:
    # 编排器不接受缺失的快照。只有看门狗脚本自己允许快照已经不在。
    try:
        result = ctx.remote.run_script("snapshot-revert.ps1", None, 1800)
    except RemoteError as exc:
        _log(ctx, "revert", RemoteResult(1, exc.output))
        return _fail_revert(ctx, exc.output)
    _log(ctx, "revert", result)
    if result.code != 0 or _bad_revert(result.out):
        return _fail_revert(ctx, result.out)
    return True


def _fetch_inrevert(ctx: Run) -> None:
    # 判定依据:RESTORE 之后立刻回到单用户，同一会话里先导出任务表再算指纹。
    parts = []
    for database in conf().databases:
        remote = conf().remote("out", "fp-inrevert-" + database + ".json")
        local = ctx.report_dir / f"fp-inrevert-{database}.json"
        try:
            ctx.remote.download(remote, local)
            parts.append(json.loads(local.read_text(encoding="utf-8")))
        except (OSError, RemoteError, json.JSONDecodeError):
            ctx.fail("取不到还原会话内的指纹")
            return
    try:
        doc = normalize(merge_parts(parts))
    except (TypeError, ValueError, KeyError):
        ctx.fail("取不到还原会话内的指纹")
        return
    (ctx.report_dir / "fingerprint-inrevert.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    ctx.after = doc


def _disarm(ctx: Run) -> None:
    result = ctx.remote.run_script("watchdog-disarm.ps1", None, 60)
    _log(ctx, "watchdog-disarm", result)
    if result.code != 0:
        raise RuntimeError("取消看门狗失败")
    ctx.disarmed = True


def _load_revert_side(ctx: Run) -> None:
    # snapshot-revert 自己写这两份，看门狗路径也能分类。不再在 MULTI_USER 之后另查。
    ctx.taskcfg_snap = load_remote_json(ctx, "taskcfg-snapshot.json", "taskcfg-snapshot.json")
    ctx.taskcfg_live = load_remote_json(ctx, "taskcfg-live.json", "taskcfg-live.json")


def stop_service(ctx: Run) -> None:
    try:
        result = ctx.remote.run_script("service-stop.ps1", None, 240)
    except RemoteError as exc:
        _log(ctx, "service-stop", RemoteResult(1, exc.output))
        ctx.fail("停止服务失败，继续尝试还原")
        return
    _log(ctx, "service-stop", result)
    if result.code != 0:
        ctx.fail("停止服务失败，继续尝试还原")


def start_service(ctx: Run) -> None:
    try:
        result = ctx.remote.run_script("service-start.ps1", None, 60)
    except RemoteError as exc:
        _log(ctx, "service-start", RemoteResult(1, exc.output))
        ctx.fail("未能重新启动服务 " + conf().service_name)
        return
    _log(ctx, "service-start", result)
    if result.code != 0:
        ctx.fail("未能重新启动服务 " + conf().service_name)


def _owner_blocks(ctx: Run) -> bool:
    if not ctx.owner:
        return False
    owner = str((read_linux_marker() or {}).get("owner") or "")
    if not owner:
        return False
    return owner != ctx.owner


def _revert_body(ctx: Run) -> None:
    stop_service(ctx)
    clear_local_inrevert(ctx.report_dir)
    ctx.reverted = _revert(ctx)
    if not ctx.reverted:
        return
    _load_revert_side(ctx)
    _fetch_inrevert(ctx)
    if ctx.watchdog:
        _disarm(ctx)


def stop_and_revert(ctx: Run) -> None:
    if not ctx.snapshots_made:
        return
    if _owner_blocks(ctx):
        ctx.fail("快照属于另一个运行，本进程不还原")
        return
    # 停服务 / 还原开始：卡住的用例线程不得再发收尾 DELETE（engine._wind_down 在有快照时也会置位）。
    ctx.cleanup_closed.set()
    try:
        wait_until_idle(ctx)
        _revert_body(ctx)
    except (RuntimeError, RemoteError) as exc:
        ctx.fail(str(exc)[:300])
    finally:
        start_if_idle(ctx)


def _drop_snapshots(ctx: Run) -> bool:
    try:
        result = ctx.remote.run_script("snapshot-drop.ps1", {"Mode": "drop"}, 600)
    except RemoteError as exc:
        result = RemoteResult(1, exc.output)
    _log(ctx, "snapshot-drop", result)
    if result.code != 0:
        ctx.fail("删除快照失败。看门狗已取消，账套停在造数后的状态，见报告里的人工步骤")
        return False
    return True


_KEEP_CHECKS = (("keep-gate", "session-gate.ps1", None), ("keep-check", "snapshot-drop.ps1", {"Mode": "check"}))


def _keep_checks(ctx: Run) -> None:
    # 取消看门狗之前在服务器上核对窗口、快照、多用户和 Seedable。旧脚本打开的会话没有 snapshot-drop.ps1，在这里就失败。
    for label, name, named in _KEEP_CHECKS:
        try:
            result = ctx.remote.run_script(name, named, 180)
        except RemoteError as exc:
            result = RemoteResult(1, exc.output)
        _log(ctx, label, result)
        if result.code != 0:
            raise RuntimeError(name + " 核对没通过，不保留，快照和看门狗都保留。旧脚本打开的会话请先不带 --keep close，再重新 open")


def _keep_body(ctx: Run) -> bool:
    count = len(ctx.reasons)
    stop_service(ctx)
    if len(ctx.reasons) > count:
        raise RuntimeError("服务没停下，不保留。快照和看门狗都保留")
    _keep_checks(ctx)
    try:
        note_keep(ctx.report_dir)
    except OSError:
        raise RuntimeError("写不了本机会话标记，不保留。快照和看门狗都保留") from None
    # 先取消看门狗：决定保留之后它不能再还原。
    _disarm(ctx)
    return _drop_snapshots(ctx)


def keep_fingerprint(ctx: Run) -> dict | None:
    try:
        return do_fingerprint(ctx, "keep")
    except (RuntimeError, RemoteError, OSError, ValueError, KeyError, TypeError):
        ctx.fail("保留后的指纹失败。快照已删、看门狗已取消，会话标记保留；再执行一次 close --keep 只补指纹")
        return None


def stop_and_keep(ctx: Run) -> dict | None:
    # close --keep：不还原、不和快照指纹比对。删掉快照后取一份新指纹作为本通道的新基线。
    ctx.snapshots_made = True
    ctx.watchdog = True
    dropped = False
    try:
        wait_until_idle(ctx)
        dropped = _keep_body(ctx)
    except (RuntimeError, RemoteError) as exc:
        ctx.fail(str(exc)[:300])
    finally:
        start_if_idle(ctx)
    if not dropped:
        return None
    return keep_fingerprint(ctx)


def _finish_watched(ctx: Run) -> None:
    ctx.snapshots_made = True
    _load_revert_side(ctx)
    _fetch_inrevert(ctx)
    ctx.reverted = ctx.after is not None
    if not start_if_idle(ctx):
        return
    try:
        _disarm(ctx)
    except RuntimeError:
        ctx.fail("取消看门狗失败")


def run_close_remote(ctx: Run, kind: str) -> None:
    if kind == "already-closed":
        return
    if kind == "verify-only":
        _finish_watched(ctx)
        return
    ctx.snapshots_made = True
    ctx.watchdog = True
    stop_and_revert(ctx)


def _diff_of(ctx: Run) -> list[str]:
    reference = ctx.reference if ctx.reference is not None else ctx.before
    if reference is not None and ctx.after is not None:
        diff = diff_fingerprints(reference, ctx.after)
        return _explain_taskcfg(ctx, diff)
    if ctx.before is not None and not ctx.dry_run and ctx.snapshots_made:
        return ["没有测试后指纹，不能证明已经还原"]
    return []


def _post_lines(ctx: Run) -> list[str]:
    if ctx.after is None or ctx.post is None:
        return []
    return diff_fingerprints(ctx.after, ctx.post)


def _explain_taskcfg(ctx: Run, diff: list[str]) -> list[str]:
    # 放行两类：U8 后台表能归到还原之后的写入（fingerprint_bg），U8TaskConfig 调度列落在还原开始之后的分钟窗口里。
    # 其余差异照报；只要有一行不能放行，整份差异原样报出，什么都不隐藏。
    tolerated, rest = split_background(diff, _post_lines(ctx))
    if tolerated and only_totals(rest):
        ctx.background_notes = tolerated
        return []
    if not only_taskcfg(rest) or ctx.taskcfg_snap is None or ctx.taskcfg_live is None:
        return diff
    started = str(ctx.taskcfg_live.get("restore_started") or "")
    ok, notes = classify(ctx.taskcfg_snap, ctx.taskcfg_live, started)
    if not ok:
        return diff + notes
    ctx.scheduler_notes = notes
    ctx.background_notes = tolerated
    return []


def close_report(ctx: Run, keep: dict | None = None) -> int:
    state = _report_state(ctx)
    if keep is not None:
        # 保留造数结果：不比对快照，差异清空，由报告写明未还原。
        state.update(keep=True, keep_fingerprint=keep.get("fingerprint"), fingerprint_diff=[])
    report = assemble(state)
    (ctx.report_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (ctx.report_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    print(ctx.report_dir)
    print(report["overall"])
    return exit_code(report["overall"])


def _report_state(ctx: Run) -> dict:
    background = []
    if ctx.before is not None and ctx.reference is not None:
        background = diff_fingerprints(ctx.before, ctx.reference)
    post = _post_lines(ctx)
    return {
        "dry_run": ctx.dry_run,
        "failed": ctx.failed,
        "snapshots_made": ctx.snapshots_made,
        "reverted": ctx.reverted,
        "watchdog_min": ctx.watchdog_min,
        "fingerprint_diff": _diff_of(ctx),
        "background_diff": background,
        "scheduler_notes": ctx.scheduler_notes,
        "background_tolerated": ctx.background_notes,
        "post_reopen_diff": post,
        "tests": ctx.tests,
        "u8_logins": ctx.u8_logins,
        "login_note": ctx.login_note,
        "bridge_logins": ctx.bridge_logins,
        "steps": ctx.steps,
        "phases": ctx.phases,
        "reasons": ctx.reasons,
        "sale_id": ctx.sale_id,
        "dispatch_id": ctx.dispatch_id,
        "dispatch_mode": ctx.dispatch_mode,
        "year_used": ctx.year_used,
        "operator_name": ctx.operator_name,
        "cases": ctx.case_names or "全部",
        "jobs": ctx.jobs,
        "needles": ctx.needles,
        "before": ctx.before,
        "after": ctx.after,
        "warnings": ctx.warnings,
    }


def cleanup(ctx: Run) -> None:
    if ctx.cleaned or ctx.dry_run:
        ctx.cleaned = True
        return
    ctx.cleaned = True
    capture_msgcenter(ctx, "close")
    stop_and_revert(ctx)
