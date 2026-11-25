"""报告组装。口令、密钥和 password_enc 不会写进 JSON 或 Markdown。"""

from __future__ import annotations

from co.e2e.cases import public_copy
from co.e2e.settings import conf
from co.e2e.timing import fmt_ms, timing_section

_MANUAL = """还原没有完成。快照和计划任务 {task} 都还留着。
不要手工删除快照，除非已经确认还原成功。
若报告写着看门狗已经还原，那之后的写入还在账套 {acc} 里，不能当成已经回到快照。
先确认计划任务不在 Running，再停服务 {service}，然后用运行 e2e 的 Windows 账户执行：
{pwsh} -NoProfile -File {root}\\snapshot-revert.ps1
不要加 -AllowMissing。那只给看门狗自己用。快照缺了就必须失败。
成功后再执行同目录的 watchdog-disarm.ps1，然后 Start-Service {service}。
标记属于回归（owner 为 run）且快照还在时，不要用 session close 去还原。
不要改 {bridge}\\config.json 和 secret.hex。不要对测试账套以外的账套做任何操作。UFSystem 不要还原。
deploy 换坏程序时，上一份在 {bridge}\\bin\\u8co-bridge.exe.prev，配置在同目录的 u8co-bridge.exe.config.prev。"""

_KEEP_MANUAL = """保留没有完成。按 steps 判断停在哪一步：
keep-gate、keep-check 没通过或 watchdog-disarm 之前就失败：快照和看门狗都还在，可以不带 --keep 执行 session close 还原，或排除原因后再 close --keep。
watchdog-disarm 一旦执行过，普通的 session close 就不能再还原（done 已写，snapshot-revert 会拒绝），看门狗也不会再还原账套 {acc}。
close --keep 也不能重试（它要求看门狗还在）。两个快照都还在时，停服务 {service} 后用运行 e2e 的 Windows 账户手工还原：
{pwsh} -NoProfile -File {root}\\snapshot-revert.ps1 -AllowMissing
-AllowMissing 只在两个快照都还在时用，否则只会还原一半。成功后删除本机 <report.dir>/session.json 和服务器 {root}\\session.json，再启动服务 {service}。
只剩一个快照：不要还原，也不要重试，找 DBA 核对后处理。
快照已删、只差指纹：再执行一次 session close --keep，它只补指纹并清标记。"""


def keep_manual_text() -> str:
    cfg = conf()
    return _KEEP_MANUAL.format(acc=cfg.account, service=cfg.service_name, root=cfg.remote_root, pwsh=cfg.pwsh)


def manual_text() -> str:
    cfg = conf()
    return _MANUAL.format(
        task=cfg.revert_task,
        acc=cfg.account,
        service=cfg.service_name,
        pwsh=cfg.pwsh,
        root=cfg.remote_root,
        bridge=cfg.bridge_root,
    )


def judge(dry_run: bool, failed: bool, rollback: bool, tests: list[dict]) -> str:
    if dry_run:
        return "FAIL" if failed else "DRY-RUN"
    if failed or not rollback:
        return "FAIL"
    for item in tests:
        if item.get("status") not in ("PASS", "N/A"):
            return "FAIL"
    return "PASS"


def exit_code(verdict: str) -> int:
    if verdict in ("PASS", "DRY-RUN", "KEPT"):
        return 0
    return 1


def _rollback(dry_run: bool, diff: list[str], reverted: bool, wrote: bool) -> bool:
    if dry_run or diff:
        return False
    if wrote and not reverted:
        return False
    return True


def _keep_totals(doc: object) -> dict:
    if not isinstance(doc, dict) or not isinstance(doc.get("databases"), dict):
        return {}
    return {name: dict(item.get("totals") or {}) for name, item in doc["databases"].items()}


