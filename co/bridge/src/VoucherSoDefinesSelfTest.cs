using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的销售订单表头自定义项写入部分（vouchers/so_defines）：请求解析与拒绝（列键、重复、空值、empty_values、
    // expect 键集合）、类型闸门、空值等价（含自定义空值）、写什么的纯判断（expect 不符的 detail.current、未变、只写有变化的列）、
    // 锁定人、列类型与长度、UPDATE 片段、回读核对、响应形状，以及写分类 / 写闸门 / 预演模式 / 权限规则 / 单据锁 / 只读例外表 /
    // 策略词表的登记。不连库。下游连带见 VoucherSoDefinesCascadeSelfTest。
    internal static class VoucherSoDefinesSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckParseRejects();
            CheckEmptyRejects();
            CheckGuard();
            CheckSame();
            CheckDecide();
            CheckDecideRefuse();
            CheckLocker();
            CheckLength();
            CheckColumns();
            CheckSetClause();
            CheckReadBack();
            CheckWiring();
            VoucherSoDefinesCascadeSelfTest.Run();
        }

        static void CheckParse()
        {
            SoDefinesAsk ask = VoucherSoDefines.Parse(Body(Map("CDefine3", "文本B", "cdefine1", "DEMO-001"),
                Map("cdefine3", null, "cDEFINE1", " 文本A "), null));
            Eq("parse keys", string.Join(",", ask.Keys.ToArray()), "cdefine1,cdefine3");
            Eq("parse value", ask.Values["cdefine1"], "DEMO-001");
            Eq("parse expect null", ask.Expect["cdefine3"], "");
            Eq("parse expect raw kept", ask.Expect["cdefine1"], " 文本A ");
            Expect("parse defaults", ask.Empty.Length == 0 && !ask.Cascade);
            ask = VoucherSoDefines.Parse(With(Body(Map("cdefine16", "文本A"), Map("cdefine16", "N/A"),
                new object[] { " N/A ", "-" }), VoucherSoDefines.CascadeKey, true));
            Expect("parse empty values", ask.Empty.Length == 2 && ask.Empty[0] == "N/A" && ask.Empty[1] == "-");
            Expect("parse cascade", ask.Cascade);
            Expect("parse empty null", VoucherSoDefines.Parse(With(Body(Map("cdefine2", "文本A"), Map("cdefine2", ""), null),
                VoucherSoDefines.EmptyKey, null)).Empty.Length == 0);
            Eq("parse long ok", VoucherSoDefines.Parse(Body(Map("cdefine1", new string('A', 120)), Map("cdefine1", ""), null))
                .Values["cdefine1"].Length, 120);
            Eq("number", VoucherSoDefines.Number("cdefine16"), 16);
            Expect("number bad", VoucherSoDefines.Number("cdefine17") == 0 && VoucherSoDefines.Number("cdefine0") == 0
                && VoucherSoDefines.Number("cdefine01") == 0 && VoucherSoDefines.Number("cdefine") == 0);
            Eq("column", VoucherSoDefines.Column("cdefine12"), "cDefine12");
        }

        static void CheckParseRejects()
        {
            Bad("unknown key", Body(Map("cfree1", "文本A"), Map("cfree1", ""), null), "fields.cfree1");
            Bad("cdefine17", Body(Map("cdefine17", "文本A"), Map("cdefine17", ""), null), "fields.cdefine17");
            Bad("duplicate after lowercasing", Body(Map("cDefine1", "文本A", "CDEFINE1", "文本B"), Map("cdefine1", ""), null), null);
            Bad("no fields", Body(null, Map("cdefine1", ""), null), "fields");
            Bad("fields empty", Body(Map(), Map(), null), "fields");
            Bad("fields not object", Body("cdefine1", Map("cdefine1", ""), null), "fields");
            Bad("empty value", Body(Map("cdefine1", ""), Map("cdefine1", ""), null), "fields.cdefine1");
            Bad("blank value", Body(Map("cdefine1", "  "), Map("cdefine1", ""), null), "fields.cdefine1");
            Bad("padded value", Body(Map("cdefine1", " 文本A"), Map("cdefine1", ""), null), "fields.cdefine1");
            Bad("control value", Body(Map("cdefine1", "文本\tA"), Map("cdefine1", ""), null), "fields.cdefine1");
            Bad("long value", Body(Map("cdefine1", new string('A', 121)), Map("cdefine1", ""), null), "fields.cdefine1");
            Bad("value not string", Body(Map("cdefine1", 12), Map("cdefine1", ""), null), "fields.cdefine1");
            Bad("value in empty_values", Body(Map("cdefine1", "N/A"), Map("cdefine1", ""), new object[] { " N/A " }),
                "fields.cdefine1");
            Bad("no expect", Body(Map("cdefine1", "文本A"), null, null), "expect");
            Bad("expect not object", Body(Map("cdefine1", "文本A"), "x", null), "expect");
            Bad("expect missing key", Body(Map("cdefine1", "文本A", "cdefine2", "文本B"), Map("cdefine1", ""), null), "expect.cdefine2");
            Bad("expect extra key", Body(Map("cdefine1", "文本A"), Map("cdefine1", "", "cdefine2", ""), null), "expect.cdefine2");
            Bad("expect unknown key", Body(Map("cdefine1", "文本A"), Map("cdefine1", "", "cdefine17", ""), null), "expect.cdefine17");
            Bad("expect number", Body(Map("cdefine1", "文本A"), Map("cdefine1", 3), null), "expect.cdefine1");
            Bad("expect control", Body(Map("cdefine1", "文本A"), Map("cdefine1", "a\nb"), null), "expect.cdefine1");
            Bad("cascade text", With(Body(Map("cdefine1", "文本A"), Map("cdefine1", ""), null), VoucherSoDefines.CascadeKey, "true"),
                "cascade");
            Bad("cascade null", With(Body(Map("cdefine1", "文本A"), Map("cdefine1", ""), null), VoucherSoDefines.CascadeKey, null),
                "cascade");
        }

        static void CheckEmptyRejects()
        {
            Bad("empty_values too long item", Body(Map("cdefine1", "文本A"), Map("cdefine1", ""), new object[] { new string('x', 21) }),
                "empty_values.0");
            Bad("empty_values blank item", Body(Map("cdefine1", "文本A"), Map("cdefine1", ""), new object[] { "-", "  " }),
                "empty_values.1");
            Bad("empty_values not string", Body(Map("cdefine1", "文本A"), Map("cdefine1", ""), new object[] { 1 }), "empty_values.0");
            Bad("empty_values over 8", Body(Map("cdefine1", "文本A"), Map("cdefine1", ""),
                new object[] { "a", "b", "c", "d", "e", "f", "g", "h", "i" }), "empty_values");
            Bad("empty_values not list", With(Body(Map("cdefine1", "文本A"), Map("cdefine1", ""), null),
                VoucherSoDefines.EmptyKey, "N/A"), "empty_values");
            VoucherSoDefines.Parse(Body(Map("cdefine1", "文本A"), Map("cdefine1", ""),
                new object[] { "a", "b", "c", "d", "e", "f", "g", new string('x', 20) }));
        }

        static void CheckGuard()
        {
            VoucherSoDefines.GuardKind(Kinds.Find("sale_order"));
            BadKind("guard dispatch", Kinds.Find("dispatch"));
            BadKind("guard purchase", Kinds.Find("purchase_order"));
            BadKind("guard null", null);
        }

        static void CheckSame()
        {
            string[] none = new string[0];
            string[] na = new string[] { "N/A" };
            Expect("blank null", VoucherSoDefines.Blank(null, none) && VoucherSoDefines.Blank("   ", none));
            Expect("blank token", VoucherSoDefines.Blank(" N/A ", na) && !VoucherSoDefines.Blank("N/A", none));
            Expect("blank token case", !VoucherSoDefines.Blank("n/a", na));
            Expect("same blanks", VoucherSoDefines.Same(null, "N/A", na) && VoucherSoDefines.Same("", " ", none));
            Expect("same trim", VoucherSoDefines.Same(" 文本A ", "文本A", none));
            Expect("same case", !VoucherSoDefines.Same("demo-001", "DEMO-001", none));
            Expect("same differs", !VoucherSoDefines.Same("文本A", "文本B", none) && !VoucherSoDefines.Same("", "文本A", none));
            Expect("exact", VoucherSoDefines.Exact(" 文本A", "文本A") && VoucherSoDefines.Exact(null, "")
                && !VoucherSoDefines.Exact("demo-001", "DEMO-001"));
        }

        // 判断表：未变（含去空白）、只写有变化的列、空值等价（含自定义空值）后照写。
        static void CheckDecide()
        {
            Plan("blank null", Ask2("文本A", "文本B", "", ""), Snap(null, null), "cdefine1,cdefine2");
            Plan("unchanged", Ask2("文本A", "文本B", "文本A", " 文本B"), Snap(" 文本A ", "文本B"), "");
            Plan("partial", Ask2("文本A", "文本B", "文本A", "文本C"), Snap("文本A", "文本C"), "cdefine2");
            Plan("case differs", Ask2("DEMO-001", "文本B", "demo-001", "文本B"), Snap("demo-001", "文本B"), "cdefine1");
            SoDefinesAsk token = Ask2("文本A", "文本B", "", "N/A");
            token.Empty = new string[] { "N/A" };
            Plan("custom empty token", token, Snap(" N/A ", null), "cdefine1,cdefine2");
        }

        static void CheckDecideRefuse()
        {
            Mismatch("expect differs", Ask2("文本A", "文本B", "", ""), Snap("文本C", null), "文本C", null);
            Mismatch("expect second differs", Ask2("文本A", "文本B", "", ""), Snap("", "文本C"), "", "文本C");
            Mismatch("expect case", Ask2("文本A", "文本B", "demo-001", ""), Snap("DEMO-001", null), "DEMO-001", null);
            Mismatch("token without empty_values", Ask2("文本A", "文本B", "", ""), Snap("N/A", null), "N/A", null);
            SoDefinesAsk token = Ask2("文本A", "文本B", "N/A", "");
            token.Empty = new string[] { "N/A" };
            Mismatch("token vs value", token, Snap("文本C", null), "文本C", null);
        }

        static void CheckLocker()
        {
            VoucherSoDefines.CheckLocker("", "张三");
            VoucherSoDefines.CheckLocker(" 张三 ", "张三");
            try
            {
                VoucherSoDefines.CheckLocker("李四", "张三");
            }
            catch (BridgeException ex)
            {
                Expect("locked", ex.Status == 409 && ex.Code == "state_mismatch"
                    && (string)ex.Detail["reason"] == "locked" && (string)ex.Detail["locker"] == "李四");
                return;
            }
            throw new InvalidOperationException("so_defines locker not refused");
        }

        static void CheckLength()
        {
            VoucherSoDefines.CheckLength("DEMO-001", 20, "fields.cdefine1");
            VoucherSoDefines.CheckLength(new string('A', 200), 0, "fields.cdefine1");
            VoucherSoDefines.CheckLength(null, 5, "fields.cdefine2");
            Eq("limit col", VoucherSoDefinesSql.Limit(20, 0), 20);
            Eq("limit def smaller", VoucherSoDefinesSql.Limit(20, 12), 12);
            Eq("limit def larger", VoucherSoDefinesSql.Limit(20, 40), 20);
            Eq("limit def only", VoucherSoDefinesSql.Limit(0, 30), 30);
            Eq("limit none", VoucherSoDefinesSql.Limit(0, 0), 0);
            try
            {
                VoucherSoDefines.CheckLength("DEMO-001", 5, "fields.cdefine1");
            }
            catch (BridgeException ex)
            {
                Expect("length", ex.Status == 400 && ex.Code == "bad_request" && ex.Field == "fields.cdefine1");
                return;
            }
            throw new InvalidOperationException("so_defines length not refused");
        }

        // SO_SOMain 上的列：非文本列、缺列 400（field fields.cdefineN）；超过长度上限 400；文本列放行。
        static void CheckColumns()
        {
            Dictionary<string, SoDefinesColumn> cols = new Dictionary<string, SoDefinesColumn>(StringComparer.OrdinalIgnoreCase);
            cols["cDefine1"] = Col("cDefine1", true, 20);
            cols["cDefine7"] = Col("cDefine7", false, 8);
            VoucherSoDefinesSql.CheckColumns(Ask1("cdefine1", "DEMO-001"), cols);
            BadColumns("not text column", Ask1("cdefine7", "1.5"), cols, "fields.cdefine7");
            BadColumns("missing column", Ask1("cdefine3", "文本A"), cols, "fields.cdefine3");
            cols["cDefine1"].Limit = 4;
            BadColumns("too long for column", Ask1("cdefine1", "DEMO-001"), cols, "fields.cdefine1");
            string sql = VoucherSoDefinesSql.HeadSql(new List<string>(new string[] { "cdefine1", "cdefine3" }), cols);
            Expect("head sql", sql.Contains("cDefine1 AS cdefine1, NULL AS cdefine3, LTRIM(RTRIM(ISNULL(cLocker,'')))")
                && sql.EndsWith("FROM SO_SOMain WITH (UPDLOCK, ROWLOCK) WHERE ID=?", StringComparison.Ordinal));
        }

        static void CheckSetClause()
        {
            List<string> names = new List<string>();
            string all = VoucherSoDefinesSql.SetClause(new List<string>(new string[] { "cdefine1", "cdefine11" }),
                new string[] { "cDefine1", "cDefine11", "cmodifier", "dModDate", "dmodifysystime" }, names);
            Eq("set all", all, "cDefine1=?, cDefine11=?, cModifier=?, dModDate=CONVERT(datetime, CONVERT(date, ?, 23)),"
                + " dmodifysystime=GETDATE()");
            Eq("set all names", string.Join(",", names.ToArray()), "cdefine1,cdefine11,who,date");
            names.Clear();
            Eq("set one", VoucherSoDefinesSql.SetClause(new List<string>(new string[] { "cdefine2" }),
                new string[] { "cDefine1", "cDefine2" }, names), "cDefine2=?");
            Eq("set one names", string.Join(",", names.ToArray()), "cdefine2");
        }

        static void CheckReadBack()
        {
            List<string> keys = new List<string>(new string[] { "cdefine1", "cdefine2" });
            SoDefinesSnap snap = VoucherSoDefinesSql.Snap(Map("code", "SO1", "cdefine2", "", "locker", "", "ts", "123"), keys);
            Expect("snap null kept", snap.Values["cdefine1"] == null && snap.Values["cdefine2"] == "");
            Eq("snap ts", snap.Ts, "123");
            SoDefinesAsk ask = Ask2("文本A", "文本B", "", "");
            SoDefinesPlan plan = VoucherSoDefines.Decide(ask, snap);
            Expect("written", VoucherSoDefinesSql.Written(ask, plan, Snap("文本A ", "文本B")));
            Expect("written differs", !VoucherSoDefinesSql.Written(ask, plan, Snap("文本A", null)));
            Expect("written case", !VoucherSoDefinesSql.Written(Ask2("DEMO-001", "文本B", "", ""), plan, Snap("demo-001", "文本B")));
            CheckBody(ask, snap);
        }

        static void CheckBody(SoDefinesAsk ask, SoDefinesSnap snap)
        {
            Dictionary<string, object> body = VoucherSoDefines.Body(ask, Snap("文本A", "文本B"), true);
            Dictionary<string, object> current = (Dictionary<string, object>)body["current"];
            Expect("body head", (bool)body["ok"] && (string)body["type"] == "sale_order" && (int)body["id"] == 7);
            Expect("body current", (bool)body["changed"] && (string)current["cdefine1"] == "文本A"
                && (string)current["cdefine2"] == "文本B" && current.Count == 2);
            Expect("body keys", body.ContainsKey("ufts") && body.ContainsKey("code") && !body.ContainsKey("dry_run")
                && !body.ContainsKey(VoucherSoDefines.CascadeKey));
            CheckDryBody(ask, snap);
        }

        static void CheckDryBody(SoDefinesAsk ask, SoDefinesSnap snap)
        {
            Dictionary<string, object> dry = VoucherSoDefines.DryBody(ask, snap, true);
            Expect("dry body", (bool)dry["dry_run"] && (string)dry["mode"] == DryRunModes.Validate
                && ((Dictionary<string, object>)dry["current"])["cdefine1"] == null);
        }

        static void CheckWiring()
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["type"] = "sale_order";
            WriteRequestInfo info = WriteClass.Of(VoucherSoDefines.Path, body);
            Expect("wiring class", info != null && info.Type == "sale_order" && info.Op == "so_defines");
            Expect("wiring op listed", WriteClass.IsOp("so_defines")
                && Array.IndexOf(WritePolicySnapshot.OpNames, "so_defines") >= 0);
            Expect("wiring write gate", WriteGate.IsWrite(VoucherSoDefines.Path));
            Expect("wiring dry accepts", DryRunReq.Accepts(VoucherSoDefines.Path));
            Eq("wiring dry mode", DryRunModes.Mode("vouchers/so_defines", Kinds.Find("sale_order"), "so_defines", ""), "validate");
            Eq("wiring dry other", DryRunModes.Mode("vouchers/so_defines", Kinds.Find("dispatch"), "so_defines", ""), "refuse");
            PermRule rule = PermRegistry.ForKey(VoucherSoDefines.RuleKey);
            Expect("wiring perm rule", rule != null && rule.Objs.Length == 4);
            Expect("wiring perm id", rule.Auths.Length == 1 && rule.Auths[0] == PermRegistry.SoChange);
            WorkItem item = new WorkItem();
            item.Path = VoucherSoDefines.Path;
            item.Type = Kinds.Find("sale_order");
            item.HasId = true;
            item.Id = 7;
            WorkItem update = new WorkItem();
            update.Path = "/u8co/v1/vouchers/update";
            update.Type = item.Type;
            update.HasId = true;
            update.Id = 7;
            Expect("wiring same lock as update", Array.IndexOf(DocLocks.KeysOf(item), "sale_order:7") >= 0
                && Array.IndexOf(DocLocks.KeysOf(update), "sale_order:7") >= 0);
            Expect("wiring spec", VoucherSoDefines.Spec[0] == VoucherSoDefines.Path && VoucherSoDefines.Spec.Length == 7);
            CheckReadOnlyTable();
        }

        static void CheckReadOnlyTable()
        {
            ReadOnlyVoucherOps.Row row = ReadOnlyVoucherOps.Find(VoucherSoDefines.Op);
            Expect("wiring readonly table", row != null && ReadOnlyVoucherOps.Root + row.Op == VoucherSoDefines.Path
                && row.Fixed && row.Kinds.Length == 1 && row.Kinds[0] == VoucherSoDefines.Kind);
            Expect("wiring readonly ops", Array.IndexOf(ConfigRulesReadOnly.VoucherOps, VoucherSoDefines.Op) >= 0
                && ConfigRulesReadOnly.VoucherOps.Length == ReadOnlyVoucherOps.Rows.Length);
        }

        static void Plan(string name, SoDefinesAsk ask, SoDefinesSnap snap, string set)
        {
            SoDefinesPlan plan = VoucherSoDefines.Decide(ask, snap);
            string got = string.Join(",", plan.Set.ToArray());
            Expect(name + " (" + got + ")", got == set && plan.Changed == (set.Length > 0));
        }

        static void Mismatch(string name, SoDefinesAsk ask, SoDefinesSnap snap, string cur1, string cur2)
        {
            try
            {
                VoucherSoDefines.Decide(ask, snap);
            }
            catch (BridgeException ex)
            {
                Dictionary<string, object> current = ex.Detail == null ? null : ex.Detail["current"] as Dictionary<string, object>;
                Expect(name, ex.Status == 409 && ex.Code == "state_mismatch" && current != null && current.Count == 2
                    && (string)current["cdefine1"] == cur1 && (string)current["cdefine2"] == cur2);
                return;
            }
            throw new InvalidOperationException("so_defines " + name + " not refused");
        }

        static void BadColumns(string name, SoDefinesAsk ask, Dictionary<string, SoDefinesColumn> cols, string field)
        {
            try
            {
                VoucherSoDefinesSql.CheckColumns(ask, cols);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request" && ex.Field == field);
                return;
            }
            throw new InvalidOperationException("so_defines " + name + " not refused");
        }

        internal static SoDefinesColumn Col(string name, bool text, int limit)
        {
            SoDefinesColumn c = new SoDefinesColumn();
            c.Name = name;
            c.Text = text;
            c.Limit = limit;
            return c;
        }

        // cdefine1 / cdefine2 两列的请求（id 7，无自定义空值）。
        internal static SoDefinesAsk Ask2(string v1, string v2, string e1, string e2)
        {
            SoDefinesAsk ask = new SoDefinesAsk();
            ask.Id = 7;
            ask.Keys.Add("cdefine1");
            ask.Keys.Add("cdefine2");
            ask.Values["cdefine1"] = v1;
            ask.Values["cdefine2"] = v2;
            ask.Expect["cdefine1"] = e1;
            ask.Expect["cdefine2"] = e2;
            return ask;
        }

        static SoDefinesAsk Ask1(string key, string value)
        {
            SoDefinesAsk ask = new SoDefinesAsk();
            ask.Id = 7;
            ask.Keys.Add(key);
            ask.Values[key] = value;
            ask.Expect[key] = "";
            return ask;
        }

        static SoDefinesSnap Snap(string v1, string v2)
        {
            SoDefinesSnap snap = new SoDefinesSnap();
            snap.Code = "SO1";
            snap.Values["cdefine1"] = v1;
            snap.Values["cdefine2"] = v2;
            snap.Locker = "";
            snap.Ts = "100";
            return snap;
        }

        static Dictionary<string, object> Body(object fields, object expect, object[] empty)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body["type"] = "sale_order";
            body["id"] = 7;
            if (fields != null)
            {
                body[VoucherSoDefines.FieldsKey] = fields;
            }
            if (expect != null)
            {
                body[VoucherSoDefines.ExpectKey] = expect;
            }
            if (empty != null)
            {
                body[VoucherSoDefines.EmptyKey] = new System.Collections.ArrayList(empty);
            }
            return body;
        }

        static Dictionary<string, object> With(Dictionary<string, object> body, string key, object value)
        {
            body[key] = value;
            return body;
        }

        static Dictionary<string, object> Map(params object[] pairs)
        {
            return VoucherHeadFixSelfTest.Map(pairs);
        }

        static void Bad(string name, Dictionary<string, object> body, string field)
        {
            try
            {
                VoucherSoDefines.Parse(body);
            }
            catch (BridgeException ex)
            {
                Expect(name + " (" + ex.Field + ")", ex.Status == 400 && ex.Code == "bad_request" && (field == null || ex.Field == field));
                return;
            }
            throw new InvalidOperationException("so_defines " + name);
        }

        static void BadKind(string name, VoucherKind kind)
        {
            try
            {
                VoucherSoDefines.GuardKind(kind);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request");
                return;
            }
            throw new InvalidOperationException("so_defines " + name);
        }

        static void Eq(string name, object got, object want)
        {
            Expect(name + " (" + got + " != " + want + ")", object.Equals(got, want));
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("so_defines " + name);
            }
        }
    }
}
