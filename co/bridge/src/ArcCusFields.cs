using System;
using System.Collections.Generic;

namespace U8Co
{
    // 客户档案字段修改（archives/customer_fields）：受控 SQL 只改 Customer 上白名单里的列（声明表 ArcCusCols：
    // 简称、名称、税号、联系方式、银行、结算与协议、分类与行业、总公司、发展 / 停用日期、自定义项、对应供应商、标志位、扣率税率等），
    // 不走 EAI：客户 diffedit 会清银行账号、改最近收款日、补助记码，并拒无所属行业的客户。
    // fields 里只写要改的键，expect 核对当前值（空串表示当前为空）；响应的 before / after 与 409 的 detail.current 只含请求的键。
    internal static class ArcCusFields
    {
        public const string Op = "customer_fields";
        public const string Path = Requests.ArcRoot + Op;
        internal const string RuleKey = "write:archive:customer:customer_fields";
        internal const string Abbr = "abbrname";
        internal const string Name = "name";
        internal const string EndDate = "end_date";
        internal const string SeedDate = "seed_date";

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

        // 逐键校验并规整值：键必须在声明表里，值必须是字符串。expect 的值不满足写入规则时同样 400（永远对不上）。
        static Dictionary<string, string> Values(Dictionary<string, object> map, string at, bool expect)
        {
            Dictionary<string, string> result = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (KeyValuePair<string, object> pair in map)
            {
                string path = FieldPath.Join(at, pair.Key);
                ArcCusCol col = ArcCusCols.Of(pair.Key);
                if (col == null)
                {
                    throw ArcReq.Bad("未知字段 " + pair.Key + "；可用的键：" + string.Join(", ", ArcCusCols.Keys), path);
                }
                string text = pair.Value as string;
                if (text == null)
                {
                    throw ArcReq.Bad(path + " 必须是字符串", path);
                }
                result[pair.Key] = ArcCusValue.Parse(col, text, path, expect);
            }
            return result;
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

        // 409 state_mismatch 的 detail.current：请求 fields 里各键的当前值（按声明表顺序，空值是空串）。
        internal static Dictionary<string, object> CurrentOf(Dictionary<string, string> cur, Dictionary<string, string> fields)
        {
            return View(cur, fields);
        }

        // 响应里的对象：只含请求的键（固定顺序，空值是空串）。
        internal static Dictionary<string, object> View(Dictionary<string, string> values, Dictionary<string, string> fields)
        {
            Dictionary<string, object> view = new Dictionary<string, object>();
            for (int i = 0; i < ArcCusCols.All.Length; i++)
            {
                string key = ArcCusCols.All[i].Key;
                if (fields.ContainsKey(key))
                {
                    view[key] = values[key];
                }
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
            body["before"] = View(before, ask.Fields);
            body["after"] = View(wrote ? Merged(before, ask.Fields) : before, ask.Fields);
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
            body["before"] = View(before, ask.Fields);
            body["after"] = View(Merged(before, ask.Fields), ask.Fields);
            return body;
        }
    }

    internal sealed class ArcCusFieldsAsk
    {
        public string Code;
        // 要改的键 → 目标值（空串表示清空）；写库前由 ArcCusCheck 换成库里存的写法。
        public Dictionary<string, string> Fields;
        // 没有 expect 时为 null；键是 Fields 键的子集。
        public Dictionary<string, string> Expect;
        // 库里存的客户编码（锁行时读出；总公司留空即指向它）。
        public string Self;
        // 联动列要写的值：请求键 → 值（所属行业带出的行业名称）。
        public Dictionary<string, string> Pairs = new Dictionary<string, string>(StringComparer.Ordinal);
    }
}
