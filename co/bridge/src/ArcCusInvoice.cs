using System;
using System.Collections.Generic;

namespace U8Co
{
    // 请求。Default 只用于新增；ExpDefault 只用于删除、设默认（必带，是并发条件）；ExpTs 是可选的 expect.customer_ufts。
    internal sealed class ArcCusInvoiceAsk
    {
        public string Code;
        public string Customer;
        public string Company;
        public bool Default;
        public bool ExpDefault;
        public string ExpTs;
    }

    // 客户开票单位（sa_invoicecustomers）的新增、删除、设默认：archives/customer_invoice_company_add、
    // archives/customer_invoice_company_delete、archives/customer_invoice_company。和 customer_address_* 一样走受控 SQL：
    // U8 客户 EAI 的修改会按单据重写整张开票单位表（缺子表时只留一条自身默认行并删掉其余），所以不走 EAI。
    // 默认开票单位变化时同事务把 Customer.cInvoiceCompany 改成新默认（带 pubufts 乐观锁、修改人、修改日期）；不改任何单据。
    internal static class ArcCusInvoice
    {
        public const string AddOp = "customer_invoice_company_add";
        public const string DeleteOp = "customer_invoice_company_delete";
        public const string DefaultOp = "customer_invoice_company";
        public const string AddPath = Requests.ArcRoot + AddOp;
        public const string DeletePath = Requests.ArcRoot + DeleteOp;
        public const string DefaultPath = Requests.ArcRoot + DefaultOp;
        public const string AddRuleKey = "write:archive:customer:" + AddOp;
        public const string DeleteRuleKey = "write:archive:customer:" + DeleteOp;
        public const string DefaultRuleKey = "write:archive:customer:" + DefaultOp;
        const string PairKind = "customer_invoice_company";
        const int PairMax = 41;
        const string TsKey = "customer_ufts";
        const string DefaultKey = "bdefault";

        public static bool Is(string op)
        {
            return op == AddOp || op == DeleteOp || op == DefaultOp;
        }

        // 登录前校验，出错抛 400。
        public static void Check(string op, Dictionary<string, object> body)
        {
            Parse(op, body);
        }

        public static ApiResult Run(WorkContext ctx, string op)
        {
            if (ctx == null || ctx.Item == null || ctx.Item.Body == null)
            {
                throw new BridgeException(500, "internal", "档案请求缺少请求体");
            }
            ArcCusInvoiceAsk ask = Parse(op, ctx.Item.Body);
            Permit(ctx, RuleKey(op), ask);
            return ArcCusInvoiceSql.Run(ctx, op, ask);
        }

        static string RuleKey(string op)
        {
            return op == AddOp ? AddRuleKey : op == DeleteOp ? DeleteRuleKey : DefaultRuleKey;
        }

        // 功能权限 AS011 + 客户记录级权限：客户、开票单位两个编码都要有。
        static void Permit(WorkContext ctx, string ruleKey, ArcCusInvoiceAsk ask)
        {
            ArcCustomerRow.Permit(ctx, ruleKey, ask.Customer);
            if (!PermCheck.Allow(PermCheck.Of(ctx), PermObj.Customer, ask.Company))
            {
                throw new BridgeException(403, "no_permission", PermCheck.DeniedRow);
            }
        }

        // 锁键用客户段：编码 "<客户>:<开票单位>" 的第一段，和收货地址等客户路由同一把 arc:customer:<客户>。
        public static string LockCode(string code)
        {
            int at = code == null ? -1 : code.IndexOf(':');
            return at < 0 ? code : code.Substring(0, at);
        }

        // 新增：code、可选 default（缺省 false）、可选 expect.customer_ufts。删除、设默认：code、
        // expect.bdefault（必带）、可选 expect.customer_ufts。
        public static ArcCusInvoiceAsk Parse(string op, Dictionary<string, object> body)
        {
            ArcCusInvoiceAsk ask = new ArcCusInvoiceAsk();
            ask.Code = ArcReq.CheckCode(ArcCustomerRow.Text(body, "code"), "code", PairMax);
            string[] parts = ArcPair.Named(PairKind).Split(ask.Code, "code");
            ask.Customer = parts[0];
            ask.Company = parts[1];
            if (!Is(op))
            {
                throw new BridgeException(500, "internal", "未处理的开票单位路由 " + (op ?? ""));
            }
            if (op == AddOp)
            {
                object flag = Requests.Field(body, "default");
                ask.Default = flag != null && ArcCusAddr.Flag(flag, "default");
            }
            ParseExpect(body, ask, op != AddOp);
            return ask;
        }

        static void ParseExpect(Dictionary<string, object> body, ArcCusInvoiceAsk ask, bool needDefault)
        {
            Dictionary<string, object> expect = ArcCusAddr.Obj(Requests.Field(body, "expect"), "expect");
            if (expect == null)
            {
                if (needDefault)
                {
                    throw ArcReq.Bad("必须带 expect（bdefault 的当前值）", "expect");
                }
                return;
            }
            foreach (string key in expect.Keys)
            {
                if (key != TsKey && !(needDefault && key == DefaultKey))
                {
                    throw ArcReq.Bad("expect 里的 " + key + " 不支持", FieldPath.Clean("expect." + key));
                }
            }
            if (needDefault)
            {
                object flag;
                if (!expect.TryGetValue(DefaultKey, out flag) || flag == null)
                {
                    throw ArcReq.Bad("必须在 expect 里带 bdefault 的当前值", "expect.bdefault");
                }
                ask.ExpDefault = ArcCusAddr.Flag(flag, "expect.bdefault");
            }
            ask.ExpTs = ParseTs(expect);
        }

        // expect.customer_ufts：客户 pubufts 的十进制串（档案 get 返回的 ufts），1 到 19 位数字。
        static string ParseTs(Dictionary<string, object> expect)
        {
            object value;
            if (!expect.TryGetValue(TsKey, out value) || value == null)
            {
                return null;
            }
            string text = value as string;
            long number;
            if (text == null || text.Length == 0 || text.Length > 19 || !long.TryParse(text,
                System.Globalization.NumberStyles.None, System.Globalization.CultureInfo.InvariantCulture, out number))
            {
                throw ArcReq.Bad("expect.customer_ufts 必须是 1 到 19 位数字串", "expect.customer_ufts");
            }
            return text;
        }

        internal static Dictionary<string, object> OkBody(ArcCusInvoiceAsk ask, ArcCusInvoicePlan plan, bool wrote)
        {
            Dictionary<string, object> body = Head(ask);
            body["changed"] = wrote;
            body["before"] = ArcCusInvoicePlanner.Maps(plan.Before);
            body["after"] = ArcCusInvoicePlanner.Maps(wrote ? plan.After : plan.Before);
            return body;
        }

        // 预演（validate）：全部检查已在事务里过完；changed 是「真写会不会改」，after 是计划中的改后各行。
        internal static Dictionary<string, object> DryBody(ArcCusInvoiceAsk ask, ArcCusInvoicePlan plan)
        {
            Dictionary<string, object> body = Head(ask);
            body["dry_run"] = true;
            body["mode"] = DryRunModes.Validate;
            body["changed"] = plan.Changed;
            body["before"] = ArcCusInvoicePlanner.Maps(plan.Before);
            body["after"] = ArcCusInvoicePlanner.Maps(plan.After);
            return body;
        }

        static Dictionary<string, object> Head(ArcCusInvoiceAsk ask)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["ok"] = true;
            body["code"] = ask.Code;
            return body;
        }
    }
}
