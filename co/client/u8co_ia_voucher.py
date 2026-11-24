"""存货核算制单、取消制单：/v1/ia/voucher、/v1/ia/voucher/delete。混入 U8CoClient。

把已记账、未制单的出入库单据生成总账凭证（U8「存货核算 → 生成凭证」）：按单据选（ids：[(type, id)]，type 是存货核算
单据类型如 01、09、32、3201，id 是来源单据表头主键）或按月份选（month，可加 types）。merge 为 per_bill（缺省，一张单据一张凭证，
ids 最多 10 张；按月份时一次做前 10 张，其余在响应的 pending）或 all（全部合成一张，最多 100 张）。取消制单按外部业务号 pz_id（4 位年度 + IA + 13 位数字）。
第二级写入：桥 config.json 的 enableReplicatedWrites 缺省关闭（403 feature_disabled）；打开后
只对 testAccounts 里的账套开放，其余账套桥在登录前返回 403 test_account_only。
预演用 client.dry()（制单是桥 validate 模式，停在凭证导入之前；取消制单 rollback），幂等键用 idempotency_key 或 client.keyed(key)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_gl_arc import _common, _int, _ymd
from co.client.u8co_idem import with_key

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

IA_VOUCHER_ROUTE = "/v1/ia/voucher"
IA_VOUCHER_DELETE_ROUTE = "/v1/ia/voucher/delete"
IA_VOUCHER_MERGES = ("per_bill", "all")
BILL_MAX = 100
PER_BILL_MAX = 10
_INT32_MAX = 2147483647
_TYPE = re.compile(r"^[0-9]{2,4}\Z")
_PZ_ID = re.compile(r"^[0-9]{4}IA[0-9]{13}\Z")


@dataclass(frozen=True)
class IaVoucherAsk:
    """ids 与 month 二选一；types 只和 month 一起用。空串 / 空元组表示不传。"""

    ids: tuple[tuple[str, int], ...] = ()
    month: int | None = None
    types: tuple[str, ...] = ()
    merge: str = ""
    sign: str = ""
    digest: str = ""
    voucher_date: str = ""


class U8CoIaVoucherMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def ia_voucher(self, call: U8Call, ask: IaVoucherAsk, idempotency_key: str | None = None) -> dict[str, Any]:
        """存货核算制单。科目没有设置、已制单、已结账等由桥返回 409；凭证已保存但结果不明时 504（先核对再处理）。"""
        return self.call(IA_VOUCHER_ROUTE, with_key(ia_voucher_fields(call, ask), idempotency_key))

    def ia_voucher_delete(self, call: U8Call, pz_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """取消存货核算制单：删除外部业务号 pz_id 的凭证，清掉存货明细账上的凭证号。"""
        return self.call(IA_VOUCHER_DELETE_ROUTE, with_key(ia_voucher_delete_fields(call, pz_id), idempotency_key))


def _type(value: object, label: str) -> str:
    if type(value) is not str or _TYPE.fullmatch(value) is None:
        raise ValueError(f"{label} 必须是存货核算单据类型编码（2 到 4 位数字，如 01、09、3201）")
    return value


def _ids(ids: tuple[tuple[str, int], ...], merge: str) -> list[dict[str, Any]]:
    most = BILL_MAX if merge == "all" else PER_BILL_MAX
    if not 1 <= len(ids) <= most:
        raise ValueError(f"ids 必须是 1 到 {most} 张单据（逐张制单最多 {PER_BILL_MAX} 张，merge=all 最多 {BILL_MAX} 张）")
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for kind, doc_id in ids:
        one = (_type(kind, "ids 的 type"), _int(doc_id, "ids 的 id", 1, _INT32_MAX))
        if one in seen:
            raise ValueError("ids 有重复的单据")
        seen.add(one)
        out.append({"type": one[0], "id": one[1]})
    return out


def _select(fields: dict[str, Any], ask: IaVoucherAsk) -> None:
    if bool(ask.ids) == (ask.month is not None):
        raise ValueError("ids（按单据）与 month（按月份）必须给且只能给一个")
    if ask.ids:
        if ask.types:
            raise ValueError("types 只能和 month 一起用")
        fields["ids"] = _ids(ask.ids, ask.merge)
        return
    fields["month"] = _int(ask.month, "month", 1, 12)
    if ask.types:
        if len(ask.types) > 20:
            raise ValueError("types 最多 20 个")
        fields["types"] = [_type(t, "types") for t in ask.types]


def ia_voucher_fields(call: U8Call, ask: IaVoucherAsk) -> dict[str, Any]:
    fields = _common(call)
    if ask.merge and ask.merge not in IA_VOUCHER_MERGES:
        raise ValueError("merge 只能是 per_bill 或 all")
    _select(fields, ask)
    if ask.merge:
        fields["merge"] = ask.merge
    if ask.sign:
        if len(ask.sign) > 2:
            raise ValueError("sign 必须是 1 到 2 个字的凭证类别")
        fields["sign"] = ask.sign
    if ask.digest:
        digest = ask.digest.strip()
        if not digest or len(digest) > 120:
            raise ValueError("digest 必须是 1 到 120 字")
        fields["digest"] = digest
    if ask.voucher_date:
        fields["voucher_date"] = _ymd(ask.voucher_date, "voucher_date")
    return fields


def ia_voucher_delete_fields(call: U8Call, pz_id: str) -> dict[str, Any]:
    if type(pz_id) is not str or _PZ_ID.fullmatch(pz_id) is None:
        raise ValueError("pz_id 必须是存货核算凭证的外部业务号：4 位年度、IA、13 位数字")
    fields = _common(call)
    fields["pz_id"] = pz_id
    return fields
