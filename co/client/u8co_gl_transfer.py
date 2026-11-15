"""总账期间损益结转、自定义转账：/v1/gl/transfer/pnl、/v1/gl/transfer/custom。混入 U8CoClient。

按 U8 的转账定义（GL_bautotran）和已记账余额生成结转凭证，标记照 U8 自动转账（coutsign 期间损益 / 自定义转账）。
fiscal_year 必须是登录日期的年份；voucher_date 缺省取该期间最后一天。自定义转账的 tran_id 只生成一个转账序号。
第二级写入：桥 config.json 的 enableReplicatedWrites 缺省关闭（403 feature_disabled）；打开后
只对 testAccounts 里的账套开放，其余账套桥在登录前返回 403 test_account_only。
预演用 client.dry()（桥 validate 模式，停在凭证导入之前，分录在 detail.transfer）；exclude_existing 只能和预演一起用，
余额里去掉本期已生成的同类结转凭证，用来与 U8 已生成的凭证核对。幂等键用 idempotency_key 或 client.keyed(key)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_idem import with_key
from co.client.u8co_gl_arc import _common, _int, _ymd

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

GL_TRANSFER_PNL_ROUTE = "/v1/gl/transfer/pnl"
GL_TRANSFER_CUSTOM_ROUTE = "/v1/gl/transfer/custom"
_TRAN_ID = re.compile(r"^[0-9A-Za-z]{1,20}\Z")


@dataclass(frozen=True)
class GlTransferAsk:
    """fiscal_year、period 是要结转的期间；tran_id 只对自定义转账有效；exclude_existing 只在预演时给。"""

    fiscal_year: int
    period: int
    voucher_date: str | None = None
    tran_id: str | None = None
    exclude_existing: bool = False


class U8CoGlTransferMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def gl_transfer_pnl(self, call: U8Call, ask: GlTransferAsk, idempotency_key: str | None = None) -> dict[str, Any]:
        if ask.tran_id is not None:
            raise ValueError("期间损益结转不能带 tran_id")
        return self.call(GL_TRANSFER_PNL_ROUTE, with_key(gl_transfer_fields(call, ask), idempotency_key))

    def gl_transfer_custom(self, call: U8Call, ask: GlTransferAsk, idempotency_key: str | None = None) -> dict[str, Any]:
        return self.call(GL_TRANSFER_CUSTOM_ROUTE, with_key(gl_transfer_fields(call, ask), idempotency_key))


def gl_transfer_fields(call: U8Call, ask: GlTransferAsk) -> dict[str, Any]:
    fields = _common(call)
    fields["fiscal_year"] = _int(ask.fiscal_year, "fiscal_year", 1900, 9999)
    fields["period"] = _int(ask.period, "period", 1, 12)
    if ask.voucher_date is not None:
        date = _ymd(ask.voucher_date, "voucher_date")
        if not date.startswith(f"{ask.fiscal_year:04d}-{ask.period:02d}-"):
            raise ValueError("voucher_date 必须在 fiscal_year 年第 period 期内")
        fields["voucher_date"] = date
    if ask.tran_id is not None:
        if type(ask.tran_id) is not str or _TRAN_ID.fullmatch(ask.tran_id) is None:
            raise ValueError("tran_id 必须是 1 到 20 个字母或数字")
        fields["tran_id"] = ask.tran_id
    if ask.exclude_existing:
        fields["exclude_existing"] = True
    return fields
