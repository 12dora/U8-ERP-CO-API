using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    internal sealed class GlFilter
    {
        public int From;
        public int To;
        public string Sign;
        public string DateFrom;
        public string DateTo;
        public string Maker;
        public string State;
        public int[] After;
        public int Limit;
    }

    // 凭证列表：按（期间、类别序号、凭证号）键集翻页。after 是上一页返回的 next，调用方不必解析。
    internal static class GlList
    {
        const string Head = "SELECT TOP (?) iperiod, isignseq, csign, ino_id, CONVERT(varchar(10), MIN(dbill_date), 23) d,"
            + " MAX(ISNULL(cbill,'')) maker, MAX(ISNULL(ccheck,'')) checker, MAX(ISNULL(ccashier,'')) cashier,"
            + " MAX(ISNULL(ibook,0)) posted, MAX(ISNULL(iflag,0)) flag, SUM(ISNULL(md,0)) debit_total, COUNT(*) n"
            + " FROM GL_accvouch WHERE iyear=? AND iperiod BETWEEN ? AND ?";
        const string Tail = " GROUP BY iperiod, isignseq, csign, ino_id ORDER BY iperiod, isignseq, ino_id";
        internal const string AfterSql = " AND (iperiod>? OR (iperiod=? AND (isignseq>? OR (isignseq=? AND ino_id>?))))";
        static readonly string[] States = new string[] { "all", "unaudited", "audited", "posted", "void" };
        static readonly string[] StateSql = new string[]
        {
            "", " AND ISNULL(ccheck,'')='' AND ISNULL(iflag,0)<>1", " AND ISNULL(ccheck,'')<>''", " AND ibook=1",
            " AND iflag=1"
        };

        public static GlFilter Parse(Dictionary<string, object> body)
        {
            GlFilter f = new GlFilter();
            f.From = GlReq.IntIn(GlReq.Field(body, "period_from"), "period_from", 1, 12);
            f.To = GlReq.IntIn(GlReq.Field(body, "period_to"), "period_to", 1, 12);
            if (f.From > f.To)
            {
                throw GlReq.Bad("period_from 不能大于 period_to");
            }
            object sign = GlReq.Field(body, "sign");
            f.Sign = sign == null ? "" : GlReq.SignText(sign, "sign");
            f.DateFrom = GlReq.OptDate(GlReq.Field(body, "date_from"), "date_from");
            f.DateTo = GlReq.OptDate(GlReq.Field(body, "date_to"), "date_to");
            f.Maker = GlReq.OptText(body, "maker", 20);
            object state = GlReq.Field(body, "state");
            f.State = state == null ? "all" : state as string;
            if (f.State == null || Array.IndexOf(States, f.State) < 0)
            {
                throw GlReq.Bad("state 只能是 all、unaudited、audited、posted、void");
            }
            f.After = Cursor(GlReq.Field(body, "after"));
            object limit = GlReq.Field(body, "limit");
            f.Limit = limit == null ? 50 : GlReq.IntIn(limit, "limit", 1, 200);
            return f;
        }

        internal static int[] Cursor(object raw)
        {
            if (raw == null)
            {
                return null;
            }
            string text = raw as string;
            string[] parts = text == null ? new string[0] : text.Split('.');
            if (parts.Length != 3)
            {
                throw GlReq.Bad("after 游标无效");
            }
            int[] values = new int[3];
            for (int i = 0; i < 3; i++)
            {
                if (!int.TryParse(parts[i], NumberStyles.None, CultureInfo.InvariantCulture, out values[i]))
                {
                    throw GlReq.Bad("after 游标无效");
                }
            }
            return values;
        }

        public static ApiResult Run(WorkContext ctx, GlFilter f)
        {
            int year = GlState.LoginYear(ctx);
            List<object> args = new List<object>();
            args.Add(f.Limit + 1);
            args.Add(year);
            args.Add(f.From);
            args.Add(f.To);
            StringBuilder sql = new StringBuilder(Head);
            Where(sql, args, f);
            // 数据权限：同一张凭证全部分录的科目都放行才列出（从严；按凭证整张保留，借方合计只含可见凭证）。
            PermHook.Where(sql, args, ctx, null);
            sql.Append(Tail);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), args.ToArray(), f.Limit + 1);
            List<object> items = new List<object>();
            string next = null;
            for (int i = 0; i < rows.Count && i < f.Limit; i++)
            {
                items.Add(Item(rows[i]));
            }
            if (rows.Count > f.Limit)
            {
                Dictionary<string, object> last = rows[f.Limit - 1];
                next = GlSql.Col(last, "iperiod") + "." + GlSql.Col(last, "isignseq") + "." + GlSql.Col(last, "ino_id");
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["items"] = items;
            body["next"] = next;
            return ApiResult.Ok(body);
        }

        static void Where(StringBuilder sql, List<object> args, GlFilter f)
        {
            Add(sql, args, " AND csign=?", f.Sign);
            Add(sql, args, " AND dbill_date>=CONVERT(date, ?, 23)", f.DateFrom);
            Add(sql, args, " AND dbill_date<DATEADD(day, 1, CONVERT(date, ?, 23))", f.DateTo);
            Add(sql, args, " AND cbill=?", f.Maker);
            sql.Append(StateSql[Array.IndexOf(States, f.State)]);
            if (f.After != null)
            {
                sql.Append(AfterSql);
                args.Add(f.After[0]);
                args.Add(f.After[0]);
                args.Add(f.After[1]);
                args.Add(f.After[1]);
                args.Add(f.After[2]);
            }
        }

        static void Add(StringBuilder sql, List<object> args, string fragment, string value)
        {
            if (value == null || value.Length == 0)
            {
                return;
            }
            sql.Append(fragment);
            args.Add(value);
        }

        static Dictionary<string, object> Item(Dictionary<string, object> row)
        {
            int flag = GlSql.Int(row, "flag");
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["period"] = GlSql.Int(row, "iperiod");
            item["sign"] = GlSql.Col(row, "csign");
            item["no"] = GlSql.Int(row, "ino_id");
            item["date"] = GlSql.Col(row, "d");
            item["maker"] = GlSql.Col(row, "maker");
            item["checker"] = GlSql.Col(row, "checker");
            item["cashier"] = GlSql.Col(row, "cashier");
            item["posted"] = GlSql.Int(row, "posted") != 0;
            item["void"] = flag == 1;
            item["debit_total"] = GlSql.Money(row, "debit_total");
            item["lines"] = GlSql.Int(row, "n");
            return item;
        }
    }
}
