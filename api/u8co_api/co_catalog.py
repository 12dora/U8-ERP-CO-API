"""/v1/co 路由目录（MCP 服务打包的 mcp/u8co_mcp/routes.json）：{route, access, summary}，按 route 排序。

route 是去掉 /v1/co/ 的 API 路径（含 GET 的 health、meta），access 取自 co_access。
多账套路由（FANOUT_ACTIONS）不进目录：MCP 服务只注入一个账套的登录，这些路由以后由专门的 MCP 工具提供。
读、写以外级别的路由（perm/evaluate）也不进目录。重新生成：
    cd api && uv run --frozen python -m u8co_api.co_catalog > ../mcp/u8co_mcp/routes.json
"""

from __future__ import annotations

import json
import sys

from u8co_api.co_access import READ, WRITE, access_of
from u8co_api.co_ic_core import FANOUT_ACTIONS
from u8co_api.co_meta import META_ACTION
from u8co_api.co_routes import POST_ROUTES

PREFIX = "/v1/co/"
# GET 路由不在 POST 路由表里：(route, action, summary)。
GET_ROUTES = (
    ("health", "co:health", "CO 桥健康检查"),
    ("meta", META_ACTION, "字段元数据"),
)


def _in_mcp(action: str) -> bool:
    return action not in FANOUT_ACTIONS and access_of(action) in (READ, WRITE)


def mcp_excluded() -> set[str]:
    """注册了但不进 MCP 目录的路由名（多账套路由，以及读、写以外级别的路由）。"""
    return {route.path.removeprefix(PREFIX) for route in POST_ROUTES if not _in_mcp(route.action)}


def catalog() -> list[dict[str, str]]:
    entries = [
        {"route": route.path.removeprefix(PREFIX), "access": access_of(route.action), "summary": route.summary}
        for route in POST_ROUTES
        if _in_mcp(route.action)
    ]
    entries += [{"route": name, "access": access_of(action), "summary": text} for name, action, text in GET_ROUTES]
    return sorted(entries, key=lambda item: item["route"])


def render() -> str:
    # 一行一条，便于审阅 diff。
    lines = ",\n".join(json.dumps(item, ensure_ascii=False) for item in catalog())
    return "[\n" + lines + "\n]\n"


def main() -> None:
    sys.stdout.write(render())


if __name__ == "__main__":
    main()
