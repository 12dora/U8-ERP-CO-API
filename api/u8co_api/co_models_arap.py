"""vouchers/verify 的应付审核 / 应收审核（arap_verify / arap_unverify），只给采购发票和销售发票。"""

from __future__ import annotations

from typing import Literal

VoucherVerifyAction = Literal["verify", "unverify", "arap_verify", "arap_unverify"]

ARAP_ACTIONS = frozenset({"arap_verify", "arap_unverify"})
ARAP_TYPES = frozenset({"purchase_invoice", "sale_invoice"})
ARAP_REFUSED = "只有采购发票、销售发票支持应收应付审核"

VERIFY_ACTION_HELP = (
    "verify 审核，unverify 弃审。\n\n"
    "- 采购发票、销售发票另有 arap_verify：应付款管理 / 应收款管理的审核\n"
    "- arap_verify 写 cPBVVerifier / cVerifier 和应付 / 应收明细；arap_unverify 取消该审核\n"
    "- 其余类型送这两个值返回 400"
)

# vouchers/verify 说明里「用法」列表的几条（Markdown 列表项，每条以换行结尾）。
ARAP_ROUTE_HELP = (
    "- 采购发票、销售发票的 action 另可送 arap_verify / arap_unverify："
    "应付款管理（采购发票）或应收款管理（销售发票）的审核、弃审。\n"
    "- 应收应付审核、弃审要求已复核、未启用审批流、登录日期所在期间未结账。\n"
    "- 应收应付弃审另要求未制单、未核销。\n"
    "- 应收应付审核的响应：verified_by / verified_at 是应付（应收）审核人和日期。\n"
    "- 应收应付审核的 state 带 arap_verified、arap_verifier、arap_verified_at、gl_voucher。\n"
)


def check_verify_action(voucher_type: str, action: str) -> None:
    """arap_* 只收采购发票、销售发票；不合规抛 ValueError（API 层 400）。"""
    if action in ARAP_ACTIONS and voucher_type not in ARAP_TYPES:
        raise ValueError(ARAP_REFUSED)
