"""在测试账套的 UFDATA 库上执行。先过 sqlguard；工人退出时退回 session-sql.ps1。

并行跑用例时各线程拿各自的 SQL 通道（Remote.sql_channel 按线程分）。本机和服务器上的查询文件名是
「本次运行序号-用例名-本用例第几条-随机数」（例如 0007-sale-verify-q2-1a2b3c4d）：按名字排序就是执行顺序，
多条用例同时查、几个进程共用服务器 out 目录也不会互相覆盖。串行（--serial）也用这个名字。
收到中断（Run.stop 置位）后不再发 SQL。
"""

from __future__ import annotations

import itertools
import re
import secrets

from co.e2e.casespec.env import Env
from co.e2e.casespec.gate import stopped
from co.e2e.remote import RemoteResult
from co.e2e.runfiles import drop_remote_out
from co.e2e.settings import conf
from co.e2e.sqlguard import sql_block_reason

_SLOT = re.compile(r"^[A-Za-z0-9-]{1,80}\Z")
_LABEL = re.compile(r"^[a-z0-9_]+(-[a-z0-9_]+)*\Z")
# 本进程的查询序号；itertools.count 的 next 在 CPython 里是原子的，多线程取号不会重复。
_SEQ = itertools.count(1)


def slot_name(label: str, number: int) -> str:
    # label 通常是用例名（挑单时是 pick）；不合规的名字换成 sql。
    head = label[:40] if _LABEL.fullmatch(label or "") else "sql"
    slot = f"{next(_SEQ):04d}-{head.replace('_', '-')}-q{number}-{secrets.token_hex(4)}"
    if _SLOT.fullmatch(slot) is None:
        raise RuntimeError("SQL 槽名不合法")
    return slot


def run_guarded(env: Env, query: str, label: str) -> str:
    reason = sql_block_reason(query)
    if reason:
        raise RuntimeError(reason)
    if stopped(env.ctx):
        raise RuntimeError("收到中断，不再查 SQL")
    env.sql_n += 1
    slot = slot_name(label, env.sql_n)
    local = env.ctx.report_dir / (slot + ".sql")
    local.write_text(query, encoding="utf-8")
    result = _query(env, query, slot)
    text = env.ctx.scrub(result.out)
    (env.ctx.report_dir / (slot + ".out.txt")).write_text(text, encoding="utf-8")
    if result.code != 0:
        raise RuntimeError(label + " SQL 失败")
    return result.out


def _query(env: Env, query: str, slot: str) -> RemoteResult:
    opener = getattr(env.ctx.remote, "sql_channel", None)
    if opener is None:
        return _legacy(env, slot)
    return opener().execute(conf().data_db, query, lambda: _legacy(env, slot))


def _legacy(env: Env, slot: str) -> RemoteResult:
    remote = conf().remote("out", slot + ".sql")
    local = env.ctx.report_dir / (slot + ".sql")
    env.ctx.remote.upload(local, remote)
    try:
        return env.ctx.remote.run_script(
            "session-sql.ps1",
            {"Database": conf().data_db, "File": remote},
            180,
        )
    finally:
        # 文件名每次不同，用完删掉，免得 out 下越积越多。
        drop_remote_out(env.ctx.remote, slot + ".sql")
