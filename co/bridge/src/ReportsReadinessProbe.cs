using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 账套体检的前六项：补丁、年度账、工作日历、年度配置、采购期初记账、供应商扩展自定义项表。
    // 每个方法一项，只读；判断规则抽成不查库的静态函数，--selftest 直接测（ReportsReadinessSelfTest）。
    internal static class ReportsReadinessProbe
    {
        const string Ok = ReportsReadiness.Ok;
        const string Warn = ReportsReadiness.Warn;
        const string Fail = ReportsReadiness.Fail;
        const string Unknown = ReportsReadiness.Unknown;
        // 工作日历离用完不到这么多天时 warn。
        internal const int CalendarMarginDays = 30;

        public static ReadyCheck Patches(ReadyScope s)
        {
            const string hint = "在数据库服务器上以 32 位运行 U8 安装目录 Admin\\UFDBTMP\\DBEnginSys.exe -all，"
                + "只勾选本账套重放补丁（先备份）；见 getting-started.md#patches";
            Dictionary<string, object> cat = s.Catalog();
            if (GlSql.Int(cat, "patch_table") == 0)
            {
                return new ReadyCheck("patches", Fail, "账套库没有 UA_PatchList（建账晚于补丁，补丁没有打到本账套）", hint);
            }
            int local = GlSql.Int(Rows.One(s.Conn, ReportsReadinessSql.PatchLocalSql, new object[0]), "n");
            int sys;
            try
            {
                sys = GlSql.Int(Rows.One(s.Conn, ReportsReadinessSql.PatchSysSql, new object[0]), "n");
            }
            catch (Exception ex)
            {
                return new ReadyCheck("patches", Unknown, "账套库补丁记录 " + N(local) + " 条；读不到 UFSYSTEM："
                    + ReportsReadiness.Brief(ex.Message), ReportsReadiness.SysHint);
            }
            bool canary = GlSql.Int(cat, "canary") == 1;
            string detail = "账套库补丁记录 " + N(local) + " 条，系统库 " + N(sys) + " 条";
            if (!canary)
            {
                detail += "；WFAudit 没有补丁加的 signatureid 列（旁证，只提示）";
            }
            if (local > 0 && local < sys)
            {
                detail += "；账套库少于系统库，可能没有重放完（系统库也会记只改系统库的补丁，差几条不一定是缺）";
            }
            return new ReadyCheck("patches", PatchStatus(local, sys, canary), detail, hint);
        }

        // 只按 UA_PatchList 定 fail：系统库有记录、账套库一条都没有。WFAudit.signatureid 只是旁证：
        // 不知道是哪个工作流补丁加的，对不上 UA_PatchList 的补丁号，所以缺了只 warn，不 fail。
        // 账套库条数少于系统库（重放了一部分）也只 warn：系统库还记着只改系统库的补丁，条数本来就可能多。
        internal static string PatchStatus(int local, int sys, bool canary)
        {
            if (local == 0 && sys > 0)
            {
                return Fail;
            }
            return canary && local >= sys ? Ok : Warn;
        }

        public static ReadyCheck Years(ReadyScope s)
        {
            const string hint = "在系统管理 → 年度账 → 建立中逐年补建缺的年度（先备份）；登录日期要落在已有年度内；"
                + "见 getting-started.md#years";
            List<Dictionary<string, object>> rows = Rows.Query(s.Conn, ReportsReadinessSql.PeriodSql,
                new object[] { s.Acc }, 200);
            List<string[]> periods = new List<string[]>(rows.Count);
            for (int i = 0; i < rows.Count; i++)
            {
                periods.Add(new string[] { GlSql.Col(rows[i], "y"), GlSql.Col(rows[i], "b"), GlSql.Col(rows[i], "e") });
            }
            s.Periods = periods;
            if (periods.Count == 0)
            {
                return new ReadyCheck("years", Fail, "UFSYSTEM..UA_Period 没有账套 " + s.Acc + " 的年度", hint);
            }
            string span = "已建年度 " + periods[0][0] + "–" + periods[periods.Count - 1][0];
            List<string> missing = new List<string>();
            if (!Covered(periods, s.AsOf))
            {
                missing.Add("as_of " + s.AsOf);
            }
            if (s.Today.Length > 0 && !Covered(periods, s.Today))
            {
                missing.Add("服务器当天 " + s.Today);
            }
            if (missing.Count == 0)
            {
                return new ReadyCheck("years", Ok, span, hint);
            }
            return new ReadyCheck("years", Fail, span + "；不在任何年度内：" + string.Join("、", missing.ToArray()), hint);
        }

        // date 落在某个年度的起止日期之间（含两端）。yyyy-MM-dd 可以直接按字符串比。
        internal static bool Covered(List<string[]> periods, string date)
        {
            if (string.IsNullOrEmpty(date))
            {
                return false;
            }
            for (int i = 0; i < periods.Count; i++)
            {
                if (string.CompareOrdinal(periods[i][1], date) <= 0 && string.CompareOrdinal(date, periods[i][2]) <= 0)
                {
                    return true;
                }
            }
            return false;
        }

        public static ReadyCheck Calendar(ReadyScope s)
        {
            const string hint = "在 U8 企业应用平台 → 基础档案 → 工作日历中延长 SYSTEM 日历；见 getting-started.md#calendar";
            if (GlSql.Int(s.Catalog(), "cal_table") == 0)
            {
                return new ReadyCheck("calendar", Fail, "账套库没有 bas_calendardetail", hint);
            }
            string max = GlSql.Col(Rows.One(s.Conn, ReportsReadinessSql.CalendarSql, new object[0]), "m");
            string status = CalendarStatus(max, s.AsOf, s.Today);
            string detail = max.Length == 0 ? "SYSTEM 日历（CalendarId=1）没有日子" : "SYSTEM 日历排到 " + max;
            return new ReadyCheck("calendar", status, detail, hint);
        }

        // 最后一天早于 as_of 或服务器当天：fail；离当天不到 CalendarMarginDays 天：warn。
        internal static string CalendarStatus(string max, string asOf, string today)
        {
            if (string.IsNullOrEmpty(max))
            {
                return Fail;
            }
            if (string.CompareOrdinal(max, asOf ?? "") < 0 || string.CompareOrdinal(max, today ?? "") < 0)
            {
                return Fail;
            }
            int left = ReportsReadiness.Days(today, max);
            return left != int.MinValue && left < CalendarMarginDays ? Warn : Ok;
        }

        public static ReadyCheck YearlyConfig(ReadyScope s)
        {
            const string hint = "建立年度账会复制科目、编码方案和年度选项；应收应付的科目设置、结算方式科目、"
                + "总账现金流量取数需在 U8 企业应用平台中逐年度补齐（先在启用年度设好再建后续年度）；"
                + "见 getting-started.md#yearly_config";
            List<int> years = YearList(s);
            if (years.Count < 2)
            {
                return new ReadyCheck("yearly_config", Ok, "只有一个年度，不需要按年度拷配置", hint);
            }
            List<Dictionary<string, object>> rows = Rows.Query(s.Conn, ReportsReadinessSql.YearlySql, new object[0], 2000);
            Dictionary<string, int> counts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < rows.Count; i++)
            {
                counts[GlSql.Col(rows[i], "t") + ":" + GlSql.Col(rows[i], "y")] = GlSql.Int(rows[i], "n");
            }
            List<string> gaps = YearlyGaps(counts, years);
            string span = "年度 " + N(years[0]) + "–" + N(years[years.Count - 1]);
            if (gaps.Count == 0)
            {
                return new ReadyCheck("yearly_config", Ok, span + "，各配置表都有数据", hint);
            }
            int limit = YearlyLimit(s.AsOf, s.Today);
            List<string> due = DueGaps(gaps, limit);
            if (due.Count == 0)
            {
                return new ReadyCheck("yearly_config", Warn, span + "；晚于 " + N(limit) + " 年的年度为空（还用不到）："
                    + Join(gaps, 12), hint);
            }
            return new ReadyCheck("yearly_config", Fail, span + "；起始年度有、之后年度为空：" + Join(due, 12), hint);
        }

        // 只要求到 as_of 和服务器当天中较晚的那一年；两个都解析不了时为 0（全部要求）。
        internal static int YearlyLimit(string asOf, string today)
        {
            return Math.Max(ReportsReadiness.YearOf(asOf), ReportsReadiness.YearOf(today));
        }

        // gaps 的元素是「表名 年度」。留下年度不晚于 limit 的；limit 为 0 时全部留下。
        internal static List<string> DueGaps(List<string> gaps, int limit)
        {
            List<string> due = new List<string>();
            for (int i = 0; i < gaps.Count; i++)
            {
                string gap = gaps[i];
                int year;
                bool parsed = int.TryParse(gap.Substring(gap.LastIndexOf(' ') + 1), NumberStyles.None,
                    CultureInfo.InvariantCulture, out year);
                if (limit == 0 || !parsed || year <= limit)
                {
                    due.Add(gap);
                }
            }
            return due;
        }

        // 年度名单：UA_Period 读到了用它，否则用 GL_mend。升序。
        static List<int> YearList(ReadyScope s)
        {
            List<int> years = new List<int>();
            if (s.Periods != null)
            {
                for (int i = 0; i < s.Periods.Count; i++)
                {
                    years.Add(int.Parse(s.Periods[i][0], CultureInfo.InvariantCulture));
                }
                return years;
            }
            List<int[]> mend = s.GlMend();
            for (int i = 0; i < mend.Count; i++)
            {
                years.Add(mend[i][0]);
            }
            return years;
        }

        // counts 的键是「表名:年度」。起始年度（years[0]）该表有行、之后某年度为 0 的，记「表名 年度」。
        internal static List<string> YearlyGaps(Dictionary<string, int> counts, List<int> years)
        {
            List<string> gaps = new List<string>();
            string[][] tables = ReportsReadinessSql.YearlyTables;
            for (int t = 0; t < tables.Length; t++)
            {
                string table = tables[t][0];
                if (Count(counts, table, years[0]) == 0)
                {
                    continue;
                }
                for (int i = 1; i < years.Count; i++)
                {
                    if (Count(counts, table, years[i]) == 0)
                    {
                        gaps.Add(table + " " + N(years[i]));
                    }
                }
            }
            return gaps;
        }

        static int Count(Dictionary<string, int> counts, string table, int year)
        {
            int n;
            return counts.TryGetValue(table + ":" + N(year), out n) ? n : 0;
        }

        public static ReadyCheck PuOpening(ReadyScope s)
        {
            const string hint = "调用 openings/post {\"module\":\"pu\",\"action\":\"post\"} 做采购期初记账";
            string start = ReportsOpening.StartDate(s.Ctx, "pu");
            if (start.Length == 0)
            {
                return new ReadyCheck("pu_opening", Fail, "采购管理没有启用日期（AccInformation PU dPUStartDate）",
                    "先启用采购管理（见 modules），再做采购期初记账");
            }
            int year = ReportsOpening.YearOf(start, ReportsReadiness.YearOf(s.AsOf));
            bool posted = ReportsOpening.Posted(s.Ctx, "pu", year);
            string detail = "采购启用日期 " + start + "，" + N(year) + " 年期初" + (posted ? "已记账" : "未记账");
            return new ReadyCheck("pu_opening", posted ? Ok : Fail, detail, hint);
        }

        public static ReadyCheck VendorExtra(ReadyScope s)
        {
            const string hint = "在 U8 基础档案 → 自定义项中为供应商增加一个扩展自定义项，U8 会建出 Vendor_extradefine；"
                + "见 getting-started.md#vendor_extradefine";
            bool has = GlSql.Int(s.Catalog(), "vendor_ext") == 1;
            return new ReadyCheck("vendor_extradefine", has ? Ok : Fail,
                has ? "Vendor_extradefine 存在" : "账套库没有 Vendor_extradefine（供应商档案写入会失败）", hint);
        }

        internal static string N(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }

        internal static string Join(List<string> items, int max)
        {
            if (items.Count <= max)
            {
                return string.Join("、", items.ToArray());
            }
            return string.Join("、", items.GetRange(0, max).ToArray()) + " 等 " + N(items.Count) + " 项";
        }
    }
}
