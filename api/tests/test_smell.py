"""Enforce the file, function, and parameter limits on this package."""

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SKIP = {".venv"}


def test_size_limits():
    problems: list[str] = []
    for path in sorted(_ROOT.rglob("*.py")):
        if _SKIP.intersection(path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        meaningful = _meaningful(text)
        if meaningful > 400:
            problems.append(f"{path.name} has {meaningful} lines")
        problems.extend(_functions(path.name, text))
    assert problems == []


def _meaningful(text: str) -> int:
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            count += 1
    return count


def _functions(name: str, text: str) -> list[str]:
    found: list[str] = []
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        span = node.end_lineno - node.lineno + 1
        if span > 60:
            found.append(f"{name}:{node.name} spans {span}")
        if _params(node) > 5:
            found.append(f"{name}:{node.name} has {_params(node)} params")
    return found


def _params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    args = node.args
    names = [item.arg for item in args.posonlyargs + args.args]
    count = len(names) + len(args.kwonlyargs)
    if names and names[0] in {"self", "cls"}:
        count -= 1
    if args.vararg:
        count += 1
    if args.kwarg:
        count += 1
    return count
