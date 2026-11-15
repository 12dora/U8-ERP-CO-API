using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的外币应收 / 应付单行币种（ArapLineFx）：按科目决定行用表头外币还是本位币、金额与合计。不连库、不建 COM。
    internal static class ArapLineFxSelfTest
    {
        public static void Run()
        {
            CheckKeeps();
            CheckApply();
            CheckLoaded();
            CheckBalance();
            CheckEditDelete();
            CheckEditRefuse();
            CheckEditKeep();
        }

        static void CheckKeeps()
        {
            Expect("fx account same", ArapLineFx.KeepsHead("美元", "美元"));
            Expect("fx plain account", !ArapLineFx.KeepsHead("", "美元"));
            Expect("fx other account", !ArapLineFx.KeepsHead("欧元", "美元"));
        }

        // 美元单据、汇率 7.1：收入科目行 20 美元 → 人民币、1、142.00；外币科目行保留美元、7.1、原币。
        static void CheckApply()
        {
            ArapInput input = Usd();
            ArapLine plain = Line(20m, input);
            ArapLineFx.Apply(plain, false, input);
            Expect("fx home currency", plain.Currency == "人民币" && plain.Rate == 1m);
            Expect("fx home amounts", plain.Amt == 142.00m && plain.AmtF == 142.00m && plain.Orig == 20m);
            ArapLine fx = Line(20.07m, input);
            ArapLineFx.Apply(fx, true, input);
            Expect("fx keep head", fx.Currency == "美元" && fx.Rate == 7.1m);
            Expect("fx keep amounts", fx.AmtF == 20.07m && fx.Orig == 20.07m && fx.Amt == 142.50m);
            ArapInput home = Usd();
            home.Currency = "人民币";
            home.Rate = 1m;
            ArapLine local = Line(30m, home);
            ArapLineFx.Apply(local, false, home);
            Expect("fx home bill", local.Currency == "人民币" && local.Rate == 1m && local.AmtF == 30m);
            // 表头：原币合计取各行原币，本币合计取各行本币。
            Expect("fx head sums", plain.Orig + fx.Orig == 40.07m && plain.Amt + fx.Amt == 284.50m);
        }

        // 修改时载入的行：记本位币的行按 round(本币 / 汇率, 2) 折回原币；与表头同币种的行原币照用。
        static void CheckLoaded()
        {
            Expect("fx float rate", ArapEditBuild.Rate("7.0999999999999996") == 7.1m && ArapEditBuild.Rate("") == 0m);
            ArapInput input = Usd();
            ArapLine rmb = new ArapLine();
            rmb.Currency = "人民币";
            rmb.Amt = 142.00m;
            rmb.AmtF = 142.00m;
            Expect("fx loaded home", ArapLineFx.LoadedOrig(rmb, input) == 20.00m);
            ArapLine usd = new ArapLine();
            usd.Currency = "美元";
            usd.Amt = 142.50m;
            usd.AmtF = 20.07m;
            Expect("fx loaded head", ArapLineFx.LoadedOrig(usd, input) == 20.07m);
            usd.Currency = "";
            Expect("fx loaded blank", ArapLineFx.LoadedOrig(usd, input) == 20.07m);
        }

        // 汇率 1.005，两行各 1.00 美元：逐行 1.01 + 1.01 = 2.02，表头 round(2.00 × 1.005, 2) = 2.01，尾差 −0.01 并到第一行可接尾差的行。
        static void CheckBalance()
        {
            ArapInput input = Usd();
            input.Rate = 1.005m;
            ArapLine a = Line(1.00m, input);
            ArapLine b = Line(1.00m, input);
            ArapLineFx.Apply(a, false, input);
            ArapLineFx.Apply(b, false, input);
            Expect("fx eligible", ArapLineFx.Eligible(a, input));
            List<ArapLine> two = new List<ArapLine>(new ArapLine[] { a, b });
            Expect("fx balance moved", ArapLineFx.Balance(input, two, 2.00m, a, null) == a);
            Expect("fx balance first", a.Amt == 1.00m && a.AmtF == 1.00m && b.Amt == 1.01m);
            Expect("fx balance head", input.Sum == 2.01m && input.SumF == 2.00m);
            // 调用方给了本币的行不接尾差：没有可接的行而差额不为 0 时 400（U8 要求表头本币 = round(原币 × 汇率, 2)）。
            ArapLine c = Line(1.00m, input);
            ArapLine d = Line(1.00m, input);
            c.Derived = false;
            d.Derived = false;
            ArapLineFx.Apply(c, false, input);
            ArapLineFx.Apply(d, false, input);
            Expect("fx given not eligible", !ArapLineFx.Eligible(c, input));
            List<ArapLine> given = new List<ArapLine>(new ArapLine[] { c, d });
            Expect("fx balance none", Refused(input, given, 2.00m) && c.Amt == 1.01m && d.Amt == 1.01m);
            // 差额为 0 时什么都不调，也不要候选行。
            Expect("fx balance exact", ArapLineFx.Balance(input, given, 2.01m, null, null) == null && input.Sum == 2.02m);
            ArapLine fx = Line(1.00m, input);
            ArapLineFx.Apply(fx, true, input);
            Expect("fx keep not eligible", !ArapLineFx.Eligible(fx, input));
        }

        // 示例：美元 7.1，表头 2.40 / 17.04；行 7 人民币 1.77、行 8 人民币 1.07、行 9 美元 1.00 / 7.10、
        // 行 10 人民币 7.10。只删行 8：表头原币 2.40 − round(1.07 / 7.1, 2) = 2.25，round(2.25 × 7.1, 2) = 15.98，各行之和 15.97；
        // 尾差 0.01 并到第一行剩余的本位币行（行 7 → 1.78，不动的行转为修改行），表头本币 15.98。不连库（删除、不动的行不查科目）。
        static void CheckEditDelete()
        {
            ArapEditPlan plan = Plan(17.04m, 2.40m);
            ArapEditRow seven = Stored("", "人民币", 1.77m, 1.77m);
            plan.Rows.Add(seven);
            plan.Rows.Add(Stored("delete", "人民币", 1.07m, 1.07m));
            plan.Rows.Add(Stored("", "美元", 7.10m, 1.00m));
            plan.Rows.Add(Stored("", "人民币", 7.10m, 7.10m));
            ArapLineFx.ForEdit(null, Spec(), plan);
            Expect("fx edit delete head", plan.Input.Sum == 15.98m && plan.Input.SumF == 2.25m);
            Expect("fx edit delete residue", seven.Line.Amt == 1.78m && seven.Line.AmtF == 1.78m);
            Expect("fx edit delete written", seven.Op == "update" && seven.AmountGiven);
        }

        // 只剩外币科目的行、删行后差额不为 0：没有本位币行可接尾差，400（field lines）。
        static void CheckEditRefuse()
        {
            ArapEditPlan plan = Plan(2.85m, 0.40m);
            plan.Rows.Add(Stored("", "美元", 1.07m, 0.15m));
            plan.Rows.Add(Stored("", "美元", 1.07m, 0.15m));
            plan.Rows.Add(Stored("delete", "美元", 0.71m, 0.10m));
            try
            {
                ArapLineFx.ForEdit(null, Spec(), plan);
            }
            catch (BridgeException ex)
            {
                Expect("fx edit refuse", ex.Status == 400 && ex.Field == "lines");
                return;
            }
            throw new InvalidOperationException("fx edit refuse");
        }

        static bool Refused(ArapInput input, List<ArapLine> lines, decimal sumF)
        {
            try
            {
                ArapLineFx.Balance(input, lines, sumF, null, null);
            }
            catch (BridgeException ex)
            {
                return ex.Status == 400 && ex.Field == "lines";
            }
            return false;
        }

        // 只改表头（行都不动）：日元 0.0478 这类低汇率下也不按行折回重算，表头合计原样保留。
        static void CheckEditKeep()
        {
            ArapEditPlan plan = Plan(47.80m, 1000.00m);
            plan.Input.Currency = "日元";
            plan.Input.Rate = 0.0478m;
            plan.Rows.Add(Stored("", "人民币", 23.90m, 23.90m));
            plan.Rows.Add(Stored("", "人民币", 23.90m, 23.90m));
            ArapLineFx.ForEdit(null, Spec(), plan);
            Expect("fx edit keep", plan.Input.Sum == 47.80m && plan.Input.SumF == 1000.00m);
        }

        static ArapEditPlan Plan(decimal sum, decimal sumF)
        {
            ArapEditPlan plan = new ArapEditPlan();
            plan.Input = Usd();
            plan.Input.Date = "2026-10-02";
            plan.Rows = new List<ArapEditRow>();
            plan.OldSum = sum;
            plan.OldSumF = sumF;
            return plan;
        }

        static ArapEditRow Stored(string op, string currency, decimal amt, decimal amtF)
        {
            ArapEditRow r = new ArapEditRow();
            r.Op = op;
            r.Line = new ArapLine();
            r.Line.Fields = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            r.Line.Currency = currency;
            r.Line.Amt = amt;
            r.Line.AmtF = amtF;
            r.OldCurrency = currency;
            r.OldAmt = amt;
            r.OldAmtF = amtF;
            r.Refs = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            return r;
        }

        static ArapSpec Spec()
        {
            ArapSpec spec = new ArapSpec();
            spec.Flag = "AR";
            spec.VouchType = "R0";
            return spec;
        }

        static ArapInput Usd()
        {
            ArapInput input = new ArapInput();
            input.Head = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            input.Lines = new List<ArapLine>();
            input.Home = "人民币";
            input.Currency = "美元";
            input.Rate = 7.1m;
            return input;
        }

        static ArapLine Line(decimal orig, ArapInput input)
        {
            ArapLine line = new ArapLine();
            line.Fields = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            line.AmtF = orig;
            line.Amt = decimal.Round(orig * input.Rate, 2, MidpointRounding.AwayFromZero);
            line.Derived = true;
            return line;
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException(name);
            }
        }
    }
}
