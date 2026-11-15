using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 一项检查的结果。Status 取 ReportsReadiness.Ok / Warn / Fail / Unknown。
    internal sealed class ReadyCheck
    {
        public string Id;
        public string Status;
        public string Detail;
        public string FixHint;

        public ReadyCheck(string id, string status, string detail, string fixHint)
        {
            Id = id;
            Status = status;
            Detail = detail;
            FixHint = fixHint;
        }
    }

    // 一次体检的输入和跨检查项共用的查询结果（同一请求里只查一次）。
    internal sealed class ReadyScope
    {
        // 只给复用 ReportsOpening 的项用（它按 WorkContext 取连接）；其余项只用 Conn。
        public WorkContext Ctx;
        public object Conn;
        public string Acc;
        public string Operator;
        public string AsOf;
        public string Today;
        // UFSYSTEM..UA_Period：{ 年度, 起, 止 }；读不到为 null。
        public List<string[]> Periods;
        // GL_mend：{ 年度, 1–12 期未结账期数, 第一个未结账期间（没有为 0） }；读过才有。
        List<int[]> _glMend;
        // 本账套库的目录检查（CatalogSql），patches、calendar、vendor_extradefine 共用。
        Dictionary<string, object> _catalog;

        public Dictionary<string, object> Catalog()
        {
            if (_catalog == null)
            {
                _catalog = Rows.One(Conn, ReportsReadinessSql.CatalogSql, new object[0])
                    ?? new Dictionary<string, object>();
            }
            return _catalog;
        }

        public List<int[]> GlMend()
        {
            if (_glMend != null)
            {
                return _glMend;
            }
            List<Dictionary<string, object>> rows = Rows.Query(Conn, ReportsReadinessSql.GlMendSql, new object[0], 200);
            List<int[]> list = new List<int[]>(rows.Count);
            for (int i = 0; i < rows.Count; i++)
            {
                list.Add(new int[] { GlSql.Int(rows[i], "y"), GlSql.Int(rows[i], "open_n"), GlSql.Int(rows[i], "first_open") });
            }
            _glMend = list;
            return list;
        }
    }

    // 账套体检 reports/account_readiness（只读，全是 SQL）：十项检查逐项给 ok / warn / fail / unknown，
    // overall 取最差的一项（fail > unknown > warn > ok）。登录和权限通过就回 200，问题都在 checks 里。
    // 每项单独接住异常：读 UFSYSTEM 的项读不到时 unknown 并提示给 SQL 登录授权，其余项查询失败也是 unknown。
    // 不写任何数据，正式账套上也可以跑（safe 恒为 true）。检查项见 ReportsReadinessProbe*.cs，设计见 docs/api-reference.md §30。
    internal static class ReportsReadiness
    {
        internal const string Ok = "ok";
        internal const string Warn = "warn";
        internal const string Fail = "fail";
        internal const string Unknown = "unknown";
        internal const string SysHint = "桥的 SQL 登录需要 UFSYSTEM 库的 SELECT 权限（UA_Period、UA_Account_sub、UA_PatchList），"
            + "授权后重跑体检";
        static readonly string[] Rank = new string[] { Ok, Warn, Unknown, Fail };

        public static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            ReadyScope s = new ReadyScope();
            s.Ctx = ctx;
            s.Conn = ctx.Conn;
            s.Acc = (ctx.Item.Acc ?? "").Trim();
            s.Operator = (ctx.Item.Operator ?? "").Trim();
            s.AsOf = a.AsOf;
            s.Today = (Rows.Scalar(ctx.Conn, ReportsReadinessSql.TodaySql, new object[0]) ?? "").Trim();
            List<ReadyCheck> checks = ProbeAll(s);
            List<object> items = new List<object>(checks.Count);
            for (int i = 0; i < checks.Count; i++)
            {
                items.Add(ToMap(checks[i]));
            }
            Dictionary<string, object> body = Reports.Body();
            body["overall"] = Overall(checks);
            body["as_of"] = s.AsOf;
            body["server_today"] = s.Today.Length == 0 ? null : s.Today;
            body["acc"] = s.Acc;
            body["checks"] = items;
            return ApiResult.Ok(body);
        }

        // 顺序同 ReportsReadinessReq.Ids。years 先跑：它读的年度名单 yearly_config 还要用。
        static List<ReadyCheck> ProbeAll(ReadyScope s)
        {
            List<ReadyCheck> list = new List<ReadyCheck>();
            list.Add(Guard("patches", s, ReportsReadinessProbe.Patches));
            list.Add(Guard("years", s, ReportsReadinessProbe.Years));
            list.Add(Guard("calendar", s, ReportsReadinessProbe.Calendar));
            list.Add(Guard("yearly_config", s, ReportsReadinessProbe.YearlyConfig));
            list.Add(Guard("pu_opening", s, ReportsReadinessProbe.PuOpening));
            list.Add(Guard("vendor_extradefine", s, ReportsReadinessProbe.VendorExtra));
            list.Add(Guard("modules", s, ReportsReadinessProbeMore.Modules));
            list.Add(Guard("prior_gl_close", s, ReportsReadinessProbeMore.PriorGlClose));
            list.Add(Guard("workflow", s, ReportsReadinessProbeMore.Workflow));
            list.Add(Guard("defaults", s, ReportsReadinessProbeMore.Defaults));
            return list;
        }

        // 一项的异常不连累别的项。years、modules 读 UFSYSTEM，失败多半是 SQL 登录没有权限。
        static ReadyCheck Guard(string id, ReadyScope s, Func<ReadyScope, ReadyCheck> probe)
        {
            try
            {
                return probe(s);
            }
            catch (Exception ex)
            {
                bool system = id == "years" || id == "modules";
                string detail = (system ? "读不到 UFSYSTEM：" : "查询失败：") + Brief(ex.Message);
                return new ReadyCheck(id, Unknown, detail, system ? SysHint : "查看桥日志和数据库权限后重跑体检");
            }
        }

        // 异常文本只留第一行、最多 160 字。
        internal static string Brief(string message)
        {
            string text = (message ?? "").Trim();
            int nl = text.IndexOfAny(new char[] { '\r', '\n' });
            if (nl >= 0)
            {
                text = text.Substring(0, nl);
            }
            return text.Length > 160 ? text.Substring(0, 160) : text;
        }

        internal static string Overall(List<ReadyCheck> checks)
        {
            int worst = 0;
            for (int i = 0; i < checks.Count; i++)
            {
                worst = Math.Max(worst, Array.IndexOf(Rank, checks[i].Status));
            }
            return Rank[worst];
        }

        static Dictionary<string, object> ToMap(ReadyCheck c)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            map["id"] = c.Id;
            map["title"] = ReportsReadinessReq.Title(c.Id);
            map["status"] = c.Status;
            map["detail"] = c.Detail ?? "";
            map["fix_hint"] = c.Status == Ok ? null : c.FixHint;
            map["docs_anchor"] = ReportsReadinessReq.DocsPage + c.Id;
            map["safe"] = true;
            return map;
        }

        // yyyy-MM-dd 两个日期相差的天数（b − a）；任一无效返回 int.MinValue。
        internal static int Days(string a, string b)
        {
            DateTime da;
            DateTime db;
            if (!ParseDay(a, out da) || !ParseDay(b, out db))
            {
                return int.MinValue;
            }
            return (int)(db - da).TotalDays;
        }

        internal static bool ParseDay(string text, out DateTime day)
        {
            return DateTime.TryParseExact((text ?? "").Trim(), "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out day);
        }

        internal static int YearOf(string date)
        {
            DateTime day;
            return ParseDay(date, out day) ? day.Year : 0;
        }
    }
}
