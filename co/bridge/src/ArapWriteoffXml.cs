using System;
using System.Globalization;
using System.Xml;

namespace U8Co
{
    // U8ApCancel.cLsCancel.Save 的报文，照 U8 信用证模块（CreditCardMngr）拼的形状，测试账套实测跑通：
    // <canceldata cdwcode crpflag dhxdate bsamehx><close …><vouch …/>…</close></canceldata>。
    // vouch 必须是 close 的子节点（平级时 U8 找不到 vouch，拿收付款单自己核销自己）；bcancelall 必须带（缺了 VB 错误 13）；
    // 数值属性缺了会被 U8 当成空串再转数字，同样报 13，所以一律写全。用 DOM 生成，属性值不做字符串拼接。
    // 币种、汇率在 close 和每个 vouch 上都写收付款单的（闸门已保证单据同币种；单据自己的汇率不同也一样，同 U8，
    // 汇兑差额留给期末汇兑损益 9M）；*_f 是原币，不带 _f 的是本币。本币照 U8 外币 9P 的常见写法：close 本币 =
    // round(原币合计 × 汇率, 2)，各 vouch 逐个折算，尾差并到第一个 vouch，使 vouch 本币之和等于 close 本币
    // （U8 自己生成的核销绝大多数如此，少数尾差在最后一个 vouch）。本位币汇率 1 时本币就是原币。
    internal static class WriteoffXml
    {
        public static string Build(WriteoffPlan plan, string date)
        {
            XmlDocument doc = new XmlDocument();
            XmlElement root = doc.CreateElement("canceldata");
            doc.AppendChild(root);
            Attr(root, "cdwcode", plan.Dw);
            Attr(root, "crpflag", plan.Flag);
            Attr(root, "dhxdate", date);
            Attr(root, "bsamehx", "True");
            XmlElement close = doc.CreateElement("close");
            root.AppendChild(close);
            Attr(close, "cdwcode", plan.Dw);
            Attr(close, "cexchname", plan.Currency);
            Attr(close, "cjsdnum", plan.ReceiptCode);
            Attr(close, "crpflag", plan.Flag);
            Attr(close, "djsddate", plan.ReceiptDate);
            Attr(close, "bprepay", plan.Prepay ? "1" : "0");
            Attr(close, "iexchrate", Rate(plan.Rate));
            Attr(close, "id", Int(plan.Line));
            Attr(close, "ijsdhxamount_f", Money(plan.Sum));
            Attr(close, "ijsdhxamount", Money(Home(plan.Sum, plan.Rate)));
            decimal[] home = Locals(plan);
            for (int i = 0; i < plan.Targets.Count; i++)
            {
                close.AppendChild(Vouch(doc, plan, plan.Targets[i], home[i]));
            }
            return root.OuterXml;
        }

        // cvouchtype / cvouchcode / dvouchdate 是被核销单据；iinvid 是往来明细的 iBVid（发票行主键，应收应付单为 0）。
        static XmlElement Vouch(XmlDocument doc, WriteoffPlan plan, WriteoffTarget t, decimal home)
        {
            XmlElement v = doc.CreateElement("vouch");
            Attr(v, "cvouchtype", t.VType);
            Attr(v, "cvouchcode", t.Code);
            Attr(v, "dvouchdate", t.Date);
            Attr(v, "cinvcode", "");
            Attr(v, "cdigest", "");
            Attr(v, "iinvid", Int(t.Line));
            Attr(v, "cexchname", plan.Currency);
            Attr(v, "iexchrate", Rate(plan.Rate));
            Attr(v, "icancelamount_f", Money(t.Amount));
            Attr(v, "icancelamount", Money(home));
            Attr(v, "bcancelall", "false");
            return v;
        }

        // 原币折本币，两位小数，四舍五入。
        public static decimal Home(decimal amount, decimal rate)
        {
            return Math.Round(amount * rate, 2, MidpointRounding.AwayFromZero);
        }

        // 各 vouch 的本币：逐个折算，尾差（close 本币减各 vouch 之和）并到第一个。
        public static decimal[] Locals(WriteoffPlan plan)
        {
            decimal[] home = new decimal[plan.Targets.Count];
            decimal sum = 0m;
            for (int i = 0; i < home.Length; i++)
            {
                home[i] = Home(plan.Targets[i].Amount, plan.Rate);
                sum += home[i];
            }
            if (home.Length > 0)
            {
                home[0] += Home(plan.Sum, plan.Rate) - sum;
            }
            return home;
        }

        // 汇率按库里的精度写，去掉末尾的 0（1 写成 "1"，7.1235000000 写成 "7.1235"）。
        public static string Rate(decimal rate)
        {
            return rate.ToString("0.##########", CultureInfo.InvariantCulture);
        }

        static void Attr(XmlElement e, string name, string value)
        {
            e.SetAttribute(name, value ?? "");
        }

        static string Int(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }

        static string Money(decimal value)
        {
            return value.ToString("0.00", CultureInfo.InvariantCulture);
        }
    }
}
