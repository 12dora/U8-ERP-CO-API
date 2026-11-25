"""资源标签的登记表（casespec/tags.py 用）：挑单键的单据类型、动现存量的类型、档案牵连、报表的域、单据链等。

新用例的挑单键、单据类型往这里追加；规则和词表见 tags.py。
"""

from __future__ import annotations

# 挑单键 → 单据类型（只列挑出来是单据主键的键；行 id、仓库、编码之类不列）。load_<类型> 另按前缀推。
NEED_KIND = {
    "arv_inv": "dispatch",
    "sale_id": "sale_order",
    "sale_verified": "sale_order",
    "so_ship": "sale_order",
    "disp_mod_src": "sale_order",
    "se_so": "sale_order",
    "dispatch_id": "dispatch",
    "dispatch_downstream": "dispatch",
    "inv_src": "dispatch",
    "sr_src": "dispatch",
    "sret_mod_src": "dispatch",
    "sinv_mod_src": "dispatch",
    "spo_src": "dispatch",
    "sale_invoice_ar": "sale_invoice",
    "po_open": "purchase_order",
    "po_lines": "purchase_order",
    "po_recv": "purchase_order",
    "arr_src": "purchase_order",
    "arr_mod_src": "purchase_order",
    "se_po": "purchase_order",
    "prpo_src": "purchase_order",
    "arrival_open": "arrival",
    "pr_src": "arrival",
    "rp_arr": "arrival",  # 红字采购链路（pu_red_chain）
    "rpu_arr": "arrival",  # 红字采购发票修改（pu_red_chain）
    "qmi_po": "purchase_order",  # 质量单据新增（qm_write）
    "qmp_mo": "production_order",
    "pret_mod_src": "arrival",
    "pinv_src": "purchase_in",
    "pinv_mod_src": "purchase_in",
    "other_out_open": "other_out",
    "other_out_done": "other_out",
    "qm04_a": "qm_product_check",
    "qm04_b": "qm_product_check",
    "qm_pin": "qm_product_check",
    "se_pq": "qm_product_check",
    "qm03_id": "qm_incoming_check",
    "qmin_src": "qm_incoming_check",
    "se_qm": "qm_incoming_check",
    "qm06_id": "qm_product_reject",
    "qmrej": "qm_product_reject",
    "mo_free": "production_order",
    "mo_audited": "production_order",
    "mo_alloc": "production_order",
    "se_mo": "production_order",
    "mo_src": "production_order",
    "ar_voucher": "ar_receipt",
    # 锁定 / 解锁（so_lock）。
    "lock_so": "sale_order",
    "lock_sov": "sale_order",
    # 红字销售发票参照退货单（sale_red_invoice）。
    "rs_src": "dispatch",
    # 应付审核（arap_verify）。
    "arv_pu": "purchase_invoice",
    "arv_pudone": "purchase_invoice",
    "arv_puraw": "purchase_invoice",
    # 应收 / 应付核销（writeoff）：收付款单和发票。
    "hx_rc": "ar_receipt",
    "hx_inv": "sale_invoice",
    "hx_pc": "ap_payment",
    "hx_pinv": "purchase_invoice",
    # 自动核销（writeoff_auto）。
    "hxa_rc": "ar_receipt",
    "hxa_inv": "sale_invoice",
    # 应付自动核销（ap_writeoff_voucher）。
    "dry_ap_pc": "ap_payment",
    "dry_ap_pinv": "purchase_invoice",
}
# 生单、改单、关行时回写来源数量的挑单键。
SOURCE_NEEDS = frozenset(
    (
        "so_ship", "disp_mod_src", "se_so", "sale_verified", "inv_src", "arv_inv", "sr_src", "sret_mod_src", "sinv_mod_src", "spo_src",
        "po_lines", "po_recv", "arr_src", "arr_mod_src", "se_po", "prpo_src", "pr_src", "pret_mod_src", "pinv_src",
        "pinv_mod_src", "qm_pin", "se_pq", "qmin_src", "se_qm", "qmrej", "mo_alloc", "se_mo", "mo_src",
    )
)
# 写了就动现存量（含待发、待入、预留）的单据类型。拿不准的也算进来（保守）。
STOCK_KINDS = frozenset(
    (
        "other_in", "other_out", "sale_out", "purchase_in", "material_out", "product_in", "transfer",
        "dispatch", "sale_return", "arrival", "purchase_return", "production_order",
    )
)
# 同桥 DocLocks.GenerateKeys 的交叉锁。
CROSS_NEW = {
    "dispatch": ("sale_return",),
    "sale_return": ("dispatch",),
    "arrival": ("purchase_return",),
    "purchase_return": ("arrival",),
}
# 构造器名就是单据类型的新建步骤（Step("create", sql_name=<类型>)）。
KINDS = frozenset(
    (
        "sale_order", "dispatch", "purchase_order", "arrival", "purchase_in", "other_in", "other_out", "product_in",
        "material_out", "sale_out", "transfer", "sale_invoice", "purchase_invoice", "production_order",
        "ar_receipt", "ap_payment", "ar_bill", "ap_bill", "sale_return", "purchase_return", "purchase_requisition",
        "bom",
    )
)
# 档案之间的牵连：写（读）前者时也锁（避开）后者。
ARC_LINKS = {
    "unit": ("unit_group",),
    "unit_group": ("unit",),
    "customer_inventory": ("customer", "inventory"),
    "customer_address": ("customer",),
    "customer_invoice_company": ("customer",),
    "pay_agreement": ("customer",),
    "purchase_type": ("rd_style",),
    "sale_type": ("rd_style",),
    "position": ("warehouse",),
    "person": ("department",),
    "currency": ("exchange_rate",),
}
# 报表 → 域。report 步骤按 fields 的 name，其余按操作名。
REPORT_DOMAIN = {
    "close_status": ("close",),
    # 账套体检读 GL_mend 结账标志、期初记账标志等账套状态，和总账写冲突。
    "account_readiness": ("close",),
    "gl_balance": ("gl",),
    "gl_aux_balance": ("gl", "arap"),
    "arap_balance": ("arap",),
    "arap_aging": ("arap",),
    "bom": ("bom",),
    "report_arap_detail": ("arap",),
    "report_gl_detail": ("gl",),
    "trace_exec": ("trace",),
    "trace_graph": ("trace",),
    "trace_qm_other": ("trace",),
    "report_stock_summary": ("stock",),
    "report_stock_ledger": ("stock",),
    "report_position_stock": ("stock",),
    "report_batch_stock": ("stock",),
    "report_customer_credit": ("arap", "trace"),
    "report_price_list": ("price",),
    # 固定资产变动单、折旧（ia_sa_fa）：没有写固定资产的用例。
    "fa_changes_read": ("fa",),
    "fa_depr_read": ("fa",),
    # 经营管理报表（总账口径，mgmt_gl）：损益、水位读总账；资金存货另读存货、采购、票据。
    "mgmt_pnl_read": ("gl",),
    "mgmt_meta_read": ("close", "gl"),
    "mgmt_cash_read": ("gl", "stock", "arap"),
    # 经营管理销售分析（mgmt_sa）读销售发票和存货核算；往来账期读应收应付明细与票据。
    "mgmt_sales_read": ("arap", "stock"),
    "mgmt_arap_read": ("arap",),
}
ARAP_WRITES = ("ar_", "ap_", "sale_invoice", "purchase_invoice")
# 读标签 → 会让它读错的写标签（以 : 或 _ 结尾的是前缀）。
RPT_HITS = {
    "rpt:gl": ("gl", "new:gl:"),
    "rpt:close": ("gl",),
    "rpt:arap": tuple("w:" + k for k in ARAP_WRITES) + tuple("new:" + k for k in ARAP_WRITES),
    "rpt:stock": ("stock",),
    "rpt:bom": ("w:bom", "new:bom"),
    # 订单执行、单据追溯、客户信用顺着单据链读，任何单据写都可能挪动数字。
    "rpt:trace": ("w:", "new:"),
    "rpt:price": (),
    "rpt:fa": (),
}
# 单据链：同一条链上的单据生单、改单、审核会回写上游订单行（发票回写订单行开票数、采购发票回写订单、
# 退货回写原发货 / 到货、质量单据回写到货 / 生产订单）。挑单只给直接来源，拿不到上游订单 id，
# 所以按链粗粒度互斥（chain:<链>）；不同链之间照常并行。
CHAIN_OF = {
    "sale_order": "sale",
    "dispatch": "sale",
    "sale_out": "sale",
    "sale_invoice": "sale",
    "sale_return": "sale",
    "purchase_order": "pu",
    "arrival": "pu",
    "purchase_return": "pu",
    "purchase_in": "pu",
    "purchase_invoice": "pu",
    "qm_incoming_inspect": "pu",
    "qm_incoming_check": "pu",
    "qm_incoming_reject": "pu",
    "production_order": "mfg",
    "material_out": "mfg",
    "product_in": "mfg",
    "qm_product_inspect": "mfg",
    "qm_product_check": "mfg",
    "qm_product_reject": "mfg",
}
EXCLUSIVE_TAGS = frozenset(("burst",))
MARK_PREFIXES = ("w:", "list:", "arc-read:", "rpt:", "wave:", "opt:")
MARKS = frozenset(("stock-read", "meta", "login", "probe"))
VOUCHER = "/v1/vouchers/"
WRITE_OPS = frozenset(("verify", "delete", "close", "update", "generate", "create", "lock"))
FIXED_ROUTES = {"/v1/sale-orders/verify": "sale_order", "/v1/dispatches/verify": "dispatch"}
WF_WRITES = frozenset(("submit", "withdraw", "approve", "disagree", "return", "abandon", "resubmit"))
GL_READS = frozenset(("load", "list"))
GL_SIGNS = {"create_transfer": "转", "create_bank": "付"}
SIMPLE_STEPS = {"audit": ("audit",), "health": ("meta",), "badsig": ("meta",), "accforeign": ("meta",), "login": ("login",)}
CORE_LISTS = frozenset(("list_filter", "list_keys", "list_page", "list_mark", "list_since"))
CUSTOM_FIXED = {
    "meta": ("meta",),
    "arc_more_project": ("arc:project",),
    "idem_ok": ("gl", "new:gl:转"),
    "idem_again": ("gl", "new:gl:转"),
    "idem_bad": ("gl", "new:gl:转"),
    "idem_mismatch": ("gl", "new:gl:转"),
}
