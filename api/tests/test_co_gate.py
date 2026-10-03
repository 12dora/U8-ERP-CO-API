"""CO gates. The fake bridge is never a socket to the Windows host."""

from tests.support import base_claims, entry, make_client
from u8co_api.co_clock import login_defaults
from u8co_api.errors import ApiError

_SECRET = "sentinel-not-a-password"
_HEX = "11" * 32
_LOCAL = "http://127.0.0.1:9/u8co"


class FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.error: ApiError | None = None

    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        return bridge_body(path, payload)

    def health(self) -> dict:
        if self.error is not None:
            raise self.error
        return {"ok": True, "version": "test"}


def _verified(payload: dict) -> dict:
    return {
        "ok": True,
        "acc": payload["acc"],
        "id": payload["id"],
        "action": payload["action"],
        "verified_by": "张三",
        "verified_at": "2026-09-27T00:00:00",
    }


def bridge_body(path: str, payload: dict) -> dict:
    builder = _BUILDERS.get(path)
    if builder is not None:
        return builder(payload)
    if path.startswith("/v1/workflow/"):
        return _flow(payload)
    return {"ok": True}


def _login_out(payload: dict) -> dict:
    return {"ok": True, "operator": payload["operator"], "operator_name": "张三"}


def _voucher_verify(payload: dict) -> dict:
    data = _verified(payload)
    data["type"] = payload["type"]
    if payload.get("type") == "transfer" and payload.get("action") == "verify":
        data["generated"] = [{"type": "other_out", "id": 8}, {"type": "other_in", "id": 9}]
    return data


def _doc_state() -> dict:
    return {"verified": False, "verifier": "", "verified_at": ""}


def _loaded(payload: dict) -> dict:
    return {
        "ok": True,
        "type": payload.get("type", "other_in"),
        "id": payload.get("id", 1),
        "code": "0000000001",
        "head": {"cCode": "0000000001"},
        "lines": [{"cInvCode": "A", "iQuantity": "1"}],
        "state": _doc_state(),
    }


def _created(payload: dict) -> dict:
    return {"ok": True, "type": payload["type"], "id": 1, "code": "0000000001", "state": _doc_state()}


def _deleted(payload: dict) -> dict:
    return {"ok": True, "type": payload["type"], "id": payload["id"], "deleted": True}


def _edited(payload: dict) -> dict:
    return {
        "ok": True,
        "type": payload["type"],
        "id": payload["id"],
        "code": "0000000001",
        "state": _doc_state(),
        "lines": 2,
    }


_SOURCE_OF = {
    "dispatch": "sale_order",
    "sale_out": "dispatch",
    "purchase_in": "purchase_order",
    "sale_invoice": "dispatch",
    "material_out": "production_order",
    "product_in": "qm_product_check",
    "purchase_invoice": "purchase_in",
    "arrival": "purchase_order",
    # 契约 B3：采购退货单缺省参照原蓝字到货单。
    "purchase_return": "arrival",
    # 契约 B2：退货单参照原蓝字发货单。
    "sale_return": "dispatch",
    # 报检单参照到货单或生产订单，检验单参照报检单。
    "qm_incoming_inspect": "arrival",
    "qm_product_inspect": "production_order",
    "qm_incoming_check": "qm_incoming_inspect",
    "qm_product_check": "qm_product_inspect",
    # 不良品处理单参照检验单。
    "qm_incoming_reject": "qm_incoming_check",
    "qm_product_reject": "qm_product_check",
    # 调拨单参照调拨申请单。
    "transfer": "transfer_request",
    # 采购结算单参照采购发票。
    "purchase_settle": "purchase_invoice",
}


def _generated_doc(payload: dict) -> dict:
    saved = _edited(payload)
    saved["id"] = 42
    saved["source_type"] = payload.get("source_type") or _SOURCE_OF[payload["type"]]
    saved["source_id"] = payload["id"]
    return saved


