using System;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.InteropServices;

namespace U8Co
{
    // 经营管理销售分析 reports/mgmt/sales（读线程，只用 ctx.Conn）：期间内按客户、存货、业务员、部门、期间的任意组合
    // 汇总销售发票收入与存货核算销售成本，算毛利、毛利率；按收入从高到低取前 top 组，其余并成 others，totals 是全部组的合计。
    // 不翻页（聚合结果）。账套没有启用销售管理（SA 选项 dSaleStartDate 为空）时 items 为空、sa_enabled=false；
    // 收入以发票为准，与总账 6001 可能有期间、口径差（非销售发票收入、跨期复核），利润以总账为准（mgmt/pnl）。
    // 期间按 U8 会计期间（UFSYSTEM..UA_Period）换成发票日期窗口（period_source=u8），不连续或无权读系统库时按自然月
    // （period_source=calendar，无权读时另给 warnings）；其他数据库错误照常报错。
    internal static class ReportsMgmtSales
    {
        // 分组数上限：超过时 400，提示减少维度或缩短期间（聚合结果不翻页）。
        internal const int MaxGroups = 20000;

        static readonly string[] Measures = new string[] { "qty", "revenue", "revenue_tax_incl", "cost_qty", "cogs" };
        static readonly string[] Cols = new string[] { "qty", "rev", "rev_tax", "cost_qty", "cogs" };

