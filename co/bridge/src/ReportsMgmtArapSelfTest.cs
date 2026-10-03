using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的经营管理往来账期部分（不连库）：参数（沿用 arap_aging basis=due 的校验，buckets 缺省四段）、
    // 三句 SQL 的 ? 与参数一一对应、加权平均与中位数、单位排序与 others / totals，以及路由、登录子系统、权限登记。
    internal static class ReportsMgmtArapSelfTest
    {
        const string AsOf = "2026-08-31";

        public static void Run()
        {
            CheckParse();
            CheckSql("ar");
            CheckSql("ap");
            CheckDays();
            CheckFill();
            Expect("arap registered", All(Array.IndexOf(Reports.Names, ReportsMgmtArapReq.Name) >= 0,
                Reports.SubOf(ReportsMgmtArapReq.Name, Body("ap")) == "AP", Reports.SubOf(ReportsMgmtArapReq.Name,
                    Body("ar")) == "AR", PermRegistry.ForKey(ReportsMgmtArapReq.RuleKey(Body("ap"))) != null,
                        ReportsMgmtArapReq.RuleKey(Body("ar")) == "report:mgmt/arap_terms:ar"));
            Expect("arap history from", All(ReportsMgmtArap.HistoryFrom(AsOf) == "2025-09-01",
                ReportsMgmtArap.HistoryFrom("2024-02-28") == "2023-02-28", ReportsMgmtArap.HistoryFrom("") == null));
        }

        static void CheckParse()
        {
            MgmtArapArgs m = ReportsMgmtArapReq.Parse(Body("ar"));
            ReportArgs g = m.Aging;
            Expect("arap defaults", All(m.Top == 200, g.Basis == "due", g.Buckets.Length == 4, g.Buckets[3] == 180,
                g.Accounts.Length == 1, g.Accounts[0] == "1122", g.AsOf == AsOf, g.DefaultCreditDays == 0));
            Dictionary<string, object> body = Body("ap");
            body["buckets"] = new object[] { 15, 45 };
            body["default_credit_days"] = 30;
            body["accounts"] = new object[] { "2202", "2241" };
            body["top"] = 500;
            m = ReportsMgmtArapReq.Parse(body);
            Expect("arap custom", All(m.Top == 500, m.Aging.Buckets.Length == 2, m.Aging.DefaultCreditDays == 30,
                m.Aging.Accounts.Length == 2, m.Aging.Side == "ap"));
            Refused("arap side", Set(Body("ar"), "side", "both"), "side");
            Refused("arap buckets", Set(Body("ar"), "buckets", new object[] { 60, 30 }), "buckets.1");
            Refused("arap credit days", Set(Body("ar"), "default_credit_days", -1), "default_credit_days");
            Refused("arap top", Set(Body("ar"), "top", 501), "top");
            Refused("arap as_of", Set(Body("ar"), "as_of", "2026/08/31"), "as_of");
            ReportArgs a = new ReportArgs();
            a.Name = ReportsMgmtArapReq.Name;
            Dictionary<string, object> missing = Body("ar");
            missing.Remove("as_of");
            Expect("arap check as_of", All(ReportsMgmtArapReq.Check(a, missing), a.AsOf == ""));
            a.Name = "gl_balance";
            Expect("arap check other", !ReportsMgmtArapReq.Check(a, missing));
        }

        // 账龄：前 n 个参数是分段天数，接着 Inner 的参数（含 as_of）；历史：三个 as_of、科目前缀、核销截止 as_of；票据：标志、as_of。
        static void CheckSql(string side)
        {
            ReportArgs g = ReportsMgmtArapReq.Parse(Body(side)).Aging;
            List<object> ps = new List<object>();
            string sql = ReportsMgmtArapSql.Aging(null, g, ps);
            Expect("arap aging placeholders " + side, All(Count(sql) == ps.Count, (int)ps[0] == 30, (int)ps[3] == 180,
                ps.Contains(AsOf), sql.Contains("GROUP BY x.cDwCode")));
            ps = new List<object>();
            sql = ReportsMgmtArapSql.History(null, g, ps);
            string[] expect = side == "ap" ? new string[] { "2202%", "Ap_Detail" } : new string[] { "1122%", "Ar_Detail" };
            Expect("arap history placeholders " + side, All(Count(sql) == ps.Count, ps.Count == 5,
                ps.GetRange(0, 3).TrueForAll(IsAsOf), (string)ps[3] == expect[0], IsAsOf(ps[4]), sql.Contains("N'9P'"),
                    sql.Contains(expect[1]), !sql.Contains("{")));
            // 单据数按张计（先按类型 + 单号合计），第一次核销不比往来单位。
            Expect("arap history docs " + side, All(sql.Contains("GROUP BY d.cDwCode, d.cVouchType, d.cVouchID) x"),
                sql.Contains("ON w.cCoVouchType = x.cVouchType AND w.cCoVouchID = x.cVouchID"), !sql.Contains("w.cDwCode")));
            ps = new List<object>();
            sql = ReportsMgmtArapSql.Notes(null, g, ps);
            Expect("arap notes placeholders " + side, All(Count(sql) == ps.Count, ps.Count == 2,
                (string)ps[0] == side.ToUpperInvariant(), (string)ps[1] == AsOf, sql.Contains("iRAmount"),
                sql.Contains("NULLIF(LTRIM(RTRIM(n.cEndorser)), N'')"), sql.Contains("GROUP BY x.cDwCode")));
        }

        static void CheckDays()
        {
            SortedDictionary<int, decimal> days = new SortedDictionary<int, decimal>();
            Expect("arap days empty", All(ReportsMgmtArapView.Average(days) == null, ReportsMgmtArapView.Median(days) == null));
            days[10] = 100m;
            days[40] = 300m;
            Expect("arap days", All((decimal)ReportsMgmtArapView.Average(days) == 32.5m,
                (int)ReportsMgmtArapView.Median(days) == 40));
            days[5] = 400m;
            // 5 天占权重正好一半：取到一半即止，中位数为 5。
            Expect("arap median half", (int)ReportsMgmtArapView.Median(days) == 5);
        }

        static void CheckFill()
        {
            List<ReportsMgmtArap.Partner> all = new List<ReportsMgmtArap.Partner>();
            all.Add(Partner("C1", 100m, 0));
            all.Add(Partner("C2", -300m, 0));
            all.Add(Partner("C3", 0m, 0));
            all.Add(Partner("C4", 0m, 2));
            all.Add(Partner("C5", 50m, 1));
            Dictionary<string, object> body = new Dictionary<string, object>();
            ReportsMgmtArap.Fill(body, all, 2, 2);
            List<object> rows = (List<object>)body["partners"];
            Dictionary<string, object> others = (Dictionary<string, object>)body["others"];
            Dictionary<string, object> totals = (Dictionary<string, object>)body["totals"];
            Expect("arap order", All(rows.Count == 2, (string)((Dictionary<string, object>)rows[0])["code"] == "C2",
                (string)((Dictionary<string, object>)rows[1])["code"] == "C1"));
            Expect("arap others", All((int)others["partners"] == 2, (decimal)others["balance"] == 50m,
                (int)others["invoice_count"] == 3));
            Expect("arap totals", All((int)totals["partners"] == 4, (decimal)totals["balance"] == -150m,
                ((List<object>)totals["aging"]).Count == 4, ((List<object>)totals["terms"]).Count == 1));
        }

        static ReportsMgmtArap.Partner Partner(string code, decimal balance, int invoices)
        {
            ReportsMgmtArap.Partner p = new ReportsMgmtArap.Partner(2);
            p.Code = code;
            p.Balance = balance;
            p.Aging[0] = balance;
            if (invoices > 0)
            {
                Dictionary<string, object> row = new Dictionary<string, object>();
                row["cp"] = "45";
                row["days"] = "30";
                row["cnt"] = invoices.ToString(System.Globalization.CultureInfo.InvariantCulture);
                row["amt"] = "10.00";
                ReportsMgmtArap.AddHistory(p, row);
            }
            return p;
        }

        static Dictionary<string, object> Body(string side)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["side"] = side;
            body["as_of"] = AsOf;
            return body;
        }

        static Dictionary<string, object> Set(Dictionary<string, object> body, string key, object value)
        {
            body[key] = value;
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
                ReportsMgmtArapReq.Parse(body);
            }
            catch (BridgeException ex)
            {
                Expect(name, All(ex.Status == 400, ex.Field == field));
                return;
            }
            throw new InvalidOperationException(name);
        }

        static bool IsAsOf(object value)
        {
            return (value as string) == AsOf;
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
