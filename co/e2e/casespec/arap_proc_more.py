"""应收应付处理用例的后两类：红票对冲 `arap-red-offset-cycle` 与汇兑损益（arap-exchange-gain-cycle / -ap）。

流程与断言见 arap_proc 的模块说明；小工具（回读、处理号、制单、回写核对）沿用 arap_proc，回读 SQL 在
arap_proc_sql。本模块只由 arap_proc.ARAP_PROC_OPS 在调用时引入。
"""

from __future__ import annotations

import re
from decimal import Decimal

from co.e2e.casespec.stock_plus import _Fail, _NotApplicable, _Run
from co.e2e.casespec.arap_proc import CANCEL, check_gl, check_marks, dec, drop_voucher, make_voucher, nums
from co.e2e.casespec.arap_proc_more_sql import (
    blue_pick_sql,
    fx_pick_sql,
    fx_rows_sql,
    fx_snap_sql,
    pl_code_sql,
    red_gone_sql,
    red_line_sql,
    red_rows_sql,
    red_snap_sql,
    red_src_sql,
    ret_line_sql,
)
from co.e2e.casespec.tier2 import tier2_refused

RED = "/v1/arap/red_offset"
EXGAIN = "/v1/arap/exchange_gain"
EXGAIN_CANCEL = "/v1/arap/exchange_gain/cancel"
_GEN = "/v1/vouchers/generate"
_VERIFY = "/v1/vouchers/verify"
_STEP = Decimal("0.000001")
_ZERO = Decimal("0")
_MISSING = "未知路径"
# 侧 → 处理号前缀、应收单 / 应付单类型前缀、收付款单类型。
_FX = {"AR": ("SYRAR", "R", "48"), "AP": ("SYPAP", "P", "49")}


# ---- 红票对冲 ----


def _red_invoice(run: _Run, dl: int, dl_line: int) -> tuple[int, int]:
    """参照发货行退 1 个（已开票退货）→ 审核 → 参照退货单生成红字发票 → 复核 → 应收审核。返回（退货单 id，红票 id）。"""
    body = {"type": "sale_return", "id": dl, "head": {"invoiced": True},
            "lines": [{"source_line_id": dl_line, "quantity": 1}]}
    ret = run.made(_GEN, body, "参照发货单生成退货单")
    run.drop("sale_return", ret)
    run.ok(_VERIFY, {"type": "sale_return", "id": ret, "action": "verify"}, "审核退货单")
    run.unverify("sale_return", ret)
    ret_line = int(run.cell(ret_line_sql(ret)) or 0)
    if ret_line <= 0:
        raise _Fail(f"退货单 {ret} 读不到行")
    body = {"type": "sale_invoice", "id": ret, "source_type": "sale_return",
            "lines": [{"source_line_id": ret_line, "quantity": 1}]}
    red = run.made(_GEN, body, "参照退货单生成红字发票")
    run.drop("sale_invoice", red)
    run.ok(_VERIFY, {"type": "sale_invoice", "id": red, "action": "verify"}, "复核红字发票")
    run.unverify("sale_invoice", red)
    run.ok(_VERIFY, {"type": "sale_invoice", "id": red, "action": "arap_verify"}, "应收审核红字发票")
    run.undo.append((_VERIFY, {"type": "sale_invoice", "id": red, "action": "arap_unverify"}, "应收弃审红字发票"))
    return ret, red


def _offset(run: _Run, cus: str, red: list[str], blue: list[str], amount: Decimal) -> str:
    body = {
        "flag": "AR",
        "partner": cus,
        "red": [{"type": red[0], "id": red[1], "line_id": int(red[2]), "amount": float(amount)}],
        "blue": [{"type": blue[0], "id": blue[1], "line_id": int(blue[2]), "amount": float(amount)}],
    }
    status, payload = run.send(RED, body)
    if status == 404 and _MISSING in str(payload.get("message")):
        raise _NotApplicable("桥还没有 arap/red_offset 路由")
    if status != 200:
        raise _Fail(f"红票对冲：{status} {payload.get('code')} {payload.get('message')}"[:240])
    raw = payload.get("cancel_no")
    no = raw if isinstance(raw, str) else ""
    if no:
        run.undo.append((CANCEL, {"flag": "AR", "cancel_no": no}, "取消红票对冲"))
    if re.fullmatch(r"HRAR[0-9]{1,20}", no) is None:
        raise _Fail(f"红票对冲的处理号应以 HRAR 开头，实际 {no[:30] or '空'}")
    return no


