using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // --selftest 的票据处理部分（notes/process）：请求校验、锁键、处理方式与号、贴现净额、分包子票区间的选取与剩余、
    // 写入语句的参数（样例数据）。只跑纯函数，不连库、不建 COM。
    internal static class NotesProcSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckRefuse();
            CheckLocks();
            CheckRule();
            CheckRanges();
            CheckWhole();
            CheckArgs();
            CheckReturn();
        }

        // 非分包票据只能整张处理（余额 30000：缺省、等于余额放行，部分金额、子票区间 409）；票据科目为空 409。
        static void CheckWhole()
        {
            NoteHead note = DiscountPlan().Note;
            note.Remain = 30000m;
            Same("notes whole default", NotesProcGate.Whole(note, null, 0), 30000m);
            Same("notes whole equal", NotesProcGate.Whole(note, 30000m, 0), 30000m);
            State("notes whole part", delegate { NotesProcGate.Whole(note, 10000m, 0); });
            State("notes whole range", delegate { NotesProcGate.Whole(note, null, 1L); });
            note.Partner = "C001";
            NotesProcGate.HeadOk(note, "人民币");
            note.Km = "";
            State("notes no km", delegate { NotesProcGate.HeadOk(note, "人民币"); });
        }

        static void State(string name, Action act)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                Expect(name + " (" + ex.Status.ToString(CultureInfo.InvariantCulture) + ")", ex.Status == 409 && ex.Code == "state_mismatch");
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void CheckParse()
        {
            NotesProcAsk ask = NotesProcReq.Parse(Body("{\"flag\":\"AR\",\"op\":\"settle\",\"note\":\"N001\",\"bank_code\":\"10029901\"}"));
            Same("notes settle op", ask.Op, "settle");
            Same("notes settle note", ask.NoteCode, "N001");
            Same("notes settle bank", ask.BankCode, "10029901");
            Same("notes settle bank name", ask.BankName, "");
            Expect("notes settle amount", ask.Amount == null);
            ask = NotesProcReq.Parse(Body("{\"flag\":\"AR\",\"op\":\"discount\",\"note\":3416,\"amount\":100000,\"bank_code\":\"1002\","
                + "\"expense\":10.00,\"rate\":0.52,\"sub_start\":50000001,\"sub_end\":60000000}"));
            Same("notes discount id", ask.NoteId, 3416);
            Expect("notes discount code", ask.NoteCode == null);
            Same("notes discount expense", ask.Expense, 10.00m);
            Same("notes discount rate", ask.Rate, 0.52m);
            Same("notes discount interest", ask.Interest, 0m);
            Same("notes discount range", ask.SubEnd, 60000000L);
            ask = NotesProcReq.Parse(Body("{\"flag\":\"AR\",\"op\":\"endorse\",\"note\":\"N1\",\"vendor\":\"S1\",\"ap_lines\":"
                + "[{\"type\":\"01\",\"id\":\"PV1\",\"line_id\":7,\"amount\":70},{\"type\":\"P0\",\"id\":\"YF1\",\"amount\":10}]}"));
            Same("notes endorse vendor", ask.Vendor, "S1");
            Same("notes endorse lines", ask.ApLines.Count, 2);
            Same("notes endorse total", NotesProcReq.Total(ask.ApLines), 80m);
            Same("notes endorse line", ask.ApLines[0].Line, 7);
            ask = NotesProcReq.Parse(Body("{\"flag\":\"AR\",\"op\":\"settle\",\"note\":\"N2\",\"bank_code\":\"1002\",\"digest\":\" 到期 \"}"));
            Same("notes digest trim", ask.Digest, "到期");
        }

        static void CheckRefuse()
        {
            Bad("notes bad op", "{\"flag\":\"AR\",\"op\":\"transfer\",\"note\":\"N1\"}", "op");
            Bad("notes bank on return", "{\"flag\":\"AR\",\"op\":\"return\",\"note\":\"N1\",\"bank_code\":\"1002\"}", "bank_code");
            Bad("notes vendor on return", "{\"flag\":\"AR\",\"op\":\"return\",\"note\":\"N1\",\"vendor\":\"S1\"}", "vendor");
            Bad("notes ap discount", "{\"flag\":\"AP\",\"op\":\"discount\",\"note\":\"N1\",\"bank_code\":\"1002\"}", "op");
            Bad("notes no bank", "{\"flag\":\"AR\",\"op\":\"settle\",\"note\":\"N1\"}", "bank_code");
            Bad("notes bank on endorse", "{\"flag\":\"AR\",\"op\":\"endorse\",\"note\":\"N1\",\"bank_code\":\"1002\"}", "bank_code");
            Bad("notes expense on settle", "{\"flag\":\"AR\",\"op\":\"settle\",\"note\":\"N1\",\"bank_code\":\"1002\",\"expense\":1}", "expense");
            Bad("notes half range", "{\"flag\":\"AR\",\"op\":\"settle\",\"note\":\"N1\",\"bank_code\":\"1002\",\"sub_start\":1}", "sub_end");
            Bad("notes range amount", "{\"flag\":\"AR\",\"op\":\"settle\",\"note\":\"N1\",\"bank_code\":\"1002\",\"amount\":2,"
                + "\"sub_start\":1,\"sub_end\":100}", "amount");
            Bad("notes no note", "{\"flag\":\"AR\",\"op\":\"settle\",\"bank_code\":\"1002\"}", "note");
            Bad("notes endorse 49", "{\"flag\":\"AR\",\"op\":\"endorse\",\"note\":\"N1\",\"vendor\":\"S1\",\"ap_lines\":"
                + "[{\"type\":\"49\",\"id\":\"FK1\",\"amount\":1}]}", "ap_lines");
            Bad("notes endorse sum", "{\"flag\":\"AR\",\"op\":\"endorse\",\"note\":\"N1\",\"vendor\":\"S1\",\"amount\":2,\"ap_lines\":"
                + "[{\"type\":\"01\",\"id\":\"PV1\",\"amount\":1}]}", "ap_lines");
            Bad("notes rate", "{\"flag\":\"AR\",\"op\":\"discount\",\"note\":\"N1\",\"bank_code\":\"1002\",\"rate\":101}", "rate");
        }

        static void CheckLocks()
        {
            string[] keys = NotesProcReq.LockKeysOf(NotesProcReq.Path, Body("{\"flag\":\"AR\",\"op\":\"endorse\",\"note\":\"N1\","
                + "\"vendor\":\"S1\",\"ap_lines\":[{\"type\":\"P0\",\"id\":\"YF1\",\"amount\":1}]}"));
            Same("notes keys endorse", string.Join(",", keys), "arap:writeoff:AP,arap:writeoff:AR");
            keys = NotesProcReq.LockKeysOf(NotesProcReq.Path, Body("{\"flag\":\"AR\",\"op\":\"settle\",\"note\":\"N1\",\"bank_code\":\"1\"}"));
            Same("notes keys settle", string.Join(",", keys), "arap:writeoff:AR");
            keys = NotesProcReq.LockKeysOf(NotesProcReq.Path, Body("{\"flag\":\"AR\",\"op\":\"return\",\"note\":\"N1\"}"));
            Same("notes keys return", string.Join(",", keys), "arap:writeoff:AR,new:ar_bill");
            keys = NotesProcReq.LockKeysOf(NotesProcReq.Path, Body("{\"flag\":\"AP\",\"op\":\"settle\",\"note\":\"N1\",\"bank_code\":\"1\"}"));
            Same("notes keys ap", string.Join(",", keys), "arap:writeoff:AP");
            Same("notes keys bad", NotesProcReq.LockKeysOf(NotesProcReq.Path, Body("{}")).Length, 0);
            Expect("notes keys other", NotesProcReq.LockKeysOf(ArapRedReq.Path, Body("{}")) == null);
            Same("notes spec path", NotesProcReq.Spec[0], NotesProcReq.Path);
            Same("notes spec date", Array.IndexOf(NotesProcReq.Spec, "date"), -1);
        }

        static void CheckRule()
        {
            Same("notes style 9A", NotesProcRule.Style("settle"), "9A");
            Same("notes style 9D", NotesProcRule.Style("discount"), "9D");
            Same("notes style 9E", NotesProcRule.Style("endorse"), "9E");
            Expect("notes no 9A", NotesProcRule.NoValid("settle", "AR", "PJJAR000000000001"));
            Expect("notes no 9E", NotesProcRule.NoValid("endorse", "AR", "PJBAR000000000001"));
            Expect("notes no type", !NotesProcRule.NoValid("discount", "AR", "PJJAR000000000001"));
            Expect("notes no flag", !NotesProcRule.NoValid("settle", "AP", "PJJAR000000000001"));
            Expect("notes no digits", !NotesProcRule.NoValid("settle", "AR", "PJJAR00000000000X"));
            Expect("notes no null", !NotesProcRule.NoValid("settle", "AR", null));
            Same("notes net", NotesProcRule.Net(100000m, 0m, 1000.00m), 99000.00m);
            Same("notes net interest", NotesProcRule.Net(100m, 5m, 10m), 95m);
            Same("notes detail id", NotesProcRule.DetailId("N1", 10600001L, 10608000L), "N1-10600001-10608000");
            Same("notes detail id plain", NotesProcRule.DetailId("N1", 0, 0), "N1");
            Same("notes digest settle", NotesProcRule.Digest("settle", "AR", "客户甲", null), "收客户甲票据到期托收");
            Same("notes digest endorse", NotesProcRule.Digest("endorse", "AR", "客户甲", "供应商乙"), "付供应商乙票据背书");
            Same("notes range amount", NotesProcRule.RangeAmount(10600001L, 10608000L), 80m);
            Same("notes range count", NotesProcRule.Count(80m), 8000L);
        }

        // 一张分包票据：可用 10000001–11000000（10000），先背书 6000、再背书 80，剩 10608001–11000000（3920）。
        static void CheckRanges()
        {
            List<NoteRange> avail = new List<NoteRange>();
            avail.Add(new NoteRange(10000001L, 11000000L));
            NoteRange used = NotesProcRule.Pick(avail, 6000m, 0, 0);
            Same("notes pick first", Text(used), "10000001-10600000");
            List<NoteRange> left = NotesProcRule.Leftover(avail, used);
            used = NotesProcRule.Pick(left, 80m, 0, 0);
            left = NotesProcRule.Leftover(left, used);
            Same("notes pick second", Text(used), "10600001-10608000");
            Same("notes left second", Text(left), "10608001-11000000");
            Same("notes left amount", NotesProcRule.Sum(left), 3920m);
            used = NotesProcRule.Pick(avail, null, 0, 0);
            Same("notes pick whole", Text(used), "10000001-11000000");
            Same("notes left whole", NotesProcRule.Leftover(avail, used).Count, 0);
            used = NotesProcRule.Pick(avail, null, 10300001L, 10300100L);
            Same("notes left middle", Text(NotesProcRule.Leftover(avail, used)), "10000001-10300000,10300101-11000000");
            Expect("notes pick over", NotesProcRule.Pick(avail, 10000.01m, 0, 0) == null);
            Expect("notes pick outside", NotesProcRule.Pick(avail, null, 1L, 2L) == null);
            Expect("notes pick empty", NotesProcRule.Pick(new List<NoteRange>(), null, 0, 0) == null);
        }

        // 贴现（样例：票面 100000、费用 1000.00、净额 99000.00）：Sub 的 iAmount 是净额、iExpense 是费用、cBank 是银行名称、cCode = cCoVouchID = 银行科目；往来明细贷方是票面。
        static void CheckArgs()
        {
            NotesProcPlan plan = DiscountPlan();
            object[] sub = NotesProcSql.SubArgs(plan);
            Same("notes sub count", sub.Length, 21);
            Same("notes sub args", Join(sub), "AR50N1|9D|2026-09-16|0.00|1000.00|99000.00|某银行某支行|0.52|100201|张三|"
                + "PJTAR000000000001|48|100201|AR||99000.00|0.00|1000.00|1.0000000000|50000001|60000000");
            object[] detail = NotesProcSql.DetailArgs(plan);
            Same("notes detail count", detail.Length, 21);
            Same("notes detail args", Join(detail), "9|50|50|N1-50000001-60000000|2026-08-28|2026-09-16|C001|112101|收客户甲票据贴现|人民币|"
                + "0.00|100000.00|0.00|100000.00|9D|PJTAR000000000001|50|N1-50000001-60000000|AR|张三|张三");
            plan.Op = "settle";
            plan.Flag = "AP";
            plan.Range = null;
            Same("notes settle sub", Join(NotesProcSql.SubArgs(plan)), "AR50N1|9A|2026-09-16|0.00|0.00|100000.00||0|100201|张三|"
                + "PJTAR000000000001|48||AP|某银行某支行|100000.00|0.00|0.00|1.0000000000||");
            Same("notes settle detail", Join(NotesProcSql.DetailArgs(plan)), "9|50|50|N1|2026-08-28|2026-09-16|C001|112101|收客户甲票据贴现|"
                + "人民币|100000.00|0.00|100000.00|0.00|9A|PJTAR000000000001|50|N1|AP|张三|张三");
        }

        // 退回（样例：票面 100000，生成应收单 YS0000000002，应收控制科目 112201，模板 9001）：
        // 解析、规则、缺省摘要，票据处理行指向 R0，SaveVouch 的输入、保存后补写、应收单往来明细行的参数，占位符个数；分包票据部分退回 409。
        static void CheckReturn()
        {
            NotesProcAsk ask = NotesProcReq.Parse(Body("{\"flag\":\"AR\",\"op\":\"return\",\"note\":\"N1\",\"sub_start\":1,\"sub_end\":10000000}"));
            Same("notes return op", ask.Op, "return");
            Same("notes return range", ask.SubEnd, 10000000L);
            Same("notes style 9C", NotesProcRule.Style("return"), "9C");
            Expect("notes no 9C", NotesProcRule.NoValid("return", "AR", "CLAR0000000000001"));
            Expect("notes no 9C type", !NotesProcRule.NoValid("return", "AR", "PJJAR000000000001"));
            Same("notes return digest", NotesProcRule.ReturnDigest("客户甲"), "退回客户甲电子承兑");
            NotesProcPlan plan = ReturnPlan();
            Same("notes return sub", Join(NotesProcSql.SubArgs(plan)), "AR50N1|9C|2026-09-16|0.00|0.00|100000.00||0|112201|张三|"
                + "CLAR0000000000001|R0|YS0000000002|AR||100000.00|0.00|0.00|1.0000000000|1|10000000");
            ArapInput input = NotesProcReturnBo.Input(plan);
            Same("notes return input head", input.Head["cdwcode"] + "|" + input.Head["cdeptcode"] + "|" + input.Head.ContainsKey("cperson") + "|"
                + input.Head["ccode"] + "|" + input.Head["cdigest"] + "|" + input.Date + "|" + NotesProcRule.Money(input.Sum),
                "C001|D01|False|112201|转出票据N1|2026-09-16|100000.00");
            Same("notes return input line", input.Lines.Count + "|" + input.Lines[0].Fields["cdigest"] + "|" + input.Lines[0].Fields.ContainsKey("ccode")
                + "|" + NotesProcRule.Money(input.Lines[0].Amt) + "|" + NotesProcRule.Money(input.Lines[0].TaxRate), "1|票据转出|False|100000.00|0.00");
            Same("notes return stamp", Join(NotesProcReturnSql.StampArgs(plan)), "N1|张三|2026-09-16|9001|||arr0|YS0000000002|7001|AR|R0");
            Same("notes return detail", Join(NotesProcReturnSql.DetailArgs(plan)), "9|R0|YS0000000002|2026-09-16|2026-09-16|C001|D01||"
                + "112201|退回客户甲电子承兑|人民币|100000.00|0.00|100000.00|0.00|CLAR0000000000001|R0|YS0000000002|AR|张三|张三");
            object[][] args = new object[][] { NotesProcReturnSql.StampArgs(plan), NotesProcReturnSql.DetailArgs(plan) };
            string[] texts = NotesProcReturnSql.Texts();
            for (int i = 0; i < texts.Length; i++)
            {
                Same("notes return marks " + i.ToString(CultureInfo.InvariantCulture), texts[i].Split('?').Length - 1, args[i].Length);
            }
            Same("notes return cols", NotesProcReturnSql.StampCols.Split(',').Length, 10);
            NotesProcReturn.WholeRemainder(plan);
            plan.Left = new List<NoteRange>();
            plan.Left.Add(new NoteRange(10000001L, 25000000L));
            State("notes return partial", delegate { NotesProcReturn.WholeRemainder(plan); });
        }

        internal static NotesProcPlan ReturnPlan()
        {
            NotesProcPlan plan = DiscountPlan();
            plan.Op = "return";
            plan.CancelNo = "CLAR0000000000001";
            plan.Digest = NotesProcRule.ReturnDigest("客户甲");
            plan.Range = new NoteRange(1L, 10000000L);
            plan.Net = plan.Amount;
            plan.Expense = 0m;
            plan.Rate = 0m;
            plan.BankCode = null;
            plan.BankName = null;
            plan.CtrlKm = "112201";
            plan.VtId = 9001;
            plan.BillCode = "YS0000000002";
            plan.BillId = 7001;
            plan.Left = new List<NoteRange>();
            plan.Local = "人民币";
            plan.Note.Dept = "D01";
            plan.Note.Person = "";
            return plan;
        }

        internal static NotesProcPlan DiscountPlan()
        {
            NotesProcPlan plan = new NotesProcPlan();
            plan.Flag = "AR";
            plan.Op = "discount";
            plan.Date = "2026-09-16";
            plan.Period = 9;
            plan.Operator = "张三";
            plan.CancelNo = "PJTAR000000000001";
            plan.Digest = "收客户甲票据贴现";
            plan.Amount = 100000m;
            plan.Expense = 1000.00m;
            plan.Rate = 0.52m;
            plan.Net = NotesProcRule.Net(plan.Amount, 0m, plan.Expense);
            plan.BankCode = "100201";
            plan.BankName = "某银行某支行";
            plan.Range = new NoteRange(50000001L, 60000000L);
            plan.Note = new NoteHead();
            plan.Note.Link = "AR50N1";
            plan.Note.Code = "N1";
            plan.Note.VouchType = "50";
            plan.Note.Partner = "C001";
            plan.Note.Km = "112101";
            plan.Note.Currency = "人民币";
            plan.Note.Nfrat = "1.0000000000";
            plan.Note.SignDate = "2026-08-28";
            return plan;
        }

        // 参数串成 a|b|c，null 写成空串。
        internal static string Join(object[] args)
        {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < args.Length; i++)
            {
                sb.Append(i > 0 ? "|" : string.Empty).Append(args[i] == null ? string.Empty : Convert.ToString(args[i], CultureInfo.InvariantCulture));
            }
            return sb.ToString();
        }

        static string Text(NoteRange r)
        {
            return r == null ? "null" : r.Start.ToString(CultureInfo.InvariantCulture) + "-" + r.End.ToString(CultureInfo.InvariantCulture);
        }

        static string Text(List<NoteRange> list)
        {
            List<string> parts = new List<string>();
            foreach (NoteRange r in list)
            {
                parts.Add(Text(r));
            }
            return string.Join(",", parts.ToArray());
        }

        static void Same(string name, object got, object want)
        {
            if (!object.Equals(got, want))
            {
                throw new InvalidOperationException(name + "：" + Convert.ToString(got, CultureInfo.InvariantCulture));
            }
        }

        static void Bad(string name, string json, string field)
        {
            try
            {
                NotesProcReq.Parse(Body(json));
            }
            catch (BridgeException ex)
            {
                Expect(name + " (" + ex.Field + ")", ex.Status == 400 && ex.Code == "bad_request" && ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
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
