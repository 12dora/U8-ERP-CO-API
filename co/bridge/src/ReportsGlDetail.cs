using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 总账明细账 gl_detail：期初取自科目总账（与 gl_balance 同一规则：起始期间的年初余额 mb 按 cbegind_c 定正负），
    // 明细取自 GL_accvouch（不含作废；缺省只含已记账），逐行滚动余额。
    // 期初按末级科目合计：不带辅助核算的末级科目取 GL_accsum，带辅助核算的取 GL_accass（两者对账户合计相同，
    // 已在测试账套逐月核对），这样辅助项条件和辅助项数据权限在期初和明细上一致。
    // 含未记账时，起始期间之前的未记账凭证进期初（同 gl_balance）；按日期查询时，起始期间里 date_from 之前的凭证也进期初。
    // 表名、列名只来自下面的常量；科目编码、辅助项编码、日期只进参数。
    internal static class ReportsGlDetail
    {
        const string CodeSql = "SELECT ccode, ccode_name, CONVERT(int, bend) bend FROM code WHERE iyear=? AND ccode=?";
        const string NoAux = "ISNULL(c.bperson,0)=0 AND ISNULL(c.bcus,0)=0 AND ISNULL(c.bsup,0)=0 AND ISNULL(c.bdept,0)=0"
            + " AND ISNULL(c.bitem,0)=0";

        // 绑定顺序：year, period_from, period_to, unposted, [date_from, date_to], {明细条件}。
        const string InnerSql = "SELECT v.iperiod, CONVERT(char(10), v.dbill_date, 23) dt, ISNULL(v.isignseq,0) sq, v.csign,"
            + " v.ino_id, v.inid, v.cdigest, v.ccode, c.ccode_name, ISNULL(v.md,0) md, ISNULL(v.mc,0) mc,"
            + " CONVERT(int, ISNULL(v.ibook,0)) posted, v.cdept_id, v.cperson_id, v.ccus_id, v.csup_id, v.citem_class,"
            + " v.citem_id FROM GL_accvouch v LEFT JOIN code c ON c.iyear=v.iyear AND c.ccode=v.ccode"
            + " WHERE v.iyear=? AND v.iperiod BETWEEN ? AND ? AND ISNULL(v.iflag,0)<>1 AND (v.ibook=1 OR ?=1)";
        const string DatesSql = " AND v.dbill_date>=CONVERT(date, ?, 23) AND v.dbill_date<DATEADD(day, 1, CONVERT(date, ?, 23))";
        const string PageSql = "SELECT TOP (?) r.iperiod, r.dt, r.sq, r.csign, r.ino_id, r.inid, r.cdigest, r.ccode,"
            + " r.ccode_name, CONVERT(decimal(18,2), r.md) md, CONVERT(decimal(18,2), r.mc) mc, r.posted, r.cdept_id,"
            + " r.cperson_id, r.ccus_id, r.csup_id, r.citem_class, r.citem_id FROM ({INNER}) r WHERE 1=1";
        // 区间合计和游标之前（含游标那一行）的发生额。{BEFORE} 的参数在 {INNER} 之前。
        const string TotalSql = "SELECT CONVERT(decimal(18,2), ISNULL(SUM(r.md),0)) td, CONVERT(decimal(18,2), ISNULL(SUM(r.mc),0)) tc,"
            + " CONVERT(decimal(18,2), ISNULL(SUM(CASE WHEN {BEFORE} THEN r.md-r.mc ELSE 0 END),0)) carry FROM ({INNER}) r";
        const string OrderSql = " ORDER BY r.iperiod, r.dt, r.sq, r.ino_id, r.inid";
        static readonly string[] CursorCols = new string[] { "r.iperiod", "r.dt", "r.sq", "r.ino_id", "r.inid" };

        public static ApiResult Detail(WorkContext ctx, ReportArgs a)
        {
            DetailArgs d = ReportsDetailReq.Parse(a.Name, ctx.Item.Body);
            bool dates = d.DateFrom.Length > 0;
            int year = dates ? int.Parse(d.DateFrom.Substring(0, 4), CultureInfo.InvariantCulture)
                : a.FiscalYear;
            Dictionary<string, object> account = Rows.One(ctx.Conn, CodeSql, new object[] { year, d.Code });
            if (account == null)
            {
                throw new BridgeException(404, "not_found", "科目 " + d.Code + " 在 " + ReportsArap.Idx(year) + " 年度不存在");
            }
            object[] after = CursorValues(Reports.Uncursor(a.After, 5));
            decimal open = Opening(ctx, d, year);
            Dictionary<string, object> totals = Totals(ctx, d, year, after);
            List<Dictionary<string, object>> rows = Page(ctx, d, year, after);
            decimal balance = open + GlSql.Money(totals, "carry");
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < d.Limit; i++)
            {
                Dictionary<string, object> item = Item(rows[i]);
                balance += (decimal)item["debit"] - (decimal)item["credit"];
                PutRunning(item, balance);
                items.Add(item);
            }
            Dictionary<string, object> body = Reports.Body();
            body["fiscal_year"] = year;
            body["code"] = d.Code;
            body["name"] = GlSql.Col(account, "ccode_name");
            body["leaf"] = GlSql.Int(account, "bend") != 0;
            body["include_sub"] = d.IncludeSub;
            body["include_unposted"] = d.Unposted;
            body["period_from"] = d.PeriodFrom;
            body["period_to"] = d.PeriodTo;
            body["date_from"] = dates ? d.DateFrom : null;
            body["date_to"] = dates ? d.DateTo : null;
            Summary(body, open, totals);
            body["items"] = items;
            body["next"] = rows.Count > d.Limit ? Next(rows[d.Limit - 1]) : null;
            return ApiResult.Ok(body);
        }

        static void Summary(Dictionary<string, object> body, decimal open, Dictionary<string, object> totals)
        {
            decimal debit = GlSql.Money(totals, "td");
            decimal credit = GlSql.Money(totals, "tc");
            Reports.PutBalance(body, "open", open);
            body["total_debit"] = debit;
            body["total_credit"] = credit;
            Reports.PutBalance(body, "close", open + debit - credit);
        }

        // 游标：期间、日期、凭证类别序号、凭证号、分录号；整数段不是整数时 400。
        static object[] CursorValues(string[] parts)
        {
            if (parts == null)
            {
                return null;
            }
            return new object[]
            {
                ReportsDetailReq.CursorInt(parts[0]), parts[1], ReportsDetailReq.CursorInt(parts[2]),
                ReportsDetailReq.CursorInt(parts[3]), ReportsDetailReq.CursorInt(parts[4])
            };
        }

        // 元组比较 (c0, c1, …) > (v0, v1, …)：(c0>? OR (c0=? AND (c1>? OR …)))。列名只来自调用方的常量，值只进参数。
        internal static string Greater(string[] cols, object[] vals, List<object> args)
        {
            StringBuilder sql = new StringBuilder();
            int last = cols.Length - 1;
            for (int i = 0; i < last; i++)
            {
                sql.Append("(").Append(cols[i]).Append(">? OR (").Append(cols[i]).Append("=? AND ");
                args.Add(vals[i]);
                args.Add(vals[i]);
            }
            sql.Append(cols[last]).Append(">?");
            args.Add(vals[last]);
            for (int i = 0; i < last; i++)
            {
                sql.Append("))");
            }
            return sql.ToString();
        }

        static string Inner(WorkContext ctx, DetailArgs d, int year, List<object> args)
        {
            StringBuilder sql = new StringBuilder(InnerSql);
            args.Add(year);
            args.Add(d.PeriodFrom);
            args.Add(d.PeriodTo);
            args.Add(d.Unposted ? 1 : 0);
            if (d.DateFrom.Length > 0)
            {
                sql.Append(DatesSql);
                args.Add(d.DateFrom);
                args.Add(d.DateTo);
            }
            VoucherFilter(ctx, d, sql, args);
            return sql.ToString();
        }

        // 凭证行条件：科目、辅助项、数据权限（科目与各辅助项，同 gl_aux_balance）。
        static void VoucherFilter(WorkContext ctx, DetailArgs d, StringBuilder sql, List<object> args)
        {
            Codes(d, sql, args, "v");
            Aux(d, sql, args, "v");
            PermHook.Where(sql, args, ctx, "v");
        }

        static void Codes(DetailArgs d, StringBuilder sql, List<object> args, string alias)
        {
            sql.Append(" AND ").Append(alias).Append(d.IncludeSub ? ".ccode LIKE ?" : ".ccode=?");
            args.Add(d.IncludeSub ? d.Code + "%" : d.Code);
        }

        static bool HasAux(DetailArgs d)
        {
            return d.Customer.Length + d.Vendor.Length + d.Dept.Length + d.Person.Length + d.Project.Length > 0;
        }

        // 辅助项条件，GL_accvouch 与 GL_accass 列名相同。
        static void Aux(DetailArgs d, StringBuilder sql, List<object> args, string alias)
        {
            string[] cols = new string[] { "ccus_id", "csup_id", "cdept_id", "cperson_id", "citem_id", "citem_class" };
            string[] vals = new string[] { d.Customer, d.Vendor, d.Dept, d.Person, d.Project, d.ProjectClass };
            for (int i = 0; i < cols.Length; i++)
            {
                if (vals[i].Length > 0)
                {
                    sql.Append(" AND ").Append(alias).Append(".").Append(cols[i]).Append("=?");
                    args.Add(vals[i]);
                }
            }
        }

        static decimal Opening(WorkContext ctx, DetailArgs d, int year)
        {
            List<object> args = new List<object>(new object[] { year, d.PeriodFrom });
            StringBuilder sql = new StringBuilder("SELECT CONVERT(decimal(18,2), ISNULL(SUM(u.v),0)) ob FROM (SELECT ")
                .Append(Signed("s")).Append(" v FROM GL_accsum s JOIN code c ON c.iyear=s.iyear AND c.ccode=s.ccode")
                .Append(" AND c.bend=1 AND ").Append(NoAux).Append(" WHERE s.iyear=? AND s.iperiod=?");
            Codes(d, sql, args, "s");
            // 不带辅助核算的科目没有辅助项，带辅助项条件时这一段为空（明细行同样查不到）。
            sql.Append(HasAux(d) ? " AND 1=0" : "");
            AccountPerm(ctx, sql, args, "s.ccode");
            sql.Append(" UNION ALL SELECT ").Append(Signed("x")).Append(" FROM GL_accass x JOIN code c ON c.iyear=x.iyear")
                .Append(" AND c.ccode=x.ccode AND c.bend=1 AND NOT (").Append(NoAux).Append(") WHERE x.iyear=? AND x.iperiod=?");
            args.Add(year);
            args.Add(d.PeriodFrom);
            Codes(d, sql, args, "x");
            Aux(d, sql, args, "x");
            PermHook.Where(sql, args, ctx, "x");
            OpeningVouchers(ctx, d, year, sql, args);
            sql.Append(") u");
            Dictionary<string, object> row = Rows.One(ctx.Conn, sql.ToString(), args.ToArray());
            return GlSql.Money(row, "ob");
        }

        // 期初里的凭证：起始期间之前的未记账凭证（含未记账时），按日期时起始期间里 date_from 之前的凭证。
        static void OpeningVouchers(WorkContext ctx, DetailArgs d, int year, StringBuilder sql, List<object> args)
        {
            bool dates = d.DateFrom.Length > 0;
            if (!d.Unposted && !dates)
            {
                return;
            }
            sql.Append(" UNION ALL SELECT ISNULL(v.md,0)-ISNULL(v.mc,0) FROM GL_accvouch v WHERE v.iyear=?")
                .Append(" AND v.iperiod BETWEEN 1 AND ? AND ISNULL(v.iflag,0)<>1 AND (");
            args.Add(year);
            args.Add(d.PeriodFrom);
            if (d.Unposted)
            {
                sql.Append("(ISNULL(v.ibook,0)=0 AND v.iperiod<?)");
                args.Add(d.PeriodFrom);
            }
            if (dates)
            {
                sql.Append(d.Unposted ? " OR " : "")
                    .Append("(v.iperiod=? AND v.dbill_date<CONVERT(date, ?, 23) AND (v.ibook=1 OR ?=1))");
                args.Add(d.PeriodFrom);
                args.Add(d.DateFrom);
                args.Add(d.Unposted ? 1 : 0);
            }
            sql.Append(")");
            VoucherFilter(ctx, d, sql, args);
        }

        static string Signed(string alias)
        {
            return "CASE " + alias + ".cbegind_c WHEN N'借' THEN ISNULL(" + alias + ".mb,0) WHEN N'贷' THEN -ISNULL("
                + alias + ".mb,0) ELSE 0 END";
        }

        // GL_accsum 只有科目列：只按科目数据权限过滤（总账选项 bQryCtlSubj 打开时，见 PermContext.Controls）。
        static void AccountPerm(WorkContext ctx, StringBuilder sql, List<object> args, string expr)
        {
            if (ctx == null || ctx.Item == null || !PermRegistry.IsRead(ctx.Item.Path))
            {
                return;
            }
            PermSql.AppendCode(sql, args, PermCheck.Of(ctx), PermObj.Account, expr, false);
        }

        static Dictionary<string, object> Totals(WorkContext ctx, DetailArgs d, int year, object[] after)
        {
            List<object> args = new List<object>();
            string before = after == null ? "1=0" : "NOT " + Greater(CursorCols, after, args);
            string sql = TotalSql.Replace("{BEFORE}", before).Replace("{INNER}", Inner(ctx, d, year, args));
            return Rows.One(ctx.Conn, sql, args.ToArray());
        }

        static List<Dictionary<string, object>> Page(WorkContext ctx, DetailArgs d, int year, object[] after)
        {
            List<object> args = new List<object>();
            args.Add(d.Limit + 1);
            StringBuilder sql = new StringBuilder(PageSql.Replace("{INNER}", Inner(ctx, d, year, args)));
            if (after != null)
            {
                sql.Append(" AND ").Append(Greater(CursorCols, after, args));
            }
            sql.Append(OrderSql);
            return Rows.Query(ctx.Conn, sql.ToString(), args.ToArray(), d.Limit + 1);
        }

        // 分录列名与 gl/vouchers/load 的 lines 一致。
        static Dictionary<string, object> Item(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["period"] = GlSql.Int(row, "iperiod");
            item["date"] = GlSql.Col(row, "dt");
            item["sign"] = GlSql.Col(row, "csign");
            item["no"] = GlSql.Int(row, "ino_id");
            item["entry"] = GlSql.Int(row, "inid");
            item["digest"] = GlSql.Col(row, "cdigest");
            item["account"] = GlSql.Col(row, "ccode");
            item["account_name"] = GlSql.Col(row, "ccode_name");
            item["debit"] = GlSql.Money(row, "md");
            item["credit"] = GlSql.Money(row, "mc");
            item["posted"] = GlSql.Int(row, "posted") != 0;
            item["dept"] = Reports.Text(row, "cdept_id");
            item["person"] = Reports.Text(row, "cperson_id");
            item["customer"] = Reports.Text(row, "ccus_id");
            item["supplier"] = Reports.Text(row, "csup_id");
            item["item_class"] = Reports.Text(row, "citem_class");
            item["item"] = Reports.Text(row, "citem_id");
            return item;
        }

        // 滚动余额：dir 借 / 贷 / 平，balance 为绝对值（同余额表的方向拆分）。
        static void PutRunning(Dictionary<string, object> item, decimal signed)
        {
            decimal value = decimal.Round(signed, 2, MidpointRounding.AwayFromZero);
            item["dir"] = value > 0 ? "借" : (value < 0 ? "贷" : "平");
            item["balance"] = value < 0 ? -value : value;
        }

        static string Next(Dictionary<string, object> row)
        {
            return Reports.Cursor(GlSql.Col(row, "iperiod"), GlSql.Col(row, "dt"), GlSql.Col(row, "sq"),
                GlSql.Col(row, "ino_id"), GlSql.Col(row, "inid"));
        }
    }
}
