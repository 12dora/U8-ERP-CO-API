"""把 SQL 工人接到一条长驻 SSH。stdin 关闭后远端进程退出。

并行跑用例时每条通道各开一个 sql-worker.ps1（服务器上的查询文件名带进程号，互不覆盖），
都复用同一条 ControlMaster，不多建 TCP 连接。一条通道同一时刻只给一个线程用。
"""

from __future__ import annotations

import select
import subprocess
import threading
import time

from co.e2e.settings import conf
from co.e2e.remote import Remote, remote_launch, ssh_argv
from co.e2e.sqlchan import SqlChannel, WorkerDown

_QUERY_TIMEOUT = 180


class _Lines:
    def __init__(self, stream: object) -> None:
        self.stream = stream
        self.buf = b""

    def read(self, timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buf:
            left = deadline - time.monotonic()
            if left <= 0 or not _wait(self.stream, left):
                return None
            chunk = _read_chunk(self.stream)
            if chunk is None:
                return _tail(self)
            self.buf += chunk
        raw, self.buf = self.buf.split(b"\n", 1)
        return raw.decode("utf-8", errors="replace")


def _wait(stream: object, timeout: float) -> bool:
    ready, _, _ = select.select([stream], [], [], timeout)
    return bool(ready)


def _read_chunk(stream: object) -> bytes | None:
    read = getattr(stream, "read", None)
    if read is None:
        return None
    chunk = read(65536)
    if not chunk:
        return None
    if not isinstance(chunk, bytes):
        raise WorkerDown("worker 输出不是字节")
    return chunk


def _tail(lines: _Lines) -> str | None:
    if lines.buf == b"":
        return None
    raw = lines.buf
    lines.buf = b""
    return raw.decode("utf-8", errors="replace")


class SshSqlTransport:
    def __init__(self, remote: Remote) -> None:
        self.remote = remote
        self.proc: subprocess.Popen[bytes] | None = None
        self.lines: _Lines | None = None
        self._up = True

    def alive(self) -> bool:
        if not self._up:
            return False
        proc = self.proc
        if proc is None:
            return True
        return proc.poll() is None

    def send(self, line: str) -> str:
        try:
            return self._send(line)
        except WorkerDown:
            self.close()
            raise
        except OSError as exc:
            self.close()
            raise WorkerDown("worker 已断开") from exc

    def close(self) -> None:
        self._up = False
        proc = self.proc
        self.proc = None
        self.lines = None
        if proc is not None:
            _finish(proc)

    def _send(self, line: str) -> str:
        proc = self._ensure()
        if proc.stdin is None or self.lines is None:
            raise WorkerDown("worker 无管道")
        proc.stdin.write((line + "\n").encode("utf-8"))
        proc.stdin.flush()
        return self._read_reply()

    def _read_reply(self) -> str:
        if self.lines is None:
            raise WorkerDown("worker 无管道")
        deadline = time.monotonic() + _QUERY_TIMEOUT
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise WorkerDown("worker 无响应")
            reply = self.lines.read(left)
            if reply is None:
                raise WorkerDown("worker 无响应")
            text = reply.strip("\r\ufeff \t")
            if text != "":
                return text

    def _ensure(self) -> subprocess.Popen[bytes]:
        proc = self.proc
        if proc is not None:
            if proc.poll() is not None:
                raise WorkerDown("worker 已退出")
            return proc
        spawned = _spawn(self.remote)
        self.proc = spawned
        self.lines = _Lines(_stdout(spawned))
        _drain(spawned.stderr)
        return spawned


def open_channel(remote: Remote) -> SqlChannel:
    return SqlChannel(SshSqlTransport(remote))


def _stdout(proc: subprocess.Popen[bytes]) -> object:
    if proc.stdout is None:
        raise WorkerDown("worker 无管道")
    return proc.stdout


def _spawn(remote: Remote) -> subprocess.Popen[bytes]:
    body = "& '" + conf().remote("sql-worker.ps1") + "'"
    argv = ssh_argv(remote.key, remote.known_hosts, remote.mux_args())
    argv.append(remote_launch(body))
    return subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )


def _drain(pipe: object) -> None:
    if pipe is None:
        return
    thread = threading.Thread(target=_read_err, args=(pipe,), daemon=True)
    thread.start()


def _read_err(pipe: object) -> None:
    read = getattr(pipe, "read", None)
    if read is None:
        return
    try:
        read()
    except OSError:
        return


def _finish(proc: subprocess.Popen[bytes]) -> None:
    _close_in(proc)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        _kill(proc)


def _kill(proc: subprocess.Popen[bytes]) -> None:
    try:
        proc.kill()
        proc.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return


def _close_in(proc: subprocess.Popen[bytes]) -> None:
    if proc.stdin is None:
        return
    try:
        proc.stdin.close()
    except OSError:
        return
