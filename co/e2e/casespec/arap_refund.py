"""供应商退款（ap_refund，AP48，应付的收款单）、客户退款（ar_refund，AR49，应收的付款单）的用例。

ap-refund-cycle / ar-refund-cycle：挑单的空闲供应商 / 客户（ap_master / ar_master，同付款单、收款单的往返用例）新建一张
  100 元退款 → 回读表头：VT_ID 是卡片 AP48 / AR49 的缺省模板、条码 ||ap48| / ||ar49| 加单号、表头和表体金额都是正数、
  表体 iType=0、bPrePay=0；vouchers/load 读得到，同一 id 按付款单 / 收款单读取 404 → 改摘要 → 审核 → 往来明细有行、
  借贷都是负数（U8 审核时自己写负数）→ 已审删除 409 → 弃审（明细删光、审核人清空）→ 删除 → 读取 404。
  自建自删，清理按建的倒序（_Run），清理失败记 FAIL。第一步 arap_ctl：账套没有应收 / 应付受控科目时 N/A。
list-ap-refund / list-ar-refund：账套里有这类单据时，列表行数与 SQL 计数一致（超过一页时正好一页）、每行 vouch_type / ledger
  对得上，按单号筛选得到挑中的单据，vouchers/load 读得到，同一 id 按付款单 / 收款单读取 404。没有这类单据记 N/A。
步骤 kind 为 "custom"，sql_name 是操作名（ARAP_REFUND_OPS 并入 ops_run）。
"""

from __future__ import annotations

from decimal import Decimal

from co.e2e.casespec.arap_ctl import ctl_step_for
from co.e2e.casespec.arap_gen_bodies import AMOUNT, _parts
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _dec, _Fail, _guard, _Run
from co.e2e.casespec.arap_refund_sql import REFUND, detail_sql, gone_sql, head_sql, pick_sql

_CREATE = "/v1/vouchers/create"
_UPDATE = "/v1/vouchers/update"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_LOAD = "/v1/vouchers/load"
_LIST = "/v1/vouchers/list"
_MEMO = "CO桥回归"
_EDITED = "CO桥回归改"
_LIMIT = 500
# 类型 → (用例名后缀, 中文名, 挑单键, 卡片缺省模板 VT_ID)。
_KINDS = {
    "ap_refund": ("ap-refund", "供应商退款", "ap_master", "8051"),
    "ar_refund": ("ar-refund", "客户退款", "ar_master", "8055"),
}


def _body(kind: str, env: Env, digest: str) -> dict:
    line = {"iType": 0, "cKm": "", "iAmt": AMOUNT}
    if kind == "ap_refund":
        ven, ctrl, bank, settle, _bill = _parts(env.bag.get("ap_master", ""), 5)
        head = {"cDwCode": ven, "cSSCode": settle, "cCode": bank}
    else:
        cus, dep, person, ctrl, bank, settle, _bill = _parts(env.bag.get("ar_master", ""), 7)
        head = {"cDwCode": cus, "cDeptCode": dep, "cPerson": person, "cSSCode": settle, "cCode": bank}
    head.update({"dVouchDate": env.today, "cDigest": digest})
    line["cKm"] = ctrl
    return {"type": kind, "head": head, "lines": [line]}


def _head(run: _Run, kind: str, doc: int) -> list[str]:
    parts = run.pick(head_sql(kind, str(doc)), 10)
    if parts is None:
        raise _Fail(f"回读{_KINDS[kind][1]}表头失败（单据不在或类型不对）")
    return parts


