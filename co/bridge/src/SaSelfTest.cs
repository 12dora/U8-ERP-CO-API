using System;
using System.Collections;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // --selftest 的销售类报表部分（reports/open_orders、sales_price_history、customer_churn）：请求校验与缺省、
    // 单价与舍入、IN / 日期条件的拼法、SQL 的 ? 个数、客户变动的窗口月数与分类排序、路由与登录子系统。只跑纯函数，不连库。
    internal static class SaSelfTest
    {
        public static void Run()
        {
            CheckOpenParse();
            CheckOpenRefused();
            CheckPriceParse();
            CheckChurnParse();
            CheckCalc();
            CheckBuilders();
            CheckMonths();
            CheckClassify();
            CheckSql();
            CheckRoutes();
        }

        static void CheckOpenParse()
        {
            OpenOrdersArgs a = SaArgs.Open(new Dictionary<string, object>());
            Expect("open defaults", a.Basis == "ship", a.GroupBy == "customer", a.Limit == 200, a.Customers.Length == 0,
                a.DateFrom == "", a.DueBefore == "");
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["basis"] = "invoice";
            body["group_by"] = "order";
            body["customer"] = new ArrayList(new object[] { " C900001 ", "C900001", "C900002" });
            body["inv"] = new object[] { "9909001" };
            body["person"] = new ArrayList(new object[] { "P001" });
            body["date_from"] = "2026-01-01";
            body["date_to"] = "2026-09-30";
            body["due_before"] = "2026-12-31";
            body["limit"] = 1000;
            a = SaArgs.Open(body);
            Expect("open full", a.Basis == "invoice", a.GroupBy == "order", a.Customers.Length == 2, a.Customers[0] == "C900001",
                a.Invs[0] == "9909001", a.Persons[0] == "P001", a.DateTo == "2026-09-30", a.DueBefore == "2026-12-31",
                a.Limit == 1000);
        }

        static void CheckOpenRefused()
        {
            RefusedOpen("open basis", "basis", "billed", "basis");
            RefusedOpen("open group", "group_by", "person", "group_by");
            RefusedOpen("open basis type", "basis", 1, "basis");
            RefusedOpen("open customer empty", "customer", new ArrayList(), "customer");
            RefusedOpen("open customer text", "customer", "C900001", "customer");
            RefusedOpen("open customer 21", "customer", Many(21), "customer");
            RefusedOpen("open customer blank", "customer", new ArrayList(new object[] { "C900001", "  " }), "customer.1");
            RefusedOpen("open customer control", "customer", new ArrayList(new object[] { "a\tb" }), "customer.0");
            RefusedOpen("open limit 0", "limit", 0, "limit");
            RefusedOpen("open limit 1001", "limit", 1001, "limit");
            RefusedOpen("open date", "date_from", "2026-02-30", "date_from");
            RefusedOpen("open due", "due_before", "2026/12/31", "due_before");
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["date_from"] = "2026-05-02";
            body["date_to"] = "2026-05-01";
            Refused("open range", delegate { SaArgs.Open(body); }, "date_from");
        }

        static void CheckPriceParse()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["date_from"] = "2026-09-01";
            Refused("price needs customer or inv", delegate { SaArgs.Price(body); }, "customer");
            body["inv"] = new ArrayList(new object[] { "9909001" });
            PriceHistoryArgs a = SaArgs.Price(body);
            Expect("price defaults", a.Limit == 500, !a.Unverified, a.DateTo == "", a.Customers.Length == 0, a.Invs.Length == 1);
            body["limit"] = 2000;
            body["include_unverified"] = true;
            body["customer"] = new ArrayList(new object[] { "C900001" });
            a = SaArgs.Price(body);
            Expect("price full", a.Limit == 2000, a.Unverified, a.Customers[0] == "C900001");
            body["limit"] = 2001;
            Refused("price limit", delegate { SaArgs.Price(body); }, "limit");
            body["limit"] = 10;
            body["include_unverified"] = "yes";
            Refused("price flag", delegate { SaArgs.Price(body); }, "include_unverified");
            Dictionary<string, object> noFrom = new Dictionary<string, object>();
            noFrom["inv"] = new ArrayList(new object[] { "9909001" });
            Refused("price from missing", delegate { SaArgs.Price(noFrom); }, "date_from");
        }

        static void CheckChurnParse()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["date_from"] = "2026-07-01";
            ChurnArgs a = SaArgs.Churn(body);
            Expect("churn defaults", a.Lookback == 6, a.DeclinePct == 30, a.Limit == 50, a.DateTo == "");
            body["lookback_months"] = 24;
            body["decline_pct"] = 99;
            body["limit"] = 200;
            a = SaArgs.Churn(body);
            Expect("churn full", a.Lookback == 24, a.DeclinePct == 99, a.Limit == 200);
            ChurnRefused("churn lookback 0", "lookback_months", 0);
            ChurnRefused("churn lookback 25", "lookback_months", 25);
            ChurnRefused("churn pct 0", "decline_pct", 0);
            ChurnRefused("churn pct 100", "decline_pct", 100);
            ChurnRefused("churn limit 201", "limit", 201);
            ChurnRefused("churn limit text", "limit", "50");
            Refused("churn from missing", delegate { SaArgs.Churn(new Dictionary<string, object>()); }, "date_from");
        }

        static void CheckCalc()
        {
            Expect("ratio zero qty", SaCalc.Ratio(10m, 0m) == null);
            Expect("ratio value", (decimal)SaCalc.Ratio(100m, 3m) == 33.333333m, (decimal)SaCalc.Ratio(-100m, -3m) == 33.333333m);
            Expect("ratio red", (decimal)SaCalc.Ratio(-9m, 3m) == -3m);
            Expect("money round", SaCalc.Money(1.005m) == 1.01m, SaCalc.Money(-1.005m) == -1.01m);
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["a"] = ".0000";
            row["b"] = "12345.1234567890";
            Expect("dec parse", SaCalc.Dec(row, "a") == 0m, SaCalc.Dec(row, "b") == 12345.123456789m, SaCalc.Dec(row, "none") == 0m);
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            for (int i = 0; i < 4; i++)
            {
                rows.Add(new Dictionary<string, object>());
            }
            Expect("cut over", SaCalc.Cut(rows, 3), rows.Count == 3);
            Expect("cut exact", !SaCalc.Cut(rows, 3), rows.Count == 3);
        }

        static void CheckBuilders()
        {
            StringBuilder sb = new StringBuilder();
            List<object> ps = new List<object>();
            SaCalc.In(sb, ps, "h.cCusCode", new string[] { "A", "B" });
            SaCalc.In(sb, ps, "d.cInvCode", new string[0]);
            SaCalc.Between(sb, ps, "h.dDate", "2026-01-01", "");
            SaCalc.Between(sb, ps, "h.dDate", "", "2026-02-01");
            Expect("in sql", sb.ToString() == " AND h.cCusCode IN (?, ?) AND h.dDate >= CONVERT(date, ?, 23)"
                + " AND h.dDate < DATEADD(day, 1, CONVERT(date, ?, 23))");
            Expect("in params", ps.Count == 4, (string)ps[0] == "A", (string)ps[1] == "B", (string)ps[2] == "2026-01-01",
                (string)ps[3] == "2026-02-01");
            Dictionary<string, object> page = SaCalc.Body(new List<object>(new object[] { 1, 2 }), true);
            Expect("page body", (int)page["count"] == 2, (bool)page["truncated"], (bool)page["ok"]);
        }

        static void CheckMonths()
        {
            Expect("months quarter", SaChurnCalc.Months(Day("2026-07-01"), Day("2026-09-30")) == 3m);
            Expect("months half", SaChurnCalc.Months(Day("2026-01-01"), Day("2026-06-30")) == 6m);
            Expect("months shifted", SaChurnCalc.Months(Day("2026-08-15"), Day("2026-10-14")) == 2m);
            Expect("months partial", SaChurnCalc.Months(Day("2026-07-01"), Day("2026-07-15")) == decimal.Round(15m / 30.4375m, 6),
                SaChurnCalc.Months(Day("2026-07-10"), Day("2026-07-10")) > 0m);
            ChurnWindows w = SaChurnCalc.Windows("2026-07-01", "2026-09-30", 6);
            Expect("windows", SaChurnCalc.Text(w.BaseFrom) == "2026-01-01", SaChurnCalc.Text(w.BaseTo) == "2026-06-30",
                SaChurnCalc.Text(w.CurTo) == "2026-09-30", w.CurMonths == 3m, w.BaseMonths == 6m);
            Dictionary<string, object> text = SaChurnCalc.WindowsBody(w);
            Expect("windows body", (string)text["current_from"] == "2026-07-01", (string)text["baseline_to"] == "2026-06-30");
        }

        // 窗口 2026-07-01 到 09-30（3 个月），基线 2026-01-01 到 06-30（6 个月），下降阈值 30%。
        static void CheckClassify()
        {
            List<ChurnRow> rows = new List<ChurnRow>();
            rows.Add(Churn("C900001", "2026-07-15", 500m, 0m, 2, 0));
            rows.Add(Churn("C900002", "2026-08-01", 900m, 0m, 1, 0));
            rows.Add(Churn("C900003", "2025-01-01", 0m, 600m, 0, 3));
            rows.Add(Churn("C900004", "2025-01-01", 0m, 1200m, 0, 2));
            rows.Add(Churn("C900005", "2025-01-01", 100m, 600m, 1, 3));
            rows.Add(Churn("C900006", "2025-01-01", 1000m, 600m, 2, 3));
            rows.Add(Churn("C900007", "2025-01-01", 140m, 200m, 1, 1));
            rows.Add(Churn("C900008", "2025-01-01", 0m, -50m, 0, 1));
            rows.Add(Churn("C900009", "2025-01-01", 100m, 1200m, 1, 2));
            rows.Add(Churn("C900010", "2026-08-01", 0m, 0m, 2, 0));
            ChurnLists lists = SaChurnCalc.Classify(rows, SaChurnCalc.Windows("2026-07-01", "2026-09-30", 6), "2026-07-01", 30);
            Expect("churn new", lists.New.Count == 2, lists.New[0].Code == "C900002", lists.New[1].Code == "C900001");
            Expect("churn lost", lists.Lost.Count == 2, lists.Lost[0].Code == "C900004", lists.Lost[1].Code == "C900003");
            Expect("churn declining", lists.Declining.Count == 2, lists.Declining[0].Code == "C900009",
                lists.Declining[1].Code == "C900005");
            Expect("churn active", lists.Active == 7);
            // 字段权限会遮收入：三张清单只按客户编码排。
            ChurnLists byCode = SaChurnCalc.Classify(rows, SaChurnCalc.Windows("2026-07-01", "2026-09-30", 6), "2026-07-01", 30, true);
            Expect("churn by code", byCode.New[0].Code == "C900001", byCode.New[1].Code == "C900002", byCode.Lost[0].Code == "C900003",
                byCode.Lost[1].Code == "C900004", byCode.Declining[0].Code == "C900005", byCode.Declining[1].Code == "C900009");
            Dictionary<string, object> counts = SaChurnCalc.Counts(lists);
            Expect("churn counts", (int)counts["new"] == 2, (int)counts["lost"] == 2, (int)counts["declining"] == 2,
                (int)counts["active"] == 7);
            List<object> top = SaChurnCalc.Items(lists.Lost, 1);
            Dictionary<string, object> first = (Dictionary<string, object>)top[0];
            Expect("churn item", top.Count == 1, (string)first["customer_code"] == "C900004", (decimal)first["revenue_baseline"] == 1200m,
                (int)first["months_baseline"] == 2);
        }

        static void CheckSql()
        {
            OpenOrdersArgs a = new OpenOrdersArgs();
            Expect("open sql marks", Count(ReportsSaOpenSql.Items(a, ""), '?') == 2, Count(ReportsSaOpenSql.Sum(a, ""), '?') == 0);
            Expect("open sql group customer", ReportsSaOpenSql.Items(a, "").Contains("GROUP BY h.cCusCode, c.cCusName"));
            a.GroupBy = "inventory";
            a.Basis = "out";
            string byInv = ReportsSaOpenSql.Items(a, " AND h.cCusCode IN (?)");
            Expect("open sql inventory", byInv.Contains("GROUP BY d.cInvCode") && byInv.Contains("d.foutquantity"), Count(byInv, '?') == 3);
            a.GroupBy = "order";
            a.Basis = "invoice";
            a.NoAmountOrder = true;
            Expect("open sql masked inventory", ReportsSaOpenSql.Items(new OpenOrdersArgs { GroupBy = "inventory", NoAmountOrder = true }, "")
                .EndsWith("ORDER BY d.cInvCode", StringComparison.Ordinal));
            Expect("open sql masked customer", ReportsSaOpenSql.Items(new OpenOrdersArgs { NoAmountOrder = true }, "")
                .EndsWith("ORDER BY h.cCusCode", StringComparison.Ordinal));
            Expect("open sql amount customer", ReportsSaOpenSql.Items(new OpenOrdersArgs(), "")
                .EndsWith("ORDER BY SUM(y.amt) DESC, h.cCusCode", StringComparison.Ordinal));
            string byOrder = ReportsSaOpenSql.Items(a, "");
            Expect("open sql order", byOrder.Contains("d.iSOsID") && byOrder.Contains("d.iKPQuantity"), !byOrder.Contains("{"));
            Expect("open sql sum", !ReportsSaOpenSql.Sum(a, "").Contains("{"), ReportsSaOpenSql.Sum(a, "").Contains("COUNT(DISTINCT h.cCusCode)"));
            Expect("churn sql marks", Count(ReportsSaChurn.Main, '?') == 5, ReportsSaChurn.Main.Contains("{PERM}"));
        }

        static void CheckRoutes()
        {
            Expect("sa names", ReportsSaReq.Names.Length == 3, ReportsSaReq.Owns("open_orders"), ReportsSaReq.Owns("customer_churn"),
                !ReportsSaReq.Owns("customer_credit"));
            Expect("sa sub", ReportsSaReq.SubOf("sales_price_history", null) == "SA", ReportsSaReq.SubOf("stock_summary", null) == null);
            Expect("sa specs", ReportsSaReq.Specs.Length == 3, ReportsSaReq.Specs[0][0].EndsWith("/open_orders", StringComparison.Ordinal),
                Array.IndexOf(ReportsSaReq.Specs[1], "include_unverified") > 0, Array.IndexOf(ReportsSaReq.Specs[2], "decline_pct") > 0);
            Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>> map =
                new Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>>();
            ReportsSaReq.AddRunners(map);
            Expect("sa runners", map.Count == 3, map.ContainsKey("open_orders"), map.ContainsKey("sales_price_history"),
                map.ContainsKey("customer_churn"));
            Dictionary<string, object> bad = new Dictionary<string, object>();
            bad["basis"] = "x";
            Refused("sa check dispatch", delegate { ReportsSaReq.Check("open_orders", bad); }, "basis");
            Refused("sa check price", delegate { ReportsSaReq.Check("sales_price_history", new Dictionary<string, object>()); }, "customer");
            Refused("sa check churn", delegate { ReportsSaReq.Check("customer_churn", new Dictionary<string, object>()); }, "date_from");
            Expect("sa check other", Passes(delegate { ReportsSaReq.Check("stock_summary", bad); }));
        }

        static ChurnRow Churn(string code, string first, decimal cur, decimal baseline, int curLines, int baseLines)
        {
            ChurnRow r = new ChurnRow();
            r.Code = code;
            r.First = first;
            r.Last = first;
            r.RevCur = cur;
            r.RevBase = baseline;
            r.LinesCur = curLines;
            r.LinesBase = baseLines;
            r.MonthsCur = curLines > 0 ? 1 : 0;
            r.MonthsBase = baseLines > 1 ? 2 : baseLines;
            return r;
        }

        static DateTime Day(string text)
        {
            return SaChurnCalc.Day(text);
        }

        static ArrayList Many(int count)
        {
            ArrayList list = new ArrayList();
            for (int i = 0; i < count; i++)
            {
                list.Add("C" + i.ToString(System.Globalization.CultureInfo.InvariantCulture));
            }
            return list;
        }

        static int Count(string text, char c)
        {
            int n = 0;
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] == c)
                {
                    n++;
                }
            }
            return n;
        }

        static void RefusedOpen(string name, string key, object value, string field)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body[key] = value;
            Refused(name, delegate { SaArgs.Open(body); }, field);
        }

        static void ChurnRefused(string name, string key, object value)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["date_from"] = "2026-07-01";
            body[key] = value;
            Refused(name, delegate { SaArgs.Churn(body); }, key);
        }

        static bool Passes(Action run)
        {
            try
            {
                run();
                return true;
            }
            catch (BridgeException)
            {
                return false;
            }
        }

        static void Refused(string name, Action run, string field)
        {
            try
            {
                run();
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400, ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void Expect(string name, params bool[] checks)
        {
            if (!Array.TrueForAll(checks, delegate(bool ok) { return ok; }))
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
