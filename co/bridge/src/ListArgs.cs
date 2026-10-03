using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 列表请求的字段。入队前（ListRoutes.Check）和读线程上（Handle）各解析一次，规则相同。
    internal sealed class ListArgs
    {
        public const int DefaultLimit = 100;
        public const int MaxLimit = 500;
        const int TextMax = 60;

        public ListKind Kind;
        public int After;
        public int Limit;
        public bool KeysOnly;
        // rowversion 的十进制串；null 表示不按变更筛选。
        public string Since;
        // 已校验的筛选，按 ListSql.FilterOrder 的顺序取用。
        public Dictionary<string, object> Filters;
        public string Wh;
        public string Inv;
        public string Batch;
        public bool NonZero;

        public static ListArgs Vouchers(Dictionary<string, object> body)
        {
            ListArgs args = Paging(body);
            object type;
            body.TryGetValue("type", out type);
            string name = type as string;
            if (name == null)
            {
                throw new BridgeException(400, "bad_request", "type 必须是字符串");
            }
            args.Kind = ListKinds.Find(name);
            if (args.Kind == null)
            {
                throw new BridgeException(400, "bad_request", "不支持的列表类型 " + Clip(name));
            }
            args.KeysOnly = OptBool(body, "keys_only", "keys_only");
            args.Filters = ListSql.CheckFilters(args.Kind, OptMap(body, "filter"));
            return args;
        }

        public static ListArgs Stock(Dictionary<string, object> body)
        {
            ListArgs args = Paging(body);
            args.Wh = OptText(body, "wh", "wh");
            args.Inv = OptText(body, "inv", "inv");
            args.Batch = OptText(body, "batch", "batch");
            args.NonZero = OptBool(body, "nonzero", "nonzero");
            return args;
        }

        static ListArgs Paging(Dictionary<string, object> body)
        {
            if (body == null)
            {
                throw new BridgeException(400, "bad_request", "请求体为空");
            }
            ListArgs args = new ListArgs();
            args.After = OptInt(body, "after", 0, 0, int.MaxValue);
            args.Limit = OptInt(body, "limit", DefaultLimit, 1, MaxLimit);
            args.Since = OptSince(body);
            return args;
        }

        static int OptInt(Dictionary<string, object> body, string key, int fallback, int min, int max)
        {
            object raw;
            if (!body.TryGetValue(key, out raw) || raw == null)
            {
                return fallback;
            }
            long number;
            if (raw is int)
            {
                number = (int)raw;
            }
            else if (raw is long)
            {
                number = (long)raw;
            }
            else
            {
                number = long.MinValue;
            }
            if (number < min || number > max)
            {
                string range = min.ToString(CultureInfo.InvariantCulture) + " 到 "
                    + max.ToString(CultureInfo.InvariantCulture);
                throw new BridgeException(400, "bad_request", key + " 必须是 " + range + " 的整数");
            }
            return (int)number;
        }

        // changed_since：上一轮拿到的 watermark 或某行 ufts，十进制数字串（也收整数）。
        static string OptSince(Dictionary<string, object> body)
        {
            object raw;
            if (!body.TryGetValue("changed_since", out raw) || raw == null)
            {
                return null;
            }
            string text = raw as string;
            if (raw is int || raw is long)
            {
                text = Convert.ToInt64(raw, CultureInfo.InvariantCulture).ToString(CultureInfo.InvariantCulture);
            }
            long value;
            if (text == null || text.Length == 0 || text.Length > 19 || !AllDigits(text)
                || !long.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out value))
            {
                throw new BridgeException(400, "bad_request", "changed_since 必须是十进制数字串");
            }
            return value.ToString(CultureInfo.InvariantCulture);
        }

        static bool AllDigits(string text)
        {
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] < '0' || text[i] > '9')
                {
                    return false;
                }
            }
            return true;
        }

        public static bool OptBool(Dictionary<string, object> body, string key, string label)
        {
            object raw;
            if (body == null || !body.TryGetValue(key, out raw) || raw == null)
            {
                return false;
            }
            if (!(raw is bool))
            {
                throw new BridgeException(400, "bad_request", label + " 必须是布尔值");
            }
            return (bool)raw;
        }

        static Dictionary<string, object> OptMap(Dictionary<string, object> body, string key)
        {
            object raw;
            if (!body.TryGetValue(key, out raw) || raw == null)
            {
                return null;
            }
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw new BridgeException(400, "bad_request", key + " 必须是对象");
            }
            return map;
        }

        // 取一个可选的筛选串：1 到 60 个字符，不含控制字符。缺省或 null 返回 null。
        public static string OptText(Dictionary<string, object> body, string key, string label)
        {
            object raw;
            if (body == null || !body.TryGetValue(key, out raw) || raw == null)
            {
                return null;
            }
            string text = raw as string;
            if (text == null || text.Length == 0 || text.Length > TextMax || HasControl(text))
            {
                throw new BridgeException(400, "bad_request", label + " 必须是 1 到 60 个字符的字符串");
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

        public static string Clip(string text)
        {
            if (text == null)
            {
                return "";
            }
            return text.Length > 40 ? text.Substring(0, 40) : text;
        }
    }
}
