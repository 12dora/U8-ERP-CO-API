"""应收汇兑损益（9M）的重做比对用例名 `arap-exchange-gain-replay`（升级依据：在 U8 客户端做过汇兑损益的测试账套上
比对通过之前，汇兑损益仍只限测试账套）。

在测试账套上把 U8 客户端做的最近一次应收汇兑损益取消后经桥重做，逐行与原批次比对：
挑与今天同一年度、最近一个登记日期（只有一种外币、单据类型都是桥做的、原币都是 0、全部未制单或同一张凭证）→
之后还有核销等处理的客户（取消会 409）、该币种在登记日期之后还有往来的客户（桥按当前余额算，重做会把之后的单据
算进去）整个跳过，其余客户的处理号参与重做 → 准备账套（replay_prep，账套列在
testAccounts 里）：该期及以后应收（连同总账）已结账时经 periods/close 倒序取消结账；有凭证时记下凭证（只有一个损益科目、
未作废）和分录，已记账时取消总账结账、逐次 gl/vouchers/unpost，取消签字、审核，再 arap/voucher/delete 取消制单 →
快照 9M 行（客户、单据、本币借贷、摘要、期间、处理号个数）和单据余额（应收单表头、收款单表体、销售发票
iExchSum / iMoneySum）→ arap/exchange_gain/cancel 按处理号取消 →
处理行删光 → arap/exchange_gain 按同一币种、同一批客户、同一登记日期（登录日期）、缺省调整汇率重做 → 两份快照一致 →
有凭证时以同一损益科目、凭证类别、制单日期重新制单，凭证按（科目、客户）合计与原凭证一致。
处理号、Auto_ID、凭证号会变，不比。不补偿：准备和重做的结果都留在账套里，由快照还原。
挑不到（没有 9M、凭证张数不对等）或准备步骤被拒记 N/A；汇兑损益仍只限测试账套，账套没列进 testAccounts 或桥没开第二级写入时（403 test_account_only / feature_disabled）也记 N/A。
步骤 kind 为 "custom"，EXGAIN_REPLAY_OPS 并入 ops_run。
"""

from __future__ import annotations

from co.e2e.casespec import exgain_replay_sql as q
from co.e2e.casespec import replay_prep as prep
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _Fail, _guard, _NotApplicable, _Run
from co.e2e.casespec.tier2 import tier2_refused

CASE = "arap-exchange-gain-replay"
EXGAIN = "/v1/arap/exchange_gain"
EXGAIN_CANCEL = "/v1/arap/exchange_gain/cancel"
VOUCHER = "/v1/arap/process/voucher"
DROP = prep.DROP
_VOUCHER_MAX = 50
_LIST_MAX = 200


class _Plan:
    """重做的范围：登记日期、币种、客户、要取消的处理号、跳过的处理号（留在原凭证上）、原凭证。"""

    def __init__(self, row: list[str]) -> None:
        self.day = row[0]
        self.closed = row[1] == "1"
        self.currency = row[3]
        self.pz = row[7]
        self.year = int(row[0][:4])
        self.period = int(row[10])
        self.partners: list[str] = []
        self.nos: list[str] = []
        self.keep: list[str] = []


def _check_day(row: list[str]) -> None:
    day, _closed, curs, _cur, unsupported, orig, pzs, _pz, unmade, rows, _per = row
    if curs != "1":
        raise _NotApplicable(f"{day} 的汇兑损益不止一种外币")
    if unsupported != "0":
        raise _NotApplicable(f"{day} 的汇兑损益含桥不做的单据类型或只有表头的发票（{unsupported} 行）")
    if orig != "0":
        raise _NotApplicable(f"{day} 的汇兑损益有原币不为 0 的行（{orig} 行）")
    whole = (pzs == "0" and unmade == rows) or (pzs == "1" and unmade == "0")
    if not whole:
        raise _NotApplicable(f"{day} 的汇兑损益部分制单或分在 {pzs} 张凭证上")