def red_offset_cycle(run: _Run) -> tuple[str, str]:
    src = run.pick(red_src_sql(run.env.today), 3)
    if src is None:
        raise _NotApplicable("挑不到可按已开票退货的蓝字发货行（同客户另有余额足够的蓝字发票），只在有种子数据的账套跑")
    ret, inv = _red_invoice(run, int(src[0]), int(src[1]))
    red = run.pick(red_line_sql(inv), 4)
    if red is None:
        raise _Fail(f"红字发票 {inv} 读不到行")
    amount = dec(red[3], "红票未对冲金额")
    if amount <= _ZERO:
        raise _Fail(f"红字发票 {red[1]} 应收审核后没有未对冲金额：{amount}")
    blue = run.pick(blue_pick_sql(src[2], amount, run.env.today), 3)
    if blue is None:
        raise _NotApplicable(f"客户 {src[2]} 没有余额不小于 {amount} 的蓝字发票行")
    snap = red_snap_sql(int(red[2]), int(blue[2]), src[2])
    before = nums(run, snap, 5, "对冲前")
    no = _offset(run, src[2], red, blue, amount)
    rows = nums(run, red_rows_sql(no, int(red[2]), int(blue[2])), 3, "9N 行")
    if rows != [Decimal(2), amount, -amount]:
        raise _Fail(f"9N 行（行数、红票借方、蓝票借方）应为 2、{amount}、{-amount}，实际 {rows}")
    want = [before[0] - amount, before[1] + amount, before[2] + amount, before[3] - amount, before[4] + 2]
    got = nums(run, snap, 5, "对冲后")
    if got != want:
        raise _Fail(f"对冲后（红票累计、蓝票累计、红票余额、蓝票余额、9N 行数）应为 {want}，实际 {got}")
    run.settle(1)
    back = nums(run, snap, 5, "取消后")
    if back != before:
        raise _Fail(f"取消红票对冲后应回到 {before}，实际 {back}")
    # 应收弃审、弃复、删除红票，弃审、删除退货单（_red_invoice 记下的五个清理动作），再核对都删掉了。
    run.settle(len(run.undo))
    gone = nums(run, red_gone_sql(inv, ret), 2, "清理后")
    if gone != [_ZERO, _ZERO]:
        raise _Fail(f"红票、退货单应已删除，实际表头行数 {gone}")
    return "PASS", f"{no}（红票 {red[1]} 对蓝票 {blue[1]}，{amount}）：9N 行、累计、余额都对，取消后复原，红票和退货单已删"


# ---- 汇兑损益 ----


def _batches(run: _Run, side: str, payload: dict) -> tuple[list[str], dict[str, Decimal]]:
    """处理号清单（先记下取消动作）和按单据类型分的差额合计（bill：应收单 / 应付单，close：收付款单，all：全部）。"""
    prefix, bill, close = _FX[side]
    raw = payload.get("batches")
    batches = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    nos = [str(item.get("cancel_no") or "") for item in batches]
    good = [no for no in nos if re.fullmatch(prefix + r"[0-9]{1,20}", no)]
    if good:
        run.undo.append((EXGAIN_CANCEL, {"flag": side, "cancel_nos": good}, "取消汇兑损益"))
    if not batches or len(good) != len(nos):
        raise _Fail(f"汇兑损益应有以 {prefix} 开头的处理号，实际 {nos[:5]}")
    sums = {"bill": _ZERO, "close": _ZERO, "all": _ZERO}
    for item in batches:
        diff = dec(str(item.get("diff")), "差额")
        kind = str(item.get("type") or "")
        sums["all"] += diff
        if kind.startswith(bill):
            sums["bill"] += diff
        elif kind == close:
            sums["close"] += diff
    return good, sums


