using System;
using System.Collections.Generic;

namespace U8Co
{
    // --selftest 的客户收货地址部分：请求解析、默认地址翻转计划、无变化判断、expect / 查重、UPDATE 生成、引用检查的 SQL 构造。不连库。
    internal static class ArcCusAddrSelfTest
    {
        public static void Run()
        {
            CheckParse();
            CheckParseRejects();
            CheckParseExpect();
            CheckPlanBasics();
            CheckDefaultFlip();
            CheckRejects();
            CheckUpdates();
            CheckUpdateFlip();
            CheckUpdateNull();
            CheckBody();
            CheckStoredAddress();
            CheckRefTargets();
            CheckRefSql();
        }

        static void CheckParse()
        {
            ArcCusAddrAsk ask = ArcCusAddr.Parse(Body("C900001", "1", Fields("code", "01", "name", "示例车间", "unit", "张三"),
                Fields("name", "3车间", "unit", "")));
            Eq("parse code", ask.Code, "C900001");
            Eq("parse address", ask.Address, "1");
            Eq("parse new code", ask.NewCode, "01");
            Eq("parse name", ask.Name, "示例车间");
            Eq("parse unit", ask.Unit, "张三");
            Eq("parse default unset", ask.Default, null);
            Eq("parse expect name", ask.ExpName, "3车间");
            Eq("parse expect unit", ask.ExpUnit, "");
            Eq("parse expect default unset", ask.ExpDefault, null);
            CheckParseVariants();
        }

        static void CheckParseVariants()
        {
            ArcCusAddrAsk ask = ArcCusAddr.Parse(Body("C900001", "1", Fields("default", true), Fields("default", false)));
            Eq("parse default", ask.Default, true);
            Eq("parse expect default", ask.ExpDefault, false);
            Eq("parse default no code", ask.NewCode, null);
            ask = ArcCusAddr.Parse(Body("C900001", "1", Fields("code", "2"), null));
            Eq("parse code only needs no expect", ask.NewCode, "2");
            Eq("parse code only keeps name", ask.Name, null);
            ask = ArcCusAddr.Parse(Body("C900001", "1", Fields("name", "  新名称  "), Fields("name", " 旧 ")));
            Eq("parse trims name", ask.Name, "新名称");
            Eq("parse trims expect", ask.ExpName, "旧");
            ask = ArcCusAddr.Parse(Body("C900001", "1", Fields("name", ""), Fields("name", "旧")));
            Eq("parse clear", ask.Name, "");
        }

        static void CheckParseRejects()
        {
            Dictionary<string, object> ok = Fields("code", "2");
            Bad("no code", Body(null, "1", ok, null));
            Bad("empty code", Body("", "1", ok, null));
            Bad("no address", Body("C900001", null, ok, null));
            Bad("empty address", Body("C900001", "", ok, null));
            Bad("long address", Body("C900001", new string('A', 31), ok, null));
            Bad("space address", Body("C900001", " 1", ok, null));
            Bad("no fields", Body("C900001", "1", null, null));
            Bad("empty fields", Body("C900001", "1", new Dictionary<string, object>(), null));
            Bad("fields not object", BodyRaw("C900001", "1", "x", null));
            Bad("unknown key", Body("C900001", "1", Fields("cLinkPerson", "x"), null));
            Bad("empty new code", Body("C900001", "1", Fields("code", ""), null));
            Bad("new code spaces", Body("C900001", "1", Fields("code", "a "), null));
            Bad("new code long", Body("C900001", "1", Fields("code", new string('B', 31)), null));
            Bad("new code type", Body("C900001", "1", Fields("code", 1), null));
            Bad("name long", Body("C900001", "1", Fields("name", new string('N', 256)), Fields("name", "")));
            Bad("name null", Body("C900001", "1", Fields("name", null), Fields("name", "")));
            Bad("name control", Body("C900001", "1", Fields("name", "a\nb"), Fields("name", "")));
            Bad("default type", Body("C900001", "1", Fields("default", "true"), Fields("default", false)));
            Bad("default number", Body("C900001", "1", Fields("default", 1), Fields("default", false)));
        }

        static void CheckParseExpect()
        {
            Bad("name without expect", Body("C900001", "1", Fields("name", "x"), null));
            Bad("unit without expect", Body("C900001", "1", Fields("unit", "x"), Fields("name", "")));
            Bad("default without expect", Body("C900001", "1", Fields("default", true), Fields()));
            Bad("expect null value", Body("C900001", "1", Fields("name", "x"), Fields("name", null)));
            Bad("expect name type", Body("C900001", "1", Fields("name", "x"), Fields("name", 5)));
            Bad("expect default type", Body("C900001", "1", Fields("default", true), Fields("default", "no")));
            Bad("expect not in fields", Body("C900001", "1", Fields("name", "x"), Fields("name", "", "unit", "")));
            Bad("expect code", Body("C900001", "1", Fields("code", "2"), Fields("code", "1")));
            Bad("expect not object", BodyRaw("C900001", "1", Fields("name", "x"), "y"));
        }