        public static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            MgmtSalesArgs m = ReportsMgmtSalesReq.Parse(ctx.Item.Body);
            Dictionary<string, object> flags = Rows.One(ctx.Conn, ReportsMgmtSalesSql.ModulesSql, new object[0]);
            bool sa = Reports.Text(flags, "sa") != null;
            string source;
            string warning;
            string[] bounds = Bounds(ctx, a.FiscalYear, m.From, m.To, out source, out warning);
            List<Group> groups = new List<Group>();
            if (sa)
            {
                List<object> ps = new List<object>();
                string sql = ReportsMgmtSalesSql.Query(ctx, m, a.FiscalYear, bounds, ps);
                List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), MaxGroups + 1);
                if (rows.Count > MaxGroups)
                {
                    throw GlReq.Bad("分组超过 " + MaxGroups.ToString(CultureInfo.InvariantCulture) + " 组，请减少 group_by 维度或缩短期间",
                        "group_by");
                }
                groups = ToGroups(rows, m);
            }
            Dictionary<string, object> body = Head(a, m, bounds, source);
            body["sa_enabled"] = sa;
            body["ia_enabled"] = Reports.Text(flags, "ia") != null;
            AddWarning(body, warning);
            Fill(body, groups, m.Top);
            return ApiResult.Ok(body);
        }

        // 各期间起始日（yyyy-MM-dd），末尾再加 period_to 的次日；共 To-From+2 个。source：u8 / calendar；
        // warning 只在读不到 UFSYSTEM..UA_Period（无权限、库或表不存在）时给出，否则为 null。
        internal static string[] Bounds(WorkContext ctx, int year, int from, int to, out string source, out string warning)
        {
            warning = null;
            try
            {
                object[] args = new object[] { ctx.Item.Acc ?? "", year, from, to };
                List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, ReportsMgmtSalesSql.PeriodSql, args, 13);
                string[] found = FromPeriods(rows, from, to);
                if (found != null)
                {
                    source = "u8";
                    return found;
                }
            }
            catch (COMException)
            {
                if (!SysUnreadable(ctx.Conn))
                {
                    throw;
                }
                warning = "读不到 U8 系统库的会计期间（UFSYSTEM..UA_Period，无权限或不存在），期间按自然月计算";
            }
            source = "calendar";
            return CalendarBounds(year, from, to);
        }

        // 读系统库失败的 SQL Server 错误号：229 无权限、208 对象不存在、916 无权访问该库、4060 打不开该库。
        static readonly int[] Unreadable = new int[] { 229, 208, 916, 4060 };

        // 看 ADO 连接的 Errors 里有没有上面的错误号（读不到错误集合时按其他错误处理）。
        static bool SysUnreadable(object conn)
        {
            object errors = null;
            try
            {
                errors = ComUtil.Get(conn, "Errors");
                int count = Convert.ToInt32(ComUtil.Get(errors, "Count"), CultureInfo.InvariantCulture);
                for (int i = 0; i < count; i++)
                {
                    if (Array.IndexOf(Unreadable, NativeError(errors, i)) >= 0)
                    {
                        return true;
                    }
                }
                return false;
            }
            catch (COMException)
            {
                return false;
            }
            finally
            {
                ComUtil.ReleaseOne(errors);
            }
        }

        static int NativeError(object errors, int index)
        {
            object item = ComUtil.Call(errors, "Item", new object[] { index });
            try
            {
                return Convert.ToInt32(ComUtil.Get(item, "NativeError"), CultureInfo.InvariantCulture);
            }
            finally
            {
                ComUtil.ReleaseOne(item);
            }
        }

        internal static void AddWarning(Dictionary<string, object> body, string warning)
        {
            if (warning != null)
            {
                body["warnings"] = new List<object> { warning };
            }
        }

        // U8 会计期间要逐期连续（下期起始日 = 本期截止日次日），否则按自然月。
        internal static string[] FromPeriods(List<Dictionary<string, object>> rows, int from, int to)
        {
            if (rows == null || rows.Count != to - from + 1)
            {
                return null;
            }
            string[] bounds = new string[rows.Count + 1];
            DateTime last = DateTime.MinValue;
            for (int i = 0; i < rows.Count; i++)
            {
                DateTime begin;
                DateTime end;
                if (!PeriodRow(rows[i], from + i, out begin, out end) || (i > 0 && begin != last.AddDays(1)))
                {
                    return null;
                }
                bounds[i] = Ymd(begin);
                last = end;
            }
            bounds[rows.Count] = Ymd(last.AddDays(1));
            return bounds;
        }

        static bool PeriodRow(Dictionary<string, object> row, int period, out DateTime begin, out DateTime end)
        {
            begin = DateTime.MinValue;
            end = DateTime.MinValue;
            return GlSql.Int(row, "p") == period && Day(GlSql.Col(row, "b"), out begin) && Day(GlSql.Col(row, "e"), out end)
                && end >= begin;
        }

        internal static string[] CalendarBounds(int year, int from, int to)
        {
            string[] bounds = new string[to - from + 2];
            for (int p = from; p <= to + 1; p++)
            {
                bounds[p - from] = Ymd(new DateTime(year, 1, 1).AddMonths(p - 1));
            }
            return bounds;
        }

        static bool Day(string text, out DateTime day)
        {
            return DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day);
        }

        static string Ymd(DateTime day)
        {
            return day.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        static Dictionary<string, object> Head(ReportArgs a, MgmtSalesArgs m, string[] bounds, string source)
        {
            Dictionary<string, object> body = Reports.Body();
            body["fiscal_year"] = a.FiscalYear;
            body["period_from"] = m.From;
            body["period_to"] = m.To;
            body["date_from"] = bounds[0];
            DateTime end;
            Day(bounds[bounds.Length - 1], out end);
            body["date_to"] = Ymd(end.AddDays(-1));
            body["period_source"] = source;
            body["group_by"] = new List<object>(m.GroupBy);
            body["top"] = m.Top;
            body["include_unverified"] = m.Unverified;
            return body;
        }

        // 一组：分组键与名称（输出字段）加五个度量（收入、成本已在 SQL 里四舍五入到分）。
        internal sealed class Group
        {
            public Dictionary<string, object> Item = new Dictionary<string, object>();
            public decimal[] Sums = new decimal[5];
        }

        static List<Group> ToGroups(List<Dictionary<string, object>> rows, MgmtSalesArgs m)
        {
            List<Group> groups = new List<Group>(rows.Count);
            for (int i = 0; i < rows.Count; i++)
            {
                Group g = new Group();
                for (int d = 0; d < m.GroupBy.Length; d++)
                {
                    PutKey(g.Item, rows[i], m.GroupBy[d]);
                }
                for (int k = 0; k < Cols.Length; k++)
                {
                    g.Sums[k] = k == 0 || k == 3 ? Reports.Qty(rows[i], Cols[k]) : GlSql.Money(rows[i], Cols[k]);
                }
                if (Array.Exists(g.Sums, delegate(decimal v) { return v != 0m; }))
                {
                    groups.Add(g);
                }
            }
            return groups;
        }

        static void PutKey(Dictionary<string, object> item, Dictionary<string, object> row, string dim)
        {
            if (dim == "period")
            {
                item["period"] = GlSql.Int(row, "k_period");
                return;
            }
            item[dim + "_code"] = Reports.Text(row, "k_" + dim);
            item[dim + "_name"] = Reports.Text(row, "n_" + dim);
            if (dim == "inventory")
            {
                item["inventory_spec"] = Reports.Text(row, "s_inventory");
            }
        }

        // 按收入降序（同收入按成本降序，再按原顺序）取前 top 组；其余并成 others（没有剩余时为 null）。
        internal static void Fill(Dictionary<string, object> body, List<Group> groups, int top)
        {
            List<KeyValuePair<int, Group>> order = new List<KeyValuePair<int, Group>>();
            for (int i = 0; i < groups.Count; i++)
            {
                order.Add(new KeyValuePair<int, Group>(i, groups[i]));
            }
            order.Sort(Compare);
            List<object> rows = new List<object>();
            decimal[] rest = new decimal[5];
            decimal[] all = new decimal[5];
            for (int i = 0; i < order.Count; i++)
            {
                Group g = order[i].Value;
                Add(all, g.Sums);
                if (i < top)
                {
                    rows.Add(Measure(g.Item, g.Sums));
                    continue;
                }
                Add(rest, g.Sums);
            }
            body["items"] = rows;
            body["others"] = order.Count > top ? Summary(rest, order.Count - top) : null;
            body["totals"] = Summary(all, order.Count);
        }

        static int Compare(KeyValuePair<int, Group> x, KeyValuePair<int, Group> y)
        {
            int c = y.Value.Sums[1].CompareTo(x.Value.Sums[1]);
            if (c == 0)
            {
                c = y.Value.Sums[4].CompareTo(x.Value.Sums[4]);
            }
            return c != 0 ? c : x.Key.CompareTo(y.Key);
        }

        static void Add(decimal[] into, decimal[] sums)
        {
            for (int k = 0; k < sums.Length; k++)
            {
                into[k] += sums[k];
            }
        }

        static Dictionary<string, object> Summary(decimal[] sums, int count)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["groups"] = count;
            return Measure(item, sums);
        }

        // 毛利 = 收入 - 成本；毛利率（%，两位小数）= 毛利 / 收入，收入为 0 时 null。
        static Dictionary<string, object> Measure(Dictionary<string, object> item, decimal[] sums)
        {
            for (int k = 0; k < Measures.Length; k++)
            {
                item[Measures[k]] = sums[k];
            }
            decimal gross = sums[1] - sums[4];
            item["gross"] = gross;
            item["gross_pct"] = sums[1] == 0m ? null : (object)decimal.Round(gross * 100m / sums[1], 2, MidpointRounding.AwayFromZero);
            return item;
        }
    }
}
