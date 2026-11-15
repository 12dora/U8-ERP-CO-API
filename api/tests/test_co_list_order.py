"""order: desc（最新在前）的转发：vouchers/search、reports/order_execution；旧桥兜底；stock/current 的 inv_unit。假桥在本机。"""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth
from tests.test_co_update_close_gen import _spec
from u8co_api.errors import ApiError

_SEARCH = "/v1/co/vouchers/search"
_EXEC = "/v1/co/reports/order_execution"
_STOCK = "/v1/co/stock/current"

# 假桥的数据：（日期，主键），主键升序就是主键顺序；日期与主键的次序不一致，才看得出排序。
_DOCS = [(7, "2026-09-01"), (8, "2026-09-03"), (9, "2026-09-03"), (10, "2026-08-30"), (11, "2026-09-02")]


def _cursor(doc: tuple[int, str]) -> str:
    return f"{doc[1]}|{doc[0]}"


def _desc_page(payload: dict) -> dict:
    docs = sorted(_DOCS, key=lambda doc: (doc[1], doc[0]), reverse=True)
    after = payload.get("after")
    if after is not None:
        date, ident = str(after).split("|")
        docs = [doc for doc in docs if doc[1] < date or (doc[1] == date and doc[0] < int(ident))]
    limit = payload.get("limit", 50)
    page = docs[:limit]
    more = len(docs) > limit
    items = [{"id": doc[0], "code": f"SO{doc[0]:04d}", "doc_date": doc[1]} for doc in page]
    return {"ok": True, "type": payload["type"], "order": "desc", "items": items, "next": _cursor(page[-1]) if more else None}


def _asc_page(payload: dict) -> dict:
    docs = sorted(doc for doc in _DOCS if doc[0] > payload.get("after", 0))
    limit = payload.get("limit", 50)
    page = docs[:limit]
    items = [{"id": doc[0], "code": f"SO{doc[0]:04d}", "doc_date": doc[1]} for doc in page]
    return {"ok": True, "type": payload["type"], "items": items, "next": page[-1][0] if len(docs) > limit else None}


class NewBridge(FakeBridge):
    """认识 order 的桥：desc 给游标分页，其余照旧。"""

    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if path == "/v1/vouchers/search":
            return _desc_page(payload) if payload.get("order") == "desc" else _asc_page(payload)
        if path == "/v1/reports/order_execution":
            return {"ok": True, "type": payload["type"], "only_open": False, "items": [], "next": None, **self._order(payload)}
        return {"ok": True}

    @staticmethod
    def _order(payload: dict) -> dict:
        return {"order": "desc"} if payload.get("order") == "desc" else {}


class OldBridge(FakeBridge):
    """还没升级的桥：不认识 order，回 400「含未知字段」。"""

    message = "含未知字段"

    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if "order" in payload:
            raise ApiError(400, "bad_request", self.message, field="order")
        if path == "/v1/vouchers/search":
            return _asc_page(payload)
        return {"ok": True, "type": payload["type"], "only_open": False, "items": [{"id": 1, "line_id": 1}], "next": "old"}


def _post(path: str, body: dict, fake: FakeBridge):
    return _client(fake).post(path, json=body)


def test_desc_pages_continue_strictly_after_the_last_row() -> None:
    fake = NewBridge()
    seen: list[int] = []
    after = None
    for _ in range(10):
        extra = {} if after is None else {"after": after}
        page = _post(_SEARCH, _auth(type="sale_order", order="desc", limit=2, **extra), fake).json()
        assert page["order"] == "desc"
        assert page["order_applied"] is True
        seen.extend(item["id"] for item in page["items"])
        after = page.get("next")
        if after is None:
            break
    # 日期降序，同日主键降序；每张只出现一次。
    assert seen == [9, 8, 11, 7, 10]
    assert all(call[1]["order"] == "desc" for call in fake.calls)
    assert isinstance(fake.calls[1][1]["after"], str)


def test_old_requests_are_unchanged() -> None:
    fake = NewBridge()
    plain = _post(_SEARCH, _auth(type="sale_order", limit=2, after=7), fake)
    assert plain.status_code == 200, plain.text
    assert "order" not in fake.calls[0][1]
    assert "order_applied" not in plain.json() and "order" not in plain.json()
    assert [item["id"] for item in plain.json()["items"]] == [8, 9]
    assert plain.json()["next"] == 9


def test_explicit_asc_is_not_forwarded_and_reports_applied() -> None:
    fake = NewBridge()
    ok = _post(_SEARCH, _auth(type="sale_order", order="asc", limit=2), fake)
    assert ok.status_code == 200, ok.text
    assert "order" not in fake.calls[0][1]
    assert ok.json()["order_applied"] is True
    assert [item["id"] for item in ok.json()["items"]] == [7, 8]
    assert ok.json()["next"] == 8


@pytest.mark.parametrize("message", ("含未知字段", "不能设置字段"))
def test_old_bridge_is_retried_once_without_order(message: str) -> None:
    fake = OldBridge()
    fake.message = message
    ok = _post(_SEARCH, _auth(type="sale_order", order="desc", limit=2, after=7), fake)
    assert ok.status_code == 200, ok.text
    assert ok.json()["order_applied"] is False
    assert [item["id"] for item in ok.json()["items"]] == [8, 9]
    assert ok.json()["next"] == 9
    assert len(fake.calls) == 2
    assert "order" in fake.calls[0][1] and "order" not in fake.calls[1][1]
    assert fake.calls[1][1]["after"] == 7


