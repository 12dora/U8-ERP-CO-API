"""会话里的 SQL 和审计。SQL 只打到测试账套的两个库；守卫是文本检查，不是沙箱。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from co.e2e.bridge_job import read_service_secret
from co.e2e.cases import load_operator, parse_audit, public_copy, rows_since, scrub_text
from co.e2e.flow import local_stamp, make_remote
from co.e2e.markers import read_linux_marker
from co.e2e.remote import Remote, RemoteError, RemoteResult
from co.e2e.runfiles import drop_remote_out, run_tag, tag_rejected, tagged, warn_old_scripts
from co.e2e.settings import conf
from co.e2e.sqlguard import sql_block_reason

_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")


def _session_dir() -> Path:
    local = read_linux_marker()
    if local is None:
        raise RuntimeError("没有打开的会话")
    report = Path(str(local.get("report_dir") or ""))
    if not report.is_dir():
        raise RuntimeError("会话目录不存在")
    return report


def _needles() -> list[str]:
    needles = []
    try:
        needles.append(load_operator(Path(conf().operator_file)).password)
    except (OSError, RuntimeError):
        pass
    try:
        needles.append(read_service_secret(Path(conf().secret_file)))
    except (OSError, RuntimeError):
        pass
    return [item for item in needles if item]


def sql_session(database: str, query: str, remote: Remote | None = None) -> int:
    if database not in conf().databases:
        print("只允许 " + " 或 ".join(conf().databases))
        return 1
    reason = sql_block_reason(query)
    if reason:
        print(reason)
        return 1
    report = _session_dir()
    remote = remote or make_remote()
    # 同一会话里可以并行跑多条 sql：本机和服务器上的文件名都带本次调用的标记，互不覆盖。
    tag = run_tag()
    local = report / tagged(tag, "session-query.sql")
    local.write_text(query, encoding="utf-8")
    try:
        result = _run_sql(remote, local, database, query)
    except RemoteError as exc:
        print(scrub_text(exc.output, _needles()))
        return 1
    text = scrub_text(result.out, _needles())
    (report / tagged(tag, "session-query.out.txt")).write_text(text, encoding="utf-8")
    print(text)
    if result.code != 0:
        return 1
    return 0


def _run_sql(remote: Remote, local: Path, database: str, query: str) -> RemoteResult:
    opener = getattr(remote, "sql_channel", None)
    if opener is None:
        return _legacy_sql(remote, local, database)
    return opener().execute(database, query, lambda: _legacy_sql(remote, local, database))


def legacy_sql_target(local: Path) -> str:
    # 服务器 out 下的文件名与本机这一份相同（已带调用标记）。
    return conf().remote("out", local.name)


def _legacy_sql(remote: Remote, local: Path, database: str) -> RemoteResult:
    target = legacy_sql_target(local)
    remote.upload(local, target)
    try:
        return remote.run_script("session-sql.ps1", {"Database": database, "File": target}, 180)
    finally:
        drop_remote_out(remote, local.name)


def _audit_since(since: str) -> str:
    if since:
        return since
    marker = read_linux_marker() or {}
    return str(marker.get("opened_at") or "")


def _audit_days() -> list[str]:
    # 今天和昨天都读，避免服务器时钟或跨夜把行写进另一天的文件。
    today = datetime.strptime(local_stamp()[:10], "%Y-%m-%d").date()
    yesterday = today - timedelta(days=1)
    return [yesterday.strftime("%Y%m%d"), today.strftime("%Y%m%d")]


def _read_audit_day(remote: Remote, report: Path, day: str, needles: list[str]) -> str:
    tag = run_tag()
    name = tagged(tag, "audit-" + day + ".log")
    remote_name = name
    result = remote.run_script("read-audit.ps1", {"Day": day, "Tag": tag}, 60)
    if result.code != 0 and tag_rejected(result.out):
        # 服务器上是旧版脚本（不认 -Tag）：退回不带标记的旧文件名。
        warn_old_scripts()
        remote_name = "audit-" + day + ".log"
        result = remote.run_script("read-audit.ps1", {"Day": day}, 60)
    if result.code != 0:
        raise RuntimeError(scrub_text(result.out, needles)[:300])
    fetched = report / name
    try:
        remote.download(conf().remote("out", remote_name), fetched)
    finally:
        if remote_name == name:
            drop_remote_out(remote, name)
    text = scrub_text(fetched.read_text(encoding="utf-8-sig"), needles)
    fetched.write_text(text, encoding="utf-8")
    return text


def audit_session(since: str, remote: Remote | None = None) -> int:
    if since and _TS.fullmatch(since) is None:
        print("时间格式应为 yyyy-MM-ddTHH:mm:ssZ")
        return 1
    report = _session_dir()
    stamp = _audit_since(since)
    if stamp and _TS.fullmatch(stamp) is None:
        print("会话打开时间格式不对")
        return 1
    remote = remote or make_remote()
    needles = _needles()
    parts: list[str] = []
    try:
        for day in _audit_days():
            parts.append(_read_audit_day(remote, report, day, needles))
    except (RuntimeError, RemoteError, OSError) as exc:
        print(scrub_text(str(exc), needles)[:300])
        return 1
    for row in rows_since(parse_audit("\n".join(parts)), stamp):
        print(json.dumps(public_copy(row, needles), ensure_ascii=False))
    return 0
