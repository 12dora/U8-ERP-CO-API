"""/v1/co: GL vouchers, archives, lists, stock and the new voucher kinds. The fake bridge is local."""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line as _audit_line
from tests.support import base_claims, write_keys
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec
from u8co_api.co_bridge import to_api_error
from u8co_api.co_service import login_defaults

_KEY = {"period": 9, "sign": "转", "no": 3}
_HEAD = {"sign": "转", "date": "2026-09-27", "attachments": 1}
_LINES = [
    {"account": "660202", "digest": "报销", "debit": 1.5, "dept": "D901"},
    {"account": "100201", "digest": "报销", "credit": 1.5, "cash_flow": [{"item": "07", "credit": 1.5}]},
]
_FIELDS = {"name": "客户甲", "abbrname": "甲", "tax_reg_code": "91000000", "bcusdomestic": True}
_FILTER = {"date_from": "2026-08-01", "date_to": "2026-08-31", "cus_code": "C000002", "verified": True}
_A1 = ("ar_receipt", "ap_payment", "ar_bill", "ap_bill")
_KEY_OPS = ("load", "void", "unvoid", "verify", "unverify", "sign", "unsign", "delete")
_LEAK = "field-value-must-not-be-audited"


def _gl(op: str, body: dict, *keys: str) -> tuple:
    return (f"/v1/co/gl/vouchers/{op}", f"/v1/gl/vouchers/{op}", body, _sealed(*keys))


def _arc(op: str, body: dict, *keys: str) -> tuple:
    return (f"/v1/co/archives/{op}", f"/v1/archives/{op}", body, _sealed("archive", "code", *keys))


def _doc(op: str, body: dict, *keys: str) -> tuple:
    return (f"/v1/co/vouchers/{op}", f"/v1/vouchers/{op}", body, _sealed("type", *keys))


_FORWARD = (
    *(_gl(op, _auth(**_KEY), "period", "sign", "no") for op in _KEY_OPS),
    _gl("list", _auth(period_from=1, period_to=12), "period_from", "period_to"),
    _gl(
        "list",
        _auth(
            period_from=9,
            period_to=9,
            sign="转",
            date_from="2026-09-01",
            date_to="2026-09-30",
            maker="张三",
            state="audited",
            after="9|转|1",
            limit=200,
        ),
        "period_from",
        "period_to",
        "sign",
        "date_from",
        "date_to",
        "maker",
        "state",
        "after",
        "limit",
    ),
    _gl("create", _auth(head=_HEAD, lines=_LINES), "head", "lines"),
    _gl("update", _auth(head=_HEAD, lines=_LINES, **_KEY), "period", "sign", "no", "head", "lines"),
    _arc("get", _auth(archive="customer", code="C900001")),
    _arc("delete", _auth(archive="warehouse", code="99")),
    _arc("create", _auth(archive="customer", code="C900001", fields=_FIELDS), "fields"),
    _arc(
        "create",
        _auth(archive="inventory", code="A01", fields={"name": "测试存货"}, template="INV0005"),
        "fields",
        "template",
    ),
    _arc("update", _auth(archive="department", code="D902", fields={"name": "测试部"}), "fields"),
    (
        "/v1/co/archives/list",
        "/v1/archives/list",
        _auth(archive="customer", code_prefix="C1", name_like="客户", changed_since="24500000", after="C1", limit=500),
        _sealed("archive", "code_prefix", "name_like", "changed_since", "after", "limit"),
    ),
    ("/v1/co/archives/list", "/v1/archives/list", _auth(archive="person"), _sealed("archive")),
    _doc(
        "list",
        _auth(type="sale_order", filter=_FILTER, keys_only=True, changed_since="24500000", after=0, limit=500),
        "filter",
        "keys_only",
        "changed_since",
        "after",
        "limit",
    ),
    _doc("list", _auth(type="ar_receipt")),
    (
        "/v1/co/stock/current",
        "/v1/stock/current",
        _auth(wh="01", inv="INV0001", batch="20260801", after=20, limit=100),
        _sealed("wh", "inv", "batch", "after", "limit"),
    ),
    ("/v1/co/stock/current", "/v1/stock/current", _auth(), _sealed()),
    _doc("load", _auth(type="ar_bill", id=5), "id"),
    _doc("create", _auth(type="ar_receipt", head={"cDwCode": "C1"}, lines=[{"iAmt": 1}]), "head", "lines"),
    _doc("verify", _auth(type="ap_payment", id=5, action="verify"), "id", "action"),
    _doc("verify", _auth(type="production_order", id=5, action="unverify"), "id", "action"),
    _doc("delete", _auth(type="ap_bill", id=5), "id"),
    _doc("delete", _auth(type="material_out", id=5), "id"),
    _doc("delete", _auth(type="product_in", id=5), "id"),
    _doc(
        "generate",
        _auth(type="material_out", id=7, head={"cWhCode": "01"}, lines=[{"source_line_id": 11, "quantity": 2}]),
        "id",
        "head",
        "lines",
    ),
    _doc("generate", _auth(type="product_in", id=8, lines=[{"source_line_id": 8, "quantity": 1}]), "id", "lines"),
)

