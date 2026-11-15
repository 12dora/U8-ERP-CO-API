using System;
using System.Collections.Generic;

namespace U8Co
{
    // 请求。新增只用 Name、Unit、Default；删除只用 ExpName、ExpUnit、ExpDefault（三项都必带，它们就是并发条件）。
    internal sealed class ArcCusAddrRowsAsk
    {
        public string Code;
        public string Address;
        public string Name = "";
        public string Unit = "";
        public bool Default;
        public string ExpName = "";
        public string ExpUnit = "";
        public bool ExpDefault;
    }

    // 客户收货地址的新增与删除（archives/customer_address_add、archives/customer_address_delete）：和 customer_address
    // 一样只动 CusDeliverAdd，走受控 SQL；客户表本身（修改人、修改日期）不动，和 U8 界面一致。
    // 新增靠地址编码判重（同编码同内容重放视为已完成），删除靠 expect 做并发条件并拒绝仍被单据引用的地址。
    internal static class ArcCusAddrRows
    {
        public const string AddOp = "customer_address_add";
        public const string DeleteOp = "customer_address_delete";
        public const string AddPath = Requests.ArcRoot + AddOp;
        public const string DeletePath = Requests.ArcRoot + DeleteOp;
        public const string AddRuleKey = "write:archive:customer:customer_address_add";
        public const string DeleteRuleKey = "write:archive:customer:customer_address_delete";
        const int CodeMax = 30;

        public static bool Is(string op)
        {
            return op == AddOp || op == DeleteOp;
        }

        // 登录前校验，出错抛 400。
        public static void Check(string op, Dictionary<string, object> body)
        {
            if (op == AddOp)
            {
                ParseAdd(body);
            }
            else if (op == DeleteOp)
            {
                ParseDelete(body);
            }
            else
            {
                throw Unknown(op);
            }
        }

        public static ApiResult Run(WorkContext ctx, string op)
        {
            if (ctx == null || ctx.Item == null || ctx.Item.Body == null)
            {
                throw new BridgeException(500, "internal", "档案请求缺少请求体");
            }
            if (op == AddOp)
            {
                ArcCusAddrRowsAsk ask = ParseAdd(ctx.Item.Body);
                ArcCustomerRow.Permit(ctx, AddRuleKey, ask.Code);
                return ArcCusAddrRowsSql.RunAdd(ctx, ask);
            }
            if (op == DeleteOp)
            {
                ArcCusAddrRowsAsk ask = ParseDelete(ctx.Item.Body);
                ArcCustomerRow.Permit(ctx, DeleteRuleKey, ask.Code);
                return ArcCusAddrRowsSql.RunDelete(ctx, ask);
            }
            throw Unknown(op);
        }

        // 登记了却没有分支：开发期错误。
        static BridgeException Unknown(string op)
        {
            return new BridgeException(500, "internal", "未处理的收货地址路由 " + (op ?? ""));
        }

        // 新增：fields.name 必填（去两端空格后 1..255），unit 可选（"" 存 NULL），default 可选（缺省 false）。
        public static ArcCusAddrRowsAsk ParseAdd(Dictionary<string, object> body)
        {
            ArcCusAddrRowsAsk ask = ParseKey(body);
            Dictionary<string, object> fields = ArcCusAddr.Obj(Requests.Field(body, "fields"), "fields");
            if (fields == null || fields.Count == 0)
            {
                throw ArcReq.Bad("缺少 fields", "fields");
            }
            foreach (KeyValuePair<string, object> pair in fields)
            {
                string at = "fields." + pair.Key;
                switch (pair.Key)
                {
                    case "name":
                        ask.Name = ArcCusAddr.Clean(ArcCustomerRow.Text(fields, pair.Key), at);
                        break;
                    case "unit":
                        ask.Unit = ArcCusAddr.Clean(ArcCustomerRow.Text(fields, pair.Key), at);
                        break;
                    case "default":
                        ask.Default = ArcCusAddr.Flag(pair.Value, at);
                        break;
                    default:
                        throw ArcReq.Bad("未知字段 " + pair.Key, FieldPath.Clean(at));
                }
            }
            if (ask.Name.Length == 0)
            {
                throw ArcReq.Bad("缺少 fields.name（地址名称不能为空）", "fields.name");
            }
            return ask;
        }

        // 删除：expect 的 name / unit / default 三项都必带（空用 ""），别的键 400。
        public static ArcCusAddrRowsAsk ParseDelete(Dictionary<string, object> body)
        {
            ArcCusAddrRowsAsk ask = ParseKey(body);
            Dictionary<string, object> expect = ArcCusAddr.Obj(Requests.Field(body, "expect"), "expect");
            if (expect == null)
            {
                throw ArcReq.Bad("删除必须带 expect（name、unit、default 的当前值）", "expect");
            }
            foreach (string key in expect.Keys)
            {
                if (key != "name" && key != "unit" && key != "default")
                {
                    throw ArcReq.Bad("expect 里的 " + key + " 不支持", FieldPath.Clean("expect." + key));
                }
            }
            ask.ExpName = ExpText(expect, "name");
            ask.ExpUnit = ExpText(expect, "unit");
            ask.ExpDefault = ArcCusAddr.Flag(Needed(expect, "default"), "expect.default");
            return ask;
        }

        static ArcCusAddrRowsAsk ParseKey(Dictionary<string, object> body)
        {
            ArcCusAddrRowsAsk ask = new ArcCusAddrRowsAsk();
            ask.Code = ArcReq.CheckCode(ArcCustomerRow.Text(body, "code"), "code");
            ask.Address = ArcReq.CheckCode(ArcCustomerRow.Text(body, "address"), "address", CodeMax);
            return ask;
        }

        static object Needed(Dictionary<string, object> expect, string key)
        {
            object value;
            if (!expect.TryGetValue(key, out value) || value == null)
            {
                throw ArcReq.Bad("删除必须在 expect 里带 " + key + " 的当前值（空用 \"\"）", "expect." + key);
            }
            return value;
        }

        static string ExpText(Dictionary<string, object> expect, string key)
        {
            Needed(expect, key);
            return ArcCustomerRow.Text(expect, key).Trim();
        }

        internal static Dictionary<string, object> OkBody(ArcCusAddrRowsAsk ask, ArcCusAddrRowsPlan plan, bool wrote)
        {
            Dictionary<string, object> body = Head(ask, plan);
            body["changed"] = wrote;
            body["before"] = ArcCusAddrPlanner.Maps(plan.Before);
            body["after"] = ArcCusAddrPlanner.Maps(wrote ? plan.After : plan.Before);
            return body;
        }

        // 预演（validate）：全部检查已在事务里过完；changed 是「真写会不会改」，after 是计划中的改后各行。
        internal static Dictionary<string, object> DryBody(ArcCusAddrRowsAsk ask, ArcCusAddrRowsPlan plan)
        {
            Dictionary<string, object> body = Head(ask, plan);
            body["dry_run"] = true;
            body["mode"] = DryRunModes.Validate;
            body["changed"] = plan.Changed;
            body["before"] = ArcCusAddrPlanner.Maps(plan.Before);
            body["after"] = ArcCusAddrPlanner.Maps(plan.After);
            return body;
        }

        static Dictionary<string, object> Head(ArcCusAddrRowsAsk ask, ArcCusAddrRowsPlan plan)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["code"] = ask.Code;
            body["address"] = plan.Address;
            return body;
        }
    }
}
