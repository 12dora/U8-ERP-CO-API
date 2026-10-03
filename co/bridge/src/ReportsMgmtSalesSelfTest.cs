using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的经营管理销售分析部分（不连库）：参数校验与缺省、期间窗口（自然月、U8 会计期间）、
    // SQL 的 ? 与参数一一对应、收入 / 成本两侧的口径条件、top / others / totals 的汇总，以及路由、登录子系统、权限登记。
    internal static class ReportsMgmtSalesSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckBounds();
            CheckQuery(new string[] { "department", "period", "customer", "person", "inventory" }, false);
            CheckQuery(new string[0], true);
            CheckFill();
            Expect("sales registered", All(Array.IndexOf(Reports.Names, ReportsMgmtSalesReq.Name) >= 0,
                Reports.SubOf(ReportsMgmtSalesReq.Name, null) == "SA", PermRegistry.ForKey(ReportsMgmtSalesReq.RuleKey) != null));
        }

        static void CheckParse()
        {
            MgmtSalesArgs m = ReportsMgmtSalesReq.Parse(Body(1, 12, null));
            Expect("sales defaults", All(m.GroupBy.Length == 1, m.GroupBy[0] == "customer", m.Top == 200, !m.Unverified));
            Dictionary<string, object> body = Body(2, 5, new object[] { "department", "period" });
            body["top"] = 500;
            body["include_unverified"] = true;
            m = ReportsMgmtSalesReq.Parse(body);
            Expect("sales group order", All(m.GroupBy.Length == 2, m.GroupBy[0] == "period", m.GroupBy[1] == "department",
                m.Top == 500, m.Unverified, m.Has("period"), !m.Has("customer")));
            Expect("sales empty group", ReportsMgmtSalesReq.Parse(Body(1, 1, new ArrayList())).GroupBy.Length == 0);
            Refused("sales from > to", Body(5, 4, null), "period_from");
            Refused("sales period 13", Body(1, 13, null), "period_to");
            Refused("sales dup dim", Body(1, 2, new object[] { "customer", "customer" }), "group_by.1");
            Refused("sales bad dim", Body(1, 2, new object[] { "warehouse" }), "group_by.0");
            Refused("sales group text", Body(1, 2, "customer"), "group_by");
            Dictionary<string, object> top = Body(1, 2, null);
            top["top"] = 0;
            Refused("sales top 0", top, "top");
            Dictionary<string, object> flag = Body(1, 2, null);
            flag["include_unverified"] = "true";
            Refused("sales unverified text", flag, "include_unverified");
        }

        static void CheckBounds()
        {
            string[] year = ReportsMgmtSales.CalendarBounds(2026, 1, 12);
            Expect("sales calendar year", All(year.Length == 13, year[0] == "2026-01-01", year[12] == "2027-01-01"));
            string[] one = ReportsMgmtSales.CalendarBounds(2026, 3, 3);
            Expect("sales calendar one", All(one.Length == 2, one[0] == "2026-03-01", one[1] == "2026-04-01"));
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(Period("2", "2026-01-26", "2026-02-25"));
            rows.Add(Period("3", "2026-02-26", "2026-03-25"));
            string[] u8 = ReportsMgmtSales.FromPeriods(rows, 2, 3);
            Expect("sales u8 periods", All(u8 != null, u8.Length == 3, u8[0] == "2026-01-26", u8[1] == "2026-02-26",
                u8[2] == "2026-03-26"));
            rows[1] = Period("3", "2026-02-27", "2026-03-25");
            Expect("sales u8 gap", ReportsMgmtSales.FromPeriods(rows, 2, 3) == null);
            Expect("sales u8 missing", ReportsMgmtSales.FromPeriods(rows.GetRange(0, 1), 2, 3) == null);
        }

        // ? 的个数等于参数个数；按期间分组时前面是各期的次期起始日，接着窗口起止，再是成本侧的年度、起止期间。
        static void CheckQuery(string[] dims, bool unverified)
        {
            Dictionary<string, object> body = Body(1, 3, dims);
            body["include_unverified"] = unverified;
            MgmtSalesArgs m = ReportsMgmtSalesReq.Parse(body);
            string[] bounds = ReportsMgmtSales.CalendarBounds(2026, 1, 3);
            List<object> ps = new List<object>();
            string sql = ReportsMgmtSalesSql.Query(null, m, 2026, bounds, ps);
            Expect("sales placeholders " + dims.Length, Count(sql) == ps.Count);
            int at = m.Has("period") ? 2 : 0;
            if (at > 0)
            {
                Expect("sales case args", All((string)ps[0] == "2026-02-01", (string)ps[1] == "2026-03-01"));
            }
            Expect("sales window args", All((string)ps[at] == "2026-01-01", (string)ps[at + 1] == "2026-04-01",
                (int)ps[at + 2] == 2026, (int)ps[at + 3] == 1, (int)ps[at + 4] == 3, ps.Count == at + 5));
            Expect("sales cost rows", All(sql.Contains("s.bSale = 1 AND s.bRdFlag = 0"), sql.Contains("FULL OUTER JOIN"),
                sql.Contains("ISNULL(h.bFirst, 0) = 0")));
            Expect("sales verified", All(sql.Contains("h.cChecker") != unverified, !sql.Contains("cVerifier")));
            bool[] keys = dims.Length == 0 ? new bool[] { sql.Contains("ON 1 = 1"), !sql.Contains("GROUP BY") }
                : new bool[] { sql.Contains("a.cCusCode = c.cCusCode"), sql.Contains("LEFT JOIN Department de") };
            Expect("sales group keys", All(keys));
        }

        static void CheckFill()
        {
            List<ReportsMgmtSales.Group> groups = new List<ReportsMgmtSales.Group>();
            groups.Add(Group("A", 10m, 4m));
            groups.Add(Group("B", 30m, 12m));
            groups.Add(Group("C", 20m, 20m));
            groups.Add(Group("D", 0m, 5m));
            Dictionary<string, object> body = new Dictionary<string, object>();
            ReportsMgmtSales.Fill(body, groups, 2);
            List<object> rows = (List<object>)body["items"];
            Dictionary<string, object> first = (Dictionary<string, object>)rows[0];
            Dictionary<string, object> second = (Dictionary<string, object>)rows[1];
            Dictionary<string, object> others = (Dictionary<string, object>)body["others"];
            Dictionary<string, object> totals = (Dictionary<string, object>)body["totals"];
            Expect("sales top order", All(rows.Count == 2, (string)first["customer_code"] == "B",
                (string)second["customer_code"] == "C"));
            Expect("sales gross", All((decimal)first["gross"] == 18m, (decimal)first["gross_pct"] == 60m,
                (decimal)second["gross_pct"] == 0m));
            Expect("sales others", All((int)others["groups"] == 2, (decimal)others["revenue"] == 10m,
                (decimal)others["cogs"] == 9m));
            Expect("sales totals", All((int)totals["groups"] == 4, (decimal)totals["revenue"] == 60m,
                (decimal)totals["cogs"] == 41m));
            body = new Dictionary<string, object>();
            ReportsMgmtSales.Fill(body, groups.GetRange(3, 1), 5);
            Dictionary<string, object> only = (Dictionary<string, object>)((List<object>)body["items"])[0];
            Expect("sales no revenue", All(body["others"] == null, only["gross_pct"] == null, (decimal)only["gross"] == -5m));
        }

        static ReportsMgmtSales.Group Group(string code, decimal revenue, decimal cogs)
        {
            ReportsMgmtSales.Group g = new ReportsMgmtSales.Group();
            g.Item["customer_code"] = code;
            g.Sums[1] = revenue;
            g.Sums[4] = cogs;
            return g;
        }

        static Dictionary<string, object> Period(string p, string b, string e)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["p"] = p;
            row["b"] = b;
            row["e"] = e;
            return row;
        }

        static Dictionary<string, object> Body(int from, int to, object groupBy)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["period_from"] = from;
            body["period_to"] = to;
            if (groupBy != null)
            {
                body["group_by"] = groupBy;
            }
            return body;
        }

        static int Count(string sql)
        {
            int n = 0;
            for (int i = 0; i < sql.Length; i++)
            {
                n += sql[i] == '?' ? 1 : 0;
            }
            return n;
        }

        static void Refused(string name, Dictionary<string, object> body, string field)
        {
            try
            {
                ReportsMgmtSalesReq.Parse(body);
            }
            catch (BridgeException ex)
            {
                Expect(name, All(ex.Status == 400, ex.Field == field));
                return;
            }
            throw new InvalidOperationException(name);
        }

        static bool All(params bool[] checks)
        {
            return Array.TrueForAll(checks, delegate(bool ok) { return ok; });
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
