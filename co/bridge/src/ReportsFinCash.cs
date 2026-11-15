using System.Collections.Generic;

namespace U8Co
{
    // 现金流量统计 cash_flow：按现金流量项目（fitemss98，直接法）汇总凭证上指定的项目（GL_CashTable），分会计期间。
    // 项目挂在凭证的对方科目分录上：流入类项目取贷方减借方，流出类项目取借方减贷方，所以流入、流出都以正数表示正常的收入、支出，
    // 冲销、红字和汇兑调整出现负数。direction 取项目档案的「流入 / 流出」（in / out）；项目不在档案里时 direction 为 null，
    // 金额按贷方减借方。class_code、class_name 是一级分类（经营活动、投资活动、筹资活动、汇率变动、现金及现金等价物）。
    // 缺省只含已记账凭证，作废凭证不算；include_unposted 为 true 时连未记账凭证一起算。
    // 只列范围内有数据的项目；periods 列出 period_from 到 period_to 每个期间（没有数据的期间为 0），amount 是各期之和。
    // unassigned：范围内现金账户（1001、1002、1012）有净流入或净流出、却没有指定任何项目的凭证张数和金额（每张凭证取净额的绝对值）。
    // period_to 缺省：会计年度是登录年度时取登录月份，否则取 12。
    internal static class ReportsFinCash
    {
        public static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            FinCashArgs q = ReportsFinParse.Cash(ctx.Item.Body);
            int year = a.FiscalYear;
            int to = q.To > 0 ? q.To : DefaultTo(ctx, year);
            if (q.From > to)
            {
                throw GlReq.Bad("period_from 不能大于 period_to（period_to 缺省为登录月份）", "period_from");
            }
            List<object> ps = new List<object>();
            string sql = ReportsFinCashSql.Items(q.Unposted, year, q.From, to, ps);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), 5000);
            Dictionary<string, object> body = Reports.Body();
            body["fiscal_year"] = year;
            body["period_from"] = q.From;
            body["period_to"] = to;
            body["include_unposted"] = q.Unposted;
            body["items"] = Fold(rows, q.From, to);
            body["unassigned"] = Unassigned(ctx, q, year, to);
            return ApiResult.Ok(body);
        }

        static int DefaultTo(WorkContext ctx, int year)
        {
            if (GlState.LoginYear(ctx) != year)
            {
                return 12;
            }
            return ReportsFinParse.MonthOf(GlState.LoginDate(ctx));
        }

        static Dictionary<string, object> Unassigned(WorkContext ctx, FinCashArgs q, int year, int to)
        {
            List<object> ps = new List<object>();
            string sql = ReportsFinCashSql.Unassigned(q.Unposted, year, q.From, to, ps);
            Dictionary<string, object> row = Rows.One(ctx.Conn, sql, ps.ToArray());
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["vouchers"] = GlSql.Int(row, "vouchers");
            map["amount"] = GlSql.Money(row, "amount");
            return map;
        }

        // 把（项目，期间）行折成项目列表：按项目编码排序，每个项目带分期金额和合计。行已按项目、期间排好序，这里不依赖。
        internal static List<object> Fold(List<Dictionary<string, object>> rows, int from, int to)
        {
            SortedDictionary<string, Dictionary<string, object>> items =
                new SortedDictionary<string, Dictionary<string, object>>(System.StringComparer.Ordinal);
            Dictionary<string, decimal[]> sums = new Dictionary<string, decimal[]>();
            for (int i = 0; i < rows.Count; i++)
            {
                string code = GlSql.Col(rows[i], "code");
                if (!items.ContainsKey(code))
                {
                    items[code] = Head(rows[i]);
                    sums[code] = new decimal[13];
                }
                int period = GlSql.Int(rows[i], "per");
                if (period >= 1 && period <= 12)
                {
                    sums[code][period] += Signed(items[code]["direction"] as string, GlSql.Money(rows[i], "md"), GlSql.Money(rows[i], "mc"));
                }
            }
            List<object> list = new List<object>();
            foreach (KeyValuePair<string, Dictionary<string, object>> pair in items)
            {
                Finish(pair.Value, sums[pair.Key], from, to);
                list.Add(pair.Value);
            }
            return list;
        }

        static Dictionary<string, object> Head(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["code"] = GlSql.Col(row, "code");
            item["name"] = Reports.Text(row, "name");
            item["class_code"] = Reports.Text(row, "class_code");
            item["class_name"] = Reports.Text(row, "class_name");
            item["direction"] = DirectionOf(GlSql.Col(row, "direction"));
            return item;
        }

        // 档案里是「流入」「流出」；其他（含项目不在档案里）为 null。
        internal static string DirectionOf(string text)
        {
            if (text == "流入")
            {
                return "in";
            }
            return text == "流出" ? "out" : null;
        }

        // 流出类取借方减贷方，其余（流入类和未知）取贷方减借方。
        internal static decimal Signed(string direction, decimal debit, decimal credit)
        {
            return direction == "out" ? debit - credit : credit - debit;
        }

        static void Finish(Dictionary<string, object> item, decimal[] sums, int from, int to)
        {
            Dictionary<string, object> periods = new Dictionary<string, object>();
            decimal total = 0m;
            for (int p = from; p <= to; p++)
            {
                decimal value = decimal.Round(sums[p], 2, System.MidpointRounding.AwayFromZero);
                periods[p.ToString(System.Globalization.CultureInfo.InvariantCulture)] = value;
                total += value;
            }
            item["amount"] = total;
            item["periods"] = periods;
        }
    }
}