def _closed(payload: dict) -> dict:
    shut = payload["action"] == "close"
    ids = payload.get("line_ids") or [1]
    stamp = "2026-09-27 12:00:00" if shut else ""
    return {
        "ok": True,
        "type": payload["type"],
        "id": payload["id"],
        "action": payload["action"],
        "closed": shut,
        "closed_by": "张三" if shut else "",
        "closed_at": stamp,
        "lines": [{"line_id": item, "closed": shut} for item in ids],
    }


def _flow(payload: dict) -> dict:
    return {
        "ok": True,
        "type": payload.get("type", "qm_product_check"),
        "id": payload.get("id", 1),
        "code": "QMPR202609290001",
        "action": "approve",
        "u8_message": "",
        "operator": payload.get("operator", "op001"),
        "person": "op001",
        "other_count": 0,
        "wf": _WF,
        "history": [_HISTORY],
        "tasks": [_TASK],
    }


_WF = {
    "controlled": True,
    "status": "in_approval",
    "verify_state": 0,
    "verify_state_new": 1,
    "return_count": 0,
    "current_auditor": "审批人乙",
    "verifier": "",
    "verified_at": "",
    "instance": {
        "piid": "pi",
        "running": True,
        "started_by": "op001",
        "started_at": "2026-09-27 02:39:43",
    },
    "pending": [
        {
            "task_id": "t1",
            "activity_id": "a1",
            "person": "op002",
            "operator": "OP-002",
            "task_type": 1,
        }
    ],
}

_HISTORY = {
    "action": 1,
    "action_name": "agree",
    "task": "t1",
    "opinion": "",
    "person": "op001",
    "operator": "OP-001",
    "name": "操作员甲",
    "at": "2026-09-27 02:40:04",
}

_TASK = {
    "task_id": "t1",
    "type": "qm_product_check",
    "biz": "QM04",
    "id": 1,
    "code": "QMPR202609290001",
    "task_type": 1,
    "activity_id": "a1",
    "from": "审批人乙",
    "created_at": "2026-09-27 02:39:43",
}


def _gl_key(payload: dict) -> dict:
    return {
        "ok": True,
        "period": payload.get("period", 9),
        "sign": payload.get("sign", "转"),
        "no": payload.get("no", 1),
        "state": {"void": False, "checker": "", "cashier": "", "posted": False},
    }


def _gl_deleted(payload: dict) -> dict:
    data = _gl_key(payload)
    data.pop("state")
    data["deleted"] = True
    return data


def _gl_loaded(payload: dict) -> dict:
    voucher = {"period": payload["period"], "sign": payload["sign"], "no": payload["no"], "date": "2026-09-27"}
    voucher.update({"maker": "张三", "checker": "", "posted": False, "void": False, "source_system": "GL"})
    line = {"entry": 1, "account": "100201", "digest": "付款", "debit": "0", "credit": "1.00"}
    line["cash_flow"] = [{"item": "07", "debit": "0", "credit": "1.00"}]
    return {"ok": True, "voucher": voucher, "lines": [line]}


def _gl_listed(_payload: dict) -> dict:
    item = {"period": 9, "sign": "转", "no": 1, "date": "2026-09-27", "maker": "张三", "debit_total": "1.00"}
    item.update({"checker": "", "cashier": "", "posted": False, "void": False, "lines": 2})
    return {"ok": True, "items": [item], "next": "9|转|1"}


def _arc_key(payload: dict) -> dict:
    return {"ok": True, "archive": payload["archive"], "code": payload["code"]}


def _arc_deleted(payload: dict) -> dict:
    data = _arc_key(payload)
    data["deleted"] = True
    return data


def _arc_got(payload: dict) -> dict:
    data = _arc_key(payload)
    data["fields"] = {"name": "客户甲", "abbrname": "甲", "ccusmngtypecode": "999"}
    return data


