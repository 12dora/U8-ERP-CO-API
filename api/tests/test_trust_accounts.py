"""信任项的静态账套上限（accounts）：可用账套 = U8CO_ACCOUNTS ∩ 令牌账套声明 ∩ accounts。
不写 accounts 不加限制；单账套路由、经营管理、公司间多账套路由都按同一结果判定。假桥，不访问网络。
"""

from __future__ import annotations

import json

import pytest
from tests.ic_fakes import ic_client
from tests.support import base_claims, entry
from tests.test_co_gate import FakeBridge, _client, _login
from tests.test_co_ic_match import _MATCH, _goods
from tests.test_co_ic_match import _body as _match_body
from tests.test_co_mgmt import _PNL, MgmtFake, _mgmt_client, _pnl
from u8co_api.auth import caller_from
from u8co_api.trust import load_trust_file, parse_entry

_ROW = {
    "name": "app-a",
    "issuer": "https://idp.example.com",
    "audience": "u8co-api",
    "jwks_url": "https://idp.example.com/oauth/jwks",
}
_CHECK = "/v1/co/login-check"


def _code(response) -> str:
    return response.json()["error"]["code"]


# ---- 解析 ----


def test_missing_key_means_no_cap() -> None:
    assert parse_entry(dict(_ROW)).accounts is None


def test_accounts_are_parsed_in_order(tmp_path) -> None:
    path = tmp_path / "trust.json"
    path.write_text(json.dumps([dict(_ROW, accounts=["803", "801"])]), encoding="utf-8")
    assert load_trust_file(str(path))[0].accounts == ("803", "801")


@pytest.mark.parametrize("accounts", ([], "803", ["9a1"], ["0803"], [803], ["803", "803"], [None]))
def test_bad_accounts_stop_the_process(accounts: object) -> None:
    with pytest.raises(SystemExit, match="accounts"):
        parse_entry(dict(_ROW, accounts=accounts))


# ---- 令牌 → 调用方 ----


def test_cap_without_an_accounts_claim() -> None:
    caller = caller_from(base_claims(), entry(accounts=("803",)))
    assert caller.accounts == frozenset({"803"})


def test_cap_intersects_the_claim() -> None:
    spec = entry(accounts_claim="accs", accounts=("803", "801"))
    assert caller_from(base_claims(accs=["803", "802"]), spec).accounts == frozenset({"803"})
    assert caller_from(base_claims(accs="801 802"), spec).accounts == frozenset({"801"})
    # 没带声明仍是空集：一个账套都不能用。
    assert caller_from(base_claims(), spec).accounts == frozenset()


def test_without_cap_the_claim_rules_as_before() -> None:
    assert caller_from(base_claims(accs=["803", "802"]), entry(accounts_claim="accs")).accounts == frozenset({"803", "802"})
    assert caller_from(base_claims(), entry()).accounts is None


# ---- 单账套路由 ----


def test_single_account_routes_respect_the_cap() -> None:
    capped = entry(accounts_claim="accs", accounts=("803",))
    claims = base_claims(u8co_write=True, accs=["803", "902"])
    fake = FakeBridge()
    client = _client(fake, claims=claims, co_accounts=("803", "902"), trust=(capped,))
    assert client.post(_CHECK, json=_login()).status_code == 200
    denied = client.post(_CHECK, json=_login(acc="902"))
    assert denied.status_code == 403 and _code(denied) == "account_not_allowed"
    assert len(fake.calls) == 1


def test_cap_never_widens_the_account_list() -> None:
    capped = entry(accounts=("803", "902"))
    fake = FakeBridge()
    client = _client(fake, claims=base_claims(u8co_write=True), co_accounts=("803",), trust=(capped,))
    assert _code(client.post(_CHECK, json=_login(acc="902"))) == "account_not_allowed"
    assert fake.calls == []


def test_no_cap_keeps_every_listed_account() -> None:
    fake = FakeBridge()
    client = _client(fake, co_accounts=("803", "902"))
    assert client.post(_CHECK, json=_login(acc="902")).status_code == 200


# ---- 经营管理、公司间 ----


def test_mgmt_respects_the_cap() -> None:
    capped = entry(mgmt_claim="u8co_mgmt", accounts=("801",))
    fake = MgmtFake()
    client = _mgmt_client(fake, trust=(capped,))
    denied = client.post(_PNL, json=_pnl(("801", "802")))
    assert denied.status_code == 403 and _code(denied) == "account_not_allowed"
    assert fake.calls == []
    assert client.post(_PNL, json=_pnl(("801",))).status_code == 200


def test_intercompany_fanout_respects_the_cap() -> None:
    fake = _goods()
    # 公司间对账要经营管理权限（ic_fakes.ic_client 的令牌带 u8co_mgmt）。
    client = ic_client(fake, trust=(entry(accounts=("801",), mgmt_claim="u8co_mgmt"),))
    denied = client.post(_MATCH, json=_match_body())
    assert denied.status_code == 403 and _code(denied) == "account_not_allowed"
    assert fake.calls == []
    wide = ic_client(_goods(), trust=(entry(accounts=("801", "802"), mgmt_claim="u8co_mgmt"),))
    assert wide.post(_MATCH, json=_match_body()).status_code == 200
