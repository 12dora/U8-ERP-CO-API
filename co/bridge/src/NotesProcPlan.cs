using System.Collections.Generic;

namespace U8Co
{
    // 带锁读出的票据表头（AP_Note）。Partner 是票据的往来单位：U8 的处理行记在 cEndorser（登记时的客户）上，
    // cDwCode 常为空、与 cEndorser 不同时处理行也用 cEndorser；cEndorser 为空时退回 cDwCode。
    internal sealed class NoteHead
    {
        public int Id;
        public string Link;
        public string Code;
        public string VouchType;
        public string Partner;
        public string Dept;
        public string Person;
        public string Km;
        public decimal Remain;
        public decimal RemainLocal;
        public string Currency;
        public string Nfrat;
        public string SignDate;
        public string ReceiptDate;
        public bool Opening;
        public int CloseId;
        public bool Sub;
        public int ChangeType;
        public string Settle;
        public Dictionary<string, object> Row;
    }

    // 过了闸门的票据处理计划。Amount 是本次处理的票面金额（往来明细 50 行的贷方、票据余额的减数）；
    // 贴现时 Sub 表的 iAmount 写净额 Net；退回时另生成一张应收单（R0），票据处理行指向它。Range 是分包票据本次用的子票区间（非分包为 null），Left 是处理后剩下的可用区间。
    internal sealed class NotesProcPlan
    {
        public string Flag;
        public string Op;
        public string Date;
        public int Year;
        public int Period;
        public string Operator;
        public string Local;
        public string CancelNo;
        public string Digest;
        public NoteHead Note;
        public string PartnerName;
        public decimal Amount;
        public decimal Net;
        public decimal Expense;
        public decimal Interest;
        public decimal Rate;
        public NoteRange Range;
        public List<NoteRange> Left;
        public string BankCode;
        public string BankName;
        public string Vendor;
        public string VendorName;
        public string ApKm;
        // 背书冲应付的应付一侧：沿用转账的计划（处理方式固定 9E），单据过转账同一套闸门，写和余额核对也走转账的代码。
        public TransferPlan Ap;
        // 退回（9C）：应收控制科目（登记生成的收款单应收款行的科目）、新应收单的模板号（SaveVouch 没写时补）、
        // 生成应收单用的组件（开事务前打开，NotesProcReturnBo），以及新应收单的主键和单号（U8 分配）。
        public string CtrlKm;
        public int VtId;
        public NotesProcReturnBo Saver;
        public int BillId;
        public string BillCode;

        public string Style
        {
            get { return NotesProcRule.Style(Op); }
        }

        public string DetailId
        {
            get { return Range == null ? Note.Code : NotesProcRule.DetailId(Note.Code, Range.Start, Range.End); }
        }

        public bool Endorse
        {
            get { return Op == "endorse"; }
        }

        public bool Return
        {
            get { return Op == "return"; }
        }
    }
}
