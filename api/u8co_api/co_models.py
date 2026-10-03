"""Request and response models for /v1/co. Passwords are write-only."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from u8co_api.co_models_arap import VERIFY_ACTION_HELP, VoucherVerifyAction, check_verify_action
from u8co_api.co_models_bom import BOM_CREATE_HELP, CoBomComponent, check_bom_create
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_error import ErrorBody as ErrorBody  # 错误体模型移到 co_models_error，这里再导出
from u8co_api.co_models_error import ErrorDetail as ErrorDetail
from u8co_api.co_models_health import CoHealthOut as CoHealthOut  # 健康检查出参移到 co_models_health，这里再导出
from u8co_api.co_models_health import license_detail as license_detail
from u8co_api.co_models_mo import MO_CREATE_HELP, CoMoCreatedLine, check_mo_create
from u8co_api.co_gen_qm import QM_OTHER_CREATE_HELP, check_qm_other_create
from u8co_api.co_edit_qm import check_qm_verify
from u8co_api.co_models_stock_opening import CoOpeningDoc
from u8co_api.co_models_types import CREATE_TYPE_HELP, DELETE_TYPE_HELP, VERIFY_TYPE_HELP, VOUCHER_TYPE_HELP
from u8co_api.co_models_types import Cell as Cell  # 单据类型枚举与说明移到 co_models_types，这里再导出
from u8co_api.co_models_types import CreateType as CreateType
from u8co_api.co_models_types import DeleteType as DeleteType
from u8co_api.co_models_types import VerifyType as VerifyType
from u8co_api.co_models_types import VoucherType as VoucherType
from u8co_api.co_pu_settle_man import LINES_MAX as SETTLE_LINES_MAX
from u8co_api.co_pu_settle_man import SETTLE_CREATE_HELP, check_settle_create
from u8co_api.co_return_apply import CREATE_HELP as RETURN_APPLY_CREATE_HELP
from u8co_api.co_return_apply import check_return_apply_create
from u8co_api.co_srcless import check_srcless_create
from u8co_api.co_stmisc import check_stmisc_lines

_FORBID = ConfigDict(extra="forbid")
_OUT = ConfigDict(extra="ignore")
_ID_MAX = 2147483647


def _operator_ok(value: str) -> str:
    if any(char.isspace() or char in "\"';" for char in value):
        raise ValueError("操作员编码无效")
    return value


class CoAuth(BaseModel):
    """账套、操作员和口令。日期与年度的缺省值在服务里填，不在模型里。"""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"acc": "801", "operator": "op001"}]},
    )
    acc: str = Field(..., pattern=r"^\d{3}$", description="账套号，三位数字", examples=["801"])
    operator: str = Field(
        ...,
        min_length=1,
        max_length=20,
        description="U8 操作员编码，1 到 20 个字符，不含空白、引号或分号",
        examples=["op001"],
    )
    password: str = Field(
        ...,
        min_length=1,
        max_length=128,
        repr=False,
        description="U8 操作员口令。每次请求单独提交，服务不保存",
        json_schema_extra={"writeOnly": True},
    )
    year: str | None = Field(
        None,
        pattern=r"^\d{4}$",
        description="四位会计年度。缺省为登录日期的年份",
    )
    date: str | None = Field(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="登录日期，yyyy-MM-dd。缺省为 U8CO_TIMEZONE 时区的今天",
    )

    @field_validator("operator")
    @classmethod
    def _operator(cls, value: str) -> str:
        return _operator_ok(value)


class CoVerifyIn(CoAuth):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"acc": "801", "operator": "op001", "id": 1, "action": "verify"}]},
    )
    id: int = Field(
        ...,
        gt=0,
        le=_ID_MAX,
        description="单据主键，1 到 2147483647。销售订单是 SO_SOMain.ID，发货单是 DispatchList.DLID",
    )
    action: Literal["verify", "unverify"] = Field(..., description="verify 审核，unverify 弃审")


class CoLoadIn(CoAuth):
    type: VoucherType = Field(..., description=VOUCHER_TYPE_HELP)
    id: int = Field(..., gt=0, le=_ID_MAX, description="单据主键，1 到 2147483647")


class CoVoucherVerifyIn(CoAuth):
    type: VerifyType = Field(..., description=VERIFY_TYPE_HELP)
    id: int = Field(..., gt=0, le=_ID_MAX, description="单据主键，1 到 2147483647")
    action: VoucherVerifyAction = Field(..., description=VERIFY_ACTION_HELP)
    dry_run: DryRunFlag = False

    @model_validator(mode="after")
    def _arap_checked(self) -> CoVoucherVerifyIn:
        check_verify_action(self.type, self.action)
        check_qm_verify(self.type, self.action)  # 产品报检单只收 unverify
        return self


class CoCreateIn(CoAuth):
    type: CreateType = Field(..., description=CREATE_TYPE_HELP)
    head: dict[str, Cell] = Field(..., description="表头。值只能是字符串、数字或布尔，不能嵌套")
    lines: list[dict[str, Cell]] = Field(
        ...,
        min_length=1,
        max_length=SETTLE_LINES_MAX,
        description="明细，1 到 200 行（生产订单 1 到 50 行，采购手工结算 1 到 400 行）。"
        "每行的值只能是字符串、数字或布尔，不能嵌套。"
        + MO_CREATE_HELP
        + BOM_CREATE_HELP
        + QM_OTHER_CREATE_HELP
        + RETURN_APPLY_CREATE_HELP
        + SETTLE_CREATE_HELP,
    )
    dry_run: DryRunFlag = False

    @model_validator(mode="after")
    def _mo_checked(self) -> CoCreateIn:
        if self.type == "production_order":
            check_mo_create(self.head, self.lines)
        if self.type == "bom":
            check_bom_create(self.head, self.lines)
        check_settle_create(self.type, self.head, self.lines)  # 采购手工结算（其余类型仍 1 到 200 行）
        check_srcless_create(self.type, self.head, self.lines)
        check_stmisc_lines(self.type, self.lines)
        check_qm_other_create(self.type, self.head, self.lines)
        check_return_apply_create(self.type, self.head, self.lines)  # 退货申请单参照蓝字发货单行
        return self


class CoDeleteIn(CoAuth):
    type: DeleteType = Field(..., description=DELETE_TYPE_HELP)
    id: int = Field(
        ...,
        gt=0,
        le=_ID_MAX,
        description="单据主键，1 到 2147483647。只删除未审核单据（报检单例外：先弃审再删除）",
    )
    dry_run: DryRunFlag = False


class CoDocState(BaseModel):
    model_config = _OUT
    verified: bool = Field(description="是否已审核")
    verifier: str = Field(description="审核人")
    verified_at: str = Field(description="审核时间")


class CoLoginOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="是否登录成功")
    operator: str = Field(description="操作员编码", examples=["op001"])
    operator_name: str = Field(description="操作员姓名")


class CoVerifyOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="是否已完成审核或弃审")
    acc: str = Field(description="账套号", examples=["801"])
    id: int = Field(description="单据主键")
    action: Literal["verify", "unverify"] = Field(description="verify 审核，unverify 弃审")
    verified_by: str = Field(description="审核人")
    verified_at: str = Field(description="审核时间")


class CoGenerated(BaseModel):
    model_config = _OUT
    type: str = Field(description="审核时生成的下游单据类型")
    id: int = Field(description="下游单据主键")


class CoVerifyState(BaseModel):
    model_config = ConfigDict(extra="allow")
    verified: bool | None = Field(None, description="是否已审核。生产订单为全部明细都已审核")


class CoVoucherVerifyOut(CoVerifyOut):
    type: str = Field(description="单据类型")
    action: VoucherVerifyAction = Field(description=VERIFY_ACTION_HELP)
    acc: str | None = Field(None, description="账套号。收付款单、应收应付单和生产订单可能不返回")
    verified_by: str | None = Field(None, description="审核人。没有则省略")
    verified_at: str | None = Field(None, description="审核时间。没有则省略")
    state: CoVerifyState | None = Field(
        None,
        description="审核后重新读到的状态。收付款单、应收应付单和生产订单返回；采购发票、销售发票的 arap_verify / "
        "arap_unverify 返回 arap_verified、arap_verifier、arap_verified_at、gl_voucher；"
        "不良品处理单（qm_incoming_reject、qm_product_reject）可能返回，审核人、审核时间另在顶层 verified_by、verified_at；"
        "其它类型省略",
    )
    generated: list[CoGenerated] | None = Field(
        None,
        description="调拨单、形态转换单、盘点单审核时生成的其他出库单和其他入库单。没有则省略",
    )
    ar_verifier: str | None = Field(
        None,
        description="销售发票的应收审核人。没有则省略",
    )
    ledger_rows: int | None = Field(
        None,
        description="货位调整单：提交前核对过的本单货位台账行数（审核后是行数的 2 倍，弃审后 0）。其它类型省略",
    )
    bin_moves: list[dict[str, str]] | None = Field(
        None,
        description="货位调整单：涉及的每个货位结存（wh、pos、inv、batch）审核或弃审前后的数量 before、after。其它类型省略",
    )


class CoCreateOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="是否已新增")
    type: str = Field(description="单据类型")
    id: int = Field(description="新单据主键。期初结存单是第一张（docs[0]）")
    code: str = Field(description="新单据编号。期初结存单是第一张（docs[0]）")
    state: CoDocState | None = Field(None, description="审核状态。收付款单和应收应付单可能不返回")
    lines: int | None = Field(None, description="保存后的明细行数。没有则省略")
    allocates: int | None = Field(None, description="生产订单：U8 按标准 BOM 展开的子件总行数。其它类型省略")
    details: list[CoMoCreatedLine] | None = Field(
        None, description="生产订单：每行的主键、行号、存货、数量、状态和子件行数。其它类型省略"
    )
    warnings: list[str] | None = Field(
        None, description="生产订单：有行没有展开子件时的提示；期初结存单：传入的日期未采用时的提示。没有则省略"
    )
    version: int | None = Field(None, description="物料清单：新版本号。其它类型省略")
    components: list[CoBomComponent] | None = Field(
        None, description="物料清单：保存后的每行子件（主键、行号、存货、用量分子分母）。其它类型省略"
    )
    dispatch_id: int | None = Field(None, description="先开票销售发票：U8 按发票生成的发货单主键。读不到或其它类型省略")
    docs: list[CoOpeningDoc] | None = Field(None, description="期初结存单：每行一张单据，按请求行次序。其它类型省略")
    count: int | None = Field(None, description="期初结存单：建成的单据张数（等于明细行数）。其它类型省略")
    date: str | None = Field(None, description="期初结存单：单据日期（库存启用日前一天）。其它类型省略")
    auto_verify_error: str | None = Field(
        None,
        description="其他报检单：保存已成功，但按选项补的自动审核没做（无审核权限）或没成功时的说明，单据为未审核，"
        "可用 vouchers/verify 补审。其它类型省略",
    )
    settle_date: str | None = Field(
        None, description="采购结算单（手工结算）：结算日期（即本次的 U8 登录日期）。其它类型省略"
    )


class CoDeleteOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="是否已删除")
    type: str = Field(description="单据类型")
    id: int = Field(description="被删单据主键")
    deleted: bool = Field(description="删除后表头已不存在则为 true")
    code: str | None = Field(None, description="被删单据编号。桥不报则省略")
    unverified: bool | None = Field(None, description="报检单（含其他报检单）：删除前先弃审了则为 true。其它类型省略")
