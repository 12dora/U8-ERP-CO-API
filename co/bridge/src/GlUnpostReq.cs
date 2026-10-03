using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 最近一次记账范围（GL_mpostcond）里的一张凭证。Seq 是 dsign.isignseq（GL_mpostcond.csign 存的就是它的文本），
    // Sign 是类别字（dsign.csign），查不到时为空串。
    internal sealed class GlUnpostItem
    {
        public int Period;
        public int Seq;
        public int No;
        public string Sign;

        public string Text()
        {
            return (Sign.Length > 0 ? Sign : "#" + Seq.ToString(CultureInfo.InvariantCulture)) + "-" + No.ToString(CultureInfo.InvariantCulture);
        }
    }

    // gl/vouchers/unpost 的请求：fiscal_year 省略取登录日期的年份；period、vouchers 可选，给了就必须与最近一次记账的
    // 范围完全一致（核对用，U8 没有按单张凭证取消记账，桥也不挑选）。Batch 是事务里读到的范围。
    internal sealed class GlUnpostAsk
    {
        public int Year;
        public int Period;
        public List<GlPostItem> Expect;
        public List<GlUnpostItem> Batch = new List<GlUnpostItem>();
        public bool Multi;
        public string Currency = "";

        public string Summary()
        {
            StringBuilder sb = new StringBuilder();
            sb.Append(Year.ToString(CultureInfo.InvariantCulture)).Append("年").Append(Period.ToString(CultureInfo.InvariantCulture)).Append("期");
            for (int i = 0; i < Batch.Count && i < 20; i++)
            {
                sb.Append(i == 0 ? " " : ",").Append(Batch[i].Text());
            }
            if (Batch.Count > 20)
            {
                sb.Append(" 等 ").Append(Batch.Count.ToString(CultureInfo.InvariantCulture)).Append(" 张");
            }
            return sb.ToString();
        }
    }

    // 登录前的字段校验（只抛 400）与常量。处理在 GlUnpost。
    internal static class GlUnpostReq
    {
        public const string Op = "unpost";
        public const string Path = Requests.GlRoot + Op;
        // 权限规则键（PermRegistryGl）。
        public const string Rule = "write:gl:unpost";
        public const string TestOnly = "取消记账只对配置为测试账套的账套开放";
        // 一次恢复的凭证张数上限（GL_mpostcond 本年度的行数）；更大的记账批次请在 U8 客户端恢复。
        public const int Max = 500;

        // loginYear 为 0 时只校验形状（登录前），fiscal_year 省略时年度留 0。
        public static GlUnpostAsk Parse(Dictionary<string, object> body, int loginYear)
        {
            GlUnpostAsk ask = new GlUnpostAsk();
            if (GlReq.Field(body, "vouchers") != null)
            {
                // 与记账同一套形状：period 必填，vouchers 是 1 到 200 张 {sign, no}，不能重复。
                GlPostReq req = GlPostParse.Parse(body, loginYear);
                ask.Year = req.Year;
                ask.Period = req.Period;
                ask.Expect = req.Items;
                return ask;
            }
            object period = GlReq.Field(body, "period");
            ask.Period = period == null ? 0 : GlReq.IntIn(period, "period", 1, 12);
            object year = GlReq.Field(body, "fiscal_year");
            ask.Year = year == null ? loginYear : GlReq.IntIn(year, "fiscal_year", 1900, 9999);
            return ask;
        }

        // 调用方给了 period / vouchers 时与事务里读到的范围核对，不一致 409（没写入）。
        public static void Match(GlUnpostAsk ask, int period)
        {
            if (ask.Period != 0 && ask.Period != period)
            {
                throw GlState.Refuse("最近一次记账是 " + period.ToString(CultureInfo.InvariantCulture) + " 期，与请求的 "
                    + ask.Period.ToString(CultureInfo.InvariantCulture) + " 期不符，未恢复");
            }
            if (ask.Expect == null)
            {
                return;
            }
            HashSet<string> batch = new HashSet<string>(StringComparer.Ordinal);
            foreach (GlUnpostItem item in ask.Batch)
            {
                batch.Add(item.Sign + "-" + item.No.ToString(CultureInfo.InvariantCulture));
            }
            int hit = 0;
            foreach (GlPostItem item in ask.Expect)
            {
                if (batch.Contains(item.Sign + "-" + item.No.ToString(CultureInfo.InvariantCulture)))
                {
                    hit++;
                }
            }
            if (hit != ask.Expect.Count || hit != batch.Count)
            {
                throw GlState.Refuse("最近一次记账的凭证（" + ask.Summary() + "）与请求的 vouchers 不一致，未恢复");
            }
        }
    }
}
