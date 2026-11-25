"""票据退回（9C）与应付票据的端到端用例。

note-return-cycle（应收，第一级）：登记 100.00 的票据（不分包，样本同 notes）→ 审核收款单
  → 部分退回 40.00 被拒（409「整张票据」）→ 整张退回预演不落库（余额、处理行、应收单都没有）→ 退回：处理号 CLAR…，
  处理行 9C（cCoVouchType R0、cCoVouchID = 应收单号、科目 = 应收控制科目、cBank 空、费用 0）；新应收单 R0（U8 组件保存后
  补写来源 50、来源号 = 票据号、余额 0、已审核（审核日期 = 退回日期）、表头借方、一行表体，往来明细里没有审核行）；它经 vouchers/verify
  审核、弃审都 409「单据由票据退回等处理生成…」；应收明细两行：R0 借方 iFlag 0（应收控制科目）、
  票据 50 贷方 iFlag 3（票据科目、单号 = 票据号），两行摘要「退回{客户名称}电子承兑」；票据余额 0；登记的收款单 48 不变；notes/get 列出该处理
  → 处理制单（PJ）：应收控制科目、票据科目各换到对方取负（借贷合计都是 −100.00），往来明细、处理行、应收单回写凭证号
  → 已制单取消 409 → 取消制单 → 取消退回：处理行、明细、应收单删光，余额回到 100.00 → 弃审收款单、删除票据。
note-return-split-cycle（应收）：登记分包票据（同 notes_split）→ 审核收款单 → 只退回子票 [起号, 起号 + 99]
  （1.00）409「分包票据退回须退回全部可用区间…」→ 退回全部可用区间（100.00）：处理行号段、应收单、票据行单号「票据号-起-止」、
  可用区间清空、余额 0 → 取消：可用区间回到登记时的整段、余额 100.00 → 弃审、删除票据。
ap-note-cycle（应付，第二级，只对测试账套开放）：先按票据号删除一张不存在的应付票据（预演）探测，403 test_account_only / feature_disabled 记 N/A
  → 挑供应商、部门、应付票据科目（基本科目 pjkm 应付一侧，没有时 2201 下的末级）、银行科目 → 登记预演不落库 → 登记：
  票据 cFlag AP、cLink AP50…、付款单 49 对上（来源 C / 50）→ 审核付款单 → 结算 PJJAP…：处理行 9A（标记 48、科目 = 银行）、
  应付明细票据行借方 → 制单（借应付票据、贷银行）→ 取消制单 → 取消结算 → 退回 CLAP…：新应付单 P0（表头贷方、已审核、不能经 vouchers/verify 审核弃审）、应付明细
  P0 贷方 + 票据借方 → 取消退回 → 弃审付款单 → 删除票据：票据、付款单都不在。

中途失败按记下的清理动作倒序补偿（取消制单 → 取消处理 → 弃审收付款单 → 删除票据）。步骤 kind 为 "custom"，NOTES_MORE_OPS
并入 ops_run；回读 SQL 在 notes_return_sql。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import NamedTuple

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _Fail, _guard, _NotApplicable, _Run
from co.e2e.casespec.arap_proc import CANCEL, DROP, VOUCHER, cancel_no, dec, nums, refuse_cancel
from co.e2e.casespec.arap_proc_sql import gl_net_sql
from co.e2e.casespec.notes import (
    CREATE,
    DELETE,
    GET,
    PROCESS,
    VERIFY,
    base,
    create,
    finish,
    new_note_no,
    refuse_partial,
    register,
    verify_receipt,
)
from co.e2e.casespec.notes_util import FACE, PART, ZERO, cash_items, expect, missing, money, row, send
from co.e2e.casespec.notes_return_sql import (
    ap_setup_sql,
    bill_sql,
    bill_type,
    close_sql,
    gl_sql,
    marks_sql,
    note_state_sql,
    proc_detail_sql,
    proc_sub_sql,
)
from co.e2e.casespec.notes_split import Split, avail, first_serial, nothing
from co.e2e.casespec.notes_split import check_registered as split_registered
from co.e2e.casespec.notes_split import verify as split_verify
from co.e2e.casespec.tier2 import tier2_refused

ONE = Decimal("1.00")
# 分包退回的子票张数（1.00）。
_SPAN = 100
_BILL = re.compile(r"^[0-9A-Za-z]{1,30}\Z")
_PZ = re.compile(r"^(AR|AP)[0-9]{1,20}\Z")
# note_state_sql 各列的位置。
_N_COUNT, _N_REMAIN, _N_AUDITED, _N_SUBS, _N_BILLS = 0, 3, 9, 10, 11
# 退回处理号前缀、响应里单据号与主键的键、生成的单据在 vouchers/verify 里的类型。
_RETURN = {"AR": ("CLAR", "r0_code", "r0_id", "ar_bill"), "AP": ("CLAP", "p0_code", "p0_id", "ap_bill")}
_GL_DIR = {"AR": "1", "AP": "0"}
# 生成的单据审核、弃审的拒绝原文（ArapCoSql.RefuseVerify）。
_BY_PROC = "单据由票据退回等处理生成"
# 分包票据部分退回的拒绝原文（NotesProcReturn.WholeRemainder）。
_WHOLE_RANGE = "分包票据退回须退回全部可用区间"


class Back(NamedTuple):
    """退回的响应：处理号、生成的应收（应付）单号与主键、往来单位名称（缺省摘要用）。"""

    pno: str
    code: str
    bill_id: int
    name: str


class Ret(NamedTuple):
    """一次退回要核对的值：flag、退回的响应、金额、往来控制科目、往来单位、票据科目、票据行单号。"""

    flag: str
    back: Back
    amount: Decimal
    ctrl: str
    partner: str
    km: str
    ref: str


# ---- 通用小工具 ----


def note_state(run: _Run, flag: str, no: str) -> list[str]:
    return row(run, note_state_sql(flag, no), 12, "票据")


def remain_is(run: _Run, flag: str, no: str, amount: Decimal, what: str) -> None:
    got = dec(note_state(run, flag, no)[_N_REMAIN], "余额")
    if got != amount:
        raise _Fail(f"{what}票据余额应为 {amount}，实际 {got}")


def control_km(run: _Run, close_id: int) -> tuple[str, list[str]]:
    """登记生成的收付款单的回读（退回前后核对不变）与它应收（应付）款行的科目（往来控制科目）。"""
    snap = row(run, close_sql(close_id), 5, "收付款单")
    if not snap[3]:
        raise _Fail(f"登记生成的收付款单 {close_id} 读不到应收（应付）款行科目")
    return snap[3], snap


def do_return(run: _Run, body: dict) -> Back:
    """退回；记下取消退回的清理动作。返回处理号、生成的应收（应付）单号与主键、往来单位名称。"""
    flag = body["flag"]
    prefix, key, id_key, _kind = _RETURN[flag]
    payload = send(run, PROCESS, body, "票据退回")
    pno = cancel_no(run, payload, prefix, (CANCEL, {"flag": flag}, "取消票据退回"))
    code, bill_id = str(payload.get(key) or ""), payload.get(id_key)
    if _BILL.fullmatch(code) is None or type(bill_id) is not int or bill_id <= 0:
        raise _Fail(f"票据退回的响应没有 {key} / {id_key}：{code[:40] or '空'} {bill_id}")
    note = payload.get("note")
    name = str(note.get("partner_name") or "") if isinstance(note, dict) else ""
    if not name:
        raise _Fail("票据退回的响应没有往来单位名称（note.partner_name）")
    return Back(pno, code, bill_id, name)


def check_return(run: _Run, r: Ret, span: tuple[int, int] | None = None) -> None:
    """处理行、生成的单据、两行往来明细，再核对生成的单据不能审核、弃审。应收单表头借方（bd_c 1），应付单贷方（0）；
    往来明细应收单行借方、应付单行贷方，两行摘要都是「退回{名称}电子承兑」。生成的单据已审核（审核日期 = 退回日期）但没有审核行。"""
    k, kind, amt = r.back, bill_type(r.flag), money(r.amount)
    sub = {0: "1", 1: "9C", 2: amt, 3: money(ZERO), 4: r.ctrl, 5: "", 6: kind, 7: k.code, 10: "0"}
    if span is not None:
        sub |= {8: str(span[0]), 9: str(span[1])}
    expect(row(run, proc_sub_sql(k.pno), 11, "处理行"), sub, f"处理行 {k.pno}")
    bill = {0: "1", 1: "50", 2: r.ref.split("-")[0], 3: "1", 4: amt, 5: money(ZERO), 6: r.partner, 7: r.ctrl}
    bill |= {8: _GL_DIR[r.flag], 9: "0", 10: "1", 11: "0", 12: run.env.today, 13: "1"}
    expect(row(run, bill_sql(r.flag, k.code), 14, "退回生成的单据"), bill, f"退回生成的 {kind} {k.code}")
    side = (amt, money(ZERO)) if r.flag == "AR" else (money(ZERO), amt)
    digest = f"退回{k.name}电子承兑"
    detail = {0: "2", 1: "1", 2: side[0], 3: side[1], 4: r.ctrl, 5: k.code}
    detail |= {6: "1", 7: side[1], 8: side[0], 9: r.km, 10: r.ref, 11: "0", 12: digest, 13: digest}
    expect(row(run, proc_detail_sql(r.flag, k.pno), 14, "往来明细"), detail, f"往来明细 {k.pno}")
    refuse_verify(run, r.flag, k)


def refuse_verify(run: _Run, flag: str, k: Back) -> None:
    """退回生成的单据由处理维护审核状态：vouchers/verify 的弃审、审核都应 409「单据由票据退回等处理生成…」。"""
    kind = _RETURN[flag][3]
    for action in ("unverify", "verify"):
        status, payload = run.send(VERIFY, {"type": kind, "id": k.bill_id, "action": action})
        message = str(payload.get("message"))
        if status != 409 or _BY_PROC not in message:
            raise _Fail(f"{kind} {k.code} 的 {action} 应 409「{_BY_PROC}」，实际 {status} {message}"[:240])


def gone(run: _Run, flag: str, pno: str, code: str = "") -> None:
    """取消后处理行、两张往来明细里该处理号的行都不在；给了单据号时该单据的表头、表体、审核行也不在。"""
    sub = row(run, proc_sub_sql(pno), 11, "处理行")
    detail = row(run, proc_detail_sql(flag, pno), 14, "往来明细")
    left = [sub[0], detail[0], detail[11]]
    if code:
        bill = row(run, bill_sql(flag, code), 14, "退回生成的单据")
        left += [bill[0], bill[10], bill[11]]
    if any(item != "0" for item in left):
        raise _Fail(f"取消 {pno} 后仍有处理行、明细或单据（{'、'.join(left)}）")


def listed(run: _Run, flag: str, no: str, pno: str, style: str) -> None:
    kind = "ar_note" if flag == "AR" else "ap_note"
    subs = run.ok(GET, {"type": kind, "code": no}, "读取票据").get("subs") or []
    if not any(isinstance(s, dict) and s.get("cancel_no") == pno and s.get("style") == style for s in subs):
        raise _Fail(f"notes/get 的处理记录里没有 {style} {pno}")


# ---- 处理制单 ----


def make_voucher(run: _Run, flag: str, pno: str) -> str:
    """处理制单（一个处理号）；409 现金流量项目推不出时带 cash_items 重试一次。成功后记下取消制单。"""
    body: dict = {"flag": flag, "cancel_nos": [pno]}
    status, payload = run.send(VOUCHER, body)
    items = cash_items(status, payload)
    if items is not None:
        status, payload = run.send(VOUCHER, dict(body, cash_items=items))
    if status != 200:
        raise _Fail(f"处理制单：{status} {payload.get('code')} {payload.get('message')}"[:240])
    pz = str(payload.get("pz_id") or "")
    if _PZ.fullmatch(pz) is None or not pz.startswith(flag):
        raise _Fail("处理制单的响应没有外部业务号：" + pz[:40])
    run.undo.append((DROP, {"flag": flag, "pz_id": pz}, "取消制单"))
    return pz


def check_gl(run: _Run, flag: str, pz: str, total: Decimal, nets: list[tuple[str, Decimal]]) -> None:
    rows, count, bad, md, mc = nums(run, gl_sql(flag, pz), 5, "凭证")
    if rows < 2 or count != 1 or bad != 0 or md != total or mc != total:
        raise _Fail(f"凭证 {pz}：{rows} 行、{count} 张、来源不是 {flag}/PJ 的 {bad} 行、借 {md} 贷 {mc}，期望借贷各 {total}")
    for code, want in nets:
        parts = row(run, gl_net_sql(pz, "ccode", code), 4, f"凭证科目 {code} ")
        net, lines = dec(parts[0], "凭证净额"), dec(parts[1], "凭证行数")
        if lines < 1 or net != want:
            raise _Fail(f"凭证 {pz} 里科目 {code} 的行 {lines} 行、净额 {net}，期望 {want}")


def check_marks(run: _Run, flag: str, pno: str, pz: str, bills: int) -> None:
    """bills 为生成的单据应回写的张数；为 -1 时核对已全部清空。"""
    total, marked, subs, gl, got = nums(run, marks_sql(flag, pno, pz), 5, "制单回写")
    if bills >= 0 and (total < 1 or marked != total or subs != 1 or gl < 2 or got != bills):
        raise _Fail(f"制单后明细 {total}、回写齐的 {marked}、处理行回写 {subs}、凭证行 {gl}、单据回写 {got}（应 {bills}）")
    if bills < 0 and (marked != 0 or subs != 0 or gl != 0 or got != 0):
        raise _Fail(f"取消制单后仍有回写 {marked}、处理行回写 {subs}、凭证行 {gl}、单据回写 {got}")


def voucher_round(run: _Run, flag: str, pno: str, total: Decimal, nets: list[tuple[str, Decimal]]) -> str:
    """制单 → 核对凭证与回写 → 已制单取消 409 → 取消制单 → 回写清空。返回外部业务号。"""
    pz = make_voucher(run, flag, pno)
    check_gl(run, flag, pz, total, nets)
    check_marks(run, flag, pno, pz, 1 if pno.startswith("CL") else 0)
    refuse_cancel(run, flag, pno)
    run.settle(1)
    check_marks(run, flag, pno, pz, -1)
    return pz


# ---- 应收票据退回 ----


def return_cycle(run: _Run) -> tuple[str, str]:
    b = base(run)
    no, receipt = register(run, b, "06")
    verify_receipt(run, no, receipt)
    ctrl, before = control_km(run, receipt)
    body = {"flag": "AR", "op": "return", "note": no, "amount": float(FACE)}
    refuse_partial(run, dict(body, amount=float(PART)), "CLAR")
    send(run, PROCESS, dict(body, dry_run=True), "预演票据退回")
    expect(note_state(run, "AR", no), {_N_REMAIN: money(FACE), _N_SUBS: "0", _N_BILLS: "0"}, "预演退回后票据")
    k = do_return(run, body)
    check_return(run, Ret("AR", k, FACE, ctrl, b.customer, b.km, no))
    expect(note_state(run, "AR", no), {_N_REMAIN: money(ZERO), _N_SUBS: "1", _N_BILLS: "1"}, f"退回 {k.pno} 后票据")
    if row(run, close_sql(receipt), 5, "收款单") != before:
        raise _Fail(f"退回后登记的收款单 {receipt} 变了")
    listed(run, "AR", no, k.pno, "9C")
    pz = voucher_round(run, "AR", k.pno, -FACE, [(ctrl, FACE), (b.km, -FACE)])
    run.settle(1)
    gone(run, "AR", k.pno, k.code)
    expect(note_state(run, "AR", no), {_N_REMAIN: money(FACE), _N_SUBS: "0", _N_BILLS: "0"}, f"取消 {k.pno} 后票据")
    finish(run, no)
    return "PASS", (
        f"{k.pno}（票据 {no} 整张退回 {FACE}）：部分金额被拒，预演不落库，处理行 9C、应收单 {k.code}（已审核、没有审核行、余额 0、"
        f"不能经 vouchers/verify 审核弃审）、两行明细与摘要、余额都对，收款单不变；制单 {pz} 换方向取负，取消后复原，票据已删"
    )


def refuse_split_partial(run: _Run, s: Split, body: dict) -> None:
    """分包票据只退回一段子票应 409「分包票据退回须退回全部可用区间…」。意外成功时记下取消退回再记 FAIL；
    结果不明时回读余额，变了同样记 FAIL（数据可能残留，留给快照还原）。"""
    status, payload = run.send(PROCESS, body)
    if missing(status, payload):
        raise _NotApplicable("接口还没有 notes/process 路由")
    if status == 200 and payload.get("cancel_no"):
        cancel_no(run, payload, "CLAR", (CANCEL, {"flag": "AR"}, "取消票据退回"))
        raise _Fail("分包票据部分退回应 409，实际 200")
    message = str(payload.get("message"))
    if status != 409 and s.state(run)[5] != money(FACE):
        raise _Fail(f"分包票据部分退回结果不明（{status}），票据余额已变（数据可能残留）")
    if status != 409 or _WHOLE_RANGE not in message:
        raise _Fail(f"分包票据部分退回应 409「{_WHOLE_RANGE}」，实际 {status} {message}"[:240])


def return_split_cycle(run: _Run) -> tuple[str, str]:
    b = base(run)
    s = Split(new_note_no("07"), first_serial())
    receipt = create(run, s.body(run, b))
    split_registered(run, s)
    split_verify(run, s, receipt)
    ctrl, _snap = control_km(run, receipt)
    body = {"flag": "AR", "op": "return", "note": s.no, "amount": float(FACE), "sub_start": s.start, "sub_end": s.end}
    hi = s.start + _SPAN - 1
    refuse_split_partial(run, s, dict(body, amount=float(ONE), sub_end=hi))
    k = do_return(run, body)
    check_return(run, Ret("AR", k, FACE, ctrl, b.customer, b.km, f"{s.no}-{s.start}-{s.end}"), (s.start, s.end))
    empty = {5: money(ZERO), 6: "0", 7: "", 8: "", 9: money(ZERO), 10: money(ZERO)}
    expect(s.state(run), empty, f"退回 {k.pno} 后可用区间")
    run.settle(1)
    gone(run, "AR", k.pno, k.code)
    expect(s.state(run), {5: money(FACE)} | avail(s.start, s.end, FACE), f"取消 {k.pno} 后可用区间")
    run.settle(2)
    nothing(run, s, "删除票据", drop=False)
    return "PASS", (
        f"{k.pno}（分包票据 {s.no}）：只退一段子票 409，退回全部可用区间 {s.start}-{s.end} 后处理行号段、应收单 {k.code}、"
        "票据行单号后缀都对、可用区间清空，取消后复原，票据已删"
    )


# ---- 应付票据 ----


def probe_ap(run: _Run) -> None:
    """应付票据只对测试账套开放：预演删除一张不存在的应付票据，403 test_account_only / feature_disabled 记 N/A（测试账套上是 404 票据不存在）。"""
    status, payload = run.send(DELETE, {"flag": "AP", "note_no": "E2E000000000000", "dry_run": True})
    if tier2_refused(status, payload):
        raise _NotApplicable(f"应付票据只对测试账套开放（403 {payload.get('code')}）")
    if missing(status, payload):
        raise _NotApplicable("接口还没有 notes/delete 路由")


def ap_body(run: _Run, pick: list[str], no: str) -> dict:
    vendor, dept, person, note_km, _bank = pick
    today = run.env.today
    body = {
        "flag": "AP", "note_no": no, "settle_code": "301", "amount": float(FACE), "sign_date": today,
        "receipt_date": today, "expire_date": (date.fromisoformat(today) + timedelta(days=180)).isoformat(),
        "vendor": vendor, "dept": dept, "receiver": "E2E 收款人", "note_km": note_km,
    }
    if person:
        body["person"] = person
    return body


def ap_register(run: _Run, body: dict) -> int:
    """登记应付票据；成功或失败后票据仍在时都记下删除票据的清理动作。返回付款单主键。"""
    no = body["note_no"]
    status, payload = run.send(CREATE, body)
    if status == 200 or note_state(run, "AP", no)[_N_COUNT] != "0":
        note_id = payload.get("note_id")
        # 清理失败的提示是「动作 + 参数」，动作文字只写到「主键」「票据号」为止（同 notes.drop_note）。
        if type(note_id) is int and note_id > 0:
            run.undo.append((DELETE, {"flag": "AP", "id": note_id}, f"删除应付票据 {no}，主键"))
        else:
            run.undo.append((DELETE, {"flag": "AP", "note_no": no}, "删除应付票据，票据号"))
    if status != 200:
        raise _Fail(f"登记应付票据：{status} {payload.get('code')} {payload.get('message')}"[:240])
    pay = payload.get("receipt_id")
    if type(pay) is not int or pay <= 0 or payload.get("note_no") != no:
        raise _Fail(f"登记应付票据的响应没有付款单主键或票据号不符：{pay} {payload.get('note_no')}")
    return pay


def ap_registered(run: _Run, pick: list[str], no: str, pay: int) -> None:
    vendor, _dept, _person, note_km, _bank = pick
    want = {0: "1", 1: "1", 2: money(FACE), 3: money(FACE), 4: note_km, 5: vendor, 6: str(pay), 7: "1", 8: "1", 9: "0"}
    expect(note_state(run, "AP", no), want, f"登记后应付票据 {no}")
    ref = {"type": "ap_payment", "id": pay}
    run.ok(VERIFY, dict(ref, action="verify"), "审核票据的付款单")
    run.undo.append((VERIFY, dict(ref, action="unverify"), "弃审票据的付款单"))
    if note_state(run, "AP", no)[_N_AUDITED] != "1":
        raise _Fail(f"付款单 {pay} 审核后回读不是已审核")


def ap_settle(run: _Run, pick: list[str], no: str) -> str:
    note_km, bank = pick[3], pick[4]
    body = {"flag": "AP", "op": "settle", "note": no, "amount": float(FACE), "bank_code": bank}
    pno = cancel_no(run, send(run, PROCESS, body, "应付票据结算"), "PJJAP", (CANCEL, {"flag": "AP"}, "取消票据结算"))
    sub = {0: "1", 1: "9A", 2: money(FACE), 3: money(ZERO), 4: bank, 6: "48", 10: "0"}
    expect(row(run, proc_sub_sql(pno), 11, "处理行"), sub, f"处理行 {pno}")
    detail = {0: "1", 1: "0", 6: "1", 7: money(FACE), 8: money(ZERO), 9: note_km, 10: no, 11: "0"}
    expect(row(run, proc_detail_sql("AP", pno), 14, "往来明细"), detail, f"往来明细 {pno}")
    remain_is(run, "AP", no, ZERO, f"结算 {pno} 后")
    listed(run, "AP", no, pno, "9A")
    pz = voucher_round(run, "AP", pno, FACE, [(note_km, FACE), (bank, -FACE)])
    run.settle(1)
    gone(run, "AP", pno)
    remain_is(run, "AP", no, FACE, f"取消 {pno} 后")
    return f"{pno}（制单 {pz}）"


def ap_return(run: _Run, pick: list[str], no: str, ctrl: str) -> str:
    k = do_return(run, {"flag": "AP", "op": "return", "note": no, "amount": float(FACE)})
    check_return(run, Ret("AP", k, FACE, ctrl, pick[0], pick[3], no))
    remain_is(run, "AP", no, ZERO, f"退回 {k.pno} 后")
    run.settle(1)
    gone(run, "AP", k.pno, k.code)
    remain_is(run, "AP", no, FACE, f"取消 {k.pno} 后")
    return f"{k.pno}（应付单 {k.code}）"


def ap_cycle(run: _Run) -> tuple[str, str]:
    probe_ap(run)
    pick = run.pick(ap_setup_sql(run.env.today), 5)
    if pick is None:
        raise _NotApplicable("挑不到付款单的供应商与部门、应付票据科目（pjkm 或 2201 下的末级）、末级银行科目，或本月应收应付已结账")
    no = new_note_no("08")
    body = ap_body(run, pick, no)
    send(run, CREATE, dict(body, dry_run=True), "预演登记应付票据")
    state = note_state(run, "AP", no)
    if state[_N_COUNT] != "0" or state[8] != "0":
        raise _Fail("预演登记后应付票据或付款单落库了")
    pay = ap_register(run, body)
    ap_registered(run, pick, no, pay)
    ctrl, before = control_km(run, pay)
    settled = ap_settle(run, pick, no)
    returned = ap_return(run, pick, no, ctrl)
    if row(run, close_sql(pay), 5, "付款单") != before:
        raise _Fail(f"结算、退回并取消后付款单 {pay} 变了")
    run.settle(2)
    state = note_state(run, "AP", no)
    if state[_N_COUNT] != "0" or state[8] != "0":
        raise _Fail(f"删除后应付票据 {state[_N_COUNT]} 张、付款单 {state[8]} 张仍在")
    return "PASS", (
        f"应付票据 {no}（{pick[0]}，{FACE}）：预演不落库，票据与付款单 {pay} 对上；结算 {settled}、退回 {returned} 的处理行、"
        "明细、余额都对，取消后复原；弃审、删除后都不在"
    )


# ---- 接线 ----


def _op(body: Callable[[_Run], tuple[str, str]]) -> Callable[[Env, CaseSpec, Step], tuple[str, str]]:
    def op(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
        del step
        return _guard(env, spec, body)

    return op


NOTES_MORE_OPS = {
    "notes_return": _op(return_cycle),
    "notes_return_split": _op(return_split_cycle),
    "notes_ap": _op(ap_cycle),
}

# 同 notes：票据登记串行（notes:AR / notes:AP）、新建并审核收付款单、查结账标志；退回另生成应收单（应付单）。
_AR = ("notes:AR", "new:ar_receipt", "w:ar_receipt", "period:close", "new:ar_bill")
_AP = ("notes:AP", "new:ap_payment", "w:ap_payment", "period:close", "new:ap_bill")
_GL = ("gl", "new:gl:转")
_ROUTES = (CREATE, GET, VERIFY, PROCESS, CANCEL, DELETE)
_CASES = (
    ("note-return-cycle", "notes_return", _AR + _GL, _ROUTES + (VOUCHER, DROP),
     "票据退回：部分金额 409；整张退回预演不落库；处理行 9C、新应收单 R0（来源 50、已审核无审核行、余额 0、不能审核弃审）、两行明细与摘要、"
     "余额 0、收款单不变都对；PJ 凭证换方向取负、回写齐全；已制单取消 409；取消制单、取消退回后复原，弃审收款单、删票据"),
    ("note-return-split-cycle", "notes_return_split", _AR, _ROUTES,
     "分包票据退回：只退一段子票 409；退回全部可用区间后处理行号段、应收单、票据行单号后缀都对、可用区间清空；取消后复原，删票据"),
    ("ap-note-cycle", "notes_ap", _AP + _GL + ("new:gl:付",), _ROUTES + (VOUCHER, DROP),
     "应付票据（仅测试账套）：登记预演不落库；票据与付款单 49 对上；结算 9A 处理行、明细借方、PJ 凭证借应付票据贷银行；"
     "退回 9C 生成应付单 P0、两行明细；各自取消后复原，弃审付款单、删票据"),
)


def notes_more_cases() -> list[CaseSpec]:
    # 应收两条要现成的应收票据作样本（挑不到 N/A），应付一条要测试账套（非测试账套 403 → N/A）。
    return [
        CaseSpec(
            name, title, (), (Step("custom", sql_name=op, id_from="none"),), True, optional=True,
            tags=frozenset(tags), routes=routes,
        )
        for name, op, tags, routes, title in _CASES
    ]
