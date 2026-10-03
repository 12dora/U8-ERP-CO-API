using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 库存与销售支持报表（stock_ledger、stock_summary、position_stock、batch_stock、customer_credit、price_list）的参数。
    // 字段名由 Requests.ReportSpecs 把关，这里只校验取值，登录前抛 400；处理函数里再解析一次。
    internal sealed class StockReportArgs
    {
        public string Inv = "";
        public string Wh = "";
        public string Batch = "";
        public string InvClass = "";
        public string Position = "";
        public string DateFrom = "";
        // 空串表示取登录日期（date）。
        public string DateTo = "";
        public string ExpiringBefore = "";
        public bool Unverified;
        public bool ByWh = true;
        public bool NonZero = true;
        public bool ControlledOnly;
        public string[] Customers = new string[0];
        // price_list：customer 客户价格表、inventory 存货价格表、vendor 供应商存货价格表。
        public string Kind = "";
        public string Customer = "";
        public string Vendor = "";
        // 空串表示取登录日期；AllDates 为 true 时不按日期过滤。
        public string AsOf = "";
        public bool AllDates;
    }

    internal static class ReportsStockReq
    {
        internal static readonly string[] Names = new string[]
        {
            "stock_ledger", "stock_summary", "position_stock", "batch_stock", "customer_credit", "price_list"
        };
        static readonly string[] PriceKinds = new string[] { "customer", "inventory", "vendor" };

        public static bool Owns(string name)
        {
            return Array.IndexOf(Names, name) >= 0;
        }

        // 登录子系统：库存类用 ST，客户信用和销售价格用 SA，供应商价格用 PU。不是这一组的报表返回 null。
        public static string SubOf(string name, Dictionary<string, object> body)
        {
            if (!Owns(name))
            {
                return null;
            }
            if (name == "customer_credit")
            {
                return "SA";
            }
            if (name == "price_list")
            {
                return (Requests.Field(body, "kind") as string) == "vendor" ? "PU" : "SA";
            }
            return "ST";
        }

        public static StockReportArgs Parse(ReportArgs a, Dictionary<string, object> body)
        {
            StockReportArgs s = new StockReportArgs();
            bool credit = a.Name == "customer_credit";
            a.Limit = credit ? OptInt(body, "limit", 100, 1, 200) : OptInt(body, "limit", 200, 1, 1000);
            switch (a.Name)
            {
                case "stock_ledger":
                    s.Inv = NeedText(body, "inv", 60);
                    s.Wh = OptText(body, "wh", 60);
                    s.Batch = OptText(body, "batch", 60);
                    s.Unverified = OptBool(body, "include_unverified", false);
                    Range(s, body);
                    break;
                case "stock_summary":
                    ParseSummary(s, body);
                    break;
                case "position_stock":
                case "batch_stock":
                    ParseStock(a.Name, s, body);
                    break;
                case "customer_credit":
                    s.Customers = CodeList(GlReq.Field(body, "customer"));
                    s.ControlledOnly = OptBool(body, "controlled_only", false);
                    break;
                default:
                    ParsePrice(s, body);
                    break;
            }
            return s;
        }

        static void Range(StockReportArgs s, Dictionary<string, object> body)
        {
            s.DateFrom = GlReq.OptDate(GlReq.Field(body, "date_from"), "date_from");
            if (s.DateFrom.Length == 0)
            {
                throw GlReq.Bad("缺少字段 date_from", "date_from");
            }
            s.DateTo = GlReq.OptDate(GlReq.Field(body, "date_to"), "date_to");
            if (s.DateTo.Length > 0 && string.CompareOrdinal(s.DateFrom, s.DateTo) > 0)
            {
                throw GlReq.Bad("date_from 不能晚于 date_to", "date_from");
            }
        }

        static void ParseSummary(StockReportArgs s, Dictionary<string, object> body)
        {
            Range(s, body);
            s.Wh = OptText(body, "wh", 60);
            s.Inv = OptText(body, "inv", 60);
            s.InvClass = OptPrefix(body, "inv_class");
            s.ByWh = OptBool(body, "by_wh", true);
            s.Unverified = OptBool(body, "include_unverified", false);
            s.NonZero = OptBool(body, "nonzero", true);
        }

        static void ParseStock(string name, StockReportArgs s, Dictionary<string, object> body)
        {
            s.Wh = OptText(body, "wh", 60);
            s.Inv = OptText(body, "inv", 60);
            s.Batch = OptText(body, "batch", 60);
            s.NonZero = OptBool(body, "nonzero", true);
            if (name == "position_stock")
            {
                s.Position = OptPrefix(body, "position");
                return;
            }
            s.ExpiringBefore = GlReq.OptDate(GlReq.Field(body, "expiring_before"), "expiring_before");
        }

        static void ParsePrice(StockReportArgs s, Dictionary<string, object> body)
        {
            s.Kind = GlReq.Field(body, "kind") as string;
            if (s.Kind == null || Array.IndexOf(PriceKinds, s.Kind) < 0)
            {
                throw GlReq.Bad("kind 只能是 customer、inventory 或 vendor", "kind");
            }
            s.Customer = OptText(body, "customer", 60);
            s.Vendor = OptText(body, "vendor", 60);
            if (s.Customer.Length > 0 && s.Kind != "customer")
            {
                throw GlReq.Bad("只有 kind=customer 才能带 customer", "customer");
            }
            if (s.Vendor.Length > 0 && s.Kind != "vendor")
            {
                throw GlReq.Bad("只有 kind=vendor 才能带 vendor", "vendor");
            }
            s.Inv = OptText(body, "inv", 60);
            s.AsOf = GlReq.OptDate(GlReq.Field(body, "as_of"), "as_of");
            s.AllDates = OptBool(body, "all_dates", false);
            if (s.AllDates && s.AsOf.Length > 0)
            {
                throw GlReq.Bad("as_of 不能和 all_dates 一起用", "as_of");
            }
        }

        // 客户编码：1 到 20 个，每个去掉首尾空白后 1 到 60 个字符，不含控制字符。
        static string[] CodeList(object raw)
        {
            if (raw == null)
            {
                return new string[0];
            }
            const string message = "customer 必须是 1 到 20 个客户编码（每个 1 到 60 个字符，不含控制字符）";
            IList list = raw as ArrayList;
            if (list == null)
            {
                list = raw as object[];
            }
            if (list == null || list.Count < 1 || list.Count > 20)
            {
                throw GlReq.Bad(message, "customer");
            }
            string[] codes = new string[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                string text = list[i] as string;
                text = text == null ? "" : text.Trim();
                if (!Clean(text, 60))
                {
                    throw GlReq.Bad(message, FieldPath.Item("customer", i));
                }
                codes[i] = text;
            }
            return codes;
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

        // 编码前缀（存货分类、货位）：只收字母、数字、点和减号，可以直接当 LIKE 前缀用。
        static string OptPrefix(Dictionary<string, object> body, string key)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return "";
            }
            if (!ReportsReq.CodeChars(raw as string, 40))
            {
                throw GlReq.Bad(key + " 格式无效", key);
            }
            return (string)raw;
        }

        static string OptText(Dictionary<string, object> body, string key, int max)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return "";
            }
            string text = raw as string;
            if (text == null || !Clean(text.Trim(), max) || text.Length > max)
            {
                throw GlReq.Bad(key + " 必须是 1 到 " + max.ToString(CultureInfo.InvariantCulture) + " 个字符，不含控制字符", key);
            }
            return text.Trim();
        }

        static string NeedText(Dictionary<string, object> body, string key, int max)
        {
            string text = OptText(body, key, max);
            if (text.Length == 0)
            {
                throw GlReq.Bad("缺少字段 " + key, key);
            }
            return text;
        }

        static bool Clean(string text, int max)
        {
            if (text.Length == 0 || text.Length > max)
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
