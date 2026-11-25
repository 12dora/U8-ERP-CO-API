"""总账选项「可以使用应收 / 应付受控科目」（AccInformation GL / bUseYSKM、bUseYFKM，u8-notes 应收应付制单一节）。

gl-controlled-ar-option / gl-controlled-ap-option：挑本年一个只受应收（应付）系统控制的末级科目（辅助核算只有客户 / 供应商、
  本币、不挂现金流量）和一个未停用的往来单位，新建一张转账凭证（借 测试数据的费用科目 1.00，贷 该受控科目 1.00）：
  1. 选项置关（原值 0 / 1 时用 0，否则 False）：桥在进 U8 之前 400 bad_request「…是应收（应付）系统受控科目，总账凭证不能使用」；
  2. 选项置开（1 / True）：桥放行，U8 的总账导入组件也接受 → 200，回读凭证该科目行，记下 bvalueedit、coutsysname；
     U8 拒绝时记 FAIL（桥的规则与 U8 不一致，要改桥）。
恢复（with_restore，无论成败）：作废、删除第 2 步（以及第 1 步意外放行时）新建的凭证；新建结果不明（超时、504、U8 拒绝等
非 200 / 400）时按摘要 CO桥回归 + 当天日期 + 该受控科目找可能已保存的凭证，一并删除。选项按原值写回：原来是 NULL 的写 NULL，
否则按十六进制回读的原值逐字写回（空格不丢），再回读核对 NULL 标志、原值和去空格的值都与改动前相同。
选项行不是恰好一行、挑不到科目记 N/A。总账写彼此互斥（gl），改的是总账选项，只在可还原快照的账套上跑。
步骤 kind 为 "custom"，sql_name 是操作名（GL_CONTROLLED_OPS 并入 ops_run）。
"""

from __future__ import annotations

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.flip_util import Fail, brief, row, rowcount, rows, send, with_restore
from co.e2e.casespec.gl_controlled_sql import (
    DIGEST,
    account_pick_sql,
    made_by_digest_sql,
    option_restore_sql,
    option_set_sql,
    option_sql,
    voucher_rows_sql,
)
from co.e2e.casespec.gl_voucher_sql import GlKey, gl_key, safe_code
from co.e2e.testdata import SIGN_TRANSFER, gl_sign
from co.e2e.testdata import value as td

_ROOT = "/v1/gl/vouchers/"
_ROUTES = tuple(_ROOT + op for op in ("create", "void", "delete"))
_DATA = ("gl.expense_account", "gl.expense_dept", SIGN_TRANSFER)
# 受控方 → （选项名, 往来单位字段, 系统名）。
_SIDES = {"AR": ("bUseYSKM", "customer", "应收"), "AP": ("bUseYFKM", "supplier", "应付")}


def switch_values(old: str) -> tuple[str, str]:
    """（关, 开）：原值是 0 / 1 时沿用数字写法，否则 False / True。"""
    return ("0", "1") if old.strip() in ("0", "1") else ("False", "True")


def voucher_body(env: Env, side: str, account: str, partner: str) -> dict:
    lines = [
        {"account": td("gl.expense_account"), "digest": DIGEST, "debit": 1.0, "dept": td("gl.expense_dept")},
        {"account": account, "digest": DIGEST, "credit": 1.0, _SIDES[side][1]: partner},
    ]
    return {"head": {"sign": gl_sign(SIGN_TRANSFER), "date": env.today}, "lines": lines}


class _Flip:
    """一次用例：改动前的选项行（行数|NULL 标志|十六进制原值|去空格的值）、当前值、新建过的凭证（恢复时作废删除）。"""

    def __init__(self, env: Env, spec: CaseSpec, side: str, original: list[str]) -> None:
        self.env = env
        self.spec = spec
        self.side = side
        self.name = _SIDES[side][0]
        self.original = original
        self.raw = None if original[1] == "1" else original[2]
        self.old = original[3]
        self.now = self.old
        self.account = ""
        self.made: list[GlKey] = []

    def set_to(self, value: str) -> None:
        if self.now == value:
            return
        if rowcount(self.env, self.spec, option_set_sql(self.name, value, self.now)) != 1:
            raise Fail(f"临时把 {self.name} 从 {self.now or '空'} 改成 {value} 应改 1 行")
        self.now = value

    def create(self, body: dict) -> tuple[int, dict]:
        status, payload = send(self.env, _ROOT + "create", body)
        if status == 200:
            self.made.append(gl_key(payload.get("period"), payload.get("sign"), payload.get("no")))
        elif status != 400:
            self.find_unknown()
        return status, payload

    def find_unknown(self) -> None:
        """结果不明时 U8 可能已保存：按摘要、当天日期、受控科目找凭证，没记过的记下，恢复时一并删除。"""
        query = made_by_digest_sql(self.env.today[:4], self.env.today, self.account)
        for parts in rows(self.env, self.spec, query):
            if len(parts) == 3:
                key = gl_key(*parts)
                if key not in self.made:
                    self.made.append(key)

    def drop_all(self) -> str:
        notes = []
        for key in self.made:
            ident = {"period": key.period, "sign": key.sign, "no": key.no}
            codes = [send(self.env, _ROOT + op, ident)[0] for op in ("void", "delete")]
            gone = row(self.env, self.spec, voucher_rows_sql(self.env.today[:4], key, "0"))
            if codes[1] != 200 or not gone or gone[0] != "0":
                notes.append(f"凭证 {key.period}-{key.sign}-{key.no} 作废 / 删除 {codes}，回读 {gone}")
        return "；".join(notes)

    def restore(self) -> str:
        if self.now != self.old:
            rowcount(self.env, self.spec, option_restore_sql(self.name, self.raw, self.now))
        got = row(self.env, self.spec, option_sql(self.name))
        if got != self.original:
            shown = "NULL" if self.raw is None else repr(self.old)
            return f"{self.name} 应原样改回 {shown}（{self.original}），实际 {got}"
        self.now = self.old
        return ""


