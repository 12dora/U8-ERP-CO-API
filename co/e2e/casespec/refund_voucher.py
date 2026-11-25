"""退款单制单（refund-voucher-cycle、ar-refund-voucher-cycle）：供应商退款 / 客户退款经 arap/voucher 制单再取消。

挑单的空闲供应商 / 客户（ap_master / ar_master，同退款往返用例）新建一张 100 元退款 → 审核 → arap/voucher 制单 →
断言：外部业务号以 AP / AR 开头；响应和总账都是 2 行、同一张凭证，往来科目红字、结算科目正数、两行在同一方（供应商退款：
借 应付 -100、借 银行 100；客户退款：贷 应收 -100、贷 银行 100，同 U8 客户端做的退款凭证）；每行带本单来源
（制单系统 AP / AR、单据类型 48 / 49、单号），往来科目行的辅助项是本单往来单位；表头 cPzID、原始往来明细全部回写 →
再制单 409 → 取消制单（总账、往来明细、表头都不再引用该外部业务号）→ 弃审 → 删除（删光）。
按建的倒序清理（stock_plus._guard），清理失败记 FAIL。第一步 arap_ctl：账套没有应收 / 应付受控科目时 N/A。
步骤 kind 为 "custom"，sql_name 是操作名（REFUND_VOUCHER_OPS 并入 ops_run）；回读 SQL 在 refund_voucher_sql。
"""

from __future__ import annotations

from decimal import Decimal

from co.e2e.casespec.arap_ctl import ctl_step_for
from co.e2e.casespec.arap_gen_bodies import AMOUNT, _parts
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _dec, _Fail, _guard, _Run
from co.e2e.casespec.arap_refund import _body
from co.e2e.casespec.arap_refund_sql import REFUND, gone_sql
from co.e2e.casespec.refund_voucher_sql import _PZ, code_sql, gl_sql, left_sql, marks_sql

_CREATE = "/v1/vouchers/create"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_VOUCHER = "/v1/arap/voucher"
_DROP = "/v1/arap/voucher/delete"
_MEMO = "CO桥回归退款制单"
# 类型 → (用例名, 中文名, 挑单键, 凭证类别, 两行所在的一方 md / mc)。
_KINDS = {
    "ap_refund": ("refund-voucher-cycle", "供应商退款", "ap_master", "收", "debit"),
    "ar_refund": ("ar-refund-voucher-cycle", "客户退款", "ar_master", "付", "credit"),
}


def _accounts(env: Env, kind: str) -> tuple[str, str, str]:
    """(往来单位, 往来科目, 结算科目)：与 arap_refund._body 取同一份挑单。"""
    if kind == "ap_refund":
        ven, ctrl, bank, _settle, _bill = _parts(env.bag.get("ap_master", ""), 5)
        return ven, ctrl, bank
    cus, _dep, _person, ctrl, bank, _settle, _bill = _parts(env.bag.get("ar_master", ""), 7)
    return cus, ctrl, bank


def _amount(line: dict, side: str) -> Decimal:
    return _dec(str(line.get(side) if line.get(side) is not None else ""))


def _check_reply(kind: str, payload: dict, accounts: tuple[str, str]) -> None:
    """响应分录：往来行在前（红字）、结算行在后（正数），都在同一方，另一方为 0。"""
    side = _KINDS[kind][4]
    other = "credit" if side == "debit" else "debit"
    lines = payload.get("lines")
    if not isinstance(lines, list) or len(lines) != 2 or not all(isinstance(line, dict) for line in lines):
        raise _Fail(f"制单响应应有 2 行分录，实际 {lines!r}"[:200])
    want = Decimal(AMOUNT)
    got = [(line.get("account"), _amount(line, side), _amount(line, other)) for line in lines]
    expect = [(accounts[0], -want, Decimal(0)), (accounts[1], want, Decimal(0))]
    if got != expect:
        raise _Fail(f"分录（科目、{side}、{other}）应为 {expect}，实际 {got}")
    voucher = payload.get("voucher") if isinstance(payload.get("voucher"), dict) else {}
    if voucher.get("sign") != _KINDS[kind][3]:
        raise _Fail(f"凭证类别应为 {_KINDS[kind][3]}，实际 {voucher.get('sign')!r}")


