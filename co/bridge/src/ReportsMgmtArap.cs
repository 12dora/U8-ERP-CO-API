using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 经营管理往来账期 reports/mgmt/arap_terms（读线程，只用 ctx.Conn）：按往来单位给出截至 as_of 的余额、预收付、
    // 按到期日的账龄与逾期、未结票据，近 12 个月原始单据的信用期分布、回款（付款）天数（金额加权的平均数与中位数）。
    // 按 |余额| 从大到小取前 top 个单位，其余并成 others，totals 是全部单位的合计。不翻页（聚合结果）。
    // 没有启用应收（应付）款管理（AR dARStartDate / AP dAPStartDate 为空）时 partners 为空、enabled=false，不查明细。
    // SQL 见 ReportsMgmtArapSql；账龄口径与 arap_aging basis=due 相同（到期日：收付款日期，否则信用起始日 + 信用期，
    // 都没有时单据日期 + default_credit_days）。
    internal static class ReportsMgmtArap
    {
        // 一个单位（或合计）：余额、预收付、逾期、账龄各段、票据，近 12 个月单据数与金额、信用期分布、已核销天数分布。
        internal sealed class Partner
        {
            public string Code;
            public string Name;
            public decimal Balance;
            public decimal Prepaid;
            public decimal Overdue;
            public decimal[] Aging;
            public decimal Notes;
            public int Count;
            public decimal Amount;
            public decimal Unpaid;
            public SortedDictionary<int, decimal[]> Terms = new SortedDictionary<int, decimal[]>();
            public SortedDictionary<int, decimal> Days = new SortedDictionary<int, decimal>();

            public Partner(int buckets)
            {
                Aging = new decimal[buckets + 2];
            }
        }

        public static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            MgmtArapArgs m = ReportsMgmtArapReq.Parse(ctx.Item.Body);
            ReportArgs g = m.Aging;
            g.AsOf = a.AsOf;
            string[] flag = g.Side == "ap" ? new string[] { "AP", "dAPStartDate" } : new string[] { "AR", "dARStartDate" };
            string start = Rows.Scalar(ctx.Conn, ReportsMgmtArapSql.EnabledSql, new object[] { flag[0], flag[1] });
            bool enabled = start != null && start.Trim().Length > 0;
            Dictionary<string, Partner> map = new Dictionary<string, Partner>(StringComparer.Ordinal);
            if (enabled)
            {
                Collect(ctx, g, map);
            }
            Dictionary<string, object> body = Head(g, m.Top);
            body["enabled"] = enabled;
            Fill(body, new List<Partner>(map.Values), g.Buckets.Length, m.Top);
            return ApiResult.Ok(body);
        }

        static void Collect(WorkContext ctx, ReportArgs g, Dictionary<string, Partner> map)
        {
            List<object> ps = new List<object>();
            string sql = ReportsMgmtArapSql.Aging(ctx, g, ps);
            foreach (Dictionary<string, object> row in Rows.Query(ctx.Conn, sql, ps.ToArray(), int.MaxValue))
            {
                Partner p = Of(map, row, g);
                p.Balance += GlSql.Money(row, "bal");
                p.Prepaid += GlSql.Money(row, "pre");
                p.Overdue += GlSql.Money(row, "ovd");
                for (int b = 0; b < p.Aging.Length; b++)
                {
                    p.Aging[b] += GlSql.Money(row, "b" + ReportsMgmtArapSql.Idx(b));
                }
            }
            ps = new List<object>();
            sql = ReportsMgmtArapSql.History(ctx, g, ps);
            foreach (Dictionary<string, object> row in Rows.Query(ctx.Conn, sql, ps.ToArray(), int.MaxValue))
            {
                AddHistory(Of(map, row, g), row);
            }
            ps = new List<object>();
            sql = ReportsMgmtArapSql.Notes(ctx, g, ps);
            foreach (Dictionary<string, object> row in Rows.Query(ctx.Conn, sql, ps.ToArray(), int.MaxValue))
            {
                Of(map, row, g).Notes += GlSql.Money(row, "amt");
            }
        }

        static Partner Of(Dictionary<string, Partner> map, Dictionary<string, object> row, ReportArgs g)
        {
            string code = GlSql.Col(row, "partner");
            Partner p;
            if (!map.TryGetValue(code, out p))
            {
                p = new Partner(g.Buckets.Length);
                p.Code = code;
                map.Add(code, p);
            }
            if (p.Name == null)
            {
                p.Name = Reports.Text(row, "pname");
            }
            return p;
        }

        // 一行是（单位、信用期、核销天数）的单据数与金额；天数为空表示到 as_of 还没有核销。
        internal static void AddHistory(Partner p, Dictionary<string, object> row)
        {
            int count = GlSql.Int(row, "cnt");
            decimal amount = GlSql.Money(row, "amt");
            AddTerm(p.Terms, GlSql.Int(row, "cp"), count, amount);
            p.Count += count;
            p.Amount += amount;
            if (GlSql.Col(row, "days").Length == 0)
            {
                p.Unpaid += amount;
                return;
            }
            AddDays(p.Days, GlSql.Int(row, "days"), amount);
        }

        static void AddTerm(SortedDictionary<int, decimal[]> terms, int days, decimal count, decimal amount)
        {
            decimal[] slot;
            if (!terms.TryGetValue(days, out slot))
            {
                slot = new decimal[2];
                terms.Add(days, slot);
            }
            slot[0] += count;
            slot[1] += amount;
        }

        static void AddDays(SortedDictionary<int, decimal> days, int key, decimal amount)
        {
            decimal sum;
            days.TryGetValue(key, out sum);
            days[key] = sum + amount;
        }

        // 近 12 个月窗口的起点：as_of 次日往前 12 个月（与 ReportsMgmtArapSql.HistorySql 的 DATEADD 一致）。
        internal static string HistoryFrom(string asOf)
        {
            DateTime day;
            if (!DateTime.TryParseExact(asOf, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
            {
                return null;
            }
            return day.AddDays(1).AddMonths(-12).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        static Dictionary<string, object> Head(ReportArgs g, int top)
        {
            Dictionary<string, object> body = Reports.Body();
            body["side"] = g.Side;
            body["as_of"] = g.AsOf;
            body["accounts"] = new List<object>(g.Accounts);
            body["buckets"] = ReportsMgmtArapView.BucketInfo(g.Buckets);
            body["default_credit_days"] = g.DefaultCreditDays;
            body["history_from"] = HistoryFrom(g.AsOf);
            body["history_to"] = g.AsOf;
            body["top"] = top;
            return body;
        }

        // 按 |余额| 降序（同额按近 12 个月单据金额降序，再按编码）取前 top 个；全为 0 的单位不列。
        internal static void Fill(Dictionary<string, object> body, List<Partner> all, int buckets, int top)
        {
            List<Partner> list = all.FindAll(delegate(Partner p) { return !ReportsMgmtArapView.Empty(p); });
            list.Sort(Compare);
            List<object> rows = new List<object>();
            Partner rest = new Partner(buckets);
            Partner total = new Partner(buckets);
            for (int i = 0; i < list.Count; i++)
            {
                ReportsMgmtArapView.Merge(total, list[i]);
                if (i < top)
                {
                    rows.Add(ReportsMgmtArapView.Item(list[i], true));
                    continue;
                }
                ReportsMgmtArapView.Merge(rest, list[i]);
            }
            body["partners"] = rows;
            body["others"] = list.Count > top ? ReportsMgmtArapView.Summary(rest, list.Count - top) : null;
            body["totals"] = ReportsMgmtArapView.Summary(total, list.Count);
        }

        static int Compare(Partner x, Partner y)
        {
            int c = Math.Abs(y.Balance).CompareTo(Math.Abs(x.Balance));
            if (c == 0)
            {
                c = y.Amount.CompareTo(x.Amount);
            }
            return c != 0 ? c : string.CompareOrdinal(x.Code, y.Code);
        }
    }
}
