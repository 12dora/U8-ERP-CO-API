"""销售订单被别的操作员锁定时的闸门，和锁定人姓名按 20 个字符比较的规则（vouchers/lock、VoucherLockGate）。

回归只有一个登录操作员，操作员档案在 UFSYSTEM（SQL 守卫不许碰），建不了第二个操作员，也建不了姓名超过 20 个字符的操作员。
所以「别的操作员锁定」用 SQL 直接把表头 cLocker 写成别人的姓名来模拟（U8 的锁定就只是这一列，见 u8-notes「销售订单锁定」）。

sale-order-lock-other：挑一张未审核、未锁定的销售订单（同 sale-order-lock 的挑单）：
  1. cLocker 写成 OTHER → 本人锁定 409「单据已被 OTHER 锁定」、解锁 409「单据由 OTHER 锁定」、预演审核和预演删除都 409
     state_mismatch 且原文带 OTHER（桥在改、删、审之前挡住别人锁定的单据）→ cLocker 仍是 OTHER → 清空。
  2. 本人锁定 200，cLocker = 本人姓名（超过 20 个字符时应是截到 20 个字符的写法——只有登录操作员姓名够长的通道能核对）；
     姓名不足 20 个字符时 SQL 在后面加一个 X → 解锁 409「单据由 <姓名>X 锁定」（按整串比较，不是按前缀）→ 去掉 X →
     解锁 200，cLocker 清空。
恢复：cLocker 不为空时用 SQL 清空并回读。挑不到订单、读不到操作员姓名记 N/A。
步骤 kind 为 "custom"，sql_name 是操作名（LOCK_OTHER_OPS 经 flip 并入 ops_run）。
"""

from __future__ import annotations

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.flip_util import Fail, ok, refused, row, rowcount, with_restore
from co.e2e.casespec.so_lock_other_sql import (
    OTHER,
    SUFFIX,
    WIDTH,
    append_sql,
    clear_sql,
    lock_as_other_sql,
    locker_sql,
    pick_sql,
    strip_sql,
)
from co.e2e.casespec.sqlexec import run_guarded

LOCK = "/v1/vouchers/lock"
VERIFY = "/v1/vouchers/verify"
DELETE = "/v1/vouchers/delete"
KIND = "sale_order"
_CONFLICT = 409, "state_mismatch"


def stored(name: str) -> str:
    """同桥 VoucherLockGate.Stored：去两端空格，超过 20 个字符截到 20 个再去尾随空格。"""
    text = name.strip()
    return text[:WIDTH].rstrip() if len(text) > WIDTH else text


class _Run:
    def __init__(self, env: Env, spec: CaseSpec, doc_id: int, name: str) -> None:
        self.env = env
        self.spec = spec
        self.doc_id = doc_id
        self.name = name

    def body(self, action: str, **extra: object) -> dict:
        return dict({"type": KIND, "id": self.doc_id, "action": action}, **extra)

    def locker(self) -> str:
        got = row(self.env, self.spec, locker_sql(self.doc_id))
        if len(got) != 2:
            raise Fail(f"读不到销售订单 {self.doc_id} 的锁定人")
        return got[0]

    def write(self, query: str, what: str) -> None:
        if rowcount(self.env, self.spec, query) != 1:
            raise Fail(f"{what}应改 1 行")

    def cleanup(self) -> str:
        if not self.locker():
            return ""
        rowcount(self.env, self.spec, clear_sql(self.doc_id))
        held = self.locker()
        return f"清空锁定人之后仍是 {held!r}" if held else ""


def _other(run: _Run) -> None:
    run.write(lock_as_other_sql(run.doc_id), "把锁定人写成 " + OTHER)
    refused(run.env, LOCK, run.body("lock"), (*_CONFLICT, f"单据已被 {OTHER} 锁定"), "别人锁定时本人锁定")
    refused(run.env, LOCK, run.body("unlock"), (*_CONFLICT, f"单据由 {OTHER} 锁定"), "别人锁定时本人解锁")
    refused(run.env, VERIFY, run.body("verify", dry_run=True), (*_CONFLICT, OTHER), "别人锁定时预演审核")
    del_body = {"type": KIND, "id": run.doc_id, "dry_run": True}
    refused(run.env, DELETE, del_body, (*_CONFLICT, OTHER), "别人锁定时预演删除")
    if run.locker() != OTHER:
        raise Fail(f"被拒之后锁定人应仍是 {OTHER}，实际 {run.locker()!r}")
    run.write(clear_sql(run.doc_id), "清空锁定人")


def _width(run: _Run) -> str:
    payload = ok(run.env, LOCK, run.body("lock"), "本人锁定")
    held = run.locker()
    want = stored(run.name)
    if held != want or payload.get("locked") is not True:
        raise Fail(f"本人锁定后 cLocker 应为 {want!r}（姓名 {len(run.name)} 个字符），实际 {held!r}")
    if len(want) < WIDTH:
        run.write(append_sql(run.doc_id), "在锁定人后加一个字符")
        refused(run.env, LOCK, run.body("unlock"), (*_CONFLICT, f"单据由 {want}{SUFFIX} 锁定"), "姓名多一个字符的锁定人，本人解锁")
        run.write(strip_sql(run.doc_id), "去掉锁定人最后一个字符")
        note = f"姓名 {len(want)} 个字符：多一个字符即视为别人"
    else:
        note = f"姓名 {len(run.name)} 个字符：cLocker 存截到 {WIDTH} 个字符的写法，本人照常解锁"
    payload = ok(run.env, LOCK, run.body("unlock"), "本人解锁")
    if payload.get("locked") is not False or run.locker():
        raise Fail(f"本人解锁后锁定人应为空，实际 {run.locker()!r}")
    return note


def lock_other(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    raw = first_cell(run_guarded(env, pick_sql(), spec.name)).strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 2147483647:
        return "N/A", "挑不到未审核、未锁定、未关闭、不受审批流控制的销售订单"
    name = env.operator_name.strip()
    if not name:
        return "N/A", "读不到登录操作员的姓名"
    run = _Run(env, spec, int(raw), name)

    def body() -> tuple[str, str]:
        _other(run)
        note = _width(run)
        return "PASS", f"销售订单 {run.doc_id}：别人锁定时锁定、解锁、预演审核、预演删除都 409；{note}"

    return with_restore(body, (("清空锁定人", run.cleanup),))


LOCK_OTHER_OPS = {"so_lock_other": lock_other}


def lock_other_cases() -> list[CaseSpec]:
    # 同 sale-order-lock：对销售订单的写（锁定、改 cLocker），销售链互斥。
    step = Step("custom", sql_name="so_lock_other", id_from="none")
    title = "销售订单被别人锁定（SQL 模拟）：锁定、解锁、预演审核、预演删除都 409；锁定人按整串（20 个字符）比较，结束清空"
    return [
        CaseSpec(
            "sale-order-lock-other", title, (), (step,), True, optional=True,
            tags=frozenset(("w:sale_order", "chain:sale")), routes=(LOCK, VERIFY, DELETE),
        )
    ]