def _check_gl(run: _Run, kind: str, pz: str, code: str, who: tuple[str, str, str]) -> None:
    parts = run.pick(gl_sql(kind, pz, code, (who[1], who[2]), who[0]), 8)
    if parts is None:
        raise _Fail("回读凭证行失败")
    count, c_md, c_mc, b_md, b_mc, foreign, aux, vouchers = parts
    cash = f"{AMOUNT:.2f}"
    red = "-" + cash
    want = (red, "0.00", cash, "0.00") if _KINDS[kind][4] == "debit" else ("0.00", red, "0.00", cash)
    if (count, foreign, aux, vouchers) != ("2", "0", "0", "1") or (c_md, c_mc, b_md, b_mc) != want:
        raise _Fail(
            f"凭证行数|无本单来源|辅助项不符|凭证张数 应为 2|0|0|1，实际 {count}|{foreign}|{aux}|{vouchers}；"
            f"往来借|贷、结算借|贷 应为 {'|'.join(want)}，实际 {c_md}|{c_mc}|{b_md}|{b_mc}"
        )


def _voucher(run: _Run, kind: str, doc: int) -> tuple[str, dict]:
    flag = REFUND[kind][0]
    payload = run.ok(_VOUCHER, {"flag": flag, "type": kind, "id": doc}, "制单")
    pz = str(payload.get("pz_id") or "")
    if _PZ.fullmatch(pz) is None or not pz.startswith(flag):
        raise _Fail(f"制单响应的外部业务号应以 {flag} 开头：{pz[:40]}")
    run.undo.append((_DROP, {"flag": flag, "pz_id": pz}, "取消制单"))
    return pz, payload


def _cycle(run: _Run, kind: str) -> tuple[str, str]:
    title = _KINDS[kind][1]
    who = _accounts(run.env, kind)
    doc = run.made(_CREATE, _body(kind, run.env, _MEMO), "新建" + title)
    run.drop(kind, doc)
    code = run.cell(code_sql(str(doc)))
    if not code:
        raise _Fail(f"读不到{title}单号")
    run.unverify(kind, doc)  # 先登记：审核报错但 U8 已提交时，清理照样弃审
    run.ok(_VERIFY, {"type": kind, "id": doc, "action": "verify"}, "审核" + title)
    pz, payload = _voucher(run, kind, doc)
    _check_reply(kind, payload, (who[1], who[2]))
    _check_gl(run, kind, pz, code, who)
    marks = run.pick(marks_sql(kind, str(doc), code, pz), 3)
    if marks is None or marks[0] != "1" or marks[1] != marks[2] or marks[2] in ("", "0"):
        raise _Fail(f"回写（表头|已回写明细|原始明细）应为 1|n|n，实际 {'|'.join(marks or [])}")
    status, again = run.send(_VOUCHER, {"flag": REFUND[kind][0], "type": kind, "id": doc})
    if status != 409 or again.get("code") != "state_mismatch":
        raise _Fail(f"已制单的{title}再制单应 409 state_mismatch，实际 {status} {again.get('code')}")
    run.settle(1)  # 取消制单
    left = run.cell(left_sql(pz))
    if left != "0":
        raise _Fail(f"取消制单后仍有 {left or '空'} 行引用外部业务号 {pz}")
    run.settle(2)  # 弃审、删除
    if run.cell(gone_sql(str(doc))) != "0":
        raise _Fail(f"删除后{title}表头或表体仍在")
    return "PASS", f"{title} {code}（金额 {AMOUNT}）：审核、制单 {pz}（往来红字、结算正数同在{'借' if _KINDS[kind][4] == 'debit' else '贷'}方）、取消制单、弃审、删除"


def _op(kind: str):
    def run_op(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
        return _guard(env, spec, lambda run: _cycle(run, kind))

    return run_op


REFUND_VOUCHER_OPS = {f"{kind}_voucher": _op(kind) for kind in _KINDS}


def _case(kind: str) -> CaseSpec:
    name, title, need, sign, _side = _KINDS[kind]
    steps = (ctl_step_for(need), Step("custom", sql_name=f"{kind}_voucher", id_from="none"))
    routes = (_CREATE, _VERIFY, _VOUCHER, _DROP, _DELETE)
    text = title + "制单：往来科目红字、结算科目正数同在一方，回写表头和往来明细；再制单 409；取消制单、弃审、删除"
    tags = frozenset(("new:" + kind, "w:" + kind, "gl", "new:gl:" + sign))
    return CaseSpec(name, text, (need,), steps, True, tags=tags, routes=routes)


def refund_voucher_cases() -> list[CaseSpec]:
    return [_case(kind) for kind in _KINDS]
