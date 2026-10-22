using System;
using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // --selftest 的 head_fix undo 部分（由 VoucherHeadFixSelfTest.Run 调用）：undo 的解析与拒绝、响应里 undo 的形状，
    // 以及用内存里的「下游世界」模拟正向调用加带 undo 的回滚调用，三种会让无状态反向调用不精确的情形都要精确还原：
    // 合并发票的兄弟发货单已是新值、下游行本来就是新值、Ar_Detail 里有行本来就是新业务员。
    // 另核对：回滚时某行当前值不是 expect → 409 state_mismatch；undo 里的 id 不属于这张发货单 → 409 undo_foreign。不连库。
    internal static class VoucherHeadFixUndoSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckParseRejects();
            CheckMapShape();
            CheckMergedInvoice();
            CheckAlreadyNewRow();
            CheckArRowsAlreadyNew();
            CheckRefusals();
        }

        static void CheckParse()
        {
            Dictionary<string, object> body = Base();
            body["undo"] = Map("person_code", Map("rdrecord32", new object[] { 5, 6L }, "SaleBillVouch", new object[] { 7 },
                "Ar_Detail", new object[] { 11, 12 }));
            HeadFixAsk ask = VoucherHeadFix.Parse(body);
            VoucherHeadFixSelfTest.Eq("undo parse", ask.HasUndo + "/" + ask.Undo["person_code"]["rdrecord32"].Count + "/"
                + ask.Undo["person_code"]["Ar_Detail"][1], "True/2/12");
            body["undo"] = new Dictionary<string, object>();
            ask = VoucherHeadFix.Parse(body);
            VoucherHeadFixSelfTest.Expect("undo empty object", ask.HasUndo && ask.Undo.Count == 0);
            body.Remove("undo");
            VoucherHeadFixSelfTest.Expect("undo absent", !VoucherHeadFix.Parse(body).HasUndo);
            body["undo"] = Map("addr_code", Map("rdrecord32", new List<object>(new object[] { 1 })));
            VoucherHeadFixSelfTest.Expect("undo list type", VoucherHeadFix.Parse(body).Undo["addr_code"]["rdrecord32"][0] == 1);
        }

        static void CheckParseRejects()
        {
            Bad("undo null", null);
            Bad("undo not object", "x");
            Bad("undo key not in fields", Map("cust", Map()));
            Bad("undo key not requested", Map("addr_code", Map("rdrecord32", new object[] { 1 })), true);
            Bad("undo ar under addr", Map("addr_code", Map("Ar_Detail", new object[] { 1 })), false);
            Bad("undo unknown table", Map("person_code", Map("DispatchList", new object[] { 1 })));
            Bad("undo table not list", Map("person_code", Map("rdrecord32", 5)));
            Bad("undo table string", Map("person_code", Map("rdrecord32", "5")));
            Bad("undo fields not object", Map("person_code", new object[] { 1 }));
            Bad("undo duplicate", Map("person_code", Map("rdrecord32", new object[] { 1, 1 })));
            Bad("undo zero id", Map("person_code", Map("rdrecord32", new object[] { 0 })));
            Bad("undo string id", Map("person_code", Map("rdrecord32", new object[] { "1" })));
            Bad("undo negative", Map("person_code", Map("SaleBillVouch", new object[] { -3 })));
            object[] many = new object[HeadFixUndo.MaxIds + 1];
            for (int i = 0; i < many.Length; i++)
            {
                many[i] = i + 1;
            }
            Bad("undo too many", Map("person_code", Map("rdrecord32", many)));
            object[] max = new object[HeadFixUndo.MaxIds];
            for (int i = 0; i < max.Length; i++)
            {
                max[i] = i + 1;
            }
            Dictionary<string, object> ok = Base();
            ok["undo"] = Map("person_code", Map("rdrecord32", max));
            VoucherHeadFixSelfTest.Expect("undo max ok", VoucherHeadFix.Parse(ok).Undo["person_code"]["rdrecord32"].Count == 500);
        }

        // 正向响应的 undo：字段 → 表 → id；只有真正写过的才出现；Ar_Detail 列 Auto_ID；什么都没改是 {}。
        static void CheckMapShape()
        {
            World w = new World();
            w.Inv(1, "SO1", "01", "P1");
            w.Ar(11, "26", "SO1", "P1");
            w.Ar(12, "26", "SO1", "P1");
            w.Out(2, "XS1", "01", "P1");
            List<HeadFixRow> rows = w.Forward(Fields(Field("addr_code", "01", "02"), Field("person_code", "P1", "P2")));
            Dictionary<string, object> undo = HeadFixUndo.Map(rows);
            Dictionary<string, object> addr = (Dictionary<string, object>)undo["addr_code"];
            Dictionary<string, object> person = (Dictionary<string, object>)undo["person_code"];
            VoucherHeadFixSelfTest.Eq("undo map addr", Join((List<object>)addr["rdrecord32"]) + "|" + Join((List<object>)addr["SaleBillVouch"]), "2|1");
            VoucherHeadFixSelfTest.Eq("undo map person", Join((List<object>)person["rdrecord32"]) + "|"
                + Join((List<object>)person["SaleBillVouch"]) + "|" + Join((List<object>)person["Ar_Detail"]), "2|1|11,12");
            VoucherHeadFixSelfTest.Expect("undo map no ar under addr", !addr.ContainsKey("Ar_Detail"));
            VoucherHeadFixSelfTest.Eq("undo map nothing", HeadFixUndo.Map(new List<HeadFixRow>()).Count, 0);
            HeadFixAsk ask = new HeadFixAsk();
            ask.Id = 3;
            ask.Fields.Add(Field("addr_code", "01", "02"));
            Dictionary<string, object> res = VoucherHeadFix.Body(ask, "FH", true, rows);
            VoucherHeadFixSelfTest.Expect("undo in body", res.ContainsKey("undo") && ((Dictionary<string, object>)res["undo"]).Count == 2);
        }

        // P1-a：合并发票的兄弟发货单已是新值——正向跟着改，无状态反向会因 shared 跳过；带 undo 的回滚必须还原。
        static void CheckMergedInvoice()
        {
            World w = new World();
            HeadFixDoc inv = w.Inv(50, "SO", "01", "P1");
            inv.Others.Add(Disp("02", "P1"));
            string before = w.Snapshot();
            List<HeadFixField> fwd = Fields(Field("addr_code", "01", "02"));
            List<HeadFixRow> rows = w.Forward(fwd);
            VoucherHeadFixSelfTest.Eq("merged forward", inv.Values["addr_code"], "02");
            HeadFixRow stateless = HeadFixPlan.DecideAll(w.Docs(), HeadFixPlan.Reverse(fwd))[0];
            VoucherHeadFixSelfTest.Eq("merged stateless reverse is not exact", stateless.Action + "/" + stateless.Reason, "skipped/shared");
            w.Rollback(fwd, rows);
            VoucherHeadFixSelfTest.Eq("merged round trip", w.Snapshot(), before);
        }

        // P1-b：下游行本来就是新值——正向 already_new 跳过，回滚不能把它改成旧值。
        static void CheckAlreadyNewRow()
        {
            World w = new World();
            w.Out(60, "XS60", "02", "P1");
            w.Out(61, "XS61", "01", "P1");
            string before = w.Snapshot();
            List<HeadFixField> fwd = Fields(Field("addr_code", "01", "02"));
            List<HeadFixRow> rows = w.Forward(fwd);
            VoucherHeadFixSelfTest.Eq("already forward", rows[0].Reason + "/" + rows[1].Action, "already_new/updated");
            HeadFixRow stateless = HeadFixPlan.DecideAll(w.Docs(), HeadFixPlan.Reverse(fwd))[0];
            VoucherHeadFixSelfTest.Eq("already stateless reverse would overwrite", stateless.Action, "updated");
            w.Rollback(fwd, rows);
            VoucherHeadFixSelfTest.Eq("already round trip", w.Snapshot(), before);
        }

        // P1-c：Ar_Detail 里有行本来就是新业务员——只回滚正向写过的 Auto_ID。
        static void CheckArRowsAlreadyNew()
        {
            World w = new World();
            w.Inv(70, "SO7", "01", "P1");
            w.Ar(1, "26", "SO7", "P1");
            w.Ar(2, "26", "SO7", "P1");
            w.Ar(3, "26", "SO7", "P2");
            string before = w.Snapshot();
            List<HeadFixField> fwd = Fields(Field("person_code", "P1", "P2"));
            List<HeadFixRow> rows = w.Forward(fwd);
            VoucherHeadFixSelfTest.Eq("ar forward", w.ArPersons(), "P2,P2,P2");
            w.Rollback(fwd, rows);
            VoucherHeadFixSelfTest.Eq("ar round trip", w.ArPersons(), "P1,P1,P2");
            VoucherHeadFixSelfTest.Eq("ar round trip snapshot", w.Snapshot(), before);
        }

        static void CheckRefusals()
        {
            World w = new World();
            w.Inv(80, "SO8", "01", "P1");
            w.Out(81, "XS8", "01", "P1");
            w.Ar(5, "26", "SO8", "P1");
            w.Out(999, "XS9", "01", "P1");
            w.Unlink("rdrecord32", 999);
            List<HeadFixField> fwd = Fields(Field("addr_code", "01", "02"), Field("person_code", "P1", "P2"));
            List<HeadFixRow> rows = w.Forward(fwd);
            HeadFixAsk ok = w.RollbackAsk(fwd, rows);
            HeadFixUndo.Build(ok, ok.Changing(), w);
            w.Docs()[0].Values["person_code"] = "P9";
            Refused("undo row not at expect", ok, w, 409, "state_mismatch");
            w.Docs()[0].Values["person_code"] = "P2";
            w.Ars()[0].Person = "P9";
            Refused("undo ar not at expect", ok, w, 409, "state_mismatch");
            w.Ars()[0].Person = "P2";
            w.Docs()[1].Values["addr_code"] = "07";
            Refused("undo out not at expect", ok, w, 409, "state_mismatch");
            w.Docs()[1].Values["addr_code"] = "02";
            ok.Undo["addr_code"]["rdrecord32"].Add(999);
            Refused("undo id not of this dispatch", ok, w, 409, "undo_foreign");
            ok.Undo["addr_code"]["rdrecord32"].Remove(999);
            ok.Undo["person_code"]["Ar_Detail"].Add(404);
            Refused("undo ar id unknown", ok, w, 409, "undo_foreign");
            ok.Undo["person_code"]["Ar_Detail"].Remove(404);
            w.UnlinkInvoice("SO8");
            Refused("undo ar of unlinked invoice", ok, w, 409, "undo_foreign");
            w.LinkInvoice("SO8");
            HeadFixAsk noChange = w.RollbackAsk(fwd, rows);
            noChange.Fields.RemoveAt(0);
            Refused("undo field that does not change", noChange, w, 409, "state_mismatch");
        }

        static void Refused(string name, HeadFixAsk ask, IHeadFixSource src, int status, string code)
        {
            try
            {
                HeadFixUndo.Build(ask, ask.Changing(), src);
            }
            catch (BridgeException ex)
            {
                VoucherHeadFixSelfTest.Expect(name + " (" + ex.Status + " " + ex.Code + ")", ex.Status == status && ex.Code == code);
                return;
            }
            throw new InvalidOperationException("head_fix " + name);
        }

        static string Join(List<object> ids)
        {
            List<string> parts = new List<string>();
            for (int i = 0; i < ids.Count; i++)
            {
                parts.Add(ids[i].ToString());
            }
            return string.Join(",", parts.ToArray());
        }

        // 请求体：fields / expect 同时改地址和业务员。
        static Dictionary<string, object> Base()
        {
            return VoucherHeadFixSelfTest.Body(Map("addr_code", "02", "person_code", "P2"), Map("addr_code", "01", "person_code", "P1"));
        }

        static void Bad(string name, object undo)
        {
            Bad(name, undo, false);
        }

        // onlyPerson：fields / expect 只含 person_code（检验 undo 的键不在 fields 里）。
        static void Bad(string name, object undo, bool onlyPerson)
        {
            Dictionary<string, object> body = onlyPerson
                ? VoucherHeadFixSelfTest.Body(Map("person_code", "P2"), Map("person_code", "P1")) : Base();
            body["undo"] = undo;
            try
            {
                VoucherHeadFix.Parse(body);
            }
            catch (BridgeException ex)
            {
                VoucherHeadFixSelfTest.Expect(name, ex.Status == 400 && ex.Code == "bad_request");
                return;
            }
            throw new InvalidOperationException("head_fix " + name);
        }

        static Dictionary<string, object> Map(params object[] pairs)
        {
            return VoucherHeadFixSelfTest.Map(pairs);
        }

        static HeadFixField Field(string key, string old, string value)
        {
            return VoucherHeadFixSelfTest.Field(key, old, value);
        }

        static List<HeadFixField> Fields(params HeadFixField[] fields)
        {
            return VoucherHeadFixSelfTest.Fields(fields);
        }

        static Dictionary<string, string> Disp(string addr, string person)
        {
            return VoucherHeadFixSelfTest.Disp(addr, person);
        }

        // 内存里的下游世界：这张发货单的出库单、发票（含其他来源发货单的值）和 Ar_Detail 行；同时是回滚模式的数据源。
        sealed class World : IHeadFixSource
        {
            readonly List<HeadFixDoc> _docs = new List<HeadFixDoc>();
            readonly List<HeadFixArRow> _ars = new List<HeadFixArRow>();
            readonly HashSet<string> _unlinked = new HashSet<string>();
            readonly HashSet<string> _unlinkedInvoices = new HashSet<string>();

            public HeadFixDoc Out(int id, string code, string addr, string person)
            {
                HeadFixDoc doc = VoucherHeadFixSelfTest.Out(id, code, addr, person, false);
                _docs.Add(doc);
                return doc;
            }

            public HeadFixDoc Inv(int id, string code, string addr, string person)
            {
                HeadFixDoc doc = VoucherHeadFixSelfTest.Inv(id, code, addr, person);
                _docs.Add(doc);
                return doc;
            }

            public void Ar(int autoId, string vouchType, string code, string person)
            {
                HeadFixArRow row = new HeadFixArRow();
                row.AutoId = autoId;
                row.VouchType = vouchType;
                row.Code = code;
                row.Person = person;
                _ars.Add(row);
            }

            public List<HeadFixDoc> Docs()
            {
                return _docs;
            }

            public List<HeadFixArRow> Ars()
            {
                return _ars;
            }

            public void Unlink(string table, int id)
            {
                _unlinked.Add(table + ":" + id.ToString());
            }

            public void UnlinkInvoice(string code)
            {
                _unlinkedInvoices.Add(code);
            }

            public void LinkInvoice(string code)
            {
                _unlinkedInvoices.Remove(code);
            }

            public HeadFixDoc Doc(string table, int id)
            {
                foreach (HeadFixDoc doc in _docs)
                {
                    if (doc.Table == table && doc.Id == id)
                    {
                        return _unlinked.Contains(table + ":" + id.ToString()) ? null : doc;
                    }
                }
                return null;
            }

            public HeadFixArRow Ar(int autoId)
            {
                foreach (HeadFixArRow row in _ars)
                {
                    if (row.AutoId == autoId)
                    {
                        return _unlinkedInvoices.Contains(row.Code) ? null : row;
                    }
                }
                return null;
            }

            // 正向：收集 Ar_Detail 里业务员等于改前值的行，判断，再把结果写进内存。
            public List<HeadFixRow> Forward(List<HeadFixField> fields)
            {
                HeadFixField person = null;
                foreach (HeadFixField f in fields)
                {
                    person = f.Key == "person_code" ? f : person;
                }
                List<HeadFixDoc> linked = Linked();
                foreach (HeadFixDoc doc in linked)
                {
                    doc.ArIds.Clear();
                    foreach (HeadFixArRow ar in _ars)
                    {
                        if (person != null && doc.Table == "SaleBillVouch" && ar.Code == doc.Code && HeadFixPlan.Same(ar.Person, person.Old))
                        {
                            doc.ArIds.Add(ar.AutoId);
                        }
                    }
                }
                List<HeadFixRow> rows = HeadFixPlan.DecideAll(linked, fields);
                Apply(rows);
                return rows;
            }

            // 属于这张发货单的单据（Unlink 过的是别的发货单的，正向不会发现它们）。
            List<HeadFixDoc> Linked()
            {
                List<HeadFixDoc> list = new List<HeadFixDoc>();
                foreach (HeadFixDoc doc in _docs)
                {
                    if (!_unlinked.Contains(doc.Table + ":" + doc.Id.ToString()))
                    {
                        list.Add(doc);
                    }
                }
                return list;
            }

            // 回滚：请求体 fields / expect 对调并带上正向响应的 undo，经真实的解析和 Build。
            public HeadFixAsk RollbackAsk(List<HeadFixField> fwd, List<HeadFixRow> rows)
            {
                Dictionary<string, object> f = new Dictionary<string, object>();
                Dictionary<string, object> e = new Dictionary<string, object>();
                foreach (HeadFixField field in fwd)
                {
                    f[field.Key] = field.Old;
                    e[field.Key] = field.New;
                }
                Dictionary<string, object> body = VoucherHeadFixSelfTest.Body(f, e);
                body["undo"] = HeadFixUndo.Map(rows);
                HeadFixAsk ask = VoucherHeadFix.Parse(body);
                foreach (HeadFixField field in ask.Fields)
                {
                    field.Old = field.Expect;
                }
                return ask;
            }

            public void Rollback(List<HeadFixField> fwd, List<HeadFixRow> rows)
            {
                HeadFixAsk ask = RollbackAsk(fwd, rows);
                List<HeadFixRow> plan = HeadFixUndo.Build(ask, ask.Changing(), this);
                Apply(plan);
                VoucherHeadFixSelfTest.Expect("rollback has no discovery", plan.Count == CountUpdated(rows));
            }

            static int CountUpdated(List<HeadFixRow> rows)
            {
                int n = 0;
                foreach (HeadFixRow r in rows)
                {
                    n += r.Action == HeadFixRow.Updated ? 1 : 0;
                }
                return n;
            }

            void Apply(List<HeadFixRow> rows)
            {
                foreach (HeadFixRow row in rows)
                {
                    if (row.Action != HeadFixRow.Updated)
                    {
                        continue;
                    }
                    if (row.IsAr)
                    {
                        foreach (int id in row.AutoIds)
                        {
                            Ar(id).Person = row.Set[0].New;
                        }
                        continue;
                    }
                    foreach (HeadFixField f in row.Set)
                    {
                        Doc(row.Table, row.Id).Values[f.Key] = f.New;
                    }
                }
            }

            public string Snapshot()
            {
                StringBuilder sb = new StringBuilder();
                foreach (HeadFixDoc doc in _docs)
                {
                    sb.Append(doc.Table).Append(doc.Id).Append(':').Append(doc.Values["addr_code"]).Append('/')
                        .Append(doc.Values["person_code"]).Append(';');
                }
                return sb.Append(ArPersons()).ToString();
            }

            public string ArPersons()
            {
                List<string> persons = new List<string>();
                foreach (HeadFixArRow ar in _ars)
                {
                    persons.Add(ar.Person);
                }
                return string.Join(",", persons.ToArray());
            }
        }
    }
}