        static void CheckPlanBasics()
        {
            ArcCusAddrAsk ask = Ask("1", null, "示例车间", null, null);
            ask.ExpName = "3车间";
            ArcCusAddrPlan plan = ArcCusAddrPlanner.Make(ask, Sample());
            Expect("plan target", plan.Target == 0 && plan.Changed && plan.Dirty.Count == 1 && !plan.CodeChanges);
            Expect("plan after", plan.After[0].Name == "示例车间" && plan.Before[0].Name == "3车间");
            Expect("plan new address", plan.NewAddress == "1");
            ask = Ask("1", "01", null, null, null);
            plan = ArcCusAddrPlanner.Make(ask, Sample());
            Expect("plan code", plan.CodeChanges && plan.NewAddress == "01" && plan.Dirty.Count == 1);
            // 无变化：新值与现状相同（包括编码同值、名称同值），不产生任何 UPDATE。
            ask = Ask("1", "1", "3车间", "", null);
            ask.ExpName = "3车间";
            ask.ExpUnit = "";
            plan = ArcCusAddrPlanner.Make(ask, Sample());
            Expect("plan noop", !plan.Changed && !plan.CodeChanges && ArcCusAddrPlanner.Updates(plan, "C900001").Count == 0);
            ask = Ask("1", "1", null, null, null);
            Expect("plan same code", !ArcCusAddrPlanner.Make(ask, Sample()).CodeChanges);
            ask = Ask("A", "a", null, null, null);
            Expect("plan case only", ArcCusAddrPlanner.Make(ask, Mixed()).CodeChanges);
            Expect("plan find ignores case", ArcCusAddrPlanner.Make(Ask("B", null, null, null, null), Mixed()).Target == 1);
        }

        static void CheckDefaultFlip()
        {
            // 行 0 默认；设行 1 为默认 → 行 0 与行 1 都要写，行 2 不动。
            ArcCusAddrAsk ask = Ask("2", null, null, null, true);
            ask.ExpDefault = false;
            ArcCusAddrPlan plan = ArcCusAddrPlanner.Make(ask, Sample());
            Expect("flip dirty", plan.Dirty.Count == 2 && plan.Dirty[0] == 0 && plan.Dirty[1] == 1);
            Expect("flip after", !plan.After[0].Default && plan.After[1].Default && !plan.After[2].Default);
            // 数据已不一致（两行都默认）时，设目标为默认也要把另一行清掉。
            List<ArcCusAddrRow> rows = Sample();
            rows[2].Default = true;
            plan = ArcCusAddrPlanner.Make(ask, rows);
            Expect("flip dirty3", plan.Dirty.Count == 3 && !plan.After[0].Default && !plan.After[2].Default);
            // 目标已是唯一默认 → 无变化。
            ask = Ask("1", null, null, null, true);
            ask.ExpDefault = true;
            Expect("flip noop", !ArcCusAddrPlanner.Make(ask, Sample()).Changed);
            // default:false 只动目标行。
            plan = ArcCusAddrPlanner.Make(WithDefault(ask, false, true), Sample());
            Expect("unset only target", plan.Dirty.Count == 1 && plan.Dirty[0] == 0 && !plan.After[0].Default
                && !plan.After[1].Default);
            ask = Ask("2", null, null, null, false);
            ask.ExpDefault = false;
            Expect("unset noop", !ArcCusAddrPlanner.Make(ask, Sample()).Changed);
        }

        static ArcCusAddrAsk WithDefault(ArcCusAddrAsk ask, bool want, bool expect)
        {
            ask.Default = want;
            ask.ExpDefault = expect;
            return ask;
        }

