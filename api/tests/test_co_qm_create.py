"""来料 / 产品报检单（QM01 / QM02）和来料 / 产品检验单（QM03 / QM04）的参照生单、删除与报检单审核。"""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards

_GEN = "/v1/co/vouchers/generate"
_INSPECT_LINES = [{"source_line_id": 11, "quantity": 2}, {"source_line_id": 12, "quantity": 0.5, "cWhCode": "01"}]
_INSPECT_HEAD = {"dDate": "2026-09-29", "cDepCode": "D01", "cInspectDepCode": "D02", "cDefine1": "x", "cdefine16": 3}
_CHECK_LINE = [{"source_line_id": 21, "quantity": 3, "fRegQuantity": 2, "fConQuantiy": 0.5, "fDisQuantity": 0.5}]
_ITEMS = [
    {"cChkItemCode": "I01", "cChkGuideCode": "G01", "cCheckValue": "10.5", "cTargetQJug": "合格"},
    {"cchkitemcode": "I02", "cchkguidecode": "G02", "ctargetqjug": "不合格"},
]
_CHECK_HEAD = {
    "cCheckPersonCode": "P01",
    "cDepCode": "D01",
    "project_code": "0000000001",
    "cChkConclusion": "合格",
    "fDtQuantity": 1,
    "dDate": "2026-09-29",
    "cDefine3": "a",
    "chDefine11": "001",
    "chdefine16": "z",
}


def _gen(kind: str, head: dict | None = None, lines: list | None = None, **extra) -> dict:
    check = kind.endswith("_check")
    body = {"type": kind, "id": 7, "lines": (_CHECK_LINE if check else _INSPECT_LINES) if lines is None else lines}
    if head is not None:
        body["head"] = head
    body.update(extra)
    return _auth(**body)


def _chk(head: dict | None = None, lines: list | None = None, kind: str = "qm_incoming_check", **extra) -> dict:
    return _gen(kind, _CHECK_HEAD if head is None else head, lines, **extra)


def _line(**extra) -> list[dict]:
    row = {"source_line_id": 21, "quantity": 3}
    row.update(extra)
    return [row]


_FORWARD = (
    (_GEN, "/v1/vouchers/generate", _gen("qm_incoming_inspect"), _sealed("type", "id", "lines")),
    (
        _GEN,
        "/v1/vouchers/generate",
        _gen("qm_product_inspect", _INSPECT_HEAD, source_type="production_order"),
        _sealed("type", "id", "head", "lines", "source_type"),
    ),
    (_GEN, "/v1/vouchers/generate", _chk(dict(_CHECK_HEAD, items=_ITEMS)), _sealed("type", "id", "head", "lines")),
    ("/v1/co/vouchers/delete", "/v1/vouchers/delete", _auth(type="qm_incoming_inspect", id=7), _sealed("type", "id")),
    ("/v1/co/vouchers/delete", "/v1/vouchers/delete", _auth(type="qm_product_check", id=7), _sealed("type", "id")),
)

_ACCEPTS = (
    _gen("qm_incoming_inspect"),
    _gen("qm_incoming_inspect", {}, source_type="arrival"),
    _gen("qm_incoming_inspect", _INSPECT_HEAD),
    _gen("qm_product_inspect", {"cdepcode": "D01"}),
    _gen("qm_incoming_inspect", lines=[{"source_line_id": i, "quantity": 1} for i in range(1, 201)]),
    _chk(),
    _chk({"cCheckPersonCode": "P01"}, _line()),
    _chk({"ccheckpersoncode": "P01"}, _line(fDisQuantity=3)),
    _chk({"cCheckPersonCode": "P01"}, _line(fConQuantiy=1, fDisQuantity=0.5)),
    _chk({"cCheckPersonCode": "P01", "cYielderCode": "P02", "dYieldDate": "2026-09-30"}, _line(fConQuantiy=1)),
    _chk(dict(_CHECK_HEAD, items=_ITEMS), source_type="qm_incoming_inspect"),
    _chk({"cCheckPersonCode": "P01", "items": [{"cChkItemCode": "I", "cChkGuideCode": "G"}] * 1}, kind="qm_product_check"),
    _chk({"cCheckPersonCode": "P01", "items": [{"cChkItemCode": f"I{i}", "cChkGuideCode": "G"} for i in range(50)]}),
    _chk({"cCheckPersonCode": "P01", "cChkConclusion": "不合格", "fDtQuantity": 0.5}),
)

