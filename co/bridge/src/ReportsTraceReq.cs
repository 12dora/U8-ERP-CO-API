using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 订单执行 order_execution 的请求参数。ids 与 code / date_from / date_to / partner 二选一。
    internal sealed class ExecArgs
    {
        public string Type;
        public int[] Ids;
        public string Code;
        public string DateFrom;
        public string DateTo;
        public string Partner;
        public bool OnlyOpen;
        public string After;
        public int Limit;
    }

    // 单据追溯 doc_trace 的请求参数：起点单据、每个方向的跳数、方向、节点上限。
    internal sealed class TraceArgs
    {
        public string Type;
        public int Id;
        public int Depth;
        public string Direction;
        public int MaxNodes;
    }

    // 订单执行、单据追溯的请求参数。字段名由 Requests.ReportSpecs 把关；fiscal_year、after 的格式由
    // ReportsReq.Parse 统一校验，这里只校验其余取值，登录前抛 400。
    internal static class ReportsTraceReq
    {
        internal const string ExecName = "order_execution";
        internal const string TraceName = "doc_trace";
        internal const int MaxIds = 100;
        internal const int MaxDepth = 3;
        internal const int MaxNodes = 200;
        static readonly string[] ExecTypes = new string[] { "sale_order", "purchase_order" };
        static readonly string[] Directions = new string[] { "both", "up", "down" };

        public static bool Owns(string name)
        {
            return name == ExecName || name == TraceName;
        }

        public static void Check(string name, Dictionary<string, object> body)
        {
            if (name == ExecName)
            {
                ParseExec(body);
                return;
            }
            ParseTrace(body);
        }

        // 登录子系统取单据类型的 Kinds.SubId（同 vouchers/load）。类型已在登录前校验。
        public static string SubOf(Dictionary<string, object> body)
        {
            VoucherKind kind = Kinds.Find(GlReq.Field(body, "type") as string);
            return kind == null || string.IsNullOrEmpty(kind.SubId) ? "SA" : kind.SubId;
        }

        public static ExecArgs ParseExec(Dictionary<string, object> body)
        {
            ExecArgs a = new ExecArgs();
            a.Type = GlReq.Field(body, "type") as string;
            if (a.Type == null || Array.IndexOf(ExecTypes, a.Type) < 0)
            {
                throw GlReq.Bad("type 只能是 sale_order 或 purchase_order", "type");
            }
            a.Ids = IdList(GlReq.Field(body, "ids"));
            ParseFilters(a, body);
            a.OnlyOpen = OptBool(body, "only_open");
            a.After = GlReq.Field(body, "after") as string ?? "";
            a.Limit = OptInt(body, "limit", 200, 1, 1000);
            return a;
        }

        static void ParseFilters(ExecArgs a, Dictionary<string, object> body)
        {
            a.Code = OptText(body, "code", 30);
            a.DateFrom = GlReq.OptDate(GlReq.Field(body, "date_from"), "date_from");
            a.DateTo = GlReq.OptDate(GlReq.Field(body, "date_to"), "date_to");
            if (a.DateFrom.Length > 0 && a.DateTo.Length > 0 && string.CompareOrdinal(a.DateFrom, a.DateTo) > 0)
            {
                throw GlReq.Bad("date_from 不能晚于 date_to", "date_from");
            }
            a.Partner = OptText(body, "partner", 20);
            bool other = a.Code.Length > 0 || a.DateFrom.Length > 0 || a.DateTo.Length > 0 || a.Partner.Length > 0;
            if (a.Ids != null && other)
            {
                throw GlReq.Bad("ids 不能和 code、date_from、date_to、partner 一起用", "ids");
            }
        }

        public static TraceArgs ParseTrace(Dictionary<string, object> body)
        {
            TraceArgs a = new TraceArgs();
            a.Type = GlReq.Field(body, "type") as string;
            if (a.Type == null || ReportsTraceMap.Node(a.Type) == null)
            {
                throw GlReq.Bad("type 不支持追溯（可用：" + string.Join("、", ReportsTraceMap.KindNames()) + "）", "type");
            }
            object id = GlReq.Field(body, "id");
            if (id == null)
            {
                throw GlReq.Bad("缺少字段 id", "id");
            }
            a.Id = GlReq.IntIn(id, "id", 1, int.MaxValue);
            a.Depth = OptInt(body, "depth", MaxDepth, 1, MaxDepth);
            object direction = GlReq.Field(body, "direction");
            a.Direction = direction == null ? "both" : direction as string;
            if (a.Direction == null || Array.IndexOf(Directions, a.Direction) < 0)
            {
                throw GlReq.Bad("direction 只能是 both、up 或 down", "direction");
            }
            a.MaxNodes = OptInt(body, "max_nodes", MaxNodes, 1, MaxNodes);
            return a;
        }

        // 单据 id：1 到 100 个正整数（JSON 整数），重复的只算一次。
        static int[] IdList(object raw)
        {
            if (raw == null)
            {
                return null;
            }
            const string message = "ids 必须是 1 到 100 个正整数";
            IList list = raw as ArrayList;
            if (list == null)
            {
                list = raw as object[];
            }
            if (list == null || list.Count < 1 || list.Count > MaxIds)
            {
                throw GlReq.Bad(message, "ids");
            }
            List<int> ids = new List<int>();
            for (int i = 0; i < list.Count; i++)
            {
                int id = IdOf(list[i], message, FieldPath.Item("ids", i));
                if (!ids.Contains(id))
                {
                    ids.Add(id);
                }
            }
            return ids.ToArray();
        }

        // 严格的 JSON 整数，1 到 int.MaxValue；布尔、字符串、小数都不收。
        static int IdOf(object item, string message, string field)
        {
            if (!(item is int) && !(item is long))
            {
                throw GlReq.Bad(message, field);
            }
            long value = Convert.ToInt64(item, CultureInfo.InvariantCulture);
            if (value < 1 || value > int.MaxValue)
            {
                throw GlReq.Bad(message, field);
            }
            return (int)value;
        }

        static int OptInt(Dictionary<string, object> body, string key, int fallback, int min, int max)
        {
            object raw = GlReq.Field(body, key);
            return raw == null ? fallback : GlReq.IntIn(raw, key, min, max);
        }

        static bool OptBool(Dictionary<string, object> body, string key)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return false;
            }
            if (!(raw is bool))
            {
                throw GlReq.Bad(key + " 必须是 true 或 false", key);
            }
            return (bool)raw;
        }

        // 去掉首尾空白后 1 到 max 个字符，不含控制字符；没给时为空串。
        static string OptText(Dictionary<string, object> body, string key, int max)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return "";
            }
            string text = raw as string;
            text = text == null ? "" : text.Trim();
            bool bad = text.Length == 0 || text.Length > max;
            for (int i = 0; i < text.Length && !bad; i++)
            {
                bad = char.IsControl(text[i]);
            }
            if (bad)
            {
                throw GlReq.Bad(key + " 必须是 1 到 " + max.ToString(CultureInfo.InvariantCulture) + " 个字符，不含控制字符", key);
            }
            return text;
        }
    }
}
