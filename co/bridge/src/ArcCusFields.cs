using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 客户档案字段修改（archives/customer_fields）：受控 SQL 只改 Customer 的简称 cCusAbbName、地区分类 cDCCode、
    // 停用日期 dEndDate 三列（ArcCusFieldsSql）。不走 EAI：客户 diffedit 会清银行账号、改最近收款日、补助记码，
    // 并拒无所属行业的客户。fields 里只写要改的键，expect 核对当前值（空串表示当前为空）。
    internal static class ArcCusFields
    {
        public const string Op = "customer_fields";
        public const string Path = Requests.ArcRoot + Op;
        internal const string RuleKey = "write:archive:customer:customer_fields";
        internal const string Abbr = "abbrname";
        internal const string District = "district";
        internal const string EndDate = "end_date";
        // 固定顺序：校验、SQL、响应都按它。
        internal static readonly string[] Keys = new string[] { Abbr, District, EndDate };
        const int AbbrMax = 60;
        const int DistrictMax = 12;
        const string DateFormat = "yyyy-MM-dd";

        public static ArcCusFieldsAsk Parse(Dictionary<string, object> body)
        {
            ArcCusFieldsAsk ask = new ArcCusFieldsAsk();
            ask.Code = ArcReq.CheckCode(ArcCustomerRow.Text(body, "code"), "code");
            Dictionary<string, object> fields = MapOf(body, "fields", true);
            if (fields.Count == 0)
            {
                throw ArcReq.Bad("fields 至少要有一个键", "fields");
            }
            ask.Fields = Values(fields, "fields", false);
            Dictionary<string, object> expect = MapOf(body, "expect", false);
            if (expect != null)
            {
                ask.Expect = Values(expect, "expect", true);
                foreach (string key in ask.Expect.Keys)
                {
                    if (!ask.Fields.ContainsKey(key))
                    {
                        throw ArcReq.Bad("expect 的 " + key + " 不在 fields 里", FieldPath.Join("expect", key));
                    }
                }
            }
            return ask;
        }

        public static ApiResult Run(WorkContext ctx, string op)
        {
            if (ctx == null || ctx.Item == null || ctx.Item.Body == null)
            {
                throw new BridgeException(500, "internal", "档案请求缺少请求体");
            }
            ArcCusFieldsAsk ask = Parse(ctx.Item.Body);
            ArcCustomerRow.Permit(ctx, RuleKey, ask.Code);
            return ArcCusFieldsSql.Run(ctx, ask);
        }

        // 请求里的对象：required 为真时缺少即 400；可选的给 null 当没有。
        static Dictionary<string, object> MapOf(Dictionary<string, object> body, string key, bool required)
        {
            object raw = Requests.Field(body, key);
            if (raw == null)
            {
                if (required)
                {
                    throw ArcReq.Bad("缺少 " + key, key);
                }
                return null;
            }
            Dictionary<string, object> map = raw as Dictionary<string, object>;
            if (map == null)
            {
                throw ArcReq.Bad(key + " 必须是对象", key);
            }
            return map;
        }

        // 逐键校验并规整值：键必须在 Keys 里，值必须是字符串。expect 的值不满足写入规则时同样 400（永远对不上）。
        static Dictionary<string, string> Values(Dictionary<string, object> map, string at, bool expect)
        {
            Dictionary<string, string> result = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (KeyValuePair<string, object> pair in map)
            {
                string path = FieldPath.Join(at, pair.Key);
                if (Array.IndexOf(Keys, pair.Key) < 0)
                {
                    throw ArcReq.Bad("未知字段 " + pair.Key + "；可用的键：" + string.Join(", ", Keys), path);
                }
                string text = pair.Value as string;
                if (text == null)
                {
                    throw ArcReq.Bad(path + " 必须是字符串", path);
                }
                result[pair.Key] = Value(pair.Key, text, path, expect);
            }
            return result;
        }

        static string Value(string key, string text, string path, bool expect)
        {
            if (HasControl(text))
            {
                throw ArcReq.Bad(path + " 含有控制字符", path);
            }
            if (key == EndDate)
            {
                return Date(text, path);
            }
            if (key == Abbr)
            {
                return Abbreviation(text, path, expect);
            }
            return DistrictCode(text, path);
        }

        // 简称：前后空格去掉后 1–60 个字符（库列 NOT NULL）。expect 允许空串（对不上而已）。
        static string Abbreviation(string text, string path, bool expect)
        {
            string trimmed = text.Trim();
            if (trimmed.Length == 0 && !expect)
            {
                throw ArcReq.Bad(path + " 不能为空", path);
            }
            if (trimmed.Length > AbbrMax)
            {
                throw ArcReq.Bad(path + " 长度不能超过 " + AbbrMax.ToString(CultureInfo.InvariantCulture), path);
            }
            return trimmed;
        }

        // 地区分类编码：空串清空；非空 1–12 个字符、前后没有空格。是否存在、是否末级在事务里查。
        static string DistrictCode(string text, string path)
        {
            if (text.Length > DistrictMax)
            {
                throw ArcReq.Bad(path + " 长度不能超过 " + DistrictMax.ToString(CultureInfo.InvariantCulture), path);
            }
            if (text.Trim().Length != text.Length)
            {
                throw ArcReq.Bad(path + " 前后不能有空格", path);
            }
            return text;
        }

        // 停用日期：空串清空；否则严格 yyyy-MM-dd，且不早于 1900-01-01（datetime 列的合理下限）。
        static string Date(string text, string path)
        {
            if (text.Length == 0)
            {
                return text;
            }
            DateTime day;
            if (!DateTime.TryParseExact(text, DateFormat, CultureInfo.InvariantCulture, DateTimeStyles.None, out day)
                || day.Year < 1900)
            {
                throw ArcReq.Bad(path + " 必须是 yyyy-MM-dd 日期或空串", path);
            }
            return text;
        }

        static bool HasControl(string text)
        {
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (c < ' ' || c == '\x7f')
                {
                    return true;
                }
            }
            return false;
        }

        // 把 cur 里被 fields 覆盖的键换成目标值（没给的键保持当前值）。
        internal static Dictionary<string, string> Merged(Dictionary<string, string> cur, Dictionary<string, string> fields)
        {
            Dictionary<string, string> merged = new Dictionary<string, string>(cur, StringComparer.Ordinal);
            foreach (KeyValuePair<string, string> pair in fields)
            {
                merged[pair.Key] = pair.Value;
            }
            return merged;
        }

        // 有任何一个请求的键与当前值不同才需要写。
        internal static bool Differs(Dictionary<string, string> cur, Dictionary<string, string> fields)
        {
            foreach (KeyValuePair<string, string> pair in fields)
            {
                if (!string.Equals(cur[pair.Key], pair.Value, StringComparison.Ordinal))
                {
                    return true;
                }
            }
            return false;
        }

        // 响应里的三键对象（固定顺序，空值是空串）。
        internal static Dictionary<string, object> View(Dictionary<string, string> values)
        {
            Dictionary<string, object> view = new Dictionary<string, object>();
            for (int i = 0; i < Keys.Length; i++)
            {
                view[Keys[i]] = values[Keys[i]];
            }
            return view;
        }

        internal static Dictionary<string, object> OkBody(
            ArcCusFieldsAsk ask, Dictionary<string, string> before, bool wrote)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["code"] = ask.Code;
            body["changed"] = wrote;
            body["before"] = View(before);
            body["after"] = View(wrote ? Merged(before, ask.Fields) : before);
            return body;
        }

        // 预演：after 是写入后将有的值，changed 表示真写入时是否会改。
        internal static Dictionary<string, object> DryBody(ArcCusFieldsAsk ask, Dictionary<string, string> before)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["dry_run"] = true;
            body["mode"] = DryRunModes.Validate;
            body["code"] = ask.Code;
            body["changed"] = Differs(before, ask.Fields);
            body["before"] = View(before);
            body["after"] = View(Merged(before, ask.Fields));
            return body;
        }
    }

    internal sealed class ArcCusFieldsAsk
    {
        public string Code;
        // 要改的键 → 目标值（空串表示清空）。
        public Dictionary<string, string> Fields;
        // 没有 expect 时为 null；键是 Fields 键的子集。
        public Dictionary<string, string> Expect;
    }
}
