"""采购结算单写入：参照采购发票自动结算（type=purchase_settle，source_type=purchase_invoice）和删除。"""

from __future__ import annotations

from typing import get_args

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth
from u8co_api.co_models import CreateType, DeleteType, VerifyType
from u8co_api.co_models_edit import GenerateType, UpdateType

_GEN = "/v1/co/vouchers/generate"
_KIND = "purchase_settle"


def _gen(**extra) -> dict:
    body = {"type": _KIND, "id": 1000000001, "source_type": "purchase_invoice", "head": {"settle_date": "2026-09-30"}}
    body.update(extra)
    return _auth(**body)


def test_only_generate_create_and_delete_are_open() -> None:
    # create 是手工结算（test_co_pu_settle_man）。
    assert _KIND in get_args(GenerateType)
    assert _KIND in get_args(DeleteType)
    assert _KIND in get_args(CreateType)
    for closed in (UpdateType, VerifyType):
        assert _KIND not in get_args(closed)


def test_generate_reaches_the_bridge_unchanged() -> None:
    fake = FakeBridge()
    done = _client(fake).post(_GEN, json=_gen())
    assert done.status_code == 200, done.text
    path, sent = fake.calls[-1]
    assert path == "/v1/vouchers/generate"
    assert (sent["type"], sent["id"], sent["source_type"], sent["head"]) == (
        _KIND, 1000000001, "purchase_invoice", {"settle_date": "2026-09-30"}
    )
    assert "lines" not in sent


def test_settle_date_year_is_not_tied_to_the_account_year() -> None:
    # year 是账套库年度，结算日不与它比较。
    fake = FakeBridge()
    done = _client(fake).post(_GEN, json=_gen(head={"settle_date": "2026-09-30"}, year="2025"))
    assert done.status_code == 200, done.text
    assert fake.calls[-1][1]["year"] == "2025"


def test_head_and_source_type_may_be_omitted() -> None:
    fake = FakeBridge()
    body = _gen()
    body.pop("head")
    body.pop("source_type")
    done = _client(fake).post(_GEN, json=body)
    assert done.status_code == 200, done.text


_REJECTS = (
    _gen(lines=[{"source_line_id": 7, "quantity": 1}]),
    _gen(lines=[]),
    _gen(head={"cMemo": "x"}),
    _gen(head={"settle_date": "2026/09/30"}),
    _gen(head={"settle_date": "20260930"}),
    _gen(head={"settle_date": "2026-02-30"}),
    _gen(head={"settle_date": 20260930}),
    _gen(head={"settle_date": "2026-09-30", "SETTLE_DATE": "2026-09-29"}),
    _gen(source_type="purchase_in"),
    _gen(head={"settle_date": "2099-01-31"}, year="2099"),
)


@pytest.mark.parametrize("body", _REJECTS)
def test_generate_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_GEN, json=body)
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_delete_reaches_the_bridge() -> None:
    fake = FakeBridge()
    done = _client(fake).post("/v1/co/vouchers/delete", json=_auth(type=_KIND, id=2773))
    assert done.status_code == 200, done.text
    assert fake.calls[-1][0] == "/v1/vouchers/delete"
    assert (fake.calls[-1][1]["type"], fake.calls[-1][1]["id"]) == (_KIND, 2773)


def test_verify_is_refused_before_the_bridge() -> None:
    fake = FakeBridge()
    denied = _client(fake).post("/v1/co/vouchers/verify", json=_auth(type=_KIND, id=2773, action="verify"))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []
