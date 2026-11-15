using System.Collections.Generic;

namespace U8Co
{
    // 采购综合统计 purchase_analysis：按物料、供应商、月份（任意 1 到 3 个维度）汇总采购数量和金额。
    // 来源 source=invoice（缺省）是采购发票行（不含期初发票），amount 无税、amount_tax 价税合计，都是本币；
    // source=receipt 是已审核的采购入库行，amount 取无税的 iPrice，amount_tax 为 null。
    // 数量是存货主计量单位的数量；分组里没有 material 时各行混了不同计量单位，qty 给 null，不相加。
    // 按金额从大到小排序（字段权限会遮金额时改按分组键，order 回显实际用的排序），超过 limit 的行不返回，truncated 为 true；totals 是全部明细的合计，不受 limit 影响。
    // 数据权限：功能 report:purchase_analysis，记录级条件（供应商可空、存货）加在明细 CTE l 上。
    internal static class ReportsPuPurchase
    {
        public static ApiResult Run(WorkContext ctx, ReportArgs ignored)
        {
            PuArgs a = ReportsPuReq.ParsePurchase(ctx.Item.Body);
            string to = a.DateTo.Length > 0 ? a.DateTo : ReportsStockSql.DateOr(ctx, "");
            if (string.CompareOrdinal(a.DateFrom, to) > 0)
            {
                throw GlReq.Bad("date_from 不能晚于 date_to（缺省为登录日期）", "date_from");
            }
            a.NoAmountOrder = PermMask.WillMaskAmounts(ctx);
            List<object> ps = new List<object>();
            string sql = ReportsPuSql.WithPerm(ReportsPuSql.GroupSql(a, to, ps), ctx, ps);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), a.Limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                items.Add(Item(rows[i], a));
            }
            List<object> tp = new List<object>();
            string totalSql = ReportsPuSql.WithPerm(ReportsPuSql.TotalsSql(a, to, tp), ctx, tp);
            Dictionary<string, object> body = Reports.Body();
            body["source"] = a.Receipt ? "receipt" : "invoice";
            body["date_from"] = a.DateFrom;
            body["date_to"] = to;
            body["group_by"] = new List<object>(a.GroupBy);
            body["order"] = a.NoAmountOrder ? "key" : "amount";
            body["items"] = items;
            body["count"] = items.Count;
            body["truncated"] = rows.Count > a.Limit;
            body["totals"] = Totals(Rows.One(ctx.Conn, totalSql, tp.ToArray()), a.Receipt);
            return ApiResult.Ok(body);
        }

        // 一行：先是分组键，再是 qty、amount、amount_tax、docs。
        internal static Dictionary<string, object> Item(Dictionary<string, object> row, PuArgs a)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            bool material = false;
            for (int i = 0; i < a.GroupBy.Length; i++)
            {
                material = material || a.GroupBy[i] == "material";
                Key(item, row, a.GroupBy[i]);
            }
            item["qty"] = material ? (object)Reports.Qty(row, "qty") : null;
            item["amount"] = GlSql.Money(row, "amount");
            item["amount_tax"] = a.Receipt ? null : (object)GlSql.Money(row, "amount_tax");
            item["docs"] = GlSql.Int(row, "docs");
            return item;
        }

        static void Key(Dictionary<string, object> item, Dictionary<string, object> row, string dim)
        {
            if (dim == "material")
            {
                item["inv_code"] = GlSql.Col(row, "inv_code");
                item["inv_name"] = Reports.Text(row, "inv_name");
                item["unit"] = Reports.Text(row, "unit");
            }
            else if (dim == "vendor")
            {
                item["vendor_code"] = Reports.Text(row, "vendor_code");
                item["vendor_name"] = Reports.Text(row, "vendor_name");
            }
            else
            {
                item["month"] = GlSql.Col(row, "mon");
            }
        }

        internal static Dictionary<string, object> Totals(Dictionary<string, object> row, bool receipt)
        {
            Dictionary<string, object> totals = new Dictionary<string, object>();
            totals["amount"] = GlSql.Money(row, "amount");
            totals["amount_tax"] = receipt ? null : (object)GlSql.Money(row, "amount_tax");
            totals["docs"] = GlSql.Int(row, "docs");
            return totals;
        }
    }
}
