"""重做用例的准备步骤：只用本服务自己的接口，把账套改到可以取消、删除原单的状态。

只在测试通道用（通道的桥把该账套列在 testAccounts 里，第二级写入放行）；改动都留在本次快照里，由快照还原，不补偿。
- reopen：按 U8 的倒序取消结账（periods/close action=reopen）：从该年度最后一个已结账的期间倒着取消到目标期间，
  同一期间先取消依赖它的模块（总账最后结账、最先取消；采购之后是库存、存货核算、应付）。
- free_voucher：让一张总账凭证可以经 arap/voucher/delete 取消制单：已记账时先取消该期及以后的总账结账，再逐次
  gl/vouchers/unpost（每次恢复最近一次记账，最多 _UNPOST_MAX 次）；已出纳签字、已审核时 unsign、unverify；最后取消制单。
任何一步被拒都记 N/A（原因带「前置步骤（replay_prep）」），已做的改动留给快照还原。
"""

from __future__ import annotations

from co.e2e.casespec import replay_prep_sql as q
from co.e2e.casespec.stock_plus import _Fail, _NotApplicable, _Run

PERIODS = "/v1/periods/close"
GL = "/v1/gl/vouchers/"
DROP = "/v1/arap/voucher/delete"
# 取消结账的顺序（与结账相反）；每个模块取消前须先取消的同期模块。
_ORDER = ("gl", "ap", "ar", "ia", "st", "sa", "pu")
_DEPENDENTS = {"ar": ("gl",), "ap": ("gl",), "gl": (), "pu": ("st", "ia", "ap", "gl")}
_UNPOST_MAX = 12


def send(run: _Run, route: str, body: dict, what: str) -> dict:
    status, payload = run.send(route, body)
    if status != 200:
        raise _NotApplicable(f"前置步骤（replay_prep）：{what}被拒 {status} {payload.get('code')} {payload.get('message')}"[:240])
    return payload


def reopen(run: _Run, module: str, year: int, period: int, day: str) -> list[str]:
    """取消 module 在 year 年 period 期及以后的结账（连同依赖它的模块），返回做过的「模块 期间」。"""
    mods = {module, *_DEPENDENTS[module]}
    done = []
    for cell in run.cells(q.closed_sql(year, period)):
        parts = cell.split("|")
        if len(parts) != 1 + len(q.MODULES):
            raise _Fail("结账标志回读不对：" + cell[:60])
        flags = dict(zip(q.MODULES, parts[1:], strict=True))
        for mod in _ORDER:
            if mod in mods and flags[mod] == "1":
                body = {"module": mod, "fiscal_year": year, "period": int(parts[0]), "action": "reopen", "date": day}
                send(run, PERIODS, body, f"取消结账 {mod} {year}-{parts[0]} ")
                done.append(f"{mod} {parts[0]}")
    return done


def _state(run: _Run, pz: str) -> list[str]:
    row = run.pick(q.voucher_sql(pz), q.VOUCHER_COLS)
    problem = _problem(row)
    if problem or row is None:
        raise _NotApplicable(f"前置步骤（replay_prep）：凭证 {pz} {problem}")
    return row


def _problem(row: list[str] | None) -> str:
    if row is None or row[0] == "0":
        return "在总账里读不到"
    if row[1] != "1":
        return "对应多张凭证"
    if row[3] != "0":
        return "已作废"
    return ""


def voucher_problem(run: _Run, pz: str) -> str:
    """只读预查：往来明细上的凭证号在总账里读不到、对应多张凭证或已作废时返回原因（空串表示可以准备）。"""
    return _problem(run.pick(q.voucher_sql(pz), q.VOUCHER_COLS))


def _unpost(run: _Run, pz: str, row: list[str], day: str) -> list[str]:
    reopen(run, "gl", int(row[4]), int(row[5]), day)
    for _ in range(_UNPOST_MAX):
        send(run, GL + "unpost", {"date": day}, "总账取消记账")
        row = _state(run, pz)
        if row[2] == "0":
            return row
    raise _NotApplicable(f"前置步骤（replay_prep）：取消 {_UNPOST_MAX} 次记账后凭证 {pz} 仍已记账")


def free_voucher(run: _Run, flag: str, pz: str, day: str) -> list[str]:
    """取消 pz 这张凭证的记账、签字、审核，再取消制单。返回做过的准备动作。"""
    row = _state(run, pz)
    notes = []
    if row[2] != "0":
        row = _unpost(run, pz, row, day)
        notes.append("取消记账")
    key = {"period": int(row[5]), "sign": row[6], "no": int(row[7]), "date": day}
    if row[9] == "1":
        send(run, GL + "unsign", key, "取消出纳签字")
        notes.append("取消签字")
    if row[8] == "1":
        send(run, GL + "unverify", key, "总账取消审核")
        notes.append("取消审核")
    send(run, DROP, {"flag": flag, "pz_id": pz, "date": day}, f"取消制单 {pz} ")
    notes.append("取消制单")
    return notes
