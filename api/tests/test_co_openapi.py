"""OpenAPI for /v1/co stays behind a caller token. The public docs URLs stay 404."""

import json

from tests.support import make_client

_PATHS = (
    "/v1/co/login-check",
    "/v1/co/sale-orders/verify",
    "/v1/co/dispatches/verify",
    "/v1/co/health",
    "/v1/co/vouchers/load",
    "/v1/co/vouchers/verify",
    "/v1/co/vouchers/create",
    "/v1/co/vouchers/delete",
    "/v1/co/workflow/state",
    "/v1/co/workflow/history",
    "/v1/co/workflow/tasks",
    "/v1/co/workflow/submit",
    "/v1/co/workflow/withdraw",
    "/v1/co/workflow/approve",
    "/v1/co/workflow/disagree",
    "/v1/co/workflow/return",
    "/v1/co/workflow/abandon",
    "/v1/co/workflow/resubmit",
    "/v1/co/reports/account_readiness",
)

_TAGS = ("单据读取", "单据审核", "单据新增删除", "审批流", "U8 业务操作")

_SUMMARIES = {
    "/v1/co/vouchers/load": "读取单据",
    "/v1/co/workflow/submit": "提交审批",
    "/v1/co/workflow/withdraw": "撤销提交",
    "/v1/co/workflow/approve": "同意",
    "/v1/co/workflow/disagree": "不同意并继续",
    "/v1/co/workflow/return": "退回提交人",
    "/v1/co/workflow/abandon": "弃审",
    "/v1/co/workflow/resubmit": "退回后重新提交",
    "/v1/co/reports/account_readiness": "账套体检",
}

_CODES = (
    "not_submitted",
    "already_submitted",
    "not_current_approver",
    "workflow_disabled",
    "stock_shortage",
    "bad_response",
)


def test_public_docs_stay_404_and_co_spec_needs_a_caller():
    client = make_client(auth=False)
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    denied = client.get("/v1/openapi.json")
    assert denied.status_code == 401
    ok = client.get("/v1/openapi.json", headers={"Authorization": f"Bearer {client.token}"})
    assert ok.status_code == 200
    spec = ok.json()
    assert spec["openapi"].startswith("3.1")
    assert spec["paths"]
    assert all(path.startswith("/v1/co/") for path in spec["paths"])
    for path in _PATHS:
        assert path in spec["paths"]
    assert "/v1/openapi.json" not in spec["paths"]
    scheme = spec["components"]["securitySchemes"]["bearerAuth"]
    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"
    assert "U8CO_ACCOUNTS" in spec["info"]["description"]
    assert "为空" in spec["info"]["description"]
    assert "outcome_unknown" in spec["info"]["description"]
    for code in _CODES:
        assert code in spec["info"]["description"]
    found_tags = _tags(spec)
    for name in _TAGS:
        assert name in found_tags
    for path, summary in _SUMMARIES.items():
        assert spec["paths"][path]["post"]["summary"] == summary
    assert "MatchIn" not in spec["components"]["schemas"]
    _assert_password_write_only(spec)
    assert '"password": "' not in json.dumps(spec)


def _tags(spec: dict) -> set[str]:
    found: set[str] = set()
    for item in spec["paths"].values():
        for operation in item.values():
            if isinstance(operation, dict):
                found.update(operation.get("tags") or [])
    return found


def _assert_password_write_only(spec: dict) -> None:
    found: list[dict] = []
    _password_schemas(spec, found)
    assert found
    for schema in found:
        assert schema.get("writeOnly") is True
        assert "example" not in schema
        assert "examples" not in schema
        assert "default" not in schema


def _password_schemas(node, found: list[dict]) -> None:
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict) and isinstance(props.get("password"), dict):
            found.append(props["password"])
        for value in node.values():
            _password_schemas(value, found)
    elif isinstance(node, list):
        for item in node:
            _password_schemas(item, found)


def test_description_lists_the_configured_accounts():
    client = make_client(accounts=("803", "902"))
    spec = client.get("/v1/openapi.json").json()
    assert "803、902" in spec["info"]["description"]
    assert "为空" not in spec["info"]["description"]


def test_healthz_needs_no_token_and_does_not_call_the_bridge():
    client = make_client(auth=False)
    ok = client.get("/healthz")
    assert ok.status_code == 200
    assert ok.json() == {"ok": True, "configured": False}
