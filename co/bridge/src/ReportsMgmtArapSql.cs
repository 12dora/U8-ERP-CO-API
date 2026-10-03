using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 经营管理往来账期的 SQL（只读）。表名、列名只来自固定表（ReportsArap.Sides 与这里的 Origins）；
    // 日期、天数、科目前缀、往来标志只进 ? 参数。三句：
    // 1. 余额与账龄：ReportsArapAging.Inner（basis=due，同 arap_aging 的逐单据余额、到期日与账龄）外面再按往来单位汇总，
    //    分段 b0（未到期，账龄 <= 0）、b1…bn（按 buckets）、b(n+1)（最后一段以上）；逾期 = 账龄 > 0 的未结余额；
    //    未核销的收付款（预收 / 预付）单列 prepaid，余额 = 各段合计 - prepaid。
    // 2. 近 12 个月的原始单据（as_of 次日往前 12 个月起、到 as_of，按单据日期）：先按单据（类型 + 单号）合计各行，
    //    单据数按张计；信用期 iCreditPeriod 分布，以及到 as_of 为止第一次收付款核销（9P，登记日期 dRegDate）距单据日期的
    //    天数（早于单据日期按 0 天）。核销行按（对方单据类型、单号）找，不比往来单位：转账、应收冲应付核销到别的单位名下。
    //    原始单据是自己那一行（cProcStyle = cVouchType，对方单据就是自己）、金额为正（红字不计）。
    // 3. 未结票据：AP_Note 余额 iRAmount 不为 0（U8 结算、贴现、背书、退回会把它减到 0），签发日期不晚于 as_of，
    //    按当前状态（as_of 之后才处理掉的票据不会再算回来）。往来单位取 cDwCode，为空时取 cEndorser（应收票据的出票单位
    //    记在 cEndorser 上，cDwCode 可能为空），数据权限与名称都按这个单位。
    internal static class ReportsMgmtArapSql
    {
        // side → { 原始单据类型条件, 单据金额表达式（应收借减贷，应付贷减借）, 票据 cFlag }。
        // 类型条件与 ReportsArapAging 的 is_open 一致：应收 26 / 27 / R0 与小于 48 的类型，应付小于 48 或 P0–P9。
        static readonly Dictionary<string, string[]> Origins = BuildOrigins();

        const string AgingSql = "SELECT x.cDwCode AS partner, n.{NAME} AS pname, CONVERT(decimal(18,2), SUM(x.bal)) AS bal,"
            + " CONVERT(decimal(18,2), SUM(CASE WHEN x.is_open = 0 THEN -x.bal ELSE 0 END)) AS pre,"
            + " CONVERT(decimal(18,2), SUM(CASE WHEN x.is_open = 1 AND x.age > 0 THEN x.bal ELSE 0 END)) AS ovd{COLS}"
            + " FROM (SELECT r.cDwCode, r.bal, r.is_open, r.age, CASE WHEN r.age <= 0 THEN 0{WHENS} END AS bk"
            + " FROM ({INNER}) r) x LEFT JOIN {NTABLE} n ON n.{CODE} = x.cDwCode WHERE 1 = 1";

        const string DaysExpr = "CASE WHEN w.f IS NULL THEN NULL WHEN w.f < x.dVouchDate THEN 0"
            + " ELSE DATEDIFF(day, x.dVouchDate, w.f) END";

        // 内层 d 是单据明细行（条件、科目前缀、数据权限都挂在 d 上），按单据合计成 x；w 是每张单据第一次核销的登记日期。
        const string HistorySql = "SELECT x.cDwCode AS partner, n.{NAME} AS pname, x.cp, " + DaysExpr + " AS days,"
            + " COUNT(*) AS cnt, CONVERT(decimal(18,2), SUM(x.amt)) AS amt"
            + " FROM (SELECT d.cDwCode, d.cVouchType, d.cVouchID, MAX(ISNULL(d.iCreditPeriod, 0)) AS cp,"
            + " MIN(d.dVouchDate) AS dVouchDate, SUM({AMT}) AS amt FROM {TABLE} d"
            + " WHERE d.iFlag < 3 AND d.cProcStyle = d.cVouchType AND d.cCoVouchType = d.cVouchType AND d.cCoVouchID = d.cVouchID"
            + " AND {ORIG} AND {AMT} > 0 AND d.dVouchDate >= DATEADD(month, -12, DATEADD(day, 1, CONVERT(date, ?, 23)))"
            + " AND d.dVouchDate < DATEADD(day, 1, CONVERT(date, ?, 23)) AND d.dRegDate < DATEADD(day, 1, CONVERT(date, ?, 23))";

        const string HistoryDocs = " GROUP BY d.cDwCode, d.cVouchType, d.cVouchID) x"
            + " LEFT JOIN (SELECT p.cCoVouchType, p.cCoVouchID, MIN(p.dRegDate) AS f FROM {TABLE} p"
            + " WHERE p.cProcStyle = N'9P' AND p.iFlag < 3 AND NOT (p.cVouchType = p.cCoVouchType AND p.cVouchID = p.cCoVouchID)"
            + " AND p.dRegDate < DATEADD(day, 1, CONVERT(date, ?, 23)) GROUP BY p.cCoVouchType, p.cCoVouchID) w"
            + " ON w.cCoVouchType = x.cVouchType AND w.cCoVouchID = x.cVouchID"
            + " LEFT JOIN {NTABLE} n ON n.{CODE} = x.cDwCode";

        const string HistoryGroup = " GROUP BY x.cDwCode, n.{NAME}, x.cp, " + DaysExpr;

        const string NotesSql = "SELECT x.cDwCode AS partner, m.{NAME} AS pname, CONVERT(decimal(18,2), SUM(x.amt)) AS amt"
            + " FROM (SELECT COALESCE(NULLIF(LTRIM(RTRIM(n.cDwCode)), N''), NULLIF(LTRIM(RTRIM(n.cEndorser)), N'')) AS cDwCode,"
            + " CASE WHEN ISNULL(n.iRAmount_Local, 0) <> 0 THEN n.iRAmount_Local ELSE n.iRAmount END AS amt FROM AP_Note n"
            + " WHERE n.cFlag = ? AND ISNULL(n.iRAmount, 0) <> 0 AND n.dSignDate < DATEADD(day, 1, CONVERT(date, ?, 23))) x"
            + " LEFT JOIN {NTABLE} m ON m.{CODE} = x.cDwCode WHERE 1 = 1";

        // 模块启用：应收 dARStartDate、应付 dAPStartDate（空表示没有启用应收 / 应付款管理）。
        internal const string EnabledSql = "SELECT TOP 1 cValue FROM AccInformation WHERE cSysID = ? AND cName = ?";

        static Dictionary<string, string[]> BuildOrigins()
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.Ordinal);
            map.Add("ar", new string[]
            {
                "(d.cVouchType IN (N'26', N'27', N'R0') OR d.cVouchType < N'48')",
                "ISNULL(d.iDAmount, 0) - ISNULL(d.iCAmount, 0)", "AR"
            });
            map.Add("ap", new string[]
            {
                "(d.cVouchType < N'48' OR (d.cVouchType >= N'P0' AND d.cVouchType <= N'P9'))",
                "ISNULL(d.iCAmount, 0) - ISNULL(d.iDAmount, 0)", "AP"
            });
            return map;
        }

        // ? 的顺序：每段天数（{WHENS}）、ReportsArapAging.Inner 的参数（含科目条件）、数据权限。
        internal static string Aging(WorkContext ctx, ReportArgs a, List<object> ps)
        {
            int n = a.Buckets.Length;
            StringBuilder whens = new StringBuilder();
            StringBuilder cols = new StringBuilder();
            for (int i = 0; i < n; i++)
            {
                whens.Append(" WHEN r.age <= ? THEN ").Append(Idx(i + 1));
                ps.Add(a.Buckets[i]);
            }
            whens.Append(" ELSE ").Append(Idx(n + 1));
            for (int b = 0; b <= n + 1; b++)
            {
                cols.Append(", CONVERT(decimal(18,2), SUM(CASE WHEN x.is_open = 1 AND x.bk = ").Append(Idx(b))
                    .Append(" THEN x.bal ELSE 0 END)) AS b").Append(Idx(b));
            }
            string inner = ReportsArapAging.Inner(a, ps);
            string[] side = ReportsArap.Sides[a.Side];
            StringBuilder sql = new StringBuilder(ReportsArap.Names(AgingSql, side).Replace("{COLS}", cols.ToString())
                .Replace("{WHENS}", whens.ToString()).Replace("{INNER}", inner));
            // 数据权限：客户或供应商。
            PermHook.Where(sql, ps, ctx, "x");
            sql.Append(" GROUP BY x.cDwCode, n.").Append(side[3]);
            return sql.ToString();
        }

        // ? 的顺序：as_of（窗口起点）、as_of（窗口终点）、as_of（登记日期）、科目前缀、数据权限、as_of（核销截止）。
        internal static string History(WorkContext ctx, ReportArgs a, List<object> ps)
        {
            string[] side = ReportsArap.Sides[a.Side];
            string[] origin = Origins[a.Side];
            for (int i = 0; i < 3; i++)
            {
                ps.Add(a.AsOf);
            }
            StringBuilder sql = new StringBuilder(ReportsArap.Names(HistorySql, side).Replace("{TABLE}", side[0])
                .Replace("{ORIG}", origin[0]).Replace("{AMT}", origin[1]));
            Reports.LikeAny(sql, ps, "d.cCode", a.Accounts, false);
            Reports.LikeAny(sql, ps, "d.cCode", a.Excludes, true);
            // 数据权限：客户或供应商（单据明细行的往来单位）。
            PermHook.Where(sql, ps, ctx, "d");
            sql.Append(ReportsArap.Names(HistoryDocs, side).Replace("{TABLE}", side[0]));
            ps.Add(a.AsOf);
            sql.Append(ReportsArap.Names(HistoryGroup, side));
            return sql.ToString();
        }

        // ? 的顺序：票据标志 AR / AP、as_of、数据权限。
        internal static string Notes(WorkContext ctx, ReportArgs a, List<object> ps)
        {
            string[] side = ReportsArap.Sides[a.Side];
            ps.Add(Origins[a.Side][2]);
            ps.Add(a.AsOf);
            StringBuilder sql = new StringBuilder(ReportsArap.Names(NotesSql, side));
            // 数据权限：客户或供应商（票据的往来单位，cDwCode 为空时取 cEndorser）。
            PermHook.Where(sql, ps, ctx, "x");
            sql.Append(" GROUP BY x.cDwCode, m.").Append(side[3]);
            return sql.ToString();
        }

        internal static string Idx(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
