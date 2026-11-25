"""把调试调用的路径和 JSON 收成桥能签的字段。口令不进这个字典。"""

from __future__ import annotations

import json
import re

from co.e2e.cases import Operator
from co.e2e.settings import conf

_PATH = re.compile(r"^v1/[A-Za-z0-9/_-]{1,100}\Z")
_SKIP = {"password", "password_enc", "secret", "secret_hex"}


def normalize_call_path(path: str) -> str:
    text = path.strip().strip("/")
    if text.startswith("u8co/"):
        text = text[len("u8co/") :]
    if text in ("health", "v1/health"):
        return "health"
    if ".." in text or _PATH.fullmatch(text) is None:
        return ""
    return text


def merge_call_fields(given: dict, operator: Operator, today: str) -> dict:
    body: dict = {}
    for key, value in given.items():
        if key in _SKIP:
            continue
        body[key] = value
    if not body.get("acc"):
        body["acc"] = operator.acc
    code = conf().account
    if str(body.get("acc") or "") != code:
        raise RuntimeError("只允许测试账套 " + code)
    body["acc"] = code
    if not body.get("year"):
        body["year"] = operator.year
    if not body.get("operator"):
        body["operator"] = operator.code
    if not body.get("date"):
        body["date"] = today
    return body


def parse_call_body(text: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError("请求体不是 JSON") from None
    if not isinstance(data, dict):
        raise RuntimeError("请求体必须是 JSON 对象")
    return data


def encode_call_body(fields: dict, password_enc: str) -> bytes:
    payload = {
        "acc": fields["acc"],
        "year": str(fields["year"]),
        "operator": fields["operator"],
        "password_enc": password_enc,
        "date": fields["date"],
    }
    for key in sorted(fields):
        if key in payload:
            continue
        payload[key] = fields[key]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
