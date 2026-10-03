using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 一次带幂等键的请求：键、调用方和业务内容的摘要。存储键在入队前按账套和路径算出。
    internal sealed class IdemAsk
    {
        public string Key;
        public string Caller;
        public string BodySha;

        // caller + acc + route + idempotency_key 的 SHA-256，也是记录文件名。
        public string StoreId(string acc, string route)
        {
            string text = Caller + "\n" + (acc ?? "") + "\n" + (route ?? "") + "\n" + Key;
            return Crypto.Hex(Crypto.Sha256(Encoding.UTF8.GetBytes(text)));
        }
    }

    // 请求体里的 idempotency_key、caller：只在写路由上认（全部写路由，含旧版审核路由），校验后从请求体移除，再交给原有的字段校验。
    internal static class IdemReq
    {
        public const string KeyField = "idempotency_key";
        public const string CallerField = "caller";
        public const string DirectCaller = "direct";
        const int KeyLimit = 128;
        const int CallerLimit = 200;

        // 支持幂等键的路由：全部写路由，唯一来源是 WriteGate 的写路由表，不另列清单。
        // 读路由上带这两个字段照旧 400「含未知字段」。
        public static bool Supports(string path)
        {
            return WriteGate.IsWrite(path);
        }

        // 不支持的路由返回 null 且不动请求体，带了这两个字段照旧 400「含未知字段」。
        // 支持的路由：没带键返回 null（与以前相同，不做幂等）；caller 单独出现只校验不生效。
        public static IdemAsk Take(Dictionary<string, object> body, string path)
        {
            if (body == null || !Supports(path))
            {
                return null;
            }
            bool hasKey = body.ContainsKey(KeyField);
            bool hasCaller = body.ContainsKey(CallerField);
            if (hasKey)
            {
                // 自动核销试算 + 幂等键：400，在取走字段、算摘要之前。
                DryRunReq.RefusePlanIdem(body, path);
            }
            object key = hasKey ? body[KeyField] : null;
            object caller = hasCaller ? body[CallerField] : null;
            body.Remove(KeyField);
            body.Remove(CallerField);
            string who = hasCaller ? CheckCaller(caller) : DirectCaller;
            if (!hasKey)
            {
                return null;
            }
            IdemAsk ask = new IdemAsk();
            ask.Key = CheckKey(key);
            ask.Caller = who;
            ask.BodySha = BodySha(body);
            return ask;
        }

        public static string CheckKey(object value)
        {
            string text = value as string;
            if (text == null || text.Length == 0 || text.Length > KeyLimit)
            {
                throw new BridgeException(400, "bad_request", "idempotency_key 必须是 1 到 128 个可见 ASCII 字符");
            }
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] < '!' || text[i] > '~')
                {
                    throw new BridgeException(400, "bad_request", "idempotency_key 必须是 1 到 128 个可见 ASCII 字符");
                }
            }
            return text;
        }

        public static string CheckCaller(object value)
        {
            string text = value as string;
            if (text == null || text.Length == 0 || text.Length > CallerLimit)
            {
                throw new BridgeException(400, "bad_request", "caller 必须是 1 到 200 个字符");
            }
            for (int i = 0; i < text.Length; i++)
            {
                if (char.IsControl(text[i]))
                {
                    throw new BridgeException(400, "bad_request", "caller 不能含控制字符");
                }
            }
            return text;
        }

        // 业务内容摘要：去掉口令密文（每次加密都不同）和登录日期 date（只决定登录上下文，
        // 过了零点重发不应算作不同请求）后的规范 JSON。键按序数排序，数字按不变区域格式。
        public static string BodySha(Dictionary<string, object> body)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append('{');
            bool first = true;
            foreach (string key in SortedKeys(body))
            {
                if (key == "password_enc" || key == "date" || key == KeyField || key == CallerField)
                {
                    continue;
                }
                if (!first)
                {
                    sb.Append(',');
                }
                first = false;
                AppendString(sb, key);
                sb.Append(':');
                AppendValue(sb, body[key]);
            }
            sb.Append('}');
            return Crypto.Hex(Crypto.Sha256(Encoding.UTF8.GetBytes(sb.ToString())));
        }

        static List<string> SortedKeys(Dictionary<string, object> map)
        {
            List<string> keys = new List<string>(map.Keys);
            keys.Sort(StringComparer.Ordinal);
            return keys;
        }

        static void AppendValue(StringBuilder sb, object value)
        {
            if (value == null)
            {
                sb.Append("null");
                return;
            }
            if (value is bool)
            {
                sb.Append((bool)value ? "true" : "false");
                return;
            }
            string text = value as string;
            if (text != null)
            {
                AppendString(sb, text);
                return;
            }
            Dictionary<string, object> map = value as Dictionary<string, object>;
            if (map != null)
            {
                AppendMap(sb, map);
                return;
            }
            IEnumerable list = value as IEnumerable;
            if (list != null)
            {
                AppendList(sb, list);
                return;
            }
            sb.Append(Number(value));
        }

        static void AppendMap(StringBuilder sb, Dictionary<string, object> map)
        {
            sb.Append('{');
            bool first = true;
            foreach (string key in SortedKeys(map))
            {
                if (!first)
                {
                    sb.Append(',');
                }
                first = false;
                AppendString(sb, key);
                sb.Append(':');
                AppendValue(sb, map[key]);
            }
            sb.Append('}');
        }

        static void AppendList(StringBuilder sb, IEnumerable list)
        {
            sb.Append('[');
            bool first = true;
            foreach (object item in list)
            {
                if (!first)
                {
                    sb.Append(',');
                }
                first = false;
                AppendValue(sb, item);
            }
            sb.Append(']');
        }

        // JavaScriptSerializer 只给 int、long、decimal、double。其他类型不该出现，按 400 处理。
        static string Number(object value)
        {
            if (value is int || value is long || value is decimal)
            {
                return Convert.ToString(value, CultureInfo.InvariantCulture);
            }
            if (value is double)
            {
                return ((double)value).ToString("R", CultureInfo.InvariantCulture);
            }
            throw new BridgeException(400, "bad_request", "请求体含无法识别的值");
        }

        static void AppendString(StringBuilder sb, string text)
        {
            sb.Append('"');
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (c == '"' || c == '\\')
                {
                    sb.Append('\\').Append(c);
                }
                else if (c < ' ')
                {
                    sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                }
                else
                {
                    sb.Append(c);
                }
            }
            sb.Append('"');
        }
    }
}
