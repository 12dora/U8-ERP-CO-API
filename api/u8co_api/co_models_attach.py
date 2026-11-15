"""/v1/co/gl/vouchers/attachments/list、/v1/co/vouchers/attachments/list 的请求和响应（只读，只列附件清单）。

字段名与桥一致，响应放行桥多给的字段。文件内容不经本接口下载。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from u8co_api.co_models import _ID_MAX, CoAuth, VoucherType
from u8co_api.co_models_gl import PASS, Scalar


class VoucherAttachIn(CoAuth):
    type: VoucherType = Field(..., description="单据类型，同 vouchers/load")
    id: int = Field(..., gt=0, le=_ID_MAX, description="单据主键，1 到 2147483647，同 vouchers/load")

    def audit_ref(self) -> str:
        return f"{self.type}:{self.id}"


class GlAttachOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到凭证")
    voucher: dict[str, Any] | None = Field(
        None,
        description="period、sign、no，以及 attachments（凭证上的附单据数，只是张数，不是文件）",
    )
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按上传顺序：id（附件行号）、name（原文件名）、file_id（文件服务器上的文件标识）、"
        "submitted_at（yyyy-MM-dd HH:mm:ss）、source（来源模块）。没有电子附件时为空数组",
    )
    truncated: Scalar = Field(None, description="超过 500 个附件时为 true，只列前 500 个")


class VoucherAttachOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到单据")
    type: Scalar = Field(None, description="单据类型")
    id: Scalar = Field(None, description="单据主键")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按文件名排序：card（U8 卡片号）、file_id、name、memo、size（存在库里时的字节数，否则 null）、"
        "stored（database 内容存在 U8 库里，file_server 只登记了文件服务器上的文件标识）。没有附件时为空数组",
    )
    truncated: Scalar = Field(None, description="超过 500 个附件时为 true，只列前 500 个")