def _plan(run: _Run) -> _Plan:
    row = run.pick(q.exgain_day_sql(run.env.today), q.DAY_COLS)
    if row is None:
        raise _NotApplicable("本年度没有应收汇兑损益（9M）")
    _check_day(row)
    plan = _Plan(row)
    batches = [cell.split("|") for cell in run.cells(q.exgain_batches_sql(plan.day))]
    blocked = {parts[1] for parts in batches if len(parts) == 3 and parts[2] == "1"}
    # 该币种在登记日期之后还有往来的客户也跳过：桥按当前余额算，会把之后的单据算进去（不是桥与 U8 的差异）。
    blocked |= {cell.strip() for cell in run.cells(q.exgain_later_sql(plan.day, plan.currency))}
    for parts in batches:
        if len(parts) != 3:
            raise _Fail("处理号清单回读不对：" + "|".join(parts)[:60])
        (plan.keep if parts[1] in blocked else plan.nos).append(parts[0])
    plan.partners = sorted({parts[1] for parts in batches} - blocked)
    if not plan.nos:
        raise _NotApplicable(f"{plan.day} 的每个客户之后都还有核销等处理或该币种的往来，不能取消重做")
    if len(plan.nos) > _LIST_MAX or len(plan.partners) > _LIST_MAX:
        raise _NotApplicable(f"{plan.day} 的处理号或客户超过 {_LIST_MAX} 个")
    if plan.pz and len(batches) > _VOUCHER_MAX:
        raise _NotApplicable(f"{plan.day} 的凭证含 {len(batches)} 个处理号，超过一次制单的 {_VOUCHER_MAX} 个")
    return plan


def _snap(run: _Run, plan: _Plan) -> tuple[list[str], list[str]]:
    rows = sorted(run.cells(q.exgain_rows_sql(plan.day, plan.partners)))
    balances = sorted(run.cells(q.exgain_balances_sql(plan.day, plan.partners)))
    if not rows:
        raise _Fail(f"{plan.day} 读不到 9M 行")
    return rows, balances


def _diff(what: str, before: list[str], after: list[str]) -> None:
    if before == after:
        return
    gone = [item for item in before if item not in after]
    extra = [item for item in after if item not in before]
    raise _Fail(f"{what}与原批次不一致：原有 {gone[:2]}，重做 {extra[:2]}（原 {len(before)} 项、重做 {len(after)} 项）"[:240])


def _voucher_before(run: _Run, plan: _Plan, notes: list[str]) -> tuple[list[str], dict]:
    """记下原凭证的损益科目、类别、日期和分录，再准备账套、取消制单（准备动作记进 notes）。"""
    info = run.pick(q.gl_info_sql(plan.pz), 7)
    if info is None or info[0] == "0":
        raise _NotApplicable(f"往来明细上的凭证 {plan.pz} 在总账里读不到")
    _rows, _posted, voided, pls, pl, sign, vdate = info
    if voided != "0":
        raise _NotApplicable(f"原凭证 {plan.pz} 已作废")
    if pls != "1":
        raise _NotApplicable(f"原凭证 {plan.pz} 不带客户的科目有 {pls} 个，定不出损益科目")
    lines = sorted(run.cells(q.gl_lines_sql(plan.pz)))
    notes += prep.free_voucher(run, "AR", plan.pz, plan.day)
    return lines, {"pl_code": pl, "sign": sign, "voucher_date": vdate}


def _prepare(run: _Run, plan: _Plan) -> tuple[list[str], dict, list[str]]:
    """准备账套：取消应收（连同总账）结账、取消原凭证。返回原凭证分录、重新制单的字段、做过的准备动作。"""
    notes = []
    if plan.closed:
        notes += ["取消结账 " + item for item in prep.reopen(run, "ar", plan.year, plan.period, plan.day)]
    if not plan.pz:
        return [], {}, notes
    lines, extra = _voucher_before(run, plan, notes)
    return lines, extra, notes


