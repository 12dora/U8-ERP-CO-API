using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 分包票据的一段可用子票区间（Ap_Note_AvailRange 一行）：号从 Start 到 End（含），每个号 0.01 元。
    internal sealed class NoteRange
    {
        public long Start;
        public long End;

        public NoteRange(long start, long end)
        {
            Start = start;
            End = end;
        }
    }

    // 票据处理的固定规则（纯函数，--selftest 核对）。依据 U8 票据管理界面在测试账套上的处理结果：
    // 处理方式 9A 托收 / 结算、9D 贴现、9E 背书、9C 退回；取号 Ap_Proc_CancelNo 的 @cType PJJ / PJT / PJB / CL，
    // @cFlag 就是票据的 AR / AP，号 = 两者相接再补零到 17 位（如 PJJAR000000000001、CLAR0000000000001）。分包票据的子票区间：每个号 0.01 元，处理行的单号 = 票据号-起-止，
    // 处理后可用区间删了重写（子票从可用区间的最小号起取）。
    internal static class NotesProcRule
    {
        public static string Style(string op)
        {
            switch (op)
            {
                case "discount":
                    return "9D";
                case "endorse":
                    return "9E";
                case "return":
                    return "9C";
                default:
                    return "9A";
            }
        }

        public static string NoType(string op)
        {
            switch (op)
            {
                case "discount":
                    return "PJT";
                case "endorse":
                    return "PJB";
                case "return":
                    return "CL";
                default:
                    return "PJJ";
            }
        }

        public static string Title(string op)
        {
            switch (op)
            {
                case "discount":
                    return "票据贴现";
                case "endorse":
                    return "票据背书";
                case "return":
                    return "票据退回";
                default:
                    return "票据结算";
            }
        }

        // 号的形状：类型 + 标志 + 数字，共 17 位。
        public static bool NoValid(string op, string flag, string no)
        {
            string head = NoType(op) + flag;
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

        // 区间的金额：号数 × 0.01。
        public static decimal RangeAmount(long start, long end)
        {
            return (end - start + 1) / 100m;
        }

        // 金额对应的号数（两位小数的金额才有整数个号）。
        public static long Count(decimal amount)
        {
            return (long)decimal.Round(amount * 100m, 0);
        }

        // 贴现净额：U8 贴现界面的校验串是「净额-利息+费用=本次」，即 净额 = 本次金额 + 票据利息 - 贴现费用
        // （例：99000.00 = 100000 + 0 - 1000.00）。
        public static decimal Net(decimal amount, decimal interest, decimal expense)
        {
            return amount + interest - expense;
        }

        // 处理行（往来明细 50 行）的单号：分包票据带子票区间后缀，否则就是票据号。
        public static string DetailId(string note, long start, long end)
        {
            if (start <= 0)
            {
                return note;
            }
            return note + "-" + start.ToString(CultureInfo.InvariantCulture) + "-" + end.ToString(CultureInfo.InvariantCulture);
        }

        // 选子票区间：给了区间就必须整个落在某一段可用区间里；没给就从最小号起取 amount 对应的号数（amount 为 null 取第一段整段）。
        // 选不出来返回 null（调用方 409）。
        public static NoteRange Pick(List<NoteRange> avail, decimal? amount, long start, long end)
        {
            List<NoteRange> sorted = Sorted(avail);
            if (start > 0)
            {
                foreach (NoteRange r in sorted)
                {
                    if (start >= r.Start && end <= r.End)
                    {
                        return new NoteRange(start, end);
                    }
                }
                return null;
            }
            if (sorted.Count == 0)
            {
                return null;
            }
            NoteRange first = sorted[0];
            if (amount == null)
            {
                return new NoteRange(first.Start, first.End);
            }
            long n = Count(amount.Value);
            return n >= 1 && first.Start + n - 1 <= first.End ? new NoteRange(first.Start, first.Start + n - 1) : null;
        }

        // 取走 used 之后剩下的可用区间（按起号排序）。
        public static List<NoteRange> Leftover(List<NoteRange> avail, NoteRange used)
        {
            List<NoteRange> left = new List<NoteRange>();
            foreach (NoteRange r in Sorted(avail))
            {
                if (used.End < r.Start || used.Start > r.End)
                {
                    left.Add(new NoteRange(r.Start, r.End));
                    continue;
                }
                if (used.Start > r.Start)
                {
                    left.Add(new NoteRange(r.Start, used.Start - 1));
                }
                if (used.End < r.End)
                {
                    left.Add(new NoteRange(used.End + 1, r.End));
                }
            }
            return left;
        }

        public static decimal Sum(List<NoteRange> ranges)
        {
            decimal sum = 0m;
            foreach (NoteRange r in ranges)
            {
                sum += RangeAmount(r.Start, r.End);
            }
            return sum;
        }

        static List<NoteRange> Sorted(List<NoteRange> avail)
        {
            List<NoteRange> sorted = new List<NoteRange>(avail);
            sorted.Sort(delegate(NoteRange a, NoteRange b) { return a.Start.CompareTo(b.Start); });
            return sorted;
        }

        // 缺省摘要用中性模板，最多 120 字。
        public static string Digest(string op, string flag, string partner, string vendor)
        {
            string text;
            switch (op)
            {
                case "discount":
                    text = "收" + partner + "票据贴现";
                    break;
                case "endorse":
                    text = "付" + vendor + "票据背书";
                    break;
                default:
                    text = flag == "AP" ? "付" + partner + "票据到期结算" : "收" + partner + "票据到期托收";
                    break;
            }
            return text.Length > ArapVoucherReq.DigestMax ? text.Substring(0, ArapVoucherReq.DigestMax) : text;
        }

        // 退回的缺省摘要：「退回」+ 客户（供应商）名称 +「电子承兑」（两条往来明细同一摘要；
        // U8 的写法与票据的结算方式无关），最多 120 字。
        public static string ReturnDigest(string partner)
        {
            string text = "退回" + partner + "电子承兑";
            return text.Length > ArapVoucherReq.DigestMax ? text.Substring(0, ArapVoucherReq.DigestMax) : text;
        }

        public static string Money(decimal value)
        {
            return value.ToString("0.00", CultureInfo.InvariantCulture);
        }
    }
}
