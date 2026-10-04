"""/v1/co/reports/* 只读报表路由。注册见 co_routes.register_co_routes。"""

from u8co_api.co_models_reports import (
    ReportAgingIn,
    ReportAgingOut,
    ReportArapIn,
    ReportArapOut,
    ReportBomIn,
    ReportBomOut,
    ReportCloseStatusIn,
    ReportCloseStatusOut,
    ReportGlAuxIn,
    ReportGlAuxOut,
    ReportGlBalanceIn,
    ReportGlBalanceOut,
)
from u8co_api.co_models_reports_fa import (
    ReportFaChangesIn,
    ReportFaChangesOut,
    ReportFaDepreciationIn,
    ReportFaDepreciationOut,
)
from u8co_api.co_models_reports_detail import (
    ReportArapDetailIn,
    ReportArapDetailOut,
    ReportGlDetailIn,
    ReportGlDetailOut,
)
from u8co_api.co_models_reports_trace import (
    ReportDocTraceIn,
    ReportDocTraceOut,
    ReportOrderExecIn,
    ReportOrderExecOut,
)
from u8co_api.co_models_reports_stock import (
    ReportBatchStockIn,
    ReportBatchStockOut,
    ReportCustomerCreditIn,
    ReportCustomerCreditOut,
    ReportPositionStockIn,
    ReportPositionStockOut,
    ReportPriceListIn,
    ReportPriceListOut,
    ReportStockLedgerIn,
    ReportStockLedgerOut,
    ReportStockSummaryIn,
    ReportStockSummaryOut,
)
from u8co_api.co_models_reports_open import ReportOpeningIn, ReportOpeningOut
from u8co_api.co_models_reports_ready import ReportReadinessIn, ReportReadinessOut
from u8co_api.co_models_reports_writeoff import (
    WRITEOFFS_HELP,
    WRITEOFFS_SUMMARY,
    ReportWriteoffsIn,
    ReportWriteoffsOut,
)
from u8co_api.co_routes_ic import IC_MATCH_ROUTE  # 公司间对账（多账套，runner 自己调桥）
from u8co_api.co_ic_aggregate import run_aggregate  # 多账套汇总
from u8co_api.co_ic_consol import run_consolidation  # 合并报表（往来抵销）
from u8co_api.co_models_ic_reports import (
    AGGREGATE_HELP,
    AGGREGATE_SUMMARY,
    CONSOLIDATION_HELP,
    CONSOLIDATION_SUMMARY,
    IcAggregateIn,
    IcAggregateOut,
    IcConsolidationIn,
    IcConsolidationOut,
)
from u8co_api.co_doctext import PAGE, op_doc
from u8co_api.co_table import CoRoute

TAG_REPORT = "报表"

_DB = "只查数据库，不调用 U8 组件"
_FY = "会计年度缺省取登录日期（date）的年份，可用 fiscal_year 指定；year 是账套库年度"


def _report(name: str, pair: tuple[type, type], summary: str, text: str) -> CoRoute:
    words = "".join(part.title() for part in name.split("_"))
    return CoRoute(
        f"/v1/co/reports/{name}",
        f"/v1/reports/{name}",
        pair[0],
        pair[1],
        summary,
        text,
        f"coReport{words}",
        f"co:reports/{name}",
        TAG_REPORT,
    )


