using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 完工产品单位成本 cost_unit：成本核算模块（CA）每个产品每个会计期间的单位成本，按成本要素（0 材料费用、1 人工费用、
    // 2 辅助费用、3 制造费用…）列出，单位是元 / 存货主计量单位。unit_total 是各要素之和。
    // 期间范围缺省为最近一个成本核算已结账的期间（GL_mend.bflag_CA，不晚于登录月份；会计年度不是登录年度时不晚于 12 月），
    // 没有已结账期间时 period_from、period_to 为 null、items 为空。只给 period_from 时，截止期间缺省取最近已结账的期间，
    // 比 period_from 早就取 period_from。未结账的期间只要有成本数据也照列，已结账的期间见 closed_periods。
    // qty 是该期间完工数量（成本核算的生产订单完工数量按产品合计，与产成品入库数量一致），没有时为 null。
    // 数据权限：存货。
    internal static class ReportsAcctCost
    {
        // 一次最多取的成本行数（产品 × 期间 × 成本要素）；超过说明范围太大。
        const int MaxRows = 20000;

        public static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            AcctCostArgs q = ReportsAcctParse.Cost(ctx.Item.Body);
            int year = a.FiscalYear;
            List<int> closed = ClosedPeriods(ctx, year);
            int[] range = Range(q, closed, ReportsAcctParse.DefaultPeriod(ctx, year));
            Dictionary<string, object> body = Reports.Body();
            body["fiscal_year"] = year;
            body["period_from"] = range[0] == 0 ? null : (object)range[0];
            body["period_to"] = range[1] == 0 ? null : (object)range[1];
            List<object> items = new List<object>();
            Dictionary<string, object> names = new Dictionary<string, object>();
            if (range[0] > 0)
            {
                List<Dictionary<string, object>> rows = Units(ctx, q, year, range);
                items = Fold(rows, Quantities(ctx, year, range), names);
                FillNames(ctx, names);
            }
            body["items"] = items;
            body["element_names"] = names;
            body["closed_periods"] = PeriodTexts(year, closed);
            return ApiResult.Ok(body);
        }

        static List<int> ClosedPeriods(WorkContext ctx, int year)
        {
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, ReportsAcctCostSql.ClosedPeriods,
                new object[] { year }, 12);
            List<int> list = new List<int>();
            for (int i = 0; i < rows.Count; i++)
            {
                list.Add(GlSql.Int(rows[i], "iperiod"));
            }
            return list;
        }

        // 实际查询的期间范围 { 起, 止 }；没有可查的期间时 { 0, 0 }。limit 是缺省时最晚可取的期间（登录月份或 12）。纯函数，--selftest 用。
        internal static int[] Range(AcctCostArgs q, List<int> closed, int limit)
        {
            int latest = 0;
            for (int i = 0; i < closed.Count; i++)
            {
                if (closed[i] <= limit && closed[i] > latest)
                {
                    latest = closed[i];
                }
            }
            int to = q.To > 0 ? q.To : latest;
            if (q.To == 0 && q.From > to)
            {
                to = q.From;
            }
            int from = q.From > 0 ? q.From : to;
            return to == 0 ? new int[] { 0, 0 } : new int[] { from, to };
        }

        static List<Dictionary<string, object>> Units(WorkContext ctx, AcctCostArgs q, int year, int[] range)
        {
            List<object> ps = new List<object>();
            ps.Add(year * 100 + range[0]);
            ps.Add(year * 100 + range[1]);
            StringBuilder sql = new StringBuilder(ReportsAcctCostSql.Unit);
            if (q.Inv.Length > 0)
            {
                sql.Append(" AND b.InvCode IN (");
                for (int i = 0; i < q.Inv.Length; i++)
                {
                    sql.Append(i == 0 ? "?" : ", ?");
                    ps.Add(q.Inv[i]);
                }
                sql.Append(")");
            }
            if (q.InvClass.Length > 0)
            {
                sql.Append(ReportsAcctCostSql.ClassFilter);
                ps.Add(q.InvClass + "%");
            }
            // 数据权限：存货。
            PermHook.Where(sql, ps, ctx, "b");
            sql.Append(ReportsAcctCostSql.CostOrder);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), ps.ToArray(), MaxRows + 1);
            if (rows.Count > MaxRows)
            {
                throw GlReq.Bad("单位成本行数太多，请缩小期间范围或用 inv、inv_class 限定产品", "period_from");
            }
            return rows;
        }

        // 完工数量：键是「YYYYMM:产品号」。
        static Dictionary<string, decimal> Quantities(WorkContext ctx, int year, int[] range)
        {
            object[] args = new object[] { year * 100 + range[0], year * 100 + range[1] };
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, ReportsAcctCostSql.FinishedQty, args, MaxRows);
            Dictionary<string, decimal> map = new Dictionary<string, decimal>();
            for (int i = 0; i < rows.Count; i++)
            {
                map[QtyKey(GlSql.Int(rows[i], "per"), GlSql.Col(rows[i], "part"))] = Reports.Qty(rows[i], "qty");
            }
            return map;
        }

        static string QtyKey(int per, string part)
        {
            return per.ToString(CultureInfo.InvariantCulture) + ":" + part;
        }

        // 把（产品、期间、成本要素）行折成（产品、期间）的列表，行已按存货编码、期间、要素排好序。
        // names 先记下出现过的要素编码（名称随后由 FillNames 补）。纯函数，--selftest 用。
        internal static List<object> Fold(List<Dictionary<string, object>> rows, Dictionary<string, decimal> qty,
            Dictionary<string, object> names)
        {
            List<object> items = new List<object>();
            Dictionary<string, object> item = null;
            decimal total = 0m;
            string key = null;
            for (int i = 0; i < rows.Count; i++)
            {
                string at = GlSql.Col(rows[i], "per") + ":" + GlSql.Col(rows[i], "part");
                if (at != key)
                {
                    Finish(item, total);
                    item = Head(rows[i], qty);
                    items.Add(item);
                    total = 0m;
                    key = at;
                }
                string element = GlSql.Col(rows[i], "typ");
                decimal amount = Reports.Qty(rows[i], "amt");
                ((Dictionary<string, object>)item["elements"])[element] = amount;
                names[element] = null;
                total += amount;
            }
            Finish(item, total);
            return items;
        }

        static Dictionary<string, object> Head(Dictionary<string, object> row, Dictionary<string, decimal> qty)
        {
            int per = GlSql.Int(row, "per");
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["inv_code"] = GlSql.Col(row, "inv_code");
            item["inv_name"] = Reports.Text(row, "inv_name");
            item["unit"] = Reports.Text(row, "unit");
            item["period"] = PeriodText(per);
            decimal done;
            item["qty"] = qty.TryGetValue(QtyKey(per, GlSql.Col(row, "part")), out done) && done > 0m ? (object)done : null;
            item["elements"] = new Dictionary<string, object>();
            return item;
        }

        static void Finish(Dictionary<string, object> item, decimal total)
        {
            if (item != null)
            {
                item["unit_total"] = total;
            }
        }

        // 把出现过的成本要素编码补上名称；名称表里没有的保持 null。
        static void FillNames(WorkContext ctx, Dictionary<string, object> names)
        {
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, ReportsAcctCostSql.ElementNames, new object[0], 100);
            for (int i = 0; i < rows.Count; i++)
            {
                string code = GlSql.Col(rows[i], "code");
                if (names.ContainsKey(code) && names[code] == null)
                {
                    names[code] = Reports.Text(rows[i], "name");
                }
            }
        }

        // YYYYMM → "YYYY-MM"。
        internal static string PeriodText(int yyyymm)
        {
            return (yyyymm / 100).ToString("0000", CultureInfo.InvariantCulture) + "-"
                + (yyyymm % 100).ToString("00", CultureInfo.InvariantCulture);
        }

        static List<object> PeriodTexts(int year, List<int> periods)
        {
            List<object> list = new List<object>();
            for (int i = 0; i < periods.Count; i++)
            {
                list.Add(PeriodText(year * 100 + periods[i]));
            }
            return list;
        }
    }
}
