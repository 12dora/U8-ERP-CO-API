"""分包应收票据登记的端到端用例名 `note-split-register-cycle`（挑样本与 N/A 规则同 notes）。

区间与票面不符的登记 400 bad_request、不落库 → 预演登记不落库 → 登记 100.00、子票区间 [start, start + 9999]
（start 由时间编出，避开现有票据的号段）→ 票据 bsubpackage = 1、起止号，可用区间正好一行（起止号、100.00 / 100.00），
收款单 48 的票据号与来源号是「票据号-起号-止号」、不带区间的写法没有，notes/get 读到起止号 → 审核收款单
→ 结算子票 [start, start + 99]（1.00）：处理行号段、明细单号后缀（沿用 notes_more 的核对），可用区间剩一行
[start + 100, end]、99.00，余额 99.00 → 取消处理：处理行、明细删光，可用区间回到登记时的一行，余额 100.00
→ 弃审收款单 → 删除票据：票据、收款单、可用区间都不在。
中途失败按记下的清理动作倒序补偿（取消处理 → 弃审收款单 → 删除票据）。

步骤 kind 为 "custom"，SPLIT_REG_OPS 并入 ops_run；回读 SQL 在 notes_split_sql。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from decimal import Decimal

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _Fail, _guard, _NotApplicable, _Run
from co.e2e.casespec.arap_proc import CANCEL
from co.e2e.casespec.notes import (
    CREATE,
    DELETE,
    FACE,
    GET,
    PROCESS,
    VERIFY,
    Base,
    base,
    create,
    create_body,
    drop_note,
    expect,
    gone,
    missing,
    money,
    new_note_no,
    process,
    row,
    send,
)
from co.e2e.casespec.notes_more import ONE, _check_split
from co.e2e.casespec.notes_split_sql import split_state_sql

# 登记的子票张数：票面 100.00，每张 0.01。
_COUNT = 10000
# 结算的子票张数（1.00）。
_SPAN = 100
_WIDTH = 15
# split_state_sql 各列的位置。
_S_REMAIN, _S_AUDITED = 5, 13
_GONE = {0: "0", 6: "0", 11: "0", 12: "0", 14: "0"}


class Split:
    """一张要登记的分包票据：票据号与子票区间。"""

    def __init__(self, no: str, start: int) -> None:
        self.no = no
        self.start = start
        self.end = start + _COUNT - 1

    def state(self, run: _Run) -> list[str]:
        return row(run, split_state_sql(self.no, self.start, self.end), _WIDTH, "分包票据")

    def body(self, run: _Run, b: Base) -> dict:
        return dict(create_body(run, b, self.no), sub_start=self.start, sub_end=self.end)


def first_serial() -> int:
    # 按秒编起号：万张一段，8 位秒数轮转（约 3 年），与现有票据的银行号段（多为 9 到 10 位）基本不相交。
    return int(time.time()) % 10**8 * _COUNT + 1


def nothing(run: _Run, s: Split, what: str, drop: bool = True) -> None:
    """票据、收款单、可用区间都不在。drop 为真时票据意外落库就记下删除它的清理动作（按回读的主键）。"""
    state = s.state(run)
    if drop and state[0] != "0":
        run.undo.append(drop_note(run, {}, s.no))
    expect(state, _GONE, f"{what}后票据、收款单或可用区间落库了")


def refuse_mismatch(run: _Run, b: Base, s: Split) -> None:
    """子票区间比票面少一张：应 400 bad_request、什么都不写。意外成功时记下删除票据的清理动作再记 FAIL；
    既非 200 也非 400 时回读，票据落库了同样记下删除再记 FAIL。"""
    body = dict(s.body(run, b), sub_end=s.end - 1)
    status, payload = run.send(CREATE, body)
    if missing(status, payload):
        raise _NotApplicable("接口还没有 notes/create 路由")
    if status == 200:
        run.undo.append(drop_note(run, payload, s.no))
        raise _Fail("子票区间与票面不符的登记应 400，实际 200")
    if status != 400:
        # 结果不明（如 504）：先回读，票据落库了就记下删除它的清理动作并 FAIL。
        nothing(run, s, f"结果不明（{status}）的登记")
    if status != 400 or payload.get("code") != "bad_request":
        raise _Fail(f"子票区间与票面不符的登记应 400 bad_request，实际 {status} {payload.get('code')} {payload.get('message')}"[:240])
    nothing(run, s, "被拒的登记")


def avail(lo: int, hi: int, amount: Decimal) -> dict[int, str]:
    return {6: "1", 7: str(lo), 8: str(hi), 9: money(amount), 10: money(amount)}


def check_registered(run: _Run, s: Split) -> None:
    want = {0: "1", 1: "1", 2: str(s.start), 3: str(s.end), 4: money(FACE), 5: money(FACE), 11: "1", 12: "1", 13: "0", 14: "0"}
    expect(s.state(run), want | avail(s.start, s.end, FACE), f"登记后分包票据 {s.no}")
    head = run.ok(GET, {"type": "ar_note", "code": s.no}, "读取票据").get("head") or {}
    if (str(head.get("sub_start")), str(head.get("sub_end"))) != (str(s.start), str(s.end)):
        raise _Fail(f"notes/get 的子票区间不符：{head.get('sub_start')}-{head.get('sub_end')}，期望 {s.start}-{s.end}")


def verify(run: _Run, s: Split, receipt: int) -> None:
    ref = {"type": "ar_receipt", "id": receipt}
    run.ok(VERIFY, dict(ref, action="verify"), "审核票据的收款单")
    run.undo.append((VERIFY, dict(ref, action="unverify"), "弃审票据的收款单"))
    if s.state(run)[_S_AUDITED] != "1":
        raise _Fail(f"收款单 {receipt} 审核后回读不是已审核")


def settle_part(run: _Run, b: Base, s: Split) -> str:
    hi = s.start + _SPAN - 1
    body = {
        "flag": "AR", "op": "settle", "note": s.no, "amount": float(ONE), "sub_start": s.start, "sub_end": hi,
        "bank_code": b.bank,
    }
    pno = process(run, body, "PJJAR")
    _check_split(run, pno, s.start, hi, b.bank)
    left = FACE - ONE
    expect(s.state(run), {_S_REMAIN: money(left)} | avail(hi + 1, s.end, left), f"结算 {pno} 后可用区间")
    return pno


def split_register_cycle(run: _Run) -> tuple[str, str]:
    b = base(run)
    s = Split(new_note_no("05"), first_serial())
    refuse_mismatch(run, b, s)
    body = s.body(run, b)
    send(run, CREATE, dict(body, dry_run=True), "预演登记分包票据")
    nothing(run, s, "预演登记")
    receipt = create(run, body)
    check_registered(run, s)
    verify(run, s, receipt)
    pno = settle_part(run, b, s)
    run.settle(1)
    gone(run, pno)
    expect(s.state(run), {_S_REMAIN: money(FACE)} | avail(s.start, s.end, FACE), f"取消 {pno} 后可用区间")
    run.settle(2)
    nothing(run, s, "删除票据", drop=False)
    return "PASS", (
        f"票据 {s.no}（子票 {s.start}-{s.end}，{FACE}）：区间不符 400 不落库，预演不落库，分包标志、可用区间、收款单票据号后缀都对；"
        f"{pno} 结算 {ONE} 后可用区间剩一段，取消后复原；删除后票据、收款单、可用区间都不在"
    )


# ---- 接线 ----


def _op(body: Callable[[_Run], tuple[str, str]]) -> Callable[[Env, CaseSpec, Step], tuple[str, str]]:
    def op(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
        del step
        return _guard(env, spec, body)

    return op


SPLIT_REG_OPS = {"notes_split_register": _op(split_register_cycle)}

# 同 notes 的登记、结算用例：票据登记串行（notes:AR）、新建并审核收款单、查结账标志。
_TAGS = ("notes:AR", "new:ar_receipt", "w:ar_receipt", "period:close")
_ROUTES = (CREATE, GET, VERIFY, PROCESS, CANCEL, DELETE)


def split_reg_cases() -> list[CaseSpec]:
    # 样本取现有票据的往来信息；挑不到时记 N/A。
    return [
        CaseSpec(
            "note-split-register-cycle",
            "分包票据登记：区间与票面不符 400 不落库；预演不落库；分包标志、起止号、一行可用区间、收款单票据号「号-起-止」都对；"
            "结算一段子票后可用区间改写、取消后复原；弃审、删除后票据、收款单、可用区间都不在",
            (),
            (Step("custom", sql_name="notes_split_register", id_from="none"),),
            True,
            optional=True,
            tags=frozenset(_TAGS),
            routes=_ROUTES,
        )
    ]
