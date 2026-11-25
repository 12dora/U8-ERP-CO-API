"""`purchase-settle-manual-replay`：采购手工结算升为第一级的重做核对用例。

在测试账套上按四种形态各挑一张 U8 做的结算单，删掉后经桥按原样重做，逐项与原单比对：
单张发票配对（pair）、红蓝入库对冲（rd，没有发票行）、红蓝发票对冲（inv，没有入库行）、多张发票（multi）。
挑单条件见 settle_replay_sql.settle_pick_sql（未经存货核算处理、发票是本位币且没有核销等处理、此前各月采购都已结账、
行不与别的结算单共用）。每种先前置步骤（replay_prep，通道把该账套列在 testAccounts 里）：结算月及以后采购已结账时经
periods/close 倒序取消结账（连同库存、存货核算、应付、总账）；发票已应付审核时取消该期及以后的应付结账，发票已制单时取消
凭证的记账、签字、审核后 arap/voucher/delete 取消制单，再 vouchers/verify arap_unverify 取消应付审核。然后快照（表头的结算类型、业务类型、采购类型、供应商、结算日期、行数；每行入库行、发票行、数量、
结算金额、税额、cUpSoType；入库行已结算数量、金额、单价；发票行和表头的结算日期、入库日期）→ vouchers/delete（组件删除，
409 时换下一张）→ vouchers/create 同一结算日期、同样的行（配对与发票对冲行带原结算金额，入库对冲行不带）→ 快照一致。
结算单号、PSVID、制单人不比。不补偿：前置步骤的改动和重做的结算单都留在测试账套里，由快照还原。
每种按候选顺序（最多 20 张）逐个试：凭证在总账里读不到、对应多张凭证或已作废的（只读预查）、准备被拒的、删除被拒的
跳过，换下一张；一种形态的候选都不能用时这一种记跳过，并按原因列出张数。四种都跳过记 N/A；重做里 403 test_account_only 说明第二级闸门还在，FAIL。
步骤 kind 为 "custom"，SETTLE_REPLAY_OPS 并入 ops_run。
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation

from co.e2e.casespec import replay_prep as prep
from co.e2e.casespec import replay_prep_sql as pq
from co.e2e.casespec import settle_replay_sql as q
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _Fail, _guard, _NotApplicable, _Run

CASE = "purchase-settle-manual-replay"
KIND = "purchase_settle"
_CREATE = "/v1/vouchers/create"
_DELETE = "/v1/vouchers/delete"
_VERIFY = "/v1/vouchers/verify"
_TITLES = {"pair": "单张发票配对", "rd": "红蓝入库对冲", "inv": "红蓝发票对冲", "multi": "多张发票"}


def _num(text: str, what: str) -> float:
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise _Fail(f"{what}不是数：{text[:40]}") from exc
    return float(value)


def _lines(run: _Run, psvid: int) -> list[list[str]]:
    lines = [cell.split("|") for cell in run.cells(q.settle_lines_sql(psvid))]
    if not lines or any(len(parts) != q.LINE_COLS for parts in lines):
        raise _Fail(f"结算单 {psvid} 的行回读不对")
    return lines


def _snap(run: _Run, psvid: int, lines: list[list[str]]) -> list[list[str]]:
    rds = sorted({int(parts[0]) for parts in lines if parts[0] != "0"})
    bills = sorted({int(parts[1]) for parts in lines if parts[1] != "0"})
    head = run.cells(q.settle_head_sql(psvid))
    rows = sorted("|".join(parts) for parts in _lines(run, psvid))
    rd = sorted(run.cells(q.settle_rd_sql(rds))) if rds else []
    bill = sorted(run.cells(q.settle_bill_sql(bills))) if bills else []
    return [head, rows, rd, bill]


def _request(lines: list[list[str]]) -> list[dict]:
    out = []
    for rd, bill, qty, amount, _tax, _upso in lines:
        line: dict = {"quantity": _num(qty, "结算数量")}
        if rd != "0":
            line["in_line_id"] = int(rd)
        if bill != "0":
            line["invoice_line_id"] = int(bill)
            line["amount"] = _num(amount, "结算金额")
        out.append(line)
    return out


def _compare(title: str, psvid: int, new: int, before: list[list[str]], after: list[list[str]]) -> None:
    names = ("表头", "结算行", "入库行", "发票行")
    for name, old, got in zip(names, before, after, strict=True):
        if old != got:
            raise _Fail(f"{title}：结算单 {psvid} 重做为 {new} 后{name}不一致，原 {old[:2]}，重做 {got[:2]}"[:240])


def _invoices(run: _Run, psvid: int) -> list[list[str]]:
    rows = [cell.split("|") for cell in run.cells(pq.invoices_sql(psvid))]
    if any(len(parts) != pq.INVOICE_COLS for parts in rows):
        raise _Fail(f"结算单 {psvid} 的发票回读不对")
    return rows


def _precheck(run: _Run, psvid: int) -> str:
    """只读预查：已应付审核的发票带的凭证号在总账里读不到、对应多张凭证或已作废时返回跳过原因，否则空串。"""
    for _pbvid, audited, pz, _year, _period in _invoices(run, psvid):
        if audited == "1" and pz:
            problem = prep.voucher_problem(run, pz)
            if problem:
                return "发票的凭证" + problem
    return ""


def _unaudit(run: _Run, parts: list[str]) -> list[str]:
    """一张发票：已应付审核时取消应付结账、取消制单、取消应付审核。"""
    pbvid, audited, pz, year, period = parts
    if audited != "1":
        return []
    today = run.env.today
    notes = ["取消结账 " + item for item in prep.reopen(run, "ap", int(year), int(period), today)] if year and period else []
    if pz:
        notes += prep.free_voucher(run, "AP", pz, today)
    prep.send(run, _VERIFY, {"type": "purchase_invoice", "id": int(pbvid), "action": "arap_unverify"}, "取消应付审核")
    return notes + [f"发票 {pbvid} 取消应付审核"]


def _prepare(run: _Run, psvid: int, day: str, closed: bool) -> list[str]:
    """前置步骤（replay_prep）：结算月及以后的采购结账、发票的应付审核（连同制单）都取消。返回做过的准备动作。"""
    notes = []
    if closed:
        reopened = prep.reopen(run, "pu", int(day[:4]), int(day[5:7]), run.env.today)
        notes += ["取消结账 " + item for item in reopened]
    for parts in _invoices(run, psvid):
        notes += _unaudit(run, parts)
    return notes


class _Skip(Exception):
    """这个候选不能用（原因按类归并计数），换下一个。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _try_one(run: _Run, title: str, row: list[str]) -> str:
    """准备并重做一个候选；不能用时抛 _Skip（预查不过、准备被拒、删除被拒）。准备做过的改动留给快照还原。"""
    psvid, day = int(row[0]), row[1]
    problem = _precheck(run, psvid)
    if problem:
        raise _Skip(problem)
    try:
        notes = _prepare(run, psvid, day, row[3] == "1")
    except _NotApplicable as exc:
        raise _Skip("准备被拒", str(exc)) from exc
    lines = _lines(run, psvid)
    before = _snap(run, psvid, lines)
    status, payload = run.send(_DELETE, {"type": KIND, "id": psvid})
    if status == 409:
        raise _Skip("删除被拒", f"结算单 {psvid}：{payload.get('message')}")
    if status != 200:
        raise _Fail(f"{title}：删除结算单 {psvid}：{status} {payload.get('code')} {payload.get('message')}"[:240])
    return _redo(run, title, row, lines, before) + (f"，准备 {len(notes)} 步" if notes else "")


