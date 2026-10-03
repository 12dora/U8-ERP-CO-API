using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 月末结账每一步在顺序闸门之外要看的账套状态：
    // 采购 / 存货核算的期初记账（启用年度 GL_mend 第 0 期）、总账结账前其他子系统（固定资产、薪资、成本）的结账标志。
    // 表名、列名只来自这里和 PeriodModules 的常量，调用方的值只进参数。
    internal static class PeriodCloseFacts
    {
        const string StartSql = "SELECT TOP 1 LEFT(LTRIM(cValue), 10) v FROM AccInformation WHERE cSysID=? AND cName=?";
        const string ZeroSql = "SELECT TOP 1 'x' FROM GL_mend WITH (UPDLOCK, HOLDLOCK) WHERE iyear=? AND iperiod=0 AND ISNULL({COL},0)=1";
        const string ExtraOpenSql = "SELECT TOP 1 'x' FROM GL_mend WHERE iyear=? AND iperiod=? AND ISNULL({COL},0)=0";

        public const string IaWarning = "ia_opening_not_posted：存货核算启用年度第 0 期没有期初记账标志（GL_mend.bflag_IA=0），"
            + "照样结账；可用 openings/post module=ia 做存货核算期初记账（有月份结账后仍可记账，但不能再取消）";

        // 结账时（事务里）读采购、存货核算的期初记账标志：已启用模块的启用年度（AccInformation，读不到用 GL_mend 最早的年度）第 0 期。
        public static void Opening(object conn, PeriodRun run, int minYear)
        {
            if (!run.Ask.Close)
            {
                return;
            }
            if (run.Started[PeriodModules.Pu])
            {
                run.PuPosted = Posted(conn, "bflag_PU", StartYear(conn, "PU", "dPUStartDate", minYear));
            }
            if (run.Started[PeriodModules.Ia])
            {
                run.IaPosted = Posted(conn, "bflag_IA", StartYear(conn, "IA", "dIAStartDate", minYear));
            }
        }

        static int StartYear(object conn, string sys, string name, int fallback)
        {
            string text = Rows.Scalar(conn, StartSql, new object[] { sys, name });
            DateTime day;
            text = text == null ? "" : text.Trim();
            if (DateTime.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
            {
                return day.Year;
            }
            return fallback;
        }

        static bool Posted(object conn, string col, int year)
        {
            return year > 0 && Rows.Scalar(conn, ZeroSql.Replace("{COL}", col), new object[] { year }) != null;
        }

        // 不连库的部分：采购未做期初记账拒绝；存货核算未做期初记账只加一条提示（只加一次）。纯函数，--selftest 用。
        internal static string Gate(PeriodRun run, int[] step)
        {
            int mod = step[0];
            if (!run.Ask.Close)
            {
                return null;
            }
            if (mod == PeriodModules.Pu && !run.PuPosted)
            {
                return "采购未做期初记账，请先 openings/post";
            }
            if (mod == PeriodModules.Ia && !run.IaPosted && !run.Warnings.Contains(IaWarning))
            {
                run.Warnings.Add(IaWarning);
            }
            return null;
        }

        // 总账结账：已启用的固定资产、薪资、成本本期还没结账时拒绝（桥不结这几个模块）。
        public static string ExtrasOpen(object conn, PeriodRun run, int year, int period)
        {
            foreach (string[] extra in run.Extras)
            {
                if (Rows.Scalar(conn, ExtraOpenSql.Replace("{COL}", extra[0]), new object[] { year, period }) != null)
                {
                    return extra[1] + "本期还没结账，总账最后结（这个模块桥不结，请在 U8 里结账）";
                }
            }
            return null;
        }

        // 一步的全部账套状态检查：先不连库的，再查总账结账前的其他子系统。
        public static string Step(object conn, PeriodRun run, int[] step)
        {
            string refusal = Gate(run, step);
            if (refusal == null && run.Ask.Close && step[0] == PeriodModules.Gl)
            {
                refusal = ExtrasOpen(conn, run, step[1], step[2]);
            }
            return refusal;
        }
    }
}
