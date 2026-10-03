"""只读账套（U8CO_READONLY_ACCOUNTS）：写路由、预演、带幂等键的重试、公司间生单一律 403 account_read_only，
不访问桥、先于写入策略；读路由、经营管理、公司间对账照常。假桥，不访问网络。
"""

from __future__ import annotations

import json

import pytest
from tests.ic_fakes import ic_client
from tests.test_co_access import _READS, _WRITES
from tests.test_co_dryrun import _DRY_WRITES
from tests.test_co_gate import FakeBridge, _client, _login
from tests.test_co_ic_generate import _GEN, _KEY, _orders
from tests.test_co_ic_generate import _body as _gen_body
from tests.test_co_ic_match import _MATCH, _goods
from tests.test_co_ic_match import _body as _match_body
from tests.test_co_mgmt import _PNL, MgmtFake, _mgmt_client, _pnl
from u8co_api.config import load_settings, parse_read_only
from u8co_api.write_policy import WritePolicy

_RO = ("803",)
_CREATE = "/v1/co/vouchers/create"
_CREATE_BODY = _login(type="sale_order", head={"a": "b"}, lines=[{"a": 1}])


def _refused(response) -> None:
    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["code"] == "account_read_only"
    assert error["message"] == "该账套只开放读取"
    assert "Retry-After" not in response.headers


def _audit(out: str, endpoint: str) -> dict:
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    found = [line for line in lines if line.get("endpoint") == endpoint]
    assert found, "missing audit line"
    return found[-1]


# ---- 配置 ----


def test_read_only_list_is_parsed_like_the_account_list() -> None:
    assert parse_read_only("", ("803",)) == ()
    assert parse_read_only(" 801, 802,801 ", ("803", "801", "802")) == ("801", "802")


@pytest.mark.parametrize("raw", ("05", "9a1", "803,abc"))
def test_bad_read_only_entries_stop_the_process(raw: str) -> None:
    with pytest.raises(SystemExit, match="U8CO_READONLY_ACCOUNTS"):
        parse_read_only(raw, ("803",))


def test_read_only_account_must_be_in_the_account_list() -> None:
    with pytest.raises(SystemExit, match="不在 U8CO_ACCOUNTS"):
        parse_read_only("803,801", ("803",))
    with pytest.raises(SystemExit):
        parse_read_only("803", ())


def test_load_settings_reads_the_env(monkeypatch) -> None:
    monkeypatch.setenv("U8CO_TRUST_FILE", "")
    monkeypatch.setenv("U8CO_ACCOUNTS", "803,801")
    monkeypatch.setenv("U8CO_READONLY_ACCOUNTS", "801")
    assert load_settings().read_only_accounts == ("801",)
    monkeypatch.setenv("U8CO_READONLY_ACCOUNTS", "802")
    with pytest.raises(SystemExit):
        load_settings()


# ---- 写路由 ----


@pytest.mark.parametrize(("path", "body"), _WRITES)
def test_every_write_route_is_refused_before_the_bridge(path: str, body: dict) -> None:
    fake = FakeBridge()
    _refused(_client(fake, read_only_accounts=_RO).post(path, json=body))
    assert fake.calls == []


@pytest.mark.parametrize(("path", "body"), _DRY_WRITES)
def test_dry_run_is_refused_too(path: str, body: dict) -> None:
    fake = FakeBridge()
    _refused(_client(fake, read_only_accounts=_RO).post(path, json={**body, "dry_run": True}))
    assert fake.calls == []


@pytest.mark.parametrize(("path", "body"), _WRITES)
def test_idempotent_retries_are_refused_not_replayed(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake, read_only_accounts=_RO)
    for _ in range(2):
        _refused(client.post(path, json=body, headers={"Idempotency-Key": "ro-0001"}))
    assert fake.calls == []


def test_other_accounts_still_write() -> None:
    fake = FakeBridge()
    client = _client(fake, co_accounts=("803", "902"), read_only_accounts=("902",))
    assert client.post(_CREATE, json=_CREATE_BODY).status_code == 200
    _refused(client.post(_CREATE, json={**_CREATE_BODY, "acc": "902"}))
    assert len(fake.calls) == 1


def test_account_list_is_checked_first() -> None:
    # 不在 U8CO_ACCOUNTS 的账套仍是 account_not_allowed（只读名单只能是它的子集）。
    denied = _client(FakeBridge(), read_only_accounts=_RO).post(_CREATE, json={**_CREATE_BODY, "acc": "902"})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "account_not_allowed"


def test_refusal_comes_before_the_write_policy(tmp_path, capsys) -> None:
    # 策略文件不存在时写入本应 503 write_policy_unavailable；只读账套先 403。
    fake = FakeBridge()
    client = _client(fake, read_only_accounts=_RO, write_policy_file=str(tmp_path / "absent.json"))
    client.app.state.write_policy = WritePolicy(str(tmp_path / "absent.json"))
    _refused(client.post(_CREATE, json=_CREATE_BODY))
    assert fake.calls == []
    line = _audit(capsys.readouterr().out, _CREATE)
    assert line["outcome"] == "account_read_only"
    assert (line["type"], line["op"]) == ("sale_order", "create")


# ---- 读路由、健康检查 ----


@pytest.mark.parametrize(("path", "body"), _READS)
def test_reads_pass(path: str, body: dict) -> None:
    fake = FakeBridge()
    ok = _client(fake, read_only_accounts=_RO).post(path, json=body)
    assert ok.status_code == 200, ok.text
    assert len(fake.calls) == 1


class _RoBridge(FakeBridge):
    def health(self) -> dict:
        return {"ok": True, "version": "test", "read_only_accounts": ["803", "bad", 5]}


def test_health_lists_both_read_only_lists() -> None:
    data = _client(_RoBridge(), read_only_accounts=_RO).get("/v1/co/health").json()
    assert data["api_read_only_accounts"] == ["803"]
    assert data["read_only_accounts"] == ["803"]


def test_health_without_read_only_keeps_its_shape() -> None:
    assert _client(FakeBridge()).get("/v1/co/health").json() == {"ok": True, "version": "test"}


# ---- 公司间、经营管理 ----


def test_generate_buyer_is_refused_for_a_read_only_buyer() -> None:
    fake = _orders()
    client = ic_client(fake, read_only_accounts=("801",))
    _refused(client.post(_GEN, json=_gen_body()))
    _refused(client.post(_GEN, json=_gen_body(dry_run=False), headers=_KEY))
    assert fake.calls == []


def test_generate_buyer_replay_is_refused_before_the_lookup() -> None:
    fake = _orders()
    fake.idem = {"found": True, "state": "done", "status": 200, "response": {"ok": True, "type": "purchase_in"}}
    _refused(ic_client(fake, read_only_accounts=("801",)).post(_GEN, json=_gen_body(dry_run=False), headers=_KEY))
    assert fake.calls == []


def test_generate_buyer_with_a_read_only_seller_still_runs() -> None:
    # 卖方账套只读取，不受只读名单限制。
    fake = _orders()
    ok = ic_client(fake, read_only_accounts=("802",)).post(_GEN, json=_gen_body())
    assert ok.status_code == 200, ok.text


def test_intercompany_match_reads_pass() -> None:
    ok = ic_client(_goods(), read_only_accounts=("801", "802")).post(_MATCH, json=_match_body())
    assert ok.status_code == 200, ok.text


def test_mgmt_reports_pass() -> None:
    fake = MgmtFake()
    ok = _mgmt_client(fake, read_only_accounts=("801", "802")).post(_PNL, json=_pnl(("801", "802")))
    assert ok.status_code == 200, ok.text