def _calc(run: _Run, side: str, pick: list[str]) -> dict:
    rate = (dec(pick[2], "单据汇率") + 1).quantize(_STEP)
    body = {"flag": side, "currency": pick[1], "rate": float(rate), "partners": [pick[0]]}
    status, payload = run.send(EXGAIN, body)
    if tier2_refused(status, payload):
        raise _NotApplicable(f"汇兑损益只对测试账套开放（403 {payload.get('code')}）")
    if status == 404 and _MISSING in str(payload.get("message")):
        raise _NotApplicable("桥还没有 arap/exchange_gain 路由")
    if status != 200:
        raise _Fail(f"汇兑损益：{status} {payload.get('code')} {payload.get('message')}"[:240])
    return payload


def _check_rows(run: _Run, side: str, nos: list[str], sums: dict[str, Decimal]) -> int:
    count, local, orig = nums(run, fx_rows_sql(side, nos), 3, "9M 行")
    if count < len(nos) or local != sums["all"] or orig != _ZERO:
        raise _Fail(f"9M 行 {count}（批次 {len(nos)}）、本币合计 {local}（差额合计 {sums['all']}）、原币合计 {orig}")
    return int(count)


def _voucher(run: _Run, nos: list[str]) -> str:
    pl = run.cell(pl_code_sql(run.env.today))
    if not pl:
        raise _NotApplicable("科目表里没有 6603 开头、不带辅助核算的末级损益科目")
    pz = make_voucher(run, "AR", nos, {"pl_code": pl})
    check_gl(run, pz, "AR", "SY", [("ccode", pl, None, pl)])
    check_marks(run, nos, pz, True)
    drop_voucher(run, nos, pz)
    return pz


def exchange_gain_cycle(run: _Run, side: str) -> tuple[str, str]:
    pick = run.pick(fx_pick_sql(side, run.env.today), 3)
    if pick is None:
        raise _NotApplicable("挑不到有外币应收单 / 应付单余额的往来单位（造数 fx_ar_bill / fx_ap_bill，只在有种子数据的账套跑）")
    if side == "AR" and not run.cell(pl_code_sql(run.env.today)):
        raise _NotApplicable("科目表里没有 6603 开头、不带辅助核算的末级损益科目")
    snap = fx_snap_sql(side, pick[0], pick[1])
    before = nums(run, snap, 6, "计算前")
    nos, sums = _batches(run, side, _calc(run, side, pick))
    count = _check_rows(run, side, nos, sums)
    got = nums(run, snap, 6, "计算后")
    # diff 是 9M 行的借方减贷方。余额按单据的正常方向变：借方单据（应收单、付款单）加 diff，
    # 贷方单据（收款单、应付单）减 diff（实测：美元 20 从 7.1 调到 8.1，收款单和应付单的本币余额都由 142 变成 162）。
    sign = 1 if side == "AR" else -1
    want = [before[0] + count, before[1] + sign * sums["bill"], before[2], before[3] - sign * sums["close"], before[4]]
    if got[:5] != want:
        raise _Fail(f"计算后（9M 行数、单据本币余额、原币余额、收付款单本币余额、原币余额）应为 {want}，实际 {got[:5]}")
    pz = _voucher(run, nos) if side == "AR" else ""
    run.settle(1)
    back = nums(run, snap, 6, "取消后")
    if back != before:
        raise _Fail(f"取消汇兑损益后应回到 {before}，实际 {back}")
    made = f"，制单 {pz} 后取消" if pz else ""
    return "PASS", f"{pick[0]} {pick[1]} {len(nos)} 批，差额合计 {sums['all']}：9M 行与本币余额都对{made}，取消后复原"
