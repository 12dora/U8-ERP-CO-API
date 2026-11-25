"""把桥源码打成一个 zip，回归只上传这一份。source_hash 是同一批文件的 SHA-256，用来判断服务器上的桥是否已是这份源码。"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def kit_files(root: Path) -> list[tuple[Path, str]]:
    """[(本机路径, 压缩包内路径)]，按压缩包内路径排好。src 下的 .cs、private 下的 .cs（可选的附加源码目录，存在时才带上）、sql 下（含子目录）的 .sql、build.ps1、exe.config、SafePath.ps1。"""
    folder = root / "co" / "bridge" / "src"
    sources = sorted(folder.glob("*.cs"))
    if not sources:
        raise RuntimeError("src 下没有 .cs")
    bridge = root / "co" / "bridge"
    items = [(path, "bridge/src/" + path.name) for path in sources]
    items.extend((path, "bridge/private/" + path.name) for path in sorted((bridge / "private").glob("*.cs")))
    items.extend(sql_files(bridge))
    items.append((bridge / "build.ps1", "bridge/build.ps1"))
    items.append((bridge / "u8co-bridge.exe.config", "bridge/u8co-bridge.exe.config"))
    items.append((root / "co" / "SafePath.ps1", "SafePath.ps1"))
    items.sort(key=lambda item: item[1])
    return items


def sql_files(bridge: Path) -> list[tuple[Path, str]]:
    """bridge/sql 下的 .sql（build.ps1 把它们嵌进 exe）。压缩包内路径是 bridge/ 加相对路径，用 /。没有 sql 目录时为空。
    只认一层模块目录 sql/<模块>/*.sql（扩展名小写），与 build.ps1、remote/kit-extract.ps1 同一规则；别处的 .sql 直接报错。"""
    folder = bridge / "sql"
    if not folder.is_dir():
        return []
    found = []
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() != ".sql":
            continue
        rel = path.relative_to(bridge)
        if len(rel.parts) != 3 or path.suffix != ".sql":
            raise RuntimeError("sql 脚本只能放在 sql/<模块>/ 下、扩展名小写 .sql：" + rel.as_posix())
        found.append((path, "bridge/" + rel.as_posix()))
    return sorted(found, key=lambda item: item[1])


def pack_bridge(dest: Path) -> None:
    root = repo_root()
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, name in kit_files(root):
            _add(archive, path, name, root)


def source_hash(root: Path | None = None) -> str:
    """套件文件的 SHA-256（小写十六进制）：按压缩包内路径排序，每个文件写入「路径、长度、原样字节」。
    不统一换行：上传的就是这些字节，换行不同编出来的程序（例如字符串常量里的换行）也可能不同。"""
    base = root or repo_root()
    digest = hashlib.sha256()
    for path, name in kit_files(base):
        full = _checked(path, name, base)
        data = full.read_bytes()
        digest.update(name.encode("utf-8") + b"\0" + str(len(data)).encode("ascii") + b"\0")
        digest.update(data)
    return digest.hexdigest()


def _checked(path: Path, name: str, root: Path) -> Path:
    _check_arc(name)
    full = path.resolve()
    if not full.is_file():
        raise RuntimeError("套件文件缺失")
    base = root.resolve()
    if full != base and base not in full.parents:
        raise RuntimeError("拒绝上传仓库以外的文件")
    return full


def _add(archive: zipfile.ZipFile, path: Path, name: str, root: Path) -> None:
    archive.write(_checked(path, name, root), name)


def _check_arc(name: str) -> None:
    parts = name.split("/")
    if name.startswith("/") or "\\" in name or ".." in parts or "" in parts:
        raise RuntimeError("压缩包路径非法")
