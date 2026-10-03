using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的结转补充部分：数据权限、自定义转账的前后依赖、期间损益补生成、部分保存的补救文案。
    // 不连库。由 GlTransferSelfTest.Run 调用。
    internal static class GlTransferFixSelfTest
    {
        public static void Run()
        {
            CheckPerm();
            CheckPermLines();
            CheckChain();
            CheckYear();
            CheckDone();
            CheckRemedy();
        }

        // 科目、部门、项目受控：科目只给 6001 / 4103，部门只给 D1，项目只给 P:I1。
        static void CheckPerm()
        {
            GlTransferCodes codes = Codes();
            PermContext p = Controlled();
            Expect("perm ok", GlTransferPerm.Problem(p, "6001", Aux("D1", ""), codes) == null);
            Expect("perm account", GlTransferPerm.Problem(p, "6602", null, codes) == "科目 6602");
            Expect("perm dept", GlTransferPerm.Problem(p, "6001", Aux("D2", ""), codes) == "部门 D2");
            Expect("perm item by account class", GlTransferPerm.Problem(p, "6001", Aux("", "I1"), codes) == null);
            Expect("perm item denied", GlTransferPerm.Problem(p, "6001", Aux("", "I2"), codes) != null);
            p.GlSubjCtl = false;
            Expect("perm subject option off", GlTransferPerm.Problem(p, "6602", null, codes) == null);
            PermContext boss = Controlled();
            boss.Supervisor = true;
            Expect("perm supervisor", GlTransferPerm.Problem(boss, "6602", Aux("D2", "I2"), codes) == null);
            Expect("perm no snapshot", Status(delegate { GlTransferPerm.Check(null, "6001", null, codes); }) == 500);
            Expect("perm accounts 403", Status(delegate { GlTransferPerm.Accounts(Controlled(), new string[] { "4103", "6602" }); }) == 403);
            List<string[]> pairs = new List<string[]>();
            pairs.Add(new string[] { "1", "转", "6001", "4103" });
            Expect("perm pairs ok", Status(delegate { GlTransferPerm.Pairs(Controlled(), pairs); }) == 0);
        }

        static void CheckPermLines()
        {
            GlTransferCodes codes = Codes();
            GlTransferPlan plan = new GlTransferPlan();
            GlTransferVoucher v = new GlTransferVoucher();
            v.Draft = new GlDraft();
            v.Draft.Lines.Add(GlTransferLines.Line("6001", "d", true, 10m, Aux("D1", "")));
            v.Draft.Lines.Add(GlTransferLines.Line("4103", "d", false, 10m, null));
            plan.Vouchers.Add(v);
            Expect("perm lines ok", Status(delegate { GlTransferPerm.Lines(Controlled(), plan, codes); }) == 0);
            v.Draft.Lines[0].Dept = "D9";
            Expect("perm lines 403", Status(delegate { GlTransferPerm.Lines(Controlled(), plan, codes); }) == 403);
            List<GlTransferBalRow> rows = new List<GlTransferBalRow>();
            GlTransferBalRow row = new GlTransferBalRow();
            row.Code = "6001";
            row.Aux = Aux("D2", "");
            rows.Add(row);
            Expect("perm source rows 403", Status(delegate { GlTransferPerm.Rows(Controlled(), rows, codes); }) == 403);
        }

        static void CheckChain()
        {
            GlCustomDef def = new GlCustomDef();
            def.TranId = "T002";
            def.Formulas.Add(GlTransferFormula.Parse("QM(6401,月)", "t："));
            List<string[]> made = new List<string[]>();
            GlTransferAsk ask = new GlTransferAsk();
            Expect("chain first", Status(delegate { GlTransferCustom.Chain(ask, def, made); }) == 0);
            made.Add(new string[] { "T001", "64019901" });
            Expect("chain overlap", Status(delegate { GlTransferCustom.Chain(ask, def, made); }) == 409);
            ask.Exclude = true;
            Expect("chain exclude", Status(delegate { GlTransferCustom.Chain(ask, def, made); }) == 0);
            ask.Exclude = false;
            ask.TranId = "T002";
            Expect("chain one tran", Status(delegate { GlTransferCustom.Chain(ask, def, made); }) == 0);
            ask.TranId = "";
            made[0][1] = "6602";
            Expect("chain disjoint", Status(delegate { GlTransferCustom.Chain(ask, def, made); }) == 0);
        }

        // 按年取数的定义：给 tran_id 时 400，不给时跳过（返回原因）；只按月取数的不拦。
        static void CheckYear()
        {
            GlCustomDef def = new GlCustomDef();
            def.TranId = "T100";
            def.Formulas.Add(GlTransferFormula.Parse("QM(4103,年,借)", "t："));
            def.Formulas.Add(GlTransferFormula.Parse("QM(4103,年,贷)*0.9", "t："));
            GlTransferAsk ask = new GlTransferAsk();
            ask.Period = 12;
            string why = GlTransferCustom.YearGate(ask, def);
            Expect("year skip", why != null && why.IndexOf("按年取数", StringComparison.Ordinal) >= 0);
            ask.TranId = "T100";
            Expect("year tran 400", Status(delegate { GlTransferCustom.YearGate(ask, def); }) == 400);
            GlCustomDef month = new GlCustomDef();
            month.TranId = "T001";
            month.Formulas.Add(GlTransferFormula.Parse("QM(6401,月)", "t："));
            month.Formulas.Add(GlTransferFormula.Parse("CE()", "t："));
            Expect("month ok", GlTransferCustom.YearGate(ask, month) == null);
        }

        static void CheckDone()
        {
            GlTransferCodes codes = Codes();
            Expect("pack of", GlTransferPnlDone.PackOf(codes, "6001") == GlTransferPnl.Income
                && GlTransferPnlDone.PackOf(codes, "6602") == GlTransferPnl.Expense && GlTransferPnlDone.PackOf(codes, "4103") == null);
            List<string[]> existing = new List<string[]>();
            existing.Add(new string[] { "转-355", "转", "355", GlTransferPnl.Income });
            GlTransferPlan plan = Plan(GlTransferPnl.Expense);
            GlTransferPnlDone.Filter(plan, existing);
            Dictionary<string, object> one = (Dictionary<string, object>)plan.Existing[0];
            Expect("done completes missing", plan.Existing.Count == 1 && (int)one["no"] == 355);
            Expect("done same pack", Status(delegate { GlTransferPnlDone.Filter(Plan(GlTransferPnl.Income), existing); }) == 409);
            Expect("done nothing missing", Status(delegate { GlTransferPnlDone.Filter(Plan(null), existing); }) == 409);
            existing[0][3] = GlTransferPnlDone.Unknown;
            Expect("done unknown", Status(delegate { GlTransferPnlDone.Filter(Plan(GlTransferPnl.Expense), existing); }) == 409);
            plan = Plan(GlTransferPnl.Income);
            GlTransferPnlDone.Filter(plan, new List<string[]>());
            Expect("done none", plan.Existing.Count == 0);
        }

        static void CheckRemedy()
        {
            GlTransferPlan plan = Plan(GlTransferPnl.Income);
            plan.Ask = new GlTransferAsk();
            plan.Ask.Pnl = true;
            GlTransferVoucher second = new GlTransferVoucher();
            second.Pack = GlTransferPnl.Expense;
            plan.Vouchers.Add(second);
            Expect("remedy none saved", GlTransferSave.Remedy(plan) == "");
            plan.Vouchers[0].Key = new GlKey();
            plan.Vouchers[0].Key.No = 5;
            Expect("remedy pnl", GlTransferSave.Remedy(plan).IndexOf("gl/transfer/pnl", StringComparison.Ordinal) >= 0);
            second.Key = new GlKey();
            second.Key.No = 6;
            Expect("remedy all saved", GlTransferSave.Remedy(plan) == "");
        }

        static GlTransferPlan Plan(string pack)
        {
            GlTransferPlan plan = new GlTransferPlan();
            if (pack != null)
            {
                GlTransferVoucher v = new GlTransferVoucher();
                v.Pack = pack;
                plan.Vouchers.Add(v);
            }
            return plan;
        }

        static PermContext Controlled()
        {
            PermContext p = new PermContext();
            p.Acc = "999";
            p.Operator = "U1";
            p.Year = 2025;
            p.DateYear = 2025;
            p.AcctYear = 2025;
            p.GlSubjCtl = true;
            p.On.Add(PermObj.Account);
            p.On.Add(PermObj.Department);
            p.On.Add(PermObj.Item);
            p.Codes[PermObj.Account] = Set("6001", "4103");
            p.Codes[PermObj.Department] = Set("D1");
            p.Codes[PermObj.Item] = Set("P" + PermContext.PairSep + "I1");
            return p;
        }

        static HashSet<string> Set(params string[] codes)
        {
            return new HashSet<string>(codes, StringComparer.OrdinalIgnoreCase);
        }

        static GlTransferCodes Codes()
        {
            GlTransferCodes codes = new GlTransferCodes();
            codes.Add(Code("6001", "0", "损益", "P"));
            codes.Add(Code("6602", "1", "损益", ""));
            codes.Add(Code("4103", "0", "权益", ""));
            return codes;
        }

        static Dictionary<string, object> Code(string code, string property, string cls, string itemClass)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["ccode"] = code;
            row["bend"] = "1";
            row["bproperty"] = property;
            row["cclass"] = cls;
            row["cass_item"] = itemClass;
            return row;
        }

        static string[] Aux(string dept, string item)
        {
            return new string[] { dept, "", "", "", "", item };
        }

        // 运行一段代码：不抛异常为 0，BridgeException 为它的状态码。
        static int Status(Action action)
        {
            try
            {
                action();
                return 0;
            }
            catch (BridgeException ex)
            {
                return ex.Status;
            }
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("gl transfer fix " + name);
            }
        }
    }
}
