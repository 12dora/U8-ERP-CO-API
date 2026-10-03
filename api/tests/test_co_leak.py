"""API 防越权：经营管理缓存按调用方与口令隔离、多账套报表要经营管理权限、幂等与名额按 sub 区分、
审计终端用户、有效权限路由、桥错误消息的外传形式。不访问网络。"""

from __future__ import annotations

from tests.ic_fakes import IC_TRUST, ic_client
from tests.support import audit_line, base_claims, entry
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_ic_core import _match_body
from tests.test_co_ic_reports import _AGG, _CONSOL, _agg, _consol
from tests.test_co_idem_get import IdemBridge
from tests.test_co_idem_get import _body as _idem_body
from tests.test_co_mgmt import _B_META, _B_PNL, _PNL, MgmtFake, _meta, _mgmt_client, _pnl
from tests.test_co_core_routes import _auth
from u8co_api import co_mgmt_cache
from u8co_api.auth import Caller, caller_key
from u8co_api.co_bridge import to_api_error
from u8co_api.co_idem import caller_tag
from u8co_api.errors import unprocessable

_MATCH = "/v1/co/reports/intercompany_match"
_IDEM = "/v1/co/idempotency/get"
_SNAP = "/v1/co/perm/snapshot"
_EVAL = "/v1/co/perm/evaluate"
_UUID = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"


class _PasswordFake(MgmtFake):
    """按口令应答的假桥。strict 为 false 时模拟「不核对口令」的桥（缓存仍不能跨口令复用）。"""

    def __init__(self, strict: bool = True) -> None:
        super().__init__()
        self.strict = strict

    def call(self, path: str, payload: dict) -> dict:
        if self.strict and payload["password"] != _right_password():
            self.calls.append((path, dict(payload)))
            raise unprocessable("用户名或口令错误", "login_failed")
        return super().call(path, payload)


def _right_password() -> str:
    return _pnl(("801",))["logins"][0]["password"]


def _wrong(accs: tuple[str, ...] = ("801",)) -> dict:
    body = _pnl(accs)
    for login in body["logins"]:
        login["password"] = "wrong-password"
    return body


def _mgmt_claims(**extra) -> dict:
    return base_claims(u8co_mgmt=True, **extra)


# ---- 1. 经营管理缓存：口令不对不出缓存 ----


def test_wrong_password_gets_the_bridge_error_not_cached_data() -> None:
    fake = _PasswordFake()
    client = _mgmt_client(fake)
    first = client.post(_PNL, json=_pnl(("801",)))
    assert first.status_code == 200 and first.json()["cache"]["hit"] is False
    denied = client.post(_PNL, json=_wrong())
    assert denied.status_code == 503
    assert "login_failed" in denied.json()["error"]["message"]
    assert "by_account" not in denied.json()
    assert fake.count(_B_PNL) == 1


def test_cache_key_carries_the_password_even_if_the_bridge_skips_the_check() -> None:
    fake = _PasswordFake(strict=False)
    client = _mgmt_client(fake)
    assert client.post(_PNL, json=_pnl(("801",))).json()["cache"]["hit"] is False
    assert client.post(_PNL, json=_pnl(("801",))).json()["cache"]["hit"] is True
    other = client.post(_PNL, json=_wrong()).json()
    assert other["cache"]["hit"] is False
    assert fake.count(_B_PNL) == 2


def test_cache_is_not_shared_between_two_users_of_one_client() -> None:
    fake = MgmtFake()
    alice = _mgmt_client(fake, claims=_mgmt_claims(sub="user-a"))
    bob = _mgmt_client(fake, claims=_mgmt_claims(sub="user-b"))
    bob.app.state.mgmt_cache = alice.app.state.mgmt_cache = co_mgmt_cache.MgmtCache()
    assert alice.post(_PNL, json=_pnl(("801",))).json()["cache"]["hit"] is False
    assert bob.post(_PNL, json=_pnl(("801",))).json()["cache"]["hit"] is False
    assert alice.post(_PNL, json=_pnl(("801",))).json()["cache"]["hit"] is True
    assert fake.count(_B_PNL) == 2


def test_cached_refuses_keys_without_caller_or_credentials() -> None:
    store = co_mgmt_cache.MgmtCache()
    metas = {"801": _meta()}
    computed: list[int] = []

    def compute() -> dict:
        computed.append(1)
        return {"complete": True}

    parts = {"action": "co:mgmt/pnl", "periods": [1, 2]}
    for _ in range(2):
        assert co_mgmt_cache.cached(parts, metas, compute, store)[1]["hit"] is False
    assert len(store) == 0 and len(computed) == 2


