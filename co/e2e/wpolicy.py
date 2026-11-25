"""写入策略文件：在本通道桥的运行目录里原子替换 config.json 的 writePolicyFile，等桥重载生效，用完还原。

替换走 SSH 跑一段 PowerShell：读桥 config.json 的 writePolicyFile（相对路径按桥的运行目录解析，必须在目录之下），
内容以 base64 带进脚本，先写同目录的临时文件，再原子改名（已有文件用 File.Replace，保留原文件的权限）。
桥每 reloadSeconds 秒读一次文件，按内容的 SHA-256 判断是否变了（与上次读到的内容相同就不重载，loaded_at 不变）；
这里轮询 /health 的 write_policy，直到 loaded_at 变了且 state 为 ok（期望无效内容时等 state 变成 invalid），
最多 WAIT_SECONDS 秒。loaded_at 只精确到秒：用例写的策略都带 reloadSeconds 1，桥两次重载至少隔 1 秒，所以「变了」可靠；
换成更短的重载间隔前要改成比较内容摘要或版本计数。
policy_swap 先读下原文件和当时的 loaded_at（基线），退出时（含异常）写回原内容；原文件不存在时写宽松的缺省策略
PERMISSIVE（通道上从此多一个文件）。写回前先读当前文件：内容已是原内容、/health 为 ok 且 loaded_at 仍是基线
（apply 没写成，或桥还没读到新内容就被写回）时桥一直用的就是原策略，直接算还原成功——此时写同样的字节桥不会重载，
等 loaded_at 变只会超时。写回带重试和退避（RESTORE_TRIES 次，每次最多等 RESTORE_WAIT 秒）；仍失败时把本通道标记为
阻塞（lane_blocked），抛 PolicyNotRestored，之后的用例由 runner 直接记 SKIP 并带上原因，不在受限策略下继续跑。
"""

from __future__ import annotations

import base64
import json
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from co.client.u8co_client import U8CoError
from co.e2e.settings import conf

# 测试通道的缺省策略：名单外账套放行，没有冻结、时段和限额。
PERMISSIVE = {"version": 1, "unlisted": "allow"}
WAIT_SECONDS = 10.0
RESTORE_TRIES = 4
RESTORE_BACKOFF = 2.0
RESTORE_WAIT = 30.0
NOT_RESTORED = "本通道写入策略没有还原（lane policy NOT restored）"
_POLL = 0.5
_TIMEOUT = 60
_NOKEY = "WPF-NOKEY"
_NONE = "WPF-NONE"
_B64 = "WPF-B64:"
_DONE = "WPF-WRITTEN"

_BODY = r"""
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath('@ROOT@').TrimEnd('\')
$cfg = Get-Content -LiteralPath ($root + '\config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$name = ([string]$cfg.writePolicyFile).Trim()
if ($name -eq '') { Write-Output 'WPF-NOKEY'; exit 0 }
if ([System.IO.Path]::IsPathRooted($name)) { $file = [System.IO.Path]::GetFullPath($name) }
else { $file = [System.IO.Path]::GetFullPath($root + '\' + $name) }
if (-not $file.StartsWith($root + '\', [System.StringComparison]::OrdinalIgnoreCase)) { throw '写入策略文件不在桥的运行目录之下' }
if ('@MODE@' -eq 'read') {
  if ([System.IO.File]::Exists($file)) {
    Write-Output ('WPF-B64:' + [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($file)))
  }
  else { Write-Output 'WPF-NONE' }
  exit 0
}
$tmp = $file + '.e2e-tmp'
[System.IO.File]::WriteAllBytes($tmp, [Convert]::FromBase64String('@B64@'))
if ([System.IO.File]::Exists($file)) { [System.IO.File]::Replace($tmp, $file, [NullString]::Value) }
else { [System.IO.File]::Move($tmp, $file) }
Write-Output 'WPF-WRITTEN'
"""


class PolicyFileError(RuntimeError):
    """读写策略文件失败，或桥在限定时间内没有按期望重载。"""


class PolicyNotRestored(PolicyFileError):
    """重试之后仍没能写回原策略：本通道已标记阻塞。"""


_blocked: list[str] = []
_block_lock = threading.Lock()


def lane_blocked() -> str:
    """本通道（本进程）因策略没有还原而阻塞时返回原因，否则空串。"""
    with _block_lock:
        return _blocked[0] if _blocked else ""


def block_lane(reason: str) -> None:
    with _block_lock:
        if not _blocked:
            _blocked.append(reason)


def policy_body(mode: str, data: bytes = b"") -> str:
    if mode not in ("read", "write"):
        raise ValueError("mode 只能是 read 或 write")
    root = conf().bridge_root.replace("'", "''")
    payload = base64.b64encode(data).decode("ascii")
    return _BODY.replace("@ROOT@", root).replace("@MODE@", mode).replace("@B64@", payload)


def _run(remote, mode: str, data: bytes = b"") -> str:
    result = remote.run_body(policy_body(mode, data), _TIMEOUT)
    out = str(result.out or "")
    if result.code != 0:
        raise PolicyFileError("写入策略文件操作失败：" + out.strip()[-200:])
    if _NOKEY in out:
        raise PolicyFileError("桥 config.json 没有配置 writePolicyFile")
    return out


