#!/usr/bin/env python3
# co/**/*.cs：文件非空非注释行，以及 lizard 的函数 NLOC、圈复杂度、参数个数。
# 没有基线。函数度量固定用 lizard 1.17.13，不把 lizard 装进本仓库。

from __future__ import annotations

import argparse, csv, subprocess, sys
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIZARD_VERSION = "1.17.13"
LIMIT_FILE = 500
LIMIT_NLOC = 60
LIMIT_CCN = 10
LIMIT_PARAMS = 6
SKIP_DIRS = {".git", "__pycache__", ".ruff_cache", ".venv", "venv", "node_modules", "out"}
RULE_ORDER = {"file-lines": 0, "CS-CCN": 1, "CS-PARAMS": 2, "CS-NLOC": 3}


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _nxt(line: str, i: int) -> str:
    return line[i + 1] if i + 1 < len(line) else ""


def _unit_block(line: str, i: int) -> tuple[str, int, bool]:
    if line[i] == "*" and _nxt(line, i) == "/":
        return "code", i + 2, False
    return "block", i + 1, False


def _unit_str(line: str, i: int) -> tuple[str, int, bool]:
    if line[i] == "\\" and _nxt(line, i):
        return "str", i + 2, True
    if line[i] == '"':
        return "code", i + 1, True
    return "str", i + 1, True


def _unit_verb(line: str, i: int) -> tuple[str, int, bool]:
    if line[i] == '"' and _nxt(line, i) == '"':
        return "verb", i + 2, True
    if line[i] == '"':
        return "code", i + 1, True
    return "verb", i + 1, not line[i].isspace()


def _unit_char(line: str, i: int) -> tuple[str, int, bool]:
    if line[i] == "\\" and _nxt(line, i):
        return "char", i + 2, True
    if line[i] == "'":
        return "code", i + 1, True
    return "char", i + 1, True


def _unit_code(line: str, i: int) -> tuple[str, int, bool]:
    nxt = _nxt(line, i)
    ch = line[i]
    if ch == "/" and nxt == "/":
        return "code", len(line), False
    if ch == "/" and nxt == "*":
        return "block", i + 2, False
    if ch == '"':
        return "str", i + 1, True
    if ch == "@" and nxt == '"':
        return "verb", i + 2, True
    if ch == "'":
        return "char", i + 1, True
    return "code", i + 1, not ch.isspace()


def _unit(line: str, i: int, mode: str) -> tuple[str, int, bool]:
    if mode == "block":
        return _unit_block(line, i)
    if mode == "str":
        return _unit_str(line, i)
    if mode == "verb":
        return _unit_verb(line, i)
    if mode == "char":
        return _unit_char(line, i)
    return _unit_code(line, i)


def _scan_line(line: str, mode: str) -> tuple[str, bool]:
    has, i = False, 0
    while i < len(line):
        mode, i, piece = _unit(line, i, mode)
        has = has or piece
    return mode, has


def cs_file_lines(text: str) -> int:
    mode, count = "code", 0
    for line in _normalize(text).split("\n"):
        mode, has = _scan_line(line, mode)
        count += int(has)
    return count


def _row(path: str, line: int, rule: str, metric: int, limit: int, name: str = "") -> str:
    text = f"{path}:{line} {rule} metric={metric} limit={limit}"
    if name:
        text += f" name={name}"
    return text


def rel_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def _under_co(path: Path) -> bool:
    if path.suffix.lower() != ".cs":
        return False
    try:
        rel = path.resolve().relative_to(ROOT)
    except ValueError:
        return False
    return bool(rel.parts) and rel.parts[0] == "co"


def iter_files(selected: list[Path] | None) -> list[Path]:
    if selected is not None:
        picked = []
        for path in selected:
            candidate = path if path.is_absolute() else ROOT / path
            if candidate.is_file() and _under_co(candidate):
                picked.append(candidate)
        return picked
    root = ROOT / "co"
    if not root.is_dir():
        return []
    return [
        path
        for path in root.rglob("*.cs")
        if path.is_file() and not any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts)
    ]


def file_rows(paths: list[Path]) -> list[tuple[str, int, int, str]]:
    found = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeError as exc:
            print(f"cs 警告: 跳过无法按 UTF-8 读取的 {path}: {exc}", file=sys.stderr)
            continue
        count = cs_file_lines(text)
        if count > LIMIT_FILE:
            found.append((rel_path(path), 1, 0, _row(rel_path(path), 1, "file-lines", count, LIMIT_FILE)))
    return found


def _lizard_stderr(proc: subprocess.CompletedProcess[str]) -> None:
    if proc.stderr:
        sys.stderr.write(proc.stderr if proc.stderr.endswith("\n") else proc.stderr + "\n")


