using System;
using System.Collections.Generic;

namespace U8Co
{
    // 检验合格率 qm_pass_rate：来料（QM03）或产品（QM04）检验单在日期区间内，按月份、供应商（仅来料）或存货分组，
    // 给出检验单数（合格、不合格、让步、未判定）和各类数量；不给比率，由调用方自己算。
    // 一张检验单表头算一单；结论含「不合格」或「不接受」、或结论为空且有不合格数量，算不合格；
    // 含「让步」（且不是不合格）算让步，让步按合格计入 pass_docs；结论为空且没有不合格数量的算未判定，
    // 计入 undecided_docs，不算合格也不算不合格。
    // 默认只含已审核的检验单。totals 合计的是全部分组，items 超过 limit 时只截断列表。
    internal static class ReportsQmPassRate
    {
        internal const int MaxGroups = 20000;
        static readonly string[] Measures = new string[]
        {
            "docs", "pass_docs", "fail_docs", "undecided_docs", "concession_docs", "qty", "reg_qty", "con_qty", "dis_qty"
        };

        public static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            QmReportArgs q = ReportsQmReq.Parse(ReportsQmReq.PassRateName, ctx.Item.Body);
            string to = ReportsStockSql.DateOr(ctx, q.DateTo);
            ReportsQmReq.CheckRange(q.DateFrom, to);
            List<object> ps = new List<object>(new object[] { q.VoucherType, q.DateFrom, to });
            string sql = ReportsQmPassRateSql.For(q.GroupBy, ReportsQmFilter.Build(ctx, q, ps));
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), MaxGroups + 1);
            if (rows.Count > MaxGroups)
            {
                throw GlReq.Bad("分组超过 " + MaxGroups + " 个，请缩小日期范围或指定存货", "date_from");
            }
            Dictionary<string, decimal> totals = new Dictionary<string, decimal>();
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count; i++)
            {
                Dictionary<string, object> item = Item(rows[i], q.GroupBy);
                Sum(totals, item);
                if (i < q.Limit)
                {
                    items.Add(item);
                }
            }
            Dictionary<string, object> body = Reports.Body();
            body["kind"] = q.Kind;
            body["date_from"] = q.DateFrom;
            body["date_to"] = to;
            body["group_by"] = q.GroupBy;
            body["include_unverified"] = q.Unverified;
            body["items"] = items;
            body["count"] = items.Count;
            body["truncated"] = rows.Count > q.Limit;
            body["totals"] = Totals(totals);
            return ApiResult.Ok(body);
        }

        // 分组键之后是九项度量。pass_docs = docs − fail_docs − undecided_docs（让步已在里面）。
        internal static Dictionary<string, object> Item(Dictionary<string, object> row, string groupBy)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            if (groupBy == "vendor")
            {
                item["vendor_code"] = Reports.Text(row, "k1");
                item["vendor_name"] = Reports.Text(row, "k2");
            }
            else if (groupBy == "inventory")
            {
                item["inv_code"] = Reports.Text(row, "k1");
                item["inv_name"] = Reports.Text(row, "k2");
                item["unit"] = Reports.Text(row, "k3");
            }
            else
            {
                item["month"] = GlSql.Col(row, "k1");
            }
            int docs = GlSql.Int(row, "docs");
            int fail = GlSql.Int(row, "fail_docs");
            int undecided = GlSql.Int(row, "undecided_docs");
            item["docs"] = docs;
            item["pass_docs"] = docs - fail - undecided;
            item["fail_docs"] = fail;
            item["undecided_docs"] = undecided;
            item["concession_docs"] = GlSql.Int(row, "con_docs");
            item["qty"] = Reports.Qty(row, "qty");
            item["reg_qty"] = Reports.Qty(row, "reg_qty");
            item["con_qty"] = Reports.Qty(row, "con_qty");
            item["dis_qty"] = Reports.Qty(row, "dis_qty");
            return item;
        }

        internal static void Sum(Dictionary<string, decimal> totals, Dictionary<string, object> item)
        {
            for (int i = 0; i < Measures.Length; i++)
            {
                decimal add = Convert.ToDecimal(item[Measures[i]]);
                decimal sum;
                totals.TryGetValue(Measures[i], out sum);
                totals[Measures[i]] = sum + add;
            }
        }

        // 单数合计是整数，数量合计保留六位小数。
        internal static Dictionary<string, object> Totals(Dictionary<string, decimal> totals)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            for (int i = 0; i < Measures.Length; i++)
            {
                decimal sum;
                totals.TryGetValue(Measures[i], out sum);
                bool count = Measures[i].EndsWith("docs", StringComparison.Ordinal);
                body[Measures[i]] = count ? (object)(int)sum : decimal.Round(sum, 6, MidpointRounding.AwayFromZero);
            }
            return body;
        }
    }
}
