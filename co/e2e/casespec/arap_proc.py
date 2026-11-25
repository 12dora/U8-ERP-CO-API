"""应收应付处理的端到端用例（只在有种子数据的账套跑）：应收冲应付 / 应付冲应收、并账、红票对冲、汇兑损益。

每条用例自己挑单（挑不到记 N/A），每一步都按回读 SQL 核对往来明细、发票累计、凭证和回写，最后取消到处理之前的状态；
中途失败时按记下的清理动作倒序补偿（取消制单 → 取消处理 → 删除自建单据），补偿失败记 FAIL，留给快照还原。

arap-transfer-cycle（9I，flag AR）：销售专用发票行对采购专用发票行 1.00 —— dry_run 不落库 → 执行：处理号 YCFAP…，
  应收明细贷方 1.00、应付明细借方 1.00、摘要「应收冲应付 …」，销售发票行 iExchSum、采购发票行 iOriTotal 各加 1，
  两行余额各减 1 → 处理制单：一张凭证、来源 AR / ZZ、借贷平、应付受控科目（2202）供应商净借 1、应收受控科目（1122）
  客户净贷 1，两张往来明细都回写 cPZid / cGLSign / iGLno_id / dPZDate → 已制单时取消处理 409「已制单」→ 取消制单
  （回写清空、凭证行删掉）→ 取消处理（处理行删光，累计和余额回到处理之前）。
arap-transfer-cycle-ap（9J，flag AP）：同上，处理号 FCYAR…、摘要「应付冲应收 …」，不制单。
arap-merge-cycle（BZ）：销售发票行 1.00 从客户 A 并到客户 B —— 处理号 BZAR…，同一 iBVid 的两行（A 借方 −1、B 借方 +1），
  两边余额各变 1 → 制单（来源 AR / BZ，A 净贷 1、B 净借 1）→ 取消制单 → 取消处理 → 回到并账之前。
arap-red-offset-cycle（9N）：用例里建红字发票（参照已开票发货行退 1 个、已开票退货 → 审核退货单 → 参照退货单生成红字发票
  → 复核 → 应收审核），再对同一客户的蓝字发票行红票对冲红票的全部金额 —— 处理号 HRAR…，9N 两行（红票 +、蓝票 −），
  红票行 iExchSum 减、蓝票行加，余额各变 → 取消处理回到之前 → 应收弃审、弃复、删除红票，弃审、删除退货单（都核对已删除）。
  桥还没有 arap/red_offset 路由（404 未知路径）时记 N/A。
arap-exchange-gain-cycle / -ap（9M，只对测试账套）：挑有外币应收单（应付单）的往来单位，按显式汇率（单据最大汇率 + 1）
  计算汇兑损益 —— 每批一个处理号 SYRAR… / SYPAP…，9M 行本币合计等于各批差额合计、原币为 0，应收单（应付单）表头本币余额
  按差额变、原币余额不变，收付款单行本币余额同理 → （仅应收一侧）选 6603 开头的末级损益科目制单（来源 AR / SY）→
  取消制单 → 按处理号取消汇兑损益 → 回到之前。非测试账套（403）或没有损益科目时记 N/A。

步骤 kind 为 "custom"，ARAP_PROC_OPS 并入 ops_run；回读 SQL 在 arap_proc_sql。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _Fail, _guard, _NotApplicable, _Run
from co.e2e.casespec.arap_proc_sql import (
    gl_net_sql,
    gl_sql,
    marks_sql,
    merge_pick_sql,
    merge_rows_sql,
    merge_snap_sql,
    proc_rows_sql,
    transfer_pick_sql,
    transfer_snap_sql,
)

TRANSFER = "/v1/arap/transfer"
MERGE = "/v1/arap/merge"
CANCEL = "/v1/arap/process/cancel"
VOUCHER = "/v1/arap/process/voucher"
DROP = "/v1/arap/voucher/delete"
ONE = Decimal("1.00")
_PZ = re.compile(r"^(AR|AP)[0-9]{1,20}\Z")
_ZERO = Decimal("0")
# flag → 处理方式、处理号前缀、摘要开头。
_TRANSFER = {"AR": ("9I", "YCFAP", "应收冲应付"), "AP": ("9J", "FCYAR", "应付冲应收")}


# ---- 小工具 ----


def dec(text: str, what: str) -> Decimal:
    try:
        return Decimal(text.strip())
    except InvalidOperation as exc:
        raise _Fail(f"{what}不是数：{text[:40]}") from exc


def nums(run: _Run, query: str, width: int, what: str) -> list[Decimal]:
    cell = run.cell(query)
    parts = cell.split("|") if cell else []
    if len(parts) != width:
        raise _Fail(f"{what}回读不到（{cell[:60] or '空'}）")
    return [dec(part, what + "回读") for part in parts]


def cancel_no(run: _Run, payload: dict, prefix: str, undo: tuple[str, dict, str] | None) -> str:
    """响应里的处理号；先记下清理动作（undo 的 extra 补上处理号），再核对前缀。"""
    raw = payload.get("cancel_no")
    no = raw if isinstance(raw, str) else ""
    if no and undo is not None:
        route, extra, what = undo
        run.undo.append((route, dict(extra, cancel_no=no), what))
    if re.fullmatch(prefix + r"[0-9]{1,20}", no) is None:
        raise _Fail(f"处理号应以 {prefix} 开头，实际 {no[:30] or '空'}")
    return no


def make_voucher(run: _Run, flag: str, nos: list[str], extra: dict) -> str:
    body = {"flag": flag, "cancel_nos": nos}
    body.update(extra)
    payload = run.ok(VOUCHER, body, "处理制单")
    pz = str(payload.get("pz_id") or "")
    if _PZ.fullmatch(pz) is None:
        raise _Fail("处理制单的响应没有外部业务号：" + pz[:40])
    run.undo.append((DROP, {"flag": flag, "pz_id": pz}, "取消制单"))
    return pz


def check_gl(run: _Run, pz: str, flag: str, sign: str, nets: list[tuple[str, str, Decimal | None, str]]) -> None:
    """一张凭证、来源 flag / sign、借贷平；nets 每项（列、值、期望净额或 None 只要有行、科目前缀）。"""
    rows, count, bad, md, mc = nums(run, gl_sql(pz, flag, sign), 5, "凭证")
    if rows < 2 or count != 1 or bad != 0 or md != mc:
        raise _Fail(f"凭证 {pz}：{rows} 行、{count} 张、来源不是 {flag}/{sign} 的 {bad} 行、借 {md} 贷 {mc}")
    for column, value, want, prefix in nets:
        cell = run.cell(gl_net_sql(pz, column, value))
        parts = cell.split("|")
        if len(parts) != 4:
            raise _Fail(f"凭证 {pz} 的 {column}={value} 行回读不到")
        net, lines = dec(parts[0], "凭证净额"), dec(parts[1], "凭证行数")
        if lines < 1 or (want is not None and net != want):
            raise _Fail(f"凭证 {pz} 里 {column}={value} 的行 {lines} 行、净额 {net}，期望 {want}")
        if not (parts[2].startswith(prefix) and parts[3].startswith(prefix)):
            raise _Fail(f"凭证 {pz} 里 {column}={value} 的科目 {parts[2]}…{parts[3]} 应以 {prefix} 开头")


def check_marks(run: _Run, nos: list[str], pz: str, made: bool) -> None:
    total, marked, refs, gl = nums(run, marks_sql(nos, pz), 4, "制单回写")
    if made and (total < 1 or marked != total or refs != total or gl < 2):
        raise _Fail(f"制单后处理行 {total}、回写齐的 {marked}、引用凭证的 {refs}、凭证行 {gl}")
    if not made and (marked != 0 or refs != 0 or gl != 0):
        raise _Fail(f"取消制单后仍有回写 {marked}、引用 {refs}、凭证行 {gl}")


def refuse_cancel(run: _Run, flag: str, no: str) -> None:
    status, payload = run.send(CANCEL, {"flag": flag, "cancel_no": no})
    if status == 200:
        # 本该拒绝却取消了：处理行已不在，凭证还在。去掉取消处理的清理动作，凭证留给取消制单。
        run.undo = [item for item in run.undo if item[1].get("cancel_no") != no]
        raise _Fail("已制单的处理取消应 409，实际 200")
    if status != 409 or "已制单" not in str(payload.get("message")):
        raise _Fail(f"已制单的处理取消应 409「已制单」，实际 {status} {payload.get('message')}"[:200])


def drop_voucher(run: _Run, nos: list[str], pz: str) -> None:
    run.settle(1)
    check_marks(run, nos, pz, False)


# ---- 应收冲应付 / 应付冲应收 ----


def _transfer_rows(run: _Run, no: str, flag: str) -> None:
    style, _prefix, title = _TRANSFER[flag]
    cell = run.cell(proc_rows_sql(no, style))
    parts = cell.split("|", 7)
    if len(parts) != 8:
        raise _Fail("处理行回读不到")
    got = [dec(part, "处理行回读") for part in parts[:7]]
    want = [Decimal(1), _ZERO, ONE, Decimal(1), ONE, _ZERO, _ZERO]
    if got != want:
        raise _Fail(f"处理行（应收行数、借、贷、应付行数、借、贷、处理方式不对的行）应为 {want}，实际 {got}")
    if not parts[7].startswith(title):
        raise _Fail(f"摘要应以「{title}」开头，实际「{parts[7][:40]}」")


def _transfer_body(flag: str, pick: list[str]) -> dict:
    cus, scode, sline, ven, pcode, pline = pick
    return {
        "flag": flag,
        "customer": cus,
        "vendor": ven,
        "ar_lines": [{"type": "26", "id": scode, "line_id": int(sline), "amount": float(ONE)}],
        "ap_lines": [{"type": "01", "id": pcode, "line_id": int(pline), "amount": float(ONE)}],
    }


def _transfer_voucher(run: _Run, no: str, pick: list[str]) -> str:
    pz = make_voucher(run, "AR", [no], {})
    nets = [("csup_id", pick[3], ONE, "2202"), ("ccus_id", pick[0], -ONE, "1122")]
    check_gl(run, pz, "AR", "ZZ", nets)
    check_marks(run, [no], pz, True)
    refuse_cancel(run, "AR", no)
    drop_voucher(run, [no], pz)
    return pz


def transfer_cycle(run: _Run, flag: str) -> tuple[str, str]:
    pick = run.pick(transfer_pick_sql(run.env.today), 6)
    if pick is None:
        raise _NotApplicable("挑不到已审核、本位币、余额 >= 2 的销售专用发票行和采购专用发票行（只在有种子数据的账套跑）")
    style, prefix, title = _TRANSFER[flag]
    snap = transfer_snap_sql(int(pick[2]), int(pick[5]), pick[0], pick[3])
    before = nums(run, snap, 5, "处理前")
    body = _transfer_body(flag, pick)
    run.ok(TRANSFER, dict(body, dry_run=True), "试算" + title)
    if nums(run, snap, 5, "试算后") != before:
        raise _Fail("dry_run 之后发票累计、余额或处理行数变了")
    no = cancel_no(run, run.ok(TRANSFER, body, title), prefix, (CANCEL, {"flag": flag}, "取消处理"))
    _transfer_rows(run, no, flag)
    want = [before[0] + ONE, before[1] + ONE, before[2] - ONE, before[3] - ONE, before[4] + 2]
    got = nums(run, snap, 5, "处理后")
    if got != want:
        raise _Fail(f"处理后（销售累计、采购累计、销售余额、采购余额、{style} 行数）应为 {want}，实际 {got}")
    pz = _transfer_voucher(run, no, pick) if flag == "AR" else ""
    run.settle(1)
    back = nums(run, snap, 5, "取消后")
    if back != before:
        raise _Fail(f"取消处理后应回到 {before}，实际 {back}")
    made = f"，制单 {pz}、已制单取消 409、取消制单" if pz else ""
    return "PASS", f"{no}（{pick[1]} 对 {pick[4]}，1.00）：试算不落库，处理行、累计、余额都对{made}，取消后复原"


# ---- 并账 ----


def merge_cycle(run: _Run) -> tuple[str, str]:
    pick = run.pick(merge_pick_sql(run.env.today), 4)
    if pick is None:
        raise _NotApplicable("挑不到已审核、本位币、余额 >= 2 的销售专用发票行，或没有另一个未停用的客户（只在有种子数据的账套跑）")
    source, code, line, target = pick
    snap = merge_snap_sql(int(line), source, target)
    before = nums(run, snap, 3, "并账前")
    body = {
        "flag": "AR",
        "from": source,
        "to": target,
        "lines": [{"type": "26", "id": code, "line_id": int(line), "amount": float(ONE)}],
    }
    no = cancel_no(run, run.ok(MERGE, body, "并账"), "BZAR", (CANCEL, {"flag": "AR"}, "取消并账"))
    rows = nums(run, merge_rows_sql(no, source, target), 5, "并账行")
    if rows != [Decimal(2), -ONE, ONE, Decimal(1), Decimal(int(line))]:
        raise _Fail(f"并账行（行数、原客户借方、并入客户借方、iBVid 个数、iBVid）应为 2、-1、1、1、{line}，实际 {rows}")
    got = nums(run, snap, 3, "并账后")
    if got != [before[0] - ONE, before[1] + ONE, before[2] + 2]:
        raise _Fail(f"并账后（原客户余额、并入客户余额、BZ 行数）应为 {before[0] - ONE}、{before[1] + ONE}、{before[2] + 2}，实际 {got}")
    pz = make_voucher(run, "AR", [no], {})
    check_gl(run, pz, "AR", "BZ", [("ccus_id", source, -ONE, "1122"), ("ccus_id", target, ONE, "1122")])
    check_marks(run, [no], pz, True)
    drop_voucher(run, [no], pz)
    run.settle(1)
    back = nums(run, snap, 3, "取消后")
    if back != before:
        raise _Fail(f"取消并账后应回到 {before}，实际 {back}")
    return "PASS", f"{no}（{code} 行 {line}，{source} → {target}，1.00）：成对行、余额、制单 {pz} 都对，取消后复原"


# ---- 登记 ----


def _op(body: Callable[[_Run], tuple[str, str]]) -> Callable[[Env, CaseSpec, Step], tuple[str, str]]:
    def op(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
        del step
        return _guard(env, spec, body)

    return op


def _later(name: str, *args: str) -> Callable[[Env, CaseSpec, Step], tuple[str, str]]:
    # 红票对冲、汇兑损益在 arap_proc_more（它引用本模块的小工具），调用时再引入，避免循环引用。
    def op(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
        del step
        from co.e2e.casespec import arap_proc_more

        body = getattr(arap_proc_more, name)
        return _guard(env, spec, lambda run: body(run, *args))

    return op


ARAP_PROC_OPS = {
    "arap_transfer_ar": _op(lambda run: transfer_cycle(run, "AR")),
    "arap_transfer_ap": _op(lambda run: transfer_cycle(run, "AP")),
    "arap_merge": _op(merge_cycle),
    "arap_red_offset": _later("red_offset_cycle"),
    "arap_exgain_ar": _later("exchange_gain_cycle", "AR"),
    "arap_exgain_ap": _later("exchange_gain_cycle", "AP"),
}

_SALE = ("w:sale_invoice",)
_PU = ("w:purchase_invoice",)
_GL = ("gl", "new:gl:转")
# 处理都查应收、应付期间未结账，汇兑损益还扫该单位全部外币单据：与临时改结账标志、新增期初单据的用例不同时跑。
_PERIOD = ("period:close",)
_RED = ("new:sale_return", "new:dispatch", "new:sale_invoice", "w:sale_return", "w:sale_invoice", "stock")
_CASES = (
    ("arap-transfer-cycle", "arap_transfer_ar", _SALE + _PU + _GL, (TRANSFER, VOUCHER, CANCEL, DROP),
     "应收冲应付：试算不落库；处理行、发票累计、余额、制单与回写都对；已制单取消 409；取消制单、取消处理后复原"),
    ("arap-transfer-cycle-ap", "arap_transfer_ap", _SALE + _PU, (TRANSFER, CANCEL),
     "应付冲应收：试算不落库；处理行、发票累计、余额都对；取消处理后复原"),
    ("arap-merge-cycle", "arap_merge", _SALE + _GL, (MERGE, VOUCHER, CANCEL, DROP),
     "并账：同一发票行的成对 ± 行、两客户余额、制单与回写都对；取消制单、取消并账后复原"),
    ("arap-red-offset-cycle", "arap_red_offset", _RED,
     ("/v1/vouchers/generate", "/v1/vouchers/verify", "/v1/vouchers/delete", "/v1/arap/red_offset", CANCEL),
     "红票对冲：自建红字发票对冲同一客户的蓝字发票，9N 行、两张发票累计与余额都对；取消后复原，红票和退货单删净"),
    ("arap-exchange-gain-cycle", "arap_exgain_ar", ("w:ar_bill", "w:ar_receipt", "w:sale_invoice") + _GL,
     ("/v1/arap/exchange_gain", "/v1/arap/exchange_gain/cancel", VOUCHER, DROP),
     "应收汇兑损益（仅测试账套）：显式汇率计算，9M 行与本币余额变动都对；损益科目制单、取消制单、按处理号取消后复原"),
    ("arap-exchange-gain-cycle-ap", "arap_exgain_ap", ("w:ap_bill", "w:ap_payment", "w:purchase_invoice"),
     ("/v1/arap/exchange_gain", "/v1/arap/exchange_gain/cancel"),
     "应付汇兑损益（仅测试账套）：显式汇率计算，9M 行与本币余额变动都对；按处理号取消后复原"),
)


def arap_proc_cases() -> list[CaseSpec]:
    # w:sale_invoice / w:purchase_invoice / w:ar_bill / w:ap_bill 推出 chain:sale / chain:pu：与核销、外币核销、红字发票等
    # 同链用例不同时跑（它们挑同一批种子单据）；制单另带 gl、new:gl:转，与应收应付制单、合并制单互斥；都带 period:close。
    return [
        CaseSpec(
            name, title, (), (Step("custom", sql_name=op, id_from="none"),), True, optional=True,
            tags=frozenset(tags + _PERIOD), routes=routes,
        )
        for name, op, tags, routes, title in _CASES
    ]
