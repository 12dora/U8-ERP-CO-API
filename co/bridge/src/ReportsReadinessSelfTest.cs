using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的账套体检部分（reports/account_readiness）：登记（报表名单、读线程池、权限规则、登录子系统）、
    // 参数校验、各项的判断规则。只跑纯函数，不连库。
    internal static class ReportsReadinessSelfTest
    {
        public static void Run()
        {
            CheckWiring();
            CheckParse();
            CheckOverall();
            CheckRules();
        }

        static void CheckWiring()
        {
            string path = ReportsReadinessReq.Path;
            Expect("ready path", path == "/u8co/v1/reports/account_readiness");
            Expect("ready name", Array.IndexOf(Reports.Names, ReportsReadinessReq.Name) >= 0);
            Expect("ready perm", PermRegistry.ForKey(ReportsReadinessReq.RuleKey) != null && PermRegistry.IsRead(path));
            Expect("ready not write", !WriteGate.IsWrite(path));
            Expect("ready sub", Reports.SubOf(ReportsReadinessReq.Name, new Dictionary<string, object>()) == "SA");
            WorkItem item = new WorkItem();
            item.Config = new BridgeConfig();
            item.Path = path;
            item.Body = new Dictionary<string, object>();
            Expect("ready sql read", RouteClass.IsSqlRead(item));
            Expect("ready ids", ReportsReadinessReq.Ids.Length == 10 && Distinct(ReportsReadinessReq.Ids));
            for (int i = 0; i < ReportsReadinessReq.Ids.Length; i++)
            {
                string id = ReportsReadinessReq.Ids[i];
                Expect("ready title " + id, ReportsReadinessReq.Title(id) != id);
            }
        }

        static void CheckParse()
        {
            ReportArgs a = ReportsReq.Parse(ReportsReadinessReq.Name, new Dictionary<string, object>());
            Expect("ready as_of default", a.AsOf == "");
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["as_of"] = "2026-03-31";
            a = ReportsReq.Parse(ReportsReadinessReq.Name, body);
            Expect("ready as_of", a.AsOf == "2026-03-31");
            body["as_of"] = "2026/03/31";
            BadAsOf("ready as_of format", body);
            body["as_of"] = 20260331;
            BadAsOf("ready as_of number", body);
        }

        static void CheckOverall()
        {
            Expect("ready overall ok", Overall("ok", "ok") == "ok");
            Expect("ready overall warn", Overall("ok", "warn") == "warn");
            Expect("ready overall unknown", Overall("warn", "unknown", "ok") == "unknown");
            Expect("ready overall fail", Overall("unknown", "fail", "warn") == "fail");
            Expect("ready brief", ReportsReadiness.Brief("a\r\nb") == "a" && ReportsReadiness.Brief(null) == "");
        }

        static void CheckRules()
        {
            List<string[]> periods = new List<string[]>();
            periods.Add(new string[] { "2024", "2024-01-01", "2024-12-31" });
            periods.Add(new string[] { "2025", "2025-01-01", "2025-12-31" });
            Expect("ready covered", ReportsReadinessProbe.Covered(periods, "2025-12-31"));
            Expect("ready not covered", !ReportsReadinessProbe.Covered(periods, "2026-01-01")
                && !ReportsReadinessProbe.Covered(periods, ""));
            Expect("ready cal ok", ReportsReadinessProbe.CalendarStatus("2026-12-31", "2026-01-05", "2026-01-05") == "ok");
            Expect("ready cal warn", ReportsReadinessProbe.CalendarStatus("2026-01-20", "2026-01-05", "2026-01-05") == "warn");
            Expect("ready cal fail", ReportsReadinessProbe.CalendarStatus("2025-12-31", "2025-06-30", "2026-01-05") == "fail");
            Expect("ready cal empty", ReportsReadinessProbe.CalendarStatus("", "2026-01-05", "2026-01-05") == "fail");
            CheckYearly();
            CheckPost();
            Expect("ready defaults fail", ReportsReadinessProbeMore.DefaultsStatus(new int[] { 0, 1, 1, 1, 1 }) == "fail");
            Expect("ready defaults warn", ReportsReadinessProbeMore.DefaultsStatus(new int[] { 1, 0, 1, 1, 1 }) == "warn");
            Expect("ready defaults ok", ReportsReadinessProbeMore.DefaultsStatus(new int[] { 1, 1, 2, 3, 9 }) == "ok");
            List<string> none = new List<string>();
            List<string> ia = new List<string>(new string[] { "IA" });
            Expect("ready modules", ReportsReadinessProbeMore.ModulesStatus(none, none) == "ok"
                && ReportsReadinessProbeMore.ModulesStatus(none, ia) == "warn"
                && ReportsReadinessProbeMore.ModulesStatus(new List<string>(new string[] { "QM" }), none) == "fail");
        }

        // 记账闸门：{ 年度, 未结账期数, 第一个未结账期间 }。
        static void CheckPost()
        {
            List<int[]> mend = new List<int[]>();
            mend.Add(new int[] { 2025, 12, 1 });
            Expect("ready post early open", Verdict(mend, 2025, 10) == "fail");
            Expect("ready post early text", ReportsReadinessProbeMore.PostVerdict(mend, 2025, 10)[1].Contains("1–9"));
            Expect("ready post first", Verdict(mend, 2025, 1) == "ok");
            Expect("ready post last month open", Verdict(mend, 2025, 2) == "warn");
            Expect("ready post no year", Verdict(mend, 2026, 3) == "warn");
            mend.Add(new int[] { 2026, 12, 1 });
            Expect("ready post jan prior open", Verdict(mend, 2026, 1) == "fail");
            mend[0][1] = 0;
            mend[0][2] = 0;
            Expect("ready post jan prior closed", Verdict(mend, 2026, 1) == "ok");
            Expect("ready post closed month", Verdict(mend, 2025, 5) == "fail");
            mend.Insert(0, new int[] { 2023, 1, 12 });
            Expect("ready post older open", Verdict(mend, 2026, 1) == "warn");
            Expect("ready hint jan", ReportsReadinessProbeMore.PriorHint(2026, 1).Contains("\"fiscal_year\":2025,\"period\":12}"));
            Expect("ready hint oct", ReportsReadinessProbeMore.PriorHint(2026, 10).Contains("\"fiscal_year\":2026,\"period\":9}"));
            Expect("ready hint none", ReportsReadinessProbeMore.PriorHint(0, 0).Contains("本年"));
        }

        static string Verdict(List<int[]> mend, int year, int month)
        {
            return ReportsReadinessProbeMore.PostVerdict(mend, year, month)[0];
        }

        static void CheckYearly()
        {
            Dictionary<string, int> counts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            counts["code:2024"] = 120;
            counts["code:2025"] = 120;
            counts["Ap_InputCode:2024"] = 4;
            List<int> years = new List<int>(new int[] { 2024, 2025, 2026 });
            List<string> gaps = ReportsReadinessProbe.YearlyGaps(counts, years);
            Expect("ready yearly gaps", gaps.Count == 3 && gaps.Contains("code 2026") && gaps.Contains("Ap_InputCode 2025")
                && gaps.Contains("Ap_InputCode 2026"));
            Expect("ready yearly limit", ReportsReadinessProbe.YearlyLimit("2025-12-31", "2024-03-01") == 2025
                && ReportsReadinessProbe.YearlyLimit("", "") == 0);
            List<string> due = ReportsReadinessProbe.DueGaps(gaps, 2025);
            Expect("ready yearly due", due.Count == 1 && due.Contains("Ap_InputCode 2025"));
            Expect("ready yearly due all", ReportsReadinessProbe.DueGaps(gaps, 0).Count == 3);
            Expect("ready patch status", ReportsReadinessProbe.PatchStatus(0, 5, true) == "fail"
                && ReportsReadinessProbe.PatchStatus(5, 5, false) == "warn"
                && ReportsReadinessProbe.PatchStatus(5, 5, true) == "ok"
                && ReportsReadinessProbe.PatchStatus(3, 5, true) == "warn");
            Expect("ready yearly sql", ReportsReadinessSql.YearlySql.IndexOf("?", StringComparison.Ordinal) < 0
                && ReportsReadinessSql.YearlySql.Contains("KEYWORD=N'code'"));
        }

        static string Overall(params string[] statuses)
        {
            List<ReadyCheck> checks = new List<ReadyCheck>();
            for (int i = 0; i < statuses.Length; i++)
            {
                checks.Add(new ReadyCheck("x", statuses[i], "", ""));
            }
            return ReportsReadiness.Overall(checks);
        }

        static void BadAsOf(string name, Dictionary<string, object> body)
        {
            try
            {
                ReportsReq.Parse(ReportsReadinessReq.Name, body);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Field == "as_of");
                return;
            }
            throw new InvalidOperationException(name);
        }

        static bool Distinct(string[] ids)
        {
            for (int i = 0; i < ids.Length; i++)
            {
                if (Array.IndexOf(ids, ids[i]) != i)
                {
                    return false;
                }
            }
            return true;
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
