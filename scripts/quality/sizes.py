#!/usr/bin/env python3
# 非空非注释行。Markdown 不查。Python 函数走 ast，bash 走文本，PowerShell 走 AST。

from __future__ import annotations

import argparse, ast, json, re, subprocess, sys, tokenize
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIMITS = {"py": 400, "py_test": 800, "sh": 300, "sql": 400, "ps1": 400}
FUNC_LIMIT = 60
SKIP_DIRS = {".git", "__pycache__", ".ruff_cache", ".venv", "venv", "node_modules", "out"}
PY_SKIP = {
    tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
    tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER,
}
FUNC_RE = re.compile(
    r"^([ \t]*)(?:function[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)(?:\(\))?[ \t]*\{(.*)$"
)
HEREDOC_RE = re.compile(r"(-)?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
SHELL_SHEBANG_RE = re.compile(r"(^|[ /\t])(bash|sh|dash)\b")


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _hash_comment(line: str, index: int) -> bool:
    if index == 0:
        return True
    prev = line[index - 1]
    return prev.isspace() or prev in ";&|()<>`"


def _scan_shell_line(line: str, in_single: bool, in_double: bool):
    has = False
    heredoc = None
    i = 0
    while i < len(line):
        ch = line[i]
        nxt = line[i + 1] if i + 1 < len(line) else ""
        if in_single:
            has = has or not ch.isspace()
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            has = True
            if ch == "\\" and nxt:
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue
        step = _shell_code(line, i, ch, nxt)
        if step[0] == "break":
            break
        has = has or step[1]
        in_single = in_single or step[2]
        in_double = in_double or step[3]
        if step[4] is not None:
            heredoc = step[4]
        i = step[5]
    return has, in_single, in_double, heredoc


def _shell_code(line: str, i: int, ch: str, nxt: str):
    if ch == "\\" and nxt:
        return ("go", True, False, False, None, i + 2)
    if ch == "'":
        return ("go", True, True, False, None, i + 1)
    if ch == '"':
        return ("go", True, False, True, None, i + 1)
    if ch == "#" and _hash_comment(line, i):
        return ("break", False, False, False, None, i)
    if ch == "<" and nxt == "<" and not (i + 2 < len(line) and line[i + 2] == "<"):
        matched = HEREDOC_RE.match(line[i + 2 :])
        if matched:
            heredoc = (matched.group(3), matched.group(1) == "-")
            return ("go", True, False, False, heredoc, i + 2 + matched.end())
    has = not ch.isspace()
    return ("go", has, False, False, None, i + 1)


def shell_parts(text: str) -> tuple[list[bool], list[bool]]:
    code: list[bool] = []
    in_here: list[bool] = []
    in_single = False
    in_double = False
    active = None
    for line in _normalize(text).splitlines():
        if active is not None:
            delim, strip_tabs = active
            body = line.lstrip("\t") if strip_tabs else line
            if body == delim:
                code.append(True)
                in_here.append(False)
                active = None
            else:
                code.append(bool(line.strip()))
                in_here.append(True)
            continue
        has, in_single, in_double, active = _scan_shell_line(line, in_single, in_double)
        code.append(has)
        in_here.append(False)
    return code, in_here


def _is_func_close(line: str, indent: str) -> bool:
    if not line.startswith(indent):
        return False
    rest = line[len(indent) :]
    return re.fullmatch(r"\}[ \t]*(?:#.*)?", rest) is not None


def _same_line_close(rest: str) -> bool:
    depth, quote, i = 1, "", 0
    while i < len(rest):
        ch = rest[i]
        nxt = rest[i + 1] if i + 1 < len(rest) else ""
        if quote == "'" and ch == "'":
            quote = ""
        elif quote == '"' and ch == "\\" and nxt:
            i += 2
            continue
        elif quote == '"' and ch == '"':
            quote = ""
        elif not quote and ch in "'\"":
            quote = ch
        elif not quote and ch == "\\" and nxt:
            i += 2
            continue
        elif not quote and ch == "#" and _hash_comment(rest, i):
            break
        elif not quote and ch == "{":
            depth += 1
        elif not quote and ch == "}":
            depth -= 1
            if depth == 0:
                return True
        i += 1
    return False


def _find_func_end(lines: list[str], in_here: list[bool], start: int, indent: str) -> int | None:
    for pos in range(start + 1, len(lines)):
        if in_here[pos]:
            continue
        if _is_func_close(lines[pos], indent):
            return pos
    return None


def shell_functions(text: str) -> tuple[list[dict], list[str]]:
    lines = _normalize(text).splitlines()
    code, in_here = shell_parts(text)
    found: list[dict] = []
    warnings: list[str] = []
    for index, line in enumerate(lines):
        if in_here[index]:
            continue
        matched = FUNC_RE.match(line)
        if matched is None or _same_line_close(matched.group(3)):
            continue
        end = _find_func_end(lines, in_here, index, matched.group(1))
        if end is None:
            warnings.append(f"未找到函数 {matched.group(2)} 的结束括号（第 {index + 1} 行）")
            continue
        count = sum(1 for pos in range(index, end + 1) if code[pos])
        found.append({"name": matched.group(2), "line": index + 1, "count": count})
    return found, warnings


def _py_skip_types() -> set[int]:
    skip = set(PY_SKIP)
    for name in ("TYPE_COMMENT", "TYPE_IGNORE", "FSTRING_START", "FSTRING_END", "FSTRING_MIDDLE"):
        token_type = getattr(tokenize, name, None)
        if token_type is not None and name.startswith("TYPE"):
            skip.add(token_type)
    return skip


def python_code_lines(text: str) -> set[int] | None:
    raw = text.splitlines()
    marked: set[int] = set()
    skip = _py_skip_types()
    try:
        tokens = tokenize.tokenize(BytesIO(text.encode("utf-8")).readline)
        for tok in tokens:
            if tok.type in skip:
                continue
            end_line = tok.end[0] - 1 if tok.end[1] == 0 else tok.end[0]
            for line_no in range(tok.start[0], end_line + 1):
                if 1 <= line_no <= len(raw) and raw[line_no - 1].strip():
                    marked.add(line_no)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    return marked


def _func_span(node: ast.AST) -> tuple[int, int]:
    start = getattr(node, "lineno", 1)
    for deco in getattr(node, "decorator_list", ()):
        start = min(start, deco.lineno)
    end = getattr(node, "end_lineno", None) or start
    return start, end


def python_functions(text: str, lines: set[int]) -> list[dict]:
    tree = ast.parse(text)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start, end = _func_span(node)
        count = sum(1 for line_no in range(start, end + 1) if line_no in lines)
        found.append({"name": node.name, "line": node.lineno, "count": count})
    return found


def _sql_line(line: str, mode: str) -> tuple[str, bool]:
    has, i = False, 0
    closers = {"sq": "'", "dq": '"', "br": "]"}
    while i < len(line):
        ch = line[i]
        nxt = line[i + 1] if i + 1 < len(line) else ""
        if mode == "block":
            if ch == "*" and nxt == "/":
                mode = "code"
                i += 1
            i += 1
            continue
        if mode in closers:
            has = True
            if ch == closers[mode] and nxt == closers[mode]:
                i += 2
                continue
            if ch == closers[mode]:
                mode = "code"
            i += 1
            continue
        if ch == "-" and nxt == "-":
            break
        if ch == "/" and nxt == "*":
            mode, i = "block", i + 2
            continue
        opened = {"'": "sq", '"': "dq", "[": "br"}.get(ch)
        if opened:
            mode, has, i = opened, True, i + 1
            continue
        has = has or not ch.isspace()
        i += 1
    return mode, has


def sql_code_lines(text: str) -> int:
    mode, count = "code", 0
    for line in _normalize(text).split("\n"):
        mode, has = _sql_line(line, mode)
        count += int(has)
    return count


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def is_test_py(path: Path) -> bool:
    name = path.name
    return name.startswith("test_") or name.endswith("_test.py")


def is_shell_shebang(path: Path) -> bool:
    if path.suffix:
        return False
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            line = handle.readline(200)
    except OSError:
        return False
    return line.startswith("#!") and SHELL_SHEBANG_RE.search(line) is not None


def kind_of(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return "py_test" if is_test_py(path) else "py"
    if suffix == ".sql":
        return "sql"
    if suffix == ".ps1":
        return "ps1"
    if suffix in {".sh", ".bash"}:
        return "sh"
    if suffix in {".md", ".markdown"}:
        return None
    if is_shell_shebang(path):
        return "sh"
    return None


def rel_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def _row(path: str, line: int, rule: str, metric: int, limit: int, name: str = "") -> str:
    text = f"{path}:{line} {rule} metric={metric} limit={limit}"
    if name:
        text += f" name={name}"
    return text


def _func_rows(path: str, funcs: list[dict]) -> list[str]:
    rows = []
    for func in funcs:
        if func["count"] > FUNC_LIMIT:
            rows.append(_row(path, func["line"], "func-lines", func["count"], FUNC_LIMIT, func["name"]))
    return rows


def measure_python(path: Path, kind: str, text: str) -> list[str]:
    name = rel_path(path)
    rows: list[str] = []
    try:
        marked = python_code_lines(text)
        if marked is None:
            raise SyntaxError("tokenize")
        funcs = python_functions(text, marked)
        count = len(marked)
    except SyntaxError as exc:
        line = exc.lineno or 1
        rows.append(_row(name, line, "py-parse", 1, 0))
        count = sum(1 for raw in _normalize(text).split("\n") if raw.split("#", 1)[0].strip())
        funcs = []
    limit = LIMITS[kind]
    if count > limit:
        rows.append(_row(name, 1, "file-lines", count, limit))
    rows.extend(_func_rows(name, funcs))
    return rows


def measure_shell(path: Path, text: str) -> list[str]:
    name = rel_path(path)
    code, _ = shell_parts(text)
    funcs, warnings = shell_functions(text)
    for warning in warnings:
        print(f"sizes 警告: {name}: {warning}", file=sys.stderr)
    rows = []
    count = sum(1 for flag in code if flag)
    if count > LIMITS["sh"]:
        rows.append(_row(name, 1, "file-lines", count, LIMITS["sh"]))
    rows.extend(_func_rows(name, funcs))
    return rows


def measure_sql(path: Path, text: str) -> list[str]:
    count = sql_code_lines(text)
    if count > LIMITS["sql"]:
        return [_row(rel_path(path), 1, "file-lines", count, LIMITS["sql"])]
    return []


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def powershell_rows(paths: list[Path]) -> list[str]:
    if not paths:
        return []
    command = ["bash", str(ROOT / "scripts" / "quality" / "pwsh.sh"), "--json", "--", *[str(p) for p in paths]]
    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if proc.stderr:
        sys.stderr.write(proc.stderr if proc.stderr.endswith("\n") else proc.stderr + "\n")
    if proc.returncode != 0:
        print("PowerShell 度量失败，体积门禁无法统计 .ps1。", file=sys.stderr)
        raise SystemExit(proc.returncode or 1)
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        print(proc.stdout, file=sys.stderr)
        print("PowerShell 度量没有返回 JSON。", file=sys.stderr)
        raise SystemExit(1) from None
    rows = []
    for item in _as_list(payload.get("results")):
        path = item["path"]
        if int(item["lines"]) > LIMITS["ps1"]:
            rows.append(_row(path, 1, "file-lines", int(item["lines"]), LIMITS["ps1"]))
        for func in _as_list(item.get("functions")):
            if int(func["lines"]) > FUNC_LIMIT:
                rows.append(
                    _row(path, int(func["line"]), "func-lines", int(func["lines"]), FUNC_LIMIT, func["name"])
                )
    return rows


def iter_files(selected: list[Path] | None) -> list[Path]:
    if selected is not None:
        picked = []
        for path in selected:
            candidate = path if path.is_absolute() else ROOT / path
            if candidate.is_file():
                picked.append(candidate)
        return picked
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file() and not any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts)
    ]


def collect(selected: list[Path] | None) -> list[str]:
    rows: list[str] = []
    ps_paths: list[Path] = []
    for path in iter_files(selected):
        kind = kind_of(path)
        if kind is None:
            continue
        if kind == "ps1":
            ps_paths.append(path)
            continue
        try:
            text = read_text(path)
        except UnicodeError as exc:
            print(f"sizes 警告: 跳过无法按 UTF-8 读取的 {path}: {exc}", file=sys.stderr)
            continue
        if kind in {"py", "py_test"}:
            rows.extend(measure_python(path, kind, text))
        elif kind == "sh":
            rows.extend(measure_shell(path, text))
        elif kind == "sql":
            rows.extend(measure_sql(path, text))
    rows.extend(powershell_rows(ps_paths))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="文件体积与函数长度门禁")
    parser.add_argument("--subset", action="store_true")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    selected = [Path(item) for item in args.paths] if args.subset else None
    rows = collect(selected)
    if rows:
        print("\n".join(rows))
        print(f"sizes FAIL {len(rows)}")
        return 1
    print("sizes OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
