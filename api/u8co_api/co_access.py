"""/v1/co 的读写分级。唯一的一张表：键是路由的审计动作（CoRoute.action），新路由必须在这里登记。

有写权限：全部 READ、WRITE 路由。只有读权限：只能调 READ 路由。没登记的动作按写处理。
读、写权限来自令牌里信任项配置的声明（缺省 u8co_read、u8co_write）或 scope，见 auth.py。
MGMT（经营管理查询，/v1/co/mgmt/*）只认经营管理权限（信任项的 mgmt_claim / mgmt_scope），读、写权限都不代替它。
多账套汇总、合并报表、公司间对账（reports/aggregate、consolidation、intercompany_match）按账套返回金额，同样归 MGMT。
PERM_EVALUATE（perm/evaluate，查别的操作员的权限）只给信任项写了 perm_evaluate: true 的调用方，且令牌要有读或写权限。
"""

from __future__ import annotations

READ = "read"
WRITE = "write"
MGMT = "mgmt"
PERM_EVALUATE = "perm_evaluate"

ACCESS = {
    "co:health": READ,
    "co:login-check": READ,
    "co:sale-orders/verify": WRITE,
    "co:dispatches/verify": WRITE,
    "co:vouchers/load": READ,
    "co:vouchers/list": READ,
    "co:vouchers/verify": WRITE,
    "co:vouchers/create": WRITE,
    "co:vouchers/delete": WRITE,
    "co:vouchers/update": WRITE,
    "co:vouchers/close": WRITE,
    "co:vouchers/generate": WRITE,
    "co:vouchers/lock": WRITE,
    "co:arap/writeoff": WRITE,
    "co:arap/writeoff/cancel": WRITE,
    "co:arap/writeoff/auto": WRITE,
    # 应收 / 应付制单、取消制单。
    "co:arap/voucher": WRITE,
    "co:arap/voucher/delete": WRITE,
    # 应收冲应付 / 应付冲应收、并账、红票对冲、取消处理、处理制单；汇兑损益与取消（只对桥的测试账套开放）。
    "co:arap/transfer": WRITE,
    "co:arap/merge": WRITE,
    "co:arap/red_offset": WRITE,
    "co:arap/process/cancel": WRITE,
    "co:arap/process/voucher": WRITE,
    # 应收应付处理记录与期间摘要（事件源，只读）。
    "co:arap/process/list": READ,
    "co:arap/exchange_gain": WRITE,
    "co:arap/exchange_gain/cancel": WRITE,
    # 坏账发生 / 收回 / 计提（只对桥的测试账套开放）。
    "co:arap/bad_debt": WRITE,
    # 应收票据登记、删除。
    "co:notes/create": WRITE,
    "co:notes/delete": WRITE,
    # 票据处理（结算、贴现、背书）。
    "co:notes/process": WRITE,
    # 采购期初记账、取消期初记账。
    "co:openings/post": WRITE,
    # 应收 / 应付期初单据（只对桥的测试账套开放）。
    "co:openings/arap": WRITE,
    # 月末结账、取消结账（只对桥的测试账套开放）。
    "co:periods/close": WRITE,
    # 存货核算正常单据记账 / 恢复记账、期末处理 / 取消期末处理（只对桥的测试账套开放）。
    "co:ia/post": WRITE,
    "co:ia/period_end": WRITE,
    "co:stock/current": READ,
    "co:workflow/state": READ,
    "co:workflow/history": READ,
    "co:workflow/tasks": READ,
    "co:workflow/submit": WRITE,
    "co:workflow/withdraw": WRITE,
    "co:workflow/approve": WRITE,
    "co:workflow/disagree": WRITE,
    "co:workflow/return": WRITE,
    "co:workflow/abandon": WRITE,
    "co:workflow/resubmit": WRITE,
    "co:gl/vouchers/load": READ,
    "co:gl/vouchers/list": READ,
    "co:gl/vouchers/digest": READ,  # 凭证摘要（事件源）
    "co:gl/vouchers/create": WRITE,
    "co:gl/vouchers/update": WRITE,
    "co:gl/vouchers/void": WRITE,
    "co:gl/vouchers/unvoid": WRITE,
    "co:gl/vouchers/verify": WRITE,
    "co:gl/vouchers/unverify": WRITE,
    "co:gl/vouchers/sign": WRITE,
    "co:gl/vouchers/unsign": WRITE,
    "co:gl/vouchers/delete": WRITE,
    "co:gl/vouchers/post": WRITE,
    "co:gl/vouchers/unpost": WRITE,  # 取消记账（测试账套）
    "co:gl/vouchers/reverse": WRITE,  # 红字冲销
    "co:gl/transfer/pnl": WRITE,  # 期间损益结转（测试账套）
    "co:gl/transfer/custom": WRITE,  # 自定义转账（测试账套）
    "co:archives/get": READ,
    "co:archives/list": READ,
    "co:archives/create": WRITE,
    "co:archives/update": WRITE,
    "co:archives/delete": WRITE,
    # 只读报表。
    "co:reports/close_status": READ,
    "co:reports/gl_balance": READ,
    "co:reports/gl_aux_balance": READ,
    "co:reports/arap_balance": READ,
    "co:reports/arap_aging": READ,
    "co:reports/bom": READ,
    "co:reports/arap_detail": READ,
    "co:reports/gl_detail": READ,
    "co:reports/order_execution": READ,
    "co:reports/doc_trace": READ,
    # 库存与销售支持报表。
    "co:reports/stock_ledger": READ,
    "co:reports/stock_summary": READ,
    "co:reports/position_stock": READ,
    "co:reports/batch_stock": READ,
    "co:reports/customer_credit": READ,
    "co:reports/price_list": READ,
    # 期初余额、凭证附件与单据附件列表（只读）。
    "co:reports/opening_balance": READ,
    # 核销记录。
    "co:reports/arap_writeoffs": READ,
    # 账套体检。
    "co:reports/account_readiness": READ,
    # 固定资产变动单、折旧。
    "co:reports/fa_changes": READ,
    "co:reports/fa_depreciation": READ,
    "co:gl/vouchers/attachments/list": READ,
    "co:vouchers/attachments/list": READ,
    # 字段元数据。
    "co:meta": READ,
    # 档案名称解析、幂等结果查询。
    "co:archives/resolve": READ,
    "co:idempotency/get": READ,
    # 字段标签、单据查询、批量读取单据和档案。
    "co:meta/fields": READ,
    "co:vouchers/search": READ,
    "co:vouchers/load_many": READ,
    "co:archives/get_many": READ,
    # 票据读取（票据列表走 vouchers/list）。
    "co:notes/get": READ,
    # 公司间对账（多账套只读）、按卖方单据生成买方单据（写）。
    # 对账按往来单位列出数量、金额：只给有经营管理权限的令牌。
    "co:reports/intercompany_match": MGMT,
    "co:intercompany/generate_buyer": WRITE,
    # 多账套汇总、合并报表（只读，需经营管理权限）。
    "co:reports/aggregate": MGMT,
    "co:reports/consolidation": MGMT,
    # 经营管理查询（多账套只读，另需经营管理权限）。
    "co:mgmt/meta": MGMT,
    "co:mgmt/pnl": MGMT,
    "co:mgmt/sales": MGMT,
    "co:mgmt/arap": MGMT,
    "co:mgmt/cash_stock": MGMT,
    "co:mgmt/overview": MGMT,
    # 当前操作员自己的有效权限（与桥过滤数据用的是同一份）；查别的操作员的权限另有一级。
    "co:perm/snapshot": READ,
    "co:perm/evaluate": PERM_EVALUATE,
}
# 经营管理路由族（/v1/co/mgmt/*，co_mgmt_core）。MGMT 级别还包括多账套报表，但它们不是这个路由族。
MGMT_ACTIONS = frozenset(action for action, level in ACCESS.items() if level == MGMT and action.startswith("co:mgmt/"))

