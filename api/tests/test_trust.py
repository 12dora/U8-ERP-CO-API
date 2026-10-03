"""Trust file parsing: strict keys, https issuers, supported algorithms, unique issuer/audience pairs."""

from __future__ import annotations

import json

import pytest
from u8co_api.trust import load_trust, load_trust_file, parse_entry

_ROW = {
    "name": "app-a",
    "issuer": "https://idp.example.com",
    "audience": "u8co-api",
    "jwks_url": "https://idp.example.com/oauth/jwks",
}


def _write(tmp_path, rows) -> str:
    path = tmp_path / "trust.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return str(path)


def test_defaults_are_neutral_claims_and_rs256() -> None:
    item = parse_entry(dict(_ROW))
    assert item.write_claim == "u8co_write"
    assert item.read_claim == "u8co_read"
    assert item.algorithms == ("RS256",)
    assert item.accounts_claim == ""
    assert item.key_source.jwks_url == _ROW["jwks_url"]


def test_legacy_caller_key_and_compat_claims(tmp_path) -> None:
    row = {key: value for key, value in _ROW.items() if key != "name"}
    row.update({"caller": "legacy", "write_claim": "app_write", "read_claim": "app_read"})
    loaded = load_trust_file(_write(tmp_path, [row]))
    assert loaded[0].name == "legacy"
    assert loaded[0].write_claim == "app_write"


def test_missing_jwks_url_means_discovery() -> None:
    row = {key: value for key, value in _ROW.items() if key != "jwks_url"}
    item = parse_entry(row)
    assert item.key_source.jwks_url == ""
    assert item.key_source.issuer == _ROW["issuer"]


def test_duplicate_issuer_and_audience_is_rejected(tmp_path) -> None:
    path = _write(tmp_path, [_ROW, dict(_ROW, name="again")])
    with pytest.raises(SystemExit):
        load_trust({"U8CO_TRUST_FILE": path})
    other = _write(tmp_path, [_ROW, dict(_ROW, name="other", audience="u8co-api-2")])
    assert len(load_trust({"U8CO_TRUST_FILE": other})) == 2


@pytest.mark.parametrize(
    "change",
    (
        {"name": "bad name"},
        {"issuer": "http://idp.example.com/"},
        {"audience": ""},
        {"jwks_url": "file:///keys.json"},
        {"jwks_url": "http://idp.example.com/oauth/jwks"},
        {"jwks_url": "http://idp.example.com/certs", "allow_insecure_http": "true"},
        {"algorithms": ["HS256"]},
        {"algorithms": []},
        {"write_claim": "same", "read_claim": "same"},
        {"write_claim": "has space"},
        {"write_scope": "u8co.write", "read_scope": "u8co.write"},
        {"accounts_claim": 3},
        {"unknown_key": True},
    ),
)
def test_bad_entries_stop_the_process(change: dict) -> None:
    with pytest.raises(SystemExit):
        parse_entry(dict(_ROW, **change))


def test_file_must_be_an_array(tmp_path) -> None:
    path = tmp_path / "trust.json"
    path.write_text('{"name": "app-a"}', encoding="utf-8")
    with pytest.raises(SystemExit):
        load_trust_file(str(path))
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        load_trust_file(str(path))


def test_http_jwks_only_for_loopback_or_explicit_opt_in() -> None:
    for url in ("http://127.0.0.1:8080/certs", "http://[::1]/certs", "http://LOCALHOST/certs"):
        assert parse_entry(dict(_ROW, jwks_url=url)).jwks_url == url
    plain = "http://idp.example.com/oauth/jwks"
    item = parse_entry(dict(_ROW, jwks_url=plain, allow_insecure_http=True))
    assert item.allow_insecure_http is True
    assert item.key_source.allow_insecure_http is True
    assert parse_entry(dict(_ROW)).allow_insecure_http is False


def test_env_entry_can_opt_in_to_http_jwks() -> None:
    env = {
        "U8CO_TRUST_FILE": "",
        "U8CO_OIDC_ISSUER": _ROW["issuer"],
        "U8CO_OIDC_AUDIENCE": _ROW["audience"],
        "U8CO_OIDC_JWKS_URL": "http://idp.example.com/certs",
    }
    with pytest.raises(SystemExit):
        load_trust(env)
    loaded = load_trust(dict(env, U8CO_OIDC_ALLOW_INSECURE_HTTP="true"))
    assert loaded[0].allow_insecure_http is True
