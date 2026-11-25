"""在快照窗口里挑单据。只跑本次用例用得到的查询。"""

from __future__ import annotations

import re

from co.e2e.casespec.assertutil import sql_cells
from co.e2e.casespec.catalog import build_cases
from co.e2e.casespec.env import Env
from co.e2e.casespec.wiring import EDIT_PACKS
from co.e2e.casespec.select import filter_cases
from co.e2e.casespec.sql_load import LOAD_SOURCES, newest_sql
from co.e2e.casespec.arap_gen_sql import CORE_IDS, CORE_PACKS, CORE_TEXTS
from co.e2e.casespec.returns_misc_sql import CUSTOM_PACKS, CUSTOM_TEXTS
from co.e2e.casespec.sql_qm import qm04_sql, qm_state_sql
from co.e2e.casespec.sql_sa import (
    dispatch_down_sql,
    dispatch_open_sql,
    dispatch_verified_sql,
    sale_master_sql,
    sale_open_sql,
)
from co.e2e.casespec.flow_pick_sql import (
    invoice_src_sql,
    po_lines_sql,
    po_master_sql,
    po_recv_sql,
    sale_invoice_ar_sql,
    sale_verified_sql,
    so_ship_sql,
    transfer_master_sql,
)
from co.e2e.casespec.sql_st import (
    arrival_open_sql,
    other_in_master_sql,
    other_out_master_sql,
    other_out_done_sql,
    other_out_open_sql,
    po_open_sql,
    voucher_prefix_sql,
)
from co.e2e.casespec.sqlexec import run_guarded

_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
# 与 edit_close_gen_bodies._TOKEN、挑单 SQL 的 NOT LIKE '%[^0-9A-Za-z._-]%' 同一字符集。超长编码不当成正文，挑单跳过。
_CODE = re.compile(r"^[0-9A-Za-z._-]{1,12}\Z")
# 挑单 SQL 执行出错的键（逗号分隔）记在 bag 的这个键下：缺单据和查询出错要分开报（sparse_samples 只放过前者）。
PICK_FAILED = "pick_failed"
_PACKS = (
    ("so_ship", ("so_ship", "so_ship_line", "so_ship_wh"), (True, True, False), so_ship_sql),
    ("po_recv", ("po_recv", "po_recv_line", "po_recv_wh"), (True, True, False), po_recv_sql),
    ("inv_src", ("inv_src", "inv_src_line"), (True, True), invoice_src_sql),
) + CORE_PACKS + CUSTOM_PACKS + EDIT_PACKS


def need_keys(names: tuple[str, ...] | None) -> set[str] | None:
    if names is None:
        return None
    keys: set[str] = set()
    for spec in filter_cases(build_cases({}), names):
        keys.update(spec.needs)
    return keys


def pick_keys() -> tuple[str, ...]:
    keys = [key for key, _sql in _id_queries()]
    keys.extend(key for key, _sql in _text_queries())
    keys.extend(["qm04_a", "qm04_b", "dispatch_id"])
    keys.extend(item[0] for item in LOAD_SOURCES)
    keys.extend(["so_ship", "so_ship_line", "so_ship_wh", "po_recv", "po_recv_line", "po_recv_wh"])
    keys.extend(["inv_src", "inv_src_line"])
    keys.extend(name for _lead, names, _flags, _sql in CORE_PACKS + CUSTOM_PACKS + EDIT_PACKS for name in names)
    return tuple(keys)


def _want(wanted: set[str] | None, key: str) -> bool:
    if wanted is None:
        return True
    return key in wanted


def _cells(env: Env, sql: str, bag: dict[str, str], keys: tuple[str, ...]) -> list[str]:
    try:
        return sql_cells(run_guarded(env, sql, "pick"))
    except RuntimeError as exc:
        env.ctx.fail(str(exc)[:200])
        failed = [item for item in bag.get(PICK_FAILED, "").split(",") if item]
        failed.extend(key for key in keys if key not in failed)
        bag[PICK_FAILED] = ",".join(failed)
        return []


def pick_failed(bag: dict[str, str]) -> set[str]:
    return {item for item in bag.get(PICK_FAILED, "").split(",") if item}


def _put_id(bag: dict[str, str], key: str, cells: list[str]) -> None:
    if cells and _ID.fullmatch(cells[0]):
        bag[key] = cells[0]