def _arc_listed(_payload: dict) -> dict:
    items = [{"code": "C900001", "name": "客户甲", "class_code": "C90", "ufts": "25131156"}]
    return {"ok": True, "items": items, "next": "C900001", "watermark": "25131155"}


def _voucher_listed(payload: dict) -> dict:
    items = [{"id": 1, "code": "0000000001", "ufts": "25131156", "verified": 0}]
    return {"ok": True, "type": payload["type"], "items": items, "next": 1, "watermark": "25131155"}


def _stock(_payload: dict) -> dict:
    row = {"id": 20, "wh_code": "01", "inv_code": "INV0001", "batch": "20260801", "qty": "120"}
    row["qty_available"] = "120"
    return {"ok": True, "items": [row], "next": 20, "watermark": "25131155"}


_GL_BUILDERS = {f"/v1/gl/vouchers/{op}": _gl_key for op in ("create", "update", "void", "unvoid", "verify")}
_GL_BUILDERS.update({f"/v1/gl/vouchers/{op}": _gl_key for op in ("unverify", "sign", "unsign")})

def _locked(payload: dict) -> dict:
    locking = payload["action"] == "lock"
    return {
        "ok": True,
        "type": payload["type"],
        "id": payload["id"],
        "action": payload["action"],
        "locked": locking,
        "locker": "张三" if locking else "",
    }


def _written_off(payload: dict) -> dict:
    # 应收 / 应付核销（test_co_writeoff）：按请求回显，余额是假的。
    receipt = payload["receipt"]
    items = [
        {"type": item["type"], "id": item["id"], "line_id": item.get("line_id"), "amount": item["amount"], "remaining": 0}
        for item in payload["items"]
    ]
    head = {"type": receipt["type"], "id": receipt["id"], "line_id": receipt.get("line_id") or 7, "remaining": 1.5}
    return {"ok": True, "cancel_no": "HXAR0000000000001", "date": payload["date"], "receipt": head, "items": items}


def _unwritten(payload: dict) -> dict:
    # 取消核销（test_co_writeoff）：回显核销号，单据是假的。
    line = {"line_id": 7, "amount": 1, "remaining": 2.5}
    receipt = {"type": "ar_receipt", "id": 5, "line_id": 7, "code": "SK1", "remaining": 2.5, "lines": [line]}
    items = [{"type": "sale_invoice", "id": 9, "line_id": 11, "code": "SO1", "amount": 1, "remaining": 1, "memo": "x"}]
    return {"ok": True, "cancel_no": payload["cancel_no"], "flag": payload["flag"], "receipt": receipt, "items": items}


def _auto_written(payload: dict) -> dict:
    # 自动核销（test_co_writeoff_auto）：一批，按请求回显 flag、partner；dry_run 时回计划。
    head = {"ok": True, "flag": payload["flag"], "partner": payload["partner"], "total": 1}
    receipt = {"type": "ar_receipt", "id": 5, "line_id": 7, "code": "SK1", "remaining": 1.5}
    target = {"type": "sale_invoice", "id": 9, "line_id": 11, "code": "SO1", "amount": 1}
    if payload.get("dry_run"):
        return {**head, "dry_run": True, "plan": [{"receipt": receipt, "targets": [target], "amount": 1}]}
    batch = {"cancel_no": "HXAR0000000000002", "receipt": receipt, "items": [{**target, "remaining": 0}], "amount": 1}
    return {**head, "batches": [batch]}


def _vouchered(payload: dict) -> dict:
    # 应收 / 应付制单（test_co_arap_voucher）：按请求回显，凭证是假的。
    voucher = {"year": 2026, "period": 9, "sign": "转", "no": 172, "num": "转-0172", "date": "2026-09-24"}
    lines = [
        {"entry": 1, "account": "112201", "digest": "销售", "debit": 113, "credit": 0, "customer": "C001"},
        {"entry": 2, "account": "6001", "digest": "销售", "debit": 0, "credit": 100, "item_class": "ch", "item": "A"},
        {"entry": 3, "account": "222101", "digest": "销售", "debit": 0, "credit": 13, "memo": "x"},
    ]
    head = {"ok": True, "flag": payload["flag"], "type": payload["type"], "id": payload.get("id", 0), "code": "SO1"}
    pz = payload["flag"] + "0000000000001"
    return {**head, "pz_id": pz, "making_system": payload["flag"], "voucher": voucher, "lines": lines}


