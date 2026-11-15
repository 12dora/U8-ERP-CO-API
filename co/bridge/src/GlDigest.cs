using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    internal sealed class GlDigestReq
    {
        // 0 表示取登录日期的年份。
        public int Year;
        // null 表示由桥按 GL_mend 填：未结账期间加最近 Closed 个已结账期间。
        public int[] Periods;
        public int Closed;
        public int[] After;
        public int Limit;
        public bool KeysOnly;
    }

    // 凭证摘要（事件源）：gl/vouchers/digest。GL_accvouch 没有 rowversion，事件服务按期间逐张比对指纹发现
    // 新增、删除、审核、签字、记账、作废和修改。只跑 SQL，走读线程，只用 ctx.Conn 和登录日期，不碰 ctx.Session。
    internal static class GlDigest
    {
        internal const string Op = "digest";
        internal const string Path = Requests.GlRoot + Op;
        public const int MaxLimit = 500;
        public const int DefaultLimit = 200;

        public static GlDigestReq Parse(Dictionary<string, object> body)
        {
            GlDigestReq req = new GlDigestReq();
            object year = GlReq.Field(body, "fiscal_year");
            req.Year = year == null ? 0 : GlReq.IntIn(year, "fiscal_year", 1900, 9999);
            object periods = GlReq.Field(body, "periods");
            object closed = GlReq.Field(body, "closed_periods");
            if (periods != null && closed != null)
            {
                throw GlReq.Bad("periods 与 closed_periods 不能同时给", "closed_periods");
            }
            req.Periods = periods == null ? null : Periods(periods);
            req.Closed = closed == null ? 1 : GlReq.IntIn(closed, "closed_periods", 0, 12);
            req.After = GlList.Cursor(GlReq.Field(body, "after"));
            object limit = GlReq.Field(body, "limit");
            req.Limit = limit == null ? DefaultLimit : GlReq.IntIn(limit, "limit", 1, MaxLimit);
            req.KeysOnly = ListArgs.OptBool(body, "keys_only", "keys_only");
            return req;
        }

        // 1 到 12 个不重复的期间（1 到 12），按升序返回。
        internal static int[] Periods(object raw)
        {
            object[] list = raw as object[];
            if (list == null || list.Length < 1 || list.Length > 12)
            {
                throw GlReq.Bad("periods 必须是 1 到 12 个期间的数组", "periods");
            }
            List<int> seen = new List<int>();
            for (int i = 0; i < list.Length; i++)
            {
                int period = GlReq.IntIn(list[i], "periods", 1, 12);
                if (seen.Contains(period))
                {
                    throw GlReq.Bad("periods 不能重复", "periods");
                }
                seen.Add(period);
            }
            seen.Sort();
            return seen.ToArray();
        }

        public static ApiResult Run(WorkContext ctx, GlDigestReq req)
        {
            int year = req.Year > 0 ? req.Year : GlState.LoginYear(ctx);
            int[] periods = req.Periods != null ? req.Periods : GlDigestSql.DefaultPeriods(ctx.Conn, year, req.Closed);
            // 水位在翻页查询之前取，与列表的约定一致。
            string[] marks = Marks(ctx, year, periods);
            List<Dictionary<string, object>> rows = Page(ctx, req, year, periods);
            List<object> items = new List<object>();
            string next = null;
            for (int i = 0; i < rows.Count && i < req.Limit; i++)
            {
                items.Add(Item(rows[i], req.KeysOnly));
            }
            if (rows.Count > req.Limit)
            {
                next = GlDigestSql.Next(rows[req.Limit - 1]);
            }
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["fiscal_year"] = year;
            body["periods"] = new List<int>(periods);
            body["items"] = items;
            body["next"] = next;
            body["watermark"] = marks[0];
            body["ident"] = marks[1];
            return ApiResult.Ok(body);
        }

        static string[] Marks(WorkContext ctx, int year, int[] periods)
        {
            List<object> args = new List<object>();
            StringBuilder sql = new StringBuilder(GlDigestSql.MarkHead);
            GlDigestSql.Scope(sql, args, year, periods);
            PermHook.Where(sql, args, ctx, null);
            Dictionary<string, object> row = Rows.One(ctx.Conn, sql.ToString(), args.ToArray());
            string mark = GlSql.Col(row, "w");
            string ident = GlSql.Col(row, "ident");
            return new string[] { mark.Length == 0 ? "0" : mark, ident.Length == 0 ? "0" : ident };
        }

        static List<Dictionary<string, object>> Page(WorkContext ctx, GlDigestReq req, int year, int[] periods)
        {
            if (periods.Length == 0)
            {
                return new List<Dictionary<string, object>>();
            }
            List<object> args = new List<object>();
            StringBuilder sql = GlDigestSql.PageSql(req, year, periods, args);
            // 数据权限：与 gl/vouchers/list 相同（整张凭证的科目全部放行才给，debit_total 只含可见凭证），条件放在 WHERE 最后。
            PermHook.Where(sql, args, ctx, null);
            sql.Append(GlDigestSql.Tail);
            return Rows.Query(ctx.Conn, sql.ToString(), args.ToArray(), req.Limit + 1);
        }

        static Dictionary<string, object> Item(Dictionary<string, object> row, bool keysOnly)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["period"] = GlSql.Int(row, "iperiod");
            item["sign"] = GlSql.Col(row, "csign");
            item["no"] = GlSql.Int(row, "ino_id");
            item["fingerprint"] = GlDigestSql.Fingerprint(row);
            if (keysOnly)
            {
                return item;
            }
            item["date"] = GlSql.Col(row, "d");
            item["maker"] = GlSql.Col(row, "maker");
            item["checker"] = GlSql.Col(row, "checker");
            item["cashier"] = GlSql.Col(row, "cashier");
            item["bookkeeper"] = GlSql.Col(row, "book");
            item["posted"] = GlSql.Int(row, "posted") != 0;
            item["void"] = GlSql.Int(row, "flag") == 1;
            item["debit_total"] = GlSql.Money(row, "dsum");
            item["lines"] = GlSql.Int(row, "n");
            return item;
        }
    }
}
