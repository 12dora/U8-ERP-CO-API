using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // 采购类报表（purchase_analysis、arrival_otif）的参数。字段名由 Requests.ReportSpecs（Specs）把关，
    // 这里只校验取值，登录前抛 400；处理函数里再解析一次。
    internal sealed class PuArgs
    {
        // 字段权限会遮金额：不按金额排序、截断（Run 在查询前定，见 PermMask.WillMaskAmounts）。
        public bool NoAmountOrder;
        public string DateFrom = "";
        // 空串：采购综合统计取登录日期，到货及时率不设上限。
        public string DateTo = "";
        public string[] Inv = new string[0];
        public string[] Vendor = new string[0];
        public string InvClass = "";
        // purchase_analysis：分组维度（material、vendor、month，1 到 3 个）和数据来源（false 发票，true 入库单）。
        public string[] GroupBy = new string[0];
        public bool Receipt;
        public int Limit = 200;
        // arrival_otif：分组（vendor、material、none）和逾期明细行数。
        public string Group = "vendor";
        public int OverdueLimit = 50;
    }

    internal static class ReportsPuReq
    {
        internal const string Purchase = "purchase_analysis";
        internal const string Arrival = "arrival_otif";

        internal static readonly string[] Names = new string[] { Purchase, Arrival };
        internal static readonly string[][] Specs = new string[][]
        {
            new string[]
            {
                Requests.ReportRoot + Purchase, "date_from", "date_to", "group_by", "inv", "inv_class", "vendor",
                "source", "limit"
            },
            new string[]
            {
                Requests.ReportRoot + Arrival, "date_from", "date_to", "vendor", "inv", "group_by", "overdue_limit"
            }
        };
        static readonly string[] Dims = new string[] { "material", "vendor", "month" };
        static readonly string[] Groups = new string[] { "vendor", "material", "none" };

        internal static bool Owns(string name)
        {
            return Array.IndexOf(Names, name) >= 0;
        }

        // 登录前校验，只抛 400。
        internal static void Check(string name, Dictionary<string, object> body)
        {
            if (name == Purchase)
            {
                ParsePurchase(body);
            }
            else if (name == Arrival)
            {
                ParseArrival(body);
            }
        }

        // 两张报表都按采购管理（PU）登录。不是这一组的报表返回 null。
        internal static string SubOf(string name, Dictionary<string, object> body)
        {
            return Owns(name) ? "PU" : null;
        }

        internal static void AddRunners(Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>> map)
        {
            map.Add(Purchase, ReportsPuPurchase.Run);
            map.Add(Arrival, ReportsPuArrival.Run);
        }

        internal static PuArgs ParsePurchase(Dictionary<string, object> body)
        {
            PuArgs a = new PuArgs();
            Range(a, body);
            a.GroupBy = Dimensions(GlReq.Field(body, "group_by"));
            a.Inv = Codes(body, "inv");
            a.Vendor = Codes(body, "vendor");
            a.InvClass = Prefix(body, "inv_class");
            a.Receipt = Source(GlReq.Field(body, "source"));
            a.Limit = OptInt(body, "limit", 200, 1, 1000);
            return a;
        }

        internal static PuArgs ParseArrival(Dictionary<string, object> body)
        {
            PuArgs a = new PuArgs();
            Range(a, body);
            a.Vendor = Codes(body, "vendor");
            a.Inv = Codes(body, "inv");
            a.Group = Group(GlReq.Field(body, "group_by"));
            a.OverdueLimit = OptInt(body, "overdue_limit", 50, 1, 200);
            return a;
        }

        static void Range(PuArgs a, Dictionary<string, object> body)
        {
            a.DateFrom = GlReq.OptDate(GlReq.Field(body, "date_from"), "date_from");
            if (a.DateFrom.Length == 0)
            {
                throw GlReq.Bad("缺少字段 date_from", "date_from");
            }
            a.DateTo = GlReq.OptDate(GlReq.Field(body, "date_to"), "date_to");
            if (a.DateTo.Length > 0 && string.CompareOrdinal(a.DateFrom, a.DateTo) > 0)
            {
                throw GlReq.Bad("date_from 不能晚于 date_to", "date_from");
            }
        }

        // group_by：1 到 3 个不重复的维度，缺省按物料。
        static string[] Dimensions(object raw)
        {
            if (raw == null)
            {
                return new string[] { "material" };
            }
            const string message = "group_by 必须是 1 到 3 个不重复的维度（material、vendor、month）";
            IList list = AsList(raw);
            if (list == null || list.Count < 1 || list.Count > Dims.Length)
            {
                throw GlReq.Bad(message, "group_by");
            }
            string[] dims = new string[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                string text = list[i] as string;
                if (text == null || Array.IndexOf(Dims, text) < 0 || Array.IndexOf(dims, text) >= 0)
                {
                    throw GlReq.Bad(message, FieldPath.Item("group_by", i));
                }
                dims[i] = text;
            }
            return dims;
        }

        static string Group(object raw)
        {
            if (raw == null)
            {
                return "vendor";
            }
            string text = raw as string;
            if (text == null || Array.IndexOf(Groups, text) < 0)
            {
                throw GlReq.Bad("group_by 只能是 vendor、material 或 none", "group_by");
            }
            return text;
        }

        static bool Source(object raw)
        {
            if (raw == null)
            {
                return false;
            }
            string text = raw as string;
            if (text != "invoice" && text != "receipt")
            {
                throw GlReq.Bad("source 只能是 invoice 或 receipt", "source");
            }
            return text == "receipt";
        }

        // 编码数组：1 到 20 个，每个去掉首尾空白后 1 到 60 个字符，不含控制字符。
        static string[] Codes(Dictionary<string, object> body, string key)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return new string[0];
            }
            string message = key + " 必须是 1 到 20 个编码（每个 1 到 60 个字符，不含控制字符）";
            IList list = AsList(raw);
            if (list == null || list.Count < 1 || list.Count > 20)
            {
                throw GlReq.Bad(message, key);
            }
            string[] codes = new string[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                string text = list[i] as string;
                text = text == null ? "" : text.Trim();
                if (!Clean(text, 60))
                {
                    throw GlReq.Bad(message, FieldPath.Item(key, i));
                }
                codes[i] = text;
            }
            return codes;
        }

        // 存货分类编码前缀：只收字母、数字、点和减号，可以直接当 LIKE 前缀用。
        static string Prefix(Dictionary<string, object> body, string key)
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

        static bool Clean(string text, int max)
        {
            if (text == null || text.Length == 0 || text.Length > max)
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
