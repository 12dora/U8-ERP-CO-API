"""应收坏账的端到端用例（有种子数据的账套，或历史单据足够的账套）：坏账发生、坏账收回、计提坏账准备。

坏账处理只对测试账套开放：先预演一次计提，403 test_account_only / feature_disabled（或接口还没有该路由）记 N/A，什么都不动。
登录年度没有坏账准备参数行（Ar_BadPara）时用例先补一行（抄最新一行、换成登录年度；一行都没有就用 1231 和 6701 / 6702
下的末级科目新建），跑完（含失败；补偿动作本身失败时留给快照还原）删掉这一行；参数行原本就有时，跑完把计提取消后 U8 留下的列改回。两种情况最后都按 SQL
核对参数行回到用例开始前的样子。其余清理按记下的动作倒序做（取消制单 → 取消处理 → 删除自建收款单），失败记 FAIL。

bad-debt-occur-cycle（9G）：挑本位币、已审核、iNatSum − iMoneySum 与往来明细余额都不小于 10 的销售发票行 ——
  超过可处理余额 409 → 试算不落库 → 坏账发生 10.00：处理号 HZAR…，一行 9G 贷方 10（挂该发票行，科目是该发票的应收科目），
  发票行 iMoneySum、iExchSum 各加 10，客户应收余额减 10，坏账准备余额减 10 → 处理制单（AR / JT，两行：借坏账准备 10、
  贷应收科目 10 带客户）、回写齐 → 已制单取消 409 → 取消制单 → （本年度还没计提过时）计提一次，与 9G 一起制单 409
  「同一种坏账处理」，取消计提 → 取消坏账发生，全部回到之前。
bad-debt-recover-cycle（9H）：给挑单的空闲客户新建一张未审核的单行收款单 20.00 —— 收回金额与收款单不等 409 → 试算不落库
  → 坏账收回：收款单被审核、余额清零，挂一行 9H 借方 20，客户应收余额不变，坏账准备余额加 20 → 对该收款单单据制单 409
  「已用于坏账收回」→ 处理制单（推不出现金流量项目时指定名称含「收到」的项目）：借收款单结算科目 20、贷坏账准备 20 →
  已制单取消 409 → 取消制单 → 取消收回（收款单回到未审核、没有往来明细、余额复原）→ 删除收款单。
bad-debt-provision-cycle（9F）：本年度还没计提过时 —— 试算不改参数行 → 计提：本次计提 = round(基数 × 比率 − 余额, 2)，
  参数行余额、本年计提额、计提日期、处理号都对 → 制单（借对方科目、贷坏账准备，负数照样记在这两边）→ 已制单取消 409 →
  取消制单 → 取消计提（余额复原，本年计提额和计提日期清空）。计提金额为 0 或本年度已计提过记 N/A。

步骤 kind 为 "custom"，BAD_DEBT_OPS 并入 ops_run；回读与参数行 SQL 在 bad_debt_sql。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from co.e2e.casespec.arap_ctl import ctl_step
from co.e2e.casespec.arap_gen_bodies import ar_receipt_body
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _Fail, _NotApplicable, _Run
from co.e2e.casespec.arap_proc import CANCEL, DROP, VOUCHER, cancel_no, dec, nums, refuse_cancel
from co.e2e.casespec.arap_proc_sql import gl_net_sql
from co.e2e.casespec.bad_debt_sql import (
    NULL,
    PARA_COLS,
    bad_gl_sql,
    bad_marks_sql,
    cash_item_sql,
    occur_pick_sql,
    occur_rows_sql,
    occur_snap_sql,
    para_delete_sql,
    para_insert_sql,
    para_restore_sql,
    para_sql,
    receipt_gone_sql,
    receipt_sql,
    recover_snap_sql,
)
from co.e2e.casespec.tier2 import tier2_refused

BAD = "/v1/arap/bad_debt"
CREATE = "/v1/vouchers/create"
DELETE = "/v1/vouchers/delete"
AR_VOUCHER = "/v1/arap/voucher"
TEN = Decimal("10.00")
TWENTY = Decimal("20.00")
_CENT = Decimal("0.01")
_ZERO = Decimal("0")
_PZ = re.compile(r"^AR[0-9]{1,20}\Z")
_NO_CASH = re.compile(r"科目 (\S+) 的现金流量项目无法按数据来源唯一确定")
_ZERO_JT = "本次计提金额为 0"
_MISSING = ("未知路径", "Not Found")


@dataclass
class _Para:
    """坏账准备参数行：用例开始前的回读、补建的 autoid（没补为 0）、准备好之后的回读。"""

    year: int
    saved: dict[str, str]
    inserted: int
    row: dict[str, str]

    @property
    def id(self) -> int:
        return int(self.row["id"])

    def fresh(self) -> bool:
        """本年度还没计提过（本年计提额为空或 0）：计提取消能回到之前。"""
        return self.row["jt_amount"] in (NULL, "0.00")


# ---- 参数行 ----


def read_para(run: _Run, year: int) -> dict[str, str]:
    cell = run.cell(para_sql(year))
    parts = cell.split("|") if cell else []
    if len(parts) != len(PARA_COLS):
        raise _Fail(f"坏账准备参数回读不到（{cell[:60] or '空'}）")
    return dict(zip(PARA_COLS, parts, strict=True))


def _setup(run: _Run) -> _Para:
    year = int(run.env.today[:4])
    saved = read_para(run, year)
    inserted = 0
    if saved["year_rows"] == "0":
        raw = run.cell(para_insert_sql(year))
        inserted = int(raw) if raw.isdigit() else 0
    return _Para(year, saved, inserted, read_para(run, year))


def _usable(para: _Para) -> None:
    row = para.row
    if row["id"] == NULL:
        raise _NotApplicable(f"没有坏账准备参数，科目表里也没有 1231 和 6701 / 6702 下不带辅助核算的末级科目（{para.year} 年）")
    if row["max_id"] != row["id"]:
        raise _NotApplicable(f"最新一行坏账准备参数是 {row['max_year']} 年的，不是登录年度 {para.year}（桥会拒绝）")
    if row["jt_style"] not in ("1", "2", "3") or NULL in (row["hz"], row["dy"]):
        raise _NotApplicable(f"坏账准备参数不全：计提方法 {row['jt_style']}、科目 {row['hz']} / {row['dy']}")


def _restore(run: _Run, para: _Para | None, left: str) -> str:
    """删掉补建的参数行，或把计提取消后留下的列改回；再核对整行回到用例开始前。返回问题说明，没问题为空。
    left 是前面清理动作的失败说明：有失败时处理行或凭证可能还挂在参数行上，不动参数行，留给快照还原。"""
    if para is None:
        return ""
    if left:
        return "坏账准备参数行未恢复（留给快照还原）"
    try:
        _put_back(run, para)
        now = read_para(run, para.year)
    except (_Fail, RuntimeError, ValueError) as exc:
        return "恢复坏账准备参数行：" + str(exc)
    diff = [f"{key} {para.saved[key]}→{now[key]}" for key in PARA_COLS if now[key] != para.saved[key]]
    return ("坏账准备参数行没有复原：" + "，".join(diff)) if diff else ""


def _put_back(run: _Run, para: _Para) -> None:
    if para.inserted:
        if run.cell(para_delete_sql(para.inserted, para.year)) != "1":
            raise _Fail(f"补建的参数行 {para.inserted} 没删掉")
    elif para.row["id"] != NULL and read_para(run, para.year) != para.saved:
        run.cell(para_restore_sql(para.id, para.saved))


def _probe(run: _Run) -> None:
    status, payload = run.send(BAD, {"action": "provision", "dry_run": True})
    if tier2_refused(status, payload):
        raise _NotApplicable(f"坏账处理只对测试账套开放（403 {payload.get('code')}）")
    if status == 404 and any(word in str(payload.get("message")) for word in _MISSING):
        raise _NotApplicable("接口还没有 arap/bad_debt 路由")


# ---- 小工具 ----


def _msg(payload: dict) -> str:
    return str(payload.get("message") or "")


def _refuse(run: _Run, route: str, body: dict, text: str, what: str) -> None:
    """应 409 且消息含 text；意外成功时先记下清理动作（取消处理 / 取消制单）再 FAIL。"""
    status, payload = run.send(route, body)
    if status == 200:
        no, pz = str(payload.get("cancel_no") or ""), str(payload.get("pz_id") or "")
        if no:
            run.undo.append((CANCEL, {"flag": "AR", "cancel_no": no}, "取消" + what))
        if pz:
            run.undo.append((DROP, {"flag": "AR", "pz_id": pz}, "取消制单"))
        raise _Fail(f"{what}应 409，实际 200")
    if status != 409 or text not in _msg(payload):
        raise _Fail(f"{what}应 409「{text}」，实际 {status} {_msg(payload)}"[:240])


def _same(run: _Run, query: str, width: int, before: list[Decimal], what: str) -> None:
    got = nums(run, query, width, what)
    if got != before:
        raise _Fail(f"{what}应保持 {before}，实际 {got}")


def _answer(payload: dict, style: str, amount: Decimal | None, remain: Decimal) -> Decimal:
    """核对响应的处理方式、金额（None 不核）、坏账准备余额前后；返回金额。"""
    got = dec(str(payload.get("amount")), "响应金额")
    before = dec(str(payload.get("remain_before")), "响应余额")
    after = dec(str(payload.get("remain_after")), "响应余额")
    if payload.get("style") != style or (amount is not None and got != amount):
        raise _Fail(f"响应应为 {style} {amount}，实际 {payload.get('style')} {got}")
    if before != remain or after != before + (got if style != "9G" else -got):
        raise _Fail(f"响应的坏账准备余额 {before} → {after}，处理前回读 {remain}、金额 {got}")
    return got


def _make(run: _Run, no: str, cash: bool) -> tuple[str, str]:
    """处理制单；cash 时现金流量项目推不出就指定名称含「收到」的项目重试一次。返回外部业务号和指定说明。"""
    body: dict = {"flag": "AR", "cancel_nos": [no]}
    status, payload = run.send(VOUCHER, body)
    hit = _NO_CASH.search(_msg(payload)) if cash and status == 409 else None
    picked = ""
    if hit is not None:
        item = run.cell(cash_item_sql())
        if not item:
            raise _NotApplicable(f"凭证要指定科目 {hit.group(1)} 的现金流量项目，但没有名称含「收到」的未关闭项目")
        picked = f"（指定现金流量项目 {hit.group(1)}→{item}）"
        status, payload = run.send(VOUCHER, dict(body, cash_items={hit.group(1): item}))
    if status != 200:
        raise _Fail(f"处理制单：{status} {payload.get('code')} {_msg(payload)}"[:240])
    pz = str(payload.get("pz_id") or "")
    if _PZ.fullmatch(pz) is None:
        raise _Fail("处理制单的响应没有外部业务号：" + pz[:40])
    run.undo.append((DROP, {"flag": "AR", "pz_id": pz}, "取消制单"))
    return pz, picked


def _marks(run: _Run, no: str, pz: str, made: bool, side: str) -> None:
    para_side = side == "para"
    total, marked, refs, para_refs, gl = nums(run, bad_marks_sql(no, pz), 5, "制单回写")
    if not made:
        if (marked, refs, para_refs, gl) != (_ZERO, _ZERO, _ZERO, _ZERO):
            raise _Fail(f"取消制单后仍有回写 {marked}、引用 {refs}、参数行 {para_refs}、凭证行 {gl}")
        return
    # 9F 只回写参数行；9G / 9H 回写本处理号的全部往来明细（至少一行），凭证都是两行；
    # 9H 另把收款单自己的两行审核明细（48，iFlag 0 / 6）标上同一张凭证，引用数多 2。
    extra = 2 if side == "cash" else 0
    want = (0, 0, 0, 1, 2) if para_side else (max(total, 1), total, total + extra, 0, 2)
    if (total, marked, refs, para_refs, gl) != want:
        raise _Fail(f"制单后处理行 {total}、回写齐的 {marked}、引用 {refs}、参数行 {para_refs}、凭证行 {gl}")


def _voucher_round(run: _Run, no: str, nets: list[tuple[str, Decimal, str]], side: str = "detail") -> str:
    """制单 → 一张两行、来源 AR / JT、借贷平，各科目净额对 → 回写齐 → 已制单取消 409 → 取消制单 → 回写清空。
    nets 每项（科目或客户、期望净额、科目前缀）：以 C: 开头的键按客户（ccus_id）找，其余按科目找。
    side：detail（9G，回写往来明细）、cash（9H，另可指定现金流量项目）、para（9F，回写参数行）。"""
    para_side = side == "para"
    pz, picked = _make(run, no, side == "cash")
    rows, count, bad, md, mc = nums(run, bad_gl_sql(pz), 5, "凭证")
    if rows != 2 or count != 1 or bad != 0 or md != mc:
        raise _Fail(f"凭证 {pz}：{rows} 行、{count} 张、来源不是 AR/JT 的 {bad} 行、借 {md} 贷 {mc}")
    for key, want, prefix in nets:
        column, value = ("ccus_id", key[2:]) if key.startswith("C:") else ("ccode", key)
        parts = run.cell(gl_net_sql(pz, column, value)).split("|")
        if len(parts) != 4 or dec(parts[0], "凭证净额") != want or not parts[2].startswith(prefix):
            raise _Fail(f"凭证 {pz} 里 {column}={value} 的行应净额 {want}、科目 {prefix}，实际 {'|'.join(parts)}"[:240])
    _marks(run, no, pz, True, side)
    refuse_cancel(run, "AR", no)
    run.settle(1)
    _marks(run, no, pz, False, side)
    return pz + picked


# ---- 坏账发生 ----


def _mixed(run: _Run, para: _Para, no: str) -> str:
    """本年度还没计提过时：计提一次，与 9G 一起制单应 409，再取消计提。返回说明。"""
    if not para.fresh():
        return "；本年度已计提过，跳过混合制单"
    status, payload = run.send(BAD, {"action": "provision"})
    if status == 409 and _ZERO_JT in _msg(payload):
        return "；计提金额为 0，跳过混合制单"
    if status != 200:
        raise _Fail(f"计提坏账准备：{status} {payload.get('code')} {_msg(payload)}"[:240])
    other = cancel_no(run, payload, "HZAR", (CANCEL, {"flag": "AR"}, "取消计提"))
    _refuse(run, VOUCHER, {"flag": "AR", "cancel_nos": [no, other]}, "同一种坏账处理", "9G 与 9F 一起制单")
    run.settle(1)
    return "；9G 与 9F 一起制单 409"


def occur_cycle(run: _Run, para: _Para) -> tuple[str, str]:
    pick = run.pick(occur_pick_sql(run.env.today), 6)
    if pick is None:
        raise _NotApplicable("挑不到已审核、本位币、iNatSum − iMoneySum 与余额都不小于 10 的销售发票行（只在有种子数据的账套或历史单据足够的账套跑）")
    cus, vt, code, line, rem, ctrl = pick
    snap = occur_snap_sql(int(line), cus, para.id)
    before = nums(run, snap, 5, "发生前")
    ref = {"type": vt, "id": code, "line_id": int(line)}
    body = {"action": "occur", "customer": cus, "lines": [dict(ref, amount=float(TEN))]}
    over = dict(body, lines=[dict(ref, amount=float(dec(rem, "可处理余额") + 1000))])
    _refuse(run, BAD, over, "超过可处理余额", "坏账金额超过余额")
    run.ok(BAD, dict(body, dry_run=True), "试算坏账发生")
    _same(run, snap, 5, before, "试算后")
    payload = run.ok(BAD, body, "坏账发生")
    no = cancel_no(run, payload, "HZAR", (CANCEL, {"flag": "AR"}, "取消坏账发生"))
    _answer(payload, "9G", TEN, before[4])
    rows = nums(run, occur_rows_sql(no, int(line), ctrl), 7, "9G 行")
    if rows != [Decimal(1), TEN, TEN, _ZERO, _ZERO, Decimal(1), Decimal(1)]:
        raise _Fail(f"9G 行（行数、贷方本币、原币、借方、处理方式不对、挂该行、应收科目）应为 1、10、10、0、0、1、1，实际 {rows}")
    want = [before[0] + TEN, before[1] + TEN, before[2] - TEN, before[3] + 1, before[4] - TEN]
    _same(run, snap, 5, want, "发生后")
    hz = para.row["hz"]
    pz = _voucher_round(run, no, [(hz, TEN, hz), ("C:" + cus, -TEN, ctrl)])
    mixed = _mixed(run, para, no)
    run.settle(1)
    _same(run, snap, 5, before, "取消后")
    return "PASS", f"{no}（{code} 行 {line}，10.00）：超额 409，试算不落库，9G 行与余额都对，制单 {pz}{mixed}，取消后复原"


# ---- 坏账收回 ----


def _receipt(run: _Run) -> tuple[int, str, str, str]:
    """新建未审核的单行收款单 20.00（挑单的空闲客户）。返回（id，单号，结算科目，客户）。"""
    try:
        body = ar_receipt_body(run.env)
    except ValueError as exc:
        raise _NotApplicable("挑不到空闲客户与收款科目（ar_master）") from exc
    body["lines"][0]["iAmt"] = float(TWENTY)
    receipt = run.made(CREATE, body, "新建收款单")
    run.drop("ar_receipt", receipt)
    got = run.pick(receipt_sql(receipt), 4)
    if got is None or not got[1] or dec(got[2], "收款单金额") != TWENTY or got[3] != "1":
        raise _Fail(f"新收款单 {receipt} 回读不符：{got}")
    return receipt, got[0], got[1], str(body["head"]["cDwCode"])


def recover_cycle(run: _Run, para: _Para) -> tuple[str, str]:
    receipt, code, bank, cus = _receipt(run)
    snap = recover_snap_sql(receipt, code, cus, para.id)
    before = nums(run, snap, 9, "收回前")
    if before[2:8] != [_ZERO, _ZERO, _ZERO, TWENTY, TWENTY, _ZERO]:
        raise _Fail(f"新收款单应未审核、没有往来明细、余额 20，实际 {before[2:8]}")
    body = {"action": "recover", "customer": cus, "receipt": code, "amount": float(TWENTY)}
    _refuse(run, BAD, dict(body, amount=float(TWENTY + 1)), "坏账收回金额须等于收款单金额", "收回金额与收款单不等")
    run.ok(BAD, dict(body, dry_run=True), "试算坏账收回")
    _same(run, snap, 9, before, "试算后")
    payload = run.ok(BAD, body, "坏账收回")
    no = cancel_no(run, payload, "HZAR", (CANCEL, {"flag": "AR"}, "取消坏账收回"))
    _answer(payload, "9H", TWENTY, before[8])
    got = nums(run, snap, 9, "收回后")
    want = [before[0], before[1], got[2], Decimal(1), TWENTY, _ZERO, TWENTY, Decimal(1), before[8] + TWENTY]
    if got != want or got[2] < 2:
        raise _Fail(f"收回后（客户余额、原币、收款单明细行、9H 行、9H 借方、余额、金额、已审核、坏账准备）应为 {want}，实际 {got}")
    voucher_body = {"flag": "AR", "type": "ar_receipt", "id": receipt}
    _refuse(run, AR_VOUCHER, voucher_body, "已用于坏账收回", "对收回用过的收款单制单")
    hz = para.row["hz"]
    pz = _voucher_round(run, no, [(bank, TWENTY, bank), (hz, -TWENTY, hz)], "cash")
    run.settle(1)
    _same(run, snap, 9, before, "取消后")
    run.settle(1)
    if run.cell(receipt_gone_sql(receipt)) != "0":
        raise _Fail(f"收款单 {receipt} 应已删除")
    return "PASS", f"{no}（收款单 {code}，20.00）：金额不等 409，试算不落库，审核、9H、余额都对，单据制单 409，制单 {pz}，取消后复原、收款单已删"


# ---- 计提坏账准备 ----


def _cents(value: Decimal) -> str:
    return format(value.quantize(_CENT), "f")


def _provision_numbers(payload: dict, remain: Decimal) -> Decimal:
    amount = _answer(payload, "9F", None, remain)
    base, rate, target = (dec(str(payload.get(key)), "响应 " + key) for key in ("base", "rate", "target"))
    want = (base * rate - remain).quantize(_CENT, rounding=ROUND_HALF_UP)
    if amount != want or amount != (target - remain).quantize(_CENT, rounding=ROUND_HALF_UP):
        raise _Fail(f"本次计提应为 round({base} × {rate} − {remain}) = {want}（target {target}），实际 {amount}")
    return amount


def provision_cycle(run: _Run, para: _Para) -> tuple[str, str]:
    if not para.fresh():
        raise _NotApplicable(f"{para.year} 年已计提过（本年计提额 {para.row['jt_amount']}），取消会冲掉之前的计提")
    before = para.row
    remain = dec(before["remain"], "坏账准备余额")
    status, payload = run.send(BAD, {"action": "provision", "dry_run": True})
    if status == 409 and _ZERO_JT in _msg(payload):
        raise _NotApplicable("本次计提金额为 0")
    if status != 200:
        raise _Fail(f"试算计提：{status} {payload.get('code')} {_msg(payload)}"[:240])
    if read_para(run, para.year) != before:
        raise _Fail("试算计提之后参数行变了")
    payload = run.ok(BAD, {"action": "provision"}, "计提坏账准备")
    no = cancel_no(run, payload, "HZAR", (CANCEL, {"flag": "AR"}, "取消计提"))
    amount = _provision_numbers(payload, remain)
    jt = _ZERO if before["jt_amount"] == NULL else dec(before["jt_amount"], "本年计提额")
    want = dict(before, remain=_cents(remain + amount), jt_amount=_cents(jt + amount), jt_date=run.env.today,
                cancel_no=no, proc_style="9F")
    after = read_para(run, para.year)
    if after != want:
        raise _Fail(f"计提后参数行应为 {want}，实际 {after}"[:240])
    hz, dy = before["hz"], before["dy"]
    pz = _voucher_round(run, no, [(dy, amount, dy), (hz, -amount, hz)], "para")
    run.settle(1)
    back = read_para(run, para.year)
    if (back["remain"], back["pz"], back["jt_amount"], back["jt_date"]) != (before["remain"], NULL, NULL, NULL):
        raise _Fail(f"取消计提后余额应回到 {before['remain']}、本年计提额和计提日期清空，实际 {back}"[:240])
    return "PASS", f"{no}（计提 {amount}）：试算不落库，金额与参数行都对，制单 {pz}，取消后余额复原"


# ---- 登记 ----


def _op(body: Callable[[_Run, _Para], tuple[str, str]]) -> Callable[[Env, CaseSpec, Step], tuple[str, str]]:
    def op(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
        del step
        run = _Run(env, spec)
        para: _Para | None = None
        try:
            _probe(run)
            para = _setup(run)
            _usable(para)
            result = body(run, para)
        except _NotApplicable as exc:
            result = ("N/A", str(exc)[:240])
        except (_Fail, KeyError, RuntimeError, ValueError) as exc:
            result = ("FAIL", str(exc)[:240])
        left = run.cleanup()
        problems = "；".join(item for item in (left, _restore(run, para, left)) if item)
        if not problems:
            return result
        head = result[1] + "；" if result[0] == "FAIL" else ""
        return "FAIL", (head + "清理失败：" + problems)[:240]

    return op


BAD_DEBT_OPS = {
    "bad_debt_occur": _op(occur_cycle),
    "bad_debt_recover": _op(recover_cycle),
    "bad_debt_provision": _op(provision_cycle),
}

# 三条用例都改同一行坏账准备参数（arap:bad_debt 彼此互斥），都查应收结账标志、都制单。
_COMMON = ("arap:bad_debt", "period:close", "gl", "new:gl:转")
_CASES = (
    ("bad-debt-occur-cycle", "bad_debt_occur", (), ("w:sale_invoice",), (BAD, VOUCHER, CANCEL, DROP),
     "坏账发生（仅测试账套）：超额 409；试算不落库；9G 行、发票累计、客户余额、坏账准备余额、制单与回写都对；"
     "混合制单 409；取消后复原"),
    ("bad-debt-recover-cycle", "bad_debt_recover", ("ar_master",), ("new:ar_receipt", "w:ar_receipt", "new:gl:收"),
     (BAD, CREATE, AR_VOUCHER, VOUCHER, CANCEL, DROP, DELETE),
     "坏账收回（仅测试账套）：金额不等 409；试算不落库；收款单审核、9H 行、余额都对；单据制单 409；处理制单与回写都对；"
     "取消后复原，收款单删除"),
    ("bad-debt-provision-cycle", "bad_debt_provision", (), (), (BAD, VOUCHER, CANCEL, DROP),
     "计提坏账准备（仅测试账套）：试算不落库；本次计提与参数行都对；制单与回写都对；取消后余额复原"),
)


def bad_debt_cases() -> list[CaseSpec]:
    # 第一步先查账套用没用应收款管理（没有应收受控科目记 N/A，arap_ctl）。
    return [
        CaseSpec(
            name, title, needs, (ctl_step("AR"), Step("custom", sql_name=op, id_from="none")), True, optional=True,
            tags=frozenset(tags + _COMMON), routes=routes,
        )
        for name, op, needs, tags, routes, title in _CASES
    ]
