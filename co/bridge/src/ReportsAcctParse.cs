using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // close_check 的参数。Period 为 0 表示缺省（会计年度是登录年度时取登录月份，否则取 12）。
    internal sealed class AcctCloseArgs
    {
        public int Period;
    }

    // ia_anomalies 的参数。Period 同上；Limit 是未回冲、未记账两张清单各自的最多行数。
    internal sealed class AcctIaArgs
    {
        public int Period;
        public int Limit;
    }

    // cost_unit 的参数。From、To 为 0 表示缺省（最近一个成本核算已结账的期间）。
    internal sealed class AcctCostArgs
    {
        public int From;
        public int To;
        public string[] Inv = new string[0];
        public string InvClass = "";
    }

    // 账务核对族请求取值的校验：只抛 400，不连库。会计年度 fiscal_year 已在 ReportsReq.Parse 里校验。
    internal static class ReportsAcctParse
    {
        internal const int DefaultLimit = 50;
        internal const int MaxLimit = 200;
        const int MaxCodes = 20;
        const int MaxText = 60;

        internal static AcctCloseArgs Close(Dictionary<string, object> body)
        {
            AcctCloseArgs q = new AcctCloseArgs();
            q.Period = Int(body, "period", 0, 1, 12);
            return q;
        }

        internal static AcctIaArgs Ia(Dictionary<string, object> body)
        {
            AcctIaArgs q = new AcctIaArgs();
            q.Period = Int(body, "period", 0, 1, 12);
            q.Limit = Int(body, "limit", DefaultLimit, 1, MaxLimit);
            return q;
        }

        internal static AcctCostArgs Cost(Dictionary<string, object> body)
        {
            AcctCostArgs q = new AcctCostArgs();
            q.From = Int(body, "period_from", 0, 1, 12);
            q.To = Int(body, "period_to", 0, 1, 12);
            if (q.From > 0 && q.To > 0 && q.From > q.To)
            {
                throw GlReq.Bad("period_from 不能大于 period_to", "period_from");
            }
            q.Inv = Codes(body, "inv");
            q.InvClass = ReportsStockReq.OptPrefix(body, "inv_class");
            return q;
        }

        // 缺省期间：会计年度是登录年度时取登录月份，否则取 12（过去的年度看年底，未来的年度也只有 12 个期间）。
        internal static int DefaultPeriod(WorkContext ctx, int year)
        {
            if (GlState.LoginYear(ctx) != year)
            {
                return 12;
            }
            return MonthOf(GlState.LoginDate(ctx));
        }

        // 登录日期（yyyy-MM-dd）的月份；格式不对时 400。
        internal static int MonthOf(string date)
        {
            DateTime day;
            if (!DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
            {
                throw GlReq.Bad("登录日期无效");
            }
            return day.Month;
        }

        // 期间首日、下月首日（yyyy-MM-dd）和期间末日。纯函数，--selftest 用。
        internal static string FirstDay(int year, int period)
        {
            return new DateTime(year, period, 1).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        internal static string NextDay(int year, int period)
        {
            return new DateTime(year, period, 1).AddMonths(1).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        internal static string LastDay(int year, int period)
        {
            return new DateTime(year, period, 1).AddMonths(1).AddDays(-1).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        // 编码数组：缺省空（不限）；给了就是 1 到 20 个，每个去掉首尾空白后 1 到 60 个字符，不含控制字符。
        internal static string[] Codes(Dictionary<string, object> body, string key)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return new string[0];
            }
            string message = key + " 必须是 1 到 20 个编码（每个 1 到 60 个字符，不含控制字符）";
            IList list = raw as IList;
            if (list == null || list.Count < 1 || list.Count > MaxCodes)
            {
                throw GlReq.Bad(message, key);
            }
            string[] codes = new string[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                string text = list[i] as string;
                text = text == null ? "" : text.Trim();
                if (text.Length == 0 || text.Length > MaxText || HasControl(text))
                {
                    throw GlReq.Bad(message, FieldPath.Item(key, i));
                }
                codes[i] = text;
            }
            return codes;
        }

        static bool HasControl(string text)
        {
            for (int i = 0; i < text.Length; i++)
            {
                if (char.IsControl(text[i]))
                {
                    return true;
                }
            }
            return false;
        }

        static int Int(Dictionary<string, object> body, string key, int fallback, int min, int max)
        {
            object raw = GlReq.Field(body, key);
            return raw == null ? fallback : GlReq.IntIn(raw, key, min, max);
        }
    }
}
