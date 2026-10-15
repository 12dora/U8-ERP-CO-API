using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 库龄分析 stock_age 的 SQL。一行 = 一个（存货，仓库）的现存量，库龄、呆滞、成本都在这一粒度上取，
    // 不按仓库分行时由 ReportsStockAgeCalc 再合并。表名只来自固定表，调用方的值只进参数。
    internal static class ReportsStockAgeSql
    {
        // 最近一个已记账（IA_Summary.iPeriod=1）且不晚于登录月份的存货核算月份。参数：登录日期的 yyyyMM（整数）。
        // iMonth=0 是年初结转行，不算月份。没有记账月份时无行。
        internal const string CostMonth = "SELECT TOP 1 a.iYear y, a.iMonth m FROM IA_Summary a"
            + " WHERE ISNULL(a.iPeriod, 0)=1 AND a.iMonth>=1 AND a.iYear*100+a.iMonth<=?"
            + " GROUP BY a.iYear, a.iMonth ORDER BY a.iYear DESC, a.iMonth DESC";

        // st：现存量按（存货，仓库）合并批次、非零。mv：已审核收发记录里最后一次入库（rd=1：采购入库 01、其他入库 08、
        // 产成品入库 10、库存期初 34）和最后一次出库（rd=0：其他出库 09、材料出库 11、销售出库 32）的日期，红字（数量不为正）不算。
        // ia：成本月份按（存货，仓库）的结存数量与金额；iv：同月按存货合并，（存货，仓库）没有成本时拿它兜底。
        // 参数：st 的仓库、存货条件 | 登录日期 | mv 的仓库、存货条件 | 成本年、月（ia）| 成本年、月（iv）| 分类前缀、数据权限。
        const string Main = ";WITH st AS (SELECT c.cInvCode, c.cWhCode, SUM(c.iQuantity) qty FROM CurrentStock c WHERE 1=1{SF}"
            + " GROUP BY c.cInvCode, c.cWhCode HAVING ROUND(SUM(c.iQuantity), 6)<>0),"
            + " mv AS (SELECT m.cInvCode, m.cWhCode, MAX(CASE WHEN m.rd=1 THEN m.ddate END) li,"
            + " MAX(CASE WHEN m.rd=0 THEN m.ddate END) lo FROM {M}"
            + " WHERE m.qty>0 AND ISNULL(m.handler, N'')<>N'' AND m.ddate<DATEADD(day, 1, CONVERT(date, ?, 23)){MF}"
            + " GROUP BY m.cInvCode, m.cWhCode),"
            + " ia AS (SELECT a.cInvCode, a.cWhCode, SUM(a.iNum) n, SUM(a.iMoney) mny FROM IA_Summary a"
            + " WHERE a.iYear=? AND a.iMonth=? GROUP BY a.cInvCode, a.cWhCode),"
            + " iv AS (SELECT a.cInvCode, SUM(a.iNum) n, SUM(a.iMoney) mny FROM IA_Summary a"
            + " WHERE a.iYear=? AND a.iMonth=? GROUP BY a.cInvCode)"
            + " SELECT s.cInvCode inv, s.cWhCode wh, i.cInvName inv_name, i.cInvStd inv_std, u.cComUnitName unit,"
            + " w.cWhName wh_name, CONVERT(decimal(28,6), s.qty) qty,"
            + " CONVERT(varchar(10), v.li, 23) li, CONVERT(varchar(10), v.lo, 23) lo,"
            + " CONVERT(decimal(28,6), a.n) ia_n, CONVERT(decimal(28,4), a.mny) ia_m,"
            + " CONVERT(decimal(28,6), b.n) iv_n, CONVERT(decimal(28,4), b.mny) iv_m"
            + " FROM st s LEFT JOIN mv v ON v.cInvCode=s.cInvCode AND v.cWhCode=s.cWhCode"
            + " LEFT JOIN ia a ON a.cInvCode=s.cInvCode AND a.cWhCode=s.cWhCode LEFT JOIN iv b ON b.cInvCode=s.cInvCode"
            + " LEFT JOIN Inventory i ON i.cInvCode=s.cInvCode LEFT JOIN ComputationUnit u ON u.cComUnitCode=i.cComUnitCode"
            + " LEFT JOIN Warehouse w ON w.cWhCode=s.cWhCode WHERE 1=1{FILTER} ORDER BY s.cInvCode, s.cWhCode";

        // costYear、costMonth 为 0 表示没有已记账月份（ia、iv 不返回行）。参数按文本里问号的顺序放进 ps。
        internal static string Build(WorkContext ctx, StockAgeArgs s, string asOf, int costYear, int costMonth,
            List<object> ps)
        {
            StringBuilder stock = new StringBuilder();
            In(stock, ps, "c.cWhCode", s.Wh);
            In(stock, ps, "c.cInvCode", s.Inv);
            ps.Add(asOf);
            StringBuilder moves = new StringBuilder();
            In(moves, ps, "m.cWhCode", s.Wh);
            In(moves, ps, "m.cInvCode", s.Inv);
            ps.AddRange(new object[] { costYear, costMonth, costYear, costMonth });
            StringBuilder filter = new StringBuilder();
            if (s.InvClass.Length > 0)
            {
                filter.Append(" AND i.cInvCCode LIKE ?");
                ps.Add(s.InvClass + "%");
            }
            // 数据权限：仓库、存货（列 cWhCode、cInvCode 在 s 上）。
            PermHook.Where(filter, ps, ctx, "s");
            return Main.Replace("{M}", ReportsStockSql.Moves).Replace("{SF}", stock.ToString())
                .Replace("{MF}", moves.ToString()).Replace("{FILTER}", filter.ToString());
        }

        // " AND col IN (?, ?)"：只按个数拼占位符，编码本身只进参数。
        static void In(StringBuilder sb, List<object> ps, string column, string[] codes)
        {
            if (codes.Length == 0)
            {
                return;
            }
            sb.Append(" AND ").Append(column).Append(" IN (");
            for (int i = 0; i < codes.Length; i++)
            {
                sb.Append(i == 0 ? "?" : ", ?");
                ps.Add(codes[i]);
            }
            sb.Append(")");
        }
    }
}
