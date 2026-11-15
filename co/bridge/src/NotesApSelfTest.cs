using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // --selftest 的应付票据部分（notes/create、notes/delete、notes/process 的 flag AP）：往来单位字段、锁键、票据行与付款单的标记、
    // 第二级写入闸门、结算 / 退回的放行与贴现 / 背书的拒绝、退回生成应付单（P0）的参数。只跑纯函数，不连库、不建 COM。
    internal static class NotesApSelfTest
    {
        const string Good = "{\"flag\":\"AP\",\"note_no\":\"N2026101\",\"settle_code\":\"301\",\"amount\":2000,"
            + "\"sign_date\":\"2026-08-01\",\"receipt_date\":\"2026-08-10\",\"expire_date\":\"2027-02-01\",\"vendor\":\"V001\","
            + "\"dept\":\"D01\",\"receiver\":\"示例供应商有限公司\"";

        public static void Run()
        {
            CheckRegAsk();
            CheckRegRules();
            CheckProcAsk();
            CheckReturn();
            CheckGate();
        }

        // 登记：应付票据收 vendor、不收 customer；应收票据反之。note_km 两边都收。删除放行 AP。
        static void CheckRegAsk()
        {
            NoteRegAsk ask = NotesRegReq.ParseCreate(Body(Good + ",\"note_km\":\"2201\"}"));
            Same("ap reg flag", ask.Flag, "AP");
            Same("ap reg partner", ask.Partner, "V001");
            Same("ap reg note km", ask.NoteKm, "2201");
            Same("ap reg no note km", NotesRegReq.ParseCreate(Body(Good + "}")).NoteKm, "");
            Bad("ap reg customer", Good + ",\"customer\":\"C001\"}", "customer");
            Bad("ap reg no vendor", Good.Replace(",\"vendor\":\"V001\"", "") + "}", "vendor");
            string ar = Good.Replace("\"AP\"", "\"AR\"").Replace("\"vendor\":\"V001\"", "\"customer\":\"C001\"");
            Same("ar reg partner", NotesRegReq.ParseCreate(Body(ar + "}")).Partner, "C001");
            Bad("ar reg vendor", ar + ",\"vendor\":\"V001\"}", "vendor");
            Bad("ap reg flag", "{\"flag\":\"ap\"}", "flag");
            NoteDelAsk del = NotesRegReq.ParseDelete(Body("{\"flag\":\"AP\",\"id\":7}"));
            Same("ap del flag", del.Flag, "AP");
            Same("ap reg locks", string.Join(",", NotesRegReq.LockKeysOf(NotesRegReq.CreatePath, Body(Good + "}"))), "note:AP,new:ap_payment");
            Same("ap del locks", string.Join(",", NotesRegReq.LockKeysOf(NotesRegReq.DeletePath, Body("{\"flag\":\"AP\",\"id\":7}"))),
                "note:AP,arap:writeoff:AP");
        }

        // 票据行的 cLink / 条码、付款单类型与卡片、往来单位字段名、权限键。
        static void CheckRegRules()
        {
            Same("ap link", NoteInsert.Link("AP", "N1"), "AP50N1");
            Same("ap barcode", NoteInsert.BarCode("AP", "N1"), "||ap50|N1");
            Same("ap close kind", NotesRegReq.CloseKind("AP"), "ap_payment");
            Same("ar close kind", NotesRegReq.CloseKind("AR"), "ar_receipt");
            Same("ap close type", NotesRegSql.CloseType("AP"), "49");
            Same("ar close type", NotesRegSql.CloseType("AR"), "48");
            NoteRow left = new NoteRow();
            left.CloseId = 12;
            left.Code = "N1";
            left.Link = "AP50N1";
            Same("ap left receipt", NotesProcSelfTest.Join(NotesRegSql.LeftReceiptArgs(left)), "12|0|N1|AP|49");
            left.Link = "AR50N1";
            left.Split = true;
            Same("ar left receipt", NotesProcSelfTest.Join(NotesRegSql.LeftReceiptArgs(left)), "12|1|N1|AR|48");
            NotesRegGate.NoteKmFree(null, "", "112101", 2026);
            NotesRegGate.NoteKmFree(null, "112101", "112101", 2026);
            Same("ap partner field", NotesReg.PartnerField("AP"), "vendor");
            Same("ap close title", NotesReg.CloseTitle("AP"), "付款单");
            Expect("ap kinds", Kinds.Find("ap_payment") != null && Kinds.Find("ap_bill") != null);
            foreach (bool delete in new bool[] { false, true })
            {
                Expect("ap reg perm " + delete, PermRegistry.ForKey(PermRegistry.NotesRegKey(delete, "AP")) != null
                    && PermRegistry.ForKey(PermRegistry.NotesRegKey(delete, "AR")) != null);
            }
            Expect("ap proc perm", PermRegistry.ForKey(PermRegistry.NotesProcKey("AP", "settle")) != null
                && PermRegistry.ForKey(PermRegistry.NotesProcKey("AP", "return")) != null);
            NoteRegPlan plan = new NoteRegPlan();
            plan.Ask = NotesRegReq.ParseCreate(Body(Good + "}"));
            plan.NoteKm = "2201";
            plan.PartnerName = "示例供应商";
            plan.Drawer = "示例供应商";
            plan.Currency = "人民币";
            object[] args = NoteInsert.Args(plan);
            Same("ap insert marks", NoteInsert.Marks(NoteInsert.Sql(plan)), args.Length);
            Same("ap insert link arg", args[0], "AP50N2026101");
            Expect("ap insert endorser", Array.IndexOf(args, "V001") >= 0 && Array.IndexOf(args, "||ap50|N2026101") >= 0);
        }

        // 处理：应付票据放行结算、退回；贴现、背书 400（op）。锁键：退回另持 new:ap_bill。
        static void CheckProcAsk()
        {
            NotesProcAsk ask = NotesProcReq.Parse(Body("{\"flag\":\"AP\",\"op\":\"settle\",\"note\":\"N1\",\"bank_code\":\"10029901\"}"));
            Same("ap settle flag", ask.Flag + "|" + ask.Op, "AP|settle");
            ask = NotesProcReq.Parse(Body("{\"flag\":\"AP\",\"op\":\"return\",\"note\":\"N1\"}"));
            Same("ap return op", ask.Op, "return");
            BadProc("ap endorse", "{\"flag\":\"AP\",\"op\":\"endorse\",\"note\":\"N1\",\"vendor\":\"S1\",\"ap_lines\":"
                + "[{\"type\":\"P0\",\"id\":\"YF1\",\"amount\":1}]}", "op");
            BadProc("ap discount", "{\"flag\":\"AP\",\"op\":\"discount\",\"note\":\"N1\",\"bank_code\":\"1002\"}", "op");
            string[] keys = NotesProcReq.LockKeysOf(NotesProcReq.Path, Body("{\"flag\":\"AP\",\"op\":\"return\",\"note\":\"N1\"}"));
            Same("ap return keys", string.Join(",", keys), "arap:writeoff:AP,new:ap_bill");
            Expect("ap no 9C", NotesProcRule.NoValid("return", "AP", "CLAP0000000000001"));
            Expect("ap no 9A", NotesProcRule.NoValid("settle", "AP", "PJJAP000000000001"));
            Same("ap digest settle", NotesProcRule.Digest("settle", "AP", "供应商乙", null), "付供应商乙票据到期结算");
        }

        // 退回（应付）：处理行指向 P0，SaveVouch 的输入取应付控制科目，保存后补写条码「||app0|号」，往来明细记应付单贷方；占位符个数一致。
        static void CheckReturn()
        {
            NotesProcPlan plan = NotesProcSelfTest.ReturnPlan();
            plan.Flag = "AP";
            plan.CancelNo = "CLAP0000000000001";
            plan.CtrlKm = "220201";
            plan.VtId = 9002;
            plan.BillCode = "YF202609160001";
            plan.Note.Link = "AP50N1";
            plan.Note.Partner = "V001";
            plan.Note.Km = "2201";
            plan.Digest = "退回供应商乙电子承兑";
            Same("ap return bill type", NotesProcReturnSql.BillType("AP"), "P0");
            Same("ap return card", NotesProcReturnSql.Card("AP"), "AP04");
            Same("ap return sub", NotesProcSelfTest.Join(NotesProcSql.SubArgs(plan)), "AP50N1|9C|2026-09-16|0.00|0.00|100000.00||0|220201|张三|"
                + "CLAP0000000000001|P0|YF202609160001|AP||100000.00|0.00|0.00|1.0000000000|1|10000000");
            ArapInput input = NotesProcReturnBo.Input(plan);
            Same("ap return input", input.Head["cdwcode"] + "|" + input.Head["ccode"] + "|" + input.Head["cdigest"], "V001|220201|转出票据N1");
            Same("ap return stamp", NotesProcSelfTest.Join(NotesProcReturnSql.StampArgs(plan)), "N1|张三|2026-09-16|9002|||app0|YF202609160001|"
                + "7001|AP|P0");
            Same("ap return detail", NotesProcSelfTest.Join(NotesProcReturnSql.DetailArgs(plan)), "9|P0|YF202609160001|2026-09-16|"
                + "2026-09-16|V001|D01||220201|退回供应商乙电子承兑|人民币|0.00|100000.00|0.00|100000.00|CLAP0000000000001|P0|"
                + "YF202609160001|AP|张三|张三");
            Same("ap note detail", NotesProcSelfTest.Join(NotesProcSql.DetailArgs(plan)), "9|50|50|N1-1-10000000|2026-08-28|2026-09-16|"
                + "V001|2201|退回供应商乙电子承兑|人民币|100000.00|0.00|100000.00|0.00|9C|CLAP0000000000001|50|N1-1-10000000|AP|张三|张三");
            string[] texts = NotesProcReturnSql.Texts();
            Same("ap return marks", texts[1].Split('?').Length - 1, NotesProcReturnSql.DetailArgs(plan).Length);
        }

        // 第二级写入：应付票据的登记、处理、取消、制单只对测试账套开放（没有任务时一律 403），应收票据不查。
        static void CheckGate()
        {
            Status("ap reg gate", 403, delegate { NotesRegReq.TestGate(null, "AP"); });
            Status("ap proc gate", 403, delegate { NotesProcReq.TestGate(null, "AP"); });
            NotesRegReq.TestGate(null, "AR");
            NotesProcReq.TestGate(null, "AR");
            ProcVoucherAsk ask = ArapProcVoucherReq.Parse(Body("{\"flag\":\"AP\",\"cancel_nos\":[\"CLAP1\"]}"));
            Status("ap voucher gate", 403, delegate { ArapProcVoucherReq.TestGate(null, ask); });
        }

        static void Bad(string name, string json, string field)
        {
            try
            {
                NotesRegReq.ParseCreate(Body(json));
            }
            catch (BridgeException ex)
            {
                Expect(name + " (" + ex.Field + ")", ex.Status == 400 && ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void BadProc(string name, string json, string field)
        {
            try
            {
                NotesProcReq.Parse(Body(json));
            }
            catch (BridgeException ex)
            {
                Expect(name + " (" + ex.Field + ")", ex.Status == 400 && ex.Field == field && ex.Message == NotesProcReq.ApNotesOps);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void Status(string name, int status, Action act)
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

        static Dictionary<string, object> Body(string json)
        {
            return Json.Parse(Encoding.UTF8.GetBytes(json));
        }

        static void Same(string name, object got, object want)
        {
            if (!object.Equals(got, want))
            {
                throw new InvalidOperationException(name + "：" + Convert.ToString(got, CultureInfo.InvariantCulture));
            }
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
