using System;
using System.Collections.Generic;
using System.Globalization;

namespace U8Co
{
    // 外币应收单 / 应付单（R0/P0）表体的币种：按行科目（code，单据日期所在年度）决定。
    // 科目核算币种（code.cexch_name）等于表头币种：行用表头币种、汇率和原币金额；
    // 否则行记本位币：汇率 1，原币金额 = 本币金额。bexchange 是期末调汇标志，不参与判断（测试账套美元户 bexchange=0）。
    // 实测（U8 客户端录入的美元应收单 / 应付单）：收入、费用等非外币科目的行一律存「人民币、1、本币金额」；
    // 行上写表头外币，U8 回「第N行的币种与科目不一致」。表头本币必须等于各行本币之和，否则 U8 回「借贷不平」。
    internal static class ArapLineFx
    {
        // accountCurrency：code.cexch_name（空串表示不核算外币）。
        internal static bool KeepsHead(string accountCurrency, string head)
        {
            return accountCurrency.Length > 0 && accountCurrency == head;
        }

        // 调用前 line.AmtF 是表头币种的原币、line.Amt 是本币。Orig 记下原币，供表头原币合计。
        internal static void Apply(ArapLine line, bool keep, ArapInput input)
        {
            line.Orig = line.AmtF;
            if (keep || !ArapReq.IsForeign(input))
            {
                line.Currency = input.Currency;
                line.Rate = input.Rate;
                return;
            }
            line.Currency = input.Home;
            line.Rate = 1m;
            line.AmtF = line.Amt;
        }

        // 已存的行：行币种与表头相同（或空）时原币就是 iAmount_f；记本位币的行没有原币，按 round(本币 / 汇率, 2) 折回。
        internal static decimal StoredOrig(string currency, decimal amt, decimal amtF, ArapInput input)
        {
            string cur = currency ?? "";
            if (cur.Length == 0 || cur == input.Currency || !ArapReq.IsForeign(input))
            {
                return amtF;
            }
            return decimal.Round(amt / input.Rate, 2, MidpointRounding.AwayFromZero);
        }

        internal static decimal LoadedOrig(ArapLine line, ArapInput input)
        {
            return StoredOrig(line.Currency, line.Amt, line.AmtF, input);
        }

        // 能接尾差的行：本币由桥按 round(原币 × 汇率, 2) 算出（调用方没给本币）、且记本位币。
        internal static bool Eligible(ArapLine line, ArapInput input)
        {
            return line.Derived && line.Currency == input.Home;
        }

        // 新增：本位币单据、收付款单不用管（行照旧写表头币种）。表头合计按 Balance 重算。
        public static void ForCreate(object conn, ArapSpec spec, ArapInput input)
        {
            if (spec.Close || !ArapReq.IsForeign(input))
            {
                return;
            }
            int year = Year(input.Date);
            Dictionary<string, bool> seen = new Dictionary<string, bool>(StringComparer.OrdinalIgnoreCase);
            ArapLine first = null;
            decimal sumF = 0m;
            for (int i = 0; i < input.Lines.Count; i++)
            {
                ArapLine line = input.Lines[i];
                bool keep = Keeps(conn, year, Text(line.Fields, "ccode"), input, FieldPath.Item("lines", i), seen);
                Apply(line, keep, input);
                sumF += line.Orig;
                if (first == null && Eligible(line, input))
                {
                    first = line;
                }
            }
            // 新增时调用方没给本币的本位币行就是可接尾差的行，没有第二候选。
            Balance(input, input.Lines, sumF, first, null);
        }

        // 表头原币 = sumF。U8 保存时同时要求：表头本币 = 各行本币之和（借贷平），且表头本币 = round(表头原币 × 汇率, 2)。
        // 两者不等时把差额并到一行本位币行：先取 first（本次送了原币、本币由桥算出的行），没有再取 fallback
        // （本次调用方没给本币的本位币行，修改时含不动的行）。差额超过 0.01 × 行数、调整后该行不大于 0、或没有可调的行：
        // 400，在调用 U8 之前。返回调过的行（没调为 null）。
        internal static ArapLine Balance(ArapInput input, List<ArapLine> lines, decimal sumF, ArapLine first, ArapLine fallback)
        {
            decimal sum = 0m;
            for (int i = 0; i < lines.Count; i++)
            {
                sum += lines[i].Amt;
            }
            input.Sum = sum;
            input.SumF = sumF;
            decimal want = decimal.Round(sumF * input.Rate, 2, MidpointRounding.AwayFromZero);
            decimal diff = want - sum;
            if (diff == 0m)
            {
                return null;
            }
            ArapLine take = first ?? fallback;
            if (take == null || Math.Abs(diff) > 0.01m * lines.Count || take.Amt + diff <= 0m)
            {
                throw new BridgeException(400, "bad_request", "表头本币 " + ArapReq.Money(sum) + " 与原币合计 "
                    + ArapReq.Money(sumF) + " × 汇率 " + ArapReq.Plain(input.Rate) + " = " + ArapReq.Money(want)
                    + " 不一致，请调整某一记本位币行的本币金额", "lines");
            }
            take.Amt += diff;
            take.AmtF = take.Amt;
            input.Sum = want;
            return take;
        }

