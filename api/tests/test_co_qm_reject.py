"""来料 / 产品不良品处理单（QM05 / QM06）参照检验单生单、直接审核 / 弃审与删除。桥是本地假桥。"""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards
from u8co_api.co_gen_qm import QM_SOURCES, check_qm_gen

_GEN = "/v1/co/vouchers/generate"
_KINDS = ("qm_incoming_reject", "qm_product_reject")
_LINE = {"source_line_id": 7, "quantity": 1, "cScrapDisCode": "Sys01", "cReasonCode": "01"}
# 有的账套把 QM06 模板的 chDefine15 设为必输，处理单表头和检验单一样收 chDefine11–16。
_HEAD = {"dDate": "2026-10-02", "cDefine1": "x", "cdefine16": 3, "chDefine11": "a", "chDefine15": "SEED", "chdefine16": 2}


def _rej(kind: str = "qm_incoming_reject", lines: list | None = None, **extra) -> dict:
    body = {"type": kind, "id": 7, "lines": [dict(_LINE)] if lines is None else lines}
    body.update(extra)
    return _auth(**body)


def _line(**extra) -> list[dict]:
    row = dict(_LINE)
    row.update(extra)
    return [row]


_FORWARD = (
    (_GEN, "/v1/vouchers/generate", _rej(), _sealed("type", "id", "lines")),
    (
        _GEN,
        "/v1/vouchers/generate",
        _rej("qm_product_reject", _line(cScrapDisCode="Sys03", cDimInvCode="I02"), head=_HEAD,
             source_type="qm_product_check"),
        _sealed("type", "id", "head", "lines", "source_type"),
    ),
    ("/v1/co/vouchers/delete", "/v1/vouchers/delete", _auth(type="qm_incoming_reject", id=7), _sealed("type", "id")),
    (
        "/v1/co/vouchers/verify",
        "/v1/vouchers/verify",
        _auth(type="qm_product_reject", id=7, action="unverify"),
        _sealed("type", "id", "action"),
    ),
)


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_qm_reject_forwards_exact_sealed_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
    _forwards(path, bridge, body, keys)


@pytest.mark.parametrize(("kind", "source"), (("qm_incoming_reject", "qm_incoming_check"),
                                              ("qm_product_reject", "qm_product_check")))
def test_qm_reject_default_source(kind: str, source: str) -> None:
    fake = FakeBridge()
    made = _client(fake).post(_GEN, json=_rej(kind))
    assert made.status_code == 200, made.text
    assert made.json()["source_type"] == source
    assert QM_SOURCES[kind] == (source,)


def test_qm_reject_lines_and_head_are_forwarded_as_is() -> None:
    fake = FakeBridge()
    lines = [
        dict(_LINE, quantity=0.5, cbWhCode="01"),
        dict(_LINE, quantity=0.5, cScrapDisCode="Sys03", cDimInvCode="I02"),
    ]
    made = _client(fake).post(_GEN, json=_rej("qm_product_reject", lines, head=_HEAD))
    assert made.status_code == 200, made.text
    sent = fake.calls[-1][1]
    assert (sent["type"], sent["id"], sent["lines"], sent["head"]) == ("qm_product_reject", 7, lines, _HEAD)


def test_qm_reject_dry_run_is_forwarded() -> None:
    from tests.test_co_dryrun import DryBridge

    fake = DryBridge()
    made = _client(fake).post(_GEN, json=_rej(dry_run=True))
    assert made.status_code == 200, made.text
    assert made.json()["dry_run"] is True
    assert fake.calls[-1][1]["dry_run"] is True