_NOTE = {
    READ: "**令牌权限：只读**（读或写权限均可，缺省声明 u8co_read / u8co_write）。",
    WRITE: "**令牌权限：写**（缺省声明 u8co_write）；只有读权限返回 403。",
    MGMT: "**令牌权限：经营管理**（信任项的 mgmt_claim / mgmt_scope，读、写权限不能代替）；没有时 403 mgmt_forbidden。",
    PERM_EVALUATE: "**令牌权限：权限评估**（信任项写了 perm_evaluate: true，且有读或写权限）；否则 403 forbidden。",
}


def is_read(action: str) -> bool:
    return ACCESS.get(action) == READ


def is_mgmt(action: str) -> bool:
    return ACCESS.get(action) == MGMT


def is_perm_evaluate(action: str) -> bool:
    return ACCESS.get(action) == PERM_EVALUATE


def may_call(write: bool, read: bool, action: str, mgmt: bool = False, perm_evaluate: bool = False) -> bool:
    if is_mgmt(action):
        return mgmt
    if is_perm_evaluate(action):
        return perm_evaluate and (write or read)
    if write:
        return True
    return read and is_read(action)


def access_of(action: str) -> str:
    """READ、WRITE、MGMT 或 PERM_EVALUATE（没登记的按写）。写进 OpenAPI 的 x-u8co-access。"""
    if is_mgmt(action):
        return MGMT
    if is_perm_evaluate(action):
        return PERM_EVALUATE
    return READ if is_read(action) else WRITE


def access_note(action: str) -> str:
    return _NOTE[access_of(action)]
