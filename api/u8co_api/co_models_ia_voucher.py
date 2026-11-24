"""存货核算制单 /v1/co/ia/voucher、取消制单 /v1/co/ia/voucher/delete 的请求和响应。

校验规则与桥的 IaVoucherReq 一致，响应放行桥多给的字段。
两条都是第二级写入：只对桥配置为测试账套（testAccounts）的账套开放。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from u8co_api.co_doctext import TEST_ONLY_GATE
from u8co_api.co_models import CoAuth
from u8co_api.co_models_dry import DryRunFlag

_OUT = ConfigDict(extra="allow")
_ID_MAX = 2147483647
BILL_MAX = 100
PER_BILL_MAX = 10
_TYPE = r"^[0-9]{2,4}$"

VOUCHER_SUMMARY = "存货核算制单"
VOUCHER_HELP = (
    "把已记账、未制单的出入库单据生成总账凭证（U8「存货核算 → 生成凭证」）。\n\n"
    "**用法**\n"
    "- 选单据：ids（[{type, id}]，type 是存货核算单据类型，id 是来源单据表头主键）"
    "或 month（该月全部可制单的单据，可加 types）\n"
    "- 支持的类型：01 采购入库、08 其他入库、09 其他出库、10 产成品入库、11 材料出库、20 入库调整、21 出库调整、"
    "24 红字回冲、30 蓝字回冲（id 为原采购入库单）、3201 销售出库（发出商品）\n"
    "- merge：per_bill（缺省）一张单据一张凭证，ids 最多 10 张；all 全部合成一张，最多 100 张\n"
    "- month 加 per_bill：一次做前 10 张，其余在 pending，再调用一次\n"
    "- sign 省略取账套最近一张存货核算凭证的类别，没有时「转」；voucher_date 省略取登录日期\n"
    "- digest 省略按 U8：单据备注，没有备注时单据类型名（如「材料出库单」）\n"
    "- dry_run：拼好分录、取到外部业务号后停在凭证导入之前（validate 模式），要生成的凭证在 detail.vouchers\n"
    "- month 选择时不合格的单据跳过，原因在 skipped；ids 选择时任一张不合格整笔 409\n\n"
    "**规则**\n"
    "- 存货科目取「存货科目」设置（按存货 → 存货分类 → 仓库）；"
    "对方科目取「对方科目」设置（按收发类别、存货、分类、部门、项目），"
    "暂估记账的采购入库和红字回冲取暂估科目，销售出库取发出商品科目\n"
    "- 入库借存货、贷对方，出库相反；金额取存货明细账的记账金额，红字为负数\n"
    "- 同方向、同科目、同辅助项的行合并，借方在前；辅助项只按科目要求填，值取单据\n"
    "- 凭证经 U8 凭证导入保存，制单系统 IA、外部业务类型「存货核算」、外部业务号 pz_id（年度 + IA + 13 位）\n"
    "- 回写存货明细账（凭证号、存货科目、对方科目、摘要）、发出商品明细、单据表头凭证号\n"
    "- 回写失败删除刚生成的凭证并 409\n\n"
    "**限制**\n" + TEST_ONLY_GATE
    + "- 调拨、形态转换、组装、拆卸、计划价 / 售价差异、外币科目、要求凭证自定义项的科目暂不支持（409）\n\n"
    "**权限**\n"
    "- 功能权限：生成凭证 IASCPZ\n\n"
    "**错误**\n"
    "- 409 state_mismatch：该单据类型暂不支持制单、已制单、未记账、科目没有设置或多行同样匹配\n"
    "- 409 state_mismatch：辅助项缺值、存货核算或总账已结账\n"
    "- 409：总账里已有本单没有回写的存货核算凭证（上次结果不明时留下），先核对后用 ia/voucher/delete 删除\n"
    "- 409 u8_rejected：U8 拒绝保存凭证（原文带回）\n"
    "- 504 outcome_unknown：凭证已保存但回写或回读结果不明\n"
    "- 逐张制单第 2 张起失败：状态码同失败原因，error.detail 带 done（已完成的凭证）和 pending（只重发这些单据）\n\n"
    "**相关**\n"
    "- 取消制单：ia/voucher/delete"
)
DELETE_SUMMARY = "取消存货核算制单"
DELETE_HELP = (
    "按外部业务号 pz_id 删除存货核算生成的凭证，并清掉存货明细账、发出商品明细、单据表头上的凭证号"
    "（U8「凭证列表」删除）。\n\n"
    "**用法**\n"
    "- pz_id 见 ia/voucher 的响应（凭证的 coutno_id）\n"
    "- 恢复记账后留下的、没有回写的存货核算凭证也能删\n"
    "- dry_run：事务里执行后回滚（rollback 模式）\n\n"
    "**限制**\n" + TEST_ONLY_GATE + "- 只收桥支持制单的单据类型生成的凭证\n"
    "- 引用了采购结算、应付、发票的凭证在 U8 客户端删除\n\n"
    "**权限**\n"
    "- 功能权限：凭证查询 IACXPZ（U8 在凭证查询里删除凭证）\n\n"
    "**错误**\n"
    "- 404 not_found：没有这张凭证\n"
    "- 409 state_mismatch：已记账、已审核、已出纳签字、所在期间已结账、存货核算已结账\n"
    "- 409 state_mismatch：不是存货核算生成的、正被编辑、被红字冲销"
)


class IaVoucherRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: StrictStr = Field(..., pattern=_TYPE, description="存货核算单据类型（cVouType），如 01、09、3201")
    id: StrictInt = Field(
        ..., ge=1, le=_ID_MAX, description="来源单据表头主键：出入库单 ID；24 / 30 为原采购入库单；20 / 21 为调整单"
    )


class CoIaVoucherIn(CoAuth):
    ids: list[IaVoucherRef] | None = Field(
        None, min_length=1, max_length=BILL_MAX, description="按单据选：[{type, id}]；与 month 二选一"
    )
    month: StrictInt | None = Field(
        None, ge=1, le=12, description="按月份选：登录年度该月已记账、未制单的单据；与 ids 二选一"
    )
    types: list[StrictStr] | None = Field(
        None, min_length=1, max_length=20, description="只和 month 一起用：只要这些单据类型；省略为全部支持的类型"
    )
    merge: Literal["per_bill", "all"] | None = Field(
        None, description="per_bill（缺省）：一张单据一张凭证，最多 10 张；all：全部合成一张凭证，最多 100 张"
    )
    sign: StrictStr | None = Field(
        None, min_length=1, max_length=2, description="凭证类别；省略取最近一张存货核算凭证的类别，没有时「转」"
    )
    digest: StrictStr | None = Field(
        None, min_length=1, max_length=120, description="摘要，每行相同；省略取单据备注或单据类型名"
    )
    voucher_date: StrictStr | None = Field(
        None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="制单日期；省略取登录日期（请求的 date），须在登录年度内"
    )
    dry_run: DryRunFlag = False

    @model_validator(mode="after")
    def _shape(self) -> CoIaVoucherIn:
        if (self.ids is None) == (self.month is None):
            raise ValueError("ids（按单据）与 month（按月份）必须给且只能给一个")
        if self.ids is not None:
            if self.types is not None:
                raise ValueError("types 只能和 month 一起用")
            if len({(ref.type, ref.id) for ref in self.ids}) != len(self.ids):
                raise ValueError("ids 有重复的单据")
            if self.merge != "all" and len(self.ids) > PER_BILL_MAX:
                raise ValueError(f"逐张制单（merge=per_bill）一次最多 {PER_BILL_MAX} 张单据；更多请分批，或 merge=all")
        if self.types is not None and any(not t.isdigit() or not 2 <= len(t) <= 4 for t in self.types):
            raise ValueError("types 的每一项必须是存货核算单据类型编码（2 到 4 位数字）")
        if self.digest is not None and not self.digest.strip():
            raise ValueError("digest 不能为空白")
        return self

    def audit_ref(self) -> str:
        if self.month is not None:
            return f"month:{self.month}"
        return "ids:" + ",".join(f"{ref.type}-{ref.id}" for ref in self.ids or [])


class IaVoucherLineOut(BaseModel):
    model_config = _OUT
    entry: int = Field(description="分录号")
    account: str = Field(description="科目编码")
    digest: str = Field(description="摘要")
    debit: float = Field(description="借方金额")
    credit: float = Field(description="贷方金额")
    dept: str | None = Field(None, description="部门")
    person: str | None = Field(None, description="个人")
    customer: str | None = Field(None, description="客户")
    supplier: str | None = Field(None, description="供应商")
    item_class: str | None = Field(None, description="项目大类")
    item: str | None = Field(None, description="项目")
    bill_code: str | None = Field(None, description="该行的来源单号（凭证行 coutid，合并的行取最大的单号）")


class IaVoucherBillOut(BaseModel):
    model_config = _OUT
    type: str = Field(description="存货核算单据类型")
    id: int = Field(description="来源单据表头主键")
    code: str | None = Field(None, description="单据号")
    rows: int | None = Field(None, description="回写的存货明细账行数")


class IaVoucherOneOut(BaseModel):
    model_config = _OUT
    pz_id: str = Field(description="外部业务号（凭证 coutno_id = 存货明细账 cPZID），取消制单要用")
    making_system: str | None = Field(
        None, description="导入凭证时用的制单系统（IA，被拒时改用的 GL）；凭证上最终都写成 IA"
    )
    year: int = Field(description="会计年度")
    period: int = Field(description="会计期间")
    sign: str = Field(description="凭证类别")
    no: int = Field(description="凭证号")
    num: str | None = Field(None, description="凭证号文本，如 转-0172")
    date: str | None = Field(None, description="制单日期")
    bills: list[IaVoucherBillOut] = Field(description="本凭证覆盖的单据")
    lines: list[IaVoucherLineOut] = Field(description="凭证分录（回读）")


class CoIaVoucherOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="是否已制单")
    acc: str | None = Field(None, description="账套号")
    merge: str | None = Field(None, description="per_bill 或 all")
    voucher_date: str | None = Field(None, description="制单日期")
    sign: str | None = Field(None, description="凭证类别")
    count: int | None = Field(None, description="生成的凭证张数")
    vouchers: list[IaVoucherOneOut] | None = Field(None, description="生成的凭证")
    skipped: list[dict[str, Any]] | None = Field(None, description="按月份选时跳过的单据：[{type, id, code, reason}]")
    pending: list[dict[str, Any]] | None = Field(
        None, description="按月份逐张制单超过一次 10 张时留到下次的单据：[{type, id}]，再调用一次（或按 ids）处理"
    )


class CoIaVoucherDeleteIn(CoAuth):
    pz_id: StrictStr = Field(
        ..., pattern=r"^[0-9]{4}IA[0-9]{13}$", description="存货核算凭证的外部业务号：4 位年度、IA、13 位数字"
    )
    dry_run: DryRunFlag = False

    def audit_ref(self) -> str:
        return self.pz_id


class IaVoucherKeyOut(BaseModel):
    model_config = _OUT
    year: int = Field(description="会计年度")
    period: int = Field(description="会计期间")
    sign: str = Field(description="凭证类别")
    no: int = Field(description="凭证号")


class CoIaVoucherDeleteOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="是否已取消")
    acc: str | None = Field(None, description="账套号")
    pz_id: str | None = Field(None, description="已取消的外部业务号")
    deleted: bool | None = Field(None, description="凭证已删除")
    voucher: IaVoucherKeyOut | None = Field(None, description="删除的凭证")
