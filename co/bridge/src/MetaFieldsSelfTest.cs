using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的 meta/fields 部分：请求校验、模板类型映射、区间展开、模板字段名换算、VT 取法登记、
    // 总账固定标题、SQL 占位符个数、路由登记。不连库、不建 COM。
    internal static class MetaFieldsSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckTypes();
            CheckNames();
            CheckPlan();
            CheckArc();
            CheckGl();
            Expect("fields not perm read", !PermRegistry.IsRead(MetaFieldsReq.Path) && !PermRegistry.PerItem(MetaFieldsReq.Path));
            WorkItem item = new WorkItem();
            item.Path = MetaFieldsReq.Path;
            Expect("fields sql read", RouteClass.IsSqlRead(item));
        }

        static void CheckParse()
        {
            MetaFieldsAsk ask = MetaFieldsReq.Parse(Body("type", "sale_order"));
            Expect("fields default op", ask.Kind.Name == "sale_order" && ask.Op == "create" && ask.Source == null);
            Dictionary<string, object> gen = Body("type", "dispatch");
            gen["op"] = "generate";
            gen["source"] = "sale_order";
            ask = MetaFieldsReq.Parse(gen);
            Expect("fields generate", ask.Op == "generate" && ask.Source.Name == "sale_order");
            Expect("fields archive", MetaFieldsReq.Parse(Body("archive", "customer")).Arc.Name == "customer");
            Expect("fields gl", MetaFieldsReq.Parse(Body("gl", true)).Gl);
            Dictionary<string, object> glOff = Body("type", "purchase_order");
            glOff["gl"] = false;
            Expect("fields gl false", MetaFieldsReq.Parse(glOff).Kind.Name == "purchase_order");
            Field("fields none", new Dictionary<string, object>(), "type");
            Field("fields gl only false", Body("gl", false), "type");
            Dictionary<string, object> two = Body("type", "sale_order");
            two["archive"] = "customer";
            Field("fields two", two, "archive");
            Dictionary<string, object> withGl = Body("type", "sale_order");
            withGl["gl"] = true;
            Field("fields type gl", withGl, "gl");
            Field("fields gl text", Body("gl", "yes"), "gl");
            Field("fields bad type", Body("type", "nope"), "type");
            Field("fields type number", Body("type", 3), "type");
            Field("fields bad archive", Body("archive", "nope"), "archive");
            CheckParseOp();
        }

        static void CheckParseOp()
        {
            Field("fields bad op", With(Body("type", "sale_order"), "op", "delete"), "op");
            Field("fields op archive", With(Body("archive", "customer"), "op", "create"), "op");
            Field("fields source archive", With(Body("gl", true), "source", "sale_order"), "source");
            Field("fields source create", With(Body("type", "dispatch"), "source", "sale_order"), "source");
            Field("fields generate no source", With(Body("type", "dispatch"), "op", "generate"), "source");
            Dictionary<string, object> wrong = With(Body("type", "dispatch"), "op", "generate");
            wrong["source"] = "purchase_order";
            Field("fields generate wrong source", wrong, "source");
            Field("fields generate no sources", With(Body("type", "sale_order"), "op", "generate"), "op");
            Field("fields update unsupported", With(Body("type", "qm_incoming_inspect"), "op", "update"), "op");
            Field("fields create unsupported", Body("type", "qm_incoming_check"), "op");
        }

        static void CheckTypes()
        {
            Expect("type bool", MetaFieldsTpl.TypeOf(0, false, false) == "bool");
            Expect("type string", MetaFieldsTpl.TypeOf(1, false, false) == "string");
            Expect("type enum", MetaFieldsTpl.TypeOf(1, true, true) == "enum");
            Expect("type enum empty", MetaFieldsTpl.TypeOf(1, true, false) == "string");
            Expect("type int", MetaFieldsTpl.TypeOf(2, false, false) == "int" && MetaFieldsTpl.TypeOf(3, false, false) == "int");
            Expect("type decimal", MetaFieldsTpl.TypeOf(4, false, false) == "decimal");
            Expect("type date", MetaFieldsTpl.TypeOf(5, false, false) == "date");
            Expect("type unknown", MetaFieldsTpl.TypeOf(9, false, false) == "string" && MetaFieldsTpl.TypeOf(-1, true, true) == "string");
            CheckEntries();
        }

        static void CheckEntries()
        {
            Dictionary<string, object> raw = new Dictionary<string, object>();
            raw["f"] = "cBusType";
            raw["s"] = "T";
            raw["t"] = "1";
            raw["n"] = "1";
            raw["e"] = "1";
            raw["et"] = "SA.cBusType";
            raw["m"] = "20";
            raw["c"] = " 示例标题 ";
            TplRow row = MetaFieldsTpl.RowOf(raw);
            Expect("tpl row head", !row.Body && row.FieldType == 1);
            Expect("tpl row flags", row.IsNull && row.IsEnum);
            Expect("tpl row text", row.MaxLength == 20 && row.Label == "示例标题");
            List<object> values = new List<object>();
            values.Add(new Dictionary<string, object>());
            Dictionary<string, object> e = MetaFieldsTpl.Entry("cbustype", row, false, values);
            Expect("entry enum", (string)e["type"] == "enum" && (bool)e["required"]);
            Expect("entry enum values", (int)e["max_length"] == 20 && e["enum"] == values);
            Dictionary<string, object> none = MetaFieldsTpl.Entry("cdefine3", null, true, null);
            Expect("entry none", none["label"] == null && none["type"] == null);
            Expect("entry none required", (bool)none["required"] && !none.ContainsKey("enum"));
            raw["t"] = "4";
            raw["n"] = "0";
            raw["s"] = "b";
            Dictionary<string, object> dec = MetaFieldsTpl.Entry("iquantity", MetaFieldsTpl.RowOf(raw), false, null);
            Dictionary<string, object> intEnum = MetaFieldsTpl.Entry("iflag", MetaFieldsTpl.RowOf(raw), false, values);
            Expect("entry non-string enum", (string)intEnum["type"] == "decimal" && intEnum["enum"] == values);
            Expect("entry decimal", (string)dec["type"] == "decimal" && !(bool)dec["required"]);
            Expect("entry decimal length", !dec.ContainsKey("max_length"));
            Expect("tpl body", MetaFieldsTpl.RowOf(raw).Body);
            Expect("enum sql", Marks(MetaFieldsTpl.EnumSql(3)) == 3);
            CheckEnumTypes();
            Expect("caption sql", Marks(MetaFieldsArc.CaptionSql(2)) == 2);
        }

        // 枚举类型去重（不分大小写），不设上限（查询按 EnumChunk 分批）。
        static void CheckEnumTypes()
        {
            List<TplRow> rows = new List<TplRow>();
            for (int i = 0; i < MetaFieldsTpl.EnumChunk + 10; i++)
            {
                TplRow r = new TplRow();
                r.IsEnum = true;
                r.EnumType = "T" + i.ToString(System.Globalization.CultureInfo.InvariantCulture);
                rows.Add(r);
            }
            TplRow dup = new TplRow();
            dup.IsEnum = true;
            dup.EnumType = "t0";
            rows.Add(dup);
            TplRow plain = new TplRow();
            plain.EnumType = "X";
            rows.Add(plain);
            Expect("enum types", MetaFieldsTpl.EnumTypes(rows).Count == MetaFieldsTpl.EnumChunk + 10);
        }

        static void CheckNames()
        {
            Dictionary<string, object> span = new Dictionary<string, object>();
            span["prefix"] = "cdefine";
            span["from"] = 2;
            span["to"] = 4;
            Dictionary<string, object> side = new Dictionary<string, object>();
            side["exact"] = new string[] { "ccuscode", "ddate" };
            side["spans"] = new object[] { span };
            string[] names = MetaFieldsDoc.Names(side, new string[] { "op", "ccuscode" });
            Expect("names expand", string.Join(",", names) == "ccuscode,ddate,cdefine2,cdefine3,cdefine4,op");
            Expect("names null side", MetaFieldsDoc.Names(null, new string[] { "op" }).Length == 0);
            Dictionary<string, object> create = MetaWritable.Of(Kinds.Find("sale_order"))["create"] as Dictionary<string, object>;
            string[] head = MetaFieldsDoc.Names(create["head"], null);
            string[] lines = MetaFieldsDoc.Names(create["lines"], null);
            Expect("names so head", Array.IndexOf(head, "ccuscode") >= 0 && Array.IndexOf(head, "cdefine16") >= 0
                && Array.IndexOf(head, "cdefine17") < 0);
            Expect("names so lines", Array.IndexOf(lines, "cinvcode") >= 0 && Array.IndexOf(lines, "cfree10") >= 0);
            Dictionary<string, object> gen = MetaGenerate.Of("dispatch", "sale_order");
            string[] genLines = MetaFieldsDoc.Names(gen["lines"], null);
            Expect("names no control", Array.IndexOf(genLines, "source_line_id") < 0);
            List<object> control = MetaFieldsDoc.ControlSide((string[])gen["line_control"], new string[] { "quantity" });
            Dictionary<string, object> first = (Dictionary<string, object>)control[0];
            Expect("control side", control.Count == 2 && (string)first["name"] == "source_line_id" && first["label"] != null);
            Expect("control required", !(bool)first["required"] && (bool)((Dictionary<string, object>)control[1])["required"]);
            Expect("tpl name plain", MetaFieldsDoc.TplName("sale_order", "cdefine1", false) == "cdefine1");
            Expect("tpl name mo line", MetaFieldsDoc.TplName("production_order", "inv_code", true) == "Dinvcode");
            Expect("tpl name mo define", MetaFieldsDoc.TplName("production_order", "define22", true) == "DDefine_22");
            Expect("tpl name mo dept", MetaFieldsDoc.TplName("production_order", "dept_code", true) == "DMDeptCode");
            Expect("tpl name mo head", MetaFieldsDoc.TplName("production_order", "mo_code", false) == "mocode");
            Expect("tpl name bom eff", MetaFieldsDoc.TplName(BomRoutes.KindName, "eff_date", false) == "VersionEffDate");
        }

        // 每个单据类型都登记了模板取法；新类型忘了登记这里就失败。
        static void CheckPlan()
        {
            foreach (VoucherKind kind in Kinds.All())
            {
                string[] plan = MetaFieldsVt.Plan(kind);
                Expect("vt plan " + kind.Name, plan != null && plan[0].Length > 0);
            }
            string[] so = MetaFieldsVt.Plan(Kinds.Find("sale_order"));
            Expect("vt so", so[0] == "17" && so[2] == MetaFieldsVt.Com);
            string[] arr = MetaFieldsVt.Plan(Kinds.Find("arrival"));
            Expect("vt arrival", arr[0] == "26" && MetaFieldsVt.ParseVt(arr[1]) == 8169 && arr[2] == MetaFieldsVt.Fixed);
            string[] qm = MetaFieldsVt.Plan(Kinds.Find("qm_incoming_check"));
            Expect("vt qm", qm[0] == "QM03" && MetaFieldsVt.ParseVt(qm[1]) == 353);
            Expect("vt parse", MetaFieldsVt.ParseVt(" 95 ") == 95 && MetaFieldsVt.ParseVt(null) == 0 && MetaFieldsVt.ParseVt("-1") == 0);
        }

        static void CheckArc()
        {
            ArcKind customer = ArcKind.Find("customer");
            Expect("arc name required", MetaFieldsArc.Required(customer, customer.NameTag));
            Expect("arc other optional", !MetaFieldsArc.Required(customer, "abbrname"));
            Expect("arc bank required", MetaFieldsArc.Required(ArcKind.Find(ArcBank.Name), "cbankcode"));
            Expect("arc borrow", MetaFieldsArc.Borrowable("vendor_bank", "cbranch") && !MetaFieldsArc.Borrowable("vendor_bank", "cVenCode"));
            Expect("arc borrow other", MetaFieldsArc.Borrowable("customer_bank", "cCusCode"));
            Expect("arc keys", MetaFieldsArc.KeysOf("customer").Length > 0 && MetaFieldsArc.KeysOf("nope").Length == 0);
            Expect("arc project tags", MetaFieldsArc.Writable(ArcKind.Find(ArcKindRo.Project)).Length > 0);
            // fa_card 可新增（FaCardReq.Kind），只读的例子改用操作员。
            Expect("arc read only", MetaFieldsArc.Writable(ArcKind.Find(ArcUa.Operator)).Length == 0);
        }

        static void CheckGl()
        {
            Dictionary<string, object> gl = MetaFieldsRoute.Gl();
            List<object> lines = (List<object>)gl["lines"];
            Expect("gl lines", lines.Count == GlReq.MetaNames()[1].Length);
            for (int i = 0; i < lines.Count; i++)
            {
                Dictionary<string, object> d = (Dictionary<string, object>)lines[i];
                Expect("gl label " + d["name"], d["label"] != null && d["type"] != null);
            }
            Dictionary<string, object> account = (Dictionary<string, object>)lines[0];
            Expect("gl required", (string)account["name"] == "account" && (bool)account["required"]);
            string rev = (string)gl["fields_revision"];
            Expect("gl revision", rev.Length == 64 && rev == (string)MetaFieldsRoute.Gl()["fields_revision"]);
        }

        static Dictionary<string, object> Body(string key, object value)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            body[key] = value;
            return body;
        }

        static Dictionary<string, object> With(Dictionary<string, object> body, string key, object value)
        {
            body[key] = value;
            return body;
        }

        static int Marks(string sql)
        {
            int n = 0;
            for (int i = 0; i < sql.Length; i++)
            {
                if (sql[i] == '?')
                {
                    n++;
                }
            }
            return n;
        }

        static void Field(string name, Dictionary<string, object> body, string field)
        {
            try
            {
                MetaFieldsReq.Parse(body);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Field == field);
                return;
            }
            throw new InvalidOperationException(name);
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
