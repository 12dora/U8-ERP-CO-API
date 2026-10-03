using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 往来账龄分析 arap_aging：按往来单位（partner）、业务员（person）或两者（partner_person）分组。
    // 业务员取原单自己的行上的 cPerson（核销行上的是收款 / 付款单的，不用），原单没有时取客户 / 供应商档案的
    // 专管业务员（cCusPPerson / cVenPPerson）。
    // 表名、列名只来自固定表（ReportsArap.Sides、Modes）；科目前缀、编码、天数只进参数。
    internal static class ReportsArapAging
    {
        // {COLS} / {SUMS} 按分段个数生成（只用下标常量），{WHENS} 每段一个天数参数；{KEYS} {SELECT} {JOINS} 取自 Modes。
        // m 层是逐张单据（应付逐行）的余额、账龄和解析后的业务员 psn（没有为空串）；src 1 单据、2 档案、0 都没有。
        // {MFILTER}：业务员条件、分组键上的游标、数据权限。绑定顺序：limit，每段天数，{INNER}（见 InnerArgs，再接
        // 明细条件），{MFILTER}。
        const string AgingSql = "SELECT TOP (?) {SELECT}, CONVERT(decimal(18,2), g.bal) bal,"
            + " CONVERT(decimal(18,2), g.pre) prepaid, CONVERT(decimal(18,2), g.ovd) ovd, g.smin, g.smax{COLS}"
            + " FROM (SELECT {KEYS}, SUM(x.bal) bal, SUM(CASE WHEN x.is_open=0 THEN -x.bal ELSE 0 END) pre,"
            + " SUM(CASE WHEN x.is_open=1 AND x.bk>0 THEN x.bal ELSE 0 END) ovd, MIN(x.src) smin, MAX(x.src) smax{SUMS}"
            + " FROM (SELECT m.cDwCode, m.bal, m.is_open, m.psn, m.src, CASE WHEN m.age<=0 THEN 0{WHENS} END bk"
            + " FROM (SELECT r.cDwCode, r.bal, r.is_open, r.age,"
            + " ISNULL(COALESCE(r.psn, NULLIF(LTRIM(RTRIM(cp.{PPERSON})), N'')), N'') psn,"
            + " CASE WHEN r.psn IS NOT NULL THEN 1 WHEN NULLIF(LTRIM(RTRIM(cp.{PPERSON})), N'') IS NOT NULL THEN 2"
            + " ELSE 0 END src FROM ({INNER}) r LEFT JOIN {NTABLE} cp ON cp.{CODE}=r.cDwCode) m WHERE 1=1{MFILTER}) x"
            + " GROUP BY {KEYS}) g{JOINS} WHERE 1=1";

        // 到期日（basis=due）：收款 / 付款日期；否则信用期（明细行的 iCreditPeriod）不为 0 时按信用期：
        // 信用起始日 + 信用期，没有信用起始日取单据日期（同原来的算法，不加信用期）；
        // 信用期为 0 或空时：起算日（信用起始日，没有取单据日期）+ default_credit_days（?，缺省 0，结果与原来相同）。
        const string DueSql = " COALESCE(d.dGatheringDate, CASE WHEN ISNULL(d.iCreditPeriod,0)<>0"
            + " THEN COALESCE(DATEADD(day, d.iCreditPeriod, d.dCreditStart), d.dVouchDate) END,"
            + " DATEADD(day, ?, COALESCE(d.dCreditStart, d.dVouchDate)))";

        // 应收：核销行（9P，对方单据不是自己）归到原单，再按单据算账龄（与 U8 账龄分析一致）。
        // 26/27/R0 与小于 48 的单据类型是应收；其余（收款单 48/49）的剩余贷方是预收。
        // 业务员只取原单自己的行（核销行上的是收款单的），原单没有就落到档案，不退回收款单的业务员。
        // 同一张单据的业务员一致。
        const string ArInner = "SELECT g.cDwCode, SUM(g.bal) bal,"
            + " CASE WHEN g.vt IN (N'26', N'27', N'R0') OR g.vt<N'48' THEN 1 ELSE 0 END is_open,"
            + " DATEDIFF(day, CASE WHEN ?=1 THEN MIN(g.dDue) ELSE MIN(g.dVouchDate) END, CONVERT(date, ?, 23)) age,"
            + " MAX(CASE WHEN g.rd=0 THEN g.psn END) psn"
            + " FROM (SELECT d.cDwCode, ISNULL(d.iDAmount,0)-ISNULL(d.iCAmount,0) bal, d.dVouchDate,"
            + DueSql + " dDue, f.rd,"
            + " CASE WHEN f.rd=1 THEN d.cCoVouchType ELSE d.cVouchType END vt,"
            + " CASE WHEN f.rd=1 THEN d.cCoVouchID ELSE d.cVouchID END vid, NULLIF(LTRIM(RTRIM(d.cPerson)), N'') psn"
            + " FROM Ar_Detail d CROSS APPLY (SELECT CASE WHEN d.cProcStyle=N'9P'"
            + " AND NOT (d.cCoVouchType=d.cVouchType AND d.cCoVouchID=d.cVouchID) THEN 1 ELSE 0 END rd) f"
            + " WHERE d.iFlag<3 AND d.dRegDate<DATEADD(day, 1, CONVERT(date, ?, 23)){FILTER}) g"
            + " GROUP BY g.cDwCode, g.vt, g.vid";

        // 应付：按明细行算账龄。对方单据类型小于 48 或 P0–P9 是应付，其余（付款单）的剩余借方是预付。
        // 业务员与应收一样按原单解析：同一（往来单位、对方单据类型、对方单据号）里取原单自己的行
        // （cCoVouchType=cVouchType 且 cCoVouchID=cVouchID）上的 cPerson，核销行（9P）不用自己的。
        const string ApInner = "SELECT d.cDwCode, ISNULL(d.iCAmount,0)-ISNULL(d.iDAmount,0) bal,"
            + " CASE WHEN d.cCoVouchType<N'48' OR (d.cCoVouchType>=N'P0' AND d.cCoVouchType<=N'P9') THEN 1 ELSE 0 END is_open,"
            + " DATEDIFF(day, CASE WHEN ?=1 THEN" + DueSql + " ELSE d.dVouchDate END,"
            + " CONVERT(date, ?, 23)) age, MAX(CASE WHEN d.cCoVouchType=d.cVouchType AND d.cCoVouchID=d.cVouchID"
            + " THEN NULLIF(LTRIM(RTRIM(d.cPerson)), N'') END) OVER (PARTITION BY d.cDwCode, d.cCoVouchType, d.cCoVouchID) psn"
            + " FROM Ap_Detail d WHERE d.iFlag<3 AND d.dRegDate<DATEADD(day, 1, CONVERT(date, ?, 23)){FILTER}";

        const string PartnerJoin = " LEFT JOIN {NTABLE} n ON n.{CODE}=g.cDwCode";
        const string PersonJoin = " LEFT JOIN Person p ON p.cPersonCode=g.psn";
        const string OptionSql = "SELECT TOP 1 cValue FROM AccInformation WHERE cSysID=? AND cName=N'PersonAuthCtrl'";

        // group_by → { 分组键, 输出列, 名称表连接, 排序 }。
        static readonly Dictionary<string, string[]> Modes = BuildModes();

        static Dictionary<string, string[]> BuildModes()
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.Ordinal);
            map.Add("partner", new string[] { "x.cDwCode", "g.cDwCode partner, n.{NAME} pname", PartnerJoin, "g.cDwCode" });
            map.Add("person", new string[] { "x.psn", "g.psn psn, p.cPersonName psname", PersonJoin, "g.psn" });
            map.Add("partner_person", new string[]
            {
                "x.cDwCode, x.psn", "g.cDwCode partner, n.{NAME} pname, g.psn psn, p.cPersonName psname",
                PartnerJoin + PersonJoin, "g.cDwCode, g.psn"
            });
            return map;
        }

        public static ApiResult Aging(WorkContext ctx, ReportArgs a)
        {
            string[] side = ReportsArap.Sides[a.Side];
            string[] mode = Modes[a.GroupBy];
            int n = a.Buckets.Length;
            List<object> args = new List<object>();
            args.Add(a.Limit + 1);
            string whens = Whens(a.Buckets, args);
            string inner = Inner(a, args);
            string mfilter = MFilter(ctx, a, args);
            string text = AgingSql.Replace("{SELECT}", mode[1]).Replace("{KEYS}", mode[0]).Replace("{JOINS}", mode[2])
                .Replace("{COLS}", Cols(n)).Replace("{SUMS}", Sums(n)).Replace("{WHENS}", whens)
                .Replace("{PPERSON}", side[5]).Replace("{INNER}", inner).Replace("{MFILTER}", mfilter);
            StringBuilder sql = new StringBuilder(ReportsArap.Names(text, side));
            if (a.NonZero)
            {
                sql.Append(" AND (ROUND(g.bal, 2)<>0 OR ROUND(g.pre, 2)<>0)");
            }
            // 逾期是毛额（未核销的收付款不冲抵，单列在 prepaid）；overdue_only 另要求净余额大于 0，
            // 预收付已经盖过逾期的单位不算催收对象。
            if (a.OverdueOnly)
            {
                sql.Append(" AND ROUND(g.ovd, 2)>0 AND ROUND(g.bal, 2)>0");
            }
            sql.Append(" ORDER BY ").Append(mode[3]);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), args.ToArray(), a.Limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                items.Add(AgingItem(rows[i], n, a));
            }
            string next = rows.Count > a.Limit ? Next(a, rows[a.Limit - 1]) : null;
            return ReportsArap.Page(a, items, next);
        }

        // {INNER} 的文本和参数（明细条件接在 InnerArgs 之后）。ReportsAgingSelfTest 核对 ? 与参数的对应。
        internal static string Inner(ReportArgs a, List<object> args)
        {
            InnerArgs(a, args);
            return (a.Side == "ar" ? ArInner : ApInner).Replace("{FILTER}", ReportsArap.Filter(a, args));
        }

        // {INNER} 前段的参数，按 ? 在文本里出现的顺序：应收 basis、as_of（账龄）、default_credit_days（到期日）、as_of（登记日）；
        // 应付的到期日在账龄的 DATEDIFF 里，是 basis、default_credit_days、as_of、as_of。
        static void InnerArgs(ReportArgs a, List<object> args)
        {
            args.Add(a.Basis == "due" ? 1 : 0);
            if (a.Side != "ar")
            {
                args.Add(a.DefaultCreditDays);
            }
            args.Add(a.AsOf);
            if (a.Side == "ar")
            {
                args.Add(a.DefaultCreditDays);
            }
            args.Add(a.AsOf);
        }

        static string Whens(int[] days, List<object> args)
        {
            StringBuilder whens = new StringBuilder();
            for (int i = 0; i < days.Length; i++)
            {
                whens.Append(" WHEN m.age<=? THEN ").Append(ReportsArap.Idx(i + 1));
                args.Add(days[i]);
            }
            whens.Append(" ELSE ").Append(ReportsArap.Idx(days.Length + 1));
            return whens.ToString();
        }

        // m 层条件：业务员编码、游标（按往来单位分组时已在明细条件里）、客商数据权限（规则登记在 cDwCode 上，
        // 放在单据层对各种分组都成立）、业务员数据权限。
        static string MFilter(WorkContext ctx, ReportArgs a, List<object> args)
        {
            StringBuilder sql = new StringBuilder();
            if (a.Persons.Length > 0)
            {
                sql.Append(" AND m.psn IN (");
                for (int i = 0; i < a.Persons.Length; i++)
                {
                    sql.Append(i == 0 ? "?" : ", ?");
                    args.Add(a.Persons[i]);
                }
                sql.Append(")");
            }
            After(a, sql, args);
            // 数据权限：客户或供应商。
            PermHook.Where(sql, args, ctx, "m");
            PersonPerm(ctx, a, sql, args);
            return sql.ToString();
        }

        // 业务员分组的游标是（group_by、往来单位、业务员）三段，group_by 对不上时 400。
        static void After(ReportArgs a, StringBuilder sql, List<object> args)
        {
            if (a.GroupBy == "partner")
            {
                return;
            }
            string[] after = Reports.Uncursor(a.After, 3);
            if (after == null)
            {
                return;
            }
            if (after[0] != a.GroupBy)
            {
                throw GlReq.Bad("after 游标无效");
            }
            if (a.GroupBy == "person")
            {
                sql.Append(" AND m.psn>?");
                args.Add(after[2]);
                return;
            }
            sql.Append(" AND (m.cDwCode>? OR (m.cDwCode=? AND m.psn>?))");
            args.Add(after[1]);
            args.Add(after[1]);
            args.Add(after[2]);
        }

        // 业务员数据权限：U8 只在应收 / 应付选项「是否启用业务员权限」（AccInformation PersonAuthCtrl）打开时控制。
        // 条件落在解析后的业务员上；没有业务员的单据放行（同单据列表上可空的业务员列）。未覆盖：与 U8 账龄表逐项核对。
        static void PersonPerm(WorkContext ctx, ReportArgs a, StringBuilder sql, List<object> args)
        {
            if (ctx == null || ctx.Item == null || !PermRegistry.IsRead(ctx.Item.Path))
            {
                return;
            }
            PermContext p = PermCheck.Of(ctx);
            if (!p.Controls(PermObj.Person) || !PersonOption(ctx, a.Side))
            {
                return;
            }
            PermSql.AppendCode(sql, args, p, PermObj.Person, "m.psn", true);
        }

        static bool PersonOption(WorkContext ctx, string side)
        {
            string value = Rows.Scalar(ctx.Conn, OptionSql, new object[] { side == "ap" ? "AP" : "AR" });
            string text = value == null ? "" : value.Trim();
            return text == "1" || text.Equals("true", StringComparison.OrdinalIgnoreCase);
        }

        static string Next(ReportArgs a, Dictionary<string, object> row)
        {
            if (a.GroupBy == "partner")
            {
                return Reports.Cursor(GlSql.Col(row, "partner"));
            }
            return Reports.Cursor(a.GroupBy, GlSql.Col(row, "partner"), GlSql.Col(row, "psn"));
        }

        static Dictionary<string, object> AgingItem(Dictionary<string, object> row, int n, ReportArgs a)
        {
            Dictionary<string, object> item = a.GroupBy == "person"
                ? new Dictionary<string, object>() : ReportsArap.Partner(row);
            if (a.GroupBy != "partner")
            {
                PutPerson(item, row, a.Side);
            }
            item["balance"] = GlSql.Money(row, "bal");
            List<object> aging = new List<object>(n + 2);
            for (int b = 0; b <= n + 1; b++)
            {
                aging.Add(GlSql.Money(row, "b" + ReportsArap.Idx(b)));
            }
            item["aging"] = aging;
            item["prepaid"] = GlSql.Money(row, "prepaid");
            if (a.Basis == "due")
            {
                item["overdue"] = GlSql.Money(row, "ovd");
            }
            return item;
        }

        // person_source：document 全部取自单据，customer / vendor 全部取自档案的专管业务员，mixed 两者都有。
        static void PutPerson(Dictionary<string, object> item, Dictionary<string, object> row, string side)
        {
            string code = GlSql.Col(row, "psn");
            item["person_code"] = code.Length == 0 ? null : code;
            item["person_name"] = Reports.Text(row, "psname");
            string source = null;
            if (code.Length > 0)
            {
                int min = GlSql.Int(row, "smin");
                int max = GlSql.Int(row, "smax");
                source = min != max ? "mixed" : (min == 1 ? "document" : (side == "ap" ? "vendor" : "customer"));
            }
            item["person_source"] = source;
        }

        static string Cols(int n)
        {
            StringBuilder sql = new StringBuilder();
            for (int b = 0; b <= n + 1; b++)
            {
                string k = ReportsArap.Idx(b);
                sql.Append(", CONVERT(decimal(18,2), g.b").Append(k).Append(") b").Append(k);
            }
            return sql.ToString();
        }

        static string Sums(int n)
        {
            StringBuilder sql = new StringBuilder();
            for (int b = 0; b <= n + 1; b++)
            {
                string k = ReportsArap.Idx(b);
                sql.Append(", SUM(CASE WHEN x.is_open=1 AND x.bk=").Append(k).Append(" THEN x.bal ELSE 0 END) b").Append(k);
            }
            return sql.ToString();
        }
    }
}