def _check_made(run: _Run, kind: str, doc: int) -> str:
    """新建后：VT、条码、金额为正、一行应收付款（iType=0、非预收付）、未审核。返回单号。"""
    code, vt, barcode, amount, checker, digest, lines, line_amt, odd, default = _head(run, kind, doc)
    card = REFUND[kind][3]
    want = f"{AMOUNT:.2f}"
    if vt not in (default, _KINDS[kind][3]) or not vt:
        raise _Fail(f"VT_ID 应是卡片 {card.upper()} 的缺省模板 {default or _KINDS[kind][3]}，实际 {vt or '空'}")
    if barcode.lower() != "//" + card + "/" + code.lower():
        raise _Fail(f"条码应为 ||{card}|{code}，实际 {barcode.replace('/', '|') or '空'}")
    if (amount, line_amt, lines, odd) != (want, want, "1", "0"):
        raise _Fail(f"表头金额|表体金额|行数|非应收付款行应为 {want}|{want}|1|0，实际 {amount}|{line_amt}|{lines}|{odd}")
    if checker or digest != _MEMO:
        raise _Fail(f"新建后应未审核、摘要为 {_MEMO}，实际审核人 {checker or '空'}、摘要 {digest or '空'}")
    return code


def _refuse_twin(run: _Run, kind: str, doc: int) -> None:
    """同一 iID 按另一种收付款单（AP48 按付款单、AR49 按收款单）读取：404，不会被当成付款单 / 收款单。"""
    twin = REFUND[kind][4]
    status, payload = run.send(_LOAD, {"type": twin, "id": doc})
    if status != 404 or payload.get("code") != "not_found":
        raise _Fail(f"按 {twin} 读取{_KINDS[kind][1]} {doc} 应 404 not_found，实际 {status} {payload.get('code')}")


def _details(run: _Run, kind: str, doc: int) -> tuple[int, int, Decimal]:
    parts = run.pick(detail_sql(kind, str(doc)), 3)
    if parts is None or not parts[0].isdigit() or not parts[1].isdigit():
        raise _Fail("回读往来明细失败")
    return int(parts[0]), int(parts[1]), _dec(parts[2])


def _verify(run: _Run, kind: str, doc: int) -> str:
    run.unverify(kind, doc)  # 先登记：审核报错但 U8 已提交时，清理照样弃审
    payload = run.ok(_VERIFY, {"type": kind, "id": doc, "action": "verify"}, "审核" + _KINDS[kind][1])
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    if state.get("verified") is not True:
        raise _Fail(f"审核响应 state.verified 应为 true，实际 {state.get('verified')!r}")
    rows, positive, total = _details(run, kind, doc)
    if rows < 1 or positive != 0 or total >= 0:
        raise _Fail(f"审核后往来明细应有行且借贷都是负数，实际 {rows} 行、正数行 {positive}、借贷合计 {total}")
    status, payload = run.send(_DELETE, {"type": kind, "id": doc})
    if status != 409 or payload.get("code") != "state_mismatch":
        raise _Fail(f"已审核的{_KINDS[kind][1]}删除应 409 state_mismatch，实际 {status} {payload.get('code')}")
    return f"往来明细 {rows} 行、借贷合计 {total}"


def _cycle(run: _Run, kind: str) -> tuple[str, str]:
    title = _KINDS[kind][1]
    doc = run.made(_CREATE, _body(kind, run.env, _MEMO), "新建" + title)
    run.drop(kind, doc)
    code = _check_made(run, kind, doc)
    loaded = run.ok(_LOAD, {"type": kind, "id": doc}, "读取" + title)
    if str(loaded.get("code") or "") != code:
        raise _Fail(f"vouchers/load 的单号应为 {code}，实际 {loaded.get('code')!r}")
    _refuse_twin(run, kind, doc)
    run.ok(_UPDATE, {"type": kind, "id": doc, "head": {"cDigest": _EDITED}}, "修改" + title + "摘要")
    if _head(run, kind, doc)[5] != _EDITED:
        raise _Fail("修改后摘要没有变成 " + _EDITED)
    seen = _verify(run, kind, doc)
    run.settle(1)
    head = _head(run, kind, doc)
    rows = _details(run, kind, doc)[0]
    if head[4] or rows != 0:
        raise _Fail(f"弃审后审核人应清空、往来明细删光，实际审核人 {head[4] or '空'}、明细 {rows} 行")
    run.settle(1)
    if run.cell(gone_sql(str(doc))) != "0":
        raise _Fail(f"删除后{title}表头或表体仍在")
    status, _payload = run.send(_LOAD, {"type": kind, "id": doc})
    if status != 404:
        raise _Fail(f"删除后读取应 404，实际 {status}")
    return "PASS", f"{title} {code}（金额 {AMOUNT}）：新建、读取、按 {REFUND[kind][4]} 读取 404、改摘要、审核（{seen}）、弃审、删除"


