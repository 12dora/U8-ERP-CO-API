using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 往来余额与账龄：Ar_Detail / Ap_Detail，iFlag<3（不含应收票据、现金），按登记日期 dRegDate 截止（含当天）。
    // 表名、列名只来自下面的固定表；科目前缀、客商编码、天数只进参数。
    internal static class ReportsArap
    {
        // side → { 明细表, 名称表, 编码列, 名称列, 余额表达式（应收借减贷，应付贷减借）, 专管业务员列 }。
        internal static readonly Dictionary<string, string[]> Sides = BuildSides();

        const string BalanceSql = "SELECT TOP (?) t.cDwCode partner, n.{NAME} pname,"
            + " CONVERT(decimal(18,2), t.d) debit, CONVERT(decimal(18,2), t.c) credit, CONVERT(decimal(18,2), {BAL}) bal"
            + " FROM (SELECT d.cDwCode, SUM(ISNULL(d.iDAmount,0)) d, SUM(ISNULL(d.iCAmount,0)) c FROM {TABLE} d"
            + " WHERE d.iFlag<3 AND d.dRegDate<DATEADD(day, 1, CONVERT(date, ?, 23)){FILTER} GROUP BY d.cDwCode) t"
            + " LEFT JOIN {NTABLE} n ON n.{CODE}=t.cDwCode WHERE 1=1";

        static Dictionary<string, string[]> BuildSides()
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.Ordinal);
            map.Add("ar", new string[] { "Ar_Detail", "Customer", "cCusCode", "cCusName", "t.d-t.c", "cCusPPerson" });
            map.Add("ap", new string[] { "Ap_Detail", "Vendor", "cVenCode", "cVenName", "t.c-t.d", "cVenPPerson" });
            return map;
        }

        public static ApiResult Balance(WorkContext ctx, ReportArgs a)
        {
            string[] side = Sides[a.Side];
            List<object> args = new List<object>();
            args.Add(a.Limit + 1);
            args.Add(a.AsOf);
            string filter = Filter(a, args);
            StringBuilder sql = new StringBuilder(Names(BalanceSql, side).Replace("{TABLE}", side[0])
                .Replace("{BAL}", side[4]).Replace("{FILTER}", filter));
            if (a.NonZero)
            {
                sql.Append(" AND ROUND(").Append(side[4]).Append(", 2)<>0");
            }
            // 数据权限：客户或供应商。
            PermHook.Where(sql, args, ctx, "t");
            sql.Append(" ORDER BY t.cDwCode");
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), args.ToArray(), a.Limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                Dictionary<string, object> item = Partner(rows[i]);
                item["debit"] = GlSql.Money(rows[i], "debit");
                item["credit"] = GlSql.Money(rows[i], "credit");
                item["balance"] = GlSql.Money(rows[i], "bal");
                items.Add(item);
            }
            return Page(a, rows, items);
        }

        // 分段描述：not_due（账龄 ≤ 0）、每段 from–to、最后一段 to 为 null。
        static List<object> BucketInfo(int[] days)
        {
            List<object> list = new List<object>(days.Length + 2);
            list.Add(Bucket("not_due", null, 0));
            int from = 1;
            for (int i = 0; i < days.Length; i++)
            {
                list.Add(Bucket(Idx(from) + "-" + Idx(days[i]), from, days[i]));
                from = days[i] + 1;
            }
            list.Add(Bucket(Idx(from) + "+", from, null));
            return list;
        }

        static Dictionary<string, object> Bucket(string key, object from, object to)
        {
            Dictionary<string, object> b = new Dictionary<string, object>();
            b["key"] = key;
            b["from"] = from;
            b["to"] = to;
            return b;
        }

        // 科目前缀（含 / 排除）、客商编码、翻页游标，全部进参数。按往来单位分组（partner）时游标才落在明细上。
        internal static string Filter(ReportArgs a, List<object> args)
        {
            StringBuilder sql = new StringBuilder();
            Reports.LikeAny(sql, args, "d.cCode", a.Accounts, false);
            Reports.LikeAny(sql, args, "d.cCode", a.Excludes, true);
            if (a.Partner.Length > 0)
            {
                sql.Append(" AND d.cDwCode=?");
                args.Add(a.Partner);
            }
            string[] after = a.GroupBy == "partner" ? Reports.Uncursor(a.After, 1) : null;
            if (after != null)
            {
                sql.Append(" AND d.cDwCode>?");
                args.Add(after[0]);
            }
            return sql.ToString();
        }

        internal static string Names(string sql, string[] side)
        {
            return sql.Replace("{NTABLE}", side[1]).Replace("{CODE}", side[2]).Replace("{NAME}", side[3]);
        }

        internal static Dictionary<string, object> Partner(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["partner"] = GlSql.Col(row, "partner");
            item["name"] = Reports.Text(row, "pname");
            return item;
        }

        internal static ApiResult Page(ReportArgs a, List<Dictionary<string, object>> rows, List<object> items)
        {
            string next = rows.Count > a.Limit ? Reports.Cursor(GlSql.Col(rows[a.Limit - 1], "partner")) : null;
            return Page(a, items, next);
        }

        internal static ApiResult Page(ReportArgs a, List<object> items, string next)
        {
            Dictionary<string, object> body = Reports.Body();
            body["side"] = a.Side;
            body["as_of"] = a.AsOf;
            if (a.Name == "arap_aging")
            {
                body["basis"] = a.Basis;
                body["group_by"] = a.GroupBy;
                body["buckets"] = BucketInfo(a.Buckets);
                if (a.HasDefaultCreditDays)
                {
                    body["default_credit_days"] = a.DefaultCreditDays;
                }
            }
            body["items"] = items;
            body["next"] = next;
            return ApiResult.Ok(body);
        }

        internal static string Idx(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