        static void CheckRejects()
        {
            Fail("not found", 404, "address_not_found", Ask("9", "x", null, null, null));
            Fail("code taken", 409, "address_code_taken", Ask("1", "2", null, null, null));
            Fail("code taken case", 409, "address_code_taken", Ask("A", "B", null, null, null), Mixed());
            ArcCusAddrAsk ask = Ask("1", null, "x", null, null);
            ask.ExpName = "错的";
            Fail("mismatch name", 409, "state_mismatch", ask);
            ask = Ask("1", null, null, "u", null);
            ask.ExpUnit = "不是空";
            Fail("mismatch unit", 409, "state_mismatch", ask);
            ask = Ask("1", null, null, null, false);
            ask.ExpDefault = false;
            Fail("mismatch default", 409, "state_mismatch", ask);
            try
            {
                ask.Name = "x";
                ask.ExpName = "3车间";
                ArcCusAddrPlanner.Make(ask, Sample());
            }
            catch (BridgeException ex)
            {
                Expect("mismatch lists field", ex.Message.IndexOf("默认地址", StringComparison.Ordinal) >= 0
                    && ex.Message.IndexOf("地址名称", StringComparison.Ordinal) < 0);
                return;
            }
            throw new InvalidOperationException("mismatch lists field");
        }

        static void CheckUpdates()
        {
            ArcCusAddrAsk ask = Ask("1", "01", "示例车间", "", false);
            ask.ExpName = "3车间";
            ask.ExpUnit = "";
            ask.ExpDefault = true;
            ArcCusAddrPlan plan = ArcCusAddrPlanner.Make(ask, Sample());
            List<ArcCusAddrStmt> list = ArcCusAddrPlanner.Updates(plan, "C900001");
            Eq("upd count", list.Count, 1);
            Eq("upd sql", list[0].Sql, "UPDATE CusDeliverAdd SET cAddCode=?, cDeliverAdd=?, bDefault=? WHERE id=? AND cCusCode=?");
            Eq("upd args", list[0].Args.Length, 5);
            Eq("upd arg code", list[0].Args[0], "01");
            Eq("upd arg name", list[0].Args[1], "示例车间");
            Eq("upd arg default", list[0].Args[2], 0);
            Eq("upd arg id", list[0].Args[3], 11);
            Eq("upd arg customer", list[0].Args[4], "C900001");
        }

        // 翻转默认时另一行只写 bDefault；清空写 NULL。
        static void CheckUpdateFlip()
        {
            const string only = "UPDATE CusDeliverAdd SET bDefault=? WHERE id=? AND cCusCode=?";
            ArcCusAddrAsk ask = Ask("2", null, null, null, true);
            ask.ExpDefault = false;
            ArcCusAddrPlan plan = ArcCusAddrPlanner.Make(ask, Sample());
            List<ArcCusAddrStmt> list = ArcCusAddrPlanner.Updates(plan, "C900001");
            Eq("flip count", list.Count, 2);
            Eq("flip off sql", list[0].Sql, only);
            Eq("flip off value", list[0].Args[0], 0);
            Eq("flip off id", list[0].Args[1], 11);
            Eq("flip on sql", list[1].Sql, only);
            Eq("flip on value", list[1].Args[0], 1);
            Eq("flip on id", list[1].Args[1], 12);
        }

        static void CheckUpdateNull()
        {
            ArcCusAddrAsk ask = Ask("3", null, "", null, null);
            ask.ExpName = "办公室";
            ArcCusAddrPlan plan = ArcCusAddrPlanner.Make(ask, Sample());
            List<ArcCusAddrStmt> list = ArcCusAddrPlanner.Updates(plan, "C900001");
            Eq("null count", list.Count, 1);
            Eq("null sql", list[0].Sql, "UPDATE CusDeliverAdd SET cDeliverAdd=? WHERE id=? AND cCusCode=?");
            Eq("null arg", list[0].Args[0], null);
            Expect("matches", ArcCusAddrPlanner.Matches(plan.After, Clone(plan.After)));
            List<ArcCusAddrRow> other = Clone(plan.After);
            other[1].Unit = "x";
            Expect("matches differs", !ArcCusAddrPlanner.Matches(plan.After, other));
            other = Clone(plan.After);
            other.RemoveAt(2);
            Expect("matches count", !ArcCusAddrPlanner.Matches(plan.After, other));
        }

        static void CheckBody()
        {
            ArcCusAddrAsk ask = Ask("1", "01", null, null, null);
            ArcCusAddrPlan plan = ArcCusAddrPlanner.Make(ask, Sample());
            Dictionary<string, object> body = ArcCusAddr.OkBody(ask, plan, true);
            Eq("ok flag", body["ok"], true);
            Eq("ok code", body["code"], "C900001");
            Eq("ok address", body["address"], "1");
            Eq("ok new address", body["new_address"], "01");
            Eq("ok changed", body["changed"], true);
            Eq("ok before rows", ((List<object>)body["before"]).Count, 3);
            Eq("ok after rows", ((List<object>)body["after"]).Count, 3);
            Dictionary<string, object> row = (Dictionary<string, object>)((List<object>)body["after"])[0];
            Eq("ok row code", row["code"], "01");
            Eq("ok row name", row["name"], "3车间");
            Eq("ok row unit", row["unit"], "");
            Eq("ok row default", row["default"], true);
            Eq("ok row keys", row.Count, 4);
            Eq("ok before kept", ((Dictionary<string, object>)((List<object>)body["before"])[0])["code"], "1");
            CheckBodyOther(ask, plan);
        }

