using System.Collections.Generic;

namespace U8Co
{
    // vouchers/search：按单号包含、往来单位、部门、业务员、仓库、制单人、存货、日期、审核 / 关闭状态查单据。
    // 读路由，只跑 SQL（读线程池、登录缓存），只用 ctx.Conn，不碰 ctx.Session。
    // 与 vouchers/list 同一套列、同一套数据权限条件、同样按主键 keyset 分页（after = 上一页的 next）；
    // order 缺省 asc（按主键升序，字节不变）；desc 按单据日期、主键降序，next / after 是游标文本（DescPaging），响应另带 order: "desc"；
    // 条目同列表的完整条目，有往来单位列的类型另加 partner_name；给了 defines 时另加 defines（请求里各键的表头值）。不给 watermark（增量同步用 vouchers/list）。
    internal static class VoucherSearch
    {
        internal const string Path = "/u8co/v1/vouchers/search";
        internal const string Action = "search";

        public static ApiResult Run(WorkContext ctx)
        {
            Dictionary<string, object> body = ctx == null || ctx.Item == null ? null : ctx.Item.Body;
            VoucherSearchArgs args = VoucherSearchArgs.Parse(body);
            List<object> ps = new List<object>();
            string sql = VoucherSearchSql.Build(args, ps, ctx);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), args.Limit + 1);
            Dictionary<string, object> result = new Dictionary<string, object>();
            result["ok"] = true;
            result["type"] = args.Kind[ListKind.Name];
            ApiResult page = ListRoutes.Page(result, rows, VoucherSearchSql.Keys(args.Kind), args.Limit, null);
            result.Remove("watermark");
            if (args.Desc)
            {
                DescResult(result, rows, args.Limit);
            }
            VoucherSearchDefines.Attach(result, rows, args.Defines);
            return page;
        }

        // 降序：响应带 order，next 换成游标（上一页最后一行的日期和主键）。
        static void DescResult(Dictionary<string, object> result, List<Dictionary<string, object>> rows, int limit)
        {
            result["order"] = "desc";
            if (rows.Count <= limit)
            {
                return;
            }
            Dictionary<string, object> last = rows[limit - 1];
            result["next"] = DescPaging.Encode(GlSql.Col(last, VoucherSearchSql.CursorDate), GlSql.Int(last, "id"));
        }
    }
}