def test_order_execution_old_bridge_and_new_bridge() -> None:
    old = OldBridge()
    ok = _post(_EXEC, _auth(type="sale_order", order="desc", after="opaque"), old)
    assert ok.status_code == 200, ok.text
    assert ok.json()["order_applied"] is False
    assert ok.json()["next"] == "old"
    assert "order" not in old.calls[1][1] and old.calls[1][1]["after"] == "opaque"
    new = NewBridge()
    ok = _post(_EXEC, _auth(type="purchase_order", order="desc"), new)
    assert ok.json()["order_applied"] is True and ok.json()["order"] == "desc"
    assert new.calls[0][1]["order"] == "desc"
    plain = _post(_EXEC, _auth(type="purchase_order"), NewBridge())
    assert "order_applied" not in plain.json() and "order" not in plain.json()


@pytest.mark.parametrize(
    "error",
    (
        ApiError(400, "bad_request", "含未知字段", field=None),
        ApiError(400, "bad_request", "含未知字段 order", field="orders"),
        ApiError(400, "bad_request", "含未知字段", field="limit"),
        ApiError(400, "bad_request", "after 游标无效", field="after"),
        ApiError(403, "no_permission", "含未知字段"),
        ApiError(503, "unavailable", "含未知字段 order"),
    ),
)
def test_other_errors_are_not_retried(error: ApiError) -> None:
    fake = FakeBridge()
    fake.error = error
    denied = _post(_SEARCH, _auth(type="sale_order", order="desc"), fake)
    assert denied.status_code == error.status
    assert denied.json()["error"]["code"] == error.code
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    ("path", "extra", "field"),
    (
        (_SEARCH, {"type": "sale_order", "order": "latest"}, "order"),
        (_SEARCH, {"type": "sale_order", "order": "DESC"}, "order"),
        (_SEARCH, {"type": "sale_order", "after": ""}, "after"),
        (_SEARCH, {"type": "sale_order", "after": -1}, "after"),
        (_SEARCH, {"type": "sale_order", "after": "a b"}, "after"),
        (_SEARCH, {"type": "sale_order", "after": "x" * 513}, "after"),
        (_EXEC, {"type": "sale_order", "order": "newest"}, "order"),
        (_EXEC, {"type": "sale_order", "order": 1}, "order"),
    ),
)
def test_bad_order_or_cursor_never_reaches_the_bridge(path: str, extra: dict, field: str) -> None:
    fake = FakeBridge()
    denied = _post(path, _auth(**extra), fake)
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == field
    assert fake.calls == []


def test_search_accepts_an_integer_or_a_cursor_after() -> None:
    fake = FakeBridge()
    assert _post(_SEARCH, _auth(type="sale_order", after=0), fake).status_code == 200
    assert _post(_SEARCH, _auth(type="sale_order", order="desc", after="MjAyNg"), fake).status_code == 200
    assert fake.calls[0][1]["after"] == 0
    assert fake.calls[1][1]["after"] == "MjAyNg"


@pytest.mark.parametrize("order", (None, "asc"))
def test_asc_after_stays_an_integer_even_when_written_as_text(order: str | None) -> None:
    # 以前 after 是 int 字段，"123" 会被收成 123；现在照旧（不转成字符串给桥）。
    extra = {} if order is None else {"order": order}
    fake = NewBridge()
    ok = _post(_SEARCH, _auth(type="sale_order", after="7", limit=2, **extra), fake)
    assert ok.status_code == 200, ok.text
    assert fake.calls[0][1]["after"] == 7 and isinstance(fake.calls[0][1]["after"], int)
    assert [item["id"] for item in ok.json()["items"]] == [8, 9]


@pytest.mark.parametrize("order", (None, "asc"))
@pytest.mark.parametrize("after", ("MjAyNg", "7x", "-1", "2147483648", ""))
def test_asc_after_that_is_not_an_id_is_rejected(order: str | None, after: str) -> None:
    extra = {} if order is None else {"order": order}
    fake = FakeBridge()
    denied = _post(_SEARCH, _auth(type="sale_order", after=after, **extra), fake)
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == "after"
    assert fake.calls == []


def test_stock_current_keeps_the_unit_name() -> None:
    class Stock(FakeBridge):
        def call(self, path: str, payload: dict) -> dict:
            self.calls.append((path, dict(payload)))
            return {"ok": True, "items": [{"id": 1, "inv_code": "A", "inv_unit": "千克", "qty": "2"}], "next": None}

    ok = _post(_STOCK, _auth(), Stock())
    assert ok.status_code == 200, ok.text
    assert ok.json()["items"][0]["inv_unit"] == "千克"
    doc = _spec()["components"]["schemas"]["CoStockOut"]["properties"]["items"]["description"]
    assert "inv_unit" in doc


def test_openapi_declares_order_and_order_applied() -> None:
    schemas = _spec()["components"]["schemas"]
    for name, out in (("VoucherSearchIn", "VoucherSearchOut"), ("ReportOrderExecIn", "ReportOrderExecOut")):
        assert "order" in schemas[name]["properties"]
        assert "order_applied" in schemas[out]["properties"]
        assert "order" in schemas[out]["properties"]