REPORT_ROUTES = (
    _report(
        "close_status",
        (ReportCloseStatusIn, ReportCloseStatusOut),
        "月结状态",
        op_doc(
            "按期间列出各模块是否已结账。",
            ("用法", (_FY,)),
            ("规则", ("模块：销售、采购、库存、存货核算、总账、应收、应付、成本、固定资产", _DB)),
        ),
    ),
    _report(
        "gl_balance",
        (ReportGlBalanceIn, ReportGlBalanceOut),
        "科目余额表",
        op_doc(
            "发生额及余额表：按科目列出期初、本期、本年累计、期末。",
            ("用法", (_FY, PAGE, "include_unposted 为 true 时，未记账凭证计入本期、累计和期末")),
            ("规则", ("缺省只含已记账凭证", "余额按方向拆成借方和贷方", "上级科目已含下级", _DB)),
        ),
    ),
    _report(
        "gl_aux_balance",
        (ReportGlAuxIn, ReportGlAuxOut),
        "辅助核算余额表",
        op_doc(
            "按科目和辅助项列出期初、本期、累计、期末。",
            ("用法", (_FY, PAGE)),
            ("规则", ("辅助项：客户、供应商、部门、个人或项目", "只含已记账凭证", _DB)),
        ),
    ),
    _report(
        "arap_balance",
        (ReportArapIn, ReportArapOut),
        "往来余额",
        op_doc(
            "按客户（应收）或供应商（应付）列出截至 as_of 的借方、贷方累计和余额。",
            ("用法", (PAGE,)),
            ("规则", ("不含应收票据和现金类记录", _DB)),
        ),
    ),
    _report(
        "arap_aging",
        (ReportAgingIn, ReportAgingOut),
        "账龄分析",
        op_doc(
            "按客户或供应商把截至 as_of 的余额分到账龄区间。",
            ("用法", (PAGE,)),
            ("规则", ("未核销的预收、预付单列", "应收先把核销分摊回原单据，再算账龄", _DB)),
        ),
    ),
    _report(
        "bom",
        (ReportBomIn, ReportBomOut),
        "物料清单",
        op_doc(
            "按生效日期读取母件的已审核物料清单。",
            ("用法", ("levels 大于 1 时多层展开", "qty 是每 1 个母件的累计用量")),
            ("规则", (_DB,)),
            ("限制", ("不分页；超过 limit 时 truncated 为 true",)),
            ("错误", ("404 not_found：该日期没有已审核的物料清单",)),
        ),
    ),
    _report(
        "arap_detail",
        (ReportArapDetailIn, ReportArapDetailOut),
        "往来明细账",
        op_doc(
            "按客户（应收）或供应商（应付）列出往来明细和滚动余额。",
            ("用法", (PAGE, "核销行缺省不列")),
            (
                "规则",
                (
                    "先列 date_from 的期初，再逐张列出区间内单据的借方、贷方",
                    "口径同往来余额：不含应收票据和现金类记录",
                    _DB,
                ),
            ),
        ),
    ),
    _report(
        "gl_detail",
        (ReportGlDetailIn, ReportGlDetailOut),
        "科目明细账",
        op_doc(
            "按期间或日期列出科目的期初、逐条凭证分录和滚动余额。",
            ("用法", (_FY, PAGE, "科目缺省含下级", "可按客户、供应商、部门、个人、项目过滤")),
            ("规则", ("期初规则同科目余额表", "缺省只含已记账凭证", _DB)),
            ("错误", ("404 not_found：科目在该年度不存在",)),
        ),
    ),
    _report(
        "order_execution",
        (ReportOrderExecIn, ReportOrderExecOut),
        "订单执行",
        op_doc(
            "逐行列出销售订单或采购订单的数量、金额和累计执行数。",
            ("用法", (PAGE, "可按 id、单号、日期、往来单位过滤", "only_open 只要未执行完且未关闭的行")),
            (
                "规则",
                (
                    "执行数取自 U8 订单行上的累计数",
                    "销售：发货、出库、开票、退货、收款",
                    "采购：到货、入库、开票、退货、付款",
                    _DB,
                ),
            ),
            ("权限", ("数据权限同该订单类型的列表",)),
        ),
    ),
    _report(
        "doc_trace",
        (ReportDocTraceIn, ReportDocTraceOut),
        "单据追溯",
        op_doc(
            "从一张单据出发，列出上游（来源）和下游（去向）的关联单据和关联行数。",
            (
                "规则",
                (
                    "覆盖销售、采购（含质检）、生产和收付款核销",
                    "没有数据权限的单据不列出、也不再往下找，只计入 omitted",
                    _DB,
                ),
            ),
            ("限制", ("每个方向最多 3 跳，共最多 200 个节点",)),
            ("错误", ("404 not_found：起点单据不存在", "403 no_permission：越权")),
        ),
    ),
    _report(
        "stock_ledger",
        (ReportStockLedgerIn, ReportStockLedgerOut),
        "库存台账",
        op_doc(
            "列出一个存货在日期区间内的每一行收发，带期初和逐行结存。",
            ("用法", (PAGE,)),
            (
                "规则",
                (
                    "单据：采购入库、其他入库、其他出库、产成品入库、材料出库、销售出库、库存期初",
                    "缺省只含已审核单据",
                    _DB,
                ),
            ),
            ("错误", ("404 not_found：存货不存在",)),
        ),
    ),
    _report(
        "stock_summary",
        (ReportStockSummaryIn, ReportStockSummaryOut),
        "收发存汇总表",
        op_doc(
            "按存货（缺省再按仓库）汇总日期区间的期初、入库、出库、结存数量。",
            ("用法", (PAGE,)),
            ("规则", ("只有数量，没有金额", "缺省只含已审核单据", _DB)),
        ),
    ),
    _report(
        "position_stock",
        (ReportPositionStockIn, ReportPositionStockOut),
        "货位存量",
        op_doc(
            "按货位列出存货、批次的结存数量。",
            ("用法", (PAGE,)),
            ("规则", ("只含已指定货位的部分", _DB)),
        ),
    ),
    _report(
        "batch_stock",
        (ReportBatchStockIn, ReportBatchStockOut),
        "批次存量",
        op_doc(
            "按仓库、存货、批号汇总现存量，带生产日期和失效日期。",
            ("用法", (PAGE, "expiring_before 用于保质期预警")),
            ("规则", (_DB,)),
        ),
    ),
    _report(
        "customer_credit",
        (ReportCustomerCreditIn, ReportCustomerCreditOut),
        "客户信用",
        op_doc(
            "列出客户的信用额度、信用期限、信用等级和信用占用。",
            ("用法", (PAGE,)),
            (
                "规则",
                (
                    "占用按 U8 信用余额表口径：订单、发货单、发票、应收余额、代垫费用",
                    "used 按销售选项的额度检查公式合计",
                    "受控客户另给可用额度 available",
                    _DB,
                ),
            ),
        ),
    ),
    _report(
        "price_list",
        (ReportPriceListIn, ReportPriceListOut),
        "价格表",
        op_doc(
            "列出客户价格表、存货价格表或供应商存货价格表。",
            ("用法", (PAGE, "缺省只列 as_of 当天有效的价格")),
            ("规则", (_DB,)),
        ),
    ),
    _report(
        "opening_balance",
        (ReportOpeningIn, ReportOpeningOut),
        "期初余额",
        op_doc(
            "按模块列出期初余额。",
            (
                "用法",
                (
                    PAGE,
                    "module=stock：库存期初，期初单据按仓库、存货、批号汇总数量和金额",
                    "module=arap：应收或应付期初单，按往来单位、科目",
                    "module=gl：总账期初余额，按科目带试算平衡，或按辅助项",
                ),
            ),
            (
                "规则",
                ("都带模块启用日期和期初是否已记账（posted）", "只读：期初的录入和修改不经本接口", _DB),
            ),
            ("权限", ("数据权限同对应的余额表",)),
        ),
    ),
    # 核销记录（co_models_reports_writeoff）。
    _report("arap_writeoffs", (ReportWriteoffsIn, ReportWriteoffsOut), WRITEOFFS_SUMMARY, WRITEOFFS_HELP),
    _report(
        "account_readiness",
        (ReportReadinessIn, ReportReadinessOut),
        "账套体检",
        op_doc(
            "检查账套能否跑回归，每项给出结论和修复提示。",
            (
                "规则",
                (
                    "检查补丁、年度、工作日历、年度配置、采购期初、供应商自定义项表",
                    "检查模块启用、以前年度总账结账、审批流和默认档案",
                    "每项给 ok、warn、fail 或 unknown，附修复提示和 getting-started.md 的小节",
                    "检查 UFSystem 的项读不到时记 unknown",
                    "问题放在 checks 里，HTTP 仍是 200",
                    _DB,
                ),
            ),
            ("错误", ("422：登录日期（date）不在已建立的年度里，登录失败",)),
        ),
    ),
    # 固定资产（co_models_reports_fa）。
    _report(
        "fa_changes",
        (ReportFaChangesIn, ReportFaChangesOut),
        "固定资产变动单",
        op_doc(
            "列出固定资产变动单（原值增减、计提减值准备、部门转移等），每张另带部门明细。",
            ("用法", (_FY, PAGE, "按变动日期的年份和变动期间过滤", "可按卡片、单号、类型过滤")),
            ("规则", ("U8 每做一次变动，卡片就多一个版本；opt_id 是变动后版本", _DB)),
            ("相关", ("卡片当时的状态：archives/get 的 fa_card",)),
        ),
    ),
    _report(
        "fa_depreciation",
        (ReportFaDepreciationIn, ReportFaDepreciationOut),
        "固定资产折旧",
        op_doc(
            "按卡片和期间列出已计提的折旧。",
            ("用法", (_FY, PAGE)),
            (
                "规则",
                (
                    "给出当月折旧、月末累计折旧、月折旧率和月初原值",
                    "只含该年度已计提的期间（posted_periods）",
                    _DB,
                ),
            ),
            ("相关", ("累计折旧与 fa_card 在同一期间的 accumulated_depreciation 一致",)),
        ),
    ),
    # 公司间对账（co_routes_ic）。
    IC_MATCH_ROUTE,
    # 多账套汇总、合并报表（co_ic_aggregate、co_ic_consol）：runner 按账套调内层报表，bridge_path 只是分类。
    CoRoute(
        "/v1/co/reports/aggregate",
        "/v1/reports/aggregate",
        IcAggregateIn,
        IcAggregateOut,
        AGGREGATE_SUMMARY,
        AGGREGATE_HELP,
        "coReportAggregate",
        "co:reports/aggregate",
        TAG_REPORT,
        runner=run_aggregate,
    ),
    CoRoute(
        "/v1/co/reports/consolidation",
        "/v1/reports/consolidation",
        IcConsolidationIn,
        IcConsolidationOut,
        CONSOLIDATION_SUMMARY,
        CONSOLIDATION_HELP,
        "coReportConsolidation",
        "co:reports/consolidation",
        TAG_REPORT,
        runner=run_consolidation,
        keep_null=True,
    ),
)
