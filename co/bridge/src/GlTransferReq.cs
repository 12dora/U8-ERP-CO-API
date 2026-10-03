using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // gl/transfer/pnl、gl/transfer/custom的请求。Pnl 为真是期间损益结转（GL_bautotran.itype=30），否则自定义转账（itype=10）。
    internal sealed class GlTransferAsk
    {
        public bool Pnl;
        public int Year;
        public int Period;
        // 凭证日期；空串取该期间最后一天（U8 的结转凭证都是月末日期）。
        public string Date = "";
        // 只生成这一个自定义转账序号（ctran_id）；空串为全部定义。
        public string TranId = "";
        // 核对模式（只能和 dry_run 一起用）：余额里去掉本期已生成的同类结转凭证及其后的结转凭证，
        // 不查结账、未记账、重复生成三道闸门，用来与 U8 已生成的凭证逐行核对。
        public bool Exclude;

        public string OutSign
        {
            get { return Pnl ? GlTransferReq.PnlSign : GlTransferReq.CustomSign; }
        }

        public string Title()
        {
            return (Pnl ? "期间损益结转 " : "自定义转账 ") + GlTransferReq.Int(Year) + "年" + GlTransferReq.Int(Period) + "期"
                + (TranId.Length > 0 ? " " + TranId : "");
        }
    }

    // 登录前的字段校验（只抛 400）、锁键和常量。处理在 GlTransfer。第二级写入：只对测试账套开放（TestAccountGate）。
    internal static class GlTransferReq
    {
        public const string PnlPath = "/u8co/v1/gl/transfer/pnl";
        public const string CustomPath = "/u8co/v1/gl/transfer/custom";
        // U8 生成的结转凭证在 GL_accvouch.coutsign 上的标记（已在测试账套核对，不随摘要改动）。
        public const string PnlSign = "期间损益";
        public const string CustomSign = "自定义转账";
        // 期间损益结转的摘要：定义上没有摘要（cdigest 为空），U8 写这一句。
        public const string PnlDigest = "期间损益结转";
        public const string PnlTestOnly = "期间损益结转只对配置为测试账套的账套开放";
        public const string CustomTestOnly = "自定义转账只对配置为测试账套的账套开放";
        public const string Sub = "GL";
        const int TranIdMax = 20;

        // 登录前的字段名单（RequestsP4 的 P4Specs）：dry_run、幂等键在字段校验前已取走。
        internal static readonly string[] PnlSpec = new string[]
        {
            PnlPath, "fiscal_year", "period", "voucher_date", "exclude_existing"
        };
        internal static readonly string[] CustomSpec = new string[]
        {
            CustomPath, "fiscal_year", "period", "voucher_date", "tran_id", "exclude_existing"
        };
        // 新凭证编号锁：凭证类别在定义里，入队前不知道，常见类别都锁（同应收应付制单没给 sign 时）。
        static readonly string[] Signs = new string[] { "转", "记", "收", "付" };

        public static bool IsPath(string path)
        {
            return path == PnlPath || path == CustomPath;
        }

        // 审计 action。
        public static string ActionOf(string path)
        {
            return path == PnlPath ? "gl_transfer_pnl" : "gl_transfer_custom";
        }

        public static string TestOnlyOf(string path)
        {
            return path == PnlPath ? PnlTestOnly : CustomTestOnly;
        }

        // loginYear 为 0 时只校验形状（登录前）；登录后 fiscal_year 必须是登录日期的年份（凭证导入按登录年度编号）。
        public static GlTransferAsk Parse(string path, Dictionary<string, object> body, bool dry, int loginYear)
        {
            GlTransferAsk ask = new GlTransferAsk();
            ask.Pnl = path == PnlPath;
            ask.Year = GlReq.IntIn(Need(body, "fiscal_year"), "fiscal_year", 1900, 9999);
            ask.Period = GlReq.IntIn(Need(body, "period"), "period", 1, 12);
            ask.Date = GlReq.OptDate(GlReq.Field(body, "voucher_date"), "voucher_date");
            ask.TranId = ask.Pnl ? "" : TranId(GlReq.Field(body, "tran_id"));
            ask.Exclude = ListArgs.OptBool(body, "exclude_existing", "exclude_existing");
            if (ask.Exclude && !dry)
            {
                throw GlReq.Bad("exclude_existing 只能和 dry_run 一起用（只核对，不生成）", "exclude_existing");
            }
            if (ask.Date.Length > 0 && !ask.Date.StartsWith(Int(ask.Year) + "-" + ask.Period.ToString("00", CultureInfo.InvariantCulture) + "-",
                StringComparison.Ordinal))
            {
                throw GlReq.Bad("voucher_date 必须在 " + Int(ask.Year) + " 年第 " + Int(ask.Period) + " 期内", "voucher_date");
            }
            if (loginYear > 0 && ask.Year != loginYear)
            {
                throw GlReq.Bad("fiscal_year 必须是登录日期 date 的年份 " + Int(loginYear), "fiscal_year");
            }
            return ask;
        }

        static object Need(Dictionary<string, object> body, string name)
        {
            object value = GlReq.Field(body, name);
            if (value == null)
            {
                throw GlReq.Bad("缺少 " + name, name);
            }
            return value;
        }

        static string TranId(object value)
        {
            if (value == null)
            {
                return "";
            }
            string text = value as string;
            text = text == null ? "" : text.Trim();
            if (text.Length == 0 || text.Length > TranIdMax || !Plain(text))
            {
                throw GlReq.Bad("tran_id 必须是 1 到 " + Int(TranIdMax) + " 个字母或数字（U8 自定义转账的转账序号）", "tran_id");
            }
            return text;
        }

        static bool Plain(string text)
        {
            for (int i = 0; i < text.Length; i++)
            {
                if (!char.IsLetterOrDigit(text[i]))
                {
                    return false;
                }
            }
            return true;
        }

        // 该期间（自然月）的最后一天。
        public static string LastDay(int year, int period)
        {
            return new DateTime(year, period, DateTime.DaysInMonth(year, period)).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        }

        // 锁键："gl:transfer:<年度>-<期间>"（同月的期间损益与自定义转账串行）、"gl:post"（闸门读记账状态，与记账、取消记账互斥）、
        // "period:gl"（闸门读结账状态，与总账结账、取消结账互斥）、常见类别的新凭证编号 "new:gl:<类别>"（与 gl/vouchers/create 互斥）。
        public static string[] LockKeys(Dictionary<string, object> body)
        {
            List<string> keys = new List<string>();
            keys.Add("gl:transfer:" + Requests.KeyPart(Requests.Field(body, "fiscal_year")) + "-"
                + Requests.KeyPart(Requests.Field(body, "period")));
            keys.Add("gl:post");
            keys.Add("period:gl");
            foreach (string sign in Signs)
            {
                keys.Add("new:gl:" + sign);
            }
            return keys.ToArray();
        }

        internal static string Int(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
