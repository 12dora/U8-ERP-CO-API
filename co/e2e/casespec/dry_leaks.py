"""写预演用例失败时，先撤回「预演万一真写了」漏出去的东西，再做各用例自己的补偿（dry_cleanup 串起来）。

依据 dry_ops / dry_fin_run 在发预演前后记进 bag 的键（键名见 dry_steps，按用例名分开）：
- lock_key：锁定预演的单据 → 解锁（没锁时 409 属正常）。
- leak_key 里预演给出的新单据，以及 max_key 记下的最大主键之后多出来的同类型单据（新建 / 生单的类型在运行时由
  new:<类型> 标签互斥，窗口里多出来的只可能是本用例的）→ 仍能读取的，先弃审再删除，下游类型在前。
- hx_key 里预演给出的核销号 → 核销记录查询里查得到、且收付款单是本用例挑的那张时才取消（并行时回滚掉的号可能被别人重用）。
补偿也失败时留给快照还原。
"""

from __future__ import annotations

from co.client.u8co_client import U8CoError
from co.e2e.casespec.assertutil import sql_cells
from co.e2e.casespec.env import Env
from co.e2e.casespec.writeoff import cancel_one
from co.e2e.casespec.dry_sql import ids_after_sql
from co.e2e.casespec.dry_steps import hx_key, leak_key, lock_key

# 下游在前：先删生成出来的出入库单、发票，再删它们的来源。
_ORDER = (
    "sale_out", "purchase_in", "product_in", "material_out", "sale_invoice", "purchase_invoice",
    "sale_return", "purchase_return", "qm_product_check", "qm_incoming_check", "qm_product_inspect",
    "qm_incoming_inspect", "other_in", "other_out", "transfer", "dispatch", "arrival",
)
# 核销预演的用例 → 它挑的收付款单 id 的 bag 键。
RECEIPT_KEYS = {"dry-ar-writeoff": "hx_rc"}
_REPORT = "/v1/reports/arap_writeoffs"


def _call(env: Env, route: str, extra: dict) -> tuple[int, dict]:
    # steps 经 wiring 引用各模块的构造器，放到调用时再引入，避免循环引用。
    from co.e2e.casespec.steps import auth_fields, invoke

    fields = auth_fields(env)
    fields.update(extra)
    try:
        return invoke(env.client, route, fields)
    except (U8CoError, OSError, RuntimeError, ValueError) as exc:
        return 0, {"code": "error", "message": str(exc)[:60]}


def _pairs(text: str) -> list[tuple[str, int]]:
    found = []
    for item in (text or "").split(","):
        kind, _sep, raw = item.partition(":")
        if kind and raw.isdigit():
            found.append((kind, int(raw)))
    return found


def _unlock(env: Env, case: str) -> list[str]:
    notes = []
    for kind, doc_id in _pairs(env.bag.get(lock_key(case), "")):
        status, _payload = _call(env, "/v1/vouchers/lock", {"type": kind, "id": doc_id, "action": "unlock"})
        notes.append(f"解锁 {kind} {doc_id} {status}")
    return notes


def _swept(env: Env, case: str) -> list[tuple[str, int]]:
    # 该用例记下的每个类型：最大主键之后多出来的单据。
    from co.e2e.casespec.sqlexec import run_guarded

    prefix = f"dry_max:{case}:"
    found = []
    for key, max_id in sorted(env.bag.items()):
        if key.startswith(prefix):
            kind = key[len(prefix) :]
            cells = sql_cells(run_guarded(env, ids_after_sql(kind, max_id), case))
            found.extend((kind, int(cell)) for cell in cells if cell.isdigit())
    return found


def _rank(item: tuple[str, int]) -> tuple[int, int]:
    kind, doc_id = item
    return (_ORDER.index(kind) if kind in _ORDER else len(_ORDER), -doc_id)


def _drop(env: Env, kind: str, doc_id: int) -> str:
    ref = {"type": kind, "id": doc_id}
    if _call(env, "/v1/vouchers/load", ref)[0] == 404:
        return ""
    if kind in ("sale_order", "purchase_order"):
        _call(env, "/v1/vouchers/lock", dict(ref, action="unlock"))
    _call(env, "/v1/vouchers/verify", dict(ref, action="unverify"))
    status, _payload = _call(env, "/v1/vouchers/delete", ref)
    return f"{kind} {doc_id} 删除 {status}"


def _docs(env: Env, case: str) -> list[str]:
    try:
        swept = _swept(env, case)
    except (RuntimeError, ValueError) as exc:
        swept = []
        note = [f"按最大主键找多出来的单据出错：{str(exc)[:60]}"]
    else:
        note = []
    items = sorted(set(_pairs(env.bag.get(leak_key(case), "")) + swept), key=_rank)
    return note + [text for text in (_drop(env, kind, doc_id) for kind, doc_id in items) if text]


def _mine(env: Env, case: str, cancel_no: str) -> bool:
    receipt = env.bag.get(RECEIPT_KEYS.get(case, ""), "")
    status, payload = _call(env, _REPORT, {"flag": cancel_no[2:4], "cancel_no": cancel_no})
    items = payload.get("items") if status == 200 else None
    if not receipt or not isinstance(items, list):
        return False
    return any(isinstance(item, dict) and str((item.get("receipt") or {}).get("id")) == receipt for item in items)


def _writeoffs(env: Env, case: str) -> list[str]:
    notes = []
    for cancel_no in [no for no in env.bag.get(hx_key(case), "").split(",") if no]:
        if _mine(env, case, cancel_no):
            notes.append(cancel_one(env, cancel_no[2:4], cancel_no[4:])[1])
    return notes


def leak_cleanup(env: Env, case: str) -> str:
    """撤回预演漏出去的锁定、单据、核销；什么都没有时返回空串。"""
    notes = _unlock(env, case) + _docs(env, case) + _writeoffs(env, case)
    return "；".join(notes)