_BAD = (
    _rej(lines=[]),
    _auth(type="qm_incoming_reject", id=7),
    _rej(source_type="qm_product_check"),
    _rej(source_type="qm_incoming_inspect"),
    _rej(lines=_line(source_line_id=8)),
    _rej(lines=_line(source_line_id=0)),
    _rej(lines=_line(quantity=0)),
    _rej(lines=_line(quantity=-1)),
    _rej(lines=_line(quantity="1")),
    _rej(lines=_line(cScrapDisCode="")),
    _rej(lines=_line(cReasonCode=" ")),
    _rej(lines=[{"source_line_id": 7, "quantity": 1, "cReasonCode": "01"}]),
    _rej(lines=[{"source_line_id": 7, "quantity": 1, "cScrapDisCode": "Sys01"}]),
    _rej(lines=_line(cDimInvCode="")),
    _rej(lines=_line(cbWhCode=3)),
    _rej(lines=_line(cbMemo="拒收")),
    _rej(lines=_line(fQuantity=1)),
    _rej(lines=_line(cscrapdiscode="Sys02")),
    _rej(head={"cCheckPersonCode": "P01"}),
    _rej(head={"chDefine10": "x"}),
    _rej(head={"chDefine17": "x"}),
    _rej(head={"chDefine15": ["x"]}),
    _rej(head={"cDefine17": "x"}),
    _rej(head={"dDate": "2026/10/02"}),
    _rej(head={"cMemo": "不良品处理"}),
    _rej(head={"items": [{"cChkItemCode": "I", "cChkGuideCode": "G"}]}),
    _rej(head={"dDate": "2026-10-02", "ddate": "2026-10-03"}),
)


@pytest.mark.parametrize("body", _BAD)
def test_qm_reject_generate_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    made = _client(fake).post(_GEN, json=body)
    assert made.status_code == 400, made.text
    assert made.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("action", ["verify", "unverify"])
def test_qm_reject_verify_is_forwarded(kind: str, action: str) -> None:
    fake = FakeBridge()
    done = _client(fake).post("/v1/co/vouchers/verify", json=_auth(type=kind, id=7, action=action))
    assert done.status_code == 200, done.text
    assert done.json()["type"] == kind
    assert fake.calls[-1][1]["action"] == action


@pytest.mark.parametrize("kind", _KINDS)
def test_qm_reject_arap_actions_are_refused(kind: str) -> None:
    fake = FakeBridge()
    denied = _client(fake).post("/v1/co/vouchers/verify", json=_auth(type=kind, id=7, action="arap_verify"))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


@pytest.mark.parametrize("kind", _KINDS)
def test_qm_reject_delete_is_forwarded(kind: str) -> None:
    fake = FakeBridge()
    gone = _client(fake).post("/v1/co/vouchers/delete", json=_auth(type=kind, id=7))
    assert gone.status_code == 200, gone.text
    assert gone.json()["deleted"] is True
    assert fake.calls[-1][1]["type"] == kind


def test_qm_reject_bridge_conflict_passes_through() -> None:
    from u8co_api.errors import ApiError

    fake = FakeBridge()
    fake.error = ApiError(409, "state_mismatch", "检验单已生成不良品处理单")
    made = _client(fake).post(_GEN, json=_rej())
    assert made.status_code == 409, made.text
    assert made.json()["error"]["code"] == "state_mismatch"


def test_check_qm_gen_without_id_skips_the_source_match() -> None:
    check_qm_gen("qm_incoming_reject", None, _line(source_line_id=99))
    with pytest.raises(ValueError, match="source_line_id"):
        check_qm_gen("qm_incoming_reject", None, _line(source_line_id=99), 7)


def test_qm_reject_kinds_are_in_the_openapi_enums() -> None:
    from tests.test_co_update_close_gen import _enum, _spec

    spec = _spec()
    for schema in ("CoGenerateIn", "CoVoucherVerifyIn", "CoDeleteIn"):
        for kind in _KINDS:
            assert kind in _enum(spec, schema, "type"), (schema, kind)
    head = spec["components"]["schemas"]["CoGenerateIn"]["properties"]["head"]["description"]
    assert "cScrapDisCode" in head
