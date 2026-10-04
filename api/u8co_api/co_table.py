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
from u8co_api.co_models_edit import (
    CoCloseIn,
    CoCloseOut,
    CoGenerateIn,
    CoGenerateOut,
    CoUpdateIn,
)
from u8co_api.co_models_lock import LOCK_HELP, LOCK_SUMMARY, CoLockIn, CoLockOut
from u8co_api.co_models_update_out import CoUpdateOut
from u8co_api.co_models_wf import (
    WF_HELP,
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
from u8co_api.co_table_help import (
    CLOSE_HELP,
    CREATE_HELP,
    DELETE_HELP,
    GENERATE_HELP,
    LOAD_HELP,
    UPDATE_HELP,
    VERIFY_HELP,
)

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
        "审核或弃审销售订单。\n\n"
        "**用法**\n"
        "- id 是 SO_SOMain.ID。\n"
        "- action 为 verify 或 unverify。",
        "coSaleOrdersVerify",
        "co:sale-orders/verify",
    ),
    CoRoute(
        "/v1/co/dispatches/verify",
        "/v1/dispatches/verify",
        CoVerifyIn,
        CoVerifyOut,
        "审核或弃审发货单",
        "审核或弃审发货单。\n\n"
        "**用法**\n"
        "- id 是 DispatchList.DLID。\n\n"
        "**限制**\n"
        "- 只处理蓝字发货单。",
        "coDispatchesVerify",
        "co:dispatches/verify",
    ),
    CoRoute(
        "/v1/co/vouchers/load",
        "/v1/vouchers/load",
        CoLoadIn,
        CoLoadOut,
        "读取单据",
        LOAD_HELP,
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
        VERIFY_HELP,
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
        CREATE_HELP,
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
        DELETE_HELP,
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
        UPDATE_HELP,
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
        CLOSE_HELP,
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
        GENERATE_HELP,
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
        WF_HELP["state"],
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
        WF_HELP["history"],
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
        WF_HELP["tasks"],
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
        WF_HELP["submit"],
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
        WF_HELP["withdraw"],
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
        WF_HELP["approve"],
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
        WF_HELP["disagree"],
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
        WF_HELP["return"],
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
        WF_HELP["abandon"],
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
        WF_HELP["resubmit"],
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
