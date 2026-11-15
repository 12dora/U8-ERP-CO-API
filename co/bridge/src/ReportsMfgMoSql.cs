using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 生产订单完工状况 mo_progress、生产订单用料分析 mo_consumption 的 SQL（ReportsMfgMo 用）。
    // 订单头 mom_order（制单日期 CreateDate）、订单行 mom_orderdetail（Qty 数量、QualifiedInQty 合格入库数量累计、Status 状态）、
    // 开工完工日期 mom_morder（StartDate、DueDate）、子件 mom_moallocate（Qty 标准用量、IssQty 已领数量累计）。
    // 表名、列名都是这里的常量，编码、日期只进 ? 参数。
    internal static class ReportsMfgMoSql
    {
        internal const string ProgressKey = "report:mo_progress";
        internal const string ConsumptionKey = "report:mo_consumption";

        // 订单行为 3（已审核）、4（已关闭）才算开工；1、2 是未审核的计划。
        const string Audited = "d.Status IN (3, 4)";
        const string Window = " WHERE o.CreateDate >= CONVERT(date, ?, 23) AND o.CreateDate < DATEADD(day, 1, CONVERT(date, ?, 23))";

        const string ProgressFrom = " FROM mom_orderdetail d JOIN mom_order o ON o.MoId = d.MoId"
            + " LEFT JOIN mom_morder m ON m.MoDId = d.MoDId LEFT JOIN Inventory i ON i.cInvCode = d.InvCode"
            + " LEFT JOIN ComputationUnit u ON u.cComunitCode = i.cComUnitCode LEFT JOIN Department dp ON dp.cDepCode = d.MDeptCode";

        const string ProgressCols = "SELECT TOP (?) o.MoCode AS mo_code, d.SortSeq AS mo_line,"
            + " CONVERT(varchar(10), o.CreateDate, 23) AS create_date, CONVERT(varchar(10), m.StartDate, 23) AS start_date,"
            + " CONVERT(varchar(10), m.DueDate, 23) AS due_date, d.InvCode AS inv_code, i.cInvName AS inv_name,"
            + " u.cComUnitName AS unit, dp.cDepName AS dept_name, CONVERT(decimal(20, 6), ISNULL(d.Qty, 0)) AS qty,"
            + " CONVERT(decimal(20, 6), ISNULL(d.QualifiedInQty, 0)) AS qualified_in, ISNULL(d.Status, 0) AS status";

        // {FILTER} 是起止日期之后的条件。参数：[TOP]、起、止、…。
        // 母件 par 是区间内已审核订单行按存货合计（订单数、数量、合格入库），comp 是它们的子件按存货合计（标准用量、已领），
        // 子件合计在订单行上做，不再连入库单，数量不会被翻倍。副产品（ByproductFlag=1）不是耗用，不计。
        const string Consumption = ";WITH l AS (SELECT d.MoId, d.MoDId, d.InvCode, ISNULL(d.Qty, 0) AS qty,"
            + " ISNULL(d.QualifiedInQty, 0) AS qi FROM mom_orderdetail d JOIN mom_order o ON o.MoId = d.MoId"
            + " JOIN Inventory i ON i.cInvCode = d.InvCode{WHERE}),"
            + " par AS (SELECT InvCode, COUNT(DISTINCT MoId) AS mo_count, SUM(qty) AS mo_qty, SUM(qi) AS qualified_in"
            + " FROM l GROUP BY InvCode),"
            + " comp AS (SELECT l.InvCode AS parent, a.InvCode AS comp, SUM(ISNULL(a.Qty, 0)) AS bom_qty,"
            + " SUM(ISNULL(a.IssQty, 0)) AS issued_qty FROM l JOIN mom_moallocate a ON a.MoDId = l.MoDId"
            + " WHERE ISNULL(a.ByproductFlag, 0) = 0 GROUP BY l.InvCode, a.InvCode)"
            + " SELECT TOP (?) par.InvCode AS parent_code, pi.cInvName AS parent_name, pu.cComUnitName AS parent_unit,"
            + " par.mo_count, CONVERT(decimal(20, 6), par.mo_qty) AS mo_qty,"
            + " CONVERT(decimal(20, 6), par.qualified_in) AS qualified_in, comp.comp AS comp_code, ci.cInvName AS comp_name,"
            + " cu.cComUnitName AS comp_unit, CONVERT(decimal(20, 6), comp.bom_qty) AS bom_qty,"
            + " CONVERT(decimal(20, 6), comp.issued_qty) AS issued_qty"
            + " FROM par JOIN comp ON comp.parent = par.InvCode LEFT JOIN Inventory pi ON pi.cInvCode = par.InvCode"
            + " LEFT JOIN ComputationUnit pu ON pu.cComunitCode = pi.cComUnitCode"
            + " LEFT JOIN Inventory ci ON ci.cInvCode = comp.comp LEFT JOIN ComputationUnit cu ON cu.cComunitCode = ci.cComUnitCode"
            + " ORDER BY par.InvCode, comp.comp";

        // 每个订单行一行，按制单日期、订单号、行号。? 的顺序：TOP、起、止、[存货编码…]、[分类前缀]、数据权限。
        internal static string Progress(MfgArgs m, string to, List<object> ps, WorkContext ctx)
        {
            ps.Add(m.Limit + 1);
            StringBuilder sql = new StringBuilder(ProgressCols).Append(ProgressFrom);
            sql.Append(Filter(m, to, ps, ctx, ProgressKey));
            sql.Append(" ORDER BY o.CreateDate, o.MoCode, d.SortSeq");
            return sql.ToString();
        }

        // 母件 × 子件。? 的顺序：起、止、[母件编码…]、[分类前缀]、数据权限、TOP。
        internal static string Usage(MfgArgs m, string to, List<object> ps, WorkContext ctx)
        {
            string where = Filter(m, to, ps, ctx, ConsumptionKey);
            ps.Add(m.Limit + 1);
            return Consumption.Replace("{WHERE}", where);
        }

        // 制单日期窗口（含两端）和过滤。完工状况可以只要未关闭的；用料分析只看已审核的订单行。
        // 数据权限：对象同 voucher:production_order，订单行（d）逐行过滤。
        static string Filter(MfgArgs m, string to, List<object> ps, WorkContext ctx, string key)
        {
            StringBuilder sql = new StringBuilder(Window);
            ps.Add(m.DateFrom);
            ps.Add(to);
            if (key == ConsumptionKey)
            {
                sql.Append(" AND ").Append(Audited);
            }
            else if (m.OpenOnly)
            {
                sql.Append(" AND ISNULL(d.Status, 0) <> 4");
            }
            string[] codes = key == ConsumptionKey ? m.Parent : m.Inv;
            if (codes.Length > 0)
            {
                sql.Append(" AND d.InvCode IN (").Append(ReportsMfgDailySql.Marks(codes.Length)).Append(")");
                ps.AddRange(codes);
            }
            if (m.InvClass != null)
            {
                sql.Append(ReportsMgmtSalesClassSql.Filter("i.cInvCCode"));
                ps.Add(ReportsMgmtSalesClassSql.FilterArg(m.InvClass));
            }
            ReportsMfgPerm.Apply(sql, ps, ctx, key, "d", "d");
            return sql.ToString();
        }
    }
}
