using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 到货及时率的 SQL（ReportsPuArrival 用）。明细是已审核采购订单的行（计划到货日 dArriveDate 在窗口内），
    // 以登录日期（as_of）判断逾期。首次到货 = 该订单行已审核到货单（PU_ArrivalVouchs.iPOsID）和已审核采购入库单
    // （rdrecords01.iPOsID，直接入库不开到货单）里最早的日期。已关闭且从未到货的行（作废订单）不进明细。
    // 三类互斥：有首次到货（已审核到货单或入库单）算已到货 arrived；没有首次到货且计划日早于 as_of 算逾期未到 overdue，不早于 as_of 算未到期 not_due。
    // 所以每个订单行恰好落在其中一类，lines = arrived_lines + open_overdue + not_due。
    // 列出的 arrived_qty 是订单行上记录的到货累计（iArrQTY 与入库累计 iReceivedQTY + freceivedqty 中较大的），含未审核单据，仅供参考。
    // 数据权限的条件加在外层 l 上，见 ReportsPuSql。
    internal static class ReportsPuArrivalSql
    {
        // 参数：as_of, date_from, [date_to], 过滤…
        const string Cte = ";WITH l AS (SELECT h.cPOID po_code, CONVERT(date, h.dPODate) po_date, h.cVenCode cVenCode,"
            + " d.cInvCode cInvCode, d.ID line_id, d.iQuantity qty, x.arr arrived_qty, CONVERT(date, d.dArriveDate) planned,"
            + " f.fa first_arrival,"
            + " CASE WHEN f.fa IS NULL AND d.dArriveDate<o.asof THEN 1 ELSE 0 END overdue,"
            + " CASE WHEN f.fa IS NULL AND d.dArriveDate>=o.asof THEN 1 ELSE 0 END not_due,"
            + " DATEDIFF(day, d.dArriveDate, o.asof) days_over"
            + " FROM PO_Pomain h JOIN PO_Podetails d ON d.POID=h.POID"
            + " CROSS JOIN (SELECT CONVERT(date, ?, 23) asof) o"
            + " CROSS APPLY (SELECT CASE WHEN ISNULL(h.cState, 0)=2 OR NULLIF(LTRIM(RTRIM(h.cCloser)), N'') IS NOT NULL"
            + " OR NULLIF(LTRIM(RTRIM(d.cbCloser)), N'') IS NOT NULL THEN 1 ELSE 0 END closed) c"
            + " CROSS APPLY (SELECT CASE WHEN ISNULL(d.iArrQTY, 0)>ISNULL(d.iReceivedQTY, 0)+ISNULL(d.freceivedqty, 0)"
            + " THEN ISNULL(d.iArrQTY, 0) ELSE ISNULL(d.iReceivedQTY, 0)+ISNULL(d.freceivedqty, 0) END arr) x"
            + " OUTER APPLY (SELECT MIN(t.dt) fa FROM ("
            + " SELECT CONVERT(date, a.dDate) dt FROM PU_ArrivalVouchs b JOIN PU_ArrivalVouch a ON a.ID=b.ID"
            + " WHERE b.iPOsID=d.ID AND ISNULL(a.bNegative, 0)=0 AND b.iQuantity>0"
            + " AND NULLIF(LTRIM(RTRIM(a.cverifier)), N'') IS NOT NULL"
            + " UNION ALL SELECT CONVERT(date, r.dDate) FROM rdrecords01 s JOIN rdrecord01 r ON r.ID=s.ID"
            + " WHERE s.iPOsID=d.ID AND s.iQuantity>0 AND NULLIF(LTRIM(RTRIM(r.cHandler)), N'') IS NOT NULL) t) f"
            + " WHERE NULLIF(LTRIM(RTRIM(h.cVerifier)), N'') IS NOT NULL AND d.dArriveDate>=CONVERT(date, ?, 23){FILTER}"
            + " AND (c.closed=0 OR f.fa IS NOT NULL))";

        const string Measures = " COUNT(*) lines, SUM(CASE WHEN l.first_arrival IS NOT NULL THEN 1 ELSE 0 END) arrived_lines,"
            + " SUM(CASE WHEN l.first_arrival<=l.planned THEN 1 ELSE 0 END) on_time,"
            + " SUM(CASE WHEN l.first_arrival>l.planned THEN 1 ELSE 0 END) late,"
            + " SUM(l.overdue) open_overdue, SUM(l.not_due) not_due";

        // 分组最多取 1001 组（常量，不是请求值；多取的一组只用来判断是否截断），按订单行数从多到少。
        const string Grouped = " SELECT TOP 1001 {KEYS}," + Measures + " FROM l{JOINS} WHERE 1=1{PERM}"
            + " GROUP BY {GROUP} ORDER BY COUNT(*) DESC, {GROUP}";

        const string Totals = " SELECT" + Measures + " FROM l WHERE 1=1{PERM}";

        // 参数：（CTE 的）…| limit | 数据权限。
        const string Overdue = " SELECT TOP (?) l.po_code, CONVERT(varchar(10), l.po_date, 23) po_date, v.cVenName vendor_name,"
            + " l.cInvCode inv_code, i.cInvName inv_name, u.cComUnitName unit, l.qty, l.arrived_qty,"
            + " CONVERT(varchar(10), l.planned, 23) planned_date, l.days_over days_overdue"
            + " FROM l LEFT JOIN Vendor v ON v.cVenCode=l.cVenCode LEFT JOIN Inventory i ON i.cInvCode=l.cInvCode"
            + " LEFT JOIN ComputationUnit u ON u.cComunitCode=i.cComUnitCode WHERE l.overdue=1{PERM}"
            + " ORDER BY l.planned, l.po_code, l.line_id";

        internal static string TotalsSql(PuArgs a, string asOf, List<object> ps)
        {
            return Head(a, asOf, ps) + Totals;
        }

        // group_by 为 none 时没有分组；调用方先判断。
        internal static string GroupSql(PuArgs a, string asOf, List<object> ps)
        {
            string keys = a.Group == "vendor"
                ? "l.cVenCode vendor_code, MAX(v.cVenName) vendor_name"
                : "l.cInvCode inv_code, MAX(i.cInvName) inv_name, MAX(u.cComUnitName) unit";
            string by = a.Group == "vendor" ? "l.cVenCode" : "l.cInvCode";
            string joins = a.Group == "vendor"
                ? " LEFT JOIN Vendor v ON v.cVenCode=l.cVenCode"
                : " LEFT JOIN Inventory i ON i.cInvCode=l.cInvCode LEFT JOIN ComputationUnit u ON u.cComunitCode=i.cComUnitCode";
            return Head(a, asOf, ps) + Grouped.Replace("{KEYS}", keys).Replace("{JOINS}", joins).Replace("{GROUP}", by);
        }

        internal static string OverdueSql(PuArgs a, string asOf, List<object> ps)
        {
            string sql = Head(a, asOf, ps);
            ps.Add(a.OverdueLimit);
            return sql + Overdue;
        }

        static string Head(PuArgs a, string asOf, List<object> ps)
        {
            ps.Add(asOf);
            ps.Add(a.DateFrom);
            StringBuilder filter = new StringBuilder();
            if (a.DateTo.Length > 0)
            {
                filter.Append(" AND d.dArriveDate<DATEADD(day, 1, CONVERT(date, ?, 23))");
                ps.Add(a.DateTo);
            }
            ReportsPuSql.Filter(filter, ps, a);
            return Cte.Replace("{FILTER}", filter.ToString());
        }
    }
}
