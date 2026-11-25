"""写预演用例的步骤构造（纯数据，不连服务器、不引用 steps）。预演步骤的参数见 dry_judge 模块说明。"""

from __future__ import annotations

from co.e2e.casespec.model import Step

CREATE = "/v1/vouchers/create"
UPDATE = "/v1/vouchers/update"
DELETE = "/v1/vouchers/delete"
VERIFY = "/v1/vouchers/verify"
CLOSE = "/v1/vouchers/close"
LOCK = "/v1/vouchers/lock"
GEN = "/v1/vouchers/generate"
LOAD = "/v1/vouchers/load"
VOUCHER_ROUTES = (CREATE, UPDATE, DELETE, VERIFY, CLOSE, LOCK, GEN, LOAD)
_OPTS = {"builder": "@b", "mode": "@m", "expect": "@x", "watch": "@w", "detail": "@d"}
_REFUSE_OPTS = {"builder": "@b", "text": "@t", "field": "@f", "dry": "@dry", "key": "@key"}


def ref(kind: str, *extra: tuple[str, str]) -> tuple[tuple[str, str], ...]:
    return (("type", kind), ("id", "$id")) + extra


def _opts(table: dict[str, str], opts: dict[str, str]) -> tuple[tuple[str, str], ...]:
    unknown = set(opts) - set(table)
    if unknown:
        raise ValueError("未知参数：" + ",".join(sorted(unknown)))
    return tuple((table[key], value) for key, value in opts.items())


def dry(route: str, fields: tuple = (), id_from: str = "none", **opts: str) -> Step:
    """预演一步。opts：builder、mode（缺省 rollback）、expect（缺省 any）、watch、detail。"""
    opts.setdefault("mode", "rollback")
    return Step("custom", route, tuple(fields) + _opts(_OPTS, opts), sql_name="dry_op", id_from=id_from)


def dry_doc(route: str, kind: str, id_from: str, *extra: tuple[str, str], **opts: str) -> Step:
    """对已有单据（$id）的预演：docs 里要有它（state exists，删除时 deleted），预演前后读取相同。"""
    expect = "deleted" if route == DELETE else "exists"
    opts.setdefault("expect", expect)
    opts.setdefault("watch", kind + ":$id")
    return dry(route, ref(kind, *extra), id_from, **opts)


def dry_new(route: str, kind: str, builder: str, id_from: str = "none", **opts: str) -> Step:
    """新建或生单的预演：docs 里要有新单据，预演之后它读取 404，该类型表头行数和最大主键不变。"""
    opts.setdefault("expect", "new")
    return dry(route, (("type", kind),), id_from, builder=builder, **opts)


def refuse(route: str, fields: tuple, expect: tuple[int, str] = (400, "bad_request"), **opts: str) -> Step:
    """要求被拒绝的一步。opts：builder、text（message 要含）、field（结构化错误的 field）、dry、key（=1 时加），
    id_from（$id 取哪张单据，缺省 none）。"""
    id_from = opts.pop("id_from", "none")
    return Step("custom", route, tuple(fields) + _opts(_REFUSE_OPTS, opts), expect[0], expect[1], (), "dry_refuse", id_from)


# ---- 预演万一真写了：记下可能漏出去的东西，失败时 dry_cleanup 按它撤回（键按用例名分开，串行共用 bag 也不串） ----


def leak_key(case: str) -> str:
    """预演给出的新单据（<类型>:<id>，逗号分隔）。"""
    return "dry_leak:" + case


def max_key(case: str, kind: str) -> str:
    """该类型第一次新建 / 生单预演之前表头的最大主键：失败时比它大的都是本用例留下的。"""
    return f"dry_max:{case}:{kind}"


def lock_key(case: str) -> str:
    """锁定预演的单据（<类型>:<id>）：失败时解锁。"""
    return "dry_lock:" + case


def hx_key(case: str) -> str:
    """核销预演 detail 给出的核销号（逗号分隔）：失败时取消。"""
    return "dry_hx:" + case


def add_item(bag: dict[str, str], key: str, item: str) -> None:
    items = [part for part in bag.get(key, "").split(",") if part]
    if item not in items:
        items.append(item)
    bag[key] = ",".join(items)
