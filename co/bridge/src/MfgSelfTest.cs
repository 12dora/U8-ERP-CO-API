using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的生产类报表部分（不连库）：参数校验与缺省、边界值、SQL 的 ? 与参数一一对应且值不拼进 SQL、
    // 分组键与排序、订单状态映射，以及路由名、登录子系统、字段表。
    internal static class MfgSelfTest
    {
        public static void Run()
        {
            CheckRoutes();
            CheckDailyParse();
            CheckDailyRefused();
            CheckProgressParse();
            CheckConsumptionParse();
            CheckDailySql();
            CheckMoSql();
            CheckStatus();
        }

        static void CheckRoutes()
        {
            Expect("mfg names", All(ReportsMfgReq.Names.Length == 3, ReportsMfgReq.Owns("production_daily"),
                ReportsMfgReq.Owns("mo_progress"), ReportsMfgReq.Owns("mo_consumption"), !ReportsMfgReq.Owns("bom")));
            Expect("mfg sub", All(ReportsMfgReq.SubOf("production_daily", null) == "ST",
                ReportsMfgReq.SubOf("mo_progress", null) == "SA", ReportsMfgReq.SubOf("mo_consumption", null) == "SA",
                ReportsMfgReq.SubOf("bom", null) == null));
            Expect("mfg specs", All(ReportsMfgReq.Specs.Length == 3,
                ReportsMfgReq.Specs[0][0] == Requests.ReportRoot + "production_daily",
                Array.IndexOf(ReportsMfgReq.Specs[0], "class_grade") > 0, Array.IndexOf(ReportsMfgReq.Specs[1], "open_only") > 0,
                Array.IndexOf(ReportsMfgReq.Specs[2], "parent") > 0, Array.IndexOf(ReportsMfgReq.Specs[2], "after") < 0));
        }

        static void CheckDailyParse()
        {
            MfgArgs m = ReportsMfgReq.Parse("production_daily", Range());
            Expect("daily defaults", All(m.DateFrom == "2026-09-01", m.DateTo == "", m.GroupBy == "day", m.By == "product",
                m.ClassGrade == 2, m.Inv.Length == 0, m.InvClass == null, !m.Unverified, m.Limit == 1000));
            Dictionary<string, object> body = Range();
            body["date_to"] = "2026-09-30";
            body["group_by"] = "month";
            body["by"] = "class";
            body["class_grade"] = 9;
            body["inv"] = new object[] { " 9909001 ", "9909002" };
            body["inv_class"] = "0101";
            body["include_unverified"] = true;
            body["limit"] = 5000;
            m = ReportsMfgReq.Parse("production_daily", body);
            Expect("daily full", All(m.DateTo == "2026-09-30", m.GroupBy == "month", m.By == "class", m.ClassGrade == 9,
                m.Inv.Length == 2, m.Inv[0] == "9909001", m.InvClass == "0101", m.Unverified, m.Limit == 5000));
            Dictionary<string, object> list = Range();
            list["inv"] = new ArrayList(new object[] { "9909001" });
            Expect("daily arraylist", ReportsMfgReq.Parse("production_daily", list).Inv.Length == 1);
        }

        static void CheckDailyRefused()
        {
            Refused("daily no date_from", new Dictionary<string, object>(), "production_daily", "date_from");
            Refused("daily bad date", With("date_from", "2026-9-1"), "production_daily", "date_from");
            Refused("daily real date", With("date_from", "2026-02-30"), "production_daily", "date_from");
            Refused("daily from > to", With("date_to", "2026-08-31"), "production_daily", "date_from");
            Refused("daily group_by", With("group_by", "week"), "production_daily", "group_by");
            Refused("daily by", With("by", "customer"), "production_daily", "by");
            Refused("daily grade 0", With("class_grade", 0), "production_daily", "class_grade");
            Refused("daily grade 10", With("class_grade", 10), "production_daily", "class_grade");
            Refused("daily limit 0", With("limit", 0), "production_daily", "limit");
            Refused("daily limit 5001", With("limit", 5001), "production_daily", "limit");
            Refused("daily limit text", With("limit", "10"), "production_daily", "limit");
            Refused("daily unverified text", With("include_unverified", "true"), "production_daily", "include_unverified");
            Refused("daily inv empty", With("inv", new object[0]), "production_daily", "inv");
            Refused("daily inv text", With("inv", "9909001"), "production_daily", "inv");
            Refused("daily inv blank", With("inv", new object[] { "  " }), "production_daily", "inv.0");
            Refused("daily inv 21", With("inv", new object[21]), "production_daily", "inv");
            Refused("daily inv control", With("inv", new object[] { "a\tb" }), "production_daily", "inv.0");
            Refused("daily inv_class wildcard", With("inv_class", "01%"), "production_daily", "inv_class");
            Refused("unknown route", Range(), "bom", null);
        }

        static void CheckProgressParse()
        {
            MfgArgs m = ReportsMfgReq.Parse("mo_progress", Range());
            Expect("progress defaults", All(m.Limit == 500, !m.OpenOnly, m.Inv.Length == 0, m.InvClass == null));
            Dictionary<string, object> body = Range();
            body["open_only"] = true;
            body["limit"] = 2000;
            body["inv"] = new object[] { "9909001" };
            m = ReportsMfgReq.Parse("mo_progress", body);
            Expect("progress full", All(m.OpenOnly, m.Limit == 2000, m.Inv.Length == 1));
            Refused("progress limit", With("limit", 2001), "mo_progress", "limit");
            Refused("progress open_only", With("open_only", 1), "mo_progress", "open_only");
        }

        static void CheckConsumptionParse()
        {
            MfgArgs m = ReportsMfgReq.Parse("mo_consumption", Range());
            Expect("usage defaults", All(m.Limit == 500, m.Parent.Length == 0, m.Inv.Length == 0));
            Dictionary<string, object> body = Range();
            body["parent"] = new object[] { "0301001", "0301002" };
            body["inv_class"] = "03";
            m = ReportsMfgReq.Parse("mo_consumption", body);
            Expect("usage full", All(m.Parent.Length == 2, m.InvClass == "03"));
            Refused("usage parent", With("parent", new object[0]), "mo_consumption", "parent");
            Refused("usage limit", With("limit", 0), "mo_consumption", "limit");
        }

        static void CheckDailySql()
        {
            MfgArgs m = ReportsMfgReq.Parse("production_daily", Range());
            List<object> ps = new List<object>();
            string sql = ReportsMfgDailySql.Build(m, "2026-09-30", ps, null);
            Expect("daily sql product", All(Count(sql) == ps.Count, ps.Count == 3, (int)ps[0] == 1001, (string)ps[1] == "2026-09-01",
                sql.Contains("CONVERT(varchar(10), h.dDate, 23) AS d"), sql.Contains("u.cComUnitName AS unit"),
                sql.Contains("cHandler"), !sql.Contains("cg.cInvCCode"), !sql.Contains("2026-09")));
            Dictionary<string, object> body = Range();
            body["group_by"] = "month";
            body["by"] = "class";
            body["class_grade"] = 3;
            body["inv"] = new object[] { "9909001", "9909002" };
            body["inv_class"] = "01";
            body["include_unverified"] = true;
            m = ReportsMfgReq.Parse("production_daily", body);
            ps = new List<object>();
            sql = ReportsMfgDailySql.Build(m, "2026-09-30", ps, null);
            Expect("daily sql class", All(Count(sql) == ps.Count, ps.Count == 7, (int)ps[1] == 3, (string)ps[2] == "2026-09-01",
                (string)ps[4] == "9909001", sql.Contains("CONVERT(varchar(7), h.dDate, 23) AS d"),
                sql.Contains("InventoryClass cc"), !sql.Contains("cHandler"), !sql.Contains("9909001")));
            List<object> dayPs = new List<object>();
            string days = ReportsMfgDailySql.Days(m, "2026-09-30", dayPs, null);
            Expect("daily sql days", All(Count(days) == dayPs.Count, dayPs.Count == 5, days.Contains("COUNT(DISTINCT CONVERT(date, h.dDate))"),
                !days.Contains("InventoryClass")));
        }

        static void CheckMoSql()
        {
            Dictionary<string, object> body = Range();
            body["open_only"] = true;
            body["inv"] = new object[] { "9909001" };
            body["inv_class"] = "01";
            MfgArgs m = ReportsMfgReq.Parse("mo_progress", body);
            List<object> ps = new List<object>();
            string sql = ReportsMfgMoSql.Progress(m, "2026-09-30", ps, null);
            Expect("progress sql", All(Count(sql) == ps.Count, ps.Count == 5, (int)ps[0] == 501, (string)ps[3] == "9909001",
                sql.Contains("<> 4"), sql.Contains("ORDER BY o.CreateDate, o.MoCode, d.SortSeq"), !sql.Contains("9909001")));
            body = Range();
            body["parent"] = new object[] { "0301001" };
            m = ReportsMfgReq.Parse("mo_consumption", body);
            ps = new List<object>();
            sql = ReportsMfgMoSql.Usage(m, "2026-09-30", ps, null);
            Expect("usage sql", All(Count(sql) == ps.Count, ps.Count == 4, (int)ps[3] == 501, (string)ps[2] == "0301001",
                sql.Contains("d.Status IN (3, 4)"), sql.Contains("ByproductFlag"), !sql.Contains("{WHERE}"),
                sql.IndexOf("TOP (?)", StringComparison.Ordinal) > sql.IndexOf("CONVERT(date, ?, 23)", StringComparison.Ordinal)));
        }

        static void CheckStatus()
        {
            Expect("status map", All(ReportsMfgMo.StatusText(4) == "closed", ReportsMfgMo.StatusText(3) == "open",
                ReportsMfgMo.StatusText(2) == "planned", ReportsMfgMo.StatusText(1) == "planned", ReportsMfgMo.StatusText(0) == "planned"));
            Expect("marks", All(ReportsMfgDailySql.Marks(1) == "?", ReportsMfgDailySql.Marks(3) == "?, ?, ?"));
        }

        static Dictionary<string, object> Range()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["date_from"] = "2026-09-01";
            return body;
        }

        static Dictionary<string, object> With(string key, object value)
        {
            Dictionary<string, object> body = Range();
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

        static void Refused(string name, Dictionary<string, object> body, string route, string field)
        {
            try
            {
                ReportsMfgReq.Check(route, body);
            }
            catch (BridgeException ex)
            {
                Expect(name, All(ex.Status == 400, field == null || ex.Field == field));
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
                throw new InvalidOperationException("mfg selftest: " + name);
            }
        }
    }
}