def _redo(run: _Run, plan: _Plan) -> list[str]:
    status, payload = run.send(EXGAIN_CANCEL, {"flag": "AR", "cancel_nos": plan.nos, "date": plan.day})
    if tier2_refused(status, payload):
        raise _NotApplicable(f"汇兑损益只对测试账套开放（403 {payload.get('code')}：桥没开第二级写入或账套没列进 testAccounts）")
    if status != 200:
        raise _Fail(f"取消原汇兑损益：{status} {payload.get('code')} {payload.get('message')}"[:240])
    left = run.cell(q.exgain_left_sql(plan.nos))
    if left != "0":
        raise _Fail(f"取消后原处理号还剩 {left or '?'} 行")
    body = {"flag": "AR", "currency": plan.currency, "partners": plan.partners, "date": plan.day}
    payload = run.ok(EXGAIN, body, "重做汇兑损益")
    raw = payload.get("batches")
    nos = [str(item.get("cancel_no") or "") for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    if not nos or not all(no.startswith("SYRAR") for no in nos):
        raise _Fail(f"重做的处理号应以 SYRAR 开头，实际 {nos[:3]}")
    return nos


def _revoucher(run: _Run, plan: _Plan, nos: list[str], extra: dict) -> str:
    every = nos + plan.keep
    if len(every) > _VOUCHER_MAX:
        raise _Fail(f"重做后处理号 {len(every)} 个，超过一次制单的 {_VOUCHER_MAX} 个")
    body = {"flag": "AR", "cancel_nos": every, "date": plan.day}
    body.update(extra)
    payload = run.ok(VOUCHER, body, "重新制单")
    pz = str(payload.get("pz_id") or "")
    if not pz:
        raise _Fail("重新制单的响应没有外部业务号")
    return pz


def replay_body(run: _Run) -> tuple[str, str]:
    plan = _plan(run)
    gl_lines, extra, notes = _prepare(run, plan)
    rows, balances = _snap(run, plan)
    nos = _redo(run, plan)
    after_rows, after_balances = _snap(run, plan)
    _diff("9M 行", rows, after_rows)
    _diff("单据余额", balances, after_balances)
    made = ""
    if plan.pz:
        pz = _revoucher(run, plan, nos, extra)
        _diff("凭证分录", gl_lines, sorted(run.cells(q.gl_lines_sql(q.safe_pz(pz)))))
        made = f"，重新制单 {pz} 与原凭证 {plan.pz} 分录一致"
    skipped = f"（跳过之后还有处理的 {len(plan.keep)} 批）" if plan.keep else ""
    prepared = f"；准备：{'、'.join(notes)}" if notes else ""
    return "PASS", (
        f"{plan.day} {plan.currency}：{len(plan.partners)} 个客户 {len(plan.nos)} 批取消后经桥重做 {len(nos)} 批，"
        f"9M 行与单据余额一致{made}{skipped}{prepared}"
    )[:240]


def exgain_replay(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, replay_body)


EXGAIN_REPLAY_OPS = {"arap_exgain_replay": exgain_replay}


def exgain_replay_cases() -> list[CaseSpec]:
    # 改应收单、收款单、销售发票的本币余额和应收往来明细，取消制单、重新制单动总账（gl、new:gl:转）；
    # 查应收期间未结账，与改结账标志的用例互斥（period:close）。不补偿，留给快照还原。
    # 准备步骤取消结账、取消记账，动账套的期间状态：与其他改结账标志、记账的用例互斥（period:close、gl）。
    tags = frozenset(("w:ar_bill", "w:ar_receipt", "w:sale_invoice", "gl", "new:gl:转", "period:close"))
    title = "应收汇兑损益（升级依据）取消 U8 做的最近一批后经桥重做，9M 行、单据余额、凭证分录与原批次一致"
    return [
        CaseSpec(CASE, title, (), (Step("custom", sql_name="arap_exgain_replay", id_from="none"),), True, optional=True,
                 tags=tags, routes=(EXGAIN, EXGAIN_CANCEL, VOUCHER, DROP, prep.PERIODS, prep.GL + "unpost",
                                    prep.GL + "unverify", prep.GL + "unsign"))
    ]
