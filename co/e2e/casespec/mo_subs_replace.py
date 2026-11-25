"""生产订单修改由调用方替换 / 清空子件替代料（mo-update-substitutes-replace，可选，第二级写入）。

照生产订单新增挑单（moc_*）新建生产订单（moc_new）→ 读取，取第一个子件（行、行号、存货）和它原有的替代料 → 预演替换 200 dry_run、
替代料不变 → 只换替代料（另挑的 bBomSub 存货，优先级 2、系数 1.5、替换、起止日期、define22）：200，changed=1，响应 allocations
即给的，AllocateId 不变（没有调用 U8）→ 读取等于给的 → 清空：读取为空 → 反例都不改单据：子件对不上 409、替代料是子件本身 400、
是母件 400、不允许做 BOM 子件的存货 400 → 改数量同时替换（调用 U8，子件重插）：读取等于给的、AllocateId 换新 → 按原有的替换
回去（原来没有就清空）→ 删除。挑不到替代存货的账套、桥没开第二级写入或账套不在测试账套名单里记 N/A。
步骤 kind 为 "custom"，sql_name 是操作名（MO_SUBS_REPLACE_OPS 并入 ops_run）；中途失败时尽力删除新单（快照还原兜底）。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.mo_subs_replace_sql import subs_pick_sql
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.mo_create import moc_new
from co.e2e.casespec.qm_reject import _brief, _Fail, _send
from co.e2e.casespec.sqlexec import run_guarded

NAME = "mo-update-substitutes-replace"
_KIND = "production_order"
CREATE = "/v1/vouchers/create"
UPDATE = "/v1/vouchers/update"
LOAD = "/v1/vouchers/load"
DELETE = "/v1/vouchers/delete"
_TIER = ("feature_disabled", "test_account_only")
_SUB_KEYS = ("sequence", "inv_code", "factor", "replace_flag", "eff_begin", "eff_end", "defines")


@dataclass
class Run:
    env: Env
    spec: CaseSpec
    sub: str
    nosub: str
    made: list[int] = field(default_factory=list)


@dataclass
class Alloc:
    line_id: int
    allocate_id: int
    sort_seq: int
    inv_code: str
    subs: list[dict]


class _NA(Exception):
    pass


def _alloc(run: Run, doc: int, seq: int, inv: str) -> Alloc:
    status, payload = _send(run.env, LOAD, {"type": _KIND, "id": doc})
    allocs = payload.get("allocations")
    if status != 200 or not isinstance(allocs, list):
        raise _Fail("读取生产订单应 200 且带 allocations，实际 " + _brief(status, payload))
    for row in allocs:
        got = Alloc(int(str(row["MoDId"])), int(str(row["AllocateId"])), int(str(row["SortSeq"])), str(row["InvCode"]).strip(),
                    list(row.get("substitutes") or []))
        if seq < 0 or (got.sort_seq == seq and got.inv_code.upper() == inv.upper()):
            return got
    raise _Fail(f"读取里没有行号 {seq}、存货 {inv} 的子件")


def _body(doc: int, a: Alloc, subs: list[dict], seq: int | None = None, **line: object) -> dict:
    alloc = {"sort_seq": a.sort_seq if seq is None else seq, "inv_code": a.inv_code, "substitutes": subs}
    return {"type": _KIND, "id": doc, "lines": [dict(line, op="update", line_id=a.line_id, allocations=[alloc])]}


def _want(run: Run) -> list[dict]:
    today = dt.date.fromisoformat(run.env.today)
    end = (today + dt.timedelta(days=30)).isoformat()
    return [{"inv_code": run.sub, "sequence": 2, "factor": 1.5, "replace_flag": True, "eff_begin": today.isoformat(),
             "eff_end": end, "defines": {"define22": "DEMO-001"}}]


def _shape(subs: list[dict]) -> list[tuple]:
    # 比较用：factor 按数比，其余原样；没有的键按 None。
    rows = []
    for sub in subs:
        factor = sub.get("factor")
        rows.append(tuple(Decimal(str(factor)).normalize() if key == "factor" and factor is not None else sub.get(key)
                          for key in _SUB_KEYS))
    return sorted(rows, key=repr)


def _same(got: list[dict], want: list[dict], what: str) -> None:
    if _shape(got) != _shape(want):
        raise _Fail(f"{what}：替代料应为 {want}，实际 {got}"[:200])


def _request(subs: list[dict]) -> list[dict]:
    # 读取的形状 → 请求：去掉值为 null 的键（日期为空写 NULL）。
    return [{key: value for key, value in sub.items() if value is not None} for sub in subs]


def _replace(run: Run, body: dict, want_subs: list[dict]) -> dict:
    status, payload = _send(run.env, UPDATE, body)
    if status == 403 and payload.get("code") in _TIER:
        raise _NA("桥没开第二级写入或账套不在测试账套名单里：" + str(payload.get("message"))[:80])
    if status != 200 or payload.get("ok") is not True or payload.get("changed") != 1:
        raise _Fail("替换替代料应 200 且 changed=1，实际 " + _brief(status, payload))
    changed = payload.get("allocations") or []
    if len(changed) != 1:
        raise _Fail(f"响应 allocations 应为 1 个子件，实际 {changed}"[:200])
    _same(changed[0].get("substitutes") or [], want_subs, "响应")
    return payload


def _refused(run: Run, doc: int, a: Alloc, body: dict, want: tuple[int, str]) -> str:
    status, payload = _send(run.env, UPDATE, body)
    if (status, payload.get("code")) != want:
        raise _Fail(f"应 {want[0]} {want[1]}，实际 " + _brief(status, payload))
    _same(_alloc(run, doc, a.sort_seq, a.inv_code).subs, a.subs, "被拒后")
    return str(status)


def _negatives(run: Run, doc: int, a: Alloc, parent: str) -> str:
    bad = (400, "bad_request")
    sub = {"inv_code": run.sub}
    done = [_refused(run, doc, a, _body(doc, a, [sub], seq=a.sort_seq + 100000), (409, "state_mismatch"))]
    done.append(_refused(run, doc, a, _body(doc, a, [{"inv_code": a.inv_code}]), bad))
    done.append(_refused(run, doc, a, _body(doc, a, [{"inv_code": parent}]), bad))
    if run.nosub:
        done.append(_refused(run, doc, a, _body(doc, a, [{"inv_code": run.nosub}]), bad))
    return "/".join(done)


def _dry(run: Run, doc: int, a: Alloc) -> None:
    status, payload = _send(run.env, UPDATE, dict(_body(doc, a, _want(run)), dry_run=True))
    if status == 403 and payload.get("code") in _TIER:
        raise _NA("桥没开第二级写入或账套不在测试账套名单里：" + str(payload.get("message"))[:80])
    if status != 200 or payload.get("dry_run") is not True:
        raise _Fail("预演替换应 200 dry_run=true，实际 " + _brief(status, payload))
    _same(_alloc(run, doc, a.sort_seq, a.inv_code).subs, a.subs, "预演后")


def _create(run: Run) -> int:
    status, payload = _send(run.env, CREATE, moc_new(run.env))
    doc = payload.get("id")
    if status != 200 or type(doc) is not int or doc <= 0:
        raise _Fail("新建生产订单应 200 并返回 id，实际 " + _brief(status, payload))
    run.made.append(doc)
    return doc


def _cycle(run: Run) -> str:
    doc = _create(run)
    a = _alloc(run, doc, -1, "")
    _dry(run, doc, a)
    want = _want(run)
    _replace(run, _body(doc, a, want), want)
    now = _alloc(run, doc, a.sort_seq, a.inv_code)
    _same(now.subs, want, "替换后读取")
    if now.allocate_id != a.allocate_id:
        raise _Fail("只换替代料后子件 AllocateId 变了（不应调用 U8）")
    _replace(run, _body(doc, a, []), [])
    _same(_alloc(run, doc, a.sort_seq, a.inv_code).subs, [], "清空后读取")
    cleared = Alloc(a.line_id, a.allocate_id, a.sort_seq, a.inv_code, [])
    refused = _negatives(run, doc, cleared, run.env.bag["moc_inv"])
    _replace(run, _body(doc, a, want, qty=3), want)
    mixed = _alloc(run, doc, a.sort_seq, a.inv_code)
    _same(mixed.subs, want, "改数量同时替换后读取")
    if mixed.allocate_id == a.allocate_id:
        raise _Fail("改数量后子件 AllocateId 应换新（U8 重插子件）")
    _replace(run, _body(doc, a, _request(a.subs)), a.subs)
    _same(_alloc(run, doc, a.sort_seq, a.inv_code).subs, a.subs, "换回原有替代料后读取")
    status, payload = _send(run.env, DELETE, {"type": _KIND, "id": doc})
    if status != 200 or payload.get("deleted") is not True:
        raise _Fail("删除应 200 deleted=true，实际 " + _brief(status, payload))
    run.made.remove(doc)
    return (f"生产订单 {doc} 子件 {a.inv_code}（行号 {a.sort_seq}，原有 {len(a.subs)} 条）：预演、替换、清空、反例 {refused}、"
            "改数量同时替换、换回、删除")


def _cleanup(run: Run) -> str:
    notes = []
    for doc in run.made:
        try:
            status, _payload = _send(run.env, DELETE, {"type": _KIND, "id": doc})
            notes.append(f"补偿删除生产订单 {doc} 返回 {status}")
        except Exception as exc:  # 尽力而为，快照还原兜底
            notes.append(f"补偿删除生产订单 {doc} 出错：{str(exc)[:60]}")
    return "；".join(notes)


def mo_subs_replace(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    parts = first_cell(run_guarded(env, subs_pick_sql(env.bag["moc_inv"]), spec.name)).strip().split("|")
    if len(parts) != 2 or not parts[0]:
        return "N/A", "挑不到可做替代料的存货（允许做 BOM 子件、未停用、不是母件和它的子件）"
    run = Run(env, spec, parts[0], parts[1])
    try:
        return "PASS", _cycle(run)
    except _NA as exc:
        tail = _cleanup(run)
        return "N/A", (str(exc) + ("；" + tail if tail else ""))[:240]
    except Exception as exc:
        # 断言失败、读取失败、中断、网络超时等任何异常，都先尽力删掉新单再记 FAIL。
        tail = _cleanup(run)
        return "FAIL", (str(exc) + ("；" + tail if tail else ""))[:240]


MO_SUBS_REPLACE_OPS = {"mo_subs_replace": mo_subs_replace}


def mo_subs_replace_cases() -> list[CaseSpec]:
    step = Step("custom", sql_name="mo_subs_replace", id_from="none")
    title = "生产订单修改替换替代料：预演、只换替代料不调用 U8、清空、反例、改数量同时替换、换回原有、删除"
    return [
        CaseSpec(
            NAME,
            title,
            ("moc_inv", "moc_dept", "moc_type"),
            (step,),
            True,
            optional=True,
            tags=frozenset(("bom",)),
            routes=(CREATE, UPDATE, LOAD, DELETE),
        )
    ]