def _keep_fields(state: dict, failed: bool) -> dict:
    # close --keep 从不声称已还原。成功时 overall 为 KEPT，退出码 0。
    totals = _keep_totals(state.get("keep_fingerprint"))
    kept = bool(totals)
    return {
        "overall": "KEPT" if kept and not failed else "FAIL",
        "rollback_verified": False,
        "rollback_note": "保留造数结果，未还原。新基线指纹见 fingerprint-keep.json" if kept else "保留未完成，未还原",
        "keep": True,
        "kept": kept,
        "keep_fingerprint": totals,
        "fingerprint_diff": [],
        "manual": "" if kept else keep_manual_text(),
    }


def assemble(state: dict) -> dict:
    needles = list(state.get("needles") or [])
    diff = list(state.get("fingerprint_diff") or [])
    dry_run = bool(state.get("dry_run"))
    wrote = bool(state.get("snapshots_made"))
    reverted = bool(state.get("reverted"))
    failed = bool(state.get("failed"))
    tests = list(state.get("tests") or [])
    rollback = _rollback(dry_run, diff, reverted, wrote)
    if state.get("before") is None or (not dry_run and state.get("after") is None):
        rollback = False
    verdict = judge(dry_run, failed, rollback, tests)
    body = {
        "dry_run": dry_run,
        "account": conf().account,
        "mode": "service",
        "watchdog_min": state.get("watchdog_min") or "",
        "databases": list(conf().databases),
        "overall": verdict,
        "rollback_verified": rollback,
        "rollback_note": "试运行未写库" if dry_run else "",
        "fingerprint_diff": diff,
        "background_diff": list(state.get("background_diff") or []),
        "post_reopen_diff": list(state.get("post_reopen_diff") or []),
        "scheduler_notes": list(state.get("scheduler_notes") or []),
        "background_tolerated": list(state.get("background_tolerated") or []),
        "tests": tests,
        "u8_logins": state.get("u8_logins"),
        # 桥 /health 登录计数在用例阶段前后的差值；没跑到用例阶段时为 None。
        "bridge_logins": state.get("bridge_logins"),
        "login_note": state.get("login_note") or "",
        "steps": state.get("steps") or [],
        "phases": dict(state.get("phases") or {}),
        "reasons": state.get("reasons") or [],
        "sale_id": state.get("sale_id"),
        "dispatch_id": state.get("dispatch_id"),
        "year_used": state.get("year_used") or "",
        "operator_name": state.get("operator_name") or "",
        "cases": state.get("cases") or "全部",
        "jobs": state.get("jobs") or 1,
        "manual": manual_text() if wrote and not reverted else "",
        "warnings": list(state.get("warnings") or []),
    }
    if state.get("keep"):
        body.update(_keep_fields(state, failed))
    return public_copy(body, needles)  # type: ignore[return-value]


def _cell(value: object) -> str:
    return str(value or "").replace("|", "/").replace("\n", " ")


def _test_table(tests: list[dict]) -> list[str]:
    if not tests:
        return ["- （没有执行 HTTP 测试）"]
    lines = ["| 用例 | 结果 | 说明 | 耗时 |", "| --- | --- | --- | --- |"]
    for item in tests:
        name = _cell(item.get("name"))
        status = _cell(item.get("status"))
        detail = _cell(item.get("detail"))
        lines.append(f"| {name} | {status} | {detail} | {fmt_ms(item.get('duration_ms'))} |")
    return lines


def _jobs_line(jobs: object) -> str:
    # 1 或缺省（旧报告、调试会话）是串行。
    count = jobs if isinstance(jobs, int) and not isinstance(jobs, bool) else 1
    return f"跑法：并行度 {count}" if count > 1 else "跑法：串行"


def bridge_login_line(info: object) -> str:
    """报告里的 U8 登录计数行。info 为 report.json 的 bridge_logins。"""
    data = info if isinstance(info, dict) else {}
    opened = data.get("opened")
    reused = data.get("reused")
    if data.get("restarted"):
        return "U8 登录：运行期间桥重启过，计数作废"
    if opened is None or reused is None:
        return "U8 登录：桥未提供计数"
    mode = {True: "登录复用开", False: "登录复用关"}.get(data.get("login_reuse"), "登录复用状态未知")
    closed = data.get("closed")
    tail = "" if closed is None else f"，关闭 {closed} 次"
    return f"U8 登录：新开 {opened} 次，复用 {reused} 次{tail}（{mode}）"