        // 修改：表头原币从载入的表头原币出发，只加减金额真正变了的行（删除、新增、送了金额、换科目改了币种）的原币差；
        // 这些行都没有时表头合计照原值不动。否则按 Balance 定表头本币和尾差行（不动的本位币行也可接尾差）。
        public static void ForEdit(object conn, ArapSpec spec, ArapEditPlan plan)
        {
            ArapInput input = plan.Input;
            if (spec.Close || !ArapReq.IsForeign(input))
            {
                return;
            }
            EditSums sums = new EditSums();
            sums.SumF = plan.OldSumF;
            sums.Lines = new List<ArapLine>();
            sums.Seen = new Dictionary<string, bool>(StringComparer.OrdinalIgnoreCase);
            sums.Year = Year(input.Date);
            for (int i = 0; i < plan.Rows.Count; i++)
            {
                EditRow(conn, input, plan.Rows[i], sums);
            }
            if (!sums.Moved)
            {
                input.Sum = plan.OldSum;
                input.SumF = plan.OldSumF;
                return;
            }
            ArapLine moved = Balance(input, sums.Lines, sums.SumF, Of(sums.First), Of(sums.Fallback));
            if (moved != null)
            {
                // 不动的行接了尾差就成了修改行：editprop=M，按新金额重拆税额（ArapEditDom.Revise）。
                ArapEditRow taken = moved == Of(sums.First) ? sums.First : sums.Fallback;
                taken.AmountGiven = true;
                if (taken.Op == "")
                {
                    taken.Op = "update";
                }
            }
        }

        static ArapLine Of(ArapEditRow r)
        {
            return r == null ? null : r.Line;
        }

        sealed class EditSums
        {
            public decimal SumF;
            public bool Moved;
            public List<ArapLine> Lines;
            public ArapEditRow First;
            public ArapEditRow Fallback;
            public Dictionary<string, bool> Seen;
            public int Year;
        }

        static void EditRow(object conn, ArapInput input, ArapEditRow r, EditSums sums)
        {
            decimal old = r.Op == "add" ? 0m : StoredOrig(r.OldCurrency, r.OldAmt, r.OldAmtF, input);
            if (r.Op == "delete")
            {
                sums.Moved = true;
                sums.SumF -= old;
                return;
            }
            sums.Lines.Add(r.Line);
            if (r.Op != "update" && r.Op != "add")
            {
                r.Line.Orig = old;
                if (sums.Fallback == null && r.Line.Currency == input.Home)
                {
                    sums.Fallback = r;
                }
                return;
            }
            Written(conn, input, r, sums, old);
        }

        // 修改行、新增行：按科目重定币种。新增行、送了金额的修改行 Line.Currency 为 null，金额已是「原币、本币」形态。
        static void Written(object conn, ArapInput input, ArapEditRow r, EditSums sums, decimal old)
        {
            ArapLine line = r.Line;
            bool fresh = line.Currency == null;
            if (!fresh)
            {
                line.AmtF = LoadedOrig(line, input);
            }
            Apply(line, Keeps(conn, sums.Year, Text(r.Refs, "ccode"), input, At(r), sums.Seen), input);
            bool switched = r.Op == "update" && (r.OldCurrency ?? "") != line.Currency;
            if (switched)
            {
                r.AmountGiven = true;
            }
            if (fresh || switched)
            {
                sums.Moved = true;
                sums.SumF += line.Orig - old;
            }
            Candidate(input, r, fresh, sums);
        }

        // 尾差候选：First 是本次送了原币、本币由桥算出的本位币行；Fallback 是调用方这次没给本币的本位币行。
        static void Candidate(ArapInput input, ArapEditRow r, bool fresh, EditSums sums)
        {
            ArapLine line = r.Line;
            if (sums.First == null && fresh && Eligible(line, input))
            {
                sums.First = r;
            }
            if (sums.Fallback == null && line.Currency == input.Home && (!fresh || line.Derived))
            {
                sums.Fallback = r;
            }
        }

        // 科目核算的外币既不是表头币种也不是本位币：U8 同样会拒绝，提前 400。
        static bool Keeps(object conn, int year, string code, ArapInput input, string at, Dictionary<string, bool> seen)
        {
            bool keep;
            if (seen.TryGetValue(code, out keep))
            {
                return keep;
            }
            Dictionary<string, object> row = Rows.One(conn,
                "select isnull(cexch_name,N'') as cur from code where iyear=? and ccode=?", new object[] { year, code });
            if (row == null)
            {
                throw new BridgeException(400, "bad_request", "科目不存在 " + code, FieldPath.Join(at, "ccode"));
            }
            string cur = CoRows.Col(row, "cur").Trim();
            if (cur.Length > 0 && cur != input.Currency && cur != input.Home)
            {
                throw new BridgeException(400, "bad_request",
                    "科目 " + code + " 按" + cur + "核算，与单据币种" + input.Currency + "不一致", FieldPath.Join(at, "ccode"));
            }
            keep = KeepsHead(cur, input.Currency);
            seen[code] = keep;
            return keep;
        }

        // 400 的 field：该行在请求 lines 里的下标，与新增一致（lines.N.ccode）。
        static string At(ArapEditRow r)
        {
            return r.At >= 0 ? FieldPath.Item("lines", r.At) : "lines";
        }

        static int Year(string date)
        {
            return int.Parse(date.Substring(0, 4), CultureInfo.InvariantCulture);
        }

        static string Text(Dictionary<string, string> map, string key)
        {
            string value;
            return map != null && map.TryGetValue(key, out value) ? value : "";
        }
    }
}
