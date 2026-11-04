"""销售订单合同号回写 /v1/co/vouchers/so_contract：只改 SO_SOMain 的 cDefine1（合同号），
可选同时改 cDefine2（客户合同号）。

走专用受控 SQL，不走单据修改（vouchers/update）：已审核、已有下游单据的销售订单，U8 客户端和 vouchers/update 都不让改。
金额、数量、日期、部门、业务员、审核人、关闭状态和下游单据（发货单、销售发票、各 History 表）一律不动。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from u8co_api.co_doctext import op_doc
from u8co_api.co_models import _ID_MAX, CoAuth
from u8co_api.co_models_arc_cus import check_text
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import PASS, Scalar

# 请求值的上限。cDefine1 / cDefine2 的实际长度是 U8 自定义项档案（UserDef）的 iMaxLen，
# 由桥核对，超出 400 bad_request；这里只挡明显离谱的输入。
_LIMIT = 120
# U8 把「无」当作未填的占位，不能当作要写入的合同号。
_PLACEHOLDER = "无"

SO_CONTRACT_HELP = op_doc(
    "销售订单合同号回写：只改销售订单表头的合同号（SO_SOMain.cDefine1），可选同时把原合同号挪进客户合同号（cDefine2）。"
    "已审核、已有下游单据的订单也能写。",
    (
        "用法",
        (
            "type 目前只收 sale_order；id 是销售订单主键（SO_SOMain.ID）",
            "contract_no 是要写入的合同号：非空、不是「无」，首尾不能有空白，不含控制字符",
            "customer_contract_no 可选，要写入客户合同号的值（SO_SOMain.cDefine2），规则同 contract_no；"
            "省略或 null 表示不动 cDefine2；给了则 cDefine2 当前必须为空",
            "expect 必填：cDefine1、cDefine2 的当前值（contract_no、customer_contract_no 两个键都要给）；"
            "null、空串、空白、「无」都表示当前为空，彼此等价；API 把 null 按空串转给桥",
            "dry_run 为 true 时跑完全部检查（含锁定、expect、长度）后不写库（validate）",
        ),
    ),
    (
        "规则",
        (
            "只写 cDefine1，给了 customer_contract_no 时再写 cDefine2；同一事务，行锁 UPDLOCK、ROWLOCK，"
            "按行版本（ufts）乐观锁，写后回读",
            "订单被别的用户锁定（cLocker 非空且不是本操作员）409 state_mismatch（detail.reason 为 locked），"
            "请对方保存或解锁后重试",
            "合同号当前已等于 contract_no（去首尾空白、不分大小写比较），"
            "且 customer_contract_no 省略或 cDefine2 当前已等于它时成功返回 changed=false，不写库",
            "给了 customer_contract_no 而 cDefine2 当前非空，409 state_mismatch（不覆盖已有的客户合同号）",
            "不动：金额、数量、日期、部门、业务员、审核和关闭状态，发货单、销售发票、各 History 表；"
            "已经开出的发货单、发票上的合同号不随之改变",
            "写入分类 (销售订单 sale_order, so_contract)，规则键 write:voucher:sale_order:so_contract："
            "放行 vouchers/update 不连带放行本路由；只读账套上仅当只读例外 type=vouchers、op=so_contract "
            "且账套、操作员命中、请求体 type 为 sale_order 才穿过闸门，vouchers/update 等其它单据写入仍 403",
            "功能权限为销售订单卡片的修改 / 变更按钮（功能 id 以桥 PermRegistry 登记为准），"
            "数据权限按订单表头的客户、部门、业务员、销售类型（cSTCode）",
            "写入建议带 Idempotency-Key 头（dry_run 不能带）；同一个键重试原样返回第一次的结果，"
            "包括 504 outcome_unknown",
        ),
    ),
    (
        "错误",
        (
            "400 bad_request：合同号或客户合同号超过 U8 自定义项档案的长度",
            "404 not_found：销售订单不存在",
            "409 state_mismatch，error.detail.reason 为 locked（detail.locker 是锁定人）：订单被别的用户锁定",
            "409 state_mismatch：expect 与当前值不符，或给了 customer_contract_no 而 cDefine2 非空；"
            "error.detail.current 是 {contract_no, customer_contract_no} 的当前原值（空为 null 或空串），"
            "并发修改（行版本变了）没有 detail",
            "504 outcome_unknown：写后回读不一致，先 vouchers/load 核对再决定是否重试",
        ),
    ),
)


def _value(value: str, label: str) -> str:
    """要写入的值：非空、不是「无」、首尾无空白、不含控制字符。"""
    if not value.strip():
        raise ValueError(f"{label} 不能为空")
    check_text(value, label, _LIMIT)
    if value == _PLACEHOLDER:
        raise ValueError(f"{label} 不能是「{_PLACEHOLDER}」")
    return value


class SoContractExpect(BaseModel):
    """调用方认为的当前值。两个键都必须给；null 与空串等价，都表示当前为空。"""

    model_config = ConfigDict(extra="forbid")
    contract_no: str | None = Field(
        ...,
        max_length=_LIMIT,
        description="cDefine1（合同号）当前原值；NULL、空串、空白、「无」都当作空，传 null 或空串",
        examples=["HT202611001"],
    )
    customer_contract_no: str | None = Field(
        ...,
        max_length=_LIMIT,
        description="cDefine2（客户合同号）当前原值；规则同 contract_no",
    )

    @field_validator("contract_no", "customer_contract_no", mode="after")
    @classmethod
    def _current(cls, value: str | None, info: ValidationInfo) -> str:
        # 只核对当前值，不要求写法合规（旧数据可以有首尾空白）；null 转成空串，免得被 API 的去空值逻辑丢掉。
        if value is None:
            return ""
        return check_text(value, f"expect.{info.field_name}", _LIMIT, trimmed=False)


class VoucherSoContractIn(CoAuth):
    type: Literal["sale_order"] = Field(..., description="单据类型，目前只收 sale_order（销售订单）")
    id: int = Field(..., gt=0, le=_ID_MAX, description="销售订单主键（SO_SOMain.ID），1 到 2147483647")
    contract_no: str = Field(
        ...,
        min_length=1,
        max_length=_LIMIT,
        description="要写入 cDefine1（合同号）的值：非空、不是「无」，首尾不能有空白",
        examples=["HT202611001"],
    )
    customer_contract_no: str | None = Field(
        None,
        max_length=_LIMIT,
        description="可选，要写入 cDefine2（客户合同号）的值；省略或 null 不动 cDefine2，给了则 cDefine2 当前必须为空",
        examples=["KH-2026-088"],
    )
    expect: SoContractExpect = Field(description="cDefine1、cDefine2 的当前值，两个键都要给")
    dry_run: DryRunFlag = False

    def audit_ref(self) -> str:
        return f"sale_order:{self.id}"

    @field_validator("contract_no")
    @classmethod
    def _contract_no(cls, value: str) -> str:
        return _value(value, "contract_no")

    @field_validator("customer_contract_no")
    @classmethod
    def _customer_contract_no(cls, value: str | None) -> str | None:
        return None if value is None else _value(value, "customer_contract_no")


class VoucherSoContractOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    type: Scalar = Field(None, description="单据类型")
    id: Scalar = Field(None, description="销售订单主键")
    code: Scalar = Field(None, description="销售订单号")
    changed: bool | None = Field(None, description="订单表头是否改了；请求值都已等于当前值时为 false")
    current: dict[str, Scalar] | None = Field(
        None,
        description="写后回读的 cDefine1、cDefine2：contract_no、customer_contract_no；空为 null",
    )
    ufts: Scalar = Field(None, description="订单行版本（写后）")
