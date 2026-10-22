"""历史单据表头修正 /v1/co/vouchers/head_fix：只改已审核发货单的收货地址编码和业务员，并同步下游销售出库单、发票。

走专用受控 SQL，不经单据修改（vouchers/update）：已审核且已有下游单据的发货单，U8 客户端不让改这两项。
金额、数量、日期、部门、总账、单据状态一律不动。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt, StrictStr, ValidationInfo, field_validator, model_validator

from u8co_api.co_doctext import op_doc
from u8co_api.co_models import _ID_MAX, CoAuth
from u8co_api.co_models_arc_cus import check_text
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import PASS, Scalar

HEAD_FIX_KEYS = ("addr_code", "person_code")
# 写入值的最长字符数（DispatchList.caddcode nvarchar(30)、cPersonCode nvarchar(20)）。
_LIMIT = {"addr_code": 30, "person_code": 20}
# undo：字段 → 允许列出的下游表（Ar_Detail 只随业务员改）；每个列表最多 500 个主键。
_UNDO_TABLES = {
    "addr_code": ("rdrecord32", "SaleBillVouch"),
    "person_code": ("rdrecord32", "SaleBillVouch", "Ar_Detail"),
}
_UNDO_MAX = 500
UndoRows = dict[str, dict[str, list[StrictInt]]]

HEAD_FIX_HELP = op_doc(
    "修正历史发货单表头的收货地址编码、业务员，并按规则同步下游销售出库单、销售发票（含应收明细的业务员）。",
    (
        "用法",
        (
            "type 目前只收 dispatch（发货单，红蓝字都可）；id 是发货单主键",
            "fields 至少一项，键只收 addr_code、person_code；addr_code 最长 30，写空串表示清空（回滚已补填的编码）；"
            "person_code 1 到 20 个字符，不能为空；值首尾不能有空白，不含控制字符",
            "expect 必填：fields 里每一项都要给出表头当前值，空串表示当前为空",
            "回滚：把 fields 与 expect 对调，并把正向响应里的 undo 原样（不能增删改）放进请求的 undo，再调一次；"
            "只恢复 undo 列出的下游行，不再做 shared / differs 判断",
            'undo 可选，形如 {"addr_code": {"rdrecord32": [主键], "SaleBillVouch": [主键]}, '
            '"person_code": {…, "Ar_Detail": [Auto_ID]}}；键必须是 fields 的子集，Ar_Detail 只能在 person_code 下，'
            "主键是 1 到 2147483647 的整数，每个列表最多 500 个且不重复，不认识的键 400",
            "dry_run 为 true 时在事务里跑完全部检查后回滚，不写库（validate）",
        ),
    ),
    (
        "规则",
        (
            "只写发货单表头的 caddcode、cPersonCode；不改送货地址文本、修改人、日期、审核人，也不改部门",
            "下游销售出库单：只含这张发货单的行、且当前值等于发货单的旧值才更新；含其它来源的行跳过（reason=shared），"
            "当前值不同跳过（reason=differs）",
            "下游销售发票：没有其它来源的行、当前值等于旧值、且其它来源发货单都已是新值才更新，"
            "否则跳过（shared / differs）；"
            "改业务员时同步应收明细里该发票各行的业务员（旧值才改）；收款、核销行不动",
            "空串与 NULL 在下游比较中视为相同；清空写 NULL；比较和写入前对值做首尾空白、大小写的规范化",
            "下游行已是新值时不更新也不报错，记 skipped、reason=already_new",
            "带 undo 时：每个列出的行当前必须等于本次请求 expect 里该字段的值（空串与 NULL 视为相同，不分大小写），"
            "且属于这张发货单的下游（出库单、发票按发货单行关联，Ar_Detail 属于这张发货单关联的发票），否则整笔失败",
            "响应 undo 记录实际写了哪些下游行（字段 → 表 → 主键），没有下游被改时为空；",
            "回滚响应的 undo 是再次回滚用的反向记录",
            "改业务员时，发货单表头当前业务员为空则拒绝（409 person_empty），因为之后无法回滚",
            "不动销售订单、历史表、总账凭证、应收汇总、应收应付结算单据",
            "全部请求值已等于当前值时成功返回 changed=false，不写库；changed 表示发货单表头是否改了",
            "响应 downstream 逐条列出下游单据的处理结果（updated / skipped 及原因），可当作留底",
            "单据锁与 vouchers/update 修改发货单相同；写后回读，不一致返回 504",
            "功能权限「发货单变更」SA03020113；数据权限按发货单表头的客户、部门、原业务员、销售类型（cSTCode），新业务员另按人员权限",
            "写入分类 (发货单 dispatch, head_fix)：放行 vouchers/update 不连带放行本路由；"
            "只读账套上仅当只读例外 type=vouchers、op=head_fix 且账套、操作员命中才穿过闸门，vouchers/update 等仍 403",
        ),
    ),
    (
        "错误",
        (
            "404 not_found：发货单不存在",
            "409 not_audited：发货单未审核（先于其它检查；本接口只修正已审核发货单，未审核的直接用 vouchers/update 改）",
            "409 state_mismatch：expect 与表头当前值不符（消息列出字段、期望值、实际值），或并发修改；"
            "带 undo 时，列出的下游行当前值与 expect 不符（消息写表名和主键）",
            "409 person_empty：要改业务员，但发货单表头当前业务员为空",
            "409 too_many：关联的下游单据过多，接口不会只处理一部分",
            "409 undo_foreign：undo 里的某个 id 不存在或不属于这张发货单",
            "409 not_reversible：原值通不过回滚时的校验（原地址已不存在，原业务员已失效、换了部门或无权限），改了就撤不回，拒绝",
            "409 address_not_found：addr_code 不在发货单客户的收货地址里（清空不检查）",
            "409 person_invalid：业务员不存在，或在发货单日期无效",
            "409 person_department_mismatch：业务员所属部门与发货单部门不同",
            "504 outcome_unknown：写后回读不一致，先 load 核对再决定是否重试",
        ),
    ),
)


def _check_fields(fields: dict[str, str]) -> None:
    if not fields:
        raise ValueError("fields 至少要有一项")
    unknown = sorted(set(fields) - set(HEAD_FIX_KEYS))
    if unknown:
        raise ValueError(f"fields 有不认识的键: {', '.join(unknown)}；可用 {'、'.join(HEAD_FIX_KEYS)}")
    for key, value in fields.items():
        # addr_code 允许空串（清空）；person_code 不能为空。
        if value == "" and key == "person_code":
            raise ValueError("fields.person_code 不能为空")
        check_text(value, f"fields.{key}", _LIMIT[key])


def _check_expect(fields: dict[str, str], expect: dict[str, str]) -> None:
    missing = [key for key in fields if key not in expect]
    if missing:
        raise ValueError(f"expect 必须给出 {'、'.join(missing)} 的当前值；空串表示当前为空")
    extra = sorted(set(expect) - set(fields))
    if extra:
        raise ValueError(f"expect 有不在 fields 里的键: {', '.join(extra)}")
    for key, value in expect.items():
        # 当前值只核对，不要求写法合规（旧数据可以有首尾空白）。
        check_text(value, f"expect.{key}", _LIMIT[key], trimmed=False)


def _check_undo(fields: dict[str, str], undo: UndoRows) -> None:
    extra = sorted(set(undo) - set(fields))
    unknown = [key for key in extra if key not in _UNDO_TABLES]
    if unknown:
        raise ValueError(f"undo 有不认识的键: {', '.join(unknown)}；可用 {'、'.join(HEAD_FIX_KEYS)}")
    if extra:
        raise ValueError(f"undo 有不在 fields 里的键: {', '.join(extra)}")
    for key, tables in undo.items():
        bad = sorted(set(tables) - set(_UNDO_TABLES[key]))
        if bad:
            raise ValueError(f"undo.{key} 有不认识的表: {', '.join(bad)}；可用 {'、'.join(_UNDO_TABLES[key])}")
        for table, ids in tables.items():
            _check_ids(f"undo.{key}.{table}", ids)


def _check_ids(where: str, ids: list[int]) -> None:
    if len(ids) > _UNDO_MAX:
        raise ValueError(f"{where} 最多 {_UNDO_MAX} 个主键")
    if any(not 0 < item <= _ID_MAX for item in ids):
        raise ValueError(f"{where} 的主键必须是 1 到 {_ID_MAX} 的整数")
    if len(set(ids)) != len(ids):
        raise ValueError(f"{where} 有重复的主键")


class VoucherHeadFixIn(CoAuth):
    type: Literal["dispatch"] = Field(..., description="单据类型，目前只收 dispatch（发货单，红蓝字都可）")
    id: int = Field(..., gt=0, le=_ID_MAX, description="发货单主键（DispatchList.DLID），1 到 2147483647")
    fields: dict[str, StrictStr] = Field(
        description="要改的表头列：addr_code 收货地址编码（空串清空）、person_code 业务员编码（不能为空）",
        examples=[{"addr_code": "02", "person_code": "P900002"}],
    )
    expect: dict[str, StrictStr] = Field(
        description="fields 里每一项的表头当前值；空串表示当前为空",
        examples=[{"addr_code": "01", "person_code": "P900001"}],
    )
    undo: UndoRows | None = Field(
        None,
        description=(
            "回滚时放入正向响应的 undo：字段 → 表（rdrecord32、SaleBillVouch，业务员另有 Ar_Detail）→ 主键列表。"
            "给了就只恢复列出的下游行；键须是 fields 的子集，每个列表最多 500 个且不重复"
        ),
        examples=[{"addr_code": {"rdrecord32": [900000002], "SaleBillVouch": [900000003]}}],
    )
    dry_run: DryRunFlag = False

    def audit_ref(self) -> str:
        return f"dispatch:{self.id}"

    # fields 单独校验，出错时响应能指出字段 fields（模型级校验只报「请求参数无效」）。
    @field_validator("fields")
    @classmethod
    def _fields_ok(cls, value: dict[str, str]) -> dict[str, str]:
        _check_fields(value)
        return value

    # undo 的键要对照 fields；fields 自己出错时不再重复报。
    @field_validator("undo")
    @classmethod
    def _undo_ok(cls, value: UndoRows | None, info: ValidationInfo) -> UndoRows | None:
        fields = info.data.get("fields")
        if value is not None and fields is not None:
            _check_undo(fields, value)
        return value

    @model_validator(mode="after")
    def _check(self) -> VoucherHeadFixIn:
        _check_expect(self.fields, self.expect)
        return self


class VoucherHeadFixOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    type: Scalar = Field(None, description="单据类型")
    id: Scalar = Field(None, description="发货单主键")
    code: Scalar = Field(None, description="发货单号")
    changed: bool | None = Field(None, description="发货单表头是否改了；请求值都已等于当前值时为 false")
    before: dict[str, Scalar] | None = Field(None, description="修改前表头的 addr_code、person_code（空为空串）")
    after: dict[str, Scalar] | None = Field(None, description="修改后（写后回读）表头的 addr_code、person_code")
    downstream: list[dict[str, Any]] | None = Field(
        None,
        description=(
            "下游单据的处理结果：table、id、code、action（updated / skipped）、reason、value；"
            "应收明细为 table=Ar_Detail、invoice、rows"
        ),
    )
    undo: dict[str, Any] | None = Field(
        None,
        description=(
            "实际写了哪些下游行，形状同请求的 undo（字段 → 表 → 主键，Ar_Detail 是 Auto_ID）；没有下游被改时为空。"
            "回滚时原样放进请求"
        ),
    )