        // 请求里的 address 大小写与库里不同时，响应的 address / new_address 都用库里存的编码。
        static void CheckStoredAddress()
        {
            ArcCusAddrAsk ask = Ask("a", null, "x", null, null);
            ask.ExpName = "甲";
            ArcCusAddrPlan plan = ArcCusAddrPlanner.Make(ask, Mixed());
            Dictionary<string, object> body = ArcCusAddr.OkBody(ask, plan, true);
            Eq("stored address", body["address"], "A");
            Eq("stored new address", body["new_address"], "A");
            ask = Ask("a", "c", null, null, null);
            plan = ArcCusAddrPlanner.Make(ask, Mixed());
            body = ArcCusAddr.OkBody(ask, plan, true);
            Eq("stored address renamed", body["address"], "A");
            Eq("stored new address renamed", body["new_address"], "c");
        }

        static void CheckBodyOther(ArcCusAddrAsk ask, ArcCusAddrPlan plan)
        {
            Dictionary<string, object> body = ArcCusAddr.OkBody(ask, plan, false);
            Eq("unchanged flag", body["changed"], false);
            Dictionary<string, object> first = (Dictionary<string, object>)((List<object>)body["after"])[0];
            Eq("unchanged after", first["code"], "1");
            body = ArcCusAddr.DryBody(ask, plan);
            Eq("dry flag", body["dry_run"], true);
            Eq("dry mode", body["mode"], DryRunModes.Validate);
            Eq("dry changed", body["changed"], true);
            Expect("dry before", body.ContainsKey("before"));
            Expect("dry after", body.ContainsKey("after"));
        }

        static void CheckRefTargets()
        {
            List<Dictionary<string, object>> cat = new List<Dictionary<string, object>>();
            cat.Add(Cat("dbo", "DispatchList", "cAddCode", "nvarchar"));
            cat.Add(Cat("dbo", "DispatchList", "cCusCode", "nvarchar"));
            cat.Add(Cat("dbo", "RdRecord09", "cAddCode", "varchar"));
            cat.Add(Cat("dbo", "CusDeliverAdd", "cAddCode", "nvarchar"));
            cat.Add(Cat("dbo", "CusDeliverAdd", "cCusCode", "nvarchar"));
            cat.Add(Cat("dbo", "OnlyCus", "cCusCode", "nvarchar"));
            cat.Add(Cat("dbo", "IntAddr", "caddcode", "int"));
            cat.Add(Cat("dbo", "IntCus", "caddcode", "nvarchar"));
            cat.Add(Cat("dbo", "IntCus", "ccuscode", "int"));
            cat.Add(Cat("dbo", "LowerCase", "ccuscode", "char"));
            cat.Add(Cat("dbo", "LowerCase", "caddcode", "char"));
            List<ArcCusAddrRefTable> list = ArcCusAddrRefs.Targets(cat);
            Expect("ref count", list.Count == 4);
            Expect("ref filter by customer", list[0].Table == "DispatchList" && list[0].AddrCol == "cAddCode" && list[0].CusCol == "cCusCode");
            Expect("ref no customer column", list[1].Table == "RdRecord09" && list[1].CusCol == null);
            Expect("ref non-text customer column ignored", list[2].Table == "IntCus" && list[2].CusCol == null);
            Expect("ref case-insensitive", list[3].Table == "LowerCase" && list[3].AddrCol == "caddcode" && list[3].CusCol == "ccuscode");
            Expect("ref msg", ArcCusAddrRefs.Message(new List<string>(new string[] { "A", "B" }))
                == "收货地址已被单据引用，不能改编码：A、B");
        }

        static void CheckRefSql()
        {
            ArcCusAddrRefTable t = new ArcCusAddrRefTable();
            t.Schema = "dbo";
            t.Table = "DispatchList";
            t.AddrCol = "cAddCode";
            t.CusCol = "cCusCode";
            Expect("sql both", ArcCusAddrRefs.Sql(t) == "SELECT TOP 1 1 FROM [dbo].[DispatchList] WHERE [cAddCode]=? AND [cCusCode]=?");
            t.CusCol = null;
            Expect("sql addr only", ArcCusAddrRefs.Sql(t) == "SELECT TOP 1 1 FROM [dbo].[DispatchList] WHERE [cAddCode]=?");
            Expect("quote", ArcCusAddrRefs.Quote("a b") == "[a b]");
            string[] bad = { "a]b", "x]; DROP TABLE y;--", "", null };
            foreach (string name in bad)
            {
                RefRejected(name);
            }
            t.Table = "evil]t";
            RefRejected2(t);
        }

