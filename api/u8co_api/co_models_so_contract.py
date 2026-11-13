"""销售订单合同号回写 /v1/co/vouchers/so_contract：只改 SO_SOMain 的 cDefine1（合同号），
可选同时改 cDefine2（客户合同号）。

走专用受控 SQL，不走单据修改（vouchers/update）：已审核、已有下游单据的销售订单，U8 客户端和 vouchers/update 都不让改。
金额、数量、日期、部门、业务员、审核人、关闭状态和下游单据（发货单、销售发票、各 History 表）一律不动。

mode="pair"（成对设置）：cDefine1 合同号、cDefine2 内部合同号一起设成请求值，已有别的值也覆盖（expect 防并发）；
cascade=true 时同一事务里把这一对写到下游发货单 / 退货单、销售出库单、销售发票的表头
（只改这两列，来源订单不一致的跳过）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    StrictBool,
    ValidationInfo,
    field_validator,
    model_serializer,
    model_validator,
)

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
_PAIR = "pair"

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
            "replace 可选，默认 false；为 true 时合同号当前已有别的值也覆盖为 contract_no（合同改号），"
            "expect.contract_no 仍须等于当前值",
            "dry_run 为 true 时跑完全部检查（含锁定、expect、长度）后不写库（validate）",
            "mode 为 pair 时成对设置：contract_no 写 cDefine1（合同号），"
            "internal_contract_no 写 cDefine2（内部合同号），两者必填；"
            "expect 改为 {contract_no, internal_contract_no}；不收 customer_contract_no、replace；"
            "cascade 可选（默认 false），为 true 时连带更新下游单据表头",
        ),
    ),
    (
        "成对设置",
        (
            "两列都设成请求值，已有别的值也覆盖；expect 仍须与当前值相符（空值等价、去首尾空白、不分大小写）",
            "两列都已是请求值（去首尾空白后逐字相等）时 changed=false、不写订单；cascade 照常执行",
            "cascade：同一事务里改下游发货单 / 退货单（dispatch）、销售出库单（sale_out）、"
            "销售发票（sale_invoice）表头的这两列；"
            "一张单据的全部来源订单（写后）都是这一对才改，否则跳过（reason=mixed_sources，有明细没有来源订单也算）；"
            "已是这一对 result=unchanged；别的操作员锁定 reason=locked",
            "来源解析：链接值 0 当缺省；发票行的直接 iSOsID 与经发货单行的订单各自解析，不同算冲突（混合来源）；"
            "两条都没有时（红冲蓝字）经 iSBVID 一跳到蓝字行；悬空、冲突、无链接的行都算没有来源",
            "要改的下游单据列宽不够时 400 bad_request（field 为 contract_no 或 internal_contract_no，消息带单号），什么都不写",
            "下游只改 cDefine1、cDefine2：不写修改人、审核人，不碰表体、金额、数量、日期、状态、各 History 表和总账；"
            "修改人 / 修改日期只盖在订单上",
            "响应另有 cascade 列表：每项 {type, id, code, result, reason, before}，before 是改前的 "
            "{contract_no, internal_contract_no}；预演给同样的列表、不写库",
            "下游单据超过 200 张 409 too_many_downstream，什么都不写",
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
            "合同号当前已有别的值：replace 为 true 时覆盖；否则只有 customer_contract_no 等于当前合同号"
            "（把它原样挪进客户合同号）时才写，不然 409 state_mismatch",
            "给了 customer_contract_no 而 cDefine2 当前非空，409 state_mismatch（不覆盖已有的客户合同号，replace 也不例外）",
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
            "409 state_mismatch：expect 与当前值不符，合同号已有别的值而没带 replace，或给了 customer_contract_no 而 cDefine2 非空；"
            "error.detail.current 是 {contract_no, customer_contract_no} 的当前原值（空为 null 或空串），"
            "并发修改（行版本变了）没有 detail",
            "409 too_many_downstream：cascade 时下游单据超过 200 张",
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
    """调用方认为的当前值。两个键都必须给：contract_no 加 customer_contract_no（mode=pair 时加 internal_contract_no）；
    null 与空串等价，都表示当前为空。"""

    model_config = ConfigDict(extra="forbid")
    contract_no: str | None = Field(
        ...,
        max_length=_LIMIT,
        description="cDefine1（合同号）当前原值；NULL、空串、空白、「无」都当作空，传 null 或空串",
        examples=["HT202611001"],
    )
    customer_contract_no: str | None = Field(
        None,
        max_length=_LIMIT,
        description="cDefine2 当前原值（不带 mode 时必填）；规则同 contract_no",
    )
    internal_contract_no: str | None = Field(
        None,
        max_length=_LIMIT,
        description="cDefine2（内部合同号）当前原值（mode=pair 时必填，代替 customer_contract_no）；规则同 contract_no",
    )

    @model_serializer(mode="wrap")
    def _given_only(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # 只转给桥请求里给了的键（两种模式的 expect 键不同）。
        data = handler(self)
        return {key: value for key, value in data.items() if key in self.model_fields_set}

    @field_validator("contract_no", "customer_contract_no", "internal_contract_no", mode="after")
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
    replace: StrictBool = Field(
        False,
        description="为 true 时 cDefine1 已有别的值也覆盖为 contract_no（合同改号）；expect.contract_no 仍须等于当前值，"
        "客户合同号规则不变",
    )
    mode: Literal["pair"] | None = Field(
        None,
        description="省略为原有行为；pair 为成对设置：cDefine1 合同号、cDefine2 内部合同号一起设成请求值（允许覆盖）",
    )
    internal_contract_no: str | None = Field(
        None,
        max_length=_LIMIT,
        description="mode=pair 时必填，要写入 cDefine2（内部合同号）的值，规则同 contract_no；不带 mode 时不收",
        examples=["HT202611001"],
    )
    cascade: StrictBool = Field(
        False,
        description="只在 mode=pair 时可用：为 true 时同一事务里把这一对写到下游发货单 / 退货单、"
        "销售出库单、销售发票表头",
    )
    dry_run: DryRunFlag = False

    def audit_ref(self) -> str:
        return f"sale_order:{self.id}"

    @model_serializer(mode="wrap")
    def _drop_default_flags(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # replace、cascade 只在为 true 时转给桥：不带它们的请求体与以前完全相同（旧桥也认）。
        data = handler(self)
        for key in ("replace", "cascade"):
            if not data.get(key):
                data.pop(key, None)
        return data

    @model_validator(mode="after")
    def _mode_keys(self) -> VoucherSoContractIn:
        given = self.model_fields_set
        if "mode" in given and self.mode is None:
            raise ValueError("mode 只能是 pair（省略即原有行为）")
        if self.mode == _PAIR:
            _refuse(given, ("customer_contract_no", "replace"), "mode=pair 不收")
            if self.internal_contract_no is None:
                raise ValueError("mode=pair 时 internal_contract_no 必填")
            _expect_keys(self.expect, "internal_contract_no", "customer_contract_no")
        else:
            _refuse(given, ("internal_contract_no", "cascade"), "只在 mode=pair 时可用")
            _expect_keys(self.expect, "customer_contract_no", "internal_contract_no")
        return self

    @field_validator("contract_no")
    @classmethod
    def _contract_no(cls, value: str) -> str:
        return _value(value, "contract_no")

    @field_validator("customer_contract_no", "internal_contract_no")
    @classmethod
    def _cdefine2(cls, value: str | None, info: ValidationInfo) -> str | None:
        return None if value is None else _value(value, str(info.field_name))


def _refuse(given: set[str], keys: tuple[str, ...], why: str) -> None:
    for key in keys:
        if key in given:
            raise ValueError(f"{key} {why}")


def _expect_keys(expect: SoContractExpect, need: str, other: str) -> None:
    if need not in expect.model_fields_set:
        raise ValueError(f"expect 必须带 {need}（当前为空用 null 或空串）")
    if other in expect.model_fields_set:
        raise ValueError(f"expect 不收 {other}")


class VoucherSoContractOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    type: Scalar = Field(None, description="单据类型")
    id: Scalar = Field(None, description="销售订单主键")
    code: Scalar = Field(None, description="销售订单号")
    changed: bool | None = Field(None, description="订单表头是否改了；请求值都已等于当前值时为 false")
    current: dict[str, Scalar] | None = Field(
        None,
        description="写后回读的 cDefine1、cDefine2：contract_no、customer_contract_no"
        "（mode=pair 时为 internal_contract_no）；空为 null",
    )
    cascade: list[dict[str, Any]] | None = Field(
        None,
        description="mode=pair 且 cascade=true 时的下游单据：type（dispatch / sale_out / sale_invoice）、id、code、"
        "result（updated / unchanged / skipped）、reason（mixed_sources / locked）、"
        "before（改前的 contract_no、internal_contract_no）",
    )
    ufts: Scalar = Field(None, description="订单行版本（写后）")