_GATED = (
    ("/v1/co/gl/vouchers/void", _auth(**_KEY)),
    ("/v1/co/gl/vouchers/list", _auth(period_from=1, period_to=12)),
    ("/v1/co/archives/get", _auth(archive="customer", code="C1")),
    ("/v1/co/vouchers/list", _auth(type="sale_order")),
    ("/v1/co/stock/current", _auth()),
)

_AUDIT = (
    ("/v1/co/gl/vouchers/void", _auth(**_KEY), "co:gl/vouchers/void:void#9-转-3"),
    ("/v1/co/gl/vouchers/create", _auth(head=_HEAD, lines=_LINES), "co:gl/vouchers/create:create"),
    (
        "/v1/co/archives/update",
        _auth(archive="customer", code="C900001", fields={"name": _LEAK}),
        "co:archives/update:update#customer:C900001",
    ),
    ("/v1/co/vouchers/list", _auth(type="sale_order", filter={"maker": _LEAK}), "co:vouchers/list:list"),
    ("/v1/co/vouchers/verify", _auth(type="production_order", id=6, action="verify"), "co:vouchers/verify:verify#6"),
)

_OPENAPI = {
    "/v1/co/gl/vouchers/load": ("读取总账凭证", "coGlVoucherLoad", "总账凭证"),
    "/v1/co/gl/vouchers/create": ("新增总账凭证", "coGlVoucherCreate", "总账凭证"),
    "/v1/co/gl/vouchers/unsign": ("取消出纳签字", "coGlVoucherUnsign", "总账凭证"),
    "/v1/co/archives/list": ("档案列表", "coArchiveList", "基础档案"),
    "/v1/co/archives/delete": ("删除档案", "coArchiveDelete", "基础档案"),
    "/v1/co/vouchers/list": ("单据列表", "coVoucherList", "单据读取"),
    "/v1/co/stock/current": ("现存量", "coStockCurrent", "现存量"),
}


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_gl_arc_forwards_exact_sealed_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert method == "POST"
    assert seen == "/u8co" + bridge
    assert set(sent) == write_keys(path, keys)
    assert _SECRET.encode() not in raw
    assert b'"password"' not in raw


