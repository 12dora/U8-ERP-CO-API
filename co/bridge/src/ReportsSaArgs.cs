using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 在手订单 open_orders 的参数。字段名由 Requests.ReportSpecs 把关，这里只校验取值，登录前抛 400；处理函数里再解析一次。
    internal sealed class OpenOrdersArgs
    {
        // 字段权限会遮金额：客户、存货分组不按余额排序、截断（Run 在查询前定，见 PermMask.WillMaskAmounts）。
        public bool NoAmountOrder;
        // ship 按未发货、out 按未出库、invoice 按未开票。
        public string Basis = "ship";
        public string GroupBy = "customer";
        public string[] Customers = new string[0];
        public string[] Invs = new string[0];
        public string[] Persons = new string[0];
        public string DateFrom = "";
        public string DateTo = "";
        public string DueBefore = "";
        public int Limit = 200;
    }

    // 销售价格历史 sales_price_history 的参数。DateTo 空串表示取登录日期。
    internal sealed class PriceHistoryArgs
    {
        public string[] Customers = new string[0];
        public string[] Invs = new string[0];
        public string DateFrom = "";
        public string DateTo = "";
        public bool Unverified;
        public int Limit = 500;
    }

    // 客户变动 customer_churn 的参数。DateTo 空串表示取登录日期。
    internal sealed class ChurnArgs
    {
        // 字段权限会遮金额：三张清单不按收入排序（Run 在查询前定，见 PermMask.WillMaskAmounts）。
        public bool NoAmountOrder;
        public string DateFrom = "";
        public string DateTo = "";
        public int Lookback = 6;
        public int DeclinePct = 30;
        public int Limit = 50;
    }

    // 销售类三张报表的取值校验。每个函数只做一件事，错误都是 400 并带 field。
    internal static class SaArgs
    {
        internal static readonly string[] Bases = new string[] { "ship", "out", "invoice" };
        internal static readonly string[] Groups = new string[] { "customer", "inventory", "order" };
        const int MaxCodes = 20;
        const int MaxCodeLength = 60;

        public static OpenOrdersArgs Open(Dictionary<string, object> body)
        {
            OpenOrdersArgs a = new OpenOrdersArgs();
            a.Basis = Pick(body, "basis", "ship", Bases);
            a.GroupBy = Pick(body, "group_by", "customer", Groups);
            a.Customers = Codes(body, "customer");
            a.Invs = Codes(body, "inv");
            a.Persons = Codes(body, "person");
            a.DateFrom = GlReq.OptDate(GlReq.Field(body, "date_from"), "date_from");
            a.DateTo = GlReq.OptDate(GlReq.Field(body, "date_to"), "date_to");
            a.DueBefore = GlReq.OptDate(GlReq.Field(body, "due_before"), "due_before");
            Order(a.DateFrom, a.DateTo);
            a.Limit = Int(body, "limit", 200, 1, 1000);
            return a;
        }

        public static PriceHistoryArgs Price(Dictionary<string, object> body)
        {
            PriceHistoryArgs a = new PriceHistoryArgs();
            a.Customers = Codes(body, "customer");
            a.Invs = Codes(body, "inv");
            if (a.Customers.Length == 0 && a.Invs.Length == 0)
            {
                throw GlReq.Bad("customer 和 inv 至少要给一个", "customer");
            }
            a.DateFrom = NeedDate(body, "date_from");
            a.DateTo = GlReq.OptDate(GlReq.Field(body, "date_to"), "date_to");
            Order(a.DateFrom, a.DateTo);
            a.Unverified = Bool(body, "include_unverified", false);
            a.Limit = Int(body, "limit", 500, 1, 2000);
            return a;
        }

        public static ChurnArgs Churn(Dictionary<string, object> body)
        {
            ChurnArgs a = new ChurnArgs();
            a.DateFrom = NeedDate(body, "date_from");
            a.DateTo = GlReq.OptDate(GlReq.Field(body, "date_to"), "date_to");
            Order(a.DateFrom, a.DateTo);
            a.Lookback = Int(body, "lookback_months", 6, 1, 24);
            a.DeclinePct = Int(body, "decline_pct", 30, 1, 99);
            a.Limit = Int(body, "limit", 50, 1, 200);
            return a;
        }

        // 起止日期都给了才比较；date_to 缺省（登录日期）的比较在处理函数里做。
        internal static void Order(string from, string to)
        {
            if (from.Length > 0 && to.Length > 0 && string.CompareOrdinal(from, to) > 0)
            {
                throw GlReq.Bad("date_from 不能晚于 date_to", "date_from");
            }
        }

        static string NeedDate(Dictionary<string, object> body, string key)
        {
            string date = GlReq.OptDate(GlReq.Field(body, key), key);
            if (date.Length == 0)
            {
                throw GlReq.Bad("缺少字段 " + key, key);
            }
            return date;
        }

        static string Pick(Dictionary<string, object> body, string key, string fallback, string[] allowed)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return fallback;
            }
            string text = raw as string;
            if (text == null || Array.IndexOf(allowed, text) < 0)
            {
                throw GlReq.Bad(key + " 只能是 " + string.Join("、", allowed), key);
            }
            return text;
        }

        static int Int(Dictionary<string, object> body, string key, int fallback, int min, int max)
        {
            object raw = GlReq.Field(body, key);
            return raw == null ? fallback : GlReq.IntIn(raw, key, min, max);
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

        // 编码数组：缺省空；给了就是 1 到 20 个，每个去掉首尾空白后 1 到 60 个字符、不含控制字符；重复的只留一个。
        static string[] Codes(Dictionary<string, object> body, string key)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return new string[0];
            }
            string message = key + " 必须是 1 到 " + MaxCodes.ToString(CultureInfo.InvariantCulture)
                + " 个编码（每个 1 到 " + MaxCodeLength.ToString(CultureInfo.InvariantCulture) + " 个字符，不含控制字符）";
            IList list = AsList(raw);
            if (list == null || list.Count < 1 || list.Count > MaxCodes)
            {
                throw GlReq.Bad(message, key);
            }
            List<string> codes = new List<string>();
            for (int i = 0; i < list.Count; i++)
            {
                string text = Trimmed(list[i]);
                if (!Clean(text))
                {
                    throw GlReq.Bad(message, FieldPath.Item(key, i));
                }
                if (!codes.Contains(text))
                {
                    codes.Add(text);
                }
            }
            return codes.ToArray();
        }

        // JSON 数组读进来是 ArrayList 或 object[]。
        static IList AsList(object raw)
        {
            IList list = raw as ArrayList;
            return list != null ? list : raw as object[];
        }

        static string Trimmed(object item)
        {
            string text = item as string;
            return text == null ? "" : text.Trim();
        }

        static bool Clean(string text)
        {
            if (text.Length == 0 || text.Length > MaxCodeLength)
            {
                return false;
            }
            for (int i = 0; i < text.Length; i++)
            {
                if (char.IsControl(text[i]))
                {
                    return false;
                }
            }
            return true;
        }
    }
}