def _put_text(bag: dict[str, str], key: str, cells: list[str]) -> None:
    if cells and cells[0] != "":
        bag[key] = cells[0]


def _id_queries() -> tuple[tuple[str, str], ...]:
    return (
        ("sale_id", sale_open_sql()),
        ("dispatch_downstream", dispatch_down_sql()),
        ("qm06_id", qm_state_sql("QM06")),
        ("qm03_id", qm_state_sql("QM03")),
        ("other_out_open", other_out_open_sql()),
        ("other_out_done", other_out_done_sql()),
        ("po_open", po_open_sql()),
        ("arrival_open", arrival_open_sql()),
        ("sale_verified", sale_verified_sql()),
        ("po_lines", po_lines_sql()),
        ("sale_invoice_ar", sale_invoice_ar_sql()),
    ) + tuple((key, sql()) for key, sql in CORE_IDS)


def _text_queries() -> tuple[tuple[str, str], ...]:
    return (
        ("other_in_master", other_in_master_sql()),
        ("other_out_master", other_out_master_sql()),
        ("sale_master", sale_master_sql()),
        ("vn_prefix", voucher_prefix_sql()),
        ("po_master", po_master_sql()),
        ("transfer_master", transfer_master_sql()),
    ) + tuple((key, sql()) for key, sql in CORE_TEXTS + CUSTOM_TEXTS)


def _fill_named(env: Env, bag: dict[str, str], wanted: set[str] | None, rows: tuple, put) -> None:
    for key, sql in rows:
        if _want(wanted, key):
            put(bag, key, _cells(env, sql, bag, (key,)))


def _fill_qm(env: Env, bag: dict[str, str], wanted: set[str] | None) -> None:
    if not (_want(wanted, "qm04_a") or _want(wanted, "qm04_b")):
        return
    cells = [item for item in _cells(env, qm04_sql(), bag, ("qm04_a", "qm04_b")) if _ID.fullmatch(item)]
    if cells:
        bag["qm04_a"] = cells[0]
    if len(cells) > 1:
        bag["qm04_b"] = cells[1]


def _fill_dispatch(env: Env, bag: dict[str, str], wanted: set[str] | None) -> None:
    if not _want(wanted, "dispatch_id"):
        return
    opened = [item for item in _cells(env, dispatch_open_sql(), bag, ("dispatch_id",)) if _ID.fullmatch(item)]
    if opened:
        bag["dispatch_id"] = opened[0]
        bag["dispatch_mode"] = "verify-first"
        return
    verified = [item for item in _cells(env, dispatch_verified_sql(), bag, ("dispatch_id",)) if _ID.fullmatch(item)]
    if verified:
        bag["dispatch_id"] = verified[0]
        bag["dispatch_mode"] = "unverify-first"


def _ok_part(part: str, must_id: bool) -> bool:
    if part == "":
        return False
    if must_id:
        return _ID.fullmatch(part) is not None
    return _CODE.fullmatch(part) is not None


def _fill_packs(env: Env, bag: dict[str, str], wanted: set[str] | None) -> None:
    for _lead, keys, flags, sql in _PACKS:
        if not any(_want(wanted, key) for key in keys):
            continue
        cells = _cells(env, sql(), bag, keys)
        if not cells:
            continue
        parts = [item.strip() for item in cells[0].split("|")]
        paired = list(zip(parts, flags))
        if len(parts) != len(keys) or not all(_ok_part(part, flag) for part, flag in paired):
            continue
        for key, part in zip(keys, parts):
            bag[key] = part


def _fill_loads(env: Env, bag: dict[str, str], wanted: set[str] | None) -> None:
    for key, table, col, where in LOAD_SOURCES:
        if _want(wanted, key):
            _put_id(bag, key, _cells(env, newest_sql(table, col, where), bag, (key,)))


def fill_bag(env: Env, names: tuple[str, ...] | None) -> dict[str, str]:
    wanted = need_keys(names)
    bag: dict[str, str] = {}
    _fill_named(env, bag, wanted, _id_queries(), _put_id)
    _fill_named(env, bag, wanted, _text_queries(), _put_text)
    _fill_qm(env, bag, wanted)
    _fill_dispatch(env, bag, wanted)
    _fill_loads(env, bag, wanted)
    _fill_packs(env, bag, wanted)
    return bag
