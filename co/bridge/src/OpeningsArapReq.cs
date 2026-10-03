using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // openings/arap 的请求：side（ar 应收 / ap 应付）、action（create / delete / verify / unverify）。
    // 新增收往来单位、金额、科目等；删除、审核、弃审只收 id。
    internal sealed class OpeningsArapAsk
    {
        public string Side;
        public string Action;
        public int Id;
        public string Partner;
        // 调用方给的金额（原币）：正数为正常方向余额，负数为反方向（预收 / 预付）。
        public decimal Amount;
        public string Account;
        public string Department;
        public string Person;
        public string Digest;
        // 空串表示本位币（登录后才知道）。
        public string Currency;
        // 0 表示没给。
        public decimal Rate;

        public bool Create
        {
            get { return Action == "create"; }
        }
    }

    // openings/arap（应收 / 应付期初单据）在登录前的校验（400）：不带 type；登录子系统按 side（AR / AP）。
    // 处理在 OpeningsArap；锁键与 ar_bill / ap_bill 相同（新增 "new:<类型>"，其余 "<类型>:<id>"），另持全局写闸门。
    internal static class OpeningsArapReq
    {
        internal const string Path = "/u8co/v1/openings/arap";
        // 审计 action：opening_arap_<action>（入队前的拒绝一律记 opening_arap）。
        public const string Action = "opening_arap";
        // 权限规则键（PermRegistryOpening）。
        public const string ArRule = "write:opening:ar";
        public const string ApRule = "write:opening:ap";
        const string SideHint = "side 只能是 ar（应收）或 ap（应付）";
        const string ActionHint = "action 只能是 create、delete、verify 或 unverify";
        const decimal AmountMax = 1000000000000m;
        const decimal RateMax = 1000000m;
        static readonly string[] CreateOnly = new string[]
        {
            "partner", "amount", "account", "department", "person", "digest", "currency", "exch_rate"
        };

        // 登录前的字段名单（RequestsP4 的 P4Specs）：dry_run、幂等键在字段校验前已取走。
        internal static readonly string[] Spec = new string[]
        {
            Path, "side", "action", "id", "partner", "amount", "account", "department", "person", "digest", "currency",
            "exch_rate"
        };

        public static OpeningsArapAsk Parse(Dictionary<string, object> body)
        {
            if (body == null)
            {
                body = new Dictionary<string, object>();
            }
            OpeningsArapAsk ask = new OpeningsArapAsk();
            ask.Side = Choice(body, "side", new string[] { "ar", "ap" }, SideHint);
            ask.Action = Choice(body, "action", new string[] { "create", "delete", "verify", "unverify" }, ActionHint);
            if (ask.Create)
            {
                if (body.ContainsKey("id"))
                {
                    throw BridgeException.BadField("id", "新增期初单据不收 id");
                }
                CreateFields(body, ask);
                return ask;
            }
            for (int i = 0; i < CreateOnly.Length; i++)
            {
                if (body.ContainsKey(CreateOnly[i]))
                {
                    throw BridgeException.BadField(CreateOnly[i], "action=" + ask.Action + " 只收 id，不收 " + CreateOnly[i]);
                }
            }
            ask.Id = Id(Requests.Field(body, "id"));
            return ask;
        }

        static void CreateFields(Dictionary<string, object> body, OpeningsArapAsk ask)
        {
            ask.Partner = Text(body, "partner", 20, true);
            ask.Amount = Amount(Requests.Field(body, "amount"));
            ask.Account = Text(body, "account", 40, true);
            ask.Department = Text(body, "department", 12, false);
            ask.Person = Text(body, "person", 20, false);
            ask.Digest = Text(body, "digest", 120, false);
            ask.Currency = Text(body, "currency", 8, false);
            object rate = Requests.Field(body, "exch_rate");
            if (rate != null)
            {
                ask.Rate = Rate(rate);
            }
        }

        static string Choice(Dictionary<string, object> body, string name, string[] allowed, string hint)
        {
            string value = Requests.Field(body, name) as string;
            if (value == null)
            {
                throw BridgeException.BadField(name, "缺少字段 " + name + " 或不是字符串").WithHint(hint);
            }
            if (Array.IndexOf(allowed, value) < 0)
            {
                throw BridgeException.BadField(name, hint);
            }
            return value;
        }

        // 字符串字段：去两端空格，空串视为没给。
        static string Text(Dictionary<string, object> body, string name, int max, bool required)
        {
            object raw = Requests.Field(body, name);
            if (raw != null && !(raw is string))
            {
                throw BridgeException.BadField(name, name + " 必须是字符串");
            }
            string text = raw == null ? "" : ((string)raw).Trim();
            if (text.Length == 0 && required)
            {
                throw BridgeException.BadField(name, "缺少字段 " + name);
            }
            if (text.Length > max)
            {
                throw BridgeException.BadField(name, name + " 不能超过 " + max.ToString(CultureInfo.InvariantCulture) + " 个字符");
            }
            return text;
        }

        // 金额：非 0、绝对值不超过 1000000000000、最多两位小数；负数表示反方向余额。
        internal static decimal Amount(object raw)
        {
            decimal value;
            if (raw == null)
            {
                throw BridgeException.BadField("amount", "缺少字段 amount");
            }
            if (!ArapWriteoffReq.TryNum(raw, out value) || value == 0 || Math.Abs(value) > AmountMax
                || decimal.Round(value, 2) != value)
            {
                throw BridgeException.BadField("amount", "amount 必须是非 0 的数、绝对值不超过 1000000000000、最多两位小数");
            }
            return value;
        }

        static decimal Rate(object raw)
        {
            decimal value;
            if (!ArapWriteoffReq.TryNum(raw, out value) || value <= 0 || value > RateMax)
            {
                throw BridgeException.BadField("exch_rate", "exch_rate 必须大于 0 且不超过 1000000");
            }
            return value;
        }

        static int Id(object raw)
        {
            if (raw == null)
            {
                throw BridgeException.BadField("id", "缺少单据 id");
            }
            if (!(raw is int) && !(raw is long))
            {
                throw BridgeException.BadField("id", "单据 id 必须是整数");
            }
            long value = Convert.ToInt64(raw, CultureInfo.InvariantCulture);
            if (value < 1 || value > int.MaxValue)
            {
                throw BridgeException.BadField("id", "单据 id 无效");
            }
            return (int)value;
        }

        // 登录子系统：ar → AR，ap → AP。
        public static string SubOf(OpeningsArapAsk ask)
        {
            return ask.Side.ToUpperInvariant();
        }

        // 对应的单据类型：期初单据就是表头带 bStartFlag=1 的应收单 / 应付单。
        public static string KindOf(OpeningsArapAsk ask)
        {
            return ask.Side == "ar" ? "ar_bill" : "ap_bill";
        }

        public static string AuditAction(OpeningsArapAsk ask)
        {
            return Action + "_" + ask.Action;
        }

        public static string RuleKey(OpeningsArapAsk ask)
        {
            return ask.Side == "ar" ? ArRule : ApRule;
        }

        // 与 ar_bill / ap_bill 共用锁键：新增与同类单据的编号串行，其余与同一张单据的写串行。
        // 请求体不合法时（登录前已 400）返回空。
        public static string[] LockKeys(Dictionary<string, object> body)
        {
            try
            {
                OpeningsArapAsk ask = Parse(body);
                string kind = KindOf(ask);
                if (ask.Create)
                {
                    return new string[] { "new:" + kind };
                }
                return new string[] { kind + ":" + ask.Id.ToString(CultureInfo.InvariantCulture) };
            }
            catch (BridgeException)
            {
                return new string[0];
            }
        }
    }
}