def _lizard_error(proc: subprocess.CompletedProcess[str]) -> None:
    _lizard_stderr(proc)
    print(
        f"无法运行 lizard {LIZARD_VERSION}（退出码 {proc.returncode}）。"
        "C# 函数行数、圈复杂度和参数个数依赖 "
        f"uvx --from lizard=={LIZARD_VERSION} lizard -l csharp --csv。"
        "请确认 uvx 能取到这个版本（本机 uv 缓存或网络）。",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _lizard_status(proc: subprocess.CompletedProcess[str]) -> None:
    _lizard_stderr(proc)
    print(
        f"lizard {LIZARD_VERSION} 退出码 {proc.returncode}，不能用它的输出做 C# 函数门禁。"
        "标准输出即使看起来像 CSV 也不采纳。"
        "函数行数、圈复杂度和参数个数依赖 "
        f"uvx --from lizard=={LIZARD_VERSION} lizard -l csharp --csv，并且该进程必须以 0 退出。",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _csv_ready(text: str) -> bool:
    # lizard 1.17.13 的 --csv 默认没有表头，第一列是 NLOC。
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = next(csv.reader([line]))
        except csv.Error:
            return False
        if row and row[0].strip().upper() == "NLOC":
            return True
        return len(row) >= 11 and row[0].isdigit() and row[1].isdigit() and row[9].isdigit()
    return False


def run_lizard(paths: list[Path]) -> str:
    command = [
        "uvx",
        "--from",
        f"lizard=={LIZARD_VERSION}",
        "lizard",
        "-l",
        "csharp",
        "--csv",
        *[rel_path(path) for path in paths],
    ]
    try:
        proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    except FileNotFoundError:
        print(
            f"无法运行 lizard {LIZARD_VERSION}。未找到 uvx。"
            f"C# 函数度量需要 uvx --from lizard=={LIZARD_VERSION} lizard。",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    if proc.returncode != 0:
        _lizard_status(proc)
    if not proc.stdout.strip():
        return ""
    if not _csv_ready(proc.stdout):
        _lizard_error(proc)
    return proc.stdout


def _metric(row: list[str], index: int, label: str) -> int:
    try:
        return int(row[index])
    except (IndexError, ValueError):
        print(f"lizard CSV 的 {label} 不是整数: {row!r}", file=sys.stderr)
        raise SystemExit(1) from None


def _func_path(raw: str) -> str:
    path = Path(raw)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _take(row: list[str], index: int, label: str) -> str:
    if len(row) <= index:
        print(f"lizard CSV 缺少 {label}。", file=sys.stderr)
        raise SystemExit(1)
    return row[index]


def function_rows(text: str) -> list[tuple[str, int, int, str]]:
    # 无表头：NLOC,CCN,token,PARAM,length,location,file,function,long_name,start,end
    # name 用 function（Namespace::Class::Method），不用带参数列表的 long_name。
    found = []
    for row in csv.reader(StringIO(text)):
        if not row or (row[0].strip().upper() == "NLOC" and not row[0].strip().isdigit()):
            continue
        if len(row) < 11:
            print(f"lizard CSV 列数不足: {row!r}", file=sys.stderr)
            raise SystemExit(1)
        path = _func_path(_take(row, 6, "file"))
        name = _take(row, 7, "function").strip() or "?"
        line = _metric(row, 9, "start")
        checks = (
            ("CS-CCN", _metric(row, 1, "CCN"), LIMIT_CCN, 1),
            ("CS-PARAMS", _metric(row, 3, "PARAM"), LIMIT_PARAMS, 2),
            ("CS-NLOC", _metric(row, 0, "NLOC"), LIMIT_NLOC, 3),
        )
        for rule, metric, limit, order in checks:
            if metric > limit:
                found.append((path, line, order, _row(path, line, rule, metric, limit, name)))
    return found


def _sort_key(item: tuple[str, int, int, str]) -> tuple[str, int, int]:
    return item[0], item[1], item[2]


def collect(paths: list[Path]) -> list[str]:
    if not paths:
        return []
    rows = file_rows(paths)
    rows.extend(function_rows(run_lizard(paths)))
    rows.sort(key=_sort_key)
    return [item[3] for item in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C# 文件行数与 lizard 函数门禁")
    parser.add_argument("--subset", action="store_true")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    selected = [Path(item) for item in args.paths] if args.subset else None
    rows = collect(iter_files(selected))
    if rows:
        print("\n".join(rows))
        print(f"cs FAIL {len(rows)}")
        return 1
    print("cs OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
