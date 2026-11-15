"""总账取消记账：/v1/gl/vouchers/unpost。混入 U8CoClient。

撤销本年度最近一次记账（同 U8「恢复记账前状态 → 最近一次记账」），范围由 U8 记录的最近一次记账决定，不能挑选凭证。
period、vouchers 只用来核对：给了就必须与这次记账的范围完全一致，否则桥 409 不写入；给 vouchers 时必须给 period。
第二级写入：桥 config.json 的 enableReplicatedWrites 缺省关闭（403 feature_disabled）；打开后
只对 testAccounts 里的账套开放，其余账套桥在登录前返回 403 test_account_only。
预演用 client.dry()（桥 rollback 模式），幂等键用 client.keyed(key)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_gl_arc import _common, _int, _text

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

GL_UNPOST_ROUTE = "/v1/gl/vouchers/unpost"
GL_UNPOST_MAX = 500


@dataclass(frozen=True)
class GlUnpostCheck:
    """核对字段。vouchers 是 (类别字, 凭证号)；fiscal_year 缺省取登录日期的年份。"""

    fiscal_year: int | None = None
    period: int | None = None
    vouchers: tuple[tuple[str, int], ...] = ()


class U8CoGlUnpostMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def gl_unpost(self, call: U8Call, check: GlUnpostCheck | None = None) -> dict[str, Any]:
        return self.call(GL_UNPOST_ROUTE, gl_unpost_fields(call, check or GlUnpostCheck()))


def gl_unpost_fields(call: U8Call, check: GlUnpostCheck) -> dict[str, Any]:
    fields = _common(call)
    if check.fiscal_year is not None:
        fields["fiscal_year"] = _int(check.fiscal_year, "fiscal_year", 1900, 9999)
    if check.period is not None:
        fields["period"] = _int(check.period, "period", 1, 12)
    if check.vouchers:
        if check.period is None:
            raise ValueError("给 vouchers 时必须给 period")
        fields["vouchers"] = _vouchers(check.vouchers)
    return fields


def _vouchers(items: tuple[tuple[str, int], ...]) -> list[dict[str, Any]]:
    if len(items) > GL_UNPOST_MAX:
        raise ValueError(f"vouchers 最多 {GL_UNPOST_MAX} 张")
    out = [{"sign": _text(sign, "sign", 2), "no": _int(no, "no", 1, 32767)} for sign, no in items]
    if len({(item["sign"], item["no"]) for item in out}) != len(out):
        raise ValueError("vouchers 不能重复")
    return out
