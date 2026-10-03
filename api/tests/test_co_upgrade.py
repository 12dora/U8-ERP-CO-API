"""复审修正：经营管理缓存认操作员权限指纹、409 消息折叠编码清单。不访问网络。"""

from __future__ import annotations

from tests.test_co_mgmt import _B_PNL, _PNL, MgmtFake, _meta, _mgmt_client, _pnl
from u8co_api import co_mgmt_cache
from u8co_api.co_bridge import to_api_error


# ---- 1. 经营管理缓存：权限指纹 ----


def test_meta_without_perm_fingerprint_caches_only_60_seconds() -> None:
    closed = _meta(closed=True)
    assert co_mgmt_cache.ttl_for((1, 2), [closed]) == co_mgmt_cache.TTL_CLOSED
    old_bridge = {key: value for key, value in closed.items() if key != "perm_fingerprint"}
    assert co_mgmt_cache.ttl_for((1, 2), [old_bridge]) == co_mgmt_cache.TTL_OPEN
    assert co_mgmt_cache.ttl_for((1, 2), [closed, old_bridge]) == co_mgmt_cache.TTL_OPEN
    assert co_mgmt_cache.ttl_for((1, 2), [dict(closed, perm_fingerprint=" ")]) == co_mgmt_cache.TTL_OPEN
    assert co_mgmt_cache.ttl_for((1, 2), [dict(closed, perm_fingerprint=7)]) == co_mgmt_cache.TTL_OPEN


def test_changed_perm_fingerprint_misses_the_cache() -> None:
    fake = MgmtFake()
    fake.meta["801"] = _meta(closed=True)
    client = _mgmt_client(fake)
    assert client.post(_PNL, json=_pnl(("801",))).json()["cache"]["hit"] is False
    assert client.post(_PNL, json=_pnl(("801",))).json()["cache"]["hit"] is True
    # 同一调用方、同一口令、同样的数据水位，只是操作员权限变了：不能命中。
    fake.meta["801"] = dict(_meta(closed=True), perm_fingerprint="fp-narrowed")
    assert client.post(_PNL, json=_pnl(("801",))).json()["cache"]["hit"] is False
    assert fake.count(_B_PNL) == 2


# ---- 2. 409 消息同样折叠编码清单 ----


def test_conflicts_fold_long_code_lists() -> None:
    codes = "、".join(f"SO{n:05d}" for n in range(6))
    error = to_api_error(409, {"code": "u8_rejected", "message": f"单据已被以下单据引用：{codes}"})
    assert error.status == 409
    assert "SO00001" not in error.message and "SO00000 等 6 项" in error.message


def test_conflicts_keep_lists_under_five_codes() -> None:
    message = "单据已被以下单据引用：SO00001、SO00002、SO00003、SO00004"
    assert to_api_error(409, {"code": "u8_rejected", "message": message}).message == message
