"""MCP 服务打包的路由目录 mcp/u8co_mcp/routes.json 必须与 API 注册的 /v1/co 路由和读写分级一致。

多账套路由（FANOUT_ACTIONS）只在 API 注册，不进 MCP 目录。目录变了用 `python -m u8co_api.co_catalog > ../mcp/u8co_mcp/routes.json`（在 api 目录下）重新生成。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.routing import APIRoute
from u8co_api.co_access import ACCESS
from u8co_api.co_catalog import PREFIX, catalog, mcp_excluded
from u8co_api.main import AppDeps, create_app
from u8co_api.openapi_export import offline_settings

_ROUTES_JSON = Path(__file__).resolve().parents[2] / "mcp" / "u8co_mcp" / "routes.json"


def _registered(*, with_fanout: bool = False) -> set[tuple[str, str]]:
    skip = set() if with_fanout else mcp_excluded()
    app = create_app(AppDeps(settings=offline_settings()))
    found: set[tuple[str, str]] = set()
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith(PREFIX) and route.path.removeprefix(PREFIX) not in skip:
            action = "co:" + route.path.removeprefix(PREFIX)
            found.add((route.path.removeprefix(PREFIX), ACCESS[action]))
    return found


def _bundled() -> list[dict]:
    return json.loads(_ROUTES_JSON.read_text(encoding="utf-8"))


_REGEN = "routes.json 过期：在 api 目录下运行 python -m u8co_api.co_catalog > ../mcp/u8co_mcp/routes.json 重新生成"


def test_routes_json_matches_the_registered_routes_and_access() -> None:
    entries = _bundled()
    pairs = {(item["route"], item["access"]) for item in entries}
    assert len(pairs) == len(entries)
    assert pairs == _registered(), _REGEN


def test_routes_json_equals_the_catalog_including_summaries() -> None:
    bundled = [(item["route"], item["access"], item["summary"]) for item in _bundled()]
    generated = [(item["route"], item["access"], item["summary"]) for item in catalog()]
    assert bundled == generated, _REGEN


def test_routes_json_entries_are_well_formed_and_sorted() -> None:
    entries = _bundled()
    for item in entries:
        assert set(item) == {"route", "access", "summary"}
        assert item["access"] in ("read", "write")
        assert item["summary"]
    assert [item["route"] for item in entries] == sorted(item["route"] for item in entries)


def test_catalog_generator_matches_the_registered_routes() -> None:
    assert {(item["route"], item["access"]) for item in catalog()} == _registered()
    assert {"health", "meta", "archives/resolve", "idempotency/get"} <= {item["route"] for item in catalog()}


def test_fanout_routes_are_registered_but_not_in_the_mcp_catalog() -> None:
    fanout = {
        "reports/intercompany_match",
        "reports/aggregate",
        "reports/consolidation",
        "intercompany/generate_buyer",
    }
    # 经营管理路由（mgmt/*）同样按账套多次调桥，由 MCP 的专门工具提供。
    # perm/evaluate（读、写以外的级别）同样不进目录。
    assert {route for route in mcp_excluded() if not route.startswith("mgmt/")} == fanout | {"perm/evaluate"}
    assert {"mgmt/meta", "mgmt/pnl"} <= mcp_excluded()
    assert fanout <= {route for route, _access in _registered(with_fanout=True)}
    assert fanout.isdisjoint(item["route"] for item in catalog())
    assert fanout.isdisjoint(item["route"] for item in _bundled())
