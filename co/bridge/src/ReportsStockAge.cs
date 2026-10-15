using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 库龄分析 stock_age：按（存货，仓库）（by_wh=false 时按存货）列出现存量的库龄、呆滞和库存金额。
    // 现存量取 CurrentStock（非零）；库龄 = 登录日期 − 最后一次已审核入库的日期（CurrentStock.dMdate 是空的，不能用）；
    // 呆滞 = 最后一次已审核出库至今不少于 idle_days 天。金额 = 现存量 × 成本月份的结存单价（IA_Summary 的金额 / 数量，
    // 成本月份 = 不晚于登录月份的最近一个已记账月份）。没有存货核算查询权限（IA3011_01）的操作员金额一律为 null，
    // 响应带 masked_fields: ["amount"]。分段合计、呆滞合计、总计覆盖全部行，items 只截前 limit 行。
    internal static class ReportsStockAge
    {
        // 一次最多处理的（存货，仓库）行数；超过时 400，请调用方缩小范围。
        const int MaxRows = 50000;

        public static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            StockAgeArgs s = ReportsStockAgeReq.Parse(ctx.Item.Body);
            DateTime asOf = AsOf(ctx);
            int[] cost = CostMonth(ctx, asOf);
            List<AgeLine> lines = Load(ctx, s, asOf, cost);
            // 金额看不到：功能权限（IA3011_01）不够，或字段权限会把金额遮掉（PermMask.WillMaskAmounts）；两条路径一样处理。
            bool visible = PermRegistry.MayViewAmount(ctx) && !PermMask.WillMaskAmounts(ctx);
            // 看不到金额时不能让排序泄露金额高低：按库龄排，同龄的不再比金额（先清掉金额）。
            string sort = visible ? s.Sort : "age";
            if (!visible)
            {
                ReportsStockAgeCalc.DropAmounts(lines);
            }
            ReportsStockAgeCalc.Order(lines, sort);
            Dictionary<string, object> body = Reports.Body();
            body["as_of"] = asOf.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            body["cost_month"] = cost[0] > 0 ? Month(cost) : null;
            body["by_wh"] = s.ByWh;
            body["idle_days"] = s.IdleDays;
            body["sort"] = sort;
            Fill(body, lines, s, visible);
            if (!visible)
            {
                PermRegistry.NoteAmountMasked(body);
            }
            return ApiResult.Ok(body);
        }

        static void Fill(Dictionary<string, object> body, List<AgeLine> lines, StockAgeArgs s, bool visible)
        {
            List<object> items = new List<object>();
            for (int i = 0; i < lines.Count && i < s.Limit; i++)
            {
                items.Add(Item(lines[i], s.ByWh, visible));
            }
            body["count"] = items.Count;
            body["truncated"] = lines.Count > s.Limit;
            body["items"] = items;
            AgeReport report = ReportsStockAgeCalc.Summarize(lines, s.Buckets);
            body["buckets"] = Buckets(report, s.Buckets, visible);
            body["idle_total"] = Sum(report.Idle, visible);
            body["total"] = Sum(report.Total, visible);
            body["uncosted_items"] = visible ? (object)(report.Total.Items - report.Total.Costed) : null;
        }

        // 登录日期（date）；缺省或无效时 400。
        static DateTime AsOf(WorkContext ctx)
        {
            DateTime parsed;
            string text = ReportsStockSql.DateOr(ctx, "");
            if (!DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed))
            {
                throw GlReq.Bad("缺少有效的登录日期 date（yyyy-MM-dd）", "date");
            }
            return parsed;
        }

        // { 年, 月 }；没有已记账的存货核算月份时为 { 0, 0 }。
        static int[] CostMonth(WorkContext ctx, DateTime asOf)
        {
            object[] args = new object[] { asOf.Year * 100 + asOf.Month };
            Dictionary<string, object> row = Rows.One(ctx.Conn, ReportsStockAgeSql.CostMonth, args);
            return row == null ? new int[] { 0, 0 } : new int[] { GlSql.Int(row, "y"), GlSql.Int(row, "m") };
        }

        static string Month(int[] cost)
        {
            return cost[0].ToString("0000", CultureInfo.InvariantCulture) + "-"
                + cost[1].ToString("00", CultureInfo.InvariantCulture);
        }

        static List<AgeLine> Load(WorkContext ctx, StockAgeArgs s, DateTime asOf, int[] cost)
        {
            List<object> ps = new List<object>();
            string sql = ReportsStockAgeSql.Build(ctx, s, asOf.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                cost[0], cost[1], ps);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), MaxRows + 1);
            if (rows.Count > MaxRows)
            {
                throw GlReq.Bad("现存量行数过多，请用 wh、inv 或 inv_class 缩小范围");
            }
            List<AgeLine> lines = new List<AgeLine>();
            for (int i = 0; i < rows.Count; i++)
            {
                lines.Add(ReportsStockAgeCalc.FromRow(rows[i]));
            }
            if (!s.ByWh)
            {
                lines = ReportsStockAgeCalc.MergeByInv(lines);
            }
            for (int i = 0; i < lines.Count; i++)
            {
                ReportsStockAgeCalc.Rate(lines[i], asOf, s.IdleDays);
            }
            return lines;
        }

        static Dictionary<string, object> Item(AgeLine line, bool byWh, bool visible)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["inv_code"] = line.Inv;
            item["inv_name"] = line.InvName;
            item["inv_std"] = line.InvStd;
            item["unit"] = line.Unit;
            if (byWh)
            {
                item["wh_code"] = line.Wh;
                item["wh_name"] = line.WhName;
            }
            item["qty"] = decimal.Round(line.Qty, 6, MidpointRounding.AwayFromZero);
            item["last_in"] = Date(line.LastIn);
            item["last_out"] = Date(line.LastOut);
            item["age_days"] = line.Age.HasValue ? (object)line.Age.Value : null;
            item["idle"] = line.Idle;
            bool costed = visible && line.Amount.HasValue && line.CostQty != 0m;
            item["unit_cost"] = costed ? (object)decimal.Round(line.Amount.Value / line.CostQty, 6, MidpointRounding.AwayFromZero) : null;
            item["amount"] = visible && line.Amount.HasValue ? (object)line.Amount.Value : null;
            return item;
        }

        static object Date(DateTime? day)
        {
            return day.HasValue ? day.Value.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) : null;
        }

        // 每段：from_days 含、to_days 含（最后一段没有上界）。
        static List<object> Buckets(AgeReport report, int[] bounds, bool visible)
        {
            List<object> list = new List<object>();
            for (int i = 0; i < report.Buckets.Length; i++)
            {
                Dictionary<string, object> map = Sum(report.Buckets[i], visible);
                map["from_days"] = i == 0 ? 0 : bounds[i - 1] + 1;
                map["to_days"] = i < bounds.Length ? (object)bounds[i] : null;
                list.Add(map);
            }
            return list;
        }

        // 一组的行数和金额。金额为 null：没有权限，或这一组有行但一行也没有成本。
        static Dictionary<string, object> Sum(AgeSum sum, bool visible)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["items"] = sum.Items;
            bool known = visible && (sum.Items == 0 || sum.Costed > 0);
            map["amount"] = known ? (object)decimal.Round(sum.Amount, 2, MidpointRounding.AwayFromZero) : null;
            return map;
        }
    }
}
