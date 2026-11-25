using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    internal static class StepIo
    {
        public static string Op(Dictionary<string, object> step)
        {
            object value;
            if (step == null || !step.TryGetValue("op", out value) || value == null)
            {
                return "";
            }
            return Convert.ToString(value);
        }

        public static bool Has(Dictionary<string, object> step, string key)
        {
            return step != null && step.ContainsKey(key) && step[key] != null;
        }

        public static bool Present(Dictionary<string, object> step, string key)
        {
            return step != null && step.ContainsKey(key);
        }

        public static string Req(Dictionary<string, object> step, string key)
        {
            string text = Opt(step, key);
            if (text == null || text.Length == 0)
            {
                throw new BridgeException(400, "bad_request", "缺少 " + key);
            }
            return text;
        }

        public static string Opt(Dictionary<string, object> step, string key)
        {
            if (step == null || !step.ContainsKey(key) || step[key] == null)
            {
                return null;
            }
            return Convert.ToString(step[key]);
        }

        public static object Raw(Dictionary<string, object> step, string key)
        {
            if (step == null || !step.ContainsKey(key))
            {
                return null;
            }
            return step[key];
        }

        public static ArrayList List(Dictionary<string, object> step, string key)
        {
            if (!Has(step, key))
            {
                return null;
            }
            ArrayList list = AsList(step[key]);
            if (list == null)
            {
                throw new BridgeException(400, "bad_request", key + " 必须是数组");
            }
            return list;
        }

        // JavaScriptSerializer 把 JSON 数组反序列化成 object[]，不是 ArrayList。
        public static ArrayList AsList(object value)
        {
            ArrayList list = value as ArrayList;
            if (list != null)
            {
                return list;
            }
            object[] items = value as object[];
            return items == null ? null : new ArrayList(items);
        }
    }

    internal static class Nums
    {
        public static int AsInt(object value)
        {
            if (value is bool)
            {
                throw new BridgeException(400, "bad_request", "需要整数");
            }
            if (value is int)
            {
                return (int)value;
            }
            if (value is long)
            {
                return LongInt((long)value);
            }
            if (value is decimal)
            {
                return DecInt((decimal)value);
            }
            throw new BridgeException(400, "bad_request", "需要整数");
        }

        static int LongInt(long value)
        {
            if (value < int.MinValue || value > int.MaxValue)
            {
                throw new BridgeException(400, "bad_request", "整数超出范围");
            }
            return (int)value;
        }

        static int DecInt(decimal value)
        {
            if (value != decimal.Truncate(value))
            {
                throw new BridgeException(400, "bad_request", "需要整数");
            }
            if (value < int.MinValue || value > int.MaxValue)
            {
                throw new BridgeException(400, "bad_request", "整数超出范围");
            }
            return (int)value;
        }

        public static string Invariant(object value)
        {
            IFormattable fmt = value as IFormattable;
            if (fmt == null)
            {
                return Convert.ToString(value);
            }
            return fmt.ToString(null, CultureInfo.InvariantCulture);
        }
    }
}
