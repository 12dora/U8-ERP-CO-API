"""同一条通道里 session 命令之间的本机锁（<report.dir>/session.lock，flock）。

open、deploy、close、reset、extend 和 comprobe build 要独占：close、reset 还原快照时，别的命令还在写，写入就留在测试账套里；
open、deploy、build 会重锁远程工作目录，和并行命令新建、删除临时文件相互打架；extend 改看门狗到点时刻和会话标记，一次只许一条。
call、sql、audit 和 comprobe run 共享：可以彼此并行，但不和上面几条同时跑。status 只读，不加锁。
只管同一台机器上的命令；别的机器上用同一份配置跑的命令拦不住（docs/testing.md 4.5）。
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from co.e2e.settings import conf

EXCLUSIVE = frozenset(("open", "deploy", "close", "reset", "extend"))
SHARED = frozenset(("call", "sql", "audit"))

_BUSY_EXCLUSIVE = "这条通道还有别的 session 命令或探针在跑（call、sql、audit、comprobe），等它们结束再执行"
_BUSY_SHARED = "这条通道正在 open、deploy、close、reset、extend 或编译探针，等它结束再执行"


def lock_path() -> Path:
    return Path(conf().report_dir) / "session.lock"


@contextmanager
def session_lock(exclusive: bool) -> Iterator[None]:
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(_BUSY_EXCLUSIVE if exclusive else _BUSY_SHARED) from None
        yield
    finally:
        os.close(fd)


@contextmanager
def command_lock(cmd: str) -> Iterator[None]:
    if cmd in EXCLUSIVE:
        with session_lock(True):
            yield
        return
    if cmd in SHARED:
        with session_lock(False):
            yield
        return
    yield
