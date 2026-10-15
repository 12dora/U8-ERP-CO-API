using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 存货核算检查 ia_anomalies：两张清单。
    // unbilled 暂估未回冲：已审核的采购入库单行（不含库存期初），日期在期末及以前，入库数量不等于已开票数量（iSumBillQuantity）；
    //   红字入库未开红字发票时未开票数量、估价为负。开票状态是查询时（登录日期 billing_as_of）的，不是期末当天的。
    //   估价 = 未开票数量 × 入库单无税单价，按估价从大到小列；count、est_amount 是全部行的个数和估价合计（不受 limit 影响）；
    //   days = 期末日 − 入库日期。采购发票还没到的正常入库也在其中。
    // unbooked 未记账单据：已审核的采购入库、其他入库、其他出库、产成品入库、材料出库、销售出库单行，日期在本期，
    //   存货核算（IA_Subsidiary）里没有行；数量为 0 的行、仓库不参与成本核算（bInCost = 0）的单据存货核算不记，不算。按日期从早到晚列；count 是全部行数。
    // 数据权限：供应商、存货、仓库（两张清单都按）。period 缺省：会计年度是登录年度时取登录月份，否则取 12。
    internal static class ReportsAcctIa
    {
        public static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            AcctIaArgs q = ReportsAcctParse.Ia(ctx.Item.Body);
            int year = a.FiscalYear;
            int period = q.Period > 0 ? q.Period : ReportsAcctParse.DefaultPeriod(ctx, year);
            string first = ReportsAcctParse.FirstDay(year, period);
            string next = ReportsAcctParse.NextDay(year, period);
            string last = ReportsAcctParse.LastDay(year, period);
            Dictionary<string, object> body = Reports.Body();
            body["fiscal_year"] = year;
            body["period"] = period;
            body["period_end"] = last;
            body["billing_as_of"] = GlState.LoginDate(ctx);
            body["unbilled"] = Unbilled(ctx, q.Limit, last, next);
            body["unbooked"] = Unbooked(ctx, q.Limit, first, next);
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> Unbilled(WorkContext ctx, int limit, string last, string next)
        {
            List<object> ps = new List<object>();
            ps.Add(limit);
            ps.Add(last);
            ps.Add(next);
            StringBuilder sql = new StringBuilder(ReportsAcctIaSql.Unbilled);
            PermHook.Where(sql, ps, ctx, "x");
            bool byDate = PermMask.WillMaskAmounts(ctx);
            sql.Append(byDate ? ReportsAcctIaSql.UnbilledOrderByDate : ReportsAcctIaSql.UnbilledOrder);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), ps.ToArray(), limit);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count; i++)
            {
                items.Add(UnbilledItem(rows[i]));
            }
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["count"] = rows.Count == 0 ? 0 : GlSql.Int(rows[0], "n");
            map["est_amount"] = rows.Count == 0 ? 0m : GlSql.Money(rows[0], "est_total");
            map["order"] = byDate ? "date" : "est_amount";
            map["items"] = items;
            return map;
        }

        static Dictionary<string, object> Unbooked(WorkContext ctx, int limit, string first, string next)
        {
            List<object> ps = new List<object>();
            ps.Add(limit);
            ReportsAcctIaSql.AddUnbookedDates(ps, first, next);
            StringBuilder sql = new StringBuilder(ReportsAcctIaSql.Unbooked);
            PermHook.Where(sql, ps, ctx, "x");
            sql.Append(ReportsAcctIaSql.UnbookedOrder);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), ps.ToArray(), limit);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count; i++)
            {
                items.Add(UnbookedItem(rows[i]));
            }
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["count"] = rows.Count == 0 ? 0 : GlSql.Int(rows[0], "n");
            map["items"] = items;
            return map;
        }

        // 纯函数，--selftest 用。
        internal static Dictionary<string, object> UnbilledItem(Dictionary<string, object> row)
        {
            decimal qty = Reports.Qty(row, "qty");
            decimal billed = Reports.Qty(row, "billed");
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["code"] = GlSql.Col(row, "code");
            item["date"] = GlSql.Col(row, "d");
            item["vendor_name"] = Reports.Text(row, "vname");
            item["inv_code"] = GlSql.Col(row, "inv_code");
            item["inv_name"] = Reports.Text(row, "iname");
            item["unit"] = Reports.Text(row, "unit");
            item["qty"] = qty;
            item["billed_qty"] = billed;
            item["residual_qty"] = qty - billed;
            item["est_amount"] = GlSql.Money(row, "est");
            item["days"] = GlSql.Int(row, "days");
            return item;
        }

        internal static Dictionary<string, object> UnbookedItem(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["type"] = GlSql.Col(row, "typ");
            item["code"] = GlSql.Col(row, "code");
            item["date"] = GlSql.Col(row, "d");
            item["wh_name"] = Reports.Text(row, "wname");
            item["inv_code"] = GlSql.Col(row, "inv_code");
            item["inv_name"] = Reports.Text(row, "iname");
            item["unit"] = Reports.Text(row, "unit");
            item["qty"] = Reports.Qty(row, "qty");
            return item;
        }
    }
}
