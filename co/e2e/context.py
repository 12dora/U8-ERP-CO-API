"""一次运行的内存状态。repr 不包含口令和密钥。并行跑用例时 add_test、fail 可能来自多个线程，加锁。"""

from __future__ import annotations

import threading
from pathlib import Path

from co.e2e.cases import scrub_text
from co.e2e.remote import Remote
from co.e2e.timing import wall


class Run:
    def __init__(self, dry_run: bool, report_dir: Path, remote: Remote) -> None:
        self.dry_run = dry_run
        self.report_dir = report_dir
        self.remote = remote
        self.failed = False
        self.reasons: list[str] = []
        self.tests: list[dict] = []
        self.steps: list[dict] = []
        # 编排阶段耗时：{阶段: {start, end, ms}}（timing.phase）。
        self.phases: dict[str, dict] = {}
        # 桥源码哈希与服务器上一致时也重新编译部署（--force-deploy）。
        self.force_deploy = False
        self.before = None
        self.reference = None
        self.after = None
        self.post = None
        self.taskcfg_snap = None
        self.taskcfg_live = None
        self.scheduler_notes: list[str] = []
        # 还原比对里已容忍的 U8 后台表差异（fingerprint_bg），报告单列。
        self.background_notes: list[str] = []
        self.snapshots_made = False
        self.reverted = False
        self.disarmed = False
        self.watchdog = False
        self.watchdog_min = 25
        self.owner = ""
        self.warnings: list[str] = []
        self.msg_open = None
        self.msg_close = None
        self.secret = ""
        self.needles: list[str] = []
        self.sale_id: int | None = None
        self.dispatch_id: int | None = None
        self.sale_mode = "verify-first"
        self.dispatch_mode = "verify-first"
        self.dispatch_downstream_id: int | None = None
        self.operator_name = ""
        self.year_used = ""
        self.u8_logins: int | None = None
        self.login_note = ""
        # 桥 /health 登录计数在用例阶段的差值（suite.login_delta）；没跑到用例阶段时为 None。
        self.bridge_logins: dict | None = None
        self.case_names = ""
        # 同时跑几条用例；1 是串行（--serial）。
        self.jobs = 1
        self.cleaned = False
        self._lock = threading.Lock()
        # 中断标志：并行跑用例时置位后，用例在下一步之前停，桥调用和 SQL 不再发（casespec/gate.py）。
        self.stop = threading.Event()
        # 还原已开始：停服务 / snapshot-revert 之前置位。收尾 DELETE 会绕过 stop，看到这个才停
        # （col_auth.restore_row）。会话内运行不还原，不置位。
        self.cleanup_closed = threading.Event()

    def __repr__(self) -> str:
        return f"Run(dry_run={self.dry_run}, failed={self.failed})"

    def fail(self, reason: str) -> None:
        text = scrub_text(reason, self.needles)[:500]
        with self._lock:
            self.failed = True
            self.reasons.append(text)

    def scrub(self, text: str) -> str:
        return scrub_text(text, self.needles)

    def add_test(self, name: str, status: str, detail: str, extra: dict | None = None) -> None:
        # extra：case（用例名，按族汇总耗时用）和 duration_ms（用例耗时，毫秒；没计到为 None）。
        info = extra or {}
        row = {
            "name": name,
            "status": status,
            "detail": self.scrub(detail)[:500],
            "case": str(info.get("case") or ""),
            "duration_ms": info.get("duration_ms"),
        }
        with self._lock:
            self.tests.append(row)

    def add_step(self, name: str, ok: bool, note: str = "", ms: int | None = None) -> None:
        # at：步骤结束时刻（UTC）。上一步的 at 就是这一步的开始；并行跑的步骤另给 ms（自身用时，毫秒）。
        row: dict = {"name": name, "ok": ok, "at": wall()}
        if note:
            row["note"] = note
        if ms is not None:
            row["ms"] = ms
        with self._lock:
            self.steps.append(row)