_REJECTS = (
    # 报检单
    _gen("qm_incoming_inspect", lines=[]),
    _auth(type="qm_incoming_inspect", id=7),
    _gen("qm_incoming_inspect", source_type="purchase_order"),
    _gen("qm_product_inspect", source_type="arrival"),
    _gen("qm_incoming_inspect", {"cVenCode": "V1"}),
    _gen("qm_incoming_inspect", {"CINSPECTCODE": "X"}),
    _gen("qm_incoming_inspect", {"cMaker": "x"}),
    _gen("qm_incoming_inspect", {"chDefine11": "x"}),
    _gen("qm_incoming_inspect", {"cDefine17": "x"}),
    _gen("qm_incoming_inspect", {"cDefine1": True}),
    _gen("qm_incoming_inspect", {"dDate": "2026/09/29"}),
    _gen("qm_incoming_inspect", {"cDepCode": 1}),
    _gen("qm_incoming_inspect", {"cDepCode": "x" * 61}),
    _gen("qm_incoming_inspect", {"cDepCode": "D1", "cdepcode": "D2"}),
    _gen("qm_incoming_inspect", {"items": [{"cChkItemCode": "I", "cChkGuideCode": "G"}]}),
    _gen("qm_incoming_inspect", lines=[{"source_line_id": 1, "quantity": 1, "cBatch": "B"}]),
    _gen("qm_incoming_inspect", lines=[{"source_line_id": 1, "quantity": 1, "cWhCode": ""}]),
    _gen("qm_incoming_inspect", lines=[{"source_line_id": 1, "quantity": 0}]),
    _gen("qm_incoming_inspect", lines=[{"source_line_id": 1, "quantity": True}]),
    _gen("qm_incoming_inspect", lines=[{"source_line_id": "1", "quantity": 1}]),
    _gen("qm_incoming_inspect", lines=[{"source_line_id": 1, "quantity": 1}, {"source_line_id": 1, "quantity": 2}]),
    _gen("qm_incoming_inspect", lines=[{"source_line_id": i, "quantity": 1} for i in range(1, 202)]),
    # 检验单：检验员必填
    _chk({}),
    _chk({"cDepCode": "D01"}),
    _chk({"cCheckPersonCode": ""}),
    _chk({"cCheckPersonCode": 5}),
    _auth(type="qm_incoming_check", id=7, lines=_CHECK_LINE),
    # 检验单：来源、表头字段
    _chk(source_type="arrival"),
    _chk(kind="qm_product_check", source_type="qm_incoming_inspect"),
    _chk({"cCheckPersonCode": "P01", "CCHECKCODE": "X"}),
    _chk({"cCheckPersonCode": "P01", "projectid": 3}),
    _chk({"cCheckPersonCode": "P01", "chDefine10": "x"}),
    _chk({"cCheckPersonCode": "P01", "chDefine17": "x"}),
    _chk({"cCheckPersonCode": "P01", "fDtQuantity": 0}),
    _chk({"cCheckPersonCode": "P01", "dYieldDate": "2026/09/30"}),
    _chk({"cCheckPersonCode": "P01", "fDtQuantity": "1"}),
    _chk({"cCheckPersonCode": "P01", "project_code": "x" * 61}),
    _chk({"cCheckPersonCode": "P01", "cMemo": ["x"]}),
    # 检验单：明细只能 1 行，合格 + 让步 + 不良 = quantity
    _chk(lines=_CHECK_LINE * 2),
    _chk(lines=[]),
    _chk(lines=_line(fRegQuantity=2)),
    _chk(lines=_line(fRegQuantity=2, fDisQuantity=2)),
    _chk(lines=_line(fDisQuantity=4)),
    _chk(lines=_line(fConQuantiy=-1)),
    _chk(lines=_line(fRegQuantity="3")),
    _chk(lines=_line(cWhCode="01")),
    _chk(lines=_line(quantity=0)),
    # 检验单：items
    _chk(dict(_CHECK_HEAD, items=[])),
    _chk(dict(_CHECK_HEAD, items=[{"cChkItemCode": f"I{i}", "cChkGuideCode": "G"} for i in range(51)])),
    _chk(dict(_CHECK_HEAD, items=[{"cChkItemCode": "I"}])),
    _chk(dict(_CHECK_HEAD, items=[{"cChkItemCode": "I", "cChkGuideCode": ""}])),
    _chk(dict(_CHECK_HEAD, items=[{"cChkItemCode": "I", "cChkGuideCode": "G", "cTargetQJug": "良"}])),
    _chk(dict(_CHECK_HEAD, items=[{"cChkItemCode": "I", "cChkGuideCode": "G", "cCheckValue": "x" * 61}])),
    _chk(dict(_CHECK_HEAD, items=[{"cChkItemCode": "x" * 61, "cChkGuideCode": "G"}])),
    _chk(dict(_CHECK_HEAD, items=[{"cChkItemCode": "I", "cChkGuideCode": "G", "cStandard": "1"}])),
    _chk(dict(_CHECK_HEAD, items=[{"cChkItemCode": "I", "cChkGuideCode": "G"}] * 2)),
    _chk(dict(_CHECK_HEAD, items=[{"cChkItemCode": "I", "cChkGuideCode": "G", "cCheckValue": {"a": 1}}])),
    _chk(dict(_CHECK_HEAD, items=["I01"])),
    _chk(dict(_CHECK_HEAD, items="I01")),
    # 别的类型的表头不能带列表
    _auth(type="arrival", id=3, head={"cWhCode": ["01"]}, lines=[{"source_line_id": 1, "quantity": 1}]),
    _auth(type="dispatch", id=3, head={"items": [{"a": 1}]}, lines=[{"source_line_id": 1, "quantity": 1}]),
)


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_qm_create_forwards_exact_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
    _forwards(path, bridge, body, keys)


