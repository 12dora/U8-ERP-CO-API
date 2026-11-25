"""货位调整单（position_adjust，U8 单据类型 19）的往返用例：全程自建自删。

position-adjust-cycle：货位管理仓库里挑一个简单存货（结存 >= 1 的末级调出货位、同仓库另一个末级调入货位）→
  调出、调入相同的新增被桥拒绝（400 lines.0.caposcode，不保存）→ 新建调整 1 → 保存后货位结存不变、没有货位台账 →
  修改（改表头备注和行备注；同一请求删旧行、加新行，新行有行号、单号等于表头单号；调出货位结存 >= 2 时已有行数量改 2 再改回 1，数量、件数符合；
  改成调出调入相同被拒 400）后行数、备注与请求一致、结存不变、没有台账 →
  审核（响应 ledger_rows=2、bin_moves 带前后结存）→ 货位台账一出一入、调出 −1、调入 +1，vouchers/load 的 positions 2 行 →
  已审核的单据修改 409 state_mismatch → 弃审 → 台账没了、结存回到原值 → 删除 → 单据不在、结存仍是原值。
写入是第一级、所有账套开放：新增回 403 test_account_only 记 FAIL（不再记 N/A）。挑不到数据记 N/A；
清理按建的倒序（_PaRun），清理失败记 FAIL：弃审在审核调用之前就登记，清理时单据确实已审核才弃审（审核 504 但 U8
已提交时也能撤回）；中途立即执行的清理动作失败时放回队列，留给最后的清理再试。步骤 kind 为 "custom"，sql_name 是操作名（POSITION_ADJUST_OPS 并入 ops_run）。
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _dec, _Fail, _NotApplicable, _Run
from co.e2e.casespec.stock_plus_sql import safe_lot
from co.e2e.casespec.position_adjust_sql import bin_sums_sql, edit_sql, gone_sql, head_sql, ledger_sql, pick_sql
from co.e2e.casespec.dry_ops import send

_CREATE = "/v1/vouchers/create"
_UPDATE = "/v1/vouchers/update"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_LOAD = "/v1/vouchers/load"
_KIND = "position_adjust"
_NAME = "position-adjust-cycle"
_OP = "position_adjust_cycle"
_MEMO = "CO桥回归"
_MEMO_EDIT = "CO桥回归改"
_SAME_FIELD = "lines.0.caposcode"


class _PaRun(_Run):
    """_Run 加两条：settle 失败时把动作放回队列；cleanup 时弃审只对仍在且已审核的单据做。"""

    def settle(self, count: int) -> None:
        for _ in range(count):
            item = self.undo.pop()
            try:
                self.ok(*item)
            except _Fail:
                self.undo.append(item)
                raise

    def cleanup(self) -> str:
        self.undo = [item for item in self.undo if not self._not_verified(item)]
        return super().cleanup()

    def _not_verified(self, item: tuple[str, dict, str]) -> bool:
        route, extra, _what = item
        if route != _VERIFY:
            return False
        try:
            head = self.cell(head_sql(str(extra["id"]))).split("|") + ["", ""]
        except (RuntimeError, ValueError):
            return False
        return head[0] == "" or head[1] == ""


def _guard_pa(env: Env, spec: CaseSpec, body: Callable[[_Run], tuple[str, str]]) -> tuple[str, str]:
    """同 stock_plus._guard，换成 _PaRun。"""
    run = _PaRun(env, spec)
    try:
        result = body(run)
    except _NotApplicable as exc:
        result = ("N/A", str(exc)[:240])
    except (_Fail, KeyError, RuntimeError, ValueError) as exc:
        result = ("FAIL", str(exc)[:240])
    left = run.cleanup()
    if not left:
        return result
    head = result[1] + "；" if result[0] == "FAIL" else ""
    return "FAIL", (head + "清理失败：" + left)[:240]


class _Pick:
    def __init__(self, parts: list[str]) -> None:
        self.wh, self.inv, self.src, self.dst = (safe_lot(part) for part in parts[:4])
        self.base = (_dec(parts[4]), _dec(parts[5]))

    def body(self, dst: str, today: str) -> dict:
        head = {"cWhCode": self.wh, "dDate": today, "cMemo": _MEMO}
        line = {"cInvCode": self.inv, "cBPosCode": self.src, "cAPosCode": dst, "iQuantity": 1}
        return {"type": _KIND, "head": head, "lines": [line]}


def _sums(run: _Run, pick: _Pick) -> tuple[Decimal, Decimal]:
    parts = run.pick(bin_sums_sql(pick.wh, pick.inv, pick.src, pick.dst), 2)
    if parts is None:
        raise _Fail("回读货位结存失败")
    return _dec(parts[0]), _dec(parts[1])


def _expect(run: _Run, pick: _Pick, doc: int, when: str) -> None:
    """审核后调出 −1、调入 +1，货位台账一出一入；其余时刻（保存、弃审、删除后）结存是挑单时的原值、没有台账。"""
    moved, ledger = (1, "2|1|1") if when == "审核后" else (0, "0|0|0")
    want = (pick.base[0] - moved, pick.base[1] + moved)
    got = _sums(run, pick)
    if got != want:
        raise _Fail(f"{when}调出 / 调入货位结存应为 {want[0]} / {want[1]}，实际 {got[0]} / {got[1]}")
    seen = run.cell(ledger_sql(str(doc)))
    if seen != ledger:
        raise _Fail(f"{when}货位台账（行数|出|入）应为 {ledger}，实际 {seen or '空'}")


def _refuse_same(run: _Run, pick: _Pick) -> None:
    """调出、调入相同：桥在调用 U8 之前 400；不限测试账套，403 test_account_only 记 FAIL。
    dry_ops.send 在错误里保留 field（_Run.send 只留 code、message）。"""
    status, payload = send(run.env, _CREATE, pick.body(pick.src, run.env.today))
    if status == 403 and payload.get("code") == "test_account_only":
        raise _Fail("货位调整单写入对所有账套开放，不应返回 403 test_account_only（桥未升级？）")
    doc = payload.get("id")
    if status == 200 and type(doc) is int and doc > 0:
        run.drop(_KIND, doc)
        raise _Fail("调出、调入货位相同的货位调整单被保存了")
    if status != 400 or payload.get("field") != _SAME_FIELD:
        raise _Fail(f"调出、调入相同应 400 field={_SAME_FIELD}，实际 {status} {payload.get('code')} "
                    f"field={payload.get('field')!r} {payload.get('message')}"[:240])


def _edit_rows(run: _Run, doc: int) -> list[str]:
    """表头备注、行数、首行 autoID、行备注、行单号、行号、行数量、行件数、表头单号。"""
    return (run.cell(edit_sql(str(doc))).split("|") + [""] * 9)[:9]


def _edit_memo(run: _Run, doc: int) -> str:
    """改表头备注和行备注，返回首行 autoID。"""
    line = _edit_rows(run, doc)[2]
    lines = [{"op": "update", "line_id": int(line), "cbMemo": _MEMO_EDIT}]
    run.ok(_UPDATE, {"type": _KIND, "id": doc, "head": {"cMemo": _MEMO_EDIT}, "lines": lines}, "修改货位调整单备注")
    seen = _edit_rows(run, doc)
    if [seen[0], seen[1], seen[2], seen[3]] != [_MEMO_EDIT, "1", line, _MEMO_EDIT]:
        raise _Fail(f"改备注后（表头备注|行数|首行|行备注）应为 {_MEMO_EDIT}|1|{line}|{_MEMO_EDIT}，实际 {'|'.join(seen[:4])}"[:240])
    return line


def _edit_swap(run: _Run, pick: _Pick, doc: int, line: str) -> list[str]:
    """同一请求删旧行、加新行（调出、调入不变）；新行要有行号、单号等于表头单号。"""
    added = pick.body(pick.dst, run.env.today)["lines"][0]
    swap = [{"op": "delete", "line_id": int(line)}, {"op": "add", **added}]
    run.ok(_UPDATE, {"type": _KIND, "id": doc, "lines": swap}, "修改货位调整单换行")
    seen = _edit_rows(run, doc)
    if seen[1] != "1" or seen[2] in ("", line):
        raise _Fail(f"删旧行、加新行后应仍是 1 行且换了新行，实际（行数|首行）{seen[1]}|{seen[2]}（原 {line}）")
    if not seen[5] or not seen[4] or seen[4] != seen[8]:
        raise _Fail(f"新行应有行号、行单号等于表头单号，实际（行号|行单号|表头单号）{seen[5]}|{seen[4]}|{seen[8]}")
    return seen


def _edit_qty(run: _Run, pick: _Pick, doc: int, seen: list[str]) -> None:
    """已有行改数量 2 再改回 1：行数量、件数（不新造）符合，货位结存不变、没有台账。"""
    for qty in (2, 1):
        lines = [{"op": "update", "line_id": int(seen[2]), "iQuantity": qty}]
        run.ok(_UPDATE, {"type": _KIND, "id": doc, "lines": lines}, f"修改货位调整单数量 {qty}")
        now = _edit_rows(run, doc)
        if _dec(now[6] or "x") != qty or now[7] != seen[7] or now[2] != seen[2] or now[1] != "1":
            raise _Fail(f"改数量 {qty} 后（行数|首行|数量|件数）应为 1|{seen[2]}|{qty}|{seen[7] or '空'}，"
                        f"实际 {now[1]}|{now[2]}|{now[6]}|{now[7] or '空'}")
        _expect(run, pick, doc, "改数量后")


def _edit(run: _Run, pick: _Pick, doc: int) -> str:
    """修改：改备注；删旧行加新行；调出货位结存够 2 时已有行改数量；改成调出调入相同 400（field 指到改完后的行）。
    返回修改阶段里没测的内容（空串为全测）。"""
    seen = _edit_swap(run, pick, doc, _edit_memo(run, doc))
    skipped = ""
    if pick.base[0] >= 2:
        _edit_qty(run, pick, doc, seen)
    else:
        skipped = "；调出货位结存不足 2，数量修改未测"
    same = [{"op": "update", "line_id": int(seen[2]), "caposcode": pick.src}]
    status, payload = send(run.env, _UPDATE, {"type": _KIND, "id": doc, "lines": same})
    if status != 400 or payload.get("field") != _SAME_FIELD:
        raise _Fail(f"改成调出、调入相同应 400 field={_SAME_FIELD}，实际 {status} {payload.get('code')} "
                    f"field={payload.get('field')!r}"[:240])
    return skipped


def _refuse_verified_update(run: _Run, doc: int) -> None:
    status, payload = send(run.env, _UPDATE, {"type": _KIND, "id": doc, "head": {"cMemo": _MEMO}})
    if status != 409 or payload.get("code") != "state_mismatch":
        raise _Fail(f"已审核的单据修改应 409 state_mismatch，实际 {status} {payload.get('code')} {payload.get('message')}"[:240])


def _verify(run: _Run, doc: int) -> str:
    run.unverify(_KIND, doc)  # 先登记：审核报错但 U8 已提交时，清理照样弃审（只对已审核的单据做）
    payload = run.ok(_VERIFY, {"type": _KIND, "id": doc, "action": "verify"}, "审核货位调整单")
    moves = payload.get("bin_moves")
    if payload.get("ledger_rows") != 2 or not isinstance(moves, list) or len(moves) != 2:
        raise _Fail(f"审核响应应有 ledger_rows=2 和两项 bin_moves，实际 {payload.get('ledger_rows')!r} / {moves!r}"[:240])
    loaded = run.ok(_LOAD, {"type": _KIND, "id": doc}, "读取货位调整单")
    positions = loaded.get("positions")
    if not isinstance(positions, list) or len(positions) != 2:
        raise _Fail(f"vouchers/load 的 positions 应有 2 行，实际 {positions!r}"[:240])
    return str(payload.get("verified_by") or "")


def _cycle_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(pick_sql(), 6)
    if parts is None:
        return "N/A", "账套挑不到货位管理仓库里结存 >= 1 的简单存货和同仓库另一个末级货位"
    pick = _Pick(parts)
    _refuse_same(run, pick)
    doc = run.made(_CREATE, pick.body(pick.dst, run.env.today), "新建货位调整单")
    run.drop(_KIND, doc)
    head = (run.cell(head_sql(str(doc))).split("|") + ["", "", ""])[:3]
    if not head[0] or head[1] or head[2] != "1":
        raise _Fail(f"新建后表头（单号|审核人|行数）应有单号、未审核、1 行，实际 {'|'.join(head)}")
    _expect(run, pick, doc, "保存后")
    skipped = _edit(run, pick, doc)
    _expect(run, pick, doc, "修改后")
    verifier = _verify(run, doc)
    _expect(run, pick, doc, "审核后")
    _refuse_verified_update(run, doc)
    run.settle(1)
    _expect(run, pick, doc, "弃审后")
    run.settle(1)
    if run.cell(gone_sql(str(doc))) != "0":
        raise _Fail("删除后货位调整单表头仍在")
    _expect(run, pick, doc, "删除后")
    return "PASS", (
        f"货位调整单 {head[0]}（{pick.wh} / {pick.inv}：{pick.src} → {pick.dst} 1）：调出、调入相同 400；保存后结存不变、无台账；修改备注、换行、改数量后结存不变、无台账；"
        f"审核（{verifier or '审核人'}）后台账一出一入、结存 {pick.base[0]} → {pick.base[0] - 1} / {pick.base[1]} → "
        f"{pick.base[1] + 1}；已审核后修改 409；弃审、删除后回到原值{skipped}"
    )


def position_adjust_cycle(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard_pa(env, spec, _cycle_body)


POSITION_ADJUST_OPS = {_OP: position_adjust_cycle}


def position_adjust_cases() -> list[CaseSpec]:
    # 动货位结存、新建货位调整单（stock、new:position_adjust），与其他库存用例互斥。
    step = Step("custom", sql_name=_OP, id_from="none")
    title = "货位调整单：新建 → 修改 → 审核写货位台账（一出一入）、货位结存调出 −1 调入 +1 → 弃审、删除后结存回到原值"
    tags = frozenset(("new:position_adjust", "w:position_adjust", "stock"))
    return [CaseSpec(_NAME, title, (), (step,), True, tags=tags, routes=(_CREATE, _UPDATE, _VERIFY, _LOAD, _DELETE))]
