using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // --selftest 的票据登记、删除部分（notes/create、notes/delete）：请求校验、锁键、收款单输入、票据行的 INSERT。只跑纯函数，不连库、不建 COM。
    internal static class NotesRegSelfTest
    {
        const string Good = "{\"flag\":\"AR\",\"note_no\":\" N2026001 \",\"settle_code\":\"301\",\"amount\":10000,"
            + "\"sign_date\":\"2026-08-01\",\"receipt_date\":\"2026-08-10\",\"expire_date\":\"2027-02-01\",\"customer\":\"C001\","
            + "\"dept\":\"D01\",\"receiver\":\"示例科技有限公司\"";

        public static void Run()
        {
            CheckCreate();
            CheckRefuse();
            CheckDelete();
            CheckLocks();
            CheckReceipt();
            CheckInsert();
            CheckUndo();
            CheckSplitAsk();
            CheckSplitRules();
            CheckSplitInsert();
            CheckSplitUndo();
        }

        const string Split = ",\"sub_start\":1,\"sub_end\":1000000";

        // 分包票据：sub_start / sub_end 的校验（NoteSplit.Parse）。
        static void CheckSplitAsk()
        {
            NoteRegAsk ask = NotesRegReq.ParseCreate(Body(Good + "}"));
            Expect("notes split none", !ask.Split && ask.SubStart == 0 && ask.SubEnd == 0);
            ask = NotesRegReq.ParseCreate(Body(Good + Split + "}"));
            Expect("notes split ok", ask.Split && ask.SubStart == 1 && ask.SubEnd == 1000000);
            ask = NotesRegReq.ParseCreate(Body(Good + ",\"sub_start\":999999999000000,\"sub_end\":999999999999999}"));
            Same("notes split 15 digits", ask.SubEnd, 999999999999999L);
            Bad("notes split half start", Good + ",\"sub_start\":1}", "sub_end");
            Bad("notes split half end", Good + ",\"sub_end\":1}", "sub_start");
            Bad("notes split text", Good + ",\"sub_start\":\"1\",\"sub_end\":1000000}", "sub_start");
            Bad("notes split float", Good + ",\"sub_start\":1.5,\"sub_end\":1000000}", "sub_start");
            Bad("notes split zero", Good + ",\"sub_start\":0,\"sub_end\":999999}", "sub_start");
            Bad("notes split 16 digits", Good + ",\"sub_start\":1,\"sub_end\":1000000000000000}", "sub_end");
            Bad("notes split reversed", Good + ",\"sub_start\":1000000,\"sub_end\":1}", "sub_end");
            BadText("notes split amount", Good + ",\"sub_start\":2,\"sub_end\":1000000}", "sub_end",
                "子票区间与金额不符：区间 999999 张 = 9999.99 元，票面 10000.00（每张子票 0.01 元）");
            string[] keys = NotesRegReq.LockKeysOf(NotesRegReq.CreatePath, Body(Good + Split + "}"));
            Same("notes split locks", string.Join(",", keys), "note:AR,new:ar_receipt");
        }

        // 收款单票据号的后缀、查重模式、可用区间核对（NoteSplit）。
        static void CheckSplitRules()
        {
            Same("notes coid plain", NoteSplit.CoId("N1", 0, 0), "N1");
            Same("notes coid split", NoteSplit.CoId("N1", 1309652951L, 1311152950L), "N1-1309652951-1311152950");
            long a;
            long b;
            Expect("notes suffix ok", NoteSplit.TrySuffix("N1-1-100", "N1", out a, out b) && a == 1 && b == 100);
            Expect("notes suffix padded", NoteSplit.TrySuffix("N1-000000000001-000000000100", "N1", out a, out b) && a == 1 && b == 100);
            foreach (string no in new string[] { "N1", "N1-2", "N1-2-1-100", "N10-1-100", "N1-a-1", "N1--1", "N1-1-" })
            {
                Expect("notes suffix refuse " + no, !NoteSplit.TrySuffix(no, "N1", out a, out b));
            }
            Same("notes suffix like", NoteSplit.SuffixPattern("A_1%[!"), "A!_1!%![!!-%-%");
            NoteRow plain = SplitNote(false);
            Expect("notes match plain", NoteSplit.Matches("N1", plain) && !NoteSplit.Matches("N1-1-1000000", plain));
            NoteRow split = SplitNote(true);
            Expect("notes match split", NoteSplit.Matches("N1-1-1000000", split)
                && NoteSplit.Matches("N1-000000000001-000001000000", split));
            Expect("notes match split refuse", !NoteSplit.Matches("N1", split) && !NoteSplit.Matches("N1-1-999999", split)
                && !NoteSplit.Matches("", split));
            Same("notes coid row", NoteSplit.CoId(split), "N1-1-1000000");
            List<NoteRange> none = new List<NoteRange>();
            List<NoteRange> whole = new List<NoteRange> { new NoteRange(1, 1000000) };
            Same("notes range plain", NoteSplit.RangeRefusal(none, plain), "");
            Expect("notes range plain extra", NoteSplit.RangeRefusal(whole, plain).Length > 0);
            Same("notes range split", NoteSplit.RangeRefusal(whole, split), "");
            Expect("notes range split none", NoteSplit.RangeRefusal(none, split).IndexOf("整段", StringComparison.Ordinal) >= 0);
            List<NoteRange> used = new List<NoteRange> { new NoteRange(1, 100), new NoteRange(201, 1000000) };
            Expect("notes range split used", NoteSplit.RangeRefusal(used, split).IndexOf("整段", StringComparison.Ordinal) >= 0);
            split.Amount = 100m;
            Expect("notes range amount", NoteSplit.RangeRefusal(whole, split).IndexOf("票面", StringComparison.Ordinal) >= 0);
            split = SplitNote(true);
            split.Start = 0;
            Expect("notes range open", NoteSplit.RangeRefusal(whole, split).IndexOf("不完整", StringComparison.Ordinal) >= 0);
            Dictionary<string, object> body = new Dictionary<string, object>();
            NoteSplit.Put(body, false, 0, 0);
            Expect("notes put plain", body["sub_start"] == null && body["sub_end"] == null);
            NoteSplit.Put(body, true, 1, 1000000);
            Same("notes put split", body["sub_end"], 1000000L);
        }

        // 分包票据行：多 csubnostart / csubnoend 两列两参数，bsubpackage 写 1。
        static void CheckSplitInsert()
        {
            NoteRegPlan plan = new NoteRegPlan();
            plan.Ask = NotesRegReq.ParseCreate(Body(Good + Split + "}"));
            plan.NoteKm = "112101";
            plan.Currency = "人民币";
            plan.VtId = 131181;
            string sql = NoteInsert.Sql(plan);
            object[] args = NoteInsert.Args(plan);
            string[] names = NoteInsert.Names(plan);
            Same("notes split insert columns", names.Length, 75);
            Same("notes split insert marks", NoteInsert.Marks(sql), args.Length);
            Same("notes split insert args", args.Length, 25);
            Expect("notes split insert cols", Array.IndexOf(names, "csubnostart") >= 0 && Array.IndexOf(names, "csubnoend") >= 0
                && Array.IndexOf(names, "bsubpackage") >= 0);
            Expect("notes split insert range", Array.IndexOf(args, "1") >= 0 && Array.IndexOf(args, "1000000") >= 0);
            const string flagged = ", 0, 1, convert(bigint, ?), convert(bigint, ?), N'', ";
            Expect("notes split insert flag", sql.IndexOf(flagged, StringComparison.Ordinal) > 0);
            plan.Ask = NotesRegReq.ParseCreate(Body(Good + "}"));
            Expect("notes plain insert flag", NoteInsert.Sql(plan).IndexOf(", 0, 0, N'', ", StringComparison.Ordinal) > 0
                && Array.IndexOf(NoteInsert.Names(plan), "csubnostart") < 0);
        }

        // 分包票据的收款单弃审：票据按 iCloseID 找到后，收款单票据号须是「票据号-起-止」。
        static void CheckSplitUndo()
        {
            NoteRow note = NoteFor(NoteDoc());
            note.Split = true;
            note.Start = 1;
            note.End = 1000000;
            string co = "N2026001-1-1000000";
            Same("notes undo split ok", ArapSql.NoteUndoRefusal(NoteDoc("note_no", co, "co_id", co), note), "");
            Hit("notes undo split plain", NoteDoc(), note, "不一致");
            co = "N2026001-1-999999";
            Hit("notes undo split range", NoteDoc("note_no", co, "co_id", co), note, "子票区间");
        }

        static NoteRow SplitNote(bool split)
        {
            NoteRow note = new NoteRow();
            note.Code = "N1";
            note.Amount = 10000m;
            note.Remain = 10000m;
            note.Split = split;
            note.Start = split ? 1 : 0;
            note.End = split ? 1000000 : 0;
            return note;
        }

        static void BadText(string name, string json, string field, string part)
        {
            try
            {
                NotesRegReq.ParseCreate(Body(json));
            }
            catch (BridgeException ex)
            {
                Expect(name + " (" + ex.Message + ")", ex.Status == 400 && ex.Field == field
                    && ex.Message.IndexOf(part, StringComparison.Ordinal) >= 0);
                return;
            }
            throw new InvalidOperationException(name);
        }

        // 票据登记生成的收款单的弃审判断（ArapSql.NoteReceipt / NoteUndoRefusal）。
        static void CheckUndo()
        {
            ArapSpec ar = ArapReq.Spec(Kinds.Find("ar_receipt"));
            ArapSpec ap = ArapReq.Spec(Kinds.Find("ap_payment"));
            ArapDoc doc = NoteDoc();
            Expect("notes undo origin", ArapSql.NoteReceipt(ar, doc));
            Expect("notes undo ap", ArapSql.NoteReceipt(ap, doc));
            doc.Row["src_flag"] = "A";
            Expect("notes undo manual", !ArapSql.NoteReceipt(ar, doc));
            NoteRow note = NoteFor(doc);
            Same("notes undo ok", ArapSql.NoteUndoRefusal(NoteDoc(), note), "");
            Hit("notes undo missing", NoteDoc(), null, "不存在");
            note.CloseId = 99;
            Hit("notes undo other receipt", NoteDoc(), note, "不存在");
            note = NoteFor(doc);
            note.Subs = 1;
            Hit("notes undo subs", NoteDoc(), note, "已有处理记录");
            note = NoteFor(doc);
            note.Remain = 100m;
            Hit("notes undo remain", NoteDoc(), note, "已有处理记录");
            note = NoteFor(doc);
            note.Change = 1;
            Hit("notes undo change", NoteDoc(), note, "已换票");
            note = NoteFor(doc);
            Hit("notes undo voucher", NoteDoc("voucher", "记-0001"), note, "已生成凭证");
            Hit("notes undo bank", NoteDoc("to_bank", "1"), note, "网银");
            Hit("notes undo settled", NoteDoc("settler", "张三"), note, "已核销");
            Hit("notes undo lines", NoteDoc("line_settled", "1"), note, "已核销");
            Hit("notes undo co id", NoteDoc("co_id", "N9"), note, "不一致");
            ArapSpec bill = ArapReq.Spec(Kinds.Find("ar_bill"));
            ArapSql.RefuseVerify(ar, NoteDoc());
            ArapSql.RefuseVerify(bill, NoteDoc("co_type", ""));
            Refused("notes return bill verify", delegate { ArapSql.RefuseVerify(bill, NoteDoc()); }, "票据退回");
        }

        static void Refused(string name, Action act, string part)
        {
            try
            {
                act();
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 409 && ex.Message.IndexOf(part, StringComparison.Ordinal) >= 0);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static ArapDoc NoteDoc(params string[] pairs)
        {
            ArapDoc doc = new ArapDoc();
            doc.Id = 1201;
            doc.Code = "SK0001";
            doc.Row = new Dictionary<string, object>();
            doc.Row["src_flag"] = "C";
            doc.Row["co_type"] = "50";
            doc.Row["note_no"] = "N2026001";
            doc.Row["co_id"] = "N2026001";
            doc.Row["settled"] = "0";
            doc.Row["line_settled"] = "0";
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                doc.Row[pairs[i]] = pairs[i + 1];
            }
            return doc;
        }

        static NoteRow NoteFor(ArapDoc doc)
        {
            NoteRow note = new NoteRow();
            note.Id = 3416;
            note.Code = "N2026001";
            note.CloseId = doc.Id;
            note.Amount = 10000m;
            note.Remain = 10000m;
            return note;
        }

        static void Hit(string name, ArapDoc doc, NoteRow note, string part)
        {
            string why = ArapSql.NoteUndoRefusal(doc, note);
            Expect(name + "：" + why, why.IndexOf(part, StringComparison.Ordinal) >= 0);
        }

        static void CheckCreate()
        {
            NoteRegAsk ask = NotesRegReq.ParseCreate(Body(Good + "}"));
            Same("notes reg no", ask.NoteNo, "N2026001");
            Same("notes reg amount", ask.Amount, 10000m);
            Same("notes reg settle", ask.SettleCode, "301");
            Same("notes reg person", ask.Person, "");
            Same("notes reg drawer", ask.Drawer, "");
            Same("notes reg expire", ask.ExpireDate, "2027-02-01");
            ask = NotesRegReq.ParseCreate(Body(Good + ",\"person\":\"P01\",\"drawer\":\"出票单位\",\"drawer_bank\":\"示例银行\","
                + "\"km\":\"112201\",\"digest\":\" 收到承兑 \"}"));
            Same("notes reg person given", ask.Person, "P01");
            Same("notes reg digest", ask.Digest, "收到承兑");
            Same("notes reg km", ask.Km, "112201");
        }

        static void CheckRefuse()
        {
            Bad("notes reg flag", "{\"flag\":\"XX\"}", "flag");
            Bad("notes reg zero", Good.Replace("10000", "0") + "}", "amount");
            Bad("notes reg cents", Good.Replace("10000", "1.005") + "}", "amount");
            Bad("notes reg date", Good.Replace("2026-08-01", "2026/08/01") + "}", "sign_date");
            Bad("notes reg expire", Good.Replace("2027-02-01", "2026-07-01") + "}", "expire_date");
            Bad("notes reg receipt", Good.Replace("2026-08-10", "2026-07-30") + "}", "receipt_date");
            Bad("notes reg receiver", Good.Replace("示例科技有限公司", " ") + "}", "receiver");
            Bad("notes reg control", Good + ",\"digest\":\"a\\nb\"}", "digest");
            Bad("notes reg number", Good.Replace("\"C001\"", "1") + "}", "customer");
        }

        static void CheckDelete()
        {
            NoteDelAsk ask = NotesRegReq.ParseDelete(Body("{\"flag\":\"AR\",\"note_no\":\"N1\"}"));
            Same("notes del no", ask.NoteNo, "N1");
            Same("notes del id none", ask.NoteId, 0);
            ask = NotesRegReq.ParseDelete(Body("{\"flag\":\"AR\",\"id\":3416}"));
            Same("notes del id", ask.NoteId, 3416);
            BadDel("notes del both", "{\"flag\":\"AR\",\"note_no\":\"N1\",\"id\":1}", "note_no");
            BadDel("notes del none", "{\"flag\":\"AR\"}", "note_no");
            BadDel("notes del id zero", "{\"flag\":\"AR\",\"id\":0}", "id");
        }

        static void CheckLocks()
        {
            string[] keys = NotesRegReq.LockKeysOf(NotesRegReq.CreatePath, Body(Good + "}"));
            Same("notes reg locks", string.Join(",", keys), "note:AR,new:ar_receipt");
            keys = NotesRegReq.LockKeysOf(NotesRegReq.DeletePath, Body("{\"flag\":\"AR\",\"id\":7}"));
            Same("notes del locks", string.Join(",", keys), "note:AR,arap:writeoff:AR");
            Same("notes bad locks", NotesRegReq.LockKeysOf(NotesRegReq.DeletePath, Body("{}")).Length, 0);
            Expect("notes other path", NotesRegReq.LockKeysOf("/u8co/v1/notes/process", Body("{}")) == null);
            Same("notes del action", NotesRegReq.ActionOf(NotesRegReq.DeletePath), NotesRegReq.DeleteAction);
        }

        // 收款单表头、表体字段（与 vouchers/create 的 ar_receipt 白名单一致：ArapReq.CloseHead / CloseLine）。
        static void CheckReceipt()
        {
            NoteRegPlan plan = new NoteRegPlan();
            plan.Ask = NotesRegReq.ParseCreate(Body(Good + ",\"drawer_bank\":\"示例银行\"}"));
            plan.NoteKm = "112101";
            plan.CtrlKm = "112201";
            plan.Currency = "人民币";
            plan.Digest = NotesRegGate.DefaultDigest;
            Dictionary<string, object> head;
            object[] lines;
            NotesRegGate.ReceiptFields(plan, out head, out lines);
            Same("notes receipt date", head["dvouchdate"], "2026-08-10");
            Same("notes receipt km", head["ccode"], "112101");
            Same("notes receipt bank", head["cbank"], "示例银行");
            Expect("notes receipt no person", !head.ContainsKey("cperson"));
            Dictionary<string, object> line = (Dictionary<string, object>)lines[0];
            Same("notes receipt line", line["ckm"] + "|" + line["iamt"] + "|" + line["itype"], "112201|10000.00|0");
            ArapInput input = ArapReq.CheckCreate(Kinds.Find("ar_receipt"), head, lines, "人民币");
            Same("notes receipt sum", input.Sum, 10000m);
            Same("notes receipt lines", input.Lines.Count, 1);
        }

        // 票据行：列数、参数个数与 SQL 一致，列不重复、必填列都在，参数值按列对上。
        static void CheckInsert()
        {
            NoteRegPlan plan = new NoteRegPlan();
            plan.Ask = NotesRegReq.ParseCreate(Body(Good + "}"));
            plan.NoteKm = "112101";
            plan.PartnerName = "示例客户";
            plan.Drawer = "示例客户";
            plan.Currency = "人民币";
            plan.Operator = "张三";
            plan.VtId = 131181;
            string sql = NoteInsert.Sql(plan);
            object[] args = NoteInsert.Args(plan);
            string[] names = NoteInsert.Names(plan);
            Same("notes insert columns", names.Length, 73);
            Same("notes insert marks", NoteInsert.Marks(sql), args.Length);
            Same("notes insert args", args.Length, 23);
            Expect("notes insert unique", new HashSet<string>(names, StringComparer.OrdinalIgnoreCase).Count == names.Length);
            foreach (string must in new string[] { "cLink", "cFlag", "iAmount", "iRAmount", "dSignDate", "dExpireDate", "iCloseID",
                "VT_ID", "iPrintCount", "bSecurityDeposit", "iChangeType", "iReceiveAmount", "iReceiveAmount_Local", "iVT_ID", "cCode" })
            {
                Expect("notes insert has " + must, Array.IndexOf(names, must) >= 0);
            }
            Expect("notes insert no operator", Array.IndexOf(names, "cOperator") < 0 && Array.IndexOf(names, "Auto_ID") < 0);
            Same("notes insert link", args[0], "AR50N2026001");
            Same("notes insert amount", args[args.Length - 1], "10000.00");
            Expect("notes insert barcode", Array.IndexOf(args, "||ar50|N2026001") >= 0);
            Expect("notes insert vt", Array.IndexOf(args, 131181) >= 0);
            Expect("notes insert sql", sql.StartsWith("insert into AP_Note (cLink, ", StringComparison.Ordinal)
                && sql.EndsWith(" from (select convert(money, ?) as a) v", StringComparison.Ordinal));
            plan.VtId = 0;
            Expect("notes insert vt none", Array.IndexOf(NoteInsert.Args(plan), null) >= 0);
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
                NotesRegReq.ParseCreate(Body(json));
            }
            catch (BridgeException ex)
            {
                Expect(name + " (" + ex.Field + ")", ex.Status == 400 && ex.Code == "bad_request" && ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void BadDel(string name, string json, string field)
        {
            try
            {
                NotesRegReq.ParseDelete(Body(json));
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
