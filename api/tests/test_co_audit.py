"""CO audit lines name the action and never the password."""

from tests.support import audit_line as _audit_line
from tests.support import entry
from tests.test_co_dryrun import DryBridge
from tests.test_co_gate import _SECRET, _client, _login

_USER = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"


def test_audit_records_the_action_and_omits_the_password(capsys):
    # 终端用户头只对写了 on_behalf_header 的信任项（代人调用的机器调用方）生效。
    client = _client(trust=(entry(on_behalf_header=True),))
    body = _login(id=1, action="verify", year="2026", date="2026-09-27")
    ok = client.post(
        "/v1/co/sale-orders/verify",
        json=body,
        headers={"X-U8co-User": _USER},
    )
    assert ok.status_code == 200
    err = capsys.readouterr().out
    assert _SECRET not in err
    line = _audit_line(err, "/v1/co/sale-orders/verify")
    assert line["accs"] == ["803"]
    assert line["operators"] == ["803=op001"]
    assert line["action"] == "co:sale-orders/verify:verify#1"
    assert line["user"] == _USER
    assert line["endpoint"] == "/v1/co/sale-orders/verify"


def test_audit_marks_dry_runs(capsys):
    client = _client(DryBridge())
    body = _login(type="sale_order", id=3, year="2026", date="2026-09-27")
    assert client.post("/v1/co/vouchers/delete", json=body).status_code == 200
    line = _audit_line(capsys.readouterr().out, "/v1/co/vouchers/delete")
    assert line["dry_run"] is False
    assert client.post("/v1/co/vouchers/delete", json={**body, "dry_run": True}).status_code == 200
    preview = _audit_line(capsys.readouterr().out, "/v1/co/vouchers/delete")
    assert preview["dry_run"] is True
    assert preview["action"] == line["action"] == "co:vouchers/delete:delete#3"


def test_audit_marks_auto_writeoff_plans(capsys):
    client = _client()
    body = _login(flag="AR", partner="C001", year="2026", date="2026-09-27")
    assert client.post("/v1/co/arap/writeoff/auto", json={**body, "dry_run": True}).status_code == 200
    assert _audit_line(capsys.readouterr().out, "/v1/co/arap/writeoff/auto")["dry_run"] is True
    assert client.post("/v1/co/arap/writeoff/auto", json=body).status_code == 200
    assert _audit_line(capsys.readouterr().out, "/v1/co/arap/writeoff/auto")["dry_run"] is False


def test_audit_records_the_document_id(capsys):
    client = _client()
    body = _login(type="qm_product_check", id=7, opinion="同意", year="2026", date="2026-09-27")
    ok = client.post("/v1/co/workflow/approve", json=body)
    assert ok.status_code == 200
    line = _audit_line(capsys.readouterr().out, "/v1/co/workflow/approve")
    assert line["action"] == "co:workflow/approve:approve#7"
    assert _SECRET not in line["action"]
    assert "同意" not in line["action"]