def test_nested_bodies_are_forwarded_without_nulls() -> None:
    fake = FakeBridge()
    client = _client(fake)
    created = client.post("/v1/co/gl/vouchers/create", json=_auth(head=_HEAD, lines=_LINES))
    assert created.status_code == 200, created.text
    sent = fake.calls[-1][1]
    assert sent["head"] == _HEAD
    assert sent["lines"] == _LINES
    fields = dict(_FIELDS, cmemo=None)
    made = client.post("/v1/co/archives/create", json=_auth(archive="customer", code="C900001", fields=fields))
    assert made.status_code == 200, made.text
    assert fake.calls[-1][1]["fields"] == _FIELDS
    listed = client.post("/v1/co/vouchers/list", json=_auth(type="sale_order", filter=dict(_FILTER, maker=None)))
    assert listed.status_code == 200, listed.text
    assert fake.calls[-1][1]["filter"] == _FILTER


@pytest.mark.parametrize("date", ("2026-09-27", None))
def test_gl_fiscal_year_follows_the_login_date_not_year(date: str | None) -> None:
    fake = FakeBridge()
    client = _client(fake)
    login = date or login_defaults()[0]
    body = _auth(year="2024", date=login, head=dict(_HEAD, date=login[:4] + "-01-31"), lines=_LINES)
    if date is None:
        body.pop("date")
    ok = client.post("/v1/co/gl/vouchers/create", json=body)
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["year"] == "2024"


class _ExtraBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        body = super().call(path, payload)
        body["bridge_note"] = "kept"
        for item in body.get("items") or []:
            item["extra_col"] = "kept"
        return body


def test_new_responses_keep_bridge_fields() -> None:
    client = _client(_ExtraBridge())
    loaded = client.post("/v1/co/gl/vouchers/load", json=_auth(**_KEY))
    assert loaded.status_code == 200, loaded.text
    body = loaded.json()
    assert body["voucher"]["sign"] == "转"
    assert body["lines"][0]["cash_flow"] == [{"item": "07", "debit": "0", "credit": "1.00"}]
    assert body["bridge_note"] == "kept"
    listed = client.post("/v1/co/gl/vouchers/list", json=_auth(period_from=9, period_to=9))
    assert listed.json()["next"] == "9|转|1"
    assert listed.json()["items"][0]["extra_col"] == "kept"
    voided = client.post("/v1/co/gl/vouchers/void", json=_auth(**_KEY))
    assert voided.json()["state"]["void"] is False
    gone = client.post("/v1/co/gl/vouchers/delete", json=_auth(**_KEY))
    assert gone.json()["deleted"] is True
    got = client.post("/v1/co/archives/get", json=_auth(archive="customer", code="C900001"))
    assert got.json()["fields"]["ccusmngtypecode"] == "999"
    arcs = client.post("/v1/co/archives/list", json=_auth(archive="customer"))
    assert arcs.json()["watermark"] == "25131155"
    assert arcs.json()["items"][0]["class_code"] == "C90"
    docs = client.post("/v1/co/vouchers/list", json=_auth(type="ar_bill", keys_only=True))
    assert docs.json()["items"][0]["extra_col"] == "kept"
    assert docs.json()["next"] == 1
    stock = client.post("/v1/co/stock/current", json=_auth(wh="01"))
    assert stock.json()["items"][0]["qty_available"] == "120"
    assert stock.json()["bridge_note"] == "kept"


class _ArapBridge(FakeBridge):
    """收付款单与生产订单按合同 A1/A3 的形状返回：state 代替 acc/verified_by/verified_at。"""

    def call(self, path: str, payload: dict) -> dict:
        body = super().call(path, payload)
        if path == "/v1/vouchers/verify":
            state = {"verified": payload["action"] == "verify", "verifier": "张三", "rows": 3}
            return {"ok": True, "type": body["type"], "id": body["id"], "action": body["action"], "state": state}
        if path == "/v1/vouchers/create":
            return {"ok": True, "type": body["type"], "id": 12, "code": "SK202609270001", "lines": 1}
        return body


@pytest.mark.parametrize("kind", (*_A1, "production_order"))
def test_verify_accepts_the_contract_shape(kind: str) -> None:
    client = _client(_ArapBridge())
    ok = client.post("/v1/co/vouchers/verify", json=_auth(type=kind, id=4, action="verify"))
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["state"] == {"verified": True, "verifier": "张三", "rows": 3}
    assert "verified_by" not in body


