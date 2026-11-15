using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 采购类报表的 SQL（ReportsPuPurchase、ReportsPuArrival 用）。表名、列名只来自本文件常量，取值一律走 ? 参数。
    // 明细先收进 CTE l（带 cVenCode、cInvCode 两列），数据权限条件加在外层 l 上（PermHook.Where(…, "l")）。
    // 返回的 SQL 里留着 {PERM}，调用方在参数顺序对齐后替换。
    internal static class ReportsPuSql
    {
        // 采购发票：本币列 iMoney（无税）、iSum（价税合计）；跳过期初发票。参数：from, to, 过滤…
        const string Invoice = ";WITH l AS (SELECT h.PBVID id, CONVERT(date, h.dPBVDate) dt, h.cVenCode cVenCode,"
            + " d.cInvCode cInvCode, d.iPBVQuantity qty, d.iMoney amount, d.iSum amount_tax"
            + " FROM PurBillVouch h JOIN PurBillVouchs d ON d.PBVID=h.PBVID"
            + " WHERE ISNULL(h.bFirst, 0)=0 AND h.dPBVDate>=CONVERT(date, ?, 23)"
            + " AND h.dPBVDate<DATEADD(day, 1, CONVERT(date, ?, 23)){FILTER})";

        // 采购入库单：只含已审核（经手人 cHandler 非空）、非期初的入库行，金额取无税的 iPrice；红字入库数量为负，照实相加。
        const string Receipt = ";WITH l AS (SELECT h.ID id, CONVERT(date, h.dDate) dt, h.cVenCode cVenCode,"
            + " d.cInvCode cInvCode, d.iQuantity qty, d.iPrice amount, CONVERT(decimal(28, 6), NULL) amount_tax"
            + " FROM RdRecord01 h JOIN rdrecords01 d ON d.ID=h.ID"
            + " WHERE ISNULL(h.bpufirst, 0)=0 AND NULLIF(LTRIM(RTRIM(h.cHandler)), N'') IS NOT NULL AND h.dDate>=CONVERT(date, ?, 23)"
            + " AND h.dDate<DATEADD(day, 1, CONVERT(date, ?, 23)){FILTER})";

        // 参数：（CTE 的）…| limit+1 | 数据权限。
        const string Grouped = " SELECT TOP (?) {KEYS}, SUM(l.qty) qty, SUM(l.amount) amount, SUM(l.amount_tax) amount_tax,"
            + " COUNT(DISTINCT l.id) docs FROM l{JOINS} WHERE 1=1{PERM} GROUP BY {GROUP} ORDER BY {ORD}";

        const string Totals = " SELECT SUM(l.amount) amount, SUM(l.amount_tax) amount_tax, COUNT(DISTINCT l.id) docs"
            + " FROM l WHERE 1=1{PERM}";

        const string InvJoin = " LEFT JOIN Inventory i ON i.cInvCode=l.cInvCode"
            + " LEFT JOIN ComputationUnit u ON u.cComunitCode=i.cComUnitCode";
        const string VenJoin = " LEFT JOIN Vendor v ON v.cVenCode=l.cVenCode";

        // 分组的 SQL；参数追加到 ps（CTE 条件、limit+1），权限的参数由调用方在替换 {PERM} 时追加。
        internal static string GroupSql(PuArgs a, string to, List<object> ps)
        {
            StringBuilder keys = new StringBuilder();
            StringBuilder group = new StringBuilder();
            StringBuilder joins = new StringBuilder();
            for (int i = 0; i < a.GroupBy.Length; i++)
            {
                AddDimension(a.GroupBy[i], i > 0, keys, group, joins);
            }
            string cte = Cte(a, to, ps);
            ps.Add(a.Limit + 1);
            return cte + Grouped.Replace("{KEYS}", keys.ToString()).Replace("{JOINS}", joins.ToString())
                .Replace("{ORD}", a.NoAmountOrder ? "{GROUP}" : "SUM(l.amount) DESC, {GROUP}").Replace("{GROUP}", group.ToString());
        }

        internal static string TotalsSql(PuArgs a, string to, List<object> ps)
        {
            return Cte(a, to, ps) + Totals;
        }

        static string Cte(PuArgs a, string to, List<object> ps)
        {
            ps.Add(a.DateFrom);
            ps.Add(to);
            StringBuilder filter = new StringBuilder();
            Filter(filter, ps, a);
            return (a.Receipt ? Receipt : Invoice).Replace("{FILTER}", filter.ToString());
        }

        // 存货、存货分类前缀、供应商（明细行别名 h 表头、d 表体）。条件追加顺序就是 ? 的顺序。
        internal static void Filter(StringBuilder sb, List<object> ps, PuArgs a)
        {
            In(sb, ps, "d.cInvCode", a.Inv);
            if (a.InvClass.Length > 0)
            {
                sb.Append(" AND d.cInvCode IN (SELECT ic.cInvCode FROM Inventory ic WHERE ic.cInvCCode LIKE ?)");
                ps.Add(a.InvClass + "%");
            }
            In(sb, ps, "h.cVenCode", a.Vendor);
        }

        static void In(StringBuilder sb, List<object> ps, string column, string[] values)
        {
            if (values.Length == 0)
            {
                return;
            }
            sb.Append(" AND ").Append(column).Append(" IN (");
            for (int i = 0; i < values.Length; i++)
            {
                sb.Append(i == 0 ? "?" : ", ?");
                ps.Add(values[i]);
            }
            sb.Append(")");
        }

        static void AddDimension(string dim, bool more, StringBuilder keys, StringBuilder group, StringBuilder joins)
        {
            string key;
            string by;
            if (dim == "material")
            {
                key = "l.cInvCode inv_code, MAX(i.cInvName) inv_name, MAX(u.cComUnitName) unit";
                by = "l.cInvCode";
                joins.Append(InvJoin);
            }
            else if (dim == "vendor")
            {
                key = "l.cVenCode vendor_code, MAX(v.cVenName) vendor_name";
                by = "l.cVenCode";
                joins.Append(VenJoin);
            }
            else
            {
                key = "CONVERT(char(7), l.dt, 23) mon";
                by = "CONVERT(char(7), l.dt, 23)";
            }
            keys.Append(more ? ", " : "").Append(key);
            group.Append(more ? ", " : "").Append(by);
        }

        // 把 {PERM} 换成数据权限条件（参数追加在 ps 末尾，所以要在其它参数都加完之后调用）。
        internal static string WithPerm(string sql, WorkContext ctx, List<object> ps)
        {
            StringBuilder perm = new StringBuilder();
            PermHook.Where(perm, ps, ctx, "l");
            return sql.Replace("{PERM}", perm.ToString());
        }
    }
}
