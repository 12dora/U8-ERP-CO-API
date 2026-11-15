"""固定资产写入：卡片新增 / 撤销本期新增（archives，fa_card）、设备台账（archives，equipment）在 API 层的
格式检查、与桥同文的拒绝、转发和响应透传。变动单不在 API 里（vouchers/create 不收 fa_change）。"""

from __future__ import annotations

from typing import get_args

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth
from tests.test_co_update_close_gen import _enum, _spec
from u8co_api.co_fa_write import EQ_NO_DELETE, EQ_NO_UPDATE, FA_CARD_NO_UPDATE
from u8co_api.co_models import CreateType
from u8co_api.co_models_arc_names import DeleteArchiveName, ReadArchiveName, UpdateArchiveName, WriteArchiveName
from u8co_api.write_class import classify

_ARC = "/v1/co/archives/"
_CARD = {
    "name": "空压机",
    "type_code": "20",
    "original_value": 12000,
    "start_date": "2026-08-15",
    "origin_code": "01",
    "status_code": "1001",
    "depreciation_method_code": "1",
    "dept_code": "D901",
}


class _FaBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if path == "/v1/archives/create" and payload["archive"] == "fa_card":
            return {"ok": True, "archive": "fa_card", "code": "FA-9002", "asset_num": payload["code"], "card_id": 1000000002}
        return {"ok": True, "archive": payload["archive"], "code": payload["code"], "deleted": path.endswith("delete")}


def _card(**extra) -> dict:
    fields = dict(_CARD)
    fields.update(extra)
    return _auth(archive="fa_card", code="ZC-001", fields=fields)


def test_names_open_only_the_new_operations() -> None:
    assert "fa_card" in get_args(WriteArchiveName) and "fa_card" in get_args(DeleteArchiveName)
    assert "equipment" in get_args(WriteArchiveName) and "equipment" in get_args(ReadArchiveName)
    assert "equipment" not in get_args(DeleteArchiveName)
    for archive in ("fa_card", "equipment"):
        assert archive not in get_args(UpdateArchiveName)
    assert "fa_change" not in get_args(CreateType)  # 变动单在 U8 客户端录入
    spec = _spec()
    assert "equipment" in _enum(spec, "ArcListIn", "archive")
    assert "lines" in spec["components"]["schemas"]["CoCreateIn"]["required"]


def test_card_create_forwards_and_passes_asset_num_through() -> None:
    fake = _FaBridge()
    done = _client(fake).post(_ARC + "create", json=_card(useful_life_months="60", net_salvage_rate=0.05))
    assert done.status_code == 200, done.text
    assert done.json() == {"ok": True, "archive": "fa_card", "code": "FA-9002", "asset_num": "ZC-001", "card_id": 1000000002}
    path, sent = fake.calls[-1]
    assert (path, sent["code"], sent["fields"]["original_value"]) == ("/v1/archives/create", "ZC-001", 12000)
    assert "template" not in sent


def test_card_delete_and_equipment_create_forward() -> None:
    fake = _FaBridge()
    client = _client(fake)
    deleted = client.post(_ARC + "delete", json=_auth(archive="fa_card", code="FA-9002"))
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True
    body = _auth(archive="equipment", code="EQ-0001", fields={"name": "数控车床", "cdepcode": "D901", "cdefine3": "A"})
    created = client.post(_ARC + "create", json=body)
    assert created.status_code == 200, created.text
    assert fake.calls[-1][1]["fields"] == {"name": "数控车床", "cdepcode": "D901", "cdefine3": "A"}


def test_equipment_list_supports_changed_since() -> None:
    fake = FakeBridge()
    listed = _client(fake).post(_ARC + "list", json=_auth(archive="equipment", changed_since="24877043", limit=5))
    assert listed.status_code == 200, listed.text
    assert fake.calls[-1][1]["changed_since"] == "24877043"


_CARD_REJECTS = (
    dict(_card(), template="ZC-000"),
    _card(sAssetName="x"),
    _card(name=None),
    _card(original_value=0),
    _card(original_value="1e13"),
    _card(original_value=True),
    _card(start_date="2026/08/15"),
    _card(start_date="2026-02-30"),
    _card(accumulated_depreciation=12000.01),
    _card(net_salvage=-1),
    _card(net_salvage_rate=1),
    _card(useful_life_months=11989),
    _card(used_months=1.5),
    _card(name="名" * 51),
    _card(dept_code="D" * 13),
    _auth(archive="fa_card", code="Z" * 21, fields=_CARD),
    _auth(archive="equipment", code="EQ-0001", fields={"cdepcode": "D901"}),
    _auth(archive="equipment", code="EQ-0001", fields={"name": "车床", "cmaker": "张三"}),
    _auth(archive="equipment", code="E" * 31, fields={"name": "车床"}),
)


@pytest.mark.parametrize("body", _CARD_REJECTS)
def test_archive_create_rejects_before_the_bridge(body: dict) -> None:
    fake = _FaBridge()
    denied = _client(fake).post(_ARC + "create", json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


_REFUSALS = (
    ("update", _auth(archive="fa_card", code="FA-9002", fields={"name": "x"}), FA_CARD_NO_UPDATE),
    ("update", _auth(archive="equipment", code="EQ-0001", fields={"name": "x"}), EQ_NO_UPDATE),
    ("delete", _auth(archive="equipment", code="EQ-0001"), EQ_NO_DELETE),
)


@pytest.mark.parametrize(("op", "body", "text"), _REFUSALS)
def test_unsupported_archive_ops_use_the_bridge_text(op: str, body: dict, text: str) -> None:
    fake = _FaBridge()
    denied = _client(fake).post(_ARC + op, json=body)
    assert denied.status_code == 400, denied.text
    error = denied.json()["error"]
    assert (error["message"], error["field"]) == (text, "archive")
    assert fake.calls == []


def test_write_policy_classifies_fa_card_by_its_own_type() -> None:
    body = {"acc": "803", "operator": "op001", "archive": "fa_card"}
    assert (classify("co:archives/create", body).type, classify("co:archives/delete", body).type) == ("fa_card", "fa_card")
    other = classify("co:archives/create", dict(body, archive="equipment"))
    assert (other.type, other.op) == ("archives", "create")
