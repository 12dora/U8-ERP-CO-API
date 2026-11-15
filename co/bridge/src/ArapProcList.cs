using System;
using System.Collections.Generic;

namespace U8Co
{
    // arap/process/list（只读，读线程池）：应收 / 应付往来明细里的处理行（核销、转账、并账、红票对冲、汇兑损益、票据等，
    // 不含单据本身的审核行），给事件服务做增量和期间摘要。只用 ctx.Conn、登录日期和权限快照，不碰 ctx.Session。
    // 明细：watermark 是查询前已提交可见的最大 Auto_ID，ident 是 IDENT_CURRENT；回滚留下的空号不会再用，但在途事务可能先拿到
    // 较小的号、后提交，调用方要按 max(滞后量, ident - watermark) 回退重读。往来明细没有 rowversion，制单（cPZid）和
    // 取消（删除）看不出来，靠摘要按期间比对。fiscal_year 是处理所在的会计年度（ProcListSql.Fy），按它和 period 归期间，
    // 不要用 reg_date 的年份。
    internal static class ArapProcList
    {
        static readonly string[] IntKeys = new string[] { "id", "line_id", "gl_no", "period", "fiscal_year", "row_flag" };
        static readonly string[] ZeroNull = new string[] { "line_id", "gl_no" };
        static readonly string[] KeyOnly = new string[] { "id", "flag", "style", "code" };
        static readonly string[] Full = new string[]
        {
            "id", "flag", "style", "code", "vouch_type", "vouch_id", "co_vouch_type", "co_vouch_id", "partner", "dept", "person",
            "line_id", "debit_f", "credit_f", "pz_id", "gl_sign", "gl_no", "reg_date", "period", "fiscal_year", "row_flag"
        };

        public static ApiResult Run(WorkContext ctx)
        {
            ProcListArgs a = ArapProcListReq.Parse(ctx.Item.Body);
            int maxYear = ArapProcListReq.LoginYear(ctx.Item);
            List<int[]> states = ProcListSql.PeriodStates(ctx.Conn, a.Flag, maxYear);
            Dictionary<string, object> body = Reports.Body();
            body["flag"] = a.Flag;
            body["open_periods"] = Open(states);
            body["last_closed"] = LastClosed(states);
            if (a.Digest)
            {
                return ArapProcDigest.Run(ctx, a, states, body);
            }
            int watermark = ProcListSql.Watermark(ctx.Conn, a.Flag);
            int ident = ProcListSql.Ident(ctx.Conn, a.Flag, watermark);
            List<object> args = new List<object>();
            string sql = ProcListSql.ListSql(ctx, a, watermark, args);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, args.ToArray(), a.Limit + 1);
            List<object> items = new List<object>();
            object next = null;
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                Dictionary<string, object> item = Shape(rows[i], a.KeysOnly ? KeyOnly : Full);
                items.Add(item);
                next = item["id"];
            }
            body["items"] = items;
            body["next"] = rows.Count > a.Limit ? next : null;
            body["watermark"] = Text(watermark);
            body["ident"] = Text(ident);
            return ApiResult.Ok(body);
        }

        // 键固定，NULL 列给 null。整数列转 JSON 整数（行号、凭证号为 0 时给 null），金额（原币，两位小数）保持字符串。
        static Dictionary<string, object> Shape(Dictionary<string, object> row, string[] keys)
        {
            Dictionary<string, object> item = new Dictionary<string, object>(keys.Length);
            foreach (string key in keys)
            {
                object raw;
                row.TryGetValue(key, out raw);
                string text = raw as string;
                if (text == null || Array.IndexOf(IntKeys, key) < 0)
                {
                    item[key] = text;
                    continue;
                }
                int value = ProcListSql.Int(text);
                item[key] = value == 0 && Array.IndexOf(ZeroNull, key) >= 0 ? null : (object)value;
            }
            return item;
        }

        static List<object> Open(List<int[]> states)
        {
            List<object> list = new List<object>();
            foreach (int[] s in states)
            {
                if (s[2] == 0)
                {
                    list.Add(Period(s));
                }
            }
            return list;
        }

        // 最近一个已结账期间（年度、期间最大）；没有为 null。
        static object LastClosed(List<int[]> states)
        {
            for (int i = states.Count - 1; i >= 0; i--)
            {
                if (states[i][2] != 0)
                {
                    return Period(states[i]);
                }
            }
            return null;
        }

        internal static Dictionary<string, object> Period(int[] s)
        {
            Dictionary<string, object> p = new Dictionary<string, object>();
            p["year"] = s[0];
            p["period"] = s[1];
            return p;
        }

        internal static string Text(int value)
        {
            return value.ToString(System.Globalization.CultureInfo.InvariantCulture);
        }
    }
}