def _verdict_line(report: dict) -> str:
    if report.get("kept"):
        suffix = "" if report.get("overall") == "KEPT" else "，但有失败项，见失败原因"
        return "结论：已保留（未还原）" + suffix
    return f"结论：{report.get('overall')}"


def _diff_section(report: dict) -> list[str]:
    fingerprint = report.get("keep_fingerprint") or {}
    if report.get("keep"):
        lines = ["", "## 保留后的指纹（本通道的新基线，不与快照比对）"]
        for name, totals in fingerprint.items():
            lines.append(f"- {name}：{totals.get('tables')} 张表，{totals.get('rows')} 行，校验 {totals.get('checksum_xor')}，出错 {totals.get('errors')}")
        return lines if fingerprint else lines + ["- 没有取到"]
    lines = ["", "## 指纹差异(快照 vs 还原会话内,判定依据)"]
    diff = report.get("fingerprint_diff") or []
    if diff:
        lines.extend(f"- {item}" for item in diff)
    else:
        lines.append("- 无")
    return lines


_RESTORE_NOTES = (
    ("scheduler_notes", ["## 已识别的 U8 调度记账(还原开始之后 U8 任务服务改写 U8TaskConfig,不做测试也会发生)"]),
    (
        "background_tolerated",
        ["## U8 后台表（已容忍）", "还原之后、重新占回单用户之前 U8 后台写入的表，不是被测业务的改动（co/e2e/README.md「已知的还原差异」）。"],
    ),
    ("post_reopen_diff", ["## 放开多用户之后 U8 后台写入(还原已完成,仅供参考)"]),
    ("background_diff", ["## 快照前 U8 后台写入(测试开始前,仅供参考)"]),
)


def _restore_notes(report: dict) -> list[str]:
    lines: list[str] = []
    for key, head in _RESTORE_NOTES:
        items = report.get(key) or []
        if items:
            lines.extend(["", *head])
            lines.extend(f"- {item}" for item in items)
    return lines


def render_markdown(report: dict) -> str:
    lines = [
        "# u8co 端到端（测试账套 " + str(report.get("account")) + "）",
        "",
        _verdict_line(report),
        f"rollback_verified：{str(report.get('rollback_verified')).lower()}",
        f"账套：{report.get('account')}",
        f"模式：{report.get('mode')}",
        f"看门狗（分钟）：{report.get('watchdog_min')}",
        f"本次登录次数（桥审计，不含 401/403/400）：{report.get('u8_logins')}",
    ]
    if report.get("bridge_logins") is not None:
        lines.append(bridge_login_line(report.get("bridge_logins")))
    lines += [
        f"用例：{report.get('cases') or '全部'}",
        _jobs_line(report.get("jobs")),
        "",
        "## 测试",
    ]
    lines.extend(_test_table(list(report.get("tests") or [])))
    lines.extend(timing_section(report))
    lines.extend(_diff_section(report))
    lines.extend(_restore_notes(report))
    note = report.get("login_note") or ""
    if note:
        lines.extend(["", "## UFSystem / 登录痕迹", note])
    warnings = report.get("warnings") or []
    if warnings:
        lines.extend(["", "## 警告"])
        lines.extend(f"- {item}" for item in warnings)
    reasons = report.get("reasons") or []
    if reasons:
        lines.extend(["", "## 失败原因"])
        lines.extend(f"- {item}" for item in reasons)
    manual = report.get("manual") or ""
    if manual:
        lines.extend(["", "## 需要人工处理", manual])
    if report.get("rollback_note"):
        lines.extend(["", report["rollback_note"]])
    lines.append("")
    return "\n".join(lines)
