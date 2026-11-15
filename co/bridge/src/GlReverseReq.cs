using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 红字冲销的请求：原凭证（fiscal_year、period、sign、no）和红字凭证日期 voucher_date（date 是登录日期，不能另作他用）。
    internal sealed class GlReverseAsk
    {
        // 原凭证的会计年度；0 表示登录前校验时还不知道登录年度。
        public int Year;
        public int Period;
        public string Sign;
        public int No;
        // 红字凭证日期；空串取登录日期。
        public string Date;

        public GlKey Source()
        {
            GlKey key = new GlKey();
            key.Year = Year;
            key.Period = Period;
            key.Sign = Sign;
            key.No = No;
            return key;
        }
    }

    // gl/vouchers/reverse的请求校验与锁键。登录前只校验形状（loginYear 传 0），登录后补齐年度并核对。
    internal static class GlReverseReq
    {
        public const string Op = "reverse";
        public const string Path = Requests.GlRoot + Op;

        public static GlReverseAsk Parse(Dictionary<string, object> body, int loginYear)
        {
            GlKey key = GlReq.Key(body, loginYear);
            GlReverseAsk ask = new GlReverseAsk();
            ask.Period = key.Period;
            ask.Sign = key.Sign;
            ask.No = key.No;
            object year = GlReq.Field(body, "fiscal_year");
            ask.Year = year == null ? loginYear : GlReq.IntIn(year, "fiscal_year", 1900, 9999);
            ask.Date = GlReq.OptDate(GlReq.Field(body, "voucher_date"), "voucher_date");
            if (loginYear > 0 && ask.Year > loginYear)
            {
                throw GlReq.Bad("fiscal_year 不能晚于登录年度 " + Int(loginYear), "fiscal_year");
            }
            if (loginYear > 0 && ask.Date.Length > 0 && !ask.Date.StartsWith(Int(loginYear) + "-", StringComparison.Ordinal))
            {
                throw GlReq.Bad("红字凭证日期 voucher_date 必须在登录年度 " + Int(loginYear) + " 内", "voucher_date");
            }
            return ask;
        }

        // 锁键：原凭证 "gl:<年度>:<期间>:<类别>:<凭证号>"，新凭证编号 "new:gl:<类别>"（与 gl/vouchers/create 互斥），
        // 以及 "gl:post"（与记账、取消记账互斥：原凭证的记账状态在冲销过程中不能变）。
        public static string[] LockKeys(Dictionary<string, object> body, string loginYear)
        {
            object year = Requests.Field(body, "fiscal_year");
            string fiscal = year == null ? loginYear : Requests.KeyPart(year);
            string sign = Requests.KeyPart(Requests.Field(body, "sign"));
            return new string[]
            {
                "gl:" + fiscal + ":" + Requests.KeyPart(Requests.Field(body, "period")) + ":" + sign + ":"
                    + Requests.KeyPart(Requests.Field(body, "no")),
                "new:gl:" + sign,
                "gl:post"
            };
        }

        internal static string Int(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }
    }
}
