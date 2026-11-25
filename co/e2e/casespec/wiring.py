"""接线：把各模块的用例、挑单、正文构造器、回读 SQL 和样例 SQL 汇到一处。

catalog、picks、steps._make、steps_sql._flow_cell、samples 只认本模块，保持它们各自够小。
"""

from __future__ import annotations

from co.e2e.casespec.model import CaseSpec
from co.e2e.casespec.arap_edit import arap_edit_cases
from co.e2e.casespec.arap_edit_bodies import EDIT_ARAP_BUILDERS
from co.e2e.casespec.arap_edit_sql import has_edit_arap, edit_arap_samples, edit_arap_sql
from co.e2e.casespec.pu_return_gen import MISC_BUILDERS, MISC_PACKS, has_misc, misc_cases, misc_samples, misc_sql
from co.e2e.casespec.mo_close import MO_BUILDERS, mo_cases
from co.e2e.casespec.mo_close_sql import MO_PACKS, has_mo, mo_samples, mo_sql
from co.e2e.casespec.pu_edit import pu_edit_cases
from co.e2e.casespec.pu_edit_bodies import PU_MOD_BUILDERS
from co.e2e.casespec.pu_edit_sql import PU_MOD_PACKS, has_pu_mod, pu_mod_samples, pu_mod_sql
from co.e2e.casespec.sale_edit import sale_edit_cases
from co.e2e.casespec.sale_edit_bodies import SA_MOD_BUILDERS
from co.e2e.casespec.sale_edit_sql import SA_MOD_PACKS, has_sa_mod, sa_mod_samples, sa_mod_sql
from co.e2e.casespec.stock_gen_edit import stock_edit_cases
from co.e2e.casespec.stock_gen_edit_bodies import SE_BUILDERS
from co.e2e.casespec.stock_gen_edit_sql import SE_PACKS, has_stock_edit, stock_edit_samples, stock_edit_sql
from co.e2e.casespec.stock_misc import STOCK_MISC_BUILDERS, stock_misc_cases
from co.e2e.casespec.stock_misc_sql import STOCK_MISC_PACKS, has_stock_misc, stock_misc_samples, stock_misc_sql
from co.e2e.casespec.bom_write import BOMW_BUILDERS
from co.e2e.casespec.bom_write_sql import BOMW_PACKS, bomw_samples, bomw_sql, has_bomw
from co.e2e.casespec.mo_create import MOC_BUILDERS
from co.e2e.casespec.mo_create_sql import MOC_PACKS, has_moc, moc_samples, moc_sql
from co.e2e.casespec.qm_write import QMI_BUILDERS
from co.e2e.casespec.qm_write_sql import QMI_PACKS, has_qmi, qmi_samples, qmi_sql
from co.e2e.casespec.sale_red_invoice import RS_BUILDERS
from co.e2e.casespec.sale_red_invoice_sql import RS_PACKS, has_rs, rs_samples, rs_sql
from co.e2e.casespec.so_lock_sql import LOCK_PACKS, has_lock, lock_samples, lock_sql
from co.e2e.casespec.pu_red_chain import REDPUR_BUILDERS
from co.e2e.casespec.pu_red_chain_sql import REDPUR_PACKS, has_redpur, redpur_samples, redpur_sql
from co.e2e.casespec.stock_other_docs import SM_BUILDERS
from co.e2e.casespec.stock_other_docs_sql import SM_PACKS, has_sm, sm_samples, sm_sql
from co.e2e.casespec.transfer_gen import TG_BUILDERS
from co.e2e.casespec.transfer_gen_sql import TG_PACKS, has_tg, tg_samples, tg_sql
from co.e2e.casespec.mo_update_subs_sql import MOS_PACKS
from co.e2e.casespec.mo_update import MOU_BUILDERS
from co.e2e.casespec.mo_update_sql import has_mou, mou_samples, mou_sql
from co.e2e.casespec.arap_verify_sql import ARV_BUILDERS, ARV_PACKS, arv_samples, arv_sql, has_arv
from co.e2e.casespec.writeoff import HX_BUILDERS
from co.e2e.casespec.writeoff_sql import HX_PACKS, has_hx, hx_samples, hx_sql
from co.e2e.casespec.writeoff_auto import HXA_BUILDERS
from co.e2e.casespec.writeoff_auto_sql import HXA_PACKS, hxa_samples
from co.e2e.casespec.arap_voucher import AV_BUILDERS
from co.e2e.casespec.arap_voucher_sql import av_samples, av_sql, has_av
from co.e2e.casespec.dry_bodies import DRY_SUITE_BUILDERS
from co.e2e.casespec.qm_update import QMU_BUILDERS
from co.e2e.casespec.qm_update_sql import QMU_PACKS, has_qmu, qmu_samples, qmu_sql
from co.e2e.casespec.dry_sql import DRY_AP_PACKS, dry_suite_samples