def _off_step(flip: _Flip, body: dict, off: str, text: str) -> str:
    flip.set_to(off)
    status, payload = flip.create(body)
    want = f"是{text}系统受控科目，总账凭证不能使用"
    if status != 400 or payload.get("code") != "bad_request" or want not in str(payload.get("message") or ""):
        raise Fail(f"{flip.name}={off} 时新建凭证应 400「{want}」，实际 " + brief(status, payload))
    return str(payload.get("message") or "")


def _on_step(flip: _Flip, body: dict, on: str, account: str) -> str:
    flip.set_to(on)
    status, payload = flip.create(body)
    if status != 200:
        raise Fail(f"{flip.name}={on} 时桥放行，U8 新建凭证应 200（桥与 U8 规则不一致），实际 " + brief(status, payload))
    key = flip.made[-1]
    got = row(flip.env, flip.spec, voucher_rows_sql(flip.env.today[:4], key, account))
    if len(got) != 4 or got[0] in ("", "0", "1") or got[1] in ("", "0"):
        raise Fail(f"凭证 {key.period}-{key.sign}-{key.no} 回读不到受控科目 {account} 的行：{got}")
    return f"凭证 {key.period}-{key.sign}-{key.no} {got[0]} 行，{account} 行 bvalueedit={got[2]}、coutsysname={got[3] or '空'}"


def gl_controlled(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    side = dict(step.fields).get("side", "")
    if side not in _SIDES:
        return "FAIL", "side 只能是 AR / AP"
    name, _field, text = _SIDES[side]
    opt = row(env, spec, option_sql(name))
    if len(opt) != 4 or opt[0] != "1":
        return "N/A", f"总账选项 {name} 不是恰好一行，不动选项"
    pick = row(env, spec, account_pick_sql(side, env.today[:4]))
    if len(pick) != 2:
        return "N/A", f"本年挑不到只受{text}系统控制、只带往来单位辅助核算的本币末级科目"
    account, partner = safe_code(pick[0]), safe_code(pick[1])
    body = voucher_body(env, side, account, partner)
    flip = _Flip(env, spec, side, opt)
    flip.account = account
    off, on = switch_values(flip.old)

    def run() -> tuple[str, str]:
        refused = _off_step(flip, body, off, text)
        made = _on_step(flip, body, on, account)
        return "PASS", f"{name} 原值 {flip.old or '空'}；置 {off}：400「{refused[:80]}」；置 {on}：{made}；凭证已删、选项已恢复"

    return with_restore(run, (("删除新建的凭证", flip.drop_all), (f"恢复 {name}", flip.restore)))


GL_CONTROLLED_OPS = {"gl_controlled": gl_controlled}


def gl_controlled_cases() -> list[CaseSpec]:
    # 总账凭证写（gl、new:gl:转）彼此互斥；改总账选项，只在可还原快照的账套上跑。
    tags = frozenset(("gl", "new:gl:转"))
    cases = []
    for side, label in (("AR", "ar"), ("AP", "ap")):
        name, _field, text = _SIDES[side]
        step = Step("custom", fields=(("side", side),), sql_name="gl_controlled", id_from="none")
        title = f"总账选项 {name}：关时用{text}受控科目新建凭证 400，开时 U8 接受、200（凭证删除，选项恢复）"
        cases.append(CaseSpec(f"gl-controlled-{label}-option", title, (), (step,), True, data=_DATA, tags=tags,
                              routes=_ROUTES))
    return cases
