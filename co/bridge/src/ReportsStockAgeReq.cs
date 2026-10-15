using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 库龄分析 stock_age 的参数。字段名由 Requests.ReportSpecs（Specs）把关，这里只校验取值，登录前抛 400；处理函数里再解析一次。
    internal sealed class StockAgeArgs
    {
        // 呆滞天数：这么多天没有出库的算呆滞。
        public int IdleDays = 180;
        // 库龄分段的上界（天，递增）；n 个上界分成 n + 1 段，最后一段没有上界。
        public int[] Buckets = new int[] { 30, 90, 180 };
        public string[] Wh = new string[0];
        public string[] Inv = new string[0];
        public string InvClass = "";
        public bool ByWh = true;
        // amount 按金额从大到小，age 按库龄从大到小。
        public string Sort = "amount";
        public int Limit = 200;
    }

    internal static class ReportsStockAgeReq
    {
        internal const string Name = "stock_age";
        internal static readonly string[] Names = new string[] { Name };
        internal static readonly string[][] Specs = new string[][]
        {
            new string[]
            {
                Requests.ReportRoot + Name, "idle_days", "buckets", "wh", "inv", "inv_class", "by_wh", "sort", "limit"
            }
        };
        const int MaxBuckets = 6;
        const int MaxDays = 3650;

        public static bool Owns(string name)
        {
            return Array.IndexOf(Names, name) >= 0;
        }

        // 登录前校验，只抛 400。不是本族的报表直接放过。
        public static void Check(string name, Dictionary<string, object> body)
        {
            if (Owns(name))
            {
                Parse(body);
            }
        }

        // 登录子系统：库存 ST。不是本族的报表返回 null。
        public static string SubOf(string name, Dictionary<string, object> body)
        {
            return Owns(name) ? "ST" : null;
        }

        public static void AddRunners(Dictionary<string, Func<WorkContext, ReportArgs, ApiResult>> map)
        {
            map.Add(Name, ReportsStockAge.Run);
        }

        public static StockAgeArgs Parse(Dictionary<string, object> body)
        {
            StockAgeArgs a = new StockAgeArgs();
            a.IdleDays = ReportsStockReq.OptInt(body, "idle_days", 180, 1, MaxDays);
            a.Buckets = BucketList(GlReq.Field(body, "buckets"));
            a.Wh = CodeList(body, "wh");
            a.Inv = CodeList(body, "inv");
            a.InvClass = ReportsStockReq.OptPrefix(body, "inv_class");
            a.ByWh = ReportsStockReq.OptBool(body, "by_wh", true);
            a.Sort = SortOf(GlReq.Field(body, "sort"));
            a.Limit = ReportsStockReq.OptInt(body, "limit", 200, 1, 2000);
            return a;
        }

        static string SortOf(object raw)
        {
            if (raw == null)
            {
                return "amount";
            }
            string text = raw as string;
            if (text != "amount" && text != "age")
            {
                throw GlReq.Bad("sort 只能是 amount 或 age", "sort");
            }
            return text;
        }

        // 1 到 6 个严格递增的天数（1 到 3650）；整数以外（布尔、字符串、小数）一律不收。
        static int[] BucketList(object raw)
        {
            if (raw == null)
            {
                return new int[] { 30, 90, 180 };
            }
            const string message = "buckets 必须是 1 到 6 个递增的天数（1 到 3650）";
            IList list = AsList(raw);
            if (list == null || list.Count < 1 || list.Count > MaxBuckets)
            {
                throw GlReq.Bad(message, "buckets");
            }
            int[] days = new int[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                long day = DayOf(list[i]);
                if (day < 1 || day > MaxDays || (i > 0 && day <= days[i - 1]))
                {
                    throw GlReq.Bad(message, FieldPath.Item("buckets", i));
                }
                days[i] = (int)day;
            }
            return days;
        }

        static long DayOf(object item)
        {
            if (item is int || item is long)
            {
                return Convert.ToInt64(item, CultureInfo.InvariantCulture);
            }
            return 0;
        }

        // 仓库、存货编码：1 到 20 个，每个去掉首尾空白后 1 到 60 个字符，不含控制字符。
        static string[] CodeList(Dictionary<string, object> body, string key)
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
                if (!ReportsStockReq.Clean(text, 60))
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
    }
}
