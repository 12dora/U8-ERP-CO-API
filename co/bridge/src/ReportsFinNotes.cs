using System.Collections.Generic;

namespace U8Co
{
    // 到期票据 notes_due：应收票据（AR）或应付票据（AP）按到期日（dExpireDate）落在 [expire_from, expire_to] 的清单。
    // 缺省 expire_from 取登录日期，expire_to 取 expire_from 后 30 天；open_only 缺省 true：只列余额不为 0 的票据。
    // 状态：余额不为 0 是 open；余额为 0 时看最近一次处理（AP_Note_Sub.cProcStyle）：9D 贴现 discounted、9E 背书 endorsed、
    // 9C 退回 returned，其余（9A 结算或没有处理记录）settled。days_to_due = 到期日 − 登录日期（过期为负）。
    // 往来单位编码为空的票据（多数应收票据只有名称）partner_code 为 null，partner_name 取票据上的名称。
    // totals 按全部符合条件的票据算，不受 limit 影响。
    internal static class ReportsFinNotes
    {
        public static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            FinNotesArgs q = ReportsFinParse.Notes(ctx.Item.Body);
            string today = GlState.LoginDate(ctx);
            string from = q.ExpireFrom.Length > 0 ? q.ExpireFrom : today;
            string to = q.ExpireTo.Length > 0 ? q.ExpireTo : ReportsFinParse.DueTo(from);
            if (string.CompareOrdinal(from, to) > 0)
            {
                throw GlReq.Bad("expire_from 不能晚于 expire_to（缺省为 expire_from 后 30 天）", "expire_from");
            }
            List<object> ps = new List<object>();
            string sql = ReportsFinNotesSql.List(ctx, q, from, to, q.Limit + 1, ps);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), q.Limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < q.Limit; i++)
            {
                items.Add(Item(rows[i], today));
            }
            Dictionary<string, object> body = Reports.Body();
            body["side"] = q.Side;
            body["expire_from"] = from;
            body["expire_to"] = to;
            body["open_only"] = q.OpenOnly;
            body["as_of"] = today;
            body["items"] = items;
            body["count"] = items.Count;
            body["truncated"] = rows.Count > q.Limit;
            body["totals"] = Totals(ctx, q, from, to);
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> Totals(WorkContext ctx, FinNotesArgs q, string from, string to)
        {
            List<object> ps = new List<object>();
            string sql = ReportsFinNotesSql.Total(ctx, q, from, to, ps);
            Dictionary<string, object> row = Rows.One(ctx.Conn, sql, ps.ToArray());
            Dictionary<string, object> totals = new Dictionary<string, object>();
            totals["count"] = GlSql.Int(row, "cnt");
            totals["amount"] = GlSql.Money(row, "amount");
            totals["remainder"] = GlSql.Money(row, "remainder");
            return totals;
        }

        internal static Dictionary<string, object> Item(Dictionary<string, object> row, string today)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            decimal remainder = GlSql.Money(row, "remainder");
            string expire = GlSql.Col(row, "expire_date");
            item["code"] = GlSql.Col(row, "code");
            item["note_type"] = Reports.Text(row, "note_type");
            item["partner_code"] = Reports.Text(row, "partner_code");
            item["partner_name"] = Reports.Text(row, "partner_name");
            item["sign_date"] = Reports.Text(row, "sign_date");
            item["expire_date"] = Reports.Text(row, "expire_date");
            item["amount"] = GlSql.Money(row, "amount");
            item["remainder"] = remainder;
            item["status"] = StatusOf(remainder, GlSql.Col(row, "last_proc"));
            item["days_to_due"] = ReportsFinParse.DaysBetween(today, expire);
            return item;
        }

        // 余额不为 0（含部分背书、部分贴现后还剩的）是 open；清零的按最近一次处理定。
        internal static string StatusOf(decimal remainder, string lastProc)
        {
            if (remainder != 0m)
            {
                return "open";
            }
            switch (lastProc)
            {
                case "9D":
                    return "discounted";
                case "9E":
                    return "endorsed";
                case "9C":
                    return "returned";
                default:
                    return "settled";
            }
        }
    }
}