def test_credential_mark_depends_on_every_part() -> None:
    base = co_mgmt_cache.credential_mark("801", "op001", "p1", None, "2026-09-30")
    assert base == co_mgmt_cache.credential_mark("801", "op001", "p1", None, "2026-09-30")
    assert base != co_mgmt_cache.credential_mark("801", "op001", "p2", None, "2026-09-30")
    assert base != co_mgmt_cache.credential_mark("801", "op002", "p1", None, "2026-09-30")
    assert "p1" not in base


# ---- 2. 水位：新桥的 gl_content_checksum；没有能反映金额的键只缓存 60 秒 ----


def test_watermark_accepts_the_content_checksum_and_legacy_totals() -> None:
    closed = _meta(closed=True)
    assert co_mgmt_cache.has_amount_mark(closed)
    assert co_mgmt_cache.ttl_for((1, 2), [closed]) == co_mgmt_cache.TTL_CLOSED
    legacy = dict(closed, watermarks={"gl_max_date": "2026-02-28", "gl_debit": "1.00", "gl_credit": "1.00"})
    assert co_mgmt_cache.ttl_for((1, 2), [legacy]) == co_mgmt_cache.TTL_CLOSED
    bare = dict(closed, watermarks={"gl_max_date": "2026-02-28", "gl_lines": 3})
    assert not co_mgmt_cache.has_amount_mark(bare)
    assert co_mgmt_cache.ttl_for((1, 2), [closed, bare]) == co_mgmt_cache.TTL_OPEN


def test_mgmt_meta_never_forwards_legacy_money_totals() -> None:
    fake = MgmtFake()
    fake.meta["801"] = dict(_meta(), watermarks={"gl_max_date": "x", "gl_debit": "99.00", "gl_credit": "98.00"})
    reply = _mgmt_client(fake).post("/v1/co/mgmt/meta", json={"logins": _pnl(("801",))["logins"], "fiscal_year": 2026})
    assert reply.status_code == 200, reply.text
    marks = reply.json()["by_account"]["801"]["watermarks"]
    assert marks == {"gl_max_date": "x"}
    assert fake.count(_B_META) == 1


# ---- 3. 多账套报表要经营管理权限 ----


def test_fanout_reports_need_the_mgmt_claim() -> None:
    fake = FakeBridge()
    client = ic_client(fake, claims=base_claims(u8co_read=True, u8co_write=True))
    for path, body in ((_MATCH, _match_body()), (_AGG, _agg("stock_current", ("801", "802"))), (_CONSOL, _consol(("801", "803")))):
        denied = client.post(path, json=body)
        assert denied.status_code == 403, path
        assert denied.json()["error"]["code"] == "mgmt_forbidden"
    assert fake.calls == []


def test_fanout_reports_are_mgmt_in_openapi() -> None:
    doc = ic_client(FakeBridge(), trust=(IC_TRUST,)).get("/v1/openapi.json").json()
    for path in (_MATCH, _AGG, _CONSOL):
        assert doc["paths"][path]["post"]["x-u8co-access"] == "mgmt"
    assert doc["paths"][_SNAP]["post"]["x-u8co-access"] == "read"
    assert doc["paths"][_EVAL]["post"]["x-u8co-access"] == "perm_evaluate"


# ---- 4. 幂等与名额按（信任项、客户端、sub）区分 ----


def test_two_subs_of_one_client_get_two_idempotency_callers() -> None:
    fake = IdemBridge()
    for sub in ("user-a", "user-b"):
        ok = _client(fake, claims=base_claims(u8co_write=True, sub=sub)).post(_IDEM, json=_idem_body())
        assert ok.status_code == 200, ok.text
    first, second = fake.calls[0][1]["caller"], fake.calls[1][1]["caller"]
    assert first == "tool:tool-a:user-a" and second == "tool:tool-a:user-b"


def test_caller_key_without_sub_is_unchanged() -> None:
    assert caller_key(Caller("tool-a", "tool")) == "tool:tool-a"
    assert caller_key(Caller("tool-a", "tool", subject="tool-a")) == "tool:tool-a"
    assert caller_tag(Caller("tool-a", "tool", subject="u1")) == "tool:tool-a:u1"


