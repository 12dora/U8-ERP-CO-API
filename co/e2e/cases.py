"""不访问网络的判断：年度重试、名单外账套是否进了 COM、审核状态、口令加载。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from co.e2e.settings import conf

_YEAR_HINTS = ("年度", "期间", "会计年", "登录日期")
_HIDDEN = {"password", "secret", "password_enc", "secret_hex"}
_NO_LOGIN = {"unauthorized", "account_not_allowed", "bad_request"}
_OPERATOR = re.compile(r"^[A-Za-z0-9_-]{1,20}\Z")


class Operator:
    def __init__(self, acc: str, year: str, code: str, password: str) -> None:
        self.acc = acc
        self.year = year
        self.code = code
        self.password = password

    def __repr__(self) -> str:
        return f"Operator(acc={self.acc!r}, year={self.year!r}, code={self.code!r})"


def load_operator(path: Path) -> Operator:
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise RuntimeError("凭证文件权限过宽")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise RuntimeError("凭证文件不是 JSON") from None
    if not isinstance(data, dict):
        raise RuntimeError("凭证文件不是对象")
    acc = str(data.get("acc") or "")
    if acc != conf().account:
        raise RuntimeError("凭证账套必须是配置里的测试账套 " + conf().account)
    year = str(data.get("year") or "")
    code = str(data.get("operator") or "")
    password = data.get("password")
    if not re.fullmatch(r"\d{4}", year):
        raise RuntimeError("凭证年度必须是 4 位数字")
    if _OPERATOR.fullmatch(code) is None:
        raise RuntimeError("凭证里的操作员编码含不安全字符")
    if not isinstance(password, str) or password == "":
        raise RuntimeError("凭证缺少口令")
    return Operator(acc, year, code, password)


def should_retry_year(message: str) -> bool:
    text = message or ""
    return any(hint in text for hint in _YEAR_HINTS)


def year_plan(preferred: str, today: str) -> list[str]:
    current = today[:4]
    if preferred == current:
        return [preferred]
    return [preferred, current]


def parse_audit(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def rows_since(rows: list[dict], since: str) -> list[dict]:
    if since == "":
        return list(rows)
    kept = []
    for row in rows:
        if str(row.get("ts") or "") >= since:
            kept.append(row)
    return kept


def blocked_without_com(rows: list[dict]) -> tuple[bool, str]:
    acc = conf().foreign_probe
    hits = [row for row in rows if str(row.get("acc") or "") == acc]
    if not hits:
        return False, "审计里没有 " + acc
    for row in hits:
        if row.get("outcome") != "account_not_allowed":
            return False, acc + " 出现了拒绝以外的结果"
        if row.get("operator") or row.get("year"):
            return False, acc + " 的审计含操作员或年度，可能已进入登录"
    return True, acc + " 只有 account_not_allowed，审计里没有操作员和年度"


def count_logins(rows: list[dict]) -> int:
    total = 0
    for row in rows:
        path = str(row.get("path") or "")
        if not path.startswith("/u8co/v1/") or path.endswith("/health"):
            continue
        if row.get("outcome") in _NO_LOGIN:
            continue
        total += 1
    return total


def verified_ok(state: dict, operator_name: str) -> bool:
    name = str(state.get("cVerifier") or "").strip()
    when = str(state.get("dverifydate") or "").strip()
    expected = (operator_name or "").strip()
    return bool(expected) and name == expected and when != ""


def cleared_ok(state: dict) -> bool:
    name = str(state.get("cVerifier") or "").strip()
    when = str(state.get("dverifydate") or "").strip()
    return name == "" and when == ""


def scrub_text(text: str, needles: list[str]) -> str:
    out = text
    for needle in needles:
        if needle:
            out = out.replace(needle, "***")
    return out


def public_copy(value: object, needles: list[str]) -> object:
    if isinstance(value, dict):
        copied = {}
        for key, item in value.items():
            if key in _HIDDEN:
                continue
            copied[key] = public_copy(item, needles)
        return copied
    if isinstance(value, list):
        return [public_copy(item, needles) for item in value]
    if isinstance(value, str):
        return scrub_text(value, needles)
    return value
