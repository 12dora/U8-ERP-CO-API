"""应收 / 应付待实测项的用例。回读 SQL 在 arap_extra_sql，挑单、余额、核销行沿用 writeoff_prepay_fx_sql。

ar-writeoff-auto-prepay / ap-writeoff-auto-prepay（自动核销 include_prepay，同 ar-writeoff-prepay / ap-writeoff-prepay 的造数）：
  挑一行已审核、有余额的预收（预付）行和同一往来单位下已审核的本币发票，限定这一行和这张发票：
  1. include_prepay 缺省（false）试算 → 计划为空、total 0（预收行不参加配对）；
  2. include_prepay true 试算 → 计划首批正是这一行、第一笔落在这张发票上，记下发票行；
  3. include_prepay false、上限 1.00 执行 → 200、没有批次、余额原样（预收行不动）；
  4. include_prepay true、上限 1.00 执行 → 恰好一批 1.00，预收行、发票行余额各少 1，没有 9M 行，
     该核销号的 9P 行里收付款单自身的冲减行带 bPrePay=1；
  5. 按该批核销号取消 → 9P 行删光，余额、表头 bPrePay、9M 行数都回到核销前。
  另核对：触发器 TR_Ap_CloseBills 的状态和收付款单表头 iRAmount* 与表体余额合计的变动（启用时表头跟着表体变，停用时只记录）；
  应收一侧发票行来自销售订单时，订单行累计收款 iExchSum / iMoneySum 核销后各加 1、取消后复原（没有来源订单行时只记录）。
  账套按规则核销（自动核销 409）记 N/A。核销成功之后任何一步失败，都按记下的核销号尽力取消（404 视为已取消），补偿也失败时留给快照还原。
pz_src：制单用例（ar-voucher、ap-voucher）里的一步，按外部业务号核对凭证来源列 coutsysname / coutsign，
  并记录受控科目行数和总账选项 bUseYSKM / bUseYFKM。
步骤 kind 为 "custom"，ARAP_EXTRA_OPS 并入 ops_run。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.writeoff import cancel_one
from co.e2e.casespec.writeoff_run import digits_of
from co.e2e.casespec.writeoff_prepay_fx import SIDE_TEXT, prepay_check
from co.e2e.casespec.writeoff_prepay_fx_sql import balance_sql, batch_sql, prepay_pick_sql
from co.e2e.casespec.arap_extra_sql import head_sql, pz_src_sql, so_sql
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke

ROUTE = "/v1/arap/writeoff/auto"
CANCEL = "/v1/arap/writeoff/cancel"
RULE_REFUSED = "按规则核销"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,20}\Z")
_INT_MAX = 2147483647
_ONE = Decimal("1")
_ZERO = Decimal("0")
_TRIGGER = {"0": "启用", "1": "停用", "-": "不存在"}


class _Stop(Exception):
    """核对不过：消息就是失败原因。"""


@dataclass
class Run:
    env: Env
    spec: CaseSpec
    side: str
    # 挑单：收付款单 iID、预收 / 预付行 ID、发票主键、发票行（挑单时的行，实际核销行以计划为准）。
    ids: list[int]
    partner: str
    trigger: str
    line: int = 0
    made: list[str] = field(default_factory=list)
    gone: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Snap:
    # 行原币余额、本币余额、表头 bPrePay、发票行原币余额、9M 行数。
    balance: list[Decimal]
    # 表头 iRAmount_f、iRAmount，表体 iRAmt_f、iRAmt 合计。
    head: list[Decimal]
    # 销售订单行 iSOsID、累计收款原币、本币；应付一侧为空。
    order: list[Decimal]


# ---- 小工具 ----


def _send(env: Env, route: str, body: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(body)
    return invoke(env.client, route, fields)


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _cells(env: Env, spec: CaseSpec, query: str, width: int) -> list[str] | None:
    cell = first_cell(run_guarded(env, query, spec.name))
    parts = [part.strip() for part in cell.split("|")] if cell else []
    return parts if len(parts) == width and all(parts) else None


def _nums(parts: list[str] | None, what: str) -> list[Decimal]:
    if parts is None:
        raise ValueError(what + "回读不到")
    try:
        return [Decimal(part) for part in parts]
    except InvalidOperation as exc:
        raise ValueError(f"{what}回读不是数：{parts}") from exc


def _money(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _ids(parts: list[str]) -> list[int]:
    if not all(_ID.fullmatch(part) and int(part) <= _INT_MAX for part in parts):
        raise ValueError(f"挑单返回的主键不对：{parts}")
    return [int(part) for part in parts]


# ---- 请求 ----


def ask_body(run: Run, include: bool, dry: bool) -> dict:
    receipt, invoice = SIDE_TEXT[run.side][0], SIDE_TEXT[run.side][1]
    body: dict = {
        "flag": run.side,
        "partner": run.partner,
        "receipt": {"type": receipt, "id": run.ids[0], "line_id": run.ids[1]},
        "targets": [{"type": invoice, "id": run.ids[2]}],
        "dry_run": dry,
        "include_prepay": include,
    }
    if not dry:
        body["max_amount"] = 1
    return body


def _call(run: Run, include: bool, dry: bool) -> dict:
    status, payload = _send(run.env, ROUTE, ask_body(run, include, dry))
    if status == 200 and not dry:
        # 先记下核销号，之后核对失败时补偿按它取消。
        batches = payload.get("batches")
        for batch in batches if isinstance(batches, list) else []:
            digits = digits_of(batch.get("cancel_no") if isinstance(batch, dict) else None, run.side)
            if digits:
                run.made.append(digits)
    if status != 200:
        raise _Stop(f"自动核销（include_prepay={include}、dry_run={dry}）应 200，实际 " + _brief(status, payload))
    return payload


def _rule_refused(run: Run) -> str:
    # 第一个调用：include_prepay 缺省试算。账套按规则核销时 409，记 N/A；计划应为空。
    body = ask_body(run, False, True)
    del body["include_prepay"]
    status, payload = _send(run.env, ROUTE, body)
    if status == 409 and RULE_REFUSED in str(payload.get("message")):
        return "账套设置了按规则核销，自动核销不可用：" + _brief(status, payload)
    if status != 200:
        raise _Stop("缺省 include_prepay 试算应 200，实际 " + _brief(status, payload))
    if payload.get("plan") != [] or _money(payload.get("total")) != _ZERO:
        raise _Stop(f"缺省 include_prepay 试算应为空计划、total 0，实际 plan={payload.get('plan')} total={payload.get('total')}"[:200])
    return ""


def _plan_line(run: Run) -> int:
    payload = _call(run, True, True)
    plan = payload.get("plan")
    first = plan[0] if isinstance(plan, list) and plan and isinstance(plan[0], dict) else {}
    receipt = first.get("receipt") if isinstance(first.get("receipt"), dict) else {}
    targets = first.get("targets") if isinstance(first.get("targets"), list) else []
    piece = targets[0] if targets and isinstance(targets[0], dict) else {}
    if receipt.get("line_id") != run.ids[1] or piece.get("id") != run.ids[2]:
        raise _Stop(f"include_prepay 试算首批应是预收付行 {run.ids[1]} 对发票 {run.ids[2]}：{first}"[:200])
    line = piece.get("line_id")
    if type(line) is not int or not 1 <= line <= _INT_MAX:
        raise _Stop(f"试算第一笔没有发票行：{piece}"[:200])
    return line


# ---- 回读 ----


def _snap(run: Run) -> Snap:
    env, spec = run.env, run.spec
    balance = _nums(_cells(env, spec, balance_sql(run.side, run.ids[1], ("invoice", run.line)), 5), "余额")
    parts = _cells(env, spec, head_sql(run.ids[0]), 6)
    head = _nums(parts[1:5] if parts else None, "收付款单表头")
    order: list[Decimal] = []
    if run.side == "AR":
        order = _nums(_cells(env, spec, so_sql(run.line), 3), "订单行累计收款")
    return Snap(balance, head, order)


def _head_note(run: Run, before: Snap, now: Snap, when: str) -> str:
    # 表头 iRAmount_f / iRAmount 的变动与表体余额合计的变动。触发器启用时必须一致，停用或没有时只记录。
    head = [now.head[0] - before.head[0], now.head[1] - before.head[1]]
    lines = [now.head[2] - before.head[2], now.head[3] - before.head[3]]
    state = _TRIGGER.get(run.trigger, run.trigger)
    if run.trigger == "0" and head != lines:
        raise _Stop(f"触发器 TR_Ap_CloseBills 启用，{when}表头 iRAmount_f / iRAmount 变动 {head} 应等于表体合计变动 {lines}")
    return f"{when}表头变动 {head[0]}、表体 {lines[0]}（触发器{state}）"


def _order_note(before: Snap, now: Snap, delta: Decimal, when: str) -> str:
    # 发票行来自销售订单时，订单行累计收款原币、本币按核销金额变动。
    if not before.order:
        return ""
    if before.order[0] == _ZERO:
        return "发票行没有来源订单行"
    want = [before.order[0], before.order[1] + delta, before.order[2] + delta]
    if now.order != want:
        raise _Stop(f"{when}订单行（iSOsID、iExchSum、iMoneySum）应为 {want}，实际 {now.order}")
    return f"订单行 {before.order[0]} 累计收款{when}对"


# ---- 一整轮 ----


def _untouched(run: Run, before: Snap) -> None:
    payload = _call(run, False, False)
    batches = payload.get("batches")
    if batches != [] or _money(payload.get("total")) != _ZERO:
        raise _Stop(f"include_prepay=false 执行应没有批次，实际 batches={batches} total={payload.get('total')}"[:200])
    now = _snap(run)
    if now.balance != before.balance or now.head != before.head:
        raise _Stop(f"include_prepay=false 执行后余额应不变：{before.balance} → {now.balance}")


def _write(run: Run) -> str:
    payload = _call(run, True, False)
    batches = payload.get("batches")
    if not isinstance(batches, list) or len(batches) != 1 or not isinstance(batches[0], dict) or len(run.made) != 1:
        raise _Stop(f"include_prepay=true 执行应恰好一批，实际 {batches}"[:200])
    batch = batches[0]
    receipt = batch.get("receipt") if isinstance(batch.get("receipt"), dict) else {}
    items = batch.get("items") if isinstance(batch.get("items"), list) else []
    piece = items[0] if len(items) == 1 and isinstance(items[0], dict) else {}
    if receipt.get("line_id") != run.ids[1] or piece.get("line_id") != run.line or _money(batch.get("amount")) != _ONE:
        raise _Stop(f"批次应是预收付行 {run.ids[1]} 对发票行 {run.line} 核销 1.00：{batch}"[:200])
    return run.made[0]


def _after_write(run: Run, before: Snap, digits: str) -> list[str]:
    now = _snap(run)
    want = [before.balance[0] - _ONE, before.balance[3] - _ONE, before.balance[4]]
    got = [now.balance[0], now.balance[3], now.balance[4]]
    if got != want:
        raise _Stop(f"核销后（行原币余额、发票行余额、9M 行数）应为 {want}，实际 {got}")
    problem = prepay_check(_nums(_cells(run.env, run.spec, batch_sql(run.side, digits), 5), "核销行"))
    if problem:
        raise _Stop(problem)
    return [_head_note(run, before, now, "核销后"), _order_note(before, now, _ONE, "核销后")]


def _cancel(run: Run, before: Snap, digits: str) -> list[str]:
    status, payload = _send(run.env, CANCEL, {"flag": run.side, "cancel_no": "HX" + run.side + digits})
    if status != 200:
        raise _Stop("取消核销应 200，实际 " + _brief(status, payload))
    run.gone.add(digits)
    if _nums(_cells(run.env, run.spec, batch_sql(run.side, digits), 5), "核销行")[0] != 0:
        raise _Stop("取消核销后该核销号仍有 9P 行")
    now = _snap(run)
    if now.balance != before.balance:
        raise _Stop(f"取消后（行原币余额、本币余额、表头 bPrePay、发票行余额、9M 行数）应回到 {before.balance}，实际 {now.balance}")
    return [_head_note(run, before, now, "取消后"), _order_note(before, now, _ZERO, "取消后")]


def _cycle(run: Run) -> tuple[str, str]:
    na = _rule_refused(run)
    if na:
        return "N/A", na
    run.line = _plan_line(run)
    before = _snap(run)
    _untouched(run, before)
    digits = _write(run)
    notes = _after_write(run, before, digits) + _cancel(run, before, digits)
    text = "；".join(note for note in notes if note)
    return "PASS", f"HX{run.side}{digits}：false 不配对、true 核销 1.00 后余额和核销行都对，取消后复原；{text}"


def _compensate(run: Run) -> str:
    notes = [cancel_one(run.env, run.side, digits)[1] for digits in run.made if digits not in run.gone]
    return "；".join(notes)


def auto_prepay_run(env: Env, spec: CaseSpec, side: str) -> tuple[str, str]:
    _r, _i, _b, name, party, pre = SIDE_TEXT[side]
    parts = _cells(env, spec, prepay_pick_sql(side), 4)
    if parts is None:
        return "N/A", f"没有已审核、余额 ≥ 2 的{pre}行和同一{party}已审核、余额 ≥ 2 的本币发票行（造数 {side.lower()}_prepay）"
    ids = _ids(parts)
    head = _cells(env, spec, head_sql(ids[0]), 6)
    if head is None:
        return "FAIL", f"{name} {ids[0]} 表头回读不到"
    run = Run(env, spec, side, ids, head[0], head[5])
    try:
        status, detail = _cycle(run)
    except (_Stop, RuntimeError, ValueError) as exc:
        status, detail = "FAIL", str(exc)[:200]
        note = _compensate(run)
        detail = f"{detail}；{note}" if note else detail
    return status, (f"{pre}{name}行 {ids[1]}：" + detail)[:300]


# ---- 制单来源列 ----


def pz_src(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    """fields：side（AR / AP）、key（记外部业务号数字的 bag 键）、sign（期望的 coutsign）。"""
    args = dict(step.fields)
    side, key, sign = args.get("side", ""), args.get("key", ""), args.get("sign", "")
    if _KEY.fullmatch(key) is None:
        return "FAIL", "key 不合法"
    parts = _cells(env, spec, pz_src_sql(side, env.bag.get(key, ""), sign), 6)
    if parts is None:
        return "FAIL", "凭证来源列回读不到"
    rows, bad_sys, bad_sign, controlled = _nums(parts[:4], "凭证来源列")
    if rows < 2 or bad_sys or bad_sign:
        return "FAIL", f"凭证 {rows} 行，coutsysname 不是 {side} 的 {bad_sys} 行、coutsign 不是 {sign} 的 {bad_sign} 行"
    return "PASS", f"凭证 {rows} 行来源 {side} / {sign}，受控科目行 {controlled}；bUseYSKM={parts[4]} bUseYFKM={parts[5]}"


def _op(side: str) -> Callable[[Env, CaseSpec, Step], tuple[str, str]]:
    def op(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
        del step
        return auto_prepay_run(env, spec, side)

    return op


ARAP_EXTRA_OPS = {"auto_prepay_ar": _op("AR"), "auto_prepay_ap": _op("AP"), "pz_src": pz_src}

_CASES = (
    ("ar-writeoff-auto-prepay", "auto_prepay_ar", ("w:ar_receipt", "w:sale_invoice"),
     "应收自动核销 include_prepay：缺省和 false 时预收行不配对、不动；true 时预收行对销售发票核销 1.00，"
     "收款单冲减行 bPrePay=1，核对表头触发器与订单累计收款；取消后复原"),
    ("ap-writeoff-auto-prepay", "auto_prepay_ap", ("w:ap_payment", "w:purchase_invoice"),
     "应付自动核销 include_prepay：缺省和 false 时预付行不配对、不动；true 时预付行对采购发票核销 1.00，"
     "付款单冲减行 bPrePay=1，核对表头触发器；取消后复原"),
)


def arap_extra_cases() -> list[CaseSpec]:
    # 与 ar-writeoff-prepay / ap-writeoff-prepay 同一造数、同一组标签，调度上不同时跑；同一侧的核销在桥里也串行。
    return [
        CaseSpec(
            name,
            title,
            (),
            (Step("custom", sql_name=op, id_from="none"),),
            True,
            optional=True,
            tags=frozenset(tags),
            routes=(ROUTE, CANCEL),
        )
        for name, op, tags, title in _CASES
    ]
