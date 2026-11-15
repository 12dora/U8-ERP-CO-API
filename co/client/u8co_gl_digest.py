"""总账凭证摘要（事件源）：/v1/gl/vouchers/digest。混入 U8CoClient。

GL_accvouch 没有 rowversion。事件服务按期间读每张凭证的指纹（fingerprint），与上一轮比对发现新增、删除、审核、
出纳签字、记账、作废和修改。periods 省略时桥取该年度的未结账期间，再加最近 closed_periods 个已结账期间（缺省 1）；
响应的 periods 是实际扫描的期间，翻页时原样放进下一页的 periods，next 放进 after。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_gl_arc import _common, _int

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

GL_DIGEST_ROUTE = "/v1/gl/vouchers/digest"
GL_DIGEST_MAX = 500
_CURSOR = re.compile(r"^[0-9]{1,10}\.[0-9]{1,10}\.[0-9]{1,10}\Z")


@dataclass(frozen=True)
class GlDigestQuery:
    """fiscal_year 缺省取登录日期的年份；periods 与 closed_periods 不能同时给；limit 1 到 500（桥缺省 200）。"""

    fiscal_year: int | None = None
    periods: tuple[int, ...] | list[int] | None = None
    closed_periods: int | None = None
    after: str | None = None
    limit: int | None = None
    keys_only: bool = False


class U8CoGlDigestMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def gl_digest(self, call: U8Call, query: GlDigestQuery | None = None) -> dict[str, Any]:
        return self.call(GL_DIGEST_ROUTE, gl_digest_fields(call, query or GlDigestQuery()))


def gl_digest_fields(call: U8Call, query: GlDigestQuery) -> dict[str, Any]:
    fields = _common(call)
    if query.fiscal_year is not None:
        fields["fiscal_year"] = _int(query.fiscal_year, "fiscal_year", 1900, 9999)
    if query.periods is not None:
        fields["periods"] = _periods(query.periods)
    if query.closed_periods is not None:
        if query.periods is not None:
            raise ValueError("periods 与 closed_periods 不能同时给")
        fields["closed_periods"] = _int(query.closed_periods, "closed_periods", 0, 12)
    if query.after is not None:
        if type(query.after) is not str or _CURSOR.fullmatch(query.after) is None:
            raise ValueError("after 必须是上一页响应的 next")
        fields["after"] = query.after
    if query.limit is not None:
        fields["limit"] = _int(query.limit, "limit", 1, GL_DIGEST_MAX)
    if query.keys_only is True:
        fields["keys_only"] = True
    return fields


def _periods(value: object) -> list[int]:
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 12:
        raise ValueError("periods 必须是 1 到 12 个期间")
    periods = [_int(item, "periods", 1, 12) for item in value]
    if len(set(periods)) != len(periods):
        raise ValueError("periods 不能重复")
    return periods
