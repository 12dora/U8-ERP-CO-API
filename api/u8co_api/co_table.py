"""Declarative /v1/co routes. One entry per bridge path."""

from collections.abc import Callable
from dataclasses import dataclass

from u8co_api.co_models import (
    CoAuth,
    CoCreateIn,
    CoCreateOut,
    CoDeleteIn,
    CoDeleteOut,
    CoLoadIn,
    CoLoginOut,
    CoVerifyIn,
    CoVerifyOut,
    CoVoucherVerifyIn,
    CoVoucherVerifyOut,
)
from u8co_api.co_models_arap import ARAP_ROUTE_HELP
from u8co_api.co_models_edit import (
    CoCloseIn,
    CoCloseOut,
    CoGenerateIn,
    CoGenerateOut,
    CoUpdateIn,
)
from u8co_api.co_models_update_out import CoUpdateOut
from u8co_api.co_models_lock import LOCK_HELP, LOCK_SUMMARY, CoLockIn, CoLockOut
from u8co_api.co_models_wf import (
    CoActionOut,
    CoHistoryOut,
    CoLoadOut,
    CoStateOut,
    CoTasksIn,
    CoTasksOut,
    CoWfActIn,
    CoWfIn,
    CoWfMustIn,
    CoWfNoteIn,
)
from u8co_api.co_models_writeoff import (
    CANCEL_HELP,
    CANCEL_SUMMARY,
    WRITEOFF_HELP,
    WRITEOFF_SUMMARY,
    CoWriteoffCancelIn,
    CoWriteoffCancelOut,
    CoWriteoffIn,
    CoWriteoffOut,
)
from u8co_api.co_models_writeoff_auto import AUTO_HELP, AUTO_SUMMARY, CoWriteoffAutoIn, CoWriteoffAutoOut

TAG_CO = "U8 业务操作"
TAG_READ = "单据读取"
TAG_AUDIT = "单据审核"
TAG_EDIT = "单据新增删除"
TAG_FLOW = "审批流"
TAG_GEN = "参照生单"


@dataclass(frozen=True)
class CoRoute:
    path: str
    bridge_path: str
    body_model: type
    out_model: type
    summary: str
    description: str
    operation_id: str
    action: str
    tag: str = TAG_CO
    # 不走通用转发时的执行函数，签名同 co_service.run_call（例如 idempotency/get 要改写请求体、翻译结果）。
    runner: Callable[..., dict] | None = None
    # 响应里保留值为 null 的键（只去掉没给的键），如经营管理和合并报表的 consolidated 总在响应里。
    keep_null: bool = False


