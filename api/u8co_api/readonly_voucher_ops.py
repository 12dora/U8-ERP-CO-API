"""只读例外 type=vouchers 的可开放 (op, 单据类型) 登记表，与桥 ReadOnlyVoucherOps.Rows 同一份内容。

一行一个 op：路径恰为 vouchers/<op>；kinds 是配置项 voucher 可列的单据类型（请求体 type 须等于规则的 voucher，
区分大小写）；ops 是该路径按 action 映射后的写入分类 op（write_class.RULES），不在里面的 action 不放行
（verify 覆盖审核与弃审，close 只覆盖关闭、不连带放行打开）；fixed 为 True 时只有一种单据类型，配置可省 voucher；
sources 与 kinds 一一对应：不为 None 时该单据类型的生单来源 source_type 须是它（省略 source_type 即缺省来源，
见 co_gen_source.SOURCES）；actions 不为 None 时请求体 action 须在里面（verify 只认 verify / unverify，
销售发票的应收审核 arap_verify / arap_unverify 虽同记 verify 也不放行）。
新增一行就是开放一种 (op, 单据类型)；登记表之外的 op 让进程起不来。
"""

from __future__ import annotations

from dataclasses import dataclass

from u8co_api.write_class import classify

SALE_ORDER = "sale_order"
DISPATCH = "dispatch"
SALE_INVOICE = "sale_invoice"


@dataclass(frozen=True)
class VoucherOp:
    kinds: tuple[str, ...]
    ops: tuple[str, ...]
    fixed: bool = False
    sources: tuple[str | None, ...] | None = None
    actions: tuple[str, ...] | None = None

    def source_of(self, kind: str) -> str | None:
        """该行里某单据类型规定的生单来源；没有规定时 None。"""
        if self.sources is None or kind not in self.kinds:
            return None
        return self.sources[self.kinds.index(kind)]


VOUCHER_OPS: dict[str, VoucherOp] = {
    # 历史发货单表头修正、销售订单合同号回写：单据类型固定，voucher 可省。
    "head_fix": VoucherOp((DISPATCH,), ("head_fix",), fixed=True),
    "so_contract": VoucherOp((SALE_ORDER,), ("so_contract",), fixed=True),
    "create": VoucherOp((SALE_ORDER,), ("create",)),
    "update": VoucherOp((SALE_ORDER,), ("update",)),
    "close": VoucherOp((SALE_ORDER,), ("close",)),
    # 销售发票的 verify 是复核（cChecker），不含应收审核。
    "verify": VoucherOp((SALE_ORDER, DISPATCH, SALE_INVOICE), ("verify", "unverify"), actions=("verify", "unverify")),
    # 发货单参照销售订单生单；销售发票参照发货单生单（单张或多张 sources），不含参照退货单、红冲蓝字发票。
    "generate": VoucherOp((DISPATCH, SALE_INVOICE), ("generate",), sources=(SALE_ORDER, DISPATCH)),
}
_PREFIX = "co:vouchers/"
_SOURCE = "source_type"
_ACTION = "action"


def voucher_of(op: str, voucher: str | None) -> str | None:
    """规则实际放行的单据类型：配置了就用配置的；单据类型固定的 op 省略时取那一种。"""
    row = VOUCHER_OPS.get(op)
    if row is None:
        return None
    if voucher is not None:
        return voucher if voucher in row.kinds else None
    return row.kinds[0] if row.fixed else None


def voucher_covers(op: str, voucher: str | None, action: str, payload: dict) -> bool:
    """路径恰为 vouchers/<op>、请求体 type 等于规则的单据类型、写入分类 op 在该行里、生单来源对得上才放行。"""
    row = VOUCHER_OPS.get(op)
    kind = voucher_of(op, voucher)
    if row is None or kind is None or action != _PREFIX + op:
        return False
    info = classify(action, payload)
    if info is None or info.type != kind or info.op not in row.ops:
        return False
    if row.actions is not None and payload.get(_ACTION) not in row.actions:
        return False
    source = row.source_of(kind)
    if source is None or _SOURCE not in payload:
        return True
    return payload[_SOURCE] == source
