using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 辅助核算余额表：GL_accass（只含已记账），按（科目、项目大类、辅助项）汇总。
    // 辅助项的列名和名称表只来自下面的固定表，调用方的值只进参数。
    internal static class ReportsAux
    {
        // dim → { GL_accass 列, 名称表, 名称表编码列, 名称表名称列 }。项目的名称在按大类分开的表里，不取。
        static readonly Dictionary<string, string[]> Dims = BuildDims();

        // {DIM} 替换为固定列名，{CLS} 为项目大类列或空串常量，{CLSG} 是项目大类的分组列（常量不能进 GROUP BY）。
        // 绑定顺序：from, from, to, from, to, to, to, to, year | year | limit。
        const string AuxSql = ";WITH a AS (SELECT x.ccode, {CLS} cls, x.{DIM} dim_code,"
            + " SUM(CASE WHEN x.iperiod=? THEN CASE x.cbegind_c WHEN N'借' THEN ISNULL(x.mb,0) WHEN N'贷' THEN -ISNULL(x.mb,0)"
            + " ELSE 0 END ELSE 0 END) signed_open,"
            + " SUM(CASE WHEN x.iperiod BETWEEN ? AND ? THEN ISNULL(x.md,0) ELSE 0 END) period_d,"
            + " SUM(CASE WHEN x.iperiod BETWEEN ? AND ? THEN ISNULL(x.mc,0) ELSE 0 END) period_c,"
            + " SUM(CASE WHEN x.iperiod BETWEEN 1 AND ? THEN ISNULL(x.md,0) ELSE 0 END) ytd_d,"
            + " SUM(CASE WHEN x.iperiod BETWEEN 1 AND ? THEN ISNULL(x.mc,0) ELSE 0 END) ytd_c,"
            + " SUM(CASE WHEN x.iperiod=? THEN CASE x.cendd_c WHEN N'借' THEN ISNULL(x.me,0) WHEN N'贷' THEN -ISNULL(x.me,0)"
            + " ELSE 0 END ELSE 0 END) signed_close"
            + " FROM GL_accass x WHERE x.iyear=? AND x.iperiod BETWEEN 1 AND 12"
            + " AND NULLIF(LTRIM(RTRIM(ISNULL(x.{DIM}, N''))), N'') IS NOT NULL{FILTER}"
            + " GROUP BY x.ccode{CLSG}, x.{DIM})"
            + " SELECT TOP (?) a.ccode, c.ccode_name, a.cls, a.dim_code, {NAME} dim_name,"
            + " CONVERT(decimal(18,2), a.signed_open) signed_open, CONVERT(decimal(18,2), a.period_d) period_d,"
            + " CONVERT(decimal(18,2), a.period_c) period_c, CONVERT(decimal(18,2), a.ytd_d) ytd_d,"
            + " CONVERT(decimal(18,2), a.ytd_c) ytd_c, CONVERT(decimal(18,2), a.signed_close) signed_close"
            + " FROM a LEFT JOIN code c ON c.iyear=? AND c.ccode=a.ccode{JOIN} WHERE 1=1";
        const string NonZeroSql = " AND (ROUND(a.signed_open,2)<>0 OR ROUND(a.ytd_d,2)<>0 OR ROUND(a.ytd_c,2)<>0"
            + " OR ROUND(a.signed_close,2)<>0)";
        const string AfterSql = " AND (a.ccode>? OR (a.ccode=? AND (a.cls>? OR (a.cls=? AND a.dim_code>?))))";

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

        public static ApiResult Balance(WorkContext ctx, ReportArgs a)
        {
            int y = a.FiscalYear;
            List<object> args = new List<object>(new object[] { a.From, a.From, a.To, a.From, a.To, a.To, a.To, a.To, y });
            string sql = Template(ctx, a, args);
            args.Add(a.Limit + 1);
            args.Add(y);
            StringBuilder text = new StringBuilder(sql);
            if (a.NonZero)
            {
                text.Append(NonZeroSql);
            }
            string[] after = Reports.Uncursor(a.After, 3);
            if (after != null)
            {
                text.Append(AfterSql);
                args.AddRange(new object[] { after[0], after[0], after[1], after[1], after[2] });
            }
            text.Append(" ORDER BY a.ccode, a.cls, a.dim_code");
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, text.ToString(), args.ToArray(), a.Limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                items.Add(Item(rows[i], a.Dim == "project"));
            }
            Dictionary<string, object> body = Reports.Body();
            body["fiscal_year"] = y;
            body["dim"] = a.Dim;
            body["period_from"] = a.From;
            body["period_to"] = a.To;
            body["items"] = items;
            body["next"] = rows.Count > a.Limit ? Next(rows[a.Limit - 1]) : null;
            return ApiResult.Ok(body);
        }

        // 固定列名替换 + 过滤条件（前缀、辅助项编码、项目大类只进参数）。
        static string Template(WorkContext ctx, ReportArgs a, List<object> args)
        {
            string[] dim = Dims[a.Dim];
            bool project = a.Dim == "project";
            StringBuilder filter = new StringBuilder();
            if (a.CodePrefix.Length > 0)
            {
                filter.Append(" AND x.ccode LIKE ?");
                args.Add(a.CodePrefix + "%");
            }
            if (a.DimCode.Length > 0)
            {
                filter.Append(" AND x.").Append(dim[0]).Append("=?");
                args.Add(a.DimCode);
            }
            if (a.ProjectClass.Length > 0)
            {
                filter.Append(" AND x.citem_class=?");
                args.Add(a.ProjectClass);
            }
            // 数据权限：科目与各辅助项，按 GL_accass 的原始行过滤。
            PermHook.Where(filter, args, ctx, "x");
            string join = project ? "" : " LEFT JOIN " + dim[1] + " n ON n." + dim[2] + "=a.dim_code";
            return AuxSql.Replace("{CLS}", project ? "ISNULL(x.citem_class, N'')" : "N''")
                .Replace("{CLSG}", project ? ", ISNULL(x.citem_class, N'')" : "")
                .Replace("{DIM}", dim[0])
                .Replace("{FILTER}", filter.ToString())
                .Replace("{NAME}", project ? "CONVERT(nvarchar(1), NULL)" : "n." + dim[3])
                .Replace("{JOIN}", join);
        }

        static Dictionary<string, object> Item(Dictionary<string, object> row, bool project)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["code"] = GlSql.Col(row, "ccode");
            item["name"] = GlSql.Col(row, "ccode_name");
            item["dim_code"] = GlSql.Col(row, "dim_code");
            item["dim_name"] = project ? null : Reports.Text(row, "dim_name");
            item["project_class"] = project ? GlSql.Col(row, "cls") : null;
            ReportsGl.Amounts(item, row);
            return item;
        }

        static string Next(Dictionary<string, object> row)
        {
            return Reports.Cursor(GlSql.Col(row, "ccode"), GlSql.Col(row, "cls"), GlSql.Col(row, "dim_code"));
        }
    }
}
