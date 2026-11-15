using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的历史发货单表头修正部分：请求解析、类型闸门、下游传播的纯判断（出库单 shared / differs、发票其余来源
    // 都已改成新值才跟着改、清空地址写 NULL、Ar_Detail 行）、UPDATE 片段、正向加反向的往返、响应形状、写分类 / 写闸门 /
    // 预演模式 / 权限规则 / 单据锁的登记。不连库。
    internal static class VoucherHeadFixSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckParseRejects();
            CheckGuard();
            CheckSame();
            CheckOutDecide();
            CheckInvoiceDecide();
            CheckPartial();
            CheckArRows();
            CheckSetClause();
            CheckRoundTripSingle();
            CheckRoundTripShared();
            CheckRoundTripClear();
            CheckBody();
            CheckValidOn();
            CheckPersonEmpty();
            CheckAudited();
            CheckWiring();
            VoucherHeadFixUndoSelfTest.Run();
            VoucherHeadFixRevertSelfTest.Run();
        }

        static void CheckParse()
        {
            HeadFixAsk ask = VoucherHeadFix.Parse(Body(Map("addr_code", "02", "person_code", "P900002"),
                Map("addr_code", "01", "person_code", " P900001 ")));
            Eq("parse count", ask.Fields.Count, 2);
            Eq("parse addr", ask.Find("addr_code").New, "02");
            Eq("parse addr expect", ask.Find("addr_code").Expect, "01");
            Eq("parse person", ask.Find("person_code").New, "P900002");
            Eq("parse person expect trimmed", ask.Find("person_code").Expect, "P900001");
            Eq("parse columns", ask.Find("addr_code").Column + "/" + ask.Find("person_code").Column, "caddcode/cPersonCode");
            ask = VoucherHeadFix.Parse(Body(Map("person_code", "P900002"), Map("person_code", "")));
            Eq("parse person only", ask.Fields.Count, 1);
            Eq("parse expect empty", ask.Find("person_code").Expect, "");
            ask = VoucherHeadFix.Parse(Body(Map("addr_code", ""), Map("addr_code", "01")));
            Eq("parse clear addr", ask.Find("addr_code").New, "");
            Eq("parse clear keeps expect", ask.Find("addr_code").Expect, "01");
            ask = VoucherHeadFix.Parse(Body(Map("addr_code", "02"), Map("addr_code", "")));
            Eq("parse fill addr from empty", ask.Find("addr_code").Expect, "");
            Eq("parse long addr", VoucherHeadFix.Parse(Body(Map("addr_code", new string('A', 30)), Map("addr_code", "")))
                .Find("addr_code").New.Length, 30);
        }

        static void CheckParseRejects()
        {
            Bad("no fields", Body(null, Map("addr_code", "01")));
            Bad("empty fields", Body(new Dictionary<string, object>(), Map("addr_code", "01")));
            Bad("no expect", Body(Map("addr_code", "02"), null));
            Bad("expect missing key", Body(Map("addr_code", "02", "person_code", "P900002"), Map("addr_code", "01")));
            Bad("expect extra key", Body(Map("addr_code", "02"), Map("addr_code", "01", "person_code", "P900001")));
            Bad("expect only other key", Body(Map("addr_code", "02"), Map("person_code", "P900001")));
            Bad("unknown field", Body(Map("addr_code", "02", "cust", "C900001"), Map("addr_code", "01")));
            Bad("unknown expect", Body(Map("addr_code", "02"), Map("addr_code", "01", "cust", "x")));
            Bad("person empty", Body(Map("person_code", ""), Map("person_code", "P900001")));
            Bad("person long", Body(Map("person_code", new string('P', 21)), Map("person_code", "P900001")));
            Bad("addr long", Body(Map("addr_code", new string('A', 31)), Map("addr_code", "")));
            Bad("leading blank", Body(Map("addr_code", " 02"), Map("addr_code", "01")));
            Bad("trailing blank", Body(Map("person_code", "P900002 "), Map("person_code", "P900001")));
            Bad("control char", Body(Map("addr_code", "0\t2"), Map("addr_code", "01")));
            Bad("control in expect", Body(Map("addr_code", "02"), Map("addr_code", "0\n1")));
            Bad("not a string", Body(Map("addr_code", 2), Map("addr_code", "01")));
            Bad("expect null", Body(Map("addr_code", "02"), Map("addr_code", null)));
            Bad("fields not object", Body("x", Map("addr_code", "01")));
            Bad("expect not object", Body(Map("addr_code", "02"), "x"));
        }

        static void CheckGuard()
        {
            VoucherHeadFix.GuardKind(Kinds.Find("dispatch"));
            BadKind("guard sale_return", Kinds.Find("sale_return"));
            BadKind("guard invoice", Kinds.Find("sale_invoice"));
            BadKind("guard null", null);
        }

        static void CheckSame()
        {
            Expect("same null empty", HeadFixPlan.Same(null, ""));
            Expect("same trim", HeadFixPlan.Same(" 01 ", "01"));
            Expect("same case", HeadFixPlan.Same("p900001", "P900001"));
            Expect("same differs", !HeadFixPlan.Same("01", "02"));
            Expect("same empty differs", !HeadFixPlan.Same("", "02"));
        }

        // 出库单：含别的发货单的行 → shared；当前值不是改前值 → differs 并报告它的值；否则更新。
        static void CheckOutDecide()
        {
            List<HeadFixField> addr = Fields(Field("addr_code", "01", "02"));
            HeadFixRow ok = HeadFixPlan.Decide(Out(1, "XS1", "01", "P1", false), addr);
            Eq("out updated", ok.Action, "updated");
            Eq("out set", ok.Set.Count, 1);
            HeadFixRow shared = HeadFixPlan.Decide(Out(2, "XS2", "01", "P1", true), addr);
            Eq("out shared", shared.Action + "/" + shared.Reason, "skipped/shared");
            Eq("out shared value", shared.Value["addr_code"], "01");
            HeadFixRow differs = HeadFixPlan.Decide(Out(3, "XS3", "09", "P1", false), addr);
            Eq("out differs", differs.Action + "/" + differs.Reason, "skipped/differs");
            Eq("out differs value", differs.Value["addr_code"], "09");
            HeadFixRow nullOld = HeadFixPlan.Decide(Out(4, "XS4", "", "P1", false), Fields(Field("addr_code", "", "02")));
            Eq("out null old matches empty", nullOld.Action, "updated");
            HeadFixRow already = HeadFixPlan.Decide(Out(5, "XS5", "02", "P1", false), addr);
            Eq("out already new", already.Action + "/" + already.Reason, "skipped/already_new");
            Eq("out already new value", already.Value["addr_code"], "02");
            Eq("out already new not in undo", HeadFixUndo.Map(new List<HeadFixRow>(new HeadFixRow[] { already })).Count, 0);
        }

        // 发票：没有无来源行、当前值等于改前值、其余来源发货单都已经是新值，才跟着改。
        static void CheckInvoiceDecide()
        {
            List<HeadFixField> addr = Fields(Field("addr_code", "01", "02"));
            HeadFixDoc single = Inv(10, "SO1", "01", "P1");
            Eq("inv single", HeadFixPlan.Decide(single, addr).Action, "updated");
            HeadFixDoc pending = Inv(11, "SO2", "01", "P1");
            pending.Others.Add(Disp("01", "P1"));
            HeadFixRow wait = HeadFixPlan.Decide(pending, addr);
            Eq("inv other not yet", wait.Action + "/" + wait.Reason, "skipped/shared");
            Eq("inv other value", wait.Value["addr_code"], "01");
            HeadFixDoc ready = Inv(12, "SO3", "01", "P1");
            ready.Others.Add(Disp("02", "P1"));
            ready.Others.Add(Disp("02", "P2"));
            Eq("inv others agree", HeadFixPlan.Decide(ready, addr).Action, "updated");
            HeadFixDoc foreign = Inv(13, "SO4", "01", "P1");
            foreign.Foreign = true;
            Eq("inv foreign line", HeadFixPlan.Decide(foreign, addr).Reason, "shared");
            HeadFixDoc moved = Inv(14, "SO5", "07", "P1");
            Eq("inv differs", HeadFixPlan.Decide(moved, addr).Reason, "differs");
            HeadFixDoc oneBad = Inv(15, "SO6", "01", "P1");
            oneBad.Others.Add(Disp("02", "P1"));
            oneBad.Others.Add(Disp("03", "P1"));
            Eq("inv one other differs", HeadFixPlan.Decide(oneBad, addr).Reason, "shared");
            HeadFixDoc clearReady = Inv(16, "SO7", "01", "P1");
            clearReady.Others.Add(Disp("", "P1"));
            Eq("inv others null equal empty", HeadFixPlan.Decide(clearReady, Fields(Field("addr_code", "01", ""))).Action,
                "updated");
        }

        // 两个字段各自判断：地址可改、业务员被别人改过时，更新地址并在 skipped_fields 里说明业务员。
        static void CheckPartial()
        {
            List<HeadFixField> both = Fields(Field("addr_code", "01", "02"), Field("person_code", "P1", "P2"));
            HeadFixRow row = HeadFixPlan.Decide(Out(20, "XS", "01", "P9", false), both);
            Eq("partial action", row.Action, "updated");
            Eq("partial set", row.Set.Count + row.Set[0].Key, "1addr_code");
            Eq("partial skipped", row.Skipped["person_code"], "differs");
            Dictionary<string, object> map = row.Map();
            Expect("partial map", ((List<object>)map["fields"]).Count == 1 && map.ContainsKey("skipped_fields")
                && !map.ContainsKey("reason"));
            HeadFixRow none = HeadFixPlan.Decide(Out(21, "XS", "09", "P9", false), both);
            Eq("none action", none.Action, "skipped");
            Eq("none reason first", none.Reason, "differs");
            Expect("none map", none.Map().ContainsKey("reason") && none.Map().ContainsKey("value"));
        }

        // 发票业务员改了才有 Ar_Detail 行；只改地址不动 Ar_Detail；没有应收行时 action=none。
        static void CheckArRows()
        {
            HeadFixDoc inv = Inv(30, "SO9", "01", "P1");
            inv.VouchType = "26";
            inv.ArIds.AddRange(new int[] { 11, 12, 13, 14 });
            List<HeadFixDoc> docs = new List<HeadFixDoc>();
            docs.Add(inv);
            List<HeadFixRow> both = HeadFixPlan.DecideAll(docs, Fields(Field("person_code", "P1", "P2")));
            Eq("ar rows count", both.Count, 2);
            Eq("ar table", both[1].Table, "Ar_Detail");
            Dictionary<string, object> ar = both[1].Map();
            Eq("ar map", ar["invoice"] + "/" + ar["rows"] + "/" + ar["action"], "SO9/4/updated");
            Expect("ar map no id", !ar.ContainsKey("id"));
            Eq("ar addr only", HeadFixPlan.DecideAll(docs, Fields(Field("addr_code", "01", "02"))).Count, 1);
            inv.ArIds.Clear();
            Eq("ar none", HeadFixPlan.DecideAll(docs, Fields(Field("person_code", "P1", "P2")))[1].Action, "none");
            HeadFixDoc skipped = Inv(31, "SO10", "01", "P7");
            skipped.ArIds.AddRange(new int[] { 21, 22, 23 });
            docs.Clear();
            docs.Add(skipped);
            Eq("ar not touched when invoice skipped",
                HeadFixPlan.DecideAll(docs, Fields(Field("person_code", "P1", "P2"))).Count, 1);
            HeadFixDoc out32 = Out(32, "XS", "01", "P1", false);
            docs.Clear();
            docs.Add(out32);
            Eq("ar not for sale-out", HeadFixPlan.DecideAll(docs, Fields(Field("person_code", "P1", "P2"))).Count, 1);
        }

        static void CheckSetClause()
        {
            List<object> args = new List<object>();
            Eq("set both", HeadFixPlan.SetClause(Fields(Field("addr_code", "01", "02"), Field("person_code", "P1", "P2")), args),
                "caddcode=?, cPersonCode=?");
            Eq("set args", args.Count + "/" + args[0] + "/" + args[1], "2/02/P2");
            args.Clear();
            Eq("set clear", HeadFixPlan.SetClause(Fields(Field("addr_code", "01", "")), args), "caddcode=NULL");
            Eq("set clear no arg", args.Count, 0);
            args.Clear();
            Eq("set person only", HeadFixPlan.SetClause(Fields(Field("person_code", "P1", "P2")), args), "cPersonCode=?");
        }

        // 单来源：正向改地址和业务员，反向（Reverse）把每一列恢复。
        static void CheckRoundTripSingle()
        {
            HeadFixDoc outDoc = Out(40, "XS", "01", "P1", false);
            HeadFixDoc inv = Inv(41, "SO", "01", "P1");
            List<HeadFixDoc> docs = new List<HeadFixDoc>();
            docs.Add(outDoc);
            docs.Add(inv);
            List<HeadFixField> fwd = Fields(Field("addr_code", "01", "02"), Field("person_code", "P1", "P2"));
            Apply(docs, HeadFixPlan.DecideAll(docs, fwd));
            Eq("single forward out", outDoc.Values["addr_code"] + outDoc.Values["person_code"], "02P2");
            Eq("single forward inv", inv.Values["addr_code"] + inv.Values["person_code"], "02P2");
            Apply(docs, HeadFixPlan.DecideAll(docs, HeadFixPlan.Reverse(fwd)));
            Eq("single reverse out", outDoc.Values["addr_code"] + outDoc.Values["person_code"], "01P1");
            Eq("single reverse inv", inv.Values["addr_code"] + inv.Values["person_code"], "01P1");
        }

        // 两张发货单合开一张发票：先改的那张跳过发票 (shared)，后改的那张（另一张已是新值）带着发票一起改；
        // 回滚时先改回的那张跳过，最后改回的那张把发票带回去。Others 总是「另一张发货单」的当前值。
        static void CheckRoundTripShared()
        {
            HeadFixDoc inv = Inv(50, "SO", "01", "P1");
            List<HeadFixDoc> docs = new List<HeadFixDoc>();
            docs.Add(inv);
            inv.Others.Add(Disp("01", "P1"));
            HeadFixRow skip = HeadFixPlan.DecideAll(docs, Fields(Field("addr_code", "01", "02")))[0];
            Eq("shared first skipped", skip.Action + "/" + skip.Reason, "skipped/shared");
            inv.Others.Clear();
            inv.Others.Add(Disp("02", "P1"));
            Apply(docs, HeadFixPlan.DecideAll(docs, Fields(Field("addr_code", "01", "02"))));
            Eq("shared second updates invoice", inv.Values["addr_code"], "02");
            HeadFixRow back = HeadFixPlan.DecideAll(docs, Fields(Field("addr_code", "02", "01")))[0];
            Eq("shared reverse first skipped", back.Action + "/" + back.Reason, "skipped/shared");
            inv.Others.Clear();
            inv.Others.Add(Disp("01", "P1"));
            Apply(docs, HeadFixPlan.DecideAll(docs, Fields(Field("addr_code", "02", "01"))));
            Eq("shared reverse restores invoice", inv.Values["addr_code"], "01");
        }

        // 清空：地址 01 → ""（写 NULL），反向 "" → 01 恢复。
        static void CheckRoundTripClear()
        {
            HeadFixDoc outDoc = Out(60, "XS", "01", "P1", false);
            List<HeadFixDoc> docs = new List<HeadFixDoc>();
            docs.Add(outDoc);
            List<HeadFixField> fwd = Fields(Field("addr_code", "01", ""));
            Apply(docs, HeadFixPlan.DecideAll(docs, fwd));
            Eq("clear forward", outDoc.Values["addr_code"], "");
            Apply(docs, HeadFixPlan.DecideAll(docs, HeadFixPlan.Reverse(fwd)));
            Eq("clear reverse", outDoc.Values["addr_code"], "01");
        }

        static void CheckBody()
        {
            HeadFixAsk ask = new HeadFixAsk();
            ask.Id = 7;
            HeadFixField a = Field("addr_code", "01", "02");
            ask.Fields.Add(a);
            ask.Fields.Add(Field("person_code", "P1", "P1"));
            HeadFixDoc inv = Inv(9, "SO1", "01", "P1");
            inv.Others.Add(Disp("01", "P1"));
            List<HeadFixDoc> docs = new List<HeadFixDoc>();
            docs.Add(Out(8, "XS1", "01", "P1", false));
            docs.Add(inv);
            List<HeadFixRow> plan = HeadFixPlan.DecideAll(docs, ask.Changing());
            Dictionary<string, object> body = VoucherHeadFix.Body(ask, "FH1", true, plan);
            Eq("body keys", body["ok"] + "/" + body["type"] + "/" + body["id"] + "/" + body["code"] + "/" + body["changed"],
                "True/dispatch/7/FH1/True");
            Dictionary<string, object> after = (Dictionary<string, object>)body["after"];
            Dictionary<string, object> before = (Dictionary<string, object>)body["before"];
            Eq("body before", before["addr_code"] + "/" + before["person_code"], "01/P1");
            Eq("body after", after["addr_code"] + "/" + after["person_code"], "02/P1");
            List<object> down = (List<object>)body["downstream"];
            Eq("body downstream", down.Count, 2);
            Dictionary<string, object> first = (Dictionary<string, object>)down[0];
            Eq("body out row", first["table"] + "/" + first["id"] + "/" + first["code"] + "/" + first["action"],
                "rdrecord32/8/XS1/updated");
            Dictionary<string, object> second = (Dictionary<string, object>)down[1];
            Eq("body invoice skipped", second["table"] + "/" + second["action"] + "/" + second["reason"], "SaleBillVouch/skipped/shared");
            Dictionary<string, object> value = (Dictionary<string, object>)second["value"];
            Eq("body skipped value", value["addr_code"], "01");
            Dictionary<string, object> dry = VoucherHeadFix.DryBody(ask, "FH1", true, plan);
            Eq("body dry", dry["dry_run"] + "/" + dry["mode"], "True/validate");
            Dictionary<string, object> none = VoucherHeadFix.Body(ask, "FH1", false, new List<HeadFixRow>());
            Eq("body no downstream", ((List<object>)none["downstream"]).Count, 0);
        }

        static void CheckAudited()
        {
            VoucherHeadFixSql.CheckAudited(Map("cVerifier", "张三"));
            Dictionary<string, object>[] bad = new Dictionary<string, object>[] { Map("cVerifier", ""), Map("cVerifier", null), Map() };
            for (int i = 0; i < bad.Length; i++)
            {
                try
                {
                    VoucherHeadFixSql.CheckAudited(bad[i]);
                }
                catch (BridgeException ex)
                {
                    Expect("not audited " + i.ToString(), ex.Status == 409 && ex.Code == "not_audited"
                        && ex.Message.IndexOf("vouchers/update", StringComparison.Ordinal) > 0);
                    continue;
                }
                throw new InvalidOperationException("head_fix not audited " + i.ToString());
            }
        }

        static void CheckPersonEmpty()
        {
            VoucherHeadFixSql.CheckPersonNotEmpty(Field("person_code", "P1", "P2"));
            VoucherHeadFixSql.CheckPersonNotEmpty(Field("addr_code", "", "02"));
            try
            {
                VoucherHeadFixSql.CheckPersonNotEmpty(Field("person_code", "", "P2"));
            }
            catch (BridgeException ex)
            {
                Expect("person empty refused", ex.Status == 409 && ex.Code == "person_empty");
                return;
            }
            throw new InvalidOperationException("head_fix person empty");
        }

        static void CheckValidOn()
        {
            Expect("valid open ended", VoucherHeadFixSql.ValidOn("", "", "2026-03-01"));
            Expect("valid inside", VoucherHeadFixSql.ValidOn("2025-01-01", "2027-01-01", "2026-03-01"));
            Expect("valid on start", VoucherHeadFixSql.ValidOn("2026-03-01", "", "2026-03-01"));
            Expect("valid on end", VoucherHeadFixSql.ValidOn("", "2026-03-01", "2026-03-01"));
            Expect("invalid before", !VoucherHeadFixSql.ValidOn("2026-03-02", "", "2026-03-01"));
            Expect("invalid after", !VoucherHeadFixSql.ValidOn("", "2026-02-28", "2026-03-01"));
            Expect("valid no date", VoucherHeadFixSql.ValidOn("2026-03-02", "2026-03-03", ""));
        }

        // 登记：写分类 (dispatch, head_fix)、写闸门、预演 validate（只认 dispatch）、权限规则、与修改发货单相同的单据锁。
        static void CheckWiring()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["type"] = "dispatch";
            WriteRequestInfo info = WriteClass.Of(VoucherHeadFix.Path, body);
            Expect("wiring class", info != null && info.Type == "dispatch" && info.Op == "head_fix");
            Expect("wiring op listed", WriteClass.IsOp("head_fix") && !WriteClass.IsOp("head-fix"));
            Expect("wiring write gate", WriteGate.IsWrite(VoucherHeadFix.Path));
            Expect("wiring dry accepts", DryRunReq.Accepts(VoucherHeadFix.Path));
            Eq("wiring dry mode", DryRunModes.Mode("vouchers/head_fix", Kinds.Find("dispatch"), "head_fix", ""), "validate");
            Eq("wiring dry other type", DryRunModes.Mode("vouchers/head_fix", Kinds.Find("sale_return"), "head_fix", ""), "refuse");
            PermRule rule = PermRegistry.ForKey(VoucherHeadFix.RuleKey);
            Expect("wiring perm rule", rule != null && rule.Auths.Length > 0 && rule.Objs.Length == 4);
            WorkItem item = new WorkItem();
            item.Path = VoucherHeadFix.Path;
            item.Type = Kinds.Find("dispatch");
            item.HasId = true;
            item.Id = 7;
            string[] keys = DocLocks.KeysOf(item);
            Expect("wiring doc lock", Array.IndexOf(keys, "dispatch:7") >= 0 && Array.IndexOf(keys, "sale_return:7") >= 0);
            WorkItem update = new WorkItem();
            update.Path = "/u8co/v1/vouchers/update";
            update.Type = item.Type;
            update.HasId = true;
            update.Id = 7;
            Expect("wiring same lock as update", Array.IndexOf(DocLocks.KeysOf(update), "dispatch:7") >= 0);
            Expect("wiring spec path", VoucherHeadFix.Spec[0] == VoucherHeadFix.Path && VoucherHeadFix.Spec.Length == 6
                && VoucherHeadFix.Spec[5] == "undo");
        }

        // 把结论里 updated 的出库单 / 发票字段写回内存里的事实（模拟 UPDATE）。
        static void Apply(List<HeadFixDoc> docs, List<HeadFixRow> rows)
        {
            for (int i = 0; i < rows.Count; i++)
            {
                if (rows[i].IsAr || rows[i].Action != HeadFixRow.Updated)
                {
                    continue;
                }
                HeadFixDoc doc = docs.Find(delegate(HeadFixDoc d) { return d.Table == rows[i].Table && d.Id == rows[i].Id; });
                for (int j = 0; j < rows[i].Set.Count; j++)
                {
                    doc.Values[rows[i].Set[j].Key] = rows[i].Set[j].New;
                }
            }
        }

        internal static HeadFixField Field(string key, string old, string value)
        {
            HeadFixField f = new HeadFixField();
            f.Key = key;
            f.Column = key == "addr_code" ? "caddcode" : "cPersonCode";
            f.Old = old;
            f.Expect = old;
            f.New = value;
            return f;
        }

        internal static List<HeadFixField> Fields(params HeadFixField[] fields)
        {
            return new List<HeadFixField>(fields);
        }

        internal static Dictionary<string, string> Disp(string addr, string person)
        {
            Dictionary<string, string> values = new Dictionary<string, string>();
            values["addr_code"] = addr;
            values["person_code"] = person;
            return values;
        }

        internal static HeadFixDoc Out(int id, string code, string addr, string person, bool foreign)
        {
            HeadFixDoc doc = new HeadFixDoc();
            doc.Table = "rdrecord32";
            doc.Id = id;
            doc.Code = code;
            doc.Foreign = foreign;
            doc.Values = Disp(addr, person);
            return doc;
        }

        internal static HeadFixDoc Inv(int id, string code, string addr, string person)
        {
            HeadFixDoc doc = Out(id, code, addr, person, false);
            doc.Table = "SaleBillVouch";
            doc.VouchType = "26";
            return doc;
        }

        internal static Dictionary<string, object> Map(params object[] pairs)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                map[(string)pairs[i]] = pairs[i + 1];
            }
            return map;
        }

        internal static Dictionary<string, object> Body(object fields, object expect)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["type"] = "dispatch";
            body["id"] = 7;
            if (fields != null)
            {
                body["fields"] = fields;
            }
            if (expect != null)
            {
                body["expect"] = expect;
            }
            return body;
        }

        static void Bad(string name, Dictionary<string, object> body)
        {
            try
            {
                VoucherHeadFix.Parse(body);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request");
                return;
            }
            throw new InvalidOperationException("head_fix " + name);
        }

        static void BadKind(string name, VoucherKind kind)
        {
            try
            {
                VoucherHeadFix.GuardKind(kind);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request");
                return;
            }
            throw new InvalidOperationException("head_fix " + name);
        }

        internal static void Eq(string name, object got, object want)
        {
            Expect(name + " (" + got + " != " + want + ")", object.Equals(got, want));
        }

        internal static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("head_fix " + name);
            }
        }
    }
}
