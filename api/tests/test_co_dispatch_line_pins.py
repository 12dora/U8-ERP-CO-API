"""发货单参照销售订单生单的表体扩展自定义项 cbdefineN（co_sale_pins.check_dispatch_gen_pins）。

合法请求原样转给桥（列是否存在、类型长度、保存后回读由桥判断）；形状不对的 400 且不调桥。
"""

from __future__ import annotations

import pytest
from tests.test_co_core_routes import _auth
from tests.test_co_gate import FakeBridge, _client
from u8co_api.co_sale_pins import check_dispatch_gen_pins, check_line_extra_pins

_LINES = [
    {"source_line_id": 1, "quantity": 2, "cwhcode": "01", "cbdefine5": "文本A", "cbdefine6": 1.5, "CBDEFINE7": "2026-01-15"},
    {"source_line_id": 2, "quantity": 1, "cbdefine99": 3},
]


class _DryBridge(FakeBridge):
    """预演时照真实桥（DryRunRun.Body）的形状回：dry_run=true、mode=rollback，docs 带发货单表体的扩展自定义项。"""

    def call(self, path: str, payload: dict) -> dict:
        if payload.get("dry_run") is not True:
            return super().call(path, payload)
        self.calls.append((path, dict(payload)))
        line = {"iDLsID": 41, "cbdefine5": "文本A"}
        doc = {"type": "dispatch", "id": 9, "state": "exists", "head": {"DLID": 9}, "lines": [line]}
        return {"ok": True, "dry_run": True, "mode": "rollback", "route": "vouchers/generate", "type": "dispatch",
                "action": "", "docs": [doc], "warnings": ["number_may_skip"], "message": "预演"}


def test_dispatch_generate_passes_line_pins_through() -> None:
    fake = FakeBridge()
    client = _client(fake)
    body = _auth(type="dispatch", id=9, head={"caddcode": "D01"}, lines=_LINES)
    sent = client.post("/v1/co/vouchers/generate", json=body)
    assert sent.status_code == 200, sent.text
    path, payload = fake.calls[0]
    assert path == "/v1/vouchers/generate"
    assert payload["lines"] == _LINES


def test_dispatch_dry_run_passes_line_pins_through() -> None:
    fake = _DryBridge()
    client = _client(fake)
    body = _auth(type="dispatch", id=9, lines=_LINES, dry_run=True)
    sent = client.post("/v1/co/vouchers/generate", json=body)
    assert sent.status_code == 200, sent.text
    assert fake.calls[0][1]["lines"] == _LINES
    assert fake.calls[0][1]["dry_run"] is True
    out = sent.json()
    assert out["dry_run"] is True and out["mode"] == "rollback"
    assert out["docs"][0]["lines"][0]["cbdefine5"] == "文本A"


_BAD = (
    ({"cbdefine0": "文本A"}, "lines.0.cbdefine0"),
    ({"cbdefine07": "文本A"}, "lines.0.cbdefine07"),
    ({"cbdefine100": "文本A"}, "lines.0.cbdefine100"),
    ({"cbdefine5": True}, "lines.0.cbdefine5"),
    ({"cbdefine5": "长" * 121}, "lines.0.cbdefine5"),
    ({"CBDEFINE6": False}, "lines.0.cbdefine6"),
    ({"cbdefine6": 10**13}, "lines.0.cbdefine6"),
    ({"cbdefine6": 10**400}, "lines.0.cbdefine6"),
)


@pytest.mark.parametrize(("pins", "field"), _BAD)
def test_bad_line_pin_is_rejected_before_bridge(pins: dict, field: str) -> None:
    fake = FakeBridge()
    client = _client(fake)
    lines = [{"source_line_id": 1, "quantity": 1, **pins}]
    denied = client.post("/v1/co/vouchers/generate", json=_auth(type="dispatch", id=9, lines=lines))
    assert denied.status_code == 400, denied.text
    error = denied.json()["error"]
    assert error["code"] == "bad_request"
    # 错误体带字段路径和具体原因，不是笼统的「请求参数无效」。
    assert error["field"] == field
    assert error["message"].startswith("请求参数无效：" + field + " ")
    assert fake.calls == []
    with pytest.raises(ValueError, match=field.replace(".", r"\.")):
        check_dispatch_gen_pins(None, lines)


def test_second_line_error_names_its_line_over_http() -> None:
    fake = FakeBridge()
    lines = [{"source_line_id": 1, "quantity": 1, "cbdefine5": "x"}, {"source_line_id": 2, "quantity": 1, "cbdefine5": "x" * 121}]
    denied = _client(fake).post("/v1/co/vouchers/generate", json=_auth(type="dispatch", id=9, lines=lines))
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["field"] == "lines.1.cbdefine5"
    assert "超长" in denied.json()["error"]["message"]
    assert fake.calls == []


def test_json_null_is_rejected_by_the_schema() -> None:
    # 行的值只能是字符串、数字或布尔（Cell）：JSON null 在模型类型这一层就 400，到不了落库核对字段的检查。
    fake = FakeBridge()
    lines = [{"source_line_id": 1, "quantity": 1, "cbdefine5": None}]
    denied = _client(fake).post("/v1/co/vouchers/generate", json=_auth(type="dispatch", id=9, lines=lines))
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["field"].startswith("lines.0.cbdefine5")
    assert fake.calls == []


def test_huge_quantity_is_400_not_500() -> None:
    fake = FakeBridge()
    lines = [{"source_line_id": 1, "quantity": 10**400}]
    denied = _client(fake).post("/v1/co/vouchers/generate", json=_auth(type="dispatch", id=9, lines=lines))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_second_line_path_and_nulls() -> None:
    with pytest.raises(ValueError, match=r"lines\.1\.cbdefine5 超长"):
        check_dispatch_gen_pins(None, [{"cbdefine5": "x"}, {"cbdefine5": "x" * 121}])
    # 帮助函数里 None 与省略相同（HTTP 请求的 null 已被 Cell 拒绝，见上）；不是扩展自定义项的键不归这里管（桥按白名单判断）。
    check_dispatch_gen_pins(None, [{"cbdefine5": None, "cmemo": 1, "cfree1": True}])
    check_dispatch_gen_pins({"chdefine1": "x"}, None)
    check_line_extra_pins({"cbdefine12": 1.5}, "sources.0.lines.0")


def test_invoice_and_dispatch_share_the_line_rule() -> None:
    for key in ("cbdefine0", "cbdefine07"):
        with pytest.raises(ValueError, match="不是有效的自定义项"):
            check_line_extra_pins({key: "x"}, "lines.0")


@pytest.mark.parametrize("value", [3.0, "3.000", "1E+01", 3, "-0.50"])
def test_numeric_spellings_go_to_bridge_unchanged(value: object) -> None:
    # API 不知道列类型：int / decimal 列的 3.0、"3.000"、"1E+01" 原样转给桥，由桥按列类型改写成纯文本再写
    # （SalePinsRange.Canonical：int 列 "3" / "10"，decimal 列 "3" / "10"），千分位、括号等由桥 400。
    fake = FakeBridge()
    lines = [{"source_line_id": 1, "quantity": 1, "cbdefine6": value, "cbdefine7": value}]
    sent = _client(fake).post("/v1/co/vouchers/generate", json=_auth(type="dispatch", id=9, lines=lines))
    assert sent.status_code == 200, sent.text
    assert fake.calls[0][1]["lines"] == lines
