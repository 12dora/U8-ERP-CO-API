using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 成本核算期间数据 ca_inputs 的 SQL（成本管理 CA）。表名、列名全是常量（来自 ReportsCaInputsDef），调用方的值只进 ? 参数。
    internal static class ReportsCaInputsSql
    {
        // 制造费用（CA_ManCt）的成本项目在成本项目定义（CA_CostCD）里属于成本要素 3（制造费用，同 cost_unit 的 elements），按年度取。
        internal const string OverheadElement = "3";

        // 成本核算对象：cPPID 即 Caq_CoReal.iRealCOID。解析成本中心、存货和生产订单；解析不到时这些列为空。
        internal const string ObjectCols = ", r.cDeptID ca_cc, r.cDepName ca_cc_name, r.InvCode ca_inv, r.cInvName ca_inv_name,"
            + " r.cMOCode ca_mo";

        internal const string ObjectJoin = " LEFT JOIN Caq_CoReal r ON r.iRealCOID=t.cPPID";

        // 基础成本核算对象：cPPID 即 Caq_CoBase.iBaseCOID（没有生产订单）。cPPID 为 -1 的行是缺省系数行，没有对象，解析列为空。
        internal const string BaseCols = ", r.cDeptID ca_cc, r.cDepName ca_cc_name, r.InvCode ca_inv, r.cInvName ca_inv_name";

        internal const string BaseJoin = " LEFT JOIN Caq_CoBase r ON r.iBaseCOID=t.cPPID";

        // 成本中心：表上的 cDeptID 是成本中心编码（CA_CostCenter.cCode）。
        internal const string CenterCols = ", c.cName ca_cc_name";

        internal const string CenterJoin = " LEFT JOIN CA_CostCenter c ON c.cCode=t.cDeptID";

        internal const string ItemCols = ", i.cAmoName ca_item_name";

        internal const string ItemJoin = " LEFT JOIN CA_CostCD i ON i.cDeptID=t.cDeptID AND i.cAmoID=t.cAmoID"
            + " AND i.iYear=t.iPeriod/100 AND i.cAmotype=" + OverheadElement;

        // 各时间条件（参数：Period 一个 YYYYMM；Date 月初、下月初两个 yyyy-MM-dd；Year 一个年度）。
        internal const string WherePeriod = " WHERE t.iPeriod=?";

        internal const string WhereDate = " WHERE t.dDate>=CONVERT(date, ?, 23) AND t.dDate<CONVERT(date, ?, 23)";

        internal const string WhereYear = " WHERE t.iYear=?";

        // 最近一次成本计算到的期间（YYYYMM，成本核算系统表 CA_CBSys）。期初行（iPeriod 为 YYYY00）不在 1 到 12 期里，不读。
        internal const string Calculated = "SELECT TOP 1 iCalculate FROM CA_CBSys ORDER BY autoid";

        // 编码按数值排序（U8 的制造费用表按 convert(int, camoid) 排）；不是纯数字的排在后面。col 是常量列名。
        internal static string NumericOrder(string col)
        {
            return "CASE WHEN LEN(" + col + ") BETWEEN 1 AND 9 AND " + col + " NOT LIKE '%[^0-9]%' THEN CONVERT(int, " + col + ") END";
        }

        // 一张表的查询（不含数据权限条件和 ORDER BY）：t.* 加解析出的列（别名 ca_ 开头）和总行数 ca_n。
        // days 是该会计期间的起始日和次日（yyyy-MM-dd，工时日报的日期条件用）。
        internal static void Compose(CaTable t, StringBuilder sql, List<object> ps, int year, int period, string[] days)
        {
            sql.Append("SELECT t.*, COUNT(*) OVER() ca_n");
            sql.Append(Cols(t));
            sql.Append(" FROM ").Append(t.Table).Append(" t");
            sql.Append(Join(t));
            switch (t.Time)
            {
                case CaTable.Date:
                    sql.Append(WhereDate);
                    ps.Add(days[0]);
                    ps.Add(days[1]);
                    return;
                case CaTable.Year:
                    sql.Append(WhereYear);
                    ps.Add(year);
                    return;
                default:
                    sql.Append(WherePeriod);
                    ps.Add(year * 100 + period);
                    return;
            }
        }

        static string Cols(CaTable t)
        {
            string cols = t.IsBase ? BaseCols : (t.HasObject ? ObjectCols : (t.HasCenter ? CenterCols : ""));
            return cols + (t.Shape == CaTable.Item ? ItemCols : "");
        }

        static string Join(CaTable t)
        {
            string join = t.IsBase ? BaseJoin : (t.HasObject ? ObjectJoin : (t.HasCenter ? CenterJoin : ""));
            return join + (t.Shape == CaTable.Item ? ItemJoin : "");
        }
    }
}
