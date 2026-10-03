"""总账红字冲销：/v1/gl/vouchers/reverse。混入 U8CoClient。

把一张已记账的总账手工凭证原样复制成红字凭证（金额、外币、数量、现金流量取负，同 U8「冲销凭证」）。原凭证用
period、sign、no 定位，fiscal_year 缺省取登录日期的年份（可以是上一年度）；voucher_date 是红字凭证日期，缺省取登录日期。
预演用 client.dry()（桥 validate 模式，停在凭证导入之前），幂等键用 idempotency_key 或 client.keyed(key)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_idem import with_key
from co.client.u8co_gl_arc import GlKey, _gl_keyed, _int, _ymd

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

GL_REVERSE_ROUTE = "/v1/gl/vouchers/reverse"


@dataclass(frozen=True)
class GlReverseAsk:
    """key 是原凭证（期间、类别字、凭证号）；fiscal_year 是原凭证的会计年度；voucher_date 是红字凭证日期。"""

    key: GlKey
    fiscal_year: int | None = None
    voucher_date: str | None = None


class U8CoGlReverseMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def gl_reverse(self, call: U8Call, ask: GlReverseAsk, idempotency_key: str | None = None) -> dict[str, Any]:
        return self.call(GL_REVERSE_ROUTE, with_key(gl_reverse_fields(call, ask), idempotency_key))


def gl_reverse_fields(call: U8Call, ask: GlReverseAsk) -> dict[str, Any]:
    fields = _gl_keyed(call, ask.key)
    if ask.fiscal_year is not None:
        fields["fiscal_year"] = _int(ask.fiscal_year, "fiscal_year", 1900, 9999)
    if ask.voucher_date is not None:
        fields["voucher_date"] = _ymd(ask.voucher_date, "voucher_date")
    return fields