# 挑单登记（同 returns_misc_sql.CUSTOM_PACKS 的形状）：前导键、按 | 拆开的键、是否必须是正整数 id、SQL。
EDIT_PACKS = SA_MOD_PACKS + PU_MOD_PACKS + SE_PACKS + STOCK_MISC_PACKS + MISC_PACKS + MO_PACKS
# 生产订单新增 / 删除（mo_create）的挑单、正文、回读也汇到这里；用例在 catalog 里另列（mo_create.moc_cases）。
EDIT_PACKS += MOC_PACKS
# 生产订单修改保留替代料（mo_update_subs）的挑单（BOM 子件带替代料）；用例经 mo_update.mou_cases 登记。
EDIT_PACKS += MOS_PACKS
# 物料清单（bom_write）同样汇到这里；用例在 catalog 里另列（bom_write.bomw_cases）。
EDIT_PACKS += BOMW_PACKS
# 质量单据新增 / 删除（qm_write）同样汇到这里；用例在 catalog 里另列（qm_write.qmi_cases）。
EDIT_PACKS += QMI_PACKS
# 销售订单 / 采购订单锁定（so_lock）的挑单与回读；用例在 catalog 里另列（so_lock.lock_cases）。
EDIT_PACKS += LOCK_PACKS
# 红字采购链路（pu_red_chain）同样汇到这里；用例在 catalog 里另列（pu_red_chain.redpur_cases）。
EDIT_PACKS += REDPUR_PACKS
# 红字销售发票参照退货单（sale_red_invoice）同样汇到这里；用例在 catalog 里另列（sale_red_invoice.rs_cases）。
EDIT_PACKS += RS_PACKS
# 形态转换单、调拨申请单、盘点单（stock_other_docs）同样汇到这里；用例在 catalog 里另列（stock_other_docs.sm_cases）。
EDIT_PACKS += SM_PACKS
# 调拨单参照调拨申请单（transfer_gen）；用例在 catalog 里另列（transfer_gen.tg_cases）。
EDIT_PACKS += TG_PACKS
# 应付审核 / 应收审核（arap_verify）；用例在 catalog 里另列（arap_verify.arv_cases）。
EDIT_PACKS += ARV_PACKS
# 应收 / 应付核销（writeoff）；用例在 catalog 里另列（writeoff.hx_cases）。
EDIT_PACKS += HX_PACKS
# 自动核销（writeoff_auto）：回读沿用 HX8；用例在 catalog 里另列（writeoff_auto.hxa_cases）。
EDIT_PACKS += HXA_PACKS
# 应付自动核销（ap_writeoff_voucher）；用例在 catalog 里另列（dry_suite.dry_suite_cases）。
EDIT_PACKS += DRY_AP_PACKS
# 检验单修改（qm_update）：不良原因编码；用例在 catalog 里另列（qm_update.qmu_cases）。
EDIT_PACKS += QMU_PACKS

_BUILDER_SETS = (SA_MOD_BUILDERS, PU_MOD_BUILDERS, SE_BUILDERS, STOCK_MISC_BUILDERS, EDIT_ARAP_BUILDERS, MISC_BUILDERS, MO_BUILDERS)
_BUILDER_SETS += (MOC_BUILDERS, BOMW_BUILDERS)
_BUILDER_SETS += (QMI_BUILDERS,)
_BUILDER_SETS += (REDPUR_BUILDERS,)
_BUILDER_SETS += (RS_BUILDERS,)
_BUILDER_SETS += (SM_BUILDERS,)
_BUILDER_SETS += (TG_BUILDERS,)
# 生产订单修改（mo_update）：挑单沿用 MOC_PACKS；用例在 catalog 里另列（mo_update.mou_cases）。
_BUILDER_SETS += (MOU_BUILDERS,)
_BUILDER_SETS += (ARV_BUILDERS,)
_BUILDER_SETS += (HX_BUILDERS,)
_BUILDER_SETS += (HXA_BUILDERS,)
# 应收制单（arap_voucher）：挑单沿用 ARV_PACKS；用例在 catalog 里另列（arap_voucher.av_cases）。
_BUILDER_SETS += (AV_BUILDERS,)
# 预演、应收 / 应付制单、应付自动核销的正文（dry_bodies）。
_BUILDER_SETS += (DRY_SUITE_BUILDERS,)
# 检验单修改（qm_update）的正文。
_BUILDER_SETS += (QMU_BUILDERS,)
_READERS = (
    (has_sa_mod, sa_mod_sql),
    (has_pu_mod, pu_mod_sql),
    (has_stock_edit, stock_edit_sql),
    (has_stock_misc, stock_misc_sql),
    (has_edit_arap, edit_arap_sql),
    (has_misc, misc_sql),
    (has_mo, mo_sql),
    (has_moc, moc_sql),
    (has_bomw, bomw_sql),
    (has_qmi, qmi_sql),
    (has_redpur, redpur_sql),
    (has_lock, lock_sql),
    (has_rs, rs_sql),
    (has_sm, sm_sql),
    (has_tg, tg_sql),
    (has_mou, mou_sql),
    (has_arv, arv_sql),
    (has_hx, hx_sql),
    (has_av, av_sql),
    (has_qmu, qmu_sql),
)


def _merge() -> dict:
    found: dict = {}
    for part in _BUILDER_SETS:
        clash = set(found) & set(part)
        if clash:
            raise RuntimeError("正文构造器重名：" + ", ".join(sorted(clash)))
        found.update(part)
    return found


EDIT_BUILDERS = _merge()


def has_edit(name: str) -> bool:
    return any(has(name) for has, _sql in _READERS)


def edit_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    for has, reader in _READERS:
        if has(name):
            return reader(name, doc_id, bag)
    raise ValueError("未知 SQL")


def edit_samples() -> list[tuple[str, str]]:
    rows = rs_samples() + sa_mod_samples() + pu_mod_samples() + stock_edit_samples() + stock_misc_samples()
    rows += edit_arap_samples() + misc_samples() + mo_samples() + moc_samples() + bomw_samples()
    rows += lock_samples()
    rows += sm_samples()
    rows += tg_samples()
    rows += mou_samples()
    rows += arv_samples()
    rows += hx_samples()
    rows += hxa_samples()
    rows += av_samples()
    rows += dry_suite_samples()
    rows += qmu_samples()
    return rows + qmi_samples() + redpur_samples()


def wiring_cases() -> list[CaseSpec]:
    first = list(sale_edit_cases()) + list(pu_edit_cases()) + list(stock_edit_cases())
    return first + list(stock_misc_cases()) + list(arap_edit_cases()) + list(misc_cases()) + list(mo_cases())
