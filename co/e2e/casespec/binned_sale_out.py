"""有货位记录的销售出库删除（`binned-sale-out-delete`）。补 binned-other-in-delete（其他入库 08）之外的一种出库单据（32）。

用例自己建来源：挑货位仓库里有结存的可销售存货（stock_plus_sql.bin_split_sql，种子数据的货位仓提供）→ 新建并审核
销售订单 1 个 → 参照生成发货单（写货位仓库）并审核 → 参照生成销售出库 1 个、带末级货位 cposition。断言：出库单的
InvPosition 有 1 行、该货位存量（InvPositionSum）比生成前少 1；删除出库单（桥先清货位再删）之后表头和 InvPosition 都没有，
货位存量和现存量回到生成前的值。之后按建的倒序弃审、删除发货单和订单（stock_plus._guard 的清理），清理失败记 FAIL。
挑不到数据记 N/A。

步骤 kind 为 "custom"，sql_name 是操作名（BINNED_SALE_OUT_OPS 并入 ops_run）。
"""

from __future__ import annotations

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _dec, _dispatch, _Fail, _guard, _Run, _sale_order, _sale_out
from co.e2e.casespec.stock_plus_sql import bin_split_sql, out_binned_sql, safe_lot
from co.e2e.casespec.binned_sale_out_sql import out_head_sql, pos_sum_sql, stock_sum_sql

_GEN = "/v1/vouchers/generate"
_CREATE = "/v1/vouchers/create"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"


def _body(run: _Run) -> tuple[str, str]:
    parts = run.pick(bin_split_sql(), 9)
    if parts is None:
        return "N/A", "测试账套挑不到货位仓库里有结存的可销售存货"
    inv, wh, pos = safe_lot(parts[4]), safe_lot(parts[5]), safe_lot(parts[6])
    psum = _dec(run.cell(pos_sum_sql(wh, pos, inv)))
    stock = _dec(run.cell(stock_sum_sql(wh, inv)))
    so = _sale_order(run, parts, 1)
    dl, line = _dispatch(run, so, wh, 1, "")
    out = _sale_out(run, dl, line, [(1, "", pos)])
    rows = run.cell(out_binned_sql(str(out)))
    psum_gen = _dec(run.cell(pos_sum_sql(wh, pos, inv)))
    # 先删出库单（_sale_out 最后记下的清理动作），再判生成后的回读，断言失败也不会把出库单留在库里。
    run.settle(1)
    if rows != "1":
        raise _Fail(f"出库单 {out} 的 InvPosition 应有 1 行，实际 {rows or '空'}")
    if psum_gen != psum - 1:
        raise _Fail(f"生成后货位 {pos} 存量应为 {psum - 1}，实际 {psum_gen}")
    gone = (run.cell(out_head_sql(str(out))), run.cell(out_binned_sql(str(out))))
    if gone != ("0", "0"):
        raise _Fail(f"删除后表头、InvPosition 行数应为 0、0，实际 {gone}")
    back = (_dec(run.cell(pos_sum_sql(wh, pos, inv))), _dec(run.cell(stock_sum_sql(wh, inv))))
    if back != (psum, stock):
        raise _Fail(f"删除后货位存量、现存量应回到 {psum}、{stock}，实际 {back[0]}、{back[1]}")
    return "PASS", f"出库单 {out}（{wh} / {pos} / {inv}）：InvPosition 1 行、货位存量 -1，删除后全部回退"


def binned_sale_out(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _body)


BINNED_SALE_OUT_OPS = {"binned_sale_out": binned_sale_out}


def binned_sale_out_cases() -> list[CaseSpec]:
    # 同 sale-out-bin-split：新建订单、发货单、出库单，动现存量和货位存量（stock）。
    tags = frozenset(
        ("new:sale_order", "w:sale_order", "new:dispatch", "new:sale_return", "w:dispatch", "new:sale_out", "w:sale_out",
         "stock")
    )
    step = Step("custom", sql_name="binned_sale_out", id_from="none")
    title = "有货位记录的销售出库删除：先清货位再删，InvPosition、货位存量与现存量回退"
    return [CaseSpec("binned-sale-out-delete", title, (), (step,), True, tags=tags,
                     routes=(_CREATE, _VERIFY, _GEN, _DELETE))]
