using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 一张单据一行（或应收应付单整单）的可转账余额：原币、本币。Line 是发票行（iBVid），整单为 0。
    internal sealed class TransferSlot
    {
        public int Line;
        public decimal RemainF;
        public decimal RemainN;
    }

    // 落到一行上的转账金额：写一条处理行（iFlag=0），扣该行余额。
    // BillA / BillB 是发票行写之前的累计核销（销售 iExchSum / iMoneySum，采购 iOriTotal / iTotal）。
    internal sealed class TransferPiece
    {
        public TransferDoc Doc;
        public int Line;
        public decimal F;
        public decimal N;
        public decimal BeforeF;
        public decimal BillA;
        public decimal BillB;
    }

    // 请求里的一项过了闸门之后：单据主键、表头（数据权限按它）、汇率、分摊到各行的金额。
    // HeadBefore 是应收单 / 应付单写之前的 Ap_Vouch.iRAmount_f。
    internal sealed class TransferDoc
    {
        public TransferAskLine Ask;
        public string Side;
        public string Kind;
        public string Title;
        public int Id;
        public decimal Rate;
        public decimal HeadBefore;
        public Dictionary<string, object> Head;
        public List<TransferPiece> Pieces = new List<TransferPiece>();

        public string Label
        {
            get { return Title + " " + Ask.Code; }
        }
    }

    // 过了闸门的转账计划。Year / Period 是转账日期所在的会计期间（UA_Period）。
    internal sealed class TransferPlan
    {
        public string Flag;
        public string Date;
        public int Year;
        public int Period;
        public string Customer;
        public string Vendor;
        public string Currency;
        public string Local;
        public bool Home;
        public string Digest;
        public string Operator;
        public string CancelNo;
        public decimal Sum;
        // 票据背书（NotesProcEndorse）借用转账计划写应付一侧时固定为 9E；为空时按 Flag 取 9I / 9J。
        public string FixedStyle;
        public List<TransferDoc> Ar = new List<TransferDoc>();
        public List<TransferDoc> Ap = new List<TransferDoc>();

        public string Style
        {
            get { return string.IsNullOrEmpty(FixedStyle) ? ArapTransferRule.Style(Flag) : FixedStyle; }
        }

        // 一侧单据的往来单位：应收一侧是客户，应付一侧是供应商。
        public string DwOf(string side)
        {
            return side == "AP" ? Vendor : Customer;
        }

        public IEnumerable<TransferDoc> Docs
        {
            get
            {
                foreach (TransferDoc d in Ar)
                {
                    yield return d;
                }
                foreach (TransferDoc d in Ap)
                {
                    yield return d;
                }
            }
        }
    }

    // 应收冲应付 / 应付冲应收的固定规则（纯函数，--selftest 核对）。依据 U8 界面转账的处理结果
    // 与 U8 的批次一致：处理方式 9I / 9J；取号 Ap_Proc_CancelNo YCF+AP / FCY+AR；应收一侧的处理行记贷方、应付一侧记借方，iFlag=0。
    // 只做发票、应收单 / 应付单之间的转账（收付款单参与时 U8 会另生成预收 / 预付等单据，见 ArapTransferReq）。
    internal static class ArapTransferRule
    {
        public const string ByInvoiceLine = "bvid";
        public const string WholeDoc = "doc";

        public static string Style(string flag)
        {
            return flag == "AP" ? "9J" : "9I";
        }

        // Ap_Proc_CancelNo 的 @cType、@cFlag：9I 是 YCF / AP，9J 是 FCY / AR（号 = 两者相接再补零到 17 位）。
        public static string[] NoArgs(string flag)
        {
            return flag == "AP" ? new string[] { "FCY", "AR" } : new string[] { "YCF", "AP" };
        }

        public static string Title(string flag)
        {
            return flag == "AP" ? "应付冲应收" : "应收冲应付";
        }

        // 号的形状：前缀 + 数字，共 17 位。
        public static bool NoValid(string flag, string no)
        {
            string[] args = NoArgs(flag);
            string head = args[0] + args[1];
            if (no == null || no.Length != 17 || !no.StartsWith(head, StringComparison.Ordinal))
            {
                return false;
            }
            for (int i = head.Length; i < no.Length; i++)
            {
                if (no[i] < '0' || no[i] > '9')
                {
                    return false;
                }
            }
            return true;
        }

        public static string SideOf(string type)
        {
            return type == "26" || type == "27" || type == "R0" ? "AR" : "AP";
        }

        // 余额按什么分：发票按行（iBVid），应收应付单整单。
        public static string Mode(string type)
        {
            return type == "R0" || type == "P0" ? WholeDoc : ByInvoiceLine;
        }

        // 单据的类型名（WriteoffKind / Kinds）。
        public static string KindOf(string type)
        {
            switch (type)
            {
                case "26":
                case "27":
                    return "sale_invoice";
                case "01":
                case "02":
                    return "purchase_invoice";
                case "R0":
                    return "ar_bill";
                default:
                    return "ap_bill";
            }
        }

        public static string TitleOf(string type)
        {
            return WriteoffKind.Of(KindOf(type)).Title;
        }

        // 处理行记贷方（应收一侧）还是借方（应付一侧）。
        public static bool Credit(string side)
        {
            return side == "AR";
        }

        // 红字：没有一行余额大于 0、且有余额小于 0 的行（蓝字转账只扣正余额，红字单据走红票对冲）。
        public static bool Red(List<TransferSlot> slots)
        {
            bool negative = false;
            foreach (TransferSlot s in slots)
            {
                if (s.RemainF > 0m)
                {
                    return false;
                }
                negative = negative || s.RemainF < 0m;
            }
            return negative;
        }

        // 原币折本币：本位币即原币；外币把一行余额全部转走时取该行的本币余额（不留尾差），否则按单据汇率折算、两位小数。
        public static decimal Native(decimal f, TransferSlot slot, bool home, decimal rate)
        {
            if (home)
            {
                return f;
            }
            if (f == slot.RemainF)
            {
                return slot.RemainN;
            }
            return decimal.Round(f * rate, 2, MidpointRounding.AwayFromZero);
        }

        // 按行主键从小到大（先开的行先转）分摊 amount，跳过余额不大于 0 的行；余额不够返回 null。
        public static List<TransferPiece> Spread(TransferDoc doc, List<TransferSlot> slots, decimal amount, bool home)
        {
            List<TransferSlot> sorted = new List<TransferSlot>(slots);
            sorted.Sort(delegate(TransferSlot a, TransferSlot b) { return a.Line.CompareTo(b.Line); });
            List<TransferPiece> pieces = new List<TransferPiece>();
            decimal left = amount;
            foreach (TransferSlot slot in sorted)
            {
                if (left <= 0m)
                {
                    break;
                }
                if (slot.RemainF <= 0m)
                {
                    continue;
                }
                TransferPiece p = new TransferPiece();
                p.Doc = doc;
                p.Line = slot.Line;
                p.BeforeF = slot.RemainF;
                p.F = Math.Min(left, slot.RemainF);
                p.N = Native(p.F, slot, home, doc.Rate);
                pieces.Add(p);
                left -= p.F;
            }
            return left > 0m ? null : pieces;
        }

        public static string Money(decimal value)
        {
            return value.ToString("0.00", CultureInfo.InvariantCulture);
        }
    }
}
