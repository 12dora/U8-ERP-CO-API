using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 期初余额报表 opening_balance（只读，全是 SQL）：module=stock 库存期初（rdrecord34 / rdrecords34，单据类型 34），
    // arap 应收应付期初（Ap_Vouch bStartFlag=1，只有表头），gl 总账期初余额（ReportsOpeningGl）。
    // 每个模块都回 start_date（AccInformation 的 d<模块>StartDate）和 posted（GL_mend 第 0 期的期初记账标志）。
    // 期初写入不做：见 docs/limitations.md。表名、列名只来自下面的固定表，调用方的值只进参数。
    internal static class ReportsOpening
    {
        // 模块 → { AccInformation.cSysID, 启用日期 cName, GL_mend 列 }。总账是 bflag（没有 bflag_GL）。
        static readonly Dictionary<string, string[]> Flags = BuildFlags();

        const string StartSql = "SELECT TOP 1 LEFT(LTRIM(cValue), 10) v FROM AccInformation WHERE cSysID=? AND cName=?";
        const string PostedSql = "SELECT CONVERT(int, ISNULL({COL},0)) f FROM GL_mend WHERE iyear=? AND iperiod=0";

        // 派生表 m：一行一条期初表体。仓库、存货列名与 PermRegistry 的 stock 规则一致（cWhCode / cInvCode）。
        // 只取启用时录入的期初：bIsSTQc=1、不是年结结转来的（bFromPreYear，UFSTTransBalanceData 结转到下一年时
        // 插入的 34 单）、日期早于库存启用日期（{START} 为 " AND h.dDate<CONVERT(date, ?, 23)" 或空串）。
        // 参数：启用日期（有时）, 过滤… | limit+1 | nonzero、游标。
        const string StockSql = ";WITH s AS (SELECT m.cWhCode, m.cInvCode, m.batch, SUM(m.qty) qty, SUM(m.num) num,"
            + " SUM(m.amt) amt, COUNT(*) lines, SUM(CASE WHEN ISNULL(m.handler, N'')=N'' THEN 1 ELSE 0 END) unverified"
            + " FROM (SELECT h.cWhCode, b.cInvCode, ISNULL(b.cBatch, N'') batch, ISNULL(b.iQuantity,0) qty,"
            + " ISNULL(b.iNum,0) num, ISNULL(b.iPrice,0) amt, h.cHandler handler FROM rdrecord34 h"
            + " JOIN rdrecords34 b ON b.ID=h.ID WHERE h.cVouchType=N'34' AND ISNULL(h.bIsSTQc,0)=1"
            + " AND ISNULL(h.bFromPreYear,0)=0{START}) m WHERE 1=1{FILTER}"
            + " GROUP BY m.cWhCode, m.cInvCode, m.batch)"
            + " SELECT TOP (?) s.cWhCode wh, w.cWhName wh_name, s.cInvCode inv, i.cInvName inv_name, i.cInvStd inv_std,"
            + " s.batch, CONVERT(decimal(28,6), s.qty) qty, CONVERT(decimal(28,6), s.num) num,"
            + " CONVERT(decimal(18,2), s.amt) amt, s.lines, s.unverified FROM s"
            + " LEFT JOIN Warehouse w ON w.cWhCode=s.cWhCode LEFT JOIN Inventory i ON i.cInvCode=s.cInvCode WHERE 1=1";
        const string StockNonZero = " AND (ROUND(s.qty, 6)<>0 OR ROUND(s.amt, 2)<>0)";
        const string StockAfter = " AND (s.cWhCode>? OR (s.cWhCode=? AND (s.cInvCode>? OR (s.cInvCode=? AND s.batch>?))))";

        // 派生表 m：一行一张期初应收 / 应付单（只有表头）。往来单位列名与往来报表规则一致（cDwCode）。
        // bd_c：1 借方、0 贷方；金额 iAmount 是本币。{PT} 是客户或供应商档案，{PC}、{PN} 是其编码、名称列。
        // 参数：cFlag, 过滤… | limit+1, code 年度 | nonzero、游标。
        const string ArapSql = ";WITH s AS (SELECT m.cDwCode, m.cCode,"
            + " SUM(CASE WHEN m.bd=1 THEN m.amt ELSE 0 END) d, SUM(CASE WHEN m.bd=1 THEN 0 ELSE m.amt END) c,"
            + " COUNT(*) docs, SUM(CASE WHEN ISNULL(m.checker, N'')=N'' THEN 1 ELSE 0 END) unverified"
            + " FROM (SELECT v.cDwCode, ISNULL(v.cCode, N'') cCode, CONVERT(int, ISNULL(v.bd_c,0)) bd,"
            + " ISNULL(v.iAmount,0) amt, v.cCheckMan checker FROM Ap_Vouch v WHERE v.bStartFlag=1 AND v.cFlag=?) m"
            + " WHERE 1=1{FILTER} GROUP BY m.cDwCode, m.cCode)"
            + " SELECT TOP (?) s.cDwCode partner, n.{PN} partner_name, s.cCode code, k.ccode_name code_name,"
            + " CONVERT(decimal(18,2), s.d) d, CONVERT(decimal(18,2), s.c) c, s.docs, s.unverified FROM s"
            + " LEFT JOIN {PT} n ON n.{PC}=s.cDwCode LEFT JOIN code k ON k.iyear=? AND k.ccode=s.cCode WHERE 1=1";
        const string ArapNonZero = " AND (ROUND(s.d, 2)<>ROUND(s.c, 2))";
        const string ArapAfter = " AND (s.cDwCode>? OR (s.cDwCode=? AND s.cCode>?))";

        static Dictionary<string, string[]> BuildFlags()
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.Ordinal);
            map.Add("stock", new string[] { "ST", "dSTStartDate", "bflag_ST" });
            map.Add("ar", new string[] { "AR", "dARStartDate", "bflag_AR" });
            map.Add("ap", new string[] { "AP", "dAPStartDate", "bflag_AP" });
            map.Add("gl", new string[] { "GL", "dGLStartDate", "bflag" });
            // 采购管理：报表不收这个模块，只给期初记账 openings/post（OpeningPost）读启用日期。
            map.Add("pu", new string[] { "PU", "dPUStartDate", "bflag_PU" });
            return map;
        }

        public static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            OpeningArgs o = ReportsOpeningReq.Parse(a, ctx.Item.Body);
            PermContext p = PermCheck.Of(ctx);
            PermRule rule = PermRegistry.ForKey(ReportsOpeningReq.RuleKey(o));
            PermCheck.RequireRule(p, rule);
            if (o.Module == "gl")
            {
                return ReportsOpeningGl.Run(ctx, a, o, p, rule);
            }
            string key = o.Module == "arap" ? o.Side : o.Module;
            string start = StartDate(ctx, key);
            int year = YearOf(start, GlState.LoginYear(ctx));
            Dictionary<string, object> body = o.Module == "stock"
                ? Stock(ctx, a, new StockScope(o, p, rule, start)) : Arap(ctx, a, o, p, rule, year);
            Stamp(ctx, body, o, key, start, year);
            return ApiResult.Ok(body);
        }

        // 公共字段：模块、启用日期、期初所在年度与期初记账标志。
        internal static void Stamp(WorkContext ctx, Dictionary<string, object> body, OpeningArgs o, string key,
            string start, int year)
        {
            body["module"] = o.Module;
            body["side"] = o.Module == "arap" ? o.Side : null;
            body["start_date"] = start.Length == 0 ? null : start;
            body["opening_year"] = year;
            body["posted"] = Posted(ctx, key, year);
        }

        // 模块启用日期 yyyy-MM-dd；没有启用（没有这一项）时为空串。
        internal static string StartDate(WorkContext ctx, string key)
        {
            string[] f = Flags[key];
            string text = Rows.Scalar(ctx.Conn, StartSql, new object[] { f[0], f[1] });
            DateTime parsed;
            text = text == null ? "" : text.Trim();
            if (!DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed))
            {
                return "";
            }
            return text;
        }

        internal static int YearOf(string start, int fallback)
        {
            return start.Length >= 4 ? int.Parse(start.Substring(0, 4), CultureInfo.InvariantCulture) : fallback;
        }

        // GL_mend 第 0 期的期初记账标志；没有这一行按未记账。
        internal static bool Posted(WorkContext ctx, string key, int year)
        {
            string sql = PostedSql.Replace("{COL}", Flags[key][2]);
            string text = Rows.Scalar(ctx.Conn, sql, new object[] { year });
            return text != null && text.Trim() == "1";
        }

        // 库存期初的条件（凑成一个参数，函数参数不超过 6 个）。
        sealed class StockScope
        {
            public readonly OpeningArgs O;
            public readonly PermContext P;
            public readonly PermRule Rule;
            public readonly string Start;

            public StockScope(OpeningArgs o, PermContext p, PermRule rule, string start)
            {
                O = o;
                P = p;
                Rule = rule;
                Start = start;
            }
        }

        static Dictionary<string, object> Stock(WorkContext ctx, ReportArgs a, StockScope scope)
        {
            OpeningArgs o = scope.O;
            PermContext p = scope.P;
            PermRule rule = scope.Rule;
            List<object> ps = new List<object>();
            if (scope.Start.Length > 0)
            {
                ps.Add(scope.Start);
            }
            StringBuilder filter = new StringBuilder();
            ReportsStockSql.Equal(filter, ps, "m.cWhCode", o.Wh);
            ReportsStockSql.Equal(filter, ps, "m.cInvCode", o.Inv);
            ReportsStockSql.Equal(filter, ps, "m.batch", o.Batch);
            // 数据权限：仓库、存货。
            PermSql.AppendRule(filter, ps, p, rule, "m");
            ps.Add(a.Limit + 1);
            string start = scope.Start.Length > 0 ? " AND h.dDate<CONVERT(date, ?, 23)" : "";
            StringBuilder sql = new StringBuilder(StockSql.Replace("{START}", start).Replace("{FILTER}", filter.ToString()));
            if (o.NonZero)
            {
                sql.Append(StockNonZero);
            }
            string[] after = Reports.Uncursor(a.After, 3);
            if (after != null)
            {
                sql.Append(StockAfter);
                ps.AddRange(new object[] { after[0], after[0], after[1], after[1], after[2] });
            }
            sql.Append(" ORDER BY s.cWhCode, s.cInvCode, s.batch");
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), ps.ToArray(), a.Limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                items.Add(StockItem(rows[i]));
            }
            string next = null;
            if (rows.Count > a.Limit)
            {
                Dictionary<string, object> last = rows[a.Limit - 1];
                next = Reports.Cursor(GlSql.Col(last, "wh"), GlSql.Col(last, "inv"), GlSql.Col(last, "batch"));
            }
            return ReportsStockSql.Page(items, next);
        }

        static Dictionary<string, object> StockItem(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["wh_code"] = GlSql.Col(row, "wh");
            item["wh_name"] = Reports.Text(row, "wh_name");
            item["inv_code"] = GlSql.Col(row, "inv");
            item["inv_name"] = Reports.Text(row, "inv_name");
            item["inv_std"] = Reports.Text(row, "inv_std");
            item["batch"] = Reports.Text(row, "batch");
            item["qty"] = Reports.Qty(row, "qty");
            item["qty_aux"] = Reports.Qty(row, "num");
            item["amount"] = GlSql.Money(row, "amt");
            item["lines"] = GlSql.Int(row, "lines");
            item["unverified_lines"] = GlSql.Int(row, "unverified");
            return item;
        }

        static Dictionary<string, object> Arap(WorkContext ctx, ReportArgs a, OpeningArgs o, PermContext p, PermRule rule,
            int year)
        {
            bool ar = o.Side == "ar";
            List<object> ps = new List<object>();
            ps.Add(ar ? "AR" : "AP");
            string filter = ArapFilter(o, ps, p, rule);
            ps.Add(a.Limit + 1);
            ps.Add(year);
            string text = ArapSql.Replace("{FILTER}", filter).Replace("{PT}", ar ? "Customer" : "Vendor")
                .Replace("{PC}", ar ? "cCusCode" : "cVenCode").Replace("{PN}", ar ? "cCusName" : "cVenName");
            StringBuilder sql = new StringBuilder(text);
            if (o.NonZero)
            {
                sql.Append(ArapNonZero);
            }
            string[] after = Reports.Uncursor(a.After, 2);
            if (after != null)
            {
                sql.Append(ArapAfter);
                ps.AddRange(new object[] { after[0], after[0], after[1] });
            }
            sql.Append(" ORDER BY s.cDwCode, s.cCode");
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), ps.ToArray(), a.Limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                items.Add(ArapItem(rows[i]));
            }
            string next = rows.Count > a.Limit
                ? Reports.Cursor(GlSql.Col(rows[a.Limit - 1], "partner"), GlSql.Col(rows[a.Limit - 1], "code")) : null;
            return ReportsStockSql.Page(items, next);
        }

        // 往来单位、科目前缀（只进参数），最后是数据权限：应收按客户、应付按供应商。
        static string ArapFilter(OpeningArgs o, List<object> ps, PermContext p, PermRule rule)
        {
            StringBuilder filter = new StringBuilder();
            ReportsStockSql.Equal(filter, ps, "m.cDwCode", o.Partner);
            if (o.CodePrefix.Length > 0)
            {
                filter.Append(" AND m.cCode LIKE ?");
                ps.Add(o.CodePrefix + "%");
            }
            PermSql.AppendRule(filter, ps, p, rule, "m");
            return filter.ToString();
        }

        // 余额按借正贷负拆成 balance_dir / balance_debit / balance_credit（应收自然在借方，应付自然在贷方）。
        static Dictionary<string, object> ArapItem(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            decimal d = GlSql.Money(row, "d");
            decimal c = GlSql.Money(row, "c");
            item["partner_code"] = GlSql.Col(row, "partner");
            item["partner_name"] = Reports.Text(row, "partner_name");
            item["account"] = Reports.Text(row, "code");
            item["account_name"] = Reports.Text(row, "code_name");
            item["debit"] = d;
            item["credit"] = c;
            Reports.PutBalance(item, "balance", d - c);
            item["docs"] = GlSql.Int(row, "docs");
            item["unverified_docs"] = GlSql.Int(row, "unverified");
            return item;
        }
    }
}