ROUTES = (
    CoRoute(
        "/v1/co/login-check",
        "/v1/login-check",
        CoAuth,
        CoLoginOut,
        "校验 U8 操作员登录",
        "校验操作员能否登录。口令随请求提交，服务不保存。",
        "coLoginCheck",
        "co:login-check",
    ),
    CoRoute(
        "/v1/co/sale-orders/verify",
        "/v1/sale-orders/verify",
        CoVerifyIn,
        CoVerifyOut,
        "审核或弃审销售订单",
        "id 是 SO_SOMain.ID。action 为 verify 或 unverify。",
        "coSaleOrdersVerify",
        "co:sale-orders/verify",
    ),
    CoRoute(
        "/v1/co/dispatches/verify",
        "/v1/dispatches/verify",
        CoVerifyIn,
        CoVerifyOut,
        "审核或弃审发货单",
        "id 是 DispatchList.DLID。只处理蓝字发货单。",
        "coDispatchesVerify",
        "co:dispatches/verify",
    ),
    CoRoute(
        "/v1/co/vouchers/load",
        "/v1/vouchers/load",
        CoLoadIn,
        CoLoadOut,
        "读取单据",
        "按类型和主键读取表头、明细和审核状态。含调拨单、销售发票、采购发票、生产订单、收款单、付款单、"
        "应收单和应付单。检验单和不良品处理单另附审批状态。来料报检单、产品报检单读取时"
        "另附来源单据 source（到货单或生产订单）。"
        "物料清单（bom，id 是 BomId）只读标准 BOM，code 是母件存货编码，head / lines 的键是小写下划线。",
        "coVoucherLoad",
        "co:vouchers/load",
        TAG_READ,
    ),
    CoRoute(
        "/v1/co/vouchers/verify",
        "/v1/vouchers/verify",
        CoVoucherVerifyIn,
        CoVoucherVerifyOut,
        "审核或弃审",
        "按单据类型审核或弃审。采购发票的 verify 是采购复核（取消复核），销售发票的 verify 是销售复核。检验单和不良品处理单不能直接审核，请用 workflow/*；"
        "来料报检单不支持单独审核、弃审（U8 保存时按选项自动审核，删除时桥会先弃审）；产品报检单只支持弃审（action=unverify）。"
        "调拨单审核可以返回生成的其他出入库单。收款单、付款单、应收单和应付单走应收应付模块的审核；"
        "生产订单审核即 U8 的审核（下达），id 是 MoId，全部明细审核后 state.verified 为 true。"
        "请购单审核、弃审走采购的 ConfirmApp / CancelconfirmApp，已被采购订单参照的不能弃审。"
        "物料清单（bom，id 是 BomId）走 U8API BomAuditing / BomUnauditing，审批流控制的版本 409 workflow_enabled。"
        + ARAP_ROUTE_HELP,
        "coVoucherVerify",
        "co:vouchers/verify",
        TAG_AUDIT,
    ),
    CoRoute(
        "/v1/co/vouchers/create",
        "/v1/vouchers/create",
        CoCreateIn,
        CoCreateOut,
        "新增单据",
        "只接受销售订单、采购订单、其他入库单、其他出库单、调拨单、收款单、付款单、应收单和应付单。"
        "表头和明细的值不能嵌套。收付款单和应收应付单的表头用 U8 列名，必须有 cDwCode；收款单和付款单还要 cSSCode "
        "和 cCode；每行 iAmt 必须大于 0。"
        "供应商退款（ap_refund，AP48，往来单位是供应商）、客户退款（ar_refund，AR49，往来单位是客户）同收付款单，"
        "金额填正数，审核时 U8 在往来明细里记负数。"
        "请购单（purchase_requisition）的明细必须有 cinvcode 和大于 0 的 fquantity，单价只收 ioricost 或 ioritaxcost。"
        "形态转换单（shape_change）每行要有 cwhcode、bavtype（转换前 / 转换后）和大于 0 的 iavquantity，按 igroupno 成组；"
        "调拨申请单（transfer_request）表头要有 cowhcode、ciwhcode；盘点单（stock_check）表头要有 cwhcode，"
        "每行要有 icvcquantity（实盘，可为 0），账面数量 icvquantity 不给时按现存量填。"
        "期初结存单（stock_opening，U8 单据类型 34）默认关闭（桥未开 enableReplicatedWrites 时 403 feature_disabled），"
        "打开后只对 CO 桥配置为测试账套的账套开放（其他账套 403 test_account_only），走 U8 官方 EAI 导入（storeqc）：每行建成一张单据，"
        "响应 docs 逐张列出 id、code、line、wh、inv、qty，顶层 id、code 是第一张，另有 count、date；"
        "字段名单见 docs/api-reference.md，行上的 cwhcode 覆盖表头；日期固定为库存启用日前一天"
        "（传入的不用，见 warnings）；预演只做调用前的检查（validate），不导入。存货核算已期初记账 409 "
        "state_mismatch；库存启用月已月结时 U8 拒绝 409 u8_rejected。不能修改，请删除后重新录入。"
        "生产订单（production_order）走 U8API MOrderAdd：字段见 lines 的说明，U8 自动编号并按标准 BOM 展开子件，"
        "响应另有 allocates、details 和 warnings；U8 拒绝 409 u8_rejected，生产制造服务未运行 503 u8_unavailable，"
        "调用异常且回读不到新订单 504 outcome_unknown（不要重投，先按单号或列表核对）。"
        "物料清单（bom）走 U8API BomAdd，建母件的一个标准 BOM 新版本，响应另有 version 和 components；"
        "版本号或生效日期与已有版本重复 409 state_mismatch，结果码同生产订单。"
        "采购结算单（purchase_settle）是手工结算（不只限测试账套，按写入策略放行），"
        "按 U8 手工结算界面执行的 SQL 写（实测核对）：明细 1 到 400 行（入库行、发票行配对，或红蓝入库对冲、红蓝发票对冲），"
        "响应另有 settle_date；删除走 vouchers/delete。",
        "coVoucherCreate",
        "co:vouchers/create",
        TAG_EDIT,
    ),
    CoRoute(
        "/v1/co/vouchers/delete",
        "/v1/vouchers/delete",
        CoDeleteIn,
        CoDeleteOut,
        "删除单据",
        "只删除未审核的销售订单、其他入库单、其他出库单、采购订单、调拨单、发货单、到货单、销售出库单、"
        "采购入库单、销售发票、采购发票、收款单、付款单、应收单或应付单。材料出库单和产成品入库单只删除参照生产订单或"
        "产品检验单生成的未审核单据。已生成凭证或已核销的收付款单和应收应付单不能删除。"
        "请购单只删除未审核、未关闭、未被采购订单参照的单据。"
        "生产订单（id 是 MoId）只删除全部行未审核、未提交审批、未被材料出库或产成品入库引用的订单，"
        "走 U8API MOrderDelete。"
        "物料清单（id 是 BomId）只删除未审核、未进审批流、没有生产订单、委外订单、组装 / 拆卸 / 形态转换单、配比出库单、调拨单引用的版本，走 U8API BomDelete。"
        "来料报检单、产品报检单只删除没有检验单引用的单据，已审核的先弃审再删除（要有弃审权限，否则 409）；"
        "来料检验单、产品检验单只删除未审核、未提交审批、未被采购入库 / 产成品入库或不良品处理单引用的单据。"
        "报检单、检验单的删除由 U8 自己提交，调用异常且回读不清时 504 outcome_unknown。",
        "coVoucherDelete",
        "co:vouchers/delete",
        TAG_EDIT,
    ),
    CoRoute(
        "/v1/co/vouchers/update",
        "/v1/vouchers/update",
        CoUpdateIn,
        CoUpdateOut,
        "修改单据",
        "修改未审核、未关闭且没有下游的单据。head 与 lines 至少给一个非空。lines 的 op 为 add、update 或 delete。"
        "生产订单（id 是 MoId）走 U8API MOrderLoad + MOrderUpdate：只改未审核、无下游的订单，行按 line_id 定位，"
        "不能增删行、换存货；桥把加载出的子件全部重送（改了数量的行按 U8 的算法重算子件数量）并写回 U8 重插时丢掉的关联列，响应另有 allocates、details、"
        "changed；修改后回读子件对不上时 504 outcome_unknown（不要重投，先读取核对）。"
        "物料清单（bom）只改未审核的标准 BOM，明细按 sort_seq 定位，桥读出全部行叠上修改后整套送 U8API BomUpdate；"
        "行上有替代料、定位符等接口带不回去的内容时 409 state_mismatch。",
        "coVoucherUpdate",
        "co:vouchers/update",
        TAG_EDIT,
    ),
    CoRoute(
        "/v1/co/vouchers/close",
        "/v1/vouchers/close",
        CoCloseIn,
        CoCloseOut,
        "关闭或打开单据",
        "只接受销售订单、采购订单、请购单和生产订单。action 为 close 或 open。line_ids 省略时处理整张单据；"
        "请购单只能整单关闭或打开，不能带 line_ids。生产订单的 id 是 MoId、line_ids 是 MoDId，"
        "省略 line_ids 时处理全部状态相符的行（关闭取已审核行，打开取已关闭行）；"
        "本月已做成本计算、还有在制品、审批流控制的行都拒绝。",
        "coVoucherClose",
        "co:vouchers/close",
        TAG_EDIT,
    ),
    CoRoute(
        "/v1/co/vouchers/generate",
        "/v1/vouchers/generate",
        CoGenerateIn,
        CoGenerateOut,
        "参照生单",
        "发货单参照销售订单，销售出库参照发货单且不能带表头（不带 lines 整张生成，带 lines 按行部分生成），采购入库参照采购订单或来料检验单"
        "（source_type=qm_incoming_check，只能 1 行，表头必须有 cWhCode），销售发票参照发货单，"
        "采购发票参照采购入库单（表头 cPBVCode 必填；红字入库单生成红字发票），"
        "红字采购入库参照采购退货单（source_type=purchase_return，表头必须有 cWhCode），到货单参照采购订单（id 是 POID，"
        "表头只收 cWhCode、dDate、cMemo、cDepCode），"
        "材料出库参照生产订单（表头必须有 cWhCode），产成品入库参照产品检验单（只能 1 行，表头必须有 cWhCode）。"
        "新单据保持未审核。"
        "来料报检单参照已审核的蓝字到货单、产品报检单参照已审核的生产订单（U8 保存时按设置自动审核，响应 state 为回读结果）；"
        "来料检验单、产品检验单参照已审核的报检单（只能 1 行，表头必须有 cCheckPersonCode，检验方案 project_code "
        "省略时取该存货最近一张检验单的方案，都没有 400），新检验单未提交审批（响应另有 wf）。"
        "调拨单参照已审核的调拨申请单（source_type=transfer_request，仓库取自申请单，数量不超过核准数量减累计调拨数量，"
        "U8 回写申请单行累计调拨数量）。"
        "U8 单据模板设为必输而请求没给、桥也推不出的字段 400 bad_request，一次列出全部。"
        "检验单的新增由 U8 自己提交，调用异常且回读不到新单 504 outcome_unknown。",
        "coVoucherGenerate",
        "co:vouchers/generate",
        TAG_GEN,
    ),
    CoRoute(
        "/v1/co/workflow/state",
        "/v1/workflow/state",
        CoWfIn,
        CoStateOut,
        "查询审批状态",
        "只接受四张质量单据。返回是否受控、当前节点和待办。",
        "coWorkflowState",
        "co:workflow/state",
        TAG_FLOW,
    ),
    CoRoute(
        "/v1/co/workflow/history",
        "/v1/workflow/history",
        CoWfIn,
        CoHistoryOut,
        "查询审批历史",
        "只接受四张质量单据。按时间返回审批记录。",
        "coWorkflowHistory",
        "co:workflow/history",
        TAG_FLOW,
    ),
    CoRoute(
        "/v1/co/workflow/tasks",
        "/v1/workflow/tasks",
        CoTasksIn,
        CoTasksOut,
        "查询待办",
        "返回当前操作员的待办。type 可省略；给出时只接受四张质量单据。",
        "coWorkflowTasks",
        "co:workflow/tasks",
        TAG_FLOW,
    ),
    CoRoute(
        "/v1/co/workflow/submit",
        "/v1/workflow/submit",
        CoWfActIn,
        CoActionOut,
        "提交审批",
        "把质量单据提交到审批流。不能带 opinion。",
        "coWorkflowSubmit",
        "co:workflow/submit",
        TAG_FLOW,
    ),
    CoRoute(
        "/v1/co/workflow/withdraw",
        "/v1/workflow/withdraw",
        CoWfActIn,
        CoActionOut,
        "撤销提交",
        "撤回尚未审完的提交。不能带 opinion。",
        "coWorkflowWithdraw",
        "co:workflow/withdraw",
        TAG_FLOW,
    ),
    CoRoute(
        "/v1/co/workflow/approve",
        "/v1/workflow/approve",
        CoWfNoteIn,
        CoActionOut,
        "同意",
        "审批同意。opinion 可省略，最长 500 字。",
        "coWorkflowApprove",
        "co:workflow/approve",
        TAG_FLOW,
    ),
    CoRoute(
        "/v1/co/workflow/disagree",
        "/v1/workflow/disagree",
        CoWfMustIn,
        CoActionOut,
        "不同意并继续",
        "不同意并继续往后流转。opinion 必填，最长 500 字。",
        "coWorkflowDisagree",
        "co:workflow/disagree",
        TAG_FLOW,
    ),
    CoRoute(
        "/v1/co/workflow/return",
        "/v1/workflow/return",
        CoWfMustIn,
        CoActionOut,
        "退回提交人",
        "把单据退回提交人。opinion 必填，最长 500 字。",
        "coWorkflowReturn",
        "co:workflow/return",
        TAG_FLOW,
    ),
    CoRoute(
        "/v1/co/workflow/abandon",
        "/v1/workflow/abandon",
        CoWfNoteIn,
        CoActionOut,
        "弃审",
        "撤销本人最近一次同意。opinion 可省略，最长 500 字。",
        "coWorkflowAbandon",
        "co:workflow/abandon",
        TAG_FLOW,
    ),
    CoRoute(
        "/v1/co/workflow/resubmit",
        "/v1/workflow/resubmit",
        CoWfActIn,
        CoActionOut,
        "退回后重新提交",
        "退回提交人之后重新提交。不能带 opinion。",
        "coWorkflowResubmit",
        "co:workflow/resubmit",
        TAG_FLOW,
    ),
    # 销售订单 / 采购订单锁定、解锁（co_models_lock）。
    CoRoute(
        "/v1/co/vouchers/lock",
        "/v1/vouchers/lock",
        CoLockIn,
        CoLockOut,
        LOCK_SUMMARY,
        LOCK_HELP,
        "coVoucherLock",
        "co:vouchers/lock",
        TAG_EDIT,
    ),
    # 应收 / 应付核销（co_models_writeoff）。
    CoRoute(
        "/v1/co/arap/writeoff",
        "/v1/arap/writeoff",
        CoWriteoffIn,
        CoWriteoffOut,
        WRITEOFF_SUMMARY,
        WRITEOFF_HELP,
        "coArapWriteoff",
        "co:arap/writeoff",
    ),
    # 取消核销（co_models_writeoff）。
    CoRoute(
        "/v1/co/arap/writeoff/cancel",
        "/v1/arap/writeoff/cancel",
        CoWriteoffCancelIn,
        CoWriteoffCancelOut,
        CANCEL_SUMMARY,
        CANCEL_HELP,
        "coArapWriteoffCancel",
        "co:arap/writeoff/cancel",
    ),
    # 自动核销（co_models_writeoff_auto）。
    CoRoute(
        "/v1/co/arap/writeoff/auto",
        "/v1/arap/writeoff/auto",
        CoWriteoffAutoIn,
        CoWriteoffAutoOut,
        AUTO_SUMMARY,
        AUTO_HELP,
        "coArapWriteoffAuto",
        "co:arap/writeoff/auto",
    ),
)
