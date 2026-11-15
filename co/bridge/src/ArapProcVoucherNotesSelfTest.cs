using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // --selftest 的票据处理制单部分（arap/process/voucher 的 PJJ / PJT / PJB / CL）：请求校验（前缀、coutsign PJ、expense_code 只给 9D）、
    // 批次核对（处理行唯一、未制单、金额对得上），以及分录的拼法（U8 PJ 凭证的形状）：
    // 贴现借银行净额、贷费用负数、贷票据票面；背书借应付、贷票据；退回换方向取负；银行 / 费用 / 对方单据行在前、票据行在后。不连库。
    internal static class ArapProcVoucherNotesSelfTest
    {
        const string NoteNo = "N000000000000000000000000000002-2";
        // 当前样例的批次号、处理类型（NewPlan 设，Row / Sub 用）。
        static string _no = "";
        static string _style = "";

        public static void Run()
        {
            CheckParse();
            CheckBad();
            CheckBatch();
            CheckDiscount();
            CheckEndorse();
            CheckReturn();
            CheckFlow();
        }

        // 现金流量项目（数据来源 GL_CashItemDataSource）：66039901 贷 -10.00 → 07（bDir 0）；220201 借 → 04（最长前缀）；
        // 1122 的贷方 → 01；找不到 409。
        static void CheckFlow()
        {
            List<Dictionary<string, object>> src = new List<Dictionary<string, object>>();
            src.Add(Source("07", "66039901", "0"));
            src.Add(Source("07", "66039901", "1"));
            src.Add(Source("04", "2202", "1"));
            src.Add(Source("05", "220201", "1"));
            src.Add(Source("01", "1122", "0"));
            Expect("pvn flow expense", ArapCashItems.Derive(src, Gl("66039901", 0m, -10.00m)) == "07");
            Expect("pvn flow longest", ArapCashItems.Derive(src, Gl("220201", 80m, 0m)) == "05");
            Expect("pvn flow return", ArapCashItems.Derive(src, Gl("112201", 0m, -100000m)) == "01");
            Status("pvn flow none", 409, delegate { ArapCashItems.Derive(src, Gl("660399", 1m, 0m)); });
            src.Add(Source("06", "1122", "0"));
            Status("pvn flow clash", 409, delegate { ArapCashItems.Derive(src, Gl("112201", 0m, 5m)); });
        }

        static Dictionary<string, object> Source(string item, string prefix, string dir)
        {
            Dictionary<string, object> one = new Dictionary<string, object>();
            one["item"] = item;
            one["src"] = prefix;
            one["dir"] = dir;
            return one;
        }

        static GlLine Gl(string account, decimal debit, decimal credit)
        {
            GlLine line = new GlLine();
            line.Account = account;
            line.Debit = debit;
            line.Credit = credit;
            return line;
        }

        static void CheckParse()
        {
            ProcVoucherAsk ask = ArapProcVoucherReq.Parse(Body("{\"flag\":\"AR\",\"cancel_nos\":[\"PJTAR000000000001\"],\"expense_code\":\"66039901\"}"));
            Expect("pvn 9D", ask.Style == "9D" && ask.Notes && ask.ExpenseCode == "66039901" && !ask.ExchangeGain);
            Expect("pvn out sign", ArapProcVoucherReq.OutSign("9A", "AR") == "PJ" && ArapProcVoucherReq.OutSign("9C") == "PJ"
                && ArapProcVoucherReq.Title("9E") == "票据背书");
            Expect("pvn styles", ArapProcVoucherReq.StyleOf("CLAR0000000000001") == "9C" && ArapProcVoucherReq.StyleOf("PJBAP1") == "9E"
                && ArapProcVoucherReq.FlagOf("PJJAP1") == "AP" && ArapProcVoucherReq.StyleOf("PJZAR1") == null);
            Expect("pvn spec", Array.IndexOf(ArapProcVoucherReq.Spec, "expense_code") > 0);
            Expect("pvn not notes", !ArapProcVoucherReq.Parse(Body("{\"flag\":\"AR\",\"cancel_nos\":[\"BZAR1\"]}")).Notes);
            CheckSign();
        }

        // 缺省凭证类别：票据结算、贴现「收」，背书、退回和往来处理「转」，给了 sign 用 sign（与 U8 的 PJ 凭证核对）。
        static void CheckSign()
        {
            Expect("pvn sign 9A", SignOf("{\"flag\":\"AR\",\"cancel_nos\":[\"PJJAR1\"]}") == "收");
            Expect("pvn sign 9D", SignOf("{\"flag\":\"AR\",\"cancel_nos\":[\"PJTAR1\"]}") == "收");
            Expect("pvn sign 9E", SignOf("{\"flag\":\"AR\",\"cancel_nos\":[\"PJBAR1\"]}") == "转");
            Expect("pvn sign 9C", SignOf("{\"flag\":\"AR\",\"cancel_nos\":[\"CLAR1\"]}") == "转");
            Expect("pvn sign 9I", SignOf("{\"flag\":\"AR\",\"cancel_nos\":[\"YCFAP1\"]}") == "转");
            Expect("pvn sign given", SignOf("{\"flag\":\"AR\",\"cancel_nos\":[\"PJJAR1\"],\"sign\":\"转\"}") == "转");
            string keys = string.Join(",", ArapProcVoucherReq.LockKeys(Body("{\"flag\":\"AR\",\"cancel_nos\":[\"PJTAR1\"]}")));
            Expect("pvn sign keys", keys == "arap:proc:PJTAR1,new:gl:收,arap:voucher:AR");
        }

        static string SignOf(string json)
        {
            return ArapProcVoucherReq.SignOf(ArapProcVoucherReq.Parse(Body(json)));
        }

        static void CheckBad()
        {
            Bad("pvn expense 9A", "{\"flag\":\"AR\",\"cancel_nos\":[\"PJJAR1\"],\"expense_code\":\"66039901\"}", "expense_code");
            Bad("pvn expense space", "{\"flag\":\"AR\",\"cancel_nos\":[\"PJTAR1\"],\"expense_code\":\"6111 02\"}", "expense_code");
            Bad("pvn mixed", "{\"flag\":\"AR\",\"cancel_nos\":[\"PJTAR1\",\"PJJAR2\"]}", "cancel_nos.1");
            Bad("pvn side", "{\"flag\":\"AP\",\"cancel_nos\":[\"CLAR1\"]}", "cancel_nos.0");
            Bad("pvn ap note 9D", "{\"flag\":\"AP\",\"cancel_nos\":[\"PJTAP1\"]}", "cancel_nos.0");
            Bad("pvn ap note 9E", "{\"flag\":\"AR\",\"cancel_nos\":[\"PJBAP1\"]}", "cancel_nos.0");
            Bad("pvn ap note 9C side", "{\"flag\":\"AR\",\"cancel_nos\":[\"CLAP1\"]}", "cancel_nos.0");
            // 应付票据结算、退回放行：第二级写入（ApNotes），应付结算缺省凭证类别「付」。
            ProcVoucherAsk ap = ArapProcVoucherReq.Parse(Body("{\"flag\":\"AP\",\"cancel_nos\":[\"PJJAP1\"]}"));
            Expect("pvn ap note 9A", ap.ApNotes && ap.Style == "9A" && ArapProcVoucherReq.SignOf(ap) == ArapVoucherRule.Pay);
            ap = ArapProcVoucherReq.Parse(Body("{\"flag\":\"AP\",\"cancel_nos\":[\"CLAP1\"]}"));
            Expect("pvn ap note 9C", ap.ApNotes && ap.Style == "9C" && ArapProcVoucherReq.SignOf(ap) == ArapVoucherRule.Transfer);
            Expect("pvn ar note not ap", !ArapProcVoucherReq.Parse(Body("{\"flag\":\"AR\",\"cancel_nos\":[\"PJJAR1\"]}")).ApNotes);
            string sql = ArapProcVoucherNotes.SubSql(2);
            Expect("pvn sub sql", Count(sql, '?') == 3 && sql.IndexOf("UPDLOCK", StringComparison.Ordinal) > 0);
            Expect("pvn load styles", ArapProcVoucherLoad.RowSql(1).IndexOf("N'9C'", StringComparison.Ordinal) > 0
                && ArapProcVoucherBack.ProcStyles.IndexOf("N'9A'", StringComparison.Ordinal) > 0);
        }

        // 批次核对：缺处理行、两条处理行、已制单、金额不符 409；9E 只要求票据所在的账有行。
        static void CheckBatch()
        {
            ProcPlan plan = Discount();
            ArapProcVoucherNotes.Check(plan.Ask, plan.Rows, plan.NoteSubs);
            plan.NoteSubs[0].Expense = 200m;
            Status("pvn amount", 409, delegate { ArapProcVoucherNotes.Check(plan.Ask, plan.Rows, plan.NoteSubs); });
            plan.NoteSubs[0].Expense = 1000.00m;
            plan.NoteSubs[0].Pz = "AR0000000000001";
            Status("pvn vouchered", 409, delegate { ArapProcVoucherNotes.Check(plan.Ask, plan.Rows, plan.NoteSubs); });
            plan.NoteSubs[0].Pz = "";
            plan.NoteSubs.Add(plan.NoteSubs[0]);
            Status("pvn two subs", 409, delegate { ArapProcVoucherNotes.Check(plan.Ask, plan.Rows, plan.NoteSubs); });
            plan.NoteSubs.Clear();
            Status("pvn no sub", 409, delegate { ArapProcVoucherNotes.Check(plan.Ask, plan.Rows, plan.NoteSubs); });
            ProcPlan endorse = Endorse();
            ArapProcVoucherLoad.CheckBatch(endorse.Ask, endorse.Ask.CancelNos[0], endorse.Rows);
            endorse.Rows.RemoveAt(1);
            Status("pvn 9E no note", 409, delegate { ArapProcVoucherLoad.CheckBatch(endorse.Ask, endorse.Ask.CancelNos[0], endorse.Rows); });
        }

        // 贴现：借 100201 99000.00、贷 66039901 -1000.00、贷 112101 100000（客户）；coutid 是票据号，银行 / 费用行不回写。
        static void CheckDiscount()
        {
            ProcPlan plan = Discount();
            Build(plan, "66039901");
            List<GlLine> lines = plan.Gl.Draft.Lines;
            Expect("pvn 9D lines", lines.Count == 3);
            Expect("pvn 9D bank", lines[0].Account == "100201" && lines[0].Debit == 99000.00m && lines[0].Customer == "");
            Expect("pvn 9D expense", lines[1].Account == "66039901" && lines[1].Credit == -1000.00m && lines[1].Debit == 0m);
            Expect("pvn 9D note", lines[2].Account == "112101" && lines[2].Credit == 100000m && lines[2].Customer == "C0002");
            Expect("pvn 9D sources", plan.Sources[0][0] == "50" && plan.Sources[0][1] == NoteNo && plan.Sources[2][1] == NoteNo);
            Expect("pvn 9D entries", plan.Gl.Entries.Count == 1 && plan.Gl.Entries["AR:30257"] == 3);
            Expect("pvn 9D digest", lines[0].Digest == "收客户甲电子承兑贴现");
        }

        // 背书：借 220201 80（供应商，来源 01 + 发票号）、贷 112101 80（来源 50 + 票据号）。
        static void CheckEndorse()
        {
            ProcPlan plan = Endorse();
            Build(plan, "");
            List<GlLine> lines = plan.Gl.Draft.Lines;
            Expect("pvn 9E lines", lines.Count == 2 && lines[0].Account == "220201" && lines[0].Debit == 80m && lines[0].Supplier == "S0001");
            Expect("pvn 9E note", lines[1].Account == "112101" && lines[1].Credit == 80m);
            Expect("pvn 9E sources", plan.Sources[0][0] == "01" && plan.Sources[0][1] == "PI0000000001" && plan.Sources[1][0] == "50");
            Expect("pvn 9E entries", plan.Gl.Entries["AP:9001"] == 1 && plan.Gl.Entries["AR:9002"] == 2);
        }

        // 退回：112201 贷 -100000（来源 R0 应收单）在前、112101 借 -100000 在后。
        static void CheckReturn()
        {
            ProcPlan plan = NewPlan("AR", "9C", "CLAR0000000000001");
            plan.Rows.Add(Row("AR", "7737", "R0", "YS0000000002", "112201", 100000m));
            plan.Rows.Add(Credit(Row("AR", "7927", "50", "N000000000000000000000000000001-1-10000000", "112101", 0m), 100000m));
            ProcNoteSub sub = Sub(100000m, 0m, "112201", "N000000000000000000000000000001");
            sub.CoType = "R0";
            sub.CoId = "YS0000000002";
            plan.NoteSubs.Add(sub);
            ArapProcVoucherNotes.Check(plan.Ask, plan.Rows, plan.NoteSubs);
            Build(plan, "");
            List<GlLine> lines = plan.Gl.Draft.Lines;
            Expect("pvn 9C lines", lines.Count == 2 && lines[0].Account == "112201" && lines[0].Credit == -100000m && lines[0].Debit == 0m);
            Expect("pvn 9C note", lines[1].Account == "112101" && lines[1].Debit == -100000m && lines[1].Credit == 0m);
            Expect("pvn 9C sources", plan.Sources[0][0] == "R0" && plan.Sources[0][1] == "YS0000000002" && plan.Sources[1][0] == "50");
            plan.Rows.RemoveAt(0);
            Status("pvn 9C no bill", 409, delegate { ArapProcVoucherNotes.Check(plan.Ask, plan.Rows, plan.NoteSubs); });
        }

        static ProcPlan Discount()
        {
            ProcPlan plan = NewPlan("AR", "9D", "PJTAR000000000001");
            ProcVoucherRow note = Credit(Row("AR", "30257", "50", "N000000000000000000000000000002-2-50000001-60000000", "112101", 0m), 100000m);
            note.Dw = "C0002";
            note.Digest = "收客户甲电子承兑贴现";
            plan.Rows.Add(note);
            plan.NoteSubs.Add(Sub(99000.00m, 1000.00m, "100201", NoteNo));
            return plan;
        }

        static ProcPlan Endorse()
        {
            ProcPlan plan = NewPlan("AR", "9E", "PJBAR000000000001");
            ProcVoucherRow ap = Row("AP", "9001", "01", "PI0000000001", "220201", 80m);
            ap.Dw = "S0001";
            plan.Rows.Add(ap);
            plan.Rows.Add(Credit(Row("AR", "9002", "50", "N000000000000000000000000000003-10600001-10608000", "112101", 0m), 80m));
            plan.NoteSubs.Add(Sub(80m, 0m, "220201", "N000000000000000000000000000003"));
            return plan;
        }

        // 不连库：拼分录 → 按表填往来单位（应收客户、应付供应商；附加行不填，代替查科目）→ 排序 → 写进计划。
        static void Build(ProcPlan plan, string expense)
        {
            List<ProcLine> lines = new List<ProcLine>();
            foreach (ProcPart part in ArapProcVoucherNotes.Parts(plan, expense))
            {
                lines.Add(Line(part));
            }
            ArapProcVoucherParts.Fill(plan, ArapProcVoucherNotes.Order(ArapProcVoucherParts.Merge(lines)));
        }

        static ProcLine Line(ProcPart part)
        {
            GlLine gl = new GlLine();
            gl.Account = part.Account;
            gl.Digest = part.Digest;
            gl.Debit = part.Debit ? part.Amount : 0;
            gl.Credit = part.Debit ? 0 : part.Amount;
            bool dw = !part.Pl;
            gl.Customer = dw && part.Row.Ledger == "AR" ? part.Row.Dw : "";
            gl.Supplier = dw && part.Row.Ledger == "AP" ? part.Row.Dw : "";
            gl.Dept = "";
            gl.Person = "";
            gl.ItemClass = "";
            gl.Item = "";
            ProcLine line = new ProcLine();
            line.Line = gl;
            line.VType = part.VType.Length > 0 ? part.VType : part.Row.VType;
            line.VId = part.VId.Length > 0 ? part.VId : part.Row.VId;
            line.Pl = part.Pl;
            if (dw)
            {
                line.Rows.Add(part.Row.Key);
            }
            return line;
        }

        static ProcPlan NewPlan(string flag, string style, string no)
        {
            ProcPlan plan = new ProcPlan();
            ProcVoucherAsk ask = new ProcVoucherAsk();
            ask.Flag = flag;
            ask.Style = style;
            ask.CancelNos.Add(no);
            _no = no;
            _style = style;
            ask.Sign = "";
            ask.Date = "";
            ask.Digest = "";
            ask.PlCode = "";
            plan.Ask = ask;
            plan.Gl.Key.Sign = "转";
            plan.Gl.Date = "2026-08-31";
            return plan;
        }

        // 贷方的票据行用 Credit 改（参数个数限制）。
        static ProcVoucherRow Row(string ledger, string aid, string vtype, string vid, string code, decimal dm)
        {
            ProcVoucherRow row = new ProcVoucherRow();
            row.Ledger = ledger;
            row.Aid = aid;
            row.VType = vtype;
            row.VId = vid;
            row.Code = code;
            row.Dm = dm;
            row.Dw = ledger == "AR" ? "C0001" : "S1";
            row.Flag = ledger;
            row.IFlag = vtype == "50" ? 3 : 0;
            row.CancelNo = _no;
            row.Style = _style;
            return row;
        }

        static ProcVoucherRow Credit(ProcVoucherRow row, decimal cm)
        {
            row.Cm = cm;
            return row;
        }

        static ProcNoteSub Sub(decimal amount, decimal expense, string code, string note)
        {
            ProcNoteSub sub = new ProcNoteSub();
            sub.Id = 3887;
            sub.CancelNo = _no;
            sub.Flag = "AR";
            sub.Code = code;
            sub.Amount = amount;
            sub.Expense = expense;
            sub.Note = note;
            return sub;
        }

        delegate void Act();

        static void Status(string name, int status, Act act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                Expect(name + " (" + ex.Status.ToString(CultureInfo.InvariantCulture) + ")", ex.Status == status);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void Bad(string name, string json, string field)
        {
            try
            {
                ArapProcVoucherReq.Parse(Body(json));
            }
            catch (BridgeException ex)
            {
                Expect(name + " (" + ex.Field + ")", ex.Status == 400 && ex.Code == "bad_request" && ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static int Count(string text, char c)
        {
            int n = 0;
            foreach (char one in text)
            {
                n += one == c ? 1 : 0;
            }
            return n;
        }

        static Dictionary<string, object> Body(string json)
        {
            return Json.Parse(Encoding.UTF8.GetBytes(json));
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