def _replay_one(run: _Run, shape: str) -> str:
    """按候选顺序重做一种形态（最多 CANDIDATES 个），返回说明；都不能用时说明以「跳过」开头，带各原因的个数。"""
    title = _TITLES[shape]
    rows = [cell.split("|") for cell in run.cells(q.settle_pick_sql(shape, run.env.today))]
    rows = [row for row in rows if len(row) == 5]
    if not rows:
        return f"跳过{title}：没有可删了重做的结算单"
    reasons: Counter[str] = Counter()
    sample = ""
    for row in rows:
        try:
            return _try_one(run, title, row)
        except _Skip as skip:
            reasons[skip.reason] += 1
            sample = sample or skip.detail
    counts = "、".join(f"{reason} {count}" for reason, count in reasons.most_common())
    tail = f"（例：{sample}）" if sample else ""
    return f"跳过{title}：候选 {len(rows)} 张都不能用：{counts}{tail}"


def _redo(run: _Run, title: str, row: list[str], lines: list[list[str]], before: list[list[str]]) -> str:
    """按同一结算日期和行重做，快照与删除前一致。"""
    psvid = int(row[0])
    body = {"type": KIND, "head": {"settle_date": row[1]}, "lines": _request(lines)}
    new = run.made(_CREATE, body, f"{title}：重做结算单 {psvid}")
    _compare(title, psvid, new, before, _snap(run, new, lines))
    return f"{title} {psvid}→{new}（{len(lines)} 行）一致"


def replay_body(run: _Run) -> tuple[str, str]:
    notes = [_replay_one(run, shape) for shape in _TITLES]
    done = [note for note in notes if not note.startswith("跳过")]
    if not done:
        raise _NotApplicable("；".join(notes))
    return "PASS", ("；".join(notes))[:240]


def settle_replay(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, replay_body)


SETTLE_REPLAY_OPS = {"settle_man_replay": settle_replay}


def settle_replay_cases() -> list[CaseSpec]:
    # 删除、新建结算单（new:/w:purchase_settle），回写入库行（w:purchase_in）和发票（w:purchase_invoice）；
    # 结算月以前各月采购须已结账，与改结账标志的用例互斥（opening:pu）。不补偿，留给快照还原：只在测试通道跑。
    # 准备步骤取消结账、取消记账：另与改结账标志、记账的用例互斥（period:close、gl）。
    tags = frozenset(("new:" + KIND, "w:" + KIND, "w:purchase_in", "w:purchase_invoice", "opening:pu", "period:close", "gl"))
    title = "采购手工结算（第一级）按配对、入库对冲、发票对冲、多发票各删一张 U8 做的结算单后经桥重做，与原单一致"
    return [
        CaseSpec(CASE, title, (), (Step("custom", sql_name="settle_man_replay", id_from="none"),), True, optional=True,
                 tags=tags, routes=(_CREATE, _DELETE, _VERIFY, prep.DROP, prep.PERIODS, prep.GL + "unpost",
                                    prep.GL + "unverify", prep.GL + "unsign"))
    ]
