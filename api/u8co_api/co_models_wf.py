"""审批流的请求和响应。输出模型忽略桥多出来的字段。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from u8co_api.co_models import CoAuth, CoDocState
from u8co_api.co_models_dry import DryRunFlag

_OUT = ConfigDict(extra="ignore")
_ID_MAX = 2147483647
_WF_TYPE = "质量单据：来料检验单、产品检验单、来料不良品处理单、产品不良品处理单"
WfType = Literal[
    "qm_incoming_check",
    "qm_product_check",
    "qm_incoming_reject",
    "qm_product_reject",
]


class CoWfIn(CoAuth):
    type: WfType = Field(..., description=_WF_TYPE)
    id: int = Field(..., gt=0, le=_ID_MAX, description="单据主键，1 到 2147483647")


class CoWfActIn(CoWfIn):
    """提交、撤销、重新提交等写操作：多一个 dry_run（读状态、历史用 CoWfIn，不收）。"""

    dry_run: DryRunFlag = False


class CoWfNoteIn(CoWfActIn):
    opinion: str | None = Field(None, max_length=500, description="审批意见，可省略，最长 500 字")


class CoWfMustIn(CoWfActIn):
    opinion: str = Field(..., min_length=1, max_length=500, description="审批意见，必填，最长 500 字")


class CoTasksIn(CoAuth):
    type: WfType | None = Field(None, description=f"可选。{_WF_TYPE}")


class CoInstance(BaseModel):
    model_config = _OUT
    piid: str = Field(description="流程实例标识")
    running: bool = Field(description="实例是否仍在运行")
    started_by: str = Field(description="发起人人员编码")
    started_at: str = Field(description="发起时间")


class CoPending(BaseModel):
    model_config = _OUT
    task_id: str = Field(description="待办标识")
    activity_id: str = Field(description="节点标识")
    person: str = Field(description="人员编码")
    operator: str = Field(description="操作员编码")
    task_type: int = Field(description="任务类型。1 审批，4 弃审后重审，5 退回后重提")


class CoWf(BaseModel):
    model_config = _OUT
    controlled: bool = Field(description="是否受审批流控制")
    status: str = Field(
        description="审批状态：not_submitted 未提交，in_approval 审批中，approved 已通过，"
        "not_approved 不通过，returned 已退回，not_controlled 未启用审批流",
    )
    verify_state: int = Field(description="表头 IVERIFYSTATE")
    verify_state_new: int = Field(description="表头 iVerifyStateNew。0 未提交，1 审批中，2 已通过，-1 不通过")
    return_count: int = Field(0, description="退回次数")
    current_auditor: str = Field("", description="当前审核人姓名")
    verifier: str = Field("", description="终审人")
    verified_at: str = Field("", description="终审日期")
    instance: CoInstance | None = Field(None, description="流程实例；未提交或已撤销时为空")
    pending: list[CoPending] = Field(default_factory=list, description="待办任务")


class CoHistoryItem(BaseModel):
    model_config = _OUT
    action: int = Field(description="动作编号")
    action_name: str = Field(
        description="动作名：submit、agree、disagree、reject、withdraw、return、abandon、resubmit，其它为 other",
    )
    task: str = Field("", description="任务名称（U8 待办标题）")
    opinion: str = Field(description="审批意见")
    person: str = Field(description="人员编码")
    operator: str = Field(description="操作员编码")
    name: str = Field(description="姓名")
    at: str = Field(description="时间")


class CoTaskItem(BaseModel):
    model_config = _OUT
    task_id: str = Field(description="待办标识")
    type: str = Field(description="单据类型")
    biz: str = Field(description="U8 业务对象，例如 QM04")
    id: int = Field(description="单据主键")
    code: str = Field(description="单据编号")
    task_type: int = Field(description="任务类型。1 审批，4 弃审后重审，5 退回后重提")
    activity_id: str = Field(description="节点标识")
    sender: str = Field(alias="from", description="上一处理人")
    created_at: str = Field(description="创建时间（待办到达时间）")
    title: str = Field("", description="待办标题，即 U8 消息中心显示的那一句")
    piid: str = Field("", description="流程实例标识，同 wf.instance.piid")


class CoLoadState(CoDocState):
    closed: bool | None = Field(None, description="生产订单是否已整单关闭。其它类型不返回")
    # 采购发票、销售发票的应付 / 应收审核状态（verified 仍是采购复核 / 销售复核）。
    arap_verified: bool | None = Field(None, description="采购发票、销售发票是否已在应付 / 应收款管理审核。其它类型不返回")
    arap_verifier: str | None = Field(None, description="应付 / 应收审核人。其它类型不返回")
    arap_verified_at: str | None = Field(None, description="应付 / 应收审核日期。其它类型不返回")
    gl_voucher: bool | str | None = Field(
        None,
        description="采购发票、销售发票：应付 / 应收明细是否已制单（布尔）。收付款单、应收应付单：凭证号（字符串）",
    )


class CoLoadSource(BaseModel):
    model_config = _OUT
    type: str = Field(description="来源单据类型：来料报检单是 arrival，产品报检单是 production_order")
    id: int = Field(description="来源单据主键（到货单 ID / 生产订单 MoId）")
    code: str = Field(description="来源单据编号")


class CoMergeSource(BaseModel):
    model_config = _OUT
    source_line_id: int = Field(description="合并来源主键 QMMergeCheckDetail.AUTOID，即参照生单时的 source_line_id")
    mo_code: str = Field(description="来源生产订单号")
    mo_seq: int = Field(description="来源生产订单行号（SortSeq）")
    mo_detail_id: int = Field(description="来源生产订单行主键 MoDId")
    inspect_code: str = Field(description="来源产品报检单号")
    qualified: float = Field(description="该来源的合格数量")
    concession: float = Field(description="该来源的让步接收数量")
    stocked: float = Field(description="该来源的累计入库数量")
    remaining: float = Field(description="剩余可入库数量：合格 + 让步接收 − 累计入库，不小于 0")
    done: bool = Field(description="U8 是否已把该来源标为入库完毕（BPROINFLAG=1），为 true 时不能再生单")


class CoLoadOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="是否读到单据")
    type: str = Field(description="单据类型")
    id: int = Field(description="单据主键")
    code: str = Field(description="单据编号")
    # 字段权限：操作员在 U8 里无权查看的字段值为 null，字段名列在 masked_fields。
    head: dict[str, str | None] = Field(
        description="表头。值是 U8 存成的字符串，空值省略；无权查看的字段为 null（见 masked_fields）"
    )
    lines: list[dict[str, str | None]] = Field(description="明细行。值是字符串；无权查看的字段为 null")
    state: CoLoadState = Field(description="审核状态")
    wf: CoWf | None = Field(None, description="检验单和不良品处理单的审批状态。报检单和其它类型不返回")
    source: CoLoadSource | None = Field(None, description="报检单的来源单据。其它类型不返回")
    lines_truncated: bool | None = Field(None, description="为 true 时明细超过 500 行，只返回前 500 行")
    merge_sources: list[CoMergeSource] | None = Field(
        None,
        description="合并检验（BMERGECHECKFLAG=1）的产品检验单：每个合并来源一项，参照生成产成品入库时按来源逐行。其它不返回",
    )
    allocations: list[dict] | None = Field(None, description="生产订单的子件分配。其它类型不返回")
    positions: list[dict[str, str | None]] | None = Field(
        None,
        description="货位调整单的货位台账（InvPosition，审核后每行一出 inbound=0、一入 inbound=1；未审核为空）。其它类型不返回",
    )
    positions_truncated: bool | None = Field(None, description="为 true 时货位台账超过 1000 行，只返回前 1000 行")
    allocations_truncated: bool | None = Field(
        None,
        description="为 true 时子件分配超过 500 行，只返回前 500 行",
    )
    masked_fields: list[str] | None = Field(
        None,
        description="按 U8 字段权限置为 null 的字段名（排序去重）。操作员对本单据没有受限字段时省略",
    )


class CoStateOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="是否读到审批状态")
    type: str = Field(description="单据类型")
    id: int = Field(description="单据主键")
    code: str = Field(description="单据编号")
    wf: CoWf = Field(description="审批状态")


class CoHistoryOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="是否读到审批历史")
    type: str = Field(description="单据类型")
    id: int = Field(description="单据主键")
    code: str = Field(description="单据编号")
    history: list[CoHistoryItem] = Field(description="按时间排列的审批记录")


class CoTasksOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="是否读到待办")
    operator: str = Field(description="操作员编码")
    person: str = Field(description="操作员对应的人员编码")
    tasks: list[CoTaskItem] = Field(description="该人员尚未处理的待办")
    other_count: int | None = Field(None, description="业务对象无法映射到已知单据类型的待办条数")


class CoActionOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="审批动作是否已完成")
    type: str = Field(description="单据类型")
    id: int = Field(description="单据主键")
    code: str = Field(description="单据编号")
    action: str = Field(description="本次动作，例如 approve、submit")
    u8_message: str = Field("", description="U8 返回的说明。没有则为空字符串")
    wf: CoWf = Field(description="动作之后重新读到的审批状态")
    orphan_tasks_cleaned: int | None = Field(
        None, description="审批后清理掉的 U8 孤儿任务行数（只在大于 0 时出现）"
    )
