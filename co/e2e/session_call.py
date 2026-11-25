"""调试会话里的桥调用。签名和连接都走 U8CoClient，不经过代理。"""

from __future__ import annotations

import json
from pathlib import Path

from co.e2e.callshape import encode_call_body, merge_call_fields, normalize_call_path, parse_call_body
from co.e2e.cases import Operator, load_operator, public_copy, scrub_text
from co.e2e.flow import local_day, make_remote, utc_stamp
from co.e2e.guard import assert_window
from co.e2e.markers import read_linux_marker
from co.e2e.remote import RemoteError
from co.e2e.runfiles import run_tag
from co.e2e.settings import conf

_BODY_LIMIT = 65536


def load_call_text(body_text: str | None, body_file: str | None) -> dict:
    if body_file:
        path = Path(body_file)
        raw = path.read_text(encoding="utf-8")
        if len(raw.encode("utf-8")) > _BODY_LIMIT:
            raise RuntimeError("请求体超过 64 KiB")
        return parse_call_body(raw)
    if body_text:
        if len(body_text.encode("utf-8")) > _BODY_LIMIT:
            raise RuntimeError("请求体超过 64 KiB")
        return parse_call_body(body_text)
    return {}


def _needles(operator: Operator, secret: str) -> list[str]:
    return [operator.password, secret]


def _post(secret: str, suffix: str, fields: dict, operator: Operator) -> dict:
    from co.client.u8co_client import U8CoClient, U8CoError, derive_keys, encrypt_password

    client = U8CoClient(conf().bridge_url, secret, timeout=90)
    try:
        if suffix == "health":
            return client.health()
        _mac, k_enc = derive_keys(secret)
        body = encode_call_body(fields, encrypt_password(k_enc, operator.password))
        return client._call("POST", "/" + suffix, body, True)
    except U8CoError as exc:
        error = {"ok": False, "status": exc.status, "code": exc.code, "message": exc.message}
        for key in ("field", "hint"):
            value = getattr(exc, key, None)
            if value:
                error[key] = value
        return error


def _save(report: Path, suffix: str, request: dict, response: dict, needles: list[str]) -> None:
    folder = report / "calls"
    folder.mkdir(exist_ok=True)
    safe = suffix.replace("/", "-")
    payload = {"request": public_copy(request, needles), "response": public_copy(response, needles)}
    # 并行的 call 可能在同一秒调同一个路由：文件名再带本次调用的标记。
    path = folder / (utc_stamp().replace(":", "") + "-" + safe + "-" + run_tag() + ".json")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def call_session(path: str, body_text: str | None, body_file: str | None) -> int:
    suffix = normalize_call_path(path)
    if suffix == "":
        print("路径不合法")
        return 1
    local = read_linux_marker()
    if local is None:
        print("没有打开的会话")
        return 1
    report = Path(str(local.get("report_dir") or ""))
    if not report.is_dir():
        print("会话目录不存在")
        return 1
    try:
        assert_window(make_remote(), report)
    except (RuntimeError, RemoteError, OSError) as exc:
        print(str(exc)[:300])
        return 1
    from co.e2e.bridge_job import read_service_secret

    operator = load_operator(Path(conf().operator_file))
    secret = read_service_secret(Path(conf().secret_file))
    fields = merge_call_fields(load_call_text(body_text, body_file), operator, local_day())
    payload = _post(secret, suffix, fields, operator)
    needles = _needles(operator, secret)
    shown = public_copy(payload, needles)
    print(scrub_text(json.dumps(shown, ensure_ascii=False, indent=2), needles))
    request = {"method": "GET"} if suffix == "health" else fields
    _save(report, suffix, request, payload, needles)
    if payload.get("ok") is True:
        return 0
    return 1
