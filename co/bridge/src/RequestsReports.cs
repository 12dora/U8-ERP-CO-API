using System;
using System.Collections.Generic;

namespace U8Co
{
    // 只读报表 reports/<name>：不带 type、不带 id；字段取值由 ReportsReq 在登录前校验。
    internal static partial class Requests
    {
        internal const string ReportRoot = "/u8co/v1/reports/";

        static readonly string[][] ReportSpecs = new string[][]
        {
            new string[] { ReportRoot + "close_status", "fiscal_year" },
            new string[]
            {
                ReportRoot + "gl_balance", "fiscal_year", "period_from", "period_to", "grade_from", "grade_to",
                "code_prefix", "leaf_only", "include_unposted", "nonzero", "after", "limit"
            },
            new string[]
            {
                ReportRoot + "gl_aux_balance", "dim", "fiscal_year", "period_from", "period_to", "code_prefix",
                "dim_code", "project_class", "nonzero", "after", "limit"
            },
            new string[]
            {
                ReportRoot + "arap_balance", "side", "as_of", "accounts", "exclude_accounts", "partner", "nonzero",
                "after", "limit"
            },
            new string[]
            {
                ReportRoot + "arap_aging", "side", "as_of", "accounts", "exclude_accounts", "partner", "nonzero",
                "after", "limit", "basis", "buckets", "group_by", "person", "overdue_only", "default_credit_days"
            },
            new string[] { ReportRoot + "bom", "parent", "as_of", "levels", "limit" },
            new string[]
            {
                ReportRoot + "arap_detail", "side", "partner", "date_from", "date_to", "basis", "accounts",
                "exclude_accounts", "dept", "person", "include_writeoff", "after", "limit"
            },
            new string[]
            {
                ReportRoot + "gl_detail", "code", "include_sub", "fiscal_year", "period_from", "period_to", "date_from",
                "date_to", "include_unposted", "customer", "vendor", "dept", "person", "project", "project_class",
                "after", "limit"
            },
            new string[]
            {
                ReportRoot + "order_execution", "type", "ids", "code", "date_from", "date_to", "partner", "only_open",
                "after", "limit"
            },
            new string[] { ReportRoot + "doc_trace", "type", "id", "depth", "direction", "max_nodes" },
            new string[]
            {
                ReportRoot + "stock_ledger", "inv", "wh", "batch", "date_from", "date_to", "include_unverified",
                "after", "limit"
            },
            new string[]
            {
                ReportRoot + "stock_summary", "date_from", "date_to", "wh", "inv", "inv_class", "by_wh",
                "include_unverified", "nonzero", "after", "limit"
            },
            new string[] { ReportRoot + "position_stock", "wh", "inv", "batch", "position", "nonzero", "after", "limit" },
            new string[] { ReportRoot + "batch_stock", "wh", "inv", "batch", "expiring_before", "nonzero", "after", "limit" },
            new string[] { ReportRoot + "customer_credit", "customer", "controlled_only", "after", "limit" },
            new string[]
            {
                ReportRoot + "price_list", "kind", "customer", "vendor", "inv", "as_of", "all_dates", "after", "limit"
            },
            // 期初余额（ReportsOpeningReq）：各 module 能带哪些字段在那里再查。
            new string[]
            {
                ReportRoot + ReportsOpeningReq.Name, "module", "side", "fiscal_year", "wh", "inv", "batch", "partner",
                "code_prefix", "leaf_only", "dim", "nonzero", "after", "limit"
            },
            // 核销记录（ReportsArapWriteoffReq）。
            new string[]
            {
                ReportRoot + ReportsArapWriteoffReq.Name, "flag", "partner", "receipt", "receipt_code", "target",
                "target_code", "date_from", "date_to", "cancel_no", "after", "limit"
            },
            // 账套体检（ReportsReadinessReq）：不收 fiscal_year、after。
            new string[] { ReportRoot + ReportsReadinessReq.Name, "as_of" },
            // 固定资产变动单、折旧（ReportsFaReq）。
            new string[]
            {
                ReportRoot + ReportsFaReq.ChangesName, "fiscal_year", "period", "card", "code", "change_type", "after", "limit"
            },
            new string[] { ReportRoot + ReportsFaReq.DeprName, "fiscal_year", "period", "card", "nonzero", "after", "limit" },
            // 经营管理（总账口径，ReportsMgmtGlReq）：聚合结果，不收 after、limit。
            new string[]
            {
                ReportRoot + ReportsMgmtGlReq.PnlName, "fiscal_year", "period_from", "period_to", "include_unposted", "detail",
                "dims", "pl_accounts", "profit_account"
            },
            new string[] { ReportRoot + ReportsMgmtGlReq.MetaName, "fiscal_year" },
            new string[]
            {
                ReportRoot + ReportsMgmtGlReq.CashName, "fiscal_year", "period", "cash_accounts", "notes_accounts", "top",
                "purchase_source", "include_unverified"
            },
            // 经营管理销售分析、往来账期（ReportsMgmtSalesReq、ReportsMgmtArapReq）：聚合结果，不收 after、limit。
            new string[]
            {
                ReportRoot + ReportsMgmtSalesReq.Name, "fiscal_year", "period_from", "period_to", "group_by", "top",
                "include_unverified"
            },
            new string[]
            {
                ReportRoot + ReportsMgmtArapReq.Name, "side", "as_of", "accounts", "buckets", "default_credit_days", "top"
            }
        };

        static bool IsReport(string path)
        {
            return path != null && path.StartsWith(ReportRoot, StringComparison.Ordinal);
        }

        static string ReportName(string path)
        {
            return path.Substring(ReportRoot.Length);
        }

        // 登录前校验参数，定登录子系统。口令密文已在 ApplyP4 里去掉。
        static void ApplyReport(Dictionary<string, object> body, WorkItem item, string path)
        {
            string name = ReportName(path);
            Reports.Check(name, body);
            item.SubId = Reports.SubOf(name, body);
        }
    }
}
