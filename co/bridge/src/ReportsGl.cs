using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 月结状态（GL_mend）与科目余额表（GL_accsum，可叠加未记账凭证）。
    internal static class ReportsGl
    {
        // 模块代码与 GL_mend 列：总账是 bflag（没有 bflag_GL）。空值按未结账。
        static readonly string[] Modules = new string[] { "SA", "PU", "ST", "IA", "GL", "AR", "AP", "CA", "FA" };
        const string CloseSql = "SELECT iperiod, CONVERT(int, ISNULL(bflag_SA,0)) SA, CONVERT(int, ISNULL(bflag_PU,0)) PU,"
            + " CONVERT(int, ISNULL(bflag_ST,0)) ST, CONVERT(int, ISNULL(bflag_IA,0)) IA, CONVERT(int, ISNULL(bflag,0)) GL,"
            + " CONVERT(int, ISNULL(bflag_AR,0)) AR, CONVERT(int, ISNULL(bflag_AP,0)) AP, CONVERT(int, ISNULL(bflag_CA,0)) CA,"
            + " CONVERT(int, ISNULL(bflag_FA,0)) FA FROM GL_mend WHERE iyear=? AND iperiod BETWEEN 1 AND 12 ORDER BY iperiod";

        // 期初取 period_from 的年初余额 mb，期末取 period_to 的 me，按 cbegind_c / cendd_c 定正负（借正贷负）。
        // 各币种行一起合计本币。GL_accsum 只有记过账的科目才有行，所以从当年的科目表出发，
        // 只列有已记账数或（含未记账时）有未记账数的科目。
        // 含未记账（U8 余额表"包含未记账凭证"）：未记账凭证只落在末级科目上，按编码前缀汇到上级。
        // 1..period_from-1 的未记账进期初，period_from..period_to 的进本期，两者都进本年累计和期末，
        // 这样期末 = 期初 + 本期借 − 本期贷 仍然成立。
        // 绑定顺序：from, from, to, from, to, to, to, to, year | from, from, from, to, from, to, unposted, year, to
        // | year | year, grade_lo, grade_hi | limit。
        const string BalanceSql = ";WITH posted AS (SELECT s.ccode,"
            + " SUM(CASE WHEN s.iperiod=? THEN CASE s.cbegind_c WHEN N'借' THEN ISNULL(s.mb,0) WHEN N'贷' THEN -ISNULL(s.mb,0)"
            + " ELSE 0 END ELSE 0 END) signed_open,"
            + " SUM(CASE WHEN s.iperiod BETWEEN ? AND ? THEN ISNULL(s.md,0) ELSE 0 END) md,"
            + " SUM(CASE WHEN s.iperiod BETWEEN ? AND ? THEN ISNULL(s.mc,0) ELSE 0 END) mc,"
            + " SUM(CASE WHEN s.iperiod BETWEEN 1 AND ? THEN ISNULL(s.md,0) ELSE 0 END) ytd_md,"
            + " SUM(CASE WHEN s.iperiod BETWEEN 1 AND ? THEN ISNULL(s.mc,0) ELSE 0 END) ytd_mc,"
            + " SUM(CASE WHEN s.iperiod=? THEN CASE s.cendd_c WHEN N'借' THEN ISNULL(s.me,0) WHEN N'贷' THEN -ISNULL(s.me,0)"
            + " ELSE 0 END ELSE 0 END) signed_close"
            + " FROM GL_accsum s WHERE s.iyear=? AND s.iperiod BETWEEN 1 AND 12 GROUP BY s.ccode),"
            + " unp_leaf AS (SELECT v.ccode,"
            + " SUM(CASE WHEN v.iperiod<? THEN ISNULL(v.md,0) ELSE 0 END) md_pre,"
            + " SUM(CASE WHEN v.iperiod<? THEN ISNULL(v.mc,0) ELSE 0 END) mc_pre,"
            + " SUM(CASE WHEN v.iperiod BETWEEN ? AND ? THEN ISNULL(v.md,0) ELSE 0 END) md_per,"
            + " SUM(CASE WHEN v.iperiod BETWEEN ? AND ? THEN ISNULL(v.mc,0) ELSE 0 END) mc_per"
            + " FROM GL_accvouch v JOIN code c ON c.iyear=v.iyear AND c.ccode=v.ccode AND c.bend=1"
            + " WHERE ?=1 AND v.iyear=? AND v.iperiod BETWEEN 1 AND ? AND ISNULL(v.ibook,0)=0"
            + " AND (v.iflag IS NULL OR v.iflag<>1) GROUP BY v.ccode),"
            + " unp_all AS (SELECT p.ccode, SUM(u.md_pre) md_pre, SUM(u.mc_pre) mc_pre, SUM(u.md_per) md_per,"
            + " SUM(u.mc_per) mc_per FROM code p"
            + " JOIN unp_leaf u ON u.ccode LIKE p.ccode + N'%' WHERE p.iyear=? GROUP BY p.ccode),"
            + " t AS (SELECT c.ccode, c.ccode_name, c.igrade, CONVERT(int, c.bend) bend, c.cclass,"
            + " CONVERT(int, ISNULL(c.bproperty,1)) bproperty,"
            + " ISNULL(p.signed_open,0) + ISNULL(u.md_pre,0) - ISNULL(u.mc_pre,0) signed_open,"
            + " ISNULL(p.md,0) + ISNULL(u.md_per,0) period_d, ISNULL(p.mc,0) + ISNULL(u.mc_per,0) period_c,"
            + " ISNULL(p.ytd_md,0) + ISNULL(u.md_pre,0) + ISNULL(u.md_per,0) ytd_d,"
            + " ISNULL(p.ytd_mc,0) + ISNULL(u.mc_pre,0) + ISNULL(u.mc_per,0) ytd_c,"
            + " ISNULL(p.signed_close,0) + ISNULL(u.md_pre,0) + ISNULL(u.md_per,0) - ISNULL(u.mc_pre,0)"
            + " - ISNULL(u.mc_per,0) signed_close"
            + " FROM code c LEFT JOIN posted p ON p.ccode=c.ccode LEFT JOIN unp_all u ON u.ccode=c.ccode"
            + " WHERE c.iyear=? AND (p.ccode IS NOT NULL OR u.ccode IS NOT NULL) AND c.igrade BETWEEN ? AND ?)"
            + " SELECT TOP (?) ccode, ccode_name, igrade, bend, cclass, bproperty,"
            + " CONVERT(decimal(18,2), signed_open) signed_open, CONVERT(decimal(18,2), period_d) period_d,"
            + " CONVERT(decimal(18,2), period_c) period_c, CONVERT(decimal(18,2), ytd_d) ytd_d,"
            + " CONVERT(decimal(18,2), ytd_c) ytd_c, CONVERT(decimal(18,2), signed_close) signed_close"
            + " FROM t WHERE 1=1";
        const string NonZeroSql = " AND (ROUND(signed_open,2)<>0 OR ROUND(ytd_d,2)<>0 OR ROUND(ytd_c,2)<>0"
            + " OR ROUND(signed_close,2)<>0)";

        public static ApiResult CloseStatus(WorkContext ctx, ReportArgs a)
        {
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, CloseSql, new object[] { a.FiscalYear }, 12);
            List<object> periods = new List<object>(rows.Count);
            for (int i = 0; i < rows.Count; i++)
            {
                Dictionary<string, object> closed = new Dictionary<string, object>();
                for (int m = 0; m < Modules.Length; m++)
                {
                    closed[Modules[m]] = GlSql.Int(rows[i], Modules[m]) != 0;
                }
                Dictionary<string, object> period = new Dictionary<string, object>();
                period["period"] = GlSql.Int(rows[i], "iperiod");
                period["closed"] = closed;
                periods.Add(period);
            }
            Dictionary<string, object> body = Reports.Body();
            body["fiscal_year"] = a.FiscalYear;
            body["modules"] = new List<object>(Modules);
            body["periods"] = periods;
            return ApiResult.Ok(body);
        }

        public static ApiResult Balance(WorkContext ctx, ReportArgs a)
        {
            int y = a.FiscalYear;
            List<object> args = new List<object>(new object[]
            {
                a.From, a.From, a.To, a.From, a.To, a.To, a.To, a.To, y,
                a.From, a.From, a.From, a.To, a.From, a.To, a.Unposted ? 1 : 0, y, a.To,
                y, y, a.GradeFrom, a.GradeTo, a.Limit + 1
            });
            StringBuilder sql = new StringBuilder(BalanceSql);
            if (a.CodePrefix.Length > 0)
            {
                sql.Append(" AND ccode LIKE ?");
                args.Add(a.CodePrefix + "%");
            }
            if (a.LeafOnly)
            {
                sql.Append(" AND bend=1");
            }
            if (a.NonZero)
            {
                sql.Append(NonZeroSql);
            }
            string[] after = Reports.Uncursor(a.After, 1);
            if (after != null)
            {
                sql.Append(" AND ccode>?");
                args.Add(after[0]);
            }
            // 数据权限：科目。
            PermHook.Where(sql, args, ctx, null);
            sql.Append(" ORDER BY ccode");
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), args.ToArray(), a.Limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                items.Add(Item(rows[i]));
            }
            Dictionary<string, object> body = Reports.Body();
            body["fiscal_year"] = y;
            body["period_from"] = a.From;
            body["period_to"] = a.To;
            body["include_unposted"] = a.Unposted;
            body["items"] = items;
            body["next"] = rows.Count > a.Limit ? Reports.Cursor(GlSql.Col(rows[a.Limit - 1], "ccode")) : null;
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> Item(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["code"] = GlSql.Col(row, "ccode");
            item["name"] = GlSql.Col(row, "ccode_name");
            item["grade"] = GlSql.Int(row, "igrade");
            item["leaf"] = GlSql.Int(row, "bend") != 0;
            item["class"] = GlSql.Col(row, "cclass");
            item["natural_dir"] = GlSql.Int(row, "bproperty") != 0 ? "借" : "贷";
            Amounts(item, row);
            return item;
        }

        // 余额表共用的金额列：期初、本期、本年累计、期末（gl_aux_balance 也用）。
        internal static void Amounts(Dictionary<string, object> item, Dictionary<string, object> row)
        {
            Reports.PutBalance(item, "open", GlSql.Money(row, "signed_open"));
            item["period_debit"] = GlSql.Money(row, "period_d");
            item["period_credit"] = GlSql.Money(row, "period_c");
            item["ytd_debit"] = GlSql.Money(row, "ytd_d");
            item["ytd_credit"] = GlSql.Money(row, "ytd_c");
            Reports.PutBalance(item, "close", GlSql.Money(row, "signed_close"));
        }
    }
}
