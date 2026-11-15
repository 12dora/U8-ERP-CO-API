"""按角色分族的只读报表 /v1/co/reports/<name>（质量、生产、库龄、采购、销售、收付款与现金流、账务核对）。

模型和说明文字在各族的 co_models_reports_<族>：<Name>In / <Name>Out 和 <NAME>_SUMMARY / <NAME>_DOC。
汇入 co_routes_reports.REPORT_ROUTES；访问级别（读）见 co_access。
"""

from __future__ import annotations

from u8co_api.co_report_route import report
from u8co_api.co_models_reports_qm import (
    QM_INDICATORS_DOC,
    QM_INDICATORS_SUMMARY,
    QM_PASS_RATE_DOC,
    QM_PASS_RATE_SUMMARY,
    QmIndicatorsIn,
    QmIndicatorsOut,
    QmPassRateIn,
    QmPassRateOut,
)
from u8co_api.co_models_reports_mfg import (
    MO_CONSUMPTION_DOC,
    MO_CONSUMPTION_SUMMARY,
    MO_PROGRESS_DOC,
    MO_PROGRESS_SUMMARY,
    MoConsumptionIn,
    MoConsumptionOut,
    MoProgressIn,
    MoProgressOut,
    PRODUCTION_DAILY_DOC,
    PRODUCTION_DAILY_SUMMARY,
    ProductionDailyIn,
    ProductionDailyOut,
)
from u8co_api.co_models_reports_stock_age import (
    STOCK_AGE_DOC,
    STOCK_AGE_SUMMARY,
    StockAgeIn,
    StockAgeOut,
)
from u8co_api.co_models_reports_pu import (
    ARRIVAL_OTIF_DOC,
    ARRIVAL_OTIF_SUMMARY,
    ArrivalOtifIn,
    ArrivalOtifOut,
    PURCHASE_ANALYSIS_DOC,
    PURCHASE_ANALYSIS_SUMMARY,
    PurchaseAnalysisIn,
    PurchaseAnalysisOut,
)
from u8co_api.co_models_reports_sa import (
    CUSTOMER_CHURN_DOC,
    CUSTOMER_CHURN_SUMMARY,
    CustomerChurnIn,
    CustomerChurnOut,
    OPEN_ORDERS_DOC,
    OPEN_ORDERS_SUMMARY,
    OpenOrdersIn,
    OpenOrdersOut,
    SALES_PRICE_HISTORY_DOC,
    SALES_PRICE_HISTORY_SUMMARY,
    SalesPriceHistoryIn,
    SalesPriceHistoryOut,
)
from u8co_api.co_models_reports_fin import (
    CASH_FLOW_DOC,
    CASH_FLOW_SUMMARY,
    CashFlowIn,
    CashFlowOut,
    NOTES_DUE_DOC,
    NOTES_DUE_SUMMARY,
    NotesDueIn,
    NotesDueOut,
    RECEIPTS_PAYMENTS_DOC,
    RECEIPTS_PAYMENTS_SUMMARY,
    ReceiptsPaymentsIn,
    ReceiptsPaymentsOut,
)
from u8co_api.co_models_reports_acct import (
    CLOSE_CHECK_DOC,
    CLOSE_CHECK_SUMMARY,
    COST_UNIT_DOC,
    COST_UNIT_SUMMARY,
    CloseCheckIn,
    CloseCheckOut,
    CostUnitIn,
    CostUnitOut,
    IA_ANOMALIES_DOC,
    IA_ANOMALIES_SUMMARY,
    IaAnomaliesIn,
    IaAnomaliesOut,
)

ROLE_REPORT_ROUTES = (
    report("qm_indicators", (QmIndicatorsIn, QmIndicatorsOut), QM_INDICATORS_SUMMARY, QM_INDICATORS_DOC),
    report("qm_pass_rate", (QmPassRateIn, QmPassRateOut), QM_PASS_RATE_SUMMARY, QM_PASS_RATE_DOC),
    report(
        "production_daily",
        (ProductionDailyIn, ProductionDailyOut),
        PRODUCTION_DAILY_SUMMARY,
        PRODUCTION_DAILY_DOC,
    ),
    report("mo_progress", (MoProgressIn, MoProgressOut), MO_PROGRESS_SUMMARY, MO_PROGRESS_DOC),
    report("mo_consumption", (MoConsumptionIn, MoConsumptionOut), MO_CONSUMPTION_SUMMARY, MO_CONSUMPTION_DOC),
    report("stock_age", (StockAgeIn, StockAgeOut), STOCK_AGE_SUMMARY, STOCK_AGE_DOC),
    report(
        "purchase_analysis",
        (PurchaseAnalysisIn, PurchaseAnalysisOut),
        PURCHASE_ANALYSIS_SUMMARY,
        PURCHASE_ANALYSIS_DOC,
    ),
    report("arrival_otif", (ArrivalOtifIn, ArrivalOtifOut), ARRIVAL_OTIF_SUMMARY, ARRIVAL_OTIF_DOC),
    report("open_orders", (OpenOrdersIn, OpenOrdersOut), OPEN_ORDERS_SUMMARY, OPEN_ORDERS_DOC),
    report(
        "sales_price_history",
        (SalesPriceHistoryIn, SalesPriceHistoryOut),
        SALES_PRICE_HISTORY_SUMMARY,
        SALES_PRICE_HISTORY_DOC,
    ),
    report("customer_churn", (CustomerChurnIn, CustomerChurnOut), CUSTOMER_CHURN_SUMMARY, CUSTOMER_CHURN_DOC),
    report(
        "receipts_payments",
        (ReceiptsPaymentsIn, ReceiptsPaymentsOut),
        RECEIPTS_PAYMENTS_SUMMARY,
        RECEIPTS_PAYMENTS_DOC,
    ),
    report("notes_due", (NotesDueIn, NotesDueOut), NOTES_DUE_SUMMARY, NOTES_DUE_DOC),
    report("cash_flow", (CashFlowIn, CashFlowOut), CASH_FLOW_SUMMARY, CASH_FLOW_DOC),
    report("close_check", (CloseCheckIn, CloseCheckOut), CLOSE_CHECK_SUMMARY, CLOSE_CHECK_DOC),
    report("ia_anomalies", (IaAnomaliesIn, IaAnomaliesOut), IA_ANOMALIES_SUMMARY, IA_ANOMALIES_DOC),
    report("cost_unit", (CostUnitIn, CostUnitOut), COST_UNIT_SUMMARY, COST_UNIT_DOC),
)
