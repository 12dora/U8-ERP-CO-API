"""造数步骤的顺序、plan（只读）和 run。"""

from __future__ import annotations

from co.e2e.seed import (
    emit,
    steps_archives,
    steps_docs_arap,
    steps_docs_arap_more,
    steps_docs_ato,
    steps_docs_fx,
    steps_docs_mo,
    steps_docs_purchase,
    steps_docs_qc,
    steps_docs_qm_rej,
    steps_docs_sale,
    steps_docs_stock,
    steps_fx_control,
    steps_inventory,
    steps_people,
    steps_period_close,
    steps_pu_opening,
)
from co.e2e.seed.base import Step, record_step
from co.e2e.seed.context import SeedContext, SeedError
from co.e2e.seed.emit import reminder

# 单据：BOM（两个母件）→ 库存 → 销售 → 采购期初记账 → 采购 → 来料检验链 →
# 生产订单（两张）、材料出库、产品检验链（含 QM04 审批和产成品入库）→ 不良品处理用检验单（QM03、QM04 各一张）→
# 收付款单 → 应付美元受控科目 → 应付核销链、预收预付 → 外币单据。
# 材料出库要在其他入库之后（子件有库存），QM04 审批复用来料检验链的改派同意（qc_approve 之后）。
DOC_STEPS: tuple[Step, ...] = (
    steps_docs_stock.STEPS
    + steps_docs_sale.STEPS
    + steps_pu_opening.STEPS  # 采购发票要求采购管理已期初记账
    + steps_docs_purchase.STEPS
    + steps_docs_qc.STEPS
    + steps_docs_mo.STEPS
    + steps_docs_qm_rej.STEPS  # 不良品处理单用例的来源检验单（合格 1、不良 1，走完审批）
    + steps_docs_arap.STEPS
    + steps_fx_control.STEPS  # 应付美元受控科目（SQL 复制同级科目），给 fx_ap_*
    + steps_docs_arap_more.STEPS  # 应付核销链、预收 / 预付款单
    + steps_docs_fx.STEPS  # 本月美元汇率和外币收付款单、应收应付单
)

# 依赖顺序：部门、人员在审批人之前；客户要业务员；存货要单位和分类；测试数据文件只依赖档案，
# 写在单据之前，单据半途失败时档案和测试数据文件已经齐了。
ALL_STEPS: tuple[Step, ...] = (
    steps_archives.STEPS
    + steps_people.STEPS
    + steps_archives.LATE_STEPS
    + steps_inventory.STEPS
    + (emit.STEP,)
    + DOC_STEPS
    + steps_docs_ato.STEPS  # ATO 模型、选项类、销售订单和选配结果，供生产订单参照销售订单生成的用例 `mo-generate-*`
    + steps_period_close.STEPS  # 最后：造的单据都在本月，只结之前的月份（不在 testAccounts 里时跳过）
)
_SHOWN = 12


def _names(text: str) -> list[str]:
    names = [name.strip() for name in text.split(",") if name.strip()]
    known = [step.name for step in ALL_STEPS]
    unknown = [name for name in names if name not in known]
    if unknown:
        raise SeedError("未知步骤：" + "、".join(unknown) + "。可选：" + "、".join(known))
    return names


def select(only: str, skip: str = "") -> tuple[Step, ...]:
    """--only 选出的步骤（空为全部），再去掉 --skip 的；顺序总是 ALL_STEPS 的顺序。"""
    picked = _names(only)
    dropped = set(_names(skip))
    return tuple(step for step in ALL_STEPS if (not picked or step.name in picked) and step.name not in dropped)


def _brief(items: tuple[str, ...]) -> str:
    text = "、".join(items[:_SHOWN])
    if len(items) > _SHOWN:
        text += " 等 " + str(len(items)) + " 项"
    return text


def plan(ctx: SeedContext, steps: tuple[Step, ...]) -> int:
    for step in steps:
        try:
            found = step.probe(ctx)
        except SeedError as exc:
            _fail(ctx, step, exc)
            return 1
        line = f"[计划] {step.title}（{step.name}）：已存在 {len(found.existing)}，待建 {len(found.missing)}"
        if found.missing:
            line += "：" + _brief(found.missing)
        print(line)
    return 0


def _fail(ctx: SeedContext, step: Step, exc: SeedError) -> None:
    print(f"[步骤] {step.title}（{step.name}）：失败")
    print(str(exc)[:500])
    for key in ("field", "hint"):
        if exc.response.get(key):
            print(key + "：" + str(exc.response[key]))
    saved = exc.saved or ctx.last_call
    if saved:
        print("调用记录：" + saved)


def run(ctx: SeedContext, steps: tuple[Step, ...]) -> int:
    for step in steps:
        try:
            ctx.link.window()
            new, existed = step.apply(ctx)
        except SeedError as exc:
            ctx.state["failed"] = step.name
            ctx.save_state()
            _fail(ctx, step, exc)
            return 1
        ctx.state.pop("failed", None)
        record_step(ctx, step.name, new, existed)
        print(f"[步骤] {step.title}：新建 {new}，已存在 {existed}")
    print("造数完成。核对后用 python -m co.e2e.session close --keep 把结果留作这条通道的基线。")
    note = reminder(ctx.state)
    if note:
        print(note)
    return 0
