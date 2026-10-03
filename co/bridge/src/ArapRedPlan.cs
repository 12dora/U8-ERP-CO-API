using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;
using System.Xml;

namespace U8Co
{
    // 一张单据一行（或应收应付单整单）的可对冲余额：Line 是发票行（iBVid），整单为 0；SignedF / SignedN 是带符号的余额
    // （应收 借-贷、应付 贷-借，口径同核销）：红字为负、蓝字为正。
    internal sealed class RedSlot
    {
        public int Line;
        public decimal SignedF;
        public decimal SignedN;
    }

    // 落到一行上的对冲金额（原币、本币都是正数）。BeforeF 是写前带符号的余额；AccA / AccB 是发票行写前的累计核销
    // （销售 iExchSum / iMoneySum，采购 iOriTotal / iTotal）；Sign 是该行审核行（部门、业务员、存货进报文）。
    internal sealed class RedPiece
    {
        public int Line;
        public decimal F;
        public decimal N;
        public decimal BeforeF;
        public decimal AccA;
        public decimal AccB;
        public Dictionary<string, object> Sign;
    }

    // 请求里的一项过了闸门之后。Core 沿用转账的 TransferDoc（类型、单号、种类、主键、表头、汇率），以便复用 TransferSql 的取数；
    // Red 为真是红字一侧；HeadBefore 是应收单 / 应付单写前的 Ap_Vouch.iRAmount_f。
    internal sealed class RedDoc
    {
        public TransferDoc Core;
        public bool Red;
        public decimal HeadBefore;
        public List<RedPiece> Pieces = new List<RedPiece>();

        public string Label
        {
            get { return Core.Label; }
        }
    }

    // 过了闸门的红票对冲计划。Year / Period 是对冲日期（登录日期）所在的会计期间；Rate 是报文根节点的汇率（第一张红字单据的）。
    internal sealed class RedPlan
    {
        public string Flag;
        public string Date;
        public int Year;
        public int Period;
        public string Partner;
        public string Currency;
        public string Local;
        public bool Home;
        public decimal Rate = 1m;
        public decimal Sum;
        public string CancelNo;
        public List<RedDoc> Red = new List<RedDoc>();
        public List<RedDoc> Blue = new List<RedDoc>();

        public IEnumerable<RedDoc> Docs
        {
            get
            {
                foreach (RedDoc d in Red)
                {
                    yield return d;
                }
                foreach (RedDoc d in Blue)
                {
                    yield return d;
                }
            }
        }
    }

    // 红票对冲的固定规则（纯函数，--selftest 核对）。依据：测试账套实测（U8ApCancel.cLsCancel.AP_JZ_Red，见 docs/u8-notes.md）、
    // U8 界面红票对冲的处理结果（9N 批次）：
    // 处理方式 9N，号 HRAR…（应收）/ HPAP…（应付）；每条处理行是单据自身（cVouchType = cCoVouchType）；
    // 红字行的余额（应收 借-贷、应付 贷-借）加本次金额（向 0 靠），蓝字行减本次金额；
    // 发票累计核销：蓝字加本次、红字减本次（实测 SaleBillVouchs.iExchSum 蓝 +10、红 -10）。
    internal static class ArapRedRule
    {
        public const string Style = "9N";
        public const string Title = "红票对冲";
        static readonly Regex NoShape = new Regex("^(HRAR|HPAP)[0-9]{1,20}\\z", RegexOptions.CultureInvariant);

        public static string Prefix(string flag)
        {
            return flag == "AP" ? "HPAP" : "HRAR";
        }

        // 号的形状：HRAR / HPAP 后接数字，且与 flag 一致。
        public static bool NoValid(string flag, string no)
        {
            return no != null && NoShape.IsMatch(no) && no.StartsWith(Prefix(flag), StringComparison.Ordinal);
        }

        // 往来余额（带符号）的变化：红字 +金额，蓝字 -金额。
        public static decimal Delta(bool red, decimal amount)
        {
            return red ? amount : -amount;
        }

        // 发票累计核销的变化：红字 -金额，蓝字 +金额。
        public static decimal AccDelta(bool red, decimal amount)
        {
            return red ? -amount : amount;
        }

        // 一行余额在这一侧可对冲的量（正数）；符号不对（红字一侧余额为正、蓝字一侧为负）为 0。
        public static decimal Open(bool red, decimal signed)
        {
            decimal v = red ? -signed : signed;
            return v > 0m ? v : 0m;
        }

        // 原币折本币：本位币即原币；外币按单据汇率、两位小数（同 U8：AP_JZ_Red 缺本币时按 原币 × 汇率 补）。
        public static decimal Native(decimal f, bool home, decimal rate)
        {
            return home ? f : decimal.Round(f * rate, 2, MidpointRounding.AwayFromZero);
        }

