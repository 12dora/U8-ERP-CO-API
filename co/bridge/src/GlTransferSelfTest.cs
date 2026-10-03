using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的结转部分（gl/transfer/pnl、gl/transfer/custom）：请求校验、锁键、公式解析与计算、QM 取数、CE 差额、
    // 期间损益分组与行序、回读核对、写入分类与测试账套登记。不连库。由 GlPostSelfTest.Run 调用。
    internal static class GlTransferSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckFormula();
            CheckQm();
            CheckPnl();
            CheckCompare();
            CheckRegistry();
            // 结转补充：数据权限、前后依赖、期间损益补生成（GlTransferFixSelfTest）。
            GlTransferFixSelfTest.Run();
        }

        static void CheckParse()
        {
            Dictionary<string, object> body = Body(2025, 6);
            GlTransferAsk ask = GlTransferReq.Parse(GlTransferReq.PnlPath, body, false, 2025);
            Expect("parse pnl", ask.Pnl && ask.Year == 2025 && ask.Period == 6 && ask.Date == "" && !ask.Exclude);
            Expect("last day", GlTransferReq.LastDay(2025, 6) == "2025-06-30" && GlTransferReq.LastDay(2024, 2) == "2024-02-29");
            body["exclude_existing"] = true;
            Expect("exclude needs dry", Code(GlTransferReq.PnlPath, body, false, 0) == "exclude_existing");
            Expect("exclude dry", GlTransferReq.Parse(GlTransferReq.PnlPath, body, true, 0).Exclude);
            body = Body(2025, 6);
            body["voucher_date"] = "2025-07-01";
            Expect("date outside period", Code(GlTransferReq.PnlPath, body, false, 0) == "voucher_date");
            body["voucher_date"] = "2025-06-15";
            Expect("date inside", GlTransferReq.Parse(GlTransferReq.PnlPath, body, false, 0).Date == "2025-06-15");
            Expect("login year", Code(GlTransferReq.PnlPath, Body(2025, 6), false, 2026) == "fiscal_year");
            body = Body(2025, 13);
            Expect("period range", Code(GlTransferReq.PnlPath, body, false, 0) == "period");
            body = Body(2025, 6);
            body["tran_id"] = "T001";
            Expect("custom tran", GlTransferReq.Parse(GlTransferReq.CustomPath, body, false, 0).TranId == "T001");
            body["tran_id"] = "00 1";
            Expect("custom tran bad", Code(GlTransferReq.CustomPath, body, false, 0) == "tran_id");
            body.Remove("fiscal_year");
            Expect("year required", Code(GlTransferReq.PnlPath, body, false, 0) == "fiscal_year");
            string[] keys = GlTransferReq.LockKeys(Body(2025, 6));
            Expect("keys", keys[0] == "gl:transfer:2025-6" && keys[1] == "gl:post" && Array.IndexOf(keys, "new:gl:转") > 0);
            Expect("paths", GlTransferReq.IsPath(GlTransferReq.CustomPath) && !GlTransferReq.IsPath(Requests.GlRoot + "create")
                && GlTransferReq.ActionOf(GlTransferReq.PnlPath) == "gl_transfer_pnl");
        }

        static void CheckFormula()
        {
            GlFormula f = GlTransferFormula.Parse("QM(4103,年,贷)*0.1", "t：");
            Expect("qm year side", f.Atoms.Count == 1 && f.Atoms[0].Year && f.Atoms[0].Side == "贷" && GlTransferFormula.UsesYear(f));
            Expect("eval mul", GlTransferFormula.Eval(f.Root, new decimal[] { 1000m }) == 100m);
            f = GlTransferFormula.Parse("QM（64019901，月，，D901）", "t：");
            Expect("qm aux", f.Atoms[0].Code == "64019901" && !f.Atoms[0].Year && f.Atoms[0].Side == "" && f.Atoms[0].Aux == "D901");
            Expect("ce", GlTransferFormula.Parse(" ce() ", "t：").Ce);
            f = GlTransferFormula.Parse("-(QM(1,月)+QM(2,月))/2-3", "t：");
            Expect("eval expr", GlTransferFormula.Eval(f.Root, new decimal[] { 10m, 4m }) == -10m);
            Expect("unsupported fn", Message("FS(6001,月,贷)").IndexOf("FS", StringComparison.Ordinal) >= 0);
            Expect("unsupported period", Message("QM(6001,季)").IndexOf("季", StringComparison.Ordinal) >= 0);
            Expect("unsupported aux2", Message("QM(6001,月,,D1,P1)").Length > 0);
            Expect("syntax", Message("QM(6001,月)+").Length > 0 && Message("QM(6001,月").Length > 0);
        }

        static void CheckQm()
        {
            GlTransferCodes codes = Codes();
            List<GlTransferBalRow> rows = new List<GlTransferBalRow>();
            rows.Add(Bal("64019901", 300m, "D901"));
            rows.Add(Bal("64019901", 200m, "D902"));
            rows.Add(Bal("6001", -800m, null));
            GlQm qm = Qm("64019901", "", "D901");
            Expect("qm dept", GlTransferCustomCalc.Qm(qm, rows, codes) == 300m);
            Expect("qm total", GlTransferCustomCalc.Qm(Qm("6401", "", ""), rows, codes) == 500m);
            Expect("qm credit nature", GlTransferCustomCalc.Qm(Qm("6001", "", ""), rows, codes) == 800m);
            Expect("qm side", GlTransferCustomCalc.Qm(Qm("6001", "贷", ""), rows, codes) == 800m
                && GlTransferCustomCalc.Qm(Qm("6001", "借", ""), rows, codes) == 0m);
            GlCustomDef def = new GlCustomDef();
            def.Rows.Add(Def("64019902", true, "CE()"));
            def.Rows.Add(Def("64019901", false, "QM(64019901,月,,D901)"));
            foreach (GlTransferDef row in def.Rows)
            {
                def.Formulas.Add(GlTransferFormula.Parse(row.Formula, "t："));
            }
            Expect("ce balance", GlTransferCustomCalc.Balance(def, new decimal[] { 0m, 300m }, 0) == 300m);
            string[] aux = GlTransferCustomCalc.LineAux(def, 1, codes);
            Expect("line aux from qm", aux != null && aux[0] == "D901");
            Expect("accounts ok", GlTransferCustomCalc.Accounts(def, codes) == null);
            def.Rows[1].Code = "6401";
            Expect("accounts non-leaf", GlTransferCustomCalc.Accounts(def, codes) != null);
        }

        static void CheckPnl()
        {
            GlTransferCodes codes = Codes();
            GlTransferPlan plan = new GlTransferPlan();
            List<Dictionary<string, object>> defs = new List<Dictionary<string, object>>();
            defs.Add(Pair("2", 1, "6602"));
            defs.Add(Pair("2", 2, "4103"));
            defs.Add(Pair("1", 1, "6001"));
            defs.Add(Pair("1", 2, "4103"));
            defs.Add(Pair("3", 1, "671199"));
            defs.Add(Pair("3", 2, "4103"));
            List<string[]> pairs = GlTransferPnl.Pairs(defs, plan, codes);
            Expect("pairs", pairs.Count == 2 && pairs[0][2] == "6001" && plan.Skipped.Count == 1);
            Dictionary<string, GlTransferBalRow> bal = new Dictionary<string, GlTransferBalRow>();
            bal["a"] = Bal("6001", -800m, null);
            bal["b"] = Bal("6602", 300m, null);
            bal["c"] = Bal("6602", -20m, "D2");
            GlTransferPnl.Assemble(plan, pairs, bal, codes);
            CheckPacks(plan);
        }

        static void CheckPacks(GlTransferPlan plan)
        {
            Expect("two packs", plan.Vouchers.Count == 2 && plan.Vouchers[0].Pack == GlTransferPnl.Income);
            List<GlLine> income = plan.Vouchers[0].Draft.Lines;
            Expect("income lines", income.Count == 2 && income[0].Account == "6001" && income[0].Debit == 800m
                && income[1].Account == "4103" && income[1].Credit == 800m);
            List<GlLine> expense = plan.Vouchers[1].Draft.Lines;
            Expect("expense lines", expense.Count == 3 && expense[0].Account == "4103" && expense[0].Debit == 280m
                && expense[1].Credit + expense[2].Credit == 280m && expense[0].Digest == GlTransferReq.PnlDigest);
            decimal[] sums = GlTransfer.Sums(plan.Vouchers[1].Draft);
            Expect("balanced", sums[0] == sums[1]);
        }

        static void CheckCompare()
        {
            GlTransferPlan plan = new GlTransferPlan();
            plan.Ask = new GlTransferAsk();
            plan.Ask.Pnl = true;
            GlTransferVoucher v = new GlTransferVoucher();
            v.OutNo = "GL0000000000007";
            v.Draft = new GlDraft();
            v.Draft.Lines.Add(GlTransferLines.Line("6001", "d", true, 800m, null));
            v.Draft.Lines.Add(GlTransferLines.Line("4103", "d", false, 800m, null));
            List<Dictionary<string, object>> rows = new List<Dictionary<string, object>>();
            rows.Add(Line("6001", "800.00", "0", v.OutNo));
            rows.Add(Line("4103", "0", "800", v.OutNo));
            Expect("compare same", GlTransferSave.Compare(plan, v, rows) == null);
            rows[1]["idoc"] = "0";
            Expect("compare mark", GlTransferSave.Compare(plan, v, rows) != null);
            rows.RemoveAt(1);
            Expect("compare count", GlTransferSave.Compare(plan, v, rows) != null);
        }

        static void CheckRegistry()
        {
            foreach (string path in new string[] { GlTransferReq.PnlPath, GlTransferReq.CustomPath })
            {
                WriteRequestInfo info = WriteClass.Of(path, new Dictionary<string, object>());
                Expect("class " + path, info != null && info.Type == "gl" && info.Op == "voucher" && WriteGate.IsWrite(path));
                bool listed = false;
                foreach (string[] site in WriteClassSelfTest.TestOnlySites)
                {
                    listed = listed || (site[0] == path && site[2] == GlTransferReq.TestOnlyOf(path));
                }
                Expect("test-only " + path, listed);
                Expect("dry mode " + path, DryRunModes.Lookup(path.Substring("/u8co/v1/".Length), "", "", "") == DryRunModes.Validate);
            }
        }

        static GlTransferCodes Codes()
        {
            GlTransferCodes codes = new GlTransferCodes();
            codes.Add(Code("6401", "0", "1", "", false));
            codes.Add(Code("64019901", "1", "1", "", true));
            codes.Add(Code("64019902", "1", "1", "", false));
            codes.Add(Code("6001", "1", "0", "损益", false));
            codes.Add(Code("6602", "1", "1", "损益", true));
            codes.Add(Code("4103", "1", "0", "权益", false));
            return codes;
        }

        static Dictionary<string, object> Code(string code, string end, string property, string cls, bool dept)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["ccode"] = code;
            row["bend"] = end;
            row["bproperty"] = property;
            row["cclass"] = cls;
            row["bdept"] = dept ? "1" : "0";
            return row;
        }

        static GlTransferBalRow Bal(string code, decimal value, string dept)
        {
            GlTransferBalRow row = new GlTransferBalRow();
            row.Code = code;
            for (int i = 0; i < row.Aux.Length; i++)
            {
                row.Aux[i] = "";
            }
            row.Aux[0] = dept ?? "";
            row.Value = value;
            return row;
        }

        static GlQm Qm(string code, string side, string aux)
        {
            GlQm qm = new GlQm();
            qm.Code = code;
            qm.Side = side;
            qm.Aux = aux;
            return qm;
        }

        static GlTransferDef Def(string code, bool debit, string formula)
        {
            GlTransferDef def = new GlTransferDef();
            def.Inid = 1;
            def.Code = code;
            def.Debit = debit;
            def.Formula = formula;
            def.Digest = "";
            for (int i = 0; i < def.Aux.Length; i++)
            {
                def.Aux[i] = "";
            }
            return def;
        }

        static Dictionary<string, object> Pair(string id, int inid, string code)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["CTRAN_ID"] = id;
            row["inid"] = inid.ToString(System.Globalization.CultureInfo.InvariantCulture);
            row["csign"] = "转";
            row["ccode"] = code;
            row["bd_c"] = "0";
            return row;
        }

        static Dictionary<string, object> Line(string code, string md, string mc, string outNo)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["ccode"] = code;
            row["md"] = md;
            row["mc"] = mc;
            row["outsign"] = GlTransferReq.PnlSign;
            row["outno"] = outNo;
            row["idoc"] = "-1";
            return row;
        }

        static Dictionary<string, object> Body(int year, int period)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["fiscal_year"] = year;
            body["period"] = period;
            return body;
        }

        static string Code(string path, Dictionary<string, object> body, bool dry, int loginYear)
        {
            try
            {
                GlTransferReq.Parse(path, body, dry, loginYear);
                return null;
            }
            catch (BridgeException ex)
            {
                return ex.Status == 400 ? ex.Field : "status " + ex.Status;
            }
        }

        static string Message(string formula)
        {
            try
            {
                GlTransferFormula.Parse(formula, "t：");
                return "";
            }
            catch (BridgeException ex)
            {
                return ex.Status == 400 ? ex.Message : "";
            }
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("gl transfer " + name);
            }
        }
    }
}
