using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 产成品入库日报 production_daily 的 SQL（ReportsMfgDaily 用）。产成品入库单 rdrecord10 / rdrecords10，
    // 缺省只计已审核（表头 cHandler 非空，同 mgmt/cash_stock 的产量段）。表名、列名都是这里的常量，编码、日期只进 ? 参数。
    internal static class ReportsMfgDailySql
    {
        internal const string PermKey = "report:production_daily";

        const string From = " FROM rdrecord10 h JOIN rdrecords10 b ON b.ID = h.ID"
            + " LEFT JOIN Inventory i ON i.cInvCode = b.cInvCode";
        const string Window = " WHERE h.dDate >= CONVERT(date, ?, 23) AND h.dDate < DATEADD(day, 1, CONVERT(date, ?, 23))";
        const string Verified = " AND NULLIF(LTRIM(RTRIM(h.cHandler)), N'') IS NOT NULL";
        const string DayKey = "CONVERT(varchar(10), h.dDate, 23)";
        const string MonthKey = "CONVERT(varchar(7), h.dDate, 23)";
        const string Qty = "SUM(ISNULL(b.iQuantity, 0))";
        const string ClassCode = "COALESCE(cg.cInvCCode, i.cInvCCode)";

        // 按日（或按月）× 产品（或存货分类）汇总，最多 limit + 1 行（多一行用来判断 truncated）。
        // ? 的顺序：TOP、[分类级次]、起、止、[存货编码…]、[分类前缀]、数据权限。
        internal static string Build(MfgArgs m, string to, List<object> ps, WorkContext ctx)
        {
            bool byClass = m.By == "class";
            string key = m.GroupBy == "month" ? MonthKey : DayKey;
            string group = byClass ? ClassCode + ", cc.cInvCName" : "b.cInvCode, i.cInvName, u.cComUnitName";
            StringBuilder sql = new StringBuilder("SELECT TOP (?) ").Append(key).Append(" AS d, ");
            ps.Add(m.Limit + 1);
            sql.Append(byClass ? ClassCode + " AS class_code, cc.cInvCName AS class_name,"
                : "b.cInvCode AS inv_code, i.cInvName AS inv_name, u.cComUnitName AS unit,");
            sql.Append(" CONVERT(decimal(20, 6), ").Append(Qty).Append(") AS qty, COUNT(DISTINCT h.ID) AS docs").Append(From);
            if (byClass)
            {
                // 往上取 class_grade 级的祖先分类；存货的分类比它浅时保持自己的分类。
                sql.Append(ReportsMgmtSalesClassSql.Apply("i.cInvCCode", "cg"));
                ps.Add(m.ClassGrade);
                sql.Append(" LEFT JOIN InventoryClass cc ON cc.cInvCCode = ").Append(ClassCode);
            }
            else
            {
                sql.Append(" LEFT JOIN ComputationUnit u ON u.cComunitCode = i.cComUnitCode");
            }
            sql.Append(Filter(m, to, ps, ctx));
            sql.Append(" GROUP BY ").Append(key).Append(", ").Append(group);
            sql.Append(" ORDER BY ").Append(key).Append(", ").Append(Qty).Append(" DESC, ");
            sql.Append(byClass ? ClassCode : "b.cInvCode");
            return sql.ToString();
        }

        // 窗口内有入库的日期个数（同样的过滤条件）。? 的顺序：起、止、[存货编码…]、[分类前缀]、数据权限。
        internal static string Days(MfgArgs m, string to, List<object> ps, WorkContext ctx)
        {
            return "SELECT COUNT(DISTINCT CONVERT(date, h.dDate)) AS days" + From + Filter(m, to, ps, ctx);
        }

        // 日期窗口（含两端）、已审核、存货编码、分类前缀、数据权限。数据权限：对象同 voucher:product_in，表体对象对行过滤（b）。
        static string Filter(MfgArgs m, string to, List<object> ps, WorkContext ctx)
        {
            StringBuilder sql = new StringBuilder(Window);
            ps.Add(m.DateFrom);
            ps.Add(to);
            if (!m.Unverified)
            {
                sql.Append(Verified);
            }
            if (m.Inv.Length > 0)
            {
                sql.Append(" AND b.cInvCode IN (").Append(Marks(m.Inv.Length)).Append(")");
                ps.AddRange(m.Inv);
            }
            if (m.InvClass != null)
            {
                sql.Append(ReportsMgmtSalesClassSql.Filter("i.cInvCCode"));
                ps.Add(ReportsMgmtSalesClassSql.FilterArg(m.InvClass));
            }
            ReportsMfgPerm.Apply(sql, ps, ctx, PermKey, "h", "b");
            return sql.ToString();
        }

        internal static string Marks(int count)
        {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < count; i++)
            {
                sb.Append(i == 0 ? "?" : ", ?");
            }
            return sb.ToString();
        }
    }
}
