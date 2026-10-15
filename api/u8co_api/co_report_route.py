"""只读报表路由 /v1/co/reports/<name> 的登记行：各报表路由模块共用。"""

from __future__ import annotations

from u8co_api.co_table import CoRoute

TAG_REPORT = "报表"


def report(name: str, pair: tuple[type, type], summary: str, text: str, runner=None) -> CoRoute:
    words = "".join(part.title() for part in name.split("_"))
    return CoRoute(
        f"/v1/co/reports/{name}",
        f"/v1/reports/{name}",
        pair[0],
        pair[1],
        summary,
        text,
        f"coReport{words}",
        f"co:reports/{name}",
        TAG_REPORT,
        runner=runner,
    )
