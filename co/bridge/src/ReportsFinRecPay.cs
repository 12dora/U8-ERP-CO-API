using System.Collections.Generic;

namespace U8Co
{
    // 收付款汇总 receipts_payments：按日、月、往来单位、业务员、部门、结算方式汇总收款单（应收）或付款单（应付）及其退款。
    // 来源 Ap_CloseBill：应收收款单 cFlag=AR 且 cVouchType=48，客户退款 AR 且 49；应付付款单 AP 且 49，供应商退款 AP 且 48。
    // 日期 dVouchDate，金额 iAmount（本币，正数）。缺省只含已审核（cCheckMan 非空），期初单据（bStartFlag=1）不算。
    // docs 是该组的单据数（收付款单加退款单），amount 是收款 / 付款合计，refund_amount 是退款合计，net_amount = amount − refund_amount。
    // 分组超过 limit 时按 amount 从大到小保留前 limit 组；含 day / month 维度时保留的行按日期输出，否则按 amount 从大到小输出。
    // 没有分组维度（group_by 为空数组）时 items 为空，只有 totals。totals 按全部符合条件的单据算，不受 limit 影响。
    internal static class ReportsFinRecPay
    {
        public static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            FinRecPayArgs q = ReportsFinParse.RecPay(ctx.Item.Body);
            string to = ReportsStockSql.DateOr(ctx, q.DateTo);
            if (string.CompareOrdinal(q.DateFrom, to) > 0)
            {
                throw GlReq.Bad("date_from 不能晚于 date_to（缺省为登录日期）", "date_from");
            }
            List<object> items = new List<object>();
            bool truncated = false;
            q.NoAmountOrder = PermMask.WillMaskAmounts(ctx);
            if (q.GroupBy.Length > 0)
            {
                List<object> ps = new List<object>();
                string sql = ReportsFinRecPaySql.Group(ctx, q, to, q.Limit + 1, ps);
                List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), q.Limit + 1);
                truncated = rows.Count > q.Limit;
                for (int i = 0; i < rows.Count && i < q.Limit; i++)
                {
                    items.Add(Item(rows[i], q.GroupBy));
                }
            }
            Dictionary<string, object> body = Reports.Body();
            body["side"] = q.Side;
            body["date_from"] = q.DateFrom;
            body["date_to"] = to;
            body["group_by"] = new List<object>(q.GroupBy);
            body["include_unverified"] = q.Unverified;
            body["order"] = q.NoAmountOrder ? "docs" : "amount";
            body["items"] = items;
            body["count"] = items.Count;
            body["truncated"] = truncated;
            body["totals"] = Totals(ctx, q, to);
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> Totals(WorkContext ctx, FinRecPayArgs q, string to)
        {
            List<object> ps = new List<object>();
            string sql = ReportsFinRecPaySql.Total(ctx, q, to, ps);
            return Measures(Rows.One(ctx.Conn, sql, ps.ToArray()), new Dictionary<string, object>());
        }

        // 一行：先是各维度的键（编码为空的写 null），再是 docs、amount、refund_amount、net_amount。
        internal static Dictionary<string, object> Item(Dictionary<string, object> row, string[] dims)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            for (int i = 0; i < dims.Length; i++)
            {
                Keys(item, row, dims[i]);
            }
            return Measures(row, item);
        }

        static void Keys(Dictionary<string, object> item, Dictionary<string, object> row, string dim)
        {
            string key = Reports.Text(row, "k_" + dim);
            switch (dim)
            {
                case "day":
                    item["date"] = key;
                    return;
                case "month":
                    item["month"] = key;
                    return;
                case "partner":
                    Pair(item, row, "partner", key);
                    return;
                case "person":
                    Pair(item, row, "person", key);
                    return;
                case "department":
                    Pair(item, row, "dept", key);
                    return;
                default:
                    Pair(item, row, "settle", key);
                    return;
            }
        }

        // 键为空（单据上没填）时编码和名称都是 null。名称取不到（档案已删）时为 null。
        static void Pair(Dictionary<string, object> item, Dictionary<string, object> row, string prefix, string key)
        {
            string dim = prefix == "dept" ? "department" : prefix;
            item[prefix + "_code"] = key;
            item[prefix + "_name"] = key == null ? null : Reports.Text(row, "n_" + dim);
        }

        // 合计行为空（没有符合条件的单据）时 SUM 为 NULL，按 0 算。
        internal static Dictionary<string, object> Measures(Dictionary<string, object> row, Dictionary<string, object> into)
        {
            decimal amount = GlSql.Money(row, "amt");
            decimal refund = GlSql.Money(row, "refund");
            into["docs"] = GlSql.Int(row, "docs");
            into["amount"] = amount;
            into["refund_amount"] = refund;
            into["net_amount"] = amount - refund;
            return into;
        }
    }
}
