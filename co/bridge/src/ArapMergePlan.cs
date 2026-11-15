using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 单据一行（iBVid；应收单 / 应付单整单为 0）在 from 名下的余额，以及本次要并走的数。
    // 余额口径同核销：cCoVouchType / cCoVouchID = 单据、cDwCode = 往来单位、iFlag < 3；应收 借-贷、应付 贷-借。
    // F 原币、N 本币、S 数量；Move* 带符号（红字行余额为负，并走的也是负数），= 余额 × 本次比例。
    internal sealed class MergeLine
    {
        public string Type;
        public string Code;
        public int DocId;
        public int Line;
        public decimal BalF;
        public decimal BalN;
        public decimal BalS;
        public decimal MoveF;
        public decimal MoveN;
        public decimal MoveS;
        public decimal ToBefore;
        public WriteoffKind Kind;
    }

    // 过了闸门的并账计划。Heads 是各单据表头（数据权限按它们和 from / to 两个往来单位）。
    internal sealed class MergePlan
    {
        public string Flag;
        public string From;
        public string To;
        public string Date;
        public int Year;
        public int Period;
        public string Digest;
        public string Currency;
        public string CancelNo;
        public string User;
        public List<MergeLine> Lines = new List<MergeLine>();
        public List<Dictionary<string, object>> Heads = new List<Dictionary<string, object>>();

        public string Side
        {
            get { return Flag == "AP" ? "应付" : "应收"; }
        }

        public string Partner
        {
            get { return Flag == "AP" ? "供应商" : "客户"; }
        }

        public decimal Total
        {
            get
            {
                decimal sum = 0m;
                foreach (MergeLine line in Lines)
                {
                    sum += line.MoveF;
                }
                return sum;
            }
        }
    }

    // 并账的纯计算（不连库，--selftest 直接测）：按请求的金额在单据各行上分配、按比例折本币和数量。
    internal static class ArapMergePlan
    {
        // 整个计划最多写这么多对往来明细（每行一对）。
        public const int MaxLines = 500;

        // open：该单据在 from 名下余额不为 0 的各行（按 iBVid 升序）；what：提示用的「单据类型 单号」。
        // 给了 line_id：只并这一行；没给：amount 省略则各行全部并走，给了就按行号从小到大依次分配（只在各行余额同方向时）。
        public static List<MergeLine> Allocate(MergeAskLine ask, List<MergeLine> open, string what)
        {
            if (ask.Line > 0)
            {
                MergeLine hit = Find(open, ask.Line);
                if (hit == null)
                {
                    throw State(what + " 第 " + ask.Line.ToString(CultureInfo.InvariantCulture) + " 行在并出单位名下没有余额");
                }
                return Single(hit, ask.Amount, what);
            }
            if (open.Count == 0)
            {
                throw State(what + " 在并出单位名下没有余额");
            }
            if (ask.Amount == null)
            {
                foreach (MergeLine line in open)
                {
                    Move(line, Math.Abs(line.BalF));
                }
                return open;
            }
            return Spread(open, ask.Amount.Value, what);
        }

        static MergeLine Find(List<MergeLine> open, int line)
        {
            foreach (MergeLine one in open)
            {
                if (one.Line == line)
                {
                    return one;
                }
            }
            return null;
        }

        static List<MergeLine> Single(MergeLine line, decimal? amount, string what)
        {
            decimal want = amount == null ? Math.Abs(line.BalF) : amount.Value;
            if (want > Math.Abs(line.BalF))
            {
                throw State(what + " 的并账金额超过余额 " + ArapWriteoffGate.Money(Math.Abs(line.BalF)));
            }
            Move(line, want);
            return new List<MergeLine> { line };
        }

        // 各行同方向（都为正或都为负）时按行号依次分配；方向不一致时只能整单并或按行指定。
        static List<MergeLine> Spread(List<MergeLine> open, decimal amount, string what)
        {
            decimal total = 0m;
            int sign = Math.Sign(open[0].BalF);
            foreach (MergeLine line in open)
            {
                total += Math.Abs(line.BalF);
                if (Math.Sign(line.BalF) != sign)
                {
                    throw State(what + " 各行余额方向不一致，部分并账请按行指定 line_id");
                }
            }
            if (amount > total)
            {
                throw State(what + " 的并账金额超过余额 " + ArapWriteoffGate.Money(total));
            }
            List<MergeLine> used = new List<MergeLine>();
            decimal left = amount;
            foreach (MergeLine line in open)
            {
                if (left <= 0m)
                {
                    break;
                }
                decimal take = Math.Min(left, Math.Abs(line.BalF));
                Move(line, take);
                used.Add(line);
                left -= take;
            }
            return used;
        }

        // 并走 amount（原币绝对值）：方向随余额；全部并走时本币、数量取余额原值，部分时按比例（本币 2 位、数量 6 位，四舍五入）。
        internal static void Move(MergeLine line, decimal amount)
        {
            decimal whole = Math.Abs(line.BalF);
            line.MoveF = line.BalF < 0 ? -amount : amount;
            if (amount == whole)
            {
                line.MoveN = line.BalN;
                line.MoveS = line.BalS;
                return;
            }
            decimal ratio = amount / whole;
            line.MoveN = Math.Round(line.BalN * ratio, 2, MidpointRounding.AwayFromZero);
            line.MoveS = Math.Round(line.BalS * ratio, 6, MidpointRounding.AwayFromZero);
        }

        // 往来明细的六个金额列，顺序 iDAmount, iCAmount, iDAmount_f, iCAmount_f, iDAmount_s, iCAmount_s。
        // 应收记在借方、应付记在贷方（单据余额的自然方向）；出方是负的并走数，入方是正的。
        internal static decimal[] Amounts(string flag, MergeLine line, bool target)
        {
            decimal k = target ? 1m : -1m;
            decimal n = k * line.MoveN;
            decimal f = k * line.MoveF;
            decimal s = k * line.MoveS;
            return flag == "AP" ? new decimal[] { 0m, n, 0m, f, 0m, s } : new decimal[] { n, 0m, f, 0m, s, 0m };
        }

        internal static BridgeException State(string message)
        {
            return ArapWriteoffGate.State(message);
        }
    }
}
