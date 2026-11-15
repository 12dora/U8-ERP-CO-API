using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的经营管理销售分析部分（不连库）：参数校验与缺省、期间窗口（自然月、U8 会计期间）、
    // SQL 的 ? 与参数一一对应、收入 / 成本两侧的口径条件、top / others / totals 的汇总，存货分类（维度、级次、过滤）、
    // 单个期间按期间分组、关联方合计那一遍，以及路由、登录子系统、权限登记。
    internal static class ReportsMgmtSalesSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckBounds();
            CheckQuery(new string[] { "department", "period", "customer", "person", "inventory" }, false);
            CheckQuery(new string[0], true);
            CheckFill();
            CheckClassParse();
            CheckClassQuery();
            CheckSinglePeriod();
            CheckIcPass();
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

        static Dictionary<string, object> With(Dictionary<string, object> body, string key, object value)
        {
            body[key] = value;
            return body;
        }

        static void CheckClassParse()
        {
            Dictionary<string, object> body = With(With(With(Body(1, 2, new object[] { "department", "inventory_class", "inventory" }),
                "class_grade", 2), "inv_class", " 1001 "), "ic_customers", new object[] { "C900001", "C900002", "C900001" });
            MgmtSalesArgs m = ReportsMgmtSalesReq.Parse(body);
            Expect("class order", All(m.GroupBy.Length == 3, m.GroupBy[0] == "inventory", m.GroupBy[1] == "inventory_class",
                m.GroupBy[2] == "department", m.NeedClass, m.RollUp));
            Expect("class fields", All(m.ClassGrade == 2, m.InvClass == "1001", m.IcCustomers.Length == 2, m.IcCustomers[1] == "C900002"));
            m = ReportsMgmtSalesReq.Parse(Body(1, 2, null));
            Expect("class defaults", All(m.ClassGrade == 0, m.InvClass == null, m.IcCustomers.Length == 0, !m.NeedClass, !m.RollUp));
            Refused("class grade 0", With(Body(1, 2, null), "class_grade", 0), "class_grade");
            Refused("class grade 10", With(Body(1, 2, null), "class_grade", 10), "class_grade");
            Refused("class grade text", With(Body(1, 2, null), "class_grade", "2"), "class_grade");
            Refused("class blank", With(Body(1, 2, null), "inv_class", "  "), "inv_class");
            Refused("class control", With(Body(1, 2, null), "inv_class", "10\u0001"), "inv_class");
            Refused("class number", With(Body(1, 2, null), "inv_class", 1001), "inv_class");
            Refused("class dup dim", Body(1, 2, new object[] { "inventory_class", "inventory_class" }), "group_by.1");
            Refused("ic text", With(Body(1, 2, null), "ic_customers", "C900001"), "ic_customers");
            Refused("ic item", With(Body(1, 2, null), "ic_customers", new object[] { "C900001", "" }), "ic_customers.1");
            object[] many = new object[ReportsMgmtSalesReq.MaxIcCustomers + 1];
            for (int i = 0; i < many.Length; i++)
            {
                many[i] = "C" + i;
            }
            Refused("ic 21", With(Body(1, 2, null), "ic_customers", many), "ic_customers");
        }

        // 分类维度 + 级次 + 过滤：收入、成本两侧用同一个分类键，? 的顺序 = 文本里的先后。
        static void CheckClassQuery()
        {
            Dictionary<string, object> body = With(With(Body(1, 3, new object[] { "customer", "inventory_class" }), "class_grade", 2),
                "inv_class", "10_01");
            MgmtSalesArgs m = ReportsMgmtSalesReq.Parse(body);
            List<object> ps = new List<object>();
            string sql = ReportsMgmtSalesSql.Query(null, m, 2026, ReportsMgmtSales.CalendarBounds(2026, 1, 3), ps);
            Expect("class placeholders", All(Count(sql) == ps.Count, ps.Count == 9, (int)ps[0] == 2, (string)ps[1] == "2026-01-01",
                (string)ps[2] == "2026-04-01", (string)ps[3] == "10\\_01%", (int)ps[4] == 2, (int)ps[5] == 2026, (int)ps[6] == 1,
                (int)ps[7] == 3, (string)ps[8] == "10\\_01%"));
            Expect("class sql", All(sql.Contains("OUTER APPLY (SELECT TOP 1 g.cInvCCode FROM InventoryClass g WHERE g.iInvCGrade = ?"),
                sql.Contains("LEFT(i.cInvCCode, LEN(g.cInvCCode)) = g.cInvCCode"), sql.Contains("ci.own LIKE ? ESCAPE"),
                sql.Contains("COALESCE(cg.cInvCCode, i.cInvCCode) AS cls FROM Inventory i OUTER APPLY"), sql.Contains("a.cInvClass = c.cInvClass"),
                sql.Contains("LEFT JOIN InventoryClass cl ON cl.cInvCCode = t.k_inventory_class"),
                sql.Contains(") ci ON ci.k = b.cInvCode"), sql.Contains(") ci ON ci.k = s.cInvCode"),
                sql.Contains("ISNULL(ci.cls, N'') AS cInvClass")));
            // 不给级次：分类取存货自己的；不需要分类时 SQL 里没有分类的任何部分。
            m = ReportsMgmtSalesReq.Parse(Body(1, 3, new object[] { "inventory_class" }));
            ps = new List<object>();
            sql = ReportsMgmtSalesSql.Query(null, m, 2026, ReportsMgmtSales.CalendarBounds(2026, 1, 3), ps);
            Expect("class own", All(Count(sql) == ps.Count, ps.Count == 5, !sql.Contains("OUTER APPLY"), sql.Contains("ISNULL(ci.cls, N'') AS cInvClass"), sql.Contains("i.cInvCCode AS cls FROM Inventory i)")));
            m = ReportsMgmtSalesReq.Parse(Body(1, 3, new object[] { "customer" }));
            sql = ReportsMgmtSalesSql.Query(null, m, 2026, ReportsMgmtSales.CalendarBounds(2026, 1, 3), new List<object>());
            Expect("class absent", All(!sql.Contains("InventoryClass"), !sql.Contains(") ci ON"), !sql.Contains("cInvClass")));
            CheckInventoryRoll();
        }

        // 存货维度：带主单位和（往上取的）分类编码；往上取的级次参数排在最后。
        static void CheckInventoryRoll()
        {
            MgmtSalesArgs m = ReportsMgmtSalesReq.Parse(With(Body(1, 3, new object[] { "inventory" }), "class_grade", 3));
            List<object> ps = new List<object>();
            string sql = ReportsMgmtSalesSql.Query(null, m, 2026, ReportsMgmtSales.CalendarBounds(2026, 1, 3), ps);
            Expect("inventory roll", All(Count(sql) == ps.Count, ps.Count == 6, (int)ps[5] == 3, !sql.Contains(") ci ON"),
                sql.Contains("u_inventory"), sql.Contains("un.cComUnitName"), sql.Contains("COALESCE(ig.cInvCCode, iv.cInvCCode) AS c_inventory"),
                sql.EndsWith("g.iInvCGrade = ? AND LEFT(iv.cInvCCode, LEN(g.cInvCCode)) = g.cInvCCode ORDER BY LEN(g.cInvCCode) DESC) ig",
                    StringComparison.Ordinal)));
            m = ReportsMgmtSalesReq.Parse(Body(1, 3, new object[] { "inventory" }));
            sql = ReportsMgmtSalesSql.Query(null, m, 2026, ReportsMgmtSales.CalendarBounds(2026, 1, 3), new List<object>());
            Expect("inventory own class", All(sql.Contains("iv.cInvCCode AS c_inventory"), !sql.Contains("OUTER APPLY")));
        }

        // 只有一个期间时按期间分组不能出 CASE（只剩 ELSE 是语法错误），期间号直接用常量。
        static void CheckSinglePeriod()
        {
            MgmtSalesArgs m = ReportsMgmtSalesReq.Parse(Body(3, 3, new object[] { "period", "customer" }));
            List<object> ps = new List<object>();
            string sql = ReportsMgmtSalesSql.Query(null, m, 2026, ReportsMgmtSales.CalendarBounds(2026, 3, 3), ps);
            Expect("single period", All(!sql.Contains("CASE"), !sql.Contains("ELSE"), sql.Contains("SELECT 3 AS per,"),
                Count(sql) == ps.Count, ps.Count == 5, (string)ps[0] == "2026-03-01", (string)ps[1] == "2026-04-01"));
            m = ReportsMgmtSalesReq.Parse(Body(2, 3, new object[] { "period" }));
            ps = new List<object>();
            sql = ReportsMgmtSalesSql.Query(null, m, 2026, ReportsMgmtSales.CalendarBounds(2026, 2, 3), ps);
            Expect("two periods", All(sql.Contains("CASE WHEN h.dDate < CONVERT(date, ?, 23) THEN 2 ELSE 3 END"), Count(sql) == ps.Count));
        }

        // 关联方合计那一遍：不分组、只含这些客户、分类过滤照旧、数据权限仍挂在 q 上（ctx 为 null 的自检里不挂）。
        static void CheckIcPass()
        {
            Dictionary<string, object> body = With(With(Body(1, 3, new object[] { "customer", "inventory" }), "inv_class", "1001"),
                "ic_customers", new object[] { "C900001", "C900002" });
            MgmtSalesArgs m = ReportsMgmtSalesReq.Parse(body);
            MgmtSalesArgs only = m.ForCustomers(m.IcCustomers);
            Expect("ic args", All(only.GroupBy.Length == 0, only.IcCustomers.Length == 0, only.OnlyCustomers.Length == 2,
                only.InvClass == "1001", only.Top == m.Top, m.GroupBy.Length == 2, m.OnlyCustomers == null));
            List<object> ps = new List<object>();
            string sql = ReportsMgmtSalesSql.Query(null, only, 2026, ReportsMgmtSales.CalendarBounds(2026, 1, 3), ps);
            Expect("ic sql", All(Count(sql) == ps.Count, ps.Count == 11, (string)ps[0] == "2026-01-01", (string)ps[2] == "1001%",
                (string)ps[3] == "C900001", (string)ps[4] == "C900002", (int)ps[5] == 2026, (string)ps[8] == "1001%",
                (string)ps[9] == "C900001", (string)ps[10] == "C900002", sql.Contains("q.cCusCode IN (?, ?)"),
                !sql.Contains("GROUP BY"), sql.Contains("ON 1 = 1")));
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
