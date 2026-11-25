"""判定能否跳过桥的编译部署：服务器上 bin\\src.sha256 记的源码哈希与本机一致、服务 binPath 是本通道的桥、
已安装的 exe 对已安装的配置 --check-config 通过、健康检查应答（remote/bridge-hash.ps1）。任何一项缺失或读不到都照常编译部署（fail open 到部署）。
源码哈希由 kitpack.source_hash 算，覆盖套件里的全部文件：桥源码、嵌进 exe 的 sql 脚本（co/bridge/sql/**）和构建文件；只改脚本也会重新部署。"""

from __future__ import annotations

import re

SKIP_NOTE = "桥未改动，跳过编译部署"
PROBE_FAILED_NOTE = "探测失败，按常规部署"
_HEX = re.compile(r"^[0-9a-f]{64}\Z")
_KEYS = ("binpath", "src_sha256", "config", "health")


def parse_probe(text: str) -> dict[str, str]:
    """bridge-hash.ps1 的输出 → {binpath, src_sha256, config, health}。不认得的行忽略，重复的键以第一次为准。"""
    found: dict[str, str] = {}
    for line in str(text or "").splitlines():
        bits = line.strip().split()
        if len(bits) == 2 and bits[0] in _KEYS and bits[0] not in found:
            found[bits[0]] = bits[1]
    return found


def local_problem(local: str, force: bool) -> str:
    """不用问服务器就知道要部署的原因（--force-deploy、本机哈希没算出来）；没有返回空串。"""
    if force:
        return "指定了 --force-deploy"
    if not _HEX.fullmatch(local or ""):
        return "本机源码哈希没算出来"
    return ""


def why_deploy(local: str, probe: dict[str, str], force: bool) -> str:
    """需要编译部署时返回原因；返回空串表示可以跳过。"""
    early = local_problem(local, force)
    if early:
        return early
    if probe.get("binpath") != "ok":
        return "服务登记核对没通过或没有取到"
    remote = probe.get("src_sha256") or ""
    if not _HEX.fullmatch(remote):
        return "服务器上没有可用的源码哈希"
    if remote != local:
        return "源码有改动"
    if probe.get("config") != "ok":
        return "已安装的配置核对（--check-config）没通过"
    if probe.get("health") != "ok":
        return "健康检查没有应答"
    return ""
