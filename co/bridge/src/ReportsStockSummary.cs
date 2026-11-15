using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 收发存汇总表 stock_summary：按存货（by_wh 为 true 时再按仓库）汇总日期区间的期初、入库、出库、结存数量。
    // 来源与库存台账相同（ReportsStockSql.Moves），缺省只含已审核单据；截至今天的结存等于现存量。
    // 数量来自收发记录；金额在存货核算（IA）里，出库成本要记账后才有，所以只有 include_amount 且区间正好是已记账的整月时
    // 才另给 opening_amt、in_amt、out_amt、closing_amt（ReportsStockAmount，取 IA_Summary）。
    internal static class ReportsStockSummary
    {
        // {WH} 是仓库列或空串常量，{WHG} 是仓库的分组列（常量不能进 GROUP BY）。
        // 参数：from, from, from, to, 过滤… | limit+1 | nonzero、游标。
        const string SumSql = ";WITH s AS (SELECT m.cInvCode inv, {WH} wh,"
            + " SUM(CASE WHEN m.ddate<CONVERT(date, ?, 23) THEN " + ReportsStockSql.Signed + " ELSE 0 END) op,"
            + " SUM(CASE WHEN m.ddate>=CONVERT(date, ?, 23) AND m.rd=1 THEN m.qty ELSE 0 END) inq,"
            + " SUM(CASE WHEN m.ddate>=CONVERT(date, ?, 23) AND m.rd<>1 THEN m.qty ELSE 0 END) outq"
            + " FROM {M} WHERE m.ddate<DATEADD(day, 1, CONVERT(date, ?, 23)){FILTER} GROUP BY m.cInvCode{WHG})"
            + " SELECT TOP (?) s.inv, i.cInvName inv_name, i.cInvStd inv_std, i.cInvCCode inv_class, u.cComUnitName unit,"
            + " s.wh, w.cWhName wh_name,"
            + " CONVERT(decimal(28,6), s.op) op, CONVERT(decimal(28,6), s.inq) inq, CONVERT(decimal(28,6), s.outq) outq,"
            + " CONVERT(decimal(28,6), s.op+s.inq-s.outq) cl FROM s LEFT JOIN Inventory i ON i.cInvCode=s.inv"
            + " LEFT JOIN ComputationUnit u ON u.cComUnitCode=i.cComUnitCode"
            + " LEFT JOIN Warehouse w ON w.cWhCode=s.wh WHERE 1=1";
        const string NonZeroSql = " AND (ROUND(s.op, 6)<>0 OR ROUND(s.inq, 6)<>0 OR ROUND(s.outq, 6)<>0)";
        const string AfterSql = " AND (s.inv>? OR (s.inv=? AND s.wh>?))";

        public static ApiResult Summary(WorkContext ctx, ReportArgs a)
        {
            StockReportArgs s = ReportsStockReq.Parse(a, ctx.Item.Body);
            string to = ReportsStockSql.DateOr(ctx, s.DateTo);
            if (string.CompareOrdinal(s.DateFrom, to) > 0)
            {
                throw GlReq.Bad("date_from 不能晚于 date_to（缺省为登录日期）");
            }
            List<object> ps = new List<object>(new object[] { s.DateFrom, s.DateFrom, s.DateFrom, to });
            string filter = Filter(ctx, s, ps);
            ps.Add(a.Limit + 1);
            StringBuilder sql = Compose(s, filter);
            string[] after = Reports.Uncursor(a.After, 2);
            if (after != null)
            {
                sql.Append(AfterSql);
                ps.AddRange(new object[] { after[0], after[0], after[1] });
            }
            sql.Append(" ORDER BY s.inv, s.wh");
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), ps.ToArray(), a.Limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                items.Add(Item(rows[i], s.ByWh));
            }
            string next = rows.Count > a.Limit
                ? Reports.Cursor(GlSql.Col(rows[a.Limit - 1], "inv"), GlSql.Col(rows[a.Limit - 1], "wh")) : null;
            Dictionary<string, object> body = ReportsStockSql.Page(items, next);
            body["date_from"] = s.DateFrom;
            body["date_to"] = to;
            body["by_wh"] = s.ByWh;
            body["include_unverified"] = s.Unverified;
            if (s.IncludeAmount)
            {
                ReportsStockAmount.Attach(ctx, s, to, items, body);
            }
            return ApiResult.Ok(body);
        }

        // 主查询文本：按是否分仓库换上仓库列，非零时追加非零条件。
        static StringBuilder Compose(StockReportArgs s, string filter)
        {
            StringBuilder sql = new StringBuilder(SumSql.Replace("{M}", ReportsStockSql.Moves)
                .Replace("{WH}", s.ByWh ? "m.cWhCode" : "N''").Replace("{WHG}", s.ByWh ? ", m.cWhCode" : "")
                .Replace("{FILTER}", filter));
            if (s.NonZero)
            {
                sql.Append(NonZeroSql);
            }
            return sql;
        }

        // 存货、存货分类前缀（只进参数），然后是仓库、审核状态和数据权限。
        static string Filter(WorkContext ctx, StockReportArgs s, List<object> ps)
        {
            StringBuilder sb = new StringBuilder();
            ReportsStockSql.Equal(sb, ps, "m.cInvCode", s.Inv);
            if (s.InvClass.Length > 0)
            {
                sb.Append(" AND m.cInvCode IN (SELECT ic.cInvCode FROM Inventory ic WHERE ic.cInvCCode LIKE ?)");
                ps.Add(s.InvClass + "%");
            }
            StockReportArgs moves = new StockReportArgs();
            moves.Wh = s.Wh;
            moves.Unverified = s.Unverified;
            ReportsStockSql.MoveFilter(sb, ps, ctx, moves);
            return sb.ToString();
        }

        static Dictionary<string, object> Item(Dictionary<string, object> row, bool byWh)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["inv_code"] = GlSql.Col(row, "inv");
            item["inv_name"] = Reports.Text(row, "inv_name");
            item["inv_std"] = Reports.Text(row, "inv_std");
            item["inv_class"] = Reports.Text(row, "inv_class");
            item["unit"] = Reports.Text(row, "unit");
            item["wh_code"] = byWh ? Reports.Text(row, "wh") : null;
            item["wh_name"] = byWh ? Reports.Text(row, "wh_name") : null;
            item["opening"] = Reports.Qty(row, "op");
            item["in_qty"] = Reports.Qty(row, "inq");
            item["out_qty"] = Reports.Qty(row, "outq");
            item["closing"] = Reports.Qty(row, "cl");
            return item;
        }
    }

    // stock_summary 的 include_amount：整月区间的金额取存货核算（IA_Summary）。
    // 区间必须是整月（起始日为月初、截止日为月末），区间内每个月都已记账（iPeriod=1），期初月份有数据：
    // 期初 = 起始月的上一个月（1 月取年初结转行 iMonth=0）的 iMoney，期末 = 末月的 iMoney，入 / 出 = 各月 iIMoney / iOMoney 之和。
    // 不满足时不给金额：amount_basis = partial，amount_reason 是 range（不是整月）/ unbooked（有月份未记账）/ no_base（没有期初月份），
    // 文字原因在 amount_note；按仓库分行或指定了仓库，而存货核算不按仓库记账（有仓库为空的行）时 amount_basis = no_wh。
    // 起始月的上一个月没有数据（存货核算年中启用）时，期初取同年的年初结转行（iMonth=0）。
    // 金额只含操作员有数据权限的仓库、存货（与数量同一条规则）。满足但没有 IA3011_01 时四个金额字段为 null，
    // 响应带 masked_fields: ["amount"]。
    internal static class ReportsStockAmount
    {
        const int MaxRows = 100000;
        const string NoteRange = "区间不是整月（起始日须是月初、截止日须是月末），只给数量";
        const string NoteOpen = "区间内有尚未记账的存货核算月份，只给数量";
        const string NoteBase = "存货核算没有期初月份的数据，只给数量";
        const string NoteWh = "存货核算不按仓库记账，按仓库分行或指定仓库时不给金额；by_wh=false 且不指定 wh 可取每个存货的金额";
        static readonly string[] Fields = new string[] { "opening_amt", "in_amt", "out_amt", "closing_amt" };

        // 各月状态：ym → 是否已记账、仓库为空的行数。从起始月所在年的年初结转行（iMonth=0，ym = 年 * 100）查到末月。
        const string StateSql = "SELECT a.iYear*100+a.iMonth ym, MAX(ISNULL(a.iPeriod, 0)) closed,"
            + " SUM(CASE WHEN ISNULL(a.cWhCode, N'')=N'' THEN 1 ELSE 0 END) nowh"
            + " FROM IA_Summary a WHERE a.iYear*100+a.iMonth BETWEEN ? AND ? GROUP BY a.iYear, a.iMonth";

        // {WH} 是仓库列或空串常量，{WHG} 是仓库的分组列。参数：期初月份 | 起始月 | 起始月 | 末月 | 期初月份、末月 | 过滤… | 存货范围。
        const string SumSql = "SELECT a.cInvCode inv, {WH} wh,"
            + " CONVERT(decimal(28,2), SUM(CASE WHEN a.iYear*100+a.iMonth=? THEN ISNULL(a.iMoney, 0) ELSE 0 END)) op,"
            + " CONVERT(decimal(28,2), SUM(CASE WHEN a.iMonth>=1 AND a.iYear*100+a.iMonth>=? THEN ISNULL(a.iIMoney, 0) ELSE 0 END)) im,"
            + " CONVERT(decimal(28,2), SUM(CASE WHEN a.iMonth>=1 AND a.iYear*100+a.iMonth>=? THEN ISNULL(a.iOMoney, 0) ELSE 0 END)) om,"
            + " CONVERT(decimal(28,2), SUM(CASE WHEN a.iYear*100+a.iMonth=? THEN ISNULL(a.iMoney, 0) ELSE 0 END)) cl"
            + " FROM IA_Summary a WHERE a.iYear*100+a.iMonth BETWEEN ? AND ?{FILTER} GROUP BY a.cInvCode{WHG}";

        public static void Attach(WorkContext ctx, StockReportArgs s, string to, List<object> items,
            Dictionary<string, object> body)
        {
            int[] ym = WholeMonths(s.DateFrom, to);
            string[] verdict = ym == null ? new string[] { "partial", NoteRange, "range" } : Verdict(ctx, s, ym);
            body["amount_basis"] = verdict == null ? "month" : verdict[0];
            body["amount_note"] = verdict == null ? null : verdict[1];
            body["amount_reason"] = verdict == null ? null : verdict[2];
            if (verdict != null || items.Count == 0)
            {
                return;
            }
            if (!PermRegistry.MayViewAmount(ctx))
            {
                Mask(items, body);
                return;
            }
            Fill(items, Amounts(ctx, s, ym, items), s.ByWh);
        }

        // { 期初月份, 起始月, 末月 }（yyyyMM；1 月的期初月份是 年 * 100，即年初结转行）；区间不是整月时为 null。
        internal static int[] WholeMonths(string from, string to)
        {
            DateTime a;
            DateTime b;
            if (!DateTime.TryParseExact(from, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out a)
                || !DateTime.TryParseExact(to, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out b))
            {
                return null;
            }
            if (a.Day != 1 || b.AddDays(1).Day != 1)
            {
                return null;
            }
            int first = a.Year * 100 + a.Month;
            return new int[] { a.Month == 1 ? a.Year * 100 : first - 1, first, b.Year * 100 + b.Month };
        }

        // 下一个月份（yyyyMM）：12 月进到下一年 1 月。
        internal static int NextMonth(int ym)
        {
            return ym % 100 == 12 ? (ym / 100 + 1) * 100 + 1 : ym + 1;
        }

        // 不能给金额时返回 { amount_basis, 原因文字, amount_reason }（no_wh 的 reason 为 null），可以给时返回 null。
        // 期初月份缺失时可能把 ym[0] 改成年初结转行（见 OpeningMonth）。
        static string[] Verdict(WorkContext ctx, StockReportArgs s, int[] ym)
        {
            Dictionary<int, int[]> states = States(ctx, ym);
            if (!OpeningMonth(states, ym))
            {
                return new string[] { "partial", NoteBase, "no_base" };
            }
            for (int m = ym[1]; m <= ym[2]; m = NextMonth(m))
            {
                int[] state;
                if (!states.TryGetValue(m, out state) || state[0] != 1)
                {
                    return new string[] { "partial", NoteOpen, "unbooked" };
                }
            }
            return (s.ByWh || s.Wh.Length > 0) && EmptyWh(states, ym) ? new string[] { "no_wh", NoteWh, null } : null;
        }

        // 期初月份有数据即可；起始月的上一个月没有数据而同年年初结转行有、且两者之间没有别的月份时（存货核算年中启用），
        // 期初改取年初结转行（ym[0] = 年 * 100）。都没有返回 false。
        internal static bool OpeningMonth(Dictionary<int, int[]> states, int[] ym)
        {
            if (states.ContainsKey(ym[0]))
            {
                return true;
            }
            int yearStart = ym[1] / 100 * 100;
            if (ym[0] == yearStart || !states.ContainsKey(yearStart))
            {
                return false;
            }
            for (int m = yearStart + 1; m < ym[1]; m++)
            {
                if (states.ContainsKey(m))
                {
                    return false;
                }
            }
            ym[0] = yearStart;
            return true;
        }

        // 期初月份到末月之间有没有仓库为空的行（不算更晚年份的年初结转行）。
        static bool EmptyWh(Dictionary<int, int[]> states, int[] ym)
        {
            foreach (KeyValuePair<int, int[]> one in states)
            {
                bool inside = one.Key >= ym[0] && one.Key <= ym[2] && (one.Key % 100 != 0 || one.Key == ym[0]);
                if (inside && one.Value[1] > 0)
                {
                    return true;
                }
            }
            return false;
        }

        static Dictionary<int, int[]> States(WorkContext ctx, int[] ym)
        {
            object[] args = new object[] { ym[1] / 100 * 100, ym[2] };
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, StateSql, args, 1000);
            Dictionary<int, int[]> states = new Dictionary<int, int[]>();
            for (int i = 0; i < rows.Count; i++)
            {
                states[GlSql.Int(rows[i], "ym")] = new int[] { GlSql.Int(rows[i], "closed"), GlSql.Int(rows[i], "nowh") };
            }
            return states;
        }

        static void Mask(List<object> items, Dictionary<string, object> body)
        {
            for (int i = 0; i < items.Count; i++)
            {
                Dictionary<string, object> item = (Dictionary<string, object>)items[i];
                for (int k = 0; k < Fields.Length; k++)
                {
                    item[Fields[k]] = null;
                }
            }
            PermRegistry.NoteAmountMasked(body);
        }

        // 存货核算里没有这个存货（仓库）的行，说明期初、发生、期末金额都是 0。
        static void Fill(List<object> items, Dictionary<string, decimal[]> sums, bool byWh)
        {
            for (int i = 0; i < items.Count; i++)
            {
                Dictionary<string, object> item = (Dictionary<string, object>)items[i];
                decimal[] v;
                if (!sums.TryGetValue(Key(Convert.ToString(item["inv_code"]), byWh ? Convert.ToString(item["wh_code"]) : ""), out v))
                {
                    v = new decimal[4];
                }
                for (int k = 0; k < Fields.Length; k++)
                {
                    item[Fields[k]] = v[k];
                }
            }
        }

        static string Key(string inv, string wh)
        {
            return inv + "\u001f" + wh;
        }

        // 只取本页存货编码范围内的行（items 已按存货编码排序），金额四项依次是 op、im、om、cl。
        static Dictionary<string, decimal[]> Amounts(WorkContext ctx, StockReportArgs s, int[] ym, List<object> items)
        {
            List<object> ps = new List<object>(new object[] { ym[0], ym[1], ym[1], ym[2], ym[0], ym[2] });
            StringBuilder filter = new StringBuilder();
            ReportsStockSql.Equal(filter, ps, "a.cInvCode", s.Inv);
            ReportsStockSql.Equal(filter, ps, "a.cWhCode", s.Wh);
            if (s.InvClass.Length > 0)
            {
                filter.Append(" AND a.cInvCode IN (SELECT ic.cInvCode FROM Inventory ic WHERE ic.cInvCCode LIKE ?)");
                ps.Add(s.InvClass + "%");
            }
            filter.Append(" AND a.cInvCode>=? AND a.cInvCode<=?");
            ps.Add(Convert.ToString(((Dictionary<string, object>)items[0])["inv_code"]));
            ps.Add(Convert.ToString(((Dictionary<string, object>)items[items.Count - 1])["inv_code"]));
            // 数据权限：仓库、存货（同 stock_summary 的规则，列 cWhCode、cInvCode 在 a 上）。
            PermHook.Where(filter, ps, ctx, "a");
            string sql = SumSql.Replace("{WH}", s.ByWh ? "a.cWhCode" : "N''").Replace("{WHG}", s.ByWh ? ", a.cWhCode" : "")
                .Replace("{FILTER}", filter.ToString());
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), MaxRows + 1);
            if (rows.Count > MaxRows)
            {
                throw GlReq.Bad("存货核算行数过多，请用 inv、wh 或 inv_class 缩小范围，或减小 limit");
            }
            Dictionary<string, decimal[]> sums = new Dictionary<string, decimal[]>();
            for (int i = 0; i < rows.Count; i++)
            {
                sums[Key(GlSql.Col(rows[i], "inv"), GlSql.Col(rows[i], "wh"))] = new decimal[]
                {
                    GlSql.Money(rows[i], "op"), GlSql.Money(rows[i], "im"), GlSql.Money(rows[i], "om"), GlSql.Money(rows[i], "cl")
                };
            }
            return sums;
        }
    }
}