        // 按行主键从小到大分摊 amount；可对冲的量不够返回 null。
        public static List<RedPiece> Spread(bool red, List<RedSlot> slots, decimal amount, bool home, decimal rate)
        {
            List<RedSlot> sorted = new List<RedSlot>(slots);
            sorted.Sort(delegate(RedSlot a, RedSlot b) { return a.Line.CompareTo(b.Line); });
            List<RedPiece> pieces = new List<RedPiece>();
            decimal left = amount;
            foreach (RedSlot slot in sorted)
            {
                decimal open = Open(red, slot.SignedF);
                if (left <= 0m || open <= 0m)
                {
                    continue;
                }
                RedPiece p = new RedPiece();
                p.Line = slot.Line;
                p.BeforeF = slot.SignedF;
                p.F = Math.Min(left, open);
                p.N = Native(p.F, home, rate);
                pieces.Add(p);
                left -= p.F;
            }
            return left > 0m ? null : pieces;
        }

        // 一侧单据的可对冲量合计（报错用）。
        public static decimal Available(bool red, List<RedSlot> slots)
        {
            decimal sum = 0m;
            foreach (RedSlot s in slots)
            {
                sum += Open(red, s.SignedF);
            }
            return sum;
        }

        // AP_JZ_Red 的报文（测试账套实测的形状）：根 canceldata 带往来单位、币种（cexch_name）、汇率、对冲日期；
        // 每个红字行一个 redvouch、每个蓝字行一个 bluevouch，属性同 U8 对冲界面生成的报文：iinvid 是发票行（iBVid），应收应付单 0；
        // 金额是不带符号的原币 icancelamount_f 和本币 icancelamount，两位小数。用 DOM 生成，属性值不做字符串拼接。
        public static string Xml(RedPlan plan)
        {
            XmlDocument doc = new XmlDocument();
            XmlElement root = doc.CreateElement("canceldata");
            doc.AppendChild(root);
            Attr(root, "cdwcode", plan.Partner);
            Attr(root, "cexch_name", plan.Currency);
            Attr(root, "iexchrate", WriteoffXml.Rate(plan.Rate));
            Attr(root, "dhxdate", plan.Date);
            foreach (RedDoc d in plan.Docs)
            {
                foreach (RedPiece p in d.Pieces)
                {
                    root.AppendChild(Vouch(doc, plan, d, p));
                }
            }
            return root.OuterXml;
        }

        static XmlElement Vouch(XmlDocument doc, RedPlan plan, RedDoc d, RedPiece p)
        {
            XmlElement v = doc.CreateElement(d.Red ? "redvouch" : "bluevouch");
            Attr(v, "cdwcode", plan.Partner);
            Attr(v, "cvouchtype", d.Core.Ask.Type);
            Attr(v, "cvouchcode", d.Core.Ask.Code);
            Attr(v, "dvouchdate", CoRows.Col(d.Core.Head, "vdate"));
            Attr(v, "cdeptcode", CoRows.Col(p.Sign, "dept"));
            Attr(v, "cperson", CoRows.Col(p.Sign, "person"));
            Attr(v, "cinvcode", CoRows.Col(p.Sign, "inv"));
            Attr(v, "iinvid", p.Line.ToString(CultureInfo.InvariantCulture));
            Attr(v, "cexchname", plan.Currency);
            Attr(v, "iexchrate", WriteoffXml.Rate(d.Core.Rate));
            Attr(v, "icancelamount_f", ArapTransferRule.Money(p.F));
            Attr(v, "icancelamount", ArapTransferRule.Money(p.N));
            return v;
        }

        static void Attr(XmlElement e, string name, string value)
        {
            e.SetAttribute(name, value ?? "");
        }

        // 处理行的期望：键（类型|单号|行）→ [原币, 本币] 带符号的余额变化。同一键的几项相加。
        public static Dictionary<string, decimal[]> Expected(RedPlan plan)
        {
            Dictionary<string, decimal[]> map = new Dictionary<string, decimal[]>(StringComparer.Ordinal);
            foreach (RedDoc d in plan.Docs)
            {
                foreach (RedPiece p in d.Pieces)
                {
                    string key = Key(d.Core.Ask.Type, d.Core.Ask.Code, p.Line);
                    decimal[] v;
                    if (!map.TryGetValue(key, out v))
                    {
                        v = new decimal[2];
                        map[key] = v;
                    }
                    v[0] += Delta(d.Red, p.F);
                    v[1] += Delta(d.Red, p.N);
                }
            }
            return map;
        }

        public static string Key(string type, string code, int line)
        {
            return type + "|" + code + "|" + line.ToString(CultureInfo.InvariantCulture);
        }

        // 期望与实际（同样的键）对得上返回 null，否则返回不符的键。
        public static string Diff(Dictionary<string, decimal[]> want, Dictionary<string, decimal[]> got, decimal tolerance)
        {
            foreach (KeyValuePair<string, decimal[]> pair in want)
            {
                decimal[] one;
                if (!got.TryGetValue(pair.Key, out one) || Math.Abs(one[0] - pair.Value[0]) > tolerance
                    || Math.Abs(one[1] - pair.Value[1]) > tolerance)
                {
                    return pair.Key;
                }
            }
            foreach (string key in got.Keys)
            {
                if (!want.ContainsKey(key))
                {
                    return key;
                }
            }
            return null;
        }
    }
}
