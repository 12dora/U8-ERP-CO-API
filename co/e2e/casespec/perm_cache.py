"""桥权限缓存秒数（健康检查 perm_cache_s）。

字段权限用例改权限表后不等缓存过期，只在缓存关闭（permCacheSeconds 为 0）的通道上运行；
第 0 波的健康检查已读到的值存在 env.bag 里，没读过时现取一次健康检查。取不到、缺这个字段（旧版桥）或不是 0 都算未关闭。
"""

from __future__ import annotations

from co.client.u8co_client import U8CoError
from co.e2e.casespec.env import Env

KEY = "perm_cache_s"
NOT_OFF = "桥权限缓存未关闭（permCacheSeconds≠0）"


def remember(env: Env, payload: object) -> None:
    """记下健康检查里的 perm_cache_s：整数存十进制文本，缺字段或类型不对存空串。"""
    value = payload.get(KEY) if isinstance(payload, dict) else None
    env.bag[KEY] = str(value) if type(value) is int else ""


def is_off(env: Env) -> bool:
    """桥权限缓存是否已关闭。"""
    if KEY not in env.bag:
        try:
            payload = env.client.health()
        except (U8CoError, OSError, ValueError):
            return False
        remember(env, payload)
    return env.bag[KEY] == "0"
