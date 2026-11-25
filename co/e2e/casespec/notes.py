"""应收票据的端到端用例（要有现成的票据、结算方式 301、票据科目 pjkm 和用过的结算银行科目）。

每条用例自己挑样本（挑不到记 N/A），每一步按回读 SQL 核对票据、收款单、票据处理行、往来明细和凭证，最后还原到用例之前；
中途失败时按记下的清理动作倒序补偿（取消制单 → 取消处理 → 弃审收款单 → 删除票据），补偿失败记 FAIL，留给快照还原。
登记（NoteSave）与删除（DeletePJ）由 U8 自行提交，用例自己删掉登记的票据（响应带票据主键时按主键删）。
用例登记的票据都不分包（bsubpackage=0），桥只允许整张处理：先断言处理部分金额 40.00 被拒（409 state_mismatch「整张票据」），
再按票面 100.00 处理全部余额。

note-register-cycle：notes/create 预演不落库 → 登记 100.00（票据号 E2E 加时间，客户、部门、业务员、收款人取最近一张票据）
  → AP_Note（cLink AR50+票据号、结算方式 301、票面与余额、票据科目 = pjkm、交票客户、iCloseID 指向收款单）与收款单 48
  （来源 C / 50、来源号与票据号）对上 → notes/get 读到 → notes/delete → 票据和收款单都不在。
note-settle-cycle：登记 → 审核收款单 → 部分结算 40.00 被拒 → 整张结算 100.00 预演不落库 → 结算：处理号 PJJAR…，
  处理行 9A（金额、银行科目）、应收明细票据行（50、iFlag 3、贷方 100.00、票据科目、单号 = 票据号、交票客户）、余额 0.00，
  notes/get 列出该处理 → 处理制单（来源 AR / PJ，借银行 100.00、贷票据科目 100.00，往来明细与处理行回写凭证号）
  → 已制单取消 409 → 取消制单 → 取消处理（处理行、明细删光，余额回到 100.00）→ 弃审收款单 → 删除票据。
note-discount-cycle：同上，整张贴现 100.00、费用 1.00：处理号 PJTAR…，处理行金额为净额 99.00、费用 1.00，明细贷方为票面 100.00；
  凭证借银行 99.00、贴现费用科目（pjyfrzkm）净借 1.00、贷票据科目 100.00。
note-endorse-cycle / note-split-settle-cycle 在 notes_more。

步骤 kind 为 "custom"，NOTES_OPS 并入 ops_run；回读 SQL 在 notes_sql。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import NamedTuple

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _Fail, _guard, _NotApplicable, _Run
from co.e2e.casespec.arap_proc import CANCEL, DROP, VOUCHER, cancel_no, dec, nums, refuse_cancel
from co.e2e.casespec.arap_proc_sql import gl_net_sql
from co.e2e.casespec.notes_sql import (
    detail_sql,
    gl_sql,
    marks_sql,
    note_id_sql,
    note_proc_sql,
    note_sql,
    setup_sql,
    sub_sql,
)
from co.e2e.casespec.notes_util import FACE, FEE, PART, ZERO, expect, missing, money, proc_voucher, row, send

CREATE = "/v1/notes/create"
DELETE = "/v1/notes/delete"
GET = "/v1/notes/get"
PROCESS = "/v1/notes/process"
VERIFY = "/v1/vouchers/verify"
# note_sql 各列的位置。
_N_COUNT, _N_REMAIN, _N_TIED, _N_RECEIPTS, _N_AUDITED = 0, 4, 8, 9, 10


class Base(NamedTuple):
    """setup_sql 挑出的样本：交票客户、部门、业务员（可空）、收款人、票据科目、结算银行科目、贴现费用科目（可空）。"""

    customer: str
    dept: str
    person: str
    receiver: str
    km: str
    bank: str
    fee_km: str


# ---- 小工具（通用的在 notes_util） ----


def base(run: _Run) -> Base:
    row = run.pick(setup_sql(run.env.today), 7)
    if row is None:
        raise _NotApplicable(
            "挑不到可作样本的应收票据（非期初、结算方式 301、有收款单）、票据科目 pjkm 或结算用过的末级银行科目，"
            "或本月应收应付已结账（没有现成的票据时记 N/A）"
        )
    return Base(*row)


def note_state(run: _Run, no: str) -> list[str]:
    return row(run, note_sql(no), 11, "票据")


def new_note_no(tail: str) -> str:
    return "E2E" + datetime.now().strftime("%Y%m%d%H%M%S%f") + tail


# ---- 登记、审核收款单、清理 ----


def create_body(run: _Run, b: Base, no: str) -> dict:
    today = run.env.today
    body = {
        "flag": "AR", "note_no": no, "settle_code": "301", "amount": float(FACE), "sign_date": today,
        "receipt_date": today, "expire_date": (date.fromisoformat(today) + timedelta(days=180)).isoformat(),
        "customer": b.customer, "dept": b.dept, "receiver": b.receiver,
    }
    if b.person:
        body["person"] = b.person
    return body


def create(run: _Run, body: dict) -> int:
    """登记；成功或失败后票据仍在时都记下删除票据的清理动作。返回收款单主键。"""
    no = body["note_no"]
    status, payload = run.send(CREATE, body)
    if missing(status, payload):
        raise _NotApplicable("接口还没有 notes/create 路由")
    if status == 200 or note_state(run, no)[_N_COUNT] != "0":
        run.undo.append(drop_note(run, payload, no))
    if status != 200:
        raise _Fail(f"登记票据：{status} {payload.get('code')} {payload.get('message')}"[:240])
    receipt = payload.get("receipt_id")
    if type(receipt) is not int or receipt <= 0 or payload.get("note_no") != no:
        raise _Fail(f"登记票据的响应没有收款单主键或票据号不符：{receipt} {payload.get('note_no')}")
    return receipt


def drop_note(run: _Run, payload: dict, no: str) -> tuple[str, dict, str]:
    """删除票据的清理动作，优先按票据主键删。清理失败的提示是「动作 + 参数」（主键，没有时票据号），
    所以动作文字只写到「主键」「票据号」为止，不再重复参数。
    主键取登记响应的 note_id；没有（登记失败但票据已落库）就回读；仍读不到才按票据号删。"""
    note_id = payload.get("note_id")
    if type(note_id) is not int or note_id <= 0:
        cell = run.cell(note_id_sql(no))
        note_id = int(cell) if cell.isdigit() else 0
    if note_id > 0:
        return DELETE, {"flag": "AR", "id": note_id}, f"删除票据 {no}，主键"
    return DELETE, {"flag": "AR", "note_no": no}, "删除票据（主键未读到），票据号"


def check_registered(run: _Run, b: Base, no: str, receipt: int) -> None:
    want = ["1", "1", "301", money(FACE), money(FACE), b.km, b.customer, str(receipt), "1", "1", "0"]
    expect(note_state(run, no), dict(enumerate(want)), f"登记后票据 {no}")


def register(run: _Run, b: Base, tail: str) -> tuple[str, int]:
    no = new_note_no(tail)
    receipt = create(run, create_body(run, b, no))
    check_registered(run, b, no, receipt)
    return no, receipt


def verify_receipt(run: _Run, no: str, receipt: int) -> None:
    ref = {"type": "ar_receipt", "id": receipt}
    run.ok(VERIFY, dict(ref, action="verify"), "审核票据的收款单")
    run.undo.append((VERIFY, dict(ref, action="unverify"), "弃审票据的收款单"))
    if note_state(run, no)[_N_AUDITED] != "1":
        raise _Fail(f"收款单 {receipt} 审核后回读不是已审核")


def finish(run: _Run, no: str) -> None:
    """弃审收款单、删除票据（最后记下的两个清理动作），再核对票据和收款单都不在。"""
    run.settle(2)
    state = note_state(run, no)
    if state[_N_COUNT] != "0" or state[_N_RECEIPTS] != "0":
        raise _Fail(f"删除后票据 {state[_N_COUNT]} 张、收款单 {state[_N_RECEIPTS]} 张仍在")


def listed(run: _Run, no: str, cancel: str, style: str) -> None:
    payload = run.ok(GET, {"type": "ar_note", "code": no}, "读取票据")
    subs = payload.get("subs") or []
    if not any(isinstance(s, dict) and s.get("cancel_no") == cancel and s.get("style") == style for s in subs):
        raise _Fail(f"notes/get 的处理记录里没有 {style} {cancel}")


# ---- 处理与回读 ----


def process(run: _Run, body: dict, prefix: str) -> str:
    payload = send(run, PROCESS, body, "票据处理 " + str(body.get("op")))
    return cancel_no(run, payload, prefix, (CANCEL, {"flag": "AR"}, "取消票据处理"))


def refuse_partial(run: _Run, body: dict, prefix: str) -> None:
    """非分包票据处理部分金额应 409 state_mismatch「整张票据须一次处理全部金额」。
    意外成功时记下取消处理的清理动作再记 FAIL；结果不明（既非 409 也非 200，或 200 没有处理号）时回读余额，
    余额变了就登记清理再记 FAIL（unclear）。"""
    status, payload = run.send(PROCESS, body)
    if missing(status, payload):
        raise _NotApplicable("接口还没有 notes/process 路由")
    if status == 200 and payload.get("cancel_no"):
        cancel_no(run, payload, prefix, (CANCEL, {"flag": "AR"}, "取消票据处理"))
        raise _Fail(f"非分包票据处理部分金额 {body.get('amount')} 应 409，实际 200")
    if status != 409:
        unclear(run, str(body.get("note")), prefix)
    message = str(payload.get("message"))
    if status != 409 or payload.get("code") != "state_mismatch" or "整张票据" not in message:
        raise _Fail(f"非分包票据处理部分金额应 409 state_mismatch「整张票据」，实际 {status} {payload.get('code')} {message}"[:240])


def unclear(run: _Run, no: str, prefix: str) -> None:
    """部分金额请求结果不明：余额仍是票面就返回；变了按回读的最近处理号记下取消处理，再记 FAIL。"""
    if dec(note_state(run, no)[_N_REMAIN], "余额") == FACE:
        return
    count, last = row(run, note_proc_sql(no), 2, "票据处理号")
    if re.fullmatch(prefix + r"[0-9]{1,20}", last) is None:
        raise _Fail(f"部分金额请求结果不明，票据余额已变，处理行 {count} 行但读不到 {prefix} 处理号（数据可能残留，留给快照还原）")
    run.undo.append((CANCEL, {"flag": "AR", "cancel_no": last}, "取消票据处理"))
    raise _Fail("部分金额请求结果不明，票据余额已变（数据可能残留，已登记清理）")


def gone(run: _Run, no: str) -> None:
    sub = row(run, sub_sql(no), 9, "处理行")
    detail = row(run, detail_sql(no), 12, "往来明细")
    if sub[0] != "0" or detail[0] != "0" or detail[7] != "0":
        raise _Fail(f"取消 {no} 后仍有处理行 {sub[0]}、应收明细 {detail[0]}、应付明细 {detail[7]}")


def check_gl(run: _Run, pz: str, total: Decimal, nets: list[tuple[str, Decimal]]) -> None:
    """一张凭证、来源 AR / PJ、借贷都等于 total；nets 每项（科目、借减贷净额）。"""
    rows, count, bad, md, mc = nums(run, gl_sql(pz), 5, "凭证")
    if rows < 2 or count != 1 or bad != 0 or md != total or mc != total:
        raise _Fail(f"凭证 {pz}：{rows} 行、{count} 张、来源不是 AR/PJ 的 {bad} 行、借 {md} 贷 {mc}，期望借贷各 {total}")
    for code, want in nets:
        parts = row(run, gl_net_sql(pz, "ccode", code), 4, f"凭证科目 {code} ")
        net, lines = dec(parts[0], "凭证净额"), dec(parts[1], "凭证行数")
        if lines < 1 or net != want:
            raise _Fail(f"凭证 {pz} 里科目 {code} 的行 {lines} 行、净额 {net}，期望 {want}")


def check_marks(run: _Run, no: str, pz: str, made: bool) -> None:
    total, marked, subs, gl = nums(run, marks_sql(no, pz), 4, "制单回写")
    if made and (total < 1 or marked != total or subs != 1 or gl < 2):
        raise _Fail(f"制单后明细 {total}、回写齐的 {marked}、处理行回写 {subs}、凭证行 {gl}")
    if not made and (marked != 0 or subs != 0 or gl != 0):
        raise _Fail(f"取消制单后仍有回写 {marked}、处理行回写 {subs}、凭证行 {gl}")


def voucher_round(run: _Run, no: str, total: Decimal, nets: list[tuple[str, Decimal]]) -> str:
    """处理制单（现金流量项目推不出时带 cash_items 重试）→ 核对凭证与回写 → 已制单取消 409 → 取消制单 → 回写清空。
    返回外部业务号，用过 cash_items 时附上「（指定现金流量项目 科目→项目）」。"""
    pz, items = proc_voucher(run, no)
    check_gl(run, pz, total, nets)
    check_marks(run, no, pz, True)
    refuse_cancel(run, "AR", no)
    run.settle(1)
    check_marks(run, no, pz, False)
    picked = "、".join(f"{code}→{item}" for code, item in (items or {}).items())
    return pz + (f"（指定现金流量项目 {picked}）" if picked else "")


# ---- 用例 ----


def register_cycle(run: _Run) -> tuple[str, str]:
    b = base(run)
    no = new_note_no("01")
    body = create_body(run, b, no)
    send(run, CREATE, dict(body, dry_run=True), "预演登记票据")
    state = note_state(run, no)
    if state[_N_COUNT] != "0" or state[_N_RECEIPTS] != "0":
        raise _Fail("预演登记后票据或收款单落库了")
    receipt = create(run, body)
    check_registered(run, b, no, receipt)
    head = run.ok(GET, {"type": "ar_note", "code": no}, "读取票据").get("head") or {}
    if head.get("code") != no or str(head.get("close_id")) != str(receipt) or dec(str(head.get("amount")), "票面") != FACE:
        raise _Fail(f"notes/get 的表头不符：{head.get('code')} {head.get('close_id')} {head.get('amount')}")
    run.settle(1)
    state = note_state(run, no)
    if state[_N_COUNT] != "0" or state[_N_RECEIPTS] != "0":
        raise _Fail(f"删除后票据 {state[_N_COUNT]} 张、收款单 {state[_N_RECEIPTS]} 张仍在")
    return "PASS", f"票据 {no}（{b.customer}，{FACE}）：预演不落库，票据与收款单 {receipt} 对上，读取、删除后都不在"


# op → 处理号前缀、处理方式、票据号尾巴。
_OPS = {"settle": ("PJJAR", "9A", "02"), "discount": ("PJTAR", "9D", "03")}


def _proc_body(op: str, no: str, b: Base, amount: Decimal) -> dict:
    body = {"flag": "AR", "op": op, "note": no, "amount": float(amount), "bank_code": b.bank}
    if op == "discount":
        body["expense"] = float(FEE)
    return body


def _check_proc(run: _Run, pno: str, op: str, b: Base, no: str) -> None:
    style = _OPS[op][1]
    fee = FEE if op == "discount" else ZERO
    sub = row(run, sub_sql(pno), 9, "处理行")
    expect(sub, {0: "1", 1: style, 2: money(FACE - fee), 3: money(fee), 4: b.bank, 6: "0"}, f"处理行 {pno}")
    detail = row(run, detail_sql(pno), 12, "往来明细")
    want = {0: "1", 1: "1", 2: money(ZERO), 3: money(FACE), 4: b.km, 5: no, 6: b.customer, 7: "0"}
    expect(detail, want, f"往来明细 {pno}")
    if dec(note_state(run, no)[_N_REMAIN], "余额") != ZERO:
        raise _Fail(f"整张处理后票据余额应为 {ZERO}")


def proc_cycle(run: _Run, op: str) -> tuple[str, str]:
    b = base(run)
    if op == "discount" and not b.fee_km:
        raise _NotApplicable("应收款管理没有设置贴现费用科目（基本科目 pjyfrzkm）")
    prefix, style, tail = _OPS[op]
    no, receipt = register(run, b, tail)
    verify_receipt(run, no, receipt)
    refuse_partial(run, _proc_body(op, no, b, PART), prefix)
    body = _proc_body(op, no, b, FACE)
    send(run, PROCESS, dict(body, dry_run=True), "预演票据处理")
    if dec(note_state(run, no)[_N_REMAIN], "余额") != FACE:
        raise _Fail("预演票据处理后余额变了")
    pno = process(run, body, prefix)
    _check_proc(run, pno, op, b, no)
    listed(run, no, pno, style)
    net = FACE - (FEE if op == "discount" else ZERO)
    nets = [(b.bank, net), (b.km, -FACE)] + ([(b.fee_km, FEE)] if op == "discount" else [])
    pz = voucher_round(run, pno, net, nets)
    run.settle(1)
    gone(run, pno)
    if dec(note_state(run, no)[_N_REMAIN], "余额") != FACE:
        raise _Fail(f"取消 {pno} 后票据余额没有回到 {FACE}")
    finish(run, no)
    return "PASS", (
        f"{pno}（票据 {no} 整张 {FACE}）：部分金额被拒，预演不落库，处理行、明细、余额、制单 {pz} 都对，取消后复原，票据已删"
    )


# ---- 接线 ----


def _op(body: Callable[[_Run], tuple[str, str]]) -> Callable[[Env, CaseSpec, Step], tuple[str, str]]:
    def op(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
        del step
        return _guard(env, spec, body)

    return op


def _later(name: str) -> Callable[[Env, CaseSpec, Step], tuple[str, str]]:
    # 背书、分包结算在 notes_more（它引用本模块的小工具），调用时再引入，避免循环引用。
    def op(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
        del step
        from co.e2e.casespec import notes_more

        return _guard(env, spec, getattr(notes_more, name))

    return op


NOTES_OPS = {
    "notes_register": _op(register_cycle),
    "notes_settle": _op(lambda run: proc_cycle(run, "settle")),
    "notes_discount": _op(lambda run: proc_cycle(run, "discount")),
    "notes_endorse": _later("endorse_cycle"),
    "notes_split_settle": _later("split_cycle"),
}

# 登记新建收款单（48 编号）、审核它；票据登记与删除在桥里串行（note:AR），这里用同名标签互斥；
# 处理都查应收、应付期间未结账，与临时改结账标志的用例、应收应付处理用例（都带 period:close）不同时跑。
_NOTE = ("notes:AR", "new:ar_receipt", "w:ar_receipt", "period:close")
_GL = ("gl", "new:gl:转")
_REG = (CREATE, GET, DELETE)
_PROC = (CREATE, VERIFY, PROCESS, CANCEL, GET, DELETE)
_CASES = (
    ("note-register-cycle", "notes_register", _NOTE, _REG,
     "票据登记：预演不落库；票据与收款单（来源 C / 50、票据号）对上，票据科目、交票客户、iCloseID 都对；读取；删除后都不在"),
    ("note-settle-cycle", "notes_settle", _NOTE + _GL, _PROC + (VOUCHER, DROP),
     "票据结算：部分金额 409；整张结算预演不落库；处理行 9A、票据行贷方票面、余额 0、notes/get 都对；"
     "PJ 凭证借银行贷票据科目、回写齐全；已制单取消 409；取消制单、取消处理后复原，弃审收款单、删票据"),
    ("note-discount-cycle", "notes_discount", _NOTE + _GL, _PROC + (VOUCHER, DROP),
     "票据贴现：部分金额 409；整张贴现处理行净额与费用、票据行贷方票面、余额 0 都对；PJ 凭证含贴现费用行；"
     "取消制单、取消处理后复原，删票据"),
    ("note-endorse-cycle", "notes_endorse", _NOTE + ("w:purchase_invoice",), _PROC,
     "票据背书：部分金额 409；整张背书处理行 9E、应收票据行、应付明细借方、采购发票累计与余额都对；取消处理后复原，删票据"),
    ("note-split-settle-cycle", "notes_split_settle", ("notes:AR", "period:close"), (PROCESS, CANCEL),
     "分包票据结算：现有分包票据取一小段子票，处理行号段、明细单号后缀、可用区间重写都对；取消后可用区间逐行复原"),
)


def notes_cases() -> list[CaseSpec]:
    # 要有现成的应收票据和结算记录作样本；挑不到时记 N/A。
    return [
        CaseSpec(
            name, title, (), (Step("custom", sql_name=op, id_from="none"),), True, optional=True,
            tags=frozenset(tags), routes=routes,
        )
        for name, op, tags, routes, title in _CASES
    ]
