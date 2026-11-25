"""调用已安装的 u8co 服务。用例在 casespec，口令只放进 U8CoClient。"""

from __future__ import annotations

import json
from pathlib import Path

from co.client.u8co_client import U8CoClient, U8CoError
from co.e2e.calllog_io import pull_audit
from co.e2e.cases import Operator, count_logins, load_operator, rows_since
from co.e2e.casespec.env import Env
from co.e2e.casespec.runner import run_selected
from co.e2e.context import Run
from co.e2e.settings import conf


def open_env(ctx: Run, client: U8CoClient, operator: Operator, today: str, audit_since: str) -> Env:
    return Env(ctx, client, operator, today, operator.year, audit_since)


def run_suite(ctx: Run, today: str, started: str, audit_since: str) -> None:
    operator = load_operator(Path(conf().operator_file))
    ctx.needles.append(operator.password)
    client = U8CoClient(conf().bridge_url, ctx.secret, timeout=90)
    before = read_login_counters(client)
    try:
        # ctx.jobs 为 1 时串行；大于 1 时资源不冲突的用例同时跑（casespec/sched.py）。
        run_selected(open_env(ctx, client, operator, today, audit_since), ctx.case_names, ctx.jobs)
    finally:
        # 用例阶段异常也要记下登录计数，报告照样写出。
        ctx.bridge_logins = login_delta(before, read_login_counters(client))
    rows = rows_since(pull_audit(ctx, today.replace("-", "")), audit_since)
    note_logins(ctx, operator, started, rows)


def _counter(value: object) -> int | None:
    # bool 是 int 的子类，要排除；负数视为无效。
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def read_login_counters(client: U8CoClient) -> dict | None:
    """读 /health 里的登录计数（服务启动以来的累计值）。旧版桥没有这些字段；健康检查失败时返回 None。"""
    try:
        payload = client.health()
    except (U8CoError, OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    reuse = payload.get("login_reuse")
    return {
        "login_reuse": reuse if isinstance(reuse, bool) else None,
        "opened": _counter(payload.get("logins_opened")),
        "reused": _counter(payload.get("logins_reused")),
        "closed": _counter(payload.get("logins_closed")),
    }


def _restarted(first: dict, last: dict) -> bool:
    # 计数变小，或前后 login_reuse 不一致（改配置必须重启桥），都说明服务中途重启过。
    if first.get("login_reuse") is not None and last.get("login_reuse") is not None:
        if first["login_reuse"] != last["login_reuse"]:
            return True
    for key in ("opened", "reused", "closed"):
        if first.get(key) is not None and last.get(key) is not None and last[key] < first[key]:
            return True
    return False


def login_delta(before: dict | None, after: dict | None) -> dict:
    """用例阶段前后两次计数之差。字段缺失或服务中途重启时 opened、reused、closed 为 None，重启时另加 restarted: True。
    closed 只在旧桥上缺失时单独为 None，不影响 opened、reused。"""
    first = before or {}
    last = after or {}
    reuse = last.get("login_reuse")
    if reuse is None:
        reuse = first.get("login_reuse")
    result: dict = {"login_reuse": reuse, "opened": None, "reused": None, "closed": None}
    if _restarted(first, last):
        result["restarted"] = True
        return result
    if any(first.get(key) is None or last.get(key) is None for key in ("opened", "reused")):
        return result
    for key in ("opened", "reused", "closed"):
        if first.get(key) is not None and last.get(key) is not None:
            result[key] = last[key] - first[key]
    return result


def note_logins(ctx: Run, operator: Operator, started: str, rows: list[dict]) -> None:
    ctx.u8_logins = count_logins(rows)
    note = f"桥审计计入的 U8 登录次数：{ctx.u8_logins}。"
    try:
        result = ctx.remote.run_script(
            "login-count.ps1",
            {"Since": started, "Operator": operator.code},
            120,
        )
        if result.code == 0:
            local = ctx.report_dir / "login-count.json"
            ctx.remote.download(conf().remote("out", "login-count.json"), local)
            info = json.loads(local.read_text(encoding="utf-8"))
            note += " " + conf().data_db + " 的 UA_Log_Ex 新增行（还原前）：" + str(info.get("ufdata_log_rows")) + "。"
            if info.get("ufsystem_access"):
                note += " 运行 e2e 的 Windows 账户能读 UFSystem。"
            else:
                note += " 运行 e2e 的 Windows 账户不能读 UFSystem，登录锁行无法在这里计数；这些行不随测试账套快照还原。"
    except (OSError, RuntimeError, json.JSONDecodeError):
        note += " 账套内登录行计数失败。"
    ctx.login_note = note
