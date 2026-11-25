"""把桥的 JSON 收成可比较的字符串。"""

from __future__ import annotations

from dataclasses import dataclass

from co.e2e.casespec.assertutil import (
    code_matches,
    dig,
    history_actions,
    instance_off,
    status_known,
    task_hit,
)


@dataclass(frozen=True)
class View:
    focus: str
    prefix: str
    day: str
    opname: str


def _plain(val: object) -> str:
    if val is None:
        return ""
    if isinstance(val, bool):
        return "true" if val else "false"
    return str(val)


def _length(payload: dict, path: str) -> str:
    val = dig(payload, path)
    if isinstance(val, list):
        return str(len(val))
    return ""


def _actions(payload: dict, _view: View) -> str:
    return history_actions(payload)


def _type5(payload: dict, view: View) -> str:
    if task_hit(payload, view.focus, "5"):
        return "5"
    return ""


def _off(payload: dict, _view: View) -> str:
    if instance_off(payload):
        return "off"
    return ""


def _head(payload: dict, _view: View) -> str:
    head = payload.get("head")
    if isinstance(head, dict) and head:
        return "yes"
    return ""


def _prefix(payload: dict, view: View) -> str:
    code = str(payload.get("code") or "")
    if code_matches(code, view.prefix, view.day):
        return "yes"
    return code


def _generated(payload: dict, _view: View) -> str:
    rows = payload.get("generated")
    if not isinstance(rows, list):
        return ""
    names = []
    for row in rows:
        if not isinstance(row, dict):
            return ""
        names.append(str(row.get("type") or ""))
    return ",".join(names)


_SPECIAL = {
    "history#actions": _actions,
    "tasks#type5": _type5,
    "instance#off": _off,
    "head#nonempty": _head,
    "code#prefix": _prefix,
    "generated#types": _generated,
}


def read_path(payload: dict, path: str, view: View) -> str:
    if path in _SPECIAL:
        return _SPECIAL[path](payload, view)
    if path.endswith("#len"):
        return _length(payload, path[:-4])
    return _plain(dig(payload, path))


def _positive(text: str) -> bool:
    try:
        return float(text) > 0
    except ValueError:
        return False


def same(got: str, want: str, opname: str) -> bool:
    if want == "$nonempty":
        return got.strip() != ""
    if want == "$opname":
        return got.strip() != "" and got.strip() == opname.strip()
    if want == "$gt0":
        return _positive(got)
    if want == "$status":
        return status_known(got)
    if want.startswith("$prefix:"):
        head = want[8:]
        return head != "" and got.startswith(head)
    return got == want


def match_checks(payload: dict, checks: tuple, view: View) -> tuple[str, str]:
    for path, want in checks:
        got = read_path(payload, path, view)
        if not same(got, want, view.opname):
            return "FAIL", path + "=" + got
    return "PASS", "ok"
