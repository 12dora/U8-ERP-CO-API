using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 生产类报表（ReportsMfgReq）的请求字段取值校验：整数、布尔、枚举、日期区间、编码数组、分类前缀。
    // 字段名由 Requests.ReportSpecs 把关，这里只校验取值，登录前抛 400。
    internal static class ReportsMfgParse
    {
        internal const int MaxCodes = 20;
        const int MaxText = 60;

        internal static int Int(Dictionary<string, object> body, string key, int fallback, int min, int max)
        {
            object raw = GlReq.Field(body, key);
            return raw == null ? fallback : GlReq.IntIn(raw, key, min, max);
        }

        internal static bool Bool(Dictionary<string, object> body, string key, bool fallback)
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

        // 枚举：只收 allowed 里的值，没给时取 fallback。
        internal static string Choice(Dictionary<string, object> body, string key, string fallback, string[] allowed)
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

        // 起止日期：date_from 必填，date_to 可选（空串表示取登录日期），date_to 不能早于 date_from。
        internal static void Range(Dictionary<string, object> body, MfgArgs m)
        {
            m.DateFrom = GlReq.OptDate(GlReq.Field(body, "date_from"), "date_from");
            if (m.DateFrom.Length == 0)
            {
                throw GlReq.Bad("缺少字段 date_from", "date_from");
            }
            m.DateTo = GlReq.OptDate(GlReq.Field(body, "date_to"), "date_to");
            if (m.DateTo.Length > 0 && string.CompareOrdinal(m.DateFrom, m.DateTo) > 0)
            {
                throw GlReq.Bad("date_from 不能晚于 date_to", "date_from");
            }
        }

        // 编码数组：没给为空数组；给了就是 1 到 20 个，每个去掉首尾空白后 1 到 60 个字符，不含控制字符。
        internal static string[] Codes(Dictionary<string, object> body, string key)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return new string[0];
            }
            string message = key + " 必须是 1 到 " + MaxCodes.ToString(CultureInfo.InvariantCulture)
                + " 个编码（每个 1 到 " + MaxText.ToString(CultureInfo.InvariantCulture) + " 个字符，不含控制字符）";
            IList list = raw as ArrayList;
            if (list == null)
            {
                list = raw as object[];
            }
            if (list == null || list.Count < 1 || list.Count > MaxCodes)
            {
                throw GlReq.Bad(message, key);
            }
            string[] codes = new string[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                string text = list[i] as string;
                text = text == null ? "" : text.Trim();
                if (!Clean(text))
                {
                    throw GlReq.Bad(message, FieldPath.Item(key, i));
                }
                codes[i] = text;
            }
            return codes;
        }

        // 存货分类编码前缀：只收字母、数字、点和减号（可以直接当 LIKE 前缀），没给为 null（不过滤）。
        internal static string Prefix(Dictionary<string, object> body, string key)
        {
            object raw = GlReq.Field(body, key);
            if (raw == null)
            {
                return null;
            }
            if (!ReportsReq.CodeChars(raw as string, 40))
            {
                throw GlReq.Bad(key + " 格式无效", key);
            }
            return (string)raw;
        }

        static bool Clean(string text)
        {
            if (text.Length == 0 || text.Length > MaxText)
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
