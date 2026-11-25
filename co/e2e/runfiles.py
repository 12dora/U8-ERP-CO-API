"""每次调用唯一的临时文件名。

同一条通道里可以并行跑多条 session sql / call / audit / status：本机和服务器 out 下的查询、结果、
状态和审计副本都带本次调用的标记（pid + 随机数），互不覆盖。服务器上的副本下载后尽力删掉。
"""

from __future__ import annotations

import os
import re
import secrets
import sys

from co.e2e.remote import RemoteError
from co.e2e.settings import conf

# 与服务器脚本（session-status.ps1、read-audit.ps1）里的 -Tag 规则一致。
_TAG = re.compile(r"^p\d{1,10}-[0-9a-f]{8}\Z")


def run_tag() -> str:
    return "p" + str(os.getpid()) + "-" + secrets.token_hex(4)


def tagged(tag: str, name: str) -> str:
    if _TAG.fullmatch(tag) is None:
        raise ValueError("调用标记不合法")
    return tag + "-" + name


def drop_remote_out(remote, name: str) -> None:
    # 删不掉只会在 out 下留一个小文件，不影响本次结果。
    try:
        remote.run_script("remove-file.ps1", {"Path": conf().remote("out", name)}, 60)
    except (RemoteError, OSError, ValueError):
        return


# 升级客户端时可能还有用旧版脚本打开的会话（脚本只在 open 和回归开始时上传，会话标记在时不许覆盖）。
# 旧版 session-status.ps1 不认 -Tag、照旧写不带标记的文件；旧版 read-audit.ps1 收到 -Tag 直接报参数错误。
# 这两种情况退回不带标记的旧文件名，会话照样能 status、call、audit、close，只是并行命令可能互相覆盖。
_OLD_NOTE = "警告：服务器上的会话脚本是旧版本（open 时上传），并行的 session 命令可能互相覆盖文件；close 之后再 open 就会更新。"


def status_tagged(out: str, tag: str) -> bool:
    # 新版 session-status.ps1 收到 -Tag 时打印「tag <标记>」。
    return ("tag " + tag) in out


def tag_rejected(out: str) -> bool:
    return "NamedParameterNotFound" in out or "parameter name 'Tag'" in out


def warn_old_scripts() -> None:
    print(_OLD_NOTE, file=sys.stderr)