@pytest.mark.parametrize("body", _ACCEPTS)
def test_qm_generate_accepts(body: dict) -> None:
    fake = FakeBridge()
    ok = _client(fake).post(_GEN, json=body)
    assert ok.status_code == 200, ok.text
    assert fake.calls[0][1]["type"] == body["type"]


@pytest.mark.parametrize("body", _REJECTS)
def test_qm_generate_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_GEN, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_qm_check_head_items_are_forwarded_as_given() -> None:
    fake = FakeBridge()
    head = dict(_CHECK_HEAD, items=_ITEMS)
    made = _client(fake).post(_GEN, json=_chk(head))
    assert made.status_code == 200, made.text
    sent = fake.calls[-1][1]
    assert sent["head"] == head
    assert sent["lines"] == _CHECK_LINE
    assert "source_type" not in sent
    assert made.json()["source_type"] == "qm_incoming_inspect"
    assert made.json()["source_id"] == 7


@pytest.mark.parametrize(
    ("kind", "source"),
    (
        ("qm_incoming_inspect", "arrival"),
        ("qm_product_inspect", "production_order"),
        ("qm_incoming_check", "qm_incoming_inspect"),
        ("qm_product_check", "qm_product_inspect"),
    ),
)
def test_qm_generate_default_sources(kind: str, source: str) -> None:
    fake = FakeBridge()
    head = _CHECK_HEAD if kind.endswith("_check") else None
    made = _client(fake).post(_GEN, json=_gen(kind, head))
    assert made.status_code == 200, made.text
    assert made.json()["source_type"] == source


