using System;
using System.Collections.Generic;

namespace U8Co
{
    // 客户收货地址（archives/customer_address）：只改 CusDeliverAdd 里已有的一行（编码、地址名称、收货单位、默认标志）。
    // U8 没有收货地址的组件，客户 EAI 又有副作用，所以走受控 SQL；客户表本身（修改人、修改日期）不动，和 U8 界面一致。
    // 该表没有 rowversion，expect 就是并发条件：要改的 name / unit / default 必须带当前值。
    internal static class ArcCusAddr
    {
        public const string Op = "customer_address";
        public const string Path = Requests.ArcRoot + Op;
        internal const string RuleKey = "write:archive:customer:customer_address";
        const int CodeMax = 30;
        const int TextMax = 255;

        public static ApiResult Run(WorkContext ctx, string op)
        {
            if (ctx == null || ctx.Item == null || ctx.Item.Body == null)
            {
                throw new BridgeException(500, "internal", "档案请求缺少请求体");
            }
            ArcCusAddrAsk ask = Parse(ctx.Item.Body);
            ArcCustomerRow.Permit(ctx, RuleKey, ask.Code);
            return ArcCusAddrSql.Run(ctx, ask);
        }

        public static ArcCusAddrAsk Parse(Dictionary<string, object> body)
        {
            ArcCusAddrAsk ask = new ArcCusAddrAsk();
            ask.Code = ArcReq.CheckCode(ArcCustomerRow.Text(body, "code"), "code");
            ask.Address = ArcReq.CheckCode(ArcCustomerRow.Text(body, "address"), "address", CodeMax);
            Dictionary<string, object> fields = Obj(Requests.Field(body, "fields"), "fields");
            if (fields == null || fields.Count == 0)
            {
                throw ArcReq.Bad("没有要修改的字段", "fields");
            }
            ParseFields(ask, fields);
            ParseExpect(ask, Obj(Requests.Field(body, "expect"), "expect"));
            return ask;
        }

        static void ParseFields(ArcCusAddrAsk ask, Dictionary<string, object> fields)
        {
            foreach (KeyValuePair<string, object> pair in fields)
            {
                string at = "fields." + pair.Key;
                switch (pair.Key)
                {
                    case "code":
                        ask.NewCode = ArcReq.CheckCode(ArcCustomerRow.Text(fields, pair.Key), at, CodeMax);
                        break;
                    case "name":
                        ask.Name = Clean(ArcCustomerRow.Text(fields, pair.Key), at);
                        break;
                    case "unit":
                        ask.Unit = Clean(ArcCustomerRow.Text(fields, pair.Key), at);
                        break;
                    case "default":
                        ask.Default = Flag(pair.Value, at);
                        break;
                    default:
                        throw ArcReq.Bad("未知字段 " + pair.Key, FieldPath.Clean(at));
                }
            }
        }

        // expect 的键只能是要改的 name / unit / default；要改而没带的直接 400。code 不需要（address 就是当前编码）。
        static void ParseExpect(ArcCusAddrAsk ask, Dictionary<string, object> expect)
        {
            foreach (string key in Keys(expect))
            {
                if (!Wanted(ask, key))
                {
                    throw ArcReq.Bad("expect 里的 " + key + " 不在 fields 中或不支持", FieldPath.Clean("expect." + key));
                }
            }
            if (ask.Name != null)
            {
                ask.ExpName = ExpText(expect, "name");
            }
            if (ask.Unit != null)
            {
                ask.ExpUnit = ExpText(expect, "unit");
            }
            if (ask.Default.HasValue)
            {
                ask.ExpDefault = Flag(Needed(expect, "default"), "expect.default");
            }
        }

        static bool Wanted(ArcCusAddrAsk ask, string key)
        {
            switch (key)
            {
                case "name":
                    return ask.Name != null;
                case "unit":
                    return ask.Unit != null;
                case "default":
                    return ask.Default.HasValue;
                default:
                    return false;
            }
        }

        static IEnumerable<string> Keys(Dictionary<string, object> map)
        {
            return map == null ? new string[0] : (IEnumerable<string>)map.Keys;
        }

        static object Pick(Dictionary<string, object> map, string key)
        {
            object value;
            return map != null && map.TryGetValue(key, out value) ? value : null;
        }

        static object Needed(Dictionary<string, object> expect, string key)
        {
            object value = Pick(expect, key);
            if (value == null)
            {
                throw ArcReq.Bad("修改 " + key + " 必须在 expect 里带当前值（空用 \"\"）", "expect." + key);
            }
            return value;
        }

        static string ExpText(Dictionary<string, object> expect, string key)
        {
            Needed(expect, key);
            return ArcCustomerRow.Text(expect, key).Trim();
        }

        static Dictionary<string, object> Obj(object value, string label)
        {
            if (value == null)
            {
                return null;
            }
            Dictionary<string, object> map = value as Dictionary<string, object>;
            if (map == null)
            {
                throw ArcReq.Bad(label + " 必须是对象", FieldPath.Clean(label));
            }
            return map;
        }

        static bool Flag(object value, string label)
        {
            if (!(value is bool))
            {
                throw ArcReq.Bad(label + " 必须是布尔值", FieldPath.Clean(label));
            }
            return (bool)value;
        }

        // 地址名称、收货单位：字符串，去两端空格，"" 表示清空。
        static string Clean(string text, string label)
        {
            if (text == null)
            {
                throw ArcReq.Bad(label + " 不能为 null（要清空请用 \"\"）", FieldPath.Clean(label));
            }
            text = text.Trim();
            if (text.Length > TextMax)
            {
                throw ArcReq.Bad(label + " 长度不能超过 " + TextMax, FieldPath.Clean(label));
            }
            for (int i = 0; i < text.Length; i++)
            {
                if (text[i] < ' ' || text[i] == '\x7f')
                {
                    throw ArcReq.Bad(label + " 含有控制字符", FieldPath.Clean(label));
                }
            }
            return text;
        }

        internal static Dictionary<string, object> OkBody(ArcCusAddrAsk ask, ArcCusAddrPlan plan, bool wrote)
        {
            Dictionary<string, object> body = Head(ask, plan);
            body["changed"] = wrote;
            body["before"] = ArcCusAddrPlanner.Maps(plan.Before);
            body["after"] = ArcCusAddrPlanner.Maps(wrote ? plan.After : plan.Before);
            return body;
        }

        // 预演（validate）：全部检查已在事务里过完；changed 是「真写会不会改」，after 是计划中的改后各行。
        internal static Dictionary<string, object> DryBody(ArcCusAddrAsk ask, ArcCusAddrPlan plan)
        {
            Dictionary<string, object> body = Head(ask, plan);
            body["dry_run"] = true;
            body["mode"] = DryRunModes.Validate;
            body["changed"] = plan.Changed;
            body["before"] = ArcCusAddrPlanner.Maps(plan.Before);
            body["after"] = ArcCusAddrPlanner.Maps(plan.After);
            return body;
        }

        static Dictionary<string, object> Head(ArcCusAddrAsk ask, ArcCusAddrPlan plan)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["code"] = ask.Code;
            body["address"] = plan.Before[plan.Target].Code;
            body["new_address"] = plan.NewAddress;
            return body;
        }
    }
}
