using System.Collections.Generic;

namespace U8Co
{
    // 月末结账检查 close_check：各模块在该期间是否已结账（GL_mend），以及没结账的模块还有多少单据挡着结账。
    // 检查项的条件同 periods/close 的结账前检查（PeriodCloseSql、PeriodStockSql.Unaudited），这里数个数、取最多 5 个示例单号。
    // 只列还没结账的模块相关的检查项（全部结账则 checks 为空），个数为 0 的也列出。
    // 应收、应付的「未制单」只有在往来选项要求月末结账前全部制单时才挡结账，这里一律列出个数。
    // 库存单据和发货单、销售发票按「单据日期在本期」数，采购发票按「日期在本期末及以前」数（同 U8 采购结账）。
    // period 缺省：会计年度是登录年度时取登录月份，否则取 12。
    internal static class ReportsAcctClose
    {
        public static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            AcctCloseArgs q = ReportsAcctParse.Close(ctx.Item.Body);
            int year = a.FiscalYear;
            int period = q.Period > 0 ? q.Period : ReportsAcctParse.DefaultPeriod(ctx, year);
            Dictionary<string, object> closed = Closed(ctx, year, period);
            List<object> checks = new List<object>();
            for (int i = 0; i < ReportsAcctCheckSql.All.Length; i++)
            {
                AcctCheck check = ReportsAcctCheckSql.All[i];
                if (!IsClosed(closed, check.Module))
                {
                    checks.Add(Counted(check, ctx, year, period));
                }
            }
            Dictionary<string, object> body = Reports.Body();
            body["fiscal_year"] = year;
            body["period"] = period;
            body["closed"] = closed;
            body["checks"] = checks;
            return ApiResult.Ok(body);
        }

        // 该期间 GL_mend 没有行时各模块都按未结账。
        static Dictionary<string, object> Closed(WorkContext ctx, int year, int period)
        {
            Dictionary<string, object> row = Rows.One(ctx.Conn, ReportsAcctCheckSql.ClosedSql, new object[] { year, period });
            Dictionary<string, object> closed = new Dictionary<string, object>();
            for (int i = 0; i < ReportsAcctCheckSql.Modules.Length; i++)
            {
                string module = ReportsAcctCheckSql.Modules[i];
                closed[module] = row != null && GlSql.Int(row, module) != 0;
            }
            return closed;
        }

        static bool IsClosed(Dictionary<string, object> closed, string module)
        {
            object value;
            return closed.TryGetValue(module, out value) && value is bool && (bool)value;
        }

        static Dictionary<string, object> Counted(AcctCheck check, WorkContext ctx, int year, int period)
        {
            object[] args = ReportsAcctCheckSql.ArgsOf(check, year, period);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, check.Sql, args, ReportsAcctCheckSql.Samples);
            return Item(check, rows);
        }

        // 一项检查的结果：个数取第一行的 n（没有行就是 0），示例是各行的 code。纯函数，--selftest 用。
        internal static Dictionary<string, object> Item(AcctCheck check, List<Dictionary<string, object>> rows)
        {
            List<object> samples = new List<object>();
            for (int i = 0; i < rows.Count && i < ReportsAcctCheckSql.Samples; i++)
            {
                samples.Add(GlSql.Col(rows[i], "code"));
            }
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["module"] = check.Module;
            item["item"] = check.Item;
            item["count"] = rows.Count == 0 ? 0 : GlSql.Int(rows[0], "n");
            item["samples"] = samples;
            return item;
        }
    }
}
