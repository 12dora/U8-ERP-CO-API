using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 客户变动 customer_churn：销售发票按客户汇总当前窗口（date_from 到 date_to）和基线窗口（date_from 之前 lookback_months 个月）
    // 的收入，分出新客户、流失客户和下降客户（分类规则见 SaChurnCalc）。
    // 收入口径同经营管理销售分析：非期初发票表体的无税本币 iNatMoney，缺省只含销售复核人 cChecker 非空的发票（红字发票是负数，直接相加）。
    // 最近开票的业务员取该客户最后一张发票上的业务员。数据权限：客户必控，业务员可空，挂在发票表头 h 上。
    // 汇总的客户数超过 MaxCustomers 时 400。读线程，只用 ctx.Conn、登录日期和权限快照。
    internal static class ReportsSaChurn
    {
        const int MaxCustomers = 20000;

        // inv：到 date_to 为止的发票行，w=1 在当前窗口、w=2 在基线窗口、0 都不在（只用来找第一张发票）。
        // 参数：当前窗口起止 | 基线窗口起、止（止 = date_from，不含）| date_to | 数据权限。
        internal const string Main = ";WITH inv AS (SELECT h.cCusCode AS cus, h.cPersonCode AS per, h.dDate AS dt, h.SBVID AS id,"
            + " ISNULL(b.iNatMoney, 0) AS amt,"
            + " CASE WHEN h.dDate>=CONVERT(date, ?, 23) AND h.dDate<DATEADD(day, 1, CONVERT(date, ?, 23)) THEN 1"
            + " WHEN h.dDate>=CONVERT(date, ?, 23) AND h.dDate<CONVERT(date, ?, 23) THEN 2 ELSE 0 END AS w"
            + " FROM SaleBillVouch h JOIN SaleBillVouchs b ON b.SBVID=h.SBVID"
            + " WHERE h.dDate<DATEADD(day, 1, CONVERT(date, ?, 23)) AND ISNULL(h.bFirst, 0)=0"
            + " AND NULLIF(LTRIM(RTRIM(h.cChecker)), N'') IS NOT NULL{PERM})"
            + " SELECT g.cus AS code, c.cCusName AS name, p.cPersonName AS person, CONVERT(varchar(10), g.f, 23) AS first_date,"
            + " CONVERT(varchar(10), g.l, 23) AS last_date, g.rc, g.rb, g.nc, g.nb, g.mc, g.mb"
            + " FROM (SELECT cus, SUM(CASE WHEN w=1 THEN amt ELSE 0 END) AS rc, SUM(CASE WHEN w=2 THEN amt ELSE 0 END) AS rb,"
            + " SUM(CASE WHEN w=1 THEN 1 ELSE 0 END) AS nc, SUM(CASE WHEN w=2 THEN 1 ELSE 0 END) AS nb,"
            + " COUNT(DISTINCT CASE WHEN w=1 THEN CONVERT(char(7), dt, 23) END) AS mc,"
            + " COUNT(DISTINCT CASE WHEN w=2 THEN CONVERT(char(7), dt, 23) END) AS mb, MIN(dt) AS f, MAX(dt) AS l"
            + " FROM inv GROUP BY cus HAVING SUM(CASE WHEN w>0 THEN 1 ELSE 0 END)>0) g"
            + " LEFT JOIN Customer c ON c.cCusCode=g.cus"
            + " OUTER APPLY (SELECT TOP 1 v.per FROM inv v WHERE v.cus=g.cus ORDER BY v.dt DESC, v.id DESC) lp"
            + " LEFT JOIN Person p ON p.cPersonCode=lp.per ORDER BY g.cus";

        public static ApiResult Run(WorkContext ctx, ReportArgs ignored)
        {
            ChurnArgs a = SaArgs.Churn(ctx.Item.Body);
            string to = ReportsStockSql.DateOr(ctx, a.DateTo);
            if (string.CompareOrdinal(a.DateFrom, to) > 0)
            {
                throw GlReq.Bad("date_from 不能晚于 date_to（缺省为登录日期）", "date_from");
            }
            ChurnWindows w = SaChurnCalc.Windows(a.DateFrom, to, a.Lookback);
            a.NoAmountOrder = PermMask.WillMaskAmounts(ctx);
            ChurnLists lists = SaChurnCalc.Classify(Load(ctx, a, w, to), w, a.DateFrom, a.DeclinePct, a.NoAmountOrder);
            Dictionary<string, object> body = Reports.Body();
            body["new"] = SaChurnCalc.Items(lists.New, a.Limit);
            body["lost"] = SaChurnCalc.Items(lists.Lost, a.Limit);
            body["declining"] = SaChurnCalc.Items(lists.Declining, a.Limit);
            body["order"] = a.NoAmountOrder ? "code" : "revenue";
            body["counts"] = SaChurnCalc.Counts(lists);
            body["windows"] = SaChurnCalc.WindowsBody(w);
            body["lookback_months"] = a.Lookback;
            body["decline_pct"] = a.DeclinePct;
            return ApiResult.Ok(body);
        }

        static List<ChurnRow> Load(WorkContext ctx, ChurnArgs a, ChurnWindows w, string to)
        {
            List<object> ps = new List<object>();
            ps.Add(a.DateFrom);
            ps.Add(to);
            ps.Add(SaChurnCalc.Text(w.BaseFrom));
            ps.Add(a.DateFrom);
            ps.Add(to);
            StringBuilder perm = new StringBuilder();
            PermHook.Where(perm, ps, ctx, "h");
            string sql = Main.Replace("{PERM}", perm.ToString());
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), MaxCustomers + 1);
            if (rows.Count > MaxCustomers)
            {
                throw GlReq.Bad("窗口内有发票的客户超过 " + MaxCustomers.ToString(CultureInfo.InvariantCulture)
                    + " 个，请缩短日期范围或回看月数");
            }
            List<ChurnRow> list = new List<ChurnRow>();
            for (int i = 0; i < rows.Count; i++)
            {
                list.Add(Row(rows[i]));
            }
            return list;
        }

        static ChurnRow Row(Dictionary<string, object> row)
        {
            ChurnRow r = new ChurnRow();
            r.Code = GlSql.Col(row, "code");
            r.Name = Reports.Text(row, "name");
            r.Person = Reports.Text(row, "person");
            r.First = GlSql.Col(row, "first_date");
            r.Last = GlSql.Col(row, "last_date");
            r.RevCur = SaCalc.Dec(row, "rc");
            r.RevBase = SaCalc.Dec(row, "rb");
            r.LinesCur = GlSql.Int(row, "nc");
            r.LinesBase = GlSql.Int(row, "nb");
            r.MonthsCur = GlSql.Int(row, "mc");
            r.MonthsBase = GlSql.Int(row, "mb");
            return r;
        }
    }
}
