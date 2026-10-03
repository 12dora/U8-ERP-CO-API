using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // reports/mgmt/pnl：损益科目按期间（× 一级科目或末级科目 × 可选部门 / 项目）的借贷发生额，去掉期间损益结转凭证
    // （规则见 ReportsMgmtPnlSql）。net_income = 贷方 − 借方，收入类为正、成本费用类为负；normal 是科目的余额方向，
    // 归到哪一行利润表、怎么取符号由调用方决定。另给每个期间的结账状态、未记账凭证张数、是否已做期间损益结转。
    // 聚合结果不翻页，超过 MaxRows 行 400。读线程，只用 ctx.Conn 和权限快照。
    internal static class ReportsMgmtPnl
    {
        public static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            MgmtGlArgs m = ReportsMgmtGlReq.Parse(a.Name, ctx.Item.Body);
            List<object> ps = new List<object>();
            string sql = ReportsMgmtPnlSql.Query(m, a.FiscalYear, ps, ctx);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), ReportsMgmtGlReq.MaxRows + 1);
            if (rows.Count > ReportsMgmtGlReq.MaxRows)
            {
                throw GlReq.Bad("结果超过 " + ReportsMgmtGlReq.MaxRows.ToString(CultureInfo.InvariantCulture)
                    + " 行，请缩小期间范围、改用 detail=prefix4 或减少 dims");
            }
            List<object> items = new List<object>(rows.Count);
            for (int i = 0; i < rows.Count; i++)
            {
                items.Add(Item(rows[i], m));
            }
            Dictionary<string, object> body = Reports.Body();
            body["fiscal_year"] = a.FiscalYear;
            body["period_from"] = m.From;
            body["period_to"] = m.To;
            body["detail"] = m.Detail;
            body["dims"] = Dims(m);
            body["include_unposted"] = m.Unposted;
            body["pl_accounts"] = new List<object>(m.PlAccounts);
            body["profit_account"] = m.ProfitAccount;
            body["periods"] = Periods(ctx, a.FiscalYear, m.From, m.To);
            body["items"] = items;
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> Item(Dictionary<string, object> row, MgmtGlArgs m)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["period"] = GlSql.Int(row, "period");
            item["code"] = GlSql.Col(row, "code");
            item["name"] = Reports.Text(row, "name");
            item["normal"] = GlSql.Int(row, "bproperty") != 0 ? "debit" : "credit";
            if (m.ByDept)
            {
                item["dept_code"] = Reports.Text(row, "dept");
                item["dept_name"] = Reports.Text(row, "dept_name");
            }
            if (m.ByItem)
            {
                item["item_class"] = Reports.Text(row, "item_class");
                item["item_code"] = Reports.Text(row, "item");
            }
            decimal debit = GlSql.Money(row, "debit");
            decimal credit = GlSql.Money(row, "credit");
            item["debit"] = debit;
            item["credit"] = credit;
            item["net_income"] = credit - debit;
            item["posted"] = GlSql.Int(row, "posted") != 0;
            return item;
        }

        // 期间的结账状态与未记账张数（GL_mend 没有该期间的行时不列）。meta、cash_stock 也用。
        internal static List<object> Periods(WorkContext ctx, int year, int from, int to)
        {
            object[] args = new object[] { year, from, to };
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, ReportsMgmtPnlSql.PeriodsSql, args, 12);
            List<object> list = new List<object>(rows.Count);
            for (int i = 0; i < rows.Count; i++)
            {
                Dictionary<string, object> p = new Dictionary<string, object>();
                p["period"] = GlSql.Int(rows[i], "period");
                p["gl_closed"] = GlSql.Int(rows[i], "gl_closed") != 0;
                p["unposted"] = GlSql.Int(rows[i], "unposted");
                p["pl_transferred"] = GlSql.Int(rows[i], "transferred") != 0;
                list.Add(p);
            }
            return list;
        }

        static List<object> Dims(MgmtGlArgs m)
        {
            List<object> dims = new List<object>();
            if (m.ByDept)
            {
                dims.Add("dept");
            }
            if (m.ByItem)
            {
                dims.Add("item");
            }
            return dims;
        }
    }
}
