using System;
using System.Collections;
using System.Collections.Generic;

namespace U8Co
{
    // 写入策略文件的字段读取：类型与取值范围不对就报错，消息带键路径。
    internal sealed partial class WritePolicySnapshot
    {
        // 未知键拒绝；"comment" 只能是字符串。
        static void CheckKeys(Dictionary<string, object> sec, string[] known, string at)
        {
            string prefix = at.Length == 0 ? "" : at + ".";
            foreach (KeyValuePair<string, object> pair in sec)
            {
                if (Array.IndexOf(known, pair.Key) < 0)
                {
                    throw Error("含未知字段 " + prefix + pair.Key + "；可用：" + string.Join(", ", known));
                }
                if (pair.Key == "comment" && !(pair.Value is string))
                {
                    throw Error(prefix + "comment 必须是字符串");
                }
            }
        }

        // 可选对象：缺少时当作空对象。
        static Dictionary<string, object> Section(Dictionary<string, object> map, string key, string at)
        {
            object raw;
            if (!map.TryGetValue(key, out raw))
            {
                return new Dictionary<string, object>();
            }
            return AsObject(raw, at);
        }

        static Dictionary<string, object> AsObject(object raw, string at)
        {
            Dictionary<string, object> sec = raw as Dictionary<string, object>;
            if (sec == null)
            {
                throw Error(at + " 必须是对象");
            }
            return sec;
        }

        static IList AsList(Dictionary<string, object> map, string key, string at)
        {
            IList list = map[key] as IList;
            if (list == null || map[key] is string)
            {
                throw Error(at + " 必须是数组");
            }
            return list;
        }

        // 可选字符串数组：缺少时为空数组；元素必须是非空字符串。
        static string[] Strings(Dictionary<string, object> map, string key, string at)
        {
            if (!map.ContainsKey(key))
            {
                return new string[0];
            }
            IList list = AsList(map, key, at);
            string[] values = new string[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                string item = list[i] as string;
                if (item == null || item.Trim().Length == 0)
                {
                    throw Error(at + " 只能含非空字符串");
                }
                values[i] = item.Trim();
            }
            return values;
        }

        static string[] Accounts(Dictionary<string, object> map, string key, string at)
        {
            string[] values = Strings(map, key, at);
            for (int i = 0; i < values.Length; i++)
            {
                if (!AccPattern.IsMatch(values[i]))
                {
                    throw Error(at + " 的每一项必须是 3 位数字的账套号：'" + values[i] + "'");
                }
            }
            return values;
        }

        static bool Bool(Dictionary<string, object> map, string key, string at)
        {
            object raw;
            if (!map.TryGetValue(key, out raw))
            {
                return false;
            }
            if (!(raw is bool))
            {
                throw Error(at + " 必须是 true 或 false");
            }
            return (bool)raw;
        }

        // 可选字符串：缺少时为 null，写了就必须是字符串（去首尾空白，空白当作没写）。
        static string Text(Dictionary<string, object> map, string key, string at)
        {
            object raw;
            if (!map.TryGetValue(key, out raw))
            {
                return null;
            }
            string text = raw as string;
            if (text == null)
            {
                throw Error(at + " 必须是字符串");
            }
            text = text.Trim();
            return text.Length == 0 ? null : text;
        }

        static string Need(Dictionary<string, object> map, string key, string at)
        {
            string text = Text(map, key, at);
            if (text == null)
            {
                throw Error("缺少 " + at);
            }
            return text;
        }

        static int Int(Dictionary<string, object> map, string key, string at, int fallback, int min, int max)
        {
            object raw;
            if (!map.TryGetValue(key, out raw))
            {
                return fallback;
            }
            if (!(raw is int) || (int)raw < min || (int)raw > max)
            {
                throw Error(at + " 必须是 " + min + " 到 " + max + " 的整数");
            }
            return (int)raw;
        }

        // 金额：非负数，0 表示不限。JSON 数字可能解析成 int、long、decimal 或 double。
        static decimal Amount(Dictionary<string, object> map, string key, string at, decimal fallback)
        {
            object raw;
            if (!map.TryGetValue(key, out raw))
            {
                return fallback;
            }
            decimal value = Number(raw, at);
            if (value < 0m || value > 1000000000000m)
            {
                throw Error(at + " 必须在 0 到 1000000000000 之间（0 表示不限）");
            }
            return value;
        }

        static decimal Number(object raw, string at)
        {
            if (raw is int || raw is long || raw is decimal)
            {
                return Convert.ToDecimal(raw);
            }
            if (!(raw is double))
            {
                throw Error(at + " 必须是数字");
            }
            double d = (double)raw;
            if (double.IsNaN(d) || double.IsInfinity(d) || Math.Abs(d) >= 1e15)
            {
                throw Error(at + " 必须在 0 到 1000000000000 之间（0 表示不限）");
            }
            return Convert.ToDecimal(d);
        }
    }
}
