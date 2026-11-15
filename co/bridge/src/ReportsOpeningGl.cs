using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 总账期初余额（opening_balance module=gl，只读）：GL_accsum / GL_accass 没有第 0 期，期初是「期初期间」那一行的 mb。
    // 期初期间：fiscal_year 等于总账启用年度时是启用月份（年中启用时启用前各期是空壳，mb 相同），其余年度是 1 月
    // （年结后上年 me 转成本年 mb）。启用前累计借贷取期初期间之前各期的 md / mc。
    // 试算平衡只在第一页按科目时给：末级科目期初借方合计与贷方合计（同样受数据权限过滤）。
    internal static class ReportsOpeningGl
    {
        // dim → { GL_accass 列, 名称表, 名称表编码列, 名称表名称列 }（同 ReportsAux）。项目的名称在按大类分开的表里，不取。
        static readonly Dictionary<string, string[]> Dims = BuildDims();

        // 按科目。参数：p0, p0, p0, year, p0 | year | 过滤… ；T 之后再接 TOP 或试算。
        const string BaseSql = ";WITH s AS (SELECT x.ccode,"
            + " SUM(CASE WHEN x.iperiod=? THEN CASE x.cbegind_c WHEN N'借' THEN ISNULL(x.mb,0) WHEN N'贷' THEN -ISNULL(x.mb,0)"
            + " ELSE 0 END ELSE 0 END) signed_open,"
            + " SUM(CASE WHEN x.iperiod<? THEN ISNULL(x.md,0) ELSE 0 END) pre_d,"
            + " SUM(CASE WHEN x.iperiod<? THEN ISNULL(x.mc,0) ELSE 0 END) pre_c"
            + " FROM GL_accsum x WHERE x.iyear=? AND x.iperiod BETWEEN 1 AND ? GROUP BY x.ccode),"
            + " t AS (SELECT s.ccode, c.ccode_name, c.igrade, CONVERT(int, c.bend) bend, CONVERT(int, ISNULL(c.bproperty,1)) bproperty,"
            + " s.signed_open, s.pre_d, s.pre_c FROM s JOIN code c ON c.iyear=? AND c.ccode=s.ccode)";
        const string PageSql = " SELECT TOP (?) ccode, ccode_name, igrade, bend, bproperty,"
            + " CONVERT(decimal(18,2), signed_open) signed_open, CONVERT(decimal(18,2), pre_d) pre_d,"
            + " CONVERT(decimal(18,2), pre_c) pre_c FROM t WHERE 1=1";
        const string TrialSql = " SELECT CONVERT(decimal(18,2), SUM(CASE WHEN ROUND(signed_open,2)>0 THEN signed_open ELSE 0 END)) d,"
            + " CONVERT(decimal(18,2), SUM(CASE WHEN ROUND(signed_open,2)<0 THEN -signed_open ELSE 0 END)) c FROM t WHERE bend=1";
        const string NonZeroSql = " AND (ROUND(signed_open,2)<>0 OR ROUND(pre_d,2)<>0 OR ROUND(pre_c,2)<>0)";

        // 按辅助项。{CLS}、{CLSG}、{DIM}、{NAME}、{JOIN} 同 ReportsAux。参数：year, p0, 过滤… | limit+1, year | nonzero、游标。
        const string AuxSql = ";WITH a AS (SELECT x.ccode, {CLS} cls, x.{DIM} dim_code,"
            + " SUM(CASE x.cbegind_c WHEN N'借' THEN ISNULL(x.mb,0) WHEN N'贷' THEN -ISNULL(x.mb,0) ELSE 0 END) signed_open"
            + " FROM GL_accass x WHERE x.iyear=? AND x.iperiod=?"
            + " AND NULLIF(LTRIM(RTRIM(ISNULL(x.{DIM}, N''))), N'') IS NOT NULL{FILTER}"
            + " GROUP BY x.ccode{CLSG}, x.{DIM})"
            + " SELECT TOP (?) a.ccode, c.ccode_name, a.cls, a.dim_code, {NAME} dim_name,"
            + " CONVERT(decimal(18,2), a.signed_open) signed_open"
            + " FROM a LEFT JOIN code c ON c.iyear=? AND c.ccode=a.ccode{JOIN} WHERE 1=1";
        const string AuxAfter = " AND (a.ccode>? OR (a.ccode=? AND (a.cls>? OR (a.cls=? AND a.dim_code>?))))";

        static Dictionary<string, string[]> BuildDims()
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.Ordinal);
            map.Add("customer", new string[] { "ccus_id", "Customer", "cCusCode", "cCusName" });
            map.Add("vendor", new string[] { "csup_id", "Vendor", "cVenCode", "cVenName" });
            map.Add("dept", new string[] { "cdept_id", "Department", "cDepCode", "cDepName" });
            map.Add("person", new string[] { "cperson_id", "Person", "cPersonCode", "cPersonName" });
            map.Add("project", new string[] { "citem_id", "", "", "" });
            return map;
        }

        public static ApiResult Run(WorkContext ctx, ReportArgs a, OpeningArgs o, PermContext p, PermRule rule)
        {
            string start = ReportsOpening.StartDate(ctx, "gl");
            int startYear = ReportsOpening.YearOf(start, 0);
            int year = a.FiscalYear;
            if (startYear > 0 && year < startYear)
            {
                throw GlReq.Bad("fiscal_year 早于总账启用年度 " + startYear.ToString(CultureInfo.InvariantCulture));
            }
            // 期初期间：启用年度取启用月份，其余取 1。
            int p0 = year == startYear ? int.Parse(start.Substring(5, 2), CultureInfo.InvariantCulture) : 1;
            Dictionary<string, object> body = o.Dim.Length > 0 ? Aux(ctx, a, o, p, rule, p0) : Accounts(ctx, a, o, p, rule, p0);
            ReportsOpening.Stamp(ctx, body, o, "gl", start, year);
            body["fiscal_year"] = year;
            body["period"] = p0;
            body["dim"] = o.Dim.Length > 0 ? o.Dim : null;
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> Accounts(WorkContext ctx, ReportArgs a, OpeningArgs o, PermContext p, PermRule rule,
            int p0)
        {
            int y = a.FiscalYear;
            object[] head = new object[] { p0, p0, p0, y, p0, y };
            List<object> ps = new List<object>(head);
            ps.Add(a.Limit + 1);
            StringBuilder sql = new StringBuilder(BaseSql).Append(PageSql);
            if (o.CodePrefix.Length > 0)
            {
                sql.Append(" AND ccode LIKE ?");
                ps.Add(o.CodePrefix + "%");
            }
            if (o.LeafOnly)
            {
                sql.Append(" AND bend=1");
            }
            if (o.NonZero)
            {
                sql.Append(NonZeroSql);
            }
            string[] after = Reports.Uncursor(a.After, 1);
            if (after != null)
            {
                sql.Append(" AND ccode>?");
                ps.Add(after[0]);
            }
            // 数据权限：科目。
            PermSql.AppendRule(sql, ps, p, rule, null);
            sql.Append(" ORDER BY ccode");
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), ps.ToArray(), a.Limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                items.Add(AccountItem(rows[i]));
            }
            string next = rows.Count > a.Limit ? Reports.Cursor(GlSql.Col(rows[a.Limit - 1], "ccode")) : null;
            Dictionary<string, object> body = ReportsStockSql.Page(items, next);
            body["trial"] = after == null ? Trial(ctx, head, p, rule) : null;
            return body;
        }

        // 期初试算平衡：末级科目的期初借方合计、贷方合计，差额为 0 时 balanced。
        static Dictionary<string, object> Trial(WorkContext ctx, object[] head, PermContext p, PermRule rule)
        {
            List<object> ps = new List<object>(head);
            StringBuilder sql = new StringBuilder(BaseSql).Append(TrialSql);
            PermSql.AppendRule(sql, ps, p, rule, null);
            Dictionary<string, object> row = Rows.One(ctx.Conn, sql.ToString(), ps.ToArray());
            decimal d = GlSql.Money(row, "d");
            decimal c = GlSql.Money(row, "c");
            Dictionary<string, object> trial = new Dictionary<string, object>();
            trial["debit"] = d;
            trial["credit"] = c;
            trial["difference"] = d - c;
            trial["balanced"] = d == c;
            return trial;
        }

        static Dictionary<string, object> AccountItem(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["code"] = GlSql.Col(row, "ccode");
            item["name"] = GlSql.Col(row, "ccode_name");
            item["grade"] = GlSql.Int(row, "igrade");
            item["leaf"] = GlSql.Int(row, "bend") != 0;
            item["natural_dir"] = GlSql.Int(row, "bproperty") != 0 ? "借" : "贷";
            Reports.PutBalance(item, "open", GlSql.Money(row, "signed_open"));
            item["pre_debit"] = GlSql.Money(row, "pre_d");
            item["pre_credit"] = GlSql.Money(row, "pre_c");
            return item;
        }

        static Dictionary<string, object> Aux(WorkContext ctx, ReportArgs a, OpeningArgs o, PermContext p, PermRule rule, int p0)
        {
            int y = a.FiscalYear;
            List<object> ps = new List<object>(new object[] { y, p0 });
            string text = AuxTemplate(o, ps, p, rule);
            ps.Add(a.Limit + 1);
            ps.Add(y);
            StringBuilder sql = new StringBuilder(text);
            if (o.NonZero)
            {
                sql.Append(" AND ROUND(a.signed_open,2)<>0");
            }
            string[] after = Reports.Uncursor(a.After, 3);
            if (after != null)
            {
                sql.Append(AuxAfter);
                ps.AddRange(new object[] { after[0], after[0], after[1], after[1], after[2] });
            }
            sql.Append(" ORDER BY a.ccode, a.cls, a.dim_code");
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), ps.ToArray(), a.Limit + 1);
            bool project = o.Dim == "project";
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                items.Add(AuxItem(rows[i], project));
            }
            string next = null;
            if (rows.Count > a.Limit)
            {
                Dictionary<string, object> last = rows[a.Limit - 1];
                next = Reports.Cursor(GlSql.Col(last, "ccode"), GlSql.Col(last, "cls"), GlSql.Col(last, "dim_code"));
            }
            return ReportsStockSql.Page(items, next);
        }

        // 固定列名替换 + 过滤条件（前缀只进参数），数据权限按 GL_accass 原始行过滤（科目与各辅助项）。
        static string AuxTemplate(OpeningArgs o, List<object> ps, PermContext p, PermRule rule)
        {
            string[] dim = Dims[o.Dim];
            bool project = o.Dim == "project";
            StringBuilder filter = new StringBuilder();
            if (o.CodePrefix.Length > 0)
            {
                filter.Append(" AND x.ccode LIKE ?");
                ps.Add(o.CodePrefix + "%");
            }
            if (o.LeafOnly)
            {
                filter.Append(" AND x.ccode IN (SELECT lc.ccode FROM code lc WHERE lc.iyear=x.iyear AND lc.bend=1)");
            }
            PermSql.AppendRule(filter, ps, p, rule, "x");
            string join = project ? "" : " LEFT JOIN " + dim[1] + " n ON n." + dim[2] + "=a.dim_code";
            return AuxSql.Replace("{CLS}", project ? "ISNULL(x.citem_class, N'')" : "N''")
                .Replace("{CLSG}", project ? ", ISNULL(x.citem_class, N'')" : "")
                .Replace("{DIM}", dim[0])
                .Replace("{FILTER}", filter.ToString())
                .Replace("{NAME}", project ? "CONVERT(nvarchar(1), NULL)" : "n." + dim[3])
                .Replace("{JOIN}", join);
        }

        static Dictionary<string, object> AuxItem(Dictionary<string, object> row, bool project)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["code"] = GlSql.Col(row, "ccode");
            item["name"] = GlSql.Col(row, "ccode_name");
            item["dim_code"] = GlSql.Col(row, "dim_code");
            item["dim_name"] = project ? null : Reports.Text(row, "dim_name");
            item["project_class"] = project ? GlSql.Col(row, "cls") : null;
            Reports.PutBalance(item, "open", GlSql.Money(row, "signed_open"));
            return item;
        }
    }
}
