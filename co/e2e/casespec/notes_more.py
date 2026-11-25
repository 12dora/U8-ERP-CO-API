"""应收票据用例的后两类：背书（`note-endorse-cycle`）与现有分包票据的子票结算（`note-split-settle-cycle`）。

note-endorse-cycle：挑一行已审核、余额 >= 100.00 的采购专用发票（01），登记 100.00 的票据（不分包）、审核收款单，
  先断言部分背书 1.00 被拒（409 state_mismatch「整张票据」），再整张背书 100.00 冲这一行
  → 处理号 PJBAR…，处理行 9E（金额 100.00、cBank = 供应商），应收票据行贷方 100.00，应付明细一行借方 100.00（供应商、来源 01），
  采购发票行 iOriTotal 加 100、余额减 100，票据余额 0.00 → 取消处理：处理行和两边明细删光，累计、余额、票据余额复原
  → 弃审、删票据。
note-split-settle-cycle：账套里现有的分包票据（登记生成的收款单已审核、可用区间合计等于余额、区间两两不相接），
  从第一段可用区间的最小号取 100 个号（1.00）结算 → 处理行号段、应收票据行单号后缀「-起-止」、可用区间重写
  （余额与区间合计各减 1.00、最小起号后移 100）都对 → 取消后余额、区间（行数、合计、起止、校验和）与处理行数逐项复原。
  这条不登记票据，只动现有票据的一次结算，取消即复原。

小工具沿用 notes；本模块只由 notes.NOTES_OPS 在调用时引入。
"""

from __future__ import annotations

from decimal import Decimal

from co.e2e.casespec.stock_plus import _Fail, _NotApplicable, _Run
from co.e2e.casespec.arap_proc import dec, nums
from co.e2e.casespec.notes import (
    FACE,
    ZERO,
    base,
    expect,
    finish,
    gone,
    money,
    note_state,
    process,
    refuse_partial,
    register,
    row,
    verify_receipt,
)
from co.e2e.casespec.notes_sql import ap_pick_sql, ap_snap_sql, detail_sql, split_pick_sql, split_snap_sql, sub_sql

ONE = Decimal("1.00")
_SPAN = 100


# ---- 背书 ----


def _endorse_body(no: str, pick: list[str], amount: Decimal) -> dict:
    vendor, code, line = pick
    return {
        "flag": "AR", "op": "endorse", "note": no, "amount": float(amount), "vendor": vendor,
        "ap_lines": [{"type": "01", "id": code, "line_id": int(line), "amount": float(amount)}],
    }


def _check_endorse(run: _Run, pno: str, no: str, vendor: str, customer: str) -> None:
    sub = row(run, sub_sql(pno), 9, "处理行")
    expect(sub, {0: "1", 1: "9E", 2: money(FACE), 3: money(ZERO), 5: vendor, 6: "0"}, f"处理行 {pno}")
    detail = row(run, detail_sql(pno), 12, "往来明细")
    want = {
        0: "1", 1: "1", 2: money(ZERO), 3: money(FACE), 5: no, 6: customer,
        7: "1", 8: money(FACE), 9: money(ZERO), 10: vendor, 11: "01",
    }
    expect(detail, want, f"往来明细 {pno}")


def endorse_cycle(run: _Run) -> tuple[str, str]:
    b = base(run)
    pick = run.pick(ap_pick_sql(run.env.today), 3)
    if pick is None:
        raise _NotApplicable(f"挑不到已审核、本位币、余额 >= {FACE}、没有合同的采购专用发票行")
    vendor, code, line = pick
    snap = ap_snap_sql(int(line), vendor)
    before = nums(run, snap, 3, "背书前")
    no, receipt = register(run, b, "04")
    verify_receipt(run, no, receipt)
    refuse_partial(run, _endorse_body(no, pick, ONE), "PJBAR")
    pno = process(run, _endorse_body(no, pick, FACE), "PJBAR")
    _check_endorse(run, pno, no, vendor, b.customer)
    got = nums(run, snap, 3, "背书后")
    if got != [before[0] + FACE, before[1] - FACE, before[2] + 1]:
        raise _Fail(f"背书后（发票行累计、余额、9E 行数）应为 {before[0] + FACE}、{before[1] - FACE}、{before[2] + 1}，实际 {got}")
    if dec(note_state(run, no)[4], "余额") != ZERO:
        raise _Fail(f"整张背书后票据余额应为 {ZERO}")
    run.settle(1)
    gone(run, pno)
    back = nums(run, snap, 3, "取消后")
    if back != before or dec(note_state(run, no)[4], "余额") != FACE:
        raise _Fail(f"取消背书后发票行应回到 {before}（实际 {back}），票据余额回到 {FACE}")
    finish(run, no)
    return "PASS", (
        f"{pno}（票据 {no} 整张背书 {code} 行 {line}，{FACE}）：部分金额被拒，处理行、两边明细、发票累计与余额都对，"
        "取消后复原，票据已删"
    )


# ---- 分包结算 ----


def _check_split(run: _Run, pno: str, lo: int, hi: int, bank: str) -> None:
    sub = row(run, sub_sql(pno), 9, "处理行")
    expect(sub, {0: "1", 1: "9A", 2: money(ONE), 4: bank, 6: "0", 7: str(lo), 8: str(hi)}, f"处理行 {pno}")
    detail = row(run, detail_sql(pno), 12, "往来明细")
    expect(detail, {0: "1", 1: "1", 3: money(ONE), 7: "0"}, f"往来明细 {pno}")
    if not detail[5].endswith(f"-{lo}-{hi}"):
        raise _Fail(f"分包票据行的单号应以 -{lo}-{hi} 结尾，实际 {detail[5][:80]}")


def _check_rewrite(before: list[str], got: list[str], hi: int) -> None:
    want = {
        0: money(dec(before[0], "余额") - ONE), 1: before[1], 2: money(dec(before[2], "区间合计") - ONE),
        3: str(hi + 1), 4: before[4], 6: str(int(before[6]) + 1),
    }
    expect(got, want, "结算后可用区间")


def split_cycle(run: _Run) -> tuple[str, str]:
    b = base(run)
    pick = run.pick(split_pick_sql(run.env.today), 3)
    if pick is None:
        raise _NotApplicable("挑不到可用区间完整、收款单已审核、第一段至少 200 个号的现有分包应收票据")
    note, lo = int(pick[0]), int(pick[1])
    hi = lo + _SPAN - 1
    snap = split_snap_sql(note)
    before = row(run, snap, 7, "结算前票据")
    body = {
        "flag": "AR", "op": "settle", "note": note, "amount": float(ONE), "sub_start": lo, "sub_end": hi,
        "bank_code": b.bank,
    }
    pno = process(run, body, "PJJAR")
    _check_split(run, pno, lo, hi, b.bank)
    _check_rewrite(before, row(run, snap, 7, "结算后票据"), hi)
    run.settle(1)
    gone(run, pno)
    back = row(run, snap, 7, "取消后票据")
    if back != before:
        raise _Fail(f"取消后票据余额、可用区间、处理行数应回到 {before}，实际 {back}")
    return "PASS", f"{pno}（票据主键 {note}，子票 {lo}-{hi}，{ONE}）：号段、单号后缀、可用区间重写都对，取消后逐项复原"