def test_qm_generate_passes_the_wf_state_through() -> None:
    class _WfBridge(FakeBridge):
        def call(self, path: str, payload: dict) -> dict:
            reply = super().call(path, payload)
            reply["wf"] = {"controlled": True, "verify_state_new": 0, "status": "not_submitted"}
            return reply

    made = _client(_WfBridge()).post(_GEN, json=_chk())
    assert made.status_code == 200, made.text
    assert made.json()["wf"] == {"controlled": True, "verify_state_new": 0, "status": "not_submitted"}


@pytest.mark.parametrize("kind", ["qm_incoming_inspect", "qm_product_inspect", "qm_incoming_check", "qm_product_check"])
def test_qm_delete_is_allowed(kind: str) -> None:
    fake = FakeBridge()
    gone = _client(fake).post("/v1/co/vouchers/delete", json=_auth(type=kind, id=7))
    assert gone.status_code == 200, gone.text
    assert gone.json()["deleted"] is True
    assert fake.calls[-1][1]["type"] == kind


# 报检单不开放单独审核、弃审（U8 的 confirm 对报检单总是失败，只在保存时自动审核）；检验单走 workflow/*。
# 不良品处理单可直接审核（见 test_co_qm_reject）。产品报检单可弃审（见下一个用例）。
_QM_REFUSED = [
    (kind, action)
    for kind in ("qm_incoming_inspect", "qm_product_inspect", "qm_incoming_check", "qm_product_check")
    for action in ("verify", "unverify")
    if (kind, action) != ("qm_product_inspect", "unverify")
]


@pytest.mark.parametrize(("kind", "action"), _QM_REFUSED)
def test_qm_verify_is_refused_before_the_bridge(kind: str, action: str) -> None:
    fake = FakeBridge()
    denied = _client(fake).post("/v1/co/vouchers/verify", json=_auth(type=kind, id=7, action=action))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []

    assert fake.calls == []


def test_qm_idempotency_key_is_forwarded() -> None:
    fake = FakeBridge()
    headers = {"Idempotency-Key": "qm-create-0001"}
    made = _client(fake).post(_GEN, json=_gen("qm_incoming_inspect"), headers=headers)
    assert made.status_code == 200, made.text
    assert fake.calls[-1][1]["idempotency_key"] == "qm-create-0001"


class _ShapeBridge(FakeBridge):
    """按桥（QmDelete / QmSaved）真实返回的形状回话，不回显请求字段。"""

    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        state = {"verified": False, "verifier": "", "verified_at": ""}
        if path == "/v1/vouchers/delete":
            return {"ok": True, "type": payload["type"], "id": payload["id"], "code": "0000000001",
                    "deleted": True, "unverified": True}
        return {"ok": True, "type": payload["type"], "id": 42, "code": "QMIN202609290001", "state": state,
                "lines": 1, "items": 3, "source_type": "qm_incoming_inspect", "source_id": payload["id"],
                "wf": {"controlled": True, "verify_state_new": 0}}


def test_qm_delete_keeps_code_and_unverified() -> None:
    gone = _client(_ShapeBridge()).post("/v1/co/vouchers/delete", json=_auth(type="qm_incoming_inspect", id=7))
    assert gone.status_code == 200, gone.text
    assert gone.json() == {"ok": True, "type": "qm_incoming_inspect", "id": 7, "deleted": True,
                           "code": "0000000001", "unverified": True}


def test_qm_generate_keeps_items_and_wf() -> None:
    made = _client(_ShapeBridge()).post(_GEN, json=_chk())
    assert made.status_code == 200, made.text
    body = made.json()
    assert body["items"] == 3
    assert body["wf"] == {"controlled": True, "verify_state_new": 0}
    assert (body["code"], body["lines"], body["source_id"]) == ("QMIN202609290001", 1, 7)


def test_other_deletes_still_validate_without_the_new_fields() -> None:
    gone = _client(FakeBridge()).post("/v1/co/vouchers/delete", json=_auth(type="sale_order", id=7))
    assert gone.status_code == 200, gone.text
    assert gone.json() == {"ok": True, "type": "sale_order", "id": 7, "deleted": True}