def test_rate_slots_are_per_sub() -> None:
    fake = FakeBridge()
    client = _client(fake, claims=base_claims(u8co_write=True, sub="user-a"))
    assert client.post("/v1/co/login-check", json=_auth()).status_code == 200
    assert "tool:tool-a:user-a" in client.app.state.co_limit._hits


# ---- 5. 审计的终端用户 ----


def test_audit_user_is_the_token_sub_and_the_header_is_ignored(capsys) -> None:
    client = _client(claims=base_claims(u8co_write=True, sub="user-a"))
    ok = client.post("/v1/co/login-check", json=_auth(), headers={"X-U8co-User": _UUID})
    assert ok.status_code == 200
    row = audit_line(capsys.readouterr().out, "/v1/co/login-check")
    assert row["user"] == "user-a" and row["sub"] == "user-a"


def test_on_behalf_rows_record_the_header_and_the_sub(capsys) -> None:
    client = _client(claims=base_claims(u8co_write=True, sub="svc-1"), trust=(entry(on_behalf_header=True),))
    ok = client.post("/v1/co/login-check", json=_auth(), headers={"X-U8co-User": _UUID})
    assert ok.status_code == 200
    row = audit_line(capsys.readouterr().out, "/v1/co/login-check")
    assert row["user"] == _UUID and row["sub"] == "svc-1"


def test_header_alone_is_ignored_without_the_flag(capsys) -> None:
    ok = _client().post("/v1/co/login-check", json=_auth(), headers={"X-U8co-User": _UUID})
    assert ok.status_code == 200
    assert audit_line(capsys.readouterr().out, "/v1/co/login-check")["user"] is None


# ---- 6. 有效权限路由 ----


def test_perm_snapshot_is_a_plain_read() -> None:
    fake = FakeBridge()
    ok = _client(fake, claims=base_claims(u8co_read=True)).post(_SNAP, json=_auth())
    assert ok.status_code == 200, ok.text
    path, payload = fake.calls[0]
    assert path == "/v1/perm/snapshot" and "caller" not in payload
    assert payload["operator"] == "op001"


def test_perm_evaluate_needs_the_trust_flag() -> None:
    fake = FakeBridge()
    body = _auth(subject="op002")
    denied = _client(fake, claims=base_claims(u8co_write=True)).post(_EVAL, json=body)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "forbidden"
    assert fake.calls == []
    flagged = (entry(perm_evaluate=True),)
    bare = _client(fake, claims=base_claims(), trust=flagged).post(_EVAL, json=body)
    assert bare.status_code == 403 and fake.calls == []
    ok = _client(fake, claims=base_claims(u8co_read=True), trust=flagged).post(_EVAL, json=body)
    assert ok.status_code == 200, ok.text
    path, payload = fake.calls[0]
    assert path == "/v1/perm/evaluate" and payload["subject"] == "op002" and "caller" not in payload


def test_perm_evaluate_rejects_a_bad_subject() -> None:
    fake = FakeBridge()
    client = _client(fake, claims=base_claims(u8co_read=True), trust=(entry(perm_evaluate=True),))
    assert client.post(_EVAL, json=_auth(subject="a;b")).status_code in (400, 422)
    assert fake.calls == []


def test_perm_evaluate_is_open_on_read_only_accounts() -> None:
    fake = FakeBridge()
    client = _client(fake, claims=base_claims(u8co_read=True), trust=(entry(perm_evaluate=True),), read_only_accounts=("803",))
    assert client.post(_EVAL, json=_auth(subject="op002")).status_code == 200


# ---- 7. 桥错误消息的外传形式 ----


def test_long_bridge_messages_are_clipped_to_300() -> None:
    error = to_api_error(409, {"code": "u8_rejected", "message": "甲" * 1000})
    assert len(error.message) == 300 and error.message.endswith("…")


def test_denials_fold_code_lists_and_drop_list_details() -> None:
    codes = "、".join(f"C{n:04d}" for n in range(30))
    body = {"code": "no_permission", "message": f"没有往来单位 {codes} 的数据权限", "detail": {"codes": ["C0001"], "type": "x"}}
    error = to_api_error(403, body)
    assert "C0001" not in error.message and "等 30 项" in error.message
    assert error.detail == {"type": "x"}


def test_non_denials_keep_short_lists() -> None:
    message = "可用类型：a、b、c、d、e、f"
    assert to_api_error(400, {"code": "bad_request", "message": message}).message == message
