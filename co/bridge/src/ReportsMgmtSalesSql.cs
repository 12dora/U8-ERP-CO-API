using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 经营管理销售分析的 SQL（只读）。表名、列名都是这里的常量；年度、期间、日期只进 ? 参数。
    // 收入：已复核（销售复核人 cChecker 非空，include_unverified 时不限；cVerifier 是应收审核人，不是销售复核）、
    // 非期初（bFirst）的销售发票表体，按发票日期落在期间窗口内；数量 iQuantity、无税本币 iNatMoney、含税本币 iNatSum
    // （红字发票本身是负数，直接相加）。
    // 成本：存货核算明细账 IA_Subsidiary 上的销售成本行（bSale=1 的出库方向行，bRdFlag=0），金额 iAOutPrice，
    // 按核算年度 iYear、会计月份 iMonth；销售成本结转方式（IA 选项 bSaleType：销售出库单 / 发出商品 / 发票）不同，
    // 单据类型也不同（如 32、3201），都带 bSale=1，所以不按单据类型列举。业务员、部门在核算明细上为空时取对应的
    // 销售出库单表头（IA_Subsidiary.ID 是出库单表体 AutoID，单号核对 cVouCode）。
    // 两边按分组键 FULL OUTER JOIN：只有成本、没有收入的组（本期出库、发票在别期）也列出。
    internal static class ReportsMgmtSalesSql
    {
        // 分组维度 → { 内层列, 名称表连接, 名称列 }；内层列在收入、成本两侧同名（q 别名下），数据权限也挂在这些列上。
        static readonly Dictionary<string, string[]> DimCols = BuildDims();

        const string SaInner = "SELECT {PER} AS per, ISNULL(h.cCusCode, N'') AS cCusCode, ISNULL(b.cInvCode, N'') AS cInvCode,"
            + " ISNULL(h.cPersonCode, N'') AS cPersonCode, ISNULL(h.cDepCode, N'') AS cDepCode, ISNULL(b.iQuantity, 0) AS qty,"
            + " ISNULL(b.iNatMoney, 0) AS rev, ISNULL(b.iNatSum, 0) AS rev_tax"
            + " FROM SaleBillVouch h JOIN SaleBillVouchs b ON b.SBVID = h.SBVID"
            + " WHERE h.dDate >= CONVERT(date, ?, 23) AND h.dDate < CONVERT(date, ?, 23) AND ISNULL(h.bFirst, 0) = 0";

        const string Verified = " AND NULLIF(LTRIM(RTRIM(h.cChecker)), N'') IS NOT NULL";

        const string IaInner = "SELECT CONVERT(int, s.iMonth) AS per, ISNULL(s.cCusCode, N'') AS cCusCode,"
            + " ISNULL(s.cInvCode, N'') AS cInvCode,"
            + " ISNULL(COALESCE(NULLIF(LTRIM(RTRIM(s.cPersonCode)), N''), r.cPersonCode), N'') AS cPersonCode,"
            + " ISNULL(COALESCE(NULLIF(LTRIM(RTRIM(s.cDepCode)), N''), r.cDepCode), N'') AS cDepCode,"
            + " ISNULL(s.iAOutQuantity, 0) AS cqty, ISNULL(s.iAOutPrice, 0) AS cogs"
            + " FROM IA_Subsidiary s LEFT JOIN rdrecords32 rs ON rs.AutoID = s.ID"
            + " LEFT JOIN rdrecord32 r ON r.ID = rs.ID AND r.cCode = s.cVouCode"
            + " WHERE s.iYear = ? AND s.iMonth BETWEEN ? AND ? AND s.bSale = 1 AND s.bRdFlag = 0";

        const string Measures = " CONVERT(decimal(28,6), ISNULL(a.qty, 0)) AS qty, CONVERT(decimal(18,2), ISNULL(a.rev, 0)) AS rev,"
            + " CONVERT(decimal(18,2), ISNULL(a.rev_tax, 0)) AS rev_tax, CONVERT(decimal(28,6), ISNULL(c.cqty, 0)) AS cost_qty,"
            + " CONVERT(decimal(18,2), ISNULL(c.cogs, 0)) AS cogs";

        // 账套选项：销售管理启用日期、存货核算启用日期（空表示未启用）。
        internal const string ModulesSql = "SELECT"
            + " (SELECT TOP 1 cValue FROM AccInformation WHERE cSysID = N'SA' AND cName = N'dSaleStartDate') AS sa,"
            + " (SELECT TOP 1 cValue FROM AccInformation WHERE cSysID = N'IA' AND cName = N'dStartDate') AS ia";

        // U8 会计期间（可能不是自然月）。参数：账套号、年度、起止期间。读不到时按自然月（ReportsMgmtSales.Bounds）。
        internal const string PeriodSql = "SELECT iId AS p, CONVERT(varchar(10), dBegin, 23) AS b, CONVERT(varchar(10), dEnd, 23) AS e"
            + " FROM UFSYSTEM..UA_Period WHERE cAcc_Id = ? AND iYear = ? AND iId BETWEEN ? AND ?"
            + " AND (bIsDelete = 0 OR bIsDelete IS NULL) ORDER BY iId";

        static Dictionary<string, string[]> BuildDims()
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.Ordinal);
            map.Add("period", new string[] { "per", "", "" });
            map.Add("customer", new string[] { "cCusCode", " LEFT JOIN Customer cu ON cu.cCusCode = t.k_customer",
                "cu.cCusName AS n_customer" });
            map.Add("inventory", new string[] { "cInvCode", " LEFT JOIN Inventory iv ON iv.cInvCode = t.k_inventory",
                "iv.cInvName AS n_inventory, iv.cInvStd AS s_inventory" });
            map.Add("person", new string[] { "cPersonCode", " LEFT JOIN Person pe ON pe.cPersonCode = t.k_person",
                "pe.cPersonName AS n_person" });
            map.Add("department", new string[] { "cDepCode", " LEFT JOIN Department de ON de.cDepCode = t.k_department",
                "de.cDepName AS n_department" });
            return map;
        }

        // 整句。bounds 是各期间起始日加 period_to 次日（ReportsMgmtSales.Bounds）。
        // ? 的顺序：收入侧（按期间分组时每个非末期间的次期起始日、窗口起、窗口止、数据权限），成本侧（年度、起止期间、数据权限）。
        internal static string Query(WorkContext ctx, MgmtSalesArgs m, int year, string[] bounds, List<object> ps)
        {
            string sa = Side(ctx, m, SaInner.Replace("{PER}", PeriodCase(m, bounds, ps)) + (m.Unverified ? "" : Verified),
                "SUM(q.qty) AS qty, SUM(q.rev) AS rev, SUM(q.rev_tax) AS rev_tax", ps, Window(bounds));
            string ia = Side(ctx, m, IaInner, "SUM(q.cqty) AS cqty, SUM(q.cogs) AS cogs", ps,
                new object[] { year, m.From, m.To });
            StringBuilder sql = new StringBuilder("SELECT ");
            StringBuilder joins = new StringBuilder();
            for (int i = 0; i < m.GroupBy.Length; i++)
            {
                string[] dim = DimCols[m.GroupBy[i]];
                sql.Append("t.k_").Append(m.GroupBy[i]).Append(", ");
                if (dim[2].Length > 0)
                {
                    sql.Append(dim[2]).Append(", ");
                    joins.Append(dim[1]);
                }
            }
            sql.Append("t.qty, t.rev, t.rev_tax, t.cost_qty, t.cogs FROM (SELECT ").Append(Coalesced(m))
                .Append(Measures).Append(" FROM (").Append(sa).Append(") a FULL OUTER JOIN (").Append(ia)
                .Append(") c ON ").Append(OnClause(m)).Append(") t").Append(joins);
            return sql.ToString();
        }

        // 一侧的聚合：内层取明细，数据权限挂在 q 上（WHERE 末尾），按分组键 GROUP BY；没有分组键时整体一行。
        static string Side(WorkContext ctx, MgmtSalesArgs m, string inner, string sums, List<object> ps, object[] innerArgs)
        {
            ps.AddRange(innerArgs);
            StringBuilder sql = new StringBuilder("SELECT ");
            string keys = Keys(m);
            sql.Append(keys.Length > 0 ? keys + ", " : "").Append(sums).Append(" FROM (").Append(inner).Append(") q WHERE 1 = 1");
            // 数据权限：客户、存货、业务员、部门。
            PermHook.Where(sql, ps, ctx, "q");
            if (keys.Length > 0)
            {
                sql.Append(" GROUP BY ").Append(keys);
            }
            return sql.ToString();
        }

        static string Keys(MgmtSalesArgs m)
        {
            StringBuilder sql = new StringBuilder();
            for (int i = 0; i < m.GroupBy.Length; i++)
            {
                sql.Append(i == 0 ? "" : ", ").Append("q.").Append(DimCols[m.GroupBy[i]][0]);
            }
            return sql.ToString();
        }

        static string Coalesced(MgmtSalesArgs m)
        {
            StringBuilder sql = new StringBuilder();
            for (int i = 0; i < m.GroupBy.Length; i++)
            {
                string col = DimCols[m.GroupBy[i]][0];
                sql.Append("COALESCE(a.").Append(col).Append(", c.").Append(col).Append(") AS k_").Append(m.GroupBy[i]).Append(",");
            }
            return sql.ToString();
        }

        static string OnClause(MgmtSalesArgs m)
        {
            if (m.GroupBy.Length == 0)
            {
                return "1 = 1";
            }
            StringBuilder sql = new StringBuilder();
            for (int i = 0; i < m.GroupBy.Length; i++)
            {
                string col = DimCols[m.GroupBy[i]][0];
                sql.Append(i == 0 ? "" : " AND ").Append("a.").Append(col).Append(" = c.").Append(col);
            }
            return sql.ToString();
        }

        // 发票日期 → 期间号：按期间分组时 CASE WHEN h.dDate < 次期起始日 THEN 期间 …，否则常量 0（不进分组）。
        static string PeriodCase(MgmtSalesArgs m, string[] bounds, List<object> ps)
        {
            if (!m.Has("period"))
            {
                return "0";
            }
            StringBuilder sql = new StringBuilder("CASE");
            for (int p = m.From; p < m.To; p++)
            {
                sql.Append(" WHEN h.dDate < CONVERT(date, ?, 23) THEN ").Append(p.ToString(CultureInfo.InvariantCulture));
                ps.Add(bounds[p - m.From + 1]);
            }
            sql.Append(" ELSE ").Append(m.To.ToString(CultureInfo.InvariantCulture)).Append(" END");
            return sql.ToString();
        }

        static object[] Window(string[] bounds)
        {
            return new object[] { bounds[0], bounds[bounds.Length - 1] };
        }
    }
}
