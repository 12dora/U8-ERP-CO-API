"""销售订单表头自定义项写入 /v1/co/vouchers/so_defines：按请求写 SO_SOMain 表头的文本型自定义项 cdefine1–16，
可选连带下游单据表头的同名列。

走专用受控 SQL，不走单据修改（vouchers/update）：已审核、已有下游单据的订单也能写。只写不清空；
写哪些列、什么值、哪些值算空都由调用方给出。
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    StrictBool,
    StrictStr,
    model_serializer,
    model_validator,
)

from u8co_api.co_doctext import op_doc
from u8co_api.co_models import _ID_MAX, CoAuth
from u8co_api.co_models_arc_cus import check_text, has_control
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import PASS, Scalar
from u8co_api.errors import ApiError

# 请求值的上限。实际长度是自定义项档案（UserDef）的最大长度和列宽，由桥核对，超出 400；这里只挡明显离谱的输入。
_LIMIT = 120
_FIELDS_MAX = 16
_EMPTY_MAX = 8
_EMPTY_LEN = 20
_KEY = re.compile(r"cdefine([1-9]|1[0-6])\Z")

SO_DEFINES_HELP = op_doc(
    "销售订单表头自定义项写入：把销售订单表头（SO_SOMain）的文本型自定义项 cdefine1–16 设成请求值，"
    "已审核、已有下游单据的订单也能写；可选连带下游单据表头的同名列。",
    (
        "用法",
        (
            "type 目前只收 sale_order；id 是销售订单主键（SO_SOMain.ID）",
            "fields 是要写的列：1 到 16 项，键为 cdefine1 … cdefine16，不分大小写，统一转成小写，转换后不能重复；"
            "值非空，首尾不能有空白，不含控制字符，最长 120 个字符，且不能等于 empty_values 里的任何一项。"
            "本路由只写不清空",
            "expect 必填，键集合必须与 fields 完全相同，值是调用方认为的当前值（字符串或 null）；"
            "null、空串、纯空白和 empty_values 里的值都表示当前为空，彼此等价；其余去首尾空白后逐字比较（区分大小写）。"
            "API 把 null 按空串转给桥",
            "empty_values 可选（默认空列表，null 同空列表），最多 8 项，每项去首尾空白后 1 到 20 个字符："
            "除 NULL 与空白外另按空值处理的取值",
            "cascade 可选，默认 false；为 true 时同一事务里把请求的列写到下游单据表头",
            "dry_run 为 true 时跑完全部检查（含锁定、expect、长度、下游列）后不写库（validate）",
        ),
    ),
    (
        "规则",
        (
            "同一事务，订单表头行锁 UPDLOCK、ROWLOCK，按行版本（ufts）乐观锁，写后在同一事务里回读",
            "请求的每一列都必须是 SO_SOMain 的文本列（char、nchar、varchar、nvarchar），"
            "值的长度不能超过自定义项档案的最大长度和列宽",
            "请求的列都已等于请求值（去首尾空白后逐字相等）时 changed=false、不写订单；cascade 照常执行",
            "否则只写不同的列，并照常盖修改人、修改日期、修改时间",
            "不动：金额、数量、日期、部门、业务员、审核和关闭状态、表体、各 History 表",
            "写入分类 (销售订单 sale_order, so_defines)，规则键 write:voucher:sale_order:so_defines："
            "放行 vouchers/update 不连带放行本路由；只读账套上仅当只读例外 type=vouchers、op=so_defines "
            "且账套、操作员命中、请求体 type 为 sale_order 才穿过闸门，vouchers/update 等其它单据写入仍 403",
            "功能权限为销售订单的修改 / 变更（功能 id 以桥 PermRegistry 登记为准），"
            "数据权限按订单表头的客户、部门、业务员、销售类型（cSTCode）",
            "写入建议带 Idempotency-Key 头（dry_run 不能带）；同一个键重试原样返回第一次的结果，"
            "包括 504 outcome_unknown",
        ),
    ),
    (
        "连带下游（cascade）",
        (
            "下游单据：发货单 / 退货单（dispatch）、销售出库单（sale_out）、销售发票（sale_invoice）的表头",
            "一张单据的全部来源订单（本订单按写后的值）在请求的每一列上都等于请求值才改，"
            "否则跳过（reason=mixed_sources，有明细没有来源订单也算）；请求的列都已相等时 result=unchanged；"
            "被别的用户锁定时跳过（reason=locked）",
            "来源解析：链接值 0 当缺省；发票行的直接订单链接与经发货单行的订单各自解析，不同算冲突（混合来源）；"
            "两条都没有时（红冲蓝字）经蓝字行一跳；悬空、冲突、无链接的行都算没有来源",
            "下游单据缺少请求的列、该列不是文本列或列宽不够时 400 bad_request（消息带单号），什么都不写（预演同样）",
            "下游只改请求的列，按行版本乐观锁、写后回读；不写修改人、审核人，"
            "不碰表体、金额、数量、日期、状态、各 History 表和总账",
            "下游单据超过 200 张、单张明细超过 5000 行或来源订单超过 200 张时 409 too_many_downstream，什么都不写",
            "响应另有 cascade 列表：每项 {type, id, code, result, reason, before}，before 是请求各列改前的值；"
            "预演给同样的列表、不写库",
        ),
    ),
    (
        "错误",
        (
            "400 bad_request：列不存在或不是文本列、值超过自定义项档案的最大长度或列宽（field 为 fields.cdefineN）",
            "404 not_found：销售订单不存在",
            "409 state_mismatch，error.detail.reason 为 locked（detail.locker 是锁定人）：订单被别的用户锁定",
            "409 state_mismatch：expect 与当前值不符；error.detail.current 是请求各列（小写 cdefineN）的当前原值，"
            "空为 null；并发修改（行版本变了）没有 detail",
            "409 too_many_downstream：cascade 时下游超出上限",
            "504 outcome_unknown：写后回读不一致，先 vouchers/load 核对再决定是否重试",
        ),
    ),
)


def _target(value: str) -> str:
    """要写入的值：非空、首尾无空白、不含控制字符。"""
    if not value.strip():
        raise ValueError("值不能为空（本路由只写不清空）")
    return check_text(value, "值", _LIMIT)


def _current(value: str | None) -> str:
    # 当前值只核对，不要求写法合规（库里的值可以有首尾空白）；null 转成空串，免得被 API 的去空值逻辑丢掉。
    if value is None:
        return ""
    return check_text(value, "当前值", _LIMIT, trimmed=False)


Target = Annotated[StrictStr, AfterValidator(_target)]
Current = Annotated[StrictStr | None, AfterValidator(_current)]


def _bad(field: str, message: str) -> ApiError:
    return ApiError(400, "bad_request", message, field=field)


def _tokens(values: object) -> set[str]:
    """empty_values 的各项去首尾空白后 1 到 20 个字符、不含控制字符；返回去空白后的集合。非字符串留给类型校验。"""
    tokens: set[str] = set()
    if not isinstance(values, list):
        return tokens
    for index, value in enumerate(values):
        if not isinstance(value, str):
            continue
        item = value.strip()
        if not 1 <= len(item) <= _EMPTY_LEN:
            raise _bad(f"empty_values.{index}", f"empty_values.{index} 去首尾空白后须为 1 到 {_EMPTY_LEN} 个字符")
        if has_control(item):
            raise _bad(f"empty_values.{index}", f"empty_values.{index} 不能含控制字符")
        tokens.add(item)
    return tokens


def _normalise(values: dict[Any, Any], label: str) -> dict[str, Any]:
    """键转成小写并核对：只收 cdefine1 … cdefine16，转换后不能重复。出错的 field 是 <label>.<小写键>。"""
    out: dict[str, Any] = {}
    for key, value in values.items():
        low = str(key).lower()
        if _KEY.fullmatch(low) is None:
            raise _bad(f"{label}.{low}", f"{label}.{low} 不是允许的列；只收 cdefine1 到 cdefine16")
        if low in out:
            raise _bad(f"{label}.{low}", f"{label}.{low} 重复（键不分大小写）")
        out[low] = value
    return out


def _check_fields(fields: dict[str, Any], tokens: set[str]) -> None:
    for key, value in fields.items():
        if isinstance(value, str) and value.strip() in tokens:
            raise _bad(f"fields.{key}", f"fields.{key} 的值在 empty_values 里，本路由只写不清空")


def _check_expect(fields: dict[str, Any], expect: dict[str, Any]) -> None:
    for key in fields:
        if key not in expect:
            raise _bad(f"expect.{key}", f"expect.{key} 必须给出（当前为空用 null 或空串）")
    for key in expect:
        if key not in fields:
            raise _bad(f"expect.{key}", f"expect.{key} 不在 fields 里")


def _prepare(data: dict[str, Any]) -> dict[str, Any]:
    """先核对、规范化键（值的校验在其后，出错路径都用小写键）；empty_values 为 null 时按空列表。"""
    data = dict(data)
    if data.get("empty_values", ()) is None:
        data["empty_values"] = []
    tokens = _tokens(data.get("empty_values"))
    fields = data.get("fields")
    if isinstance(fields, dict):
        fields = data["fields"] = _normalise(fields, "fields")
        _check_fields(fields, tokens)
    expect = data.get("expect")
    if isinstance(expect, dict):
        expect = data["expect"] = _normalise(expect, "expect")
        if isinstance(fields, dict):
            _check_expect(fields, expect)
    return data


class VoucherSoDefinesIn(CoAuth):
    type: Literal["sale_order"] = Field(..., description="单据类型，目前只收 sale_order（销售订单）")
    id: int = Field(..., gt=0, le=_ID_MAX, description="销售订单主键（SO_SOMain.ID），1 到 2147483647")
    empty_values: list[StrictStr] = Field(
        default_factory=list,
        max_length=_EMPTY_MAX,
        description="可选（null 同空列表），除 NULL 与空白外另按空值处理的取值，最多 8 项，"
        "每项去首尾空白后 1 到 20 个字符",
        examples=[["-"]],
    )
    fields: dict[str, Target] = Field(
        min_length=1,
        max_length=_FIELDS_MAX,
        description="要写入的表头自定义项 cdefine1–16 → 值，1 到 16 项；键不分大小写；值非空、首尾无空白、"
        "不含控制字符、不等于 empty_values 的任何一项",
        examples=[{"cdefine1": "DEMO-001", "cdefine2": "文本A"}],
    )
    expect: dict[str, Current] = Field(
        description="fields 每一列的当前值，键集合与 fields 相同；null、空串、空白和 empty_values 的值都表示当前为空",
        examples=[{"cdefine1": None, "cdefine2": "文本B"}],
    )
    cascade: StrictBool = Field(
        False,
        description="为 true 时同一事务里把请求的列写到下游发货单 / 退货单、销售出库单、销售发票表头",
    )
    dry_run: DryRunFlag = False

    def audit_ref(self) -> str:
        return f"sale_order:{self.id}"

    @model_serializer(mode="wrap")
    def _drop_defaults(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # cascade 只在为 true 时、empty_values 只在非空时转给桥。
        data = handler(self)
        if not data.get("cascade"):
            data.pop("cascade", None)
        if not data.get("empty_values"):
            data.pop("empty_values", None)
        return data

    @model_validator(mode="before")
    @classmethod
    def _keys_first(cls, data: Any) -> Any:
        return _prepare(data) if isinstance(data, dict) else data


class VoucherSoDefinesOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    type: Scalar = Field(None, description="单据类型")
    id: Scalar = Field(None, description="销售订单主键")
    code: Scalar = Field(None, description="销售订单号")
    changed: bool | None = Field(None, description="订单表头是否改了；请求的列都已等于请求值时为 false")
    current: dict[str, Scalar] | None = Field(None, description="写后回读的请求各列（小写 cdefineN）；空为 null")
    cascade: list[dict[str, Any]] | None = Field(
        None,
        description="cascade=true 时的下游单据：type（dispatch / sale_out / sale_invoice）、id、code、"
        "result（updated / unchanged / skipped）、reason（mixed_sources / locked）、before（请求各列改前的值）",
    )
    ufts: Scalar = Field(None, description="订单行版本（写后）")
