using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // receipts_payments 的参数。日期空串表示缺省（截止日取登录日期）。
    internal sealed class FinRecPayArgs
    {
        // 字段权限会遮金额：不按金额排序、截断（Run 在查询前定，见 PermMask.WillMaskAmounts）。
        public bool NoAmountOrder;
        public string Side;
        public string DateFrom;
        public string DateTo;
        // 分组维度，按请求给的顺序、不重复；空数组表示只出合计。
        public string[] GroupBy;
        public string[] Partners;
        public string[] Persons;
        public string[] Depts;
        public bool Unverified;
        public int Limit;
    }

    // notes_due 的参数。日期空串表示缺省（到期起始日取登录日期，截止日取起始日后 30 天）。
    internal sealed class FinNotesArgs
    {
        public string Side;
        public string ExpireFrom;
        public string ExpireTo;
        public bool OpenOnly;
        public string[] Partners;
        public int Limit;
    }

    // cash_flow 的参数。会计年度在 ReportArgs.FiscalYear（缺省已取登录年度）；To 为 0 表示缺省（登录月份）。
    internal sealed class FinCashArgs
    {
        public int From;
        public int To;
        public bool Unposted;
    }

    // Fin 族请求取值的校验：只抛 400，不连库。
    internal static class ReportsFinParse
    {
        // 收付款汇总的分组维度；输出顺序按请求里给的顺序。
        internal static readonly string[] Dims = new string[] { "day", "month", "partner", "person", "department", "settle" };
        internal const int DefaultLimit = 200;
        internal const int MaxLimit = 1000;
        internal const int DefaultDueDays = 30;
        const int MaxCodes = 20;
        const int MaxText = 60;

        internal static FinRecPayArgs RecPay(Dictionary<string, object> body)
        {
            FinRecPayArgs q = new FinRecPayArgs();
            q.Side = Side(body);
            q.DateFrom = GlReq.OptDate(GlReq.Field(body, "date_from"), "date_from");
            if (q.DateFrom.Length == 0)
            {
                throw GlReq.Bad("缺少字段 date_from", "date_from");
            }
            q.DateTo = GlReq.OptDate(GlReq.Field(body, "date_to"), "date_to");
            if (q.DateTo.Length > 0 && string.CompareOrdinal(q.DateFrom, q.DateTo) > 0)
            {
                throw GlReq.Bad("date_from 不能晚于 date_to", "date_from");
            }
            q.GroupBy = GroupBy(GlReq.Field(body, "group_by"));
            q.Partners = Codes(body, "partner");
            q.Persons = Codes(body, "person");
            q.Depts = Codes(body, "dept");
            q.Unverified = Bool(body, "include_unverified", false);
            q.Limit = Int(body, "limit", DefaultLimit, 1, MaxLimit);
            return q;
        }

        internal static FinNotesArgs Notes(Dictionary<string, object> body)
        {
            FinNotesArgs q = new FinNotesArgs();
            q.Side = Side(body);
            q.ExpireFrom = GlReq.OptDate(GlReq.Field(body, "expire_from"), "expire_from");
            q.ExpireTo = GlReq.OptDate(GlReq.Field(body, "expire_to"), "expire_to");
            if (q.ExpireFrom.Length > 0 && q.ExpireTo.Length > 0 && string.CompareOrdinal(q.ExpireFrom, q.ExpireTo) > 0)
            {
                throw GlReq.Bad("expire_from 不能晚于 expire_to", "expire_from");
            }
            q.OpenOnly = Bool(body, "open_only", true);
            q.Partners = Codes(body, "partner");
            q.Limit = Int(body, "limit", DefaultLimit, 1, MaxLimit);
            return q;
        }

        internal static FinCashArgs Cash(Dictionary<string, object> body)
        {
            FinCashArgs q = new FinCashArgs();
            q.From = Int(body, "period_from", 1, 1, 12);
            q.To = Int(body, "period_to", 0, 1, 12);
            if (q.To > 0 && q.From > q.To)
            {
                throw GlReq.Bad("period_from 不能大于 period_to", "period_from");
            }
            q.Unposted = Bool(body, "include_unposted", false);
            return q;
        }

        // 缺省的到期截止日：起始日后 30 天。两个日期都必须是有效的 yyyy-MM-dd。
        internal static string DueTo(string from)
        {
            DateTime day = DateTime.ParseExact(from, "yyyy-MM-dd", CultureInfo.InvariantCulture);
            return day.AddDays(DefaultDueDays).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        // 到期日相对登录日期的天数（到期日在后为正）。任一方无法解析时返回 null。
        internal static object DaysBetween(string from, string to)
        {
            DateTime a;
            DateTime b;
            if (!DateTime.TryParseExact(from, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out a)
                || !DateTime.TryParseExact(to, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out b))
            {
                return null;
            }
            return (int)(b - a).TotalDays;
        }

        // 登录日期的月份（yyyy-MM-dd）；格式不对时 400。
        internal static int MonthOf(string date)
        {
            DateTime day;
            if (!DateTime.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day))
            {
                throw GlReq.Bad("登录日期无效");
            }
            return day.Month;
        }

        static string Side(Dictionary<string, object> body)
        {
            string side = GlReq.Field(body, "side") as string;
            if (side != "ar" && side != "ap")
            {
                throw GlReq.Bad("side 只能是 ar 或 ap", "side");
            }
            return side;
        }

        // group_by：0 到 2 个维度，不重复；缺省 ["day"]。
        static string[] GroupBy(object raw)
        {
            if (raw == null)
            {
                return new string[] { "day" };
            }
            IList list = raw as IList;
            if (list == null || list.Count > 2)
            {
                throw GlReq.Bad("group_by 必须是不超过 2 个维度的数组（day、month、partner、person、department、settle）", "group_by");
            }
            string[] dims = new string[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                string dim = list[i] as string;
                if (dim == null || Array.IndexOf(Dims, dim) < 0)
                {
                    throw GlReq.Bad("group_by 只能是 day、month、partner、person、department、settle", FieldPath.Item("group_by", i));
                }
                if (Array.IndexOf(dims, dim) >= 0)
                {
                    throw GlReq.Bad("group_by 不能重复", FieldPath.Item("group_by", i));
                }
                dims[i] = dim;
            }
            return dims;
        }

        // 编码数组：缺省空（不限）；给了就是 1 到 20 个，每个去掉首尾空白后 1 到 60 个字符，不含控制字符。
        static string[] Codes(Dictionary<string, object> body, string key)
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
                codes[i] = CodeAt(list[i], message, FieldPath.Item(key, i));
            }
            return codes;
        }

        static string CodeAt(object item, string message, string field)
        {
            string text = item as string;
            text = text == null ? "" : text.Trim();
            if (text.Length == 0 || text.Length > MaxText || HasControl(text))
            {
                throw GlReq.Bad(message, field);
            }
            return text;
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

        static bool Bool(Dictionary<string, object> body, string key, bool fallback)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return fallback;
            }
            if (!(raw is bool))
            {
                throw GlReq.Bad(key + " 必须是 true 或 false", key);
            }
            return (bool)raw;
        }

        static int Int(Dictionary<string, object> body, string key, int fallback, int min, int max)
        {
            object raw = GlReq.Field(body, key);
            return raw == null ? fallback : GlReq.IntIn(raw, key, min, max);
        }
    }
}
