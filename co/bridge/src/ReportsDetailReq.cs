using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 明细账报表 arap_detail（往来明细账）、gl_detail（总账明细账）的请求参数。
    // 字段名由 Requests.ReportSpecs 把关；fiscal_year、after 由 ReportsReq.Parse 统一校验，这里只校验其余取值，登录前抛 400。
    internal sealed class DetailArgs
    {
        public string Side;
        public string[] Partners;
        // 空串：arap_detail 的 date_to 取登录日期；gl_detail 两个都空表示按期间。
        public string DateFrom;
        public string DateTo;
        // arap_detail 的日期口径：register 登记日期（缺省，同 arap_balance）或 document 单据日期。
        public string Basis;
        public string[] Accounts;
        public string[] Excludes;
        public string Dept;
        public string Person;
        public bool Writeoff;
        public string Code;
        public bool IncludeSub;
        public int PeriodFrom;
        public int PeriodTo;
        public bool Unposted;
        public string Customer;
        public string Vendor;
        public string Project;
        public string ProjectClass;
        public int Limit;
    }

    internal static class ReportsDetailReq
    {
        internal const string ArapName = "arap_detail";
        internal const string GlName = "gl_detail";
        const int MaxPartners = 20;

        public static bool Owns(string name)
        {
            return name == ArapName || name == GlName;
        }

        public static DetailArgs Parse(string name, Dictionary<string, object> body)
        {
            DetailArgs d = new DetailArgs();
            d.Dept = OptText(body, "dept", 20);
            d.Person = OptText(body, "person", 20);
            d.Limit = OptInt(body, "limit", 200, 1, 1000);
            if (name == ArapName)
            {
                ParseArap(d, body);
            }
            else
            {
                ParseGl(d, body);
            }
            return d;
        }

        static void ParseArap(DetailArgs d, Dictionary<string, object> body)
        {
            d.Side = GlReq.Field(body, "side") as string;
            if (d.Side != "ar" && d.Side != "ap")
            {
                throw GlReq.Bad("side 只能是 ar 或 ap", "side");
            }
            d.Partners = PartnerList(GlReq.Field(body, "partner"));
            d.DateFrom = GlReq.OptDate(GlReq.Field(body, "date_from"), "date_from");
            if (d.DateFrom.Length == 0)
            {
                throw GlReq.Bad("缺少字段 date_from", "date_from");
            }
            d.DateTo = GlReq.OptDate(GlReq.Field(body, "date_to"), "date_to");
            CheckOrder(d.DateFrom, d.DateTo);
            object basis = GlReq.Field(body, "basis");
            d.Basis = basis == null ? "register" : basis as string;
            // 单据日期口径暂不开放：核销（9P）两半各带不同单据的日期，按单据日期累计会把之后才核销的也抵掉（需改为按登记日期截止后再开放）。
            if (d.Basis != "register")
            {
                throw GlReq.Bad("basis 暂只支持 register（登记日期口径）", "basis");
            }
            d.Accounts = CodeList(body, "accounts", new string[] { d.Side == "ar" ? "1122" : "2202" }, 1);
            d.Excludes = CodeList(body, "exclude_accounts", new string[0], 0);
            d.Writeoff = OptBool(body, "include_writeoff", false);
        }

        static void ParseGl(DetailArgs d, Dictionary<string, object> body)
        {
            d.Code = GlReq.Field(body, "code") as string;
            if (!ReportsReq.CodeChars(d.Code, 40))
            {
                throw GlReq.Bad("code 必须是 1 到 40 位数字、字母、点或短横", "code");
            }
            d.IncludeSub = OptBool(body, "include_sub", true);
            d.Unposted = OptBool(body, "include_unposted", false);
            d.Customer = OptText(body, "customer", 60);
            d.Vendor = OptText(body, "vendor", 60);
            d.Project = OptText(body, "project", 60);
            d.ProjectClass = OptText(body, "project_class", 20);
            if (d.ProjectClass.Length > 0 && d.Project.Length == 0)
            {
                throw GlReq.Bad("project_class 只能和 project 一起用", "project_class");
            }
            ParseRange(d, body);
        }

        // 期间（period_from / period_to）和日期（date_from / date_to）二选一，各自成对。日期必须在同一年度内。
        static void ParseRange(DetailArgs d, Dictionary<string, object> body)
        {
            object pf = GlReq.Field(body, "period_from");
            object pt = GlReq.Field(body, "period_to");
            d.DateFrom = GlReq.OptDate(GlReq.Field(body, "date_from"), "date_from");
            d.DateTo = GlReq.OptDate(GlReq.Field(body, "date_to"), "date_to");
            bool periods = pf != null || pt != null;
            bool dates = d.DateFrom.Length > 0 || d.DateTo.Length > 0;
            if (periods == dates)
            {
                throw GlReq.Bad("period_from / period_to 与 date_from / date_to 必须二选一", "period_from");
            }
            if (periods)
            {
                d.PeriodFrom = GlReq.IntIn(GlReq.Field(body, "period_from"), "period_from", 1, 12);
                d.PeriodTo = GlReq.IntIn(GlReq.Field(body, "period_to"), "period_to", 1, 12);
                if (d.PeriodFrom > d.PeriodTo)
                {
                    throw GlReq.Bad("period_from 不能大于 period_to", "period_from");
                }
                return;
            }
            ParseDates(d, body);
        }

        static void ParseDates(DetailArgs d, Dictionary<string, object> body)
        {
            if (d.DateFrom.Length == 0 || d.DateTo.Length == 0)
            {
                throw GlReq.Bad("date_from 和 date_to 必须同时给出", "date_from");
            }
            CheckOrder(d.DateFrom, d.DateTo);
            if (d.DateFrom.Substring(0, 4) != d.DateTo.Substring(0, 4))
            {
                throw GlReq.Bad("date_from 和 date_to 必须在同一年度", "date_from");
            }
            object year = GlReq.Field(body, "fiscal_year");
            if (year != null && GlReq.IntIn(year, "fiscal_year", 2000, 2099).ToString(CultureInfo.InvariantCulture)
                != d.DateFrom.Substring(0, 4))
            {
                throw GlReq.Bad("fiscal_year 与 date_from 的年份不一致", "fiscal_year");
            }
            // 期间按自然月（同 GlSave 的 iperiod）。
            d.PeriodFrom = int.Parse(d.DateFrom.Substring(5, 2), CultureInfo.InvariantCulture);
            d.PeriodTo = int.Parse(d.DateTo.Substring(5, 2), CultureInfo.InvariantCulture);
        }

        // yyyy-MM-dd 按字符串比较即按日期比较。
        internal static void CheckOrder(string from, string to)
        {
            if (from.Length > 0 && to.Length > 0 && string.CompareOrdinal(from, to) > 0)
            {
                throw GlReq.Bad("date_from 不能晚于 date_to", "date_from");
            }
        }

        // partner：一个编码，或 1 到 20 个编码的数组；每个去掉首尾空白后 1 到 60 个字符，不含控制字符。
        static string[] PartnerList(object raw)
        {
            const string message = "partner 必须是往来单位编码或 1 到 20 个编码的数组（每个 1 到 60 个字符，不含控制字符）";
            if (raw == null)
            {
                throw GlReq.Bad("缺少字段 partner", "partner");
            }
            IList list = raw is string ? new object[] { raw } : AsList(raw);
            if (list == null || list.Count < 1 || list.Count > MaxPartners)
            {
                throw GlReq.Bad(message, "partner");
            }
            List<string> codes = new List<string>(list.Count);
            for (int i = 0; i < list.Count; i++)
            {
                string text = Word(list[i] as string, 60);
                if (text == null)
                {
                    throw GlReq.Bad(message, raw is string ? "partner" : FieldPath.Item("partner", i));
                }
                if (!codes.Contains(text))
                {
                    codes.Add(text);
                }
            }
            return codes.ToArray();
        }

        static string[] CodeList(Dictionary<string, object> body, string key, string[] fallback, int min)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return fallback;
            }
            string message = key + " 必须是 " + min.ToString(CultureInfo.InvariantCulture) + " 到 20 个科目编码前缀";
            IList list = AsList(raw);
            if (list == null || list.Count < min || list.Count > 20)
            {
                throw GlReq.Bad(message, key);
            }
            string[] codes = new string[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                string text = list[i] as string;
                if (!ReportsReq.CodeChars(text, 40))
                {
                    throw GlReq.Bad(message, FieldPath.Item(key, i));
                }
                codes[i] = text;
            }
            return codes;
        }

        static IList AsList(object raw)
        {
            IList list = raw as ArrayList;
            if (list != null)
            {
                return list;
            }
            return raw as object[];
        }

        static int OptInt(Dictionary<string, object> body, string key, int fallback, int min, int max)
        {
            object raw = GlReq.Field(body, key);
            return raw == null ? fallback : GlReq.IntIn(raw, key, min, max);
        }

        static bool OptBool(Dictionary<string, object> body, string key, bool fallback)
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

        static string OptText(Dictionary<string, object> body, string key, int max)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return "";
            }
            string text = Word(raw as string, max);
            if (text == null)
            {
                throw GlReq.Bad(key + " 必须是 1 到 " + max.ToString(CultureInfo.InvariantCulture) + " 个字符，不含控制字符", key);
            }
            return text;
        }

        // 去掉首尾空白后的编码；空、超长或含控制字符返回 null。
        static string Word(string text, int max)
        {
            if (text == null || text.Trim().Length == 0 || text.Length > max)
            {
                return null;
            }
            for (int i = 0; i < text.Length; i++)
            {
                if (char.IsControl(text[i]))
                {
                    return null;
                }
            }
            return text.Trim();
        }

        // after 游标按段解析整数段；不是整数时 400（游标被改过）。
        internal static int CursorInt(string text)
        {
            int value;
            if (!int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out value))
            {
                throw GlReq.Bad("after 游标无效", "after");
            }
            return value;
        }
    }
}
