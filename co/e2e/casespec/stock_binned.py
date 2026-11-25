"""库存待实测用例：有货位记录的采购入库、其他出库删除，无来源采购入库的货位台账写入时点。

binned-purchase-in-delete：货位仓库里新建无来源采购入库 1 个、带末级货位 cposition → 回读 InvPosition 行数和货位存量
  （保存后）→ 审核 → 再回读（审核后）→ 弃审 → 删除（桥先清货位再删）。断言：审核后 InvPosition 1 行、货位存量 +1；
  保存后要么已是 1 行且存量 +1（U8 保存时写），要么 0 行且存量不变（审核时写），结论写进判定说明；删除后表头和
  InvPosition 都没有，货位存量、现存量回到新建前。采购选项「普通业务必有订单」打开时在快照里临时关闭，跑完写回原值。
binned-other-out-delete：货位仓库里新建其他出库 1 个、带有结存的末级货位 → InvPosition 1 行、货位存量 −1 → 删除 →
  表头和 InvPosition 都没有，货位存量、现存量回到新建前。
binned-material-out-delete：同其他出库，单据换成无来源材料出库（行带 cposition，收发类别取测试数据
  stock.material_out_rd）；库存选项「领料必有订单」（ST.ballowAddnewVouch）打开时记 N/A。
binned-product-in-delete：参照不需检验的生产订单行（同 product-in-from-mo）生成产成品入库 1 个进货位仓的末级货位
  （收发类别取 stock.product_in_rd）→ InvPosition 1 行、货位存量 +1 → 删除 → 全部回退。

挑不到数据记 N/A；清理按建的倒序（_guard），清理失败记 FAIL。
步骤 kind 为 "custom"，sql_name 是操作名（STOCK_BINNED_OPS 并入 ops_run）。
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _dec, _Fail, _guard, _Run
from co.e2e.casespec.stock_plus_sql import havepo_sql, safe_lot
from co.e2e.casespec.binned_sale_out_sql import pos_sum_sql, stock_sum_sql
from co.e2e.casespec.stock_binned_sql import (
    head_rows_sql,
    mo_bin_pick_sql,
    mout_pick_sql,
    havepo_value_sql,
    oout_pick_sql,
    pin_pick_sql,
    pos_rows_sql,
    set_havepo_sql,
)
from co.e2e.testdata import MATERIAL_OUT_RD, PRODUCT_IN_RD
from co.e2e.testdata import value as td

_CREATE = "/v1/vouchers/create"
_GEN = "/v1/vouchers/generate"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_MEMO = "CO桥回归"


class _Bin:
    """一张带货位的单据：仓库、货位、存货、短码、单据 id，以及新建前的货位存量与现存量。"""

    def __init__(self, run: _Run, vt: str, codes: list[str]) -> None:
        self.run = run
        self.vt = vt
        self.wh, self.pos, self.inv = (safe_lot(code) for code in codes)
        self.doc = 0
        self.psum = self.pos_sum()
        self.stock = _dec(run.cell(stock_sum_sql(self.wh, self.inv)))

    def pos_sum(self) -> Decimal:
        return _dec(self.run.cell(pos_sum_sql(self.wh, self.pos, self.inv)))

    def probe(self) -> tuple[str, Decimal]:
        """InvPosition 行数、货位存量。"""
        return self.run.cell(pos_rows_sql(self.vt, str(self.doc))), self.pos_sum()

    def where(self) -> str:
        return f"{self.doc}（{self.wh} / {self.pos} / {self.inv}）"

    def gone_problem(self) -> str:
        gone = (self.run.cell(head_rows_sql(self.vt, str(self.doc))), self.run.cell(pos_rows_sql(self.vt, str(self.doc))))
        if gone != ("0", "0"):
            return f"删除后表头、InvPosition 行数应为 0、0，实际 {gone}"
        back = (self.pos_sum(), _dec(self.run.cell(stock_sum_sql(self.wh, self.inv))))
        if back != (self.psum, self.stock):
            return f"删除后货位存量、现存量应回到 {self.psum}、{self.stock}，实际 {back[0]}、{back[1]}"
        return ""


def _stage_problem(label: str, got: tuple[str, Decimal], want: Decimal) -> str:
    if got != ("1", want):
        return f"{label} InvPosition 应 1 行、货位存量 {want}，实际 {got[0] or '空'} 行、{got[1]}"
    return ""


def _saved_problem(bin_: _Bin, saved: tuple[str, Decimal]) -> str:
    # 入库保存后：已写货位台账（1 行、存量 +1）或尚未写（0 行、存量不变）两种都接受，结论写进说明。
    if saved in (("1", bin_.psum + 1), ("0", bin_.psum)):
        return ""
    return f"保存后 InvPosition {saved[0] or '空'} 行、货位存量 {saved[1]}，与是否已写货位台账对不上"


# ---- 采购入库（无来源） ----


def _pin_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(pin_pick_sql(), 6)
    if parts is None:
        return "N/A", "测试账套挑不到货位仓库里可无来源入库的外购存货"
    if run.cell(havepo_sql()) != "1":
        return _pin_run(run, parts)
    # 「普通业务必有订单」打开：在快照里临时关掉，跑完原样写回（写回失败记 FAIL，快照还原兜底）。
    original = run.cell(havepo_value_sql())
    if original not in ("True", "TRUE", "true", "1") or run.cell(set_havepo_sql("False")) != "1":
        return "N/A", "采购选项「普通业务必有订单」打开且无法临时关闭，不能无来源录入采购入库单"
    try:
        result = _pin_run(run, parts)
    finally:
        restored = run.cell(set_havepo_sql(original)) == "1"
    if not restored:
        return "FAIL", "采购选项「普通业务必有订单」没能写回原值（留给快照还原）"
    return result[0], result[1] + "（「普通业务必有订单」临时关闭后已写回）"


def _pin_run(run: _Run, parts: list[str]) -> tuple[str, str]:
    bin_ = _Bin(run, "01", parts[:3])
    ven, dep, ptype = (safe_lot(part) for part in parts[3:])
    head = {"cwhcode": bin_.wh, "cvencode": ven, "cdepcode": dep, "cptcode": ptype, "ddate": run.env.today, "cmemo": _MEMO}
    line = {"cinvcode": bin_.inv, "iquantity": 1, "iunitcost": 1, "itaxrate": 13, "cposition": bin_.pos}
    bin_.doc = run.made(_CREATE, {"type": "purchase_in", "head": head, "lines": [line]}, "新建无来源采购入库")
    run.drop("purchase_in", bin_.doc)
    saved = bin_.probe()
    run.ok(_VERIFY, {"type": "purchase_in", "id": bin_.doc, "action": "verify"}, "审核采购入库")
    run.unverify("purchase_in", bin_.doc)
    verified = bin_.probe()
    # 先弃审、删除（记下的两个清理动作），再判回读，断言失败也不会把单据留在库里。
    run.settle(2)
    problem = _saved_problem(bin_, saved) or _stage_problem("审核后", verified, bin_.psum + 1) or bin_.gone_problem()
    if problem:
        raise _Fail(problem)
    when = "保存时" if saved[0] == "1" else "审核时"
    return "PASS", f"采购入库 {bin_.where()}：InvPosition 由 U8 {when}写（保存后 {saved[0]} 行），删除后全部回退"


# ---- 其他出库 ----


def _oout_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(oout_pick_sql(), 5)
    if parts is None:
        return "N/A", "测试账套挑不到货位仓库里有结存的普通存货或其他出库的收发类别"
    bin_ = _Bin(run, "09", parts[:3])
    rd, dep = safe_lot(parts[3]), safe_lot(parts[4])
    head = {"cwhcode": bin_.wh, "crdcode": rd, "cdepcode": dep, "ddate": run.env.today, "cmemo": _MEMO}
    line = {"cinvcode": bin_.inv, "iquantity": 1, "cposition": bin_.pos}
    bin_.doc = run.made(_CREATE, {"type": "other_out", "head": head, "lines": [line]}, "新建其他出库")
    return _saved_back(run, bin_, ("other_out", "其他出库"), -1)


def _saved_back(run: _Run, bin_: _Bin, kind: tuple[str, str], delta: int) -> tuple[str, str]:
    """（类型, 中文名）的单据：保存后 InvPosition 1 行、货位存量按 delta 变 → 删除 → 全部回退。"""
    run.drop(kind[0], bin_.doc)
    saved = bin_.probe()
    run.settle(1)
    problem = _stage_problem("保存后", saved, bin_.psum + delta) or bin_.gone_problem()
    if problem:
        raise _Fail(problem)
    return "PASS", f"{kind[1]} {bin_.where()}：InvPosition 1 行、货位存量 {delta:+d}，删除后全部回退"


# ---- 材料出库（无来源）、产成品入库（参照生产订单） ----


def _mout_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(mout_pick_sql(), 5)
    if parts is None:
        return "N/A", "测试账套挑不到货位仓库里有结存的普通存货或材料出库的部门"
    if parts[0].upper() in ("1", "TRUE"):
        return "N/A", "库存选项「领料必有订单」（ST.ballowAddnewVouch）打开，不能无来源录入材料出库单"
    bin_ = _Bin(run, "11", parts[1:4])
    head = {"cwhcode": bin_.wh, "crdcode": td(MATERIAL_OUT_RD), "cdepcode": safe_lot(parts[4]),
            "ddate": run.env.today, "cmemo": _MEMO}
    line = {"cinvcode": bin_.inv, "iquantity": 1, "cposition": bin_.pos}
    bin_.doc = run.made(_CREATE, {"type": "material_out", "head": head, "lines": [line]}, "新建无来源材料出库")
    return _saved_back(run, bin_, ("material_out", "材料出库"), -1)


def _pin10_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(mo_bin_pick_sql(), 5)
    if parts is None:
        return "N/A", "测试账套挑不到不需检验、剩余 >= 2 的已审核生产订单行或货位仓库的末级货位"
    mo, mod = int(parts[0]), int(parts[1])
    bin_ = _Bin(run, "10", parts[2:5])
    head = {"cwhcode": bin_.wh, "crdcode": td(PRODUCT_IN_RD), "ddate": run.env.today, "cmemo": _MEMO}
    line = {"source_line_id": mod, "quantity": 1, "cposition": bin_.pos}
    body = {"type": "product_in", "id": mo, "source_type": "production_order", "head": head, "lines": [line]}
    bin_.doc = run.made(_GEN, body, "参照生产订单生成产成品入库")
    return _saved_back(run, bin_, ("product_in", "产成品入库"), 1)


def binned_purchase_in(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _pin_body)


def binned_other_out(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _oout_body)


def binned_material_out(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _mout_body)


def binned_product_in(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _pin10_body)


STOCK_BINNED_OPS = {
    "binned_purchase_in": binned_purchase_in,
    "binned_other_out": binned_other_out,
    "binned_material_out": binned_material_out,
    "binned_product_in": binned_product_in,
}


def _case(name: str, title: str, op: str, tags: tuple[str, ...], routes: tuple[str, ...]) -> CaseSpec:
    step = Step("custom", sql_name=op, id_from="none")
    return CaseSpec(name, title, (), (step,), True, tags=frozenset(tags), routes=routes)


def stock_binned_cases() -> list[CaseSpec]:
    # 都动现存量和货位存量（stock），与其他货位用例互斥。
    return [
        _case("binned-purchase-in-delete",
              "有货位记录的无来源采购入库：保存 / 审核后的货位台账，弃审删除后 InvPosition 与货位存量回退",
              "binned_purchase_in", ("opt:havepo", "new:purchase_in", "w:purchase_in", "stock"),
              (_CREATE, _VERIFY, _DELETE)),
        _case("binned-other-out-delete", "有货位记录的其他出库删除：先清货位再删，InvPosition、货位存量与现存量回退",
              "binned_other_out", ("new:other_out", "w:other_out", "stock"), (_CREATE, _DELETE)),
        # 收发类别取测试数据，缺键时用例记 SKIP。
        replace(_case("binned-material-out-delete",
                      "有货位记录的无来源材料出库删除：行带货位，InvPosition、货位存量与现存量回退",
                      "binned_material_out", ("opt:ballowAddnewVouch", "new:material_out", "w:material_out", "stock"),
                      (_CREATE, _DELETE)), data=(MATERIAL_OUT_RD,)),
        replace(_case("binned-product-in-delete",
                      "参照生产订单生成带货位的产成品入库后删除：InvPosition、货位存量与现存量回退",
                      "binned_product_in", ("new:product_in", "w:product_in", "w:production_order", "stock"),
                      (_GEN, _DELETE)), data=(PRODUCT_IN_RD,)),
    ]
