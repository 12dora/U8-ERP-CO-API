using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 固定资产写入（卡片新增、撤销新增）的期间闸门：登录日期必须落在固定资产当前未结账的期间。
    // 当前期间在 AccInformation（cSysID='FA'）：iLastPeriod 是正在处理的月份，dWritableDate 是该月第一天；结账标志是
    // GL_mend.bflag_FA（桥不做固定资产结账）。U8 的 EAI 导入按登录期间写卡片和变动单，桥在调用前先拒绝（409），不交给 U8。
    // 会计期间按自然月划分。只用登录日期的年月：账套库年度（请求的 year）可以早于固定资产当前期间，
    // 不拿它比较；GL_mend 按登录日期的年份取行。
    internal sealed class FaPeriod
    {
        public int Year;
        public int Period;
        // 当前期间的第一天、最后一天（yyyy-MM-dd）。
        public string First;
        public string Last;

        const string OptSql = "SELECT cName AS n, CONVERT(nvarchar(40), cValue) AS v FROM AccInformation"
            + " WHERE cSysID=N'FA' AND cName IN (N'iLastPeriod', N'dWritableDate')";
        const string MendSql = "SELECT CONVERT(varchar(10), CONVERT(int, ISNULL(bflag_FA, 0))) AS f FROM GL_mend WHERE iyear=? AND iperiod=?";

        // 读选项、结账标志并判断；不通过 409 state_mismatch。
        internal static FaPeriod Require(WorkContext ctx)
        {
            DateTime login = DateTime.ParseExact(ctx.Item.Date.Trim(), "yyyy-MM-dd", CultureInfo.InvariantCulture);
            Dictionary<string, string> opts = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (Dictionary<string, object> row in Rows.Query(ctx.Conn, OptSql, new object[0], 10))
            {
                opts[ArcRead.Cell(row, "n") ?? ""] = ArcRead.Cell(row, "v");
            }
            string closed = Rows.Scalar(ctx.Conn, MendSql, new object[] { login.Year, login.Month });
            string refusal = Judge(login, Opt(opts, "iLastPeriod"), Opt(opts, "dWritableDate"), closed);
            if (refusal != null)
            {
                throw ArcGuard.State(refusal);
            }
            return Of(login);
        }

        static string Opt(Dictionary<string, string> opts, string name)
        {
            string value;
            return opts.TryGetValue(name, out value) ? value : null;
        }

        internal static FaPeriod Of(DateTime login)
        {
            FaPeriod p = new FaPeriod();
            p.Year = login.Year;
            p.Period = login.Month;
            DateTime first = new DateTime(login.Year, login.Month, 1);
            p.First = first.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            p.Last = first.AddMonths(1).AddDays(-1).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            return p;
        }

        // 纯判断（自检覆盖）：返回拒绝原因，null 放行。lastPeriod、writable 是选项原文，closed 是结账标志（"1" 已结账，没有行为 null）。
        internal static string Judge(DateTime login, string lastPeriod, string writable, string closed)
        {
            int period;
            if (lastPeriod == null || !int.TryParse(lastPeriod.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out period)
                || period < 1 || period > 12)
            {
                return "账套没有启用固定资产，或读不到固定资产的当前期间";
            }
            string day = login.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            if (login.Month != period)
            {
                return "固定资产当前期间是第 " + Num(period) + " 期，登录日期 " + day + " 不在该期间，请把登录日期放在固定资产当前期间内";
            }
            string other = Writable(login, writable);
            if (other != null)
            {
                return other;
            }
            if (closed != null && closed.Trim() == "1")
            {
                return "固定资产 " + Num(login.Year) + " 年第 " + Num(period) + " 期已结账";
            }
            return null;
        }

        // dWritableDate 解析得出、且年月与登录日期不同时给原因；解析不了不判断（以 iLastPeriod 为准）。
        static string Writable(DateTime login, string writable)
        {
            DateTime open;
            if (writable == null || !DateTime.TryParse(writable.Trim(), CultureInfo.InvariantCulture, DateTimeStyles.None, out open))
            {
                return null;
            }
            if (open.Year == login.Year && open.Month == login.Month)
            {
                return null;
            }
            return "固定资产当前期间是 " + Num(open.Year) + " 年 " + Num(open.Month) + " 月，登录日期 "
                + login.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) + " 不在该期间";
        }

        internal string Label()
        {
            return Num(Year) + " 年第 " + Num(Period) + " 期";
        }

        static string Num(int n)
        {
            return n.ToString(CultureInfo.InvariantCulture);
        }
    }
}