def _unvouchered(payload: dict) -> dict:
    # 取消制单（test_co_arap_voucher）：回显外部业务号。
    voucher = {"year": 2026, "period": 9, "sign": "转", "no": 172}
    return {"ok": True, "flag": payload["flag"], "pz_id": payload["pz_id"], "deleted": True, "voucher": voucher}


def _resolved(payload: dict) -> dict:
    # 档案名称解析（test_co_resolve）：每项都没找到。
    results = [{"archive": item["archive"], "q": item["q"], "status": "none"} for item in payload["items"]]
    return {"ok": True, "results": results}


_BUILDERS = {
    **_GL_BUILDERS,
    "/v1/gl/vouchers/load": _gl_loaded,
    "/v1/gl/vouchers/list": _gl_listed,
    "/v1/gl/vouchers/delete": _gl_deleted,
    "/v1/archives/get": _arc_got,
    "/v1/archives/list": _arc_listed,
    "/v1/archives/create": _arc_key,
    "/v1/archives/update": _arc_key,
    "/v1/archives/delete": _arc_deleted,
    "/v1/vouchers/list": _voucher_listed,
    "/v1/stock/current": _stock,
    "/v1/health": lambda _payload: {"ok": True, "version": "test"},
    "/v1/login-check": _login_out,
    "/v1/sale-orders/verify": _verified,
    "/v1/dispatches/verify": _verified,
    "/v1/vouchers/verify": _voucher_verify,
    "/v1/vouchers/load": _loaded,
    "/v1/vouchers/create": _created,
    "/v1/vouchers/delete": _deleted,
    "/v1/vouchers/update": _edited,
    "/v1/vouchers/close": _closed,
    "/v1/vouchers/generate": _generated_doc,
    # 销售订单 / 采购订单锁定、解锁（test_co_lock）。
    "/v1/vouchers/lock": _locked,
    # 应收 / 应付核销（test_co_writeoff）。
    "/v1/arap/writeoff": _written_off,
    "/v1/arap/writeoff/cancel": _unwritten,
    "/v1/arap/writeoff/auto": _auto_written,
    # 应收 / 应付制单、取消制单（test_co_arap_voucher）。
    "/v1/arap/voucher": _vouchered,
    "/v1/arap/voucher/delete": _unvouchered,
    # 档案名称解析、幂等结果查询（test_co_resolve、test_co_idem_get）。
    "/v1/archives/resolve": _resolved,
    "/v1/idempotency/get": lambda _payload: {"ok": True, "found": False},
    # 字段标签、单据查询、批量读取（test_co_lookup_routes）。
    "/v1/meta/fields": lambda payload: {"ok": True, "type": payload.get("type"), "head": [], "lines": []},
    "/v1/vouchers/search": lambda payload: {"ok": True, "type": payload["type"], "items": []},
    "/v1/vouchers/load_many": lambda payload: {"ok": True, "type": payload["type"], "items": []},
    "/v1/archives/get_many": lambda payload: {"ok": True, "archive": payload["archive"], "items": []},
}




def _login(**extra) -> dict:
    body = {"acc": "803", "operator": "op001", "password": _SECRET}
    body.update(extra)
    return body


def _client(fake=None, claims=None, auth=True, **flags):
    enabled = flags.pop("co_enabled", True)
    configured = flags.pop("co_configured", True)
    accounts = flags.pop("co_accounts", ("803",))
    if claims is None:
        claims = base_claims(u8co_write=True)
    client = make_client(
        claims=claims,
        auth=auth,
        enabled=enabled,
        accounts=accounts,
        bridge_secret=_HEX if configured else "",
        bridge_url=_LOCAL,
        **flags,
    )
    bridge = fake if fake is not None else FakeBridge()
    client.app.state.co_bridge = bridge
    return client