@pytest.mark.parametrize("kind", _A1)
def test_a1_kinds_load_create_delete(kind: str) -> None:
    client = _client(_ArapBridge())
    loaded = client.post("/v1/co/vouchers/load", json=_auth(type=kind, id=4))
    assert loaded.status_code == 200, loaded.text
    created = client.post("/v1/co/vouchers/create", json=_auth(type=kind, head={"cDwCode": "C1"}, lines=[{"iAmt": 1}]))
    assert created.status_code == 200, created.text
    assert created.json()["code"] == "SK202609270001"
    assert "state" not in created.json()
    deleted = client.post("/v1/co/vouchers/delete", json=_auth(type=kind, id=4))
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True


@pytest.mark.parametrize(("kind", "source"), (("material_out", "production_order"), ("product_in", "qm_product_check")))
def test_mfg_generate_reports_the_source(kind: str, source: str) -> None:
    client = _client()
    lines = [{"source_line_id": 3, "quantity": 1}]
    made = client.post("/v1/co/vouchers/generate", json=_auth(type=kind, id=9, lines=lines))
    assert made.status_code == 200, made.text
    assert made.json()["source_type"] == source
    assert made.json()["source_id"] == 9


@pytest.mark.parametrize(("path", "body"), _GATED)
def test_gl_arc_routes_are_gated(path: str, body: dict) -> None:
    fake = FakeBridge()
    assert _client(fake, co_enabled=False).post(path, json=body).status_code == 404
    denied = _client(fake, claims=base_claims()).post(path, json=body)
    assert denied.status_code == 403
    outside = _client(fake).post(path, json=dict(body, acc="001"))
    assert outside.status_code == 403
    assert outside.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []


@pytest.mark.parametrize(("path", "body", "view"), _AUDIT)
def test_gl_arc_audit_names_the_document(path: str, body: dict, view: str, capsys) -> None:
    client = _client()
    ok = client.post(path, json=body)
    assert ok.status_code == 200, ok.text
    err = capsys.readouterr().out
    assert _SECRET not in err
    assert _LEAK not in err
    assert _audit_line(err, path)["action"] == view


def test_u8_unavailable_maps_to_503() -> None:
    error = to_api_error(503, {"code": "u8_unavailable", "message": "U8 生产制造服务未运行"})
    assert error.status == 503
    assert error.code == "u8_unavailable"


def test_gl_arc_openapi_paths_summaries_and_tags() -> None:
    spec = _spec()
    for path, (summary, operation_id, tag) in _OPENAPI.items():
        operation = spec["paths"][path]["post"]
        assert operation["summary"] == summary
        assert operation["operationId"] == operation_id
        assert operation["tags"] == [tag]
    for op in (*_KEY_OPS, "list", "create", "update"):
        assert f"/v1/co/gl/vouchers/{op}" in spec["paths"]
    for kind in _A1:
        assert kind in _enum(spec, "CoLoadIn", "type")
        assert kind in _enum(spec, "CoCreateIn", "type")
        assert kind in _enum(spec, "CoDeleteIn", "type")
        assert kind in _enum(spec, "CoVoucherVerifyIn", "type")
        assert kind in _enum(spec, "CoVoucherListIn", "type")
    assert "production_order" in _enum(spec, "CoVoucherVerifyIn", "type")
    assert "material_out" in _enum(spec, "CoGenerateIn", "type")
    assert "product_in" in _enum(spec, "CoDeleteIn", "type")
    assert "customer_class" in _enum(spec, "ArcKeyIn", "archive")
    assert "u8_unavailable" in spec["info"]["description"]
    assert "8MiB" in spec["info"]["description"]
    stock = spec["components"]["schemas"]["CoStockOut"]["properties"]["items"]["description"]
    assert "fStopQuantity" in stock
