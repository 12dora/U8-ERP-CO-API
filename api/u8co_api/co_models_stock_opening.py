"""期初结存单（stock_opening，库存期初，U8 单据类型 34）在 API 层的规则：不能修改；新增响应里每行一张单据（docs）。
新增走 U8 官方 EAI 导入，规则、闸门都在桥里，这里只做与桥同文的拒绝和响应形状。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from u8co_api.errors import ApiError

# 不能修改的类型 → 说明（与桥 StockOpening.NoUpdateText 一致）。
NO_UPDATE = {"stock_opening": "期初结存单不能修改，请删除后重新录入"}


def refuse_no_update(data: object) -> object:
    """修改请求的类型不能修改时直接 400，给出与桥相同的说明，而不是笼统的「请求参数无效：type」。"""
    kind = data.get("type") if isinstance(data, dict) else None
    if isinstance(kind, str) and kind in NO_UPDATE:
        raise ApiError(400, "bad_request", NO_UPDATE[kind], field="type")
    return data


class CoOpeningDoc(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int = Field(description="新单据主键")
    code: str = Field(description="新单据编号（U8 自编）")
    line: int = Field(description="对应请求 lines 的下标，从 0 开始")
    wh: str = Field(description="仓库编码")
    inv: str = Field(description="存货编码")
    qty: float = Field(description="数量")