def _list_rows(run: _Run, kind: str, count: int) -> None:
    flag, vouch = REFUND[kind][0], REFUND[kind][1]
    payload = run.ok(_LIST, {"type": kind, "limit": _LIMIT}, "列表")
    rows = payload.get("items")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise _Fail("列表响应缺 items")
    want = min(count, _LIMIT)
    if len(rows) != want or (payload.get("next") is None) != (count <= _LIMIT):
        raise _Fail(f"列表应有 {want} 行（SQL 计数 {count}），实际 {len(rows)} 行、next={payload.get('next')!r}")
    bad = [row.get("id") for row in rows if (str(row.get("vouch_type") or "").strip(), str(row.get("ledger") or "").strip())
           != (vouch, flag)]
    if bad:
        raise _Fail(f"列表里有不是 {flag}{vouch} 的行：{bad[:5]}")


def _list_body(run: _Run, kind: str) -> tuple[str, str]:
    title = _KINDS[kind][1]
    parts = run.pick(pick_sql(kind), 3)
    if parts is None:
        return "N/A", f"账套里没有{title}（{REFUND[kind][0]}{REFUND[kind][1]}）"
    doc, code, count = int(parts[0]), parts[1], int(parts[2])
    _list_rows(run, kind, count)
    found = run.ok(_LIST, {"type": kind, "filter": {"code": code}, "limit": 20}, "按单号筛选")
    if doc not in [row.get("id") for row in found.get("items") or [] if isinstance(row, dict)]:
        raise _Fail(f"按单号 {code} 筛选没有得到 id {doc}")
    loaded = run.ok(_LOAD, {"type": kind, "id": doc}, "读取" + title)
    if str(loaded.get("code") or "") != code:
        raise _Fail(f"vouchers/load 的单号应为 {code}，实际 {loaded.get('code')!r}")
    _refuse_twin(run, kind, doc)
    return "PASS", f"{title} {count} 张，列表与计数一致；{code}（id {doc}）读取正常，按 {REFUND[kind][4]} 读取 404"


def _op(kind: str, body: str):
    def run_op(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
        if body == "cycle":
            return _guard(env, spec, lambda run: _cycle(run, kind))
        return _guard(env, spec, lambda run: _list_body(run, kind))

    return run_op


ARAP_REFUND_OPS = {f"{kind}_{body}": _op(kind, body) for kind in _KINDS for body in ("cycle", "list")}


def _cycle_case(kind: str) -> CaseSpec:
    stem, title, need, _vt = _KINDS[kind]
    steps = (ctl_step_for(need), Step("custom", sql_name=f"{kind}_cycle", id_from="none"))
    routes = (_CREATE, _LOAD, _UPDATE, _VERIFY, _DELETE)
    text = title + "新建、读取（VT、条码、正数金额）、修改、审核（往来明细为负数）、弃审、删除"
    tags = frozenset(("new:" + kind, "w:" + kind))
    return CaseSpec(stem + "-cycle", text, (need,), steps, True, tags=tags, routes=routes)


def _list_case(kind: str) -> CaseSpec:
    stem, title, _need, _vt = _KINDS[kind]
    step = Step("custom", sql_name=f"{kind}_list", id_from="none")
    text = title + "列表与计数一致、按单号筛选、读取，按收付款单读取同一 id 404（没有这类单据 N/A）"
    tags = frozenset(("list:" + kind,))
    return CaseSpec("list-" + stem, text, (), (step,), True, tags=tags, routes=(_LIST, _LOAD))


def arap_refund_cases() -> list[CaseSpec]:
    return [_cycle_case(kind) for kind in _KINDS] + [_list_case(kind) for kind in _KINDS]