def test_missing_token_is_401():
    client = _client(auth=False)
    denied = client.post("/v1/co/login-check", json=_login())
    assert denied.status_code == 401
    assert denied.headers["www-authenticate"] == "Bearer"
    assert client.app.state.co_bridge.calls == []


def test_claim_must_be_true():
    missing = _client(claims=base_claims())
    denied = missing.post("/v1/co/login-check", json=_login())
    assert denied.status_code == 403
    assert denied.json()["error"]["message"] == "无权使用 CO 接口"
    false_claim = _client(claims=base_claims(u8co_write=False))
    assert false_claim.post("/v1/co/login-check", json=_login()).status_code == 403
    text = _client(claims=base_claims(u8co_write="true"))
    assert text.post("/v1/co/login-check", json=_login()).status_code == 403
    assert missing.app.state.co_bridge.calls == []


def test_disabled_is_404_before_the_bridge():
    fake = FakeBridge()
    client = _client(fake, co_enabled=False)
    denied = client.post("/v1/co/login-check", json=_login())
    assert denied.status_code == 404
    assert fake.calls == []


def test_unconfigured_is_503_before_the_bridge():
    fake = FakeBridge()
    client = _client(fake, co_configured=False)
    denied = client.post("/v1/co/login-check", json=_login())
    assert denied.status_code == 503
    assert denied.json()["error"]["code"] == "unavailable"
    assert fake.calls == []


def test_account_outside_allowlist_does_not_call():
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post("/v1/co/login-check", json=_login(acc="001"))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []


def test_token_accounts_are_ignored_without_an_accounts_claim():
    fake = FakeBridge()
    claims = base_claims(u8co_accounts=["001"], u8co_write=True)
    client = _client(fake, claims=claims)
    ok = client.post("/v1/co/login-check", json=_login())
    assert ok.status_code == 200
    assert fake.calls[0][1]["acc"] == "803"


def test_accounts_claim_narrows_the_allowlist():
    fake = FakeBridge()
    trust = (entry(accounts_claim="u8co_accounts"),)
    narrow = _client(fake, claims=base_claims(u8co_accounts=["902"], u8co_write=True), trust=trust)
    denied = narrow.post("/v1/co/login-check", json=_login())
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "account_not_allowed"
    missing = _client(fake, claims=base_claims(u8co_write=True), trust=trust)
    assert missing.post("/v1/co/login-check", json=_login()).status_code == 403
    text = _client(fake, claims=base_claims(u8co_accounts="803 902", u8co_write=True), trust=trust)
    assert text.post("/v1/co/login-check", json=_login()).status_code == 200
    both = _client(fake, claims=base_claims(u8co_accounts=["803", "001"], u8co_write=True), trust=trust)
    assert both.post("/v1/co/login-check", json=_login(acc="001")).status_code == 403
    assert len(fake.calls) == 1


def test_empty_allowlist_denies_every_account():
    fake = FakeBridge()
    client = _client(fake, co_accounts=())
    denied = client.post("/v1/co/login-check", json=_login())
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []


def test_date_and_year_default_from_the_login_date():
    fake = FakeBridge()
    client = _client(fake)
    ok = client.post("/v1/co/login-check", json=_login())
    assert ok.status_code == 200
    payload = fake.calls[0][1]
    date, year = login_defaults()
    assert payload["date"] == date
    assert payload["year"] == year
    dated = client.post("/v1/co/login-check", json=_login(date="2024-03-01"))
    assert dated.status_code == 200
    assert fake.calls[1][1]["date"] == "2024-03-01"
    assert fake.calls[1][1]["year"] == "2024"