        static void RefRejected(string name)
        {
            try
            {
                ArcCusAddrRefs.Quote(name);
            }
            catch (BridgeException ex)
            {
                Expect("quote reject", ex.Status == 500);
                return;
            }
            throw new InvalidOperationException("quote reject " + name);
        }

        static void RefRejected2(ArcCusAddrRefTable t)
        {
            try
            {
                ArcCusAddrRefs.Sql(t);
            }
            catch (BridgeException)
            {
                return;
            }
            throw new InvalidOperationException("sql reject");
        }

        // 样本：同一客户三行；行 0 是默认地址（编码 1，id 11），行 1 编码 2（id 12），行 2 编码 3（id 13）。
        static List<ArcCusAddrRow> Sample()
        {
            List<ArcCusAddrRow> rows = new List<ArcCusAddrRow>();
            rows.Add(Row(11, "1", "3车间", "", true));
            rows.Add(Row(12, "2", "示例车间二", "张三", false));
            rows.Add(Row(13, "3", "办公室", "李四", false));
            return rows;
        }

        static List<ArcCusAddrRow> Mixed()
        {
            List<ArcCusAddrRow> rows = new List<ArcCusAddrRow>();
            rows.Add(Row(21, "A", "甲", "", false));
            rows.Add(Row(22, "b", "乙", "", false));
            return rows;
        }

        static ArcCusAddrRow Row(int id, string code, string name, string unit, bool dflt)
        {
            ArcCusAddrRow row = new ArcCusAddrRow();
            row.Id = id;
            row.Code = code;
            row.Name = name;
            row.Unit = unit;
            row.Default = dflt;
            return row;
        }

        static List<ArcCusAddrRow> Clone(List<ArcCusAddrRow> rows)
        {
            List<ArcCusAddrRow> list = new List<ArcCusAddrRow>();
            foreach (ArcCusAddrRow row in rows)
            {
                list.Add(row.Copy());
            }
            return list;
        }

        static ArcCusAddrAsk Ask(string address, string newCode, string name, string unit, bool? dflt)
        {
            ArcCusAddrAsk ask = new ArcCusAddrAsk();
            ask.Code = "C900001";
            ask.Address = address;
            ask.NewCode = newCode;
            ask.Name = name;
            ask.Unit = unit;
            ask.Default = dflt;
            return ask;
        }

        static Dictionary<string, object> Cat(string sch, string tbl, string col, string typ)
        {
            Dictionary<string, object> row = new Dictionary<string, object>();
            row["sch"] = sch;
            row["tbl"] = tbl;
            row["col"] = col;
            row["typ"] = typ;
            return row;
        }

        static Dictionary<string, object> Fields(params object[] pairs)
        {
            Dictionary<string, object> map = new Dictionary<string, object>();
            for (int i = 0; i + 1 < pairs.Length; i += 2)
            {
                map[(string)pairs[i]] = pairs[i + 1];
            }
            return map;
        }

        static Dictionary<string, object> Body(string code, string address, Dictionary<string, object> fields,
            Dictionary<string, object> expect)
        {
            return BodyRaw(code, address, fields, expect);
        }

        static Dictionary<string, object> BodyRaw(string code, string address, object fields, object expect)
        {
            Dictionary<string, object> body = new Dictionary<string, object>();
            if (code != null)
            {
                body["code"] = code;
            }
            if (address != null)
            {
                body["address"] = address;
            }
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
                ArcCusAddr.Parse(body);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == 400 && ex.Code == "bad_request");
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void Fail(string name, int status, string code, ArcCusAddrAsk ask)
        {
            Fail(name, status, code, ask, Sample());
        }

        static void Fail(string name, int status, string code, ArcCusAddrAsk ask, List<ArcCusAddrRow> rows)
        {
            try
            {
                ArcCusAddrPlanner.Make(ask, rows);
            }
            catch (BridgeException ex)
            {
                Expect(name, ex.Status == status && ex.Code == code);
                return;
            }
            throw new InvalidOperationException(name);
        }

        static void Eq(string name, object got, object want)
        {
            Expect(name, object.Equals(got, want));
        }

        static void Expect(string name, bool ok)
        {
            if (!ok)
            {
                throw new InvalidOperationException("customer_address " + name);
            }
        }
    }
}
