using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 制单时一行分录的来源：往来单位、部门、业务员、项目、存货（辅助核算按科目要求从这里取）。
    internal sealed class VoucherCtx
    {
        public string Dw = "";
        public string Dept = "";
        public string Person = "";
        public string ItemClass = "";
        public string ItemCode = "";
        public string Inventory = "";

        public VoucherCtx With(string dept, string person, string itemClass, string itemCode, string inventory)
        {
            VoucherCtx c = new VoucherCtx();
            c.Dw = Dw;
            c.Dept = dept.Length > 0 ? dept : Dept;
            c.Person = person.Length > 0 ? person : Person;
            c.ItemClass = itemClass;
            c.ItemCode = itemCode;
            c.Inventory = inventory;
            return c;
        }
    }

    // 桥按 U8 规则拼出的一行（合并前）。Settle / DocNo / DocDate 只在收付款单的结算行上有。
    internal sealed class VoucherPart
    {
        public string Account = "";
        public bool Debit;
        public decimal Amount;
        public VoucherCtx Ctx = new VoucherCtx();
        public string Settle = "";
        public string DocNo = "";
        public string DocDate = "";
        // 来源：往来行 dw、结算行 settle、对方行 opp（ArapVoucherBuild 的常量）。往来行和非现金银行的结算行回写明细的 ino_id。
        public string Kind = "";
        // 往来行、结算行来自哪一行往来明细（Auto_ID）；对方行为空。
        public string Aid = "";
    }

    // 一张单据：表头（带锁读出）、原始往来明细（cProcStyle = cVouchType）、制单参数。
    internal sealed class VoucherDoc
    {
        public VoucherAsk Ask;
        public string Flag;
        public string Detail;
        public Dictionary<string, object> Head;
        public List<Dictionary<string, object>> Rows = new List<Dictionary<string, object>>();
        public int Id;
        public string Code = "";
        public string VType = "";
        public string VDate = "";
        public string NoteNo = "";

        public string Dw
        {
            get { return CoRows.Col(Head, "cDwCode"); }
        }

        public VoucherCtx HeadCtx()
        {
            VoucherCtx c = new VoucherCtx();
            c.Dw = Dw;
            c.Dept = CoRows.Col(Head, "cDeptCode");
            c.Person = CoRows.Col(Head, "cPerson");
            return c;
        }
    }

    // 制单计划：凭证键、外部业务号、分录（合并、排好序后的 GlDraft）、回写需要的对应关系。
    // 合并制单时 Docs 是全部单据（按请求顺序），Doc 是第一张；每行分录只来自一张单据（Bills），不跨单据合并。
    internal sealed class VoucherPlan
    {
        public VoucherDoc Doc;
        public List<VoucherDoc> Docs = new List<VoucherDoc>();
        // 分录号 - 1 → 该行来自的单据在 Docs 里的下标（回写凭证行的 coutbillsign / coutid）。
        public List<int> Bills = new List<int>();
        public GlKey Key = new GlKey();
        public int Seq;
        public string Date = "";
        public string Digest = "";
        public string OutSign = "";
        public string PzId = "";
        public GlDraft Draft = new GlDraft();
        // 分录号 → 业务员姓名（cname，只在有客户 / 供应商辅助核算的行上）。
        public Dictionary<int, string> Operators = new Dictionary<int, string>();
        // 往来明细 Auto_ID → 回写的分录号（ino_id），按行不按科目：一个科目拆成几行分录时各明细行写自己那一行（同 U8）。
        public Dictionary<string, int> Entries = new Dictionary<string, int>(StringComparer.Ordinal);
        // （单据下标、科目）→ 该单据该科目第一行要回写的分录号：只给不出分录的票据登记行（iFlag=3）用，它们跟着本单同科目的结算行。
        public Dictionary<string, int> ByAccount = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        // 受控科目（code.cother 非空）的分录号：U8 制单的这些行 bvalueedit=0。
        public List<int> Controlled = new List<int>();
        public string MakingSystem = "";
        // 调用导入前本期同类别的最大凭证号（导入被拒时确认没写进去）。
        public int MaxBefore;
        // 回写核对发现外部业务号撞号（别的凭证或别的单据也用了它）：补偿时只删本凭证，不按外部业务号清回写。
        public bool Clash;

        // 第 bill 张单据的往来明细行要回写的分录号；0 表示不写（现金 / 银行结算行，同 U8）。
        public int EntryOf(int bill, Dictionary<string, object> row)
        {
            int entry;
            if (Entries.TryGetValue(CoRows.Col(row, "aid"), out entry))
            {
                return entry;
            }
            if (CoRows.Col(row, "iflag") == "3" && ByAccount.TryGetValue(AccountKey(bill, CoRows.Col(row, "cCode")), out entry))
            {
                return entry;
            }
            return 0;
        }

        public static string AccountKey(int bill, string account)
        {
            return bill.ToString(CultureInfo.InvariantCulture) + "\u0001" + account;
        }

        // 全部单据的原始往来明细行数（回写核对用）。
        public int RowCount()
        {
            int n = 0;
            foreach (VoucherDoc doc in Docs)
            {
                n += doc.Rows.Count;
            }
            return n;
        }

        public string PzNum()
        {
            string no = Key.No.ToString(CultureInfo.InvariantCulture);
            return Key.Sign + "-" + (no.Length < 4 ? new string('0', 4 - no.Length) : "") + no;
        }
    }

    // 凭证类别、coutsign、摘要前缀的规则（同 U8）。
    internal static class ArapVoucherRule
    {
        public const string Receive = "收";
        public const string Pay = "付";
        public const string Transfer = "转";
        public static readonly string[] Signs = new string[] { Receive, Pay, Transfer };

        // coutsign：付款单 RP、应付单 AR，其余同 flag。收款单 AR / RP 两种都有，取 AR（未经实测）。
        // 退款单两种都是 RP（同 U8）。
        public static string OutSign(string kind, string flag)
        {
            if (kind == "ap_payment" || IsRefund(kind))
            {
                return "RP";
            }
            if (kind == "ap_bill")
            {
                return "AR";
            }
            return flag;
        }

        // 摘要前缀：销售发票 / 应收单「销售」，采购发票 / 应付单「购」，收款单、供应商退款「收」，付款单、客户退款「付」。
        public static string Prefix(string kind)
        {
            switch (kind)
            {
                case "ar_receipt":
                case "ap_refund":
                    return "收";
                case "ap_payment":
                case "ar_refund":
                    return "付";
                case "purchase_invoice":
                case "ap_bill":
                    return "购";
                default:
                    return "销售";
            }
        }

        // 摘要后缀：退款单「退款」（缺省摘要「收<供应商>退款」「付<客户>退款」，同 U8），其余没有。
        public static string Suffix(string kind)
        {
            return IsRefund(kind) ? "退款" : "";
        }

        // 退款单：供应商退款 ap_refund（AP48）、客户退款 ar_refund（AR49）。
        public static bool IsRefund(string kind)
        {
            return kind == "ap_refund" || kind == "ar_refund";
        }

        // 外部业务号：AR / AP 加 13 位补零的数字（同 U8，Ap_CancelNo 的 PZ 号）。
        public static string PzId(string flag, int no)
        {
            string digits = no.ToString(CultureInfo.InvariantCulture);
            return flag + (digits.Length < 13 ? new string('0', 13 - digits.Length) : "") + digits;
        }
    }
}
