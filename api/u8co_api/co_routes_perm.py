"""有效权限路由：perm/snapshot（自己的权限，读权限）、perm/evaluate（别的操作员的权限，另有一级）。

两条都只转给桥（桥上是 SQL 读，用登录操作员的口令登录）。perm/evaluate 不是写路由：不走写入分类、写入策略和
只读账套，也不带 caller；权限由 co_access.PERM_EVALUATE 决定（信任项的 perm_evaluate），桥上再按
permEvaluateOperators 拦一次。注册见 co_routes.POST_ROUTES。
"""

from __future__ import annotations

from typing import Any

from u8co_api.co_models import CoAuth
from u8co_api.co_models_perm import (
    EVALUATE_HELP,
    EVALUATE_SUMMARY,
    SNAPSHOT_HELP,
    SNAPSHOT_SUMMARY,
    PermEvaluateIn,
    PermEvaluateOut,
    PermSnapshotOut,
)
from u8co_api.co_service import allow_acc, bridge_payload, call_bridge, ensure_co, remember_co
from u8co_api.co_table import TAG_CO, CoRoute

SNAPSHOT_ACTION = "co:perm/snapshot"
EVALUATE_ACTION = "co:perm/evaluate"


def run_perm_evaluate(request, caller, action: str, bridge_path: str, body: PermEvaluateIn) -> dict[str, Any]:
    """审计、权限（PERM_EVALUATE）、账套白名单，然后原样转给桥。"""
    remember_co(request, action, body)
    ensure_co(request, caller, action)
    allow_acc(request.app.state.settings, caller, body.acc)
    return call_bridge(request, body.acc, bridge_path, bridge_payload(body))


PERM_ROUTES = (
    CoRoute(
        "/v1/co/perm/snapshot",
        "/v1/perm/snapshot",
        CoAuth,
        PermSnapshotOut,
        SNAPSHOT_SUMMARY,
        SNAPSHOT_HELP,
        "coPermSnapshot",
        SNAPSHOT_ACTION,
        TAG_CO,
    ),
    CoRoute(
        "/v1/co/perm/evaluate",
        "/v1/perm/evaluate",
        PermEvaluateIn,
        PermEvaluateOut,
        EVALUATE_SUMMARY,
        EVALUATE_HELP,
        "coPermEvaluate",
        EVALUATE_ACTION,
        TAG_CO,
        runner=run_perm_evaluate,
    ),
)
