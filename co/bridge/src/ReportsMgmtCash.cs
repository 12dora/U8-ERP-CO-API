using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // reports/mgmt/cash_stock：一个会计期间的资金与存货概况（读线程，只用 ctx.Conn、账套号和权限快照）。
    // cash：货币资金末级科目的期末余额（GL_accsum，只含已记账）；notes_receivable：应收票据科目期末余额（total），另给应收票据
    // 当前的未处理余额（Ap_Note，查询时点）；inventory：存货核算汇总表该期间的结存数量、金额；production：产成品入库按存货
    // 的数量排行；purchases：采购按供应商的金额排行（auto：账套有采购发票时取发票，否则取采购入库）。排行取前 top 个，
    // 其余合到 others。日期类数据按该会计期间的起止日期（与 mgmt/sales 同一取法 ReportsMgmtSales.Bounds：UA_Period 去掉已删除
    // 期间、逐期连续才用，不连续或无权读系统库时按自然月，period_source 标明 u8 / calendar，无权读时另给 warnings）。
    // 数据权限按段过滤（ReportsMgmtCashSql.Perm）：科目、客户（未处理票据）、存货与仓库、供应商。
    internal static class ReportsMgmtCash
    {
        const int MaxLeaves = 2000;

        public static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            MgmtGlArgs m = ReportsMgmtGlReq.Parse(a.Name, ctx.Item.Body);
            int y = a.FiscalYear;
            string source;
            string warning;
            string[] range = PeriodRange(ReportsMgmtSales.Bounds(ctx, y, m.Period, m.Period, out source, out warning));
            Dictionary<string, object> body = Reports.Body();
            body["fiscal_year"] = y;
            body["period"] = m.Period;
            body["period_start"] = range[0];
            body["period_end"] = range[1];
            body["period_source"] = source;
            body["closed"] = Closed(ctx, y, m.Period);
            body["cash"] = Leaves(ctx, y, m.Period, m.CashAccounts);
            Dictionary<string, object> notes = Leaves(ctx, y, m.Period, m.NotesAccounts);
            notes["open"] = NotesOpen(ctx);
            body["notes_receivable"] = notes;
            body["inventory"] = Inventory(ctx, y, m.Period);
            body["production"] = Production(ctx, m, range);
            body["purchases"] = Purchases(ctx, m, range);
            body["include_unverified"] = m.Unverified;
            ReportsMgmtSales.AddWarning(body, warning);
            return ApiResult.Ok(body);
        }

        // ReportsMgmtSales.Bounds 的一期（起始日、次期起始日）→ 起止日期（止 = 次期起始日前一天）。
        internal static string[] PeriodRange(string[] bounds)
        {
            DateTime next = DateTime.ParseExact(bounds[1], "yyyy-MM-dd", CultureInfo.InvariantCulture);
            return new string[] { bounds[0], next.AddDays(-1).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) };
        }

        static Dictionary<string, object> Closed(WorkContext ctx, int year, int period)
        {
            Dictionary<string, object> row = Rows.One(ctx.Conn, ReportsMgmtCashSql.CloseSql, new object[] { year, period });
            Dictionary<string, object> closed = new Dictionary<string, object>();
            string[] modules = new string[] { "GL", "IA", "ST", "PU" };
            for (int i = 0; i < modules.Length; i++)
            {
                closed[modules[i]] = GlSql.Int(row, modules[i]) != 0;
            }
            return closed;
        }

        // 末级科目期末余额：items 每个科目一行，group 是它命中的前缀；total 为合计（借正贷负）。
        static Dictionary<string, object> Leaves(WorkContext ctx, int year, int period, string[] prefixes)
        {
            List<object> ps = new List<object>();
            string sql = ReportsMgmtCashSql.Balances(year, period, prefixes, ps, ctx);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), MaxLeaves);
            List<object> items = new List<object>(rows.Count);
            decimal total = 0m;
            for (int i = 0; i < rows.Count; i++)
            {
                string code = GlSql.Col(rows[i], "code");
                decimal balance = GlSql.Money(rows[i], "balance");
                Dictionary<string, object> item = new Dictionary<string, object>();
                item["code"] = code;
                item["name"] = Reports.Text(rows[i], "name");
                item["group"] = GroupOf(code, prefixes);
                item["balance"] = balance;
                items.Add(item);
                total += balance;
            }
            Dictionary<string, object> result = new Dictionary<string, object>();
            result["accounts"] = new List<object>(prefixes);
            result["items"] = items;
            result["total"] = total;
            return result;
        }

        static string GroupOf(string code, string[] prefixes)
        {
            for (int i = 0; i < prefixes.Length; i++)
            {
                if (code.StartsWith(prefixes[i], StringComparison.Ordinal))
                {
                    return prefixes[i];
                }
            }
            return null;
        }

        static Dictionary<string, object> NotesOpen(WorkContext ctx)
        {
            List<object> ps = new List<object>();
            string sql = ReportsMgmtCashSql.NotesOpen(ps, ctx);
            Dictionary<string, object> row = Rows.One(ctx.Conn, sql, ps.ToArray());
            Dictionary<string, object> open = new Dictionary<string, object>();
            open["as_of"] = "current";
            open["count"] = GlSql.Int(row, "n");
            open["amount"] = GlSql.Money(row, "amount");
            open["first_due"] = row == null ? null : Reports.Text(row, "first_due");
            return open;
        }

        static Dictionary<string, object> Inventory(WorkContext ctx, int year, int period)
        {
            List<object> ps = new List<object>();
            string sql = ReportsMgmtCashSql.Inventory(year, period, ps, ctx);
            Dictionary<string, object> row = Rows.One(ctx.Conn, sql, ps.ToArray());
            Dictionary<string, object> inv = new Dictionary<string, object>();
            inv["rows"] = GlSql.Int(row, "n");
            inv["qty"] = Reports.Qty(row, "qty");
            inv["amount"] = GlSql.Money(row, "amount");
            return inv;
        }

        static Dictionary<string, object> Purchases(WorkContext ctx, MgmtGlArgs m, string[] range)
        {
            bool invoice = m.PurchaseSource == "invoice"
                || (m.PurchaseSource == "auto" && Rows.One(ctx.Conn, ReportsMgmtCashSql.HasInvoiceSql, new object[0]) != null);
            List<object> topPs = new List<object>();
            List<object> allPs = new List<object>();
            string top = ReportsMgmtCashSql.Purchases(invoice, false, m, range, topPs, ctx);
            string all = ReportsMgmtCashSql.Purchases(invoice, true, m, range, allPs, ctx);
            Dictionary<string, object> result = Ranked(ctx, m.Top, new object[] { top, topPs, all, allPs }, true);
            result["source"] = invoice ? "invoice" : "receipt";
            return result;
        }

        static Dictionary<string, object> Production(WorkContext ctx, MgmtGlArgs m, string[] range)
        {
            List<object> topPs = new List<object>();
            List<object> allPs = new List<object>();
            string top = ReportsMgmtCashSql.Production(false, m, range, topPs, ctx);
            string all = ReportsMgmtCashSql.Production(true, m, range, allPs, ctx);
            return Ranked(ctx, m.Top, new object[] { top, topPs, all, allPs }, false);
        }

        // 排行与合计：items 是前 top 个，others 是其余的合计（只给个数、数量、金额的差额）。
        // q 是 { 排行 SQL, 其参数, 合计 SQL, 其参数 }。
        static Dictionary<string, object> Ranked(WorkContext ctx, int top, object[] q, bool money)
        {
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, (string)q[0], ((List<object>)q[1]).ToArray(), top);
            Dictionary<string, object> all = Rows.One(ctx.Conn, (string)q[2], ((List<object>)q[3]).ToArray());
            List<object> items = new List<object>(rows.Count);
            decimal qty = 0m;
            decimal amount = 0m;
            for (int i = 0; i < rows.Count; i++)
            {
                Dictionary<string, object> item = RankItem(rows[i], money);
                qty += (decimal)item["qty"];
                amount += money ? (decimal)item["amount"] : 0m;
                items.Add(item);
            }
            Dictionary<string, object> result = new Dictionary<string, object>();
            result["items"] = items;
            result["total_qty"] = Reports.Qty(all, "qty");
            result["count"] = GlSql.Int(all, "n");
            Dictionary<string, object> others = new Dictionary<string, object>();
            others["count"] = Math.Max(0, GlSql.Int(all, "n") - rows.Count);
            others["qty"] = Reports.Qty(all, "qty") - qty;
            if (money)
            {
                result["total_amount"] = GlSql.Money(all, "amount");
                result["total_amount_tax_incl"] = GlSql.Col(all, "amount_tax_incl").Length == 0 ? null
                    : (object)GlSql.Money(all, "amount_tax_incl");
                others["amount"] = GlSql.Money(all, "amount") - amount;
            }
            result["others"] = others;
            return result;
        }

        static Dictionary<string, object> RankItem(Dictionary<string, object> row, bool money)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["code"] = GlSql.Col(row, "code");
            item["name"] = Reports.Text(row, "name");
            if (money)
            {
                item["amount"] = GlSql.Money(row, "amount");
                item["amount_tax_incl"] = GlSql.Col(row, "amount_tax_incl").Length == 0 ? null
                    : (object)GlSql.Money(row, "amount_tax_incl");
            }
            else
            {
                item["unit"] = Reports.Text(row, "unit");
            }
            item["qty"] = Reports.Qty(row, "qty");
            item["docs"] = GlSql.Int(row, "docs");
            return item;
        }
    }
}