def read_policy(remote) -> bytes | None:
    """当前策略文件的原始字节；文件不存在为 None。"""
    out = _run(remote, "read")
    for line in out.splitlines():
        if line.startswith(_B64):
            return base64.b64decode(line[len(_B64) :].strip())
    if _NONE in out:
        return None
    raise PolicyFileError("读取写入策略文件没有结果：" + out.strip()[-200:])


def write_policy(remote, data: bytes) -> None:
    out = _run(remote, "write", data)
    if _DONE not in out:
        raise PolicyFileError("替换写入策略文件没有完成：" + out.strip()[-200:])


def health_policy(client) -> dict | None:
    """/health 的 write_policy 对象；没有（未升级的桥）为 None。"""
    found = client.health().get("write_policy")
    return found if isinstance(found, dict) else None


def _reached(found: dict, before: object, want: str) -> bool:
    if want == "invalid":
        return found.get("state") == "invalid"
    return found.get("state") == "ok" and found.get("loaded_at") != before


def wait_reload(client, before: object, want: str = "ok", timeout: float = WAIT_SECONDS) -> dict:
    """轮询 /health，直到策略重载到期望状态；返回当时的 write_policy。超时抛 PolicyFileError。"""
    deadline = time.monotonic() + timeout
    while True:
        found = health_policy(client) or {}
        if _reached(found, before, want):
            return found
        if time.monotonic() >= deadline:
            got = f"state={found.get('state')} loaded_at={found.get('loaded_at')}"
            raise PolicyFileError(f"写入策略 {timeout:g} 秒内没有重载为 {want}：{got}")
        time.sleep(_POLL)


def _bytes(policy: dict | str | bytes) -> bytes:
    if isinstance(policy, bytes):
        return policy
    if isinstance(policy, str):
        return policy.encode("utf-8")
    return json.dumps(policy, ensure_ascii=False).encode("utf-8")


class PolicySwap:
    """一次用例里的策略替换：apply 换内容并等生效，restore 写回原内容。"""

    def __init__(self, remote, client, original: bytes | None, baseline: object = None) -> None:
        self.remote = remote
        self.client = client
        self.original = original
        # 替换前桥的 loaded_at；None 表示不知道，不走「内容未变、无需重载」的捷径。
        self.baseline = baseline

    def apply(self, policy: dict | str | bytes, want: str = "ok") -> dict:
        before = (health_policy(self.client) or {}).get("loaded_at")
        write_policy(self.remote, _bytes(policy))
        return wait_reload(self.client, before, want)

    def restore(self) -> dict:
        """写回原内容并等重载为 ok，失败按 RESTORE_BACKOFF 起倍增退避重试；loaded_at 以第一次写之前为准，
        前一次写入已生效只是等超时的，重试时同样认。文件已是原内容且桥仍停在基线（见模块说明）时不写、不等。
        全部失败时标记本通道阻塞并抛 PolicyNotRestored。"""
        data = _bytes(self.original if self.original is not None else PERMISSIVE)
        before = _loaded_at(self.client)
        delay = RESTORE_BACKOFF
        last = ""
        for attempt in range(1, RESTORE_TRIES + 1):
            try:
                before = before if before is not None else _loaded_at(self.client)
                if before is None:
                    # 不知道写之前的 loaded_at，就分不清桥是否已重载，不能算还原成功。
                    raise PolicyFileError("读不到 /health 的 write_policy.loaded_at")
                return self._restore_once(data, before)
            except (PolicyFileError, OSError, ValueError, U8CoError) as exc:
                last = f"第 {attempt} 次：{exc}"
                print(f"写回原写入策略失败，{last}", file=sys.stderr)
            if attempt < RESTORE_TRIES:
                time.sleep(delay)
                delay *= 2
        reason = f"{NOT_RESTORED}：重试 {RESTORE_TRIES} 次仍失败，请手工恢复桥的 writePolicyFile；最后一次 {last}"
        block_lane(reason[:400])
        raise PolicyNotRestored(reason)

    def _restore_once(self, data: bytes, before: object) -> dict:
        if read_policy(self.remote) == data:
            found = self._untouched()
            if found is not None:
                return found
            # 文件已是原内容但桥重载过别的内容：上一次写回已落盘，只等桥重载（内容与桥上次读到的不同才会重载）。
        else:
            write_policy(self.remote, data)
        return wait_reload(self.client, before, "ok", RESTORE_WAIT)

    def _untouched(self) -> dict | None:
        """桥自替换前一直没有重载（state ok、loaded_at 仍是基线）时返回当时的 write_policy，否则 None。"""
        if self.baseline is None:
            return None
        found = health_policy(self.client) or {}
        if found.get("state") == "ok" and found.get("loaded_at") == self.baseline:
            return found
        return None


def _loaded_at(client) -> object:
    try:
        return (health_policy(client) or {}).get("loaded_at")
    except (OSError, ValueError, U8CoError):
        return None


@contextmanager
def policy_swap(remote, client) -> Iterator[PolicySwap]:
    """读下原策略文件；退出时（含异常）写回并等桥重载为 ok。用例本身的异常先记到 stderr，再还原，
    还原失败（PolicyNotRestored）不会把它悄悄吞掉。"""
    swap = PolicySwap(remote, client, read_policy(remote), _loaded_at(client))
    try:
        yield swap
    except Exception as exc:
        print(f"写入策略用例异常，先还原原策略：{exc!r}", file=sys.stderr)
        raise
    finally:
        swap.restore()
