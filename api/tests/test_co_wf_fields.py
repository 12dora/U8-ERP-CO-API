"""审批流字段：待办的 title / piid，质量单据列表行的 wf_state / current_auditor。桥是本地假桥。"""

from __future__ import annotations

from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth
from u8co_api.co_models_list import CoVoucherListOut
from u8co_api.co_models_wf import CoTaskItem

_TASK = {
    "task_id": "00000000-0000-0000-0000-00000000000a",
    "type": "qm_product_check",
    "biz": "QM04",
    "id": 12,
    "code": "QC-0001",
    "task_type": 1,
    "activity_id": "00000000-0000-0000-0000-00000000000b",
    "from": "提交人甲",
    "created_at": "2026-09-28 08:00:00",
    "title": "提交人甲 提交的产品检验单 QC-0001 已进入审批流，等待您的审批！",
    "piid": "00000000-0000-0000-0000-00000000000c",
}
_ROW = {
    "id": 12,
    "code": "QC-0001",
    "verified": False,
    "closed": None,
    "red": None,
    "ufts": "123",
    "wf": True,
    "wf_state": "1",
    "current_auditor": "审批人乙",
}


class _Bridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if path == "/v1/workflow/tasks":
            return {"ok": True, "operator": "op001", "person": "P01", "tasks": [_TASK], "other_count": 0}
        if path == "/v1/vouchers/list":
            return {"ok": True, "type": payload["type"], "items": [_ROW], "watermark": "200"}
        return super().call(path, payload)


def test_tasks_carry_title_and_instance() -> None:
    fake = _Bridge()
    client = _client(fake)
    got = client.post("/v1/co/workflow/tasks", json=_auth())
    assert got.status_code == 200, got.text
    task = got.json()["tasks"][0]
    assert task["title"] == _TASK["title"]
    assert task["piid"] == _TASK["piid"]
    assert task["from"] == "提交人甲"
    assert task["created_at"] == "2026-09-28 08:00:00"


def test_task_title_and_piid_default_for_old_bridges() -> None:
    old = {key: value for key, value in _TASK.items() if key not in ("title", "piid")}
    item = CoTaskItem.model_validate(old)
    assert item.title == ""
    assert item.piid == ""


def test_task_schema_lists_new_fields() -> None:
    props = CoTaskItem.model_json_schema(by_alias=True)["properties"]
    assert {"title", "piid", "created_at", "activity_id", "from"} <= set(props)


def test_qm_list_rows_keep_workflow_fields() -> None:
    fake = _Bridge()
    client = _client(fake)
    got = client.post("/v1/co/vouchers/list", json=_auth(type="qm_product_check"))
    assert got.status_code == 200, got.text
    row = got.json()["items"][0]
    assert row["wf_state"] == "1"
    assert row["current_auditor"] == "审批人乙"
    assert fake.calls[-1][0] == "/v1/vouchers/list"


def test_list_item_description_mentions_workflow_fields() -> None:
    text = CoVoucherListOut.model_fields["items"].description or ""
    assert "wf_state" in text
    assert "current_auditor" in text
