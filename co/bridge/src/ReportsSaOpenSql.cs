using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 在手订单 open_orders 的 SQL。未执行的销售订单行：表头和表行都没有关闭人，且按所选口径还有余量
    // （ship 取 iQuantity − iFHQuantity，out 取 iQuantity − foutquantity，invoice 取 iQuantity − iKPQuantity，余量大于 0）。
    // 余额金额 = 余量 × 本币价税合计 iNatSum ÷ iQuantity。列名只来自这里的常量；取值只进 ? 参数。
    internal static class ReportsSaOpenSql
    {
        // {COL} 是口径对应的累计列，{JOINS} 是名称表连接，{FILTER} 是过滤条件和数据权限。
        const string From = " FROM SO_SOMain h JOIN SO_SODetails d ON d.ID=h.ID{JOINS}"
            + " CROSS APPLY (SELECT ISNULL(d.iQuantity, 0)-ISNULL(d.{COL}, 0) AS res) x"
            + " CROSS APPLY (SELECT x.res*ISNULL(d.iNatSum, 0)/NULLIF(d.iQuantity, 0) AS amt) y"
            + " WHERE NULLIF(LTRIM(RTRIM(h.cCloser)), N'') IS NULL AND NULLIF(LTRIM(RTRIM(d.cSCloser)), N'') IS NULL"
            + " AND x.res>0{FILTER}";

        const string Joins = " LEFT JOIN Customer c ON c.cCusCode=h.cCusCode LEFT JOIN Inventory i ON i.cInvCode=d.cInvCode"
            + " LEFT JOIN ComputationUnit u ON u.cComunitCode=i.cComUnitCode";

        // 参数：limit+1 | 登录日期 | 过滤条件的参数。
        const string Head = "SELECT TOP (?) {KEYS}, COUNT(*) AS lines, SUM(x.res) AS open_qty, SUM(y.amt) AS open_amount,"
            + " SUM(CASE WHEN d.dPreDate < CONVERT(date, ?, 23) THEN 1 ELSE 0 END) AS overdue_lines,"
            + " CONVERT(varchar(10), MIN(d.dPreDate), 23) AS earliest_due";

        // 参数：过滤条件的参数。
        const string Totals = "SELECT COUNT(*) AS lines, SUM(y.amt) AS open_amount, COUNT(DISTINCT h.cCusCode) AS customers";

        // 分组维度 → { 输出列, GROUP BY, ORDER BY }。客户和存货按余额金额从大到小（字段权限会遮金额时改按编码）；逐行按预发货日期从早到晚。
        static string[] Parts(string groupBy, bool noAmountOrder)
        {
            if (groupBy == "inventory")
            {
                return new string[]
                {
                    "d.cInvCode AS inv_code, i.cInvName AS inv_name, u.cComUnitName AS unit",
                    "d.cInvCode, i.cInvName, u.cComUnitName", noAmountOrder ? "d.cInvCode" : "SUM(y.amt) DESC, d.cInvCode"
                };
            }
            if (groupBy == "order")
            {
                return new string[]
                {
                    "h.cSOCode AS so_code, CONVERT(varchar(10), h.dDate, 23) AS so_date, c.cCusName AS customer_name,"
                        + " d.cInvCode AS inv_code, i.cInvName AS inv_name, u.cComUnitName AS unit,"
                        + " CONVERT(varchar(10), d.dPreDate, 23) AS due_date",
                    "h.cSOCode, h.dDate, c.cCusName, d.cInvCode, i.cInvName, u.cComUnitName, d.dPreDate, d.iSOsID",
                    "CASE WHEN d.dPreDate IS NULL THEN 1 ELSE 0 END, d.dPreDate, h.cSOCode, d.iSOsID"
                };
            }
            return new string[]
            {
                "h.cCusCode AS customer_code, c.cCusName AS customer_name", "h.cCusCode, c.cCusName",
                noAmountOrder ? "h.cCusCode" : "SUM(y.amt) DESC, h.cCusCode"
            };
        }

        // 口径 → 订单行上的累计列（白名单，不是请求原文）。
        static string Column(string basis)
        {
            if (basis == "out")
            {
                return "foutquantity";
            }
            return basis == "invoice" ? "iKPQuantity" : "iFHQuantity";
        }

        internal static string Items(OpenOrdersArgs a, string filter)
        {
            string[] parts = Parts(a.GroupBy, a.NoAmountOrder);
            return Head.Replace("{KEYS}", parts[0]) + From.Replace("{JOINS}", Joins).Replace("{COL}", Column(a.Basis))
                .Replace("{FILTER}", filter) + " GROUP BY " + parts[1] + " ORDER BY " + parts[2];
        }

        internal static string Sum(OpenOrdersArgs a, string filter)
        {
            return Totals + From.Replace("{JOINS}", "").Replace("{COL}", Column(a.Basis)).Replace("{FILTER}", filter);
        }

        // 过滤条件和数据权限。ps 按出现顺序追加。
        // 数据权限取 report:open_orders（对象同销售订单查询）：表头对象（客户、部门、业务员、销售类型）过滤 h，
        // 表体存货汇总的是行，所以按行过滤 d（ReportsMfgPerm.Apply），没有存货权限的行不进合计。
        internal static string Filter(WorkContext ctx, OpenOrdersArgs a, List<object> ps)
        {
            StringBuilder sb = new StringBuilder();
            SaCalc.In(sb, ps, "h.cCusCode", a.Customers);
            SaCalc.In(sb, ps, "d.cInvCode", a.Invs);
            SaCalc.In(sb, ps, "h.cPersonCode", a.Persons);
            SaCalc.Between(sb, ps, "h.dDate", a.DateFrom, a.DateTo);
            if (a.DueBefore.Length > 0)
            {
                sb.Append(" AND d.dPreDate < CONVERT(date, ?, 23)");
                ps.Add(a.DueBefore);
            }
            ReportsMfgPerm.Apply(sb, ps, ctx, "report:" + ReportsSaReq.OpenOrders, "h", "d");
            return sb.ToString();
        }
    }
}
