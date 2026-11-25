"""应收合并制单 `ar-voucher-merge`：两张收款单合成一张凭证（arap/voucher 的 ids），再一次取消制单。

用例自己建单据（种子数据的客户、银行科目、结算方式，同种子的 ar_receipt）：同一客户的两张收款单（60、40，金额不同，
好分辨每行来自哪张）→ 各自审核 → ids 合并制单（直接调桥，顶层 type 自己带上）→ 断言：pz_id 以 AR 开头、响应 bills 两张、
分录 bill_code 正是这两张的单号；凭证是同一张、借贷平、每行带应收来源（AR、48、coutid 是两张之一）、每张单据的分录借贷合计
等于它的金额；两张单据的往来明细和表头都回写了同一个外部业务号 → 单张再制单 409（凭证已生成）→ 取消制单（一次）→
凭证行、往来明细、两张表头都清干净。之后按建的倒序弃审、删除两张收款单（stock_plus._guard 的清理），清理失败记 FAIL。
科目表里没有应收受控科目、或账套里没有种子的客户 / 部门 / 业务员时记 N/A。

步骤 kind 为 "custom"，sql_name 是操作名（VOUCHER_MERGE_OPS 并入 ops_run）；回读 SQL 在 arap_voucher_merge_sql。
"""

from __future__ import annotations

import re

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _Fail, _guard, _Run
from co.e2e.casespec.arap_voucher_merge_sql import code_sql, gl_bad_sql, km_sql, left_sql, marks_bad_sql, seed_ready_sql
from co.e2e.seed import data

_CREATE = "/v1/vouchers/create"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_VOUCHER = "/v1/arap/voucher"
_DROP = "/v1/arap/voucher/delete"
_KIND = "ar_receipt"
_AMOUNTS = (60, 40)
_MEMO = "CO桥回归合并制单"
_PZ = re.compile(r"^AR[0-9]{1,20}\Z")


def _receipt(run: _Run, km: str, amount: int) -> int:
    head = {
        "cDwCode": data.CUSTOMERS[0].code,
        "cCode": data.bank_account(),
        "cSSCode": data.SETTLE[0].code,
        "cDeptCode": data.DEPT_SALES,
        "cPerson": data.SALES_PERSON,
        "dVouchDate": run.env.today,
        "cDigest": _MEMO,
    }
    lines = [{"iType": 0, "cKm": km, "iAmt": amount}]
    doc = run.made(_CREATE, {"type": _KIND, "head": head, "lines": lines}, "新建收款单")
    run.drop(_KIND, doc)
    run.ok(_VERIFY, {"type": _KIND, "id": doc, "action": "verify"}, "审核收款单")
    run.unverify(_KIND, doc)
    return doc


def _made(run: _Run, docs: tuple[int, ...]) -> tuple[str, dict]:
    refs = [{"type": _KIND, "id": doc} for doc in docs]
    # 直接调桥：桥要求顶层 type（API 层会从 ids 补），这里自己带上。
    payload = run.ok(_VOUCHER, {"flag": "AR", "type": _KIND, "ids": refs}, "合并制单")
    pz = str(payload.get("pz_id") or "")
    if _PZ.fullmatch(pz) is None:
        raise _Fail("合并制单的响应没有外部业务号：" + pz[:40])
    run.undo.append((_DROP, {"flag": "AR", "pz_id": pz}, "取消制单"))
    return pz, payload


def _check_reply(payload: dict, docs: tuple[int, ...], codes: tuple[str, ...]) -> None:
    bills = payload.get("bills")
    got = [bill.get("id") for bill in bills] if isinstance(bills, list) and all(isinstance(b, dict) for b in bills) else None
    if got != list(docs):
        raise _Fail(f"响应 bills 应为 {list(docs)}，实际 {got}")
    lines = payload.get("lines")
    seen = {line.get("bill_code") for line in lines if isinstance(line, dict)} if isinstance(lines, list) else set()
    if seen != set(codes):
        raise _Fail(f"分录的来源单号应为 {sorted(codes)}，实际 {sorted(str(code) for code in seen)}")


def _body(run: _Run) -> tuple[str, str]:
    if run.cell(seed_ready_sql(data.CUSTOMERS[0].code, data.DEPT_SALES, data.SALES_PERSON)) != "1":
        return "N/A", "账套里没有种子的客户、部门、业务员（只在有种子数据的账套跑）"
    km = run.cell(km_sql(run.env.today))
    if not km:
        return "N/A", "科目表里找不到应收受控科目（1122 开头、带客户辅助的末级科目）"
    docs = tuple(_receipt(run, km, amount) for amount in _AMOUNTS)
    codes = tuple(run.cell(code_sql(str(doc))) for doc in docs)
    if not all(codes):
        raise _Fail(f"读不到收款单号：{codes}")
    pz, payload = _made(run, docs)
    _check_reply(payload, docs, codes)
    gl_bad = run.cell(gl_bad_sql(pz, tuple(zip(codes, _AMOUNTS, strict=True))))
    marks_bad = run.cell(marks_bad_sql(pz, tuple(str(doc) for doc in docs), codes))
    status, again = run.send(_VOUCHER, {"flag": "AR", "type": _KIND, "id": docs[1]})
    extra = str(again.get("pz_id") or "")
    if status == 200 and _PZ.fullmatch(extra):
        # 本该被拒绝却又制了一张：记下清理（在合并凭证之前取消）再报失败。
        run.undo.insert(len(run.undo) - 1, (_DROP, {"flag": "AR", "pz_id": extra}, "取消多出的制单"))
    # 先取消制单（_made 最后记下的清理动作），再判制单后的回读，断言失败也不会把凭证留在库里。
    run.settle(1)
    if gl_bad != "0" or marks_bad != "0":
        raise _Fail(f"凭证问题数 {gl_bad or '空'}、回写问题数 {marks_bad or '空'}，期望 0、0（外部业务号 {pz}）")
    if status != 409 or again.get("code") != "state_mismatch":
        raise _Fail(f"已合并制单的单据再单张制单应 409 state_mismatch，实际 {status} {again.get('code')}")
    left = run.cell(left_sql(pz, tuple(str(doc) for doc in docs)))
    if left != "0":
        raise _Fail(f"取消制单后仍有 {left or '空'} 行引用外部业务号 {pz}")
    return "PASS", f"收款单 {codes[0]}、{codes[1]} 合并制单 {pz}：凭证行各带各的单号、借贷平，取消后两张都清干净"


def voucher_merge(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _body)


VOUCHER_MERGE_OPS = {"ar_voucher_merge": voucher_merge}


def voucher_merge_cases() -> list[CaseSpec]:
    # 新建、审核、删除收款单；新建凭证占总账「收」字编号（银行科目的借方），另带 gl。
    tags = frozenset(("new:ar_receipt", "w:ar_receipt", "gl", "new:gl:收"))
    step = Step("custom", sql_name="ar_voucher_merge", id_from="none")
    title = (
        "应收合并制单：两张收款单 ids 合成一张凭证，凭证行各带各的单号、借贷平、回写同一个外部业务号；"
        "单张再制单 409；一次取消制单两张都清干净"
    )
    return [
        CaseSpec(
            "ar-voucher-merge", title, (), (step,), True, tags=tags,
            routes=(_CREATE, _VERIFY, _VOUCHER, _DROP, _DELETE),
        )
    ]
