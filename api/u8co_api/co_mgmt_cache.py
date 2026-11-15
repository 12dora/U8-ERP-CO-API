"""经营管理查询的进程内缓存：线程安全的 LRU（最多 256 项），键含各账套的数据水位（mgmt/meta）。

所选期间在每个账套都已总账结账时缓存 6 小时，否则 60 秒。有账套读取失败的结果不缓存。
水位取 mgmt/meta 的整个响应（去掉 ok 和计时类字段）的摘要：任何结账、记账或新单据都会换一个键。

隔离：键里还有调用方（信任项:客户端:sub）和每个账套「账套、操作员、口令、年度、日期」的 HMAC（进程内随机密钥，
不落盘、不进日志）。口令不对的请求算不出同一个键，读不到别人的缓存；mgmt/meta 本身也要在这次请求里由桥登录成功，
有一个账套失败就既不读也不存缓存。
水位：桥的 mgmt/meta 不再给借贷方合计（gl_debit / gl_credit），改给不含金额的内容校验和（gl_content_checksum）。
有账套的 meta 两者都没有（改金额可能不换水位）时只缓存 60 秒。旧桥的 gl_debit / gl_credit 不转给调用方（public_meta）。
权限：桥的 mgmt/meta 带登录操作员的权限指纹（perm_fingerprint，同 perm/snapshot），水位摘要整个 meta，
权限一变就换键；旧桥的 meta 没有这个键时只缓存 60 秒（与桥的权限快照时效一致），收窄或收回权限最迟 60 秒生效。
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

MAX_ENTRIES = 256
TTL_CLOSED = 6 * 3600
TTL_OPEN = 60
# meta 里不进水位的字段：与数据无关、每次都可能不同。
_VOLATILE = frozenset(("ok", "took_ms", "elapsed_ms", "generated_at", "now"))
# 能反映金额变化的水位：新版桥的内容校验和（及同类不含金额的新键），或旧桥的借贷方合计。
_AMOUNT_MARK = re.compile(r"^gl_(?:content|amount|money)\w*\Z")
_LEGACY_MONEY = frozenset(("gl_debit", "gl_credit"))
# 操作员权限指纹（新版桥）：没有它时权限变化不换键，只能缓存 TTL_OPEN。
_PERM_MARK = "perm_fingerprint"
# 每个进程一把随机密钥，只用来算口令的 HMAC；重启后旧缓存键自然作废。
_CRED_KEY = secrets.token_bytes(32)


def _clock() -> float:
    return time.monotonic()


class MgmtCache:
    def __init__(self, size: int = MAX_ENTRIES) -> None:
        self._size = size
        self._lock = threading.Lock()
        self._items: OrderedDict[str, tuple[float, float, dict]] = OrderedDict()

    def get(self, key: str) -> tuple[dict, int] | None:
        """命中时返回（结果的副本，已缓存秒数）；过期的顺手删掉。"""
        now = _clock()
        with self._lock:
            found = self._items.get(key)
            if found is None:
                return None
            stored, ttl, value = found
            if now - stored >= ttl:
                del self._items[key]
                return None
            self._items.move_to_end(key)
        return copy.deepcopy(value), int(now - stored)

    def put(self, key: str, value: dict, ttl: float) -> None:
        with self._lock:
            self._items[key] = (_clock(), ttl, copy.deepcopy(value))
            self._items.move_to_end(key)
            while len(self._items) > self._size:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


# 没有应用对象时（例如直接调用 cached）用的缓存；路由用各自应用上的缓存（cache_of），测试之间互不影响。
CACHE = MgmtCache()
_CREATE = threading.Lock()


def cache_of(app) -> MgmtCache:
    """应用自己的缓存（app.state.mgmt_cache），第一次用时建立。"""
    with _CREATE:
        found = getattr(app.state, "mgmt_cache", None)
        if not isinstance(found, MgmtCache):
            found = MgmtCache()
            app.state.mgmt_cache = found
        return found


def watermark(meta: dict) -> str:
    stable = {key: value for key, value in meta.items() if key not in _VOLATILE}
    text = json.dumps(stable, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def credential_mark(*parts: str | None) -> str:
    """账套、操作员、口令、年度、日期的 HMAC-SHA256（十六进制）。只进缓存键，不进日志和响应。"""
    text = "\x1f".join("" if part is None else str(part) for part in parts)
    return hmac.new(_CRED_KEY, text.encode("utf-8"), hashlib.sha256).hexdigest()


def has_amount_mark(meta: dict) -> bool:
    """meta 的 watermarks 里有能反映改金额的键（新桥的内容校验和或旧桥的借贷方合计）。"""
    marks = meta.get("watermarks")
    if not isinstance(marks, dict):
        return False
    return any(_AMOUNT_MARK.fullmatch(str(key)) or key in _LEGACY_MONEY for key in marks)


def has_perm_mark(meta: dict) -> bool:
    """meta 带非空的操作员权限指纹（新版桥）。"""
    mark = meta.get(_PERM_MARK)
    return isinstance(mark, str) and bool(mark.strip())


def _long_lived(meta: dict) -> bool:
    return has_amount_mark(meta) and has_perm_mark(meta)


def public_meta(meta: dict) -> dict:
    """转给调用方的 meta：去掉旧桥水位里的借贷方合计（金额不随 mgmt/meta 外传）。"""
    marks = meta.get("watermarks")
    if not isinstance(marks, dict) or not _LEGACY_MONEY & set(marks):
        return meta
    out = dict(meta)
    out["watermarks"] = {key: value for key, value in marks.items() if key not in _LEGACY_MONEY}
    return out


def period_entry(meta: dict, period: int) -> dict | None:
    for item in meta.get("periods") or []:
        if isinstance(item, dict) and item.get("period") == period:
            return item
    return None


def gl_closed(meta: dict, period: int) -> bool:
    """该期间总账是否已结账：periods[] 里 closed.GL（也认小写 gl）为 true。"""
    item = period_entry(meta, period)
    if item is None:
        return False
    closed = item.get("closed")
    if isinstance(closed, dict):
        return closed.get("GL") is True or closed.get("gl") is True
    return item.get("gl_closed") is True


def ttl_for(periods: tuple[int, int] | None, metas: list[dict]) -> int:
    # 缺金额水位或权限指纹的 meta（旧桥）一律按 TTL_OPEN：改金额、收权限都可能不换键。
    if periods is None or not metas or not all(_long_lived(meta) for meta in metas):
        return TTL_OPEN
    low, high = periods
    every = all(gl_closed(meta, period) for meta in metas for period in range(low, high + 1))
    return TTL_CLOSED if every else TTL_OPEN


def cache_key(key_parts: dict[str, Any], accounts_meta: dict[str, dict | None]) -> str | None:
    """有账套没读到 meta 时返回 None（不缓存）。"""
    if not accounts_meta or any(meta is None for meta in accounts_meta.values()):
        return None
    marks = {acc: watermark(meta or {}) for acc, meta in sorted(accounts_meta.items())}
    text = json.dumps({"parts": key_parts, "marks": marks}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cached(
    key_parts: dict[str, Any],
    accounts_meta: dict[str, dict | None],
    compute_fn: Callable[[], dict],
    cache: MgmtCache | None = None,
) -> tuple[dict, dict]:
    """按 key_parts + 各账套水位取缓存，没有就算 compute_fn() 并按时效存入。返回（结果, {hit, age_s}）。

    key_parts 是可 JSON 化的字典，必须含 periods（[起始, 截止] 或 None，决定时效），其余键（路由、账套=操作员、
    参数）原样进键；路由还放进 who（调用方）和 creds（各账套登录的 credential_mark），两者缺一不读也不存。
    accounts_meta 是每个请求账套 → meta 响应体（读取失败为 None，此时不读也不存缓存）。
    compute_fn 的结果里 complete 不为 true 时不存（部分失败的结果不缓存）。cache 缺省为模块级的 CACHE。"""
    store = cache if cache is not None else CACHE
    key = cache_key(key_parts, accounts_meta) if _isolated(key_parts) else None
    if key is not None:
        hit = store.get(key)
        if hit is not None:
            return hit[0], {"hit": True, "age_s": hit[1]}
    value = compute_fn()
    if key is not None and value.get("complete") is True:
        periods = key_parts.get("periods")
        span = (periods[0], periods[1]) if isinstance(periods, (list, tuple)) else None
        metas = [meta for meta in accounts_meta.values() if meta is not None]
        store.put(key, value, ttl_for(span, metas))
    return value, {"hit": False, "age_s": 0}


def _isolated(key_parts: dict[str, Any]) -> bool:
    # 键里没有调用方或口令摘要时一律不走缓存（宁可多查一次，也不跨人、跨口令复用）。
    return bool(key_parts.get("who")) and bool(key_parts.get("creds"))
